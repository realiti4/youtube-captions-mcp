"""MCP server exposing YouTube transcript tools over stdio."""

from __future__ import annotations

import argparse
from typing import TypedDict

from mcp.server.fastmcp import FastMCP, Image

from youtube_context_mcp import links, media, metadata, transcripts

mcp = FastMCP("youtube-context")


class TranscriptInfo(TypedDict):
    language: str
    language_code: str
    is_generated: bool
    is_translatable: bool


class TranslationLanguage(TypedDict):
    language: str
    language_code: str


class TranscriptListing(TypedDict):
    transcripts: list[TranscriptInfo]
    translation_languages: list[TranslationLanguage]


@mcp.tool(structured_output=False)
def get_transcript(
    video: str,
    languages: list[str] | None = None,
    include_timestamps: bool = False,
    translate_to: str | None = None,
) -> str:
    """Fetch a YouTube video's existing captions as text so you can answer questions about it.

    Returns existing captions/subtitles only; it does not transcribe audio. Videos without
    captions have nothing to return.

    Args:
        video: A YouTube URL (watch, youtu.be, shorts, embed, live) or an 11-character video ID.
        languages: Preferred language codes in priority order. Defaults to ["en"].
        include_timestamps: If true, group the transcript into ~15s blocks, each prefixed with
            [mm:ss] (or [h:mm:ss] past an hour). Use this to find where a topic is discussed and
            pass that [mm:ss] to build_video_link.
        translate_to: Optional ISO language code to translate the transcript into.

    Returns:
        The transcript as plain text.
    """
    return transcripts.get_transcript(
        video,
        tuple(languages) if languages else ("en",),
        include_timestamps,
        translate_to,
    )


@mcp.tool(structured_output=False)
def build_video_link(video: str, start: int | float | str) -> str:
    """Build a YouTube link that opens a video at a specific moment.

    Returns a watch URL like https://www.youtube.com/watch?v=<id>&t=<seconds> so a user can click
    straight to the moment something is discussed. Pair it with get_transcript(include_timestamps=
    True) -- read off the [mm:ss] of the relevant block and pass it as start -- to turn "where is X
    mentioned?" into a clickable link.

    Args:
        video: A YouTube URL (watch, youtu.be, shorts, embed, live) or an 11-character video ID.
        start: The moment to jump to -- seconds (e.g. 90) or a "mm:ss" / "h:mm:ss" string.

    Returns:
        The watch URL as a string.
    """
    return links.build_video_link(video, start)


@mcp.tool()
def list_transcripts(video: str) -> TranscriptListing:
    """List the transcripts available for a YouTube video.

    Use this when get_transcript can't find your requested language. It reports each available
    transcript (language, code, whether it's auto-generated, whether it's translatable) plus
    the set of languages you can pass to get_transcript's translate_to.

    Args:
        video: A YouTube URL or an 11-character video ID.
    """
    return transcripts.list_transcripts(video)


@mcp.tool()
def get_video_metadata(video: str, include_description: bool = False) -> metadata.VideoMetadata:
    """Get a YouTube video's metadata: title, channel, upload date, duration, view/like counts,
    chapters and tags.

    Use this to answer questions about a video (its name, who made it, how long it is, when it
    came out) without fetching its transcript.

    Args:
        video: A YouTube URL (watch, youtu.be, shorts, embed, live) or an 11-character video ID.
        include_description: If true, also return the (often long) description; otherwise it's
            omitted to keep the response small.
    """
    return metadata.get_video_metadata(video, include_description)


@mcp.tool()
def get_most_replayed(video: str, top_n: int = 8) -> metadata.MostReplayed:
    """Get a YouTube video's "most replayed" moments -- the peaks of its viewer-interest heatmap
    (the curve shown above the timeline marking where people rewatch most).

    Use this for "what are the best / most-rewatched parts?", "jump me to the good part", or to
    weight a summary toward what viewers actually care about. Each peak is a high-interest
    *region* (region_start_seconds..region_end_seconds) with the hottest instant at
    peak_start_seconds, a ready-to-share url that opens the video at the start of the stretch, and
    the chapter it falls in. relative_intensity is 0..1 *within this video* (1.0 = its single
    most-rewatched moment) -- it is NOT a view count and is not comparable across videos.

    A peak with is_opening=True sits at the very start (t~=0): that spot is almost always inflated
    by playback starting there, not a genuine rewatch, so discount it as a "best part". It is
    returned in addition to (not counted against) top_n, so the opening can't crowd out content.

    To say what is actually happening at a peak, read its peak_label (mm:ss) and look it up with
    get_transcript(include_timestamps=True); profile is a coarse 0..1 curve for the overall shape
    (front-loaded vs steady vs spikes near the end).

    has_data may be False -- then peaks is empty and note explains why (many newer, low-traffic,
    or Shorts videos have no heatmap).

    Args:
        video: A YouTube URL (watch, youtu.be, shorts, embed, live) or an 11-character video ID.
        top_n: Max number of content peak regions (clamped to 1..20; default 8). The flagged
            opening (t~=0) peak, when present, is returned in addition to these.
    """
    return metadata.get_most_replayed(video, top_n)


