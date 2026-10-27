from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit, urlencode
import asyncio
import pytest
from app.services.exchanges.kite import assisted_login as flow
from app.api.v1.endpoints import kite_login_credentials as endpoint
from fastapi import HTTPException, Response

@pytest.fixture(autouse=True)
def interactive_feature_policy(tmp_path, monkeypatch):
    from app.services import db
    from app.services.auth import user_store, account_policy
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'interactive-policy.db'))
    assert db.init()
    monkeypatch.setattr(user_store, '_users', {})
    monkeypatch.setattr(user_store, '_loaded', True)
    user_store._init_table()
    user_store.add('primary', 'Synthetic-password-123', role='admin', user_id='default')
    user_store.add('member', 'Synthetic-password-123', user_id='owner')
    for ident in ('default', 'owner'):
        account_policy.set_features('default', ident, dict.fromkeys(account_policy.FEATURES, True), 0)


@pytest.fixture(autouse=True)
async def clear_attempts():
    flow.attempts.clear()
    yield
    for value in flow.attempts.values():
        if value.task and not value.task.done():
            value.task.cancel()
            await value.task
    flow.attempts.clear()

async def test_attempt_ownership_concurrency_and_cancel(monkeypatch):
    waiting = asyncio.Event()
    async def browser(*args): await waiting.wait()
    monkeypatch.setattr(flow, 'browser_login', browser)
    value = flow.start('owner', 'account', 'AA001', 'password', 'encrypted-version')
    await asyncio.sleep(0)
    with pytest.raises(ValueError): flow.get('other', value.id)
    with pytest.raises(ValueError): flow.start('owner', 'second', 'BB001', 'password', 'version')
    await endpoint.cancel_assisted(value.id, SimpleNamespace(user_id='owner'))
    assert value.phase == 'cancelled'
    assert 'password' not in str(value.public())

async def test_totp_requires_owner_and_waiting_state(monkeypatch):
    value = flow.Attempt('attempt', 'owner', 'account', 'AA001')
    flow.attempts[value.id] = value
    monkeypatch.setattr(endpoint, 'owned', lambda *args: True)
    with pytest.raises(HTTPException) as denied:
        await endpoint.assisted_totp(value.id, endpoint.AssistedTotp(code='123456'), Response(), SimpleNamespace(user_id='other'))
    assert denied.value.status_code == 404
    value.phase = 'awaiting_totp'
    result = await endpoint.assisted_totp(value.id, endpoint.AssistedTotp(code='123456'), Response(), SimpleNamespace(user_id='owner'))
    assert result['phase'] == 'verifying_totp'
    assert value.otp.get_nowait() == '123456'
    with pytest.raises(HTTPException) as duplicate:
        await endpoint.assisted_totp(value.id, endpoint.AssistedTotp(code='123456'), Response(), SimpleNamespace(user_id='owner'))
    assert duplicate.value.status_code == 409

