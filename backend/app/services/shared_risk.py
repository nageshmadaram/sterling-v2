"""Owner-selected shared risk caps; separate account/safety halts always apply."""

from __future__ import annotations
from math import isfinite
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.services import db

Strategy = Literal["snapback", "gamma_unwind", "supertrend"]
ALIASES = {
    "sterling-kite": "supertrend",
    "sterling_kite_engine": "supertrend",
    "gamma_move": "gamma_unwind",
    "gamma-unwind": "gamma_unwind",
}
KEY = "shared_risk:"


class SharedRiskConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, revalidate_instances="always"
    )
    revision: int = Field(default=0, ge=0)
    enabled: bool = Field(default=False, strict=True)
    strategies: list[Strategy] = Field(default_factory=list)
    allocation_inr: float | None = Field(default=None, gt=0, strict=True)
    max_daily_loss_inr: float | None = Field(default=None, gt=0, strict=True)
    per_trade_risk_inr: float | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def coherent(self):
        if len(self.strategies) != len(set(self.strategies)):
            raise ValueError("Select each strategy once")
        amounts = [
            self.allocation_inr,
            self.max_daily_loss_inr,
            self.per_trade_risk_inr,
        ]
        if self.enabled and (
            not self.strategies or not any(v is not None for v in amounts)
        ):
            raise ValueError(
                "Enabled shared risk needs a strategy and at least one limit"
            )
        if (
            self.allocation_inr is not None
            and self.per_trade_risk_inr is not None
            and self.per_trade_risk_inr > self.allocation_inr
        ):
            raise ValueError("Per-trade risk cannot exceed allocation")
        return self

    def applies(self, strategy):
        return (
            self.enabled
            and ALIASES.get(str(strategy).lower(), str(strategy).lower())
            in self.strategies
        )


class RevisionConflict(ValueError):
    pass


def _decode(raw):
    return SharedRiskConfig.model_validate_json(raw) if raw else SharedRiskConfig()


def get_config(uid):
    if not uid:
        raise ValueError("Authenticated user required")
    if not db.is_available():
        raise RuntimeError("Risk configuration database unavailable")
    with db._conn() as conn:
        row = conn.execute(
            "SELECT value FROM system_config WHERE key=?", (KEY + uid,)
        ).fetchone()
    return _decode(row[0] if row else None)


def save_config(uid, config):
    config = SharedRiskConfig.model_validate(config)
    if not uid or not db.is_available():
        raise RuntimeError("Risk configuration database unavailable")
    with db._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM system_config WHERE key=?", (KEY + uid,)
        ).fetchone()
        old = _decode(row[0] if row else None)
        if config.revision != old.revision:
            raise RevisionConflict("Risk settings changed; reload before saving")
        new = config.model_copy(update={"revision": old.revision + 1})
        conn.execute(
            "INSERT OR REPLACE INTO system_config(key,value) VALUES(?,?)",
            (KEY + uid, new.model_dump_json()),
        )
    return new


def selected(uid, strategy):
    cfg = get_config(uid)
    return cfg if cfg.applies(strategy) else None


def snapback_config(uid, base):
    if not uid or not db.is_available():
        return base
    from dataclasses import replace

    try:
        cfg = selected(uid, "snapback")
    except Exception:
        return base  # Entry authority still refuses; protection keeps local config.
    if not cfg:
        return base
    update = {}
    capital = cfg.allocation_inr or base.capital_inr
    if cfg.allocation_inr is not None:
        update["capital_inr"] = capital
    if cfg.per_trade_risk_inr is not None:
        update["premium_pct_of_capital"] = min(cfg.per_trade_risk_inr, capital) / capital * 100
    return replace(base, **update)


def supertrend_config(uid, base):
    if not db.is_available():
        return base
    try:
        cfg = selected(uid, "supertrend")
    except Exception:
        return base  # Do not interrupt exits when risk storage is unreadable.
    if not cfg:
        return base
    update = {}
    if cfg.per_trade_risk_inr is not None:
        update.update(risk_sizing=True, allow_min_lot_over_risk=False)
    if cfg.max_daily_loss_inr is not None:
        update["max_daily_loss_pct"] = (
            cfg.max_daily_loss_inr / cfg.allocation_inr * 100
            if cfg.allocation_inr
            else 100.0
        )
    return base.model_copy(update=update)


