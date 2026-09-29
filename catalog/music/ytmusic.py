"""
HexCast - YouTube Music Desktop module
======================================

A Hexcast plugin (see plugin.py): install it from the + tab and it adds:
    http://localhost:4747/ytm          -> control panel (pair + settings)
    http://localhost:4747/ytm/overlay  -> now-playing overlay (OBS browser source)

Requires YouTube Music Desktop App 2.0.0+ with the Companion Server enabled
(Settings -> Integrations -> Companion Server), plus "Enable companion
authorization" switched on while pairing.

State arrives over the app's Socket.IO feed rather than polling, so the
progress bar is live and the REST rate limits never come into play.

The Music tab has a second source: a local-file player (localmusic.py), mounted
automatically by attach_ytm() (a late import, so the two modules never import each
other at load time). A "source" config key
("ytm" | "local") decides which player drives the shared overlay; see
active_source(). The two share this module's overlay, panel, websocket hub,
config and now-playing contract.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import socketio

# Audio reactivity is optional. Without these the visualiser still runs, driven
# by playback position instead of real levels.
try:
    import warnings as _warnings

    import numpy as _np
    import soundcard as _sc

    # WASAPI reports a discontinuity whenever the device goes briefly idle or
    # resamples. It is routine for a loopback tap and says nothing useful, but
    # soundcard warns on every occurrence and floods the console.
    try:
        _warnings.filterwarnings("ignore", category=_sc.SoundcardRuntimeWarning)
    except Exception:
        _warnings.filterwarnings("ignore", message="data discontinuity in recording")

    HAS_AUDIO_CAPTURE = True
    _AUDIO_IMPORT_ERROR = ""
except Exception as _exc:          # pragma: no cover - depends on the host
    _np = _sc = None
    HAS_AUDIO_CAPTURE = False
    _AUDIO_IMPORT_ERROR = str(_exc)
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse, Response,
                               StreamingResponse)
from starlette.background import BackgroundTask

from hexcast_core import paths

# --------------------------------------------------------------------------
# paths / constants
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent          # this plugin's folder
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = paths.CONFIG_DIR
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = CONFIG_DIR / "ytmusic.json"
SECRETS_PATH = CONFIG_DIR / "ytmusic_secrets.json"

# appId must be lowercase alphanumeric, 2-32 chars. appVersion must be semver.
APP_ID = "hexcast"
APP_NAME = "HexCast"
APP_VERSION = "1.0.0"

TRACK_STATES = {-1: "unknown", 0: "paused", 1: "playing", 2: "buffering"}

# /api/v1/realtime is a Socket.IO *namespace*, not the transport path. The JS
# client infers this from the URL path; python-socketio needs it spelled out,
# and the transport itself stays on the default /socket.io/ endpoint.
NAMESPACE = "/api/v1/realtime"


def _read_static(name: str) -> str:
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The YouTube Music module "
            f"needs ytm_panel.html and ytm_overlay.html in the plugin's static/ folder."
        )
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    # The companion server binds to IPv4 only. On Windows "localhost" can
    # resolve to ::1, which fails, so the default is the literal address.
    "host": "127.0.0.1",
    "port": 9863,
    "hexcast_url": "http://127.0.0.1:4747",
    "forward_url": "",
    "track_change_clip": "",
    "use_socketio": True,
    "poll_interval": 2,
    # Which player drives the shared overlay: "ytm" observes the YouTube Music
    # Desktop app; "local" plays files from a mapped directory (see localmusic.py).
    "source": "ytm",
    "local": {
        "music_dir": "",           # folder to index / play from
    },
    # YouTube sign-in used when looking up music-video streams: "" (not linked)
    # | "firefox" | "chrome". Only the Link button on the Music page sets it
    # (the generic config POST ignores it). What's saved is the browser's NAME;
    # yt-dlp reads that browser's cookies itself at lookup time and Hexcast
    # never copies or stores them. Separate from the clips page's link.
    "youtube_login": {"browser": ""},
    "overlay": {
        "layout": "card",
        "font_family": "Inter",
        "title_size": 30,
        "artist_size": 20,
        "title_weight": 800,
        "artist_weight": 500,
        "text_color": "#ffffff",
        "muted_color": "#b9b9c6",
        "accent_mode": "artwork",
        "accent_color": "#ff3b30",
        "bubble_color": "#0b0b10",
        "bubble_opacity": 0.82,
        "bubble_radius": 18,
        "padding": 16,
        "gap": 16,
        "width": 560,
        "show_art": True,
        "art_source": "artwork",
        "video_when": "auto",
        "video_fit": "cover",
        "video_quality": "small",
        # Start buffering the next song's video a few seconds before the
        # current one ends, so it appears right at the track change.
        "video_prebuffer": False,
        # At each track change, pause the app and rewind to 0:00 until the
        # overlay says the video is buffered (or failed), then resume, so the
        # song and video start together. Capped at VIDEO_HOLD_MAX seconds.
        "video_hold": True,
        "art_size": 96,
        "art_radius": 12,
        "art_spin": False,
        "backdrop": True,
        "backdrop_blur": 42,
        "backdrop_opacity": 0.45,
        "glow": True,
        "show_album": False,
        "show_progress": True,
        "show_time": True,
        "show_next": False,
        "show_like": True,
        "show_source_label": False,
        "source_label": "Now playing",
        "marquee": True,
        "hide_when_paused": False,
        "hide_when_idle": True,
        "hide_delay": 6,
        "hide_during_ads": True,
        "align": "left",
        "valign": "bottom",
        "animation": "slide",
        "outline": False,
        "outline_color": "#000000",
    },
    "visualizer": {
        "mode": "simulated",
        "bars": 28,
        "style": "bars",
        "position": "behind",
        "height": 54,
        "width_pct": 100,
        "color_mode": "accent",
        "color": "#ff3b30",
        "opacity": 0.55,
        "fps": 18,
        "smoothing": 0.72,
        "sensitivity": 1.0,
        "cap": True,
        "peak_hold": 0.45,
        "peak_fall": 0.5,
        "device": "",
        "samplerate": 48000,
        "blocksize": 2048,
        "floor_db": -70,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict:
    raw = {}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg: dict) -> dict:
    merged = _deep_merge(DEFAULT_CONFIG, cfg)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


CONFIG = load_config()


def active_source() -> str:
    """Which player currently drives the shared overlay: "ytm" | "local"."""
    return str(CONFIG.get("source") or "ytm").lower()


def api_base() -> str:
    return f"http://{CONFIG['host']}:{CONFIG['port']}"


# --------------------------------------------------------------------------
# token storage
# --------------------------------------------------------------------------

class Secrets:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        if SECRETS_PATH.exists():
            try:
                self.data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def save(self) -> None:
        SECRETS_PATH.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        try:
            os.chmod(SECRETS_PATH, 0o600)
        except Exception:
            pass

    @property
    def token(self) -> str:
        return self.data.get("token", "")

    def set_token(self, token: str) -> None:
        self.data["token"] = token
        self.data["paired_at"] = time.time()
        self.save()

    def clear(self) -> None:
        self.data.pop("token", None)
        self.data.pop("paired_at", None)
        self.save()


SECRETS = Secrets()


# --------------------------------------------------------------------------
# runtime state
# --------------------------------------------------------------------------

class State:
    def __init__(self) -> None:
        self.connected = False
        self.source = "none"          # none | socket | poll
        self.app_version = ""
        self.last_error = ""
        self.pairing_code = ""
        self.pairing = False
        self.now: dict[str, Any] | None = None       # active source's now-playing
        self.ytm_now: dict[str, Any] | None = None   # last YTM payload (for re-seed on switch back)
        self.log: list[str] = []

    def note(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log.append(line)
        del self.log[:-60]
        print(f"[ytm] {msg}", flush=True)

    def snapshot(self) -> dict:
        return {
            "connected": self.connected,
            "source": self.source,
            "paired": bool(SECRETS.token),
            "app_version": self.app_version,
            "last_error": self.last_error,
            "pairing": self.pairing,
            "pairing_code": self.pairing_code,
            "now": self.now,
            "host": CONFIG["host"],
            "port": CONFIG["port"],
            "audio_available": HAS_AUDIO_CAPTURE,
            "audio_running": AUDIO.running,
            "audio_device": AUDIO.device_name,
            "audio_peak": round(AUDIO.peak, 4),
            "audio_error": AUDIO.error or (_AUDIO_IMPORT_ERROR if not HAS_AUDIO_CAPTURE else ""),
            "log": self.log[-25:],
        }


STATE = State()


# --------------------------------------------------------------------------
# broadcast hub
# --------------------------------------------------------------------------

class Hub:
    def __init__(self) -> None:
        self.overlay: set[WebSocket] = set()
        self.panel: set[WebSocket] = set()
        self.player_ws: WebSocket | None = None   # the one overlay that owns local audio playback

    async def _send(self, group: set[WebSocket], payload: dict) -> None:
        dead = []
        text = json.dumps(payload)
        for ws in list(group):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            group.discard(ws)

    async def to_overlay(self, payload: dict) -> None:
        await self._send(self.overlay, payload)

    async def to_panel(self, payload: dict) -> None:
        await self._send(self.panel, payload)

    async def broadcast_config(self) -> None:
        await self.to_overlay({"type": "config", "config": CONFIG})

    async def broadcast_status(self) -> None:
        await self.to_panel({"type": "status", "status": STATE.snapshot()})


HUB = Hub()
VIDEO_STATUS: dict = {}     # last video_status reported by the player overlay


# --------------------------------------------------------------------------
# state normalisation
# --------------------------------------------------------------------------

_SIZE_RE = re.compile(r"=w\d+-h\d+")


def _best_art(thumbnails: list[dict] | None) -> str:
    """Largest thumbnail, unmodified. Upscaling happens in the proxy, which can
    fall back if the bigger render 404s."""
    if not thumbnails:
        return ""
    best = max(thumbnails, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
    return best.get("url") or ""


def _upscaled(url: str) -> str:
    """YTM artwork URLs carry their dimensions inline, so a bigger render is
    free. Returns "" when the URL has no size to rewrite."""
    return _SIZE_RE.sub("=w600-h600", url) if _SIZE_RE.search(url) else ""


def _proxied(url: str) -> str:
    """Serve artwork through HexCast. Same-origin means the overlay can read it
    into a canvas for the accent colour without tainting it - Google does not
    send CORS headers, so a direct crossOrigin load fails outright."""
    return f"/ytm/art?u={urllib.parse.quote(url, safe='')}" if url else ""


def _selected_item(player: dict) -> dict | None:
    queue = player.get("queue") or {}
    items = list(queue.get("items") or []) + list(queue.get("automixItems") or [])
    idx = queue.get("selectedItemIndex")
    if idx is None or idx < 0 or idx >= len(items):
        return None
    return items[idx]


def _counterpart_id(item: dict | None) -> str:
    """YTM keeps song and video versions of a track paired as 'counterparts'.
    When the app is playing the audio version there is no embeddable video in
    `video.id`, but the counterpart points at the real one."""
    for c in (item or {}).get("counterparts") or []:
        vid = c.get("videoId")
        if vid:
            return vid
    return ""


def _queue_next(player: dict) -> dict | None:
    queue = player.get("queue") or {}
    items = list(queue.get("items") or []) + list(queue.get("automixItems") or [])
    idx = queue.get("selectedItemIndex")
    if idx is None or idx + 1 >= len(items):
        return None
    nxt = items[idx + 1]
    raw = _best_art(nxt.get("thumbnails"))
    return {
        "title": nxt.get("title", ""),
        "author": nxt.get("author", ""),
        "art": _proxied(raw),
        "art_url": raw,
        "id": nxt.get("videoId", ""),
        "video_type": nxt.get("videoType"),
        "counterpart_id": _counterpart_id(nxt),
    }


def normalise(state: dict) -> dict:
    player = state.get("player") or {}
    video = state.get("video") or None
    track_state = player.get("trackState", -1)

    selected = _selected_item(player)

    payload: dict[str, Any] = {
        "type": "state",
        "ts": time.time(),
        "counterpart_id": _counterpart_id(selected),
        "state": TRACK_STATES.get(track_state, "unknown"),
        "playing": track_state == 1,
        "ad": bool(player.get("adPlaying")),
        "progress": float(player.get("videoProgress") or 0),
        "volume": player.get("volume"),
        "repeat": (player.get("queue") or {}).get("repeatMode", -1),
        "next": _queue_next(player),
        "playlist_id": state.get("playlistId") or "",
    }

    if video:
        payload.update({
            "id": video.get("id", ""),
            "title": video.get("title", ""),
            "author": video.get("author", ""),
            "album": video.get("album") or "",
            "art": _proxied(_best_art(video.get("thumbnails"))),
            "art_url": _best_art(video.get("thumbnails")),
            "duration": float(video.get("durationSeconds") or 0),
            "like": video.get("likeStatus"),
            "is_live": bool(video.get("isLive")),
            "video_type": video.get("videoType"),
            # YTM fills metadata in two passes; the first can be incomplete.
            "meta_ready": video.get("metadataFilled", True),
        })
    else:
        payload.update({
            "id": "", "title": "", "author": "", "album": "", "art": "", "art_url": "",
            "duration": 0, "like": None, "is_live": False,
            "video_type": None, "meta_ready": True,
        })

    return payload


# --------------------------------------------------------------------------
# side effects on track change
# --------------------------------------------------------------------------

async def _fire_clip(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    base = CONFIG.get("hexcast_url", "http://127.0.0.1:4747").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            await c.get(f"{base}/api/play/{name}")
    except Exception as exc:
        STATE.note(f"clip '{name}' failed: {exc}")


async def _forward(payload: dict) -> None:
    url = (CONFIG.get("forward_url") or "").strip()
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            await c.post(url, json=payload)
    except Exception:
        pass


# --------------------------------------------------------------------------
# audio level capture (optional)
# --------------------------------------------------------------------------

class AudioLevels:
    """Capture whatever the speakers are playing via WASAPI loopback, reduce it
    to a handful of log-spaced bands, and push those to the overlay.

    A browser source cannot reach desktop audio, so genuine reactivity has to
    be measured here and sent over. Runs in a thread because the capture call
    blocks; nothing starts until an overlay is actually watching.
    """

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.error = ""
        self.device_name = ""
        self.peak = 0.0            # last block's level, for the panel meter
        self.RATE = 48000
        self.BLOCK = 2048

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def devices(self) -> list[dict]:
        """Both loopback taps on playback devices and genuine capture devices.

        Virtual mixers like Voicemeeter expose their buses as recording devices
        (Voicemeeter Out B1/B2/B3), which is a far more reliable tap than
        looping back a virtual playback endpoint."""
        if not HAS_AUDIO_CAPTURE:
            return []
        out: list[dict] = []
        try:
            for sp in _sc.all_speakers():
                out.append({"id": "loopback:" + str(sp.name),
                            "label": str(sp.name) + "  (loopback)"})
        except Exception:
            pass
        try:
            for m in _sc.all_microphones(include_loopback=False):
                out.append({"id": "input:" + str(m.name),
                            "label": str(m.name) + "  (input)"})
        except Exception:
            pass
        return out

    def start(self, loop: asyncio.AbstractEventLoop) -> bool:
        if not HAS_AUDIO_CAPTURE:
            self.error = ("numpy and soundcard are not installed - "
                          "pip install -r plugins/music/requirements-audio.txt")
            return False
        if self.running:
            return True
        self.stop_evt = threading.Event()
        self.loop = loop
        self.thread = threading.Thread(target=self._run, name="ytm-audio", daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        self.stop_evt.set()
        self.thread = None

    def _band_edges(self, bars: int, nbins: int) -> list[tuple[int, int]]:
        """Log-spaced from 40Hz to 16kHz - linear bands would put almost every
        bar in the treble, where there is nothing to look at."""
        lo, hi = 40.0, 16000.0
        nyq = self.RATE / 2
        edges = []
        for i in range(bars):
            f0 = lo * (hi / lo) ** (i / bars)
            f1 = lo * (hi / lo) ** ((i + 1) / bars)
            b0 = max(1, int(f0 / nyq * nbins))
            b1 = max(b0 + 1, int(f1 / nyq * nbins))
            edges.append((b0, min(b1, nbins)))
        return edges

    def _open(self):
        """Resolve the configured device. Accepts 'loopback:<name>',
        'input:<name>', a bare name (treated as loopback, for older configs),
        or blank for the default speakers."""
        want = (CONFIG.get("visualizer", {}).get("device") or "").strip()

        if want.startswith("input:"):
            name = want[6:]
            for m in _sc.all_microphones(include_loopback=False):
                if str(m.name) == name:
                    self.device_name = str(m.name) + " (input)"
                    return m
            raise RuntimeError(f"capture device '{name}' is gone")

        name = want[9:] if want.startswith("loopback:") else want
        speaker = None
        if name:
            for sp in _sc.all_speakers():
                if str(sp.name) == name:
                    speaker = sp
                    break
            if speaker is None:
                raise RuntimeError(f"playback device '{name}' is gone")
        else:
            speaker = _sc.default_speaker()
        self.device_name = str(speaker.name) + " (loopback)"
        return _sc.get_microphone(str(speaker.name), include_loopback=True)

    def _run(self) -> None:
        viz = CONFIG.get("visualizer", {})
        self.RATE = int(viz.get("samplerate", 48000))
        self.BLOCK = int(viz.get("blocksize", 2048))

        try:
            mic = self._open()
        except Exception as exc:
            self.error = f"could not open audio device: {exc}"
            STATE.note(self.error)
            return

        self.error = ""
        STATE.note(f"audio capture running on {self.device_name} "
                   f"at {self.RATE}Hz / {self.BLOCK} frames")
        window = _np.hanning(self.BLOCK)
        last_send = 0.0
        bars = int(viz.get("bars", 28))
        edges = self._band_edges(bars, self.BLOCK // 2 + 1)

        try:
            # channels=None takes the device's native layout, which avoids the
            # downmix soundcard would otherwise have to do every block.
            with mic.recorder(samplerate=self.RATE, channels=None,
                              blocksize=self.BLOCK) as rec:
                while not self.stop_evt.is_set():
                    data = rec.record(numframes=self.BLOCK)
                    now = time.monotonic()
                    v = CONFIG.get("visualizer", {})
                    fps = max(4.0, float(v.get("fps", 18)))
                    if now - last_send < 1.0 / fps:
                        continue
                    last_send = now

                    want_bars = int(v.get("bars", 28))
                    if want_bars != bars:
                        bars = want_bars
                        edges = self._band_edges(bars, self.BLOCK // 2 + 1)

                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    if len(mono) < self.BLOCK:
                        continue
                    self.peak = float(_np.abs(mono).max())

                    spec = _np.abs(_np.fft.rfft(mono[:self.BLOCK] * window))
                    floor = float(v.get("floor_db", -70))
                    sens = float(v.get("sensitivity", 1.0))

                    vals = []
                    for b0, b1 in edges:
                        mag = float(spec[b0:b1].mean()) if b1 > b0 else 0.0
                        db = 20.0 * _np.log10(mag + 1e-9)
                        lvl = (db - floor) / (0.0 - floor)
                        vals.append(round(min(1.0, max(0.0, lvl * sens)), 3))

                    if self.loop and not self.loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            HUB.to_overlay({"type": "levels", "v": vals}), self.loop)
        except Exception as exc:
            self.error = f"audio capture stopped: {exc}"
            STATE.note(self.error)
        finally:
            self.peak = 0.0


AUDIO = AudioLevels()


def _sync_audio() -> None:
    """Capture only while an overlay is connected and the mode asks for it. In
    local mode the overlay measures its own audio via Web Audio, so the
    server-side loopback stays off to avoid double-driving the visualiser."""
    want = ((CONFIG.get("visualizer", {}).get("mode") == "audio")
            and bool(HUB.overlay) and active_source() == "ytm")
    if want and not AUDIO.running:
        try:
            AUDIO.start(asyncio.get_running_loop())
        except RuntimeError:
            pass
    elif not want and AUDIO.running:
        AUDIO.stop()


# --------------------------------------------------------------------------
# companion server client
# --------------------------------------------------------------------------

async def fetch_metadata() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(f"{api_base()}/metadata")
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def auth_headers() -> dict:
    # The companion server wants the bare token, not a Bearer prefix.
    return {"Authorization": SECRETS.token}


async def request_pairing() -> str:
    """Run the two-step pairing handshake. Blocks until the user approves."""
    STATE.pairing = True
    STATE.pairing_code = ""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{api_base()}/api/v1/auth/requestcode",
                json={"appId": APP_ID, "appName": APP_NAME, "appVersion": APP_VERSION},
            )
        if r.status_code != 200:
            raise RuntimeError(
                f"requestcode returned {r.status_code}. Is 'Enable companion "
                f"authorization' switched on in YouTube Music Desktop?"
            )
        code = r.json()["code"]
        STATE.pairing_code = code
        STATE.note(f"pairing code {code} - approve it in YouTube Music Desktop")
        await HUB.broadcast_status()

        # This call parks until the user clicks Allow, or 30s elapses.
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(
                f"{api_base()}/api/v1/auth/request",
                json={"appId": APP_ID, "code": code},
            )
        if r.status_code != 200:
            raise RuntimeError(f"pairing was denied or timed out ({r.status_code})")

        token = r.json()["token"]
        SECRETS.set_token(token)
        STATE.note("paired successfully")
        return token
    finally:
        STATE.pairing = False
        STATE.pairing_code = ""
        await HUB.broadcast_status()


async def send_command(command: str, data: Any = None) -> tuple[bool, str]:
    if not SECRETS.token:
        return False, "not paired"
    body: dict[str, Any] = {"command": command}
    if data is not None:
        body["data"] = data
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{api_base()}/api/v1/command", json=body, headers=auth_headers())
    except Exception as exc:
        return False, str(exc)
    if r.status_code in (200, 204):
        return True, ""
    return False, f"{r.status_code} {r.text[:200]}"


# --------------------------------------------------------------------------
# Socket.IO feed
# --------------------------------------------------------------------------

class Feed:
    def __init__(self) -> None:
        self.sio: socketio.AsyncClient | None = None
        self.task: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self.started = False
        self.last_id = ""

    async def _handle_state(self, raw: dict) -> None:
        try:
            payload = normalise(raw)
        except Exception as exc:
            STATE.note(f"could not read state update: {exc}")
            return

        is_ytm = active_source() == "ytm"
        changed = payload.get("id") and payload["id"] != self.last_id
        if changed and payload.get("meta_ready", True):
            self.last_id = payload["id"]
            payload["track_changed"] = True
            STATE.note(f"now playing: {payload['author']} - {payload['title']}")
            # Track-change side effects belong to YTM; skip them while local plays.
            if is_ytm:
                asyncio.create_task(_fire_clip(CONFIG.get("track_change_clip", "")))
                asyncio.create_task(_forward({**payload, "event": "track_change"}))
                _maybe_hold_for_video(payload)

        # Always keep the last YTM payload so switching back to YTM is instant, but
        # only drive the shared overlay/now when YTM is the active source.
        STATE.ytm_now = payload
        if is_ytm:
            _prefetch_videos(payload)
            STATE.now = payload
            await HUB.to_overlay(payload)
            await HUB.to_panel({"type": "now", "now": payload})

    async def _run(self, stop: asyncio.Event) -> None:
        backoff = 2
        sock_fails = 0
        while not stop.is_set():
            if not SECRETS.token:
                STATE.connected = False
                STATE.source = "none"
                await HUB.broadcast_status()
                await asyncio.sleep(3)
                continue

            if await fetch_metadata() is None:
                STATE.connected = False
                STATE.source = "none"
                STATE.last_error = "YouTube Music Desktop is not reachable"
                await HUB.broadcast_status()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            backoff = 2

            if CONFIG.get("use_socketio", True) and sock_fails < 3:
                if await self._socket_session(stop):
                    sock_fails = 0
                else:
                    sock_fails += 1
                    if sock_fails >= 3:
                        STATE.note("state feed will not connect - falling back to polling")
                    await asyncio.sleep(2)
            else:
                # Poll for a while, then give the realtime feed another chance.
                await self._poll_session(stop, seconds=180)
                sock_fails = 0

    async def _socket_session(self, stop: asyncio.Event) -> bool:
        """Hold a Socket.IO session open. Returns True if it ever connected."""
        sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
        self.sio = sio
        connected = False

        @sio.on("state-update", namespace=NAMESPACE)
        async def _on_state(data):  # noqa: ANN001
            await self._handle_state(data)

        @sio.on("disconnect", namespace=NAMESPACE)
        async def _on_disconnect(*_args):
            STATE.connected = False
            STATE.source = "none"
            STATE.note("state feed disconnected")
            await HUB.broadcast_status()

        try:
            await sio.connect(
                api_base(),
                namespaces=[NAMESPACE],
                transports=["websocket"],
                auth={"token": SECRETS.token},
                wait_timeout=10,
            )
            connected = True
            STATE.connected = True
            STATE.source = "socket"
            STATE.last_error = ""
            STATE.note(f"live state feed connected to {api_base()}")
            await HUB.broadcast_status()
            await self._seed_state()
            await sio.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            msg = str(exc)
            STATE.connected = False
            STATE.source = "none"
            STATE.last_error = msg
            if "401" in msg or "Unauthorized" in msg:
                STATE.note("token rejected - pair again from the panel")
                SECRETS.clear()
            else:
                STATE.note(f"state feed error: {type(exc).__name__}: {msg}")
            await HUB.broadcast_status()
        finally:
            try:
                await sio.disconnect()
            except Exception:
                pass
            self.sio = None
        return connected

    async def _poll_session(self, stop: asyncio.Event, seconds: int = 180) -> None:
        """Fallback when the realtime feed is unavailable. The REST route is
        rate limited, so this stays gentle and is never the first choice."""
        interval = max(1.0, float(CONFIG.get("poll_interval", 2)))
        STATE.source = "poll"
        STATE.note(f"polling /state every {interval:g}s")
        await HUB.broadcast_status()
        deadline = time.time() + seconds

        while not stop.is_set() and time.time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.get(f"{api_base()}/api/v1/state", headers=auth_headers())
            except Exception as exc:
                STATE.connected = False
                STATE.source = "none"
                STATE.last_error = str(exc)
                await HUB.broadcast_status()
                return

            if r.status_code == 200:
                if not STATE.connected:
                    STATE.connected = True
                    STATE.last_error = ""
                    await HUB.broadcast_status()
                await self._handle_state(r.json())
            elif r.status_code in (401, 403):
                STATE.note("token rejected while polling - pair again")
                SECRETS.clear()
                return
            elif r.status_code == 429:
                STATE.note("rate limited, backing off")
                await asyncio.sleep(10)

            await asyncio.sleep(interval)

    async def _seed_state(self) -> None:
        """Pull /state once on connect so the overlay isn't blank until the
        next song change."""
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{api_base()}/api/v1/state", headers=auth_headers())
            if r.status_code == 200:
                await self._handle_state(r.json())
        except Exception:
            pass

    async def restart(self) -> None:
        async with self.lock:
            await self._stop()
            self.stop = asyncio.Event()
            self.task = asyncio.create_task(self._run(self.stop))
            self.started = True

    async def _stop(self) -> None:
        self.stop.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        STATE.connected = False

    async def ensure_started(self) -> None:
        if not self.started:
            await self.restart()


FEED = Feed()


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/ytm", tags=["ytmusic"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    await FEED.ensure_started()
    return HTMLResponse(_read_static("ytm_panel.html"))


@router.get("/overlay", response_class=HTMLResponse)
async def overlay():
    await FEED.ensure_started()
    return HTMLResponse(_read_static("ytm_overlay.html"))


_ART_HOSTS = re.compile(r"^https://([a-z0-9-]+\.)?(googleusercontent\.com|ytimg\.com|ggpht\.com)/")
_ART_CACHE: dict[str, tuple[bytes, str]] = {}


@router.get("/art")
async def art_proxy(u: str = ""):
    """Fetch album art server-side. Two reasons: it makes the image same-origin
    so the overlay can sample it for the accent colour, and it lets us try a
    larger render first and quietly fall back if that 404s."""
    if not u or not _ART_HOSTS.match(u):
        return Response(status_code=400)

    hit = _ART_CACHE.get(u)
    if hit:
        return Response(hit[0], media_type=hit[1],
                        headers={"Cache-Control": "public, max-age=86400"})

    candidates = [c for c in (_upscaled(u), u) if c]
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
        for candidate in candidates:
            try:
                r = await c.get(candidate)
            except Exception:
                continue
            if r.status_code == 200 and r.content:
                ctype = r.headers.get("content-type", "image/jpeg")
                if len(_ART_CACHE) > 60:
                    _ART_CACHE.clear()
                _ART_CACHE[u] = (r.content, ctype)
                return Response(r.content, media_type=ctype,
                                headers={"Cache-Control": "public, max-age=86400"})
    return Response(status_code=404)


# --------------------------------------------------------------------------
# music videos: yt-dlp finds the stream, Hexcast relays it
# --------------------------------------------------------------------------
# The overlay used to embed YouTube's iframe player, which drags in YouTube's
# own controls (the stuck centre pause button, auto captions) and refuses any
# video whose uploader disabled embedding. Instead, yt-dlp (the shared "ytdlp"
# plugin, also used by Clips) looks up the direct video-only stream and /ytm/video/<id> relays it
# to the overlay's plain <video>. Browsers can't load these streams directly
# (YouTube's servers don't allow it cross-site), so the bytes pass through
# Hexcast - streamed, never written to disk. Lookups are anonymous; the
# linked sign-in (if any) is only tried when YouTube refuses.

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_QUALITY_HEIGHT = {"small": 240, "medium": 360, "large": 480, "hd720": 720}
_VID_URLS: dict[tuple[str, int], tuple[str, float]] = {}   # (id, height) -> (url, when)
_VID_FAILS: dict[tuple[str, int], tuple[str, float]] = {}  # (id, height) -> (error, when)
_VID_TASKS: dict[tuple[str, int], asyncio.Task] = {}
_VID_URL_TTL = 3 * 3600       # stream URLs expire after ~6h; refresh well before
_VID_FAIL_TTL = 15 * 60


def _video_height() -> int:
    q = (CONFIG.get("overlay") or {}).get("video_quality", "small")
    return _QUALITY_HEIGHT.get(q, 240)


def _login_browser() -> str:
    return (CONFIG.get("youtube_login") or {}).get("browser", "")


async def _lookup_stream(vid: str, height: int) -> str:
    from hexcast_plugins.ytdlp import lib as clips
    fmt = (f"bestvideo[height<={height}][vcodec^=avc1][protocol=https]"
           f"/bestvideo[height<={height}][protocol=https]"
           f"/best[height<={height}][protocol=https]")
    out = await clips.ytdlp_with_login(
        "-f", fmt, "-g", f"https://www.youtube.com/watch?v={vid}",
        timeout=60, browser=_login_browser())
    lines = out.decode("utf-8", errors="replace").strip().splitlines()
    if not lines or not lines[0].startswith("http"):
        raise RuntimeError("no playable video stream")
    return lines[0]


async def video_stream_url(vid: str, fresh: bool = False) -> str:
    """Direct stream URL for a video, looked up once and shared by concurrent
    callers (the overlay and the next-song prefetch). Kept in memory only."""
    key = (vid, _video_height())
    now = time.monotonic()
    if not fresh:
        hit = _VID_URLS.get(key)
        if hit and now - hit[1] < _VID_URL_TTL:
            return hit[0]
        fail = _VID_FAILS.get(key)
        if fail and now - fail[1] < _VID_FAIL_TTL:
            raise RuntimeError(fail[0])
    task = _VID_TASKS.get(key)
    if task is None:
        task = asyncio.create_task(_lookup_stream(*key))
        _VID_TASKS[key] = task
    try:
        url = await asyncio.shield(task)
    except Exception as exc:
        _VID_FAILS[key] = (str(exc), time.monotonic())
        raise RuntimeError(str(exc)) from exc
    finally:
        if task.done():
            _VID_TASKS.pop(key, None)
    _VID_URLS[key] = (url, time.monotonic())
    _VID_FAILS.pop(key, None)
    if len(_VID_URLS) > 40:
        for k in sorted(_VID_URLS, key=lambda k: _VID_URLS[k][1])[:10]:
            _VID_URLS.pop(k, None)
    return url


def _video_choice(vid: str, video_type, counterpart: str) -> str:
    """Same rule as the overlay: which id (if any) to show for a track."""
    o = CONFIG.get("overlay") or {}
    if o.get("art_source") != "video" or not vid:
        return ""
    real = video_type in (1, 2)
    when = o.get("video_when", "auto")
    if when == "always":
        return vid
    if when == "video_tracks":
        return vid if real else ""
    return vid if real else (counterpart or "")


def _prefetch_videos(payload: dict) -> None:
    """Look up the current and next song's streams in the background, so the
    overlay (or the track change) doesn't wait on yt-dlp."""
    ids = [_video_choice(payload.get("id", ""), payload.get("video_type"),
                         payload.get("counterpart_id", ""))]
    nxt = payload.get("next") or {}
    ids.append(_video_choice(nxt.get("id", ""), nxt.get("video_type"),
                             nxt.get("counterpart_id", "")))
    height = _video_height()
    for vid in ids:
        if not vid or (vid, height) in _VID_URLS or (vid, height) in _VID_TASKS:
            continue

        async def go(v=vid):
            try:
                await video_stream_url(v)
            except Exception:
                pass                    # the overlay reports it if it asks
        asyncio.create_task(go())


