from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import hashlib
import json

import pytest

from app.core.acceptance_evidence import AcceptanceRegister, GATE_SOURCES
from app.services import release_entry_authority as authority
from tests.unit.test_acceptance_evidence import SHA, claim, economic_report


@pytest.fixture
def scope():
    return dict(source_sha=SHA, runtime_release_sha=SHA, deployment_id="host-a", account_id="account-a",
                strategy_id="sterling-kite", mode="swing", strategy_version=SHA, config_hash="c" * 64)


def approve(tmp_path, store, scope, gates=None):
    for gate in gates or (*authority.OPERATIONAL_GATES, "G7"):
        current = claim(tmp_path, claim_id=gate, source_type=sorted(GATE_SOURCES[gate])[0], **scope)
        if gate == "G7":
            path = tmp_path / "economic.json"
            report = economic_report(**scope)
            report["dataset_id"] = "frozen-test-dataset"
            path.write_text(json.dumps(report))
            current = replace(current, evidence_path=str(path), log_hash=hashlib.sha256(path.read_bytes()).hexdigest(), dataset_id=report["dataset_id"])
        store.append(current)


def test_default_posture_is_observation_only(tmp_path, scope):
    report = authority.release_permission(scope, register=AcceptanceRegister(tmp_path / "absent.db"))
    assert not report["new_entries_allowed"]
    assert not any(report[key] for key in ("r1_verified", "r2_verified", "r3_verified"))
    assert report["gates"]["G7"] == "UNKNOWN"


def test_operational_certification_cannot_substitute_for_economic_evidence(tmp_path, scope):
    store = AcceptanceRegister(tmp_path / "register.db")
    approve(tmp_path, store, scope, authority.OPERATIONAL_GATES)
    report = authority.release_permission(scope, register=store)
    assert report["r1_verified"] and report["r2_verified"]
    assert not report["r3_verified"] and not report["new_entries_allowed"]
    assert report["blockers"] == ["G7"]


def test_full_approval_and_append_only_revocation(tmp_path, scope):
    store = AcceptanceRegister(tmp_path / "register.db")
    approve(tmp_path, store, scope)
    assert authority.release_permission(scope, register=store)["new_entries_allowed"]
    store.append(claim(tmp_path, claim_id="G7", source_type="RESEARCH", result="BLOCKED", **scope))
    report = authority.release_permission(scope, register=store)
    assert not report["new_entries_allowed"] and report["gates"]["G7"] == "BLOCKED"


@pytest.mark.parametrize("changed", [
    {"source_sha": "b" * 40}, {"runtime_release_sha": "b" * 40}, {"account_id": "other"},
    {"deployment_id": "other"}, {"mode": "intraday"}, {"config_hash": "d" * 64},
    {"strategy_version": "other"}, {"strategy_id": "snapback"},
])
def test_approvals_do_not_cross_account_release_or_experiment(tmp_path, scope, changed):
    store = AcceptanceRegister(tmp_path / "register.db")
    approve(tmp_path, store, scope)
    assert not authority.release_permission({**scope, **changed}, register=store)["new_entries_allowed"]


def test_unreadable_register_blocks_all_entries(scope):
    store = SimpleNamespace(read=lambda: (_ for _ in ()).throw(OSError("disk unavailable")))
    report = authority.release_permission(scope, register=store)
    assert report["blockers"] == ["ACCEPTANCE_EVIDENCE_UNAVAILABLE"]


def test_paper_clients_do_not_receive_live_authority_checks():
    assert authority.entry_refusal(None, SimpleNamespace(_is_paper=True)) is None


def controlled_grant(tmp_path, store, scope, request, **changes):
    from datetime import timedelta
    receipt = {**request.__dict__, "schema_version": 1, "owner_authorized": True,
               "purpose": "ONE_CONTROLLED_CERTIFICATION_ORDER", "product": "NRML", "max_capital_required": 7500, "max_order_notional": 7500}
    path = tmp_path / "owner-receipt.json"
    path.write_text(json.dumps(receipt))
    current = claim(tmp_path, claim_id="CONTROLLED_LIVE_AUTHORIZATION", source_type="OPERATOR",
                    evidence_path=str(path), log_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                    expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), **scope)
    store.append(replace(current, **changes))


