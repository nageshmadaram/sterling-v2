# Sterling recovery candidate — latest verified status

Candidate: `sterling-rc-20261020-recovery-final`.
Source archive and SHA-256 checksum are beside this file.
Read `docs/ops/FAMILY_OPERATOR_HANDOVER.md` in that archive or the project.

Latest checks supersede the earlier observations in the handover:
- Production security: PASS.
- Fresh real-broker binding/inventory proof: PASS at 2026-10-20 00:18:27 UTC; zero positions, orders and protective orders observed. This proves the existing session worked, not a new interactive login.
- Latest certification open exposure: PASS, zero unresolved exposure. Connectivity was intermittent earlier; refresh before use.
- Latest isolated restore: PASS, 626,110 rows in two databases, backup 2026-10-20T002024.853856Z on candidate SHA.
- Tests: 191 backend plus ten CLI tests passed.
- Telegram API delivery accepted; physical phone receipt unverified.
- Current credentials passed validation; key rotation was rehearsed on an isolated database, not applied to the live store.

Strict certification remains BLOCKED. See strict-certification.log. Real trade lifecycle, moving-market/reconnect acceptance, twelve physical drills, exact-SHA full CI, whitelist confirmation and operator/nominee handover remain outstanding. A supervised restart was requested; see candidate.json for its final verification. A tag or archive does not certify production acceptance.

Keep the private encryption keys and credentials separate and secure. This source archive contains tracked source only; it is not a complete database or secret recovery bundle. The database backups require matching keys.

Cold-process reconciliation was repaired and subsequently reported CLEAN for the configured broker account at 00:20:06 UTC. Use sterling-recovery-final-source.tar.gz and source-final.sha256 for the final candidate.
