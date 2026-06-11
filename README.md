# youtube-context-mcp

An [MCP](https://modelcontextprotocol.io) server that gives agents rich context about any
YouTube video — what's said, what's popular, and what's on screen:

- **Transcript** — plain or `[mm:ss]`-timestamped text, with optional translation
- **Deep links** — `watch?v=…&t=…` URLs that jump straight to a moment
- **Metadata** — title, channel, upload date, duration, view/like counts, chapters, tags
- **Most-replayed moments** — the peaks of YouTube's viewer-interest heatmap
- **Visuals** (for multimodal models) — a still frame at any moment, or a tiled preview sheet
  of the whole video

Agents use it to answer questions about a video, summarize it, pull quotes, surface highlights,
read what's on screen, or link you to exactly where something is said.

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

In every tool, `video` accepts a full YouTube URL (watch, youtu.be, shorts, embed, live) or a
bare 11-character video ID.

### `get_transcript(video, languages=["en"], include_timestamps=False, translate_to=None)`

Returns the transcript as plain text. Set `include_timestamps=True` to group it into ~15s
`[mm:ss]` blocks — handy for locating a topic and building a link; `translate_to` takes an ISO
language code.

### `build_video_link(video, start)`

Builds a `watch?v=…&t=<seconds>` URL that opens the video at a moment, so a user can click
straight to it. `start` is seconds or a `"mm:ss"` / `"h:mm:ss"` string. Pairs with
`get_transcript(include_timestamps=True)` to turn "where is X mentioned?" into a clickable link.

### `list_transcripts(video)`

Lists available transcripts (language, code, manual vs auto-generated, translatable) plus the
translation targets. Use it when `get_transcript` can't find your language.

### `get_video_metadata(video, include_description=False)`

Returns the video's title, channel, upload date, duration, view/like counts, chapters and tags —
answers "what's this video / who made it?" without fetching the transcript. Set
`include_description=True` to also include the (often long) description.

### `get_most_replayed(video, top_n=8)`

Returns the video's **most-replayed moments** (YouTube's viewer-interest heatmap) as up to
`top_n` distinct high-interest content regions — each with a `peak_label`/`region_label`
(mm:ss), a clickable jump `url`, the chapter it falls in, and a `relative_intensity` (0–1 within
the video, 1.0 = its single most-rewatched moment — not a view count). Use it for "what are the
best parts?" or to weight a summary by what viewers actually rewatch.

A peak flagged `is_opening: true` sits at the start (t≈0) — usually a playback-start artifact,
not a real rewatch; it's returned *in addition to* `top_n` so it can't crowd out the content
peaks. `has_data` is `false` when YouTube has no heatmap (common for newer/low-traffic videos
and some Shorts).

### `get_video_frame(video, at, max_width=640)`

Captures a single still **frame** (screenshot) at a moment and returns it as an image, so a
multimodal model can answer "what's on screen here?" — read a slide, a burned-in caption, or a
UI being demoed. `at` is seconds or a `"mm:ss"` / `"h:mm:ss"` string; the frame is the nearest
keyframe at/just before it and is downscaled to `max_width` (clamped 64–1280) to stay cheap on
the image budget. Pairs with `get_most_replayed` / `get_transcript(include_timestamps=True)` to
pick the moment first. **Requires [ffmpeg](#media-tools-ffmpeg).**

### `get_video_preview(video, tiles=12, tile_width=320, start=None, end=None)`

Returns a **contact sheet**: `tiles` frames sampled evenly across the whole video, composited
into one tiled grid image, plus a text legend mapping each tile to its `mm:ss` timestamp — a
cheap visual overview of the entire video (a 4×3 sheet costs about 3× one frame). Pass `start` /
`end` (seconds or `"mm:ss"` / `"h:mm:ss"`) to preview just a window of the video — e.g. zoom into
the part an overview sheet or `get_most_replayed` flagged as interesting. Tiles are small by
design: spot scene changes and pick a moment here, then zoom in further with `get_video_frame`
at a tile's timestamp. **Requires [ffmpeg](#media-tools-ffmpeg).**

## Media tools (ffmpeg)

`get_video_frame` and `get_video_preview` are the only tools that need an
[`ffmpeg`](https://ffmpeg.org/) binary (everything else is metadata/transcript only). yt-dlp
resolves the video stream and ffmpeg grabs downscaled JPEGs; images come back as MCP image
content, so the client/model must be able to display images (e.g. a multimodal model such as
Gemma in LM Studio).

The system ffmpeg is used when it's on `PATH`. The optional `media` extra installs
[`imageio-ffmpeg`](https://pypi.org/project/imageio-ffmpeg/), whose wheel bundles a static ffmpeg,
making the install self-contained:

```bash
pip install "youtube-context-mcp[media]"   # or: uvx youtube-context-mcp[media]@latest
# or bring your own: apt install ffmpeg | brew install ffmpeg | see ffmpeg.org
```

If neither is available, only the two media tools are affected and they return a clear error.

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
- **"ffmpeg is not installed"** — the media tools need it; install it or use the `[media]` extra
  ([Media tools](#media-tools-ffmpeg)).
- **Frame/preview returns but no image shows** — the MCP client/model must support image
  content; text-only setups drop it silently.

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
