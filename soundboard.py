#!/usr/bin/env python3
"""
Hexcast: dead-simple folder-watching soundboard & clip overlay for OBS.

Drop media into ./media/{sounds,gifs,videos}/, then:
    python soundboard.py

Control panel:       http://localhost:4747/
OBS browser source:  http://localhost:4747/overlay

Dependencies:
    pip install fastapi "uvicorn[standard]" watchdog httpx python-multipart

Optional environment variables for web search:
    TENOR_API_KEY=...        # https://developers.google.com/tenor/guides/quickstart
    FREESOUND_API_KEY=...    # https://freesound.org/apiv2/apply

Per-gif positioning: each gif can have a sidecar .json with {"x":50,"y":50,"scale":3.0}.
Use the Edit Mode toggle in the control panel to drag-position visually.
"""

import asyncio
import json
import os
import re
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ---- config ----------------------------------------------------------------
PORT = 4747
ROOT = Path(__file__).parent
# SOUNDBOARD_MEDIA_DIR lets you point the library anywhere (another drive, shared folder,
# network mount, etc.) without editing this file. Defaults to ./media next to the script.
MEDIA_DIR = Path(os.getenv("SOUNDBOARD_MEDIA_DIR", str(ROOT / "media"))).expanduser().resolve()
GIFS_DIR = MEDIA_DIR / "gifs"
SOUNDS_DIR = MEDIA_DIR / "sounds"
VIDEOS_DIR = MEDIA_DIR / "videos"

# Create the media tree at import time. StaticFiles (mounted below) refuses to mount a
# directory that doesn't exist, and the mount runs before the lifespan startup handler —
# so on a fresh clone these must exist now, not later.
for _d in (MEDIA_DIR, GIFS_DIR, SOUNDS_DIR, VIDEOS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Canvas dimensions — match your OBS canvas. Affects only the editor preview.
CANVAS_W = 1920
CANVAS_H = 1080

# Defaults when a gif has no sidecar
DEFAULT_X = 50.0
DEFAULT_Y = 50.0
DEFAULT_SCALE = 3.0

IMAGE_EXTS = {".gif", ".webp", ".apng", ".png", ".jpg", ".jpeg"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}
GIF_EXTS = IMAGE_EXTS | VIDEO_EXTS   # what the gifs folder accepts (silent visuals)
SOUND_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus"}

TENOR_KEY = os.getenv("TENOR_API_KEY", "")
FREESOUND_KEY = os.getenv("FREESOUND_API_KEY", "")

# ---- state -----------------------------------------------------------------
index = {"gifs": [], "sounds": []}
overlay_clients: set[WebSocket] = set()
control_clients: set[WebSocket] = set()
main_loop: asyncio.AbstractEventLoop | None = None


# ---- helpers ---------------------------------------------------------------
def safe_filename(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w.\- ]", "_", name)
    name = re.sub(r"_+", "_", name).strip("._ ")
    return name or "unnamed"


def unique_path(dir_: Path, name: str) -> Path:
    p = dir_ / name
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 1
    while (dir_ / f"{stem}_{i}{suffix}").exists():
        i += 1
    return dir_ / f"{stem}_{i}{suffix}"


def kind_for_ext(ext: str) -> str | None:
    ext = ext.lower()
    if ext in GIF_EXTS:
        return "gif"
    if ext in SOUND_EXTS:
        return "sound"
    return None


def read_sidecar(media_path: Path) -> dict:
    """Read position/scale sidecar JSON next to a media file. Returns defaults if missing/bad."""
    sidecar = media_path.with_suffix(".json")
    if sidecar.exists():
        try:
            data = json.loads(sidecar.read_text())
            return {
                "x": float(data.get("x", DEFAULT_X)),
                "y": float(data.get("y", DEFAULT_Y)),
                "scale": float(data.get("scale", DEFAULT_SCALE)),
            }
        except Exception:
            pass
    return {"x": DEFAULT_X, "y": DEFAULT_Y, "scale": DEFAULT_SCALE}


def write_sidecar(media_path: Path, x: float, y: float, scale: float):
    sidecar = media_path.with_suffix(".json")
    sidecar.write_text(json.dumps({"x": x, "y": y, "scale": scale}, indent=2))


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


HAS_FFMPEG = check_ffmpeg()


def has_audio_stream(path: Path) -> bool:
    """Use ffprobe to check if a video file has an audio track. False if ffmpeg missing."""
    if not HAS_FFMPEG:
        return False
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0",
             str(path)],
            timeout=5, capture_output=True, text=True,
        )
        return r.returncode == 0 and r.stdout.strip() == "audio"
    except (subprocess.SubprocessError, OSError):
        return False


def ensure_poster(media_path: Path) -> Path | None:
    """Generate (or reuse) a first-frame JPEG poster next to the media file.
    Returns the poster path, or None if ffmpeg is unavailable or extraction failed."""
    if not HAS_FFMPEG:
        return None
    poster = media_path.parent / f"{media_path.stem}.poster.jpg"
    # Reuse if fresh
    if poster.exists():
        try:
            if poster.stat().st_mtime >= media_path.stat().st_mtime:
                return poster
        except OSError:
            pass
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(media_path), "-vframes", "1", "-q:v", "5",
             str(poster)],
            timeout=15, capture_output=True,
        )
        if r.returncode == 0 and poster.exists():
            return poster
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def cleanup_orphan_posters(dir_: Path, exts: set[str]):
    """Remove .poster.jpg files whose media file no longer exists."""
    if not dir_.exists():
        return
    suffix = ".poster.jpg"
    for poster in dir_.glob(f"*{suffix}"):
        stem = poster.name[:-len(suffix)]
        if not any((dir_ / f"{stem}{ext}").exists() for ext in exts):
            try:
                poster.unlink()
            except OSError:
                pass


