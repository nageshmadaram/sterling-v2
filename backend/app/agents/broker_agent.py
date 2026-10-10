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

    async def place_order(self, **kwargs) -> dict:
        from app.core.lane_registry import noncanonical_broker_write_allowed

        # BrokerAgent is a legacy facade, not the canonical execution authority.
        # Do not trust a caller-provided reduce_only flag: Kite's generic order
        # path cannot enforce that claim against current broker inventory.
        if not noncanonical_broker_write_allowed(self.adapter):
            raise RuntimeError(
                "BrokerAgent cannot perform live or mode-unknown broker writes; "
                "route orders through CanonicalExecutionService"
            )
        result = await self.adapter.place_order(**kwargs)
        if self.bus is not None:
            await self.bus.publish(OrderAccepted(order_id=str((result or {}).get("id", ""))))
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
