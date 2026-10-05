"""Adapter from OperationalAlert to Sterling's existing Kite Telegram transport.

This adds no new notification system and no new bot credentials: it reuses the
encrypted per-user target store and its async ``send_via``. Only operational faults
are sent — never signals, wins or losses. With no configured target, logging remains
the fallback and health reports ``alert_transport_configured=false``.
"""

from __future__ import annotations

import asyncio
import html as _html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _targets(user_id: str) -> List[Any]:
    from app.services.notifications import kite_telegram_store as kts

    return list(kts.enabled_targets(user_id) or [])


def alert_user_id() -> str:
    """Whose Telegram targets Snapback alerts go to.

    The owner of the bound family account when Family Mode names one. Sending to a
    hard-coded "default" user delivers nothing when the account belongs to anyone
    else, and the only symptom is that no alert ever arrives.
    """
    try:
        from app.services.snapback_family_account import (
            binding_configured, configured_binding,
        )

        if binding_configured():
            return configured_binding().user_id
    except Exception as exc:  # noqa: BLE001
        log.warning("Snapback alerts: family user unresolved, using default: %s", exc)
    return "default"


def transport_configured(user_id: str | None = None) -> bool:
    """Whether at least one enabled Telegram target exists. Unknown counts as false."""
    try:
        return bool(_targets(user_id or alert_user_id()))
    except Exception as exc:
        log.warning("Snapback alerts: Telegram target probe failed: %s", exc)
        return False


# Telegram rejects a message over 4096 characters outright; an alert that carries a
# long exception string must be shortened rather than lost.
_MAX_MESSAGE_CHARS = 3000


def _as_ist(value: Any) -> datetime | None:
    """A stored timestamp (ISO text or datetime) as IST; naive input is UTC."""
    if value is None or value == "":
        return None
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_IST)


def _policy(alert: Any):
    """The written-down action and exposure effect for this fault, if any."""
    try:
        from app.core.operator_alerts import from_operational

        return from_operational(alert)
    except Exception as exc:  # noqa: BLE001 - never lose an alert over its decoration
        log.warning("Snapback alerts: no operator policy for %s: %s",
                    getattr(alert, "code", ""), exc)
        return None


def format_alert_html(alert: Any, *, system_status: str = "", now: datetime | None = None) -> str:
    """Render one alert for Telegram HTML.

    The time shown is when the fault was detected (``created_at``), in IST; a retry
    that finally gets through an hour later must not look like a fresh event. The
    state shown is the one the caller observed; with none supplied nothing is
    asserted, because a CRITICAL backup failure is not a halted system.
    """
    severity = str(getattr(alert, "severity", "WARNING")).upper()
    when = _as_ist(getattr(alert, "created_at", None)) or (
        now.astimezone(_IST) if now is not None else datetime.now(_IST)
    )
    message = str(getattr(alert, "message", ""))
    if len(message) > _MAX_MESSAGE_CHARS:
        message = message[: _MAX_MESSAGE_CHARS - 1] + "…"

    lines = [
        f"<b>[STERLING {_html.escape(severity)}]</b>",
        "Frozen Snapback prospective runtime",
        "",
        f"Fault: {_html.escape(str(getattr(alert, 'title', '')))}",
    ]
    if system_status:
        lines.append(f"State: {_html.escape(system_status)}")

    policy = _policy(alert)
    if policy is not None:
        lines.append(f"New trading: {'BLOCKED' if policy.trading_blocked else 'unaffected'}")
    lines.append(f"Time: {when.strftime('%Y-%m-%d %H:%M:%S')} IST")
    lines.append("Action: Open Sterling \u2192 Family Operations")
    lines.append("")
    lines.append(_html.escape(message))
    if policy is not None:
        lines.append("")
        lines.append(f"Do this: {_html.escape(policy.next_action)}")
    return "\n".join(lines)


# Background delivery tasks must be referenced: the event loop holds only a weak
# reference, so an unreferenced task can be collected before it finishes.
_background: set = set()


