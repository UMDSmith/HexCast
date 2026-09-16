# Hexcast

Self-hosted, completely free stream tools that push audio and video to an OBS
overlay — with full control over position, scale, start/stop times, volume,
delay, and more. **No data collection, no monthly fee, no sign-ups, always
free.**

---

## ⚠️ Security: local network use only

**Hexcast has no authentication of any kind. It is designed to run on a trusted
home/studio LAN — never expose it to the public internet.**

Anyone who can reach the port can:
- trigger any media into your stream,
- **upload arbitrary files** to your machine (the upload endpoint accepts files and writes them to disk),
- **delete any media** in your library,
- enumerate your entire library via the API.

There is no rate limiting and no input gating beyond file-extension checks. Do
**not** port-forward this, do **not** put it on a public VPS, and do **not**
assume "nobody knows the URL" protects you.

**Safe ways to run it:**
- On the same machine as OBS, reached only via `localhost` (set the bind host to `127.0.0.1` near the bottom of `hexcast.py`).
- On a LAN, with a firewall rule restricting the port to your local subnet.
- If you genuinely need remote access, put it behind a reverse proxy (nginx/Caddy) that enforces authentication and TLS, on a private network or VPN — that's on you to set up correctly.

The optional integrations store secrets under `config/` (a Twitch OAuth token +
client secret, a YouTube Music pairing token, a Discord RPC token). Keep that
folder out of git and keep the port on your LAN.

---

## What can Hexcast do?

At its core, Hexcast is a **soundboard + media launcher** that drives a single
set of OBS browser-source overlays from a web control panel. On top of that core
sit five optional **integration tabs** — Twitch, Music, Discord, Clips, and
Countdown — each self-contained, each with its own overlay and settings panel,
each reachable from a button in the control panel's top bar. Turn on as many or
as few as you like; none of them changes how the soundboard behaves.

Everything runs on your own machine and streams to OBS over your LAN.

### 🔊 Soundboard (the core)

**What it does.** Trigger audio and video clips into your stream with a click.
Clips **layer** — spam them and they all fire at once (unless you set a per-clip
cooldown). A **⏹ Stop All** panic button clears every visual and stops every
sound instantly.

Each clip has an in-panel **editor**:
- **Video** — drag-to-position on a 16:9 canvas preview (or a 3×3 quick grid), scale, volume (for clips with an audio track), a dual-thumb **start/end trim** with live **▶ Preview**, per-clip **chroma key** (color + tolerance + eyedropper, keyed inside Hexcast so overlapping green-screen clips composite cleanly), rename, and a per-clip **cooldown**.
- **Audio** — volume, trim, rename, cooldown, with preview through your speakers.

**Edit Mode** and **Delete Mode** let you tune or remove clips without touching
the filesystem.

