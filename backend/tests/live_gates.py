"""One place that satisfies the real-broker entry gates for harness-based tests.

Every real broker entry (a client whose ``_is_paper`` is False) is gated, in every
environment, by three checks that production satisfies through deployment ceremonies:

  1. verified static egress     ``continuity_doctor.egress_status().verified``
  2. an ACTIVE live-ready broker binding bound to this deployment
                                ``CanonicalExecutionService.verify_production_account_binding``
  3. strong, distinct secrets   ``core.security.validate_live_security`` (the root conftest
                                pins synthetic test-only values before ``app`` is imported)

Tests whose subject is something else (sizing, journaling, recovery, protection) must not
each stand up deployment manifests. They call :func:`satisfy_live_entry_gates`, which
patches only the PUBLIC seams the execution service itself calls, on whichever module
objects are loaded right now (the end-to-end machine re-imports ``app.*`` on every
simulated restart and re-applies this after each one). Production code has no bypass, and
the suites that exercise the gates themselves simply do not call this.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any


def satisfy_live_entry_gates(monkeypatch: Any, *, reason: str = "pytest verified") -> None:
    """Make the egress and binding gates answer 'verified' on the loaded modules."""
    doctor = sys.modules.get("app.services.continuity_doctor")
    if doctor is None:
        from app.services import continuity_doctor as doctor  # noqa: PLC0415
    monkeypatch.setattr(
        doctor, "egress_status",
        lambda: SimpleNamespace(verified=True, reason=f"{reason} egress"))

    service = sys.modules.get("app.services.execution_service")
    if service is None:
        from app.services import execution_service as service  # noqa: PLC0415

    async def _verified_binding(_self, _broker_client, _request):
        return True, f"{reason} broker binding"

    monkeypatch.setattr(
        service.CanonicalExecutionService, "verify_production_account_binding",
        _verified_binding)

    # Unrelated lifecycle fixtures carry synthetic release permission. The
    # release-authority suite exercises the real gate and is excluded below.
    from app.services import release_entry_authority
    monkeypatch.setattr(release_entry_authority, "entry_refusal", lambda request, client, *args: None)
    monkeypatch.setattr(release_entry_authority, "operator_release_status", lambda uid, account_id: {
        "new_entries_allowed": True, "status": "AUTHORIZED", "blockers": [], "strategies": []})