def scan() -> dict:
    def collect(dir_: Path, exts: set[str], url_prefix: str, with_pos: bool = False) -> list[dict]:
        if not dir_.exists():
            return []
        if with_pos:
            cleanup_orphan_posters(dir_, exts)
        out = []
        for p in sorted(dir_.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            if p.name.endswith(".poster.jpg"):
                continue
            entry = {"name": p.stem, "file": p.name, "url": f"{url_prefix}/{p.name}"}
            if with_pos:
                entry["pos"] = read_sidecar(p)
                poster = ensure_poster(p)
                entry["poster"] = f"{url_prefix}/{poster.name}" if poster else entry["url"]
            out.append(entry)
        return out

    return {
        "gifs": collect(GIFS_DIR, GIF_EXTS, "/media/gifs", with_pos=True),
        "sounds": collect(SOUNDS_DIR, SOUND_EXTS, "/media/sounds"),
        "videos": collect(VIDEOS_DIR, VIDEO_EXTS, "/media/videos", with_pos=True),
    }


def reindex():
    global index
    index = scan()
    if main_loop:
        asyncio.run_coroutine_threadsafe(broadcast_index(), main_loop)


async def broadcast_index():
    msg = json.dumps({"type": "index", "data": index})
    dead = set()
    for ws in control_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    control_clients.difference_update(dead)


class WatchHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        if event.is_directory:
            return
        path = str(event.src_path)
        # Ignore our own bookkeeping files
        if path.endswith(".poster.jpg") or path.endswith(".json"):
            return
        reindex()


# ---- lifespan / app --------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_loop
    main_loop = asyncio.get_running_loop()
    # Dirs are created at import time; re-ensure here in case they were removed while idle.
    for d in (MEDIA_DIR, GIFS_DIR, SOUNDS_DIR, VIDEOS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    reindex()

    obs = Observer()
    obs.schedule(WatchHandler(), str(MEDIA_DIR), recursive=True)
    obs.start()

    print(f"\n  ==== Hexcast ====")
    print(f"  Control panel:       http://localhost:{PORT}/")
    print(f"  OBS browser source:  http://localhost:{PORT}/overlay")
    print(f"  Media root:          {MEDIA_DIR}")
    print(f"  Canvas:              {CANVAS_W}x{CANVAS_H}")
    print(f"  Posters (ffmpeg):    {'enabled' if HAS_FFMPEG else 'disabled (install ffmpeg)'}")
    print(f"  Tenor search:        {'enabled' if TENOR_KEY else 'disabled (set TENOR_API_KEY)'}")
    print(f"  Freesound search:    {'enabled' if FREESOUND_KEY else 'disabled (set FREESOUND_API_KEY)'}")
    print(f"\n  ! No authentication — keep this on a trusted LAN behind a firewall.")
    print(f"    Do NOT expose this to the internet.\n")
    yield
    obs.stop()
    obs.join()


app = FastAPI(lifespan=lifespan)
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")


# ---- HTML (inlined) --------------------------------------------------------
def control_html() -> str:
    return (CONTROL_HTML_TEMPLATE
            .replace("__CANVAS_W__", str(CANVAS_W))
            .replace("__CANVAS_H__", str(CANVAS_H))
            .replace("__ICON__", ICON_DATA_URI))


ICON_DATA_URI = "data:image/png;base64,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"


CONTROL_HTML_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Hexcast</title>
<link rel="icon" type="image/png" href="__ICON__">
<style>
  :root {
    color-scheme: dark;
    --bg: #1a1212;
    --panel: #221818;
    --card: #1d1515;
    --text: #e8dede;
    --muted: #a08c8c;
    --dim: #6a5555;
    --border: #3a2a2a;
    --border-hover: #5a3838;
    --red: #e63429;
    --red-soft: #ff7066;
    --red-deep: #7a1410;
    --amber: #d99320;
    --amber-soft: #f0c45a;
    --amber-deep: #4a3a16;
  }
  body { font: 14px system-ui, sans-serif; background: var(--bg); color: #ddd; margin: 0; padding: 16px; }
  h2 { margin: 18px 0 8px; font-weight: 500; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; }
  .topbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 12px; }
  .topbar .brand { display: flex; align-items: center; gap: 10px; }
  .topbar .logo { width: 34px; height: 34px; filter: drop-shadow(0 1px 3px rgba(0,0,0,0.6)); }
  .topbar h1 { font-size: 18px; margin: 0; font-weight: 700; letter-spacing: 0.5px;
               background: linear-gradient(180deg, #ff6055, var(--red));
               -webkit-background-clip: text; background-clip: text; color: transparent; }
  .editmode-btn { background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 4px;
                  padding: 6px 12px; cursor: pointer; font: inherit; font-size: 12px; }
  .editmode-btn:hover { background: #2a1e1e; border-color: var(--border-hover); }
  body.edit-mode #editmode { background: var(--amber-deep); border-color: var(--amber); color: var(--amber-soft); }
  body.delete-mode #deletemode { background: #3a1818; border-color: var(--red); color: var(--red-soft); }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; }
  button { background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 10px;
           cursor: pointer; font-size: 13px; text-align: left; transition: background 0.08s, border-color 0.08s;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: inherit; }
  button:hover { background: #2a1e1e; border-color: var(--border-hover); }
  button:active { background: #341f1f; transform: translateY(1px); }
  button.gif { aspect-ratio: 4/3; background-size: cover; background-position: center;
               background-color: #120c0c; position: relative; padding: 0; }
  button.gif span { position: absolute; bottom: 0; left: 0; right: 0; padding: 6px 8px;
                    background: linear-gradient(transparent, rgba(0,0,0,0.85));
                    text-shadow: 0 1px 2px rgba(0,0,0,0.9); }
  body.edit-mode #gifs button, body.edit-mode #videos button { border-color: var(--amber); }
  body.edit-mode #gifs button:hover, body.edit-mode #videos button:hover { border-color: var(--amber-soft); }
  body.edit-mode #gifs button::after, body.edit-mode #videos button::after {
    content: "✎"; position: absolute; top: 4px; right: 6px; color: var(--amber-soft);
    text-shadow: 0 1px 2px #000; font-size: 14px;
  }
  body.delete-mode #sounds button,
  body.delete-mode #gifs button,
  body.delete-mode #videos button {
    border-color: var(--red); position: relative;
  }
  body.delete-mode #sounds button:hover,
  body.delete-mode #gifs button:hover,
  body.delete-mode #videos button:hover {
    border-color: var(--red-soft); background: #2a1414;
  }
  body.delete-mode #sounds button::after,
  body.delete-mode #gifs button::after,
  body.delete-mode #videos button::after {
    content: "✕"; position: absolute; top: 4px; right: 6px; color: var(--red-soft);
    text-shadow: 0 1px 2px #000; font-size: 14px; font-weight: bold;
  }
  .empty { color: var(--dim); padding: 8px; font-style: italic; font-size: 13px; }
  .status { position: fixed; bottom: 8px; right: 10px; font-size: 11px; color: var(--dim); }
  .status.connected { color: var(--red-soft); }

  /* add-media panel */
  .addmedia { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 4px; }
  .dropzone { border: 2px dashed var(--border); border-radius: 6px; padding: 14px; text-align: center; color: var(--muted);
              cursor: pointer; transition: border-color 0.1s, background 0.1s; margin-bottom: 10px; font-size: 13px; }
  .dropzone:hover, .dropzone.dragover { border-color: var(--red); background: #2a1414; color: var(--text); }
  .dropzone a { color: var(--red-soft); text-decoration: underline; cursor: pointer; }
  .searchrow { display: flex; gap: 6px; }
  .searchrow select, .searchrow input, .searchrow button {
    background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 4px;
    padding: 6px 10px; font: inherit;
  }
  .searchrow input { flex: 1; }
  .searchrow button { cursor: pointer; }
  .searchrow button:hover { background: #2a1e1e; }
  .results { margin-top: 10px; max-height: 360px; overflow-y: auto;
             display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 6px; }
  .result { background: var(--card); border: 1px solid var(--border); border-radius: 4px;
            overflow: hidden; font-size: 11px; cursor: pointer; transition: border-color 0.1s; }
  .result:hover { border-color: var(--red); }
  .result.gif { padding: 0; aspect-ratio: 4/3; background-size: cover; background-position: center; position: relative; background-color: #120c0c; }
  .result.gif span { position: absolute; bottom: 0; left: 0; right: 0; padding: 4px 6px;
                     background: linear-gradient(transparent, rgba(0,0,0,0.85)); font-size: 10px;
                     overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .result.sound { padding: 8px; display: flex; flex-direction: column; gap: 4px; cursor: default; }
  .result.sound .name { font-size: 12px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .result.sound .meta { font-size: 10px; color: var(--muted); }
  .result.sound audio { width: 100%; height: 26px; }
  .result.sound .import { background: #3a1c1a; border-color: var(--red-deep); color: var(--red-soft); padding: 4px;
                          font-size: 11px; text-align: center; border-radius: 3px;
                          border: 1px solid var(--red-deep); cursor: pointer; }
  .result.sound .import:hover { background: #4a2420; }
  .results .empty, .results .err { grid-column: 1/-1; text-align: center; padding: 10px; }
  .results .err { color: var(--red-soft); }
  .toast { position: fixed; bottom: 30px; right: 10px; background: #2a1818; color: var(--red-soft);
           padding: 8px 12px; border-radius: 4px; font-size: 12px; opacity: 0;
           transition: opacity 0.2s; border: 1px solid var(--border); pointer-events: none; z-index: 1000; }
  .toast.show { opacity: 1; }
  .toast.err { background: #3a1414; color: #ff8a80; border-color: #5a2020; }

  /* Editor modal */
  .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 100;
                   display: flex; align-items: center; justify-content: center; padding: 20px; }
  .modal { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px;
           display: flex; flex-direction: column; gap: 12px; min-width: 720px; max-width: 90vw; }
  .modal-header { display: flex; justify-content: space-between; align-items: center;
                  color: var(--muted); font-size: 13px; }
  .modal-header .title { font-weight: 500; color: var(--text); }
  .modal-header .close { background: transparent; border: none; color: var(--dim); font-size: 22px;
                         cursor: pointer; padding: 0 6px; line-height: 1; }
  .modal-header .close:hover { color: var(--text); }
  .canvas-preview { background: #000; border: 1px solid var(--border); border-radius: 4px;
                    position: relative; width: 720px; aspect-ratio: 16/9; overflow: hidden; }
  .canvas-preview .grid-overlay {
    position: absolute; inset: 0; pointer-events: none;
    background-image:
      linear-gradient(rgba(255,255,255,0.06) 1px, transparent 1px),
      linear-gradient(90deg, rgba(255,255,255,0.06) 1px, transparent 1px);
    background-size: 10% 10%;
  }
  .media-wrapper { position: absolute; cursor: move; user-select: none;
                   transform-origin: center; will-change: transform; }
  .media-wrapper img, .media-wrapper video { display: block; pointer-events: none;
                                              max-width: none; max-height: none; }
  .editor-controls { display: flex; flex-direction: column; gap: 10px; }
  .editor-row { display: flex; align-items: center; gap: 10px; }
  .editor-row label { font-size: 12px; color: var(--muted); min-width: 50px; }
  .editor-row input[type=range] { flex: 1; accent-color: var(--red); }
  .editor-row .value { font-size: 12px; color: var(--text); min-width: 50px; text-align: right;
                       font-family: ui-monospace, monospace; }
  .quick-positions { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px;
                     width: 120px; aspect-ratio: 16/9; }
  .quick-positions button { padding: 0; font-size: 14px; background: var(--panel); border: 1px solid var(--border);
                            color: var(--muted); border-radius: 3px; min-height: 0; cursor: pointer; }
  .quick-positions button:hover { background: #2a1e1e; color: var(--text); border-color: var(--border-hover); }
  .modal-actions { display: flex; gap: 8px; justify-content: space-between; margin-top: 4px; }
  .modal-actions .left { display: flex; gap: 8px; }
  .modal-actions .right { display: flex; gap: 8px; }
  .modal-actions button { padding: 8px 14px; font-size: 13px; cursor: pointer; }
  .modal-actions .save { background: #3a1c1a; border-color: var(--red); color: var(--red-soft); }
  .modal-actions .save:hover { background: #4a2420; border-color: var(--red-soft); }
  .modal-actions .reset { background: var(--panel); border-color: var(--border-hover); color: var(--muted); }
  .modal-actions .reset:hover { background: #2a1e1e; color: var(--text); }
  .modal-actions .test { background: var(--amber-deep); border-color: var(--amber); color: var(--amber-soft); }
  .modal-actions .test:hover { background: #5a4520; }
</style></head>
<body data-canvas-w="__CANVAS_W__" data-canvas-h="__CANVAS_H__">
  <div class="topbar">
    <div class="brand">
      <img class="logo" src="__ICON__" alt="Hexcast">
      <h1>Hexcast</h1>
    </div>
    <div style="display:flex;gap:6px;">
      <button class="editmode-btn" id="editmode">Edit Mode</button>
      <button class="editmode-btn" id="deletemode">Delete Mode</button>
    </div>
  </div>

  <div class="addmedia">
    <div class="dropzone" id="dropzone">
      <input type="file" id="fileinput" multiple accept="audio/*,video/*,image/*" style="display:none">
      <span>Drop files here or <a id="browse">click to browse</a></span>
    </div>
    <div class="searchrow">
      <select id="searchtype">
        <option value="gif">GIFs</option>
        <option value="sound">Sounds</option>
      </select>
      <input id="searchbox" placeholder="search Tenor / Freesound…" type="search">
      <button id="searchbtn">Search</button>
    </div>
    <div id="results" class="results" hidden></div>
  </div>

  <h2>Sounds</h2>
  <div id="sounds" class="grid"></div>
  <h2>Videos</h2>
  <div id="videos" class="grid"></div>
  <h2>GIFs</h2>
  <div id="gifs" class="grid"></div>

  <div class="status" id="status">connecting…</div>
  <div class="toast" id="toast"></div>

<script>
const $ = (s) => document.querySelector(s);
const CANVAS_W = parseInt(document.body.dataset.canvasW, 10);
const CANVAS_H = parseInt(document.body.dataset.canvasH, 10);

let editMode = false;
let deleteMode = false;
const editBtn = $("#editmode");
const delBtn = $("#deletemode");

editBtn.onclick = () => {
  editMode = !editMode;
  if (editMode && deleteMode) {
    deleteMode = false;
    document.body.classList.remove("delete-mode");
    delBtn.textContent = "Delete Mode";
  }
  document.body.classList.toggle("edit-mode", editMode);
  editBtn.textContent = editMode ? "✓ Edit Mode" : "Edit Mode";
};

delBtn.onclick = () => {
  deleteMode = !deleteMode;
  if (deleteMode && editMode) {
    editMode = false;
    document.body.classList.remove("edit-mode");
    editBtn.textContent = "Edit Mode";
  }
  document.body.classList.toggle("delete-mode", deleteMode);
  delBtn.textContent = deleteMode ? "✕ Delete Mode" : "Delete Mode";
};

async function deleteItem(kind, item) {
  if (!confirm(`Delete ${kind} "${item.name}"?\n\nFile, saved position, and thumbnail will be removed.`)) return;
  try {
    const r = await fetch("/delete", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({kind, file: item.file})
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) toast(`delete failed: ${j.detail || r.statusText}`, true);
    else toast(`deleted ${kind}: ${item.name}`);
  } catch (e) { toast(`delete failed: ${e.message}`, true); }
}

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), 2800);
}

// ---- library render ----
function render(idx) {
  const sounds = $("#sounds"), gifs = $("#gifs"), videos = $("#videos");
  sounds.innerHTML = idx.sounds.length ? "" : '<div class="empty">drop audio into ./media/sounds/</div>';
  for (const s of idx.sounds) {
    const b = document.createElement("button");
    b.textContent = s.name;
    b.title = s.file;
    b.onclick = () => {
      if (deleteMode) deleteItem("sound", s);
      else fire("sound", s);
    };
    sounds.appendChild(b);
  }
  const renderVisual = (items, container, kind, emptyMsg) => {
    container.innerHTML = items.length ? "" : `<div class="empty">${emptyMsg}</div>`;
    for (const g of items) {
      const b = document.createElement("button");
      b.className = "gif";
      b.style.backgroundImage = `url("${g.poster || g.url}")`;
      const span = document.createElement("span");
      span.textContent = g.name;
      b.appendChild(span);
      const pos = g.pos || {x:50,y:50,scale:3};
      b.title = `${g.file} (x:${pos.x.toFixed(0)} y:${pos.y.toFixed(0)} scale:${pos.scale.toFixed(1)})`;
      b.onclick = () => {
        if (deleteMode) deleteItem(kind, g);
        else if (editMode) openEditor(g, kind);
        else fire(kind, g);
      };
      container.appendChild(b);
    }
  };
  renderVisual(idx.videos || [], videos, "video", "drop video clips with audio into ./media/videos/");
  renderVisual(idx.gifs, gifs, "gif", "drop gifs/images/clips into ./media/gifs/");
}

async function fire(type, item, overridePos) {
  const payload = {type, url: item.url, name: item.name, file: item.file};
  const pos = overridePos || item.pos;
  if (pos && (type === "gif" || type === "video")) {
    payload.x = pos.x; payload.y = pos.y; payload.scale = pos.scale;
  }
  await fetch("/trigger", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
}

// ---- editor modal ----
function openEditor(item, kind) {
  kind = kind || "gif";
  const pos = Object.assign({x:50,y:50,scale:3}, item.pos || {});
  let x = pos.x, y = pos.y, scale = pos.scale;

  const ov = document.createElement("div");
  ov.className = "modal-overlay";
  ov.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <span class="title">${item.name}</span>
        <button class="close" title="Close">×</button>
      </div>
      <div class="canvas-preview" id="preview">
        <div class="grid-overlay"></div>
        <div class="media-wrapper" id="wrapper"></div>
      </div>
      <div class="editor-controls">
        <div class="editor-row">
          <label>Scale</label>
          <input type="range" id="scaleslider" min="0.2" max="10" step="0.1" value="${scale}">
          <div class="value" id="scaleval">${scale.toFixed(1)}x</div>
        </div>
        <div class="editor-row">
          <label>Position</label>
          <div class="value" style="flex:1;text-align:left;">
            x:<span id="xval">${x.toFixed(0)}</span>% y:<span id="yval">${y.toFixed(0)}</span>%
          </div>
          <div class="quick-positions">
            <button data-pos="15,15" title="Top Left">↖</button>
            <button data-pos="50,15" title="Top">↑</button>
            <button data-pos="85,15" title="Top Right">↗</button>
            <button data-pos="15,50" title="Left">←</button>
            <button data-pos="50,50" title="Center">●</button>
            <button data-pos="85,50" title="Right">→</button>
            <button data-pos="15,85" title="Bottom Left">↙</button>
            <button data-pos="50,85" title="Bottom">↓</button>
            <button data-pos="85,85" title="Bottom Right">↘</button>
          </div>
        </div>
      </div>
      <div class="modal-actions">
        <div class="left">
          <button class="reset">Reset to Default</button>
          <button class="test">Test in OBS</button>
        </div>
        <div class="right">
          <button class="cancel">Cancel</button>
          <button class="save">Save</button>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(ov);

  const wrapper = ov.querySelector("#wrapper");
  const preview = ov.querySelector("#preview");
  const slider = ov.querySelector("#scaleslider");

  // Static poster only — no video, no animation, no memory leak
  const media = document.createElement("img");
  media.src = item.poster || item.url;
  media.draggable = false;
  wrapper.appendChild(media);

  // preview-to-canvas ratio so the wrapper renders at proportional size
  function previewRatio() {
    return preview.getBoundingClientRect().width / CANVAS_W;
  }

  function update() {
    const r = previewRatio();
    wrapper.style.left = x + "%";
    wrapper.style.top = y + "%";
    wrapper.style.transform = `translate(-50%, -50%) scale(${scale * r})`;
    ov.querySelector("#xval").textContent = x.toFixed(0);
    ov.querySelector("#yval").textContent = y.toFixed(0);
    ov.querySelector("#scaleval").textContent = scale.toFixed(1) + "x";
  }
  // Wait one frame so layout settles before measuring
  requestAnimationFrame(update);

  // Drag
  let dragging = false, startX, startY, startMx, startMy;
  wrapper.addEventListener("pointerdown", (e) => {
    dragging = true;
    startX = x; startY = y;
    startMx = e.clientX; startMy = e.clientY;
    wrapper.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  wrapper.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const rect = preview.getBoundingClientRect();
    const dx = ((e.clientX - startMx) / rect.width) * 100;
    const dy = ((e.clientY - startMy) / rect.height) * 100;
    x = Math.max(0, Math.min(100, startX + dx));
    y = Math.max(0, Math.min(100, startY + dy));
    update();
  });
  wrapper.addEventListener("pointerup", (e) => {
    dragging = false;
    try { wrapper.releasePointerCapture(e.pointerId); } catch (_) {}
  });

  // Scale slider
  slider.oninput = () => { scale = parseFloat(slider.value); update(); };

  // Quick positions
  ov.querySelectorAll(".quick-positions button").forEach(b => {
    b.onclick = () => {
      const [px, py] = b.dataset.pos.split(",").map(Number);
      x = px; y = py; update();
    };
  });

  // Actions
  const close = () => ov.remove();
  ov.querySelector(".close").onclick = close;
  ov.querySelector(".cancel").onclick = close;
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });

  ov.querySelector(".test").onclick = () => {
    fire(kind, item, {x, y, scale});
    toast("fired to OBS — not saved yet");
  };

  ov.querySelector(".save").onclick = async () => {
    try {
      const r = await fetch("/position", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({file: item.file, kind, x, y, scale})
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        toast(`save failed: ${j.detail || r.statusText}`, true);
        return;
      }
      item.pos = {x, y, scale};
      toast(`saved position for ${item.name}`);
      close();
    } catch (e) { toast(`save failed: ${e.message}`, true); }
  };

  ov.querySelector(".reset").onclick = async () => {
    if (!confirm("Reset to default position (center, scale 3.0)?")) return;
    try {
      await fetch("/position", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({file: item.file, kind, reset: true})
      });
      item.pos = {x: 50, y: 50, scale: 3};
      toast(`reset position for ${item.name}`);
      close();
    } catch (e) { toast(`reset failed: ${e.message}`, true); }
  };
}

// ---- upload (drag-drop + browse) ----
const dropzone = $("#dropzone");
const fileinput = $("#fileinput");
$("#browse").onclick = (e) => { e.preventDefault(); e.stopPropagation(); fileinput.click(); };
dropzone.onclick = () => fileinput.click();
fileinput.onchange = () => { uploadFiles(fileinput.files); fileinput.value = ""; };

let dragDepth = 0;
dropzone.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragover", (e) => e.preventDefault());
dropzone.addEventListener("dragleave", () => { dragDepth--; if (dragDepth <= 0) { dragDepth = 0; dropzone.classList.remove("dragover"); } });
dropzone.addEventListener("drop", (e) => {
  e.preventDefault(); dragDepth = 0; dropzone.classList.remove("dragover");
  uploadFiles(e.dataTransfer.files);
});

async function uploadFiles(files) {
  for (const file of files) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/upload", { method: "POST", body: fd });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) toast(`${file.name}: ${j.detail || r.statusText}`, true);
      else toast(`added ${j.kind}: ${j.name}`);
    } catch (e) { toast(`${file.name}: ${e.message}`, true); }
  }
}

// ---- search & import ----
const searchbox = $("#searchbox"), searchbtn = $("#searchbtn"),
      searchtype = $("#searchtype"), results = $("#results");

async function doSearch() {
  const q = searchbox.value.trim();
  if (!q) { results.hidden = true; return; }
  results.hidden = false;
  results.innerHTML = '<div class="empty">searching…</div>';
  try {
    const r = await fetch(`/search?q=${encodeURIComponent(q)}&type=${searchtype.value}`);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { results.innerHTML = `<div class="err">${j.detail || r.statusText}</div>`; return; }
    renderResults(j.results || [], searchtype.value);
  } catch (e) { results.innerHTML = `<div class="err">${e.message}</div>`; }
}

function renderResults(items, type) {
  if (!items.length) { results.innerHTML = '<div class="empty">no results</div>'; return; }
  results.innerHTML = "";
  for (const item of items) {
    const el = document.createElement("div");
    el.className = `result ${type}`;
    if (type === "gif") {
      el.style.backgroundImage = `url("${item.preview}")`;
      const span = document.createElement("span");
      span.textContent = item.name;
      el.appendChild(span);
      el.title = item.name + " — click to import";
      el.onclick = () => importItem(item);
    } else {
      const name = document.createElement("div");
      name.className = "name"; name.textContent = item.name; name.title = item.name;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = item.duration ? `${item.duration.toFixed(1)}s` : "";
      const audio = document.createElement("audio");
      audio.controls = true; audio.preload = "none"; audio.src = item.preview;
      const btn = document.createElement("div");
      btn.className = "import"; btn.textContent = "import";
      btn.onclick = () => importItem(item);
      el.append(name, meta, audio, btn);
    }
    results.appendChild(el);
  }
}

async function importItem(item) {
  toast(`importing ${item.name}…`);
  try {
    const r = await fetch("/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(item)
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) toast(`import failed: ${j.detail || r.statusText}`, true);
    else toast(`imported: ${j.name}`);
  } catch (e) { toast(`import failed: ${e.message}`, true); }
}

searchbtn.onclick = doSearch;
searchbox.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// ---- websocket for live index updates ----
let ws;
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws/control`);
  ws.onopen = () => { $("#status").textContent = "connected"; $("#status").classList.add("connected"); };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "index") render(m.data);
  };
  ws.onclose = () => {
    $("#status").textContent = "reconnecting…";
    $("#status").classList.remove("connected");
    setTimeout(connect, 1000);
  };
}
connect();
fetch("/index").then(r => r.json()).then(render);
</script></body></html>
"""

OVERLAY_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Hexcast Overlay</title>
<style>
  html, body { margin: 0; padding: 0; background: transparent; overflow: hidden;
               width: 100vw; height: 100vh; }
  #stage { position: fixed; inset: 0; }
  #stage img, #stage video {
    position: absolute;
    transform-origin: center;
    filter: drop-shadow(0 8px 24px rgba(0,0,0,0.5));
    animation: pop 200ms ease-out;
  }
  @keyframes pop {
    0%   { opacity: 0; }
    100% { opacity: 1; }
  }
  .fade-out { animation: fade 300ms ease-in forwards !important; }
  @keyframes fade { to { opacity: 0; } }
</style></head>
<body>
<div id="stage"></div>
<script>
const stage = document.getElementById("stage");
const GIF_DURATION_MS = 4000;

function playSound(url) {
  const a = new Audio(url);
  a.play().catch(e => console.error("audio play failed:", e));
}

function removeWithFade(el) {
  el.classList.add("fade-out");
  setTimeout(() => { if (el.parentNode === stage) stage.removeChild(el); }, 320);
}

function showGif(url, x, y, scale) {
  if (x === undefined) x = 50;
  if (y === undefined) y = 50;
  if (scale === undefined) scale = 3;
  const isVideo = /\.(mp4|webm|mov|mkv)$/i.test(url);
  const el = isVideo ? document.createElement("video") : document.createElement("img");
  el.src = url;
  el.style.left = x + "%";
  el.style.top = y + "%";
  el.style.transform = `translate(-50%, -50%) scale(${scale})`;
  if (isVideo) {
    el.autoplay = true; el.muted = true; el.playsInline = true; el.loop = false;
    el.onended = () => removeWithFade(el);
  } else {
    setTimeout(() => removeWithFade(el), GIF_DURATION_MS);
  }
  stage.appendChild(el);
}

function showVideo(url, x, y, scale) {
  if (x === undefined) x = 50;
  if (y === undefined) y = 50;
  if (scale === undefined) scale = 3;
  const el = document.createElement("video");
  el.src = url;
  el.style.left = x + "%";
  el.style.top = y + "%";
  el.style.transform = `translate(-50%, -50%) scale(${scale})`;
  el.autoplay = true; el.muted = false; el.playsInline = true; el.loop = false;
  el.onended = () => removeWithFade(el);
  stage.appendChild(el);
  // Force playback and recover from autoplay restrictions by trying muted as fallback
  const tryPlay = el.play();
  if (tryPlay && typeof tryPlay.catch === "function") {
    tryPlay.catch((err) => {
      console.error("video autoplay blocked, retrying muted:", err);
      el.muted = true;
      el.play().catch(e => console.error("muted retry also failed:", e));
    });
  }
}

let ws;
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws/overlay`);
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "sound") playSound(m.url);
    else if (m.type === "gif") showGif(m.url, m.x, m.y, m.scale);
    else if (m.type === "video") showVideo(m.url, m.x, m.y, m.scale);
  };
  ws.onclose = () => setTimeout(connect, 1000);
}
connect();
</script></body></html>
"""


# ---- routes ----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def control_page():
    return control_html()


@app.get("/overlay", response_class=HTMLResponse)
async def overlay_page():
    return OVERLAY_HTML


@app.get("/index")
async def get_index():
    return JSONResponse(index)


async def _broadcast_trigger(payload: dict) -> dict:
    """Broadcast a trigger to all overlays. Auto-merges sidecar position for gifs/videos."""
    if payload.get("type") in ("gif", "video") and "x" not in payload:
        url = payload.get("url", "")
        for prefix, d in (("/media/gifs/", GIFS_DIR), ("/media/videos/", VIDEOS_DIR)):
            if url.startswith(prefix):
                path = d / url[len(prefix):]
                if path.exists():
                    payload.update(read_sidecar(path))
                break

    msg = json.dumps(payload)
    dead = set()
    for ws in overlay_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    overlay_clients.difference_update(dead)
    return {"ok": True, "delivered": len(overlay_clients)}


def find_media(name: str, kind: str | None = None) -> tuple[str, dict] | None:
    """Find media by name (case-insensitive, matches stem or full filename).
    Returns (kind_singular, entry) or None. Search order: sounds, gifs, videos."""
    nl = name.lower()
    plurals = {"sound": "sounds", "gif": "gifs", "video": "videos"}
    search = [kind] if kind in plurals else list(plurals.keys())
    for k in search:
        for item in index.get(plurals[k], []):
            if item["name"].lower() == nl or item["file"].lower() == nl:
                return (k, item)
    return None


@app.post("/trigger")
async def trigger(payload: dict):
    """Fire a media event to all connected overlays (raw payload, used internally)."""
    return await _broadcast_trigger(payload)


# ---- bot-friendly API ------------------------------------------------------

@app.get("/api")
async def api_root():
    return {
        "list":          "GET /api/list",
        "play_by_name":  "GET|POST /api/play/{name}",
        "play_by_kind":  "GET|POST /api/play/{kind}/{name}    (kind = sound|gif|video)",
        "position_override (gif/video)": "?x=50&y=50&scale=2",
        "examples": [
            "curl http://host:4747/api/play/airhorn",
            "curl http://host:4747/api/play/gif/wow",
            "curl 'http://host:4747/api/play/video/cheer?x=80&y=20&scale=2'",
        ],
    }


@app.get("/api/list")
async def api_list():
    """List all triggerable media. Useful for bots to discover what's available."""
    return {
        "sounds": [s["name"] for s in index.get("sounds", [])],
        "gifs":   [g["name"] for g in index.get("gifs", [])],
        "videos": [v["name"] for v in index.get("videos", [])],
    }


@app.api_route("/api/play/{kind}/{name}", methods=["GET", "POST"])
async def api_play_kind(kind: str, name: str,
                        x: float | None = None, y: float | None = None, scale: float | None = None):
    if kind not in ("sound", "gif", "video"):
        raise HTTPException(400, "kind must be sound, gif, or video")
    found = find_media(name, kind=kind)
    if not found:
        raise HTTPException(404, f"no {kind} named '{name}'")
    _, item = found
    payload = {"type": kind, "url": item["url"], "name": item["name"], "file": item["file"]}
    if x is not None:     payload["x"] = x
    if y is not None:     payload["y"] = y
    if scale is not None: payload["scale"] = scale
    return await _broadcast_trigger(payload)


@app.api_route("/api/play/{name}", methods=["GET", "POST"])
async def api_play_fuzzy(name: str,
                         x: float | None = None, y: float | None = None, scale: float | None = None):
    """Trigger by name across all kinds. Search order: sounds, gifs, videos. First match wins."""
    found = find_media(name)
    if not found:
        raise HTTPException(404, f"no media named '{name}'")
    kind, item = found
    payload = {"type": kind, "url": item["url"], "name": item["name"], "file": item["file"]}
    if x is not None:     payload["x"] = x
    if y is not None:     payload["y"] = y
    if scale is not None: payload["scale"] = scale
    return await _broadcast_trigger(payload)


@app.post("/delete")
async def delete_media(payload: dict):
    """Delete a media file along with its sidecar JSON and poster JPG (if any)."""
    file = payload.get("file")
    kind = payload.get("kind")
    if kind not in ("sound", "gif", "video") or not file:
        raise HTTPException(400, "file and kind ('sound', 'gif', 'video') required")

    target_dir = {"sound": SOUNDS_DIR, "gif": GIFS_DIR, "video": VIDEOS_DIR}[kind]
    path = target_dir / safe_filename(file)
    if not path.exists():
        raise HTTPException(404, f"file not found: {file}")

    # Remove sidecar + poster first so the only remaining filesystem event
    # is the media-file removal (which triggers reindex).
    sidecar = path.with_suffix(".json")
    if sidecar.exists():
        sidecar.unlink(missing_ok=True)
    poster = target_dir / f"{path.stem}.poster.jpg"
    if poster.exists():
        poster.unlink(missing_ok=True)
    path.unlink()
    return {"ok": True, "deleted": path.name, "kind": kind}


@app.post("/position")
async def set_position(payload: dict):
    file = payload.get("file")
    kind = payload.get("kind")
    if kind not in ("gif", "video") or not file:
        raise HTTPException(400, "file and kind ('gif' or 'video') required")

    target_dir = GIFS_DIR if kind == "gif" else VIDEOS_DIR
    path = target_dir / safe_filename(file)
    if not path.exists():
        raise HTTPException(404, f"media file not found: {file}")

    sidecar = path.with_suffix(".json")
    if payload.get("reset"):
        if sidecar.exists():
            sidecar.unlink()
    else:
        x = float(payload.get("x", DEFAULT_X))
        y = float(payload.get("y", DEFAULT_Y))
        scale = float(payload.get("scale", DEFAULT_SCALE))
        write_sidecar(path, x, y, scale)
    # No reindex. /trigger reads the sidecar fresh when it fires.
    return {"ok": True}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SOUND_EXTS and ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
        raise HTTPException(400, f"unsupported extension: {ext or '(none)'}")

    content = await file.read()
    name = safe_filename(file.filename or f"upload{ext}")

    if ext in SOUND_EXTS:
        target_dir, kind = SOUNDS_DIR, "sound"
    elif ext in IMAGE_EXTS:
        target_dir, kind = GIFS_DIR, "gif"
    else:
        # Video extension — probe for audio, route accordingly
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            if has_audio_stream(tmp_path):
                target_dir, kind = VIDEOS_DIR, "video"
            else:
                target_dir, kind = GIFS_DIR, "gif"
        finally:
            tmp_path.unlink(missing_ok=True)

    path = unique_path(target_dir, name)
    path.write_bytes(content)
    return {"ok": True, "name": path.name, "kind": kind}


@app.get("/search")
async def search(q: str, type: str = "gif", limit: int = 24):
    if not q.strip():
        return {"results": []}

    if type == "gif":
        if not TENOR_KEY:
            raise HTTPException(
                400,
                "TENOR_API_KEY not set — get a free key at https://developers.google.com/tenor/guides/quickstart"
            )
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://tenor.googleapis.com/v2/search", params={
                "q": q, "key": TENOR_KEY, "limit": limit,
                "media_filter": "mp4,tinygif", "contentfilter": "off",
                "client_key": "hexcast",
            })
            r.raise_for_status()
            data = r.json()
        out = []
        for item in data.get("results", []):
            mf = item.get("media_formats", {})
            if "mp4" not in mf or "tinygif" not in mf:
                continue
            label = item.get("content_description") or item.get("title") or item.get("id")
            out.append({
                "name": str(label)[:60],
                "preview": mf["tinygif"]["url"],
                "url": mf["mp4"]["url"],
                "ext": ".mp4",
                "kind": "gif",
            })
        return {"results": out}

    if type == "sound":
        if not FREESOUND_KEY:
            raise HTTPException(
                400,
                "FREESOUND_API_KEY not set — get a free key at https://freesound.org/apiv2/apply"
            )
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://freesound.org/apiv2/search/text/", params={
                "query": q, "token": FREESOUND_KEY,
                "fields": "id,name,previews,duration",
                "page_size": limit,
            })
            r.raise_for_status()
            data = r.json()
        out = []
        for item in data.get("results", []):
            preview = (item.get("previews") or {}).get("preview-hq-mp3")
            if not preview:
                continue
            out.append({
                "name": (item.get("name") or str(item.get("id")))[:60],
                "preview": preview,
                "url": preview,
                "ext": ".mp3",
                "duration": item.get("duration"),
                "kind": "sound",
            })
        return {"results": out}

    raise HTTPException(400, "type must be 'gif' or 'sound'")


@app.post("/import")
async def import_url(payload: dict):
    url = payload.get("url")
    kind = payload.get("kind")
    name = payload.get("name", "")
    ext = payload.get("ext", "")
    if not url or kind not in ("gif", "sound"):
        raise HTTPException(400, "url and kind ('gif' or 'sound') required")
    target_dir = GIFS_DIR if kind == "gif" else SOUNDS_DIR
    if not name:
        name = Path(url.split("?")[0]).name
    name = safe_filename(name)
    if not Path(name).suffix:
        name = f"{name}{ext or '.bin'}"
    path = unique_path(target_dir, name)
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        path.write_bytes(r.content)
    return {"ok": True, "name": path.name, "kind": kind}


@app.websocket("/ws/overlay")
async def ws_overlay(ws: WebSocket):
    await ws.accept()
    overlay_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        overlay_clients.discard(ws)


@app.websocket("/ws/control")
async def ws_control(ws: WebSocket):
    await ws.accept()
    control_clients.add(ws)
    await ws.send_text(json.dumps({"type": "index", "data": index}))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        control_clients.discard(ws)


if __name__ == "__main__":
    # host="0.0.0.0" listens on all interfaces (LAN access). Change to "127.0.0.1"
    # if you want local-only access on this machine.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
