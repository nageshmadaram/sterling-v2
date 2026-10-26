"""Legacy observation/startup diagnostics that remain advisory.

These compatibility helpers do not authorize live risk. The 2026-10-26 release
entry authority requires independently verified G0-G9 evidence for new broker
exposure, even while an old doctor/rehearsal report remains informational.
Safety, recovery, binding, capital, clock and storage checks continue to block.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Iterable

__all__ = [
    "ADVISORY_DOCTOR_CHECKS",
    "ADVISORY_RELEASE_GATES",
    "DoctorSplit",
    "WAIVER_NOTE",
    "is_advisory_doctor_check",
    "is_advisory_release_gate",
    "split_doctor_checks",
    "advisory_gate_lines",
]

#: One sentence an operator reads next to any waived result.
WAIVER_NOTE: Final[str] = (
    "waived for observation/startup diagnostics only; live entry authority remains mandatory"
)

#: Doctor / preflight check names that are evidence or release-identity
#: bookkeeping rather than a safety fact about trading right now.
ADVISORY_DOCTOR_CHECKS: Final[frozenset[str]] = frozenset(
    {
        # preflight: build and evidence identity
        "build_identity",
        "worktree_clean",
        "evidence_meta",
        "dataset_start",
        "evidence_authority",
        "experiment_config_identity",
        # lane doctor: release identity and certification
        "release_tag",
        "supertrend_core_frozen",
        "release_certification",
    }
)

#: Every row of the release certification table is advisory. The real checks
#: that overlap with a gate (production secrets, the broker account binding,
#: unresolved exposure) run as their own blocking steps in the start sequence
#: and in the SafetySupervisor, so waiving the table does not waive them.
ADVISORY_RELEASE_GATES: Final[frozenset[str]] = frozenset(
    {
        "source_identity",
        "production_security",
        "deployment_identity",
        "account_binding",
        "remote_ci",
        "test_suites",
        "kite_live_acceptance",
        "reconnect",
        "persistence",
        "release_manifest",
        "backup_restore",
        "failure_drills",
        "open_exposure",
    }
)


def is_advisory_doctor_check(name: str) -> bool:
    return str(name or "") in ADVISORY_DOCTOR_CHECKS


def is_advisory_release_gate(key: str) -> bool:
    return str(key or "") in ADVISORY_RELEASE_GATES


@dataclass(frozen=True)
class DoctorSplit:
    """A doctor report separated into what blocks and what is only reported."""

    failures: tuple[Any, ...] = field(default_factory=tuple)
    unknowns: tuple[Any, ...] = field(default_factory=tuple)
    #: Waived checks that did not pass (failed or could not be checked).
    advisories: tuple[Any, ...] = field(default_factory=tuple)
    checks: int = 0

    @property
    def safe(self) -> bool:
        """Every blocking check ran and passed. Advisory results do not count."""
        return self.checks > 0 and not self.failures and not self.unknowns

    @property
    def exit_code(self) -> int:
        """0 safe, 1 a blocking check failed, 2 a blocking check could not run.

        With nothing to run at all the answer is 2: reporting success when no
        check ran is the single worst thing a doctor can do.
        """
        if self.checks == 0:
            return 2
        if self.failures:
            return 1
        if self.unknowns:
            return 2
        return 0


def split_doctor_checks(checks: Iterable[Any]) -> DoctorSplit:
    """Partition ``DoctorCheck``-shaped objects into blocking and advisory."""
    failures: list[Any] = []
    unknowns: list[Any] = []
    advisories: list[Any] = []
    total = 0
    for check in checks:
        total += 1
        passed = getattr(check, "passed", None)
        if passed is True:
            continue
        if is_advisory_doctor_check(getattr(check, "name", "")):
            advisories.append(check)
        elif passed is False:
            failures.append(check)
        else:
            unknowns.append(check)
    return DoctorSplit(
        failures=tuple(failures),
        unknowns=tuple(unknowns),
        advisories=tuple(advisories),
        checks=total,
    )


def advisory_gate_lines(results: Iterable[Any]) -> list[str]:
    """One line per release gate that is not PASS, for display next to ``waived``."""
    lines: list[str] = []
    for result in results:
        if getattr(result, "passed", False):
            continue
        lines.append(
            f"{getattr(result, 'key', '?')}={getattr(result, 'status', 'UNKNOWN')}"
        )
    return lines
