"""
User store — SQLite ``users`` table + write-through in-memory dict.

Same proven persistence idiom as
:mod:`app.services.exchanges.kite.accounts` (idempotent ``_init_table`` with
additive ``ALTER``, ``bootstrap`` load, CRUD), but for first-party identities.

Passwords are stored as Argon2id hashes (:mod:`app.core.passwords`) — one-way,
never reversible ``security.encrypt``.

The bootstrap admin is seeded with ``id="default"`` on purpose: the pre-auth
system attributed everything to the ``"default"`` user id, so seeding the admin
with that id keeps every existing per-user row (kite accounts, navigator configs,
paper positions) owned by a real, now-authenticated account.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.core.logging import get_logger
from app.core.passwords import hash_password, verify_password

log = get_logger(__name__)

DEFAULT_ADMIN_ID = "default"

_users: Dict[str, "_User"] = {}
_loaded = False


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class _User:
    id: str
    username: str
    password_hash: str = ""
    role: str = "user"                       # "admin" | "user"
    is_active: bool = True
    token_version: int = 1
    created_at_ms: int = field(default_factory=_now_ms)
    updated_at_ms: int = field(default_factory=_now_ms)


# ─── SQLite persistence ───────────────────────────────────────────────────────
def _init_table() -> None:
    from app.services import db
    if not db._available:
        return
    try:
        with db._conn() as c:
            c.execute("BEGIN")
            try:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id            TEXT PRIMARY KEY,
                        username      TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL DEFAULT '',
                        role          TEXT NOT NULL DEFAULT 'user',
                        is_active     INTEGER NOT NULL DEFAULT 1,
                        token_version INTEGER NOT NULL DEFAULT 1,
                        created_at_ms INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL
                    )
                """)
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username ON users(username)")
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
    except Exception as exc:
        log.warning("users table init failed: %s", exc)


def _persist(u: "_User") -> None:
    from app.services import db
    if not db._available:
        return
    try:
        with db._conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO users
                    (id, username, password_hash, role, is_active, token_version,
                     created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                u.id, u.username, u.password_hash, u.role, int(u.is_active),
                int(u.token_version), u.created_at_ms, u.updated_at_ms,
            ))
    except Exception as exc:
        log.warning("users persist failed for %s: %s", u.id, exc)


def _load_from_db() -> List["_User"]:
    from app.services import db
    if not db._available:
        return []
    try:
        with db._conn() as c:
            rows = c.execute("SELECT * FROM users").fetchall()
        return [
            _User(
                id=r["id"], username=r["username"], password_hash=r["password_hash"],
                role=r["role"], is_active=bool(r["is_active"]),
                token_version=int(r["token_version"]),
                created_at_ms=r["created_at_ms"], updated_at_ms=r["updated_at_ms"],
            )
            for r in rows
        ]
    except Exception as exc:
        log.warning("users load failed: %s", exc)
        return []


def bootstrap() -> None:
    global _loaded
    if _loaded:
        return
    _init_table()
    for u in _load_from_db():
        _users[u.id] = u
    _maybe_seed_admin()
    _loaded = True


_MIN_ADMIN_PASSWORD_LEN = 12
_COMMON_PASSWORDS = frozenset({
    "admin", "administrator", "password", "password1", "passw0rd", "changeme", "letmein",
    "welcome", "qwerty", "123456", "12345678", "sterling", "sterling123",
})


def _is_weak_admin_password(password: str) -> bool:
    """An administrator password must be long and not a well-known default."""
    value = (password or "").strip()
    return len(value) < _MIN_ADMIN_PASSWORD_LEN or value.lower() in _COMMON_PASSWORDS


