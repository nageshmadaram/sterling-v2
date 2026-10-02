"""Release certification: the gate table of §5, recorded against one exact SHA.

Certification is evidence about a build, not about a repository. The moment a
line of code changes, the SHA changes and every attestation recorded against
the old one stops applying — not "mostly applies", stops. That rule is the
entire reason this module keys everything by SHA and refuses to inherit.

Some gates the software can check for itself (is the tree clean, does the
frozen manifest match what is running, did the last restore-check pass).
Remote CI is derived from its per-context register. Moving-market Kite
acceptance, forced reconnect and persistence are derived from the structured
acceptance artifact itself, bound to the exact runtime SHA. A typed PASS can
never substitute for those bytes. Anything unrecorded, unreadable or incomplete
is UNKNOWN, and UNKNOWN is not a pass.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.state_paths import state_root
from typing import Any, Final, Iterable, Mapping

from app.core.release_manifest import (
    lane_manifest_file_drift,
    read_manifest,
    runtime_sha,
    release_tag,
    release_tag_points_at,
    verify_manifest,
    working_tree_clean,
)

log = logging.getLogger(__name__)

__all__ = [
    "Gate",
    "GateResult",
    "CertificationReport",
    "CertificationStore",
    "certification_report",
    "render_certification",
    "RELEASE_GATES",
    "PRE_FREEZE_GATES",
    "pre_freeze_blockers",
]

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

CERTIFICATION_DIR: Final[str] = "data/manifests/certification"
ACCEPTANCE_DIR: Final[str] = "data/evidence_acceptance"

_ACCEPTANCE_GATE_KEYS: Final[tuple[str, ...]] = (
    "kite_live_acceptance",
    "reconnect",
    "persistence",
)
_RECONNECT_CHECKS: Final[tuple[str, ...]] = (
    "forced_disconnect",
    "disconnect_observed",
    "reconnect_attempted",
    "reconnect_connected",
    "post_reconnect_fresh_ticks",
    "post_reconnect_full_mode",
)
_PERSISTENCE_CHECKS: Final[tuple[str, ...]] = (
    "persistence",
    "reload_reproduces_values",
    "writer_healthy",
)
_TEST_SUITE_CONTEXTS: Final[tuple[str, ...]] = (
    "Backend Tests (Python 3.12)",
    "Backend Tests (Python 3.13)",
    "Frontend Tests (Vitest)",
    "Frontend Type Check",
    "E2E Playwright (chromium)",
    "E2E Playwright (firefox)",
    "E2E Playwright (webkit)",
)


@dataclass(frozen=True)
class Gate:
    """One row of the §5 table."""

    key: str
    requirement: str
    #: What this gate blocks when it is not PASS.
    blocks: str
    #: Can the running system determine this itself?
    automated: bool


RELEASE_GATES: Final[tuple[Gate, ...]] = (
    Gate("source_identity", "clean worktree; exact SHA; exact immutable tag", "all production", True),
    Gate("production_security", "production mode; strong distinct secrets; persisted credentials encrypted and readable", "release", True),
    Gate("deployment_identity", "stable deployment id; registered static egress matches a fresh observation on this exact SHA", "release", True),
    Gate("account_binding", "ACTIVE Production Shadow binding has fresh broker profile/inventory proof for this deployment", "release", True),
    Gate("remote_ci", "all required protected checks green on the exact SHA", "release", False),
    Gate("test_suites", "backend, frontend and E2E suites PASS on the exact SHA in clean CI", "release", True),
    Gate("kite_live_acceptance", "engineering PASS against a moving market", "release", True),
    Gate("reconnect", "forced disconnect -> observed close -> reconnect -> fresh ticks -> FULL depth", "release", True),
    Gate("persistence", "isolated run, reload and evidence checks PASS", "release", True),
    Gate("release_manifest", "the frozen manifest matches the running build and all lanes", "authoritative evidence", True),
    Gate("backup_restore", "fresh backup plus an isolated restore-check PASS", "handoff", True),
    Gate("failure_drills", "every capital-critical drill PASS", "unattended operation", False),
    Gate("open_exposure", "zero unresolved or unknown exposure before freeze", "release", False),
)

_GATES_BY_KEY: Final[Mapping[str, Gate]] = {g.key: g for g in RELEASE_GATES}

#: Gates composed from a per-item register rather than attested in one line.
#: `certify attest` refuses these: the register is the attestation, item by item.
_DERIVED_GATES: Final[frozenset[str]] = frozenset(
    {
        "failure_drills",
        "open_exposure",
        "remote_ci",
        "kite_live_acceptance",
        "reconnect",
        "persistence",
    }
)

#: The gates RELEASE_READY is the conjunction of. Every gate blocks something,
#: but these are the ones that block shipping at all.
_REQUIRED_FOR_READY: Final[tuple[str, ...]] = tuple(g.key for g in RELEASE_GATES)

#: Gates that must already be PASS before the manifest is written. The local
#: annotated release tag is created on the exact candidate SHA before freeze; a
#: Git tag does not change that SHA. Only the manifest gate is necessarily
#: post-freeze because the artifact does not exist until freeze writes it.
PRE_FREEZE_GATES: Final[tuple[str, ...]] = tuple(
    key for key in _REQUIRED_FOR_READY
    if key != "release_manifest"
)


def pre_freeze_blockers(report: "CertificationReport") -> tuple["GateResult", ...]:
    """Return every gate that makes freezing premature.

    UNKNOWN is a blocker. A release manifest is an evidence artifact, not a
    planning file; writing one before these gates pass creates an object that
    looks authoritative while naming an unproven build.
    """
    by_key = report.by_key
    return tuple(
        by_key.get(key, GateResult(key))
        for key in PRE_FREEZE_GATES
        if not by_key.get(key, GateResult(key)).passed
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    return state_root()


def _valid_evidence_reference(reference: str) -> bool:
    """A PASS must point somewhere another operator can actually inspect."""
    ref = str(reference or "").strip()
    if not ref:
        return False
    lowered = ref.lower()
    if lowered.startswith(("https://", "http://")):
        return True
    if "://" in ref:
        return False
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = _root() / path
    return path.is_file()


def _local_evidence_sha256(reference: str) -> str:
    """Hash a local evidence artifact; URLs are external references, not local bytes."""
    ref = str(reference or "").strip()
    if not ref or ref.lower().startswith(("https://", "http://")) or "://" in ref:
        return ""
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = _root() / path
    if not path.is_file():
        return ""
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class GateResult:
    """One gate's state for one SHA."""

    key: str
    status: str = UNKNOWN
    detail: str = ""
    #: Who observed it, for the human gates. Empty for automated ones.
    attested_by: str = ""
    #: Where the artifact lives: a CI run URL, a log path, a drill report.
    evidence_ref: str = ""
    recorded_at: str = ""
    #: Local evidence is content-addressed so later edits invalidate PASS.
    evidence_sha256: str = ""

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "detail": self.detail,
            "attested_by": self.attested_by,
            "evidence_ref": self.evidence_ref,
            "recorded_at": self.recorded_at,
            "evidence_sha256": self.evidence_sha256,
            "requirement": _GATES_BY_KEY[self.key].requirement if self.key in _GATES_BY_KEY else "",
            "blocks": _GATES_BY_KEY[self.key].blocks if self.key in _GATES_BY_KEY else "",
        }


