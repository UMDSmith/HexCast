# YouTube Music Desktop — now playing overlay

A now-playing overlay fed by the
[YouTube Music Desktop App](https://ytmdesktop.github.io/)'s companion server.
Album art, live progress, an accent colour pulled from the artwork itself, an
audio visualiser, and optionally the music video embedded in the card.

This integration talks **specifically to that app** — it does not connect to
YouTube Music in a browser or to the service directly. Requires
**[YouTube Music Desktop App](https://ytmdesktop.github.io/) 2.0.0 or newer**.
Everything lives under `/ytm/*`.

---

## Install

Copy `ytmusic.py` next to `hexcast.py`, and these two files into `static/`:

```
static/ytm_panel.html
static/ytm_overlay.html
```

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
the bottom, along the top), bars or mirrored, colour (fixed or matched to the
artwork accent), opacity, sensitivity, smoothing, and peak caps.

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

which proxies to the companion server. Valid commands: `playPause`, `play`,
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