def _maybe_seed_admin() -> None:
    """Seed a bootstrap admin (id="default") when no users exist yet.

    Credentials come from ``STERLING_ADMIN_USERNAME`` / ``STERLING_ADMIN_PASSWORD``.
    No environment gets an unrecoverable generated password. Local loopback
    development can use the anonymous development identity while no live broker
    authority exists; a real broker account requires an explicit administrator
    credential first."""
    if _users:
        return
    username = (os.environ.get("STERLING_ADMIN_USERNAME", "") or "admin").strip() or "admin"
    password = (os.environ.get("STERLING_ADMIN_PASSWORD", "") or "").strip()
    if not password:
        # Local development can use the loopback-only anonymous identity while
        # there is no live broker account. Do not manufacture an administrator
        # password nobody can recover: adding live broker authority requires a
        # real admin credential first.
        log.warning(
            "No bootstrap administrator was seeded because "
            "STERLING_ADMIN_PASSWORD is unset. Local anonymous development remains "
            "available until a live broker account is configured."
        )
        return

    if _is_weak_admin_password(password):
        log.error(
            "STERLING_ADMIN_PASSWORD is too weak for an administrator (at least %d "
            "characters, not a common password) — refusing to seed it.",
            _MIN_ADMIN_PASSWORD_LEN,
        )
        return
    u = _User(
        id=DEFAULT_ADMIN_ID, username=username,
        password_hash=hash_password(password), role="admin", is_active=True,
    )
    _users[u.id] = u
    _persist(u)
    log.info("Seeded bootstrap admin id=%s username=%s", u.id, u.username)


# ─── Lookups / CRUD ───────────────────────────────────────────────────────────
def get_by_id(user_id: str) -> Optional["_User"]:
    return _users.get(user_id)


def get_by_username(username: str) -> Optional["_User"]:
    if not username:
        return None
    uname = username.strip()
    return next((u for u in _users.values() if u.username == uname), None)


def add(username: str, password: str, role: str = "user",
        user_id: Optional[str] = None) -> "_User":
    if get_by_username(username):
        raise ValueError(f"username already exists: {username!r}")
    if str(role or "").lower() == "admin" and _is_weak_admin_password(password):
        raise ValueError(
            f"administrator password must be at least {_MIN_ADMIN_PASSWORD_LEN} "
            "characters and not a common password"
        )
    import uuid
    u = _User(
        id=user_id or ("USR-" + uuid.uuid4().hex[:10].upper()),
        username=username.strip(),
        password_hash=hash_password(password),
        role=role,
    )
    _users[u.id] = u
    _persist(u)
    return u


def set_password(user_id: str, password: str) -> Optional["_User"]:
    u = _users.get(user_id)
    if not u:
        return None
    if str(u.role or "").lower() == "admin" and _is_weak_admin_password(password):
        raise ValueError(
            f"administrator password must be at least {_MIN_ADMIN_PASSWORD_LEN} "
            "characters and not a common password"
        )
    u.password_hash = hash_password(password)
    u.updated_at_ms = _now_ms()
    _persist(u)
    return u


def bump_token_version(user_id: str) -> Optional["_User"]:
    """Invalidate every outstanding token for a user (logout-all)."""
    u = _users.get(user_id)
    if not u:
        return None
    u.token_version += 1
    u.updated_at_ms = _now_ms()
    _persist(u)
    return u


def verify_credentials(username: str, password: str) -> Optional["_User"]:
    """Return the user iff the password matches and the account is active.

    Anti-enumeration: callers should still run a dummy verify on the miss path so
    a nonexistent username costs the same time as a wrong password."""
    u = get_by_username(username)
    if u is None or not u.is_active:
        return None
    if not verify_password(password, u.password_hash):
        return None
    return u


def all_users() -> List["_User"]:
    return list(_users.values())


def has_active_admin_credential() -> bool:
    """Whether production has at least one usable administrator identity.

    Startup cannot prove a password without knowing it, but it can reject the
    dangerous persisted states that are objectively unusable or legacy:
    inactive-only users, user-only roles, blank hashes, and non-Argon2id hashes.
    """
    return any(
        u.is_active
        and u.role == "admin"
        and str(u.password_hash or "").startswith("$argon2id$")
        for u in _users.values()
    )


def clear() -> None:
    """Test hook — wipe in-memory state and force the next bootstrap to reseed."""
    global _loaded
    _users.clear()
    _loaded = False
