"""One durable public origin for the app, Mini App and broker callback guidance."""
from __future__ import annotations
import json
import time
from urllib.parse import urlsplit
from app.services import db


def validate_origin(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {'', '/'}):
        raise ValueError('Use an HTTPS origin without a path, credentials, query or fragment.')
    return f'https://{parsed.netloc}'.rstrip('/')


def status() -> dict:
    try:
        data = json.loads(db.get_config_strict('sterling_public_access', '{}'))
        if data.get('app_url'):
            origin = validate_origin(data['app_url'])
            data.update(app_url=origin, mini_app_url=origin+'/tma', callback_url=origin+'/api/v1/kite/callback')
        return data
    except (ValueError, TypeError):
        return {}


def publish(origin: str, *, mode: str = 'quick') -> dict:
    origin = validate_origin(origin)
    data = {'app_url':origin, 'mini_app_url':origin+'/tma', 'callback_url':origin+'/api/v1/kite/callback',
            'mode':mode, 'updated_at_ms':int(time.time()*1000)}
    # Both destinations change atomically. Reads see either the old or the new deployment.
    with db._conn() as connection:
        connection.executemany('INSERT OR REPLACE INTO system_config (key,value) VALUES (?,?)',[
            ('sterling_public_access',json.dumps(data)),('sterling_tma_url',data['mini_app_url'])])
        connection.commit()
    return data
