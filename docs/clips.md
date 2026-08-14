# Clips — Twitch clip player with a queue

A clip player for stream: queue up Twitch clip and VOD links, then fire them
one at a time at a full-window OBS browser source. **Nothing auto-advances** —
every item is played deliberately, from the panel or from a bot over a simple
GET API, the same philosophy as the soundboard's `/api/play/{name}`.

Everything lives under `/clips/*`.

---

## Why yt-dlp

The module plays **direct media, not the Twitch iframe**: the overlay gets a
real `<video>` element it can pause, seek, report progress on, and clear the
instant it ends. Twitch doesn't hand out MP4/HLS URLs in the page link, so
something has to resolve them — that something is `yt-dlp`, which despite the
name is the standard resolver for Twitch clips and VODs too (nothing here
touches YouTube). It's in the main `requirements.txt`; if you already have
`yt-dlp` on your PATH, that copy is used automatically and the pip install is
unnecessary.

The Twitch iframe embed is kept only as a best-effort **fallback** for things
direct playback can't reach (sub-only VODs, expired clips).

---

## Install

Copy `clips.py` next to `hexcast.py`, and these two files into `static/`:

```
static/clips_panel.html
static/clips_overlay.html
```

`hexcast.py` already mounts the module when the file is present. If you're
wiring it into your own copy by hand, it's the same two lines as the other
integrations, after `app.mount("/media", ...)`:

```python
from clips import attach_clips
attach_clips(app, PORT)
```

Restart. Two new URLs are printed at startup. As with the other modules, the
module starts lazily on the first request because Hexcast uses
`FastAPI(lifespan=...)`; call `start_clips()` from inside that lifespan to
start at boot instead.

---

## Browser source

| Source | URL |
| --- | --- |
| Clip player | `http://localhost:4747/clips/overlay` |

Add it as a Browser source sized and placed where clips should appear — the
video letterboxes to fit (`object-fit: contain`), so covering the whole canvas
is fine: the overlay renders **nothing at all** while idle. Stopped or ended =
fully transparent.

Uncheck **Shutdown source when not visible**. In the browser source's audio
settings, "Control audio via OBS" works normally — media elements are attached
to the DOM specifically so OBS captures their audio, the same trick the
soundboard overlay uses.

---

## Queue workflow

1. **Add** — paste into the box on the Queue tab: a single URL, or any blob of
   text (a chat log, a Discord dump). Every Twitch link in it is extracted:

   - `clips.twitch.tv/SLUG`
   - `twitch.tv/CHANNEL/clip/SLUG`
   - `twitch.tv/videos/ID` — an optional `?t=1h2m3s` start offset is honoured

   Duplicates are skipped. Each new item gets a stable **#number** that never
   changes and never gets reused — that's what bots reference.

2. **Resolve** — metadata (title, duration, thumbnail) fills in a few seconds
   after adding, in the background. With **Pre-download clips** on (the
   default), each clip's MP4 is also downloaded to `media/clips/{id}.mp4`, so
   playback is instant and immune to Twitch's short-lived media URLs. VODs are
   never downloaded — they stream as HLS.

3. **Play** — hit Play on a row (or `GET /clips/api/play/{num}`). The Now
   Playing strip shows title, progress, Pause/Resume and Stop. When the clip
   ends the overlay clears itself and the item is marked **played** — dimmed
   in the list, but kept until you **Clear played**.

   - **Stop** clears the overlay immediately; the item stays queued and is
     *not* marked played.
   - Drag rows to reorder; the per-row buttons toggle played state and remove
     items (removal also deletes the cached MP4).

Playback state is owned by the server and pushed to every open panel and
overlay over websockets, so multiple panels stay in sync and a reloaded OBS
source rejoins mid-clip at the right position.

---

## Fallback behaviour

If resolution fails — sub-only VOD, deleted clip, network trouble — the item
gets an error badge and, if **Fall back to the Twitch embed** is on, playback
retries through the official iframe embed (`clips.twitch.tv/embed` /
`player.twitch.tv`). Two caveats, both inherent to the iframe:

- It cannot report progress or "ended". When the duration is known, the
  server clears the overlay itself a few seconds after the clip should have
  finished; otherwise press Stop.
- Twitch requires a `parent` hostname it accepts. The overlay passes the
  hostname it was loaded from — `localhost` works; a bare LAN IP may not.
  Pause/Resume don't work in this mode.

---

## Bot / HTTP API

All endpoints are GET-friendly and return `{"ok": true, ...}` or
`{"ok": false, "error": "..."}`. `{ref}` is a **#number**, an item id, an
exact clip slug, or `next` (the first still-queued item).

| Endpoint | What it does |
| --- | --- |
| `GET /clips/api/queue` | full queue + player state as JSON |
| `GET /clips/api/add?url=...` | add a URL (returns the created entry incl. its `num`); POST a JSON `{"text": "..."}` blob to add many at once |
| `GET /clips/api/play/{ref}` | play an item |
| `GET /clips/api/pause` · `resume` · `toggle` · `stop` | transport |
| `GET /clips/api/status` | player state, current item, queue counts |
| `GET /clips/api/remove/{ref}` | remove an item (`DELETE /clips/api/queue/{ref}` also works) |

Examples:

```
curl "http://localhost:4747/clips/api/add?url=https://clips.twitch.tv/SomeSlug"
curl http://localhost:4747/clips/api/play/7
curl http://localhost:4747/clips/api/play/next
curl http://localhost:4747/clips/api/stop
```

So a channel-point redeem or a `!playclip 7` chat command is one HTTP call.

---

## Storage

Everything persists in `config/clips.json` — settings, the number counter,
and the queue itself (each entry: id, num, url, kind `clip|vod`, title,
duration, thumbnail, status `queued|played`, start offset, error, source
`manual|api`). Cached clip MP4s live in `media/clips/` and are served through
the existing `/media` mount; they're deleted when their queue item is removed
or cleared.

No tokens, no secrets — the module talks to Twitch anonymously through
yt-dlp. Same security posture as the rest of Hexcast otherwise: no auth, keep
it on the LAN.

---

## Troubleshooting

**Status pill says "yt-dlp missing".** Install it into Hexcast's environment
(`pip install yt-dlp`) or put the standalone `yt-dlp` binary on PATH, then
reload the panel.

**A clip resolves but won't pre-download.** It streams at play time instead —
pre-download is an optimisation, not a requirement. Check the Log card on the
Settings tab for the reason.

**Sub-only VODs.** Anonymous yt-dlp can't fetch them, so they fall back to
the iframe embed — which also only works if the OBS browser source is logged
out-of-scope. Expect these to need the fallback, or to fail entirely.

**Clip plays but there's no audio in the stream.** Check the browser source's
audio routing in OBS (Advanced Audio Properties), and the master volume on
the panel's Settings tab.

**Old clips error with "no longer available".** Twitch deletes clips; the
error badge shows exactly what yt-dlp reported. Remove the row.

**The overlay shows a Twitch player frame instead of clean video.** That's
the iframe fallback kicking in — the row will carry an error badge explaining
why direct playback failed.
