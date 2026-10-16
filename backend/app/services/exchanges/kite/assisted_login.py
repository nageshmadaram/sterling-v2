"""One user-initiated browser login, paused for human TOTP. No private login API."""
from __future__ import annotations
import asyncio
import logging
import re
from dataclasses import dataclass, field
import secrets
import time
import traceback
from typing import Any
from urllib.parse import urlsplit, parse_qs
from . import accounts, login_recovery, session

TTL = 300
MAX_ATTEMPTS = 8
log = logging.getLogger(__name__)
TERMINAL = {'done', 'tested', 'failed', 'cancelled'}

class LoginFailure(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message

def navigation_in_progress(error):
    # Inspect only fixed engine messages; never expose or log the raw error,
    # which may contain the code, credentials, or callback token.
    text = str(error).lower()
    return any(marker in text for marker in (
        'execution context was destroyed', 'cannot find context',
        'interrupted by another navigation', 'net::err_aborted', 'net::err_failed',
        'element is not attached to the dom',
    ))

def rejection(message):
    text = str(message or "").lower()
    if re.search(r"(invalid|incorrect|wrong).*(password|user.?id|credentials)|(password|user.?id|credentials).*(invalid|incorrect|wrong)", text):
        return LoginFailure("credentials_rejected", "Kite rejected the saved user ID or password. Update the saved Kite login details before trying again.")
    if any(word in text for word in ("blocked", "locked", "suspended", "captcha")):
        return LoginFailure("broker_challenge", "Kite requires extra verification or account recovery. Open manual Kite login. Your saved password has not been confirmed incorrect.")
    return None

@dataclass
class Attempt:
    id: str
    user_id: str
    account_id: str
    username: str
    phase: str = 'opening'
    message: str = 'Opening an isolated Kite browser…'
    test_only: bool = False
    credential_status: str = 'not_checked'
    error_code: str = ''
    stage: str = 'browser_start'
    verification_method: str = 'TOTP'
    created: float = field(default_factory=time.monotonic)
    otp: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1), repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)

    page: Any = field(default=None, repr=False)
    preview_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def public(self):
        return {'id': self.id, 'account_id': self.account_id, 'username': self.username,
                'phase': self.phase, 'message': self.message, 'credential_status': self.credential_status,
                'error_code': self.error_code, 'test_only': self.test_only, 'stage': self.stage, 'verification_method': self.verification_method}

attempts: dict[str, Attempt] = {}


def get(user_id, attempt_id):
    value = attempts.get(attempt_id)
    if value is None or value.user_id != user_id:
        raise ValueError('Login attempt not found. Start again.')
    return value


def start(user_id, account_id, username, password, credential_version, *, test_only=False):
    # A single pending browser per owner, including double-clicks and other tabs.
    for ident, value in list(attempts.items()):
        if time.monotonic() - value.created > TTL + 60:
            if value.task and not value.task.done(): value.task.cancel()
            del attempts[ident]
        elif value.user_id == user_id and value.phase not in TERMINAL:
            raise ValueError('A broker login is already running. Finish or cancel it first.')
    if sum(a.phase not in TERMINAL for a in attempts.values()) >= MAX_ATTEMPTS:
        raise ValueError('Login browsers are busy. Try again shortly.')
    value = Attempt(secrets.token_urlsafe(24), user_id, account_id, username, test_only=test_only)
    attempts[value.id] = value
    value.task = asyncio.create_task(run(value, password, credential_version))
    return value


def _credentials_unchanged(attempt, version):
    from app.services import db
    return (accounts.get(attempt.user_id, attempt.account_id) is not None and
            db.get_config_strict(f'kite_login_credentials:{attempt.user_id}:{attempt.account_id}', '') == version)


