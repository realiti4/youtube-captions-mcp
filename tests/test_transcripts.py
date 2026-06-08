"""Tests for fetching, formatting, listing, and error mapping (library mocked)."""

from __future__ import annotations

import pytest
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
)

from youtube_context_mcp import transcripts
from youtube_context_mcp.transcripts import TranscriptError, _format_timestamp, _map_error

VIDEO = "dQw4w9WgXcQ"


def make_error(cls):
    """Instantiate a library exception without invoking its varied constructors."""
    return cls.__new__(cls)


# ---- formatting ----


def test_format_timestamp_under_hour():
    assert _format_timestamp(65) == "01:05"


def test_format_timestamp_over_hour():
    assert _format_timestamp(3725) == "1:02:05"


def test_get_transcript_text(fake_api, snippets):
    fake_api.fetch_return = snippets
    out = transcripts.get_transcript(f"https://youtu.be/{VIDEO}")
    assert out == "Hello world second line after an hour"
    assert fake_api.calls["fetch"] == {"video_id": VIDEO, "languages": ("en",)}


def test_get_transcript_languages_passthrough(fake_api, snippets):
    fake_api.fetch_return = snippets
    transcripts.get_transcript(VIDEO, languages=("de", "en"))
    assert fake_api.calls["fetch"]["languages"] == ("de", "en")


def test_get_transcript_with_timestamps(fake_api, snippets):
    fake_api.fetch_return = snippets
    out = transcripts.get_transcript(VIDEO, include_timestamps=True)
    assert out.splitlines() == [
        "[00:00] Hello world",
        "[01:05] second line",
        "[1:02:05] after an hour",
    ]


def test_get_transcript_translate(monkeypatch, snippets):
    captured = {}

    class FakeTranslated:
        def fetch(self):
            return snippets

    class FakeTranscript:
        def translate(self, code):
            captured["translate_to"] = code
            return FakeTranslated()

    class FakeList:
        def find_transcript(self, languages):
            captured["languages"] = tuple(languages)
            return FakeTranscript()

    class FakeApi:
        def list(self, video_id):
            captured["video_id"] = video_id
            return FakeList()

    monkeypatch.setattr(transcripts, "_build_api", lambda: FakeApi())
    out = transcripts.get_transcript(VIDEO, languages=("en",), translate_to="de")
    assert out == "Hello world second line after an hour"
    assert captured == {"video_id": VIDEO, "languages": ("en",), "translate_to": "de"}


# ---- list_transcripts ----


def test_list_transcripts(fake_api):
    class FakeTranslationLang:
        def __init__(self, language, language_code):
            self.language = language
            self.language_code = language_code

    class FakeTranscript:
        def __init__(self, language, code, generated, translatable, translations=()):
            self.language = language
            self.language_code = code
            self.is_generated = generated
            self.is_translatable = translatable
            self.translation_languages = list(translations)

    fake_api.list_return = [
        FakeTranscript("English", "en", False, True, [FakeTranslationLang("German", "de")]),
        FakeTranscript("Spanish (auto-generated)", "es", True, False),
    ]

    result = transcripts.list_transcripts(f"https://youtu.be/{VIDEO}")
    assert fake_api.calls["list"] == {"video_id": VIDEO}
    assert result["transcripts"] == [
        {
            "language": "English",
            "language_code": "en",
            "is_generated": False,
            "is_translatable": True,
        },
        {
            "language": "Spanish (auto-generated)",
            "language_code": "es",
            "is_generated": True,
            "is_translatable": False,
        },
    ]
    assert result["translation_languages"] == [{"language": "German", "language_code": "de"}]


# ---- error mapping (unit) ----


@pytest.mark.parametrize(
    "cls, needle",
    [
        (NoTranscriptFound, "No transcript found"),
        (TranscriptsDisabled, "disabled"),
        (AgeRestricted, "age-restricted"),
        (PoTokenRequired, "PO token"),
        (InvalidVideoId, "valid YouTube video ID"),
        (RequestBlocked, "blocked"),
        (IpBlocked, "blocked"),
        (VideoUnavailable, "unavailable"),
    ],
)
def test_map_error_messages(cls, needle):
    err = _map_error(make_error(cls), requested_languages=["en"])
    assert isinstance(err, TranscriptError)
    assert needle in str(err)


def test_map_error_no_transcript_includes_languages():
    err = _map_error(make_error(NoTranscriptFound), requested_languages=["de", "fr"])
    assert "['de', 'fr']" in str(err)


@pytest.mark.parametrize("cls", [NotTranslatable, TranslationLanguageNotAvailable])
def test_map_error_translation(cls):
    err = _map_error(make_error(cls), translate_to="de")
    assert "translated to 'de'" in str(err)


def test_map_error_blocked_mentions_proxy():
    err = _map_error(make_error(IpBlocked))
    assert "PROXY" in str(err).upper()


def test_map_error_fallback_uses_library_message():
    err = _map_error(ValueError("boom"))
    assert "Could not retrieve transcript" in str(err)
    assert "boom" in str(err)


# ---- error propagation through the public functions ----


def test_get_transcript_wraps_library_error(fake_api):
    fake_api.fetch_exc = make_error(NoTranscriptFound)
    with pytest.raises(TranscriptError) as excinfo:
        transcripts.get_transcript(VIDEO)
    assert "list_transcripts" in str(excinfo.value)


def test_get_transcript_invalid_input_raises_valueerror():
    with pytest.raises(ValueError):
        transcripts.get_transcript("not a youtube url")


def test_list_transcripts_wraps_library_error(fake_api):
    fake_api.list_exc = make_error(TranscriptsDisabled)
    with pytest.raises(TranscriptError):
        transcripts.list_transcripts(VIDEO)
