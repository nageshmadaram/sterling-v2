"""Rule fixtures from operator-supplied GAMMA_Buzzer posts (not market replays)."""
from datetime import date
from app.engines.gamma_move import GammaMoveConfig, lists_for, target_price, trading_days_between
from app.engines.gamma_move.exit import d68_action


def test_oct14_oct15_reference_expiry_rollover():
    cfg = GammaMoveConfig()
    expiries = ['2026-10-27', '2026-11-23']
    before = lists_for(date(2026, 10, 14), expiries, cfg)
    after = lists_for(date(2026, 10, 15), expiries, cfg)
    assert trading_days_between(date(2026, 10, 14), date(2026, 10, 27)) == 8
    assert trading_days_between(date(2026, 10, 15), date(2026, 10, 27)) == 7
    assert {s.expiry_date for s in before if s.list_name == 'BREAKDOWN-PE'} == {date(2026, 10, 27)}
    assert {s.expiry_date for s in after if s.list_name.startswith('BREAK')} == {date(2026, 11, 23)}
    assert {s.expiry_date for s in after if s.list_name.startswith('EXPIRY')} == {date(2026, 10, 27)}


def test_indigo_reference_targets_and_armed_stop_do_not_immediately_exit():
    assert target_price(151, .20, .05) == 181.20
    assert target_price(151, .40, .05) == 211.40
    assert d68_action('PE', 4731.8, 4718.5, False) == 'arm'
    assert d68_action('PE', 4731.8, 4718.5, True) is None
    assert d68_action('PE', 4731.8, 4732, True) == 'stop'


def test_oct16_alert_refers_to_oct15_last_regular_minute_close():
    # Reference delivery is 16 Oct morning; broker-dated completed closes are
    # 14 Oct (4718.5, arm), then 15 Oct (4778.3, stop). The reference option
    # close is the 15:29 minute's 87.15, not the daily candle's 89.15.
    from decimal import Decimal, ROUND_HALF_UP
    assert d68_action('PE', 4731.8, 4718.5, False) == 'arm'
    assert d68_action('PE', 4731.8, 4778.3, True) == 'stop'
    entry, exit_price, qty = Decimal('151'), Decimal('87.15'), 150
    assert ((exit_price / entry - 1) * 100).quantize(Decimal('.1')) == Decimal('-42.3')
    pnl = (exit_price - entry) * qty
    assert pnl == Decimal('-9577.50')
    assert pnl.quantize(Decimal('1'), rounding=ROUND_HALF_UP) == Decimal('-9578')
