"""Owner-scoped durable UI preferences; never broker authority or credentials."""
import json
import re
import time
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.core.auth import UserContext, get_current_user
from app.services import db

router = APIRouter(prefix="/preferences", tags=["preferences"])
EXACT = set('sterling.experience.options.v1 kite-settings sterling_theme sterling_theme_default_migrated_v1 sterling_appearance_v1 sterling_zoom sterling_density sterling_autofit sterling_mobile_style sterling_activity_rail kite_terminal_theme sterling.kite.tickerPins.v1 sterling.kite.watchlist.v1 sterling.kite.watchlist.manual-empty.v1 sterling:kite-chart-templates:v1 sterling.pcr.desk.v2'.split())
EXACT.update(['sterling.tv.streamos_sources_v14', 'sterling.tv.streamos_source_mode_v14', 'sterling.tv.streamos_favs_v14', 'sterling.tv.streamos_stats_v14', 'sterling.tv.streamos_history_v14', 'sterling.tv.streamos_startup_channel_v14', 'sterling.tv.streamos_theme_v14', 'sterling.tv.streamos_ui_v14', 'sterling.radio.omnicast_v11_config', 'sterling.radio.omnicast_v11_recents', 'sterling.radio.omnicast_v11_counts', 'sterling.radio.omnicast_v11_liked', 'sterling.tv.streamos_extras_v14', 'sterling.tv.streamos_youtube_v14', 'sterling.radio.omnicast_v11_extras'])
PREFIXES = ('sterling.board.hidden.v2.',)

def permitted(key: str) -> bool:
    return len(key) <= 160 and (key in EXACT or any(key.startswith(p) and re.fullmatch(r'[a-zA-Z0-9_.:-]+', key) for p in PREFIXES))

class PreferencePatch(BaseModel):
    values: dict[str, str | None] = Field(default_factory=dict)
    initialize_only: bool = False

def _table(c):
    c.execute('CREATE TABLE IF NOT EXISTS ui_preferences (owner_uid TEXT NOT NULL, key TEXT NOT NULL, value TEXT, updated_ms INTEGER NOT NULL, PRIMARY KEY(owner_uid,key))')

def owner(user: UserContext) -> str:
    return user.owner_uid or user.user_id

@router.get('')
def read_preferences(user: UserContext = Depends(get_current_user)):
    if not db.is_available():
        raise HTTPException(503, 'Preference database unavailable')
    with db._conn() as c:
        _table(c)
        rows = c.execute('SELECT key,value FROM ui_preferences WHERE owner_uid=?', (owner(user),)).fetchall()
    return {'values': {r['key']: r['value'] for r in rows}, 'initialized': bool(rows)}

@router.patch('')
def write_preferences(body: PreferencePatch, user: UserContext = Depends(get_current_user)):
    if not db.is_available():
        raise HTTPException(503, 'Preference database unavailable')
    if len(body.values) > 300 or len(json.dumps(body.values).encode()) > 5_000_000:
        raise HTTPException(413, 'Preference package too large')
    for key, value in body.values.items():
        if not permitted(key) or (value is not None and len(value.encode()) > (500_000 if key.startswith('sterling.tv.') else 200_000)):
            raise HTTPException(422, 'Unsupported preference key or value size')
    with db._conn() as c:
        _table(c)
        c.execute('BEGIN IMMEDIATE')
        existing = c.execute('SELECT 1 FROM ui_preferences WHERE owner_uid=? LIMIT 1', (owner(user),)).fetchone()
        if not (body.initialize_only and existing):
            for key, value in body.values.items():
                c.execute('INSERT INTO ui_preferences(owner_uid,key,value,updated_ms) VALUES(?,?,?,?) ON CONFLICT(owner_uid,key) DO UPDATE SET value=excluded.value,updated_ms=excluded.updated_ms', (owner(user), key, value, int(time.time()*1000)))
        rows = c.execute('SELECT key,value FROM ui_preferences WHERE owner_uid=?', (owner(user),)).fetchall()
    return {'values': {r['key']: r['value'] for r in rows}, 'initialized': bool(rows)}
