"""Backup manifests, and the restore proof that makes a backup real.

A backup nobody has restored is a hypothesis. This module turns it into a
checked fact: every backup gets a manifest naming the release it came from and
the exact row counts it contained, and ``prove_restore`` copies it into an
isolated directory, opens it, and compares what came back against that manifest.

The row counts are the part that matters. A file that restores and opens is not
evidence that the evidence survived — a truncated table restores perfectly well.
Comparing counts per table is what catches silent loss, so a restore whose
counts differ from the manifest fails even when every checksum matches.

``restore_test_status`` is deliberately tri-state. ``UNTESTED`` is not a pass,
and it is not the same as ``FAILED``; the handoff gate requires ``PASSED``, so
a backup that was never exercised cannot be mistaken for one that was.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final, Iterable, Mapping, Sequence

from app.core.backup_coverage import Coverage as BackupCoverage
from app.core.backup_coverage import REQUIRED_ARTIFACTS
from app.core.backup_coverage import coverage_report as backup_coverage_report
from app.core.state_paths import state_root
from app.services.snapback_backup import BackupIntegrityError, sha256_file

log = logging.getLogger(__name__)

#: Written relative to the repository root.
BACKUP_MANIFEST_DIR: Final[str] = "data/backups/manifests"

MANIFEST_SCHEMA_VERSION: Final[str] = "1"

#: A release/handoff backup older than this is historical, not current recovery proof.
MAX_BACKUP_AGE: Final[timedelta] = timedelta(days=1)


class RestoreStatus(StrEnum):
    """Whether this backup has ever been proved to restore."""

    #: Never exercised. Not a pass.
    UNTESTED = "UNTESTED"
    PASSED = "PASSED"
    FAILED = "FAILED"


def table_row_counts(db_path: Path | str) -> dict[str, int]:
    """Row count per user table. Ordered, so two manifests compare cleanly."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts: dict[str, int] = {}
        for table in tables:
            # Table names come from sqlite_master, not from a caller, and are
            # quoted anyway; parameters are not permitted in this position.
            counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return counts
    finally:
        conn.close()


def _is_sqlite_sidecar(name: str) -> bool:
    """``foo.db-wal`` and friends describe a connection, not the data."""
    return name.endswith(("-wal", "-shm", "-journal"))


