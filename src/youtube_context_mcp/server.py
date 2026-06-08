"""MCP server exposing YouTube transcript tools over stdio."""

from __future__ import annotations

import argparse
from typing import TypedDict

from mcp.server.fastmcp import FastMCP

from youtube_context_mcp import links, metadata, transcripts

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