@mcp.tool(structured_output=False)
def get_video_frame(video: str, at: int | float | str, max_width: int = 640) -> Image:
    """Capture a single still frame (screenshot) from a YouTube video at a moment and return it as
    an image, so a multimodal model can answer "what's on screen here?".

    Use this to see the video at a specific time -- e.g. read a slide, a caption burned into the
    video, or a UI being demoed. Pair it with get_most_replayed or get_transcript(include_timestamps
    =True) to pick an interesting moment, then grab the frame there.

    Requires ffmpeg on the server. The captured frame is the nearest keyframe at or just before the
    requested moment (it can be off by a second or two) and is downscaled to max_width to keep the
    response small.

    Args:
        video: A YouTube URL (watch, youtu.be, shorts, embed, live) or an 11-character video ID.
        at: The moment to capture -- seconds (e.g. 90) or a "mm:ss" / "h:mm:ss" string.
        max_width: Max width in pixels of the returned image (clamped 64..1280; default 640).
            Smaller is cheaper on a vision model's image-token budget.
    """
    return media.get_video_frame(video, at, max_width)


@mcp.tool(structured_output=False)
def get_video_preview(
    video: str,
    tiles: int = 12,
    tile_width: int = 320,
    start: int | float | str | None = None,
    end: int | float | str | None = None,
) -> tuple[str, Image]:
    """Get a visual overview of a YouTube video as ONE tiled contact-sheet image: `tiles` frames
    sampled evenly across the video (or across a start..end window of it), plus a text legend
    mapping each tile to its mm:ss timestamp.

    Use this to see what's going on across a video (talking head vs slides vs demo footage, scene
    changes, "is there a chart anywhere?") and to pick moments worth a closer look. To inspect one
    part in more detail, call it again with start/end around that part -- but pick the window from
    the transcript, chapters, or get_most_replayed first and zoom once; don't binary-search the
    video with repeated sheets, since every returned image stays in context. Read each tile's
    timestamp from the legend -- do not count grid cells yourself. Tiles are small and not
    readable: to read a slide, caption, or UI, follow up with get_video_frame(video, at=<that
    tile's timestamp>). Windows under ~1 minute may return near-duplicate tiles (frames land on
    keyframes, a few seconds apart).

    Requires ffmpeg on the server (the system binary, or the one bundled by the [media] extra).

    Args:
        video: A YouTube URL (watch, youtu.be, shorts, embed, live) or an 11-character video ID.
        tiles: How many frames to sample (clamped 4..24; default 12).
        tile_width: Width in pixels of each tile (clamped 160..480; default 320). The whole sheet
            stays around 1000-1300 px wide at the defaults -- cheap on a vision model's image
            budget while keeping tiles recognizable.
        start: Optional window start -- seconds (e.g. 90) or a "mm:ss" / "h:mm:ss" string.
            Defaults to the beginning of the video.
        end: Optional window end, same forms. Defaults to (and is clamped to) the video's end.
    """
    return media.get_video_preview(video, tiles, tile_width, start, end)


def main() -> None:
    """Console-script entry point.

    Defaults to stdio, for clients that spawn the server themselves. Use ``--transport http``
    to run a long-lived HTTP server instead, which is handy when the MCP client lives on a
    different host than the server -- e.g. LM Studio on Windows connecting to this server
    running in WSL2 at ``http://localhost:8000/mcp``.
    """
    parser = argparse.ArgumentParser(prog="youtube-context-mcp", description=main.__doc__)
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="Transport to serve on (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind for http/sse (default: 127.0.0.1; use 0.0.0.0 to reach it from "
        "Windows when running in WSL2).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind for http/sse (default: 8000).",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run()
        return

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http" if args.transport == "http" else "sse")


if __name__ == "__main__":
    main()