# ---- holding the song for its video --------------------------------------

VIDEO_HOLD_MAX = 8.0
_HOLD: dict[str, Any] = {}      # {"track", "vid", "event"} while a hold is running


def _maybe_hold_for_video(payload: dict) -> None:
    """Called on a track change. Only for a genuine song start (not the first
    state after Hexcast boots mid-song), only when a video will be shown and
    an overlay is connected to say when it's ready."""
    o = CONFIG.get("overlay") or {}
    if not o.get("video_hold", True) or HUB.player_ws is None:
        return
    if not payload.get("playing") or float(payload.get("progress") or 0) > 3:
        return
    vid = _video_choice(payload.get("id", ""), payload.get("video_type"),
                        payload.get("counterpart_id", ""))
    if not vid:
        return
    if VIDEO_STATUS.get("id") == vid and VIDEO_STATUS.get("state") in ("ready", "playing"):
        return                                  # pre-buffered and already there
    ev = asyncio.Event()
    _HOLD.clear()
    _HOLD.update(track=payload.get("id", ""), vid=vid, event=ev)
    asyncio.create_task(_hold_for_video(payload.get("id", ""), vid, ev))


def _release_hold(status: dict) -> None:
    if _HOLD and status.get("id") == _HOLD.get("vid") \
            and status.get("state") in ("ready", "playing", "fallback"):
        _HOLD["event"].set()


