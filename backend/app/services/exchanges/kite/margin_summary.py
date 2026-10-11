"""Read-only broker margin presentation shared by Telegram and Mini App."""
from collections.abc import Mapping
from math import isfinite


def _number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def margin_segments(margins):
    """Keep broker net, debits and opening balance, including zero and negatives."""
    payload = margins if isinstance(margins, Mapping) else {}
    result = {}
    for name in ("equity", "commodity"):
        segment = payload.get(name)
        if not isinstance(segment, Mapping):
            segment = {}
        available = segment.get("available") or {}
        utilised = segment.get("utilised") or {}
        result[name] = {
            "available": _number(segment.get("net")),
            "used": _number(utilised.get("debits")),
            "opening_balance": _number(available.get("opening_balance")),
        }
    return result
