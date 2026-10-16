"""Invalid market/config observations must fail before entering the reducer."""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.engines.ema_trend.config import EmaTrendConfig, canonical_mode
from app.engines.ema_trend.contracts import Bar
from .helpers import bar


@pytest.mark.parametrize("changes", [
    {"close": float("nan")}, {"open": float("inf")}, {"low": 101},
    {"high": 99}, {"volume": -1}, {"available_ms": 1},
    {"open_ms": 9999999999999},
])
def test_invalid_market_facts_are_rejected(changes):
    values = {**bar(0).model_dump(), **changes}
    with pytest.raises(ValidationError):
        Bar(**values)


def test_configuration_is_disabled_paper_and_unfunded_by_default():
    cfg = EmaTrendConfig()
    assert not cfg.enabled and not cfg.auto_execute
    assert cfg.execution_mode == "PAPER"
    assert cfg.allocation_inr == Decimal(0)


@pytest.mark.parametrize("changes", [
    {"execution_mode": "LIVE"}, {"unknown_risk_bypass": True},
    {"risk_fraction": .006}, {"allocation_inr": -1},
    {"modes": ["scalp", "scalping"]}, {"symbols": ["TEST"]},
    {"symbols": ["NSE:TEST", "nse:test"]},
])
def test_unsafe_or_ambiguous_config_is_rejected(changes):
    with pytest.raises((ValidationError, ValueError)):
        EmaTrendConfig(**changes)


def test_mode_aliases_normalize_without_accepting_unknown_strategy():
    assert canonical_mode("scalp") == "scalping"
    assert canonical_mode("ultrascalp") == "ultra_scalping"
    with pytest.raises(ValueError):
        canonical_mode("ema")


def test_hash_is_stable_across_serialization_and_changes_with_risk():
    first = EmaTrendConfig(symbols=[" nse:test "])
    assert first.symbols == ["NSE:TEST"]
    assert first.config_hash == EmaTrendConfig.model_validate_json(first.model_dump_json()).config_hash
    changed = first.model_copy(update={"risk_fraction": Decimal("0.003")})
    assert changed.config_hash != first.config_hash
