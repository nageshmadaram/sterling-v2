# Retired-worktree database recovery

The three obsolete checkouts were clean, at `1c604f95898209ae0e0b54b9cae8d10c53891670`, and ancestors of `origin/main` before removal. No process had its working directory there. Four SQLite online backups passed `PRAGMA integrity_check`, authenticated decryption, SHA-256 comparison and a second restored-database integrity check before the directories were removed. Relevant recovery configuration was preserved separately when present.

The destination `/mnt/OS` does not enforce private Unix permissions. Backups there are therefore AES-256-GCM encrypted; plaintext snapshots existed only in a private RAM directory and were removed. Filenames and sizes are metadata, not credentials.

- Private manifest: `/home/nageshmadaram/Sterling/.runtime/qa/2026-10-23/worktree-retirement-backups.json`.
- Encrypted backups: `/mnt/OS/SterlingReadinessBackups/worktree-dbs/<worktree>/20261023T122151Z/`.
- Recovery key: the `private_recovery_key` path in the manifest, under `~/.local/share/sterling/backup-keys/`, mode `0600`. Keep this key and manifest; encrypted files cannot replace a lost key.

Restore into a **new private local directory**, never over the live database. The following decrypts one manifest entry, validates its digest and checks the SQLite file read-only. It refuses an existing output file and does not load it into Sterling:

```python
import hashlib, json, os, sqlite3
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

manifest = json.loads(Path('/home/nageshmadaram/Sterling/.runtime/qa/2026-10-23/worktree-retirement-backups.json').read_text())
entry = manifest['worktrees'][0]['databases'][0]  # select the intended worktree/database
key_path = Path(manifest['private_recovery_key'])
assert key_path.stat().st_mode & 0o077 == 0
blob = Path(entry['backup']).read_bytes()
assert hashlib.sha256(blob).hexdigest() == entry['ciphertext_sha256']
header = b'STERLING-BACKUP-v1\0'
assert blob.startswith(header)
nonce = blob[len(header):len(header)+12]
plain = AESGCM(key_path.read_bytes()).decrypt(nonce, blob[len(header)+12:], entry['associated_path'].encode())
assert hashlib.sha256(plain).hexdigest() == entry['sha256']
folder = Path.home() / 'SterlingRecoveredDatabase'
folder.mkdir(mode=0o700, exist_ok=False)
assert folder.stat().st_mode & 0o077 == 0
output = folder / 'recovered.sqlite'
fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'wb') as stream:
    stream.write(plain)
with sqlite3.connect(output.as_uri() + '?mode=ro', uri=True) as connection:
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
print(output)
```

Recovery-config entries use the same authenticated envelope and associated path; do not interpret them as SQLite. Preserve their original secret-handling restrictions. No production restore was performed for QA. The active implementation and temporary verification worktrees are retained until merge/verification finish; their later removal is separate from these three verified retirements.
