"""Offline registry commands: authenticated and audited in a single SQL transaction.

This module is not an HTTP route or a broker execution authority.
Callers MUST supply a signed Sterling access JWT; development header identities
and caller-provided roles are never trusted.
"""
from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.strategy_registry import StrategyRunIntent, StrategyVersionSnapshot
from app.persistence.session import session_scope
from app.persistence.strategy_registry_models import (
    PlatformStrategyRow, PlatformStrategyRunRow, PlatformStrategyVersionRow,
)
from app.persistence.strategy_registry_audit import PlatformStrategyAuditRow


def _identity(access_token: str):
    if not isinstance(access_token, str) or not access_token.strip():
        raise PermissionError("signed access token required")
    # Token signature, expiry, audience/type and revocation are verified against
    # Sterling's actual user store. Never use get_current_user: in development
    # it also accepts an unauthenticated identity header.
    from fastapi import HTTPException
    from app.core.auth import _context_from_access_token
    try:
        actor = _context_from_access_token(access_token.strip())
    except HTTPException as exc:
        raise PermissionError("signed access token invalid or revoked") from exc
    if actor.role not in ("admin", "operator") or not actor.user_id:
        raise PermissionError("admin/operator registry authority required")
    if actor.view or actor.owner_uid:
        raise PermissionError("simulation identities cannot mutate registry")
    return actor


def _reason(value: str) -> str:
    value = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(value) <= 512:
        raise ValueError("non-empty audit reason (max 512 characters) required")
    return value


def _digest(document: dict) -> str:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot(row: PlatformStrategyRow) -> dict:
    return {
        "id": row.id, "owner_id": row.owner_id, "slug": row.slug,
        "display_name": row.display_name, "state": row.state, "is_locked": bool(row.is_locked),
    }


def _audit(db: Session, actor, owner_id: str, action: str, resource_type: str,
           resource_id: str, reason: str, before_hash: str | None, after_hash: str | None) -> None:
    db.add(PlatformStrategyAuditRow(
        id=str(uuid4()), actor_id=actor.user_id, actor_role=actor.role,
        owner_id=owner_id, action=action, resource_type=resource_type,
        resource_id=resource_id, reason=reason, before_sha256=before_hash,
        after_sha256=after_hash, created_at_ms=int(time.time() * 1000),
    ))


def _strategy(db: Session, actor, strategy_id: str) -> PlatformStrategyRow:
    # Row lock on PostgreSQL: a concurrent lock/retire cannot race version
    # publication. SQLite serializes writers; concurrency must still be
    # separately stress-tested in the actual deployment database.
    row = db.scalar(select(PlatformStrategyRow).where(
        PlatformStrategyRow.id == strategy_id).with_for_update())
    if row is None:
        raise LookupError("strategy not found")
    if row.owner_id != actor.user_id and actor.role != "admin":
        raise PermissionError("strategy belongs to another owner")
    return row


def _version(db: Session, version_id: str) -> PlatformStrategyVersionRow:
    row = db.get(PlatformStrategyVersionRow, version_id)
    if row is None:
        raise LookupError("strategy version not found")
    # Hash alone does not confer authority; verify before consuming a version.
    snap = StrategyVersionSnapshot(
        strategy_id=row.strategy_id, version=row.version, parameters_json=row.parameters_json
    )
    if snap.canonical_json() != row.parameters_json or snap.sha256() != row.content_sha256:
        raise ValueError("strategy version integrity mismatch")
    return row


