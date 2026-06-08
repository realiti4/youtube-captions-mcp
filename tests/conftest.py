"""Shared test fixtures.

Tests never hit the network: ``fake_api`` swaps out ``transcripts._build_api`` so the wrapped
``youtube-transcript-api`` is never actually called.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from yt_transcript_mcp import transcripts


@dataclass
class FakeSnippet:
    """Stand-in for ``FetchedTranscriptSnippet`` (text / start / duration)."""

    text: str
    start: float
    duration: float = 0.0


@pytest.fixture
def snippets() -> list[FakeSnippet]:
    return [
        FakeSnippet("Hello world", 0.0, 2.0),
        FakeSnippet("second line", 65.0, 2.0),
        FakeSnippet("after an hour", 3725.0, 2.0),
    ]


class FakeApi:
    """Configurable fake standing in for ``YouTubeTranscriptApi``."""

    def __init__(self) -> None:
        self.fetch_return: list[FakeSnippet] = []
        self.fetch_exc: Exception | None = None
        self.list_return = None
        self.list_exc: Exception | None = None
        self.calls: dict[str, dict] = {}

    def fetch(self, video_id, languages=("en",)):
        self.calls["fetch"] = {"video_id": video_id, "languages": tuple(languages)}
        if self.fetch_exc:
            raise self.fetch_exc
        return self.fetch_return

    def list(self, video_id):
        self.calls["list"] = {"video_id": video_id}
        if self.list_exc:
            raise self.list_exc
        return self.list_return


@pytest.fixture
def fake_api(monkeypatch) -> FakeApi:
    api = FakeApi()
    monkeypatch.setattr(transcripts, "_build_api", lambda: api)
    return api