@dataclass(frozen=True)
class CertificationReport:
    """The whole gate table for one SHA, plus the RELEASE_READY verdict."""

    sha: str
    tag: str
    results: tuple[GateResult, ...] = field(default_factory=tuple)
    unresolved_exposure: int | None = None

    @property
    def by_key(self) -> dict[str, GateResult]:
        return {r.key: r for r in self.results}

    @property
    def failures(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if r.status == FAIL)

    @property
    def unknowns(self) -> tuple[GateResult, ...]:
        return tuple(r for r in self.results if r.status == UNKNOWN)

    @property
    def release_ready(self) -> bool:
        """§5: every required gate PASS and zero unresolved exposure.

        ``unresolved_exposure is None`` refuses: an exposure count nobody could
        read is not a count of zero.
        """
        if self.unresolved_exposure is None or self.unresolved_exposure != 0:
            return False
        results = self.by_key
        return all(results.get(key, GateResult(key)).passed for key in _REQUIRED_FOR_READY)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "tag": self.tag,
            "release_ready": self.release_ready,
            "unresolved_exposure": self.unresolved_exposure,
            "gates": [r.as_dict() for r in self.results],
            "failures": [r.key for r in self.failures],
            "unknowns": [r.key for r in self.unknowns],
        }


class CertificationStore:
    """One JSON file per SHA. Attestations are never copied between SHAs."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else _root() / CERTIFICATION_DIR

    def _path(self, sha: str) -> Path:
        return self.directory / f"{sha}.json"

    def read(self, sha: str) -> dict[str, GateResult]:
        path = self._path(sha)
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))

        # The path name is not build identity. A copied certification file from
        # another commit must not carry human PASS attestations onto this SHA.
        recorded_sha = str(payload.get("sha") or "").strip()
        if recorded_sha != str(sha):
            return {}

        out: dict[str, GateResult] = {}
        for row in payload.get("gates", []):
            key = str(row.get("key") or "")
            if not key:
                continue

            status = str(row.get("status") or UNKNOWN).strip().upper()
            detail = str(row.get("detail") or "")
            attested_by = str(row.get("attested_by") or "").strip()
            evidence_ref = str(row.get("evidence_ref") or "").strip()
            evidence_sha256 = str(row.get("evidence_sha256") or "").strip()

            gate = _GATES_BY_KEY.get(key)
            if (
                status == PASS
                and gate is not None
                and not gate.automated
                and key not in _DERIVED_GATES
                and (
                    not attested_by
                    or not evidence_ref
                    or not _valid_evidence_reference(evidence_ref)
                    or (
                        not evidence_ref.lower().startswith(("https://", "http://"))
                        and (
                            not evidence_sha256
                            or _local_evidence_sha256(evidence_ref) != evidence_sha256
                        )
                    )
                )
            ):
                status = UNKNOWN
                detail = (
                    "PASS evidence is missing, invalid, or changed since attestation; "
                    "re-attestation required on this exact SHA"
                )

            out[key] = GateResult(
                key=key,
                status=status,
                detail=detail,
                attested_by=attested_by,
                evidence_ref=evidence_ref,
                recorded_at=str(row.get("recorded_at") or ""),
                evidence_sha256=evidence_sha256,
            )
        return out

    def attest(
        self,
        sha: str,
        key: str,
        status: str,
        *,
        attested_by: str,
        evidence_ref: str = "",
        detail: str = "",
    ) -> GateResult:
        """Record one human gate against one exact SHA."""
        if key not in _GATES_BY_KEY:
            raise KeyError(f"unknown certification gate {key!r}")
        if key in _DERIVED_GATES:
            raise ValueError(
                f"{key} is derived from its own register or machine evidence and "
                "cannot be attested in one line"
            )
        if _GATES_BY_KEY[key].automated:
            raise ValueError(
                f"{key} is automated and cannot be replaced by a human attestation"
            )
        status = status.strip().upper()
        if status not in (PASS, FAIL, UNKNOWN):
            raise ValueError(f"status must be {PASS}, {FAIL} or {UNKNOWN}; got {status!r}")
        if status == PASS and not attested_by.strip():
            raise ValueError("a PASS must name who observed it")
        if status == PASS and not evidence_ref.strip():
            raise ValueError("a PASS must include an evidence reference")
        if status == PASS and not _valid_evidence_reference(evidence_ref):
            raise ValueError(
                "a PASS evidence reference must be an existing local file or http(s) URL"
            )

        existing = self.read(sha)
        existing[key] = GateResult(
            key=key,
            status=status,
            detail=detail,
            attested_by=attested_by.strip(),
            evidence_ref=evidence_ref.strip(),
            recorded_at=_now(),
            evidence_sha256=(
                _local_evidence_sha256(evidence_ref)
                if status == PASS
                else ""
            ),
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(sha).write_text(
            json.dumps(
                {
                    "sha": sha,
                    "updated_at": _now(),
                    "gates": [r.as_dict() for r in existing.values()],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return existing[key]


# -- the automated gates ---------------------------------------------------
def _git_tag_target(tag: str) -> str:
    """Resolve one Git tag to the commit it names; empty means absent/unreadable."""
    if not tag:
        return ""
    try:
        out = subprocess.run(
            ["git", "rev-list", "-n", "1", tag],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=_root(),
        )
    except Exception:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _source_identity(sha: str, tag: str) -> GateResult:
    clean = working_tree_clean()
    problems: list[str] = []
    unknown = clean is None
    if clean is not True:
        problems.append("working tree is not clean" if clean is False else "cleanliness unknown")

    actual_sha = runtime_sha()
    if not actual_sha or actual_sha.upper() == "UNKNOWN":
        problems.append("actual runtime SHA is unknown")
        unknown = True
    elif sha and sha.upper() != "UNKNOWN" and actual_sha != sha:
        problems.append(
            f"candidate SHA {sha[:12]} is not the running SHA {actual_sha[:12]}"
        )

    if not sha or sha.upper() == "UNKNOWN":
        problems.append("runtime SHA is unknown")
    if not tag or tag.upper() == "UNKNOWN":
        problems.append("no exact immutable release tag")
    else:
        points = release_tag_points_at(tag, sha)
        if points is not True:
            problems.append(
                "release tag cannot be verified at this exact SHA"
                if points is None else "release tag does not point at this exact SHA"
            )
            unknown = unknown or points is None
    if problems:
        return GateResult("source_identity", UNKNOWN if unknown else FAIL, "; ".join(problems))
    return GateResult("source_identity", PASS, f"{tag} @ {sha[:12]}", attested_by="sterlingctl")

def _release_manifest_gate() -> GateResult:
    try:
        stored = read_manifest()
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            "release_manifest",
            UNKNOWN,
            f"frozen release manifest unreadable: {exc}",
        )
    if stored is None:
        return GateResult("release_manifest", FAIL, "no frozen release manifest; run `sterlingctl freeze`")
    verdict = verify_manifest(stored)
    if not verdict.matches:
        return GateResult(
            "release_manifest",
            FAIL,
            f"{len(verdict.drift)} field(s) drifted; "
            f"{len(verdict.identity_drift)} change a lane identity",
        )

    lane_file_problems = lane_manifest_file_drift(stored)
    if lane_file_problems:
        return GateResult(
            "release_manifest",
            FAIL,
            "lane manifest artifact drift: " + "; ".join(lane_file_problems[:5]),
        )

    return GateResult(
        "release_manifest",
        PASS,
        "manifest and per-lane artifacts match the running build",
        attested_by="sterlingctl",
    )


def _backup_restore_gate(sha: str) -> GateResult:
    try:
        from app.core.backup_manifest import (
            MAX_BACKUP_AGE,
            latest_backup_dir,
            prove_restore,
            read_backup_manifest,
            sha256_file,
        )
    except Exception as exc:  # pragma: no cover - import guard
        return GateResult("backup_restore", UNKNOWN, f"backup module unavailable: {exc}")

    backup_root = Path(
        os.environ.get("STERLING_BACKUP_ROOT")
        or os.environ.get("STERLING_SNAPBACK_BACKUP_ROOT")
        or _root() / "backups" / "snapback"
    )
    directory = latest_backup_dir(backup_root)
    if directory is None:
        return GateResult("backup_restore", FAIL, f"no backup under {backup_root}")
    manifest_file = directory / "manifest.json"
    if not manifest_file.exists():
        return GateResult("backup_restore", UNKNOWN, f"backup at {directory} has no manifest")
    try:
        manifest = read_backup_manifest(manifest_file)
    except Exception as exc:  # noqa: BLE001
        return GateResult("backup_restore", UNKNOWN, f"manifest unreadable: {exc}")
    if str(getattr(manifest, "release_sha", "") or "") != sha:
        return GateResult(
            "backup_restore",
            FAIL,
            f"{directory.name}: backup release SHA "
            f"{str(getattr(manifest, 'release_sha', '') or 'UNKNOWN')[:12]} "
            f"does not match candidate {sha[:12]}",
        )

    # Exact SHA is necessary but not sufficient. State can change while code does
    # not, so a historical backup must not certify today's handoff. Use the same
    # 24-hour policy as the operator doctor and fail closed on clock anomalies.
    try:
        created_at = datetime.fromisoformat(str(getattr(manifest, "created_at", "") or ""))
    except (TypeError, ValueError):
        return GateResult(
            "backup_restore", UNKNOWN,
            f"{directory.name}: backup created_at is unreadable",
        )
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    backup_age = now - created_at
    if backup_age.total_seconds() < 0:
        return GateResult(
            "backup_restore", UNKNOWN,
            f"{directory.name}: backup timestamp is in the future; clock state is not trustworthy",
        )
    if backup_age > MAX_BACKUP_AGE:
        hours = int(backup_age.total_seconds() // 3600)
        return GateResult(
            "backup_restore", FAIL,
            f"{directory.name}: backup is {hours}h old; release limit is "
            f"{int(MAX_BACKUP_AGE.total_seconds() // 3600)}h",
        )

    status_text = str(getattr(manifest, "restore_test_status", "") or "UNTESTED")
    if status_text.upper() in ("PASSED", "PASS", "VERIFIED"):
        tested_at = str(getattr(manifest, "restore_tested_at", "") or "").strip()
        recorded_failure = str(getattr(manifest, "restore_failure", "") or "").strip()
        if not tested_at:
            return GateResult(
                "backup_restore",
                FAIL,
                f"{directory.name}: restore PASS has no tested-at timestamp",
            )
        try:
            restore_tested_at = datetime.fromisoformat(tested_at)
        except ValueError:
            return GateResult(
                "backup_restore", UNKNOWN,
                f"{directory.name}: restore tested-at timestamp is unreadable",
            )
        if restore_tested_at.tzinfo is None:
            restore_tested_at = restore_tested_at.replace(tzinfo=timezone.utc)
        if restore_tested_at > now:
            return GateResult(
                "backup_restore", UNKNOWN,
                f"{directory.name}: restore proof timestamp is in the future; clock state is not trustworthy",
            )
        if restore_tested_at < created_at:
            return GateResult(
                "backup_restore", FAIL,
                f"{directory.name}: restore proof predates the backup it claims to verify",
            )
        if recorded_failure:
            return GateResult(
                "backup_restore",
                FAIL,
                f"{directory.name}: restore PASS still records failure: {recorded_failure}",
            )

        checksums = dict(getattr(manifest, "file_checksums", {}) or {})
        if "release.json" not in checksums or "coverage.json" not in checksums:
            return GateResult(
                "backup_restore",
                FAIL,
                f"{directory.name}: backup manifest does not checksum release/coverage artifacts",
            )
        for name, expected in checksums.items():
            artifact = directory / name
            if not artifact.exists():
                return GateResult(
                    "backup_restore",
                    FAIL,
                    f"{directory.name}: checksummed artifact missing: {name}",
                )
            try:
                actual = sha256_file(artifact)
            except Exception as exc:  # noqa: BLE001
                return GateResult(
                    "backup_restore",
                    UNKNOWN,
                    f"{directory.name}: cannot verify {name}: {exc}",
                )
            if actual != str(expected):
                return GateResult(
                    "backup_restore",
                    FAIL,
                    f"{directory.name}: checksum changed after restore proof: {name}",
                )

        try:
            release_payload = json.loads(
                (directory / "release.json").read_text(encoding="utf-8")
            )
            build = release_payload.get("build") or {}
        except Exception as exc:  # noqa: BLE001
            return GateResult(
                "backup_restore",
                UNKNOWN,
                f"{directory.name}: release.json unreadable: {exc}",
            )
        if str(build.get("runtime_sha") or "") != str(manifest.release_sha or ""):
            return GateResult(
                "backup_restore",
                FAIL,
                f"{directory.name}: release.json SHA disagrees with backup manifest",
            )
        if str(build.get("release_tag") or "") != str(manifest.release_tag or ""):
            return GateResult(
                "backup_restore",
                FAIL,
                f"{directory.name}: release.json tag disagrees with backup manifest",
            )

        # Do not trust the mutable PASS stamp as the proof itself.  Re-run
        # the isolated restore against the bytes and metadata that exist NOW.
        # This catches a hand-edited manifest that says PASSED, a DB whose
        # checksum was recomputed after corruption, and stale row/schema claims.
        try:
            fresh_proof = prove_restore(directory, manifest)
        except Exception as exc:  # noqa: BLE001
            return GateResult(
                "backup_restore",
                UNKNOWN,
                f"{directory.name}: fresh restore verification unavailable: {exc}",
            )
        if not fresh_proof.passed:
            failures = "; ".join(fresh_proof.failures[:5]) or "unspecified restore failure"
            return GateResult(
                "backup_restore",
                FAIL,
                f"{directory.name}: fresh isolated restore verification failed: {failures}",
            )

        # Before freeze, release_manifest may legitimately be absent from the
        # backup (otherwise freeze and backup would be circular). Once THIS SHA
        # has a frozen manifest, final handoff requires a newer backup that
        # actually captured it. A pre-freeze backup cannot certify post-freeze
        # recoverability forever.
        frozen = read_manifest()
        frozen_sha = str(((frozen or {}).get("build") or {}).get("runtime_sha") or "")
        if frozen is not None and frozen_sha == sha:
            try:
                coverage_rows = json.loads(
                    (directory / "coverage.json").read_text(encoding="utf-8")
                )
            except Exception as exc:  # noqa: BLE001
                return GateResult(
                    "backup_restore",
                    UNKNOWN,
                    f"{directory.name}: cannot verify post-freeze coverage: {exc}",
                )
            release_row = next(
                (
                    row
                    for row in coverage_rows
                    if isinstance(row, dict)
                    and row.get("name") == "release_manifest"
                ),
                None,
            )
            if (
                release_row is None
                or str(release_row.get("coverage") or "") != "COPIED"
            ):
                return GateResult(
                    "backup_restore",
                    FAIL,
                    f"{directory.name}: current release is frozen but this backup "
                    "does not contain the frozen release_manifest; take and prove "
                    "a fresh post-freeze backup",
                )

            frozen_copy = directory / "state" / "release_manifest.json"
            frozen_rel = frozen_copy.relative_to(directory).as_posix()
            if not frozen_copy.is_file():
                return GateResult(
                    "backup_restore",
                    FAIL,
                    f"{directory.name}: coverage claims release_manifest was copied "
                    "but state/release_manifest.json is missing",
                )
            if frozen_rel not in checksums:
                return GateResult(
                    "backup_restore",
                    FAIL,
                    f"{directory.name}: copied frozen release manifest is not "
                    "checksummed by the backup manifest",
                )
            try:
                backed_up_frozen = json.loads(
                    frozen_copy.read_text(encoding="utf-8")
                )
            except Exception as exc:  # noqa: BLE001
                return GateResult(
                    "backup_restore",
                    UNKNOWN,
                    f"{directory.name}: backed-up frozen release manifest is unreadable: {exc}",
                )
            if backed_up_frozen != frozen:
                return GateResult(
                    "backup_restore",
                    FAIL,
                    f"{directory.name}: backed-up frozen release manifest does not "
                    "match the currently frozen release",
                )

        return GateResult(
            "backup_restore",
            PASS,
            f"{directory.name}: {status_text} on {sha[:12]} at {tested_at}; "
            "fresh isolated restore verified",
            attested_by="sterlingctl",
        )
    return GateResult(
        "backup_restore",
        FAIL,
        f"{directory.name}: restore_test_status={status_text}; run `sterlingctl restore-check`",
    )


def _production_security_gate() -> GateResult:
    """Production security is a machine fact, not an operator attestation."""
    environment = (os.environ.get("ENVIRONMENT") or "development").strip().lower()
    if environment != "production":
        return GateResult(
            "production_security",
            FAIL,
            f"ENVIRONMENT={environment or 'unset'}; exact release certification requires production",
        )
    try:
        from app.core.security import validate_production_security

        validate_production_security()
    except RuntimeError as exc:
        return GateResult("production_security", FAIL, str(exc)[:500])
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            "production_security",
            UNKNOWN,
            f"production security could not be verified: {type(exc).__name__}: {exc}",
        )
    return GateResult(
        "production_security",
        PASS,
        "production secret policy and persisted credential encryption verified",
        attested_by="sterlingctl",
    )


def _deployment_identity_gate(sha: str) -> GateResult:
    """Require one stable deployment and a fresh static-egress observation on this SHA."""
    deployment_id = (os.environ.get("STERLING_DEPLOYMENT_ID") or "").strip()
    if not deployment_id:
        return GateResult(
            "deployment_identity",
            FAIL,
            "STERLING_DEPLOYMENT_ID is unset",
        )
    try:
        from app.core.deployment_identity import verify_deployment_identity

        verdict = verify_deployment_identity()
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            "deployment_identity",
            UNKNOWN,
            f"deployment identity could not be read: {type(exc).__name__}: {exc}",
        )

    if verdict.verified is None:
        return GateResult("deployment_identity", UNKNOWN, verdict.reason)
    if verdict.verified is False:
        return GateResult("deployment_identity", FAIL, verdict.reason)

    observation = verdict.observation
    if observation is None:
        return GateResult(
            "deployment_identity",
            UNKNOWN,
            "static egress matched but no deployment observation is available",
        )
    observed_sha = str(observation.runtime_sha or "").strip()
    if not observed_sha or observed_sha.upper() == "UNKNOWN":
        return GateResult(
            "deployment_identity",
            UNKNOWN,
            "latest deployment observation is not bound to an exact runtime SHA",
        )
    if observed_sha != sha:
        return GateResult(
            "deployment_identity",
            FAIL,
            f"latest deployment observation belongs to {observed_sha[:12]}, "
            f"not candidate {sha[:12]}",
        )
    return GateResult(
        "deployment_identity",
        PASS,
        f"{deployment_id}: fresh registered-egress observation on {sha[:12]}",
        attested_by="sterlingctl",
    )


def _account_binding_gate() -> GateResult:
    """Require an ACTIVE shadow binding backed by fresh read-only broker evidence."""
    try:
        from app.core.broker_account_binding import BindingStatus
        from app.services.account_binding_service import (
            AccountBindingStore,
            verification_is_current,
        )

        binding = AccountBindingStore().active()
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            "account_binding",
            UNKNOWN,
            f"account binding store could not be read: {type(exc).__name__}: {exc}",
        )

    if binding is None:
        return GateResult("account_binding", FAIL, "no ACTIVE broker account binding")
    if binding.status is not BindingStatus.ACTIVE:
        return GateResult(
            "account_binding",
            FAIL,
            f"binding {binding.binding_id} is {binding.status.value}, not ACTIVE",
        )
    if "shadow" not in binding.account_scope.strip().lower():
        return GateResult(
            "account_binding",
            FAIL,
            f"binding {binding.binding_id} scope={binding.account_scope!r}; "
            "Production Shadow requires an explicit shadow scope",
        )
    if not verification_is_current(binding):
        return GateResult(
            "account_binding",
            FAIL,
            f"binding {binding.binding_id} has no fresh broker-backed verification",
        )

    deployment_id = (os.environ.get("STERLING_DEPLOYMENT_ID") or "").strip()
    if not deployment_id:
        return GateResult("account_binding", FAIL, "STERLING_DEPLOYMENT_ID is unset")
    if binding.deployment_id != deployment_id:
        return GateResult(
            "account_binding",
            FAIL,
            f"binding deployment {binding.deployment_id!r} does not match "
            f"running deployment {deployment_id!r}",
        )

    configured_account = (os.environ.get("STERLING_FAMILY_ACCOUNT_ID") or "").strip()
    if not configured_account:
        return GateResult(
            "account_binding",
            FAIL,
            "STERLING_FAMILY_ACCOUNT_ID is unset",
        )
    if binding.verification_account_id != configured_account:
        return GateResult(
            "account_binding",
            FAIL,
            f"verified application account {binding.verification_account_id!r} "
            f"does not match configured {configured_account!r}",
        )

    evidence_ref = str(binding.verification_evidence_ref or "").strip()
    evidence_path = Path(evidence_ref).expanduser()
    if not evidence_path.is_absolute():
        evidence_path = _root() / evidence_path
    if not evidence_path.is_file():
        return GateResult(
            "account_binding",
            UNKNOWN,
            f"fresh binding evidence file is missing: {evidence_path}",
        )
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return GateResult(
            "account_binding",
            UNKNOWN,
            f"binding evidence is unreadable: {type(exc).__name__}: {exc}",
        )
    expected = {
        "binding_id": binding.binding_id,
        "broker": binding.broker,
        "profile_client_id": binding.client_id,
        "application_account_id": configured_account,
        "deployment_id": deployment_id,
        "account_scope": binding.account_scope,
    }
    mismatches = [
        f"{key}={evidence.get(key)!r}"
        for key, value in expected.items()
        if str(evidence.get(key) or "") != str(value)
    ]
    if mismatches:
        return GateResult(
            "account_binding",
            FAIL,
            "broker-binding evidence identity mismatch: " + ", ".join(mismatches),
            evidence_ref=str(evidence_path),
            evidence_sha256=_local_evidence_sha256(str(evidence_path)),
        )
    if evidence.get("schema_version") != 1:
        return GateResult(
            "account_binding",
            FAIL,
            f"broker-binding evidence schema_version={evidence.get('schema_version')!r}; expected 1",
            evidence_ref=str(evidence_path),
            evidence_sha256=_local_evidence_sha256(str(evidence_path)),
        )
    evidence_stamp = str(evidence.get("observed_at") or "").strip()
    binding_stamp = str(binding.verified_at or "").strip()
    try:
        evidence_time = datetime.fromisoformat(evidence_stamp)
        binding_time = datetime.fromisoformat(binding_stamp)
    except ValueError:
        return GateResult(
            "account_binding",
            UNKNOWN,
            "broker-binding evidence/binding verification timestamp is unreadable",
            evidence_ref=str(evidence_path),
            evidence_sha256=_local_evidence_sha256(str(evidence_path)),
        )
    if evidence_time.tzinfo is None:
        evidence_time = evidence_time.replace(tzinfo=timezone.utc)
    if binding_time.tzinfo is None:
        binding_time = binding_time.replace(tzinfo=timezone.utc)
    if evidence_time.astimezone(timezone.utc) != binding_time.astimezone(timezone.utc):
        return GateResult(
            "account_binding",
            FAIL,
            "broker-binding evidence observed_at does not match binding verified_at",
            evidence_ref=str(evidence_path),
            evidence_sha256=_local_evidence_sha256(str(evidence_path)),
        )
    if not all(
        evidence.get(key) is True
        for key in ("positions_read", "orders_read", "protective_orders_read")
    ):
        return GateResult(
            "account_binding",
            FAIL,
            "broker-binding evidence did not prove positions/orders/protection reads",
            evidence_ref=str(evidence_path),
            evidence_sha256=_local_evidence_sha256(str(evidence_path)),
        )

    return GateResult(
        "account_binding",
        PASS,
        f"{binding.binding_id}: fresh real-broker identity and inventory proof",
        attested_by="broker-binding-verifier",
        evidence_ref=str(evidence_path),
        recorded_at=str(binding.verified_at or ""),
        evidence_sha256=_local_evidence_sha256(str(evidence_path)),
    )


def _acceptance_artifact(sha: str) -> tuple[Path | None, dict[str, Any] | None, str]:
    """Newest chronologically valid acceptance report for this exact runtime SHA.

    A report from another SHA satisfies nothing. If several runs exist on the
    same SHA, the newest one is authoritative so a later failed rerun cannot be
    hidden behind an older PASS. Clock-invalid evidence is never treated as
    chronological proof, and a newer invalid rerun blocks an older PASS.
    """
    directory = _root() / ACCEPTANCE_DIR
    if not directory.is_dir():
        return None, None, f"no acceptance directory at {directory}"

    now = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, Path, dict[str, Any]]] = []
    invalid_exact_sha: list[tuple[Path, str]] = []
    unreadable = 0

    def _stamp(value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc)

    for path in directory.glob("acceptance-*.json"):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - unreadable evidence cannot certify
            unreadable += 1
            continue
        if not isinstance(payload, dict):
            unreadable += 1
            continue
        if str(payload.get("runtime_sha") or "").strip() != str(sha):
            continue

        started = _stamp(payload.get("started_at"))
        finished = _stamp(payload.get("finished_at"))
        reason = ""
        if started is None or finished is None:
            reason = "acceptance timestamps are missing, naive, or unreadable"
        elif finished < started:
            reason = "acceptance finished_at predates started_at"
        elif finished > now:
            reason = "acceptance finished_at is in the future; clock state is not trustworthy"

        if reason:
            invalid_exact_sha.append((path, reason))
            continue
        candidates.append((finished, path, payload))

    if not candidates:
        details = []
        if invalid_exact_sha:
            details.append(
                f"{len(invalid_exact_sha)} clock-invalid exact-SHA artifact(s)"
            )
        if unreadable:
            details.append(f"{unreadable} unreadable artifact(s)")
        suffix = "; " + "; ".join(details) if details else ""
        return None, None, f"no valid acceptance artifact for exact SHA {sha[:12]}{suffix}"

    _finished, path, payload = max(candidates, key=lambda row: row[0])

    # Harness filenames are UTC timestamp ordered. If a later exact-SHA rerun
    # exists but its internal clock evidence is invalid, do not silently fall
    # back to the older PASS.
    newer_invalid = [
        (bad_path, reason)
        for bad_path, reason in invalid_exact_sha
        if bad_path.name > path.name
    ]
    if newer_invalid:
        bad_path, reason = max(newer_invalid, key=lambda row: row[0].name)
        return (
            None,
            None,
            f"newer exact-SHA acceptance artifact {bad_path.name} is invalid: {reason}",
        )

    return path, payload, ""


def _artifact_gate_result(
    key: str,
    *,
    path: Path,
    payload: Mapping[str, Any],
    required: Mapping[str, str],
    detail: str,
) -> GateResult:
    missing = [name for name in required if name not in payload]
    if missing:
        return GateResult(
            key,
            UNKNOWN,
            "acceptance artifact is incomplete; missing: " + ", ".join(missing),
            evidence_ref=str(path),
        )

    wrong = [
        f"{name}={payload.get(name)!r}"
        for name, expected in required.items()
        if str(payload.get(name) or "") != expected
    ]
    if wrong:
        return GateResult(
            key,
            FAIL,
            "acceptance artifact did not prove the gate: " + ", ".join(wrong),
            evidence_ref=str(path),
            evidence_sha256=_local_evidence_sha256(str(path)),
        )

    return GateResult(
        key,
        PASS,
        detail,
        attested_by="acceptance-harness",
        evidence_ref=str(path),
        recorded_at=str(payload.get("finished_at") or ""),
        evidence_sha256=_local_evidence_sha256(str(path)),
    )


def _acceptance_gates(sha: str) -> dict[str, GateResult]:
    """Derive the three live-acceptance gates from report content, not a typed PASS."""
    path, payload, reason = _acceptance_artifact(sha)
    if path is None or payload is None:
        return {key: GateResult(key, UNKNOWN, reason) for key in _ACCEPTANCE_GATE_KEYS}

    # Structural identity: this must be the read-only acceptance harness output,
    # not an arbitrary JSON file carrying a matching runtime_sha field.
    if (
        str(payload.get("evidence_class") or "") != "MODELLED"
        or str(payload.get("tag") or "") != "TEST_ACCEPTANCE"
    ):
        detail = (
            "exact-SHA artifact is not a TEST_ACCEPTANCE/MODELLED acceptance report"
        )
        return {
            key: GateResult(
                key,
                FAIL,
                detail,
                evidence_ref=str(path),
                evidence_sha256=_local_evidence_sha256(str(path)),
            )
            for key in _ACCEPTANCE_GATE_KEYS
        }

    conclusions = payload.get("conclusions")
    if not isinstance(conclusions, dict):
        return {
            key: GateResult(
                key,
                UNKNOWN,
                "acceptance artifact has no readable conclusions object",
                evidence_ref=str(path),
            )
            for key in _ACCEPTANCE_GATE_KEYS
        }

    live_payload = {
        "overall": payload.get("overall"),
        "engineering_gate": conclusions.get("engineering_gate"),
        "vendor_assumptions": conclusions.get("vendor_assumptions"),
        "market_data_live": payload.get("market_data_live"),
    }
    reconnect_payload = {name: payload.get(name) for name in _RECONNECT_CHECKS}
    persistence_payload = {name: payload.get(name) for name in _PERSISTENCE_CHECKS}

    return {
        "kite_live_acceptance": _artifact_gate_result(
            "kite_live_acceptance",
            path=path,
            payload=live_payload,
            required={
                "overall": PASS,
                "engineering_gate": PASS,
                "vendor_assumptions": "PROVEN",
                "market_data_live": PASS,
            },
            detail=f"moving-market acceptance PASS from {path.name}",
        ),
        "reconnect": _artifact_gate_result(
            "reconnect",
            path=path,
            payload=reconnect_payload,
            required={name: PASS for name in _RECONNECT_CHECKS},
            detail=f"forced disconnect/reconnect sequence PASS from {path.name}",
        ),
        "persistence": _artifact_gate_result(
            "persistence",
            path=path,
            payload=persistence_payload,
            required={name: PASS for name in _PERSISTENCE_CHECKS},
            detail=f"acceptance persistence/reload/writer checks PASS from {path.name}",
        ),
    }


def _failure_drills_gate(sha: str) -> GateResult:
    """Every declared drill, on this exact SHA, with a name against each PASS."""
    try:
        from app.core.failure_drills import DRILLS, drill_report

        report = drill_report(sha)
    except Exception as exc:  # noqa: BLE001
        return GateResult("failure_drills", UNKNOWN, f"drill register unavailable: {exc}")
    if report.all_passed:
        names = sorted({r.observed_by for r in report.results if r.observed_by})
        return GateResult("failure_drills", PASS,
                          f"all {len(DRILLS)} drills passed on this build",
                          attested_by=", ".join(names))
    if report.failures:
        return GateResult("failure_drills", FAIL,
                          "failed: " + ", ".join(r.key for r in report.failures))
    return GateResult("failure_drills", UNKNOWN,
                      f"{len(report.unknowns)} of {len(DRILLS)} drills have no record "
                      "on this build")


def _test_suites_gate(sha: str) -> GateResult:
    """Derive clean-checkout suite status from exact-SHA GitHub CI records.

    Local developer output is useful diagnostics, but it is not release evidence:
    it can come from a dirty tree, a different interpreter, or another commit. The
    release gate therefore consumes the same SHA-bound GitHub context records as
    remote_ci and selects only the backend/frontend/E2E suite contexts.
    """
    try:
        from app.core.ci_certification import ci_report

        report = ci_report(sha)
    except Exception as exc:  # noqa: BLE001
        return GateResult("test_suites", UNKNOWN, f"CI suite records unavailable: {exc}")

    by_context = report.by_context
    records = [by_context[name] for name in _TEST_SUITE_CONTEXTS]
    failures = [record for record in records if record.conclusion and not record.passed]
    unknowns = [record for record in records if not record.conclusion]

    if failures:
        return GateResult(
            "test_suites",
            FAIL,
            "failed: " + ", ".join(record.context for record in failures),
        )
    if unknowns:
        return GateResult(
            "test_suites",
            UNKNOWN,
            f"{len(unknowns)} of {len(records)} exact-SHA suite contexts have no record: "
            + ", ".join(record.context for record in unknowns),
        )

    runs = ", ".join(sorted({record.run_id for record in records if record.run_id}))
    return GateResult(
        "test_suites",
        PASS,
        f"all {len(records)} backend/frontend/E2E suite contexts succeeded on this SHA"
        + (f" (runs {runs})" if runs else ""),
        attested_by="github-actions",
    )


def _remote_ci_gate(sha: str) -> GateResult:
    """All required CI contexts, recorded against this exact commit.

    Derived rather than attested. "CI was green" cannot say which contexts ran,
    and a green run on another commit is the mistake this replaces.
    """
    try:
        from app.core.ci_certification import REQUIRED_CONTEXTS, ci_report

        report = ci_report(sha)
    except Exception as exc:  # noqa: BLE001
        return GateResult("remote_ci", UNKNOWN, f"CI record store unavailable: {exc}")
    if report.all_passed:
        runs = ", ".join(sorted({r.run_id for r in report.records if r.run_id}))
        return GateResult("remote_ci", PASS,
                          f"all {len(REQUIRED_CONTEXTS)} required contexts succeeded"
                          + (f" (runs {runs})" if runs else ""),
                          attested_by="github-actions")
    if report.failures:
        return GateResult("remote_ci", FAIL,
                          "failed: " + ", ".join(r.context for r in report.failures))
    return GateResult("remote_ci", UNKNOWN,
                      f"{len(report.unknowns)} of {len(REQUIRED_CONTEXTS)} required "
                      "contexts have no record on this commit")


def _open_exposure_gate(snapshot=None) -> GateResult:
    """Derived, not attested: nobody may declare exposure closed that is open.

    Takes the snapshot the caller already read. Reading it twice asks the broker
    twice and lets the gate and the summary line disagree about the same
    account, which is worse than either answer.
    """
    from app.services.exposure_snapshot import exposure_snapshot

    if snapshot is None:
        snapshot = exposure_snapshot(include_broker=False)
    if snapshot.total is None:
        return GateResult("open_exposure", UNKNOWN,
                          snapshot.detail or snapshot.external_detail
                          or "durable exposure could not be read")
    if snapshot.total:
        parts = []
        if snapshot.open_positions:
            parts.append(f"{snapshot.open_positions} open position(s)"
                         + (f" [{', '.join(snapshot.held[:5])}]" if snapshot.held else ""))
        if snapshot.unresolved_intents:
            parts.append(f"{snapshot.unresolved_intents} unresolved intent(s)")
        if snapshot.external_positions:
            # Includes unmanaged broker positions and pending exposure-capable
            # broker automation (orders/GTT/MF/ATO) observed outside local state.
            parts.append(
                f"{snapshot.external_positions} EXTERNAL broker exposure/automation "
                "blocker(s)"
                + (f" [{', '.join(snapshot.external_instruments[:5])}]"
                   if snapshot.external_instruments else ""))
        return GateResult("open_exposure", FAIL, "; ".join(parts))
    return GateResult(
        "open_exposure",
        PASS,
        "no open position, unresolved intent, unmanaged broker position, or pending broker automation",
        attested_by="sterlingctl",
    )


def certification_report(
    *,
    sha: str | None = None,
    tag: str | None = None,
    store: CertificationStore | None = None,
    unresolved_exposure: int | None = None,
    exposure_snapshot_value: Any | None = None,
) -> CertificationReport:
    """Compose the automated checks with whatever has been attested for this SHA."""
    resolved_sha = sha if sha is not None else runtime_sha()
    resolved_tag = tag if tag is not None else release_tag()
    exposure = exposure_snapshot_value
    if exposure is not None:
        # A caller performing a release-critical one-shot operation can inject
        # the fresh broker observation it just obtained. Reuse that exact answer
        # for both the numeric summary and the gate so two reads cannot disagree.
        unresolved_exposure = exposure.total
    elif unresolved_exposure is None:
        # Nothing supplied this, so the gate was permanently UNKNOWN — not
        # because exposure was open but because nobody had read the stores.
        # One read, shared with the gate below.
        from app.services.exposure_snapshot import exposure_snapshot

        # The recorded broker answer, not a fresh call: this report is rendered
        # by an operator dashboard on a timer, and it must not open a broker
        # connection every few seconds. `sterlingctl exposure` refreshes it.
        exposure = exposure_snapshot(include_broker=False)
        unresolved_exposure = exposure.total
    try:
        attested = (store or CertificationStore()).read(resolved_sha)
    except Exception as exc:  # noqa: BLE001
        log.error("certification attestation store unreadable: %s", exc)
        attested = {}

    acceptance = _acceptance_gates(resolved_sha)
    automated = {
        "source_identity": _source_identity(resolved_sha, resolved_tag),
        "production_security": _production_security_gate(),
        "deployment_identity": _deployment_identity_gate(resolved_sha),
        "account_binding": _account_binding_gate(),
        "test_suites": _test_suites_gate(resolved_sha),
        "release_manifest": _release_manifest_gate(),
        "backup_restore": _backup_restore_gate(resolved_sha),
        **acceptance,
        # Derived from the drill register rather than attested as one line: a
        # single "drills passed" cannot say which of the twelve was run, or
        # what the system did when it was.
        "failure_drills": _failure_drills_gate(resolved_sha),
        "open_exposure": _open_exposure_gate(exposure),
        "remote_ci": _remote_ci_gate(resolved_sha),
    }

    results: list[GateResult] = []
    for gate in RELEASE_GATES:
        # An operator attestation never overrides a machine check that FAILs:
        # a human cannot attest that the working tree is clean when it is not.
        auto = automated.get(gate.key)
        if gate.key in _DERIVED_GATES:
            # These are composed from their own per-item registers, so a blanket
            # attestation would be exactly the claim the register exists to
            # replace: "the drills passed", with no record of which ones ran.
            results.append(auto if auto is not None else GateResult(gate.key))
            continue
        if auto is not None and gate.automated:
            # Automated gates are authoritative even when UNKNOWN. A human must
            # never turn "the machine could not verify this" into PASS.
            results.append(auto)
            continue
        if auto is not None and auto.status != UNKNOWN:
            results.append(auto)
            continue
        results.append(attested.get(gate.key, GateResult(gate.key)))

    return CertificationReport(
        sha=resolved_sha,
        tag=resolved_tag,
        results=tuple(results),
        unresolved_exposure=unresolved_exposure,
    )


def render_certification(report: CertificationReport) -> str:
    lines = [
        "RELEASE CERTIFICATION",
        "",
        f"{'sha':<20}{report.sha}",
        f"{'tag':<20}{report.tag}",
        "",
        f"{'gate':<24}{'status':<10}detail",
    ]
    for result in report.results:
        lines.append(f"{result.key:<24}{result.status:<10}{result.detail}")
    lines.append("")
    exposure = report.unresolved_exposure
    lines.append(
        f"{'unresolved exposure':<24}"
        + (str(exposure) if exposure is not None else "UNKNOWN (counts as blocking)")
    )
    lines.append("")
    if report.release_ready:
        lines.append("RELEASE_READY: this SHA may be tagged and deployed to SHADOW production.")
    else:
        lines.append("NOT release ready.")
        if report.failures:
            lines.append("  FAILED:  " + ", ".join(r.key for r in report.failures))
        if report.unknowns:
            lines.append("  UNKNOWN: " + ", ".join(r.key for r in report.unknowns))
        lines.append("  An unrecorded gate is not a passed gate.")
    return "\n".join(lines)