async def run(value, password, version):
    try:
        async with asyncio.timeout(TTL):
            operation = browser_login(value, password, version)
            password = ''
            await operation
    except asyncio.CancelledError:
        value.phase, value.message = 'cancelled', 'Broker login cancelled.'
    except LoginFailure as failure:
        value.error_code = failure.code
        if failure.code == 'credentials_rejected': value.credential_status = 'rejected'
        value.phase, value.message = 'failed', failure.message
        log.warning('Assisted login failed account=%s stage=%s code=%s', value.account_id, value.stage, value.error_code)
    except Exception as error:
        # Never log raw exceptions: browser errors can embed passwords and URLs.
        messages = {
            'browser_start': ('browser_unavailable', 'The login browser could not start. Saved credentials were not tested. Sterling’s browser runtime needs attention.'),
            'opening_page': ('kite_unreachable', 'The login browser could not reach Kite. Saved credentials were not tested. Check connectivity and retry.'),
            'filling_credentials': ('login_form_changed', 'Sterling could not locate Kite’s login fields. Saved credentials were not tested; use manual login while this form is checked.'),
            'submitting_password': ('credentials_unconfirmed', 'Kite did not confirm the saved credentials. They are not proven incorrect. Try manual login for any additional challenge.'),
            'waiting_totp': ('totp_timeout', 'The login expired while waiting for TOTP. Start a fresh login; your saved password was accepted.'),
            'verifying_totp': ('totp_unconfirmed', 'Kite did not complete TOTP verification. Check the current code and start a fresh login; your saved password was accepted.'),
            'verification_input': ('verification_input_failed', 'Sterling could not enter the verification code into Kite’s form. This does not mean your code was incorrect. Start a fresh login.'),
            'verification_submit': ('verification_submit_failed', 'Sterling could not submit Kite’s verification form. This does not mean your code was incorrect. Start a fresh login.'),
            'verification_callback': ('verification_callback_failed', 'The broker connection stopped while waiting for Kite’s redirect. Your code has not been reported incorrect.'),
            'saving_session': ('session_save_failed', 'Kite sign-in completed, but Sterling could not save the session. Check your API configuration and retry.'),
        }
        value.error_code, value.message = messages.get(value.stage, ('login_unconfirmed', 'Broker login could not be confirmed. Use manual login or test the saved credentials.'))
        value.phase = 'failed'
        log.warning('Assisted login failed account=%s stage=%s code=%s exception_type=%s', value.account_id, value.stage, value.error_code, type(error).__name__)
        # Stack locations contain no browser values, URLs, credentials or OTP.
        log.warning('Assisted login failure locations=%s',
                    [(frame.name, frame.lineno) for frame in traceback.extract_tb(error.__traceback__)
                     if frame.filename == __file__])
    finally:
        password = ''
        while not value.otp.empty(): value.otp.get_nowait()


