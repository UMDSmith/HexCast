# Music — now playing overlay (YouTube Music **or** local files)

A now-playing overlay with two sources you pick from the panel:

- **YouTube Music** — fed by the
  [YouTube Music Desktop App](https://ytmdesktop.github.io/)'s companion server.
  Album art, live progress, an accent colour pulled from the artwork itself, an
  audio visualiser, and optionally the music video shown in the card. It
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

The music video (optional) is looked up with **yt-dlp**, which comes from the
main `requirements.txt` along with `yt-dlp-ejs`, its YouTube challenge-solver
scripts. Nothing else is needed for anonymous lookups; the optional YouTube
sign-in also needs a JavaScript runtime (see
[YouTube sign-in](#youtube-sign-in-optional)).

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

**Artwork → What to show there → The music video** shows the track's video in
the art slot, muted and kept in step with the song. Crop-to-square or 16:9 box.

**How it's played.** Hexcast doesn't use YouTube's embed player. When a track
starts, yt-dlp looks up the video's direct stream (video only, at the **Video
quality** you pick), and the overlay plays it in a plain `<video>` through
`/ytm/video/<id>`, which relays the stream from YouTube. Browsers can't load
these streams straight from YouTube, which is why they pass through Hexcast.
Nothing is written to disk; the stream address lives in memory and is
refreshed when it expires. Because there's no YouTube player in the overlay:

- no YouTube controls, pause button or captions ever appear on the video;
- videos whose owner turned off embedding play fine (that rule only applies
  to YouTube's embed player).

It needs yt-dlp (in `requirements.txt`). YouTube changes often, so if videos
stop appearing, update yt-dlp from the Clips page first.

YouTube Music plays two different kinds of thing: real music videos, and
**art tracks** (auto-generated audio uploads with a still image, what you get
on the Song side of the app's Song/Video toggle). The state feed exposes
`videoType`, so the overlay knows which it has.

**When to try video** controls the policy:

- **Video, or the song's video counterpart** *(default)* — if the app is on the
  Video toggle, shows that. If it's on Song, it reads the `counterparts` entry
  from the queue and shows the paired video instead, so you get the music video
  on screen while the app plays the audio track. A paired video runs on its own
  timeline (intros, skits), so it isn't forced into step with the song.
- **Only when playing the video version** — mirrors the app exactly.
- **Every track** — tries the raw id regardless.

If YouTube Music has no video paired with a song, there's nothing to show and
the art stays up.

**Getting it on screen faster.** As soon as a song starts, Hexcast looks up the
*next* song's stream in the background, so the lookup (a second or two) is
already done at the track change. **Pre-buffer the next song's video**
*(off by default)* goes further: in the last ~15 seconds of a song, the overlay
starts loading the next video in a hidden second player, so it appears the
moment the track changes. It costs a second stream for those few seconds.
If you skip or reorder, the prepared video simply isn't used.

**Starting song and video together.** **Hold each new song until its video is
ready** *(on by default)*: at a track change, Hexcast pauses the app and rewinds
it to 0:00, waits for the overlay to report the video buffered (or failed), then
resumes, so the song and video start in step. The hold never lasts more than
8 seconds. It only happens at the very start of a song, only when a video will
be shown, and only while an overlay is connected to report back. With
pre-buffering on, the video is usually ready already and the pause is barely
noticeable.

### How video loading works, step by step

What happens from one song to the next, with the default settings (hold on,
pre-buffer off):

1. **A song starts in the app.** Its state arrives over the companion feed with
   the track's id, its `videoType` and any paired `counterparts`. Hexcast works
   out which video, if any, goes with it (per **When to try video**).
2. **The lookups start straight away, in the background.** Hexcast asks yt-dlp
   for the direct stream of this song's video *and* the next song's in the
   queue. A lookup takes a second or two; the result is just a URL, kept in
   memory (never on disk) for up to 3 hours and shared by everything that
   needs it. Failed lookups are remembered for 15 minutes so a refused video
   isn't retried on every state update.
3. **The song is held (track changes only).** If this is the very start of a
   song, a video will be shown, and an overlay is connected, Hexcast pauses the
   app and rewinds it to 0:00 (the song has usually played for a moment before
   Hexcast hears about it).
4. **The overlay loads the video, paused.** It asks `/ytm/api/video/<id>` whether
   the stream is ready (instant if step 2 already did it) and points a hidden,
   muted `<video>` at `/ytm/video/<id>`. That address relays the stream from
   YouTube through Hexcast, passing seek (range) requests along. A real video
   track starts at the song's position; a paired video starts at 0:00.
5. **The overlay reports back.** When the video has buffered enough to play it
   sends *ready*; if the lookup or stream fails it sends *fallback* with the
   reason. The panel's *Video:* line shows each step.
6. **The song resumes.** On *ready* or *fallback* — or after 8 seconds at most —
   Hexcast tells the app to play. The overlay starts the video the moment the
   song's state flips to playing, and fades it in over the artwork. On
   *fallback* the artwork simply stays.
7. **While it plays.** Every few seconds the overlay follows the song's
   play/pause, and keeps a real video track within 2 seconds of the song.
   A paired video isn't forced into step (its timeline differs), and loops if
   it's shorter than the song.
8. **Near the end (pre-buffer on).** In the last ~15 seconds, the overlay loads
   the next song's video into its second, hidden `<video>`. At the track change
   it swaps to that one instead of loading from scratch, so step 5 happens
   almost immediately and the hold in step 3 is barely noticeable.

If the queue changes (you skip, reorder or pick something else), the prepared
lookup or buffer just goes unused and the new song goes through the steps
above. Stream URLs expire after a few hours; if YouTube rejects an expired one
mid-song, the relay looks it up again and carries on.

**Seeing why a video didn't show.** The Music panel's now-playing box has a
*Video:* line — playing, loading, or "showing album art — <reason>" (no linked
video, lookup refused, didn't start in time, …). Hover it for the video id.

## YouTube sign-in (optional)

Most videos look up fine anonymously. For the ones that don't, the Music page
has a **YouTube sign-in for music videos** card with **Link** and **Unlink**
buttons. This is separate from the Clips page's sign-in; linking one doesn't
link the other. It is *not* the YouTube Music Desktop app's session — that app
doesn't share its sign-in with other programs.

**What Link does, exactly**

- Nothing happens until you click **Link**. The default is *not linked*, and
  every lookup is anonymous.
- Link first runs a test: yt-dlp reads the chosen browser's cookies, checks
  there's a YouTube sign-in among them, and does one test lookup. It tells
  you what it found. If the cookies can't be read or there's no YouTube
  sign-in, nothing is saved.
- What's saved is only the **browser's name** (`"firefox"` or `"chrome"`),
  in `config/ytmusic.json` (`youtube_login.browser`). Your cookies are never copied, stored or logged by Hexcast.
- Lookups stay **anonymous first**. Only when YouTube refuses one for a reason
  a sign-in can fix (age-restricted, members-only, "Sign in to confirm you're
  not a bot") does Hexcast re-run yt-dlp with `--cookies-from-browser <name>`.
  yt-dlp then reads that browser's cookies from its profile on the PC running
  Hexcast, at that moment, and sends them to YouTube as part of that one
  lookup. That lookup is done **as your YouTube account**.
- The link **stays across restarts** until you click **Unlink**, which takes
  effect immediately.
- Chrome locks its cookie database while it's running, so the Chrome option
  only works with Chrome fully closed. Firefox works while open.
- Signed-in lookups make yt-dlp solve a JavaScript challenge from YouTube,
  which needs two things on the PC running Hexcast:
  - **a JavaScript runtime** — [Deno](https://deno.com) is what yt-dlp prefers
    (`winget install DenoLand.Deno` on Windows, then restart Hexcast). If
    there's no Deno but Node or Bun is installed, Hexcast uses that instead.
  - **yt-dlp's challenge-solver scripts** — the `yt-dlp-ejs` package, installed
    by `requirements.txt`. **Update yt-dlp** (Clips page) upgrades it together
    with yt-dlp, since the two have to match.

  The sign-in card shows a *Requirements* line with both, and if the Link test
  fails it names the missing piece.

**How to link**

1. Sign in to YouTube in Firefox — or in Chrome, then close Chrome completely.
2. Check the card's *Requirements* line shows both pieces ready.
3. Pick the browser and click **Link**. Read the message: it says whether the
   test lookup worked, or what's missing.

Hexcast is built for a home/studio network. Anything that can reach its port
can use these features, so **never expose Hexcast to the internet** (see
[Security](../README.md#️-security-local-network-use-only)). Linking is your
choice and your responsibility.

| Endpoint | |
|---|---|
| `POST /ytm/api/login/link` | body `{"browser": "firefox"}` or `"chrome"` — test, then link if the test passes |
| `POST /ytm/api/login/unlink` | unlink (back to anonymous) |
| `GET /ytm/api/video/{id}` | look up a video's stream; `{"ok": true}` or `{"ok": false, "error": "…"}` |
| `GET /ytm/video/{id}` | the relayed video stream (supports range requests) |

**Worth considering instead:** capture the YouTube Music Desktop window in OBS
directly, cropped to the video area. It shows exactly what the app shows with
no lookups at all. Then run this overlay beside it for the title, progress and
visualiser, with the art slot hidden.

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

**The music video never appears.** Check the *Video:* line on the Music panel's
now-playing box — it gives the reason. "No video linked to this track" means
YouTube Music has no video paired with that song. A lookup error usually means
yt-dlp is out of date: **Update yt-dlp** on the Clips page. Age-restricted and
bot-checked videos need the optional [YouTube sign-in](#youtube-sign-in-optional).

**The song pauses for a moment at every track change.** That's **Hold each new
song until its video is ready** keeping the song and video in step (8 seconds
at most). Turn on **Pre-buffer the next song's video** to shorten it, or turn
the hold off under Artwork if you'd rather the song never waits.

**The video drifts out of step.** Real video tracks are corrected to within
2 seconds. Paired videos (the song's video counterpart) are deliberately not
forced into step, because their timeline often differs from the song's (intros,
skits); switch the app to its Video toggle for a frame-accurate match.
