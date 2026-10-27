"""No historical/mixed-build trade can masquerade as forward profitability."""
from copy import deepcopy

import pytest

from app.services.snapback_promotion_scope import out_of_scope_trade_errors


def sample():
    return dict(
        experiment_id="RUN-B",
        runtime_build_sha="FROZEN-BUILD",
        sessions=[
            {"session_date": "2026-10-22", "experiment_id": "RUN-A",
             "runtime_build_sha": "FROZEN-BUILD"},
            {"session_date": "2026-10-23", "experiment_id": "RUN-B",
             "runtime_build_sha": "FROZEN-BUILD"},
        ],
        opportunities=[
            {"opportunity_id": "OPP-1",
             "signal_timestamp": "2026-10-23T15:20:00+05:30"}
        ],
        outcomes=[{"opportunity_id": "OPP-1"}],
    )


def check(**overrides):
    data = sample()
    data.update(overrides)
    return out_of_scope_trade_errors(**data)


def test_current_run_trade_is_attributable():
    assert check() == []


def test_previous_experiment_signal_is_never_counted():
    data = sample()
    data["opportunities"][0]["signal_timestamp"] = "2026-10-22T15:20:00+05:30"
    result = check(**data)
    assert len(result) == 1
    assert "not a session" in result[0]


def test_old_build_session_cannot_vouch_for_same_experiment_trade():
    data = sample()
    data["sessions"][1]["runtime_build_sha"] = "OLD-BUILD"
    assert check(**data)


@pytest.mark.parametrize("timestamp", [
    "", None, 1711500000, "not-a-date", "2026-13-23",
    "2026-10-24T15:20:00+05:30",
])
def test_missing_malformed_or_unobserved_signal_time_is_inadmissible(timestamp):
    data = sample()
    data["opportunities"][0]["signal_timestamp"] = timestamp
    assert check(**data)


def test_missing_day_t_opportunity_is_inadmissible():
    assert check(opportunities=[])


def test_two_possible_day_t_rows_are_ambiguous():
    data = sample()
    data["opportunities"].append(deepcopy(data["opportunities"][0]))
    assert check(**data)


def test_no_outcomes_means_no_cross_experiment_trade_claim():
    assert check(outcomes=[]) == []


@pytest.mark.parametrize("field", ["experiment_id", "runtime_build_sha"])
def test_missing_promotion_identity_is_not_an_empty_clean_sample(field):
    data = sample()
    data[field] = ""
    assert check(**data)


def test_second_foreign_trade_blocks_entire_sample():
    data = sample()
    data["opportunities"].append(
        {"opportunity_id": "OLD-OPP",
         "signal_timestamp": "2026-10-22T14:45:00+05:30"})
    data["outcomes"].append({"opportunity_id": "OLD-OPP"})
    errors = check(**data)
    assert len(errors) == 1
    assert "OLD-OPP" in errors[0]


def test_session_date_is_validated_not_just_prefix_matched():
    data = sample()
    data["sessions"][1]["session_date"] = "2026-99-99"
    assert check(**data)
