"""Media tools: resolve a video stream URL via yt-dlp, extract frames with ffmpeg.

Two tools live here: ``get_video_frame`` (one still at a moment) and ``get_video_preview``
(a contact sheet -- N frames sampled across the video, tiled into one grid image). Unlike
:mod:`metadata` they need an ``ffmpeg`` binary: the system one, or the static build bundled by
``imageio-ffmpeg`` when installed via the ``[media]`` extra. yt-dlp only resolves a direct stream
URL here; ffmpeg seeks and pipes out downscaled JPEGs. Kept free of MCP concerns apart from the
:class:`~mcp.server.fastmcp.Image` return type, so the logic can be unit-tested in isolation
(tests monkeypatch :func:`_stream_url` / :func:`_extract_frame` / :func:`_compose_sheet`).
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from mcp.server.fastmcp import Image
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from youtube_context_mcp.links import _parse_timestamp
from youtube_context_mcp.metadata import _timeout
from youtube_context_mcp.proxies import build_proxy_url
from youtube_context_mcp.transcripts import _format_timestamp, extract_video_id


class MediaError(Exception):
    """A user-facing error describing why a frame could not be captured."""


_DEFAULT_MAX_WIDTH = 640
_MIN_WIDTH = 64
_MAX_WIDTH = 1280
_JPEG_QUALITY = 5  # ffmpeg -q:v (2 = best .. 31 = worst); ~5 is a good size/detail balance
# A frame only needs the video track, so a video-only DASH stream is fine and avoids the heavier
# progressive formats. Capped at 720p to keep the seek/decode cheap.
_FRAME_FORMAT = "best[height<=720][vcodec!=none]/bestvideo[height<=720]/best"
_DEFAULT_FRAME_TIMEOUT = 45.0  # seek + decode over the network takes longer than a metadata fetch

_DEFAULT_TILES = 12
_MIN_TILES = 4
_MAX_TILES = 24
_DEFAULT_TILE_WIDTH = 320  # per-tile px; 12 tiles of 320x180 ~= a 1280x540 sheet, cheap on vision
_MIN_TILE_WIDTH = 160
_MAX_TILE_WIDTH = 480
_PREVIEW_MAX_WORKERS = 6  # parallel ffmpeg seeks; each is its own connection to the stream
# Signed googlevideo URLs stay valid for hours; 10 minutes is safely inside that and saves the
# ~1-3s yt-dlp resolution when an agent grabs several frames/sheets from the same video.
_STREAM_CACHE_TTL = 600.0


def _ffmpeg_path() -> str:
    """Locate ffmpeg: the system binary first, else the static build bundled by imageio-ffmpeg
    (the ``[media]`` extra).

    Raises:
        MediaError: if neither is available.
    """
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg  # optional dep from the [media] extra; ships a static ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        pass
    raise MediaError(
        "ffmpeg is not installed or not on PATH; it's required to capture frames. "
        "Install it (e.g. `apt install ffmpeg`, `brew install ffmpeg`, or see ffmpeg.org), "
        'or get a bundled build with `pip install "youtube-context-mcp[media]"`.'
    )


def _ffmpeg_timeout() -> float:
    try:
        return float(os.environ.get("YT_FRAME_TIMEOUT", _DEFAULT_FRAME_TIMEOUT))
    except ValueError:
        return _DEFAULT_FRAME_TIMEOUT


def _build_opts() -> dict:
    """Build yt-dlp options that resolve a single stream URL, with the env-driven proxy/timeout."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": _timeout(),
        "format": _FRAME_FORMAT,
    }
    proxy = build_proxy_url()
    if proxy:
        opts["proxy"] = proxy
    return opts


