"""Core transcript logic: URL parsing, fetching, formatting, and error mapping.

This module wraps ``youtube-transcript-api`` (which does the actual fetching) and is kept free
of any MCP concerns so it can be unit-tested in isolation.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

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

from yt_transcript_mcp.proxies import build_proxy_config

DEFAULT_TIMEOUT = 20.0

_VIDEO_ID = r"(?P<id>[A-Za-z0-9_-]{11})"
_BARE_ID = re.compile(rf"^{_VIDEO_ID}$")
# Patterns for the common YouTube URL shapes. Subdomains (www., m., music.) all contain the
# "youtube.com" substring, so matching it as a substring covers them.
_URL_PATTERNS = [
    re.compile(p)
    for p in (
        rf"(?:youtube\.com|youtube-nocookie\.com)/embed/{_VIDEO_ID}",
        rf"youtube\.com/shorts/{_VIDEO_ID}",
        rf"youtube\.com/live/{_VIDEO_ID}",
        rf"youtu\.be/{_VIDEO_ID}",
        rf"(?:youtube\.com|youtube-nocookie\.com)/.*[?&]v={_VIDEO_ID}",
    )
]


class TranscriptError(Exception):
    """A user-facing error describing why a transcript could not be retrieved."""


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


def extract_video_id(value: str) -> str:
    """Extract an 11-character YouTube video ID from a URL or a bare ID.

    Raises:
        ValueError: if no video ID can be found.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("No video URL or ID provided.")
    bare = _BARE_ID.match(value)
    if bare:
        return bare.group("id")
    for pattern in _URL_PATTERNS:
        match = pattern.search(value)
        if match:
            return match.group("id")
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


def _format_transcript(snippets, include_timestamps: bool) -> str:
    if include_timestamps:
        return "\n".join(
            f"[{_format_timestamp(snippet.start)}] {snippet.text}" for snippet in snippets
        )
    return " ".join(snippet.text.replace("\n", " ") for snippet in snippets).strip()


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
        include_timestamps: Prefix each line with ``[mm:ss]`` / ``[h:mm:ss]`` when true.
        translate_to: Optional ISO language code to translate the transcript into.

    Raises:
        TranscriptError: with a user-facing message if the transcript can't be retrieved.
    """
    video_id = extract_video_id(video)
    languages = list(languages) or ["en"]
    api = _build_api()
    try:
        if translate_to:
            transcript = api.list(video_id).find_transcript(languages)
            fetched = transcript.translate(translate_to).fetch()
        else:
            fetched = api.fetch(video_id, languages=languages)
    except Exception as exc:  # noqa: BLE001 - re-raised as a friendly TranscriptError
        raise _map_error(exc, requested_languages=languages, translate_to=translate_to) from exc
    return _format_transcript(fetched, include_timestamps)


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
