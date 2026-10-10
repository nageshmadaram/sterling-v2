"""The suite never reads the operator's real backend/.env through ``import main``."""
from app.core import runtime_env


def test_default_dotenv_export_reads_nothing_under_test():
    assert runtime_env.load_sterling_env() == []


def test_an_explicit_file_and_environment_still_use_the_real_loader(tmp_path):
    f = tmp_path / ".env"
    f.write_text("STERLING_X=1\nOTHER=2\nSTERLING_BLANK=\n", encoding="utf-8")
    env: dict = {}
    assert runtime_env.load_sterling_env(f, env) == ["STERLING_X"]
    assert env == {"STERLING_X": "1"}