def schema_signature(db_path: Path | str) -> dict[str, str]:
    """The CREATE statement per table, so a schema change is visible as text."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            name: (sql or "")
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        }
    finally:
        conn.close()


@dataclass(frozen=True)
class BackupManifest:
    """Everything needed to judge one backup without opening it."""

    backup_id: str
    created_at: str

    release_sha: str
    release_tag: str

    #: ``{"evidence": "3", "universe": "2", ...}``
    schema_versions: Mapping[str, str]

    #: ``{"snapback_observations.db": "<sha256>"}``, relative to the backup dir.
    file_checksums: Mapping[str, str]

    #: ``{"<file>": {"<table>": count}}``
    row_counts: Mapping[str, Mapping[str, int]]

    #: ``{"<file>": {"<table>": "CREATE TABLE ..."}}``
    schema_signatures: Mapping[str, Mapping[str, str]]

    restore_test_status: RestoreStatus = RestoreStatus.UNTESTED
    restore_tested_at: str | None = None
    restore_failure: str | None = None

    manifest_schema_version: str = MANIFEST_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "release_sha": self.release_sha,
            "release_tag": self.release_tag,
            "schema_versions": dict(self.schema_versions),
            "file_checksums": dict(self.file_checksums),
            "row_counts": {k: dict(v) for k, v in self.row_counts.items()},
            "schema_signatures": {k: dict(v) for k, v in self.schema_signatures.items()},
            "restore_test_status": str(self.restore_test_status),
            "restore_tested_at": self.restore_tested_at,
            "restore_failure": self.restore_failure,
            "manifest_schema_version": self.manifest_schema_version,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BackupManifest":
        return cls(
            backup_id=payload["backup_id"],
            created_at=payload["created_at"],
            release_sha=payload.get("release_sha", "UNKNOWN"),
            release_tag=payload.get("release_tag", "UNKNOWN"),
            schema_versions=payload.get("schema_versions", {}),
            file_checksums=payload.get("file_checksums", {}),
            row_counts=payload.get("row_counts", {}),
            schema_signatures=payload.get("schema_signatures", {}),
            restore_test_status=RestoreStatus(
                payload.get("restore_test_status", RestoreStatus.UNTESTED)
            ),
            restore_tested_at=payload.get("restore_tested_at"),
            restore_failure=payload.get("restore_failure"),
            manifest_schema_version=payload.get(
                "manifest_schema_version", MANIFEST_SCHEMA_VERSION
            ),
        )


def _release_facts() -> tuple[str, str, dict[str, str]]:
    from app.core.release_manifest import release_tag, runtime_sha
    from app.core.strategy_identity import EVIDENCE_SCHEMA_VERSION

    try:
        from app.services.snapback_candidate_universe import UNIVERSE_SCHEMA_VERSION

        universe = str(UNIVERSE_SCHEMA_VERSION)
    except Exception:  # pragma: no cover - defensive
        universe = "UNKNOWN"

    return (
        runtime_sha(),
        release_tag(),
        {"evidence": EVIDENCE_SCHEMA_VERSION, "universe": universe},
    )


def build_backup_manifest(
    backup_dir: Path | str,
    *,
    backup_id: str | None = None,
    created_at: datetime | None = None,
    databases: Sequence[Path | str] | None = None,
) -> BackupManifest:
    """Describe a backup directory: checksums, schemas and row counts per file.

    ``databases`` defaults to every ``*.db`` in the directory, so a backup that
    grows a second database is covered without anybody remembering to add it.
    """
    directory = Path(backup_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"backup directory does not exist: {directory}")

    files = (
        [Path(p) for p in databases]
        if databases is not None
        else sorted(directory.glob("*.db"))
    )
    if not files:
        raise FileNotFoundError(f"no databases found in backup directory {directory}")

    checksums: dict[str, str] = {}
    counts: dict[str, dict[str, int]] = {}
    schemas: dict[str, dict[str, str]] = {}
    for db in files:
        name = db.name
        checksums[name] = sha256_file(db)
        counts[name] = table_row_counts(db)
        schemas[name] = schema_signature(db)

    # Every non-database file that travelled with the backup is checksummed,
    # recursively. Safety state and manifests live under state/.
    for extra in sorted(p for p in directory.rglob("*") if p.is_file()):
        rel = extra.relative_to(directory).as_posix()
        if rel == "manifest.json":
            continue
        if extra.suffix == ".db" or _is_sqlite_sidecar(extra.name):
            continue
        checksums[rel] = sha256_file(extra)

    sha, tag, schema_versions = _release_facts()
    stamp = created_at or datetime.now(timezone.utc)

    return BackupManifest(
        backup_id=backup_id or directory.name,
        created_at=stamp.isoformat(),
        release_sha=sha,
        release_tag=tag,
        schema_versions=schema_versions,
        file_checksums=checksums,
        row_counts=counts,
        schema_signatures=schemas,
    )


#: Databases a full backup must contain, resolved from the environment.
#: ``STERLING_BACKUP_DATABASES`` overrides it with a ``:``-separated list.
#:
#: Section 14.1 asks for evidence AND runtime databases. Backing up only the
#: evidence store leaves the durable intent record — the thing restart recovery
#: reads to decide whether an order was ever sent — outside the backup.
DEFAULT_DATABASE_ENV: Final[str] = "STERLING_BACKUP_DATABASES"


def declared_databases(root: Path | str | None = None) -> tuple[Path, ...]:
    """Every database a full backup should contain, in a stable order.

    Missing paths stay in the declaration so :func:`create_full_backup` can
    report them explicitly instead of silently shrinking the backup set.  Two
    different source databases may not share a basename: snapshots are stored
    by basename inside one backup directory, so accepting that configuration
    would overwrite one database with the other.
    """
    import os

    base = Path(root) if root is not None else state_root()

    declared = (os.environ.get(DEFAULT_DATABASE_ENV) or "").strip()
    if declared:
        candidates = [Path(part) for part in declared.split(":") if part.strip()]
    else:
        # Environment-resolved runtime paths are authoritative.  A deployment
        # may deliberately separate immutable source from persistent state; a
        # backup that falls back to repository-era backend/*.db paths would
        # silently omit the database the running service is actually using.
        persistent_layout = bool(
            (os.environ.get("STERLING_STATE_ROOT") or "").strip()
        )
        evidence = (
            os.environ.get("STERLING_EVIDENCE_DB")
            or os.environ.get("STERLING_OBSERVATIONS_DB_PATH")
            or str(
                base
                / (
                    "data/snapback_observations.db"
                    if persistent_layout
                    else "backend/snapback_observations.db"
                )
            )
        )
        runtime = (
            os.environ.get("STERLING_DB_PATH")
            or str(
                base
                / (
                    "data/sterling_paper.db"
                    if persistent_layout
                    else "backend/sterling_paper.db"
                )
            )
        )
        engine = (
            os.environ.get("STERLING_KITE_ENGINE_DB")
            or str(
                base
                / (
                    "data/kite_engine.db"
                    if persistent_layout
                    else "backend/kite_engine.db"
                )
            )
        )
        intent = (
            os.environ.get("STERLING_INTENT_DB")
            or str(base / "data/intents.db")
        )
        candidates = [
            Path(evidence),
            Path(runtime),
            # The SuperTrend engine's own durable state: its position registry
            # projection and config. Left out, a restore brings back the
            # evidence and forgets what the engine was holding.
            Path(engine),
            Path(intent),
        ]

    seen: dict[str, Path] = {}
    for path in candidates:
        expanded = path.expanduser()
        canonical = expanded.resolve(strict=False)
        prior = seen.get(expanded.name)
        if prior is not None:
            if prior.resolve(strict=False) != canonical:
                raise BackupIntegrityError(
                    "declared backup databases collide by basename: "
                    f"{prior} and {expanded} would both become {expanded.name}"
                )
            continue
        seen[expanded.name] = expanded
    return tuple(seen.values())


def snapshot_database(source: Path | str, destination: Path | str) -> str:
    """Atomically snapshot an existing SQLite DB, without creating its source.

    A unique temporary file prevents concurrent attempts from deleting or
    interleaving each other's partial backups. Failures remove the temporary
    snapshot and leave any previously committed destination unchanged.
    """
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise FileNotFoundError(f"snapshot source database does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    source_uri = source.resolve(strict=True).as_uri()
    try:
        src = sqlite3.connect(f"{source_uri}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        # A WAL database can require a read-write connection to attach shared
        # memory. mode=rw refuses absent files; bare connect() would CREATE one.
        src = sqlite3.connect(f"{source_uri}?mode=rw", uri=True)

    try:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
        )
    except BaseException:
        src.close()
        raise
    os.close(fd)
    partial = Path(temporary)
    try:
        try:
            dst = sqlite3.connect(str(partial))
            try:
                src.backup(dst)
                integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                dst.close()
        finally:
            src.close()

        if integrity != "ok":
            raise BackupIntegrityError(
                f"snapshot of {source} failed integrity_check: {integrity}"
            )

        partial.replace(destination)
        # The closed snapshot connection's sidecars are not backup artifacts.
        for sidecar in ("-wal", "-shm", "-journal"):
            Path(str(destination) + sidecar).unlink(missing_ok=True)
        return sha256_file(destination)
    finally:
        partial.unlink(missing_ok=True)
        for sidecar in ("-wal", "-shm", "-journal"):
            Path(str(partial) + sidecar).unlink(missing_ok=True)


@dataclass(frozen=True)
class FullBackup:
    """Where a full backup landed, and what it could not include."""

    directory: Path
    manifest: "BackupManifest"
    databases: tuple[str, ...]
    missing: tuple[str, ...] = field(default_factory=tuple)
    #: Non-database artifacts copied into the backup, by declared name.
    captured: tuple[str, ...] = field(default_factory=tuple)
    #: Artifacts recorded by checksum only — the deployment configuration, which
    #: holds credentials and must not travel in a file people copy around.
    referenced: tuple[str, ...] = field(default_factory=tuple)
    #: Artifacts that exist on this host and were NOT captured. Always a defect.
    gaps: tuple[str, ...] = field(default_factory=tuple)


def create_full_backup(
    *,
    backup_root: Path | str,
    root: Path | str | None = None,
    databases: Sequence[Path | str] | None = None,
    now: datetime | None = None,
) -> FullBackup:
    """A backup that contains what section 14.1 asks for.

    Databases, the release manifest and the schema versions travel together, so
    a restored directory can say which build wrote it without reference to the
    repository it came from.
    """
    from app.core.release_manifest import build_release_manifest

    base = Path(root) if root is not None else state_root()
    stamp = now or datetime.now(timezone.utc)
    # A full backup is immutable evidence for one run. Date-only directories let
    # a second backup on the same day inherit files from the first run when a
    # source disappears, producing a mixed-generation artifact. Keep scheduled
    # augmentation's date directory separate; full backups get unique sortable
    # UTC timestamps.
    stamp_utc = stamp.astimezone(timezone.utc)
    backup_id = stamp_utc.strftime("%Y-%m-%dT%H%M%S.%fZ")

    requested = (
        [Path(p) for p in databases] if databases is not None else list(declared_databases(base))
    )
    # An explicit database override must observe the SAME basename collision
    # rule as declared_databases(). Otherwise two distinct sources named
    # sterling_paper.db (or any same name) silently overwrite one snapshot.
    unique: dict[str, Path] = {}
    for source in requested:
        existing = unique.get(source.name)
        if existing is not None:
            if existing.expanduser().resolve(strict=False) != source.expanduser().resolve(strict=False):
                raise BackupIntegrityError(
                    "backup database basename collision: "
                    f"{existing} and {source} both snapshot as {source.name}"
                )
            continue
        unique[source.name] = source
    requested = list(unique.values())
    # Validate before creating a backup directory, so a rejected declaration
    # does not leave a misleading partial backup artifact on disk.
    directory = Path(backup_root) / backup_id
    directory.mkdir(parents=True, exist_ok=False)

    missing = [str(p) for p in requested if not Path(p).exists()]
    present = [Path(p) for p in requested if Path(p).exists()]

    if not present:
        raise FileNotFoundError(
            f"no database to back up; requested: {', '.join(map(str, requested)) or 'none'}"
        )

    for source in present:
        snapshot_database(source, directory / source.name)

    # The release identity travels with the bytes. Restoring a backup and then
    # asking the repository what build wrote it gives the CURRENT build, which
    # is exactly the wrong answer.
    (directory / "release.json").write_text(
        json.dumps(build_release_manifest(), indent=2, sort_keys=True) + "\n"
    )

    # Section 14 asks for more than databases: the safety state, the lane and
    # release manifests, the shadow records. A backup that restores the numbers
    # and loses the system is not a backup of the system.
    captured, referenced = _capture_declared_artifacts(base, directory)

    statuses = backup_coverage_report(
        base,
        captured=[*captured, *_database_artifact_names(base, present)],
        referenced=referenced,
    )
    gaps = tuple(s.artifact.name for s in statuses if s.coverage is BackupCoverage.GAP)
    (directory / "coverage.json").write_text(
        json.dumps([s.as_dict() for s in statuses], indent=2, sort_keys=True) + "\n"
    )

    # Coverage decides whether a restore is complete, so it must be part of the
    # checksummed bytes rather than being written after the manifest.
    manifest = build_backup_manifest(
        directory,
        created_at=stamp,
        databases=[directory / source.name for source in present],
    )
    write_backup_manifest(manifest, root=base, backup_dir=directory)

    return FullBackup(
        directory=directory,
        manifest=manifest,
        databases=tuple(source.name for source in present),
        missing=tuple(missing),
        captured=tuple(captured),
        referenced=tuple(referenced),
        gaps=gaps,
    )


#: Where a copied artifact lands inside the backup directory.
ARTIFACT_SUBDIR: Final[str] = "state"


def _database_artifact_names(root: Path, sources: Iterable[Path]) -> list[str]:
    """Map snapshotted databases to declarations by canonical source path.

    Filename similarity alone is not identity. A caller can explicitly back
    up another file named sterling_paper.db while omitting the actual intent
    journal. That file must NOT satisfy canonical_intent_journal coverage.
    Environment overrides already resolve through resolve_artifacts().
    """
    from app.core.backup_coverage import ArtifactKind, resolve_artifacts

    declared = [
        (artifact, path)
        for artifact, path in resolve_artifacts(root)
        if artifact.kind is ArtifactKind.DATABASE and path is not None
    ]
    names: list[str] = []
    for source in sources:
        resolved = Path(source).expanduser().resolve(strict=False)
        match = next(
            (
                a.name
                for a, p in declared
                if p.expanduser().resolve(strict=False) == resolved
            ),
            None,
        )
        if match:
            names.append(match)
    return names


def _capture_declared_artifacts(
    root: Path, directory: Path
) -> tuple[list[str], list[str]]:
    """Copy every declared non-database artifact into ``directory``/state.

    Returns the names copied and the names recorded by reference. A copy that
    fails is left out of both, so it shows up as a GAP in the coverage report
    rather than being silently counted as present.
    """
    import shutil

    from app.core.backup_coverage import ArtifactKind, resolve_artifacts

    state_dir = directory / ARTIFACT_SUBDIR
    # A same-day backup directory may already contain an earlier capture. Never
    # let files that disappeared from the source survive into the new manifest:
    # stale safety/manifests are worse than an explicit GAP/ABSENT verdict.
    if state_dir.exists():
        shutil.rmtree(state_dir)
    captured: list[str] = []
    referenced: list[str] = []

    for artifact, path in resolve_artifacts(root):
        if path is None or not path.exists():
            continue
        if artifact.kind is ArtifactKind.DATABASE:
            continue  # snapshotted separately, with SQLite's own backup API
        if artifact.kind is ArtifactKind.REFERENCED:
            # Checksum only. The contents hold credentials and must not travel.
            digest = sha256_file(path)
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / f"{artifact.name}.sha256").write_text(
                f"{digest}  {path}\n", encoding="utf-8")
            referenced.append(artifact.name)
            continue
        try:
            # A directory artifact may contain symlinks to *outside* the
            # declared state tree (including operator secrets). copytree's
            # default follows those links, which would leak their contents
            # into an exportable backup. Reject the whole artifact instead
            # of silently including external files or calling it complete.
            if path.is_symlink():
                raise BackupIntegrityError(
                    f"artifact root is a symlink: {artifact.name}"
                )
            if artifact.kind is ArtifactKind.DIRECTORY:
                symlinks = [p for p in path.rglob("*") if p.is_symlink()]
                if symlinks:
                    raise BackupIntegrityError(
                        f"directory artifact contains symlink(s): {artifact.name}"
                    )
            destination = state_dir / artifact.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if artifact.kind is ArtifactKind.DIRECTORY:
                # Even if the tree changes after preflight, copytree must never
                # dereference a newly introduced symlink to outside data.
                shutil.copytree(
                    path, destination, dirs_exist_ok=True, symlinks=True
                )
            else:
                shutil.copyfile(path, destination.with_suffix(path.suffix))
        except Exception as exc:  # noqa: BLE001
            log.warning("backup: could not capture %s (%s): %s", artifact.name, path, exc)
            continue
        captured.append(artifact.name)

    return captured, referenced



def augment_backup_directory(
    directory: Path | str,
    *,
    root: Path | str | None = None,
    already_included: Sequence[Path | str] = (),
    now: datetime | None = None,
) -> FullBackup:
    """Bring an existing backup directory up to the section 14.1 contents.

    The scheduled backup snapshots the evidence database and verifies its
    checksum. That path is left exactly as it is — it is the one that has been
    proved — and this adds what section 14.1 also asks for: the remaining
    declared databases and the release identity that wrote them.

    Additive by design. A failure here must never invalidate an evidence backup
    that already succeeded, so the caller records it as an error and keeps the
    backup.
    """
    from app.core.release_manifest import build_release_manifest

    # The runtime's durable data may live on a separate mounted state root.
    # repo_root() points at immutable application source, not that volume.
    base = Path(root) if root is not None else state_root()
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)

    # Only snapshots the caller actually re-verified in THIS operation may be
    # reused. In particular, do not trust stale *.db files from a prior daily
    # augmentation just because they have the right filenames.
    included_names: set[str] = set()
    for supplied in already_included:
        candidate = Path(supplied)
        if (
            not candidate.is_file()
            or candidate.is_symlink()
            or candidate.parent.resolve() != target.resolve()
            or candidate.suffix != ".db"
        ):
            raise BackupIntegrityError(
                f"already_included must be a verified snapshot within {target}: {candidate}"
            )
        included_names.add(candidate.name)

    requested = list(declared_databases(base))
    missing = [str(p) for p in requested if not Path(p).exists()]
    added = [p for p in requested if p.exists() and p.name not in included_names]

    for source in added:
        snapshot_database(source, target / source.name)

    # If a declared database existed in an earlier same-day augmentation but is
    # missing now, remove that stale snapshot. Keeping it would make a restore
    # look complete using bytes that no longer describe the source host.
    for source in requested:
        if source.exists():
            included_names.add(source.name)
        elif source.name not in included_names:
            (target / source.name).unlink(missing_ok=True)

    (target / "release.json").write_text(
        json.dumps(build_release_manifest(), indent=2, sort_keys=True) + "\n"
    )

    captured, referenced = _capture_declared_artifacts(base, target)
    statuses = backup_coverage_report(
        base,
        captured=[*captured, *_database_artifact_names(
            base, [p for p in requested if p.exists()]
        )],
        referenced=referenced,
    )
    gaps = tuple(
        status.artifact.name
        for status in statuses
        if status.coverage is BackupCoverage.GAP
    )
    (target / "coverage.json").write_text(
        json.dumps([s.as_dict() for s in statuses], indent=2, sort_keys=True) + "\n"
    )

    manifest_databases = [
        target / name
        for name in sorted(included_names)
        if (target / name).exists()
    ]
    manifest = build_backup_manifest(
        target,
        created_at=now or datetime.now(timezone.utc),
        databases=manifest_databases,
    )
    write_backup_manifest(manifest, root=base, backup_dir=target)

    return FullBackup(
        directory=target,
        manifest=manifest,
        databases=tuple(p.name for p in manifest_databases),
        missing=tuple(missing),
        captured=tuple(captured),
        referenced=tuple(referenced),
        gaps=gaps,
    )


def manifest_path(
    manifest: BackupManifest, root: Path | str | None = None
) -> Path:
    # Manifests describe durable state and therefore travel with state_root,
    # not necessarily with immutable application source.
    base = Path(root) if root is not None else state_root()
    return base / BACKUP_MANIFEST_DIR / f"{manifest.backup_id}.json"


def write_backup_manifest(
    manifest: BackupManifest,
    *,
    root: Path | str | None = None,
    backup_dir: Path | str | None = None,
) -> Path:
    """Write the manifest beside the backup and into the manifest directory."""
    path = manifest_path(manifest, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n"
    path.write_text(body)
    if backup_dir is not None:
        (Path(backup_dir) / "manifest.json").write_text(body)
    return path


def read_backup_manifest(path: Path | str) -> BackupManifest:
    return BackupManifest.from_dict(json.loads(Path(path).read_text()))


@dataclass(frozen=True)
class RestoreProof:
    """The outcome of actually restoring a backup into an isolated location."""

    backup_id: str
    passed: bool
    checked_at: str
    failures: tuple[str, ...] = field(default_factory=tuple)
    restored_row_counts: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "passed": self.passed,
            "checked_at": self.checked_at,
            "failures": list(self.failures),
            "restored_row_counts": {
                k: dict(v) for k, v in self.restored_row_counts.items()
            },
        }



def _safe_restore_member(
    name: str, source_root: Path, destination_root: Path
) -> tuple[Path, Path] | None:
    """Resolve a manifest member strictly inside the backup and restore roots.

    Manifest paths are data, not trusted filesystem instructions. In particular,
    a recorded relative path must never write outside an isolated restore via
    '..', an absolute path, or a symlink supplied inside either directory.
    """
    if not isinstance(name, str) or not name or "\\" in name:
        return None
    relative = PurePosixPath(name)
    if (
        relative.is_absolute()
        or any(part in ("", ".", "..") for part in name.split("/"))
        or relative.as_posix() != name
    ):
        return None
    source = source_root.joinpath(*relative.parts)
    destination = destination_root.joinpath(*relative.parts)
    try:
        if source.is_symlink() or destination.is_symlink():
            return None
        if not source.resolve(strict=False).is_relative_to(source_root.resolve()):
            return None
        if not destination.resolve(strict=False).is_relative_to(destination_root.resolve()):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return source, destination


def prove_restore(
    backup_dir: Path | str,
    manifest: BackupManifest | None = None,
    *,
    destination: Path | str | None = None,
) -> RestoreProof:
    """Restore into an isolated directory and check it against the manifest.

    Nothing in the live tree is touched: the copy lands in a temporary
    directory that is removed afterwards unless ``destination`` is given.
    """
    directory = Path(backup_dir)
    # The original stored manifest is mandatory for operational restore proof.
    # Recalculating today's checksums from a directory with no provenance
    # would incorrectly describe lost/corrupted historical evidence as valid.
    try:
        stored = manifest if manifest is not None else _load_or_build(directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return RestoreProof(
            backup_id=directory.name,
            passed=False,
            checked_at=datetime.now(timezone.utc).isoformat(),
            failures=(f"backup manifest unavailable or invalid: {exc}",),
        )
    failures: list[str] = []
    restored_counts: dict[str, dict[str, int]] = {}
    for required in ("release.json", "coverage.json"):
        if required not in stored.file_checksums:
            failures.append(f"{required}: missing checksum evidence")

    own_tmp = destination is None
    target = Path(destination) if destination is not None else Path(
        tempfile.mkdtemp(prefix="sterling-restore-check-")
    )

    try:
        # Every physical artifact (except the self-describing manifest and
        # disposable SQLite sidecars) must be named in the stored checksum set.
        # Otherwise an incomplete or mixed-generation backup can appear valid
        # merely because unlisted files are never checked.
        for member in directory.rglob("*"):
            if member.is_symlink():
                failures.append(
                    f"{member.relative_to(directory).as_posix()}: symlink in backup"
                )
                continue
            if not member.is_file():
                continue
            relative_name = member.relative_to(directory).as_posix()
            if relative_name == "manifest.json" or _is_sqlite_sidecar(member.name):
                continue
            if relative_name not in stored.file_checksums:
                failures.append(
                    f"{relative_name}: backup file missing checksum evidence"
                )
        # A mutable manifest must not be able to make a database disappear from
        # the verification surface. Every database physically present in the
        # backup is required to carry checksum, row-count and schema evidence.
        present_databases = sorted(p.name for p in directory.glob("*.db"))
        for db_name in present_databases:
            if db_name not in stored.file_checksums:
                failures.append(f"{db_name}: database missing checksum evidence")
            if db_name not in stored.row_counts:
                failures.append(f"{db_name}: database missing row-count evidence")
            if db_name not in stored.schema_signatures:
                failures.append(f"{db_name}: database missing schema evidence")

        for name, expected_sum in stored.file_checksums.items():
            safe_paths = _safe_restore_member(name, directory, target)
            if safe_paths is None:
                failures.append(f"{name}: unsafe backup member path")
                continue
            source, copy = safe_paths
            if not source.is_file():
                failures.append(f"{name}: missing or non-file backup member")
                continue
            try:
                actual_sum = sha256_file(source)
            except (OSError, ValueError) as exc:
                failures.append(f"{name}: cannot checksum backup member: {exc}")
                continue
            if actual_sum != expected_sum:
                failures.append(
                    f"{name}: checksum mismatch "
                    f"(manifest {str(expected_sum)[:16]}, file {actual_sum[:16]})"
                )
                continue

            try:
                copy.parent.mkdir(parents=True, exist_ok=True)
                # Recheck containment after mkdir: explicit restore destinations
                # can have pre-existing symlinks within their directory tree.
                if _safe_restore_member(name, directory, target) is None:
                    failures.append(f"{name}: unsafe restore destination")
                    continue
                shutil.copyfile(source, copy)
                restored_sum = sha256_file(copy)
                if restored_sum != actual_sum:
                    failures.append(
                        f"{name}: isolated restore copy checksum mismatch"
                    )
                    continue
            except (OSError, ValueError) as exc:
                failures.append(f"{name}: cannot copy into isolated restore: {exc}")
                continue

            if name not in stored.row_counts:
                continue

            try:
                conn = sqlite3.connect(str(copy))
                try:
                    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                failures.append(f"{name}: cannot be opened after restore: {exc}")
                continue

            if integrity != "ok":
                failures.append(f"{name}: integrity_check returned {integrity!r}")
                continue

            actual_counts = table_row_counts(copy)
            restored_counts[name] = actual_counts
            failures.extend(
                _count_failures(name, stored.row_counts[name], actual_counts)
            )

            expected_schema = stored.schema_signatures.get(name)
            if expected_schema is not None:
                actual_schema = schema_signature(copy)
                for table, sql in expected_schema.items():
                    if actual_schema.get(table) != sql:
                        failures.append(f"{name}.{table}: schema differs after restore")
        # The manifest's release identity is metadata and manifest.json itself is
        # intentionally not included in its own checksum set. Bind that metadata
        # back to the checksummed release artifact so a stale/mis-copied manifest
        # cannot make a backup from another build look like this candidate.
        release_record = directory / "release.json"
        if not release_record.exists():
            failures.append("release.json: missing from backup")
        else:
            try:
                release_payload = json.loads(release_record.read_text(encoding="utf-8"))
                release_build = release_payload.get("build") or {}
            except Exception as exc:  # noqa: BLE001
                failures.append(f"release.json: unreadable ({exc})")
            else:
                actual_sha = str(release_build.get("runtime_sha") or "")
                actual_tag = str(release_build.get("release_tag") or "")
                if actual_sha != str(stored.release_sha or ""):
                    failures.append(
                        "release.json: runtime_sha does not match backup manifest "
                        f"({actual_sha or 'UNKNOWN'} != {stored.release_sha or 'UNKNOWN'})"
                    )
                if actual_tag != str(stored.release_tag or ""):
                    failures.append(
                        "release.json: release_tag does not match backup manifest "
                        f"({actual_tag or 'UNKNOWN'} != {stored.release_tag or 'UNKNOWN'})"
                    )

        # A frozen release is not just one JSON file: the actual per-lane
        # artifacts must be the exact projection of that frozen contract.
        # Checksums only prove bytes have not changed since backup creation;
        # they cannot prove that an empty or stale lane directory was correct
        # when those checksums were recorded.
        frozen_copy = directory / ARTIFACT_SUBDIR / "release_manifest.json"
        if frozen_copy.exists():
            try:
                frozen = json.loads(frozen_copy.read_text(encoding="utf-8"))
                if not isinstance(frozen, dict):
                    raise ValueError("frozen release manifest must be a JSON object")
                frozen_build = frozen.get("build") or {}
                if not isinstance(frozen_build, dict):
                    raise ValueError("frozen release build must be a JSON object")
                frozen_lanes = frozen.get("lanes")
                frozen_lane_count = frozen.get("lane_count")
                if not isinstance(frozen_lanes, list) or not frozen_lanes:
                    raise ValueError("frozen release has no declared strategy lanes")
                if (
                    type(frozen_lane_count) is not int
                    or frozen_lane_count != len(frozen_lanes)
                ):
                    raise ValueError(
                        "frozen lane_count does not match declared strategy lanes"
                    )
                if str(frozen_build.get("runtime_sha") or "") != str(stored.release_sha or ""):
                    failures.append(
                        "frozen release_manifest: runtime_sha differs from backup identity"
                    )
                if str(frozen_build.get("release_tag") or "") != str(stored.release_tag or ""):
                    failures.append(
                        "frozen release_manifest: release_tag differs from backup identity"
                    )
                from app.core.release_manifest import lane_manifest_file_drift

                lane_problems = lane_manifest_file_drift(
                    frozen,
                    lane_directory=directory / ARTIFACT_SUBDIR / "lane_manifests",
                )
                failures.extend(
                    f"frozen lane manifests: {problem}" for problem in lane_problems
                )
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                failures.append(f"frozen release_manifest: invalid ({exc})")

        # A restore that verifies only what the backup happens to contain can
        # pass while the backup is missing half the system. The coverage record
        # written at backup time is what makes that visible here.
        coverage_file = directory / "coverage.json"
        if not coverage_file.exists():
            failures.append(
                "coverage.json: missing, so this backup cannot say which "
                "artifacts it was supposed to contain")
        else:
            try:
                rows = json.loads(coverage_file.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                failures.append(f"coverage.json: unreadable ({exc})")
            else:
                if not isinstance(rows, list):
                    failures.append("coverage.json: expected a list of artifact rows")
                    rows = []

                declared = {artifact.name: artifact for artifact in REQUIRED_ARTIFACTS}
                names = [
                    str(row.get("name") or "")
                    for row in rows
                    if isinstance(row, dict)
                ]
                duplicates = sorted({name for name in names if name and names.count(name) > 1})
                if duplicates:
                    failures.append(
                        "coverage.json: duplicate artifact rows: " + ", ".join(duplicates)
                    )
                missing = sorted(set(declared) - set(names))
                if missing:
                    failures.append(
                        "coverage.json: missing required artifact declarations: "
                        + ", ".join(missing)
                    )
                unknown = sorted(set(names) - set(declared))
                if unknown:
                    failures.append(
                        "coverage.json: unknown artifact declarations: "
                        + ", ".join(unknown)
                    )

                if any(
                    not isinstance(row, dict) or "required_before_live" not in row
                    for row in rows
                ):
                    failures.append(
                        "coverage.json: missing required_before_live metadata; "
                        "take a fresh backup with this release"
                    )

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    name = str(row.get("name") or "")
                    artifact = declared.get(name)
                    if artifact is None:
                        continue
                    if row.get("required_before_live") is not artifact.required_before_live:
                        failures.append(
                            f"coverage.json: {name} required_before_live disagrees "
                            "with this release"
                        )
                    if str(row.get("kind") or "") != artifact.kind.value:
                        failures.append(
                            f"coverage.json: {name} kind disagrees with this release"
                        )

                    coverage = str(row.get("coverage") or "")
                    is_reference_kind = artifact.kind.value == "referenced"
                    if coverage == BackupCoverage.REFERENCED.value and not is_reference_kind:
                        failures.append(
                            f"coverage.json: {name} cannot be REFERENCED; "
                            "this release requires its bytes to be backed up"
                        )
                    if coverage == BackupCoverage.COPIED.value and is_reference_kind:
                        failures.append(
                            f"coverage.json: {name} is reference-only and must not "
                            "claim copied secret-bearing configuration"
                        )
                    if coverage == BackupCoverage.REFERENCED.value:
                        marker = directory / ARTIFACT_SUBDIR / f"{name}.sha256"
                        marker_rel = marker.relative_to(directory).as_posix()
                        if not marker.exists():
                            failures.append(
                                f"{name}: recorded as referenced but checksum marker is missing"
                            )
                        elif marker_rel not in stored.file_checksums:
                            failures.append(
                                f"{name}: referenced checksum marker is not covered by "
                                "the backup manifest"
                            )

                valid_coverage = {item.value for item in BackupCoverage}
                for row in rows:
                    if not isinstance(row, dict):
                        failures.append("coverage.json: artifact row must be an object")
                        continue
                    coverage = str(row.get("coverage") or "")
                    if coverage not in valid_coverage:
                        failures.append(
                            f"coverage.json: {row.get('name') or '<unnamed>'} has "
                            f"unknown coverage state {coverage or '<blank>'}"
                        )

                gaps = [
                    str(row.get("name") or "")
                    for row in rows
                    if isinstance(row, dict)
                    and row.get("coverage") == BackupCoverage.GAP.value
                ]
                if gaps:
                    failures.append(
                        "artifacts existed and were not backed up: " + ", ".join(sorted(gaps)))
                absent_required = [
                    str(row.get("name") or "")
                    for row in rows
                    if isinstance(row, dict)
                    and row.get("coverage") == BackupCoverage.ABSENT.value
                    and row.get("required_before_live") is True
                ]
                if absent_required:
                    failures.append(
                        "required artifacts did not exist on the source host: "
                        + ", ".join(sorted(absent_required))
                    )
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if row.get("coverage") != BackupCoverage.COPIED.value:
                        continue
                    name = str(row.get("name") or "")
                    # A database travels under its own file name at the top of
                    # the backup; everything else under state/<artifact name>.
                    filename = Path(row["path"]).name if row.get("path") else ""
                    candidates: list[Path] = []
                    top_level = directory / filename if filename else None
                    if top_level is not None and top_level.exists():
                        candidates = [top_level]
                    else:
                        state_dir = directory / ARTIFACT_SUBDIR
                        exact = state_dir / name
                        if exact.exists():
                            candidates = [exact]
                        else:
                            candidates = list(state_dir.glob(f"{name}.*"))

                    if not candidates:
                        failures.append(
                            f"{name}: recorded as copied but not present in the backup")
                        continue

                    # Presence alone is not evidence: every copied file must be
                    # content-addressed by the manifest. Directories are expanded
                    # recursively; an empty copied directory has no bytes to hash.
                    copied_files: list[Path] = []
                    for candidate in candidates:
                        if candidate.is_file():
                            copied_files.append(candidate)
                        elif candidate.is_dir():
                            copied_files.extend(
                                p for p in candidate.rglob("*") if p.is_file()
                            )
                    if name == "lane_manifests" and not copied_files:
                        failures.append(
                            "lane_manifests: frozen lane directory contains no files"
                        )
                    for copied in copied_files:
                        rel = copied.relative_to(directory).as_posix()
                        if rel not in stored.file_checksums:
                            failures.append(
                                f"{name}: copied artifact is not checksummed: {rel}")
    finally:
        if own_tmp:
            shutil.rmtree(target, ignore_errors=True)

    return RestoreProof(
        backup_id=stored.backup_id,
        passed=not failures,
        checked_at=datetime.now(timezone.utc).isoformat(),
        failures=tuple(failures),
        restored_row_counts=restored_counts,
    )


def _count_failures(
    name: str,
    expected: Mapping[str, int],
    actual: Mapping[str, int],
) -> Iterable[str]:
    for table in sorted(set(expected) | set(actual)):
        want = expected.get(table)
        got = actual.get(table)
        if want is None:
            yield f"{name}.{table}: table appeared after restore ({got} rows)"
        elif got is None:
            yield f"{name}.{table}: table missing after restore (expected {want} rows)"
        elif want != got:
            yield f"{name}.{table}: {got} rows restored, manifest recorded {want}"


def _load_or_build(directory: Path) -> BackupManifest:
    """Load original backup provenance; never certify reconstructed metadata."""
    beside = directory / "manifest.json"
    if beside.is_symlink():
        raise ValueError("manifest.json: symlink provenance is not permitted")
    if not beside.is_file():
        raise FileNotFoundError(
            f"manifest.json: missing original backup manifest at {beside}"
        )
    return read_backup_manifest(beside)


def record_restore_proof(
    manifest: BackupManifest, proof: RestoreProof
) -> BackupManifest:
    """Return the manifest with this backup's own restore verdict stamped on it."""
    if str(proof.backup_id) != str(manifest.backup_id):
        raise ValueError(
            "restore_proof_backup_mismatch:"
            f"{proof.backup_id}!={manifest.backup_id}"
        )
    if proof.passed and proof.failures:
        raise ValueError("restore_proof_pass_contains_failures")
    if not str(proof.checked_at or "").strip():
        raise ValueError("restore_proof_checked_at_missing")
    return BackupManifest(
        backup_id=manifest.backup_id,
        created_at=manifest.created_at,
        release_sha=manifest.release_sha,
        release_tag=manifest.release_tag,
        schema_versions=manifest.schema_versions,
        file_checksums=manifest.file_checksums,
        row_counts=manifest.row_counts,
        schema_signatures=manifest.schema_signatures,
        restore_test_status=RestoreStatus.PASSED if proof.passed else RestoreStatus.FAILED,
        restore_tested_at=proof.checked_at,
        restore_failure="; ".join(proof.failures) or None,
        manifest_schema_version=manifest.manifest_schema_version,
    )


