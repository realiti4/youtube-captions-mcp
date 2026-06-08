# youtube-context-mcp

A small [MCP](https://modelcontextprotocol.io) server that gives agents rich context about a
YouTube video — its transcript (plain or timestamped), jump-to-the-moment deep links,
metadata (title, channel, upload date, duration, view/like counts, chapters, tags), and the
most-replayed moments (where viewers rewatch most) — so they can answer questions, summarize,
pull quotes, surface highlights, or point you to exactly where something is said.

It builds on [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api)
(transcripts) and [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) (metadata), which do the actual
fetching, and shapes them into a focused set of MCP tools designed for agents.

> Transcripts are a video's **existing captions/subtitles** — it does **not** transcribe audio
> (no Whisper/ASR). Videos without captions have no transcript to return.

## Install

Run it on demand with [uv](https://docs.astral.sh/uv/) (no install needed):

```bash
uvx youtube-context-mcp@latest
```

Or install it:

```bash
pip install youtube-context-mcp
```

## Use it with an agent

Add it to your MCP client config:

```json
{
  "mcpServers": {
    "youtube-context": {
      "command": "uvx",
      "args": ["youtube-context-mcp@latest"]
    }
  }
}
```

Or, in Claude Code:

```bash
claude mcp add youtube-context -- uvx youtube-context-mcp@latest
```

### Running over HTTP

By default the server talks **stdio** (the client launches it). If your client runs on a
different host — for example LM Studio on Windows while this server runs in WSL2 — run it as a
long-lived HTTP server instead and point the client at a URL:

```bash
youtube-context-mcp --transport http --host 0.0.0.0 --port 8000
```

Then add it by URL:

```json
{
  "mcpServers": {
    "youtube-context": { "url": "http://localhost:8000/mcp" }
  }
}
```

(`--host 0.0.0.0` makes it reachable from the Windows side; WSL2 forwards `localhost`.)

## Tools

| Tool | What it does |
| --- | --- |
| `get_transcript(video, languages=["en"], include_timestamps=False, translate_to=None)` | Returns the transcript as text. `video` is a URL or 11-char ID. Set `include_timestamps` to group it into ~15s `[mm:ss]` blocks (handy for locating a topic and building a link); `translate_to` for an ISO language code. |
| `build_video_link(video, start)` | Builds a `watch?v=…&t=<seconds>` URL that opens the video at a moment, so a user can click straight to it. `start` is seconds or a `"mm:ss"` / `"h:mm:ss"` string. Pairs with `get_transcript(include_timestamps=True)` to turn "where is X mentioned?" into a clickable link. |
| `list_transcripts(video)` | Lists available transcripts (language, code, manual vs auto-generated, translatable) plus the translation targets. Use it when `get_transcript` can't find your language. |
| `get_video_metadata(video, include_description=False)` | Returns the video's title, channel, upload date, duration, view/like counts, chapters and tags. `video` is a URL or 11-char ID. Set `include_description=True` to also include the (often long) description. Use it to answer "what's this video / who made it?" without fetching the transcript. |
| `get_most_replayed(video, top_n=5)` | Returns the video's **most-replayed moments** (YouTube's viewer-interest heatmap) as up to `top_n` high-interest regions — each with a `peak_label`/`region_label` (mm:ss), a clickable jump `url`, the chapter it falls in, and a `relative_intensity` (0–1 within the video, 1.0 = its single most-rewatched moment — not a view count). Use it for "what are the best parts?" or to weight a summary by what viewers actually rewatch. `has_data` is `false` when YouTube has no heatmap (common for newer/low-traffic videos and some Shorts). |

## Proxies (optional)

YouTube blocks most datacenter/cloud IPs, so on a server you may hit `RequestBlocked` /
`IpBlocked` (transcripts) or a "Sign in to confirm you're not a bot" block (metadata). Locally
this is rarely needed. The same env vars route both transcript and metadata requests through a
proxy:

| Env var | Purpose |
| --- | --- |
| `WEBSHARE_PROXY_USERNAME`, `WEBSHARE_PROXY_PASSWORD` | Use [Webshare](https://www.webshare.io/) rotating residential proxies. |
| `WEBSHARE_PROXY_LOCATIONS` | Optional CSV of country codes, e.g. `us,de`. |
| `YT_TRANSCRIPT_HTTP_PROXY`, `YT_TRANSCRIPT_HTTPS_PROXY` | Use a generic HTTP/HTTPS proxy instead. |
| `YT_TRANSCRIPT_TIMEOUT` | Per-request timeout in seconds (default `20`). |

With no env set, requests go out directly.

## Troubleshooting

- **`RequestBlocked` / `IpBlocked`** — YouTube blocked the IP. Set the proxy env vars above.
- **No transcript found** — call `list_transcripts` to see which languages exist for that video.
- **Transcripts disabled** — the uploader turned captions off; nothing can be fetched.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
uv run mcp dev src/youtube_context_mcp/server.py --with-editable .   # interactive inspector
```

## License

MIT

## Credits

Transcript fetching is done by [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api)
by Jonas Depoix, and metadata by [`yt-dlp`](https://github.com/yt-dlp/yt-dlp). This project is
the MCP adapter that wires them together for agents.
