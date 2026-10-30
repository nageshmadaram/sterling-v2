"""Recover a real SQLite schema into an isolated copy without touching the source."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.verify_sqlite_recovery import verify_snapshot


def _source(tmp_path):
    source = tmp_path / 'current' / 'state.db'
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        c.execute('INSERT INTO records VALUES(1, ?)', ('synthetic-state',))
        c.execute('PRAGMA user_version=3')
        c.execute('INSERT INTO records VALUES(2, ?)', ('uncheckpointed-state',))
    return source


def test_isolated_backup_restores_wal_commits_and_schema(tmp_path):
    source = _source(tmp_path)
    restored = tmp_path / 'isolated' / 'recovery.db'
    result = verify_snapshot(source, restored)
    assert result['result'] == 'PASS'
    assert result['tables_checked'] == 1
    with sqlite3.connect(restored) as c:
        assert c.execute('SELECT value FROM records ORDER BY id').fetchall() == [
            ('synthetic-state',), ('uncheckpointed-state',)
        ]
        c.execute("INSERT INTO records VALUES(3,'restore-only')")
    with sqlite3.connect(source) as c:
        assert c.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 2
        assert c.execute('PRAGMA user_version').fetchone()[0] == 3


def test_destination_must_be_new_and_isolated(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(ValueError, match='new isolated'):
        verify_snapshot(source, source)
    existing = tmp_path / 'isolated' / 'existing.db'
    existing.parent.mkdir()
    existing.write_bytes(b'never overwrite this')
    with pytest.raises(ValueError, match='new isolated'):
        verify_snapshot(source, existing)
    assert existing.read_bytes() == b'never overwrite this'


def test_mismatched_source_is_refused_without_creating_artifact(tmp_path):
    out = tmp_path / 'isolated' / 'copy.db'
    with pytest.raises(FileNotFoundError):
        verify_snapshot(tmp_path / 'absent.db', out)
    assert not out.exists()


def test_backup_failure_removes_partial_copy(tmp_path, monkeypatch):
    source = _source(tmp_path)
    out = tmp_path / 'isolated' / 'copy.db'
    from scripts import verify_sqlite_recovery as recovery

    def fail(_conn):
        raise OSError('synthetic backup interruption')

    # Simulate a read/parity exception after the staging file exists, without
    # modifying the original database or bypassing SQLite's backup API.
    monkeypatch.setattr(recovery, '_tables', fail)
    with pytest.raises(OSError, match='synthetic backup interruption'):
        verify_snapshot(source, out)
    assert not out.exists()
    with sqlite3.connect(source) as c:
        assert c.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 2