@pytest.mark.parametrize("verification_method", ["TOTP", "Kite Mobile App Code"])
@pytest.mark.parametrize("test_only", [False, True])
async def test_browser_fills_credentials_pauses_for_otp_and_saves_bound_callback(monkeypatch, test_only, verification_method):
    import playwright.async_api
    filled = []; clicks = []; closed = []; callback_route = None
    value = flow.Attempt('a', 'owner', 'account', 'AA001', test_only=test_only)
    acct = SimpleNamespace(api_key='test-key')
    monkeypatch.setattr(flow, 'select_verification_method', AsyncMock(return_value=verification_method))
    monkeypatch.setattr(flow.accounts, 'get', lambda *args: acct)
    monkeypatch.setattr(flow, '_credentials_unchanged', lambda *args: True)
    complete = AsyncMock()
    monkeypatch.setattr(flow.login_recovery, 'complete_login', complete)
    class Locator:
        first = property(lambda self: self)
        def __init__(self, selector): self.selector = selector
        async def fill(self, text): filled.append((self.selector, text))
        async def press_sequentially(self, text, **kw):
            filled.append((self.selector, text))
            await page.press('auto-submit')
        async def get_attribute(self, name): return 'text' if name == 'type' else 'twofactor'
        async def wait_for(self, **kw): pass
        async def is_visible(self): return self.selector in {'#userid', 'verification-field'} or (self.selector == 'mobile-label' and verification_method == 'Kite Mobile App Code')
        def filter(self, **kw): return self
        def or_(self, other): return self
        async def all_text_contents(self): return []
        async def click(self): clicks.append(self.selector)
    class Page:
        main_frame = object()
        def __init__(self): self.keyboard = self
        def set_default_timeout(self, value): pass
        def on(self, event, handler): pass
        def locator(self, selector): return Locator(selector)
        def get_by_role(self, role, **kw): return Locator(kw['name'])
        def get_by_label(self, *args): return Locator('verification-field')
        def get_by_text(self, matcher, **kw): return Locator('mobile-label' if getattr(matcher, 'pattern', '') == '^Mobile App Code$' else 'not-visible')
        async def goto(self, url, **kw): self.url = url
        async def press(self, key):
            outer = parse_qs(urlsplit(self.url).query)
            state = parse_qs(outer['redirect_params'][0])['state'][0]
            url = 'http://localhost:5173/api/v1/kite/callback?'+urlencode({'status':'success','state':state,'request_token':'one-use'})
            await callback_route({'requestId': 'callback-hop', 'frameId': 'main', 'request': {'url': url}})
            assert cdp_calls[-1] == ('Fetch.failRequest', {'requestId': 'callback-hop', 'errorReason': 'Aborted'})
    page = Page()
    cdp_calls = []
    class CDP:
        def on(self, name, handler):
            nonlocal callback_route
            callback_route = handler
        async def send(self, method, params=None):
            cdp_calls.append((method, params))
            if method == 'Page.getFrameTree': return {'frameTree': {'frame': {'id': 'main'}}}
    class Context:
        async def new_page(self): return page
        async def new_cdp_session(self, page): return CDP()
    class Browser:
        async def new_context(self, **kw): return Context()
        async def close(self): closed.append(True)
    class Driver:
        chromium = property(lambda self: self)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def launch(self, **kw): return Browser()
    monkeypatch.setattr(playwright.async_api, 'async_playwright', Driver)
    task = asyncio.create_task(flow.run(value, 'saved-password', 'version'))
    for _ in range(20):
        if value.phase in {'awaiting_totp', 'tested'}: break
        await asyncio.sleep(0)
    if test_only:
        await task
        assert value.phase == 'tested'
        assert value.credential_status == 'accepted'
        assert 'No broker session' in value.message
        complete.assert_not_awaited()
        assert closed == [True]
        return
    assert value.phase == 'awaiting_totp'
    assert value.verification_method == verification_method
    assert clicks == ['Login']
    assert filled[:2] == [('#userid','AA001'), ('#password','saved-password')]
    assert not complete.called
    value.otp.put_nowait('123456')
    await task
    assert value.phase == 'done'
    assert closed == [True]
    complete.assert_awaited_once()
    assert complete.await_args.kwargs['expected_kite_user_id'] == 'AA001'
    assert '123456' not in str(value.public())

async def test_errors_do_not_expose_credentials_or_retry(monkeypatch):
    browser = AsyncMock(side_effect=RuntimeError('secret-password and request_token=secret'))
    monkeypatch.setattr(flow, 'browser_login', browser)
    value = flow.Attempt('id','owner','account','AA001')
    await flow.run(value, 'secret-password', 'version')
    browser.assert_awaited_once()
    assert value.phase == 'failed'
    assert 'secret-password' not in str(value.public())
    assert 'request_token' not in str(value.public())

async def test_wrong_broker_return_is_rejected_before_session_save(monkeypatch):
    from app.services.exchanges.kite import login_recovery
    client = SimpleNamespace(generate_session=AsyncMock(return_value={'user_id':'WRONG','access_token':'secret'}), close=AsyncMock())
    acct = SimpleNamespace(api_key='key', api_secret='secret')
    monkeypatch.setattr(flow.accounts, 'build_client', lambda acct: client)
    save = AsyncMock()
    monkeypatch.setattr(flow.accounts, 'save_session', save)
    with pytest.raises(login_recovery.LoginIdentityError):
        await login_recovery.complete_login('owner', acct, 'token', expected_kite_user_id='AA001')
    assert not save.called
    client.close.assert_awaited_once()