async def browser_login(value, password, version):
    from playwright.async_api import async_playwright
    acct = accounts.get(value.user_id, value.account_id)
    if not acct or not _credentials_unchanged(value, version):
        raise ValueError('Account changed')
    state = session.make_state(value.user_id, value.account_id)
    callback = asyncio.get_running_loop().create_future()
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(channel='chrome', headless=True)
        try:
            context = await browser.new_context(service_workers='block', viewport={'width': 520, 'height': 820})
            page = await context.new_page()
            value.page = page
            page.set_default_timeout(15000)
            result = {'accepted': False, 'failure': None}
            verification = {'status': None}
            async def login_response(response):
                parsed = urlsplit(response.url)
                if parsed.scheme != 'https' or parsed.hostname != 'kite.zerodha.com': return
                if (value.stage in {'verification_input', 'verification_submit', 'verification_callback'}
                        and response.request.method == 'POST' and parsed.path.startswith('/api/')):
                    try:
                        data = await response.json()
                        if data.get('status') in {'success', 'error'}:
                            verification['status'] = data['status']
                            log.info('Assisted login verification account=%s outcome=%s', value.account_id,
                                     'accepted' if data['status'] == 'success' else 'rejected')
                    except Exception:
                        pass
                    return
                if parsed.path != '/api/login': return
                try:
                    data = await response.json()
                    if data.get('status') == 'success' and data.get('data', {}).get('request_id'):
                        result['accepted'] = True
                        value.credential_status = 'accepted'
                    elif data.get('status') == 'error':
                        result['failure'] = rejection(data.get('message'))
                except Exception:
                    pass
            page.on('response', login_response)
            await install_navigation_guard(context, page, value, state, callback)
            value.stage = 'opening_page'
            await page.goto(session.login_url(acct.api_key, state=state), wait_until='domcontentloaded')
            if urlsplit(page.url).hostname != 'kite.zerodha.com': raise ValueError('Unexpected origin')
            if not _credentials_unchanged(value, version): raise ValueError('Credentials changed')
            value.stage = 'filling_credentials'
            user_field = page.locator('#userid')
            if not await user_field.is_visible():
                change_user = page.get_by_role('link', name='Change user', exact=True)
                if await change_user.is_visible(): await change_user.click()
            await user_field.fill(value.username)
            await page.locator('#password').fill(password)
            password = ''
            value.stage = 'submitting_password'
            value.phase, value.message = 'submitting_password', 'Submitting the saved Kite username and password…'
            await page.get_by_role('button', name='Login', exact=True).click()
            # No CAPTCHA bypass, credential retries, traces or saved browser profile.
            otp_field = page.get_by_label(re.compile(r'Mobile App Code|TOTP|OTP|authenticator|verification code', re.I)).or_(
                page.locator('input[autocomplete="one-time-code"], input#totp, input#pin, input#otp, input#twofactor, input[name="totp"], input[placeholder*="TOTP" i], input[placeholder*="OTP" i], input[inputmode="numeric"]')
            ).filter(visible=True).first
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if result['failure']: raise result['failure']
                if await otp_field.is_visible():
                    value.credential_status = 'accepted'
                    break
                if value.test_only and result['accepted']: break
                # Only classify fixed known rejection messages; never return page text.
                for text in await page.locator('.error, .error-message, [role="alert"]').all_text_contents():
                    failure = rejection(text)
                    if failure: raise failure
                await asyncio.sleep(.2)
            else:
                if result['accepted']:
                    raise LoginFailure('totp_form_changed', 'Kite accepted the saved credentials, but Sterling could not locate the TOTP prompt. Use manual login; no password update is indicated.')
                raise LoginFailure('credentials_unconfirmed', 'Kite did not confirm whether these credentials are correct. Check the live preview or use manual login; do not assume the password is wrong.')
            if value.test_only:
                value.phase, value.message = 'tested', 'Kite accepted the saved user ID and password. TOTP is still required to log in. No broker session was created by this test.'
                return
            value.verification_method = await select_verification_method(page)
            value.stage = 'waiting_totp'
            log.info('Assisted verification field account=%s type=%s id=%s', value.account_id,
                     await otp_field.get_attribute('type'), await otp_field.get_attribute('id'))
            value.phase = 'awaiting_totp'
            value.message = ('Kite accepted your password. Enter the Mobile App Code shown in your Kite mobile app.'
                             if value.verification_method == 'Kite Mobile App Code' else
                             'Kite accepted your password. Enter the current TOTP from your authenticator.')
            await verify_and_wait(value, page, otp_field, callback, verification,
                                  session.login_url(acct.api_key, state=state), version)
            token, received_state = callback.result()
            if not _credentials_unchanged(value, version): raise ValueError('Credentials changed')
            value.stage = 'saving_session'
            value.phase, value.message = 'saving_session', 'Kite returned a login token. Saving your broker session…'
            await login_recovery.complete_login(value.user_id, acct, token, login_state=received_state,
                                                expected_kite_user_id=value.username)
            value.phase, value.message = 'done', f'Kite login successful for {value.username}. Broker session saved.'
        finally:
            value.page = None
            await browser.close()


