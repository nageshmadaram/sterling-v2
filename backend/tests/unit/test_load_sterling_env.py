from app.core.runtime_env import load_sterling_env


def test_sterling_lines_are_exported_and_a_real_variable_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text("ENVIRONMENT=production\nSTERLING_DB_PATH=/data/live.db\nSTERLING_STATE_ROOT='/data'\n"
                 "STERLING_INTENT_DB=\nSTERLING_EXPERIMENT_ID=x\nSTERLING_FAMILY_ACCOUNT_ID=KITE-X\nKITE_API_KEY=k\n")
    env = {"STERLING_SAFE_MODE_FILE": "already"}
    f.write_text(f.read_text() + "STERLING_SAFE_MODE_FILE=/other\n")
    loaded = load_sterling_env(f, env)
    assert env["STERLING_DB_PATH"] == "/data/live.db"
    assert env["STERLING_STATE_ROOT"] == "/data"
    assert env["STERLING_SAFE_MODE_FILE"] == "already"          # a real variable wins
    # only location keys are aligned; nothing else changes behaviour
    assert not any(k in env for k in ("STERLING_EXPERIMENT_ID", "STERLING_FAMILY_ACCOUNT_ID",
                                      "STERLING_INTENT_DB", "KITE_API_KEY", "ENVIRONMENT"))
    assert loaded == ["STERLING_DB_PATH", "STERLING_STATE_ROOT"]


def test_a_missing_env_file_is_not_an_error(tmp_path):
    assert load_sterling_env(tmp_path / "nope.env", {}) == []
