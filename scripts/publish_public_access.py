"""Publish one verified primary and optional second public tunnel.

This utility cannot change broker callback settings at Kite. The primary
Cloudflare HTTPS origin remains the only Telegram persistent WebApp button,
but /menu exposes both public providers.
"""
import asyncio
import html
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
os.chdir(ROOT/'backend')
from app.core.runtime_env import load_sterling_env
load_sterling_env()
from app.services import db, public_access
db.init()
from app.services.notifications import telegram, telegram_bot
telegram.restore_legacy_config_from_store()


async def run():
    secondary = sys.argv[3] if len(sys.argv) > 3 else ''
    alternatives = {'ngrok': secondary} if secondary else {}
    data = public_access.publish(sys.argv[1], mode=sys.argv[2],
                                 alternate_origins=alternatives)
    urls = data['urls']
    result = {'published': True, 'telegram_configured': bool(
        telegram.TELEGRAM_TOKEN and telegram.TELEGRAM_CHAT_ID)}
    if result['telegram_configured']:
        # Telegram allows exactly one persistent menu_button, not two.
        # Inline /menu provides distinct Cloudflare and ngrok launch links.
        menu = await telegram_bot._api('setChatMenuButton', {'menu_button': {
            'type': 'web_app', 'text': 'Sterling desk',
            'web_app': {'url': data['mini_app_url']},
        }}, timeout=10)
        result['telegram_menu_updated'] = menu.get('ok') is True
        if not menu.get('ok'):
            result['telegram_menu_error'] = menu.get('description', 'setChatMenuButton failed')
        signature = json.dumps(urls, sort_keys=True, separators=(',', ':'))
        previous = db.get_config_strict('sterling_public_access_notified', '')
        if previous != signature and os.environ.get('STERLING_ACCESS_NOTIFY') == '1':
            buttons = []
            if 'cloudflare' in urls:
                buttons.append([{'text': 'Cloudflare · Open Sterling',
                                 'url': urls['cloudflare']}])
            if 'ngrok' in urls:
                buttons.append([{'text': 'ngrok · Open Sterling',
                                 'url': urls['ngrok']}])
            buttons.append([{'text': 'Open primary Telegram desk',
                             'web_app': {'url': data['mini_app_url']}}])
            message = ('<b>Sterling public access updated</b>\n\n'
                       + '\n'.join(f'{provider.title()}: {html.escape(url)}'
                                   for provider, url in urls.items())
                       + '\n\nUse /menu for both access links. '
                       + 'Kite OAuth callback stays on the configured primary origin.')
            sent = await telegram_bot._api('sendMessage', {
                'chat_id': telegram.TELEGRAM_CHAT_ID,
                'parse_mode': 'HTML', 'text': message,
                'reply_markup': {'inline_keyboard': buttons},
            }, timeout=10)
            result['telegram_notice_sent'] = sent.get('ok') is True
            if sent.get('ok'):
                db.set_config_strict('sterling_public_access_notified', signature)
    print(json.dumps(result))


try:
    asyncio.run(run())
except Exception as error:
    print(json.dumps({'published': False, 'failure_type': type(error).__name__}))
    sys.exit(1)
