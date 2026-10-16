"""Offline differential checks against an operator-supplied Gamma client.

Imports only its pure rule modules, never its launcher, config loader, broker adapters,
watcher, database, scheduler or Telegram router. No settings or credentials read.
Run from backend with --reference /path/to/client and an optional --output.
Reference dependencies must exist in a separate audit environment.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import random
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = args.reference.resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from gammad.watch import engine as ref
    from gammad.watch.types import Bar, TickView
    from gammad.core import zones as rz, chain_select as rs
    from gammad.broker.base import ChainRow
    from gammad.alerts import gates as rg
    from gammad.trade import rules as rr
    from gammad.config import ProximityCfg, GatesCfg
    from app.engines.gamma_move import GammaMoveConfig, Candle, InstrumentRef, WatchContract
    from app.engines.gamma_move import levels, trigger, selection, regime
    from app.engines.gamma_move.exit import d68_action
    cfg = GammaMoveConfig()
    base = int(datetime(2026, 10, 5, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))).timestamp()*1000)
    rng = random.Random(82016)
    counts = {'evaluations': 0, 'zone_paths': 0, 'strike_selections': 0, 'tick_paths': 0, 'gate_paths': 0, 'stock_stop_paths': 0}
    failures = []
    def equal(a, b):
        if isinstance(a, float) and isinstance(b, float):
            import math
            return (math.isnan(a) and math.isnan(b)) or math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-9)
        return a == b
    def check(label,a,b):
        if not equal(a,b): failures.append({'case':label,'reference':a,'sterling':b})
    for case in range(100):
        bars=[]; price=100.
        for i in range(65):
            price=max(10,price+rng.uniform(-1,1))
            bars.append(Candle(base+i*300000,price,price+rng.random(),price-rng.random(),price,rng.randint(0,300),rng.randint(800,1000)))
        a=ref.evaluate(bars,900,1000,ref.EngineParams(),.05)
        b=trigger.evaluate(bars,900,1000,cfg,.05)
        for field in ('a','b','c','h','oi_drop','sma','vol_ratio','b_count','atr','range','drift','consol_lo','consol_hi','zero_vol_slots'):
            check(f'evaluation:{case}:{field}',getattr(a,field),getattr(b,field))
        counts['evaluations']+=1
        for side in ('PE','CE'):
            ts=bars[-1].ts_ms+300000
            rd=rg.direction(list(reversed(bars)),ts,side,101)
            sd=regime.direction(list(reversed(bars)),ts,side,101)
            check(f'direction:{case}:{side}',None if rd is None else (rd['f1'],rd['f2']),sd)
            check(f'zone-gap:{case}:{side}',rg.zone_gap_pct(side,101,100),regime.zone_gap_pct(side,101,100))
            check(f'zone-limit:{case}:{side}',rg.zone_limit(2.8,GatesCfg()),regime.zone_limit(2.8,cfg))
            counts['gate_paths']+=1

        daily=bars*8
        a=rz.compute_zones(daily,rz.ZoneParams());b=levels.compute_zones(daily,cfg)
        for side in ('live_buy','live_sell'):
            az=getattr(a,side);bz=getattr(b,side)
            check(f'zones:{case}:{side}:length',len(az),len(bz))
            for n,(x,y) in enumerate(zip(az,bz)):
                for field in ('top','bottom','mid','formed_bar_index','confirmed_bar_index'):
                    check(f'zones:{case}:{side}:{n}:{field}',getattr(x,field),getattr(y,field))
        counts['zone_paths']+=1
        rows=[{'instrument_token':k+1,'option_type':'CE','strike':100+k*.5,'oi':rng.randint(1000,20000),'ltp':5,'previous_volume':20000,'previous_close':10} for k in range(12)]
        chain=[ChainRow(r['strike'],'CE',r['instrument_token'],5,r['oi'],100,None,None,20000,10) for r in rows]
        a=rs.select_strikes(chain,'CE',100,102,2.5,2,cfg=ProximityCfg(),lot_size=10)
        b=selection.select_strikes(rows,'CE',100,102,10,cfg)
        check(f'selection:{case}',[p.security_id for p in a],[p['instrument_token'] for p in b])
        counts['strike_selections']+=1
    watch=WatchContract('TEST',111,'BREAKOUT-CE',InstrumentRef('222','TEST26OCT100CE','CE',100,'2026-10-27',10,.05,'NFO'),'HOI-1',10000,2,102,103,2,build_oi=10000,chain_ltp=100,dte=5)
    for oi,price,volume in [(900,102,200),(990,102,200),(900,101,200),(900,102,10)]:
        bars=[Candle(base+i*300000,100,101,99,100.5 if i==19 else 100,200 if i>=17 else 100,900 if i==19 else 1000) for i in range(20)]
        a=ref.ContractEngine(222,client_rules=True,tick_size=.05);b=trigger.ContractEngine(watch,cfg)
        a.on_bar_close(bars,900,1000);b.on_bar_close(bars,oi_now=900,oi_peak_value=1000)
        check('armed-state',str(a.state),b.state.value)
        forming=Bar(base+20*300000,100,price,100,price,volume,oi)
        tick=TickView(forming.ts_ms+60000,forming.ts_ms+60000,price,10000,oi)
        sa=a.on_tick(tick,forming,bars,oi,1000)
        sb=b.breakout(bars,forming,price,102,[],tick_ts_ms=tick.ts_ms,oi_now=oi,oi_peak_value=1000)
        check(f'tick:{oi}:{price}:{volume}:emits',sa is not None,sb is not None)
        if sa and sb:
            for field,our in [('ltp','ltp'),('oi_drop_pct','oi_drop_pct'),('vol_ratio','vol_ratio'),('ts_ms','at_ms')]:
                check(f'tick:{field}',getattr(sa,field),getattr(sb,our))
        counts['tick_paths']+=1
    for side in ('PE','CE'):
        for armed in (False,True):
            for close in (98,100,102):
                check(f'stock-stop:{side}:{armed}:{close}',rr.d68_step(side,100,close,armed),d68_action(side,100,close,armed))
                counts['stock_stop_paths']+=1
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [root/'VERSION',root/'config/strategy.yaml',root/'gammad/watch/engine.py',root/'gammad/core/zones.py',root/'gammad/core/chain_select.py']}
    result={'reference_version':(root/'VERSION').read_text().strip(),'counts':counts,'failures':failures,'source_sha256':hashes,'scope':'Synthetic identical-input pure rules, not live broker/historical parity. HOI-2 intentionally uses later requested update.'}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2,allow_nan=False))
    print(json.dumps({'counts':counts,'failure_count':len(failures),'failure_sample':failures[:5]}))
    return bool(failures)

if __name__ == '__main__':
    raise SystemExit(main())
