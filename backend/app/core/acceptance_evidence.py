"""Append-only, content-addressed acceptance evidence for the 2026-10-26 spec.

The register records observations; it does not manufacture them. Software work,
local tests, CI, broker receipts and independent human observations have distinct
source types. A missing register is UNKNOWN and reading it never creates state.
SQLite transactions serialize writers; triggers prohibit accidental edits and
the hash chain detects altered rows. Off-host anchors/backups are still required
to detect deletion or replacement of the entire register.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

from app.core.state_paths import state_root

RESULTS = frozenset({"VERIFIED", "FAILED", "BLOCKED", "UNKNOWN"})
SOURCES = frozenset({"CODE_IMPLEMENTED", "LOCAL_TEST", "CI", "HOST", "BROKER", "OPERATOR", "RESEARCH"})
GATE_SOURCES = {
    "G0": {"LOCAL_TEST"}, "G1": {"CI"}, "G2": {"HOST"},
    "G3": {"BROKER"}, "G4": {"LOCAL_TEST", "BROKER"},
    "G5": {"HOST"}, "G6": {"OPERATOR"}, "G7": {"RESEARCH"},
    "G8": {"OPERATOR"}, "G9": {"BROKER"},
    "CONTROLLED_LIVE_AUTHORIZATION": {"OPERATOR"},
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_timestamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("evidence timestamps must include a timezone")
    return stamp.astimezone(timezone.utc)


@dataclass(frozen=True)
class AcceptanceClaim:
    claim_id: str
    source_sha: str
    runtime_release_sha: str
    observed_at: str
    source_type: str
    result: str
    test_command: str = ""
    log_hash: str = ""
    evidence_path: str = ""
    independent_reviewer: str = ""
    observer: str = ""
    dataset_id: str = ""
    deployment_id: str = ""
    account_id: str = ""
    strategy_id: str = ""
    mode: str = ""
    strategy_version: str = ""
    config_hash: str = ""
    expires_at: str = ""
    detail: str = ""

    def validate(self, *, evidence_bytes: bytes | None = None) -> None:
        if not re.fullmatch(r"(?:G[0-9]|FR-\d{2}|E\d{2}|[A-Z][A-Z0-9_.:-]{1,100})", self.claim_id):
            raise ValueError("invalid claim_id")
        for sha in (self.source_sha, self.runtime_release_sha):
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError("evidence requires exact lowercase 40-character source and runtime SHAs")
        if self.result not in RESULTS or self.source_type not in SOURCES:
            raise ValueError("unknown evidence result or source type")
        observed = utc_timestamp(self.observed_at)
        if observed > datetime.now(timezone.utc):
            raise ValueError("future evidence is not an observation")
        if self.expires_at and utc_timestamp(self.expires_at) <= observed:
            raise ValueError("evidence expiry must follow observation")
        if self.result == "VERIFIED":
            if self.source_type == "CODE_IMPLEMENTED":
                raise ValueError("CODE_IMPLEMENTED is not VERIFIED evidence")
            if self.claim_id in GATE_SOURCES and self.source_type not in GATE_SOURCES[self.claim_id]:
                raise ValueError("source type cannot verify this gate")
            if self.claim_id in GATE_SOURCES and self.claim_id not in {"G0", "G1"} and not self.expires_at:
                raise ValueError("host, broker, economic and operator gate evidence requires explicit expiry")
            if not self.observer.strip() or not self.independent_reviewer.strip() or self.observer.strip().casefold() == self.independent_reviewer.strip().casefold():
                raise ValueError("VERIFIED requires an observer and a distinct independent reviewer")
            if not re.fullmatch(r"[0-9a-f]{64}", self.log_hash):
                raise ValueError("VERIFIED requires a SHA-256 log_hash")
            if not self.evidence_path or not Path(self.evidence_path).is_absolute():
                raise ValueError("VERIFIED requires an absolute local evidence path")
            actual_hash = hashlib.sha256(evidence_bytes).hexdigest() if evidence_bytes is not None else sha256_file(Path(self.evidence_path))
            if actual_hash != self.log_hash:
                raise ValueError("evidence bytes do not match log_hash")
            if self.claim_id == "G7" and not all((self.dataset_id, self.strategy_id, self.mode, self.strategy_version, self.config_hash)):
                raise ValueError("economic approval requires dataset, strategy, mode, version and config identity")
            if self.claim_id == "G7":
                from app.core.economic_acceptance import validate_economic_report
                document = evidence_bytes if evidence_bytes is not None else Path(self.evidence_path).read_bytes()
                validate_economic_report(json.loads(document), asdict(self))


class AcceptanceRegister:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else state_root() / "data/manifests/acceptance.sqlite3"

    def append(self, claim: AcceptanceClaim) -> str:
        claim.validate()
        evidence = None
        if claim.result == "VERIFIED":
            if Path(claim.evidence_path).stat().st_size > 32 * 1024 * 1024:
                raise ValueError("acceptance receipt exceeds the 32 MiB register limit; reference a sealed manifest")
            evidence = Path(claim.evidence_path).read_bytes()
            claim.validate(evidence_bytes=evidence)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError:
            pass
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS claims (
                    seq INTEGER PRIMARY KEY, payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS artifacts (sha256 TEXT PRIMARY KEY, content BLOB NOT NULL);
                CREATE TRIGGER IF NOT EXISTS artifacts_no_update BEFORE UPDATE ON artifacts
                    BEGIN SELECT RAISE(ABORT, 'acceptance artifacts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS artifacts_no_delete BEFORE DELETE ON artifacts
                    BEGIN SELECT RAISE(ABORT, 'acceptance artifacts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS claims_no_update BEFORE UPDATE ON claims
                    BEGIN SELECT RAISE(ABORT, 'acceptance evidence is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS claims_no_delete BEFORE DELETE ON claims
                    BEGIN SELECT RAISE(ABORT, 'acceptance evidence is append-only'); END;
            """)
            conn.execute("BEGIN IMMEDIATE")
            # Do not extend a corrupt register and give it a fresh-looking tail.
            self._read(conn)
            if evidence is not None:
                conn.execute("INSERT OR IGNORE INTO artifacts(sha256, content) VALUES (?, ?)", (claim.log_hash, evidence))
                stored = conn.execute("SELECT content FROM artifacts WHERE sha256=?", (claim.log_hash,)).fetchone()
                if stored is None or hashlib.sha256(stored[0]).hexdigest() != claim.log_hash:
                    raise ValueError("acceptance artifact storage is corrupt")
            last = conn.execute("SELECT event_hash FROM claims ORDER BY seq DESC LIMIT 1").fetchone()
            previous = last[0] if last else "0" * 64
            payload = canonical_bytes(asdict(claim)).decode()
            event_hash = hashlib.sha256(previous.encode() + payload.encode()).hexdigest()
            conn.execute("INSERT INTO claims(payload, previous_hash, event_hash) VALUES (?, ?, ?)",
                         (payload, previous, event_hash))
        return event_hash

    @staticmethod
    def _read(conn: sqlite3.Connection) -> list[AcceptanceClaim]:
        previous = "0" * 64
        claims = []
        for seq, payload, prev, event_hash in conn.execute("SELECT seq, payload, previous_hash, event_hash FROM claims ORDER BY seq"):
            expected = hashlib.sha256(previous.encode() + payload.encode()).hexdigest()
            if seq != len(claims) + 1 or prev != previous or event_hash != expected:
                raise ValueError("acceptance evidence hash chain is corrupt")
            claims.append(AcceptanceClaim(**json.loads(payload)))
            previous = event_hash
        return claims

    def read(self) -> list[AcceptanceClaim]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            return self._read(conn)

    def status(self, claim: AcceptanceClaim, *, now: datetime | None = None) -> str:
        """Verify sealed bytes inside the backup-portable register, not a live path."""
        if claim.result != "VERIFIED":
            return gate_status(claim, now=now)
        try:
            with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                row = conn.execute("SELECT content FROM artifacts WHERE sha256=?", (claim.log_hash,)).fetchone()
            if row is None:
                return "UNKNOWN"
            return gate_status(claim, now=now, evidence_bytes=row[0])
        except (OSError, sqlite3.Error):
            return "UNKNOWN"

    def artifact(self, claim: AcceptanceClaim) -> bytes:
        """Return verified sealed bytes; callers still enforce expiry and scope."""
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT content FROM artifacts WHERE sha256=?", (claim.log_hash,)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != claim.log_hash:
            raise ValueError("sealed acceptance artifact is missing or corrupt")
        return row[0]


def gate_status(claim: AcceptanceClaim | None, *, now: datetime | None = None, evidence_bytes: bytes | None = None) -> str:
    if claim is None:
        return "UNKNOWN"
    try:
        claim.validate(evidence_bytes=evidence_bytes)
        if claim.expires_at and utc_timestamp(claim.expires_at) <= (now or datetime.now(timezone.utc)):
            return "UNKNOWN"
    except (ValueError, OSError, TypeError, KeyError):
        return "UNKNOWN"
    return claim.result
