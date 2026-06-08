"""Core transcript logic: URL parsing, fetching, formatting, and error mapping.

This module wraps ``youtube-transcript-api`` (which does the actual fetching) and is kept free
of any MCP concerns so it can be unit-tested in isolation.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import TypedDict
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import (
    AgeRestricted,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    NotTranslatable,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    TranslationLanguageNotAvailable,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

from youtube_context_mcp.proxies import build_proxy_config

DEFAULT_TIMEOUT = 20.0

_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Hosts we accept, plus their subdomains (www., m., music.).
_ALLOWED_HOSTS = ("youtube.com", "youtube-nocookie.com", "youtu.be")
# Path prefixes that carry the video ID as the following path segment.
_PATH_PREFIXES = {"embed", "shorts", "live", "v"}


class TranscriptError(Exception):
    """A user-facing error describing why a transcript could not be retrieved."""


class TranscriptSegment(TypedDict):
    start: float  # seconds from the start of the video
    text: str


class _TimeoutSession(requests.Session):
    """A ``requests`` session that applies a default timeout to every request.

    ``youtube-transcript-api`` does not set request timeouts itself, so without this a stuck
    YouTube request could hang the agent indefinitely.
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self._timeout = timeout

    def request(self, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self._timeout)
        return super().request(*args, **kwargs)


def _host_allowed(host: str) -> bool:
    return any(host == allowed or host.endswith("." + allowed) for allowed in _ALLOWED_HOSTS)


