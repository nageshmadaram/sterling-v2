"""Called by the local launcher using the backend runtime; never consumes bot updates."""
import asyncio
import html
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.chdir(ROOT/'backend')
from app.core.runtime_env import load_sterling_env
load_sterling_env()
from app.services import db, public_access
db.init()
from app.services.notifications import telegram, telegram_bot
telegram.restore_legacy_config_from_store()


async def run():
    data = public_access.publish(sys.argv[1],mode=sys.argv[2])
    result = {'published':True,'telegram_configured':bool(telegram.TELEGRAM_TOKEN and telegram.TELEGRAM_CHAT_ID)}
    if result['telegram_configured']:
        menu = await telegram_bot._api('setChatMenuButton',{'menu_button':{
            'type':'web_app','text':'Sterling desk','web_app':{'url':data['mini_app_url']}}},timeout=10)
        result['telegram_menu_updated'] = menu.get('ok') is True
        previous = db.get_config_strict('sterling_public_access_notified','')
        if previous != data['app_url']:
            sent = await telegram_bot._api('sendMessage',{'chat_id':telegram.TELEGRAM_CHAT_ID,'parse_mode':'HTML',
                'text':f'<b>Sterling access updated</b>\n\nApp: {html.escape(data["app_url"])}\nMini App: {html.escape(data["mini_app_url"])}\n\nOpen the new link or use /menu. Existing tabs on an old hostname need to be reopened.',
                'reply_markup':{'inline_keyboard':[[{'text':'Open app','url':data['app_url']},
                    {'text':'Open desk','web_app':{'url':data['mini_app_url']}}]]}},timeout=10)
            result['telegram_notice_sent'] = sent.get('ok') is True
            if sent.get('ok'):db.set_config_strict('sterling_public_access_notified',data['app_url'])
    print(json.dumps(result))


try:
    asyncio.run(run())
except Exception as error:
    print(json.dumps({'published':False,'failure_type':type(error).__name__}))
    sys.exit(1)
