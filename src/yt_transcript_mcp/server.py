"""MCP server exposing YouTube transcript tools over stdio."""

from __future__ import annotations

from typing import TypedDict

from mcp.server.fastmcp import FastMCP

from yt_transcript_mcp import transcripts

mcp = FastMCP("yt-transcript")


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
        include_timestamps: If true, prefix each line with [mm:ss] (or [h:mm:ss] past an hour).
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


def main() -> None:
    """Console-script entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
