from pathlib import Path

from app.core.runtime_env import load_sterling_env


def test_all_sterling_lines_are_exported_and_a_real_variable_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "ENVIRONMENT=production\n"
        "STERLING_DB_PATH=/data/live.db\n"
        "STERLING_STATE_ROOT='/data'\n"
        "STERLING_INTENT_DB=\n"
        "STERLING_EXPERIMENT_ID=x\n"
        "STERLING_FAMILY_ACCOUNT_ID=KITE-X\n"
        "STERLING_LIVE_EXECUTION=0\n"
        "STERLING_SECRET_KEY=test-secret-key-from-dotenv\n"
        "KITE_API_KEY=k\n"
    )
    env = {"STERLING_SAFE_MODE_FILE": "already"}
    f.write_text(f.read_text() + "STERLING_SAFE_MODE_FILE=/other\n")

    loaded = load_sterling_env(f, env)

    assert env["STERLING_DB_PATH"] == "/data/live.db"
    assert env["STERLING_STATE_ROOT"] == "/data"
    assert env["STERLING_EXPERIMENT_ID"] == "x"
    assert env["STERLING_FAMILY_ACCOUNT_ID"] == "KITE-X"
    assert env["STERLING_LIVE_EXECUTION"] == "0"
    assert env["STERLING_SECRET_KEY"] == "test-secret-key-from-dotenv"
    assert env["STERLING_SAFE_MODE_FILE"] == "already"  # a real variable wins

    # Empty values stay absent and non-Sterling settings are still handled by
    # pydantic-settings rather than being injected into os.environ here.
    assert "STERLING_INTENT_DB" not in env
    assert env["KITE_API_KEY"] == "k"
    assert "ENVIRONMENT" not in env
    assert loaded == [
        "STERLING_DB_PATH",
        "STERLING_STATE_ROOT",
        "STERLING_EXPERIMENT_ID",
        "STERLING_FAMILY_ACCOUNT_ID",
        "STERLING_LIVE_EXECUTION",
        "STERLING_SECRET_KEY",
        "KITE_API_KEY",
    ]


def test_live_kill_switch_from_dotenv_is_visible_before_strategy_imports(tmp_path):
    f = tmp_path / ".env"
    f.write_text("STERLING_LIVE_EXECUTION=off\n")
    env = {}

    load_sterling_env(f, env)

    assert env["STERLING_LIVE_EXECUTION"] == "off"


def test_shell_launcher_uses_one_fail_closed_python_env_path():
    backend = Path(__file__).resolve().parents[2]
    script = (backend / "run_backend.sh").read_text(encoding="utf-8")

    assert "from app.core.runtime_env import load_sterling_env" in script
    assert "load_sterling_env()" in script
    assert "os.execve(uvicorn, argv, os.environ)" in script

    # Never reintroduce a second parser or process-substitution path whose
    # failure status Bash can lose while continuing to uvicorn.
    assert "grep -E '^STERLING_" not in script
    assert "while IFS=" not in script
    assert "< <(" not in script
    assert "exec ./.venv/bin/uvicorn" not in script


def test_a_missing_env_file_is_not_an_error(tmp_path):
    assert load_sterling_env(tmp_path / "nope.env", {}) == []


def test_operator_cli_loads_dotenv_before_importing_runtime_authorities(tmp_path):
    import os
    import shutil
    import subprocess
    import sys

    backend = Path(__file__).resolve().parents[2]
    core = tmp_path / "backend/app/core"
    core.mkdir(parents=True)
    (core.parent / "__init__.py").touch()
    (core / "__init__.py").touch()
    shutil.copy2(backend / "app/core/runtime_env.py", core / "runtime_env.py")
    (tmp_path / "backend/.env").write_text("STERLING_LIVE_EXECUTION=false\n")
    (core / "release_manifest.py").write_text(
        "import os\n"
        "assert os.environ['STERLING_LIVE_EXECUTION'] == 'false'\n"
        "def read_manifest(): return 'isolated'\n"
        "def build_release_manifest(): return 'isolated'\n"
        "def render_manifest(value): return 'isolated environment loaded'\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith('STERLING_')}
    env.update(STERLING_ROOT=str(tmp_path), STERLING_PYTHON=sys.executable)
    result = subprocess.run(
        ['bash', str(backend.parent / 'scripts/sterlingctl'), 'manifest'],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert 'isolated environment loaded' in result.stdout


def test_safe_mode_cli_loads_config_and_initializes_alert_storage(tmp_path):
    import os,shutil,subprocess,sys
    backend=Path(__file__).resolve().parents[2]
    core=tmp_path/"backend/app/core"
    core.mkdir(parents=True)
    (core.parent / "__init__.py").touch()
    (core / "__init__.py").touch()
    services=tmp_path/"backend/app/services"
    services.mkdir()
    (services / "__init__.py").touch()
    shutil.copy2(backend/"app/core/runtime_env.py",core/"runtime_env.py")
    (tmp_path/"backend/.env").write_text("STERLING_SAFE_MODE_FILE=isolated-safe-state\n")
    (services/"db.py").write_text("import os\ndef init(): os.environ['ALERT_DB_READY']='1'; return True\n")
    (services/"safe_mode.py").write_text(
        "import os\nclass SafeModeError(Exception): pass\n"
        "class SafeModeTrigger: OPERATOR='OPERATOR'\n"
        "class SafeModeService:\n"
        " def __init__(self,path): assert path=='isolated-safe-state'\n"
        " def engage(self,**kw): assert os.environ['ALERT_DB_READY']=='1'; return type('State',(),{'reason':'isolated'})()\n")
    env={k:v for k,v in os.environ.items() if not k.startswith('STERLING_')}
    env.update(STERLING_ROOT=str(tmp_path),STERLING_PYTHON=sys.executable)
    result=subprocess.run(['bash',str(backend.parent/'scripts/sterling-safe-mode'),'on'],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert 'SAFE MODE is now ON' in result.stdout