**How it works.** A small local web server hosts the control panel at
`http://localhost:4747/` and drives the overlay at `/overlay` over a websocket.
Media lives in `media/audio/` and `media/video/`; a folder watcher imports new
files instantly, drag-and-drop uploads route by type, and animated
`.gif`/`.webp`/`.apng` files auto-convert to seekable `.mp4`. Per-clip settings
save to a small sidecar JSON next to the file. Anything can also be triggered by
name over a simple HTTP **[Bot API](#bot-api)** — great for chat bots and
stream-deck buttons.

> **This `/overlay` is where every soundboard clip plays — including clips fired
> by the other tabs** (Twitch alerts, Countdown cues, a Music track-change clip).
> Those integrations trigger the soundboard rather than drawing on their own
> overlays, so keep the base `/overlay` source in your scene alongside whatever
> integration overlays you're using.

### 💬 Twitch

**What it does.** Adds a **chat overlay** and an **alert overlay** for follows,
subs, gift subs, bits, raids, channel-point redeems, and hype trains, with a
FIFO alert queue. Any alert can **fire a soundboard clip by name** — just put
the clip in the Clip column of the Alerts table — so you don't need a separate
bot for sound alerts. (Those clips play on the base soundboard overlay
(`/overlay`), so keep it in your scene alongside the Twitch overlays.) The
**`!so` shoutout** command fires the official Twitch shoutout banner, a
configurable chat line, and a random clip of that channel at the **Clips**
overlay — all in one.

**How it works.** You sign in from the Twitch panel; Hexcast subscribes to
Twitch EventSub and renders the overlays. It can also POST every event to a
forward URL if you want your own bot or a model reacting to chat.
See [docs/twitch.md](docs/twitch.md).

### 🎵 Music

**What it does.** A now-playing overlay — album art, live progress, an accent
colour pulled from the artwork, and an audio visualiser — from one of **two
sources you pick in the panel**:

- **YouTube Music** — mirrors the [YouTube Music Desktop App](https://ytmdesktop.github.io/), optionally embedding the music video in the card.
- **Local files** — point it at a music folder (**Browse…** opens your OS's native folder picker), then browse or **search-as-you-type** across a library of **any size**, build a **queue** (multi-select, "add whole folder + subfolders", drag-to-reorder, virtualised so 15k tracks scroll smoothly), save queues as reusable **playlists** (load to replace or append), and **preview** tracks right in the panel while you curate. Local audio plays *inside the overlay* so OBS captures it, and the visualiser reacts to the real audio.

**How it works.** YouTube Music comes over the desktop app's companion server.
For local files, Hexcast builds a **cached index of your folder** — saved to disk
and loaded instantly on every launch, so you only press **Re-index** when the
files on disk actually change. That makes folder browsing and type-ahead search
(filtered in the browser over the cached index) instant even on tens of thousands
of tracks. Files are served with HTTP range requests for instant seeking and
played by an `<audio>` element in the overlay with a Web Audio visualiser. A
plain-text `!song` endpoint is included for chat bots. See [docs/music.md](docs/music.md).

### 🎙️ Discord

**What it does.** A voice-reactive overlay: everyone in your current Discord
voice channel appears in OBS and lights up as they speak — using Discord avatars
or custom PNGTuber-style idle/talking image pairs.

**How it works.** It talks to the Discord **desktop app's local RPC** — no bot,
no server-side token setup beyond authorizing once.
See [docs/discord.md](docs/discord.md).

### 🎬 Clips

**What it does.** Queue up Twitch clip/VOD links — paste a single URL or a whole
blob of chat — then fire them **one at a time** to a full-window overlay (no
auto-advance, so you stay in control). Bots can trigger items by number, and the
Twitch **`!so` shoutouts** play through this same overlay. An optional
**auto-leveler** brings every clip (and shoutout) in at a consistent volume, so
one clip doesn't blast while the next whispers.

**How it works.** yt-dlp resolves direct MP4/HLS playback, with optional
pre-download so a clip is ready the instant you play it. With **auto-level** on,
the server measures each clip's loudness with ffmpeg (streamed through it —
nothing is downloaded or saved) and the overlay turns louder clips down toward a
target loudness (default −16 LUFS). It normalises *toward* the target, so it
never adds start-up lag; a clip that plays before it's measured (e.g. an instant
shoutout) corrects its volume a moment in. See [docs/clips.md](docs/clips.md).

### ⏱️ Countdown

**What it does.** A fully styleable countdown timer overlay — count down a fixed
duration or to a wall-clock time — positioned on a 1920×1080 stage like the chat
window. **Media cues** can fire any number of soundboard clips, each anchored to
its own countdown threshold: set a clip to *end* at a point (e.g. intro music
finishing exactly at 0:00, timed backwards from the clip's length) or to *start*
at a point.

**How it works.** The server owns the authoritative remaining time and streams
it to the overlay, so clock skew between machines never matters; cues are
computed against that timer and fired through the soundboard. **Those clips play
on the base soundboard overlay (`/overlay`), not the countdown overlay** — keep
both browser sources in your scene. See [docs/countdown.md](docs/countdown.md).

---

## Install, upgrade & uninstall

### What you need

- **Python 3.10+** (uses modern type-union syntax).
- **OBS Studio 28+** with browser-source support.
- **ffmpeg** — *optional but recommended*. It powers gif/webp → mp4 conversion, thumbnails, media-duration/audio probing, and the Music tab's local tag/cover-art reading. Hexcast runs without it, but animated GIFs won't be seekable, thumbnails won't generate, the 🔊 audio badge won't appear, and local music shows filenames only.

### Install — the easy way

Hexcast is meant to be **double-click-and-go**; you don't need to know anything
about Python. The launcher (`start.bat` on Windows, `start.sh` on Mac/Linux)
checks everything, sets itself up, downloads what it needs, and tells you in
plain English if something's missing.

**Windows**
1. **Install Python** (one time). Open <https://www.python.org/downloads/>, click **Download Python**, run the installer, and on the first screen **tick "Add python.exe to PATH"** before clicking **Install Now**.
2. **Get Hexcast.** On [the GitHub page](https://github.com/UMDSmith/hexcast), click **`< > Code` → Download ZIP**, then right-click → **Extract All…** to a permanent spot like your Documents folder. *(Prefer git? `git clone https://github.com/UMDSmith/hexcast.git` makes updating a one-liner.)*
3. **Run it.** Double-click **`start.bat`**. First launch takes a minute while it sets up; later launches start in seconds. When the window shows `Control panel: http://localhost:4747/`, open that address in your browser.

   > If **Windows SmartScreen** appears, click **More info → Run anyway** — it's a plain-text launcher you can open in Notepad.

**Mac / Linux**
1. Install Python 3.10+ — macOS: `brew install python`; Ubuntu/Debian: `sudo apt install python3 python3-venv`.
2. Get Hexcast: `git clone https://github.com/UMDSmith/hexcast.git && cd hexcast` (or download + extract the ZIP).
3. Run it:
   ```bash
   chmod +x start.sh   # first time only
   ./start.sh
   ```
   Open `http://localhost:4747/` when it prints the address.

Leave the launcher window open while you stream; close it to stop Hexcast.

### Set it up in OBS

1. In OBS: **Sources → + → Browser**.
2. **URL:** `http://localhost:4747/overlay` · **Width/Height:** match your canvas (typically 1920×1080).
3. **Control audio via OBS:** ON (routes sound through your mixer). **Shutdown source when not visible:** OFF (otherwise the websocket dies). **Refresh when scene becomes active:** OFF.
4. Select the source and press **Ctrl+F** to fit.

Each integration adds its own browser source (e.g. `/ytm/overlay`,
`/twitch/chat`, `/countdown/overlay`); its panel shows the exact URL.

### Install — advanced / manual

The launchers aren't required:

```bash
# Windows: python -m venv .venv && .venv\Scripts\activate
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python hexcast.py
```

All five integrations work from the main install — their dependencies are
already included. The only separate optional extra is the **"react to real
audio" visualiser**, which needs `numpy` + `soundcard`:
`pip install -r requirements-ytm-audio.txt`.

Each integration is just two lines in `hexcast.py` after the `/media` mount
(e.g. `from twitch import attach_twitch` then `attach_twitch(app, PORT)`, and
the same shape for `ytmusic`, `discord_reactive`, `clips`, `countdown`). Delete
a pair to disable that module.

### Install — Docker

```bash
docker build -t hexcast:latest .          # add --platform linux/amd64|arm64|arm/v7 to cross-build
mkdir -p ./hexcast-media/audio ./hexcast-media/video
docker run -d --name hexcast -p 4747:4747 -v ./hexcast-media:/app/media hexcast:latest
```

The image bundles ffmpeg. The `-v` mount persists your library. Control panel at
`http://localhost:4747/`; OBS source at `http://<your-machine-ip>:4747/overlay`.
For a registry multiarch push: `docker buildx build --platform
linux/amd64,linux/arm64,linux/arm/v7 -t your-registry/hexcast:latest --push .`.
**Still LAN-only — don't expose port 4747 to the internet.**

### Installing ffmpeg

- **Debian/Ubuntu:** `sudo apt install ffmpeg` · **Fedora:** `sudo dnf install ffmpeg` · **macOS:** `brew install ffmpeg`
- **Windows:** download "release essentials" from <https://www.gyan.dev/ffmpeg/builds/>, extract to e.g. `C:\ffmpeg\`, add `C:\ffmpeg\bin` to your PATH (System Properties → Environment Variables → Path → New), open a new terminal and verify with `ffmpeg -version`.

### Upgrading

Your library (`media/`) and all settings/secrets (`config/`) are git-ignored, so
updating never touches them. The top bar of every panel shows your version; when
a newer release is out it turns green and reads **• update**.

- **Git:** `git pull`, then run the launcher.
- **ZIP:** download the latest from **`< > Code`** and extract over your existing folder (keep `media/` and `config/`), then run the launcher.
- **Docker:** `git pull && docker build -t hexcast:latest . && docker stop hexcast && docker rm hexcast`, then re-run the `docker run …` command above.

The launcher notices when requirements changed and re-installs automatically —
no manual `pip` to remember.

### Uninstalling

Hexcast is self-contained — everything lives inside its folder.

1. Stop it (close the launcher window, or `docker stop hexcast && docker rm hexcast && docker rmi hexcast:latest`).
2. Delete the `hexcast` folder — the app, its `.venv`, your `media/`, and `config/` all live there; nothing is installed elsewhere. (Back up `media/` and `config/` first if you want to keep them.)
3. Remove the browser source(s) you added in OBS.
4. Optional: uninstall Python and ffmpeg if you added them only for Hexcast.

### Caveats

- **Security:** LAN-only, no auth — see the **⚠️ Security** warning at the top. Integration tokens live under `config/`; keep it private.
- **ffmpeg** is optional but strongly recommended (see above for what you lose without it).
- **Local music playback** happens in the OBS overlay browser source, so open `/ytm/overlay` (in OBS or a browser) to actually hear it; the panel's **▸ Preview** is a local audition only. Playback is limited to codecs the browser engine supports (MP3, M4A/AAC, OGG/Opus, FLAC, WAV all work; exotic formats like WMA may not).

---

## Reference

### Bot API

Trigger media over plain HTTP — for chat bots, stream decks, scripts, anything.
**No authentication** (trusted-LAN use).

```
GET  /api                          → endpoint reference
GET  /api/list                     → JSON: { audio: [...], video: [...] }
GET|POST /api/play/{name}          → fuzzy: searches audio, then video
GET|POST /api/play/{kind}/{name}   → explicit: kind = audio | video
     ?x=&y=&scale=                 → optional position override (video)
     ?volume=                      → optional volume override 0.0–1.0
     ?start=&end=                  → optional trim window in seconds
GET|POST /api/stop                 → clear all visuals + stop all audio (panic)
POST /rename                       → {file, kind, new_stem} → rename media + sidecar + poster
```

Names are case-insensitive and match the filename stem (`airhorn`) or full name
(`airhorn.mp3`). Saved editor values (position/scale/volume/trim) apply
automatically. If a clip has a non-zero `cooldown_ms`, triggers during its
cooldown return `{"ok": true, "delivered": 0, "suppressed": true, "next_in_ms": N}`.

```bash
curl http://localhost:4747/api/play/airhorn
curl "http://localhost:4747/api/play/video/cheer?x=80&y=20&scale=2&volume=0.6&end=3"
curl http://localhost:4747/api/stop
```

For Twitch redemptions, the Twitch integration handles this natively (no bot
needed); or map a redemption/command to a clip name and call `/api/play/{name}`
from your own bot.

### Supported media formats

- **Audio:** `.mp3`, `.wav`, `.ogg`, `.m4a`, `.flac`, `.opus`
- **Video — static images:** `.png`, `.jpg`, `.jpeg` (shown for a fixed duration)
- **Video — animated images:** `.gif`, `.webp`, `.apng` (auto-converted to `.mp4`)
- **Video — native:** `.mp4`, `.webm`, `.mov`, `.mkv`

For best compatibility, transcode unfamiliar video to H.264 + AAC:
```bash
ffmpeg -i input.whatever -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -movflags +faststart output.mp4
```

### Configuration

**Media location.** Media lives in `media/` by default; set `HEXCAST_MEDIA_DIR`
to store it elsewhere (a drive, NAS mount, shared folder) — `audio/` and
`video/` are created inside it. Upgrading from old `sounds/`/`gifs/`/`videos/`
folders migrates automatically on first run.

```bash
export HEXCAST_MEDIA_DIR="/mnt/storage/hexcast"   # Windows: set HEXCAST_MEDIA_DIR=D:\hexcast-media
```

**Other settings** — constants at the top of `hexcast.py` (`PORT`, `CANVAS_W/H`,
`DEFAULT_X/Y`, `DEFAULT_SCALE`) and the bind host near the bottom:

```python
uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
#                       ^^^^^^^^^ change to "127.0.0.1" for local-only (no LAN access)
```

The overlay is canvas-agnostic (percentage-based), so the same setup works at
1080p, 1440p, 4K, or vertical.

### File layout

```
hexcast/
├── hexcast.py                 # the server (soundboard core)
├── twitch.py                  # optional Twitch integration
├── ytmusic.py                 # optional Music integration (YouTube Music source)
├── localmusic.py              # optional Music integration (local-file player, shares the Music tab)
├── discord_reactive.py        # optional Discord voice-reactive integration
├── clips.py                   # optional Twitch clip player integration
├── countdown.py               # optional countdown timer integration
├── static/                    # control panel + every overlay/panel (HTML/CSS/JS)
├── docs/                      # per-integration docs: twitch, music, discord, clips, countdown
├── config/                    # tokens, playlists & integration settings (gitignored)
├── requirements*.txt          # core + per-integration dependency lists
├── start.sh / start.bat       # launchers
└── media/                     # auto-created: audio/ and video/
```

Each clip may have adjacent files: `airhorn.mp4` (media), `airhorn.json` (saved
settings, non-default values only), `airhorn.poster.jpg` (auto thumbnail). You
can hand-edit the JSON; the watcher ignores `.json` writes.

### Troubleshooting

- **Check `hexcast.log` first** (next to `hexcast.py`, rotated at 1 MB × 3). It records conversion/poster failures with ffmpeg's real error output, and playback errors reported back from inside the OBS browser source.
- **No posters / gifs animate in the picker:** ffmpeg isn't in PATH — run `ffmpeg -version`.
- **Clip fires but nothing shows, log says "decode failed":** the file is corrupt — re-upload (a race in versions ≤1.1.0 could corrupt converted gifs; fixed since).
- **No audio in OBS:** enable **Control audio via OBS** on the browser source; it appears in the Audio Mixer.
- **Browser source stays black:** right-click → **Interact** → check the DevTools console; confirm Width/Height match the canvas and you've fit with Ctrl+F.
- **Overlay CSS changes don't show:** OBS caches hard — **Interact → Ctrl+Shift+R**, or append `?v=N` to the URL.
- **"unsupported extension" / black-in-OBS video:** convert to H.264 + AAC MP4 as shown above.
- **SSL error in the browser:** you typed `https://`; the server only speaks `http://`.

---

## Links

- **Repository & downloads:** <https://github.com/UMDSmith/hexcast>
- **Integration docs:** [Twitch](docs/twitch.md) · [Music](docs/music.md) · [Discord](docs/discord.md) · [Clips](docs/clips.md) · [Countdown](docs/countdown.md)
- **License:** MIT — see [LICENSE](LICENSE)

<p align="center">
  <img src="assets/hexcast.png" width="96" alt="Hexcast">
</p>
