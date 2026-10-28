"""User-specific workspace discovery visibility is not an authorization grant."""
import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from app.api.v1.endpoints import sterling_users, auth
from app.core.auth import UserContext
from app.services import db
from app.services.auth import account_policy, access_requests, operator_credentials, user_store


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "visible-workspaces.db"))
    assert db.init()
    user_store._init_table()
    monkeypatch.setattr(user_store, "_users", {})
    user_store.add("primary", "Synthetic-admin-password-123", role="admin", user_id="default")
    user_store.add("member", "Synthetic-member-password-123", user_id="member")
    return UserContext(user_id="default", role="admin"), UserContext(user_id="member", role="user")


def test_legacy_accounts_default_visible_but_not_granted(accounts):
    policy = account_policy.read("member")
    assert policy.visibility == {"kite": True, "tv": True, "radio": True}
    assert policy.features == {"kite": False, "tv": False, "radio": False}


def test_hide_kite_without_granting_or_revoking_access(accounts):
    features = {"kite": False, "tv": True, "radio": True}
    visibility = {"kite": False, "tv": True, "radio": True}
    saved = account_policy.set_features("default", "member", features, 0, visibility=visibility)
    assert saved.revision == 1
    assert saved.visibility == visibility
    assert saved.features == features
    with pytest.raises(PermissionError, match="not available"):
        access_requests.request_access("member", "kite")
    # Radio is already granted. Discovery visibility must not create a redundant
    # approval request or change that existing authorization.
    assert access_requests.request_access("member", "radio")["status"] == "granted"
    with db._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM feature_access_requests WHERE user_id='member'").fetchone()[0] == 0
    # A hidden-but-granted feature remains granted: visibility is presentation only.
    saved = account_policy.set_features("default", "member", {**features, "kite": True}, 1, visibility=visibility)
    assert saved.features["kite"] and not saved.visibility["kite"]
    assert account_policy.require_feature("member", "kite").features["kite"]


def test_workspace_visibility_persists_and_conflict_rolls_back(accounts):
    hidden = {"kite": False, "tv": True, "radio": True}
    account_policy.set_features("default", "member", dict.fromkeys(account_policy.FEATURES, False), 0, visibility=hidden)
    user_store._init_table()
    assert account_policy.read("member").visibility == hidden
    with pytest.raises(account_policy.PolicyConflict):
        account_policy.set_features("default", "member", dict.fromkeys(account_policy.FEATURES, True), 0,
                                    visibility={"kite": True, "tv": True, "radio": True})
    assert account_policy.read("member").visibility == hidden
    with pytest.raises(ValueError):
        account_policy.set_features("default", "member", dict.fromkeys(account_policy.FEATURES, False), 1,
                                    visibility={"kite": 0, "tv": True, "radio": True})


@pytest.mark.asyncio
async def test_create_hidden_kite_and_return_visibility_in_session(accounts):
    admin, member = accounts
    created = await sterling_users.add_user(sterling_users.UserCreate(
        username="radio-member", password="Synthetic-member-password-123",
        features={"kite": False, "tv": True, "radio": True},
        visibility={"kite": False, "tv": True, "radio": True}
    ), admin)
    policy = account_policy.read(created["id"])
    assert policy.revision == 1 and policy.visibility["kite"] is False
    view = await auth.me(Response(), UserContext(user_id=created["id"], role="user"))
    assert view.visibility == {"kite": False, "tv": True, "radio": True}
    assert not view.features["kite"]
    with pytest.raises(PermissionError):
        access_requests.request_access(created["id"], "kite")


@pytest.mark.asyncio
async def test_admin_update_visibility_uses_revision_and_strict_schema(accounts):
    admin, member = accounts
    body = sterling_users.FeatureEdit(expected_revision=0,
        features={"kite": False, "tv": True, "radio": True},
        visibility={"kite": False, "tv": True, "radio": True})
    saved = await sterling_users.update_features("member", body, Response(), admin)
    assert saved["visibility"]["kite"] is False
    with pytest.raises(HTTPException) as duplicate:
        await sterling_users.update_features("member", body, Response(), admin)
    assert duplicate.value.status_code == 409
    with pytest.raises(ValidationError):
        sterling_users.FeatureEdit(expected_revision=1,
            features={"kite": False, "tv": True, "radio": True},
            visibility={"kite": "false", "tv": True, "radio": True})


def test_hidden_request_has_no_pending_side_effect(accounts):
    account_policy.set_features("default", "member",
                                {"kite": False, "tv": False, "radio": False}, 0,
                                visibility={"kite": False, "tv": True, "radio": True})
    with pytest.raises(PermissionError):
        access_requests.request_access("member", "kite")
    with db._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM feature_access_requests WHERE user_id='member'").fetchone()[0] == 0


def test_pending_request_cannot_be_approved_after_admin_hides_workspace(accounts):
    prior = access_requests.request_access("member", "kite")
    assert prior["status"] == "pending"
    account_policy.set_features("default", "member",
        {"kite": False, "tv": False, "radio": False}, 0,
        visibility={"kite": False, "tv": True, "radio": True})
    with pytest.raises(PermissionError, match="Show this workspace"):
        access_requests.decide("default", prior["id"], "approved")
    assert account_policy.read("member").features["kite"] is False
    with db._conn() as c:
        row = c.execute("SELECT status FROM feature_access_requests WHERE id=?", (prior["id"],)).fetchone()
        assert row["status"] == "pending"
