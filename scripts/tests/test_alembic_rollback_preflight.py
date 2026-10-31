"""No database connection required; destructive downgrade is visible and blocked."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.audit_alembic_rollback import analyze_revision, audit


def test_repository_baseline_is_not_safe_to_auto_downgrade():
    revisions = Path(__file__).resolve().parents[2] / "backend" / "alembic" / "versions"
    result = audit(revisions)
    assert result["production_database_touched"] is False
    assert result["postgres_restore_proven"] is False
    assert result["automatic_rollback_authorized"] is False
    assert result["status"] == "BLOCKED_AUTOMATIC_DOWNGRADE"
    drops = [
        op["target"] for rev in result["revisions"]
        for op in rev["destructive_or_unreviewed_operations"]
        if op["operation"] == "drop_table"
    ]
    assert {"positions", "mirrored_records", "equity_snapshots"}.issubset(set(drops))


def test_preflight_detects_destructive_or_opaque_downgrade_without_executing(tmp_path):
    path = tmp_path / "123_fake.py"
    path.write_text(
        "from alembic import op\n"
        "def downgrade():\n"
        "    op.execute('DROP TABLE customer_accounts')\n",
        encoding="utf-8",
    )
    item = analyze_revision(path)
    assert item["destructive_or_unreviewed_operations"][0]["operation"] == "execute"
    assert audit(tmp_path)["status"] == "BLOCKED_AUTOMATIC_DOWNGRADE"


def test_empty_revision_dir_cannot_be_certified(tmp_path):
    with pytest.raises(ValueError, match="No Alembic revisions"):
        audit(tmp_path)
