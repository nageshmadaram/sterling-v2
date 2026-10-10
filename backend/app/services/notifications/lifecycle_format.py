"""One wording, one number format, one layout for every Telegram trade alert.

SuperTrend, Snapback and Gamma Unwind all emit lifecycle alerts through the same
durable outbox (``telegram_lifecycle``).  Before this module each call-site
phrased its own detail string ("Fill" vs "Avg" vs "Entry", ``₹1234.5`` vs
``₹1,234.50``, ``₹-50.00`` for a loss) and the renderer pasted them verbatim, so
an operator had to learn three dialects while real money was at risk.

This module is the single place that decides:

* the strategy display names (``SuperTrend`` / ``Snapback`` / ``Gamma Unwind``),
* the field vocabulary and order (Entry, LTP, SL, TSL, Target, Exit, P&L, Qty,
  Capital) and the rupee format (``₹1,234.50``; signed P&L ``-₹50.00``),
* IST timestamps,
* HTML escaping and the Telegram 4096-character limit,
* secret redaction (bot tokens, broker keys) so no alert can leak a credential,
* the instrument side line (``CE``/``PE``/``FUT`` and ``BUY``/``SELL``).

Existing producers keep emitting their historical strings; ``canonicalize_detail``
maps every known dialect onto the canonical vocabulary at render time (so events
already sitting in the outbox before an upgrade are normalised too).  New
producers should use :func:`detail` to build the string in canonical form.
"""
from __future__ import annotations

import html as _html
import math
import re
import time
from datetime import datetime, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from app.services.simulation import identity as _sim

IST = ZoneInfo("Asia/Kolkata")

#: Leads every alert that comes from the virtual Simulation tenant. It is added AFTER the
#: house wording rules (which turn the word into "test" for live alerts) so it survives them.
SIM_BANNER = "🧪 SIMULATION"

#: Telegram rejects sendMessage text longer than 4096 characters (after entity
#: parsing).  Stay well inside it so a late-added footer can never tip over.
TELEGRAM_MAX_CHARS = 4096
_BUDGET_TITLE = 300
_BUDGET_DETAIL = 2600
_BUDGET_HEADER = 300
_BUDGET_FOOTER = 200

_SEP = " · "

# ── strategy names ────────────────────────────────────────────────────────────
_STRATEGY_LABELS = {
    "supertrend": "SuperTrend",
    "super trend": "SuperTrend",
    "super-trend": "SuperTrend",
    "kite_supertrend": "SuperTrend",
    "sterling_kite_engine": "SuperTrend",
    "sterling-kite": "SuperTrend",
    "sterling_kite": "SuperTrend",
    "snapback": "Snapback",
    "gamma": "Gamma Unwind",
    "gamma_move": "Gamma Unwind",
    "gamma-unwind": "Gamma Unwind",
    "gamma unwind": "Gamma Unwind",
    "gamma_unwind": "Gamma Unwind",
    "gammaunwind": "Gamma Unwind",
}


def strategy_label(name: object) -> str:
    """Canonical display name; unknown names pass through unchanged."""
    raw = str(name or "").strip()
    if not raw:
        return "Sterling"
    return _STRATEGY_LABELS.get(raw.lower(), raw)


def is_gamma(name: object) -> bool:
    return strategy_label(name) == "Gamma Unwind"


