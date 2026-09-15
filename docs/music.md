# Music — now playing overlay (YouTube Music **or** local files)

A now-playing overlay with two sources you pick from the panel:

- **YouTube Music** — fed by the
  [YouTube Music Desktop App](https://ytmdesktop.github.io/)'s companion server.
  Album art, live progress, an accent colour pulled from the artwork itself, an
  audio visualiser, and optionally the music video embedded in the card. It
  talks **specifically to that app** (2.0.0+) — not the website or the service.
- **Local files** — map a music folder, build a queue, and play it **through the
  overlay itself**, so OBS captures the audio straight from the browser source.
  The same card, styling and visualiser apply; the visualiser reacts to the real
  audio via the Web Audio API. See [Local files](#local-files) below.

Flip between them with the **Music source** control at the top of the panel.
Everything lives under `/ytm/*`.

---

## Install

Copy `ytmusic.py` and `localmusic.py` next to `hexcast.py`, and these two files
into `static/`:

```
static/ytm_panel.html
static/ytm_overlay.html
```

`localmusic.py` is optional — it's the local-file player, mounted automatically
by `ytmusic.py` when present. Without it the Music tab is YouTube-Music-only.
The local player needs **ffmpeg/ffprobe** (already a Hexcast dependency) to read
tags, durations and embedded cover art; without ffmpeg it still plays, showing
filenames and no art.

Install the dependencies:

```
pip install -r requirements-ytm.txt
```

Add to `hexcast.py`, after `app.mount("/media", ...)`:

```python
from ytmusic import attach_ytm
attach_ytm(app, PORT)
```

Optionally, for real audio reactivity in the visualiser:

```
pip install -r requirements-ytm-audio.txt
```

As with the Twitch module, the connection opens lazily on the first request
because Hexcast uses `FastAPI(lifespan=...)`. To connect at boot, call
`start_ytm()` and `stop_ytm()` from inside that lifespan.

---

## Pairing

Open <http://localhost:4747/ytm>.

In YouTube Music Desktop: **Settings → Integrations**, turn on **Companion
Server**, then turn on **Enable companion authorization**. That second switch
is deliberately temporary — it only needs to be on while you pair.

Click **Pair with the app**. A code appears in the panel; approve it in
YouTube Music Desktop within 30 seconds. The token is stored in
`config/ytmusic_secrets.json` and survives restarts, so this is a one-time
step. You can switch companion authorization back off afterwards; leave the
Companion Server itself on.

Use `127.0.0.1`, not `localhost`. The companion server binds IPv4 only, and
Windows resolves `localhost` to `::1`.

---

## Browser source

| Source | URL |
| --- | --- |
| Now playing | `http://localhost:4747/ytm/overlay` |

Size the browser source to the card you want. Uncheck **Shutdown source when
not visible**. Settings apply live — save in the panel and the overlay
restyles without a refresh.

---

## Local files

Set **Music source → Local files** and open the **Local library** tab.

**Map a folder.** Type a path, or click **Browse…** to pick one with the
in-panel folder browser (it lists the host's drives and folders, so it works
even when you're driving the panel from another device). Hexcast then walks the
folder in a background thread and builds a filename index, so even tens of
thousands of tracks stay responsive — nothing is copied or modified, and tags/
duration/art are read only when a track is queued or played. The status line
shows progress and the final count; **Re-scan** rebuilds it after you add or
remove files.

**Build a queue.** The **Library** card browses and searches; the **Queue** card
holds the play order:

- **Browse** folders (breadcrumbs jump back up); **search** filters the whole
  library by filename/folder.
- Tick tracks (or **Select shown**) and **Add selected** — selections persist as
  you move between folders. To queue a lot at once, use **Add this folder (+
  subfolders)**, which expands server-side — the way to queue thousands (or the
  whole library from the root) without the browser choking on them.
- Click **▸** on any track to **preview** it in the panel (local to that tab —
  it does *not* go to the stream), so you can audition while curating.
- In the queue, **drag** to reorder, **double-click** to play a track now, **▸**
  to preview, **✕** to remove, **Clear** to empty. The queue list is virtualised,
  so a 15k-track queue scrolls smoothly.
- The queue is saved to `config/localmusic.json` and survives restarts. Track
  ids are derived from the file path, so a saved queue re-links after a restart;
  files that have since moved show as *(missing)* and are skipped on play.

**Playlists.** Build a queue, then **Save as playlist** (named, stored in
`config/playlists.json`). The **Playlists** card lists them: **Load** replaces
the queue with the playlist, **+** appends it, plus rename and delete. Handy for
keeping a few set-lists around and swapping between them.

**Playback lives in the overlay.** The audio element is inside the OBS browser
source, so OBS captures it like any browser-source audio — add the same
`/ytm/overlay` source to your scene and you'll hear it on stream. Transport
(play/pause, next, previous) is on the **Now playing** tab and works the same
for both sources. Metadata, duration and embedded cover art are read on demand
with ffprobe/ffmpeg and cached.

A couple of things worth knowing:

- If more than one overlay is open, only the **first** one plays the audio (the
  rest are muted "viewers" that still animate), so you never get double sound.
- The **visualiser** reacts to the real audio via the Web Audio API right in the
  overlay — set the visualiser mode to *React to real audio*. No desktop
  loopback or extra packages are needed for local playback (that server-side
  capture is only used for the YouTube Music source).
- Playback needs codecs the browser engine (OBS's Chromium) supports: MP3, M4A/
  AAC, OGG/Opus, FLAC and WAV all play; exotic formats (e.g. WMA) may not.

## How the data arrives

State comes over the companion server's Socket.IO feed rather than polling, so
the REST rate limits never come into play and the progress bar is genuinely
live. The overlay interpolates between updates with `requestAnimationFrame`,
so the bar moves at display rate rather than stepping once a second.

If the realtime feed can't connect, the module falls back to polling
`GET /state` every two seconds and retries the feed every three minutes. The
status pill distinguishes the two — "connected" versus "connected (polling)".

One implementation note worth recording, because the API docs are easy to
misread: `/api/v1/realtime` is a Socket.IO **namespace**, not the transport
path. The JavaScript client infers that from the URL automatically; other
clients have to be told. In Python that means `namespaces=["/api/v1/realtime"]`
with the transport left on the default `/socket.io/` endpoint. Passing it as
`socketio_path` produces a 404 from the app's HTTP router. The server also has
the polling transport disabled, so `transports=["websocket"]` is mandatory.

---

## Artwork

YouTube Music hands out small thumbnails, but the URLs carry their dimensions
inline, so a larger render costs nothing. Art is fetched through `/ytm/art`
rather than loaded directly, for two reasons: the proxy can request the bigger
version and quietly fall back if it 404s, and it makes the image same-origin so
the overlay can read it into a canvas.

That canvas read is what drives the accent colour. The cover is averaged in a
16×16 canvas, then saturation is pushed up and lightness clamped, so pale or
near-black covers still produce a usable colour. It tints the progress bar, the
label, the card glow and optionally the visualiser bars, so the overlay
re-colours itself every song. Set **Accent colour** to *Always the same* if you
want a fixed one.

Also available: a blurred artwork backdrop bleeding behind the card, and a
spinning-record mode.

---

## The music video

**Artwork → What to show there → The music video** embeds the track's video in
the art slot, muted, synced to `videoProgress` with drift correction every few
seconds. Crop-to-square or 16:9 box.

YouTube Music plays two different kinds of thing. Real music videos embed fine.
**Art tracks** — auto-generated audio uploads with a still image, which is what
you get on the Song side of the app's Song/Video toggle — cannot be embedded
and produce a YouTube error card. The state feed exposes `videoType`, so the
overlay knows which it has.

**When to try video** controls the policy:

- **Video, or the song's video counterpart** *(default)* — if the app is on the
  Video toggle, embeds that. If it's on Song, it reads the `counterparts` entry
  from the queue and embeds the paired video version instead, so you get the
  music video on screen while the app plays the audio track.
- **Only when playing the video version** — mirrors the app exactly.
- **Every track** — tries the raw id regardless. Expect failures.

Embedding permission is still the video owner's call, and a lot of official
music videos block it. Those hit a six-second watchdog and fall back to artwork
silently, then get remembered so they don't retry. The watchdog exists because
some failures render an error card inside the iframe without ever firing the
API's error event.

**Worth considering instead:** capture the YouTube Music Desktop window in OBS
directly, cropped to the video area. No embedding restrictions, no second
decode of the same video, no sync drift, works on every track. Then run this
overlay beside it for the title, progress and visualiser, with the art slot
hidden. The embed's only real advantage is that everything stays in one browser
source you can move as a unit.

---

## Visualiser

Two modes.

**Simulated** needs no extra packages. Bass-weighted, several phases per band
so neighbouring bars don't move in lockstep, and it freezes when playback
pauses. It looks right; it isn't real.

**React to real audio** measures actual levels. A browser source cannot reach
desktop audio, so the capture happens server-side: WASAPI loopback on whatever
your speakers are playing, an FFT, and 28 log-spaced bands from 40 Hz to 16 kHz
streamed over the existing websocket. Linear bands would put almost every bar
in the treble where there's nothing to look at, hence the log spacing. Requires
`requirements-ytm-audio.txt`, and only runs while an overlay is actually
connected, so it costs nothing when unused.

Windows works against the default playback device out of the box. Linux needs
PulseAudio or PipeWire. macOS has no system loopback, so it needs a virtual
device such as BlackHole.

Configurable: number of bars, height, width, position (behind the text, along
the bottom, along the top), style, colour (fixed or matched to the artwork
accent), opacity, sensitivity, smoothing, and peak caps.

Seven styles: **Bars**, **Mirrored**, **Line** (oscilloscope trace through the
band values), **Wave** (the same trace, filled), **Dots**, **LED segments**
(stacked blocks with unlit segments faintly visible), and **VU needle** — an
analogue gauge with a tick arc and a needle swung by the overall level, for a
retro/steampunk look. Peak caps apply to bars, dots, LED (top segment) and the
needle (a ghost needle at the recent peak).

Three settings control timing, and they matter:

- **Bar updates per second** — the bars run on their own clock rather than
  once per animation frame, so behaviour doesn't change with your monitor's
  refresh rate.
- **Peak hold** — how long a cap sits at its high point before falling.
- **Peak fall** — decay in units per second.

For a slow, heavy VU feel, try 12 updates per second and a fall of 0.25.

---

## Chat bot `!song`

```
http://localhost:4747/ytm/api/nowplaying
```

Returns one line of plain text: `Title - Artist (Album)`. There's a JSON
version at `/ytm/api/nowplaying.json` with the full state.

---

## Feeding a bot or a model

Set **Forward URL** in the panel. Every track change POSTs the full state plus
`"event": "track_change"`.

**Clip on track change** fires a Hexcast clip by name on every song change,
through the same `GET /api/play/{name}` endpoint the bot API uses.

---

## Controlling playback

The Now playing tab has transport buttons. They post to:

```
POST /ytm/api/command   {"command": "next"}
```

The command is routed to whichever source is active. With **Local files** it
drives the local queue (`playPause`, `play`, `pause`, `next`, `previous`,
`seekTo`, `setVolume`, `playQueueIndex`, `repeatMode`, `shuffle`). The queue is
read with `GET /ytm/queue/state` (lightweight — counts + current track) and
`GET /ytm/queue/items?offset=&limit=` (a window, for the virtualised list), and
edited with `POST /ytm/queue/{add,add-folder,remove,move,clear,play}`. The
library is at `GET /ytm/library/{status,browse,search}`, playlists at
`GET /ytm/playlists` + `POST /ytm/playlists/{save,load,rename,delete}`, and the
folder picker at `GET /ytm/fs/list?path=`.

With **YouTube Music** the command proxies to the companion server. Valid
commands there: `playPause`, `play`,
`pause`, `next`, `previous`, `volumeUp`, `volumeDown`, `setVolume` (0–100),
`mute`, `unmute`, `seekTo`, `shuffle`, `repeatMode`, `toggleLike`,
`toggleDislike`, `playQueueIndex`, `changeVideo`.

Worth knowing about — it means a chat bot or a channel point redeem could skip
a track.

---

## Troubleshooting

**"app not running".** The token is stored but the companion server isn't
answering. Check the app is open and Companion Server is on.

**Pairing errors immediately.** "Enable companion authorization" is off. It's a
separate switch from the server itself.

**Pairing times out.** The approval prompt appears inside YouTube Music
Desktop, not the browser. If the app is minimised you may not have seen it.

**Transport buttons work but nothing else does.** REST is fine and the realtime
feed isn't. Run `ytm_check.py` from the project folder — it tests each layer in
order and prints the real error. `ytm_probe.py` goes further and tries several
client configurations.

**Connects then drops with an auth error.** Tokens are bound to an app ID, and
requesting a new one for the same ID invalidates the old. Pair once more and
leave it.

**Settings tab renders empty.** `ytmusic.py` is older than the HTML in
`static/`. Restart Hexcast, then reload the page with Ctrl+Shift+R — the
browser caches the page even though the server doesn't.

**No album art.** YouTube Music fills metadata in two passes; artwork arrives a
moment after the track starts.
