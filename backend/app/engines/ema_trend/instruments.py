"""Exact-contract execution policy. Prices are always the selected contract's."""
from datetime import date
from decimal import Decimal


def contract(meta: dict, mode: str) -> dict:
    exchange=str(meta.get("exchange","")).upper()
    kind=str(meta.get("instrument_type","")).upper()
    raw_lot=meta.get("lot_size")
    if isinstance(raw_lot,bool) or not str(raw_lot or "").isdigit():
        raise ValueError("INVALID_CONTRACT_QUANTITY_STEP")
    lot=int(raw_lot)
    tick=Decimal(str(meta.get("tick_size") or 0))
    expiry=str(meta.get("expiry") or "")[:10]
    segment=str(meta.get("segment") or "").upper()
    if exchange=="MCX":
        raise ValueError("MCX_MULTIPLIER_CALENDAR_ACCOUNTING_NOT_IMPLEMENTED")
    if "INDICES" in segment:
        raise ValueError("INDEX_REQUIRES_A_TRADABLE_FUTURE_OR_OPTION_CONTRACT")
    equity=exchange in {"NSE","BSE"} and kind=="EQ" and not expiry
    derivative=exchange in {"NFO","BFO"} and kind in {"FUT","CE","PE"} and bool(expiry)
    if not equity and not derivative:
        raise ValueError("UNSUPPORTED_OR_NONTRADABLE_INSTRUMENT")
    if lot<1 or not tick.is_finite() or tick<=0 or (equity and lot!=1):
        raise ValueError("INVALID_CONTRACT_QUANTITY_OR_TICK")
    if derivative:
        date.fromisoformat(expiry)
    return {"instrument_token":int(meta.get("instrument_token") or 0),"exchange":exchange,"tradingsymbol":str(meta.get("tradingsymbol",meta.get("symbol",""))),
            "name":str(meta.get("name") or ""),"strike":meta.get("strike"),
            "instrument_type":kind,"lot_size":lot,"tick_size":str(tick),"expiry":expiry,
            "execution_vehicle":"EQUITY" if equity else "FUTURE" if kind=="FUT" else "OPTION",
            "product":"MIS" if mode!="swing" else "CNC" if equity else "NRML",
            "multiplier":1,"capital_model":"FULL_NOTIONAL_RESERVE" if kind=="FUT" else "CASH_OR_PREMIUM",
            "signal_source":"SELECTED_CONTRACT_OHLC","allows_short":False}


def entry_expiry_block(info: dict, day: date) -> bool:
    return bool(info.get("expiry") and day>=date.fromisoformat(info["expiry"]))