async def install_navigation_guard(context, page, value, state, callback):
    """Intercept every document hop, including server redirects, before network I/O.

    Playwright routing only handles the first URL in an HTTP redirect chain.
    Chromium Fetch pauses each hop, so localhost callbacks need never load and
    the callback endpoint cannot race Sterling to exchange the one-use token.
    """
    cdp = await context.new_cdp_session(page)
    main_frame = (await cdp.send('Page.getFrameTree'))['frameTree']['frame']['id']
    async def paused(event):
        request_id = event['requestId']
        if event.get('frameId') != main_frame:
            await cdp.send('Fetch.failRequest', {'requestId': request_id, 'errorReason': 'Aborted'})
            return
        url = event['request']['url']
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        if 'request_token' in query and 'state' in query:
            try:
                token, received_state = login_recovery.parse_callback_handoff(
                    url, expected_user_id=value.user_id, expected_account_id=value.account_id)
                if received_state != state: raise ValueError('Different attempt')
            except ValueError:
                await cdp.send('Fetch.failRequest', {'requestId': request_id, 'errorReason': 'Aborted'})
                if not callback.done(): callback.set_exception(LoginFailure('invalid_callback', 'Kite returned a callback that did not match this login. Start a fresh login.'))
                return
            await cdp.send('Fetch.failRequest', {'requestId': request_id, 'errorReason': 'Aborted'})
            log.info('Assisted login signed callback captured account=%s', value.account_id)
            if not callback.done(): callback.set_result((token, received_state))
            return
        if parsed.scheme != 'https' or parsed.hostname != 'kite.zerodha.com':
            await cdp.send('Fetch.failRequest', {'requestId': request_id, 'errorReason': 'Aborted'})
            if not callback.done(): callback.set_exception(LoginFailure('unexpected_navigation', 'Kite redirected outside the supported login flow without a signed broker callback. Use manual Kite login for this additional step.'))
            return
        await cdp.send('Fetch.continueRequest', {'requestId': request_id})
    cdp.on('Fetch.requestPaused', paused)
    await cdp.send('Fetch.enable', {'patterns': [{'urlPattern': '*', 'resourceType': 'Document', 'requestStage': 'Request'}]})
    return cdp


async def select_verification_method(page):
    """Select authenticator TOTP only after the asynchronous challenge renders."""
    problem = page.get_by_text('Problem with Mobile App Code?', exact=True).first
    if await problem.is_visible():
        await problem.click()
        external = page.get_by_text(re.compile(r'^(Use )?(external )?TOTP( instead)?$', re.I)).first
        # Some Kite versions switch directly, others first show a method menu.
        try:
            await external.wait_for(state='visible', timeout=2000)
        except Exception:
            pass
        if await external.is_visible(): await external.click()
    totp_label = page.locator('label').filter(has_text=re.compile(r'TOTP|authenticator', re.I)).first
    mobile_label = page.locator('label').filter(has_text=re.compile(r'Mobile App Code', re.I)).first
    for _ in range(30):
        if await totp_label.is_visible(): return 'TOTP'
        if await mobile_label.is_visible() and not await problem.is_visible():
            return 'Kite Mobile App Code'
        await asyncio.sleep(.1)
    if await mobile_label.is_visible(): return 'Kite Mobile App Code'
    raise LoginFailure('verification_method_unknown',
                       'Kite accepted your password, but its verification method could not be identified. Use manual Kite login; no code was submitted.')


