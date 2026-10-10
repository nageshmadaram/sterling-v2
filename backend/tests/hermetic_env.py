"""Keep the operator's real ``backend/.env`` out of the test process.

``main`` calls ``app.core.runtime_env.load_sterling_env()`` at import, which exports every
non-empty ``STERLING_*`` line of ``backend/.env`` that the process environment does not already
set. On the owner's machine that file names real data paths (halt file, freeze record, evidence
database, backup roots) and the real family-account binding, so any test that imported ``main``
would silently read, and could write, the operator's live state, and would behave differently
there than anywhere else.

The default call (no ``path`` / ``environ``) therefore reads nothing under test. Calls that pass
an explicit file and mapping (the loader's own tests) run the real implementation.
"""
from __future__ import annotations

from typing import Any


def isolate_dotenv(runtime_env: Any) -> None:
    real = getattr(runtime_env, "_pytest_real_load_sterling_env", None) or runtime_env.load_sterling_env

    def load_sterling_env(path=None, environ=None):  # noqa: ANN001
        if path is None and environ is None:
            return []
        return real(path, environ)

    runtime_env._pytest_real_load_sterling_env = real
    runtime_env.load_sterling_env = load_sterling_env
