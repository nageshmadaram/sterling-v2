"""Administrator user management; no passwords or hashes in API responses."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ConfigDict, StrictBool
from app.core.auth import UserContext, get_current_user
from app.services.auth import user_store, operator_credentials, account_policy
from app.services import db

router=APIRouter(prefix='/users',tags=['Sterling users'])


def admin(user):
    if user.role!='admin':raise HTTPException(403,'Sterling administrator access required.')
    from app.core.configuration_access import require_configuration
    require_configuration(user, 'brokers', administrator_only=True)


def _photo(user_id: str) -> str:
    """Return the saved profile photo (base64 data URL) or empty string."""
    if not db.is_available():
        return ''
    try:
        with db._conn() as c:
            row = c.execute(
                "SELECT photo FROM user_profiles WHERE user_id=?", (user_id,)
            ).fetchone()
        return row['photo'] if row and row['photo'] else ''
    except Exception:
        return ''


def public(user):
    return {'id': user.id, 'username': user.username, 'role': user.role, 'active': user.is_active,
            'password_set': bool(user.password_hash), 'primary': user.id == 'default',
            'photo': _photo(user.id)}


@router.get('')
async def list_users(response:Response,user:UserContext=Depends(get_current_user)):
    admin(user)
    response.headers['Cache-Control']='no-store, private'
    return {'current_user_id':user.user_id,'users':[public(u) for u in user_store.all_users()]}


class UserEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username:str=Field(min_length=3,max_length=64)
    password:str=''
    role:str='user'
    active:bool=True


def save(user,body,action,target_id=None):
    admin(user)
    try:return public(operator_credentials.manage(user.user_id,action,body.username,body.password,target_id=target_id,role=body.role,active=body.active))
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except PermissionError:raise HTTPException(403,'Sterling administrator access required.') from None
    except RuntimeError as exc:raise HTTPException(503,str(exc)) from None


@router.post('')
async def add_user(body:UserEdit,user:UserContext=Depends(get_current_user)):
    return save(user,body,'add')


@router.put('/{user_id}')
async def update_user(user_id:str,body:UserEdit,user:UserContext=Depends(get_current_user)):
    admin(user)
    if not user_store.get_by_id(user_id):raise HTTPException(404,'User not found.')
    return save(user,body,'update',user_id)


@router.delete('/{user_id}')
async def delete_user(user_id:str,user:UserContext=Depends(get_current_user)):
    admin(user)
    target=user_store.get_by_id(user_id)
    if not target:raise HTTPException(404,'User not found.')
    # Retain identity for journal/audit ownership; disabling revokes every token.
    result=save(user,UserEdit(username=target.username,role=target.role,active=False),'update',user_id)
    return {'ok':True,'user':result,'message':'Login suspended. Identity and broker connections retained.'}


class FeatureGrants(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kite: StrictBool
    tv: StrictBool
    radio: StrictBool


class FeatureEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    features: FeatureGrants


def policy_public(policy):
    return {'user_id': policy.user_id, 'revision': policy.revision,
            'active': policy.active, 'administrator': policy.administrator,
            'features': policy.features}


@router.get('/{user_id}/features')
async def get_features(user_id: str, response: Response, user: UserContext = Depends(get_current_user)):
    admin(user)
    response.headers['Cache-Control'] = 'no-store, private'
    try:
        if not account_policy.read(user.user_id).administrator:
            raise HTTPException(403, 'Designated administrator access required.')
        if not user_store.get_by_id(user_id):
            raise HTTPException(404, 'User not found.')
        return policy_public(account_policy.read(user_id))
    except account_policy.PolicyUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@router.put('/{user_id}/features')
async def update_features(user_id: str, body: FeatureEdit, response: Response,
                          user: UserContext = Depends(get_current_user)):
    admin(user)
    response.headers['Cache-Control'] = 'no-store, private'
    try:
        return policy_public(account_policy.set_features(
            user.user_id, user_id, body.features.model_dump(), body.expected_revision))
    except account_policy.PolicyConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except account_policy.PolicyUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
