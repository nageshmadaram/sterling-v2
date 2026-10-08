from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.broker_account_binding import BindingError, BindingStatus
from app.services.account_binding_service import (
    AccountBindingStore,
    BindingVerificationEvidence,
    binding_health,
)
from app.services.broker_binding_verification import verify_current_kite_binding


def _declared(store: AccountBindingStore, *, client_id: str = "AB1234"):
    return store.declare(
        broker="zerodha",
        legal_account_holder="Holder",
        client_id=client_id,
        account_scope="live",
        deployment_id="prod-1",
        static_egress_profile="mumbai-static",
    )


class _Client:
    _account_id = "KITE-ACCOUNT-1"
    _is_paper = False

    def __init__(self, profile_id="AB1234", fail=None):
        self.profile_id = profile_id
        self.fail = fail

    async def get_profile(self):
        if self.fail == "profile":
            raise RuntimeError("profile unavailable")
        return {"user_id": self.profile_id, "user_name": "Holder"}

    async def get_positions_raw(self):
        if self.fail == "positions":
            raise RuntimeError("positions unavailable")
        return {"net": []}

    async def get_orders(self):
        if self.fail == "orders":
            raise RuntimeError("orders unavailable")
        return []

    async def get_gtts(self):
        if self.fail == "gtts":
            raise RuntimeError("gtts unavailable")
        return []


@pytest.mark.asyncio
async def test_fresh_read_only_broker_proof_advances_binding(tmp_path, monkeypatch):
    from app.services.exchanges.kite import accounts

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    store = AccountBindingStore(tmp_path / "bindings")
    binding = _declared(store)
    active = SimpleNamespace(
        id="KITE-ACCOUNT-1",
        user_id="operator",
        kite_user_id="AB1234",
        is_paper=False,
        connected=True,
    )
    client = _Client()

    monkeypatch.setattr(accounts, "bootstrap", lambda: None)
    monkeypatch.setattr(accounts, "get_active", lambda uid: active if uid == "operator" else None)

    async def acquire(acct):
        assert acct is active
        return client

    monkeypatch.setattr(accounts, "acquire_client", acquire)

    verified = await verify_current_kite_binding(
        binding.binding_id,
        store=store,
        user_id="operator",
        application_account_id="KITE-ACCOUNT-1",
        deployment_id="prod-1",
    )

    assert verified.status is BindingStatus.VERIFIED
    assert verified.verification_profile_client_id == "AB1234"
    assert verified.verification_account_id == "KITE-ACCOUNT-1"
    assert verified.verification_inventory_read is True
    assert verified.verification_evidence_ref
    assert len(verified.verification_evidence_sha256) == 64
    assert (tmp_path / "data/manifests/account_binding_verifications").is_dir()

    activated = store.activate(binding.binding_id)
    assert activated.status is BindingStatus.ACTIVE
    health = binding_health(store.directory)
    assert health["account_binding_ready"] is True
    assert health["account_binding_verification_current"] is True


    # Readiness is evidence-backed, not a sticky metadata status. Any later
    # mutation to the verifier artifact invalidates health and activation.
    evidence_path = __import__("pathlib").Path(activated.verification_evidence_ref)
    evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
    health_after_tamper = binding_health(store.directory)
    assert health_after_tamper["account_binding_ready"] is False
    assert health_after_tamper["account_binding_verification_current"] is False
    with pytest.raises(BindingError, match="absent or stale"):
        store.activate(binding.binding_id)


@pytest.mark.asyncio
async def test_wrong_broker_profile_cannot_advance_binding(tmp_path, monkeypatch):
    from app.services.exchanges.kite import accounts

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    store = AccountBindingStore(tmp_path / "bindings")
    binding = _declared(store)
    active = SimpleNamespace(
        id="KITE-ACCOUNT-1",
        user_id="operator",
        kite_user_id="",
        is_paper=False,
        connected=True,
    )
    monkeypatch.setattr(accounts, "bootstrap", lambda: None)
    monkeypatch.setattr(accounts, "get_active", lambda _uid: active)

    async def acquire(_acct):
        return _Client(profile_id="ZZ9999")

    monkeypatch.setattr(accounts, "acquire_client", acquire)

    with pytest.raises(BindingError, match="does not match"):
        await verify_current_kite_binding(
            binding.binding_id,
            store=store,
            user_id="operator",
            application_account_id="KITE-ACCOUNT-1",
            deployment_id="prod-1",
        )
    assert store.get(binding.binding_id).status is BindingStatus.DECLARED


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["profile", "positions", "orders", "gtts"])
async def test_unreadable_broker_evidence_is_not_pass(tmp_path, monkeypatch, failure):
    from app.services.exchanges.kite import accounts

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    store = AccountBindingStore(tmp_path / "bindings")
    binding = _declared(store)
    active = SimpleNamespace(
        id="KITE-ACCOUNT-1",
        user_id="operator",
        kite_user_id="AB1234",
        is_paper=False,
        connected=True,
    )
    monkeypatch.setattr(accounts, "bootstrap", lambda: None)
    monkeypatch.setattr(accounts, "get_active", lambda _uid: active)

    async def acquire(_acct):
        return _Client(fail=failure)

    monkeypatch.setattr(accounts, "acquire_client", acquire)

    with pytest.raises(BindingError):
        await verify_current_kite_binding(
            binding.binding_id,
            store=store,
            user_id="operator",
            application_account_id="KITE-ACCOUNT-1",
            deployment_id="prod-1",
        )
    assert store.get(binding.binding_id).status is BindingStatus.DECLARED


def test_declared_binding_cannot_activate_without_broker_evidence(tmp_path):
    store = AccountBindingStore(tmp_path)
    binding = _declared(store)
    with pytest.raises(BindingError, match="verification"):
        store.activate(binding.binding_id)


def test_stale_verification_cannot_activate(tmp_path):
    store = AccountBindingStore(tmp_path)
    binding = _declared(store)
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    verified = binding.verified(
        profile_client_id=binding.client_id,
        application_account_id="KITE-ACCOUNT-1",
        deployment_id=binding.deployment_id,
        inventory_read=True,
        evidence_ref="/test/stale.json",
        when=old,
    )
    store.save(verified)
    with pytest.raises(BindingError, match="stale"):
        store.activate(binding.binding_id)


def test_store_rejects_stale_or_incomplete_evidence(tmp_path):
    store = AccountBindingStore(tmp_path)
    binding = _declared(store)
    stale = BindingVerificationEvidence(
        binding_id=binding.binding_id,
        profile_client_id=binding.client_id,
        application_account_id="KITE-ACCOUNT-1",
        deployment_id=binding.deployment_id,
        observed_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        positions_read=True,
        orders_read=True,
        protective_orders_read=True,
        evidence_ref="/test/stale.json",
    )
    with pytest.raises(BindingError, match="stale"):
        store.verify(binding.binding_id, stale)

    incomplete = BindingVerificationEvidence(
        binding_id=binding.binding_id,
        profile_client_id=binding.client_id,
        application_account_id="KITE-ACCOUNT-1",
        deployment_id=binding.deployment_id,
        observed_at=datetime.now(timezone.utc).isoformat(),
        positions_read=True,
        orders_read=False,
        protective_orders_read=True,
        evidence_ref="/test/incomplete.json",
    )
    with pytest.raises(BindingError, match="incomplete"):
        store.verify(binding.binding_id, incomplete)
