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

import asyncio
import datetime
import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
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

    # --- media cues ---
    # Auto-fire soundboard clips against the master timer. Add as many as you like.
    # Each cue is anchored to a countdown threshold (seconds remaining):
    #   anchor "start" -> the clip STARTS when the countdown hits `offset`.
    #   anchor "end"   -> the clip ENDS when the countdown hits `offset`; the
    #                     server reads the clip's playable length and works
    #                     backwards so (e.g.) intro music finishes exactly at 0:00.
    # Each cue: {"enabled": bool, "kind": "audio"|"video", "name": str,
    #            "anchor": "start"|"end", "offset": int}  (offset = seconds remaining)
    "media_cues": [],

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


def _norm_cue(c: dict) -> dict:
    """Coerce one media cue into a clean, fully-populated dict."""
    kind = str(c.get("kind", "audio")).lower()
    if kind not in ("audio", "video"):
        kind = "audio"
    anchor = str(c.get("anchor", "end")).lower()
    if anchor not in ("start", "end"):
        anchor = "end"
    # `offset` is the canonical field; fall back to the legacy `end_offset`.
    raw_off = c.get("offset", c.get("end_offset", 0))
    try:
        off = max(0, int(float(raw_off or 0)))
    except (TypeError, ValueError):
        off = 0
    return {
        "enabled": bool(c.get("enabled", True)),
        "kind": kind,
        "name": str(c.get("name", "") or "").strip(),
        "anchor": anchor,
        "offset": off,
    }


def _migrate_cues(cfg: dict) -> dict:
    """Normalise `media_cues`, migrating the legacy single-cue flat keys if present."""
    cues = cfg.get("media_cues")
    if not isinstance(cues, list):
        cues = []
    if not cues and (cfg.get("media_name") or cfg.get("media_enabled")):
        cues = [{
            "enabled": bool(cfg.get("media_enabled", False)),
            "kind": cfg.get("media_kind", "audio"),
            "name": cfg.get("media_name", ""),
            "anchor": "end",
            "offset": cfg.get("media_end_offset", 0),
        }]
    cfg["media_cues"] = [_norm_cue(c) for c in cues if isinstance(c, dict)]
    for k in ("media_enabled", "media_kind", "media_name", "media_end_offset"):
        cfg.pop(k, None)
    return cfg


def load_config() -> dict:
    raw = {}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    return _migrate_cues(_deep_merge(DEFAULT_CONFIG, raw))


def save_config(cfg: dict) -> dict:
    merged = _migrate_cues(_deep_merge(DEFAULT_CONFIG, cfg))
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


CONFIG = load_config()


# --------------------------------------------------------------------------
# timer state (in-memory; resets on restart)
# --------------------------------------------------------------------------

TIMER: dict[str, Any] = {"running": False, "ends_at": None, "remaining": None}

# --------------------------------------------------------------------------
# media cue state (in-memory)
# --------------------------------------------------------------------------
# The cue talks to the soundboard's own /index and /api/play routes, so it stays
# decoupled from hexcast's internals and honours the clip's saved trim/volume/
# cooldown. It calls the FastAPI app DIRECTLY in-process (ASGITransport) rather
# than over 127.0.0.1 - a loopback socket can be hijacked by port forwards
# (e.g. VS Code Remote-SSH auto-forwarding 4747), which silently routes the
# trigger to a different hexcast instance.
_PORT: int = 4747
_APP = None                               # FastAPI app, set by attach_countdown()
MEDIA_TASKS: list[asyncio.Task] = []     # one pending "trigger at start_epoch" task per armed cue
MEDIA_FIRED: dict[int, bool] = {}        # cue index -> fired this run (so a cue fires once)


def _client() -> httpx.AsyncClient:
    if _APP is not None:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP),
                                 base_url="http://hexcast.internal", timeout=5)
    # Fallback if attach_countdown() never ran (shouldn't happen in practice).
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{_PORT}", timeout=5)


async def _clip_play_seconds(kind: str, name: str) -> float | None:
    """Playable length (seconds) of a soundboard clip, honouring its saved trim.

    Reads the soundboard's own /index so we never touch hexcast internals. The
    playable span is (end or natural duration) - start. Returns None if the clip
    can't be found or carries no duration (e.g. a static image)."""
    try:
        async with _client() as c:
            r = await c.get("/index")
            r.raise_for_status()
            idx = r.json()
    except Exception:
        return None
    nl = (name or "").strip().lower()
    if not nl:
        return None
    for item in idx.get(kind, []) or []:
        if str(item.get("name", "")).lower() == nl or str(item.get("file", "")).lower() == nl:
            start = float(item.get("start") or 0.0)
            end = item.get("end")
            span_end = float(end) if end is not None else item.get("duration")
            if span_end is None:
                return None
            return max(0.0, float(span_end) - start)
    return None


