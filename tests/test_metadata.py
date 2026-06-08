"""Tests for metadata mapping and error mapping (yt-dlp mocked, never hits the network)."""

from __future__ import annotations

import pytest
from yt_dlp.utils import DownloadError

from youtube_context_mcp import metadata
from youtube_context_mcp.metadata import (
    MetadataError,
    _map_metadata_error,
    _to_metadata,
    _to_most_replayed,
)

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


# ---- most-replayed (heatmap) mapping ----


def _heatmap(values, seg=10.0):
    """Build a synthetic yt-dlp heatmap from a list of intensities, ``seg`` seconds each."""
    return [
        {"start_time": i * seg, "end_time": (i + 1) * seg, "value": v} for i, v in enumerate(values)
    ]


def test_most_replayed_single_peak_fields():
    info = {
        "duration": 50,
        "heatmap": _heatmap([0.1, 0.2, 1.0, 0.3, 0.2]),
        "chapters": [{"title": "Hook", "start_time": 0.0, "end_time": 50.0}],
    }
    out = _to_most_replayed(info, VIDEO, top_n=5)
    assert out["has_data"] is True
    assert out["note"] is None
    assert out["duration_seconds"] == 50
    assert len(out["peaks"]) == 1
    peak = out["peaks"][0]
    assert peak["peak_label"] == "00:20"
    assert peak["region_label"] == "00:20-00:30"
    assert peak["peak_start_seconds"] == 20
    assert peak["region_start_seconds"] == 20
    assert peak["region_end_seconds"] == 30
    assert peak["relative_intensity"] == 1.0
    assert peak["url"] == f"https://www.youtube.com/watch?v={VIDEO}&t=20"
    assert peak["chapter"] == "Hook"


def test_most_replayed_two_regions_ordered_by_time_and_chaptered():
    info = {
        "duration": 100,
        "heatmap": _heatmap([0.1, 0.3, 1.0, 0.4, 0.2, 0.2, 0.3, 0.9, 0.6, 0.1]),
        "chapters": [
            {"title": "Intro", "start_time": 0.0, "end_time": 25.0},
            {"title": "Climax", "start_time": 60.0, "end_time": 100.0},
        ],
    }
    out = _to_most_replayed(info, VIDEO, top_n=5)
    assert [p["peak_start_seconds"] for p in out["peaks"]] == [20, 70]  # sorted by time
    assert [p["chapter"] for p in out["peaks"]] == ["Intro", "Climax"]
    # The second region merges the two adjacent above-threshold segments (70-90).
    assert out["peaks"][1]["region_label"] == "01:10-01:30"


def test_most_replayed_top_n_clamps_count():
    info = {"heatmap": _heatmap([1.0, 0.1, 0.9, 0.1, 0.8, 0.1, 0.7])}
    out = _to_most_replayed(info, VIDEO, top_n=2)
    assert len(out["peaks"]) == 2
    # Keeps the two strongest (1.0 and 0.9), still ordered by time.
    assert [p["relative_intensity"] for p in out["peaks"]] == [1.0, 0.9]


def test_most_replayed_peak_outside_chapters_has_none():
    info = {
        "heatmap": _heatmap([0.1, 1.0, 0.1]),
        "chapters": [{"title": "Later", "start_time": 100.0, "end_time": 200.0}],
    }
    assert _to_most_replayed(info, VIDEO)["peaks"][0]["chapter"] is None


def test_most_replayed_subsecond_region_ceils_end():
    # Short video -> sub-second heatmap buckets. The end must ceil so a 0.2-0.4s region reads as
    # 00:00-00:01 instead of collapsing to a zero-length 00:00-00:00.
    info = {"duration": 1, "heatmap": _heatmap([0.1, 1.0, 0.1], seg=0.2)}
    peak = _to_most_replayed(info, VIDEO)["peaks"][0]
    assert peak["region_start_seconds"] == 0
    assert peak["region_end_seconds"] == 1
    assert peak["region_end_seconds"] > peak["region_start_seconds"]
    assert peak["region_label"] == "00:00-00:01"


@pytest.mark.parametrize("heatmap", [None, []])
def test_most_replayed_no_data(heatmap):
    out = _to_most_replayed({"duration": 30, "heatmap": heatmap}, VIDEO)
    assert out["has_data"] is False
    assert out["peaks"] == []
    assert out["profile"] == []
    assert out["note"] and "most replayed" in out["note"].lower()


def test_most_replayed_flat_heatmap_falls_back_to_global_max():
    # Nothing clears the 0.5 threshold -> still return the single hottest segment.
    info = {"heatmap": _heatmap([0.2, 0.2, 0.45, 0.3, 0.1])}
    out = _to_most_replayed(info, VIDEO)
    assert out["has_data"] is True
    assert len(out["peaks"]) == 1
    assert out["peaks"][0]["peak_start_seconds"] == 20
    assert out["peaks"][0]["relative_intensity"] == 0.45


def test_most_replayed_profile_shape_and_range():
    info = {"heatmap": _heatmap([i / 99 for i in range(100)])}  # ramp 0..1
    profile = _to_most_replayed(info, VIDEO)["profile"]
    assert len(profile) == 12
    assert all(0.0 <= v <= 1.0 for v in profile)
    assert profile == sorted(profile)  # monotonic ramp stays monotonic after downsampling


def test_get_most_replayed_delegates(monkeypatch):
    info = {"duration": 30, "heatmap": _heatmap([0.1, 1.0, 0.1])}
    monkeypatch.setattr(metadata, "_extract_info", lambda video_id: info)
    out = metadata.get_most_replayed(f"https://youtu.be/{VIDEO}")
    assert out["video_id"] == VIDEO
    assert out["peaks"][0]["relative_intensity"] == 1.0


def test_get_most_replayed_wraps_library_error(monkeypatch):
    def boom(video_id):
        raise DownloadError("ERROR: Private video")

    monkeypatch.setattr(metadata, "_extract_info", boom)
    with pytest.raises(MetadataError):
        metadata.get_most_replayed(VIDEO)