async def test_preview_masks_fields_and_is_not_persisted():
    value = flow.Attempt('id', 'owner', 'account', 'AA001', phase='awaiting_totp')
    masked = object()
    page = SimpleNamespace(url='https://kite.zerodha.com/connect/login', locator=lambda selector: masked,
                           screenshot=AsyncMock(return_value=b'png-frame'))
    value.page = page
    assert await flow.preview(value) == b'png-frame'
    kwargs = page.screenshot.await_args.kwargs
    assert kwargs['mask'] == [masked]
    assert 'color: transparent' in kwargs['style']
    assert kwargs['full_page'] is False
    assert 'page' not in value.public()
    assert 'png-frame' not in str(value.public())

async def test_preview_rejects_non_kite_and_terminal_pages():
    value = flow.Attempt('id','owner','account','AA001', phase='awaiting_totp')
    value.page = SimpleNamespace(url='https://other.example', screenshot=AsyncMock())
    with pytest.raises(ValueError): await flow.preview(value)
    value.page.screenshot.assert_not_awaited()
    value.page.url = 'https://kite.zerodha.com/'
    value.phase = 'done'
    with pytest.raises(ValueError): await flow.preview(value)
    value.page.screenshot.assert_not_awaited()

async def test_preview_discards_frame_if_navigation_changes_during_capture():
    value = flow.Attempt('id','owner','account','AA001', phase='awaiting_totp')
    page = SimpleNamespace(url='https://kite.zerodha.com/', locator=lambda selector: object())
    async def screenshot(**kwargs):
        page.url = 'http://localhost:5173/api/v1/kite/callback?request_token=secret'
        return b'must-not-return'
    page.screenshot = screenshot
    value.page = page
    with pytest.raises(ValueError): await flow.preview(value)

async def test_another_user_cannot_view_preview(monkeypatch):
    value = flow.Attempt('id','owner','account','AA001', phase='awaiting_totp')
    flow.attempts[value.id] = value
    preview = AsyncMock()
    monkeypatch.setattr(flow, 'preview', preview)
    with pytest.raises(HTTPException) as error:
        await endpoint.assisted_preview(value.id, Response(), SimpleNamespace(user_id='other'))
    assert error.value.status_code == 404
    preview.assert_not_awaited()


@pytest.mark.parametrize('message', ['Invalid user ID or password.', 'User ID or password is incorrect', 'Wrong password'])
def test_only_explicit_credential_rejections_recommend_updating(message):
    failure = flow.rejection(message)
    assert failure.code == 'credentials_rejected'
    assert 'Update' in failure.message

@pytest.mark.parametrize('message', ['Invalid api_key', 'Network unavailable', 'Please try again'])
def test_app_and_network_errors_are_not_mislabeled_bad_password(message):
    assert flow.rejection(message) is None

async def test_browser_failure_reports_untested_credentials_without_leaking_exception(monkeypatch, caplog):
    monkeypatch.setattr(flow, 'browser_login', AsyncMock(side_effect=RuntimeError('password=secret')))
    value = flow.Attempt('id','owner','account','AA001')
    await flow.run(value, 'secret', 'version')
    assert value.error_code == 'browser_unavailable'
    assert value.credential_status == 'not_checked'
    assert 'not tested' in value.message
    assert 'secret' not in caplog.text

async def test_accepted_password_is_not_reported_rejected_on_totp_form_failure(monkeypatch):
    async def browser(value, *args):
        value.credential_status = 'accepted'
        raise flow.LoginFailure('totp_form_changed', 'Kite accepted the saved credentials; TOTP prompt not located.')
    monkeypatch.setattr(flow, 'browser_login', browser)
    value = flow.Attempt('id','owner','account','AA001')
    await flow.run(value, 'secret', 'version')
    assert value.credential_status == 'accepted'
    assert value.error_code == 'totp_form_changed'

