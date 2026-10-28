# Blocking gates

Observed source: `416bf6b4c9c5f98e1efbd3d9d683be5834fcedd0`.
Scope: local checkout and host; no broker or production instance inspected.

R1: not certified. R2: blocked. R3: unsupported. New live entries are not authorized.

| Gate | State | Required evidence |
|---|---|---|
| G0 | UNKNOWN | Clean immutable source and dependency provenance |
| G1 | UNKNOWN | Executed current-SHA CI jobs, nonzero runner and steps |
| G2 | UNKNOWN | Measured production host SLO and storage |
| G3 | UNKNOWN | Owner-approved static IP, callback, binding and fresh broker inventory |
| G4 | UNKNOWN | Lifecycle, risk and protection acceptance |
| G5 | UNKNOWN | Independent encrypted off-host restore and failure drills |
| G6 | UNKNOWN | Telegram receipt on operator device |
| G7 | UNKNOWN | Independent per-mode after-cost OOS and forward evidence |
| G8 | UNKNOWN | Independent operator handoff |
| G9 | UNKNOWN | Separately authorized broker order, protection and exit receipts |

Local tests are recorded separately and cannot become CI, broker or human acceptance.
Code present in a source file does not establish that its acceptance criterion passed.
