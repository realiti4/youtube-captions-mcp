"""Tests that the MCP tools are registered and delegate to the core module."""

from __future__ import annotations

import sys

from youtube_context_mcp import links, metadata, server, transcripts


def test_tools_registered():
    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    assert names == {
        "get_transcript",
        "build_video_link",
        "list_transcripts",
        "get_video_metadata",
        "get_most_replayed",
    }


def test_get_transcript_tool_delegates(monkeypatch):
    captured = {}

    def fake(video, languages, include_timestamps, translate_to):
        captured["args"] = (video, languages, include_timestamps, translate_to)
        return "TRANSCRIPT TEXT"

    monkeypatch.setattr(transcripts, "get_transcript", fake)
    out = server.get_transcript("vid", languages=["de"], include_timestamps=True, translate_to="en")
    assert out == "TRANSCRIPT TEXT"
    assert captured["args"] == ("vid", ("de",), True, "en")


def test_get_transcript_tool_uses_default_language(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        transcripts,
        "get_transcript",
        lambda *args: captured.setdefault("args", args) or "X",
    )
    server.get_transcript("vid")
    assert captured["args"] == ("vid", ("en",), False, None)


def test_build_video_link_tool_delegates(monkeypatch):
    captured = {}

    def fake(video, start):
        captured["args"] = (video, start)
        return "URL"

    monkeypatch.setattr(links, "build_video_link", fake)
    assert server.build_video_link("vid", "1:30") == "URL"
    assert captured["args"] == ("vid", "1:30")


def test_list_transcripts_tool_delegates(monkeypatch):
    expected = {"transcripts": [], "translation_languages": []}
    monkeypatch.setattr(transcripts, "list_transcripts", lambda video: expected)
    assert server.list_transcripts("vid") == expected


def test_get_video_metadata_tool_delegates(monkeypatch):
    captured = {}

    def fake(video, include_description):
        captured["args"] = (video, include_description)
        return {"video_id": video}

    monkeypatch.setattr(metadata, "get_video_metadata", fake)
    out = server.get_video_metadata("vid", include_description=True)
    assert out == {"video_id": "vid"}
    assert captured["args"] == ("vid", True)


def test_get_video_metadata_tool_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        metadata,
        "get_video_metadata",
        lambda *args: captured.setdefault("args", args) or {},
    )
    server.get_video_metadata("vid")
    assert captured["args"] == ("vid", False)


def test_get_most_replayed_tool_delegates(monkeypatch):
    captured = {}

    def fake(video, top_n):
        captured["args"] = (video, top_n)
        return {"video_id": video, "has_data": True}

    monkeypatch.setattr(metadata, "get_most_replayed", fake)
    out = server.get_most_replayed("vid", top_n=3)
    assert out == {"video_id": "vid", "has_data": True}
    assert captured["args"] == ("vid", 3)


def test_get_most_replayed_tool_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        metadata,
        "get_most_replayed",
        lambda *args: captured.setdefault("args", args) or {},
    )
    server.get_most_replayed("vid")
    assert captured["args"] == ("vid", 5)


def test_main_defaults_to_stdio(monkeypatch):
    calls = {}
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.setdefault("kwargs", kwargs))
    monkeypatch.setattr(sys, "argv", ["youtube-context-mcp"])
    server.main()
    assert calls["kwargs"] == {}


def test_main_http_sets_transport_and_binding(monkeypatch):
    original = (server.mcp.settings.host, server.mcp.settings.port)
    calls = {}
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.setdefault("kwargs", kwargs))
    monkeypatch.setattr(
        sys,
        "argv",
        ["youtube-context-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "9000"],
    )
    try:
        server.main()
        assert calls["kwargs"] == {"transport": "streamable-http"}
        assert server.mcp.settings.host == "0.0.0.0"
        assert server.mcp.settings.port == 9000
    finally:
        server.mcp.settings.host, server.mcp.settings.port = original
