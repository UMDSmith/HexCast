# Hexcast

<p align="center">
  <img src="assets/hexcast.png" width="96" alt="Hexcast">
</p>

A self-hosted, folder-watching soundboard for OBS streams. Drop GIFs, sounds, and video clips into a folder, click buttons in a web control panel, and they fire into your OBS overlay. Includes a clean HTTP API so chat bots can trigger media too.

Local, no accounts, no rate limits, and you own your library.

## ⚠️ Security: local network use only

**This has no authentication of any kind. It is designed to run on a trusted home/studio LAN — never expose it to the public internet.**

Anyone who can reach the port can:
- trigger any media into your stream,
- **upload arbitrary files** to your machine (the upload endpoint accepts files and writes them to disk),
- **delete any media** in your library,
- enumerate your entire library via the API.

There is no rate limiting and no input gating beyond file-extension checks. Do **not** port-forward this, do **not** put it on a public VPS, and do **not** assume "nobody knows the URL" protects you.

Safe ways to run it:
- On the same machine as OBS, accessed only via `localhost` (set `host="127.0.0.1"` near the bottom of `soundboard.py`).
- On a LAN, with a firewall rule restricting the port to your local subnet (see [Network access & firewall](#network-access--firewall)).
- If you genuinely need remote access, put it behind a reverse proxy (nginx/Caddy) that enforces authentication and TLS, on a private network or VPN — that's on you to set up correctly.

## Features

- **Folder-watch**: drop media into `media/sounds/`, `media/gifs/`, or `media/videos/` and it appears in the control panel instantly
- **Drag-and-drop uploads** from the control panel with smart routing (videos with audio → `videos/`, silent MP4s → `gifs/`)
- **Web search** for GIFs (Tenor) and sounds (Freesound) with one-click import — optional, requires free API keys
- **Per-clip positioning**: drag-to-position editor with scale slider, saved to sidecar JSON next to each file
- **Concurrent playback**: spam-click to layer multiple gifs and sounds at once
- **Video clips with audio** routed through OBS's PipeWire/audio path
- **Edit Mode** for positioning, **Delete Mode** for cleanup — no manual filesystem digging
- **Bot API**: trigger anything by name via a simple HTTP GET, no auth needed (designed for trusted LAN)
- **Static thumbnails**: first-frame poster generation via ffmpeg keeps the picker calm

## Prerequisites

- **Python 3.10+** (uses modern type union syntax)
- **ffmpeg** (for poster thumbnails and audio detection on upload)
- **OBS Studio 28+** with browser source support

## Install

### Linux / macOS

```bash
git clone https://github.com/UMDSmith/HexCast.git
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
python soundboard.py
```

### Windows

```cmd
git clone https://github.com/UMDSmith/HexCast.git
cd hexcast
start.bat
```

Or double-click `start.bat` from Explorer.

Manual setup:
```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python soundboard.py
```

### Installing ffmpeg

Without ffmpeg the server still runs, but gifs animate in the picker (no static thumbnails) and uploaded MP4s can't be auto-routed by audio content.

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
- **Drop directly** into `media/sounds/`, `media/gifs/`, or `media/videos/`

The folder watcher picks up new files instantly.

### 4. Trigger clips

Click any button in the control panel. Multiple clicks layer naturally — spam them, they'll all fire.

### 5. Position GIFs and Videos visually

Toggle **Edit Mode** in the top-right. Click any GIF or video → the drag editor opens with a 16:9 preview of your OBS canvas. Drag the clip where you want, scrub the scale slider, click **Test in OBS** to preview, then **Save**. The position is stored in a `.json` sidecar next to the media file.

### 6. Delete media

Toggle **Delete Mode**. Click any button → confirms → removes the file, its sidecar, and its thumbnail.

## Configuration

### Media library location

By default media lives in `media/` next to the script. To store it elsewhere — a different drive, a NAS mount, a shared folder — set the `SOUNDBOARD_MEDIA_DIR` environment variable. The `sounds/`, `gifs/`, and `videos/` subfolders are created automatically inside it.

**Linux/macOS:**
```bash
export SOUNDBOARD_MEDIA_DIR="/mnt/storage/soundboard"
./start.sh
```

**Windows:**
```cmd
set SOUNDBOARD_MEDIA_DIR=D:\soundboard-media
start.bat
```

To make it permanent on Windows, set it via System Properties → Environment Variables. On Linux, add the `export` line to your `~/.bashrc`, or use `Environment=` in the systemd unit (see [Running on startup](#running-on-startup)).

### Other settings

Edit the constants at the top of `soundboard.py`:

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
GET /api/list                    → JSON: { sounds: [...], gifs: [...], videos: [...] }
GET|POST /api/play/{name}        → fuzzy: searches sounds, gifs, videos
GET|POST /api/play/{kind}/{name} → explicit: kind = sound | gif | video
    ?x=&y=&scale=                → optional position override
```

Names are case-insensitive and match either the filename stem (`airhorn`) or full filename (`airhorn.mp3`).

### Examples

```bash
curl http://localhost:4747/api/list
curl http://localhost:4747/api/play/airhorn
curl http://localhost:4747/api/play/gif/wow
curl "http://localhost:4747/api/play/video/cheer?x=80&y=20&scale=2"
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
ExecStart=%h/hexcast/.venv/bin/python %h/hexcast/soundboard.py
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

**Sounds:** `.mp3`, `.wav`, `.ogg`, `.m4a`, `.flac`, `.opus`
**Images / animated:** `.gif`, `.webp`, `.apng`, `.png`, `.jpg`, `.jpeg`
**Videos:** `.mp4`, `.webm`, `.mov`, `.mkv`

For best video compatibility, transcode unfamiliar formats to H.264 + AAC MP4:

```bash
ffmpeg -i input.whatever -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -movflags +faststart output.mp4
```

## File layout

```
hexcast/
├── soundboard.py        # the server (single file)
├── requirements.txt
├── start.sh / start.bat # launcher scripts
├── README.md
├── LICENSE
└── media/               # auto-created on first run
    ├── sounds/          # .mp3, .wav, etc.
    ├── gifs/            # .gif, .png, silent .mp4, etc.
    └── videos/          # .mp4, .webm with audio
```

Each gif/video can have two adjacent files:
- `airhorn.mp4` — the media
- `airhorn.json` — saved position (created by Edit Mode)
- `airhorn.poster.jpg` — first-frame thumbnail (auto-generated by ffmpeg)

You can edit the `.json` by hand if you prefer: `{"x": 50, "y": 80, "scale": 2.5}`.

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

- Twitch channel-point redemption integration (consumer of `/api/play/{name}`)
- Per-clip cooldowns to prevent spam
- Hotkey triggers via OBS WebSocket
- Multi-track audio splitting (separate OBS audio source per clip kind)

## License

MIT — see [LICENSE](LICENSE).



<img width="1219" height="1067" alt="image" src="https://github.com/user-attachments/assets/fe07e6ae-96b2-4372-9d44-1baacd37830b" />
<img width="1560" height="1025" alt="image" src="https://github.com/user-attachments/assets/faf593e7-765d-4311-b93e-c32474c38e36" />
<img width="1219" height="1067" alt="image" src="https://github.com/user-attachments/assets/78f89072-fb07-4f9a-a823-535cd23a1275" />
<img width="1560" height="1025" alt="image" src="https://github.com/user-attachments/assets/c286a815-3836-49fe-8a75-1d5a3d676633" />
<img width="1599" height="898" alt="image" src="https://github.com/user-attachments/assets/a6b3a8d0-a3af-480e-b153-350ad4ca2bbf" />
This is only washed out in brightness in the screenshot.


