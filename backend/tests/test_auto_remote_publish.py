"""The remote main publisher deploys only committed frontend, not broker runtime."""
import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "sterling_auto_publish_test", ROOT / "scripts/sterling_stack.py"
)
stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)


def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(stack, "STATE", tmp_path)
    return stack.Stack(SimpleNamespace(
        provider="local", hostname="", port=5180, dev=True, dev_port=5174
    ))


def test_remote_origin_main_is_default_without_touching_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("STERLING_PUBLISH_REF", raising=False)
    monkeypatch.delenv("STERLING_PUBLISH_FETCH", raising=False)
    instance = manager(tmp_path, monkeypatch)
    assert instance.publisher.ref == "origin/main"
    assert instance.auto_fetch is True
    assert instance.status["release_auto_fetch"] is True
    assert instance.backend_owned is False
    assert instance.children == {}


def test_manual_pin_and_explicit_opt_out(monkeypatch, tmp_path):
    monkeypatch.setenv("STERLING_PUBLISH_REF", "main")
    monkeypatch.delenv("STERLING_PUBLISH_FETCH", raising=False)
    assert manager(tmp_path, monkeypatch).auto_fetch is False
    monkeypatch.setenv("STERLING_PUBLISH_REF", "origin/main")
    monkeypatch.setenv("STERLING_PUBLISH_FETCH", "0")
    assert manager(tmp_path, monkeypatch).auto_fetch is False


def test_fetch_is_fast_forward_only_and_publishes_isolated_frontend(monkeypatch, tmp_path):
    monkeypatch.delenv("STERLING_PUBLISH_REF", raising=False)
    monkeypatch.delenv("STERLING_PUBLISH_FETCH", raising=False)
    instance = manager(tmp_path, monkeypatch)
    calls = []

    def fetch(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(stack.subprocess, "run", fetch)
    monkeypatch.setattr(instance.publisher, "publish", lambda: ("a" * 40, True))
    instance.refresh_committed_release(True)
    assert instance.release_results.get_nowait() == ("ready", "a" * 40, True)
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        "git", "-C", str(stack.ROOT), "fetch", "--no-tags", "origin",
        "refs/heads/main:refs/remotes/origin/main",
    ]
    assert kwargs["check"] is True
    assert "merge" not in command and "--force" not in command
    assert instance.backend_owned is False
    assert instance.children == {}


def test_remote_rewind_never_promotes(monkeypatch, tmp_path):
    monkeypatch.setattr(stack, "STATE", tmp_path)
    publisher = stack.CommittedFrontend("origin/main")
    monkeypatch.setattr(publisher, "resolve_commit", lambda: "b" * 40)
    monkeypatch.setattr(publisher, "current", lambda: "a" * 40)
    commands = []

    def reject(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(stack.subprocess, "run", reject)
    with pytest.raises(RuntimeError, match="not a descendant"):
        publisher.publish()
    assert commands == [
        ["git", "-C", str(stack.ROOT), "merge-base",
         "--is-ancestor", "a" * 40, "b" * 40],
    ]
    assert not (tmp_path / "published.next").exists()
