"""Release certification: the gate table of §5, recorded against one exact SHA.

Certification is evidence about a build, not about a repository. The moment a
line of code changes, the SHA changes and every attestation recorded against
the old one stops applying — not "mostly applies", stops. That rule is the
entire reason this module keys everything by SHA and refuses to inherit.

Some gates the software can check for itself (is the tree clean, does the
frozen manifest match what is running, did the last restore-check pass). The
rest are human observations — a moving-market Kite acceptance, a forced
reconnect, a CI run on a remote — and those are recorded as attestations
naming who observed them and where the artifact lives. An unrecorded gate is
UNKNOWN, and UNKNOWN is not a pass.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
    Gate("remote_ci", "all required protected checks green on the exact SHA", "release", False),
    Gate("test_suites", "backend, frontend and E2E suites PASS on a clean checkout", "release", False),
    Gate("kite_live_acceptance", "engineering PASS against a moving market", "release", False),
    Gate("reconnect", "forced disconnect -> observed close -> reconnect -> fresh ticks -> FULL depth", "release", False),
    Gate("persistence", "isolated run, reload and evidence checks PASS", "release", False),
    Gate("release_manifest", "the frozen manifest matches the running build and all lanes", "authoritative evidence", True),
    Gate("backup_restore", "fresh backup plus an isolated restore-check PASS", "handoff", True),
    Gate("failure_drills", "every capital-critical drill PASS", "unattended operation", False),
    Gate("open_exposure", "zero unresolved or unknown exposure before freeze", "release", False),
)

_GATES_BY_KEY: Final[Mapping[str, Gate]] = {g.key: g for g in RELEASE_GATES}

#: Gates composed from a per-item register rather than attested in one line.
#: `certify attest` refuses these: the register is the attestation, item by item.
_DERIVED_GATES: Final[frozenset[str]] = frozenset(
    {"failure_drills", "open_exposure", "remote_ci"}
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
    configured = os.environ.get("STERLING_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3]


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
                f"{key} is derived from its own register and cannot be attested "
                "in one line; record each item with `sterlingctl drills record`"
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

    automated = {
        "source_identity": _source_identity(resolved_sha, resolved_tag),
        "release_manifest": _release_manifest_gate(),
        "backup_restore": _backup_restore_gate(resolved_sha),
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
