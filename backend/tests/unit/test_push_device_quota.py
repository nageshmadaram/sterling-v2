"""Device quotas count the receiving owner and must hold under concurrent signups."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event, Lock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import push
from app.core.auth import UserContext, get_current_user
from app.services import db
from tests.unit.test_experience_features import client, subscription


def device(base, name):
    return {**base, 'endpoint': 'https://fcm.googleapis.com/test/' + name}


def seed(c, base, count):
    ids = []
    for index in range(count):
        reply = c.post('/push/subscribe', json={'subscription': device(base, f'alice-{index}')})
        assert reply.status_code == 200
        ids.append(reply.json()['id'])
    return ids


def foreign_device(c, app, base):
    app.dependency_overrides[get_current_user] = lambda: UserContext('bob')
    body = {'subscription': device(base, 'shared-phone'), 'label': 'Bob phone'}
    reply = c.post('/push/subscribe', json=body)
    assert reply.status_code == 200
    identifier = reply.json()['id']
    with db._conn() as conn:
        conn.execute('INSERT INTO push_deliveries VALUES(?,?,?)', (identifier, 'bob-event', 1))
    app.dependency_overrides[get_current_user] = lambda: UserContext('alice')
    return body, identifier


@pytest.mark.parametrize('view', ['live', 'simulation'])
def test_full_owner_can_refresh_its_own_registered_device(client, view):
    c, app = client
    base = subscription()
    ids = seed(c, base, 16)
    if view == 'simulation':
        app.dependency_overrides[get_current_user] = lambda: UserContext('sim:alice', view='simulation', owner_uid='alice')
    response = c.post('/push/subscribe', json={'subscription': device(base, 'alice-0'), 'label': 'Updated phone'})
    assert response.status_code == 200 and response.json()['id'] == ids[0]
    with db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM push_devices WHERE uid=?', ('alice',)).fetchone()[0] == 16
        assert conn.execute('SELECT label FROM push_devices WHERE id=?', (ids[0],)).fetchone()[0] == 'Updated phone'


@pytest.mark.parametrize('view', ['live', 'simulation'])
def test_foreign_registration_cannot_bypass_full_owner_quota(client, view):
    c, app = client
    base = subscription()
    seed(c, base, 16)
    body, identifier = foreign_device(c, app, base)
    if view == 'simulation':
        app.dependency_overrides[get_current_user] = lambda: UserContext('sim:alice', view='simulation', owner_uid='alice')
    response = c.post('/push/subscribe', json=body)
    assert response.status_code == 409
    with db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM push_devices WHERE uid=?', ('alice',)).fetchone()[0] == 16
        row = conn.execute('SELECT uid,label FROM push_devices WHERE id=?', (identifier,)).fetchone()
        assert tuple(row) == ('bob', 'Bob phone')
        assert conn.execute('SELECT event_key FROM push_deliveries WHERE device_id=?', (identifier,)).fetchone()[0] == 'bob-event'


def test_foreign_transfer_with_room_is_allowed_and_clears_old_delivery_dedupe(client):
    c, app = client
    base = subscription()
    seed(c, base, 15)
    body, identifier = foreign_device(c, app, base)
    response = c.post('/push/subscribe', json=body)
    assert response.status_code == 200 and response.json()['id'] == identifier
    with db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM push_devices WHERE uid=?', ('alice',)).fetchone()[0] == 16
        assert conn.execute('SELECT uid FROM push_devices WHERE id=?', (identifier,)).fetchone()[0] == 'alice'
        assert conn.execute('SELECT count(*) FROM push_deliveries WHERE device_id=?', (identifier,)).fetchone()[0] == 0


def test_brand_new_device_is_refused_at_the_same_limit(client):
    c, _ = client
    base = subscription()
    seed(c, base, 16)
    assert c.post('/push/subscribe', json={'subscription': device(base, 'new-phone')}).status_code == 409


def test_two_concurrent_registrations_cannot_both_take_the_last_slot(client, monkeypatch):
    c, _ = client
    base = subscription()
    seed(c, base, 15)
    # Force the old read/count/write path to expose its race. A serialized writer
    # legitimately waits here until the bounded pause expires, then the other
    # registration reads the committed count. The assertion is public behavior.
    original = db._conn
    lock = Lock()
    second_count_read = Event()
    counts = 0
    first_insert = True

    class CountResult:
        def __init__(self, cursor):
            self.cursor = cursor
        def fetchone(self):
            nonlocal counts
            row = self.cursor.fetchone()
            with lock:
                counts += 1
                if counts >= 2:
                    second_count_read.set()
            return row

    class Connection:
        def __init__(self, connection):
            self.connection = connection
        def execute(self, sql, parameters=()):
            nonlocal first_insert
            if sql.startswith('SELECT count(*) FROM push_devices WHERE uid='):
                return CountResult(self.connection.execute(sql, parameters))
            if sql.startswith('INSERT INTO push_devices('):
                with lock:
                    pause = first_insert
                    first_insert = False
                if pause:
                    second_count_read.wait(timeout=2)
            return self.connection.execute(sql, parameters)

    @contextmanager
    def connections():
        with original() as conn:
            yield Connection(conn)
    monkeypatch.setattr(db, '_conn', connections)
    start = Barrier(2)
    def register(name):
        start.wait(timeout=5)
        try:
            push.subscribe(push.Subscription(subscription=device(base, name)), UserContext('alice'))
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, ['simultaneous-a', 'simultaneous-b']))
    assert sorted(results) == [200, 409]
    with original() as conn:
        assert conn.execute('SELECT count(*) FROM push_devices WHERE uid=?', ('alice',)).fetchone()[0] == 16


@pytest.mark.asyncio
async def test_removed_subscription_does_not_inherit_previous_owners_delivery_dedupe(client, monkeypatch):
    import pywebpush
    from app.services.notifications import web_push
    c, app = client
    body = {'subscription': subscription()}
    identifier = c.post('/push/subscribe', json=body).json()['id']
    with db._conn() as conn:
        conn.execute('INSERT INTO push_deliveries VALUES(?,?,?)', (identifier, 'same-market-event', 1))
    assert c.delete('/push/devices/'+identifier).status_code == 200
    app.dependency_overrides[get_current_user] = lambda: UserContext('bob')
    assert c.post('/push/subscribe', json=body).status_code == 200
    assert c.put('/push/config', json={**web_push.DEFAULTS, 'enabled': True}).status_code == 200
    sent = []
    monkeypatch.setattr(pywebpush, 'webpush', lambda **kwargs: sent.append(kwargs))
    assert await web_push.send('bob', 'same-market-event', 'T', 'B', 'signals') == 1
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_other_owner_removal_cannot_erase_delivery_dedupe_or_replay_an_alert(client, monkeypatch):
    import pywebpush
    from app.services.notifications import web_push
    c, app = client
    identifier = c.post('/push/subscribe', json={'subscription': subscription()}).json()['id']
    with db._conn() as conn:
        conn.execute('INSERT INTO push_deliveries VALUES(?,?,?)', (identifier, 'alice-only-event', 1))
    app.dependency_overrides[get_current_user] = lambda: UserContext('bob')
    assert c.delete('/push/devices/'+identifier).status_code == 200
    app.dependency_overrides[get_current_user] = lambda: UserContext('alice')
    assert c.put('/push/config', json={**web_push.DEFAULTS, 'enabled': True}).status_code == 200
    sent = []
    monkeypatch.setattr(pywebpush, 'webpush', lambda **kwargs: sent.append(kwargs))
    assert await web_push.send('alice', 'alice-only-event', 'T', 'B', 'signals') == 0
    assert sent == []
    with db._conn() as conn:
        assert conn.execute('SELECT uid FROM push_devices WHERE id=?', (identifier,)).fetchone()[0] == 'alice'
        assert conn.execute('SELECT count(*) FROM push_deliveries WHERE device_id=?', (identifier,)).fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('registration', ['transfer', 'refresh'])
@pytest.mark.parametrize('outcome', ['accepted', 'gone', 'network'])
async def test_inflight_old_delivery_cannot_corrupt_a_new_registration(client, monkeypatch, registration, outcome):
    import pywebpush
    from types import SimpleNamespace
    from app.services.notifications import web_push
    c, app = client
    body = {'subscription': subscription(), 'label': 'Original registration'}
    identifier = c.post('/push/subscribe', json=body).json()['id']
    assert c.put('/push/config', json={**web_push.DEFAULTS, 'enabled': True}).status_code == 200
    target = 'bob' if registration == 'transfer' else 'alice'
    sent = []
    def transport(**kwargs):
        sent.append(kwargs)
        if len(sent) != 1:
            return
        # The response to Alice's old delivery arrives after the browser has
        # refreshed its registration or explicitly registered under Bob.
        app.dependency_overrides[get_current_user] = lambda: UserContext(target)
        assert c.post('/push/subscribe', json={**body, 'label': 'New registration'}).status_code == 200
        assert c.put('/push/config', json={**web_push.DEFAULTS, 'enabled': True}).status_code == 200
        if outcome == 'gone':
            raise pywebpush.WebPushException('old endpoint gone', response=SimpleNamespace(status_code=410))
        if outcome == 'network':
            raise RuntimeError('old delivery failed')
    monkeypatch.setattr(pywebpush, 'webpush', transport)
    first = await web_push.send('alice', 'old-event', 'T', 'B', 'signals')
    assert first == int(outcome == 'accepted')
    with db._conn() as conn:
        row = conn.execute('SELECT uid,label,enabled,last_error FROM push_devices WHERE id=?', (identifier,)).fetchone()
        assert tuple(row) == (target, 'New registration', 1, '')
    if registration == 'transfer':
        # Bob has not received Alice's event, so Alice's completion must not
        # insert a device-only record that deduplicates Bob's own event.
        assert await web_push.send('bob', 'old-event', 'T', 'B', 'signals') == 1
    elif outcome == 'accepted':
        # Refreshing one's own registration does not replay an accepted event.
        assert await web_push.send('alice', 'old-event', 'T', 'B', 'signals') == 0
    else:
        assert await web_push.send('alice', 'next-event', 'T', 'B', 'signals') == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['gone', 'network'])
async def test_current_registration_delivery_errors_still_apply(client, monkeypatch, outcome):
    import pywebpush
    from types import SimpleNamespace
    from app.services.notifications import web_push
    c, _ = client
    identifier = c.post('/push/subscribe', json={'subscription': subscription()}).json()['id']
    c.put('/push/config', json={**web_push.DEFAULTS, 'enabled': True})
    def transport(**_kwargs):
        if outcome == 'gone':
            raise pywebpush.WebPushException('gone', response=SimpleNamespace(status_code=410))
        raise RuntimeError('network')
    monkeypatch.setattr(pywebpush, 'webpush', transport)
    assert await web_push.send('alice', 'event', 'T', 'B', 'signals') == 0
    with db._conn() as conn:
        row = conn.execute('SELECT enabled,last_error FROM push_devices WHERE id=?', (identifier,)).fetchone()
        assert row['enabled'] == int(outcome != 'gone')
        assert row['last_error']


@pytest.mark.asyncio
async def test_late_server_error_cannot_reenable_an_already_disabled_registration(client, monkeypatch):
    import pywebpush
    from types import SimpleNamespace
    from app.services.notifications import web_push
    c, _ = client
    identifier = c.post('/push/subscribe', json={'subscription': subscription()}).json()['id']
    c.put('/push/config', json={**web_push.DEFAULTS, 'enabled': True})
    def transport(**_kwargs):
        with db._conn() as conn:
            conn.execute('UPDATE push_devices SET enabled=0 WHERE id=?', (identifier,))
        raise pywebpush.WebPushException('server unavailable', response=SimpleNamespace(status_code=500))
    monkeypatch.setattr(pywebpush, 'webpush', transport)
    assert await web_push.send('alice', 'event', 'T', 'B', 'signals') == 0
    with db._conn() as conn:
        assert conn.execute('SELECT enabled FROM push_devices WHERE id=?', (identifier,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_inflight_accepted_delivery_cannot_recreate_dedupe_for_a_removed_device(client, monkeypatch):
    import pywebpush
    from app.services.notifications import web_push
    c, _ = client
    identifier = c.post('/push/subscribe', json={'subscription': subscription()}).json()['id']
    c.put('/push/config', json={**web_push.DEFAULTS, 'enabled': True})
    def transport(**_kwargs):
        assert c.delete('/push/devices/'+identifier).status_code == 200
    monkeypatch.setattr(pywebpush, 'webpush', transport)
    assert await web_push.send('alice', 'event', 'T', 'B', 'signals') == 1
    with db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM push_devices WHERE id=?', (identifier,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM push_deliveries WHERE device_id=?', (identifier,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_old_acceptance_preserves_refreshed_registration_metadata_but_dedupes_same_owner(client, monkeypatch):
    import pywebpush
    from app.services.notifications import web_push
    c, _ = client
    body = {'subscription': subscription()}
    identifier = c.post('/push/subscribe', json=body).json()['id']
    c.put('/push/config', json={**web_push.DEFAULTS, 'enabled': True})
    def transport(**_kwargs):
        assert c.post('/push/subscribe', json=body).status_code == 200
        with db._conn() as conn:
            conn.execute('UPDATE push_devices SET last_delivery_ms=7,last_error=? WHERE id=?', ('new-registration-status', identifier))
    monkeypatch.setattr(pywebpush, 'webpush', transport)
    assert await web_push.send('alice', 'event', 'T', 'B', 'signals') == 1
    with db._conn() as conn:
        row = conn.execute('SELECT last_delivery_ms,last_error FROM push_devices WHERE id=?', (identifier,)).fetchone()
        assert tuple(row) == (7, 'new-registration-status')
    assert await web_push.send('alice', 'event', 'T', 'B', 'signals') == 0
