"""Central management adapters over the existing assignment-safe provider stores.

These routes keep the real actor separate from the selected Sterling tenant.
They do not reinterpret management authority as trading or broker-login authority.
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from app.core.auth import UserContext, get_current_user
from app.core.configuration_access import require_configuration
from app.services.auth import user_store
from app.services.exchanges.kite import accounts
from app.services.exchanges.kite.models import KiteAccountCreate, KiteAccountUpdate
from app.services.execution_identity_lock import account_lock

router = APIRouter(prefix='/admin', tags=['administration'])


def _admin(user):
    require_configuration(user, 'brokers', administrator_only=True)


def _target(user_id):
    target = user_store.get_by_id(user_id)
    if not target or not target.is_active:
        raise HTTPException(422, 'Choose an active Sterling account.')
    return target


def _broker(account_id):
    account = next((a for a in accounts.live_accounts() if a.id == account_id), None)
    if not account:
        raise HTTPException(404, 'Broker account not found.')
    return account


class BrokerCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    label: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=1, max_length=512)
    api_secret: str = Field(min_length=1, max_length=512)


class BrokerEdit(BaseModel):
    expected_updated_at_ms: int = Field(ge=0, strict=True)
    model_config = ConfigDict(extra='forbid')
    label: str | None = Field(default=None, min_length=1, max_length=100)
    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    api_secret: str | None = Field(default=None, min_length=1, max_length=512)


@router.get('/brokers')
async def list_brokers(response: Response, user: UserContext = Depends(get_current_user)):
    _admin(user)
    response.headers['Cache-Control'] = 'no-store, private'
    return {'brokers': [accounts.to_response(a) for a in accounts.live_accounts()]}


@router.post('/brokers')
async def create_broker(body: BrokerCreate, user: UserContext = Depends(get_current_user)):
    _admin(user)
    _target(body.user_id)
    async with account_lock(body.user_id):
        _admin(user)
        _target(body.user_id)
        try:
            account = accounts.add(body.user_id, KiteAccountCreate(label=body.label, api_key=body.api_key, api_secret=body.api_secret), audit_actor=user.user_id)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None
    return accounts.to_response(account)


@router.patch('/brokers/{account_id}')
async def update_broker(account_id: str, body: BrokerEdit, user: UserContext = Depends(get_current_user)):
    _admin(user)
    account = _broker(account_id)
    from app.api.v1.endpoints.kite import _require_account_detachable
    async with account_lock(account.user_id):
        if account.updated_at_ms != body.expected_updated_at_ms:
            raise HTTPException(409, 'Connection changed; reload before saving.')
        if body.api_key is not None or body.api_secret is not None:
            await _require_account_detachable(account.user_id, account, 'Central credential replacement', allow_initial_credentials=True)
        _admin(user)
        if account.updated_at_ms != body.expected_updated_at_ms:
            raise HTTPException(409, 'Connection changed during verification; reload before saving.')
        try:
            changed = accounts.update(account.user_id, account.id, KiteAccountUpdate(**body.model_dump(exclude_none=True, exclude={'expected_updated_at_ms'})), audit_actor=user.user_id)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from None
        if not changed:
            raise HTTPException(404, 'Broker account no longer exists.')
    return accounts.to_response(changed)


@router.delete('/brokers/{account_id}', status_code=204)
async def remove_broker(account_id: str, user: UserContext = Depends(get_current_user)):
    _admin(user)
    account = _broker(account_id)
    from app.api.v1.endpoints.kite import _require_account_detachable
    async with account_lock(account.user_id):
        await _require_account_detachable(account.user_id, account, 'Central broker removal')
        _admin(user)
        if not accounts.delete(account.user_id, account.id, audit_actor=user.user_id):
            raise HTTPException(404, 'Broker account no longer exists.')
        await accounts.release_client(account.id)
        from app.services.exchanges.kite import ticker_manager
        await ticker_manager.stop(account.user_id)


class TelegramCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=100)
    bot_token: str = Field(min_length=1, max_length=512)
    chat_id: str = Field(min_length=1, max_length=128)


@router.get('/users/{user_id}/telegram')
async def list_telegram(user_id: str, response: Response, user: UserContext = Depends(get_current_user)):
    _admin(user)
    _target(user_id)
    from app.services.notifications import kite_telegram_store as store
    from app.api.v1.endpoints.kite_telegram import _to_out
    response.headers['Cache-Control'] = 'no-store, private'
    return {'targets': [_to_out(t) for t in store.list_targets(user_id)]}


@router.post('/users/{user_id}/telegram')
async def create_telegram(user_id: str, body: TelegramCreate, user: UserContext = Depends(get_current_user)):
    _admin(user)
    _target(user_id)
    from app.services.notifications import kite_telegram_store as store
    from app.api.v1.endpoints.kite_telegram import _to_out
    return _to_out(store.add(user_id, label=body.label, bot_token=body.bot_token, chat_id=body.chat_id, enabled=True))


@router.delete('/users/{user_id}/telegram/{target_id}', status_code=204)
async def remove_telegram(user_id: str, target_id: str, user: UserContext = Depends(get_current_user)):
    _admin(user)
    _target(user_id)
    from app.services.notifications import kite_telegram_store as store
    if not store.delete(user_id, target_id):
        raise HTTPException(404, 'Telegram destination not found.')


class BrokerAssignment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str
    expected_user_id: str
    expected_updated_at_ms: int = Field(ge=0,strict=True)


@router.post('/brokers/{account_id}/assignment')
async def assign_broker(account_id: str, body: BrokerAssignment, user: UserContext = Depends(get_current_user)):
    from contextlib import AsyncExitStack
    from app.services.auth.account_policy import PolicyConflict
    from app.api.v1.endpoints.kite import _require_account_detachable
    _admin(user);_target(body.user_id)
    if body.user_id==body.expected_user_id: raise HTTPException(422,'Choose another Sterling user.')
    async with AsyncExitStack() as stack:
        for uid in sorted({body.user_id,body.expected_user_id}):
            await stack.enter_async_context(account_lock(uid))
        account=_broker(account_id)
        if account.user_id!=body.expected_user_id or account.updated_at_ms!=body.expected_updated_at_ms:
            raise HTTPException(409,'Broker assignment changed; reload connections.')
        await _require_account_detachable(account.user_id,account,'Broker reassignment',allow_initial_credentials=True)
        _admin(user);_target(body.user_id)
        try:
            changed=accounts.reassign(account_id,actor_id=user.user_id,source_user_id=body.expected_user_id,
                target_user_id=body.user_id,expected_updated_at_ms=body.expected_updated_at_ms)
        except PolicyConflict as exc: raise HTTPException(409,str(exc)) from None
        except PermissionError as exc: raise HTTPException(403,str(exc)) from None
        except ValueError as exc: raise HTTPException(422,str(exc)) from None
        except RuntimeError as exc: raise HTTPException(503,str(exc)) from None
        await accounts.release_client(account_id)
        from app.services.exchanges.kite import ticker_manager
        await ticker_manager.stop(body.expected_user_id)
        return accounts.to_response(changed)
