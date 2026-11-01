"""Read-only route inventory must not represent a proposed writer as mounted parity."""
from __future__ import annotations

from pathlib import Path

import pytest

from upgrade.api_inventory import declared_routes, report


ROOT = Path(__file__).resolve().parents[2]
ENDPOINTS = ROOT / "backend" / "app" / "api" / "v1" / "endpoints"


def test_real_kite_and_compat_endpoint_declarations_are_detected():
    outcome = report(ENDPOINTS)
    assert outcome["mode"] == "STATIC_SOURCE_ONLY"
    assert outcome["status"] == "COMPATIBILITY_INCOMPLETE"
    assert "POST /api/v1/kite/orders" in outcome["routes"]["kite"]
    assert "GET /api/v1/kite/orders" in outcome["routes"]["kite"]
    assert "DELETE /api/v1/kite/orders/{order_id}" in outcome["routes"]["kite"]
    assert "POST /api/v1/compat/openalgo/orderstatus" in outcome["routes"]["openalgo_compat"]
    statuses = {
        row["openalgo"]: row["status"] for row in outcome["openalgo_route_delta"]
    }
    assert statuses["POST /api/v1/orderstatus"] == "JWT_READ_ONLY_ADAPTER_SOURCE_PRESENT"
    assert statuses["POST /api/v1/placeorder"] == "NO_COMPATIBILITY_WRAPPER"
    assert statuses["POST /api/v1/cancelorder"] == "NO_COMPATIBILITY_WRAPPER"
    assert outcome["broker_connection"] == "NOT_ATTEMPTED"


def test_dynamic_route_requires_review_not_silent_assumed_compatibility(tmp_path):
    p = tmp_path / "unsafe.py"
    p.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/x')\n"
        "PATH = '/send'\n"
        "@router.post(PATH)\n"
        "def place(): pass\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Dynamic API path"):
        declared_routes(p)


def test_route_inventory_is_source_only_and_does_not_import_or_run_handlers(tmp_path):
    p = tmp_path / "safe.py"
    p.write_text(
        "from fastapi import APIRouter\n"
        "raise RuntimeError('import of this file would be unsafe')\n"
        "router = APIRouter(prefix='/test')\n"
        "@router.get('/read')\n"
        "def data(): pass\n",
        encoding="utf-8",
    )
    assert declared_routes(p) == ["GET /api/v1/test/read"]
