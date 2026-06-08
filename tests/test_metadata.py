"""Tests for metadata mapping and error mapping (yt-dlp mocked, never hits the network)."""

from __future__ import annotations

import pytest
from yt_dlp.utils import DownloadError

from youtube_context_mcp import metadata
from youtube_context_mcp.metadata import MetadataError, _map_metadata_error, _to_metadata

VIDEO = "dQw4w9WgXcQ"


@pytest.fixture
def info() -> dict:
    """A representative yt-dlp info dict."""
    return {
        "title": "Never Gonna Give You Up",
        "channel": "Rick Astley",
        "channel_url": "https://www.youtube.com/channel/UC123",
        "upload_date": "20091025",
        "duration": 213,
        "view_count": 1_600_000_000,
        "like_count": 17_000_000,
        "description": "The official video ...",
        "tags": ["rick astley", "never gonna give you up"],
        "chapters": [
            {"title": "Intro", "start_time": 0.0, "end_time": 5.0},
            {"title": "Chorus", "start_time": 43.0, "end_time": 60.0},
        ],
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    }


# ---- mapping ----


def test_to_metadata_maps_fields(info):
    out = _to_metadata(info, VIDEO)
    assert out["video_id"] == VIDEO
    assert out["title"] == "Never Gonna Give You Up"
    assert out["channel"] == "Rick Astley"
    assert out["upload_date"] == "2009-10-25"
    assert out["duration_seconds"] == 213
    assert out["view_count"] == 1_600_000_000
    assert out["tags"] == ["rick astley", "never gonna give you up"]
    assert out["chapters"] == [
        {"title": "Intro", "start": 0.0, "end": 5.0},
        {"title": "Chorus", "start": 43.0, "end": 60.0},
    ]


def test_chapters_without_start_are_skipped():
    # A startless chapter is unusable and dropped; a 0.0 start (falsy) must be kept.
    out = _to_metadata(
        {"chapters": [{"title": "no start"}, {"title": "Intro", "start_time": 0.0}]}, VIDEO
    )
    assert out["chapters"] == [{"title": "Intro", "start": 0.0, "end": None}]


def test_description_omitted_by_default(info):
    assert _to_metadata(info, VIDEO)["description"] is None


def test_description_included_when_requested(info):
    out = _to_metadata(info, VIDEO, include_description=True)
    assert out["description"] == "The official video ..."


def test_channel_falls_back_to_uploader():
    out = _to_metadata({"uploader": "Some Channel", "uploader_url": "u"}, VIDEO)
    assert out["channel"] == "Some Channel"
    assert out["channel_url"] == "u"


def test_missing_fields_default_to_none_or_empty():
    out = _to_metadata({}, VIDEO)
    assert out["title"] is None
    assert out["duration_seconds"] is None
    assert out["like_count"] is None
    assert out["tags"] == []
    assert out["chapters"] == []
    # webpage_url falls back to the canonical watch URL.
    assert out["webpage_url"] == f"https://www.youtube.com/watch?v={VIDEO}"


# ---- error mapping (unit) ----


@pytest.mark.parametrize(
    "message, needle",
    [
        ("Private video. Sign in if you've been granted access", "private"),
        ("Sign in to confirm your age", "age-restricted"),
        ("Sign in to confirm you're not a bot", "blocked"),
        ("This video is not available in your country", "region"),
        ("Video unavailable. This video has been removed", "unavailable"),
        ("Something unexpected went wrong", "could not retrieve metadata"),
    ],
)
def test_map_metadata_error_messages(message, needle):
    err = _map_metadata_error(DownloadError(f"ERROR: {message}"))
    assert isinstance(err, MetadataError)
    assert needle in str(err).lower()


def test_map_metadata_error_blocked_mentions_proxy():
    err = _map_metadata_error(DownloadError("ERROR: Sign in to confirm you're not a bot"))
    assert "PROXY" in str(err).upper()


def test_map_metadata_error_proxy_does_not_leak_credentials():
    err = _map_metadata_error(
        DownloadError("ERROR: Unable to connect to proxy http://user:secret@host:80")
    )
    assert "secret" not in str(err)
    assert "proxy" in str(err).lower()


# ---- propagation through the public function ----


def test_get_video_metadata_delegates_and_maps(monkeypatch, info):
    monkeypatch.setattr(metadata, "_extract_info", lambda video_id: dict(info, video_id=video_id))
    out = metadata.get_video_metadata(f"https://youtu.be/{VIDEO}")
    assert out["video_id"] == VIDEO
    assert out["title"] == "Never Gonna Give You Up"


def test_get_video_metadata_passes_include_description(monkeypatch, info):
    monkeypatch.setattr(metadata, "_extract_info", lambda video_id: info)
    assert metadata.get_video_metadata(VIDEO, include_description=True)["description"]


def test_get_video_metadata_wraps_library_error(monkeypatch):
    def boom(video_id):
        raise DownloadError("ERROR: Private video")

    monkeypatch.setattr(metadata, "_extract_info", boom)
    with pytest.raises(MetadataError):
        metadata.get_video_metadata(VIDEO)


def test_get_video_metadata_invalid_input_raises_valueerror():
    with pytest.raises(ValueError):
        metadata.get_video_metadata("not a youtube url")
