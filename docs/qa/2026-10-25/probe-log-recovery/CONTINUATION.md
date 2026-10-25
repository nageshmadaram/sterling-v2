# Damaged session evidence and durable probe writes

Baseline: `b02ca6e628e159597626f55ecca0d1d0e15191b1`.

## Reproduction

Following the 25 October 17:29 IST system restart, the shared session-probe JSONL contained one damaged record at line 4809: 375 bytes including 201 NUL bytes. Its origin is not proven. The session-audit CLI parsed all JSON lines in a comprehension and raised JSONDecodeError before producing a report. Private continuation helpers using the same pattern also stopped. Healthy later samples did not make the damaged historical record valid.

A byte-preserving gzip snapshot, checksum and line metadata were captured before changing either source or evidence. Running the original CLI against that frozen real input exits 1 with JSONDecodeError. The fixed CLI exits 0 with a generated report that explicitly retains malformed evidence and market-load acceptance false. Exit 0 means the report was generated, not that acceptance passed.

## Exact fix

- `scripts/trading_session_audit.py` reads probe input as bytes, decodes strict UTF-8 and handles each JSON record independently. It reports invalid line numbers and SHA-256 fingerprints without echoing raw data. Each invalid line also contributes a malformed sentinel to the existing coverage calculation, so the audit stays unverified. Input files are never repaired or rewritten by the reader.
- `scripts/trading_session_probe.py` flushes and fsyncs the complete JSONL record. This is a standalone read-only QA process, not work added to the broker event loop. Fsync reduces unsynced evidence exposure; it does not establish immunity to all power/storage failures. Errors still propagate rather than inventing samples.
- Three CLI regression cases cover NUL-damaged data, invalid UTF-8 and truncated JSON. They require structured output, unchanged raw input, no raw private canary disclosure and failed coverage despite otherwise complete healthy samples. A fourth case requires a complete flushed record before durable sync.

All four new cases failed on the baseline; 16 focused tests pass after the fix. Full make verify passed: 11787 passed, 30 skipped in 1010.51s (0:16:50); TypeScript and VERIFY OK. Frontend: 2,380 tests across 257 files. Failure sets remain empty and the exact 30-skip set is unchanged. Final log and source hashes are recorded in validation.json. No engine, strategy, order, GTT, account-authentication, live-execution or risk setting is changed by this patch.

## Evidence and safe continuation

Private evidence: `.runtime/qa/2026-10-25/probe-log-recovery/`. The original frozen damaged log remains archived and its audit remains unverified. Before starting the next active evidence segment, the standalone QA probe is stopped, the complete old file is preserved byte-for-byte with verified archives, and a new private segment is created at the configured path. The monitor is then restarted. No missing sample is filled, no corrupt line is silently removed from its original archive, and old coverage is not attributed to the new segment. The archive manifest records this boundary separately after deployment.

The system restart loaded the previously configured DNS option in both services; the launcher resolver measured 0.099 seconds in its actual environment. The temporary Cloudflare hostname changed during that normal startup. Current origin and process identities are recorded at deployment time, not assumed from older reports. Origin-bound notification registrations require the current hostname.

## Remaining acceptance

Actual complete market-session load, Gamma recovery/speed/two-day and full-day simulation, real Snapback saved-row restore, authoritative research, hosted CI billing, sustained temporary-endpoint availability and real phone visual/touch observations retain their prior requirements. A clean new probe segment is not a completed market session. Sunday checks cannot close those gates.
