"""EMA hot read paths must never reserve the SQLite writer lease."""
import pytest

from app.services import db
from app.services.ema_trend import repository as repo


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "ema_reads.sqlite"))
    monkeypatch.setattr(db, "_available", False)
    assert db.init()
    return monkeypatch


def test_list_and_snapshot_queries_do_not_enter_write_transaction(storage):
    repo.put("alice", "signal", "swing-1", {"mode": "swing", "status": "armed"}, 0)
    repo.put("alice", "signal", "scalp-1", {"mode": "scalping", "status": "armed"}, 0)
    repo.put("bob", "signal", "other-1", {"mode": "swing", "status": "armed"}, 0)
    repo.event("alice", "event-1", "campaign-1", "OBSERVED", {"mark": 123})

    def no_writer():
        raise AssertionError("A read-only query tried to reserve the write transaction")

    storage.setattr(repo, "transaction", no_writer)
    assert repo.get("alice", "signal", "swing-1")["mode"] == "swing"
    assert len(repo.list_documents("alice", "signal")) == 2
    assert [r["id"] for r in repo.list_signals_by_mode("alice", "swing")] == ["swing-1"]
    assert [r["event_id"] for r in repo.events("alice")] == ["event-1"]
    assert set(repo.owners()) == {"alice", "bob"}
    with pytest.raises(repo.NotFound):
        repo.get("alice", "signal", "other-1")


def test_schema_created_once_but_cas_writes_remain_strict(storage, monkeypatch):
    original_schema = repo._schema
    calls = []
    def counted(conn):
        calls.append(1)
        return original_schema(conn)
    monkeypatch.setattr(repo, "_schema", counted)
    # Each test has an isolated DB pathname. Schema must exist before the first
    # write, but no subsequent hot read or write recreates every index.
    initial = repo.put("alice", "signal", "id", {"mode": "intraday"}, 0)
    assert initial["revision"] == 1
    for _ in range(5):
        assert repo.list_signals_by_mode("alice", "intraday")[0]["id"] == "id"
    assert len(calls) == 1
    with pytest.raises(repo.Conflict):
        repo.put("alice", "signal", "id", {"mode": "intraday"}, 0)
    assert repo.put("alice", "signal", "id", {"mode": "intraday"}, 1)["revision"] == 2


def test_schema_is_recreated_after_database_replacement_at_same_path(storage):
    from pathlib import Path
    repo.put('alice', 'signal', 'before', {'mode': 'swing'}, 0)
    path = Path(db._DB_PATH)
    # All connections above have closed; simulate an isolated restore/replacement.
    path.unlink()
    for suffix in ('-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    assert db.init()
    assert repo.list_documents('alice', 'signal') == []
    assert repo.put('alice', 'signal', 'after', {'mode': 'swing'}, 0)['revision'] == 1
