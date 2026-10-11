"""Causal bridges from the shared replay clock to the production strategy cores.

These functions cannot submit broker orders. Historical Gamma rows are signal
observations; minute OHLCV does not establish a live executable tick/fill.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import asyncio

from app.domain.models import Candle


def hourly_prefix(runner, symbol, at_ms):
    from app.services.kite_engine.scanner import drop_forming
    rows = getattr(runner, '_supertrend_hourly', {}).get(symbol, [])
    candles = [Candle(timestamp_ms=int(float(b['time']) * 1000), open=b['open'],
                      high=b['high'], low=b['low'], close=b['close'], volume=b.get('volume', 0))
               for b in rows]
    return drop_forming(candles, at_ms, exchange='BSE' if symbol in ('SENSEX', 'BANKEX') else 'NSE', cas_eligible=True)


def supertrend_entry(runner, symbol, at_ms):
    from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
    candles = hourly_prefix(runner, symbol, at_ms)
    if not candles:
        runner._strategy_notes['supertrend'] = 'Completed 1H history is unavailable; lower-resolution bars are not substituted.'
        return []
    last_ms = candles[-1].timestamp_ms
    seen = getattr(runner, '_supertrend_seen', None)
    if seen is None:
        seen = runner._supertrend_seen = {}
    if seen.get(symbol) == last_ms:
        return []
    seen[symbol] = last_ms
    if last_ms < runner._start_epoch * 1000:
        return []  # a pre-window alignment is warmup, never a fresh entry
    cfg = runner._supertrend_cfg
    signals = SterlingKiteEngine(cfg).generate(candles, symbol)
    return [dict(strategy='supertrend', direction='BULLISH' if signal.direction == 'long' else 'BEARISH',
                 strength=signal.strength, stop=signal.stop_loss, target=None,
                 no_target=True, no_trail=True, fill_spot=candles[-1].close,
                 canonical_entry_bar_ms=last_ms, strategy_version='canonical_1h',
                 max_hold_bars=0) for signal in signals]


def settle_supertrend(runner, trade, bar_dt):
    from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
    from app.engines.sterling_kite_engine.exits import resolve_exit, reported_trail_level
    candles = hourly_prefix(runner, trade.underlying, int(bar_dt.timestamp() * 1000))
    if not candles:
        return
    entry_i = next((i for i, b in enumerate(candles) if b.timestamp_ms == trade.canonical_entry_bar_ms), None)
    if entry_i is None:
        runner._strategy_notes['supertrend'] = 'Entry candle is missing from retained 1H history; lifecycle cannot be reconstructed.'
        return
    cfg = runner._supertrend_cfg
    regime = compute_regime(*([getattr(b, name) for b in candles] for name in ('open', 'high', 'low', 'close')), cfg)
    longs, shorts = entry_transitions(regime)
    direction = 'long' if trade.opt_type == 'CE' else 'short'
    exit_i, reason = resolve_exit(regime, direction, entry_i, len(candles) - 1, cfg, longs, shorts,
                                  is_stock=trade.underlying not in ('NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'BANKEX'))
    trail = reported_trail_level(regime, direction, entry_i, exit_i, len(candles) - 1, cfg)
    trade.spot_stop = trail
    trade.stop_loss = runner._premium_for_spot(trade, trail)
    if exit_i is not None:
        # The prior-close stop is known; gaps fill adversely at the raw open.
        price = candles[exit_i].close
        if 'trail breach' in reason or 'raw price stop' in reason:
            price = min(candles[exit_i].open, trail) if direction == 'long' else max(candles[exit_i].open, trail)
        runner._close_position(trade, price, bar_dt, reason)
    else:
        from app.services.simulation import _mark_open_trade
        mark = runner._premium_for_spot(trade, candles[-1].close)
        _mark_open_trade(trade, mark, raw_mark=mark)


async def load_gamma_days(runner, first: date, last: date):
    from app.services import gamma_move, gamma_move_history, gamma_move_historical
    from app.services.exchanges.kite import accounts
    uid = runner._uid
    runner._gamma_replay_days = {}
    cfg = gamma_move.get_config(uid)
    cursor = first
    can_fetch = bool(accounts.get_active(uid))
    while cursor <= last:
        archive = gamma_move_history.read_day(uid, cursor)
        if not archive and can_fetch and cursor < gamma_move.ist_today():
            try:
                archive = await asyncio.wait_for(gamma_move_historical.replay_day(uid, cursor, cfg), timeout=30)
            except Exception as exc:
                archive = {'signals': [], 'watchlist': [], 'coverage': [f'{cursor}: Gamma history unavailable ({type(exc).__name__})'], 'complete': False}
        if not archive:
            archive = {'signals': [], 'watchlist': [], 'coverage': [f'{cursor}: no recorded Gamma events or authenticated historical data'], 'complete': False}
        runner._gamma_replay_days[cursor.isoformat()] = deepcopy(archive)
        cursor += timedelta(days=1)
    runner._strategy_notes['gamma_move'] = 'Gamma replays recorded E1 or minute-proxy signals; executable tick fills and trade P&L are not reconstructed.'


def gamma_snapshot(runner):
    from app.services.gamma_move import get_config, descriptor
    now_ms = runner._get_sim_now_ms()
    current = datetime.fromtimestamp(now_ms / 1000, runner._replay_ist).date().isoformat()
    archive = getattr(runner, '_gamma_replay_days', {}).get(current, {})
    # Watch snapshots saved at session end contain future state. Only immutable
    # timestamped E1 events can be released on a replay clock.
    rows = [deepcopy(row) for row in archive.get('signals', []) if 0 < int(row.get('at_ms') or 0) <= now_ms]
    for row in rows:
        row['execution_eligible'] = False
        row['historical'] = True
    coverage = list(archive.get('coverage') or [])
    return dict(day=current, today=current, historical=True, recorded=True, source=archive.get('source', 'historical_replay'),
                replay_available=False, is_trading_day=True, latest_trading_day=current, history_days=[],
                strategy=descriptor(), config=get_config(runner._uid).as_dict(), scan={}, session=None,
                candidates=rows, positions=[], record={}, coverage=coverage,
                mode={'auto_execute': False, 'is_paper': True, 'live_execution_enabled': False},
                warnings=[runner._strategy_notes.get('gamma_move', 'Read-only replay')], blockers=coverage)


def emit_gamma_until(runner, at_ms):
    from app.services.simulation import SimSignalEvent
    seen = getattr(runner, '_gamma_seen', None)
    if seen is None:
        seen = runner._gamma_seen = set()
    for archive in getattr(runner, '_gamma_replay_days', {}).values():
        for row in archive.get('signals', []):
            timestamp = int(row.get('at_ms') or 0)
            key = (row.get('id'), timestamp)
            if key in seen or timestamp <= 0 or timestamp > at_ms or timestamp < runner._start_epoch * 1000:
                continue
            instrument = row.get('instrument') or {}
            if runner._config.instruments and row.get('underlying') not in runner._config.instruments:
                continue
            seen.add(key)
            levels = row.get('levels') or {}
            when = datetime.fromtimestamp(timestamp / 1000, runner._replay_ist)
            event = SimSignalEvent(time_iso=when.isoformat(), timestamp_ms=timestamp, strategy='gamma_move',
                                   instrument=row.get('underlying') or '', contract=instrument.get('tradingsymbol'),
                                   direction='BULLISH' if instrument.get('option_type') == 'CE' else 'BEARISH',
                                   strength=row.get('tier') or 'WATCHING', entry=levels.get('entry'), stop=levels.get('stop'), target=levels.get('t1'),
                                   premium_entry=levels.get('entry'), premium_sl=levels.get('stop'), premium_target=levels.get('t1'),
                                   strike=instrument.get('strike'), opt_type=instrument.get('option_type'), scan_origin=archive.get('source', 'historical_replay'),
                                   strategy_version='gamma_e1_observation')
            runner._stats.events.append(event)
            runner._stats.signals_fired = len(runner._stats.events)
            runner._last_signal = event
            runner._publish('signal', event.model_dump())


def snapback_snapshot(runner):
    from app.services.snapback import descriptor, get_config, CONTRACT_VERSION
    cfg = getattr(runner, '_cached_snapback_cfg', None) or get_config(runner._uid)
    rows = []
    for event in runner._events_for('snapback'):
        trade = next((t for t in runner._stats.trades if t.strategy == 'snapback' and t.underlying == event.instrument and t.timestamp_ms == event.timestamp_ms), None)
        quantity = trade.quantity if trade else 0
        contract = None
        # A theoretical symbol is retained only as model context, never a broker identity.
        if event.contract:
            contract = dict(symbol=event.contract, exchange='BFO' if event.instrument == 'SENSEX' else 'NFO',
                            strike=event.strike, option_type=event.opt_type, expiry=None, dte=None,
                            lot_size=quantity // trade.lots if trade and trade.lots else 0, token=0)
        rows.append(dict(signal_id=f'replay:{event.instrument}:{event.timestamp_ms}', strategy='snapback',
                         trading_mode='swing', side='fade_up' if event.direction == 'BEARISH' else 'fade_down',
                         symbol=event.instrument, state='ended' if trade and trade.status != 'OPEN' else 'running' if trade else 'watching',
                         reason='Historical model; no live execution', execution_eligible=False, historical=True, origin='replay',
                         direction=event.direction, opt_type=event.opt_type, timestamp_ms=event.timestamp_ms, spot=event.spot or event.entry,
                         mean_target=event.target, distance_pct=0, stretch=0, level=event.entry, strength=event.strength,
                         realized_vol_pct=0, assumed_iv_pct=0, assumed_vrp=0, hold_days=cfg.hold_days, underlying_token=0,
                         contract=contract, premium=trade.mark_price if trade and trade.status == 'OPEN' else event.premium_entry,
                         premium_is_modelled=True, modelled_premium=event.premium_entry, quote=None,
                         stop_premium=trade.stop_loss if trade else event.premium_sl, target_premium=event.premium_target, trail_premium=None,
                         runner_premium=None, outcome=None if not trade else dict(entry_premium=trade.entry_price, exit_premium=trade.mark_price if trade.status == 'OPEN' else trade.exit_price,
                         open=trade.status == 'OPEN', exit_day='', exit_ms=trade.exit_timestamp_ms, reason=trade.exit_reason or 'OPEN', held_days=0, sessions_left=0, net=trade.pnl_usd, return_pct=trade.pnl_pct, spot_out=trade.spot_entry, beta=None, market_pnl=None, hedge_cost=None, net_unhedged=None),
                         lots=trade.lots if trade else 0, quantity=quantity, deployed_inr=trade.entry_price * quantity if trade else None,
                         min_outlay_inr=None, reasons=[], metrics={"missing_setup_metrics": True}))
    return dict(strategy=descriptor(cfg), config=cfg.as_dict(), contract_version=CONTRACT_VERSION, trading_mode='swing',
                rows=rows, armed=0, scanned=len(rows), scanning=False, last_scan_ms=runner._get_sim_now_ms(), last_error=None, failures=[],
                warnings=[runner._strategy_notes.get('snapback', 'Historical Snapback model')], auto_execution_blocker='Historical replay is read-only',
                catchup_sessions=0, capabilities={'manual_execution': {'single_leg': False, 'reason': 'Historical replay is read-only'},
                                                 'auto_execution': {'available': False, 'reason': 'Historical replay is read-only'}})


def kite_snapshot(runner):
    from app.services.simulation import KITE_TOKENS
    from app.services.ohlcv_store import INDEX_ALIASES
    now_ms = runner._get_sim_now_ms()
    rows = []
    for event in runner._events_for('supertrend', 'spot_scan', 'kite_engine'):
        if event.entry is None or event.timestamp_ms > now_ms:
            continue
        trade = next((t for t in runner._stats.trades if t.strategy == event.strategy and t.underlying == event.instrument and t.timestamp_ms == event.timestamp_ms), None)
        raw = next((r.get('raw_row') or {} for r in getattr(runner, '_recorded_signals', [])
                    if r.get('underlying') == event.instrument and r.get('timestamp_ms') == event.timestamp_ms), {})
        identity = next((leg for leg in raw.get('legs', []) if leg.get('option_symbol') == event.contract), {})
        live = bool(trade and trade.status == 'OPEN')
        legs = []
        if event.contract:
            lot_size = identity.get('lot_size') or (trade.quantity // trade.lots if trade and trade.lots else None)
            legs.append(dict(moneyness=identity.get('moneyness', 'ATM'), option_type=event.opt_type,
                             option_symbol=event.contract, strike=event.strike or event.entry,
                             expiry=identity.get('expiry', ''), lot_size=lot_size,
                             premium_spot=event.premium_entry, entry_sl=event.premium_sl,
                             premium_sl=trade.stop_loss if trade else event.premium_sl,
                             premium_target=None, last_price=trade.mark_price if live else None,
                             exit_price=trade.exit_price if trade and not live else None,
                             exit_state=trade.exit_reason if trade and not live else None,
                             is_active=live, lots=trade.lots if trade else 0,
                             signal_timestamp_ms=event.timestamp_ms, entry_timestamp_ms=event.timestamp_ms))
        long = event.direction.upper() in ('BULLISH', 'LONG', 'BUY')
        rows.append(dict(underlying=event.instrument, token=raw.get('token') or KITE_TOKENS.get(event.instrument.upper(), 0),
                         exchange='BFO' if event.instrument in ('SENSEX', 'BANKEX') else 'NFO',
                         regime='BULL' if long else 'BEAR', alignment={'fast': 1 if long else -1, 'mid': 1 if long else -1, 'slow': 1 if long else -1},
                         direction='long' if long else 'short', option_type=event.opt_type or ('CE' if long else 'PE'), legs=legs,
                         spot=event.spot or event.entry, underlying_spot=event.spot or event.entry,
                         stop_loss=event.stop or 0, entry_sl=event.stop, target=None,
                         exit_state=trade.exit_reason if trade and not live else '—', exit_reason=trade.exit_reason if trade and not live else None,
                         score=raw.get('score') or 0, timestamp_ms=event.timestamp_ms, is_active=live, is_fresh=False,
                         source=raw.get('source', 'spot'), is_replay=True))
    return dict(generated_ms=now_ms, rows=rows, scanning=False, scanning_label='Historical replay', next_scan_ms=0, auto_scan=False,
                market_open=False, feed_mode='replay' if runner.is_replay_live else 'replay_review', replay_live=runner.is_replay_live)
