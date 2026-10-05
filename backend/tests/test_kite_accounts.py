"""Multi-tenant Kite account store: CRUD isolation, encryption, sessions."""
import pytest
from app.core.security import decrypt
from app.services.exchanges.kite import accounts
from app.services.exchanges.kite.models import KiteAccountCreate, KiteAccountUpdate


def _create(user="u1", label="A", key="apikey123", secret="topsecret"):
    return accounts.add(user, KiteAccountCreate(label=label, api_key=key, api_secret=secret))


def test_add_first_account_is_active_and_secret_encrypted():
    a = _create()
    assert a.is_active is True
    assert a.api_secret == "topsecret"          # decrypts
    assert a.api_secret_enc != "topsecret"      # stored encrypted
    assert decrypt(a.api_secret_enc) == "topsecret"
    assert a.api_key_hint() == "****y123"


def test_second_account_not_auto_active():
    _create()
    b = accounts.add("u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2"))
    assert b.is_active is False


def test_user_isolation():
    _create(user="u1")
    _create(user="u2", key="other")
    assert len(accounts.list_accounts("u1")) == 1
    assert len(accounts.list_accounts("u2")) == 1
    # u2 cannot fetch u1's account by id
    a1 = accounts.list_accounts("u1")[0]
    assert accounts.get("u2", a1.id) is None


def test_update_reencrypts_secret():
    a = _create()
    accounts.update("u1", a.id, KiteAccountUpdate(api_secret="newsecret", label="renamed"))
    got = accounts.get("u1", a.id)
    assert got.label == "renamed"
    assert got.api_secret == "newsecret"


def test_credential_rotation_invalidates_existing_session():
    a = _create()
    accounts.save_session(
        "u1",
        a.id,
        access_token="ATOK",
        refresh_token="RTOK",
        kite_user_id="ZID1",
    )
    assert accounts.validated_age_ms(a.id) is not None

    accounts.update(
        "u1",
        a.id,
        KiteAccountUpdate(api_secret="rotated-secret"),
    )

    got = accounts.get("u1", a.id)
    assert got.api_secret == "rotated-secret"
    assert got.connected is False
    assert got.has_refresh_token is False
    # Broker ownership remains pinned; the next login must prove the same user.
    assert got.kite_user_id == "ZID1"
    assert accounts.validated_age_ms(a.id) is None


def test_set_active_switches_exclusively():
    a = _create()
    b = accounts.add("u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2"))
    accounts.set_active("u1", b.id)
    assert accounts.get_active("u1").id == b.id
    assert accounts.get("u1", a.id).is_active is False


def test_failed_active_persistence_does_not_change_runtime_identity(monkeypatch):
    a = _create()
    b = accounts.add(
        "u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2")
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("disk write failed")

    monkeypatch.setattr(accounts, "_set_active_db", fail)

    with pytest.raises(RuntimeError, match="disk write failed"):
        accounts.set_active("u1", b.id)

    assert accounts.get_active("u1").id == a.id
    assert accounts.get("u1", b.id).is_active is False


def test_multiple_active_accounts_fail_closed():
    a = _create()
    b = accounts.add(
        "u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2")
    )
    b.is_active = True

    assert accounts.get_active("u1") is None


def test_bootstrap_does_not_turn_store_failure_into_empty_registry(monkeypatch):
    monkeypatch.setattr(accounts, "_loaded", False)
    monkeypatch.setattr(accounts, "_init_table", lambda: None)

    def unreadable():
        raise RuntimeError("sqlite unreadable")

    monkeypatch.setattr(accounts, "_load_from_db", unreadable)

    with pytest.raises(RuntimeError, match="sqlite unreadable"):
        accounts.bootstrap()


def test_save_and_clear_session():
    a = _create()
    accounts.save_session("u1", a.id, access_token="ATOK", public_token="PTOK", kite_user_id="ZID1")
    got = accounts.get("u1", a.id)
    assert got.connected is True
    assert got.access_token == "ATOK"
    assert got.kite_user_id == "ZID1"
    assert got.last_login_at_ms is not None
    accounts.clear_session("u1", a.id)
    cleared = accounts.get("u1", a.id)
    assert cleared.connected is False
    assert cleared.has_refresh_token is False
    assert cleared.refresh_token == ""


def test_empty_access_token_never_creates_connected_session():
    a = _create()

    with pytest.raises(ValueError, match="kite_access_token_missing"):
        accounts.save_session(
            "u1", a.id, access_token="", kite_user_id="ZID1"
        )

    got = accounts.get("u1", a.id)
    assert got.connected is False
    assert got.kite_user_id == ""
    assert accounts.validated_age_ms(a.id) is None


def test_failed_session_persistence_never_publishes_connection(monkeypatch):
    a = _create()

    def fail(_candidate):
        raise RuntimeError("session disk write failed")

    monkeypatch.setattr(accounts, "_persist", fail)

    with pytest.raises(RuntimeError, match="session disk write failed"):
        accounts.save_session(
            "u1", a.id, access_token="ATOK", kite_user_id="ZID1"
        )

    got = accounts.get("u1", a.id)
    assert got.connected is False
    assert got.kite_user_id == ""
    assert accounts.validated_age_ms(a.id) is None


