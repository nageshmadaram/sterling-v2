"""Read-only audit output must distinguish observations from live acceptance."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('trading_session_audit', Path(__file__).resolve().parents[2] / 'scripts/trading_session_audit.py')
audit = importlib.util.module_from_spec(spec)


def test_audit_filters_day_and_uses_strict_stall_thresholds():
    spec.loader.exec_module(audit)
    lines = ['2026-10-22 11:00:00 Event loop stalled 99.0s; locations=old',
             '2026-10-23 11:00:00 Event loop stalled 5.0s; locations=a > b',
             '2026-10-23 12:00:00 Event loop stalled 1.2s; locations=a > b',
             '2026-10-23 16:00:00 Event loop stalled 9.0s; locations=closed']
    result = audit.summarize(lines, '2026-10-23')
    assert result['market_stalls']['count'] == 2
    assert result['market_stalls']['max_seconds'] == 5.0
    assert result['observed_threshold_met'] is True
    assert result['full_session_accepted'] is False
    assert result['after_hours_stalls']['count'] == 1


def test_missing_session_coverage_never_becomes_a_pass():
    spec.loader.exec_module(audit)
    result = audit.summarize([], '2026-10-23')
    assert result['full_session_accepted'] is False
    assert result['coverage'] == 'unverified'
