"""
Symmetric encryption for secrets at rest (Kite API secrets / access tokens).

Uses Fernet (AES-128-CBC + HMAC) from ``cryptography`` when available, keyed by
``STERLING_SECRET_KEY``. If the key is absent a deterministic dev key is derived
and a loud warning is logged (never do this in production). If ``cryptography``
itself is unavailable the module degrades to base64 obfuscation with a warning, so
dev/test still works — but that is NOT real encryption.

Ciphertext is prefixed with a scheme tag (``fernet:`` / ``b64:``) so values can be
decrypted regardless of which backend wrote them.
"""
from __future__ import annotations

import base64
import hashlib
import os

from app.core.logging import get_logger

log = get_logger(__name__)

_FERNET_PREFIX = "fernet:"
_B64_PREFIX = "b64:"

_fernet = None
_backend = "b64"
_active_secret_hash = ""


def _derive_fernet_key(secret: str) -> bytes:
    """A urlsafe-base64 32-byte key derived from the configured secret."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_jwt_key() -> str:
    """Return the signing key used for JWT access and refresh tokens."""
    return (
        os.environ.get("STERLING_JWT_SECRET")
        or os.environ.get("STERLING_SECRET_KEY")
        or "sterling-dev-jwt-secret-key"
    )


def _init() -> None:
    """Initialise encryption for the current configured key.

    A process must never keep encrypting under the deterministic development
    key after a real key becomes configured. Track a key fingerprint and rebuild
    the Fernet instance when the effective secret changes.
    """
    global _fernet, _backend, _active_secret_hash
    secret = os.environ.get("STERLING_SECRET_KEY", "")
    if not secret:
        secret = "sterling-dev-insecure-key"
        log.warning(
            "STERLING_SECRET_KEY not set — using an insecure dev key for secret "
            "encryption. Set STERLING_SECRET_KEY before configuring a live broker."
        )
    fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    if (
        _active_secret_hash == fingerprint
        and _backend == "fernet"
        and _fernet is not None
    ):
        return

    _fernet = None
    _backend = "b64"
    _active_secret_hash = fingerprint
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_derive_fernet_key(secret))
        _backend = "fernet"
    except Exception as exc:  # pragma: no cover - only when cryptography missing
        log.warning("cryptography unavailable (%s) — falling back to base64 obfuscation "
                    "(NOT encryption). Install 'cryptography' for at-rest encryption.", exc)
        _backend = "b64"


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Empty input → empty output."""
    if not plaintext:
        return ""
    _init()
    if _backend == "fernet" and _fernet is not None:
        token = _fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        return _FERNET_PREFIX + token
    return _B64_PREFIX + base64.urlsafe_b64encode(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a stored secret. Tolerates legacy plaintext (returns as-is)."""
    if not ciphertext:
        return ""
    _init()
    if ciphertext.startswith(_FERNET_PREFIX):
        if _fernet is None:
            raise RuntimeError("Cannot decrypt Fernet secret — cryptography unavailable.")
        return _fernet.decrypt(ciphertext[len(_FERNET_PREFIX):].encode("utf-8")).decode("utf-8")
    if ciphertext.startswith(_B64_PREFIX):
        return base64.urlsafe_b64decode(ciphertext[len(_B64_PREFIX):].encode("utf-8")).decode("utf-8")
    # Legacy/plaintext value written before encryption was introduced.
    return ciphertext


DEV_FALLBACK_VALUES = {
    "sterling-dev-insecure-key",
    "sterling-dev-jwt-secret-key",
    "dev-secret",
    "secret",
    "change-me",
}


def _validate_strict_security(scope: str) -> None:
    """Apply the security posture required by any real-money broker runtime."""
    error = f"{scope} security error"

    secret = os.environ.get("STERLING_SECRET_KEY", "")
    if not secret or secret in DEV_FALLBACK_VALUES or len(secret) < 32:
        raise RuntimeError(
            f"{error}: STERLING_SECRET_KEY must be a distinct, randomly generated "
            f"secret of at least 32 characters (found length {len(secret)})."
        )

    jwt_secret = os.environ.get("STERLING_JWT_SECRET", "")
    if not jwt_secret or jwt_secret in DEV_FALLBACK_VALUES or len(jwt_secret) < 32:
        raise RuntimeError(
            f"{error}: STERLING_JWT_SECRET must be a distinct, randomly generated "
            f"secret of at least 32 characters (found length {len(jwt_secret)})."
        )

    if secret == jwt_secret:
        raise RuntimeError(
            f"{error}: STERLING_SECRET_KEY and STERLING_JWT_SECRET must be distinct"
        )

    from urllib.parse import urlsplit
    from app.core.config import settings

    for raw_origin in settings.cors_origins:
        origin = str(raw_origin or "").strip()
        if not origin or origin in {"*", "null"}:
            raise RuntimeError(
                f"{error}: CORS origins must be explicit trusted origins"
            )
        parsed = urlsplit(origin)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RuntimeError(f"{error}: invalid CORS origin {origin!r}")
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not loopback:
            raise RuntimeError(
                f"{error}: non-loopback CORS origins must use HTTPS"
            )

    _init()
    if _backend != "fernet" or _fernet is None:
        raise RuntimeError(
            f"{error}: Fernet encryption is unavailable (weaker backend active)"
        )

    from app.services import db

    if not (hasattr(db, "is_available") and db.is_available()):
        raise RuntimeError(
            f"{error}: Database store is uninitialized or unavailable for credential inspection"
        )

    try:
        configs = db.get_all_configs()
        for k, v in configs.items():
            if ("secret" in k.lower() or "token" in k.lower() or "password" in k.lower()) and v:
                if not v.startswith(_FERNET_PREFIX):
                    raise RuntimeError(
                        f"{error}: Secret in system_config for key '{k}' is not Fernet-encrypted"
                    )
                try:
                    decrypt(v)
                except Exception as exc:
                    raise RuntimeError(
                        f"{error}: Secret in system_config for key '{k}' failed decryption: {exc}"
                    )

        with db._conn() as conn:
            table_names = {
                (row["name"] if hasattr(row, "keys") else row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "exchange_configs" in table_names:
                legacy_rows = conn.execute(
                    "SELECT id, api_key, api_secret FROM exchange_configs "
                    "WHERE COALESCE(api_key, '') <> '' OR COALESCE(api_secret, '') <> ''"
                ).fetchall()
                if legacy_rows:
                    raise RuntimeError(
                        f"{error}: legacy exchange_configs contains plaintext broker "
                        "credentials. The legacy exchange service is removed; "
                        "export/rotate those credentials and clear the legacy rows "
                        "before live startup."
                    )

        with db._conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            for table in tables:
                tname = table["name"] if isinstance(table, dict) else table[0]
                cols_info = conn.execute(f"PRAGMA table_info({tname})").fetchall()
                cols = [
                    info["name"] if isinstance(info, dict) else info[1]
                    for info in cols_info
                ]
                enc_cols = [col for col in cols if col.endswith("_enc")]
                if not enc_cols:
                    continue
                rows = conn.execute(
                    f"SELECT {', '.join(enc_cols)} FROM {tname}"
                ).fetchall()
                for row in rows:
                    for index, col in enumerate(enc_cols):
                        val = row[col] if hasattr(row, "keys") else row[index]
                        if not val:
                            continue
                        sval = str(val)
                        if not sval.startswith(_FERNET_PREFIX):
                            raise RuntimeError(
                                f"{error}: Credential column '{col}' in table "
                                f"'{tname}' contains unencrypted/legacy value "
                                f"(must start with '{_FERNET_PREFIX}')"
                            )
                        try:
                            decrypt(sval)
                        except Exception as exc:
                            raise RuntimeError(
                                f"{error}: Credential in '{tname}.{col}' failed "
                                f"decryption under active key: {exc}"
                            )
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc).startswith(error + ":"):
            raise
        raise RuntimeError(
            f"{error}: Credential store inspection failed: {exc}"
        ) from exc


def validate_live_security() -> None:
    """Require production-grade security for any live broker authority.

    This is independent of ENVIRONMENT. A process with a live Kite account can
    move real money even when somebody accidentally labels it development, so
    that label cannot weaken encryption, JWT signing, browser trust, or
    persisted-credential integrity.
    """
    _validate_strict_security("Live")


def validate_production_security() -> None:
    """Enforce the same strict posture for every production-labelled process."""
    from app.core import runtime_env

    if not runtime_env.is_production():
        return
    _validate_strict_security("Production")

