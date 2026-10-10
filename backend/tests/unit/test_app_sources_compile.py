"""Every module under app/ must at least compile.

A merge that leaves one mis-indented line in a rarely imported module does not fail
at deploy time: it fails when that module is first imported, which for a live-money
exit path is the first time a stop fires. This catches it in one cheap pass.
"""
from __future__ import annotations

import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


@pytest.mark.parametrize(
    "path", sorted(APP.rglob("*.py")), ids=lambda p: str(p.relative_to(APP))
)
def test_module_compiles(path: pathlib.Path) -> None:
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
