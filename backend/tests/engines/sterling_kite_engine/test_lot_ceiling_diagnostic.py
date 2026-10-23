from datetime import datetime, timezone
from app.services.kite_engine import service

NOW = int(datetime(2026, 10, 23, 8, tzinfo=timezone.utc).timestamp()*1000)


def row(entry=100, stop=20, at=NOW):
    return {'timestamp_ms': at, 'horizon_mode': 'swing', 'is_active': True,
            'legs': [{'option_symbol': 'TESTCE', 'premium_spot': entry, 'entry_sl': stop, 'lot_size': 100}]}


def test_two_lot_signal_ceiling_can_exceed_the_daily_halt():
    data, warning = service._lot_ceiling_diagnostic([row()], 'swing', 2, 15000, NOW)
    assert data['median_one_lot_stop_inr'] == 8000
    assert data['lot_ceiling_stop_inr'] == 16000
    assert 'ceiling' in warning and 'not a target' in warning
    assert service._lot_ceiling_diagnostic([row()], 'swing', 1, 15000, NOW)[1] is None


def test_diagnostic_deduplicates_contract_plans_and_keeps_missing_evidence_unknown():
    data, _ = service._lot_ceiling_diagnostic([row(), row(80, 50, NOW+1)], 'swing', 10, 15000, NOW+2)
    assert data['sample_contracts'] == 1 and data['median_one_lot_stop_inr'] == 3000
    data, warning = service._lot_ceiling_diagnostic([row(at=NOW-86400000)], 'swing', 10, 15000, NOW)
    assert data['median_one_lot_stop_inr'] is None and warning is None


def test_closed_legs_do_not_inflate_the_current_plan_warning():
    plan = row()
    plan['legs'][0]['is_active'] = False
    data, warning = service._lot_ceiling_diagnostic([plan], 'swing', 10, 15000, NOW)
    assert data['sample_contracts'] == 0 and warning is None
