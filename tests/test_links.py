"""Tests for timestamp parsing and watch-link construction (pure string logic, no network)."""

from __future__ import annotations

import pytest

from youtube_context_mcp.links import _parse_timestamp, build_video_link

VIDEO = "dQw4w9WgXcQ"


# ---- _parse_timestamp ----


@pytest.mark.parametrize(
    "value, expected",
    [
        (90, 90),
        (90.5, 90),  # floats are floored, matching _format_timestamp's int(seconds)
        ("90", 90),
        ("90.5", 90),
        ("1:30", 90),
        ("1:02:05", 3725),
        ("0:00", 0),
        # Positional parse is deliberately lenient (no 0-59 clock validation); do not "fix" this.
        ("75:30", 4530),
        ("1:90", 150),
    ],
)
def test_parse_timestamp_ok(value, expected):
    assert _parse_timestamp(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "abc",
        "1:ab",
        "1:2:3:4",  # more than 3 colon parts
        "-1",
        "-1:30",
        "1:-30",  # negative component, even though the total is positive
        "-1:90",  # negative component, even though the total is positive
        -5,
        float("inf"),
        float("nan"),
        "inf",
        "nan",
    ],
)
def test_parse_timestamp_rejects(value):
    with pytest.raises(ValueError):
        _parse_timestamp(value)


# ---- build_video_link ----


def test_build_video_link_from_bare_id():
    assert build_video_link(VIDEO, 90) == f"https://www.youtube.com/watch?v={VIDEO}&t=90"


def test_build_video_link_from_url_and_mmss():
    out = build_video_link(f"https://youtu.be/{VIDEO}", "1:30")
    assert out == f"https://www.youtube.com/watch?v={VIDEO}&t=90"


def test_build_video_link_invalid_video_raises():
    with pytest.raises(ValueError):
        build_video_link("not a youtube url", 10)


def test_build_video_link_invalid_start_raises():
    with pytest.raises(ValueError):
        build_video_link(VIDEO, "nope")
