# Gamma offline review — reject SQLite hard-link aliases

**Base:** Sterling V2 draft PR #38, exact SHA `4a812400a219f426c01dfb18fdc236e030ffda19`.
**Release:** PREPRODUCTION / DRAFT / NO-GO. No live broker routing, protective GTT mutation, or order authority.

## Confirmed problem and correction

The offline review guard required a test process and a database path resolving inside the temporary directory. However, a temporary-looking filename could be an ordinary filesystem **hard link** to another SQLite file. Unlike a symbolic link, this alias passes `os.path.islink` and the prior realpath check. It therefore permitted a review-hold schema write to an inode that might also be accessible through an operator database path.

A new negative test reproduced this: `hold_terminal_child_review` returned `review_held_offline_only` when `db._DB_PATH` pointed to a second hard-link name in the temporary directory. The guard now requires an existing regular file with **exactly one filesystem link**, refusing multi-linked aliases before opening the SQLite DB. It returns `offline_disposable_test_database_required` without creating the review table. The test checks the original DB remains unchanged.

## Scope and limitations

This is a conservative, **test-only** boundary. `stat` followed by `sqlite3.connect` is not a race-proof descriptor-pinning protocol; malicious filesystem mutation between these operations is still outside the security guarantee. The feature cannot be deployed or used to approve an order. The existing prototype remains non-authorizing (`execution_authorized=False`) and does not modify broker orders, canonical journal intents, or GTTs.

The separate inventory and parent-projection replay-provenance review is not copied into this branch; its integration needs independent exact-SHA CI and code reconciliation. Existing `immutable_intent_conflict`, global live kill switch, account-wide no-resend UNKNOWN handling, and 0/12 independent production approvals remain intact.

**Verification:** negative hard-link test reproduced the failure before the patch and passed after. Run the whole offline Gamma/canonical suite at this proposed head before considering review acceptance.

**Release decision: NO-GO / LIVE EXECUTION OFF.**
