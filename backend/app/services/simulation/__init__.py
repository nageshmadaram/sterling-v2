"""Simulation mode: a virtual tenant that runs the real strategy code with virtual money.

See docs/design/SIMULATION_MODE.md for the contract.

This package shadows the older module ``app/services/simulation.py`` (the market-replay
engine). Python prefers a package over a same-named module, so without the loader below
``from app.services.simulation import simulation_runner`` (the replay API, config,
Snapback replay, ~40 tests) raises ImportError and ``main`` cannot be imported.

The replay source is executed into THIS namespace the first time a name that is not
defined here is asked for (PEP 562), so ``app.services.simulation.identity`` and the other
tenant modules import without pulling numpy and the indicator stack, while every name the
old module exported stays importable and monkeypatchable from ``app.services.simulation``.
"""
from pathlib import Path as _Path

_HERE = _Path(__file__).resolve().parent
_REPLAY_SOURCE = _HERE.parent / "simulation.py"
_replay_loaded = False


def _load_replay() -> None:
    global _replay_loaded
    if _replay_loaded or not _REPLAY_SOURCE.is_file():
        return
    _replay_loaded = True
    exec(compile(_REPLAY_SOURCE.read_text(encoding="utf-8"), str(_REPLAY_SOURCE), "exec",
                 dont_inherit=True), globals())


def __getattr__(name: str):
    if name.startswith("__") or _replay_loaded:
        raise AttributeError(name)
    if (_HERE / f"{name}.py").is_file() or (_HERE / name).is_dir():
        raise AttributeError(name)      # a submodule of this package: let the import system find it
    _load_replay()
    try:
        return globals()[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
