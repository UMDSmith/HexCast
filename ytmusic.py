"""
HexCast - YouTube Music Desktop module
======================================

Drop this next to hexcast.py, then add two lines after the /media mount:

    from ytmusic import attach_ytm
    attach_ytm(app, PORT)

Adds:
    http://localhost:4747/ytm          -> control panel (pair + settings)
    http://localhost:4747/ytm/overlay  -> now-playing overlay (OBS browser source)

Requires YouTube Music Desktop App 2.0.0+ with the Companion Server enabled
(Settings -> Integrations -> Companion Server), plus "Enable companion
authorization" switched on while pairing.

State arrives over the app's Socket.IO feed rather than polling, so the
progress bar is live and the REST rate limits never come into play.
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
    import numpy as _np
    import soundcard as _sc
    HAS_AUDIO_CAPTURE = True
    _AUDIO_IMPORT_ERROR = ""
except Exception as _exc:          # pragma: no cover - depends on the host
    _np = _sc = None
    HAS_AUDIO_CAPTURE = False
    _AUDIO_IMPORT_ERROR = str(_exc)
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

# --------------------------------------------------------------------------
# paths / constants
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = Path(os.environ.get("HEXCAST_CONFIG_DIR", BASE_DIR / "config"))
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
            f"needs ytm_panel.html and ytm_overlay.html in ./static/ next to hexcast.py."
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
        self.now: dict[str, Any] | None = None
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

    RATE = 44100
    BLOCK = 1024

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.error = ""
        self.device_name = ""

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def devices(self) -> list[str]:
        if not HAS_AUDIO_CAPTURE:
            return []
        try:
            return [str(s.name) for s in _sc.all_speakers()]
        except Exception:
            return []

    def start(self, loop: asyncio.AbstractEventLoop) -> bool:
        if not HAS_AUDIO_CAPTURE:
            self.error = ("numpy and soundcard are not installed - "
                          "pip install -r requirements-ytm-audio.txt")
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

    def _run(self) -> None:
        try:
            want = (CONFIG.get("visualizer", {}).get("device") or "").strip()
            speaker = None
            if want:
                for sp in _sc.all_speakers():
                    if str(sp.name) == want:
                        speaker = sp
                        break
            speaker = speaker or _sc.default_speaker()
            self.device_name = str(speaker.name)
            mic = _sc.get_microphone(str(speaker.name), include_loopback=True)
        except Exception as exc:
            self.error = f"could not open a loopback device: {exc}"
            STATE.note(self.error)
            return

        self.error = ""
        STATE.note(f"audio capture running on '{self.device_name}'")
        window = _np.hanning(self.BLOCK)
        last_send = 0.0
        bars = int(CONFIG.get("visualizer", {}).get("bars", 28))
        edges = self._band_edges(bars, self.BLOCK // 2 + 1)

        try:
            with mic.recorder(samplerate=self.RATE, channels=1, blocksize=self.BLOCK) as rec:
                while not self.stop_evt.is_set():
                    data = rec.record(numframes=self.BLOCK)
                    now = time.monotonic()
                    fps = max(4.0, float(CONFIG.get("visualizer", {}).get("fps", 18)))
                    if now - last_send < 1.0 / fps:
                        continue
                    last_send = now

                    want_bars = int(CONFIG.get("visualizer", {}).get("bars", 28))
                    if want_bars != bars:
                        bars = want_bars
                        edges = self._band_edges(bars, self.BLOCK // 2 + 1)

                    mono = data[:, 0] if data.ndim > 1 else data
                    if len(mono) < self.BLOCK:
                        continue
                    spec = _np.abs(_np.fft.rfft(mono[:self.BLOCK] * window))

                    vals = []
                    sens = float(CONFIG.get("visualizer", {}).get("sensitivity", 1.0))
                    for b0, b1 in edges:
                        mag = float(spec[b0:b1].mean()) if b1 > b0 else 0.0
                        # -70..0 dBFS mapped onto 0..1
                        db = 20.0 * _np.log10(mag + 1e-9)
                        v = (db + 70.0) / 70.0
                        vals.append(round(min(1.0, max(0.0, v * sens)), 3))

                    if self.loop and not self.loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            HUB.to_overlay({"type": "levels", "v": vals}), self.loop)
        except Exception as exc:
            self.error = f"audio capture stopped: {exc}"
            STATE.note(self.error)


AUDIO = AudioLevels()


def _sync_audio() -> None:
    """Capture only while an overlay is connected and the mode asks for it."""
    want = (CONFIG.get("visualizer", {}).get("mode") == "audio") and bool(HUB.overlay)
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

        changed = payload.get("id") and payload["id"] != self.last_id
        if changed and payload.get("meta_ready", True):
            self.last_id = payload["id"]
            payload["track_changed"] = True
            STATE.note(f"now playing: {payload['author']} - {payload['title']}")
            asyncio.create_task(_fire_clip(CONFIG.get("track_change_clip", "")))
            asyncio.create_task(_forward({**payload, "event": "track_change"}))

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


@router.get("/api/status")
async def api_status():
    return STATE.snapshot()


@router.get("/api/config")
async def api_get_config():
    return CONFIG


@router.post("/api/config")
async def api_set_config(request: Request):
    global CONFIG
    incoming = await request.json()
    old = (CONFIG["host"], CONFIG["port"])
    CONFIG = save_config(_deep_merge(CONFIG, incoming))
    await HUB.broadcast_config()
    if AUDIO.running:
        AUDIO.stop()          # pick up device / mode changes on the next start
    _sync_audio()
    if (CONFIG["host"], CONFIG["port"]) != old:
        await FEED.restart()
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
    ok, err = await send_command(cmd, body.get("data"))
    if not ok:
        return JSONResponse({"error": err}, status_code=400)
    return {"ok": True}


@router.get("/api/audio-devices")
async def api_audio_devices():
    return {"available": HAS_AUDIO_CAPTURE, "devices": AUDIO.devices(),
            "current": AUDIO.device_name, "error": AUDIO.error}


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
    await ws.accept()
    HUB.overlay.add(ws)
    await FEED.ensure_started()
    _sync_audio()
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        if STATE.now:
            await ws.send_text(json.dumps(STATE.now))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.overlay.discard(ws)
        _sync_audio()


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    HUB.panel.add(ws)
    await FEED.ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "status", "status": STATE.snapshot()}))
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
