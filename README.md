# Hexcast

<p align="center">
  <img src="assets/hexcast.png" width="96" alt="Hexcast">
</p>

Self hosted, completely free set of stream tools to fully control audio and video to an OBS overlay. Edit position, scale, start times, stop times, volume, delay, etc. 
No data collection, no monthly fee, no sign ups, always free!

## ⚠️ Security: local network use only

**This has no authentication of any kind. It is designed to run on a trusted home/studio LAN — never expose it to the public internet.**

Anyone who can reach the port can:
- trigger any media into your stream,
- **upload arbitrary files** to your machine (the upload endpoint accepts files and writes them to disk),
- **delete any media** in your library,
- enumerate your entire library via the API.

There is no rate limiting and no input gating beyond file-extension checks. Do **not** port-forward this, do **not** put it on a public VPS, and do **not** assume "nobody knows the URL" protects you.

Safe ways to run it:
- On the same machine as OBS, accessed only via `localhost` (set `host="127.0.0.1"` near the bottom of `hexcast.py`).
- On a LAN, with a firewall rule restricting the port to your local subnet.
- If you genuinely need remote access, put it behind a reverse proxy (nginx/Caddy) that enforces authentication and TLS, on a private network or VPN — that's on you to set up correctly.

## Features

- **Folder-watch**: drop media into `media/audio/` or `media/video/` and it appears in the control panel instantly
- **Drag-and-drop uploads** from the control panel — audio extensions route to `audio/`, everything visual to `video/`
- **Auto-conversion**: dropped `.gif`/`.webp`/`.apng` files get transcoded to `.mp4` on the spot, so every clip is frame-precise seekable
- **Per-clip editor** for video: drag-to-position canvas, scale, volume (when the clip has audio), and a dual-thumb start/end trim slider with **▶ Preview** that plays the trimmed range live in the canvas
- **Per-clip editor** for audio: volume, start/end trim, and live preview through your speakers
- **Rename in editor**: change a clip's filename without touching the filesystem; the sidecar and poster follow automatically
- **Per-clip cooldown**: set ms-level cooldown on any clip so spam clicks don't queue up — `0` = current spam-friendly behavior
- **🔊 audio badge** on video buttons that carry an audio track
- **Concurrent playback**: spam-click to layer multiple clips at once (cooldowns are per-clip, so other clips still overlap freely)
- **Edit Mode** for tuning, **Delete Mode** for cleanup — no manual filesystem digging
- **Bot API**: trigger anything by name via a simple HTTP GET, no auth needed (designed for trusted LAN)
- **⏹ Stop All** panic button to clear every visual and stop every playing sound at once
- **Optional integrations**: Twitch chat and alert overlays, a now-playing overlay for the [YouTube Music Desktop App](https://ytmdesktop.github.io/), a Discord voice-reactive overlay, and a Twitch clip player with a bot-drivable queue — see [Integrations](#integrations)

## Prerequisites

- **Python 3.10+** (uses modern type union syntax)
- **ffmpeg** — for gif/webp → mp4 conversion, thumbnail generation, and duration/audio probing. Hexcast still runs without it, but animated GIFs won't be seekable, thumbnails won't generate, and the audio badge won't appear on video buttons.
- **OBS Studio 28+** with browser source support

## Install

Hexcast is meant to be **double-click-and-go**. You do **not** need to know anything about Python. The launcher (`start.bat` on Windows, `start.sh` on Mac/Linux) checks that everything is in place, sets itself up, downloads what it needs, and tells you in plain English if something's missing — you just run it.

### Windows — the easy way

1. **Install Python** (one time only). Open **https://www.python.org/downloads/**, click the big yellow **Download Python** button, and run the installer. On the first screen, **tick the box that says "Add python.exe to PATH"** — this is the one step that matters — then click **Install Now** and let it finish.
2. **Get Hexcast.** On [the Hexcast GitHub page](https://github.com/UMDSmith/hexcast), click the green **`< > Code`** button → **Download ZIP**. Then right-click the downloaded file → **Extract All…** → choose a permanent spot like your Documents folder.
   *(Know git already? `git clone https://github.com/UMDSmith/hexcast.git` also works and makes updating a one-liner.)*
3. **Run it.** Open the extracted `hexcast` folder and **double-click `start.bat`**. The first launch takes a minute or two while it downloads its components; every launch after that starts in seconds. When the black window shows `Control panel: http://localhost:4747/`, open that address in your browser — that's your control panel.

That's the whole thing. Leave the black window open while you stream; close it (or press a key) to stop Hexcast.

> If **Windows SmartScreen** pops up about running a `.bat`, click **More info → Run anyway**. It's just a plain-text launcher — you can open it in Notepad and read it yourself.

**ffmpeg is optional but recommended** — it lets animated GIFs convert and thumbnails generate. You can skip it for now and add it later; Hexcast runs fine without it. See [Installing ffmpeg](#installing-ffmpeg) when you're ready.

### Mac / Linux — the easy way

1. **Install Python 3.10+** if you don't already have it — macOS: `brew install python`; Ubuntu/Debian: `sudo apt install python3 python3-venv`.
2. **Get Hexcast** (clone, or download + extract the ZIP as above):
   ```bash
   git clone https://github.com/UMDSmith/hexcast.git
   cd hexcast
   ```
3. **Run it:**
   ```bash
   chmod +x start.sh   # first time only
   ./start.sh
   ```
   It sets everything up on the first run and just starts on later runs. Open `http://localhost:4747/` when it prints the address.

### Advanced / manual setup

The launchers aren't required — if you'd rather manage the environment yourself:

**Windows**
```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python hexcast.py
```

**Mac / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python hexcast.py
```

### Installing ffmpeg

Without ffmpeg the server still runs, but: animated GIFs/WebPs won't convert to MP4 (they stay as `<img>` elements with limited functionality), thumbnails won't generate (videos show empty cards), and the 🔊 audio badge won't appear on video buttons.

**Linux (Debian/Ubuntu):**
```bash
sudo apt install ffmpeg
```

**Linux (Fedora):**
```bash
sudo dnf install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

**Windows:**
1. Download a release build from https://www.gyan.dev/ffmpeg/builds/ (get "release essentials")
2. Extract somewhere permanent (e.g., `C:\ffmpeg\`)
3. Add `C:\ffmpeg\bin` to your PATH (System Properties → Environment Variables → Path → New)
4. Open a new terminal and verify: `ffmpeg -version`

### Docker

A `Dockerfile` is provided with a multistage build for efficient image creation. The image includes ffmpeg for full media support.

#### Build a standard image

For your current architecture (e.g., x86_64 on Linux, ARM64 on macOS):

```bash
docker build -t hexcast:latest .
```

#### Build for a specific architecture

To build for a different architecture:

```bash
# For AMD64 (x86_64)
docker build --platform linux/amd64 -t hexcast:latest .

# For ARM64 (Apple Silicon, Raspberry Pi, etc.)
docker build --platform linux/arm64 -t hexcast:latest .

# For ARMv7 (Raspberry Pi 32-bit)
docker build --platform linux/arm/v7 -t hexcast:latest .
```

#### Build and push multiarch images

To create and push a multiarch image to a registry (requires Docker Buildx):

```bash
docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t your-registry/hexcast:latest \
  --push .
```

#### Run the container

```bash
# Create media directories on the host
mkdir -p ./hexcast-media/audio ./hexcast-media/video

# Run the container
docker run -d \
  --name hexcast \
  -p 4747:4747 \
  -v ./hexcast-media:/app/media \
  hexcast:latest
```

Access the control panel at `http://localhost:4747/`

For OBS browser source, use: `http://<your-machine-ip>:4747/overlay`

**Notes:**
- The `-v ./hexcast-media:/app/media` mount persists your media library
- Media uploaded or discovered will be saved to `./hexcast-media/audio/` and `./hexcast-media/video/`
- Remember: **⚠️ This is for local networks only** — do not expose port 4747 to the internet

## Updating

Updating is safe: your library (`media/`) and all settings and secrets (`config/`) are ignored by git, so updating never touches them.

**Just get the new files, then run the launcher — it handles the rest.** `start.bat` / `start.sh` now notice when the requirements have changed and re-install what's needed automatically, so there are no manual `pip` commands to remember.

The top bar of every panel shows your version (e.g. `v1.1.0`); when a newer release is out it turns green and reads `• update` — that's your cue to update. Click it to open the repo.

**If you cloned with git:**
```bash
git pull
```
then double-click `start.bat` (Windows) or run `./start.sh` (Mac/Linux).

**If you downloaded the ZIP:** grab the latest ZIP from the green **`< > Code`** button again and extract it over your existing `hexcast` folder (keep your `media/` and `config/` folders), then run the launcher.

All four integrations (Twitch, YouTube Music, Discord, Clips) work straight from the main install — their dependencies are already included. The only separate optional extra is the **"react to real audio" visualiser**, which needs `numpy` + `soundcard`:
```
pip install -r requirements-ytm-audio.txt
```

**Docker:** pull, rebuild the image, and recreate the container (your media persists in the host mount):
```bash
git pull
docker build -t hexcast:latest .
docker stop hexcast && docker rm hexcast
docker run -d --name hexcast -p 4747:4747 -v ./hexcast-media:/app/media hexcast:latest
```

## Usage

### 1. Start the server

After running `start.sh` / `start.bat`, you'll see:

```
Control panel:       http://localhost:4747/
OBS browser source:  http://localhost:4747/overlay
Media root:          /path/to/hexcast/media
Canvas:              1920x1080
Posters (ffmpeg):    enabled
```

### 2. Add the browser source to OBS

In OBS: **Sources** → **+** → **Browser**.

- **URL:** `http://localhost:4747/overlay`
- **Width / Height:** match your canvas (typically 1920×1080)
- **Control audio via OBS:** ON (so audio routes through your OBS mixer)
- **Shutdown source when not visible:** OFF (kills the WebSocket otherwise)
- **Refresh browser when scene becomes active:** OFF

After clicking OK, select the source in the canvas and press **Ctrl+F** to fit to screen.

### 3. Add media

Two ways:
- **Drag-and-drop** onto the dropzone in the control panel
- **Drop directly** into `media/audio/` or `media/video/`

The folder watcher picks up new files instantly. Animated `.gif`/`.webp`/`.apng` files auto-convert to `.mp4` on first sight so the editor can seek into them frame-precisely.

### 4. Trigger clips

Click any button in the control panel. Multiple clicks layer naturally — spam them, they'll all fire (unless you've set a per-clip cooldown).

### 5. Edit a clip: position, volume, trim, rename, cooldown

Toggle **Edit Mode** in the top-right (clip buttons get an amber border + ✎). Click any clip to open its editor.

**Video editor** (any visual file — mp4, gif, png, etc.):
- **Position** — drag the clip around a 16:9 preview of your OBS canvas, or use the 3×3 quick-position grid
- **Scale** slider
- **Volume** slider — only shown for video files that actually have an audio track (the ones with a 🔊 badge on the button)
- **Playback range** — a dual-thumb slider on a single bar. Drag the start (◀) and end (▶) thumbs to trim. As you drag, the preview canvas scrubs to that frame so you can see exactly where you are.
- **▶ Preview** — plays the trimmed range inside the editor canvas with current position, scale, and volume so you can fine-tune everything before committing. ⏸ to stop.
- **Rename** — change the clip's filename. Sidecar and poster follow automatically.
- **Cooldown (ms)** — if > 0, after this clip fires, further triggers of *this clip* are dropped until the clip finishes playing plus the cooldown elapses. `0` means no cooldown (current spam-friendly behavior). Per-clip only; other clips still overlap freely.

**Audio editor**:
- Volume, trim, rename, cooldown — same as above. **▶ Preview** plays through your speakers.

Static images (`.png`, `.jpg`) can't be seeked; for those the trim becomes a single "Display duration" slider, and Preview is disabled.

Click **Test in OBS** to fire the current (unsaved) settings to OBS, **Save** to write them to the sidecar JSON. Defaults are omitted from the JSON to keep it clean: a clip with only a custom end time saves as just `{"volume": 1.0, "end": 3.5}`.

### 6. Delete media

Toggle **Delete Mode** (buttons get a red border + ✕). Click any button → confirms → removes the file, its sidecar, and its thumbnail.

### 7. Panic / Stop All

The **⏹ Stop All** button in the top-right instantly clears every visual from the overlay and stops all playing audio. Also available to bots at `GET /api/stop`.

## Configuration

### Media library location

By default media lives in `media/` next to the script. To store it elsewhere — a different drive, a NAS mount, a shared folder — set the `HEXCAST_MEDIA_DIR` environment variable. The `audio/` and `video/` subfolders are created automatically inside it.

If you're upgrading from a previous version with `sounds/`, `gifs/`, and `videos/` folders, Hexcast migrates them automatically on first run: contents of `sounds/` move to `audio/`, contents of `gifs/` and `videos/` merge into `video/`, and the old folders are removed when empty.

**Linux/macOS:**
```bash
export HEXCAST_MEDIA_DIR="/mnt/storage/hexcast"
./start.sh
```

**Windows:**
```cmd
set HEXCAST_MEDIA_DIR=D:\hexcast-media
start.bat
```

To make it permanent on Windows, set it via System Properties → Environment Variables. On Linux, add the `export` line to your `~/.bashrc`.

### Other settings

Edit the constants at the top of `hexcast.py`:

```python
PORT = 4747              # change if conflicting with another service
CANVAS_W = 1920          # only affects the editor preview proportions
CANVAS_H = 1080
DEFAULT_X = 50.0         # default position (% of canvas)
DEFAULT_Y = 50.0
DEFAULT_SCALE = 3.0      # default scale multiplier for new gifs/videos
```

And the bind host near the bottom of the file:

```python
uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
#                       ^^^^^^^^^ change to "127.0.0.1" for local-only (no LAN access)
```

The overlay itself is canvas-agnostic — it uses percentages, so the same setup works whether your OBS canvas is 1080p, 1440p, 4K, or vertical.

## Bot API

The server exposes an HTTP API designed for triggering media from chat bots, stream-deck buttons, scripts, or any HTTP client. **No authentication** — intended for trusted LAN use.

```
GET /api                         → endpoint reference
GET /api/list                    → JSON: { audio: [...], video: [...] }
GET|POST /api/play/{name}        → fuzzy: searches audio, then video
GET|POST /api/play/{kind}/{name} → explicit: kind = audio | video
    ?x=&y=&scale=                → optional position override (video)
    ?volume=                     → optional volume override 0.0-1.0 (audio, or video with audio)
    ?start=&end=                 → optional trim window in seconds (both kinds; static images use end only)
GET|POST /api/stop               → clear all visuals + stop all audio (panic button)
POST /rename                     → {file, kind, new_stem} → renames media file + sidecar + poster
```

Names are case-insensitive and match either the filename stem (`airhorn`) or full filename (`airhorn.mp3`). If you don't pass overrides, the values saved in the editor (position, scale, volume, trim) are applied automatically.

**Cooldowns**: if a clip has a non-zero `cooldown_ms` saved in its sidecar, triggers that arrive while the clip is still in its cooldown window return `{"ok": true, "delivered": 0, "suppressed": true, "next_in_ms": N}` and don't fire. Cooldowns are per-clip — other clips can still overlap freely.

### Examples

```bash
curl http://localhost:4747/api/list
curl http://localhost:4747/api/play/airhorn
curl http://localhost:4747/api/play/video/wow
curl "http://localhost:4747/api/play/video/cheer?x=80&y=20&scale=2&volume=0.6&end=3"
curl http://localhost:4747/api/stop
```

**Python:**
```python
import requests
requests.get("http://localhost:4747/api/play/airhorn")
```

**Node.js:**
```js
await fetch(`http://localhost:4747/api/play/${name}`);
```

### Triggering from a chat bot / Twitch redemptions

Hexcast ships a dedicated Twitch integration (see [Integrations](#integrations)) that handles this natively: the alerts panel can fire any clip from your library on follows, subs, gift subs, bits, raids, channel-point redeems, and hype trains — just put the clip name in the Clip column of the Alerts table. No bot required.

If you'd rather drive it from your own bot instead, the generic API still works: have it call `GET /api/play/{name}` when the relevant trigger fires. Map a redemption title or chat command to a media name and hit the endpoint. The overlay applies the saved position/scale/volume automatically, so the bot only needs to know the clip name.

## Integrations

Four optional modules ship alongside the soundboard. All are self-contained:
each is a single Python file plus its own pages in `static/`, each namespaces
all of its routes, and none changes how the soundboard behaves. Install
any combination, or none.

| | What it adds | Docs |
| --- | --- | --- |
| **Twitch** | Chat overlay, alert overlay for follows/subs/bits/raids/redeems with a FIFO alert queue, settings panel | [docs/twitch.md](docs/twitch.md) |
| **YouTube Music Desktop** | Now-playing overlay with album art, live progress, audio visualiser, optional embedded music video. Requires the [YouTube Music Desktop App](https://ytmdesktop.github.io/) — it pairs with that app's companion API, not with YouTube Music directly | [docs/music.md](docs/music.md) |
| **Discord** | Voice-reactive overlay: everyone in your current voice channel appears in OBS, lighting up as they speak — Discord avatars or custom PNGTuber-style idle/talking image pairs. Talks to the Discord desktop app's local RPC; no bot needed | [docs/discord.md](docs/discord.md) |
| **Clips** | Queue up Twitch clip/VOD links (paste one URL or a whole blob of chat), then fire them one at a time at a full-window overlay — no auto-advance. Direct MP4/HLS playback via yt-dlp with optional pre-download; bots trigger items by number over a simple GET API | [docs/clips.md](docs/clips.md) |

Twitch and Music both hook into the existing `GET /api/play/{name}` endpoint,
so a raid or a track change can fire a clip from your library. Both can also
POST every event to a URL of your choice, if you want a bot or a local model
reacting to chat and to what you're listening to.

**All four ship enabled out of the box.** They're already wired into
`hexcast.py` and their dependencies are part of the main install, so there is
nothing extra to download or edit — just run the launcher. You set each one up
(sign in / pair) from its own panel in the control panel; see the per-module
docs linked in the table above.

*Advanced:* each integration is just two lines in `hexcast.py` after the
`/media` mount — `from twitch import attach_twitch` then `attach_twitch(app, PORT)`,
and the same shape for `ytmusic`, `discord_reactive`, and `clips`. Delete a pair
to disable that module. The only add-on with its own separate dependency is the
optional "react to real audio" visualiser (`pip install -r requirements-ytm-audio.txt`).

A Twitch, a Music, a Discord, and a Clips button appear in the control panel's
top bar, each with a status dot showing whether that integration is currently
connected.

**These carry the same security caveat as everything else here, and then
some** — the Twitch module stores an OAuth token and a client secret, the
YouTube Music module stores a pairing token, and the Discord module stores an
RPC access token, all under `config/`. Keep that folder out of git and keep
the port on your LAN.

## Supported media formats

**Audio:** `.mp3`, `.wav`, `.ogg`, `.m4a`, `.flac`, `.opus`
**Video — static images:** `.png`, `.jpg`, `.jpeg` (no seeking, shown for a fixed duration)
**Video — animated images:** `.gif`, `.webp`, `.apng` (auto-converted to `.mp4` on first sight)
**Video — native:** `.mp4`, `.webm`, `.mov`, `.mkv`

For best video compatibility, transcode unfamiliar formats to H.264 + AAC MP4:

```bash
ffmpeg -i input.whatever -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -movflags +faststart output.mp4
```

## File layout

```
hexcast/
├── hexcast.py            # the server
├── twitch.py                # optional Twitch integration
├── ytmusic.py               # optional YouTube Music integration
├── discord_reactive.py      # optional Discord voice-reactive integration
├── clips.py                 # optional Twitch clip player integration
├── static/
│   ├── control.html         # control panel UI (HTML + CSS + JS)
│   ├── overlay.html         # OBS browser-source overlay
│   ├── twitch_panel.html    # Twitch settings panel
│   ├── twitch_chat.html     # Twitch chat overlay
│   ├── twitch_events.html   # Twitch alert overlay
│   ├── twitch_boot.js       # shared overlay helpers
│   ├── ytm_panel.html       # YouTube Music settings panel
│   ├── ytm_overlay.html     # YouTube Music now-playing overlay
│   ├── discord_panel.html   # Discord settings panel
│   ├── discord_overlay.html # Discord voice-reactive overlay
│   ├── clips_panel.html     # Clips queue + transport panel
│   └── clips_overlay.html   # Clips player overlay
├── docs/
│   ├── twitch.md
│   ├── music.md
│   ├── discord.md
│   └── clips.md
├── config/                  # tokens and integration settings (gitignored)
├── requirements.txt
├── requirements-twitch.txt
├── requirements-ytm.txt
├── requirements-ytm-audio.txt
├── requirements-discord.txt
├── start.sh / start.bat     # launcher scripts
├── README.md
├── LICENSE
└── media/                   # auto-created on first run
    ├── audio/               # .mp3, .wav, .ogg, .m4a, .flac, .opus
    └── video/               # .mp4, .webm, .png/.jpg (static), and animated images auto-converted to .mp4
```

Each clip can have two adjacent files:
- `airhorn.mp4` — the media
- `airhorn.json` — saved settings (created by Edit Mode)
- `airhorn.poster.jpg` — first-frame thumbnail (auto-generated by ffmpeg for video clips)

The sidecar JSON contains only non-default values. A typical video sidecar might be:
```json
{"x": 80, "y": 20, "scale": 2.5, "volume": 0.6, "end": 3.5, "cooldown_ms": 1500}
```

A typical audio sidecar:
```json
{"volume": 0.8, "start": 0.5, "cooldown_ms": 500}
```

You can hand-edit these if you prefer — the watcher ignores `.json` writes so it won't trigger a reindex loop.

## Troubleshooting

**Posters not generating, gifs animate in picker:** ffmpeg not in PATH. Run `ffmpeg -version` to verify.

**Audio doesn't play in OBS:** enable **Control audio via OBS** on the browser source. The soundboard appears in your Audio Mixer. Set monitoring to "Monitor and Output" if you want to hear it locally too.

**Browser source stays black:** right-click the source → **Interact** → check the DevTools console for errors. Make sure Width/Height match your canvas and the source has been transformed to fit (Ctrl+F).

**Changes to overlay CSS don't show up in OBS:** OBS aggressively caches. Right-click source → **Interact** → press **Ctrl+Shift+R** for a hard reload. Or append `?v=N` to the URL and bump N.

**Edit Mode opens once then breaks:** you have an old version. Update to the latest, which uses static posters in the editor preview (no looping video element).

**"unsupported extension" on upload:** the file type isn't in the lists above. Convert it first.

**Video plays in picker editor but is black/silent in OBS overlay:** codec issue, transcode as shown above.

**Page in browser shows SSL error:** you typed `https://`. The server only speaks `http://`. Type the URL explicitly, or disable HTTPS-only mode in your browser for this host.

## Roadmap

- Hotkey triggers via OBS WebSocket
- Search/filter box in the control panel for large libraries
- Multi-track audio splitting (separate OBS audio source per clip)

Twitch and YouTube Music now have optional modules of their own — see
[Integrations](#integrations). The soundboard core stays independent of both:
anything can still drive it through `GET /api/play/{name}` (see
[Triggering from a chat bot](#triggering-from-a-chat-bot--twitch-redemptions)).

## License

MIT — see [LICENSE](LICENSE).











