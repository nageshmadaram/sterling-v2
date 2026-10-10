"""The virtual tenant never inherits the owner's legacy single-user config row (intraday, adaptive edge)."""
from __future__ import annotations

import json

import pytest
from tenancy_support import OWNER, SIM_UID

from app.services import adaptive_edge, db, intraday


@pytest.mark.parametrize("module, key", [(intraday, "intraday_config"), (adaptive_edge, "adaptive_edge_config")])
def test_a_real_uid_inherits_the_legacy_row_and_the_sim_tenant_does_not(module, key):
    for k in (key, f"{key}:{OWNER}", f"{key}:{SIM_UID}"):
        db.set_config(k, "")
    db.set_config(key, json.dumps({"enabled": False}))
    try:
        assert module.get_config(OWNER).enabled is False          # the real tenant still inherits it
        assert module.get_config(SIM_UID).enabled is True         # the virtual one starts from the defaults
        db.set_config(f"{key}:{SIM_UID}", json.dumps({"enabled": False}))
        assert module.get_config(SIM_UID).enabled is False        # its own row still wins
    finally:
        for k in (key, f"{key}:{SIM_UID}"):
            db.set_config(k, "")
