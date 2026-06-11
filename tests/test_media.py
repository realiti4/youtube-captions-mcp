"""Tests for the media tools (yt-dlp + ffmpeg mocked, never hits the network or the binary)."""

from __future__ import annotations

import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from mcp.server.fastmcp import Image
from yt_dlp.utils import DownloadError

from youtube_context_mcp import media
from youtube_context_mcp.media import (
    MediaError,
    _map_media_error,
    _resolve_stream,
    _sample_timestamps,
    _stream_url,
)

VIDEO = "dQw4w9WgXcQ"
JPEG = b"\xff\xd8\xff\xe0jpegbytes\xff\xd9"


@pytest.fixture(autouse=True)
def clear_stream_cache():
    media._stream_cache.clear()
    yield
    media._stream_cache.clear()


class FakeYDL:
    """Stand-in for yt-dlp's YoutubeDL context manager returning a canned info dict."""

    def __init__(self, info: dict):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def extract_info(self, url, download=False):
        return self._info


class FakeProc:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---- get_video_frame (public) ----


def test_get_video_frame_returns_jpeg_image(monkeypatch):
    monkeypatch.setattr(media, "_resolve_stream", lambda vid: ("http://stream", {}, 300.0))
    monkeypatch.setattr(media, "_extract_frame", lambda *a, **k: JPEG)
    out = media.get_video_frame(f"https://youtu.be/{VIDEO}", "0:30")
    assert isinstance(out, Image)
    assert out._mime_type == "image/jpeg"
    assert out.data == JPEG


def test_get_video_frame_parses_timestamp(monkeypatch):
    captured = {}

    def fake_extract(stream_url, seconds, max_width, headers=None):
        captured["seconds"] = seconds
        return JPEG

    monkeypatch.setattr(media, "_resolve_stream", lambda vid: ("http://stream", {}, 300.0))
    monkeypatch.setattr(media, "_extract_frame", fake_extract)
    media.get_video_frame(VIDEO, "1:30")
    assert captured["seconds"] == 90


@pytest.mark.parametrize("requested,expected", [(99999, 1280), (1, 64), (640, 640)])
def test_get_video_frame_clamps_max_width(monkeypatch, requested, expected):
    captured = {}

    def fake_extract(stream_url, seconds, max_width, headers=None):
        captured["max_width"] = max_width
        return JPEG

    monkeypatch.setattr(media, "_resolve_stream", lambda vid: ("http://stream", {}, 300.0))
    monkeypatch.setattr(media, "_extract_frame", fake_extract)
    media.get_video_frame(VIDEO, 10, max_width=requested)
    assert captured["max_width"] == expected


def test_get_video_frame_wraps_library_error(monkeypatch):
    def boom(video_id):
        raise DownloadError("ERROR: Private video")

    monkeypatch.setattr(media, "_resolve_stream", boom)
    with pytest.raises(MediaError):
        media.get_video_frame(VIDEO, 10)


def test_get_video_frame_invalid_video_raises_valueerror():
    with pytest.raises(ValueError):
        media.get_video_frame("not a youtube url", 10)


def test_get_video_frame_invalid_time_raises_valueerror():
    with pytest.raises(ValueError):
        media.get_video_frame(VIDEO, "nope")


# ---- _stream_url ----


def test_stream_url_returns_direct_url_headers_and_duration(monkeypatch):
    info = {"url": "http://direct", "http_headers": {"User-Agent": "yt"}, "duration": 213}
    monkeypatch.setattr(media, "YoutubeDL", lambda opts: FakeYDL(info))
    url, headers, duration = _stream_url(VIDEO)
    assert url == "http://direct"
    assert headers == {"User-Agent": "yt"}
    assert duration == 213


