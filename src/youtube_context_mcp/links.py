"""Build "jump to this moment" YouTube links.

Pure string construction with no network access: given a video and a start time, produce a
shareable watch URL that opens the video at that moment. Composes with
``get_transcript(include_timestamps=True)`` -- the agent finds where topic X is discussed (reads
the ``[mm:ss]`` of the relevant block) and turns it into a clickable link.

Kept free of any MCP concerns so it can be unit-tested in isolation.
"""

from __future__ import annotations

import math

from youtube_context_mcp.transcripts import extract_video_id


def _parse_timestamp(value: int | float | str) -> int:
    """Normalise a start time to a whole number of seconds (the inverse of ``_format_timestamp``).

    Accepts:
        * a number of seconds (floored, e.g. ``90.5`` -> ``90``), or
        * a string: plain seconds (``"90"`` / ``"90.5"``), ``"mm:ss"`` (``"1:30"`` -> ``90``),
          or ``"h:mm:ss"`` (``"1:02:05"`` -> ``3725``).

    Colon forms are parsed **positionally and summed** (``hours*3600 + minutes*60 + seconds``)
    rather than validated as a clock. This leniency is deliberate -- ``"75:30"`` -> ``4530`` and
    ``"1:90"`` -> ``150`` -- so do not "tighten" it into strict 0-59 component checks.

    Raises:
        ValueError: for empty/non-numeric input, negative results, non-finite numbers
            (``inf`` / ``nan``), or more than three colon-separated parts.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Start time is empty.")
        parts = text.split(":")
        if len(parts) > 3:
            raise ValueError(
                f"Could not parse start time {value!r}; expected seconds, 'mm:ss', or 'h:mm:ss'."
            )
        try:
            values = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError(
                f"Could not parse start time {value!r}; expected seconds, 'mm:ss', or 'h:mm:ss'."
            ) from exc
        if any(v < 0 for v in values):
            # Reject a negative component (e.g. "1:-30") even when the total comes out positive.
            raise ValueError("Start time can't be negative.")
        # Right-aligned weights so [ss], [mm, ss], [h, mm, ss] all work.
        weights = [1, 60, 3600]
        seconds = sum(v * w for v, w in zip(reversed(values), weights))
    else:
        seconds = float(value)

    if not math.isfinite(seconds):
        raise ValueError("Start time must be a finite number of seconds.")
    if seconds < 0:
        raise ValueError("Start time can't be negative.")
    return int(seconds)


def build_video_link(video: str, start: int | float | str) -> str:
    """Build a YouTube watch URL that opens ``video`` at ``start``.

    Args:
        video: A YouTube URL or 11-character video ID.
        start: The moment to jump to -- seconds (int/float) or a ``"mm:ss"`` / ``"h:mm:ss"`` string.

    Returns:
        A ``https://www.youtube.com/watch?v=<id>&t=<seconds>`` URL.

    Raises:
        ValueError: if the video ID can't be extracted or ``start`` can't be parsed.
    """
    video_id = extract_video_id(video)
    seconds = _parse_timestamp(start)
    return f"https://www.youtube.com/watch?v={video_id}&t={seconds}"
