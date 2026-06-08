"""Core video-metadata logic: fetch a video's details via yt-dlp and map them to a small shape.

This module wraps ``yt-dlp`` (which does the actual fetching) and is kept free of any MCP
concerns so it can be unit-tested in isolation. It only reads metadata
(``skip_download=True`` / ``extract_info(download=False)``), so no ffmpeg is required.
"""

from __future__ import annotations

import math
import os
from typing import TypedDict

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from youtube_context_mcp import links
from youtube_context_mcp.proxies import build_proxy_url
from youtube_context_mcp.transcripts import DEFAULT_TIMEOUT, _format_timestamp, extract_video_id


class MetadataError(Exception):
    """A user-facing error describing why a video's metadata could not be retrieved."""


class Chapter(TypedDict):
    title: str
    start: float
    end: float | None


class MostReplayedPeak(TypedDict):
    peak_label: str  # "mm:ss" of the hottest instant, e.g. "04:22"
    region_label: str  # "mm:ss-mm:ss" of the stretch, e.g. "04:10-04:45"
    peak_start_seconds: int  # the single hottest second in the region
    region_start_seconds: int  # start of the high-interest stretch
    region_end_seconds: int  # end of the high-interest stretch
    relative_intensity: float  # 0..1 within THIS video (rounded), 1.0 = hottest moment
    url: str  # watch?v=<id>&t=<region_start> -- lands at the start of the stretch
    chapter: str | None  # title of the chapter this moment falls in, if any


class MostReplayed(TypedDict):
    video_id: str
    has_data: bool
    duration_seconds: float | None
    peaks: list[MostReplayedPeak]
    profile: list[float]  # ~12 downsampled buckets, mean intensity 0..1; [] when no data
    note: str | None  # set when has_data is False (why there's nothing to return)


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


def get_most_replayed(video: str, top_n: int = 5) -> MostReplayed:
    """Fetch a video's "most replayed" peaks (YouTube's heatmap).

    Args:
        video: A YouTube URL or 11-character video ID.
        top_n: Maximum number of peak regions to return (clamped to 1..20).

    Raises:
        MetadataError: with a user-facing message if the data can't be retrieved.
    """
    video_id = extract_video_id(video)
    try:
        info = _extract_info(video_id)
    except (DownloadError, ExtractorError) as exc:
        raise _map_metadata_error(exc) from exc
    return _to_most_replayed(info, video_id, top_n)


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


# A heatmap segment counts as "hot" at or above this fraction of the per-video peak (which is
# normalised to 1.0). Tunable: too low merges everything into one region, too high drops real
# peaks. 0.5 is a reasonable v1 default; see ROADMAP for a possible local-maxima refinement.
_HEATMAP_THRESHOLD = 0.5
_PROFILE_BUCKETS = 12


def _chapter_at(seconds: float, info: dict) -> str | None:
    """Title of the chapter whose span contains ``seconds``, or ``None``.

    A chapter with no ``end_time`` is bounded by the next chapter's start (or runs to the end).
    """
    chapters = [c for c in (info.get("chapters") or []) if c.get("start_time") is not None]
    chapters.sort(key=lambda c: c["start_time"])
    for i, chapter in enumerate(chapters):
        start = chapter["start_time"]
        end = chapter.get("end_time")
        if end is None:
            end = chapters[i + 1]["start_time"] if i + 1 < len(chapters) else float("inf")
        if start <= seconds < end:
            return chapter.get("title") or None
    return None


def _downsample_profile(values: list[float], buckets: int) -> list[float]:
    """Reduce per-segment intensities to a coarse curve (mean per bucket, rounded 0..1)."""
    if not values:
        return []
    n = min(buckets, len(values))
    profile: list[float] = []
    for b in range(n):
        lo = b * len(values) // n
        hi = (b + 1) * len(values) // n
        chunk = values[lo:hi]
        profile.append(round(sum(chunk) / len(chunk), 3))
    return profile


def _to_most_replayed(info: dict, video_id: str, top_n: int = 5) -> MostReplayed:
    """Map yt-dlp's ``heatmap`` (~100 equal-width ``{start_time, end_time, value}`` segments,
    value normalised 0..1) to a small set of agent-actionable peak regions plus a coarse profile.

    Pure: does no network I/O, so it can be unit-tested with a synthetic info dict.
    """
    duration = info.get("duration")
    segments = info.get("heatmap")
    if not segments:
        return MostReplayed(
            video_id=video_id,
            has_data=False,
            duration_seconds=duration,
            peaks=[],
            profile=[],
            note=(
                "YouTube has no 'most replayed' data for this video "
                "(common for newer or low-traffic videos and some Shorts)."
            ),
        )

    top_n = max(1, min(int(top_n), 20))
    values = [float(s.get("value") or 0.0) for s in segments]

    # Hot regions: maximal runs of consecutive segments at or above the threshold.
    regions: list[tuple[int, int]] = []  # (first_index, last_index), inclusive
    run_start: int | None = None
    for i, value in enumerate(values):
        if value >= _HEATMAP_THRESHOLD:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            regions.append((run_start, i - 1))
            run_start = None
    if run_start is not None:
        regions.append((run_start, len(values) - 1))

    # Flat heatmap (nothing clears the bar): fall back to the single hottest segment so we still
    # return the top moment when data exists.
    if not regions:
        peak_i = max(range(len(values)), key=values.__getitem__)
        regions = [(peak_i, peak_i)]

    def peak_index(region: tuple[int, int]) -> int:
        lo, hi = region
        return max(range(lo, hi + 1), key=values.__getitem__)

    # Rank by peak intensity, keep the strongest top_n, then order by time for readable output.
    regions.sort(key=lambda r: values[peak_index(r)], reverse=True)
    regions = regions[:top_n]
    regions.sort(key=lambda r: r[0])

    peaks: list[MostReplayedPeak] = []
    for lo, hi in regions:
        pi = peak_index((lo, hi))
        region_start = float(segments[lo].get("start_time") or 0.0)
        region_end = float(segments[hi].get("end_time") or region_start)
        peak_start = float(segments[pi].get("start_time") or region_start)
        # Floor the start (jump just before the moment) but ceil the end, so a sub-second region
        # (e.g. 0.0-0.6s on a short video) reads as 00:00-00:01 rather than collapsing to 00:00.
        region_start_seconds = int(region_start)
        region_end_seconds = math.ceil(region_end)
        peaks.append(
            MostReplayedPeak(
                peak_label=_format_timestamp(peak_start),
                region_label=(
                    f"{_format_timestamp(region_start)}-{_format_timestamp(region_end_seconds)}"
                ),
                peak_start_seconds=int(peak_start),
                region_start_seconds=region_start_seconds,
                region_end_seconds=region_end_seconds,
                relative_intensity=round(values[pi], 3),
                url=links.build_video_link(video_id, region_start_seconds),
                chapter=_chapter_at(peak_start, info),
            )
        )

    return MostReplayed(
        video_id=video_id,
        has_data=True,
        duration_seconds=duration,
        peaks=peaks,
        profile=_downsample_profile(values, _PROFILE_BUCKETS),
        note=None,
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
