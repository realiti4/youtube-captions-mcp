"""Tests that the MCP tools are registered and delegate to the core module."""

from __future__ import annotations

import sys

from yt_transcript_mcp import server, transcripts


def test_tools_registered():
    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    assert names == {"get_transcript", "list_transcripts"}


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


def test_list_transcripts_tool_delegates(monkeypatch):
    expected = {"transcripts": [], "translation_languages": []}
    monkeypatch.setattr(transcripts, "list_transcripts", lambda video: expected)
    assert server.list_transcripts("vid") == expected


def test_main_defaults_to_stdio(monkeypatch):
    calls = {}
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.setdefault("kwargs", kwargs))
    monkeypatch.setattr(sys, "argv", ["yt-transcript-mcp"])
    server.main()
    assert calls["kwargs"] == {}


def test_main_http_sets_transport_and_binding(monkeypatch):
    original = (server.mcp.settings.host, server.mcp.settings.port)
    calls = {}
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: calls.setdefault("kwargs", kwargs))
    monkeypatch.setattr(
        sys,
        "argv",
        ["yt-transcript-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "9000"],
    )
    try:
        server.main()
        assert calls["kwargs"] == {"transport": "streamable-http"}
        assert server.mcp.settings.host == "0.0.0.0"
        assert server.mcp.settings.port == 9000
    finally:
        server.mcp.settings.host, server.mcp.settings.port = original
