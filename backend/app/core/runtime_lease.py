"""One execution process per durable trading database, before workers start."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


@contextmanager
def engine_lease(database_path: str):
    path = Path(database_path).expanduser().resolve()
    lock_path = path.with_name(path.name + '.engine.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Never unlink the lock: a replacement inode would permit two owners.
    with lock_path.open('a+') as handle:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another Sterling execution process owns this database; startup refused.') from None
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
