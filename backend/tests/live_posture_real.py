"""Put the REAL continuity gates back for the live-posture suites.

The shared autouse fixture in ``conftest.py`` makes ``egress_status`` and the order-time
binding check answer "verified" for every test whose subject is something else. The
live-posture tests are about those very gates, so they restore the genuine functions,
captured at import time (collection runs before any test patches them).
"""
from __future__ import annotations

from app.services import continuity_doctor as _doctor
from app.services import execution_service as _service

_REAL_EGRESS_STATUS = _doctor.egress_status
_REAL_VERIFY_BINDING = _service.CanonicalExecutionService.verify_production_account_binding


def restore_real_gates(monkeypatch) -> None:
    monkeypatch.setattr(_doctor, "egress_status", _REAL_EGRESS_STATUS)
    monkeypatch.setattr(
        _service.CanonicalExecutionService,
        "verify_production_account_binding",
        _REAL_VERIFY_BINDING,
    )
