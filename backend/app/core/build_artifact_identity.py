"""Content-verified executable identity for Git-less deployment artifacts.

A runtime environment variable is only an expectation.  It must never become the
source of truth for the code that is actually executing.  Host checkouts retain
Git as the authoritative source.  Production images, which intentionally do not
carry a writable checkout, carry this build-time metadata file instead.

The metadata binds a declared source SHA/tag to a digest of the immutable backend
build context.  Runtime verification recomputes that digest.  A missing, malformed
or tampered artifact therefore cannot certify itself merely by setting
STERLING_EXPECTED_BUILD_SHA.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable

SCHEMA_VERSION: Final[int] = 1
METADATA_FILENAME: Final[str] = ".sterling-build.json"
UNKNOWN: Final[str] = "UNKNOWN"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# These locations are runtime state/caches, not immutable executable input.
_EXCLUDED_DIRS: Final[frozenset[str]] = frozenset(
    {"data", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
_EXCLUDED_SUFFIXES: Final[frozenset[str]] = frozenset({".pyc", ".pyo"})


@dataclass(frozen=True)
class ArtifactBuildIdentity:
    present: bool
    valid: bool
    build_sha: str = UNKNOWN
    release_tag: str = UNKNOWN
    expected_source_sha256: str = ""
    actual_source_sha256: str = ""
    source_file_count: int = 0
    reason: str = ""

    @property
    def source_clean(self) -> bool | None:
        if not self.present:
            return None
        return self.valid


def backend_root() -> Path:
    """Root copied into the production backend image."""
    return Path(__file__).resolve().parents[2]


def metadata_path(root: Path | None = None) -> Path:
    return (root or backend_root()) / METADATA_FILENAME


def _source_files(root: Path) -> Iterable[tuple[str, Path]]:
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if rel.name == METADATA_FILENAME:
            continue
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.suffix in _EXCLUDED_SUFFIXES:
            continue
        yield rel.as_posix(), path


def source_digest(root: Path | None = None) -> tuple[str, int]:
    """Deterministic digest of the immutable backend source/build context."""
    base = (root or backend_root()).resolve()
    digest = hashlib.sha256()
    count = 0
    for rel, path in _source_files(base):
        content_hash = hashlib.sha256(path.read_bytes()).digest()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_hash)
        digest.update(b"\0")
        count += 1
    return digest.hexdigest(), count


def _normalise_sha(value: str) -> str:
    sha = (value or "").strip().lower()
    if not sha or sha == UNKNOWN.lower():
        return UNKNOWN
    if not _SHA_RE.fullmatch(sha):
        raise ValueError("build SHA must be 40 lowercase hexadecimal characters or UNKNOWN")
    return sha


def _normalise_tag(value: str) -> str:
    tag = (value or "").strip()
    return tag if tag else UNKNOWN


def write_build_metadata(
    *,
    sha: str,
    tag: str = UNKNOWN,
    root: Path | None = None,
    output: Path | None = None,
) -> Path:
    """Write build metadata after source COPY and before the image is committed."""
    base = (root or backend_root()).resolve()
    destination = output or metadata_path(base)
    resolved_sha = _normalise_sha(sha)
    resolved_tag = _normalise_tag(tag)
    digest, count = source_digest(base)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "build_sha": resolved_sha,
        "release_tag": resolved_tag,
        "source_sha256": digest,
        "source_file_count": count,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temp.replace(destination)
    return destination


def artifact_identity(root: Path | None = None) -> ArtifactBuildIdentity:
    """Read and independently verify build metadata against executing source."""
    base = (root or backend_root()).resolve()
    path = metadata_path(base)
    if not path.exists():
        return ArtifactBuildIdentity(
            present=False, valid=False, reason="build_metadata_missing"
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - invalid identity must fail closed
        return ArtifactBuildIdentity(
            present=True,
            valid=False,
            reason=f"build_metadata_unreadable:{type(exc).__name__}",
        )

    if raw.get("schema_version") != SCHEMA_VERSION:
        return ArtifactBuildIdentity(
            present=True, valid=False, reason="build_metadata_schema_mismatch"
        )

    try:
        sha = _normalise_sha(str(raw.get("build_sha") or ""))
    except ValueError:
        return ArtifactBuildIdentity(
            present=True, valid=False, reason="build_metadata_sha_invalid"
        )
    tag = _normalise_tag(str(raw.get("release_tag") or ""))
    expected = str(raw.get("source_sha256") or "").strip().lower()
    expected_count = raw.get("source_file_count")

    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return ArtifactBuildIdentity(
            present=True,
            valid=False,
            build_sha=sha,
            release_tag=tag,
            reason="build_metadata_digest_invalid",
        )
    if not isinstance(expected_count, int) or expected_count < 1:
        return ArtifactBuildIdentity(
            present=True,
            valid=False,
            build_sha=sha,
            release_tag=tag,
            expected_source_sha256=expected,
            reason="build_metadata_file_count_invalid",
        )

    try:
        actual, count = source_digest(base)
    except Exception as exc:  # noqa: BLE001
        return ArtifactBuildIdentity(
            present=True,
            valid=False,
            build_sha=sha,
            release_tag=tag,
            expected_source_sha256=expected,
            reason=f"source_digest_unavailable:{type(exc).__name__}",
        )

    if count != expected_count:
        return ArtifactBuildIdentity(
            present=True,
            valid=False,
            build_sha=sha,
            release_tag=tag,
            expected_source_sha256=expected,
            actual_source_sha256=actual,
            source_file_count=count,
            reason="source_file_count_mismatch",
        )
    if actual != expected:
        return ArtifactBuildIdentity(
            present=True,
            valid=False,
            build_sha=sha,
            release_tag=tag,
            expected_source_sha256=expected,
            actual_source_sha256=actual,
            source_file_count=count,
            reason="source_digest_mismatch",
        )

    return ArtifactBuildIdentity(
        present=True,
        valid=True,
        build_sha=sha,
        release_tag=tag,
        expected_source_sha256=expected,
        actual_source_sha256=actual,
        source_file_count=count,
    )


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write")
    write.add_argument("--sha", required=True)
    write.add_argument("--tag", default=UNKNOWN)
    write.add_argument("--root", type=Path, default=backend_root())
    write.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.command == "write":
        path = write_build_metadata(
            sha=args.sha, tag=args.tag, root=args.root, output=args.output
        )
        print(path)
        return 0
    return 64


if __name__ == "__main__":  # pragma: no cover - Docker build entry point
    raise SystemExit(_main())