async def _hold_for_video(track: str, vid: str, ev: asyncio.Event) -> None:
    started = time.monotonic()
    await send_command("pause")
    await send_command("seekTo", 0)      # the song ran briefly before we saw it
    try:
        await asyncio.wait_for(ev.wait(), VIDEO_HOLD_MAX)
        why = f"video ready after {time.monotonic() - started:.1f}s"
    except asyncio.TimeoutError:
        why = f"video not ready after {VIDEO_HOLD_MAX:.0f}s - playing anyway"
    if _HOLD.get("event") is ev:
        _HOLD.clear()
    # Only resume if it's still the same song (a skip during the hold moves on).
    if FEED.last_id == track:
        await send_command("play")
        STATE.note(f"held the song for its video: {why}")


@router.get("/api/video/{vid}")
async def api_video(vid: str):
    """The overlay asks this before pointing its <video> at /ytm/video/<id>,
    so a failure comes back as a readable reason for the panel."""
    if not _VIDEO_ID.fullmatch(vid):
        return JSONResponse({"ok": False, "error": "bad video id"}, status_code=400)
    try:
        await video_stream_url(vid)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


_RELAY_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")


@router.get("/video/{vid}")
async def video_relay(vid: str, request: Request):
    if not _VIDEO_ID.fullmatch(vid):
        return Response(status_code=400)
    headers = {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    client = httpx.AsyncClient(timeout=httpx.Timeout(20, read=60), follow_redirects=True)
    try:
        for attempt in (0, 1):
            url = await video_stream_url(vid, fresh=attempt == 1)
            upstream = await client.send(client.build_request("GET", url, headers=headers),
                                         stream=True)
            if upstream.status_code in (403, 410) and attempt == 0:
                await upstream.aclose()          # expired/rotated URL: look it up again
                continue
            break
    except Exception:
        await client.aclose()
        return Response(status_code=502)

    async def close():
        await upstream.aclose()
        await client.aclose()

    out = {k: v for k, v in upstream.headers.items() if k.lower() in _RELAY_HEADERS}
    out["Cache-Control"] = "no-store"
    return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code,
                             headers=out, background=BackgroundTask(close))


