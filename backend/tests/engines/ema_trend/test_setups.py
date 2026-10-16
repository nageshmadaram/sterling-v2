from decimal import Decimal

from app.engines.ema_trend.setups import evaluate_setups
from .helpers import daily_bar as bar, feature, pivot


def retest_features():
    result = [feature(i, 103, open_=103, ema10=102, ema21=101) for i in range(8)]
    result[0] = feature(0, 103, ema10=100, ema21=101)
    result[2].bar = bar(2, 103, open_=103, low=95, high=104)
    result[3].bar = bar(3, 103, open_=103, low=100, high=104)
    result[4].bar = bar(4, 103, open_=103, low=100, high=104)
    result[4].pivots = [pivot("LOW", 95, 2)]
    result[5].bar = bar(5, 103, open_=103, low=99, high=104)
    result[6].bar = bar(6, 103, open_=103, low=100, high=104)
    result[7] = feature(7, 106, open_=104, ema10=102, ema21=101,
                        pivots=[pivot("LOW", 99, 5)])
    return result


def test_crossover_is_watch_evidence_and_cannot_enter_alone():
    fs = [feature(0, 100, ema10=99, ema21=100),
          feature(1, 105, open_=100, ema10=101, ema21=100)]
    result = evaluate_setups("NSE:TEST", "swing", fs)
    assert result[-1].reason == "CROSS_ARMED_NOT_ENTRY"
    assert result[-1].status != "armed"


def test_retest_keeps_stop_known_at_touch_despite_later_confirmed_pivot():
    fs = retest_features()
    result = evaluate_setups("NSE:TEST", "swing", fs)
    assert result[4].status != "armed"
    assert result[7].status == "armed"
    assert result[7].path.startswith("RETEST")
    assert result[7].stop == Decimal("94.50")
    assert result[7].ceiling == Decimal("108.5")


def test_no_confirmed_post_cross_pivot_means_no_guessed_stop():
    fs = retest_features()
    for f in fs:
        f.pivots = []
    assert not any(x.status == "armed" for x in evaluate_setups("NSE:TEST", "swing", fs))


def test_longest_preceding_box_wins_and_current_breakout_not_in_box():
    fs = [feature(i, 103.5, open_=103.5, ema10=101, ema21=100) for i in range(8)]
    fs[0].ema10 = 99
    for i in range(2, 7):
        fs[i].bar = bar(i, 103.5, open_=103.5, low=103 if i == 2 else 103.2, high=104.2)
    fs[4].pivots = [pivot("LOW", 103, 2)]
    fs[7] = feature(7, 105, open_=104, ema10=101, ema21=100)
    result = evaluate_setups("NSE:TEST", "swing", fs)
    assert result[-1].status == "armed"
    assert result[-1].path == "BOX_5"
    assert result[-1].stop == Decimal("102.50")


def test_bearish_cross_invalidates_unfilled_setup():
    fs = retest_features()
    fs[6].ema10 = 100
    result = evaluate_setups("NSE:TEST", "swing", fs)
    assert result[6].cross_index is None
    assert result[7].status != "armed"


def test_append_only_setup_outputs_do_not_rewrite_prior_decisions():
    fs = retest_features()
    full = evaluate_setups("NSE:TEST", "swing", fs, config_revision=2)
    for length in range(1, len(fs)):
        prefix = evaluate_setups("NSE:TEST", "swing", fs[:length], config_revision=2)
        assert [s.model_dump() for s in full[:length]] == [s.model_dump() for s in prefix]


def test_warmup_cannot_publish_an_actionable_setup():
    fs = retest_features()
    for f in fs:
        f.ready = False
    result = evaluate_setups("NSE:TEST", "swing", fs)
    assert all(s.status != "armed" for s in result)


def test_missing_signal_session_invalidates_prior_cross_and_retest():
    fs = retest_features()
    assert evaluate_setups("NSE:TEST", "swing", fs)[7].status == "armed"

    # Skip the first expected session after bar 4, then resume with ordinary
    # adjacent sessions. The old implementation cleared only the touch, kept
    # the crossover, and could arm the stale retest at bar 7.
    for i in range(5, len(fs)):
        original = fs[i].bar
        replacement_start = bar(i + 1).open_ms
        duration = original.close_ms - original.open_ms
        fs[i].bar = original.model_copy(update={
            "open_ms": replacement_start,
            "close_ms": replacement_start + duration,
            "available_ms": replacement_start + duration,
        })

    result = evaluate_setups("NSE:TEST", "swing", fs)
    assert result[5].reason == "MISSING_SIGNAL_BARS_RESET_SETUP"
    assert all(row.cross_index is None and row.status != "armed" for row in result[5:])


def test_missing_signal_session_cannot_be_treated_as_fresh_crossover():
    fs = [
        feature(0, 100, ema10=99, ema21=100),
        feature(1, 105, open_=100, ema10=101, ema21=100),
    ]
    # EMA crossing across an absent session is not a known completed-bar edge.
    fs[1].bar = bar(2, 105, open_=100)
    result = evaluate_setups("NSE:TEST", "swing", fs)
    assert result[-1].cross_index is None
    assert result[-1].reason == "MISSING_SIGNAL_BARS_RESET_SETUP"
