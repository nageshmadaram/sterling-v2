from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Response

from app.api.v1.endpoints.family_operations import family_operations


def call(monkeypatch, *, positions=None, old=False):
    from app.services import family_operations_v2, release_entry_authority
    from app.services.ema_trend import snapshot
    stamp = datetime.now(timezone.utc) - timedelta(minutes=10 if old else 0)
    payload = {"environment": "production", "production_mode": "LIVE", "new_risk_allowed": True,
               "new_risk_blockers": [], "safety": {"new_risk_allowed": True, "blockers": []},
               "broker": {"account_id": "broker-1", "broker_observed_at": stamp.isoformat(),
                          "broker_state_readable": True, "broker_flatness": "PASS", "managed_positions": positions or [],
                          "external_positions": [], "unresolved_intents": 0}}
    monkeypatch.setattr(family_operations_v2, "family_operations_v2", lambda: payload)
    monkeypatch.setattr(release_entry_authority, "operator_release_status", lambda uid, account: {
        "new_entries_allowed": False, "status": "OBSERVATION_ONLY", "blockers": ["G7"], "strategies": []})
    monkeypatch.setattr(snapshot, "operations", lambda uid: {})
    return family_operations(Response(), SimpleNamespace(user_id="owner"))


def test_safe_but_uncertified_operator_payload_does_not_claim_live_permission(monkeypatch):
    result = call(monkeypatch)
    assert result["new_risk_allowed"] is False
    assert result["safety"]["new_risk_allowed"] is False
    assert result["production_mode"] == "LIVE_HALTED"
    assert "RELEASE_NOT_AUTHORIZED" in result["new_risk_blockers"]
    assert result["operator_summary"] == {"BROKER": "READABLE", "RISK": "NEW_ENTRIES_BLOCKED",
                                            "PROTECTION": "NO_EXPOSURE", "AUTOMATION": "OBSERVATION_ONLY"}


def test_managed_exposure_or_stale_broker_data_is_not_called_protected(monkeypatch):
    assert call(monkeypatch, positions=[{"instrument": "NIFTY"}])["operator_summary"]["PROTECTION"] == "UNKNOWN"
    summary = call(monkeypatch, old=True)["operator_summary"]
    assert summary["BROKER"] == "UNKNOWN" and summary["PROTECTION"] == "UNKNOWN"


def test_common_live_status_cannot_call_an_uncertified_account_ready(monkeypatch):
    from app.services import live_runtime, release_entry_authority
    from app.services.safety_supervisor import SafetySupervisor
    monkeypatch.setattr(SafetySupervisor, "authorize", lambda *args, **kwargs: SimpleNamespace(allowed=True, snapshot=None, code="", reason=""))
    monkeypatch.setattr(release_entry_authority, "operator_release_status", lambda uid, account: {
        "new_entries_allowed": False, "status": "OBSERVATION_ONLY", "blockers": ["G7"]})
    account = SimpleNamespace(user_id="owner", id="account")
    status = live_runtime._admission_summary([account])
    assert not status["may_open_new_exposure"]
    assert "release_not_authorized" in status["accounts"][0]["blockers"]
