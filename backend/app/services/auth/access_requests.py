"""Durable access inbox; approvals and feature grants commit together."""
import sqlite3
import uuid
from app.services import db
from app.services.auth import account_policy as policy
from app.services.auth.user_store import _now_ms


def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS feature_access_requests (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
        feature TEXT NOT NULL CHECK(feature IN ('kite','tv','radio')),
        status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
        created_at_ms INTEGER NOT NULL, decided_at_ms INTEGER, decided_by TEXT)''')
    c.execute("INSERT OR IGNORE INTO account_schema_versions VALUES(3,'workspace access request inbox')")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_pending_feature_request ON feature_access_requests(user_id,feature) WHERE status='pending'")


def _admin(c, actor):
    if not c.execute("SELECT 1 FROM users u JOIN platform_admin a ON a.user_id=u.id WHERE u.id=? AND u.is_active=1 AND u.role='admin'", (actor,)).fetchone():
        raise PermissionError('Designated administrator access required.')


def list_requests(actor, *, administrator=False):
    policy._available()
    try:
        with db._conn() as c:
            c.execute('BEGIN')
            if administrator: _admin(c, actor)
            rows = c.execute('''SELECT r.*,u.username FROM feature_access_requests r JOIN users u ON u.id=r.user_id ''' +
                ('' if administrator else 'WHERE r.user_id=? ') + 'ORDER BY CASE r.status WHEN \'pending\' THEN 0 ELSE 1 END,r.created_at_ms DESC LIMIT 200',
                () if administrator else (actor,)).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as exc:
        raise policy.PolicyUnavailable('Access request inbox unavailable.') from exc


def request_access(user_id, feature):
    if feature not in policy.FEATURES: raise ValueError('Unknown workspace.')
    policy._available()
    try:
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM users WHERE id=? AND is_active=1', (user_id,)).fetchone():
                raise PermissionError('An active Sterling account is required.')
            if c.execute('SELECT 1 FROM user_feature_grants WHERE user_id=? AND feature=? AND enabled=1', (user_id,feature)).fetchone():
                return {'feature': feature, 'status': 'granted'}
            prior=c.execute("SELECT * FROM feature_access_requests WHERE user_id=? AND feature=? AND status='pending'", (user_id,feature)).fetchone()
            if prior: return dict(prior)
            # A declined request remains visible instead of auto-resubmitting on every visit.
            prior=c.execute('SELECT * FROM feature_access_requests WHERE user_id=? AND feature=? ORDER BY created_at_ms DESC LIMIT 1', (user_id,feature)).fetchone()
            if prior and prior['status']=='rejected': return dict(prior)
            now=_now_ms();request_id=uuid.uuid4().hex
            c.execute("INSERT INTO feature_access_requests VALUES(?,?,?,'pending',?,NULL,NULL)", (request_id,user_id,feature,now))
            policy.audit(c,user_id,user_id,'access.requested',{}, {'request_id':request_id,'feature':feature},now)
            return dict(c.execute('SELECT * FROM feature_access_requests WHERE id=?',(request_id,)).fetchone())
    except sqlite3.Error as exc:
        raise policy.PolicyUnavailable('Access request could not be saved.') from exc


def decide(actor, request_id, decision):
    if decision not in ('approved','rejected'): raise ValueError('Choose approve or reject.')
    policy._available()
    try:
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE');_admin(c,actor)
            row=c.execute('SELECT * FROM feature_access_requests WHERE id=?',(request_id,)).fetchone()
            if not row: raise ValueError('Request not found.')
            if row['status']!='pending': raise policy.PolicyConflict('This request has already been reviewed.')
            if not c.execute('SELECT 1 FROM users WHERE id=? AND is_active=1',(row['user_id'],)).fetchone():
                raise ValueError('Restore this user before reviewing access.')
            now=_now_ms()
            if decision=='approved':
                target=row['user_id'];feature=row['feature']
                prior=c.execute('SELECT revision FROM account_policy_revisions WHERE user_id=?',(target,)).fetchone()
                revision=(int(prior['revision']) if prior else 0)+1
                c.execute('''INSERT INTO user_feature_grants VALUES(?,?,1,?,?) ON CONFLICT(user_id,feature)
                    DO UPDATE SET enabled=1,actor_id=excluded.actor_id,updated_at_ms=excluded.updated_at_ms''',(target,feature,actor,now))
                c.execute('INSERT INTO account_policy_revisions VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision',(target,revision))
                event=policy.audit(c,actor,target,'access.approved',{'request_id':request_id},{'feature':feature},now)
                c.execute('INSERT INTO auth_event_outbox(event_id,user_id,revision) VALUES(?,?,?)',(event,target,revision))
            else: policy.audit(c,actor,row['user_id'],'access.rejected',{'request_id':request_id},{'feature':row['feature']},now)
            c.execute('UPDATE feature_access_requests SET status=?,decided_at_ms=?,decided_by=? WHERE id=?',(decision,now,actor,request_id))
            return dict(c.execute('SELECT * FROM feature_access_requests WHERE id=?',(request_id,)).fetchone())
    except sqlite3.Error as exc:
        raise policy.PolicyUnavailable('Review could not be saved; access unchanged.') from exc
