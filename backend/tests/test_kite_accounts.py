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
    assert client._is_paper is False             # a stored live account builds a live client


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
    # No mode is passed: a default account is live and so needs a bound identity.
    live = accounts.add(
        "u1",
        KiteAccountCreate(label="Live", api_key="livekey", api_secret="livesecret"),
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


# ─── Live-only contract ──────────────────────────────────────────────────────
def test_accounts_are_live_by_default():
    assert KiteAccountCreate(api_key="k", api_secret="s").is_paper is False
    a = _create()
    assert a.is_paper is False
    assert accounts.to_response(a).is_paper is False


def test_new_account_row_is_stored_live():
    from app.services import db

    a = _create()
    with db._conn() as c:
        row = c.execute("SELECT is_paper FROM kite_accounts WHERE id=?", (a.id,)).fetchone()
    assert row["is_paper"] == 0


def test_stored_flag_not_a_default_decides_the_broker_mode():
    from tests.kite_paper_double import as_paper_double

    a = _create()
    assert accounts.build_client(a)._is_paper is False
    as_paper_double(a.id)
    assert accounts.build_client(accounts.get("u1", a.id))._is_paper is True


# ─── Legacy non-live account conversion ──────────────────────────────────────
def _legacy(user="u1", key="legacykey", *, kite_user_id="AA0595", active_session=True):
    """An account row exactly as an older build left it: simulated mode, logged in."""
    from tests.kite_paper_double import as_paper_double

    a = _create(user=user, key=key, label="SterlingKite")
    as_paper_double(a.id)
    if active_session:
        accounts.save_session(
            user, a.id, access_token="OLD-ACCESS", refresh_token="OLD-REFRESH",
            public_token="OLD-PUBLIC", kite_user_id=kite_user_id, user_name="Old Name",
        )
    return accounts.get(user, a.id)


def _audit_rows(account_id):
    from app.services import db

    with db._conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM kite_account_audit WHERE account_id=? ORDER BY id", (account_id,)
        ).fetchall()]


def _reload_from_db(account_id):
    return next(x for x in accounts._load_from_db() if x.id == account_id)


def test_safe_legacy_account_is_converted_to_live_with_identity_cleared(monkeypatch):
    monkeypatch.delenv(accounts.ACK_LEGACY_STORE_ENV, raising=False)
    a = _legacy()
    assert a.is_paper is True and a.connected is True and a.kite_user_id == "AA0595"

    results = accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)

    assert [(r.account_id, r.outcome) for r in results] == [(a.id, "converted")]
    got = accounts.get("u1", a.id)
    assert got.id == a.id                       # same id, so references to it survive
    assert got.is_paper is False
    assert got.label == "SterlingKite" and got.is_active is True
    assert got.api_key == "legacykey" and got.api_secret == "topsecret"   # credentials kept
    # Every session artefact and the broker binding are gone: a fresh real login
    # decides who this account belongs to.
    assert got.connected is False and got.has_refresh_token is False
    assert got.public_token == "" and got.kite_user_id == "" and got.user_name == ""
    assert got.token_expires_at_ms is None
    assert accounts.validated_age_ms(a.id) is None
    # Durable, not just in memory.
    stored = _reload_from_db(a.id)
    assert stored.is_paper is False and stored.kite_user_id == "" and stored.access_token_enc == ""
    assert accounts.build_client(got)._is_paper is False


def test_conversion_is_audited_without_secrets():
    a = _legacy()

    accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)

    rows = [r for r in _audit_rows(a.id) if r["event"] == "legacy_paper_converted_to_live"]
    assert len(rows) == 1
    import json as _json
    detail = _json.loads(rows[0]["detail_json"])
    assert detail["had_access_token"] is True and detail["had_refresh_token"] is True
    assert detail["previous_kite_user_id"] == "AA0595"
    assert "OLD-ACCESS" not in rows[0]["detail_json"] and "OLD-REFRESH" not in rows[0]["detail_json"]


def test_conversion_is_idempotent():
    a = _legacy()
    first = accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)
    second = accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)

    assert [r.outcome for r in first] == ["converted"]
    assert second == []
    assert len([r for r in _audit_rows(a.id)
                if r["event"] == "legacy_paper_converted_to_live"]) == 1


def test_a_live_account_is_never_touched_by_the_conversion():
    live = _create(user="u1", key="livekey")
    accounts.save_session("u1", live.id, access_token="LIVE-TOKEN", kite_user_id="LIVE1")

    assert accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True) == []

    got = accounts.get("u1", live.id)
    assert got.connected is True and got.kite_user_id == "LIVE1"
    assert _audit_rows(live.id) == []


def test_converted_account_rebinds_to_the_next_real_login_only():
    a = _legacy()
    accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)

    # Live now: a session without a broker identity is refused...
    with pytest.raises(ValueError, match="live_kite_session_identity_missing"):
        accounts.save_session("u1", a.id, access_token="NEW")
    # ...and the identity the real login reports becomes the binding.
    accounts.save_session("u1", a.id, access_token="NEW", kite_user_id="REAL42")
    assert accounts.get("u1", a.id).kite_user_id == "REAL42"
    with pytest.raises(ValueError, match="kite_user_id_mismatch"):
        accounts.save_session("u1", a.id, access_token="NEW2", kite_user_id="SOMEONE-ELSE")