def test_separately_authorized_certification_intent_does_not_certify_r2_or_r3(tmp_path, scope):
    store = AcceptanceRegister(tmp_path / "register.db")
    approve(tmp_path, store, scope, tuple(g for g in authority.OPERATIONAL_GATES if g != "G9"))
    request = SimpleNamespace(uid="owner", account_id=scope["account_id"], strategy_id=scope["strategy_id"],
                              generation_id="reviewed-plan-v1", signal_id="reviewed-signal-v1", exchange="NFO",
                              symbol="NIFTY26OCT25000CE", side="BUY", quantity=75,
                              order_type="LIMIT", price=100, trigger_price=0, payload={"product":"NRML"})
    controlled_grant(tmp_path, store, scope, request)
    permission = authority.release_permission(scope, register=store, request=request, capital_required=7500)
    assert permission["new_entries_allowed"] and permission["controlled_test_authorized"]
    assert permission["r1_verified"] and not permission["r2_verified"] and not permission["r3_verified"]
    assert permission["gates"]["G9"] == "UNKNOWN" and permission["gates"]["G7"] == "UNKNOWN"
    assert not authority.release_permission(scope, register=store)["new_entries_allowed"]
    assert not authority.release_permission(scope, register=store, request=request, capital_required=7501)["new_entries_allowed"]
    for field, value in (("uid", "other"), ("symbol", "OTHER"), ("quantity", 150),
                         ("generation_id", "new"), ("signal_id", "new"), ("side", "SELL"), ("price", 200), ("order_type", "MARKET")):
        altered = SimpleNamespace(**{**request.__dict__, field: value})
        assert not authority.release_permission(scope, register=store, request=altered, capital_required=7500)["new_entries_allowed"]
    # A later revocation in this exact scope applies even while the receipt
    # remains sealed and readable in history.
    store.append(claim(tmp_path, claim_id="CONTROLLED_LIVE_AUTHORIZATION", source_type="OPERATOR", result="BLOCKED", **scope))
    assert not authority.release_permission(scope, register=store, request=request, capital_required=7500)["new_entries_allowed"]


def test_controlled_authorization_cannot_waive_host_broker_or_safety_acceptance(tmp_path, scope):
    store = AcceptanceRegister(tmp_path / "register.db")
    request = SimpleNamespace(uid="owner", account_id=scope["account_id"], strategy_id=scope["strategy_id"],
                              generation_id="g", signal_id="s", exchange="NFO", symbol="NIFTY", side="BUY", quantity=75,
                              order_type="LIMIT", price=100, trigger_price=0, payload={"product":"NRML"})
    controlled_grant(tmp_path, store, scope, request)
    assert not authority.release_permission(scope, register=store, request=request, capital_required=7500)["new_entries_allowed"]


@pytest.mark.parametrize("paper", [False, None])
def test_real_and_unlabelled_clients_fail_closed_when_evidence_is_absent(tmp_path, monkeypatch, scope, paper):
    monkeypatch.setattr(authority, "entry_scope", lambda request: scope)
    monkeypatch.setattr(authority, "AcceptanceRegister", lambda: AcceptanceRegister(tmp_path / "absent.db"))
    assert "RELEASE_NOT_AUTHORIZED" in authority.entry_refusal(None, SimpleNamespace(_is_paper=paper))


@pytest.mark.asyncio
async def test_master_entry_halt_precedes_release_authority(monkeypatch):
    """The production default-off gate stays enforced, even before G0-G9."""
    from app.core import lane_registry
    from app.services.execution_service import CanonicalExecutionService
    from tests.unit.test_production_binding_execution_gate import _request, _approval
    from app.services import fee_safety
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    async def no_fee_refresh(*args):
        return None
    monkeypatch.setattr(fee_safety, "refresh", no_fee_refresh)
    def release_never_called(*args, **kwargs):
        raise AssertionError("master kill switch must be checked first")
    monkeypatch.setattr(authority, "entry_refusal", release_never_called)
    request = _request(strategy_id="gamma_move")
    client = SimpleNamespace(_is_paper=True, _account_id=request.account_id)
    result = await CanonicalExecutionService().submit_order(
        request, broker_client=client, risk_approval=_approval(request)
    )
    assert result.status == "HALTED"
    assert result.intent_key == ""
    assert "STERLING_LIVE_EXECUTION" in result.error