def test_stream_url_falls_back_to_first_video_format(monkeypatch):
    # Skips a URL-less entry and an audio-only entry (vcodec == "none"); picks the video format.
    info = {
        "formats": [
            {"url": None, "vcodec": "avc1"},
            {"url": "http://audio", "vcodec": "none", "acodec": "mp4a"},
            {"url": "http://video", "vcodec": "avc1", "http_headers": {"X": "1"}},
        ]
    }
    monkeypatch.setattr(media, "YoutubeDL", lambda opts: FakeYDL(info))
    url, headers, duration = _stream_url(VIDEO)
    assert url == "http://video"
    assert headers == {"X": "1"}
    assert duration is None


def test_stream_url_no_stream_raises(monkeypatch):
    monkeypatch.setattr(media, "YoutubeDL", lambda opts: FakeYDL({"formats": []}))
    with pytest.raises(MediaError):
        _stream_url(VIDEO)


# ---- _resolve_stream (TTL cache) ----


def test_resolve_stream_caches_within_ttl(monkeypatch):
    calls = {"n": 0}

    def fake_stream_url(video_id):
        calls["n"] += 1
        return "http://stream", {"User-Agent": "yt"}, 100.0

    monkeypatch.setattr(media, "_stream_url", fake_stream_url)
    assert _resolve_stream(VIDEO) == ("http://stream", {"User-Agent": "yt"}, 100.0)
    assert _resolve_stream(VIDEO) == ("http://stream", {"User-Agent": "yt"}, 100.0)
    assert calls["n"] == 1


def test_resolve_stream_expired_entry_is_refreshed(monkeypatch):
    calls = {"n": 0}

    def fake_stream_url(video_id):
        calls["n"] += 1
        return "http://stream", {}, 100.0

    monkeypatch.setattr(media, "_stream_url", fake_stream_url)
    _resolve_stream(VIDEO)
    expired = (time.monotonic() - 1, *media._stream_cache[VIDEO][1:])
    media._stream_cache[VIDEO] = expired
    _resolve_stream(VIDEO)
    assert calls["n"] == 2


# ---- _extract_frame ----


def test_extract_frame_missing_ffmpeg_raises(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda _: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)  # blocks the bundled fallback too
    with pytest.raises(MediaError) as exc:
        media._extract_frame("http://stream", 30, 640)
    assert "ffmpeg" in str(exc.value).lower()


