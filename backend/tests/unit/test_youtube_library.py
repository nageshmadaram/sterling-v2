"""YouTube library parsing and the public route's contract."""
import asyncio

from fastapi.testclient import TestClient

from app.services import youtube_library as yl

FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
 <entry><yt:videoId>AAAAAAAAAAA</yt:videoId><title>Long film</title><link rel="alternate" href="https://www.youtube.com/watch?v=AAAAAAAAAAA"/>
  <published>2026-10-20T10:00:00+00:00</published><media:group><media:community><media:statistics views="1234"/></media:community></media:group></entry>
 <entry><yt:videoId>BBBBBBBBBBB</yt:videoId><title>A short</title><link rel="alternate" href="https://www.youtube.com/shorts/BBBBBBBBBBB"/>
  <published>2026-10-21T10:00:00+00:00</published></entry>
 <entry><yt:videoId>bad id</yt:videoId><title>Broken</title><link rel="alternate" href="https://www.youtube.com/watch?v=x"/></entry>
</feed>"""


def test_feed_keeps_full_videos_and_drops_shorts_and_bad_ids():
    rows = yl.parse_feed(FEED, yl.Channel("UCxxxxxxxxxxxxxxxxxxxxxx", "Demo", "Hindi"), "movies")
    assert [r["id"] for r in rows] == ["AAAAAAAAAAA"]
    assert rows[0] | {} == rows[0] and rows[0]["views"] == 1234 and rows[0]["category"] == "movies"
    assert rows[0]["channel"] == "Demo" and rows[0]["lang"] == "Hindi"


def test_channel_lists_are_unique_and_well_formed():
    ids = [c.id for _, (_, channels) in yl.LIBRARY.items() for c in channels]
    assert len(ids) == len(set(ids))
    for channel, genre in yl.LIVE:
        assert channel.id.startswith("UC") and len(channel.id) == 24 and genre


def test_route_is_public_and_cross_origin(monkeypatch):
    async def fake():
        return {"generated": 1, "categories": [], "live": []}
    monkeypatch.setattr(yl, "library", fake)
    from app.api.v1.endpoints.media_youtube import router
    from fastapi import FastAPI
    app = FastAPI(); app.include_router(router, prefix="/api/v1")
    response = TestClient(app).get("/api/v1/media/youtube/library")
    assert response.status_code == 200 and response.json()["live"] == []
    assert response.headers["access-control-allow-origin"] == "*"


def test_cache_serves_last_good_copy_when_refresh_is_empty():
    yl._cache.clear()
    calls = iter([[1], []])

    async def build():
        return next(calls)

    first = asyncio.run(yl._cached("t", 0, build))
    second = asyncio.run(yl._cached("t", 0, build))
    assert first == [1] and second == [1]


def test_archive_keeps_older_videos_newest_first_and_dedupes():
    archive = {"music": [{"id": "OLDOLDOLD01", "published": "2025-01-01", "title": "old"}]}
    fresh = [{"id": "NEWNEWNEW01", "published": "2026-10-01", "title": "new"},
             {"id": "OLDOLDOLD01", "published": "2025-01-01", "title": "old (renamed)"}]
    rows = yl.merge_archive(archive, "music", fresh)
    assert [r["id"] for r in rows] == ["NEWNEWNEW01", "OLDOLDOLD01"]
    assert rows[1]["title"] == "old (renamed)" and archive["music"] is rows


def test_archive_path_override(monkeypatch, tmp_path):
    monkeypatch.setenv("STERLING_YOUTUBE_ARCHIVE", str(tmp_path / "a.json"))
    yl._save_archive({"x": [{"id": "AAAAAAAAAAA"}]})
    assert yl._load_archive() == {"x": [{"id": "AAAAAAAAAAA"}]}


def test_archive_drops_videos_from_channels_no_longer_listed():
    archive = {"movies": [{"id": "HINDIHINDI1", "channelId": "UCold", "published": "2026-01-01"},
                          {"id": "TELUGUTELU1", "channelId": "UCkeep", "published": "2026-01-02"}]}
    rows = yl.merge_archive(archive, "movies", [], {"UCkeep"})
    assert [r["id"] for r in rows] == ["TELUGUTELU1"]


def test_documentaries_are_english_and_movies_are_telugu():
    assert {c.lang for c in yl.LIBRARY["documentaries"][1]} == {"English"}
    assert {c.lang for c in yl.LIBRARY["movies"][1]} == {"Telugu"}


def test_music_and_podcast_languages_follow_the_request():
    music = {key: {c.lang for c in channels} for key, (_, channels) in yl.LIBRARY.items() if key.startswith("music")}
    assert set(music) == {"music_hindi", "music_telugu", "music_tamil", "music_english"}
    assert set().union(*music.values()) == {"Hindi", "Telugu", "Tamil", "English"}
    assert {c.lang for c in yl.LIBRARY["podcasts"][1]} == {"English"}
