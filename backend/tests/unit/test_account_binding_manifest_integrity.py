"""Regression coverage for filesystem-backed broker identity durability.

No broker connection or order routing is exercised by these tests.
"""
from __future__ import annotations

import json
import stat
from dataclasses import replace

import pytest

from app.core.broker_account_binding import BindingError, BindingStatus
from app.services.account_binding_service import AccountBindingStore


def _binding(store: AccountBindingStore):
    return store.declare(
        broker="zerodha",
        legal_account_holder="Local Test Holder",
        client_id="AB1234",
        account_scope="read_only",
        deployment_id="dev-only",
    )


def test_published_manifest_is_private_and_readable_after_restart(tmp_path):
    store = AccountBindingStore(tmp_path / "bindings")
    binding = _binding(store)
    path = store._path_for(binding.binding_id)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["binding_id"] == binding.binding_id
    assert AccountBindingStore(store.directory).get(binding.binding_id) == binding

    retired = store.deactivate(binding.binding_id, reason="operator test retirement")
    assert retired.status is BindingStatus.DEACTIVATED
    assert AccountBindingStore(store.directory).get(binding.binding_id) == retired
    assert not list(store.directory.glob("*.tmp"))


def test_failed_replace_preserves_previous_manifest_and_cleans_staging(tmp_path, monkeypatch):
    store = AccountBindingStore(tmp_path / "bindings")
    binding = _binding(store)
    path = store._path_for(binding.binding_id)
    original = path.read_bytes()

    def fail_replace(*_args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("app.services.account_binding_service.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        store.save(replace(binding, notes="must not appear"))
    assert path.read_bytes() == original
    assert AccountBindingStore(store.directory).get(binding.binding_id) == binding
    assert not list(store.directory.glob(".*.tmp"))


@pytest.mark.parametrize("bad_id", ["../other", "../../escape", "/tmp/bindings", ".hidden", "foo/bar", "foo\\\\bar", ""])
def test_unsafe_binding_identifier_cannot_write_outside_manifest_directory(tmp_path, bad_id):
    store = AccountBindingStore(tmp_path / "bindings")
    binding = _binding(store)
    with pytest.raises((BindingError, ValueError)):
        store.save(replace(binding, binding_id=bad_id))
    assert store.all() == [binding]


def test_manifest_filename_identity_mismatch_is_not_silently_accepted(tmp_path):
    store = AccountBindingStore(tmp_path / "bindings")
    binding = _binding(store)
    path = store._path_for(binding.binding_id)
    path.rename(path.with_name("other-binding.json"))
    with pytest.raises(BindingError, match="identity does not match"):
        store.all()
    with pytest.raises(BindingError, match="identity does not match"):
        store.active()


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("live", True),
        ("live_minimum", True),
        (" LIVE ", True),
        ("LiVe_MiNiMuM", True),
        ("not_live", False),
        ("live_disabled", False),
        ("olive", False),
        ("read_only", False),
        ("shadow", False),
        ("live-minimum", False),
        ("live_minimum_extra", False),
        ("", False),
    ],
)
def test_live_authority_requires_an_exact_recognized_scope(
    tmp_path, scope, expected
):
    # This is an isolated metadata property test, not real broker verification.
    store = AccountBindingStore(tmp_path / "bindings")
    binding = _binding(store)
    candidate = replace(
        binding,
        status=BindingStatus.ACTIVE,
        activated_at="2026-10-30T00:00:00+00:00",
        account_scope=scope,
        static_egress_profile="named-static-egress",
    )
    assert candidate.live_ready is expected
    assert replace(candidate, static_egress_profile="").live_ready is False
    assert replace(candidate, status=BindingStatus.VERIFIED).live_ready is False
