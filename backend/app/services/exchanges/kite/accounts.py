"""
Multi-tenant Kite account store.

Per-user credential + session persistence (SQLite ``kite_accounts`` table,
write-through over an in-memory dict — same proven pattern as
``exchange_account_store``, but scoped by ``user_id`` and with secrets encrypted
at rest via :mod:`app.core.security`).

Secrets (``api_secret``, ``access_token``) are encrypted; ``api_key`` is stored in
the clear (it is semi-public — it appears in the login URL). Responses never leak
raw secrets, only a masked hint.
"""
from __future__ import annotations

import time
import uuid
import hashlib
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.core.security import decrypt, encrypt

from . import session as _session
from .models import KiteAccountCreate, KiteAccountResponse, KiteAccountUpdate

log = get_logger(__name__)

_accounts: Dict[str, "_Account"] = {}
_loaded = False


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return "KITE-" + uuid.uuid4().hex[:10].upper()


@dataclass
class _Account:
    id: str
    user_id: str
    label: str
    api_key: str = ""
    api_secret_enc: str = ""
    access_token_enc: str = ""
    refresh_token_enc: str = ""
    public_token: str = ""
    kite_user_id: str = ""
    user_name: str = ""
    is_paper: bool = True
    is_active: bool = False
    last_login_at_ms: Optional[int] = None
    token_expires_at_ms: Optional[int] = None
    created_at_ms: int = field(default_factory=_now_ms)
    updated_at_ms: int = field(default_factory=_now_ms)

    # ── derived ──
    @property
    def api_secret(self) -> str:
        return decrypt(self.api_secret_enc)

    @property
    def access_token(self) -> str:
        return decrypt(self.access_token_enc)

    @property
    def refresh_token(self) -> str:
        return decrypt(self.refresh_token_enc)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret_enc)

    @property
    def connected(self) -> bool:
        return bool(self.access_token_enc)

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.refresh_token_enc)

    @property
    def token_expires_at(self) -> Optional[int]:
        """When this access_token dies, in epoch-ms — ``None`` when unknowable.

        Prefers the stamp recorded at login. Rows written before expiry tracking
        existed carry no stamp, so we derive one from ``last_login_at_ms``: Kite's
        reset is a wall-clock event, so the boundary is reconstructible from the
        login time alone. Only a row with neither is genuinely unknown, and those
        fall through to a network validation rather than being trusted.
        """
        if self.token_expires_at_ms:
            return self.token_expires_at_ms
        if self.last_login_at_ms:
            return _session.token_expiry_ms(self.last_login_at_ms)
        return None

    @property
    def token_is_live(self) -> bool:
        """A stored token still inside its validity window. Says nothing about
        whether Kite would *accept* it (revoked, logged out elsewhere) — proving
        that is :func:`app.services.exchanges.kite.auth.ensure_session`'s job."""
        if not self.access_token_enc:
            return False
        expires = self.token_expires_at
        if expires is None:
            return False                      # unknown ⇒ not provable ⇒ validate
        return not _session.is_expired(expires)

    def api_key_hint(self) -> str:
        if not self.api_key or len(self.api_key) < 4:
            return "****"
        return "****" + self.api_key[-4:]


