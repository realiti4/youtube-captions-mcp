"""Core video-metadata logic: fetch a video's details via yt-dlp and map them to a small shape.

This module wraps ``yt-dlp`` (which does the actual fetching) and is kept free of any MCP
concerns so it can be unit-tested in isolation. It only reads metadata
(``skip_download=True`` / ``extract_info(download=False)``), so no ffmpeg is required.
"""

from __future__ import annotations

import os
from typing import TypedDict

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from youtube_context_mcp.proxies import build_proxy_url
from youtube_context_mcp.transcripts import DEFAULT_TIMEOUT, extract_video_id


class MetadataError(Exception):
    """A user-facing error describing why a video's metadata could not be retrieved."""


class Chapter(TypedDict):
    title: str
    start: float
    end: float | None


class VideoMetadata(TypedDict):
    video_id: str
    title: str | None
    channel: str | None
    channel_url: str | None
    upload_date: str | None  # ISO YYYY-MM-DD
    duration_seconds: float | None
    view_count: int | None
    like_count: int | None
    description: str | None
    tags: list[str]
    chapters: list[Chapter]
    thumbnail: str | None
    webpage_url: str | None


def _timeout() -> float:
    try:
        return float(os.environ.get("YT_TRANSCRIPT_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def _build_opts() -> dict:
    """Build yt-dlp options: quiet, metadata-only, with the env-driven proxy/timeout."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": _timeout(),
    }
    proxy = build_proxy_url()
    if proxy:
        opts["proxy"] = proxy
    return opts


def _extract_info(video_id: str) -> dict:
    """Fetch raw metadata for a video via yt-dlp. Isolated so tests can monkeypatch it."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    with YoutubeDL(_build_opts()) as ydl:
        return ydl.extract_info(url, download=False)


def get_video_metadata(video: str, include_description: bool = False) -> VideoMetadata:
    """Fetch a video's metadata.

    Args:
        video: A YouTube URL or 11-character video ID.
        include_description: Include the (often long) description when true; otherwise the
            ``description`` field is ``None`` to keep the common lookup cheap.

    Raises:
        MetadataError: with a user-facing message if the metadata can't be retrieved.
    """
    video_id = extract_video_id(video)
    try:
        info = _extract_info(video_id)
    except (DownloadError, ExtractorError) as exc:
        raise _map_metadata_error(exc) from exc
    return _to_metadata(info, video_id, include_description)


def _format_upload_date(value: str | None) -> str | None:
    """Normalise yt-dlp's ``YYYYMMDD`` upload date to ISO ``YYYY-MM-DD``."""
    if value and len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value or None


def _format_chapters(info: dict) -> list[Chapter]:
    chapters: list[Chapter] = []
    for chapter in info.get("chapters") or []:
        start = chapter.get("start_time")
        if start is None:
            continue  # a chapter with no start time can't be positioned; skip it
        chapters.append(
            Chapter(
                title=chapter.get("title") or "",
                start=start,
                end=chapter.get("end_time"),
            )
        )
    return chapters


def _to_metadata(info: dict, video_id: str, include_description: bool = False) -> VideoMetadata:
    """Map yt-dlp's large info dict to our small, JSON-clean shape.

    yt-dlp omits or names fields inconsistently, so read defensively with fallbacks.
    """
    return VideoMetadata(
        video_id=video_id,
        title=info.get("title"),
        channel=info.get("channel") or info.get("uploader"),
        channel_url=info.get("channel_url") or info.get("uploader_url"),
        upload_date=_format_upload_date(info.get("upload_date")),
        duration_seconds=info.get("duration"),
        view_count=info.get("view_count"),
        like_count=info.get("like_count"),
        description=info.get("description") if include_description else None,
        tags=list(info.get("tags") or []),
        chapters=_format_chapters(info),
        thumbnail=info.get("thumbnail"),
        webpage_url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}",
    )


def _map_metadata_error(exc: Exception) -> MetadataError:
    """Translate a yt-dlp error into a concise, user-facing ``MetadataError``.

    yt-dlp lacks granular typed exceptions, so we match conservatively on its message text.
    Order matters: the age check must precede the bot-block check, since both mention
    "Sign in to confirm ...".
    """
    message = str(exc).lower()
    if "private video" in message:
        return MetadataError("This video is private, so its metadata can't be retrieved.")
    if "confirm your age" in message or "age-restricted" in message:
        return MetadataError("This video is age-restricted, so its metadata can't be retrieved.")
    if "not a bot" in message or "sign in to confirm" in message or "blocked" in message:
        return MetadataError(
            "YouTube blocked this request (likely an IP block on this network). "
            "Set WEBSHARE_PROXY_USERNAME / WEBSHARE_PROXY_PASSWORD (or "
            "YT_TRANSCRIPT_HTTP_PROXY / YT_TRANSCRIPT_HTTPS_PROXY) to route through a proxy. "
            "See the README."
        )
    if "not available in your country" in message or "geo restrict" in message:
        return MetadataError("This video isn't available in this region.")
    if "unavailable" in message or "removed" in message or "has been terminated" in message:
        return MetadataError("This video is unavailable.")
    if "proxy" in message:
        # Don't echo the exception: a proxy URL with credentials may be embedded in it.
        return MetadataError("Could not connect through the configured proxy. Check its settings.")
    return MetadataError(f"Could not retrieve metadata: {exc}")