def test_failed_revocation_persistence_quarantines_runtime_and_safe_mode(
    tmp_path, monkeypatch
):
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    safe_file = tmp_path / "safe_mode.json"
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(safe_file))

    a = _create()
    accounts.save_session(
        "u1",
        a.id,
        access_token="ATOK",
        refresh_token="RTOK",
        kite_user_id="ZID1",
    )

    def fail(_candidate):
        raise RuntimeError("revocation disk write failed")

    monkeypatch.setattr(accounts, "_persist", fail)

    with pytest.raises(
        RuntimeError, match="kite session revocation persistence failed"
    ):
        accounts.clear_session("u1", a.id)

    got = accounts.get("u1", a.id)
    assert got.connected is False
    assert got.has_refresh_token is False
    assert accounts.validated_age_ms(a.id) is None

    state = SafeModeService(safe_file).read()
    assert state.active is True
    assert SafeModeTrigger.EXECUTION_DISCREPANCY in state.triggers


def test_failed_account_add_is_never_memory_only(monkeypatch):
    before = list(accounts.all_accounts())

    def fail(_candidate):
        raise RuntimeError("account disk write failed")

    monkeypatch.setattr(accounts, "_persist", fail)

    with pytest.raises(RuntimeError, match="account disk write failed"):
        _create(user="u-new", key="new-key")

    assert accounts.all_accounts() == before


def test_delete_promotes_another_to_active():
    a = _create()
    b = accounts.add("u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2"))
    accounts.delete("u1", a.id)  # a was active
    assert accounts.get_active("u1").id == b.id


def test_failed_delete_persistence_keeps_runtime_account(monkeypatch):
    a = _create()
    b = accounts.add(
        "u1", KiteAccountCreate(label="B", api_key="k2", api_secret="s2")
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("delete transaction failed")

    monkeypatch.setattr(accounts, "_delete_db_state", fail)

    with pytest.raises(RuntimeError, match="delete transaction failed"):
        accounts.delete("u1", a.id)

    assert accounts.get("u1", a.id) is a
    assert accounts.get_active("u1").id == a.id
    assert accounts.get("u1", b.id).is_active is False


def test_to_response_redacts_secrets():
    a = _create()
    resp = accounts.to_response(a)
    dumped = resp.model_dump()
    assert "topsecret" not in str(dumped)
    assert "apikey123" not in str(dumped)
    assert dumped["api_key_hint"] == "****y123"
    assert dumped["has_credentials"] is True


def test_build_client_carries_decrypted_creds():
    a = _create()
    accounts.save_session("u1", a.id, access_token="ATOK", kite_user_id="ZID1")
    client = accounts.build_client(accounts.get("u1", a.id))
    assert client._api_key == "apikey123"
    assert client._api_secret == "topsecret"
    assert client._access_token == "ATOK"
    assert client._account_id == str(a.id)
    assert client._kite_user_id == "ZID1"


def test_save_session_persists_refresh_token():
    a = _create()
    accounts.save_session("u1", a.id, access_token="ATOK", refresh_token="RTOK", kite_user_id="ZID1")
    got = accounts.get("u1", a.id)
    assert got.refresh_token == "RTOK"          # decrypts
    assert got.refresh_token_enc != "RTOK"      # stored encrypted


def test_to_response_reports_refresh_token_capability():
    a = _create()
    assert accounts.to_response(a).has_refresh_token is False
    accounts.save_session("u1", a.id, access_token="ATOK", refresh_token="RTOK", kite_user_id="ZID1")
    assert accounts.to_response(accounts.get("u1", a.id)).has_refresh_token is True


def test_find_by_kite_user_id_routes_postbacks():
    a = _create(user="u1")
    accounts.save_session("u1", a.id, access_token="ATOK", kite_user_id="ZID1")
    found = accounts.find_by_kite_user_id("ZID1")
    assert found is not None
    assert found.user_id == "u1"
    assert found.id == a.id
    assert accounts.find_by_kite_user_id("UNKNOWN") is None


def test_second_account_cannot_claim_same_kite_user_id():
    a = _create(user="u1")
    b = _create(user="u2", key="other")
    accounts.save_session("u1", a.id, access_token="ATOK1", kite_user_id="ZID1")

    with pytest.raises(ValueError, match="kite_user_id_already_bound"):
        accounts.save_session(
            "u2", b.id, access_token="ATOK2", kite_user_id="ZID1"
        )

    assert accounts.get("u2", b.id).connected is False
    assert accounts.get("u2", b.id).kite_user_id == ""


def test_live_session_requires_broker_user_identity():
    live = accounts.add(
        "u1",
        KiteAccountCreate(
            label="Live",
            api_key="livekey",
            api_secret="livesecret",
            is_paper=False,
        ),
    )

    with pytest.raises(ValueError, match="live_kite_session_identity_missing"):
        accounts.save_session("u1", live.id, access_token="ATOK")

    assert accounts.get("u1", live.id).connected is False


def test_duplicate_kite_user_id_is_ambiguous_not_first_match():
    a = _create(user="u1")
    b = _create(user="u2", key="other")
    accounts.save_session("u1", a.id, access_token="ATOK1", kite_user_id="ZID1")
    # A second account can no longer be bound to the same broker identity through
    # the API...
    with pytest.raises(ValueError, match="kite_user_id_already_bound"):
        accounts.save_session("u2", b.id, access_token="ATOK2", kite_user_id="ZID1")
    # ...but a duplicate that predates the guard (a hand-edited or legacy row) must
    # still be treated as ambiguous by the lookup rather than resolved to the first.
    b.kite_user_id = "ZID1"

    assert accounts.find_by_kite_user_id("ZID1") is None
