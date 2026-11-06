"""A new OpenAlgo compatibility handler must not inherit Kite write authority."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.feature_access import enforce_feature_access, route_policy


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/compat/openalgo"),
    ("GET", "/api/v1/compat/openalgo/orderstatus"),
    ("DELETE", "/api/v1/compat/openalgo/orderstatus"),
    ("POST", "/api/v1/compat/openalgo/placeorder"),
    ("POST", "/api/v1/compat/openalgo/cancelorder"),
    ("PUT", "/api/v1/compat/openalgo/broker"),
    ("PATCH", "/api/v1/compat/openalgo/orderstatus"),
    ("POST", "/api/v1/compat/openalgo/orderstatus/"),
])
def test_all_unreviewed_compatibility_methods_fail_closed(method, path):
    assert route_policy(path, method) == "compatibility_denied"


def test_only_reviewed_orderstatus_post_is_classified_read_only():
    assert route_policy("/api/v1/compat/openalgo/orderstatus", "POST") == "kite_read_only"
    # Do not accidentally change unrelated canonical order routing.
    assert route_policy("/api/v1/kite/orders", "POST") == "kite"


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/compat/openalgo/placeorder"),
    ("POST", "/api/v1/compat/openalgo/cancelorder"),
    ("DELETE", "/api/v1/compat/openalgo/orderstatus"),
    ("GET", "/api/v1/compat/openalgo/unknown"),
])
def test_unreviewed_compatibility_route_does_not_enter_handler(method, path):
    app = FastAPI(dependencies=[Depends(enforce_feature_access)])
    touched = []
    for verb in ("GET", "POST", "DELETE"):
        app.add_api_route(path, lambda: touched.append("executed") or {"ok": True},
                          methods=[verb])
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == 403
    assert touched == []


def test_only_one_explicit_compatibility_handler_is_declared_in_source():
    path = Path(__file__).resolve().parents[2] / "app/api/v1/endpoints/openalgo_compat.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    declared = {
        (dec.func.attr.upper(), dec.args[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for dec in node.decorator_list
        if isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and isinstance(dec.func.value, ast.Name)
        and dec.func.value.id == "router"
        and dec.args and isinstance(dec.args[0], ast.Constant)
    }
    assert declared == {("POST", "/orderstatus")}
