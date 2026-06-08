"""Tests for extract_video_id across YouTube URL shapes."""

from __future__ import annotations

import pytest

from yt_transcript_mcp.transcripts import extract_video_id

VIDEO_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "value",
    [
        "dQw4w9WgXcQ",
        "  dQw4w9WgXcQ  ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123",
        "http://www.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=42",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
    ],
)
def test_extract_video_id(value):
    assert extract_video_id(value) == VIDEO_ID


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "not a youtube url",
        "https://example.com/watch?v=dQw4w9WgXcQ",  # not a YouTube domain
        "https://notyoutube.com/watch?v=dQw4w9WgXcQ",  # look-alike host
        "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",  # look-alike host
        "https://www.youtube.com/watch?v=tooShort",  # < 11 chars
    ],
)
def test_extract_video_id_invalid(value):
    with pytest.raises(ValueError):
        extract_video_id(value)