# ── time ──────────────────────────────────────────────────────────────────────
def ist_stamp(ms: object) -> str:
    """``05 Oct 10:12:03`` in Asia/Kolkata for an epoch-millisecond value."""
    try:
        value = int(ms or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    try:
        return (
            datetime.fromtimestamp(value / 1000.0, timezone.utc)
            .astimezone(IST)
            .strftime("%d %b %H:%M:%S")
        )
    except (OverflowError, OSError, ValueError):
        return ""


# ── numbers ───────────────────────────────────────────────────────────────────
def _finite(value: object) -> Optional[float]:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def inr(value: object, *, signed: bool = False, dp: int = 2) -> str:
    """``₹1,234.50`` / signed ``+₹1,234.50`` / ``-₹50.00``; unknown is an em dash."""
    number = _finite(value)
    if number is None:
        return "—"
    if round(number, dp) == 0:
        number = 0.0
    sign = ""
    if number < 0:
        sign = "-"
    elif signed:
        sign = "+"
    return f"{sign}₹{abs(number):,.{dp}f}"


def qty_text(done: object, total: object = None) -> str:
    try:
        d = int(done)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return "Qty —"
    if total is not None:
        try:
            t = int(total)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            t = None
        if t is not None and t != d:
            return f"Qty {d:,}/{t:,}"
    return f"Qty {d:,}"


# ── canonical detail builder (for new producers) ─────────────────────────────
def detail(
    *,
    entry: object = None,
    ltp: object = None,
    sl: object = None,
    tsl: object = None,
    tsl_from: object = None,
    targets: Iterable[tuple[str, object]] | None = None,
    exit: object = None,  # noqa: A002 - field name is the public vocabulary
    pnl: object = None,
    qty: object = None,
    qty_total: object = None,
    capital: object = None,
    reason: str = "",
    notes: Iterable[str] = (),
) -> str:
    """Build a detail string in the canonical field order.

    Order: Entry, LTP, SL, TSL, Target(s), Exit, P&L, Qty, Capital, Reason, notes.
    Fields whose value is ``None`` are omitted rather than printed as zero.
    """
    parts: list[str] = []
    if entry is not None:
        parts.append(f"Entry {inr(entry)}")
    if ltp is not None:
        parts.append(f"LTP {inr(ltp)}")
    if sl is not None:
        parts.append(f"SL {inr(sl)}")
    if tsl is not None:
        if tsl_from is not None:
            parts.append(f"TSL {inr(tsl_from)} → {inr(tsl)}")
        else:
            parts.append(f"TSL {inr(tsl)}")
    for label, value in targets or ():
        if value is not None:
            parts.append(f"{label} {inr(value)}")
    if exit is not None:
        parts.append(f"Exit {inr(exit)}")
    if pnl is not None:
        parts.append(f"P&L {inr(pnl, signed=True)}")
    if qty is not None:
        parts.append(qty_text(qty, qty_total))
    if capital is not None:
        parts.append(f"Capital {inr(capital)}")
    if reason:
        parts.append(f"Reason {reason}")
    parts.extend(str(n) for n in notes if str(n or "").strip())
    return _SEP.join(parts)


# ── canonicalisation of historical dialects ──────────────────────────────────
_NUM = r"-?[\d,]*\.?\d+"
_RUPEE = rf"(?:₹\s*)?({_NUM})"

_ENTRY_EVENT = re.compile(r"ENTRY|SIGNAL", re.I)
_EXIT_EVENT = re.compile(r"CLOSED|EXIT|TARGET|STOPPED|SQUARE", re.I)

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"^Qty\s+(\d[\d,]*)(?:/(\d[\d,]*))?$", re.I), "qty"),
    (re.compile(rf"^(?:Exit\s+)?filled\s+(\d[\d,]*)/(\d[\d,]*)$", re.I), "qty_frac"),
    (re.compile(rf"^Sold\s+(\d[\d,]*)/(\d[\d,]*)\s*@\s*{_RUPEE}$", re.I), "sold"),
    (re.compile(rf"^(?:Fill|Entry|Avg(?:\s+price)?)\s+{_RUPEE}$", re.I), "entry_or_avg"),
    (re.compile(rf"^Exit\s+{_RUPEE}$", re.I), "exit"),
    (re.compile(rf"^(?:LTP|Last|Mark)\s+{_RUPEE}$", re.I), "ltp"),
    (re.compile(rf"^(?:required\s+)?SL\s+{_RUPEE}$", re.I), "sl"),
    (re.compile(rf"^(?:(?:TSL|SL)\s+)?{_RUPEE}\s*(?:→|->)\s*{_RUPEE}$", re.I), "trail"),
    (re.compile(rf"^TSL\s+{_RUPEE}$", re.I), "tsl"),
    (re.compile(rf"^TSL moved to entry\s+{_RUPEE}$", re.I), "tsl"),
    (re.compile(rf"^(T[12])\s+{_RUPEE}(?:\s+reached)?$", re.I), "target"),
    (re.compile(rf"^Target\s+{_RUPEE}$", re.I), "target_plain"),
    (re.compile(rf"^P&L\s+([+-]?)(?:₹\s*)?([+-]?{_NUM})$", re.I), "pnl"),
    (re.compile(rf"^Capital\s+{_RUPEE}$", re.I), "capital"),
    (re.compile(r"^(\d[\d,]*)\s+remains?$", re.I), "remaining"),
    (re.compile(r"^(?:Broker\s+)?GTT\s+#(\d+)$", re.I), "gtt"),
    (re.compile(r"^Reason\s+(.+)$", re.I), "reason"),
]


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def canonicalize_detail(event_type: str, text: str) -> str:
    """Rewrite a historical free-text detail onto the canonical vocabulary.

    Tokens the rules do not recognise are kept verbatim (as ``Reason`` on an exit
    event, otherwise as trailing notes) so no information is ever dropped.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    event = str(event_type or "")
    is_exit = bool(_EXIT_EVENT.search(event)) and not _ENTRY_EVENT.search(event)

    fields: dict[str, str] = {}
    targets: dict[str, str] = {}
    notes: list[str] = []

    def put(key: str, value: str) -> None:
        fields.setdefault(key, value)

    for token in [t.strip() for t in re.split(r"\s+·\s+|\s*·\s*", raw) if t.strip()]:
        for pattern, kind in _RULES:
            m = pattern.match(token)
            if not m:
                continue
            if kind == "qty":
                done, total = m.group(1), m.group(2)
                put("qty", qty_text(int(done.replace(",", "")), int(total.replace(",", "")) if total else None))
            elif kind == "qty_frac":
                put("qty", qty_text(int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))))
            elif kind == "sold":
                put("qty", qty_text(int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))))
                put("exit", f"Exit {inr(_num(m.group(3)))}")
            elif kind == "entry_or_avg":
                label = token.split()[0].lower()
                value = inr(_num(m.group(1)))
                if is_exit and label.startswith("avg"):
                    put("exit", f"Exit {value}")
                else:
                    put("entry", f"Entry {value}")
            elif kind == "exit":
                put("exit", f"Exit {inr(_num(m.group(1)))}")
            elif kind == "ltp":
                put("ltp", f"LTP {inr(_num(m.group(1)))}")
            elif kind == "sl":
                put("sl", f"SL {inr(_num(m.group(1)))}")
            elif kind == "trail":
                put("tsl", f"TSL {inr(_num(m.group(1)))} → {inr(_num(m.group(2)))}")
            elif kind == "tsl":
                put("tsl", f"TSL {inr(_num(m.group(1)))}")
            elif kind == "target":
                targets.setdefault(m.group(1).upper(), f"{m.group(1).upper()} {inr(_num(m.group(2)))}")
            elif kind == "target_plain":
                targets.setdefault("T", f"Target {inr(_num(m.group(1)))}")
            elif kind == "pnl":
                value = _num(m.group(2))
                if m.group(1) == "-":
                    value = -abs(value)
                put("pnl", f"P&L {inr(value, signed=True)}")
            elif kind == "capital":
                put("capital", f"Capital {inr(_num(m.group(1)))}")
            elif kind == "remaining":
                put("remaining", f"Remaining {m.group(1)}")
            elif kind == "gtt":
                put("gtt", f"GTT #{m.group(1)}")
            elif kind == "reason":
                put("reason", f"Reason {m.group(1)}")
            break
        else:
            notes.append(token)

    ordered = [fields[k] for k in ("entry", "ltp", "sl", "tsl") if k in fields]
    ordered += [targets[k] for k in sorted(targets)]
    ordered += [fields[k] for k in ("exit", "pnl", "qty", "remaining", "capital", "gtt") if k in fields]
    if "reason" in fields:
        ordered.append(fields["reason"])
        ordered.extend(notes)
    elif notes:
        if is_exit:
            ordered.append(f"Reason {notes[0]}")
            ordered.extend(notes[1:])
        else:
            ordered.extend(notes)
    return _SEP.join(ordered)


# ── instrument side ──────────────────────────────────────────────────────────
_OPT_RE = re.compile(r"(CE|PE)$")


def instrument_kind(symbol: object) -> str:
    """``CE`` / ``PE`` / ``FUT`` read from a tradingsymbol, else empty."""
    s = str(symbol or "").strip().upper()
    if ":" in s:
        s = s.rsplit(":", 1)[-1]
    if s.endswith("FUT"):
        return "FUT"
    m = _OPT_RE.search(s)
    if m and any(ch.isdigit() for ch in s):
        return m.group(1)
    return ""


def action_side(event_type: str, symbol: object) -> str:
    """BUY/SELL for the transaction the event describes, when it is knowable.

    Sterling's option strategies are long-option: entries BUY the CE/PE and every
    exit or stop SELLs it.  Futures can be either direction, so no side is
    asserted for them unless the producer says so in the detail text.
    """
    if instrument_kind(symbol) not in {"CE", "PE"}:
        return ""
    event = str(event_type or "").upper()
    if "ENTRY" in event or event.startswith("SIGNAL"):
        return "BUY"
    if _EXIT_EVENT.search(event):
        return "SELL"
    return ""


# ── safety: secrets and forbidden wording ────────────────────────────────────
def redact(text: object) -> str:
    """Strip bot tokens, broker keys and bearer tokens (shared with the log filter)."""
    from app.core.logging import redact_secrets

    return redact_secrets(text)


_FORBIDDEN_WORDS = re.compile(r"(?i)\b(?:paper|simulat\w*)\b")


def neutral_wording(text: object) -> str:
    """Operator alerts describe real trading; never say paper or simulation."""
    return _FORBIDDEN_WORDS.sub("test", str(text or ""))


def mode_text(raw: object) -> str:
    """Footer account-mode label with the forbidden vocabulary removed."""
    value = str(raw or "").strip()
    if not value:
        return ""
    if value.upper() == "LIVE":
        return "LIVE"
    if value.lower() == "no account":
        return "NO ACCOUNT"
    return "TEST" if _FORBIDDEN_WORDS.search(value) else value.upper()


# ── html ──────────────────────────────────────────────────────────────────────
_LIVE_WORD = re.compile(r"(?i)\blive\b")


def sim_wording(text: object) -> str:
    """A Simulation alert never claims to be live (nor does it hide that it is simulated)."""
    return _LIVE_WORD.sub("virtual", redact(text))


def clean(text: object, *, simulated: bool = False) -> str:
    return sim_wording(text) if simulated else neutral_wording(redact(text))


def esc(text: object) -> str:
    return _html.escape(clean(text), quote=False)


def esc_clip(text: object, limit: int, *, simulated: bool = False) -> str:
    """Escape ``text`` and cut it so the *escaped* length fits ``limit``."""
    raw = clean(text, simulated=simulated).strip()
    out = _html.escape(raw, quote=False)
    if len(out) <= limit:
        return out
    while raw and len(_html.escape(raw, quote=False)) + 1 > limit:
        raw = raw[: max(0, int(len(raw) * 0.85) - 1)]
    return _html.escape(raw.rstrip(), quote=False) + "…"


_TAG_RE = re.compile(r"<[^>]+>")


def to_plain(markup: str) -> str:
    """Strip our <b>/<i> markup for the parse-error fallback send."""
    return _html.unescape(_TAG_RE.sub("", markup or ""))


_SEVERITY_ICON = {"CRITICAL": "🔴", "ERROR": "🔴", "WARNING": "🟡"}


def severity_icon(severity: object) -> str:
    return _SEVERITY_ICON.get(str(severity or "INFO").upper(), "🟢")


def header(strategy: object, event: object, severity: object = "INFO", *,
           simulated: bool = False) -> str:
    label = strategy_label(strategy)
    scope = "" if label == "Sterling" else f"{esc_clip(label, 60, simulated=simulated)}{_SEP}"
    line = (
        f"{severity_icon(severity)} <b>STERLING{_SEP}{scope}"
        f"{esc_clip(str(event or 'UPDATE').upper(), 80, simulated=simulated)}</b>"
    )
    return f"{SIM_BANNER}{_SEP}{line}" if simulated else line


def footer(mode: object, at_ms: object = None, *, now_ms: int | None = None,
           simulated: bool = False) -> str:
    stamp = ist_stamp(at_ms) if at_ms else ""
    delayed = ""
    try:
        at = int(at_ms or 0)
    except (TypeError, ValueError):
        at = 0
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if at > 0 and now - at > 180_000:
        delayed = f"{_SEP}delayed {max(1, (now - at) // 60_000)}m"
    if simulated:
        rest = _SEP.join([f"{stamp} IST" if stamp else "now"]) + delayed
        return f"<i>SIMULATION{_SEP}{esc_clip(rest, _BUDGET_FOOTER)}</i>"
    parts = [p for p in (mode_text(mode), f"{stamp} IST" if stamp else "now") if p]
    return f"<i>{esc_clip(_SEP.join(parts) + delayed, _BUDGET_FOOTER)}</i>"


def render_lifecycle(
    *,
    strategy: object,
    event_type: object,
    title: object,
    detail_text: object,
    severity: object = "INFO",
    created_at_ms: object = None,
    mode: object = "",
    now_ms: int | None = None,
    uid: object = "",
) -> str:
    """Canonical HTML for one lifecycle event (always ≤ 4096 characters).

    ``uid`` is the tenant the event belongs to: an event of the virtual Simulation tenant
    is prefixed ``🧪 SIMULATION``, footed ``SIMULATION`` and never says "live".
    """
    sim = _sim.is_sim_uid(uid)
    event = str(event_type or "UPDATE")
    symbol = str(title or "").strip()
    kind = instrument_kind(symbol)
    side = action_side(event, symbol)
    heading = esc_clip(symbol, _BUDGET_TITLE, simulated=sim)
    tags = [t for t in (kind, side) if t]
    title_line = f"<b>{heading}</b>" if heading else ""
    if title_line and tags:
        title_line += _SEP + _SEP.join(tags)
    body = esc_clip(canonicalize_detail(event, str(detail_text or "")), _BUDGET_DETAIL, simulated=sim)
    lines = [
        header(strategy, event, severity, simulated=sim),
        title_line,
        body,
        footer(mode, created_at_ms, now_ms=now_ms, simulated=sim),
    ]
    text = "\n".join(line for line in lines if line)
    if len(text) > TELEGRAM_MAX_CHARS:  # pragma: no cover - budgets make this unreachable
        text = text[: TELEGRAM_MAX_CHARS - 1] + "…"
    return text


__all__ = [
    "IST",
    "TELEGRAM_MAX_CHARS",
    "action_side",
    "canonicalize_detail",
    "clean",
    "detail",
    "esc",
    "esc_clip",
    "footer",
    "header",
    "inr",
    "instrument_kind",
    "ist_stamp",
    "is_gamma",
    "mode_text",
    "neutral_wording",
    "qty_text",
    "redact",
    "render_lifecycle",
    "severity_icon",
    "sim_wording",
    "SIM_BANNER",
    "strategy_label",
    "to_plain",
]
