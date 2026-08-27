"""
HexCast - Countdown overlay module
===================================

Drop this next to hexcast.py, then add two lines after the /media mount:

    from countdown import attach_countdown
    attach_countdown(app, PORT)

Adds:
    http://localhost:4747/countdown          -> control panel (style + timer)
    http://localhost:4747/countdown/overlay  -> OBS browser source

A single customisable countdown timer you position on a scaled 1920x1080 stage
exactly like the chat window. Two ways to run it:

  * Duration   - count down a fixed span (H:M:S) from when you press Start.
  * Target     - count down to a wall-clock time (e.g. 22:00). If that time has
                 already passed today it rolls to tomorrow.

The server holds the authoritative remaining time and streams it to the overlay,
which anchors to it and ticks locally - so clock skew between machines never
matters. Resolution is to the second; no finer.
"""

from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = Path(os.environ.get("HEXCAST_CONFIG_DIR", BASE_DIR / "config"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "countdown.json"


def _read_static(name: str) -> str:
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The Countdown module needs "
            f"countdown_panel.html and countdown_overlay.html in ./static/ next to hexcast.py."
        )
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    # --- timer ---
    "mode": "duration",            # "duration" | "target"
    "duration_seconds": 300,       # used in duration mode
    "target_time": "22:00:00",     # HH:MM[:SS], server-local, used in target mode
    "autostart": False,            # start ticking as soon as an overlay connects

    # --- text / format ---
    "label": "",                   # optional caption shown with the digits ("" = none)
    "label_position": "above",     # "above" | "below"
    "finished_text": "",           # shown at 0 ("" = keep showing 0:00)
    "hours_mode": "auto",          # "auto" (hide when 0) | "always" | "never"

    "font_family": "Inter",
    "font_size": 120,
    "font_weight": 800,
    "letter_spacing": 0,
    "text_color": "#ffffff",
    "label_size": 34,
    "label_color": "#b9b9c6",
    "outline": False,
    "outline_color": "#000000",
    "outline_width": 2,
    "shadow": True,

    # --- background box (same styles as chat) ---
    "bg_style": "none",            # none|solid|gradient|glass|image|slice|frame|glow
    "bubble_color": "#0b0b10",
    "bubble_opacity": 0.75,
    "bg_color2": "#1a1a2e",
    "bg_gradient_angle": 135,
    "bg_image_url": "",
    "bg_blur": 8,
    "bg_border_color": "#ff3b30",
    "bg_border_width": 2,
    "bg_slice": 32,
    "bg_slice_width": 24,
    "bg_slice_repeat": "stretch",
    "bg_pad": 24,
    "radius": 18,

    # --- placement (percentages on the 1920x1080 stage) ---
    # box_* = the digits/caption box; bg_box_* = the background panel box (its own
    # position + size, like chat/alerts). bg_full paints the background full-screen.
    "box_x": 38, "box_y": 40, "box_w": 24, "box_h": 20,
    "bg_full": False,
    "bg_box_x": 35, "bg_box_y": 36, "bg_box_w": 30, "bg_box_h": 28,
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


# --------------------------------------------------------------------------
# timer state (in-memory; resets on restart)
# --------------------------------------------------------------------------

TIMER: dict[str, Any] = {"running": False, "ends_at": None, "remaining": None}


def _target_epoch(hhmmss: str) -> float:
    """Epoch seconds for the next occurrence of a wall-clock time (server-local).
    If the time has already passed today, roll to tomorrow."""
    parts = str(hhmmss or "0:0:0").split(":")
    try:
        h = int(parts[0]); m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        h = m = s = 0
    now = datetime.datetime.now()
    target = now.replace(hour=h % 24, minute=m % 60, second=s % 60, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return target.timestamp()


def _initial_remaining() -> float:
    """The remaining time to show when the timer is idle (not started)."""
    if CONFIG.get("mode") == "target":
        return max(0.0, _target_epoch(CONFIG.get("target_time", "0:0:0")) - time.time())
    return float(CONFIG.get("duration_seconds", 0) or 0)


def timer_snapshot() -> dict:
    """Current remaining time + running flag for the overlay/panel."""
    if TIMER["running"] and TIMER["ends_at"] is not None:
        return {"running": True, "remaining": max(0.0, TIMER["ends_at"] - time.time())}
    rem = TIMER["remaining"]
    if rem is None:
        rem = _initial_remaining()
    return {"running": False, "remaining": float(rem)}


def _apply_action(action: str, body: dict) -> None:
    now = time.time()
    if action == "start":
        secs = body.get("seconds")
        if secs is not None:                       # ad-hoc duration override
            TIMER["ends_at"] = now + max(0.0, float(secs))
        elif CONFIG.get("mode") == "target":
            TIMER["ends_at"] = _target_epoch(CONFIG.get("target_time", "0:0:0"))
        else:
            TIMER["ends_at"] = now + float(CONFIG.get("duration_seconds", 0) or 0)
        TIMER["running"] = True
        TIMER["remaining"] = None
    elif action == "pause":
        if TIMER["running"] and TIMER["ends_at"] is not None:
            TIMER["remaining"] = max(0.0, TIMER["ends_at"] - now)
        TIMER["running"] = False
        TIMER["ends_at"] = None
    elif action == "resume":
        if not TIMER["running"] and TIMER["remaining"] is not None:
            TIMER["ends_at"] = now + float(TIMER["remaining"])
            TIMER["running"] = True
            TIMER["remaining"] = None
    elif action == "reset":
        TIMER["running"] = False
        TIMER["ends_at"] = None
        TIMER["remaining"] = _initial_remaining()


# --------------------------------------------------------------------------
# websocket hub
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

    async def broadcast_config(self) -> None:
        await self._send(self.overlay, {"type": "config", "config": CONFIG})
        await self._send(self.panel, {"type": "config", "config": CONFIG})

    async def broadcast_timer(self) -> None:
        snap = {"type": "timer", **timer_snapshot()}
        await self._send(self.overlay, snap)
        await self._send(self.panel, snap)


HUB = Hub()


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/countdown", tags=["countdown"])
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    return HTMLResponse(_read_static("countdown_panel.html"), headers=_NOCACHE)


@router.get("/overlay", response_class=HTMLResponse)
async def overlay():
    return HTMLResponse(_read_static("countdown_overlay.html"), headers=_NOCACHE)


@router.get("/api/status")
async def api_status():
    return {"connected": True, "overlays": len(HUB.overlay), "timer": timer_snapshot()}


@router.get("/api/config")
async def api_get_config():
    return CONFIG


@router.post("/api/config")
async def api_set_config(request: Request):
    global CONFIG
    incoming = await request.json()
    CONFIG = save_config(_deep_merge(CONFIG, incoming))
    await HUB.broadcast_config()
    # An idle timer's displayed remaining follows the config (duration/target),
    # so refresh it too - but never disturb a running countdown.
    if not TIMER["running"]:
        TIMER["remaining"] = None
        await HUB.broadcast_timer()
    return {"ok": True, "config": CONFIG}


@router.post("/api/timer")
async def api_timer(request: Request):
    body = await request.json()
    action = str(body.get("action", "")).lower()
    if action not in ("start", "pause", "resume", "reset"):
        return JSONResponse({"error": "unknown action"}, status_code=400)
    _apply_action(action, body)
    await HUB.broadcast_timer()
    return {"ok": True, "timer": timer_snapshot()}


@router.websocket("/ws/overlay")
async def ws_overlay(ws: WebSocket):
    await ws.accept()
    HUB.overlay.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        # Optionally kick off the countdown the moment the overlay appears.
        if CONFIG.get("autostart") and not TIMER["running"]:
            _apply_action("start", {})
        await ws.send_text(json.dumps({"type": "timer", **timer_snapshot()}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.overlay.discard(ws)


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    HUB.panel.add(ws)
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        await ws.send_text(json.dumps({"type": "timer", **timer_snapshot()}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.panel.discard(ws)


# --------------------------------------------------------------------------
# attach
# --------------------------------------------------------------------------

def attach_countdown(app, port: int = 4747) -> None:
    """Mount the Countdown routes onto an existing FastAPI app."""
    app.include_router(router)
    print(f"  Countdown panel:     http://localhost:{port}/countdown", flush=True)
    print(f"  Countdown source:    http://localhost:{port}/countdown/overlay", flush=True)
