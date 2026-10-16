"""Administrator user management; no passwords or hashes in API responses."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from app.core.auth import UserContext, get_current_user
from app.services.auth import user_store, operator_credentials
from app.services import db

router=APIRouter(prefix='/users',tags=['Sterling users'])


def admin(user):
    if user.role!='admin':raise HTTPException(403,'Sterling administrator access required.')


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
    return {'ok':True,'user':result,'message':'Login deleted. User identity retained for audit history.'}
