"""No network or broker required: Add User runtime-contract probe parsing tests."""
from pathlib import Path
import runpy

script = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "verify_user_create_contract.py"))
supported = script["supported"]


def _doc(fields):
    return {
        "paths": {
            "/api/v1/auth/users": {
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/UserCreate"}}}
                    }
                }
            }
        },
        "components": {"schemas": {"UserCreate": {"properties": fields}}},
    }


def test_current_user_create_contract_advertises_access_fields():
    assert supported(_doc({"username": {}, "features": {}, "configuration": {}}))


def test_old_user_create_contract_is_blocked():
    assert not supported(_doc({"username": {}, "password": {}, "active": {}}))
    assert not supported(_doc({"username": {}, "features": {}}))


def test_contract_probe_handles_missing_endpoint_and_broken_references():
    assert not supported({})
    broken = _doc({"features": {}, "configuration": {}})
    broken["paths"]["/api/v1/auth/users"]["post"]["requestBody"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/DoesNotExist"
    }
    assert not supported(broken)


def test_contract_probe_handles_allof_composed_schemas():
    value = _doc({})
    value["components"]["schemas"]["UserCreate"]["allOf"] = [
        {"$ref": "#/components/schemas/InitialAccess"}
    ]
    value["components"]["schemas"]["InitialAccess"] = {"properties": {"features": {}, "configuration": {}}}
    assert supported(value)
