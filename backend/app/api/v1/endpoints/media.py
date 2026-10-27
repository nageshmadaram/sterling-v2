"""Protected opaque-origin player documents, with media-only finite cookies.

These credentials never authenticate trading, session-refresh or admin APIs.
The browser bearer token stays in the parent and is never sent to the player.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from app.core.auth import UserContext, get_current_user
from app.core.csp import csp_nonce
from app.core.feature_access import require_feature
from app.core import runtime_env
from app.services import db
from app.services.auth import account_policy

router = APIRouter(prefix='/media', tags=['media'])
TEMPLATE_ROOT = Path(__file__).resolve().parents[3] / 'media_templates'
TTL_SECONDS = 10 * 60


def _feature(feature: str) -> str:
    if feature not in ('tv', 'radio'):
        raise HTTPException(404, 'Unknown media workspace.')
    return feature


def _cookie(feature: str) -> str:
    return ('__Secure-' if runtime_env.is_production() else '') + 'sterling_media_' + feature



@router.post('/{feature}/access')
async def issue_access(feature: str, response: Response, user: UserContext = Depends(get_current_user)):
    _feature(feature)
    if user.view:
        raise HTTPException(403, 'Open media from the feature launcher.')
    require_feature(user, feature)
    credential = secrets.token_urlsafe(32)
    digest = hashlib.sha256(credential.encode()).hexdigest()
    now = int(time.time())
    try:
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            stored = c.execute('SELECT is_active,token_version FROM users WHERE id=?', (user.user_id,)).fetchone()
            enabled = c.execute('SELECT enabled FROM user_feature_grants WHERE user_id=? AND feature=?', (user.user_id, feature)).fetchone()
            if not stored or stored['is_active'] != 1 or not enabled or enabled['enabled'] != 1:
                raise HTTPException(403, 'Media access is not enabled.')
            c.execute('DELETE FROM media_document_sessions WHERE expires_at<=?', (now,))
            # Bound credentials per user/feature without invalidating another current tab.
            c.execute('''DELETE FROM media_document_sessions WHERE credential_hash IN (
                SELECT credential_hash FROM media_document_sessions WHERE user_id=? AND feature=?
                ORDER BY created_at DESC,credential_hash LIMIT -1 OFFSET 7)''', (user.user_id, feature))
            c.execute('INSERT INTO media_document_sessions VALUES(?,?,?,?,?,?)',
                      (digest, user.user_id, feature, stored['token_version'], now + TTL_SECONDS, now))
    except sqlite3.Error as exc:
        raise HTTPException(503, 'Media access storage unavailable.') from exc
    response.set_cookie(_cookie(feature), credential, max_age=TTL_SECONDS,
        path=f'/api/v1/media/{feature}/document', httponly=True,
        secure=runtime_env.is_production(), samesite='lax')
    response.headers['Cache-Control'] = 'no-store, private'
    return {'document_url': f'/api/v1/media/{feature}/document', 'expires_in': TTL_SECONDS}


def media_csp(feature: str, nonce: str) -> str:
    sandbox = 'allow-scripts allow-forms allow-downloads allow-presentation'
    if feature == 'radio':
        sandbox += ' allow-popups allow-popups-to-escape-sandbox'
    return '; '.join((f'sandbox {sandbox}', "default-src 'none'",
        f"script-src 'self' 'nonce-{nonce}'", "script-src-attr 'unsafe-inline'",
        "style-src 'self' 'unsafe-inline'", 'font-src data:', 'img-src https: data: blob:',
        'media-src https: http: data: blob:', 'connect-src https: http:', 'worker-src blob:',
        'frame-src https:', "object-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'self'"))


@router.api_route('/{feature}/document', methods=['GET', 'HEAD'])
async def document(feature: str, request: Request):
    _feature(feature)
    credential = request.cookies.get(_cookie(feature), '')
    if not credential or len(credential) > 128:
        raise HTTPException(401, 'Open this player from the Sterling launcher.')
    if not db.is_available():
        raise HTTPException(503, 'Media access storage unavailable.')
    try:
        with db._conn() as c:
            row = c.execute('''SELECT s.user_id FROM media_document_sessions s JOIN users u ON u.id=s.user_id
                JOIN user_feature_grants g ON g.user_id=u.id AND g.feature=s.feature
                WHERE s.credential_hash=? AND s.feature=? AND s.expires_at>?
                AND u.is_active=1 AND u.token_version=s.token_version AND g.enabled=1''',
                (hashlib.sha256(credential.encode()).hexdigest(), feature, int(time.time()))).fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(503, 'Media access storage unavailable.') from exc
    if not row:
        raise HTTPException(403, 'Media access expired or was revoked. Reopen this workspace.')
    try:
        html = (TEMPLATE_ROOT / (feature + '.html')).read_text()
    except OSError as exc:
        raise HTTPException(503, 'Player template unavailable.') from exc
    nonce = csp_nonce()
    html = re.sub(r'<script\b', f'<script nonce="{nonce}"', html)
    response = HTMLResponse('' if request.method == 'HEAD' else html)
    response.headers.update({'Cache-Control': 'no-store, private', 'Pragma': 'no-cache',
        'Content-Security-Policy': media_csp(feature, nonce), 'Referrer-Policy': 'no-referrer',
        'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()'})
    return response