def test_extract_frame_builds_command_and_returns_stdout(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc(returncode=0, stdout=JPEG)

    monkeypatch.setattr(media.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    out = media._extract_frame("http://stream", 30, 480, headers={"User-Agent": "yt"})
    assert out == JPEG
    cmd = captured["cmd"]
    # -ss must precede -i (fast input seek), and the scale/codec args must be present.
    assert cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-ss") + 1] == "30"
    assert "scale='min(iw,480)':-2" in cmd  # caps width, never upscales
    assert "mjpeg" in cmd
    assert "-user_agent" in cmd  # signed-URL header forwarded to ffmpeg


def test_extract_frame_failure_raises_without_leaking_stderr(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda cmd, **kw: FakeProc(returncode=1, stdout=b"", stderr=b"proxy http://u:secret@h"),
    )
    with pytest.raises(MediaError) as exc:
        media._extract_frame("http://stream", 30, 640)
    assert "secret" not in str(exc.value)


def test_extract_frame_timeout_raises(monkeypatch):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 45)

    monkeypatch.setattr(media.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", boom)
    with pytest.raises(MediaError):
        media._extract_frame("http://stream", 30, 640)


# ---- _ffmpeg_path ----


def test_ffmpeg_path_prefers_system_binary(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    assert media._ffmpeg_path() == "/usr/bin/ffmpeg"


def test_ffmpeg_path_falls_back_to_imageio(monkeypatch):
    fake = types.ModuleType("imageio_ffmpeg")
    fake.get_ffmpeg_exe = lambda: "/bundled/ffmpeg"
    monkeypatch.setattr(media.shutil, "which", lambda _: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", fake)
    assert media._ffmpeg_path() == "/bundled/ffmpeg"


def test_ffmpeg_path_missing_everywhere_raises(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda _: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)
    with pytest.raises(MediaError) as exc:
        media._ffmpeg_path()
    assert "youtube-context-mcp[media]" in str(exc.value)


# ---- _sample_timestamps ----


def test_sample_timestamps_midpoints():
    # Midpoint sampling avoids t=0 and never lands past the end.
    assert _sample_timestamps(0, 120.0, 4) == [15, 45, 75, 105]


def test_sample_timestamps_window():
    assert _sample_timestamps(60, 180, 4) == [75, 105, 135, 165]


def test_sample_timestamps_dedupes_short_window():
    assert _sample_timestamps(0, 2.0, 4) == [0, 1]


# ---- get_video_preview ----


def _patch_preview(monkeypatch, duration=120.0, extract=None, compose=None):
    monkeypatch.setattr(media, "_resolve_stream", lambda vid: ("http://stream", {}, duration))
    monkeypatch.setattr(media, "_extract_frame", extract or (lambda *a, **k: JPEG))
    monkeypatch.setattr(media, "_compose_sheet", compose or (lambda frames, cols: JPEG))


def test_get_video_preview_returns_legend_and_image(monkeypatch):
    captured = {}

    def fake_compose(frames, cols):
        captured["frames"], captured["cols"] = frames, cols
        return JPEG

    _patch_preview(monkeypatch, compose=fake_compose)
    legend, image = media.get_video_preview(VIDEO)
    assert isinstance(image, Image)
    assert image.data == JPEG
    assert len(captured["frames"]) == 12
    assert captured["cols"] == 4  # ceil(sqrt(12))
    assert legend.startswith("4x3 grid")
    # duration=120, 12 tiles -> midpoints at 5s, 15s, ..., 115s
    assert "1) 00:05" in legend
    assert "12) 01:55" in legend


@pytest.mark.parametrize("requested,expected", [(100, 24), (1, 4), (9, 9)])
def test_get_video_preview_clamps_tiles(monkeypatch, requested, expected):
    seen = []

    def fake_extract(stream_url, seconds, max_width, headers=None):
        seen.append(seconds)
        return JPEG

    _patch_preview(monkeypatch, duration=10_000.0, extract=fake_extract)
    media.get_video_preview(VIDEO, tiles=requested)
    assert len(seen) == expected


@pytest.mark.parametrize("requested,expected", [(99999, 480), (1, 160), (320, 320)])
def test_get_video_preview_clamps_tile_width(monkeypatch, requested, expected):
    widths = set()

    def fake_extract(stream_url, seconds, max_width, headers=None):
        widths.add(max_width)
        return JPEG

    _patch_preview(monkeypatch, extract=fake_extract)
    media.get_video_preview(VIDEO, tile_width=requested)
    assert widths == {expected}


def test_get_video_preview_unknown_duration_raises(monkeypatch):
    monkeypatch.setattr(media, "_resolve_stream", lambda vid: ("http://stream", {}, None))
    with pytest.raises(MediaError) as exc:
        media.get_video_preview(VIDEO)
    assert "duration" in str(exc.value).lower()


def test_get_video_preview_drops_failed_frames(monkeypatch):
    def flaky_extract(stream_url, seconds, max_width, headers=None):
        if seconds == 15:
            raise MediaError("flaky seek")
        return JPEG

    captured = {}

    def fake_compose(frames, cols):
        captured["frames"] = frames
        return JPEG

    _patch_preview(monkeypatch, extract=flaky_extract, compose=fake_compose)
    legend, _ = media.get_video_preview(VIDEO)
    assert len(captured["frames"]) == 11
    assert "00:15" not in legend
    assert "11) 01:55" in legend  # numbering follows the kept tiles


def test_get_video_preview_too_many_failures_raises(monkeypatch):
    def broken_extract(stream_url, seconds, max_width, headers=None):
        raise MediaError("no frame")

    _patch_preview(monkeypatch, extract=broken_extract)
    with pytest.raises(MediaError):
        media.get_video_preview(VIDEO)


def test_get_video_preview_wraps_library_error(monkeypatch):
    def boom(video_id):
        raise DownloadError("ERROR: Private video")

    monkeypatch.setattr(media, "_resolve_stream", boom)
    with pytest.raises(MediaError):
        media.get_video_preview(VIDEO)


def test_get_video_preview_invalid_video_raises_valueerror():
    with pytest.raises(ValueError):
        media.get_video_preview("not a youtube url")


def test_get_video_preview_window_samples_and_legend(monkeypatch):
    seen = []

    def fake_extract(stream_url, seconds, max_width, headers=None):
        seen.append(seconds)
        return JPEG

    _patch_preview(monkeypatch, duration=600.0, extract=fake_extract)
    legend, _ = media.get_video_preview(VIDEO, tiles=4, start="1:00", end="3:00")
    # Extraction is parallel, so observe the *set* of seeks; the legend's order is guaranteed.
    assert sorted(seen) == [75, 105, 135, 165]
    assert "01:00-03:00 of the video" in legend
    assert "1) 01:15" in legend
    assert "4) 02:45" in legend


def test_get_video_preview_end_clamped_to_duration(monkeypatch):
    _patch_preview(monkeypatch, duration=120.0)
    legend, _ = media.get_video_preview(VIDEO, end=10_000)
    # Clamping makes the window the whole video, so the legend doesn't claim a sub-range.
    assert "across the video" in legend
    assert "12) 01:55" in legend


def test_get_video_preview_start_past_end_of_video_raises(monkeypatch):
    _patch_preview(monkeypatch, duration=120.0)
    with pytest.raises(MediaError) as exc:
        media.get_video_preview(VIDEO, start=300)
    assert "02:00" in str(exc.value)  # tells the agent the actual duration


def test_get_video_preview_end_not_after_start_raises():
    with pytest.raises(ValueError):
        media.get_video_preview(VIDEO, start="2:00", end="1:00")


def test_get_video_preview_invalid_start_raises_valueerror():
    with pytest.raises(ValueError):
        media.get_video_preview(VIDEO, start="nope")


# ---- _compose_sheet ----


def test_compose_sheet_builds_tile_command(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        pattern = cmd[cmd.index("-i") + 1]
        captured["written"] = len(list(Path(pattern).parent.glob("*.jpg")))
        return FakeProc(returncode=0, stdout=JPEG)

    monkeypatch.setattr(media.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    out = media._compose_sheet([JPEG] * 6, cols=3)
    assert out == JPEG
    cmd = captured["cmd"]
    assert "tile=3x2" in cmd
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[-1] == "pipe:1"
    assert captured["written"] == 6  # all frames were on disk when ffmpeg ran


def test_compose_sheet_failure_raises_without_leaking_stderr(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda cmd, **kw: FakeProc(returncode=1, stdout=b"", stderr=b"proxy http://u:secret@h"),
    )
    with pytest.raises(MediaError) as exc:
        media._compose_sheet([JPEG], cols=1)
    assert "secret" not in str(exc.value)


def test_compose_sheet_timeout_raises(monkeypatch):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 45)

    monkeypatch.setattr(media.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", boom)
    with pytest.raises(MediaError):
        media._compose_sheet([JPEG], cols=1)


# ---- error mapping ----


@pytest.mark.parametrize(
    "message,needle",
    [
        ("Private video. Sign in if you've been granted access", "private"),
        ("Sign in to confirm your age", "age-restricted"),
        ("Sign in to confirm you're not a bot", "blocked"),
        ("This video is not available in your country", "region"),
        ("Video unavailable. This video has been removed", "unavailable"),
        ("Something unexpected went wrong", "could not capture a frame"),
    ],
)
def test_map_media_error_messages(message, needle):
    err = _map_media_error(DownloadError(f"ERROR: {message}"))
    assert isinstance(err, MediaError)
    assert needle in str(err).lower()


def test_map_media_error_proxy_does_not_leak_credentials():
    err = _map_media_error(DownloadError("ERROR: Unable to connect to proxy http://u:secret@h:80"))
    assert "secret" not in str(err)
    assert "proxy" in str(err).lower()