def sizing_inputs(uid, capital, risk_pct):
    cfg = selected(uid, "supertrend")
    if not cfg:
        return capital, risk_pct
    if cfg.allocation_inr is not None:
        capital = min(capital, cfg.allocation_inr)
    if cfg.per_trade_risk_inr is not None and capital > 0:
        risk_pct = min(100.0, cfg.per_trade_risk_inr / capital * 100)
    return capital, risk_pct


def entry_refusal(request, order_value, *, ignore_intent_key=""):
    """Called under canonical account lock, including immediately before send.

    Allocation measures the whole account's deployed and reserved capital; daily
    loss is account-wide. Only selected strategies inherit these new caps.
    Per-trade risk conservatively caps committed premium/notional, not a promised
    stop fill. Proven already-armed Snapback hedge continuation stays protective.
    """
    from app.services.live_minimum_gate import (
        _is_replay_of_placed_intent,
        _read_inventory,
        open_gross_exposure,
        _daily_loss,
    )

    if _is_replay_of_placed_intent(request):
        return None
    if str(request.strategy_id).lower() == "snapback":
        from app.services import protection_health, snapback_live_positions

        if any(
            protection_health._protected_snapback_hedge(p, request)
            for p in snapback_live_positions.open_positions(request.uid)
        ):
            return None
    cfg = selected(request.uid, request.strategy_id)
    if not cfg:
        return None
    amount = float(order_value or 0)
    if (cfg.allocation_inr is not None or cfg.per_trade_risk_inr is not None) and (
        not isfinite(amount) or amount <= 0
    ):
        return "Shared risk: committed capital is unavailable"
    if cfg.per_trade_risk_inr is not None and amount > cfg.per_trade_risk_inr:
        return "Shared per-trade risk cap exceeded"
    if cfg.allocation_inr is not None:
        gross = open_gross_exposure(_read_inventory(request))
        if gross is None or not isfinite(gross) or gross < 0:
            return "Shared allocation: account exposure is unreadable"
        from app.services.kite_engine.order_journal import UNRESOLVED

        marks = ",".join("?" for _ in UNRESOLVED)
        with db._conn() as conn:
            pending = conn.execute(
                f"SELECT COALESCE(SUM(capital_required),0) FROM kite_order_intents WHERE uid=? AND account_id=? AND intent_key<>? AND (state IN ({marks}) OR reconciliation_required=1)",
                (
                    request.uid,
                    request.account_id,
                    ignore_intent_key,
                    *sorted(UNRESOLVED),
                ),
            ).fetchone()[0]
        if not isfinite(float(pending)) or float(pending) < 0:
            return "Shared allocation: reservations are unreadable"
        if gross + float(pending) + amount > cfg.allocation_inr:
            return "Shared allocation cap exceeded"
    if cfg.max_daily_loss_inr is not None:
        loss, _ = _daily_loss(request.uid)
        if loss is None or not isfinite(loss):
            return "Shared daily loss: account loss is unreadable"
        if loss >= cfg.max_daily_loss_inr:
            return "Shared daily loss cap reached"
    return None


def bounded_quantity(uid, strategy, quantity, unit_price, lot_size):
    cfg = selected(uid, strategy)
    if not cfg:
        return quantity
    caps = [v for v in (cfg.allocation_inr, cfg.per_trade_risk_inr) if v is not None]
    if not caps:
        return quantity
    if not isfinite(unit_price) or unit_price <= 0 or lot_size <= 0:
        return 0
    maximum = int(min(caps) // (unit_price * lot_size)) * lot_size
    return min(int(quantity) // lot_size * lot_size, maximum)


def daily_loss_limit(uid, capital, local_pct):
    cfg = selected(uid, "supertrend")
    if cfg and cfg.max_daily_loss_inr is not None:
        return cfg.max_daily_loss_inr
    return capital * local_pct / 100
