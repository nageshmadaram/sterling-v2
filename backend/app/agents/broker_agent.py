"""BrokerAgent — thin facade over an exchange adapter (auth/order surface).

Delegates to the injected adapter (a TradingExchangeAdapter / BrokerProtocol);
optionally announces order outcomes on the bus. Moves no logic.
"""
from __future__ import annotations

from typing import Any, Optional

from app.bus.event_bus import EventBus
from app.domain.events import OrderAccepted


class BrokerAgent:
    def __init__(self, adapter: Any, bus: Optional[EventBus] = None) -> None:
        self.adapter = adapter
        self.bus = bus

    async def place_order(self, *, request=None, risk_approval=None, **legacy_kwargs):
        """Canonical order submission only; old unbound adapter arguments fail closed.

        The old paper-only dispatch accepted arbitrary symbol/size/side fields
        and bypassed the durable execution journal. This facade has no authority
        to infer account identity, signal generation or risk approval from them.
        """
        from app.services.execution_service import (
            CanonicalExecutionService,
            ExecutionRequest,
            RiskApproval,
        )

        if (legacy_kwargs or not isinstance(request, ExecutionRequest)
                or (risk_approval is not None and not isinstance(risk_approval, RiskApproval))):
            raise RuntimeError(
                "BrokerAgent requires a typed ExecutionRequest and optional RiskApproval; "
                "route submissions through CanonicalExecutionService"
            )
        result = await CanonicalExecutionService().submit_order(
            request, broker_client=self.adapter, risk_approval=risk_approval
        )
        if result.success and self.bus is not None:
            await self.bus.publish(OrderAccepted(order_id=str(result.order_id)))
        return result

    async def cancel_order(self, order_id: str, product_id: int) -> dict:
        from app.core.lane_registry import noncanonical_broker_write_allowed

        # Cancellation can remove a protective order. This facade has no order
        # ownership/protection proof, so live cancellation is noncanonical too.
        if not noncanonical_broker_write_allowed(self.adapter):
            raise RuntimeError(
                "BrokerAgent cannot cancel live or mode-unknown broker orders; "
                "use a canonical cancellation authority"
            )
        return await self.adapter.cancel_order(order_id, product_id)
