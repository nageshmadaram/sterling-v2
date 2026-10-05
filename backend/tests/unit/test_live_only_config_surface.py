"""Sterling is live-only: the configuration surface must say so and must not offer
a non-live mode.

These tests read the shipped ops files (compose, env templates, systemd unit,
Makefile) and the health endpoint. They assert the contract, not the prose:
no PAPER_TRADING switch anywhere, the kill switch is documented and defaults on,
Family Mode is not required, and `make clean` can never delete the live database.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]

OPS_FILES = (
    REPO / "docker-compose.yml",
    REPO / "backend" / ".env.example",
    REPO / "deploy" / "sterling.env.example",
    REPO / "deploy" / "systemd" / "sterling-backend.service",
    REPO / "Makefile",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", OPS_FILES, ids=lambda p: p.name)
def test_no_ops_file_offers_a_paper_trading_switch(path):
    body = _text(path)
    assert "PAPER_TRADING" not in body
    # KITE_PAPER may be mentioned only to say it is gone, never assigned a value.
    assert not re.search(r"^\s*KITE_PAPER\s*=", body, re.M)


@pytest.mark.parametrize(
    "path",
    (REPO / "backend" / ".env.example", REPO / "deploy" / "sterling.env.example"),
    ids=lambda p: p.parent.name + "/" + p.name,
)
def test_env_templates_document_the_kill_switch_and_leave_it_on(path):
    body = _text(path)
    assert re.search(r"^STERLING_LIVE_EXECUTION=true\s*$", body, re.M)
    # The accepted kill-switch values are listed next to the setting.
    assert re.search(r"0\s*[|/]?\s*false\s*[|/]?\s*off\s*[|/]?\s*no\s*[|/]?\s*disabled", body)


def test_compose_defaults_the_kill_switch_to_on_and_drops_paper():
    body = _text(REPO / "docker-compose.yml")
    assert "STERLING_LIVE_EXECUTION: ${STERLING_LIVE_EXECUTION:-true}" in body
    assert "PAPER_TRADING" not in body


@pytest.mark.parametrize(
    "path",
    (REPO / "backend" / ".env.example", REPO / "deploy" / "sterling.env.example"),
    ids=lambda p: p.parent.name + "/" + p.name,
)
def test_family_mode_is_not_required_by_any_template(path):
    body = _text(path)
    assert not re.search(r"^\s*STERLING_FAMILY_MODE\s*=\s*(1|true|yes|on)\s*$", body, re.M | re.I)


def test_the_database_file_name_is_kept_so_existing_state_is_found():
    # Renaming the file would silently start Sterling on an empty database.
    assert "STERLING_DB_PATH: /app/data/sterling_paper.db" in _text(REPO / "docker-compose.yml")
    assert "STERLING_DB_PATH=/home/<user>/Sterling/data/sterling_paper.db" in _text(
        REPO / "deploy" / "sterling.env.example"
    )


def test_make_clean_never_deletes_the_database():
    clean = _text(REPO / "Makefile").split("\nclean:", 1)[1].split("\n\n", 1)[0]
    assert "rm -f" not in clean or ".db" not in clean
    assert "sterling_paper.db" not in re.sub(r"(?m)^\s*#.*$", "", clean)


def test_settings_has_no_paper_trading_field():
    from app.core.config import Settings

    assert "paper_trading" not in Settings.model_fields


@pytest.fixture()
def client():
    from main import create_app

    with TestClient(create_app()) as c:
        yield c


def test_health_reports_live_execution_not_paper_trading(client):
    body = client.get("/health").json()
    assert "paper_trading" not in body
    assert body["live_execution"] is True
    assert "environment" in body


def test_health_tracks_the_kill_switch(client, monkeypatch):
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    assert client.get("/health").json()["live_execution"] is False
