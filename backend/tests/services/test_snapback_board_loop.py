import pytest


@pytest.mark.asyncio
async def test_board_refresh_is_display_only(monkeypatch):
    from app.services import snapback_board_loop

    calls = []

    monkeypatch.setattr(
        snapback_board_loop,
        "_kite_user_ids",
        lambda: ["u1"],
    )

    async def scan_once(uid, *, record_evidence=False):
        calls.append((uid, record_evidence))
        return {}

    from app.services import snapback
    monkeypatch.setattr(snapback, "scan_once", scan_once)

    await snapback_board_loop.refresh_all_once()

    assert calls == [("u1", False)]
