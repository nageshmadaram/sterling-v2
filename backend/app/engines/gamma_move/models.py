"""Gamma-Unwind client signal and durable execution models."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Literal, Optional

Side = Literal["PE", "CE"]
Tier = Literal["ENTRY", "STRONG_WATCHLIST", "WATCHLIST"]
TIER_ENTRY: Tier = "ENTRY"
TIER_STRONG_WATCHLIST: Tier = "STRONG_WATCHLIST"
TIER_WATCHLIST: Tier = "WATCHLIST"
T1_PCT = 0.20
T2_PCT = 0.40
MIN_TICK = 0.01


def q2(value: float) -> float:
    return round(float(value), 2)


def align_to_tick(price: float, tick: float, *, side: str = "buy") -> float:
    """Snap to the contract tick: BUY rounds down, SELL/exit rounds up.

    Decimal arithmetic is required. ``102.15 / 0.05`` is 2042.9999999999998 in
    binary floating point, so a float floor() moved ~35% of already-aligned
    prices one tick away (a BUY limit one tick under the E1 print).
    """
    t = Decimal(repr(float(tick or 0.05) or 0.05))
    raw = Decimal(repr(float(price))) / t
    rounding = ROUND_CEILING if str(side).lower() in ("sell", "exit") else ROUND_FLOOR
    return q2(float(raw.to_integral_value(rounding=rounding) * t))


def target_price(entry: float, pct: float, tick: float | None) -> float:
    """Reference target rule: entry*(1+pct), rounded UP to the contract tick."""
    t = Decimal(repr(float(tick or MIN_TICK)))
    raw = Decimal(repr(float(entry))) * (1 + Decimal(repr(float(pct))))
    return float((raw / t).to_integral_value(rounding=ROUND_CEILING) * t)


@dataclass(frozen=True)
class Candle:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    oi: float = 0.0


@dataclass(frozen=True)
class InstrumentRef:
    instrument_id: str
    tradingsymbol: str
    option_type: Side
    strike: float
    expiry: str
    lot_size: int = 1
    tick_size: float = 0.05
    exchange: str = "NFO"


@dataclass(frozen=True)
class WatchContract:
    underlying: str
    underlying_token: int
    list_name: str
    instrument: InstrumentRef
    tag: str
    oi: int
    candidates_n: int
    ref_price: float
    zone_edge: float
    atr_pct: float | None
    build_oi: float | None = None
    chain_ltp: float | None = None
    dte: int | None = None
    carried: bool = False


@dataclass(frozen=True)
class Evaluation:
    ts_ms: int
    oi_drop: float | None = None
    a: bool = False
    strong: bool = False
    sma: float | None = None
    vol_ratio: float | None = None
    b_count: int = 0
    b: bool = False
    atr: float | None = None
    range: float | None = None
    drift: float | None = None
    c: bool = False
    consol_lo: float | None = None
    consol_hi: float | None = None
    h: bool = False
    zero_vol_slots: int | None = None
    h_tick: float | None = None

    def as_dict(self) -> dict:
        return {
            "ts_ms": self.ts_ms,
            "oi_drop_pct": None if self.oi_drop is None else self.oi_drop * 100.0,
            "a": self.a, "strong": self.strong, "sma": self.sma,
            "vol_ratio": self.vol_ratio, "b_count": self.b_count, "b": self.b,
            "atr": self.atr, "range": self.range, "drift": self.drift, "c": self.c,
            "consol_lo": self.consol_lo, "consol_hi": self.consol_hi,
            "h": self.h, "zero_vol_slots": self.zero_vol_slots,
            "h_tick": self.h_tick,
        }


@dataclass(frozen=True)
class GateContext:
    zone_gap_pct: float | None
    zone_limit_pct: float
    avg_vol_lots: float | None
    zero_vol_bars: int | None
    f1: bool | None
    f2: bool | None
    unwind: bool | None
    gate_reasons: tuple[str, ...]
    gate_unknown: tuple[str, ...]
    passes: tuple[str, ...]
    quadrant: str | None

    @property
    def tier(self) -> Tier:
        n = len(self.passes)
        if n >= 4:
            return TIER_ENTRY
        if n == 3:
            return TIER_STRONG_WATCHLIST
        return TIER_WATCHLIST

    def as_dict(self) -> dict:
        return {
            "zone_gap_pct": self.zone_gap_pct,
            "zone_limit_pct": self.zone_limit_pct,
            "avg_vol_lots": self.avg_vol_lots,
            "zero_vol_bars": self.zero_vol_bars,
            "f1": self.f1, "f2": self.f2, "unwind": self.unwind,
            "gate_reasons": list(self.gate_reasons),
            "gate_unknown": list(self.gate_unknown),
            "passes": list(self.passes),
            "tier": self.tier,
            "quadrant": self.quadrant,
        }


@dataclass(frozen=True)
class GammaSignal:
    id: str
    at_ms: int
    contract: WatchContract
    ltp: float
    oi_drop_pct: float
    vol_ratio: float
    strong: bool
    base_low: float
    base_high: float
    reason: str
    snapshot: dict[str, Any]
    gates: GateContext
    tier: Tier
    warnings: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        return "armed" if self.tier == TIER_ENTRY else "watching"

    @property
    def entry(self) -> float:
        return self.ltp

    @property
    def stop(self) -> float:
        return self.base_low

    def as_dict(self) -> dict:
        c, i = self.contract, self.contract.instrument
        return {
            "id": self.id, "state": self.state, "tier": self.tier,
            "at_ms": self.at_ms, "underlying": c.underlying,
            "reason": self.reason, "warnings": list(self.warnings),
            "list_name": c.list_name, "tag": c.tag, "strong": self.strong,
            "instrument": {
                "instrument_id": i.instrument_id,
                "tradingsymbol": i.tradingsymbol,
                "option_type": i.option_type,
                "strike": i.strike,
                "expiry": i.expiry,
                "lot_size": i.lot_size,
                "tick_size": i.tick_size,
                "exchange": i.exchange,
            },
            "watch": {
                "underlying_token": c.underlying_token,
                "ref_price": c.ref_price,
                "zone_edge": c.zone_edge,
                "atr_pct": c.atr_pct,
                "oi": c.oi,
                "candidates_n": c.candidates_n,
                "dte": c.dte,
                "build_oi": c.build_oi,
                "chain_ltp": c.chain_ltp,
                "carried": c.carried,
            },
            "metrics": {
                "oi_drop_pct": self.oi_drop_pct,
                "vol_ratio": self.vol_ratio,
                "base_low": self.base_low,
                "base_high": self.base_high,
            },
            "gates": self.gates.as_dict(),
            "levels": {
                "ltp": self.ltp,
                "entry": self.entry,
                # D125: only DTE<=1 has a premium base-low stop at entry.
                # Swing uses D68 underlying daily-close logic until T1.
                "stop": self.base_low if c.dte is not None and c.dte <= 1 else None,
                "t1": target_price(self.entry, T1_PCT, i.tick_size),
                "t2": target_price(self.entry, T2_PCT, i.tick_size),
            },
            "snapshot": dict(self.snapshot),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GammaSignal":
        inst_d = d.get("instrument") or {}
        w = d.get("watch") or {}
        inst = InstrumentRef(
            instrument_id=str(inst_d.get("instrument_id") or ""),
            tradingsymbol=str(inst_d.get("tradingsymbol") or ""),
            option_type=str(inst_d.get("option_type") or "CE"),
            strike=float(inst_d.get("strike") or 0.0),
            expiry=str(inst_d.get("expiry") or "")[:10],
            lot_size=int(inst_d.get("lot_size") or 1),
            tick_size=float(inst_d.get("tick_size") or 0.05),
            exchange=str(inst_d.get("exchange") or "NFO"),
        )
        contract = WatchContract(
            underlying=str(d.get("underlying") or ""),
            underlying_token=int(w.get("underlying_token") or 0),
            list_name=str(d.get("list_name") or ""),
            instrument=inst,
            tag=str(d.get("tag") or "HOI-1"),
            oi=int(w.get("oi") or 0),
            candidates_n=int(w.get("candidates_n") or 0),
            ref_price=float(w.get("ref_price") or 0.0),
            zone_edge=float(w.get("zone_edge") or 0.0),
            atr_pct=w.get("atr_pct"),
            build_oi=w.get("build_oi"),
            chain_ltp=w.get("chain_ltp"),
            dte=w.get("dte"),
            carried=bool(w.get("carried")),
        )
        g = d.get("gates") or {}
        gates = GateContext(
            zone_gap_pct=g.get("zone_gap_pct"),
            zone_limit_pct=float(g.get("zone_limit_pct") or 0.0),
            avg_vol_lots=g.get("avg_vol_lots"),
            zero_vol_bars=g.get("zero_vol_bars"),
            f1=g.get("f1"), f2=g.get("f2"), unwind=g.get("unwind"),
            gate_reasons=tuple(g.get("gate_reasons") or ()),
            gate_unknown=tuple(g.get("gate_unknown") or ()),
            passes=tuple(g.get("passes") or ()),
            quadrant=g.get("quadrant"),
        )
        m = d.get("metrics") or {}
        lv = d.get("levels") or {}
        return cls(
            id=str(d.get("id") or ""),
            at_ms=int(d.get("at_ms") or 0),
            contract=contract,
            ltp=float(lv.get("ltp") or lv.get("entry") or 0.0),
            oi_drop_pct=float(m.get("oi_drop_pct") or 0.0),
            vol_ratio=float(m.get("vol_ratio") or 0.0),
            strong=bool(d.get("strong")),
            base_low=float(m.get("base_low") or lv.get("stop") or 0.0),
            base_high=float(m.get("base_high") or lv.get("entry") or 0.0),
            reason=str(d.get("reason") or ""),
            snapshot=dict(d.get("snapshot") or {}),
            gates=gates,
            tier=str(d.get("tier") or gates.tier),
            warnings=tuple(d.get("warnings") or ()),
        )


@dataclass
class PositionState:
    signal_id: str
    instrument: InstrumentRef
    list_name: str
    tier: str
    underlying: str
    underlying_token: int
    entry: float
    quantity: int
    lots: int
    entered_ms: int
    entry_day: str
    hold: str
    zone_edge: float
    base_low: float
    t1: float
    t2: float
    stop: float = 0.0
    trade_state: str = "OPEN"
    swing_armed: bool = False
    sessions_held: int = 0
    exiting: bool = False
    exit_order_id: str = ""
    exit_requested_qty: int = 0
    exit_requested_price: float = 0.0
    exit_pending_reason: str = ""
    partial_realised_inr: float = 0.0
    status: str = "pending"
    order_id: str = ""
    fill_price: float = 0.0
    gtt_id: int = 0
    gtt_at: float = 0.0
    gtt_qty: int = 0
    stop_mode: str = "monitor"
    idempotency_key: str = ""
    account_id: str = ""
    exit_reason: str = ""
    high_water: float = 0.0
    # Last broker exit order whose cash was booked into the daily-loss
    # accounting (idempotency across crash/retry), and a flag when it could not
    # be booked at all.
    exit_booked_order_id: str = ""
    pnl_reconciliation_required: bool = False
    product: str = "NRML"

    @property
    def effective_entry(self) -> float:
        return self.fill_price if self.fill_price > 0 else self.entry

    @property
    def target(self) -> float:
        return self.t2

    @property
    def trail(self) -> Optional[float]:
        return self.stop if self.trade_state == "T1_TRAIL" and self.stop > 0 else None

    @property
    def is_open(self) -> bool:
        return self.status in ("pending", "open") and self.trade_state in ("OPEN", "T1_TRAIL")

    def __post_init__(self) -> None:
        if self.product not in {"MIS", "NRML"}:
            raise ValueError("gamma_position_product_unknown")
        if self.high_water <= 0:
            self.high_water = self.effective_entry

    @classmethod
    def from_signal(cls, signal: GammaSignal, *, fill: float, quantity: int,
                    lots: int, at_ms: int, day: str) -> "PositionState":
        inst = signal.contract.instrument
        hold = "intraday" if signal.contract.dte is not None and signal.contract.dte <= 1 else "swing"
        entry = q2(fill)
        return cls(
            signal_id=signal.id,
            instrument=inst,
            list_name=signal.contract.list_name,
            tier=signal.tier,
            underlying=signal.contract.underlying,
            underlying_token=int(signal.contract.underlying_token or 0),
            entry=entry,
            quantity=int(quantity),
            lots=int(lots),
            entered_ms=int(at_ms),
            entry_day=str(day),
            hold=hold,
            zone_edge=float(signal.contract.zone_edge),
            base_low=float(signal.base_low),
            t1=target_price(entry, T1_PCT, inst.tick_size),
            t2=target_price(entry, T2_PCT, inst.tick_size),
            # Reference D125: DTE<=1 gets base-low premium SL. Swing has no
            # premium SL before T1 and is governed by D68 daily closes.
            stop=q2(signal.base_low) if hold == "intraday" else 0.0,
        )


@dataclass
class TradeRecord:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    realised_inr: float = 0.0
    day_realised_inr: float = 0.0
    day: str = ""
    history: list = field(default_factory=list)

    def record(
        self,
        pnl_inr: float,
        day: str,
        *,
        outcome_pnl: float | None = None,
        **_: object,
    ) -> None:
        """Book final-leg cash and classify the whole completed trade."""
        if day != self.day:
            self.day, self.day_realised_inr = day, 0.0
        classification = float(
            pnl_inr if outcome_pnl is None else outcome_pnl
        )
        self.trades += 1
        # Earlier partial cash was already booked by add_realised().
        self.realised_inr += float(pnl_inr)
        self.day_realised_inr += float(pnl_inr)
        if classification >= 0:
            self.wins += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
        self.history.append({
            "pnl_inr": q2(pnl_inr),
            "outcome_pnl_inr": q2(classification),
            "day": day,
        })

    def add_realised(self, pnl_inr: float, day: str) -> None:
        if day != self.day:
            self.day, self.day_realised_inr = day, 0.0
        self.realised_inr += pnl_inr
        self.day_realised_inr += pnl_inr
        self.history.append({"pnl_inr": q2(pnl_inr), "day": day, "partial": True})

    def as_dict(self) -> dict:
        return {
            "trades": self.trades, "wins": self.wins, "losses": self.losses,
            "win_rate": q2(100.0 * self.wins / self.trades) if self.trades else None,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins": self.consecutive_wins,
            "descaled": False, "descale_step": 0,
            "realised_inr": q2(self.realised_inr),
            "day_realised_inr": q2(self.day_realised_inr),
            "day": self.day, "history": list(self.history),
            "verdict": "no realised trades yet" if not self.trades else f"{self.wins}/{self.trades} winners",
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "TradeRecord":
        if not d:
            return cls()
        rec = cls()
        for name in ("trades", "wins", "losses", "consecutive_losses", "consecutive_wins"):
            try:
                setattr(rec, name, int(d.get(name) or 0))
            except (TypeError, ValueError):
                pass
        for name in ("realised_inr", "day_realised_inr"):
            try:
                setattr(rec, name, float(d.get(name) or 0.0))
            except (TypeError, ValueError):
                pass
        rec.day = str(d.get("day") or "")
        rec.history = list(d.get("history") or []) if isinstance(d.get("history"), list) else []
        return rec
