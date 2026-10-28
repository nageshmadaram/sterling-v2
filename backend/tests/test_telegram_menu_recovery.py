"""Telegram repair contracts: key rotation, menu dispatch, diagnostics and ownership."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.auth import UserContext, get_current_user
from app.core.security import encrypt
from app.api.v1.endpoints import kite_telegram as api
from app.services.notifications import kite_telegram_store as store, telegram_bot as bot, telegram_kite as desk


@pytest.fixture
def configs(monkeypatch):
    values = {}
    monkeypatch.setattr(store.db, 'get_config', lambda key, default='': values.get(key, default))
    monkeypatch.setattr(store.db, 'set_config_strict', lambda key, value: values.__setitem__(key, value))
    return values


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(api.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id='default', username='operator', role='admin')
    with TestClient(app) as client:
        yield client


@pytest.mark.kite_features('default', 'other')
def test_unreadable_target_is_listed_and_can_be_repaired_or_removed(configs, client):
    configs['kite_tg_targets:default'] = json.dumps([{'id': 'broken', 'bot_token_enc': 'fernet:invalid', 'chat_id': '123', 'reachable': True}])
    response = client.get('/api/v1/kite/telegram')
    assert response.status_code == 200
    target = response.json()['targets'][0]
    assert target['credential_error'] and not target['reachable']
    assert not target['bot_token_hint'] and target['bot_token_set']
    assert 'bot_token_enc' not in target
    response = client.put('/api/v1/kite/telegram/broken', json={'bot_token': 'synthetic-token'})
    assert response.status_code == 200 and not response.json()['credential_error']
    assert store.get('default', 'broken').bot_token == 'synthetic-token'
    assert not store.get('default', 'broken').reachable
    assert client.delete('/api/v1/kite/telegram/broken').status_code == 200


def test_unreadable_target_test_is_actionable_and_does_not_send(configs, client, monkeypatch):
    configs['kite_tg_targets:default'] = json.dumps([{'id': 'broken', 'bot_token_enc': 'fernet:invalid', 'chat_id': '123'}])
    send = AsyncMock()
    monkeypatch.setattr(store, 'send_via', send)
    response = client.post('/api/v1/kite/telegram/broken/test')
    assert response.status_code == 409 and 'enter its token again' in response.json()['detail']
    send.assert_not_awaited()


def test_status_reports_queue_and_never_exposes_control_credentials(configs, client, monkeypatch):
    monkeypatch.setattr('app.services.notifications.telegram_lifecycle.pending_summary', lambda uid: {'pending': 4})
    monkeypatch.setattr(bot._tg, 'TELEGRAM_TOKEN', 'secret-synthetic-token')
    monkeypatch.setattr(bot._tg, 'TELEGRAM_CHAT_ID', '123')
    response = client.get('/api/v1/kite/telegram/status')
    assert response.status_code == 200 and response.json()['queued_lifecycle_alerts'] == 4
    assert response.json()['control']['configured']
    assert 'secret-synthetic-token' not in response.text
    client.app.dependency_overrides[get_current_user] = lambda: UserContext(user_id='other', username='other', role='user')
    assert client.get('/api/v1/kite/telegram/status').json()['control'] == {'state': 'not_available_this_user'}
    assert client.post('/api/v1/kite/telegram/control/check').status_code == 403


@pytest.mark.parametrize('webhook,commands,code', [(False, True, ''), (True, True, 'WEBHOOK_ACTIVE'), (False, False, 'STALE_COMMANDS')])
def test_connection_check_only_uses_read_only_methods(configs, client, monkeypatch, webhook, commands, code):
    monkeypatch.setattr(bot._tg, 'TELEGRAM_TOKEN', 'synthetic-token')
    monkeypatch.setattr(bot._tg, 'TELEGRAM_CHAT_ID', '123')
    async def reply(method, payload, **kwargs):
        result = {'getMe': {'id': 1}, 'getChat': {'type': 'private'},
                  'getWebhookInfo': {'url': 'https://example.test/hook' if webhook else ''},
                  'getMyCommands': bot.COMMANDS if commands else [{'command': 'obsolete'}]}[method]
        return {'ok': True, 'result': result}
    tx = AsyncMock(side_effect=reply)
    monkeypatch.setattr(bot, '_api', tx)
    monkeypatch.setattr(bot, 'poll_status', lambda: {'state': 'polling', 'commands_registered': True})
    response = client.post('/api/v1/kite/telegram/control/check')
    assert response.status_code == 200 and response.json()['error_code'] == code
    assert all(call.args[0] in {'getMe', 'getChat', 'getWebhookInfo', 'getMyCommands'} for call in tx.await_args_list)


@pytest.mark.parametrize('worker_state,error_code,expected', [
    ('error', 'POLL_CONFLICT', 'POLL_CONFLICT'),
    ('stalled', '', 'POLL_STALLED'),
    ('starting', '', 'WORKER_STARTING'),
    ('error', 'NETWORK_ERROR', 'NETWORK_ERROR'),
])
def test_check_reports_broken_telegram_polling_even_when_bot_credentials_work(
        configs, client, monkeypatch, worker_state, error_code, expected):
    monkeypatch.setattr(bot._tg, 'TELEGRAM_TOKEN', 'synthetic-token')
    monkeypatch.setattr(bot._tg, 'TELEGRAM_CHAT_ID', '123')

    async def valid(method, payload, **kwargs):
        values = {
            'getMe': {'id': 1},
            'getChat': {'type': 'private'},
            'getWebhookInfo': {'url': ''},
            'getMyCommands': bot.COMMANDS,
        }
        return {'ok': True, 'result': values[method]}
    tx = AsyncMock(side_effect=valid)
    monkeypatch.setattr(bot, '_api', tx)
    monkeypatch.setattr(bot, 'poll_status', lambda: {
        'state': worker_state, 'error_code': error_code,
        'commands_registered': True,
    })

    response = client.post('/api/v1/kite/telegram/control/check')
    assert response.status_code == 200
    assert response.json()['ok'] is False
    assert response.json()['error_code'] == expected
    # Diagnostics only inspect configuration, without deleting webhooks,
    # consuming bot updates, executing trades or sending test messages.
    assert all(call.args[0] in {'getMe', 'getChat', 'getWebhookInfo',
                               'getMyCommands'} for call in tx.await_args_list)


@pytest.mark.asyncio
async def test_crashed_telegram_worker_restarts_serially_and_hides_secret_errors(caplog):
    from main import _supervise_telegram
    count = 0
    resumed = asyncio.Event()

    async def run():
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError('Sensitive Telegram API URL with secret token')
        resumed.set()
        await asyncio.Future()

    task = asyncio.create_task(_supervise_telegram('control', run, restart_delay=0))
    try:
        await asyncio.wait_for(resumed.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert count == 2
    assert 'Sensitive Telegram API URL' not in caplog.text


@pytest.mark.asyncio
async def test_telegram_shutdown_never_restarts_a_cancelled_poll_worker():
    from main import _supervise_telegram
    started = asyncio.Event()
    count = 0

    async def run():
        nonlocal count
        count += 1
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(_supervise_telegram('control', run, restart_delay=0))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('command', [row['command'] for row in bot.COMMANDS])
async def test_each_advertised_command_has_a_response(monkeypatch, command):
    send = AsyncMock(return_value={'ok': True})
    monkeypatch.setattr(desk, '_send', send)
    monkeypatch.setattr(desk, '_active_account', lambda uid=desk.UID: None)
    monkeypatch.setattr(desk, '_ready_rows', lambda uid=desk.UID: [])
    monkeypatch.setattr(desk, 'alerts_enabled', lambda uid=desk.UID: True)
    monkeypatch.setattr(desk, 'set_alerts_enabled', lambda *args: None)
    monkeypatch.setattr('app.services.kite_engine.state.get_config', lambda uid: SimpleNamespace(auto_execute=False))
    await desk.handle_text_command('123', '/' + command + '@ExampleBot')
    send.assert_awaited_once()
    assert send.await_args.args[0]
    if command == 'algo':
        assert 'Confirm ALGO' in send.await_args.args[0]
        assert send.await_args.args[2]['inline_keyboard'][0][0]['callback_data'].startswith('kok|')


@pytest.mark.asyncio
@pytest.mark.parametrize('callback', ['kmenu', 'ksig', 'kpos', 'kpnl', 'kscan', 'kalert', 'kauto', 'obsolete_menu'])
async def test_all_menu_buttons_and_old_buttons_respond(monkeypatch, callback):
    send = AsyncMock(return_value={'ok': True})
    edit = AsyncMock(return_value={'ok': True})
    monkeypatch.setattr(desk, '_send', send)
    monkeypatch.setattr(desk, '_edit', edit)
    monkeypatch.setattr(desk, '_answer_cb', AsyncMock())
    monkeypatch.setattr(desk, '_active_account', lambda uid=desk.UID: None)
    monkeypatch.setattr(desk, '_ready_rows', lambda uid=desk.UID: [])
    monkeypatch.setattr(desk, 'alerts_enabled', lambda uid=desk.UID: True)
    monkeypatch.setattr(desk, 'set_alerts_enabled', lambda *args: None)
    monkeypatch.setattr('app.services.kite_engine.state.get_config', lambda uid: SimpleNamespace(auto_execute=False))
    await desk.handle_kite_callback('123', 1, 'callback', callback)
    assert edit.await_count >= 1 or send.await_count >= 1


@pytest.mark.asyncio
async def test_poll_registers_menu_and_requests_callbacks(monkeypatch):
    monkeypatch.setattr(bot._tg, 'TELEGRAM_TOKEN', 'synthetic-token')
    monkeypatch.setattr(bot._tg, 'TELEGRAM_CHAT_ID', '123')
    calls = []
    async def reply(method, payload, **kwargs):
        calls.append((method, payload))
        if method == 'getUpdates' and payload.get('timeout') == 30:
            raise asyncio.CancelledError()
        return {'ok': True, 'result': []}
    monkeypatch.setattr(bot, '_api', reply)
    with pytest.raises(asyncio.CancelledError):
        await bot.poll_loop()
    assert calls[0] == ('setMyCommands', {'commands': bot.COMMANDS})
    assert calls[-1][1]['allowed_updates'] == ['message', 'callback_query']


def test_restored_scanners_send_alerts_only_for_explicit_user_opt_in(monkeypatch):
    from unittest.mock import Mock
    for name in ['_supertrend_alerts', '_gamma_move_alerts', '_snapback_alerts']:
        monkeypatch.setattr(desk, name, lambda uid: [])
    extra = Mock(return_value=['restored-alert'])
    monkeypatch.setattr(desk, '_adaptive_edge_alerts', extra)
    monkeypatch.setattr('app.services.archived_strategies.enabled', lambda uid, strategy: uid == 'owner' and strategy == 'adaptive_edge')
    assert desk._strategy_alerts('other') == []
    extra.assert_not_called()
    assert desk._strategy_alerts('owner') == ['restored-alert']
    extra.assert_called_once_with('owner')


@pytest.mark.asyncio
async def test_slow_scan_does_not_block_menu_and_duplicate_scan_is_not_started(monkeypatch):
    waiting = asyncio.Event()
    async def scan():
        await waiting.wait()
        return 'Scan complete'
    tx = AsyncMock(return_value={'ok': True})
    runner = AsyncMock(side_effect=scan)
    monkeypatch.setattr(desk, '_run_scan', runner)
    monkeypatch.setattr(desk, '_send', tx)
    monkeypatch.setattr(desk, 'build_kite_status_text', lambda: 'Menu status')
    await desk._request_scan('123')
    await asyncio.sleep(0)
    await desk.handle_text_command('123', '/menu')
    assert tx.await_args.args[0] == 'Menu status'
    await desk._request_scan('123')
    runner.assert_awaited_once()
    assert 'already running' in tx.await_args.args[0]
    waiting.set()
    await asyncio.gather(*list(desk._scan_tasks.values()))
    assert tx.await_args.args[0] == 'Scan complete'
    assert not desk._scan_tasks


def test_action_handles_are_random_and_fit_telegram_callback_limit():
    handles = {desk._next_id() for _ in range(100)}
    assert len(handles) == 100
    assert all(len(handle) == 32 and len(('kbuy|' + handle).encode()) <= 64 for handle in handles)


def test_signal_alert_preferences_are_user_scoped_and_strict(configs, client, monkeypatch):
    monkeypatch.setattr(store.db, 'set_config_strict', lambda key, value: configs.__setitem__(key, value))
    client.app.dependency_overrides[get_current_user] = lambda: UserContext(user_id='other', username='other', role='user')
    response = client.put('/api/v1/kite/telegram/preferences', json={'signal_alerts_enabled': True})
    assert response.status_code == 200 and configs['kite_tg_alerts_enabled:other'] == '1'
    assert 'kite_tg_alerts_enabled:default' not in configs
    assert client.put('/api/v1/kite/telegram/preferences', json={'signal_alerts_enabled': 'false'}).status_code == 422