def latest_backup_dir(backup_root: Path | str) -> Path | None:
    """The newest backup by recorded creation time, independent of directory naming.

    Full backups use timestamped immutable directories while the scheduled
    augmentation path may still use a date-only directory. Lexicographic path
    ordering cannot compare those two formats correctly, so prefer the
    manifest's ``created_at`` and fall back to filesystem mtime only when the
    manifest itself cannot be read.
    """
    root = Path(backup_root)
    if not root.is_dir():
        return None
    candidates = [p for p in root.iterdir() if p.is_dir() and any(p.glob("*.db"))]
    if not candidates:
        return None

    def _created_key(directory: Path) -> float:
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = read_backup_manifest(manifest_path)
                created = datetime.fromisoformat(str(manifest.created_at))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                return created.timestamp()
            except Exception:  # noqa: BLE001
                pass
        try:
            return directory.stat().st_mtime
        except OSError:
            return float("-inf")

    return max(candidates, key=_created_key)


def render_restore_proof(proof: RestoreProof) -> str:
    """Operator-readable result. The failure list is the whole point."""
    head = f"restore-check {proof.backup_id}: {'PASSED' if proof.passed else 'FAILED'}"
    if proof.passed:
        total = sum(sum(c.values()) for c in proof.restored_row_counts.values())
        return f"{head}\n{total} rows restored and verified across " \
               f"{len(proof.restored_row_counts)} database(s)."
    return "\n".join([head, *(f"  - {f}" for f in proof.failures)])


__all__ = [
    "BACKUP_MANIFEST_DIR",
    "MAX_BACKUP_AGE",
    "BackupManifest",
    "RestoreProof",
    "RestoreStatus",
    "augment_backup_directory",
    "build_backup_manifest",
    "create_full_backup",
    "declared_databases",
    "latest_backup_dir",
    "manifest_path",
    "prove_restore",
    "read_backup_manifest",
    "record_restore_proof",
    "render_restore_proof",
    "schema_signature",
    "snapshot_database",
    "table_row_counts",
    "write_backup_manifest",
]