def test_unresolved_journal_intent_blocks_conversion_and_alerts(caplog):
    from app.services.kite_engine import order_journal

    a = _legacy()
    order_journal.reserve(
        uid="u1", account_id=a.id, strategy_id="sterling-kite", generation_id="g1",
        signal_id="sig-legacy-1", exchange="NFO", symbol="NIFTY26OCT25000CE",
        side="BUY", quantity=75,
    )

    with caplog.at_level("ERROR"):
        results = accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)

    assert [(r.outcome) for r in results] == ["blocked"]
    assert any("unresolved order intent" in reason for reason in results[0].reasons)
    got = accounts.get("u1", a.id)
    assert got.is_paper is True                         # left exactly as it was
    assert got.connected is True and got.kite_user_id == "AA0595"
    assert any("legacy_account_not_live" in r.getMessage() and a.id in r.getMessage()
               for r in caplog.records)                 # operator alert rendered + logged


def test_blocked_conversion_is_audited_once_not_once_per_boot():
    from app.services.kite_engine import order_journal

    a = _legacy()
    order_journal.reserve(
        uid="u1", account_id=a.id, strategy_id="sterling-kite", generation_id="g1",
        signal_id="sig-legacy-2", exchange="NFO", symbol="NIFTY26OCT25000PE",
        side="BUY", quantity=75,
    )

    accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)
    accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)

    refused = [r for r in _audit_rows(a.id) if r["event"] == "legacy_paper_conversion_refused"]
    assert len(refused) == 1


def test_open_local_position_owned_by_the_account_blocks_conversion():
    from app.services.kite_engine import positions

    a = _legacy()
    positions.reset("u1")
    positions.register(positions.OpenPosition(
        uid="u1", account_id=a.id, symbol="NIFTY26OCT25000CE", exchange="NFO",
        qty=75, status=positions.OPEN,
    ))
    try:
        results = accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)
    finally:
        positions.reset("u1")

    assert results[0].outcome == "blocked"
    assert any("NIFTY26OCT25000CE" in reason for reason in results[0].reasons)
    assert accounts.get("u1", a.id).is_paper is True


def test_open_position_with_unknown_owner_blocks_but_another_accounts_does_not():
    from app.services.kite_engine import positions

    a = _legacy()
    positions.reset("u1")
    positions.register(positions.OpenPosition(
        uid="u1", account_id="KITE-SOMEONEELSE", symbol="SBIN", exchange="NSE",
        qty=10, status=positions.OPEN,
    ))
    try:
        assert accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)[0].outcome == "converted"
    finally:
        positions.reset("u1")

    b = _legacy(user="u2", key="otherkey", kite_user_id="BB1")
    positions.reset("u2")
    positions.register(positions.OpenPosition(
        uid="u2", account_id="", symbol="INFY", exchange="NSE", qty=10, status=positions.OPEN,
    ))
    try:
        results = accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)
    finally:
        positions.reset("u2")
    assert [(r.account_id, r.outcome) for r in results] == [(b.id, "blocked")]
    assert any("owner unknown" in reason for reason in results[0].reasons)


def test_execution_ledger_not_flat_blocks_conversion():
    from app.services import db

    a = _legacy()
    with db._conn() as c:
        c.execute(
            "INSERT INTO kite_inventory (account_id, uid, symbol, exchange, net_quantity, updated_ms) "
            "VALUES (?, 'u1', 'NIFTY26OCT25000CE', 'NFO', 75, 1)", (a.id,),
        )
    try:
        results = accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM kite_inventory WHERE account_id=?", (a.id,))

    assert results[0].outcome == "blocked"
    assert any("execution ledger" in reason for reason in results[0].reasons)


def test_open_rows_in_legacy_position_table_need_an_explicit_acknowledgement(monkeypatch):
    from app.services import db

    monkeypatch.delenv(accounts.ACK_LEGACY_STORE_ENV, raising=False)
    a = _legacy()
    with db._conn() as c:
        c.execute(
            "INSERT INTO positions (id, underlying, status, data, entry_ts, updated_ts) "
            "VALUES ('legacy-open-1', 'NIFTY', 'open', '{}', 1, 1)"
        )
    try:
        blocked = accounts.migrate_legacy_paper_accounts()
        assert blocked[0].outcome == "blocked"
        assert any(accounts.ACK_LEGACY_STORE_ENV in reason for reason in blocked[0].reasons)
        assert accounts.get("u1", a.id).is_paper is True

        monkeypatch.setenv(accounts.ACK_LEGACY_STORE_ENV, "1")
        converted = accounts.migrate_legacy_paper_accounts()
        assert converted[0].outcome == "converted"
        assert accounts.get("u1", a.id).is_paper is False
    finally:
        with db._conn() as c:
            c.execute("DELETE FROM positions WHERE id='legacy-open-1'")


def test_unreadable_exposure_state_is_a_blocker_not_a_pass(monkeypatch):
    from app.services.kite_engine import positions

    a = _legacy()

    def _boom(_uid):
        raise positions.RegistryUnreadable("u1")

    monkeypatch.setattr(positions, "open_positions", _boom)

    results = accounts.migrate_legacy_paper_accounts(acknowledge_legacy_store=True)

    assert results[0].outcome == "blocked"
    assert any("unreadable" in reason for reason in results[0].reasons)
    assert accounts.get("u1", a.id).is_paper is True