@router.post("/api/login/link")
async def api_login_link(request: Request):
    """Music page Link button. Tests the browser session and only saves the
    browser's name if its cookies were readable and include a YouTube sign-in.
    Stays linked across restarts until Unlink."""
    global CONFIG
    from hexcast_plugins.ytdlp import lib as clips
    try:
        body = await request.json()
    except Exception:
        body = {}
    browser = str(body.get("browser") or "").strip().lower()
    if browser not in clips.VALID_COOKIE_BROWSERS:
        return JSONResponse({"ok": False, "message": "browser must be firefox or chrome"},
                            status_code=400)
    result = await clips.test_login_link(browser)
    if result["ok"]:
        CONFIG = save_config(_deep_merge(CONFIG, {"youtube_login": {"browser": browser}}))
        _VID_FAILS.clear()                       # give refused videos another go
    STATE.note(result["message"])
    return {**result, "browser": _login_browser()}


@router.get("/api/login")
async def api_login_status():
    from hexcast_plugins.ytdlp import lib as clips
    return {"browser": _login_browser(), **clips.signin_readiness()}


@router.post("/api/login/unlink")
async def api_login_unlink():
    global CONFIG
    CONFIG = save_config(_deep_merge(CONFIG, {"youtube_login": {"browser": ""}}))
    STATE.note("YouTube sign-in unlinked - music video lookups are anonymous")
    return {"ok": True, "browser": ""}