@pytest.mark.asyncio
async def test_canonical_entry_checks_release_authority_before_reserving_or_sending(monkeypatch):
    from app.services.execution_service import CanonicalExecutionService
    from tests.unit.test_production_binding_execution_gate import _request, _approval
    from app.services import fee_safety, continuity_doctor
    from app.services.kite_engine import order_journal
    req = _request(strategy_id="gamma_move")
    client = SimpleNamespace(_is_paper=False, _account_id=req.account_id)
    # Isolate release-authority rejection, which is downstream of the master
    # transport switch. This patch affects only this test; never the process env.
    from app.core import lane_registry
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    service = CanonicalExecutionService()
    monkeypatch.setattr(service, "verify_exposure_effect", lambda **kwargs: req.exposure_effect)
    monkeypatch.setattr(service, "is_trading_allowed", lambda **kwargs: (True, ""))
    monkeypatch.setattr(continuity_doctor, "egress_status", lambda: SimpleNamespace(verified=True))
    async def binding(*args): return True, ""
    async def fees(*args): return None
    monkeypatch.setattr(service, "verify_production_account_binding", binding)
    monkeypatch.setattr(fee_safety, "refresh", fees)
    monkeypatch.setattr(authority, "entry_refusal", lambda request, client, *args: "RELEASE_NOT_AUTHORIZED: G7")
    def unexpected(*args, **kwargs): raise AssertionError("unauthorized entry must not reserve")
    monkeypatch.setattr(order_journal, "reserve", unexpected)
    result = await service.submit_order(req, broker_client=client, risk_approval=_approval(req))
    assert result.status == "REJECTED_RELEASE_AUTHORITY"
    assert result.intent_key == "" and "G7" in result.error


@pytest.mark.asyncio
async def test_revocation_during_transport_wait_never_sends_or_becomes_unknown(tmp_path, monkeypatch):
    from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, RiskApproval
    from app.services.kite_engine import order_journal
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setattr(db, "_available", False)
    assert db.init()
    # The transport-wait race must reach its in-memory broker double; otherwise
    # the default-off operator kill switch rightly ends the test too early.
    from app.core import lane_registry
    from app.services import shared_risk
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    monkeypatch.setattr(shared_risk, "entry_refusal", lambda *a, **k: None)
    monkeypatch.setattr(shared_risk, "committed_value", lambda *a, **k: 2500.0)
    monkeypatch.setattr(CanonicalExecutionService, "is_trading_allowed", lambda *a, **k: (True, ""))
    revoked = False
    sends = []
    monkeypatch.setattr(authority, "entry_refusal", lambda request, client, *args: "RELEASE_NOT_AUTHORIZED: G7" if revoked else None)
    class Broker:
        _is_paper = True
        _account_id = "account"
        async def place_order(self, **kwargs):
            nonlocal revoked
            revoked = True
            from app.services.snapback_family_mode import recheck_before_send_async
            await recheck_before_send_async()
            sends.append(kwargs)
            return {"order_id": "must-not-send"}
    req = ExecutionRequest(uid="owner", account_id="account", strategy_id="gamma_move", generation_id="g", signal_id="s",
                           exchange="NFO", symbol="NIFTY26OCT23000CE", side="BUY", quantity=25,
                           capital_required=2500, available_capital=100000)
    approval = RiskApproval(approval_id="a", uid=req.uid, account_id=req.account_id, symbol=req.symbol,
                            side=req.side, quantity=req.quantity, generation_id=req.generation_id,
                            strategy_id=req.strategy_id, signal_id=req.signal_id,
                            available_capital=100000, capital_required=2500)
    result = await CanonicalExecutionService().submit_order(req, broker_client=Broker(), risk_approval=approval)
    assert not result.success and not sends and result.status == "REJECTED"
    assert "G7" in result.error
    assert not order_journal.get(result.intent_key).reconciliation_required


@pytest.mark.asyncio
async def test_verified_reduction_does_not_consult_entry_release_authority(tmp_path, monkeypatch):
    from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, ExposureEffect
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setattr(db, "_available", False)
    assert db.init()
    service = CanonicalExecutionService()
    monkeypatch.setattr(service, "verify_exposure_effect", lambda **kwargs: ExposureEffect.REDUCE_EXPOSURE)
    monkeypatch.setattr(service, "is_trading_allowed", lambda **kwargs: (True, ""))
    async def verified(*args): return True, ""
    monkeypatch.setattr(service, "verify_broker_reduction", verified)
    def unexpected(*args): raise AssertionError("entry release gate must not block an exit")
    monkeypatch.setattr(authority, "entry_refusal", unexpected)
    class Broker:
        _is_paper = False
        _account_id = "account"
        async def place_order(self, **kwargs): return {"order_id": "exit-1"}
    req = ExecutionRequest(uid="owner", account_id="account", strategy_id="gamma_move", generation_id="g", signal_id="exit",
                           exchange="NFO", symbol="NIFTY26OCT23000CE", side="SELL", quantity=25,
                           exposure_effect=ExposureEffect.REDUCE_EXPOSURE)
    result = await service.submit_order(req, broker_client=Broker())
    assert result.success and result.order_id == "exit-1"
