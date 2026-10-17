"""Read-only links between strategy artifacts and canonical order observations."""
from dataclasses import asdict

from app.services.kite_engine import order_journal
from app.services.simulation.identity import sim_uid, sim_account_id
from . import repository as repo


def campaign_trace(uid: str, campaign_id: str) -> dict:
    campaign = repo.get(uid, "campaign", campaign_id)
    gaps = []

    def linked(kind, ident):
        if not ident:
            gaps.append(f"{kind.upper()}_LINK_MISSING")
            return None
        try:
            return repo.get(uid, kind, ident)
        except repo.NotFound:
            gaps.append(f"{kind.upper()}_RECORD_MISSING")
            return None

    plan = linked("plan", campaign.get("plan_id"))
    signal = linked("signal", plan.get("signal_id") if plan else None)
    dataset = linked("dataset", signal.get("dataset_id") if signal else None)
    actions = []
    # Paginate before filtering so old campaigns retain their complete action chain.
    cursor = 0
    while True:
        page = repo.list_documents(uid, "outbox", limit=2000, cursor=cursor)
        for row in page:
            if row.get("campaign_id") != campaign_id:
                continue
            intent = order_journal.get(row["intent_key"]) if row.get("intent_key") else None
            if intent and (intent.uid != sim_uid(uid) or intent.account_id != sim_account_id(uid)
                           or intent.strategy_id != "ema_trend"
                           or intent.signal_id != row.get("action", {}).get("id")):
                gaps.append("CANONICAL_IDENTITY_MISMATCH")
                intent = None  # Never expose an unowned canonical record.
            if row.get("intent_key") and intent is None:
                gaps.append("CANONICAL_ORDER_RECORD_MISSING")
            if intent and (intent.reconciliation_required or intent.projection_pending):
                gaps.append("CANONICAL_RECONCILIATION_PENDING")
            if intent and intent.state in order_journal.UNRESOLVED:
                gaps.append("ORDER_SETTLEMENT_UNRESOLVED")
            if intent and int(row.get("projected_quantity", 0)) != intent.filled_quantity:
                gaps.append("FILL_PROJECTION_MISMATCH")
            if intent and row.get("order_id") and row["order_id"] != intent.order_id:
                gaps.append("ORDER_ID_MISMATCH")
            actions.append({"outbox": row, "canonical_order": asdict(intent) if intent else None,
                            "confirmed_quantity": intent.filled_quantity if intent else None,
                            "confirmed_value": intent.filled_value if intent else None})
        if len(page) < 2000:
            break
        cursor += len(page)
    actions.sort(key=lambda item: (item["outbox"].get("created_ms", 0), item["outbox"]["id"]))
    if not actions:
        gaps.append("ACTION_CHAIN_MISSING")
    return {"schema_version": 1, "read_only": True, "generated_ms": repo.now_ms(),
            "execution_mode": "PAPER", "market_data_source": "SEE_DATASET_MANIFEST",
            "protection": "PAPER_MONITOR_ONLY", "live_eligible": False,
            "campaign": campaign, "plan": plan, "signal": signal, "dataset": dataset,
            "actions": actions, "gaps": sorted(set(gaps)),
            "status": "INCOMPLETE" if gaps else "LINKED",
            "limitations": ["CANONICAL_QUANTITIES_ARE_CUMULATIVE_PER_ORDER",
                            "RECORDS_OBSERVED_SEQUENTIALLY_DURING_RECONCILIATION",
                            "LINKED_DOES_NOT_CERTIFY_LIVE_READINESS_OR_PROFITABILITY"]}
