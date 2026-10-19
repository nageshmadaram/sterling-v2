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
