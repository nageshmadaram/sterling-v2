"""Simulation mode: a virtual tenant that runs the real strategy code with virtual money.

See docs/design/SIMULATION_MODE.md for the contract.

This package shadows the older module ``app/services/simulation.py`` (the market-replay
engine). Python prefers a package over a same-named module, so without the block below
``from app.services.simulation import simulation_runner`` (the replay API, config,
Snapback replay, ~40 tests) raises ImportError and ``main`` cannot be imported. The replay
source is therefore executed into THIS namespace: every name the old module exported is
still importable and still monkeypatchable from ``app.services.simulation``.
"""
from pathlib import Path as _Path

_REPLAY_SOURCE = _Path(__file__).resolve().parent.parent / "simulation.py"
if _REPLAY_SOURCE.is_file():
    exec(compile(_REPLAY_SOURCE.read_text(encoding="utf-8"), str(_REPLAY_SOURCE), "exec",
                 dont_inherit=True), globals())
