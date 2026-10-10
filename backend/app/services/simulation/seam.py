"""The broker-mode seam: which kind of client is this?

``client._is_paper`` means "no real money". It is ``False`` only for a real broker session and
``True`` for the legacy instant-fill paper client AND for the virtual Simulation client, so the
real-money safety machinery (live-minimum, external exposure, the family gate, the global kill
switch) keys on ``is False`` and never applies to Simulation.

Simulation must nevertheless run the SAME order lifecycle as live: fills arrive by order postback
(the exchange answers first and reports the fill a moment later), a GTT fires on its own and its
child order fills later, protection is armed against the broker's book. Code that implements that
lifecycle asks :func:`is_live_like`; code that guards real money keeps asking ``_is_paper is False``.

====================  ===============  ===========  =================
client                ``_is_paper``    live_like    instant paper
====================  ===============  ===========  =================
real ``KiteClient``   ``False``        yes          no
``SimulatedKite...``  ``True``         yes          no
legacy paper client   ``True``         no           yes
unknown / a double    ``None``         no           no
====================  ===============  ===========  =================

Nothing here imports anything: the predicates are safe to use from any module.
"""
from __future__ import annotations

from typing import Any


def is_simulated(client: Any) -> bool:
    """True only for the virtual Simulation client (an explicit ``True``, never a truthy mock)."""
    return getattr(client, "_is_simulated", False) is True


def is_live_like(client: Any) -> bool:
    """True for a real-money client and for a simulated one: both run the postback-driven lifecycle."""
    return getattr(client, "_is_paper", None) is False or is_simulated(client)


def is_instant_paper(client: Any) -> bool:
    """True only for the legacy paper client that fills synchronously and has no broker book."""
    return getattr(client, "_is_paper", None) is True and not is_simulated(client)


__all__ = ["is_simulated", "is_live_like", "is_instant_paper"]