@pytest.mark.parametrize('reject_first', [False, True])
async def test_verified_code_resumes_connect_and_rejected_code_can_retry(monkeypatch, reject_first):
    value = flow.Attempt('id', 'owner', 'account', 'AA001', phase='awaiting_totp')
    callback = asyncio.get_running_loop().create_future()
    verification = {'status': None}
    monkeypatch.setattr(flow, '_credentials_unchanged', lambda *args: True)
    clock = [0]
    monkeypatch.setattr(flow.time, 'monotonic', lambda: clock[0])
    original_sleep = asyncio.sleep
    async def advance(delay):
        clock[0] += 1
        if value.phase == 'awaiting_totp' and verification['status'] == 'error':
            assert 'fresh code' in value.message
            value.otp.put_nowait('222222')
        await original_sleep(0)
    monkeypatch.setattr(flow.asyncio, 'sleep', advance)
    filled = []
    class Field:
        async def fill(self, code): filled.append(code)
        async def press_sequentially(self, code, **kw):
            filled.append(code)
            verification['status'] = 'error' if reject_first and code == '111111' else 'success'
    class Submit:
        first = property(lambda self: self)
        def filter(self, **kw): return self
        async def is_visible(self): return True
        async def click(self):
            verification['status'] = 'error' if reject_first and filled[-1] == '111111' else 'success'
    class Page:
        def get_by_role(self, *args, **kw): return Submit()
        async def goto(self, url, **kw):
            assert url == 'https://kite.zerodha.com/connect/login?state=bound'
            callback.set_result(('one-use', 'bound'))
            raise RuntimeError('navigation aborted after callback interception')
    value.otp.put_nowait('111111')
    # Queue the next human code after the rejected-code state is published.
    async def retry():
        while not callback.done():
            if value.phase == 'awaiting_totp' and verification['status'] == 'error' and value.otp.empty():
                value.otp.put_nowait('222222')
            await original_sleep(0)
    task = asyncio.create_task(retry())
    try:
        await flow.verify_and_wait(value, Page(), Field(), callback, verification,
                                   'https://kite.zerodha.com/connect/login?state=bound', 'version')
    finally:
        task.cancel()
    assert callback.result() == ('one-use', 'bound')
    assert filled == (['', '111111', '', '', '222222'] if reject_first else ['', '111111'])
    assert '111111' not in str(value.public())


@pytest.mark.parametrize('menu', [False, True])
async def test_switches_mobile_challenge_after_form_is_visible(monkeypatch, menu):
    state = {'mobile': True, 'menu': False, 'totp': False}
    clicks = []
    class Locator:
        first = property(lambda self: self)
        def __init__(self, kind): self.kind = kind
        def filter(self, *, has_text):
            return Locator('mobile' if 'Mobile' in has_text.pattern else 'totp')
        async def is_visible(self): return state.get(self.kind, False)
        async def click(self):
            clicks.append(self.kind)
            state['mobile'] = False
            if self.kind == 'problem':
                state['menu' if menu else 'totp'] = True
            else: state['totp'] = True
        async def wait_for(self, **kwargs):
            if not state['menu']: raise TimeoutError()
    class Page:
        def get_by_text(self, text, **kwargs):
            if isinstance(text, str):
                state['problem'] = state['mobile']
                return Locator('problem')
            return Locator('menu')
        def locator(self, selector): return Locator('label')
    assert await flow.select_verification_method(Page()) == 'TOTP'
    assert clicks == (['problem', 'menu'] if menu else ['problem'])

async def test_does_not_guess_totp_when_mobile_code_is_the_only_method():
    class Locator:
        first = property(lambda self: self)
        def __init__(self, mobile=False): self.mobile = mobile
        def filter(self, *, has_text): return Locator('Mobile' in has_text.pattern)
        async def is_visible(self): return self.mobile
    page = SimpleNamespace(get_by_text=lambda *args, **kw: Locator(), locator=lambda selector: Locator())
    assert await flow.select_verification_method(page) == 'Kite Mobile App Code'

async def test_navigation_during_code_submission_waits_for_callback(monkeypatch):
    value = flow.Attempt('id', 'owner', 'account', 'AA001', phase='awaiting_totp')
    callback = asyncio.get_running_loop().create_future()
    monkeypatch.setattr(flow, '_credentials_unchanged', lambda *args: True)
    class Field:
        async def fill(self, code): pass
        async def press_sequentially(self, code, **kw): pass
    class Submit:
        first = property(lambda self: self)
        def filter(self, **kw): return self
        async def is_visible(self): return True
        async def is_enabled(self): return True
        async def click(self):
            asyncio.get_running_loop().call_soon(callback.set_result, ('token', 'state'))
            raise RuntimeError('Execution context was destroyed, most likely because of a navigation')
        async def all_text_contents(self): return []
    page = SimpleNamespace(get_by_role=lambda *args, **kw: Submit(), locator=lambda *args: Submit())
    value.otp.put_nowait('123456')
    await flow.verify_and_wait(value, page, Field(), callback, {'status': None}, 'connect-url', 'version')
    assert callback.result() == ('token', 'state')

