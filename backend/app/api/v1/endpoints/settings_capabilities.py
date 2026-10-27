from typing import Literal
"""Secret-free configuration bootstrap and admin delegation adapters."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from app.core.auth import UserContext, get_current_user
from app.services.auth import account_policy, configuration_policy

router = APIRouter()


class ScopedGrant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: configuration_policy.Action
    resource_id: str = Field(min_length=1, max_length=128)
    expires_at_ms: int | None = Field(default=None, strict=True, ge=0, le=2**63 - 1)


class SectionCapabilities(BaseModel):
    visible: bool
    grants: list[ScopedGrant]


class CapabilitiesResponse(BaseModel):
    user_id: str
    revision: int
    administrator: bool
    active: bool
    sections: dict[configuration_policy.Section, SectionCapabilities]


class ConfigurationEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    section: configuration_policy.Section
    visible: StrictBool
    grants: list[ScopedGrant] = Field(default_factory=list, max_length=100)
    expected_revision: int = Field(ge=0, strict=True)


@router.get('/settings/capabilities', response_model=CapabilitiesResponse)
async def capabilities(response: Response, user: UserContext = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store, private'
    if user.view or user.owner_uid:
        raise HTTPException(403, 'Configuration is unavailable in simulation.')
    try:
        return configuration_policy.read(user.user_id)
    except account_policy.PolicyUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@router.get('/auth/users/{user_id}/configuration-access', response_model=CapabilitiesResponse)
async def inspect_configuration(user_id: str, response: Response, user: UserContext = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store, private'
    from app.core.configuration_access import require_configuration
    require_configuration(user, 'brokers', administrator_only=True)
    try:
        result = configuration_policy.read(user_id)
        if not result['active']:
            # Inactive accounts still need an inspectable configuration for restoration.
            from app.services.auth import user_store
            if not user_store.get_by_id(user_id):
                raise HTTPException(404, 'User not found.')
        return result
    except account_policy.PolicyUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@router.put('/auth/users/{user_id}/configuration-access', response_model=CapabilitiesResponse)
async def update_configuration(user_id: str, body: ConfigurationEdit, response: Response,
                               user: UserContext = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store, private'
    if user.view or user.owner_uid:
        raise HTTPException(403, 'Configuration is unavailable in simulation.')
    try:
        return configuration_policy.replace_section(user.user_id, user_id, body.section,
            visible=body.visible, grants=[g.model_dump() for g in body.grants], expected_revision=body.expected_revision)
    except account_policy.PolicyConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except account_policy.PolicyUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


class AccessRequestCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    feature: account_policy.Feature


class AccessDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['approved','rejected']


def request_operation(fn):
    try: return fn()
    except account_policy.PolicyConflict as exc: raise HTTPException(409,str(exc)) from None
    except PermissionError as exc: raise HTTPException(403,str(exc)) from None
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    except account_policy.PolicyUnavailable as exc: raise HTTPException(503,str(exc)) from None


@router.get('/settings/access-requests')
async def own_requests(response: Response, user: UserContext = Depends(get_current_user)):
    from app.services.auth import access_requests
    response.headers['Cache-Control']='no-store, private'
    if user.view or user.owner_uid: raise HTTPException(403,'Use your Sterling account to request access.')
    return {'requests': request_operation(lambda:access_requests.list_requests(user.user_id))}


@router.post('/settings/access-requests')
async def create_request(body: AccessRequestCreate, user: UserContext = Depends(get_current_user)):
    from app.services.auth import access_requests
    if user.view or user.owner_uid: raise HTTPException(403,'Use your Sterling account to request access.')
    return request_operation(lambda:access_requests.request_access(user.user_id,body.feature))


@router.get('/admin/access-requests')
async def admin_requests(response: Response, user: UserContext = Depends(get_current_user)):
    from app.services.auth import access_requests
    from app.core.configuration_access import require_configuration
    require_configuration(user,'brokers',administrator_only=True)
    response.headers['Cache-Control']='no-store, private'
    return {'requests':request_operation(lambda:access_requests.list_requests(user.user_id,administrator=True))}


@router.patch('/admin/access-requests/{request_id}')
async def review_request(request_id: str, body: AccessDecision, user: UserContext = Depends(get_current_user)):
    from app.services.auth import access_requests
    from app.core.configuration_access import require_configuration
    require_configuration(user,'brokers',administrator_only=True)
    return request_operation(lambda:access_requests.decide(user.user_id,request_id,body.decision))