async def _fire_media_clip(kind: str, name: str) -> None:
    """Trigger one soundboard clip via its public /api/play endpoint."""
    name = (name or "").strip()
    if not name:
        return
    url = f"/api/play/{kind}/{urllib.parse.quote(name)}"
    try:
        async with _client() as c:
            r = await c.get(url)
            print(f"[countdown cue] /api/play {kind}/{name!r} -> {r.status_code} {r.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[countdown cue] /api/play failed: {exc}", flush=True)


async def _cue_runner(idx: int, kind: str, name: str, delay: float) -> None:
    """Sleep until the computed start moment, then fire this cue's clip once."""
    try:
        if delay > 0:
            await asyncio.sleep(delay)
        # The world may have changed while we slept: only fire if the timer is
        # still running and this cue hasn't already fired this run.
        if MEDIA_FIRED.get(idx) or not TIMER["running"]:
            print(f"[countdown cue] not firing #{idx}: fired={MEDIA_FIRED.get(idx)} "
                  f"running={TIMER['running']}", flush=True)
            return
        MEDIA_FIRED[idx] = True
        print(f"[countdown cue] FIRING #{idx} {kind}/{name!r}", flush=True)
        await _fire_media_clip(kind, name)
    except asyncio.CancelledError:
        pass


def _cancel_media_cues() -> None:
    global MEDIA_TASKS
    for t in MEDIA_TASKS:
        if not t.done():
            t.cancel()
    MEDIA_TASKS = []


async def _schedule_media_cues(reset_fired: bool = True) -> None:
    """Arm (or re-arm) every enabled media cue against the current running timer.

    A cue is anchored to a countdown threshold (`offset` = seconds remaining):
      * anchor "start" -> fire when the countdown reaches `offset`:
                          start_epoch = ends_at - offset.
      * anchor "end"   -> the clip should END at `offset`, so start it its own
                          length earlier: start_epoch = ends_at - offset - clip_len.
    If a start moment is already past, the runner fires immediately as a best
    effort. Cancels any previously pending cues first."""
    global MEDIA_TASKS, MEDIA_FIRED
    _cancel_media_cues()
    if reset_fired:
        MEDIA_FIRED = {}
    if not TIMER["running"] or TIMER["ends_at"] is None:
        print("[countdown cue] skip: timer not running", flush=True)
        return
    ends_at = float(TIMER["ends_at"])
    now = time.time()
    for idx, cue in enumerate(CONFIG.get("media_cues") or []):
        name = str(cue.get("name", "")).strip()
        if not cue.get("enabled") or not name:
            continue
        if MEDIA_FIRED.get(idx):   # already fired this run (e.g. on resume)
            continue
        kind = str(cue.get("kind", "audio"))
        offset = float(cue.get("offset", 0) or 0)
        anchor = str(cue.get("anchor", "end"))
        if anchor == "start":
            start_epoch = ends_at - offset
            detail = "start-at"
        else:
            clip_len = await _clip_play_seconds(kind, name)
            if clip_len is None:
                print(f"[countdown cue] skip #{idx}: no playable length for {kind}/{name!r} "
                      f"(clip missing from /index or no duration)", flush=True)
                continue
            start_epoch = ends_at - offset - clip_len
            detail = f"end-at (len={clip_len:.2f}s)"
        delay = start_epoch - now
        print(f"[countdown cue] armed #{idx}: {kind}/{name!r} {detail} "
              f"offset={offset:.0f}s -> fires in {delay:.2f}s", flush=True)
        MEDIA_TASKS.append(asyncio.create_task(_cue_runner(idx, kind, name, delay)))


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
    # Arm the media cue on a fresh start, re-arm (without re-firing) on resume,
    # and tear it down on pause/reset.
    if action == "start":
        await _schedule_media_cues(reset_fired=True)
    elif action == "resume":
        await _schedule_media_cues(reset_fired=False)
    else:  # pause | reset
        _cancel_media_cues()
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
            await _schedule_media_cues(reset_fired=True)
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
    global _PORT, _APP
    _PORT = port
    _APP = app
    app.include_router(router)
    print(f"  Countdown panel:     http://localhost:{port}/countdown", flush=True)
    print(f"  Countdown source:    http://localhost:{port}/countdown/overlay", flush=True)
