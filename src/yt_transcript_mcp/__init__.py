"""A small MCP server for YouTube transcripts, wrapping youtube-transcript-api."""

from importlib.metadata import version

__version__ = version("yt-transcript-mcp")

__all__ = ["__version__"]
