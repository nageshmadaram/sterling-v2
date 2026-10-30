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


def test_backup_publishes_private_standalone_copy_after_content_verification(tmp_path):
    import stat
    source = _source(tmp_path)
    target = tmp_path / "verified" / "snapshot.db"
    report = verify_snapshot(source, target)
    assert report["schema_verified"] is True
    assert report["content_verified"] is True
    assert report["source_modified"] is False
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(target.parent.glob(".sterling-recovery-*"))
    assert not (target.parent / "snapshot.db-wal").exists()
    assert not (target.parent / "snapshot.db-shm").exists()
    with sqlite3.connect(target) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2


def test_same_row_count_corruption_in_staging_is_refused(tmp_path, monkeypatch):
    from scripts import verify_sqlite_recovery as recovery

    source = _source(tmp_path)
    target = tmp_path / "verified" / "corrupt.db"
    original_content = source.read_bytes()
    actual = recovery._table_fingerprints
    calls = 0

    def mutate_staged_copy_only(conn, tables):
        nonlocal calls
        calls += 1
        if calls == 2:
            # The source is opened query-only. The second digest belongs to
            # the staged, uncommitted-to-users copy; keep row counts unchanged.
            conn.execute("UPDATE records SET value='tampered' WHERE id=2")
            conn.commit()
        return actual(conn, tables)

    monkeypatch.setattr(recovery, "_table_fingerprints", mutate_staged_copy_only)
    with pytest.raises(RuntimeError, match="table content differs"):
        verify_snapshot(source, target)
    assert calls == 2
    assert not target.exists()
    assert not list(target.parent.glob(".sterling-recovery-*"))
    assert source.read_bytes() == original_content
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT value FROM records WHERE id=2").fetchone()[0] == "uncheckpointed-state"


def test_read_only_backup_verifies_blob_and_without_rowid_content(tmp_path):
    source = _source(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE binary_state (id TEXT PRIMARY KEY, raw BLOB) WITHOUT ROWID")
        conn.execute(
            "INSERT INTO binary_state(id,raw) VALUES (?,?)",
            ("proof", sqlite3.Binary(b"\\x00\\xfe\\xffbinary")),
        )
    target = tmp_path / "verified" / "blob.db"
    report = verify_snapshot(source, target)
    assert report["content_verified"] is True
    assert report["tables_checked"] == 2
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT raw FROM binary_state WHERE id='proof'").fetchone()[0] == b"\\x00\\xfe\\xffbinary"


def test_symlink_source_is_refused_without_a_published_copy(tmp_path):
    source = _source(tmp_path)
    link = tmp_path / "linked-source.sqlite"
    link.symlink_to(source)
    target = tmp_path / "verified" / "not-created.db"
    with pytest.raises(ValueError, match="non-symlink"):
        verify_snapshot(link, target)
    assert not target.exists()


def test_concurrent_destination_creation_is_never_overwritten(tmp_path, monkeypatch):
    from scripts import verify_sqlite_recovery as recovery

    source = _source(tmp_path)
    target = tmp_path / "verified" / "concurrent.db"
    original_link = recovery.os.link

    def collision(_stage, dest):
        # Simulates a different writer racing with the publication step.
        Path(dest).write_bytes(b"OTHER-WRITER")
        raise FileExistsError("destination was created concurrently")

    monkeypatch.setattr(recovery.os, "link", collision)
    try:
        with pytest.raises(FileExistsError, match="concurrently"):
            verify_snapshot(source, target)
    finally:
        monkeypatch.setattr(recovery.os, "link", original_link)
    assert target.read_bytes() == b"OTHER-WRITER"
    assert not list(target.parent.glob(".sterling-recovery-*"))