def _stream_url(video_id: str) -> tuple[str, dict, float | None]:
    """Resolve a direct stream URL (plus HTTP headers and duration) for a video via yt-dlp.

    Isolated so tests can monkeypatch it. The headers matter: YouTube's googlevideo URLs are
    signed and tied to a User-Agent, so passing them to ffmpeg avoids ``403`` responses. The
    duration (seconds; ``None`` when unknown, e.g. live) drives preview-sheet sampling.

    Raises:
        MediaError: if no downloadable stream URL can be resolved (e.g. a live stream).
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    with YoutubeDL(_build_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    direct = info.get("url")
    headers = dict(info.get("http_headers") or {})
    if not direct:
        # The selector returned a merge (separate video/audio): take the first format that carries
        # a video track, skipping audio-only / storyboard entries (vcodec == "none") so ffmpeg has
        # a frame to grab.
        for fmt in info.get("requested_formats") or info.get("formats") or []:
            if fmt.get("url") and fmt.get("vcodec") != "none":
                direct = fmt["url"]
                headers = dict(fmt.get("http_headers") or headers)
                break
    if not direct:
        raise MediaError(
            "Couldn't resolve a video stream for this video "
            "(it may be a live stream or have no downloadable formats)."
        )
    return direct, headers, info.get("duration")


_stream_cache: dict[str, tuple[float, str, dict, float | None]] = {}


def _resolve_stream(video_id: str) -> tuple[str, dict, float | None]:
    """:func:`_stream_url` behind a short TTL cache, so several frames / a preview sheet from
    the same video pay the yt-dlp resolution only once."""
    now = time.monotonic()
    for key in [k for k, v in _stream_cache.items() if v[0] <= now]:
        del _stream_cache[key]
    cached = _stream_cache.get(video_id)
    if cached:
        return cached[1], cached[2], cached[3]
    url, headers, duration = _stream_url(video_id)
    _stream_cache[video_id] = (now + _STREAM_CACHE_TTL, url, headers, duration)
    return url, headers, duration


def _extract_frame(
    stream_url: str, seconds: int, max_width: int, headers: dict | None = None
) -> bytes:
    """Seek to ``seconds`` and grab one downscaled JPEG frame via ffmpeg. Isolated for tests.

    ``-ss`` is placed **before** ``-i`` for a fast keyframe input-seek -- essential over a network
    stream (output-side seeking would decode from the start). The trade-off is that the frame lands
    on the nearest preceding keyframe, so it can be off by a second or two; fine for a screenshot.

    Raises:
        MediaError: if ffmpeg is missing, times out, or can't produce a frame.
    """
    cmd = [_ffmpeg_path(), "-nostdin", "-loglevel", "error"]
    headers = headers or {}
    user_agent = headers.get("User-Agent")
    if user_agent:
        cmd += ["-user_agent", user_agent]
    extra = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k != "User-Agent")
    if extra:
        cmd += ["-headers", extra]
    cmd += [
        "-ss",
        str(seconds),
        "-i",
        stream_url,
        "-frames:v",
        "1",
        "-vf",
        # Cap width at max_width but never upscale (min(iw,…)); -2 keeps the aspect ratio with an
        # even height. The single quotes protect the comma in min() from the filtergraph parser.
        f"scale='min(iw,{max_width})':-2",
        "-f",
        "image2",
        "-c:v",
        "mjpeg",
        "-q:v",
        str(_JPEG_QUALITY),
        "pipe:1",
    ]

    env = dict(os.environ)
    proxy = build_proxy_url()  # best-effort; the README notes proxies are rarely needed locally
    if proxy:
        env["http_proxy"] = env["https_proxy"] = proxy

    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=_ffmpeg_timeout(), env=env, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(
            "Timed out capturing the frame; try a smaller max_width or an earlier timestamp."
        ) from exc

    if proc.returncode != 0 or not proc.stdout:
        # Never echo proc.stderr: a proxy URL with credentials may be embedded in ffmpeg's output.
        raise MediaError(
            "ffmpeg could not capture a frame at that timestamp "
            "(it may be past the end of the video, or the stream couldn't be read)."
        )
    return proc.stdout


def get_video_frame(
    video: str, at: int | float | str, max_width: int = _DEFAULT_MAX_WIDTH
) -> Image:
    """Capture a single still frame from a YouTube video at ``at`` and return it as an image.

    Args:
        video: A YouTube URL or 11-character video ID.
        at: The moment to capture -- seconds (int/float) or a ``"mm:ss"`` / ``"h:mm:ss"`` string.
        max_width: Max width in pixels of the returned JPEG (clamped to 64..1280); smaller keeps
            the response cheap on a vision model's image budget.

    Returns:
        An :class:`~mcp.server.fastmcp.Image` (JPEG) the MCP client renders as image content.

    Raises:
        ValueError: if the video ID or ``at`` can't be parsed.
        MediaError: with a user-facing message if the frame can't be captured.
    """
    video_id = extract_video_id(video)
    seconds = _parse_timestamp(at)
    max_width = max(_MIN_WIDTH, min(int(max_width), _MAX_WIDTH))
    try:
        stream_url, headers, _duration = _resolve_stream(video_id)
    except (DownloadError, ExtractorError) as exc:
        raise _map_media_error(exc) from exc
    data = _extract_frame(stream_url, seconds, max_width, headers)
    return Image(data=data, format="jpeg")


def _sample_timestamps(start: float, end: float, tiles: int) -> list[int]:
    """Pick ``tiles`` evenly spread sample points in ``[start, end)`` as whole seconds, deduped.

    Midpoint sampling (``start + span * (i + 0.5) / tiles``) avoids both the window's first
    instant (t=0 intro/branding on a full-video sheet) and a past-the-end seek at the tail;
    deduping handles windows shorter than the tile count.
    """
    span = end - start
    out: list[int] = []
    for i in range(tiles):
        t = int(start + span * (i + 0.5) / tiles)
        if not out or t != out[-1]:
            out.append(t)
    return out


def _compose_sheet(frames: list[bytes], cols: int) -> bytes:
    """Tile JPEG frames into one grid image (left-to-right, top-to-bottom) via ffmpeg's ``tile``
    filter -- no Pillow needed. Isolated for tests.

    Raises:
        MediaError: if ffmpeg is missing, times out, or can't compose the sheet.
    """
    rows = math.ceil(len(frames) / cols)
    cmd = [_ffmpeg_path(), "-nostdin", "-loglevel", "error"]
    with tempfile.TemporaryDirectory() as tmp:
        for i, frame in enumerate(frames):
            with open(os.path.join(tmp, f"f_{i:03d}.jpg"), "wb") as fh:
                fh.write(frame)
        cmd += [
            "-f",
            "image2",
            "-start_number",
            "0",
            "-i",
            os.path.join(tmp, "f_%03d.jpg"),
            "-filter_complex",
            f"tile={cols}x{rows}",
            "-frames:v",
            "1",
            "-f",
            "image2",
            "-c:v",
            "mjpeg",
            "-q:v",
            str(_JPEG_QUALITY),
            "pipe:1",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=_ffmpeg_timeout(), check=False)
        except subprocess.TimeoutExpired as exc:
            raise MediaError("Timed out composing the preview sheet.") from exc
    if proc.returncode != 0 or not proc.stdout:
        # Never echo proc.stderr: same credential-leak rule as _extract_frame.
        raise MediaError("ffmpeg could not compose the preview sheet from the captured frames.")
    return proc.stdout


def get_video_preview(
    video: str,
    tiles: int = _DEFAULT_TILES,
    tile_width: int = _DEFAULT_TILE_WIDTH,
    start: int | float | str | None = None,
    end: int | float | str | None = None,
) -> tuple[str, Image]:
    """Sample frames evenly across a video (or a window of it) and tile them into one
    contact-sheet image.

    Args:
        video: A YouTube URL or 11-character video ID.
        tiles: How many frames to sample (clamped to 4..24).
        tile_width: Width in pixels of each tile (clamped to 160..480).
        start: Optional window start -- seconds or a ``"mm:ss"`` / ``"h:mm:ss"`` string.
            Defaults to the beginning of the video.
        end: Optional window end, same forms. Defaults to (and is clamped to) the video's end.

    Returns:
        A ``(legend, image)`` pair: the legend maps tile order (left-to-right, top-to-bottom) to
        ``mm:ss`` timestamps, and the image is the JPEG grid. Returned together so the MCP layer
        can emit them as one text + one image content block.

    Raises:
        ValueError: if the video ID, ``start`` or ``end`` can't be parsed, or ``end <= start``.
        MediaError: with a user-facing message if the sheet can't be built.
    """
    video_id = extract_video_id(video)
    tiles = max(_MIN_TILES, min(int(tiles), _MAX_TILES))
    tile_width = max(_MIN_TILE_WIDTH, min(int(tile_width), _MAX_TILE_WIDTH))
    start_s = _parse_timestamp(start) if start is not None else 0
    end_s = _parse_timestamp(end) if end is not None else None
    if end_s is not None and end_s <= start_s:
        raise ValueError("The preview window's end must be after its start.")
    try:
        stream_url, headers, duration = _resolve_stream(video_id)
    except (DownloadError, ExtractorError) as exc:
        raise _map_media_error(exc) from exc
    if not duration:
        raise MediaError(
            "This video's duration is unknown (it may be a live stream), "
            "so a preview sheet can't be built."
        )
    if start_s >= duration:
        raise MediaError(
            f"The preview window starts at or past the end of the video "
            f"(its duration is {_format_timestamp(duration)})."
        )
    end_s = duration if end_s is None else min(end_s, duration)

    timestamps = _sample_timestamps(start_s, end_s, tiles)

    def grab(t: int) -> bytes | None:
        try:
            return _extract_frame(stream_url, t, tile_width, headers)
        except MediaError:
            return None  # a flaky seek drops one tile, not the whole sheet

    with ThreadPoolExecutor(max_workers=min(len(timestamps), _PREVIEW_MAX_WORKERS)) as pool:
        results = list(pool.map(grab, timestamps))

    kept = [(t, frame) for t, frame in zip(timestamps, results) if frame is not None]
    if len(kept) * 2 < len(timestamps):
        raise MediaError(
            "Could not capture enough frames for a preview sheet "
            "(the stream may be unreadable on this network)."
        )

    cols = math.ceil(math.sqrt(len(kept)))
    rows = math.ceil(len(kept) / cols)
    sheet = _compose_sheet([frame for _, frame in kept], cols)
    if start_s > 0 or end_s < duration:
        scope = f"{_format_timestamp(start_s)}-{_format_timestamp(end_s)} of the video"
    else:
        scope = "the video"
    legend = (
        f"{cols}x{rows} grid of frames sampled across {scope}, read left-to-right then "
        "top-to-bottom. Tile timestamps: "
        + "  ".join(f"{i + 1}) {_format_timestamp(t)}" for i, (t, _) in enumerate(kept))
    )
    return legend, Image(data=sheet, format="jpeg")


def _map_media_error(exc: Exception) -> MediaError:
    """Translate a yt-dlp error into a concise, user-facing ``MediaError``.

    Mirrors :func:`metadata._map_metadata_error`: yt-dlp lacks granular typed exceptions, so match
    conservatively on the message text. Order matters -- the age check precedes the bot-block check
    since both mention "Sign in to confirm ...".
    """
    message = str(exc).lower()
    if "private video" in message:
        return MediaError("This video is private, so a frame can't be captured.")
    if "confirm your age" in message or "age-restricted" in message:
        return MediaError("This video is age-restricted, so a frame can't be captured.")
    if "not a bot" in message or "sign in to confirm" in message or "blocked" in message:
        return MediaError(
            "YouTube blocked this request (likely an IP block on this network). "
            "Set WEBSHARE_PROXY_USERNAME / WEBSHARE_PROXY_PASSWORD (or "
            "YT_TRANSCRIPT_HTTP_PROXY / YT_TRANSCRIPT_HTTPS_PROXY) to route through a proxy. "
            "See the README."
        )
    if "not available in your country" in message or "geo restrict" in message:
        return MediaError("This video isn't available in this region.")
    if "live" in message and "not" in message:
        return MediaError("This looks like an ongoing live stream, so a frame can't be captured.")
    if "unavailable" in message or "removed" in message or "has been terminated" in message:
        return MediaError("This video is unavailable.")
    if "proxy" in message:
        # Don't echo the exception: a proxy URL with credentials may be embedded in it.
        return MediaError("Could not connect through the configured proxy. Check its settings.")
    return MediaError(f"Could not capture a frame: {exc}")
