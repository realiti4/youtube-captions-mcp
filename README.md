# youtube-captions-mcp

A small [MCP](https://modelcontextprotocol.io) server that lets agents read YouTube video
transcripts so you can ask questions about a video, summarize it, or pull quotes.

It's a thin wrapper around [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api),
which does all the actual fetching. This project just exposes it as two MCP tools.

> It returns a video's **existing captions/subtitles** — it does **not** transcribe audio
> (no Whisper/ASR). Videos without captions have nothing to return.

## Install

Run it on demand with [uv](https://docs.astral.sh/uv/) (no install needed):

```bash
uvx youtube-captions-mcp
```

Or install it:

```bash
pip install youtube-captions-mcp
```

## Use it with an agent

Add it to your MCP client config:

```json
{
  "mcpServers": {
    "youtube-captions": {
      "command": "uvx",
      "args": ["youtube-captions-mcp"]
    }
  }
}
```

Or, in Claude Code:

```bash
claude mcp add youtube-captions -- uvx youtube-captions-mcp
```

### Running over HTTP

By default the server talks **stdio** (the client launches it). If your client runs on a
different host — for example LM Studio on Windows while this server runs in WSL2 — run it as a
long-lived HTTP server instead and point the client at a URL:

```bash
youtube-captions-mcp --transport http --host 0.0.0.0 --port 8000
```

Then add it by URL:

```json
{
  "mcpServers": {
    "youtube-captions": { "url": "http://localhost:8000/mcp" }
  }
}
```

(`--host 0.0.0.0` makes it reachable from the Windows side; WSL2 forwards `localhost`.)

## Tools

| Tool | What it does |
| --- | --- |
| `get_transcript(video, languages=["en"], include_timestamps=False, translate_to=None)` | Returns the transcript as text. `video` is a URL or 11-char ID. Set `include_timestamps` for `[mm:ss]` / `[h:mm:ss]` lines; `translate_to` for an ISO language code. |
| `list_transcripts(video)` | Lists available transcripts (language, code, manual vs auto-generated, translatable) plus the translation targets. Use it when `get_transcript` can't find your language. |

## Proxies (optional)

YouTube blocks most datacenter/cloud IPs, so on a server you may hit `RequestBlocked` /
`IpBlocked`. Locally this is rarely needed. To route requests through a proxy, set env vars:

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
uv run mcp dev src/youtube_captions_mcp/server.py --with-editable .   # interactive inspector
```

## License

MIT

## Credits

All transcript fetching is done by [`youtube-transcript-api`](https://github.com/jdepoix/youtube-transcript-api)
by Jonas Depoix. This project is just an MCP adapter on top of it.