class TelegramAlertSink:
    """Synchronous sink bridging to the async Telegram transport.

    ``AlertDispatcher`` is synchronous and is called from inside the ops scheduler's
    async task, so a running loop gets a background task and a bare thread gets its
    own short-lived loop. Delivery is best effort: a failure is logged, never raised,
    because monitoring must outlive its own transport.
    """

    def __init__(self, user_id: str | None = None, *, system_status_fn=None) -> None:
        self.user_id = user_id or alert_user_id()
        self.system_status_fn = system_status_fn

    def _status(self) -> str:
        if self.system_status_fn is None:
            return ""
        try:
            return str(self.system_status_fn() or "")
        except Exception:
            return ""

    async def _deliver(self, html: str) -> None:
        from app.services.notifications import kite_telegram_store as kts

        targets = _targets(self.user_id)
        delivered = 0
        errors: list[str] = []
        for target in targets:
            try:
                ok = await kts.send_via(target.bot_token, target.chat_id, html)
            except Exception as exc:
                errors.append(str(exc))
                log.warning("Snapback alerts: Telegram delivery failed: %s", exc)
                continue
            if ok:
                delivered += 1
            else:
                errors.append(f"target {getattr(target, 'id', target.chat_id)} rejected delivery")

        if targets and delivered == 0:
            raise RuntimeError(
                "Telegram rejected every configured target"
                + (f": {'; '.join(errors[:3])}" if errors else "")
            )

    def targets_configured(self) -> bool:
        return transport_configured(self.user_id)

    async def deliver(self, alert: Any) -> None:
        """Await actual delivery, so a failure is a failure the caller can see."""
        await self._deliver(format_alert_html(alert, system_status=self._status()))

    def send_now(self, alert: Any) -> bool:
        """Deliver synchronously and report whether Telegram accepted it.

        For callers on a worker thread with no event loop (the ops scheduler's
        dispatch step). Inside a running loop this refuses rather than blocking it.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return False
        try:
            if not self.targets_configured():
                return False
            asyncio.run(self.deliver(alert))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapback alerts: direct Telegram delivery failed: %s", exc)
            return False

    def send(self, alert: Any) -> None:
        try:
            targets = _targets(self.user_id)
        except Exception as exc:
            log.warning("Snapback alerts: Telegram target lookup failed: %s", exc)
            return

        if not targets:
            log.info(
                "Snapback alerts: no Telegram target configured; logging only [%s] %s",
                getattr(alert, "severity", ""),
                getattr(alert, "title", ""),
            )
            return

        html = format_alert_html(alert, system_status=self._status())

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop is not None:
                # Inside the ops scheduler's loop: deliver without blocking it.
                task = loop.create_task(self._deliver(html))
                _background.add(task)
                task.add_done_callback(_background.discard)
            else:
                # No loop (scripts, tests): deliver synchronously so the caller can
                # observe the outcome rather than racing a daemon thread.
                asyncio.run(self._deliver(html))
        except Exception as exc:
            log.warning("Snapback alerts: Telegram dispatch failed: %s", exc)


class CompositeAlertSink:
    """Fan out to independent sinks without inventing shared sink state."""

    def __init__(self, *sinks: Any) -> None:
        self.sinks = list(sinks)

    def targets_configured(self) -> bool:
        """True when any child sink reports an actual configured transport."""
        for sink in self.sinks:
            probe = getattr(sink, "targets_configured", None)
            if not callable(probe):
                continue
            try:
                if probe():
                    return True
            except Exception as exc:
                log.warning(
                    "Snapback alerts: sink %s config probe failed: %s",
                    type(sink).__name__, exc,
                )
        return False

    async def deliver(self, alert: Any) -> None:
        """Await async-capable children; fall back to synchronous send."""
        failures: list[str] = []
        for sink in self.sinks:
            try:
                deliver = getattr(sink, "deliver", None)
                if callable(deliver):
                    await deliver(alert)
                    continue
                send = getattr(sink, "send", None)
                if callable(send):
                    send(alert)
            except Exception as exc:
                failures.append(f"{type(sink).__name__}: {exc}")
                log.warning(
                    "Snapback alerts: sink %s failed: %s",
                    type(sink).__name__, exc,
                )
        if failures:
            raise RuntimeError("; ".join(failures))

    def send(self, alert: Any) -> None:
        """Offer the alert to every sink, then report any failure to the caller.

        Swallowing a child's failure here told the dispatcher the alert had gone
        out when the durable sink had refused it; the dispatcher then suppressed it
        for hours and the notification was lost. Every sink still gets its turn,
        but a failure is raised so the dispatcher retries on the next poll.
        """
        failures: list[str] = []
        for sink in self.sinks:
            try:
                sink.send(alert)
            except Exception as exc:
                failures.append(f"{type(sink).__name__}: {exc}")
                log.warning("Snapback alerts: sink %s failed: %s", type(sink).__name__, exc)
        if failures:
            raise RuntimeError("; ".join(failures))