class StrategyRegistryService:
    """No network effects, no public routes, no authorization by UI claims."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def create_strategy(self, *, access_token: str, slug: str,
                        display_name: str, reason: str) -> str:
        actor = _identity(access_token)
        reason = _reason(reason)
        slug, display_name = slug.strip(), display_name.strip()
        if not 1 <= len(slug) <= 96 or not 1 <= len(display_name) <= 200:
            raise ValueError("invalid strategy name")
        now = int(time.time() * 1000)
        key = str(uuid4())
        with session_scope(self._session_factory) as db:
            row = PlatformStrategyRow(
                id=key, owner_id=actor.user_id, slug=slug,
                display_name=display_name, state="draft", is_locked=False,
                created_at_ms=now, updated_at_ms=now,
            )
            db.add(row)
            db.flush()
            _audit(db, actor, row.owner_id, "create_strategy", "strategy",
                   row.id, reason, None, _digest(_snapshot(row)))
        return key

    def update_strategy(self, *, access_token: str, strategy_id: str,
                        display_name: str, reason: str) -> None:
        actor, reason = _identity(access_token), _reason(reason)
        display_name = display_name.strip()
        if not 1 <= len(display_name) <= 200:
            raise ValueError("invalid strategy name")
        with session_scope(self._session_factory) as db:
            row = _strategy(db, actor, strategy_id)
            if row.is_locked or row.state == "retired":
                raise PermissionError("locked or retired strategy")
            before = _digest(_snapshot(row))
            if row.display_name == display_name:
                raise ValueError("no metadata change")
            row.display_name = display_name
            row.updated_at_ms = int(time.time() * 1000)
            db.flush()
            _audit(db, actor, row.owner_id, "update_strategy", "strategy",
                   row.id, reason, before, _digest(_snapshot(row)))

    def lock_strategy(self, *, access_token: str, strategy_id: str,
                      reason: str) -> None:
        actor, reason = _identity(access_token), _reason(reason)
        with session_scope(self._session_factory) as db:
            row = _strategy(db, actor, strategy_id)
            if row.is_locked:
                raise ValueError("strategy already locked")
            before = _digest(_snapshot(row))
            row.is_locked = True
            row.updated_at_ms = int(time.time() * 1000)
            db.flush()
            _audit(db, actor, row.owner_id, "lock_strategy", "strategy",
                   row.id, reason, before, _digest(_snapshot(row)))

    def retire_strategy(self, *, access_token: str, strategy_id: str,
                        reason: str) -> None:
        """Audited soft-delete only; database hard DELETE is prohibited."""
        actor, reason = _identity(access_token), _reason(reason)
        with session_scope(self._session_factory) as db:
            row = _strategy(db, actor, strategy_id)
            if row.is_locked or row.state != "disabled":
                raise PermissionError("retirement requires unlocked, disabled strategy")
            before = _digest(_snapshot(row))
            row.state = "retired"
            row.updated_at_ms = int(time.time() * 1000)
            db.flush()
            _audit(db, actor, row.owner_id, "retire_strategy", "strategy",
                   row.id, reason, before, _digest(_snapshot(row)))

    def publish_version(self, *, access_token: str, strategy_id: str,
                        version: str, parameters: dict, reason: str) -> str:
        actor, reason = _identity(access_token), _reason(reason)
        snap = StrategyVersionSnapshot.from_parameters(
            strategy_id=strategy_id, version=version, parameters=parameters
        )
        now, key = int(time.time() * 1000), str(uuid4())
        with session_scope(self._session_factory) as db:
            strategy = _strategy(db, actor, strategy_id)
            if strategy.is_locked or strategy.state == "retired":
                raise PermissionError("cannot publish into locked/retired strategy")
            db.add(PlatformStrategyVersionRow(
                id=key, strategy_id=strategy_id, version=snap.version,
                parameters_json=snap.canonical_json(), content_sha256=snap.sha256(),
                created_by=actor.user_id, created_at_ms=now,
            ))
            db.flush()  # unique strategy/version constraint is enforced by DB
            _audit(db, actor, strategy.owner_id, "publish_version", "version",
                   key, reason, None, snap.sha256())
        return key

    def create_offline_run(self, *, access_token: str, version_id: str,
                           mode: str = "validation", reason: str) -> str:
        actor, reason = _identity(access_token), _reason(reason)
        intent = StrategyRunIntent(version_id=version_id, requested_by=actor.user_id, mode=mode)
        key, now = str(uuid4()), int(time.time() * 1000)
        with session_scope(self._session_factory) as db:
            version = _version(db, version_id)
            strategy = _strategy(db, actor, version.strategy_id)
            if strategy.is_locked or strategy.state == "retired":
                raise PermissionError("cannot start a locked/retired strategy")
            db.add(PlatformStrategyRunRow(
                id=key, version_id=intent.version_id,
                requested_by=actor.user_id, mode=intent.mode, state="queued",
                created_at_ms=now, updated_at_ms=now,
            ))
            db.flush()
            _audit(db, actor, strategy.owner_id, "create_run", "run",
                   key, reason, None, version.content_sha256)
        return key
