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
- On a LAN, with a firewall rule restricting the port to your local subnet (see [Network access & firewall](#network-access--firewall)).
- If you genuinely need remote access, put it behind a reverse proxy (nginx/Caddy) that enforces authentication and TLS, on a private network or VPN — that's on you to set up correctly.

## Features

- **Folder-watch**: drop media into `media/audio/` or `media/video/` and it appears in the control panel instantly
- **Drag-and-drop uploads** from the control panel — audio extensions route to `audio/`, everything visual to `video/`
- **Auto-conversion**: dropped `.gif`/`.webp`/`.apng` files get transcoded to `.mp4` on the spot, so every clip is frame-precise seekable
- **Web search** for video clips (Tenor) and audio (Freesound) with one-click import — optional, requires free API keys
- **Per-clip editor** for video: drag-to-position canvas, scale, volume (when the clip has audio), and a dual-thumb start/end trim slider with **▶ Preview** that plays the trimmed range live in the canvas
- **Per-clip editor** for audio: volume, start/end trim, and live preview through your speakers
- **Rename in editor**: change a clip's filename without touching the filesystem; the sidecar and poster follow automatically
- **Per-clip cooldown**: set ms-level cooldown on any clip so spam clicks don't queue up — `0` = current spam-friendly behavior
- **🔊 audio badge** on video buttons that carry an audio track
- **Concurrent playback**: spam-click to layer multiple clips at once (cooldowns are per-clip, so other clips still overlap freely)
- **Edit Mode** for tuning, **Delete Mode** for cleanup — no manual filesystem digging
- **Bot API**: trigger anything by name via a simple HTTP GET, no auth needed (designed for trusted LAN)
- **⏹ Stop All** panic button to clear every visual and stop every playing sound at once

## Prerequisites

- **Python 3.10+** (uses modern type union syntax)
- **ffmpeg** — for gif/webp → mp4 conversion, thumbnail generation, and duration/audio probing. Hexcast still runs without it, but animated GIFs won't be seekable, thumbnails won't generate, and the audio badge won't appear on video buttons.
- **OBS Studio 28+** with browser source support

## Install

### Linux / macOS

```bash
git clone https://github.com/YOUR_USERNAME/hexcast.git
cd hexcast
chmod +x start.sh
./start.sh
```

`start.sh` creates a venv, installs dependencies, and launches the server. Subsequent runs just launch.

If you prefer manual setup:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python hexcast.py
```

### Windows

```cmd
git clone https://github.com/YOUR_USERNAME/hexcast.git
cd hexcast
start.bat
```

Or double-click `start.bat` from Explorer.

Manual setup:
```cmd
python -m venv .venv
.venv\Scripts\activate
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

## Usage

### 1. Start the server

After running `start.sh` / `start.bat`, you'll see:

```
Control panel:       http://localhost:4747/
OBS browser source:  http://localhost:4747/overlay
Media root:          /path/to/hexcast/media
Canvas:              1920x1080
Posters (ffmpeg):    enabled
Tenor search:        disabled (set TENOR_API_KEY)
Freesound search:    disabled (set FREESOUND_API_KEY)
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

Three ways:
- **Drag-and-drop** onto the dropzone in the control panel
- **Search** Tenor/Freesound (requires API keys, see below)
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

To make it permanent on Windows, set it via System Properties → Environment Variables. On Linux, add the `export` line to your `~/.bashrc`, or use `Environment=` in the systemd unit (see [Running on startup](#running-on-startup)).

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

## Web search (optional)

Both providers are free with a quick sign-up.

### Tenor (GIFs)

1. https://developers.google.com/tenor/guides/quickstart → create a project, enable Tenor API, generate a key
2. Set the environment variable `TENOR_API_KEY` before launching:

   **Linux/macOS:**
   ```bash
   export TENOR_API_KEY="AIza..."
   ./start.sh
   ```

   **Windows:**
   ```cmd
   set TENOR_API_KEY=AIza...
   start.bat
   ```

   Or set it permanently via System Properties → Environment Variables.

### Freesound (Sound effects)

1. Sign up at https://freesound.org/
2. Apply at https://freesound.org/apiv2/apply (instant approval)
3. Set `FREESOUND_API_KEY` the same way

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

There's no dedicated Twitch script — Hexcast stays generic. If you already have a bot monitoring Twitch chat, channel-point redemptions, or events, just have it call `GET /api/play/{name}` when the relevant trigger fires. Map a redemption title or chat command to a media name and hit the endpoint. The overlay applies the saved position/scale/volume automatically, so the bot only needs to know the clip name.

## Network access & firewall

The server binds to `0.0.0.0`, so the control panel is reachable from any device on your network. Limit access with your firewall.

### Linux (UFW)

```bash
sudo ufw allow from 192.168.1.0/24 to any port 4747 proto tcp
```

Replace `192.168.1.0/24` with your actual LAN range. Find it with `ip route`.

### Windows Defender Firewall

1. Open **Windows Defender Firewall with Advanced Security**
2. **Inbound Rules** → **New Rule** → Port → TCP, specific port 4747 → Allow the connection
3. In the **Scope** tab, restrict **Remote IP addresses** to your LAN range

## Running on startup

### Linux (systemd user service)

Create `~/.config/systemd/user/hexcast.service`:

```ini
[Unit]
Description=Hexcast
After=network.target

[Service]
WorkingDirectory=%h/hexcast
ExecStart=%h/hexcast/.venv/bin/python %h/hexcast/hexcast.py
Restart=on-failure
# Optional API keys:
# Environment=TENOR_API_KEY=AIza...
# Environment=FREESOUND_API_KEY=...

[Install]
WantedBy=default.target
```

Enable:
```bash
systemctl --user daemon-reload
systemctl --user enable --now hexcast
journalctl --user -u hexcast -f   # watch logs
```

### Windows (Task Scheduler)

1. Open **Task Scheduler** → **Create Task**
2. **General**: name it "Hexcast", select "Run only when user is logged on"
3. **Triggers**: New → "At log on"
4. **Actions**: New → Program/script: full path to `start.bat`, Start in: the project folder
5. **Conditions**: uncheck "Start only if on AC power" if on a laptop
6. **Settings**: check "Allow task to be run on demand"

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
├── static/
│   ├── control.html         # control panel UI (HTML + CSS + JS)
│   └── overlay.html         # OBS browser-source overlay
├── requirements.txt
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

Twitch integration is intentionally out of scope — point any bot at `GET /api/play/{name}` (see [Triggering from a chat bot](#triggering-from-a-chat-bot--twitch-redemptions)).

## License

MIT — see [LICENSE](LICENSE).


<img width="1560" height="1178" alt="image" src="https://github.com/user-attachments/assets/fb81bb83-ff32-4376-b000-b199edfb5cae" />
<img width="1560" height="1178" alt="image" src="https://github.com/user-attachments/assets/04f1080d-f4a7-4559-a9ac-a5815428bb9d" />
<img width="1560" height="1178" alt="image" src="https://github.com/user-attachments/assets/129b170d-36e9-4a5d-8c8f-4df6d7f20678" />


https://github.com/user-attachments/assets/c0579df7-ed5a-4602-a7e0-e5c038506679