@router.get("/api/status")
async def api_status():
    return STATE.snapshot()


@router.get("/api/config")
async def api_get_config():
    return CONFIG


@router.post("/api/config")
async def api_set_config(request: Request):
    global CONFIG
    from . import localmusic
    incoming = await request.json()
    if isinstance(incoming, dict):
        incoming.pop("youtube_login", None)      # only the Link/Unlink buttons change it
    old = (CONFIG["host"], CONFIG["port"])
    old_source = active_source()
    old_dir = (CONFIG.get("local") or {}).get("music_dir", "")
    CONFIG = save_config(_deep_merge(CONFIG, incoming))
    await HUB.broadcast_config()
    if AUDIO.running:
        AUDIO.stop()          # pick up device / mode changes on the next start
    _sync_audio()
    if (CONFIG["host"], CONFIG["port"]) != old:
        await FEED.restart()
    # Local-music side: re-index if the directory changed, and re-drive the
    # overlay when the source changes so it isn't left showing stale info.
    new_dir = (CONFIG.get("local") or {}).get("music_dir", "")
    if new_dir != old_dir:
        localmusic.reindex_from_config()
    new_source = active_source()
    if new_source == "local":
        await localmusic.activate()
    elif new_source == "ytm" and old_source != "ytm":
        if STATE.ytm_now:
            STATE.now = STATE.ytm_now
            await HUB.to_overlay(STATE.ytm_now)
            await HUB.to_panel({"type": "now", "now": STATE.ytm_now})
    return {"ok": True, "config": CONFIG}


