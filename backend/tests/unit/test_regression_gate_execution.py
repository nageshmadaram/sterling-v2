"""Exercise the actual shell gate with controlled pytest outcomes in a tiny repo."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

GATE = Path(__file__).resolve().parents[2] / "scripts/regression_gate.sh"


def invoke(repo, *args):
    return subprocess.run(args, cwd=repo, check=True, text=True, capture_output=True)


def prepare(tmp_path, baseline, head):
    root = tmp_path / "repo"
    backend = root / "backend"
    backend.mkdir(parents=True)
    (backend / "result.json").write_text(json.dumps(baseline))
    invoke(root, "git", "init", "-q")
    invoke(root, "git", "add", "backend/result.json")
    invoke(root, "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
           "-c", "commit.gpgsign=false", "commit", "-qm", "baseline")
    invoke(root, "git", "branch", "baseline")
    (backend / "result.json").write_text(json.dumps(head))
    runner = backend / ".venv/bin/python"
    runner.parent.mkdir(parents=True)
    runner.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
if sys.argv[1:3] != ["-m", "pytest"]:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
result = json.loads(Path("result.json").read_text())
report = next(arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--junitxml="))
Path(report).write_text(result["xml"])
print(result.get("log", "1 passed"))
sys.exit(result["code"])
''')
    runner.chmod(0o755)
    shutil.copyfile(GATE, root / "gate.sh")
    return root


def result(code=0, kind="pass", log="1 passed"):
    child = {"pass": "", "fail": "<failure/>", "error": "<error/>",
             "skip": "<skipped/>"}[kind]
    return {"code": code, "log": log,
            "xml": '<testsuites><testsuite><testcase classname="tests.test_sample" '
                   f'name="test_one">{child}</testcase></testsuite></testsuites>'}


@pytest.mark.parametrize("baseline,head,expected", [
    (result(), result(), 0),
    (result(), result(1, "fail"), 1),
    (result(), result(1, "error", "1 error"), 1),
    (result(1, "error"), result(1, "error"), 0),
    (result(), result(2, "error", "1 error during collection"), 2),
    (result(), result(3, log="1 passed before internal error"), 2),
    (result(), result(5, "skip"), 2),
    (result(), result(0, "skip"), 2),
    (result(), result(1), 2),
    (result(), result(0, "fail"), 2),
    (result(2, "error"), result(), 2),
    (result(), {"code": 0, "xml": "", "log": "1 passed"}, 2),
    (result(), result(log="FAILED fake application diagnostic\n1 passed"), 0),
])
def test_gate_uses_exit_status_and_structured_test_outcomes(tmp_path, baseline, head, expected):
    repo = prepare(tmp_path, baseline, head)
    completed = subprocess.run(["bash", "gate.sh", "baseline"], cwd=repo,
                               capture_output=True, text=True, timeout=30,
                               env={k: v for k, v in os.environ.items()
                                    if not k.startswith("BASH_FUNC_")})
    assert completed.returncode == expected, completed.stdout + completed.stderr
    assert len(invoke(repo, "git", "worktree", "list").stdout.splitlines()) == 1