def extract_video_id(value: str) -> str:
    """Extract an 11-character YouTube video ID from a URL or a bare ID.

    Only genuine YouTube hosts are accepted (youtube.com and its subdomains, youtu.be, and
    youtube-nocookie.com), so look-alike hosts such as ``notyoutube.com`` are rejected.

    Raises:
        ValueError: if no video ID can be found on a recognised YouTube host.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("No video URL or ID provided.")
    if _BARE_ID.match(value):
        return value

    # Prepend a scheme when missing so urlparse populates the host (e.g. "youtu.be/<id>").
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.hostname or "").lower()

    if _host_allowed(host):
        if host == "youtu.be" or host.endswith(".youtu.be"):
            candidate = parsed.path.lstrip("/").split("/", 1)[0]
            if _BARE_ID.match(candidate):
                return candidate
        else:
            query_v = parse_qs(parsed.query).get("v", [])
            if query_v and _BARE_ID.match(query_v[0]):
                return query_v[0]
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in _PATH_PREFIXES and _BARE_ID.match(parts[1]):
                return parts[1]

    raise ValueError(
        f"Could not extract a YouTube video ID from {value!r}. "
        "Pass a watch / youtu.be / shorts / embed / live URL, or an 11-character video ID."
    )


def _build_api() -> YouTubeTranscriptApi:
    """Construct a ``YouTubeTranscriptApi`` with a timeout session and env-driven proxy."""
    try:
        timeout = float(os.environ.get("YT_TRANSCRIPT_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    session = _TimeoutSession(timeout=timeout)
    return YouTubeTranscriptApi(proxy_config=build_proxy_config(), http_client=session)


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# Captions are tiny (~1-3s) fragments; grouping them into coarser blocks keeps the timestamped
# form cheap on tokens while staying precise enough to jump to where a topic starts.
_CHUNK_SECONDS = 15.0


def _format_chunked(snippets) -> str:
    """Group snippets into ~``_CHUNK_SECONDS`` blocks, one ``[mm:ss] text`` line each.

    Each block is timestamped with its first snippet's start, so the line composes directly with
    ``build_video_link`` (which accepts ``"mm:ss"``).
    """
    lines: list[str] = []
    block_start: float | None = None
    block_parts: list[str] = []
    for snippet in snippets:
        text = snippet.text.replace("\n", " ").strip()
        if not text:
            continue
        if block_start is not None and snippet.start - block_start >= _CHUNK_SECONDS:
            lines.append(f"[{_format_timestamp(block_start)}] {' '.join(block_parts)}")
            block_start = None
            block_parts = []
        if block_start is None:
            block_start = snippet.start
        block_parts.append(text)
    if block_parts:
        lines.append(f"[{_format_timestamp(block_start)}] {' '.join(block_parts)}")
    return "\n".join(lines)


def _format_transcript(snippets, include_timestamps: bool) -> str:
    if include_timestamps:
        return _format_chunked(snippets)
    return " ".join(snippet.text.replace("\n", " ") for snippet in snippets).strip()


def _fetch_snippets(
    video: str,
    languages: Sequence[str],
    translate_to: str | None,
):
    """Fetch the raw transcript snippets, mapping any library error to a ``TranscriptError``.

    Shared by :func:`get_transcript` and :func:`get_transcript_segments` so the fetch / translate /
    error-mapping path lives in exactly one place.
    """
    video_id = extract_video_id(video)
    languages = list(languages) or ["en"]
    api = _build_api()
    try:
        if translate_to:
            transcript = api.list(video_id).find_transcript(languages)
            return transcript.translate(translate_to).fetch()
        return api.fetch(video_id, languages=languages)
    except Exception as exc:  # noqa: BLE001 - re-raised as a friendly TranscriptError
        raise _map_error(exc, requested_languages=languages, translate_to=translate_to) from exc


def get_transcript(
    video: str,
    languages: Sequence[str] = ("en",),
    include_timestamps: bool = False,
    translate_to: str | None = None,
) -> str:
    """Fetch a video's transcript as text.

    Args:
        video: A YouTube URL or 11-character video ID.
        languages: Preferred language codes in priority order.
        include_timestamps: When true, group the transcript into ~15s blocks, each prefixed with
            ``[mm:ss]`` / ``[h:mm:ss]`` -- handy for locating a topic and building a link.
        translate_to: Optional ISO language code to translate the transcript into.

    Raises:
        TranscriptError: with a user-facing message if the transcript can't be retrieved.
    """
    snippets = _fetch_snippets(video, languages, translate_to)
    return _format_transcript(snippets, include_timestamps)


def get_transcript_segments(
    video: str,
    languages: Sequence[str] = ("en",),
    translate_to: str | None = None,
) -> list[TranscriptSegment]:
    """Fetch a video's transcript as structured ``{start, text}`` segments.

    Not exposed as an MCP tool: for agent-facing "where is X / link to it" use
    ``get_transcript(include_timestamps=True)`` (chunked + cheap on tokens) instead. This function
    is kept as a library building block for future *programmatic* use (e.g. an analysis tool) that
    wants exact per-snippet data rather than text.

    Unlike :func:`get_transcript` (which flattens to text), this preserves each snippet's exact
    float ``start``, which feeds straight into ``build_video_link`` with no timestamp re-parsing.
    (``duration`` is intentionally omitted to keep the per-snippet token cost down -- a YouTube
    transcript has many short snippets, so repeated fields add up fast.)

    Args:
        video: A YouTube URL or 11-character video ID.
        languages: Preferred language codes in priority order.
        translate_to: Optional ISO language code to translate the transcript into.

    Raises:
        TranscriptError: with a user-facing message if the transcript can't be retrieved.
    """
    snippets = _fetch_snippets(video, languages, translate_to)
    return [TranscriptSegment(start=s.start, text=s.text) for s in snippets]


def list_transcripts(video: str) -> dict:
    """List the transcripts available for a video.

    Returns a dict with a ``transcripts`` list (one entry per available track) and a top-level
    ``translation_languages`` list (the same target set applies to every translatable track,
    so it is reported once instead of per entry).

    Raises:
        TranscriptError: with a user-facing message if the listing can't be retrieved.
    """
    video_id = extract_video_id(video)
    api = _build_api()
    try:
        transcript_list = api.list(video_id)
    except Exception as exc:  # noqa: BLE001 - re-raised as a friendly TranscriptError
        raise _map_error(exc) from exc

    transcripts_meta: list[dict] = []
    translation_languages: list[dict] = []
    for transcript in transcript_list:
        transcripts_meta.append(
            {
                "language": transcript.language,
                "language_code": transcript.language_code,
                "is_generated": transcript.is_generated,
                "is_translatable": transcript.is_translatable,
            }
        )
        if transcript.is_translatable and not translation_languages:
            translation_languages = [
                {
                    "language": lang.language,
                    "language_code": lang.language_code,
                }
                for lang in transcript.translation_languages
            ]

    return {
        "transcripts": transcripts_meta,
        "translation_languages": translation_languages,
    }


def _map_error(
    exc: Exception,
    *,
    requested_languages: Sequence[str] | None = None,
    translate_to: str | None = None,
) -> TranscriptError:
    """Translate a library exception into a concise, user-facing ``TranscriptError``.

    Messages are built from our own inputs rather than the exception's private fields. Checks
    are ordered most-specific first, since several of these exceptions subclass one another.
    """
    langs = list(requested_languages or []) or ["en"]
    if isinstance(exc, NoTranscriptFound):
        return TranscriptError(
            f"No transcript found for languages {langs}. "
            "Call list_transcripts to see which languages are available for this video."
        )
    if isinstance(exc, TranscriptsDisabled):
        return TranscriptError(
            "Subtitles are disabled for this video, so no transcript can be fetched."
        )
    if isinstance(exc, (NotTranslatable, TranslationLanguageNotAvailable)):
        return TranscriptError(
            f"This transcript can't be translated to {translate_to!r}. "
            "Call list_transcripts to see the available translation languages."
        )
    if isinstance(exc, AgeRestricted):
        return TranscriptError(
            "This video is age-restricted, so its transcript can't be retrieved."
        )
    if isinstance(exc, PoTokenRequired):
        return TranscriptError(
            "YouTube requires a PO token for this video, which isn't supported. "
            "Retrying later or routing through a proxy sometimes helps."
        )
    if isinstance(exc, InvalidVideoId):
        return TranscriptError("That doesn't look like a valid YouTube video ID.")
    if isinstance(exc, (IpBlocked, RequestBlocked)):
        return TranscriptError(
            "YouTube blocked this request (likely an IP block on this network). "
            "Set WEBSHARE_PROXY_USERNAME / WEBSHARE_PROXY_PASSWORD (or "
            "YT_TRANSCRIPT_HTTP_PROXY / YT_TRANSCRIPT_HTTPS_PROXY) to route through a proxy. "
            "See the README."
        )
    if isinstance(exc, VideoUnavailable):
        return TranscriptError("This video is unavailable.")
    return TranscriptError(f"Could not retrieve transcript: {exc}")
