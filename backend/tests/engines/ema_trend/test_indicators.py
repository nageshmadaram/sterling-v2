"""Independent seeds, gap arithmetic, and causal-prefix acceptance fixtures."""
import json
from pathlib import Path

import pytest

from app.engines.ema_trend.indicators import dema, ema, supertrend
from .helpers import bar

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "indicator_golden.json").read_text())


def assert_optional_numbers(actual, expected):
    assert len(actual) == len(expected)
    for observed, wanted in zip(actual, expected):
        if wanted is None:
            assert observed is None
        else:
            assert observed == pytest.approx(wanted, rel=1e-12, abs=1e-12)


def test_ema_sma_seed_and_no_zero_padding():
    fixture = GOLDEN["ema3"]
    assert_optional_numbers(ema(fixture["input"], 3), fixture["expected"])


def test_dema_second_seed_starts_on_valid_first_ema_suffix():
    fixture = GOLDEN["dema3"]
    assert_optional_numbers(dema(fixture["input"], 3), fixture["expected"])


def test_real_dema50_first_exists_on_observation_99():
    observed = dema(list(range(1, 121)))
    assert observed[:98] == [None] * 98
    # For a linear ramp the double EMA removes the single EMA's lag exactly.
    assert_optional_numbers(observed[98:], list(range(99, 121)))


def test_supertrend_wilder_gap_atr_and_initial_down_direction():
    fixture = GOLDEN["supertrend3_factor1"]
    bars = [bar(i, close) for i, close in enumerate(fixture["closes"])]
    lines, directions = supertrend(bars, period=3, factor=1)
    assert_optional_numbers(lines, fixture["expected_line"])
    assert directions == fixture["expected_direction"]


def test_supertrend_equality_does_not_flip_down_to_up():
    bars = [bar(i, close) for i, close in enumerate([10, 11, 12, 14])]
    lines, directions = supertrend(bars, period=3, factor=1)
    assert lines[-1] == pytest.approx(14)
    assert directions[-1] == "DOWN"


@pytest.mark.parametrize("length", [1, 2, 9, 10, 21, 50, 99, 150])
def test_appending_future_bars_never_rewrites_indicator_prefix(length):
    values = [100 + i * .17 + ((i % 7) - 3) * .8 for i in range(160)]
    bars = [bar(i, close) for i, close in enumerate(values)]
    assert_optional_numbers(ema(values, 10)[:length], ema(values[:length], 10))
    assert_optional_numbers(dema(values)[:length], dema(values[:length]))
    full_line, full_direction = supertrend(bars)
    prefix_line, prefix_direction = supertrend(bars[:length])
    assert_optional_numbers(full_line[:length], prefix_line)
    assert full_direction[:length] == prefix_direction


@pytest.mark.parametrize("values,period", [([], 3), ([1, 2], 3)])
def test_insufficient_seed_history_stays_unavailable(values, period):
    assert ema(values, period) == [None] * len(values)
    assert dema(values, period) == [None] * len(values)
