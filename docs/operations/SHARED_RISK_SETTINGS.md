# Shared risk settings

Open **Tools → Connect → Trading Config → Shared risk settings**. Enter only the limits
you intend to override, select Snapback, Gamma-Unwind and/or SuperTrend, turn on
shared overrides and save. No money amounts or strategy selections are supplied
by default. Saving settings never turns on live entries.

A filled value replaces the selected strategies' corresponding entry setting or
adds a limit where none exists. A blank value retains the strategy setting.
Unselected strategies keep their own settings. Stored strategy configurations are
not rewritten, so disabling the shared policy restores them.

- **Allocation cap:** account capital already deployed plus pending reservations
  and the proposed trade. It is an account-book cap applied to selected entries.
- **Maximum daily loss:** the account's realised INR loss for the IST session,
  including its existing authoritative accounting. This is not separate P&L for
  each selected strategy. Unreadable loss accounting cannot authorize an entry.
- **Per-trade risk cap:** committed option premium or futures notional. It is
  deliberately not a promise that a stop fills at its requested price. Fees and
  execution gaps still matter. SuperTrend sizing respects whole lots; a budget
  below one executable lot refuses the entry. Gamma's chosen quantity must fit.

Account-wide halts, external exposure, broker identity, risk hierarchy, margin,
protection and release gates remain independent requirements. Exits and protective
actions are not turned off by this policy. A Snapback hedge continuation is exempt
only when the existing durable protection/ownership proof authorizes that exact leg.

Profiles belong to the authenticated user and persist in the encrypted-credential
application database's configuration table. Concurrent editors use revision checks;
a stale save returns 409 rather than overwriting newer limits. Unsaved UI drafts
survive refreshes and participate in the settings navigation guard.

API: authenticated `GET`/`PUT /api/v1/config/shared-risk`. Start with the revision
returned by GET. Unknown fields, unsupported strategies, nonfinite/nonpositive
amounts and a per-trade cap above allocation are rejected. Monetary fields accept
`null` for inheritance. Enabled profiles require a strategy and at least one limit.