async def verify_and_wait(value, page, otp_field, callback, verification, connect_url, version):
    """Keep human-code retries separate from password login; resume OAuth once verified."""
    while not callback.done():
        otp = await value.otp.get()
        if not _credentials_unchanged(value, version): raise ValueError('Credentials changed')
        verification['status'] = None
        value.stage = 'verification_input'
        value.phase, value.message = 'verifying_totp', 'Verifying your code with Kite…'
        await otp_field.fill('')
        # Kite's current verification form can submit from the sixth digit's
        # keyboard event. fill() only emits an input event; Enter alone does
        # not activate that form. Type the human code into the visible field.
        try:
            await otp_field.press_sequentially(otp, delay=80)
        except Exception as error:
            if not callback.done() and not navigation_in_progress(error): raise
        finally:
            otp = ''
        if not callback.done():
            value.stage = 'verification_submit'
            submit = page.get_by_role('button', name=re.compile(r'^(Continue|Verify|Login|Submit)$', re.I)).filter(visible=True).first
            try:
                if verification['status'] is None and await submit.is_visible() and await submit.is_enabled():
                    await submit.click()
            except Exception as error:
                if not callback.done() and not navigation_in_progress(error): raise
        deadline = time.monotonic() + 40
        value.stage = 'verification_callback'
        accepted_at = None
        resumed = False
        while not callback.done() and time.monotonic() < deadline:
            if verification['status'] is None:
                try:
                    errors = await page.locator('.error, .error-message, [role="alert"]').all_text_contents()
                except Exception as error:
                    if not callback.done() and not navigation_in_progress(error): raise
                    errors = []
                for text in errors:
                    if re.search(r'(invalid|incorrect|expired|wrong).*(code|otp|totp)|(code|otp|totp).*(invalid|incorrect|expired|wrong)', text, re.I):
                        verification['status'] = 'error'
                        break
            if verification['status'] == 'error':
                await otp_field.fill('')
                value.stage, value.phase = 'waiting_totp', 'awaiting_totp'
                value.message = 'Kite rejected or expired that verification code. Enter a fresh code; your saved password was accepted.'
                break
            if verification['status'] == 'success':
                if accepted_at is None:
                    accepted_at = time.monotonic()
                    value.message = 'Kite accepted your code. Completing the broker connection…'
                # Switching verification methods can lose the original Connect
                # continuation. Reopen it in the SAME authenticated browser.
                if not resumed and time.monotonic() - accepted_at >= 2:
                    resumed = True
                    try:
                        await page.goto(connect_url, wait_until='domcontentloaded')
                    except Exception:
                        # Our signed callback is intentionally aborted before
                        # leaving Kite; Playwright may report that as navigation failure.
                        if not callback.done(): raise
            await asyncio.sleep(.2)
        else:
            if callback.done(): return
            log.warning('Assisted verification timeout account=%s response=%s form_visible=%s',
                        value.account_id,
                        'accepted' if accepted_at else 'not_observed',
                        await otp_field.is_visible())
            form_visible = await otp_field.is_visible()
            raise LoginFailure('callback_not_received' if accepted_at or not form_visible else 'totp_unconfirmed',
                               'Kite left the verification form, but Sterling did not receive a signed broker callback. This does not indicate an incorrect code. Use manual Kite login while the redirect is checked.' if accepted_at or not form_visible else
                               'Kite did not confirm that code. Start a fresh login and enter the current verification code.')


async def preview(value: Attempt) -> bytes:
    """A masked, owner-requested viewport frame; never persisted on the server."""
    async with value.preview_lock:
        page = value.page
        allowed = {'submitting_password', 'awaiting_totp', 'verifying_totp'}
        if page is None or value.phase not in allowed:
            raise ValueError('Preview is not available at this login stage')
        def trusted():
            parsed = urlsplit(page.url)
            return parsed.scheme == 'https' and parsed.hostname == 'kite.zerodha.com'
        if not trusted():
            raise ValueError('Preview is not on Kite')
        frame = await page.screenshot(
            type='png', full_page=False, timeout=4000,
            mask=[page.locator('input, textarea, [contenteditable="true"]')],
            mask_color='#cbd5e1',
            style='input,textarea,[contenteditable="true"] { color: transparent !important; text-shadow: none !important; caret-color: transparent !important; }',
        )
        if value.page is not page or value.phase not in allowed or not trusted():
            raise ValueError('Preview changed while being captured')
        return frame
