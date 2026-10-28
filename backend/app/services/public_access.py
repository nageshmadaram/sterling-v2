"""One Cloudflare public origin for the Sterling Telegram desk and Kite callback."""
from __future__ import annotations
import json
import time
from urllib.parse import urlsplit
from app.services import db

SUPPORTED_PUBLIC_PROVIDERS = frozenset({"cloudflare"})


def validate_origin(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {'', '/'}
            or parsed.port is not None):
        raise ValueError('Use a public HTTPS origin without a port, path, credentials, query or fragment.')
    if '.ngrok' in parsed.hostname:
        raise ValueError('Unsupported tunnel provider')
    return f'https://{parsed.netloc}'.rstrip('/')


def _verified_urls(values: object) -> dict[str, str]:
    if not isinstance(values, dict):
        return {}
    result = {}
    for name, value in values.items():
        if name not in SUPPORTED_PUBLIC_PROVIDERS:
            continue
        try:
            result[name] = validate_origin(value)
        except (ValueError, TypeError):
            continue
    return result


def status() -> dict:
    try:
        data = json.loads(db.get_config_strict('sterling_public_access', '{}'))
        if not isinstance(data, dict):
            return {}
        if data.get('app_url'):
            if data.get('mode') not in {'quick','named'}:
                return {}
            origin = validate_origin(data['app_url'])
            data.update(app_url=origin, mini_app_url=origin+'/tma',
                        callback_url=origin+'/api/v1/kite/callback')
            data['urls'] = _verified_urls(data.get('urls'))
            if not data['urls']:
                data['urls'] = {'cloudflare': origin}
            # Stale secondary providers never become the registered callback.
            return data
        return {}
    except (ValueError, TypeError, OverflowError):
        return {}


def publish(origin: str, *, mode: str = 'quick') -> dict:
    if mode not in {'quick','named'}:
        raise ValueError('Unsupported tunnel mode')
    origin = validate_origin(origin)
    origins = {'cloudflare': origin}
    data = {
        'app_url': origin, 'mini_app_url': origin+'/tma',
        'callback_url': origin+'/api/v1/kite/callback', 'urls': origins,
        'mode': mode, 'updated_at_ms': int(time.time()*1000),
    }
    # Cloudflare and the single Telegram callback change atomically.
    with db._conn() as connection:
        connection.executemany('INSERT OR REPLACE INTO system_config (key,value) VALUES (?,?)', [
            ('sterling_public_access', json.dumps(data)),
            ('sterling_tma_url', data['mini_app_url']),
        ])
        connection.commit()
    return data