# ─── SQLite persistence ───────────────────────────────────────────────────────
def _init_table() -> None:
    from app.services import db
    if not db._available:
        try:
            db.init()
        except Exception as exc:
            raise RuntimeError("kite account database unavailable") from exc
    if not db._available:
        raise RuntimeError("kite account database unavailable")
    try:
        with db._conn() as c:
            c.execute("BEGIN")
            try:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS kite_accounts (
                        id               TEXT PRIMARY KEY,
                        user_id          TEXT NOT NULL,
                        label            TEXT NOT NULL DEFAULT 'My Kite',
                        api_key          TEXT NOT NULL DEFAULT '',
                        api_secret_enc   TEXT NOT NULL DEFAULT '',
                        access_token_enc TEXT NOT NULL DEFAULT '',
                        refresh_token_enc TEXT NOT NULL DEFAULT '',
                        public_token     TEXT NOT NULL DEFAULT '',
                        kite_user_id     TEXT NOT NULL DEFAULT '',
                        user_name        TEXT NOT NULL DEFAULT '',
                        is_paper         INTEGER NOT NULL DEFAULT 1,
                        is_active        INTEGER NOT NULL DEFAULT 0,
                        last_login_at_ms INTEGER,
                        token_expires_at_ms INTEGER,
                        created_at_ms    INTEGER NOT NULL,
                        updated_at_ms    INTEGER NOT NULL
                    )
                """)
                c.execute("CREATE INDEX IF NOT EXISTS ix_kite_accounts_user ON kite_accounts(user_id)")
                # Execution identity is a database invariant, not just an
                # in-process convention. Partial unique indexes let many inactive
                # accounts coexist while making ambiguous routing impossible
                # across workers and restarts.
                c.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ux_kite_accounts_one_active_per_user "
                    "ON kite_accounts(user_id) WHERE is_active=1"
                )
                c.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ux_kite_accounts_kite_user_id "
                    "ON kite_accounts(kite_user_id) WHERE kite_user_id<>''"
                )
                # Idempotent migrations for DBs created before these columns existed.
                # Each ALTER is tried independently so one already-applied column
                # cannot mask a genuinely missing later one.
                for _ddl in (
                    "ALTER TABLE kite_accounts ADD COLUMN refresh_token_enc TEXT NOT NULL DEFAULT ''",
                    "ALTER TABLE kite_accounts ADD COLUMN token_expires_at_ms INTEGER",
                    "ALTER TABLE kite_accounts ADD COLUMN user_name TEXT NOT NULL DEFAULT ''",
                ):
                    try:
                        c.execute(_ddl)
                    except Exception as _exc:
                        log.debug("suppressed: %s", _exc)
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
    except Exception as exc:
        log.error("kite_accounts table init failed: %s", exc)
        raise RuntimeError("kite account table initialization failed") from exc


def _persist(a: _Account) -> None:
    """Persist one account or raise; execution identity is never memory-only."""
    from app.services import db

    # Direct unit/service callers may reach add() before bootstrap(). Make the
    # persistence contract self-contained rather than depending on call order.
    _init_table()
    if not db._available:
        raise RuntimeError("kite account database unavailable")
    try:
        with db._conn() as c:
            c.execute("""
                INSERT INTO kite_accounts
                    (id, user_id, label, api_key, api_secret_enc, access_token_enc,
                     refresh_token_enc, public_token, kite_user_id, user_name, is_paper,
                     is_active, last_login_at_ms, token_expires_at_ms, created_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    user_id=excluded.user_id,
                    label=excluded.label,
                    api_key=excluded.api_key,
                    api_secret_enc=excluded.api_secret_enc,
                    access_token_enc=excluded.access_token_enc,
                    refresh_token_enc=excluded.refresh_token_enc,
                    public_token=excluded.public_token,
                    kite_user_id=excluded.kite_user_id,
                    user_name=excluded.user_name,
                    is_paper=excluded.is_paper,
                    is_active=excluded.is_active,
                    last_login_at_ms=excluded.last_login_at_ms,
                    token_expires_at_ms=excluded.token_expires_at_ms,
                    created_at_ms=excluded.created_at_ms,
                    updated_at_ms=excluded.updated_at_ms
            """, (
                a.id, a.user_id, a.label, a.api_key, a.api_secret_enc, a.access_token_enc,
                a.refresh_token_enc, a.public_token, a.kite_user_id, a.user_name,
                int(a.is_paper), int(a.is_active), a.last_login_at_ms, a.token_expires_at_ms,
                a.created_at_ms, a.updated_at_ms,
            ))
    except Exception as exc:
        log.error("kite_accounts persist failed for %s: %s", a.id, exc)
        raise RuntimeError(f"kite account persistence failed:{a.id}") from exc


def _publish(candidate: _Account) -> _Account:
    """Publish a durably-written candidate without invalidating held references."""
    current = _accounts.get(candidate.id)
    if current is None:
        _accounts[candidate.id] = candidate
        return candidate
    current.__dict__.update(candidate.__dict__)
    return current


def _delete_db_state(
    user_id: str, account_id: str, replacement_id: str | None
) -> None:
    """Delete one account and, when needed, promote its replacement atomically."""
    from app.services import db

    if not db._available:
        raise RuntimeError("kite account database unavailable")

    now = _now_ms()
    try:
        with db._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT is_active FROM kite_accounts WHERE id=? AND user_id=?",
                (account_id, user_id),
            ).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                raise RuntimeError("kite account disappeared during delete")
            was_active = bool(row["is_active"])
            c.execute(
                "DELETE FROM kite_accounts WHERE id=? AND user_id=?",
                (account_id, user_id),
            )
            if was_active:
                c.execute(
                    "UPDATE kite_accounts SET is_active=0, updated_at_ms=? "
                    "WHERE user_id=?",
                    (now, user_id),
                )
                if replacement_id:
                    changed = c.execute(
                        "UPDATE kite_accounts SET is_active=1, updated_at_ms=? "
                        "WHERE id=? AND user_id=?",
                        (now, replacement_id, user_id),
                    ).rowcount
                    if changed != 1:
                        c.execute("ROLLBACK")
                        raise RuntimeError("replacement Kite account is not durable")
            c.execute("COMMIT")
    except Exception as exc:
        log.error("kite_accounts delete transaction failed: %s", exc)
        raise


def _set_active_db(user_id: str, account_id: str, updated_at_ms: int) -> None:
    """Persist exactly one active account for a user in one transaction."""
    from app.services import db

    if not db._available:
        raise RuntimeError("kite account database unavailable")
    try:
        with db._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            exists = c.execute(
                "SELECT 1 FROM kite_accounts WHERE id=? AND user_id=?",
                (account_id, user_id),
            ).fetchone()
            if exists is None:
                c.execute("ROLLBACK")
                raise RuntimeError("target Kite account is not durable")
            c.execute(
                "UPDATE kite_accounts SET is_active=0, updated_at_ms=? "
                "WHERE user_id=?",
                (updated_at_ms, user_id),
            )
            changed = c.execute(
                "UPDATE kite_accounts SET is_active=1, updated_at_ms=? "
                "WHERE id=? AND user_id=?",
                (updated_at_ms, account_id, user_id),
            ).rowcount
            if changed != 1:
                c.execute("ROLLBACK")
                raise RuntimeError("failed to persist active Kite account")
            c.execute("COMMIT")
    except Exception as exc:
        log.error("kite_accounts active-account transaction failed: %s", exc)
        raise


def _load_from_db() -> List[_Account]:
    from app.services import db
    if not getattr(db, "_available", False):
        try:
            db.init()
        except Exception as exc:
            raise RuntimeError("kite account database unavailable") from exc
    if not getattr(db, "_available", False):
        raise RuntimeError("kite account database unavailable")
    try:
        with db._conn() as c:
            rows = c.execute("SELECT * FROM kite_accounts").fetchall()
        out = []
        for r in rows:
            out.append(_Account(
                id=r["id"], user_id=r["user_id"], label=r["label"], api_key=r["api_key"],
                api_secret_enc=r["api_secret_enc"], access_token_enc=r["access_token_enc"],
                refresh_token_enc=(r["refresh_token_enc"] if "refresh_token_enc" in r.keys() else ""),
                public_token=r["public_token"], kite_user_id=r["kite_user_id"],
                user_name=(r["user_name"] if "user_name" in r.keys() else ""),
                is_paper=bool(r["is_paper"]), is_active=bool(r["is_active"]),
                last_login_at_ms=r["last_login_at_ms"],
                token_expires_at_ms=(r["token_expires_at_ms"]
                                     if "token_expires_at_ms" in r.keys() else None),
                created_at_ms=r["created_at_ms"], updated_at_ms=r["updated_at_ms"],
            ))
        return out
    except Exception as exc:
        log.error("kite_accounts load failed: %s", exc)
        raise RuntimeError("kite account store unreadable") from exc


def bootstrap() -> None:
    global _loaded
    if _loaded:
        return
    _init_table()
    for a in _load_from_db():
        _accounts[a.id] = a
    _loaded = True


# ─── CRUD (user-scoped) ───────────────────────────────────────────────────────
def add(user_id: str, data: KiteAccountCreate) -> _Account:
    first_for_user = not any(a.user_id == user_id for a in _accounts.values())
    candidate = _Account(
        id=_new_id(), user_id=user_id, label=data.label or "My Kite",
        api_key=data.api_key, api_secret_enc=encrypt(data.api_secret),
        is_paper=data.is_paper, is_active=first_for_user,  # first account auto-active
    )
    # Durability first: a successful API response must survive restart.
    _persist(candidate)
    return _publish(candidate)


def get(user_id: str, account_id: str) -> Optional[_Account]:
    a = _accounts.get(account_id)
    return a if a and a.user_id == user_id else None


def list_accounts(user_id: str) -> List[_Account]:
    return [a for a in _accounts.values() if a.user_id == user_id]


def all_accounts() -> List[_Account]:
    """Every stored account across all tenants. For background workers that must
    tend sessions with no request (and therefore no user) in scope."""
    return list(_accounts.values())


def update(user_id: str, account_id: str, data: KiteAccountUpdate) -> Optional[_Account]:
    a = get(user_id, account_id)
    if not a:
        return None
    candidate = replace(a)
    credentials_changed = False
    if data.label is not None:
        candidate.label = data.label
    if data.api_key is not None and data.api_key != a.api_key:
        candidate.api_key = data.api_key
        credentials_changed = True
    if data.api_secret is not None:
        encrypted_secret = encrypt(data.api_secret)
        if encrypted_secret != a.api_secret_enc:
            candidate.api_secret_enc = encrypted_secret
            credentials_changed = True
    if data.is_paper is not None:
        candidate.is_paper = data.is_paper

    if credentials_changed:
        # Access/refresh tokens are issued under a concrete Kite app identity.
        # Carrying them across an API-key/secret rotation mixes two identities
        # and can silently renew the old session under new credentials.
        candidate.access_token_enc = ""
        candidate.refresh_token_enc = ""
        candidate.public_token = ""
        candidate.token_expires_at_ms = None

    candidate.updated_at_ms = _now_ms()
    _persist(candidate)
    published = _publish(candidate)
    if credentials_changed:
        _validated_at.pop(published.id, None)
    return published


def delete(user_id: str, account_id: str) -> bool:
    a = get(user_id, account_id)
    if not a:
        return False
    was_active = a.is_active
    remaining = [
        item for item in list_accounts(user_id)
        if item.id != account_id
    ]
    replacement = remaining[0] if was_active and remaining else None

    # Durability first. If this fails, in-memory routing remains untouched.
    _delete_db_state(
        user_id,
        account_id,
        replacement.id if replacement is not None else None,
    )

    del _accounts[account_id]
    if was_active:
        now = _now_ms()
        for item in remaining:
            item.is_active = bool(replacement is not None and item.id == replacement.id)
            if item.is_active:
                item.updated_at_ms = now
    return True


def set_active(user_id: str, account_id: str) -> Optional[_Account]:
    target = get(user_id, account_id)
    if not target:
        return None

    now = _now_ms()
    # Persist the identity switch atomically before changing runtime routing.
    _set_active_db(user_id, account_id, now)

    for a in list_accounts(user_id):
        want = a.id == account_id
        if a.is_active != want:
            a.is_active = want
            a.updated_at_ms = now
    return get(user_id, account_id)


def get_active(user_id: str) -> Optional[_Account]:
    matches = [
        a for a in _accounts.values()
        if a.user_id == user_id and a.is_active
    ]
    if len(matches) > 1:
        log.error(
            "kite account identity ambiguous for %s: %s active rows",
            user_id,
            len(matches),
        )
        return None
    return matches[0] if matches else None


def find_by_kite_user_id(kite_user_id: str) -> Optional[_Account]:
    """Resolve a Zerodha user id (carried in order postbacks) → stored account.

    Order postbacks identify the trader by their Kite ``user_id``, not our app
    ``user_id``; this maps it back so the update can be routed to the right tenant.
    """
    if not kite_user_id:
        return None
    matches = [a for a in _accounts.values() if a.kite_user_id == kite_user_id]
    if len(matches) > 1:
        log.error(
            "kite postback identity ambiguous for %s: %s stored accounts",
            kite_user_id,
            len(matches),
        )
        return None
    return matches[0] if matches else None


def _assert_unique_kite_user_id(account_id: str, kite_user_id: str) -> None:
    """One broker identity may belong to only one stored Sterling account."""
    value = str(kite_user_id or "").strip()
    if not value:
        return
    conflicts = [
        a for a in _accounts.values()
        if a.id != account_id and str(a.kite_user_id or "").strip() == value
    ]
    if conflicts:
        raise ValueError(
            f"kite_user_id_already_bound:{value}:{conflicts[0].id}"
        )


def _resolved_kite_user_id(a: _Account, incoming: str) -> str:
    """Resolve broker identity without ever silently rebinding an account."""
    current = str(a.kite_user_id or "").strip()
    supplied = str(incoming or "").strip()
    if current and supplied and current != supplied:
        raise ValueError(
            f"kite_user_id_mismatch:{current}!={supplied}"
        )
    resolved = supplied or current
    if not a.is_paper and not resolved:
        raise ValueError("live_kite_session_identity_missing")
    _assert_unique_kite_user_id(a.id, resolved)
    return resolved


def save_session(user_id: str, account_id: str, *, access_token: str,
                 refresh_token: str = "", public_token: str = "",
                 kite_user_id: str = "", user_name: str = "",
                 expires_at_ms: Optional[int] = None) -> Optional[_Account]:
    a = get(user_id, account_id)
    if not a:
        return None

    token = str(access_token or "").strip()
    if not token:
        raise ValueError("kite_access_token_missing")

    resolved_kite_user_id = _resolved_kite_user_id(a, kite_user_id)

    candidate = replace(a)
    candidate.access_token_enc = encrypt(token)
    # Keep the existing refresh_token if the caller didn't supply a new one (a
    # token refresh may not return one); only re-encrypt when present.
    if refresh_token:
        candidate.refresh_token_enc = encrypt(refresh_token)
    candidate.public_token = public_token
    candidate.kite_user_id = resolved_kite_user_id
    # Persisted so /status can name the trader without spending a /user/profile
    # call on every poll.
    candidate.user_name = user_name or a.user_name
    candidate.last_login_at_ms = _now_ms()
    # Stamp the validity window now, so every later "is this usable?" question is
    # answerable locally instead of costing a round-trip to Kite.
    candidate.token_expires_at_ms = expires_at_ms or _session.token_expiry_ms(
        candidate.last_login_at_ms
    )
    candidate.updated_at_ms = candidate.last_login_at_ms

    # Only publish "connected" and validation proof after durable persistence.
    _persist(candidate)
    published = _publish(candidate)
    mark_validated(published.id)
    return published


def clear_session(user_id: str, account_id: str) -> Optional[_Account]:
    """Remove every credential that can recreate an authenticated session.

    Revocation is safety-monotone: if durable persistence fails, runtime authority
    is still removed immediately and SAFE_MODE is engaged so a restart cannot
    quietly turn a stale on-disk token back into trading authority.
    """
    a = get(user_id, account_id)
    if not a:
        return None
    candidate = replace(a)
    candidate.access_token_enc = ""
    candidate.refresh_token_enc = ""
    candidate.public_token = ""
    candidate.token_expires_at_ms = None
    candidate.updated_at_ms = _now_ms()

    persistence_error: Exception | None = None
    try:
        _persist(candidate)
    except Exception as exc:  # noqa: BLE001
        persistence_error = exc

    published = _publish(candidate)
    _validated_at.pop(published.id, None)

    if persistence_error is not None:
        try:
            from app.services.safe_mode import SafeModeService, SafeModeTrigger
            from app.services.safety_supervisor import safe_mode_path

            SafeModeService(safe_mode_path()).engage(
                trigger=SafeModeTrigger.EXECUTION_DISCREPANCY,
                reason=(
                    f"Runtime Kite session for {account_id} was revoked but the "
                    f"durable account store could not be updated: {persistence_error}"
                ),
            )
        except Exception as safe_exc:  # noqa: BLE001
            log.critical(
                "Kite session persistence failed and SAFE_MODE could not engage: %s",
                safe_exc,
            )
        raise RuntimeError(
            f"kite session revocation persistence failed:{account_id}"
        ) from persistence_error

    return published


# ─── Validation cache ─────────────────────────────────────────────────────────
# A stored token being inside its window does not prove Kite still honours it (it
# could have been revoked, or the user logged out on another device). Proving that
# costs a /user/profile call, so we remember when we last proved it. In-memory on
# purpose: a process restart should re-prove once rather than trust a stamp
# written by a process that may have been down across the 06:00 IST reset.
_validated_at: Dict[str, int] = {}


def mark_validated(account_id: str) -> None:
    """Record that Kite accepted this account's token just now."""
    _validated_at[account_id] = _now_ms()


def validated_age_ms(account_id: str) -> Optional[int]:
    """How long ago Kite last accepted this token — ``None`` if never proven."""
    at = _validated_at.get(account_id)
    return None if at is None else _now_ms() - at


def set_identity(user_id: str, account_id: str, *, user_name: str = "",
                 kite_user_id: str = "") -> Optional[_Account]:
    """Record who this session belongs to, outside the login handshake — used to
    backfill accounts that connected before the identity was persisted."""
    a = get(user_id, account_id)
    if not a:
        return None
    if kite_user_id:
        _resolved_kite_user_id(a, kite_user_id)
    candidate = replace(a)
    changed = False
    if user_name and user_name != candidate.user_name:
        candidate.user_name, changed = user_name, True
    if kite_user_id and kite_user_id != candidate.kite_user_id:
        candidate.kite_user_id, changed = kite_user_id, True
    if changed:
        candidate.updated_at_ms = _now_ms()
        _persist(candidate)
        return _publish(candidate)
    return a


def forget_validation(account_id: str) -> None:
    """Drop the proof, forcing the next check to ask Kite. For tokens adopted from
    outside the login handshake (e.g. seeded from the environment), where we have
    no evidence Kite ever accepted them."""
    _validated_at.pop(account_id, None)


def to_response(a: _Account) -> KiteAccountResponse:
    return KiteAccountResponse(
        id=a.id, user_id=a.user_id, label=a.label, api_key_hint=a.api_key_hint(),
        has_credentials=a.has_credentials, is_paper=a.is_paper, is_active=a.is_active,
        connected=a.connected, has_refresh_token=a.has_refresh_token,
        kite_user_id=a.kite_user_id or None, user_name=a.user_name or None,
        last_login_at_ms=a.last_login_at_ms,
        token_expires_at_ms=a.token_expires_at,
        created_at_ms=a.created_at_ms, updated_at_ms=a.updated_at_ms,
    )


def build_client(a: _Account):
    """Construct a fresh KiteClient from a stored account (secrets decrypted
    in-memory). Prefer :func:`acquire_client` on hot paths — a fresh client has an
    empty instrument cache and no connection pool, so building one per request
    re-downloads the ~80k-row instrument dump and re-does the TLS handshake."""
    from .client import KiteClient
    client = KiteClient(
        api_key=a.api_key, api_secret=a.api_secret, access_token=a.access_token,
        is_paper=a.is_paper,
    )
    client._account_id = str(a.id)
    client._owner_user_id = str(a.user_id)
    client._kite_user_id = str(a.kite_user_id or "")
    client._account_generation = _client_identity(a)
    return client


# ─── Cached clients (warm instrument dump + connection pool, per account) ──────
# One live KiteClient per account, reused across requests so its InstrumentCache
# (1h TTL) and httpx connection pool stay warm. Identity changes (including paper
# mode) rebuild the client. Compare encrypted fields so the hot path never
# decrypts. This is the fix for instrument search taking minutes
# (every request used to rebuild the client and re-fetch the full dump).
_client_cache: Dict[str, Tuple[str, "object"]] = {}  # id -> (identity, client)


def _client_identity(a: _Account) -> str:
    """Fingerprint execution mode and encrypted credential identity without logging secrets."""
    values = (a.id, a.user_id, a.api_key, a.api_secret_enc, a.access_token_enc,
              str(a.is_paper), a.kite_user_id)
    return hashlib.sha256(repr(values).encode()).hexdigest()


def client_is_current(client, *, user_id: str) -> bool:
    """Verify a retained client still owns the account's current execution identity."""
    account = get(user_id, str(getattr(client, "_account_id", "") or ""))
    return bool(account is not None and
                getattr(client, "_account_generation", "") == _client_identity(account))


def _running_loop_id() -> int:
    """Which event loop is asking. Clients are not portable between loops."""
    import asyncio

    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


async def acquire_client(a: _Account):
    """Return a warm KiteClient for the account, reusing its instrument cache and
    connection pool. Execution mode and every credential/owner change rebuild it.

    A cached client also belongs to the event loop that built it: its HTTP
    session holds that loop's transports. A command-line process that calls
    `asyncio.run` twice gets a second loop, and the cached client then fails
    with "Event loop is closed" — which reads as "the broker is unreachable"
    and, for a flatness check, as "the account might be flat". So the loop is
    part of the cache identity."""
    cached = _client_cache.get(a.id)
    identity = (_client_identity(a), _running_loop_id())
    if cached is not None and cached[0] == identity:
        return cached[1]
    if cached is not None:                      # credentials rotated — drop the stale client
        try:
            await cached[1].close()
        except Exception as _exc:# noqa: BLE001
            log.debug("suppressed: %s", _exc)
    client = build_client(a)
    _client_cache[a.id] = (identity, client)
    return client


async def release_client(account_id: str) -> None:
    """Drop and close a cached client (account deleted / switched / logged out)."""
    cached = _client_cache.pop(account_id, None)
    if cached is not None:
        try:
            await cached[1].close()
        except Exception as _exc:# noqa: BLE001
            log.debug("suppressed: %s", _exc)


def clear() -> None:
    """Test hook — wipe in-memory state into a 'loaded & empty' state.

    Sets ``_loaded=True`` so a subsequent app-startup ``bootstrap()`` is a no-op and
    never reloads stale rows from a shared test DB (kite has no default account to
    seed, so there is nothing to bootstrap in tests). Keeps the suite deterministic
    regardless of whether another test has flipped ``db._available`` on."""
    global _loaded
    _accounts.clear()
    _validated_at.clear()
    _loaded = True
    from app.services import db
    if getattr(db, "_available", False):
        try:
            with db._conn() as c:
                c.execute("DELETE FROM kite_accounts")
        except Exception as _exc:  # noqa: BLE001
            log.debug("suppressed: %s", _exc)

