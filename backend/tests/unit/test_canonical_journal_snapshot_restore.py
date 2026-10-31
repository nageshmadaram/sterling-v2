"""Real SQLite canonical journal restore/restart drill, entirely offline.

Uses production migrations and durable order_journal transactions, then runs
the operator's copy-only recovery utility as a separate Python process. No
broker connection, simulated sign-off or production database is touched.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import db
from app.services.kite_engine import order_journal


@pytest.fixture
def source(tmp_path, monkeypatch):
    db_path = tmp_path / 'active' / 'sterling.sqlite'
    db_path.parent.mkdir()
    monkeypatch.setattr(db, '_DB_PATH', str(db_path))
    assert db.init()
    return db_path


def _request(signal='same-signal'):
    return dict(
        uid='test-uid', account_id='SYNTHETIC-ACCOUNT',
        strategy_id='synthetic-offline', generation_id='gen-1',
        signal_id=signal, exchange='NFO', symbol='SYNTHETIC26NOV25000CE',
        side='BUY', quantity=1, capital_required=0,
        payload={'product': 'NRML', 'mode': 'OFFLINE_TEST'},
    )


def _restore_copy(source: Path, tmp_path: Path, monkeypatch) -> Path:
    output = tmp_path / 'isolated-restore' / 'sterling.sqlite'
    tool = Path(__file__).resolve().parents[3] / 'scripts' / 'verify_sqlite_recovery.py'
    result = subprocess.run(
        [sys.executable, str(tool), '--source', str(source), '--destination', str(output)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    evidence = json.loads(result.stdout)
    assert evidence['result'] == 'PASS'
    assert evidence['production_recovery'] == 'NOT_CERTIFIED'
    monkeypatch.setattr(db, '_DB_PATH', str(output))
    return output


def test_confirmed_fill_and_projection_survive_isolated_recovery(source, tmp_path, monkeypatch):
    req = _request()
    original = order_journal.reserve(**req)
    assert original.state == 'RESERVED'
    assert order_journal.claim_submission(original.intent_key)
    order_journal.acknowledge(original.intent_key, 'PAPER-ACK-1')
    partial = order_journal.observe_order(
        original.intent_key, status='OPEN', order_id='PAPER-ACK-1',
        filled_quantity=0, average_price=0,
    )
    assert partial.accepted
    completed = order_journal.observe_order(
        original.intent_key, status='COMPLETE', order_id='PAPER-ACK-1',
        filled_quantity=1, average_price=120,
    )
    assert completed.accepted and completed.intent.state == 'FILLED'
    assert completed.intent.projection_pending

    _restore_copy(source, tmp_path, monkeypatch)
    recovered = order_journal.get(original.intent_key)
    assert recovered is not None
    assert (recovered.state, recovered.order_id, recovered.filled_quantity) == (
        'FILLED', 'PAPER-ACK-1', 1,
    )
    assert recovered.projection_pending

    # Configuration generation changes must never duplicate the same signal.
    replay = order_journal.reserve(**{**req, 'generation_id': 'gen-after-restart'})
    assert replay.intent_key == original.intent_key and replay.state == 'FILLED'
    assert not order_journal.claim_submission(original.intent_key)
    duplicate = order_journal.observe_order(
        original.intent_key, status='COMPLETE', order_id='PAPER-ACK-1',
        filled_quantity=1, average_price=120,
    )
    assert not duplicate.accepted and duplicate.reason == 'duplicate'
    assert order_journal.mark_projected(recovered.intent_key, recovered.projection_version)
    assert not order_journal.get(recovered.intent_key).projection_pending


def test_unknown_send_remains_unclaimable_after_restore(source, tmp_path, monkeypatch):
    req = _request('timeout-after-send')
    intent = order_journal.reserve(**req)
    assert order_journal.claim_submission(intent.intent_key)
    uncertain = order_journal.submission_uncertain(intent.intent_key, 'synthetic read timeout')
    assert uncertain.state == 'UNKNOWN'
    _restore_copy(source, tmp_path, monkeypatch)

    recovered = order_journal.get(intent.intent_key)
    assert recovered.state == 'UNKNOWN'
    assert recovered.order_id == ''
    assert any(row.intent_key == intent.intent_key for row in order_journal.unresolved('test-uid'))
    replay = order_journal.reserve(**req)
    assert replay.intent_key == intent.intent_key and replay.state == 'UNKNOWN'
    assert not order_journal.claim_submission(intent.intent_key)
    # Only external broker truth may bind this UNKNOWN to an acknowledged order.
    after_evidence = order_journal.acknowledge(intent.intent_key, 'PAPER-TRUTH-2')
    assert after_evidence.state == 'SUBMITTED'
    assert after_evidence.order_id == 'PAPER-TRUTH-2'
    assert not order_journal.claim_submission(intent.intent_key)