@pytest.mark.parametrize('stage,code', [
    ('verification_input', 'verification_input_failed'),
    ('verification_submit', 'verification_submit_failed'),
    ('verification_callback', 'verification_callback_failed'),
])
async def test_browser_failures_do_not_blame_human_code(monkeypatch, stage, code):
    async def browser(value, *args):
        value.stage = stage
        raise RuntimeError('secret-code')
    monkeypatch.setattr(flow, 'browser_login', browser)
    value = flow.Attempt('id', 'owner', 'account', 'AA001')
    await flow.run(value, 'password', 'version')
    assert value.error_code == code
    assert 'incorrect' in value.message
    assert 'secret-code' not in str(value.public())


@pytest.mark.parametrize('wrong_state', [False, True])
async def test_redirect_hops_capture_callback_before_localhost_is_requested(monkeypatch, wrong_state):
    value = flow.Attempt('id', 'owner', 'account', 'AA001')
    state = flow.session.make_state('owner', 'account')
    other = flow.session.make_state('owner', 'account') if wrong_state else state
    # Ensure a distinct signed attempt even if the timestamp happens to match.
    if wrong_state: other = flow.session.make_state('other', 'account')
    calls = []
    handler = None
    class CDP:
        def on(self, event, fn):
            nonlocal handler
            handler = fn
        async def send(self, method, args=None):
            calls.append((method, args))
            if method == 'Page.getFrameTree': return {'frameTree': {'frame': {'id': 'main'}}}
    callback = asyncio.get_running_loop().create_future()
    context = SimpleNamespace(new_cdp_session=AsyncMock(return_value=CDP()))
    await flow.install_navigation_guard(context, object(), value, state, callback)
    await handler({'requestId': 'start', 'frameId': 'main', 'request': {'url': 'https://kite.zerodha.com/connect/login'}})
    assert calls[-1][0] == 'Fetch.continueRequest'
    # A second HTTP redirect, which ordinary route handlers would not see.
    url = 'http://localhost:5173/api/v1/kite/callback?'+urlencode({'status':'success','state':other,'request_token':'one-use'})
    await handler({'requestId': 'redirect', 'frameId': 'main', 'request': {'url': url}})
    assert calls[-1] == ('Fetch.failRequest', {'requestId':'redirect', 'errorReason':'Aborted'})
    if wrong_state:
        with pytest.raises(flow.LoginFailure): callback.result()
    else:
        assert callback.result() == ('one-use', state)

@pytest.mark.parametrize('guard_kind', ['routing', 'cdp'])
async def test_chromium_captures_real_http_redirect_chain_without_loading_callback(monkeypatch, guard_kind):
    """No broker credentials: exercise actual browser redirect interception locally."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from playwright.async_api import async_playwright
    real_split = flow.urlsplit
    state = flow.session.make_state('owner', 'account')
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            received.append(self.path)
            if self.path.startswith('/api/v1/kite/callback'):
                self.send_response(200)
                self.end_headers()
                return
            self.send_response(302)
            target = '/middle' if self.path == '/start' else f'http://localhost:{self.server.server_port}/api/v1/kite/callback?'+urlencode({'status':'success','state':state,'request_token':'synthetic-token'})
            self.send_header('Location', target)
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def test_split(url):
        parsed = real_split(url)
        if parsed.hostname == '127.0.0.1' and parsed.path in {'/start', '/middle'}:
            return parsed._replace(scheme='https', netloc='kite.zerodha.com')
        return parsed
    monkeypatch.setattr(flow, 'urlsplit', test_split)
    try:
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(channel='chrome', headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                callback = asyncio.get_running_loop().create_future()
                value = flow.Attempt('id', 'owner', 'account', 'AA001')
                if guard_kind == 'cdp':
                    await flow.install_navigation_guard(context, page, value, state, callback)
                else:
                    async def old_guard(route):
                        if 'request_token=' in route.request.url:
                            callback.set_result(('synthetic-token', state))
                            await route.abort()
                        else:
                            await route.continue_()
                    await context.route('**/*', old_guard)
                try:
                    await page.goto(f'http://127.0.0.1:{server.server_port}/start')
                except Exception:
                    pass  # callback navigation is deliberately blocked before network I/O
                if guard_kind == 'cdp':
                    assert await asyncio.wait_for(callback, 5) == ('synthetic-token', state)
                    assert received == ['/start', '/middle']
                else:
                    assert not callback.done()
                    assert len(received) == 3
                    assert received[-1].startswith('/api/v1/kite/callback')
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()