@router.post("/api/pair")
async def api_pair():
    try:
        await request_pairing()
    except Exception as exc:
        STATE.note(f"pairing failed: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=400)
    await FEED.restart()
    return {"ok": True}


@router.post("/api/unpair")
async def api_unpair():
    SECRETS.clear()
    STATE.note("token cleared")
    await FEED.restart()
    return {"ok": True}


@router.post("/api/reconnect")
async def api_reconnect():
    await FEED.restart()
    return {"ok": True}


@router.post("/api/command")
async def api_command(request: Request):
    body = await request.json()
    cmd = body.get("command", "")
    if not cmd:
        return JSONResponse({"error": "command is required"}, status_code=400)
    # Route transport to whichever player is active. The panel's data-cmd buttons
    # (playPause/next/previous/…) work unchanged for both sources.
    if active_source() == "local":
        from . import localmusic
        ok, err = await localmusic.handle_command(cmd, body.get("data"))
    else:
        ok, err = await send_command(cmd, body.get("data"))
    if not ok:
        return JSONResponse({"error": err}, status_code=400)
    return {"ok": True}


@router.get("/api/audio-devices")
async def api_audio_devices():
    return {"available": HAS_AUDIO_CAPTURE, "devices": AUDIO.devices(),
            "current": AUDIO.device_name, "error": AUDIO.error,
            "configured": CONFIG.get("visualizer", {}).get("device", "")}


@router.get("/api/nowplaying", response_class=PlainTextResponse)
async def api_nowplaying():
    """Plain text, for chat bots. Wire a !song command straight to this."""
    now = STATE.now
    if not now or not now.get("title"):
        return PlainTextResponse("Nothing playing right now")
    if now.get("ad"):
        return PlainTextResponse("An ad is playing")
    line = f"{now['title']} - {now['author']}"
    if now.get("album"):
        line += f" ({now['album']})"
    if not now.get("playing"):
        line += " [paused]"
    return PlainTextResponse(line)


@router.get("/api/nowplaying.json")
async def api_nowplaying_json():
    return STATE.now or {}


@router.websocket("/ws/overlay")
async def ws_overlay(ws: WebSocket):
    global VIDEO_STATUS
    from . import localmusic
    await ws.accept()
    HUB.overlay.add(ws)
    await FEED.ensure_started()
    _sync_audio()
    # The first live overlay becomes the "player" (owns local audio + reports
    # position/levels); the rest are muted "viewers" that just animate. Prevents
    # double audio when more than one overlay is open.
    if HUB.player_ws is None or HUB.player_ws not in HUB.overlay:
        HUB.player_ws = ws
    role = "player" if HUB.player_ws is ws else "viewer"
    try:
        await ws.send_text(json.dumps({"type": "role", "role": role}))
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        if STATE.now:
            await ws.send_text(json.dumps(STATE.now))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mt = msg.get("type")
            if ws is not HUB.player_ws:
                continue                      # only the authority reports
            if mt == "levels":
                await HUB.to_overlay({"type": "levels", "v": msg.get("v") or []})
            elif mt in ("local_progress", "local_ended", "local_error", "local_ready"):
                await localmusic.handle_overlay_message(msg)
            elif mt == "video_status":
                # What the overlay's music-video slot is doing, and why it fell
                # back to artwork if it did - shown in the panel.
                VIDEO_STATUS = {k: str(msg.get(k) or "")[:200] for k in ("state", "id", "reason")}
                await HUB.to_panel({"type": "video_status", **VIDEO_STATUS})
                _release_hold(VIDEO_STATUS)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.overlay.discard(ws)
        if HUB.player_ws is ws:              # promote another overlay to player
            HUB.player_ws = next(iter(HUB.overlay), None)
            if HUB.player_ws is not None:
                try:
                    await HUB.player_ws.send_text(json.dumps({"type": "role", "role": "player"}))
                    if STATE.now:
                        await HUB.player_ws.send_text(json.dumps(STATE.now))
                except Exception:
                    pass
        _sync_audio()


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    HUB.panel.add(ws)
    await FEED.ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "status", "status": STATE.snapshot()}))
        if VIDEO_STATUS:
            await ws.send_text(json.dumps({"type": "video_status", **VIDEO_STATUS}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.panel.discard(ws)


# --------------------------------------------------------------------------
# attach
# --------------------------------------------------------------------------

async def start_ytm() -> None:
    """Call from inside hexcast's lifespan startup."""
    await FEED.ensure_started()


async def stop_ytm() -> None:
    """Call from inside hexcast's lifespan shutdown."""
    AUDIO.stop()
    await FEED._stop()


def attach_ytm(app, port: int = 4747) -> None:
    """Mount the YouTube Music routes onto an existing FastAPI app.

    hexcast.py builds its app with FastAPI(lifespan=...), so Starlette ignores
    add_event_handler("startup"). The feed therefore starts lazily on the first
    panel or overlay request. Call start_ytm()/stop_ytm() from that lifespan to
    connect at boot instead.
    """
    app.include_router(router)
    print(f"  Music panel:         http://localhost:{port}/ytm", flush=True)
    print(f"  Music overlay:       http://localhost:{port}/ytm/overlay", flush=True)
    # Local-file player shares this overlay/panel/hub. Late import avoids an
    # import cycle (localmusic imports ytmusic, which is fully loaded by now).
    try:
        from . import localmusic
        localmusic.attach_local(app, port)
    except Exception as exc:                    # pragma: no cover
        print(f"  [localmusic] not attached: {exc}", flush=True)
