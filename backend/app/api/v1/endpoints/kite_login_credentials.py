"""Owner-scoped credential vault; storing credentials does not automate Kite login."""
import json
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from app.core.configuration_access import require_configuration
from app.core.auth import UserContext, get_current_user
from app.core.security import encrypt, decrypt
from app.services import db
from app.services.exchanges.kite import accounts
from app.services.execution_identity_lock import account_lock

router = APIRouter()

class LoginCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(default='', max_length=512)


def owned(user, account_id):
    acct = accounts.get(user.user_id, account_id)
    if acct is None or accounts.is_sim_account(acct):
        raise HTTPException(404, 'Kite account not found')
    return acct


def key(user, account_id):
    return f'kite_login_credentials:{user.user_id}:{account_id}'


def read(user, account_id):
    raw = db.get_config_strict(key(user, account_id), '')
    if not raw:
        return {'username': '', 'password': ''}
    if not raw.startswith('fernet:'):
        raise HTTPException(503, 'Encrypted credential storage unavailable')
    try:
        return json.loads(decrypt(raw))
    except Exception:
        raise HTTPException(503, 'Saved broker credentials cannot be read') from None


def public(value):
    return {'username': value['username'], 'password_saved': bool(value['password']), 'automatic_login_available': True}


@router.get('/accounts/{account_id}/login-credentials')
async def get_credentials(account_id: str, response: Response, user: UserContext = Depends(get_current_user)):
    acct = owned(user, account_id)
    value = read(user, account_id)
    if not value['username']:
        value['username'] = acct.kite_user_id or ''
    response.headers['Cache-Control'] = 'no-store, private'
    return public(value)


@router.put('/accounts/{account_id}/login-credentials')
async def save_credentials(account_id: str, body: LoginCredentials, response: Response, user: UserContext = Depends(get_current_user)):
    require_configuration(user, 'brokers', 'update', account_id)
    async with account_lock(user.user_id):
        require_configuration(user, 'brokers', 'update', account_id)
        acct = owned(user, account_id)
        username = body.username.strip().upper()
        if not username or not username.isascii() or not username.isalnum():
            raise HTTPException(422, 'Enter your Kite user ID using letters and numbers')
        if acct.kite_user_id and username != acct.kite_user_id.upper():
            raise HTTPException(409, 'This Kite user ID differs from the linked broker account. Add a separate account for another Kite user.')
        previous = read(user, account_id)
        if previous['username'] and previous['username'] != username and not body.password:
            raise HTTPException(422, 'Enter the password when changing the Kite user ID')
        value = {'username': username, 'password': body.password or previous['password']}
        if not value['password']:
            raise HTTPException(422, 'Enter a Kite password to save these credentials')
        try:
            encrypted = encrypt(json.dumps(value))
            if not encrypted.startswith('fernet:'):
                raise ValueError()
            db.set_config_strict(key(user, account_id), encrypted)
        except Exception:
            raise HTTPException(503, 'Encrypted credential storage unavailable; credentials were not saved') from None
        response.headers['Cache-Control'] = 'no-store, private'
        return public(value)


@router.delete('/accounts/{account_id}/login-credentials', status_code=204)
async def remove_credentials(account_id: str, user: UserContext = Depends(get_current_user)):
    require_configuration(user, 'brokers', 'update', account_id)
    async with account_lock(user.user_id):
        require_configuration(user, 'brokers', 'update', account_id)
        owned(user, account_id)
        with db._conn() as connection:
            connection.execute('DELETE FROM system_config WHERE key=?', (key(user, account_id),))
            connection.commit()


class AssistedStart(BaseModel):
    account_id: str
    test_only: bool = False

class AssistedTotp(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r'^\d{6}$')


@router.post('/assisted-login')
async def start_assisted(body: AssistedStart, response: Response, user: UserContext = Depends(get_current_user)):
    from app.services.exchanges.kite import assisted_login
    async with account_lock(user.user_id):
        acct = owned(user, body.account_id)
        if not acct.api_key or not acct.api_secret:
            raise HTTPException(400, 'Save this account’s API key and secret first')
        credentials = read(user, acct.id)
        if not credentials['username'] or not credentials['password']:
            raise HTTPException(400, 'Save this account’s Kite username and password first')
        try:
            value = assisted_login.start(user.user_id, acct.id, credentials['username'], credentials['password'],
                                         db.get_config_strict(key(user, acct.id), ''), test_only=body.test_only)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        response.headers['Cache-Control'] = 'no-store, private'
        return value.public()


@router.get('/assisted-login/{attempt_id}')
async def assisted_status(attempt_id: str, response: Response, user: UserContext = Depends(get_current_user)):
    from app.services.exchanges.kite import assisted_login
    try:
        value = assisted_login.get(user.user_id, attempt_id)
    except ValueError:
        raise HTTPException(404, 'Login attempt not found') from None
    response.headers['Cache-Control'] = 'no-store, private'
    return value.public()


@router.post('/assisted-login/{attempt_id}/totp')
async def assisted_totp(attempt_id: str, body: AssistedTotp, response: Response, user: UserContext = Depends(get_current_user)):
    from app.services.exchanges.kite import assisted_login
    try:
        value = assisted_login.get(user.user_id, attempt_id)
    except ValueError:
        raise HTTPException(404, 'Login attempt not found') from None
    owned(user, value.account_id)
    if value.phase != 'awaiting_totp' or not value.otp.empty():
        raise HTTPException(409, 'This login is not waiting for TOTP. Check its status.')
    value.phase, value.message = 'verifying_totp', 'Submitting your TOTP to Kite…'
    value.otp.put_nowait(body.code)
    response.headers['Cache-Control'] = 'no-store, private'
    return value.public()


@router.delete('/assisted-login/{attempt_id}', status_code=204)
async def cancel_assisted(attempt_id: str, user: UserContext = Depends(get_current_user)):
    from app.services.exchanges.kite import assisted_login
    try:
        value = assisted_login.get(user.user_id, attempt_id)
    except ValueError:
        raise HTTPException(404, 'Login attempt not found') from None
    if value.task and not value.task.done() and value.phase not in {'done', 'tested', 'failed', 'cancelled'}:
        import asyncio
        value.task.cancel()
        try:
            await value.task
        except asyncio.CancelledError:
            pass
        value.phase, value.message = 'cancelled', 'Broker login cancelled.'


@router.get('/assisted-login/{attempt_id}/preview')
async def assisted_preview(attempt_id: str, response: Response, user: UserContext = Depends(get_current_user)):
    import base64
    import time
    from app.services.exchanges.kite import assisted_login
    try:
        value = assisted_login.get(user.user_id, attempt_id)
    except ValueError:
        raise HTTPException(404, 'Login attempt not found') from None
    owned(user, value.account_id)
    try:
        frame = await assisted_login.preview(value)
    except ValueError:
        raise HTTPException(409, 'Kite preview is not available at this login stage') from None
    except Exception:
        raise HTTPException(503, 'Kite preview is temporarily unavailable') from None
    response.headers['Cache-Control'] = 'no-store, private'
    return {'image': 'data:image/png;base64,' + base64.b64encode(frame).decode('ascii'),
            'captured_at_ms': int(time.time() * 1000)}
