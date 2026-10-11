from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.services.notifications import telegram_kite as desk, telegram_lifecycle as outbox
from app.services.exchanges.kite import session
from app.core.runtime_env import load_sterling_env


def test_integration_allowlist_and_environment_precedence(tmp_path):
    f = tmp_path / '.env'
    f.write_text('KITE_API_KEY=synthetic\nKITE_API_SECRET=synthetic-secret\nTELEGRAM_TOKEN=synthetic-token\nPATH=/bad\nKITE_UNKNOWN=x\n')
    env = {'KITE_API_KEY': 'external'}
    load_sterling_env(f, env)
    assert env == {'KITE_API_KEY': 'external', 'KITE_API_SECRET': 'synthetic-secret', 'TELEGRAM_TOKEN': 'synthetic-token'}


def test_login_url_binds_existing_account_and_warns_about_local_redirect(monkeypatch):
    from app.services import public_access
    monkeypatch.setattr(public_access, 'status', lambda: {})
    from urllib.parse import urlparse, parse_qs
    account = SimpleNamespace(id='KITE-SYNTHETIC', api_key='synthetic', api_secret='synthetic-secret', access_token='', token_expires_at=None, token_expires_at_ms=None, last_login_at_ms=None, user_name='', kite_user_id='', is_paper=False)
    monkeypatch.setattr(desk, '_active_account', lambda uid=desk.UID: account)
    text, keyboard = desk.broker_login_card()
    query = parse_qs(urlparse(keyboard['inline_keyboard'][0][0]['url']).query)
    state = parse_qs(query['redirect_params'][0])['state'][0]
    assert session.parse_state(state) == (desk.UID, account.id)
    assert 'localhost' in text and '15 min' in text
    assert 'Generated' in text and 'earlier messages' in text
    assert 'synthetic-secret' not in str((text, keyboard))
    assert session.parse_state(state + 'tampered') is None


def test_missing_credentials_has_no_login_url(monkeypatch):
    monkeypatch.setattr(desk, '_active_account', lambda uid=desk.UID: None)
    text, keyboard = desk.broker_login_card()
    assert 'Connect' in text and 'url' not in str(keyboard)


def test_connection_transition_notifies_once_without_market_or_signal_gate(monkeypatch):
    states = iter(['OFFLINE', 'OFFLINE', 'SESSION STORED · broker validation in app'])
    monkeypatch.setattr(desk, '_connection_seen', {})
    monkeypatch.setattr(desk, 'connection_state', lambda uid: next(states))
    calls = []
    monkeypatch.setattr(outbox, 'enqueue_best_effort', lambda **kw: calls.append(kw))
    for _ in range(3): desk.check_connection_alert()
    assert len(calls) == 1
    assert '/login' in calls[0]['detail']


def test_old_status_snapshot_refreshes_but_trade_history_is_preserved(monkeypatch):
    monkeypatch.setattr(desk, 'live_health_text', lambda: 'CURRENT HEALTH')
    stale = SimpleNamespace(event_type='LIVE STATUS', detail='KILLED SAFE_MODE old snapshot')
    assert desk._lifecycle_html(stale) == 'CURRENT HEALTH'
    stale.event_type = 'ENTRY FILLED'
    monkeypatch.setattr(desk, '_mode_label', lambda uid: 'LIVE')
    assert 'old snapshot' in desk._lifecycle_html(stale)


@pytest.mark.parametrize('command,callback,builder', [('/login','klogin','broker_login_card'),('/health','khealth','live_health_text'),('/strategies','kstrategies','strategies_text')])
async def test_new_commands_and_buttons_route(monkeypatch, command, callback, builder):
    monkeypatch.setattr(desk, builder, lambda: ('card', {}) if builder == 'broker_login_card' else 'card')
    if builder == 'broker_login_card': monkeypatch.setattr(desk, 'checked_broker_login_card', AsyncMock(return_value=('card', {})))
    send, edit = AsyncMock(), AsyncMock()
    monkeypatch.setattr(desk, '_send', send)
    monkeypatch.setattr(desk, '_edit', edit)
    monkeypatch.setattr(desk, '_answer_cb', AsyncMock())
    await desk.handle_text_command('123', command)
    await desk.handle_kite_callback('123', 1, 'callback', callback)
    assert send.await_count == 2
    edit.assert_not_awaited()
