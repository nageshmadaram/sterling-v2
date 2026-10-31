"""Offline-only regression: restart schemas must never capture FakeDatetime.

The production datetime field contract is unchanged; Pydantic should accept
ordinary ISO-8601 timestamps on every synthetic machine process restart.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.e2e_live.machine import NOW


@pytest.mark.asyncio
async def test_snapback_repeated_fresh_import_preserves_pydantic_datetime_contract(machine):
    for _ in range(2):
        machine.fresh_process()
        api = machine.m("app.api.v1.endpoints.opening_volume_leaders")
        payload = api.OpeningVolumeBarRequest.model_validate({
            "timestamp": NOW.isoformat(),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
        })
        assert isinstance(payload.timestamp, datetime)
        assert payload.timestamp.isoformat() == NOW.isoformat()

        # A rebuild must still produce a valid OpenAPI schema while the
        # scenario clock is active; no broker, account or order is used.
        app = machine.m("main").app
        document = app.openapi()
        assert document["openapi"].startswith("3.")
        assert "/api/v1/opening-volume-leaders/evaluate" in document["paths"]
