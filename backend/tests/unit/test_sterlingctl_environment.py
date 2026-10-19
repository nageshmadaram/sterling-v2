"""Operator commands must select the backend's configured durable stores."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]


def cli_environment(tmp_path, text, overrides=None):
    backend = tmp_path / 'backend'
    core = backend / 'app' / 'core'
    core.mkdir(parents=True)
    # Make this an actual importable package. Without __init__.py Python can
    # import the checkout's app package instead of this isolated fixture.
    (core.parent / "__init__.py").touch()
    (core / "__init__.py").touch()
    (core / 'runtime_env.py').write_text(
        (ROOT / 'backend/app/core/runtime_env.py').read_text()
    )
    (backend / '.env').write_text(text)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('STERLING_')}
    env.update(STERLING_ROOT=str(tmp_path), STERLING_PYTHON=sys.executable)
    env.update(overrides or {})
    prefix = (ROOT / 'scripts/sterlingctl').read_text().split('\nusage() {', 1)[0]
    command = prefix + '''
"$PYTHON" - <<'PY'
import json, os
print(json.dumps({k: v for k, v in os.environ.items() if k.startswith('STERLING_')}))
PY
'''
    result = subprocess.run(['bash', '-c', command], env=env, text=True,
                            capture_output=True, check=True)
    return json.loads(result.stdout)


def test_configured_database_selected_before_defaults(tmp_path):
    path = str(tmp_path / 'evidence with spaces.db')
    result = cli_environment(tmp_path, f'STERLING_OBSERVATIONS_DB_PATH="{path}"\n')
    assert result['STERLING_OBSERVATIONS_DB_PATH'] == path
    assert result['STERLING_EVIDENCE_DB'] == path


def test_process_override_wins(tmp_path):
    result = cli_environment(tmp_path, 'STERLING_OBSERVATIONS_DB_PATH=file.db\n',
                             {'STERLING_OBSERVATIONS_DB_PATH': 'override.db'})
    assert result['STERLING_EVIDENCE_DB'] == 'override.db'


def test_state_root_in_env_sets_persistent_defaults(tmp_path):
    state = str(tmp_path / 'persistent')
    result = cli_environment(tmp_path, f'STERLING_STATE_ROOT={state}\n')
    assert result['STERLING_DB_PATH'] == f'{state}/data/sterling_paper.db'
    assert result['STERLING_SAFE_MODE_FILE'] == f'{state}/data/safe_mode.json'


def test_legacy_defaults_and_literal_shell_text(tmp_path):
    marker = tmp_path / 'must-not-exist'
    literal = f'$(touch {marker}) `touch {marker}`'
    result = cli_environment(tmp_path, f'STERLING_TEST_LITERAL={literal}\n')
    assert result['STERLING_TEST_LITERAL'] == literal
    assert not marker.exists()
    assert result['STERLING_DB_PATH'] == str(tmp_path / 'backend/sterling_paper.db')


def test_cli_rejects_failed_environment_loader(tmp_path):
    """A failed process substitution must not continue with fallback stores."""
    core = tmp_path / "backend/app/core"
    core.mkdir(parents=True)
    # Make this an actual importable package. Without __init__.py Python can
    # import the checkout's app package instead of this isolated fixture.
    (core.parent / "__init__.py").touch()
    (core / "__init__.py").touch()
    (core / "runtime_env.py").write_text(
        "raise RuntimeError('simulated config import failure')\\n".replace("\\n", "\n"),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("STERLING_")}
    env.update(STERLING_ROOT=str(tmp_path), STERLING_PYTHON=sys.executable)
    prefix = (ROOT / "scripts/sterlingctl").read_text().split("\nusage() {", 1)[0]
    result = subprocess.run(
        ["bash", "-c", prefix + '\nprintf "UNSAFE_CONTINUATION\\n"\n'],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2
    assert "UNSAFE_CONTINUATION" not in result.stdout
    assert "configuration did not load" in result.stderr


def test_cli_rejects_invalid_environment_variable_name(tmp_path):
    """A broken imported key must not partially initialize the operator CLI."""
    core = tmp_path / "backend/app/core"
    core.mkdir(parents=True)
    # Make this an actual importable package. Without __init__.py Python can
    # import the checkout's app package instead of this isolated fixture.
    (core.parent / "__init__.py").touch()
    (core / "__init__.py").touch()
    (core / "runtime_env.py").write_text(
        (ROOT / "backend/app/core/runtime_env.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "backend/.env").write_text("STERLING_INVALID-NAME=bad\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("STERLING_")}
    env.update(STERLING_ROOT=str(tmp_path), STERLING_PYTHON=sys.executable)
    prefix = (ROOT / "scripts/sterlingctl").read_text().split("\nusage() {", 1)[0]
    result = subprocess.run(
        ["bash", "-c", prefix + '\nprintf "UNSAFE_CONTINUATION\\n"\n'],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 2
    assert "UNSAFE_CONTINUATION" not in result.stdout
    assert "Invalid Sterling configuration key" in result.stderr


def test_release_probe_keeps_all_failed_process_logs_private():
    """Pytest and build output can contain secrets as easily as operator logs."""
    script = (ROOT / "scripts/release_certification_probe.sh").read_text(encoding="utf-8")
    assert 'tail -n 12 "$REPORT/$label.log"' not in script
    assert 'tail -n 16 "$REPORT/$label.log"' not in script
    assert 'umask 077' in script
    assert 'chmod 700 "$REPORT"' in script


def test_release_probe_scrubs_live_credentials_before_test_imports():
    """Synthetic credentials must not be replaced by the operator's actual shell."""
    script = (ROOT / "scripts/release_certification_probe.sh").read_text(encoding="utf-8")
    start = script.index("    for inherited in ${!STERLING_@}")
    end = script.index('    export STERLING_STATE_ROOT="$isolated"', start)
    scrub = script[start:end]
    env = dict(os.environ)
    env.update({
        "STERLING_TEST_SECRET": "cannot-reach-pytest",
        "STERLING_LIVE_EXECUTION": "1",
        "KITE_ACCESS_TOKEN": "cannot-reach-pytest",
        "ZERODHA_API_KEY": "cannot-reach-pytest",
        "TELEGRAM_BOT_TOKEN": "cannot-reach-pytest",
        "ENVIRONMENT": "production",
    })
    command = (
        "set -euo pipefail\n" + scrub + "\n"
        + '''printf '%s\\n' "${STERLING_TEST_SECRET-absent}" "${KITE_ACCESS_TOKEN-absent}" "${ZERODHA_API_KEY-absent}" "${TELEGRAM_BOT_TOKEN-absent}" "${ENVIRONMENT-unset}" "${STERLING_LIVE_EXECUTION-unset}"\n'''
    )
    result = subprocess.run(
        ["bash", "-c", command],
        env=env, capture_output=True, text=True, timeout=15, check=True,
    )
    assert result.stdout.splitlines() == [
        "absent", "absent", "absent", "absent", "development", "0"
    ]
