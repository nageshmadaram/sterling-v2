"""Central management adapters over the existing assignment-safe provider stores.

These routes keep the real actor separate from the selected Sterling tenant.
They do not reinterpret management authority as trading or broker-login authority.
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
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


@router.get('/directory')
async def directory(response: Response,user: UserContext=Depends(get_current_user)):
    from app.services import db
    from app.services.auth import account_policy,configuration_policy,login_activity
    from app.services.notifications import kite_telegram_store
    _admin(user);response.headers['Cache-Control']='no-store, private'
    from app.api.v1.endpoints.sterling_users import public
    from app.api.v1.endpoints.kite_telegram import _to_out
    result=[]
    for target in user_store.all_users():
        policy=account_policy.read(target.id)
        with db._conn() as c:
            activity=login_activity.for_user(c,target.id)
        result.append({**public(target),'features':policy.features,'revision':policy.revision,
            'configuration':configuration_policy.read(target.id),'activity':activity,
            'online':any(bool(row['online']) for row in activity),
            'last_login_ms':max((row['last_login_ms'] or 0 for row in activity),default=0) or None,
            'brokers':[accounts.to_response(a) for a in accounts.live_accounts() if a.user_id==target.id],
            'telegram':[_to_out(t) for t in kite_telegram_store.list_targets(target.id)]})
    return {'users':result,'current_user_id':user.user_id,'brokers':[accounts.to_response(a) for a in accounts.live_accounts()]}


class BulkUser(BaseModel):
    model_config=ConfigDict(extra='forbid')
    user_id: str
    expected_revision: int=Field(ge=0,strict=True)

class BulkAccess(BaseModel):
    model_config=ConfigDict(extra='forbid')
    users: list[BulkUser]=Field(min_length=1,max_length=100)
    features: dict[str,StrictBool]


@router.patch('/directory/access')
async def bulk_access(body:BulkAccess,user:UserContext=Depends(get_current_user)):
    from app.services import db
    from app.services.auth import account_policy
    from app.services.auth.user_store import _now_ms
    from app.services.auth.access_requests import _admin as transaction_admin
    _admin(user)
    if not body.features or not set(body.features)<=set(account_policy.FEATURES): raise HTTPException(422,'Choose workspaces.')
    if len({item.user_id for item in body.users})!=len(body.users): raise HTTPException(422,'Choose each user once.')
    try:
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE');transaction_admin(c,user.user_id)
            for item in body.users:
                if not c.execute('SELECT 1 FROM users WHERE id=? AND is_active=1',(item.user_id,)).fetchone(): raise ValueError('Choose active users.')
                row=c.execute('SELECT revision FROM account_policy_revisions WHERE user_id=?',(item.user_id,)).fetchone()
                if (int(row['revision']) if row else 0)!=item.expected_revision: raise account_policy.PolicyConflict('Access changed; refresh the user list.')
            now=_now_ms()
            for item in body.users:
                old={r['feature']:bool(r['enabled']) for r in c.execute('SELECT feature,enabled FROM user_feature_grants WHERE user_id=?',(item.user_id,))}
                for feature,enabled in body.features.items():
                    c.execute('''INSERT INTO user_feature_grants VALUES(?,?,?,?,?) ON CONFLICT(user_id,feature)
                        DO UPDATE SET enabled=excluded.enabled,actor_id=excluded.actor_id,updated_at_ms=excluded.updated_at_ms''',(item.user_id,feature,int(enabled),user.user_id,now))
                revision=item.expected_revision+1
                c.execute('INSERT INTO account_policy_revisions VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision',(item.user_id,revision))
                event=account_policy.audit(c,user.user_id,item.user_id,'features.bulk_updated',old,{**old,**body.features},now)
                c.execute('INSERT INTO auth_event_outbox(event_id,user_id,revision) VALUES(?,?,?)',(event,item.user_id,revision))
        return {'updated':len(body.users)}
    except account_policy.PolicyConflict as exc: raise HTTPException(409,str(exc)) from None
    except ValueError as exc: raise HTTPException(422,str(exc)) from None
    except Exception as exc: raise HTTPException(503,'Access update failed; no changes saved.') from exc


class BrokerLogin(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:str=Field(min_length=1,max_length=64)
    password:str=Field(min_length=1,max_length=512)
    expected_user_id:str
    expected_updated_at_ms:int


@router.put('/brokers/{account_id}/login-credentials')
async def broker_login(account_id:str,body:BrokerLogin,response:Response,user:UserContext=Depends(get_current_user)):
    import json
    from app.core.security import encrypt
    from app.services import db
    from app.services.auth.account_policy import audit
    from app.services.auth.user_store import _now_ms
    _admin(user)
    async with account_lock(body.expected_user_id):
        _admin(user);account=_broker(account_id)
        if account.user_id!=body.expected_user_id or account.updated_at_ms!=body.expected_updated_at_ms:
            raise HTTPException(409,'Broker assignment changed; reload connections.')
        username=body.username.strip().upper()
        if not username.isascii() or not username.isalnum(): raise HTTPException(422,'Enter a valid Kite user ID.')
        if account.kite_user_id and account.kite_user_id.upper()!=username:
            raise HTTPException(409,'Kite user ID does not match this broker profile.')
        encrypted=encrypt(json.dumps({'username':username,'password':body.password}))
        if not encrypted.startswith('fernet:'): raise HTTPException(503,'Encrypted credential storage unavailable.')
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            from app.services.auth.access_requests import _admin as transaction_admin
            transaction_admin(c,user.user_id)
            current=c.execute('SELECT user_id,updated_at_ms FROM kite_accounts WHERE id=?',(account_id,)).fetchone()
            if not current or current['user_id']!=body.expected_user_id or current['updated_at_ms']!=body.expected_updated_at_ms:
                raise HTTPException(409,'Broker assignment changed; reload connections.')
            c.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      (f'kite_login_credentials:{account.user_id}:{account_id}',encrypted))
            audit(c,user.user_id,account.user_id,'broker.login_credentials_updated',{}, {'broker_id':account_id,'password_saved':True},_now_ms())
        response.headers['Cache-Control']='no-store, private'
        return {'username':username,'password_saved':True,'automatic_login_available':True}


@router.get('/brokers/{account_id}/login-credentials')
async def broker_login_status(account_id:str,response:Response,user:UserContext=Depends(get_current_user)):
    from types import SimpleNamespace
    from app.api.v1.endpoints.kite_login_credentials import read,public
    _admin(user);account=_broker(account_id)
    value=read(SimpleNamespace(user_id=account.user_id),account_id)
    if not value['username']:value['username']=account.kite_user_id or ''
    response.headers['Cache-Control']='no-store, private'
    return public(value)
