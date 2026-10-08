"""The comparison gate must never classify an incomplete baseline as PASS."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


GATE = Path(__file__).resolve().parents[2] / "scripts" / "regression_gate.sh"
PASS = (0, "3 passed in 0.10s\n")
FAIL = (1, "FAILED tests/test_example.py::test_one - AssertionError\n1 failed, 2 passed in 0.10s\n")


def run_gate(tmp_path, head=PASS, baseline=PASS, checkout_ok=True):
    root = tmp_path / "repo"
    (root / "backend").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    responses = tmp_path / "responses.json"
    responses.write_text(json.dumps({"head": head, "baseline": baseline}))
    python = bin_dir / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "runs = json.loads(Path(os.environ['GATE_RESPONSES']).read_text())\n"
        "kind = 'head' if Path.cwd().parent == Path(os.environ['GATE_ROOT']) else 'baseline'\n"
        "code, output = runs[kind]\n"
        "print(output, end='')\n"
        "sys.exit(code)\n"
    )
    git = bin_dir / "git"
    git.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'rev-parse': print(os.environ['GATE_ROOT'])\n"
        "elif args[0] == 'merge-base': print('baseline-sha')\n"
        "elif args[:2] == ['worktree', 'add']:\n"
        "    if os.environ['GATE_CHECKOUT_OK'] != '1': sys.exit(1)\n"
        "    (Path(args[-2]) / 'backend').mkdir()\n"
    )
    python.chmod(0o755)
    git.chmod(0o755)
    # Isolate the gate's temporary files, including its two comparison lists.
    gate = tmp_path / "gate.sh"
    gate.write_text(GATE.read_text().replace("/tmp/rg_", str(tmp_path / "rg_")))
    env = {k: v for k, v in os.environ.items() if not k.startswith("BASH_FUNC_")}
    env.update(
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        GATE_ROOT=str(root),
        GATE_RESPONSES=str(responses),
        GATE_CHECKOUT_OK="1" if checkout_ok else "0",
        TMPDIR=str(tmp_path),
    )
    return subprocess.run(
        ["bash", str(gate)], env=env, text=True, capture_output=True, timeout=20
    )


@pytest.mark.parametrize("stage", ["head", "baseline"])
@pytest.mark.parametrize(
    "result",
    [
        (2, "ERROR tests/test_example.py - ImportError\n1 error in 0.10s\n"),
        (2, "1 passed in 0.10s\nKeyboardInterrupt\n"),
        (3, "INTERNALERROR\n1 passed in 0.10s\n"),
        (4, "usage error\n"),
        (5, "no tests ran in 0.10s\n"),
        (1, "ERROR tests/test_example.py::test_one\n2 passed, 1 error in 0.10s\n"),
        (1, "1 failed in 0.10s\n"),
        (0, "application log: 1 passed\n"),
    ],
)
def test_incomplete_run_is_unknown(tmp_path, stage, result):
    run = run_gate(tmp_path, **{stage: result})
    assert run.returncode == 2, run.stdout + run.stderr
    assert "UNKNOWN" in run.stdout + run.stderr
    assert "No regressions" not in run.stdout
    assert "baseline failures: 0" not in run.stdout


@pytest.mark.parametrize("head,baseline", [(PASS, PASS), (FAIL, FAIL), (PASS, FAIL)])
def test_completed_comparison_can_pass(tmp_path, head, baseline):
    run = run_gate(tmp_path, head, baseline)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "No regressions" in run.stdout


def test_new_failure_blocks(tmp_path):
    run = run_gate(tmp_path, FAIL, PASS)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "REGRESSION" in run.stdout
    assert "tests/test_example.py::test_one" in run.stdout


def test_unavailable_baseline_checkout_is_unknown(tmp_path):
    run = run_gate(tmp_path, checkout_ok=False)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "UNKNOWN: baseline checkout" in run.stdout
