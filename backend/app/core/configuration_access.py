"""HTTP adapter; policy itself is independent of FastAPI."""
from fastapi import HTTPException
from app.core.auth import UserContext
from app.services.auth import configuration_policy, account_policy


def require_configuration(user: UserContext, section: str, action: str | None = None,
                          resource_id: str = 'own', *, administrator_only: bool = False):
    # Virtual simulation tenants never obtain their owner's live configuration.
    if getattr(user, 'view', '') or getattr(user, 'owner_uid', ''):
        raise HTTPException(403, 'Live configuration is unavailable in simulation.')
    try:
        return configuration_policy.require(user.user_id, section, action, resource_id,
                                            administrator_only=administrator_only)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except account_policy.PolicyUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
