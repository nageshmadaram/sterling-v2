"""Phase 10 isolated offline-auth and MFA enforcement (never a trading app).

Only use via app.offline_security_profile; legacy main.py retains its separate
surface and is NOT certified by this policy. The first factor is the existing
signed and revocable Sterling access JWT; the second is a per-admin TOTP
from a root-owned 0400 secret file, never in source or API response.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import stat
import struct
import time
from pathlib import Path

import jwt
from fastapi import HTTPException
from app.core.security import get_jwt_key
from app.persistence import strategy_registry_service as registry

TTL=300
MAX_SKEW=1


def role_context(bearer:str):
    # JWT signature, expiry, access type and revocation via existing user store.
    actor=registry._identity(bearer)
    if actor.role not in ("admin","operator") or actor.view or actor.owner_uid:
        raise PermissionError("offline profile denies viewer and virtual tenants")
    return actor


def _totp(secret:str,counter:int)->str:
    key=base64.b32decode(secret.strip().upper(),casefold=True)
    if len(key)<20: raise ValueError("MFA secret too short")
    raw=hmac.new(key,struct.pack(">Q",counter),hashlib.sha1).digest()
    offset=raw[-1]&15
    return f"{(struct.unpack('>I',raw[offset:offset+4])[0]&0x7fffffff)%1000000:06d}"


def _mfa_users()->dict:
    path=os.environ.get("STERLING_OFFLINE_MFA_FILE","")
    if not path or not Path(path).is_absolute():
        raise PermissionError("per-admin MFA secret file required")
    file=Path(path)
    if file.is_symlink(): raise PermissionError("MFA secret symlink forbidden")
    mode=file.stat().st_mode
    if not stat.S_ISREG(mode) or mode&0o077:
        raise PermissionError("MFA secret file must be private 0400/0600")
    if file.stat().st_size>65_536: raise PermissionError("MFA secret file oversized")
    body=json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(body,dict): raise PermissionError("invalid MFA secret mapping")
    return body


def verify_totp_for_admin(user_id:str,code:str,now:int|None=None)->bool:
    if not isinstance(code,str) or len(code)!=6 or not code.isascii() or not code.isdigit():
        return False
    secret=_mfa_users().get(user_id)
    if not isinstance(secret,str): return False
    stamp=(int(time.time()) if now is None else int(now))//30
    valid=False
    for delta in range(-MAX_SKEW,MAX_SKEW+1):
        valid |= hmac.compare_digest(_totp(secret,stamp+delta),code)
    return valid


def issue_proof(bearer:str,code:str,now:int|None=None)->str:
    actor=role_context(bearer)
    if actor.role!="admin": raise PermissionError("only admins need MFA step-up")
    stamp=int(time.time()) if now is None else int(now)
    if not verify_totp_for_admin(actor.user_id,code,stamp):
        raise PermissionError("MFA verification refused")
    payload={"iss":"sterling-offline-mfa","typ":"offline_mfa","sub":actor.user_id,
             "binding":hashlib.sha256(bearer.encode()).hexdigest(),
             "iat":stamp,"exp":stamp+TTL}
    return jwt.encode(payload,get_jwt_key(),algorithm="HS256")


def check_access(bearer:str,proof:str|None,now:int|None=None):
    actor=role_context(bearer)
    if actor.role=="operator": return actor
    if not proof: raise PermissionError("admin MFA step-up required")
    try:
        payload=jwt.decode(proof,get_jwt_key(),algorithms=["HS256"],
            issuer="sterling-offline-mfa",options={"require":["exp","iat","sub","typ","binding"]},
            leeway=0)
    except jwt.PyJWTError as exc:
        raise PermissionError("admin MFA proof invalid or expired") from exc
    if (payload.get("typ")!="offline_mfa" or payload["sub"]!=actor.user_id or
        not hmac.compare_digest(str(payload["binding"]),
                               hashlib.sha256(bearer.encode()).hexdigest())):
        raise PermissionError("MFA proof not bound to current access session")
    if now is not None and (int(payload["exp"])<int(now) or int(payload["iat"])>int(now)):
        raise PermissionError("MFA proof out of time")
    return actor


def validate_profile_config():
    """Fail closed; never infer secure posture from ENVIRONMENT=test."""
    if os.environ.get("ENVIRONMENT","").lower()!="production":
        raise RuntimeError("offline secure profile must reject development identity seam")
    if os.environ.get("STERLING_OFFLINE_ONLY")!="true":
        raise RuntimeError("offline-only runtime flag required")
    if os.environ.get("STERLING_OFFLINE_REQUIRE_MFA")!="true":
        raise RuntimeError("admin MFA must be enforced")
    if os.environ.get("PAPER_TRADING","").lower()!="true":
        raise RuntimeError("paper/offline mode must be explicit")
    if os.environ.get("STERLING_OFFLINE_NO_BROKER")!="true":
        raise RuntimeError("broker access must be disallowed in isolated profile")
    if os.environ.get("STERLING_ACCESS_TTL_S","600") not in ("300","600"):
        raise RuntimeError("long access sessions forbidden")
    if os.environ.get("STERLING_REFRESH_TTL_S","86400") not in ("1800","3600","86400"):
        raise RuntimeError("long refresh sessions forbidden")
    from app.core.security import _validate_strict_security
    _validate_strict_security("offline-only deployment")
    # Read config without emitting any secrets.
    _mfa_users()
    url=os.environ.get("DATABASE_URL","")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("isolated offline-only profile requires PostgreSQL")
    if any(x in url.lower() for x in ("localhost","127.0.0.1")):
        raise RuntimeError("offline profile database must use isolated container network")
    return True
