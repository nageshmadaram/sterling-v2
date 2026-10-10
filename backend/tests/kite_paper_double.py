"""Test-only helper: stand up a simulated-broker Kite account.

Sterling has no non-live mode. Every account the public API or the env seed
creates is live, and ``is_paper`` survives in the store only so the suite can
exercise broker-transport behaviour (simulated order ids, no network) and the
conversion of rows an older build wrote. Production code never calls this module.
"""
from __future__ import annotations

from app.services.exchanges.kite import accounts as kite_accounts


def as_paper_double(account_id: str, *, is_paper: bool = True):
    """Flip a stored account's broker mode in place, as a test double.

    Writes through the store's own persistence so the row and the in-memory object
    agree, exactly as an older build's row would after a restart.
    """
    account = kite_accounts._accounts[account_id]
    account.is_paper = bool(is_paper)
    kite_accounts._persist(account)
    return account
