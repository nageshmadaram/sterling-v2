import pytest

from app.services.simulation import identity as i


def test_sim_identities_round_trip_and_never_collide_with_live():
    assert i.sim_uid("default") == "sim:default"
    assert i.sim_uid("sim:default") == "sim:default"           # idempotent
    assert i.owner_uid("sim:default") == "default" and i.owner_uid("default") == "default"
    assert i.is_sim_uid("sim:default") and not i.is_sim_uid("default")
    assert not i.is_sim_uid("sim:") and not i.is_sim_uid(None) and not i.is_sim_uid(5)
    assert i.mode_for_uid("sim:x") == "SIMULATION" and i.mode_for_uid("x") == "LIVE"


def test_the_virtual_account_id_is_stable_and_recognisable():
    a = i.sim_account_id("default")
    assert a == i.sim_account_id("sim:default") and a.startswith("SIM-")
    assert i.is_sim_account_id(a) and not i.is_sim_account_id("KITE-8261BCE645")
    assert i.sim_account_id("alice") != a


def test_an_empty_owner_is_refused():
    with pytest.raises(ValueError):
        i.sim_uid("  ")
