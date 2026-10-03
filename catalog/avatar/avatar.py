"""
Hexcast - Avatars
=================

Live2D avatars that an AI vtuber (any bot) drives through Hexcast's API - a lighter VTube Studio
with no face tracking: the bot is the tracker. A Hexcast plugin (see plugin.py):

    http://localhost:4747/avatar           -> the Avatars tab (live preview, placement, settings)
    http://localhost:4747/avatar/overlay   -> OBS browser source (every avatar; ?avatar=name for one)
    http://localhost:4747/avatar/api       -> the API (GET it for the full list)

Each avatar has a name (`main`, `guest`, `cat` ...) and shows a model from the library; any number
of them can be on screen. The renderer (avatar_engine.js, the same in OBS and in the tab's preview)
keeps a model alive by itself - blinking, breathing, idle sway, eyes that wander, head motion while
talking - and the API steers it: any parameter, expressions, motions, emotions, gaze, gestures,
placement, speech with lipsync (vowels, like VTube Studio's advanced lipsync), items and light.

The server holds what has to survive an OBS reload (settings, plus the "sticky" live state: held
parameters, expressions, emotion, gaze, light, items) and relays everything else to the renderers.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from hexcast_core import paths
from hexcast_core.api import ApiError, check_same_origin
from hexcast_core.staticfiles import RevalidatingStaticFiles

from . import capture, library, runtime

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = paths.CONFIG_DIR / "avatar.json"
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
STATIC_URL = "/plugins/avatar/static"          # set by attach() from the plugin host

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# The inputs of the built-in "virtual tracker" - VTube Studio's own names, so a model's VTS
# parameter setup works as it is. A key of a `params` command that is one of these sets the input
# (before the model's mappings); any other key is a Live2D parameter id.
INPUTS = [
    "FaceAngleX", "FaceAngleY", "FaceAngleZ", "FacePositionX", "FacePositionY", "FacePositionZ",
    "EyeOpenLeft", "EyeOpenRight", "EyeLeftX", "EyeLeftY", "EyeRightX", "EyeRightY",
    "Brows", "BrowLeftY", "BrowRightY", "MouthSmile", "MouthOpen", "MouthX", "CheekPuff", "TongueOut",
    "VoiceA", "VoiceI", "VoiceU", "VoiceE", "VoiceO", "VoiceSilence", "VoiceVolume", "VoiceFrequency",
    "VoiceVolumePlusMouthOpen", "VoiceFrequencyPlusMouthSmile",
]

# Face controls: a simpler way to steer a face than inputs, relative to the model's own neutral
# pose (so they look right on any model). Emotions are named sets of them.
FACE = {"smile": (-1, 1), "brows": (-1, 1), "eyes": (0, 1.6), "mouth_open": (0, 1), "cheek": (0, 1),
        "mouth_x": (-1, 1), "tilt": (-1, 1), "wink_left": (0, 1), "wink_right": (0, 1)}

DEFAULT_EMOTIONS: dict[str, dict] = {
    "neutral": {"face": {}},
    "happy": {"face": {"smile": 1.0, "brows": 0.3, "eyes": 0.85}},
    "sad": {"face": {"smile": -0.7, "brows": -0.6, "eyes": 0.75}},
    "angry": {"face": {"smile": -0.45, "brows": -1.0, "eyes": 0.95}},
    "surprised": {"face": {"brows": 1.0, "eyes": 1.3, "mouth_open": 0.35}},
    "smug": {"face": {"smile": 0.6, "brows": 0.25, "eyes": 0.62, "tilt": 0.35}},
    "sleepy": {"face": {"eyes": 0.3, "brows": -0.3, "smile": 0.05}},
    "thinking": {"face": {"brows": 0.45, "smile": -0.1, "mouth_x": 0.5, "tilt": -0.3}},
    "embarrassed": {"face": {"smile": 0.35, "brows": -0.2, "eyes": 0.7, "cheek": 0.8}},
}
# expression names that suggest an emotion (filled in when an avatar is created)
EMOTION_WORDS = {
    "happy": ["happy", "smile", "joy", "glad", "laugh"], "sad": ["sad", "cry", "tear", "sorrow"],
    "angry": ["angry", "anger", "mad", "rage", "annoyed"], "surprised": ["surprise", "shock", "shocked", "gasp"],
    "smug": ["smug"], "sleepy": ["sleep", "tired"], "thinking": ["think", "hmm"],
    "embarrassed": ["blush", "embarrass", "shy"],
}
GESTURES = ["nod", "shake", "tilt", "bounce", "lean_left", "lean_right", "look_away", "double_nod"]

AVATAR_DEFAULTS: dict[str, Any] = {
    "name": "", "model": "", "visible": True, "locked": False,
    # placement on the 1920x1080 stage: the model's centre at x/y (% of the stage); scale 1 = the
    # model's full height fills the stage height
    "x": 50.0, "y": 55.0, "scale": 1.0, "rotation": 0.0, "flip": False,
    "idle": {"blink": True, "breath": True, "sway": 50, "gaze": 50, "speech_motion": 50,
             "physics": True, "fps": 60, "idle_motion": True},
    "mouth": {"lipsync": True, "source": "speech", "device": "", "gain": 1.0, "cutoff": 0.08,
              "vowels": True, "smoothing": 45, "delay_ms": 60, "open": 1.0, "calibration": None},
    "emotions": {},
    "items": [],
    "light": {"enabled": False, "preset": "none", "color": "#ffd9a8", "intensity": 0.5, "angle": -35,
              "ambient": "#ffffff", "ambient_amount": 0.0, "rim": "#7fb6ff", "rim_amount": 0.0, "speed": 1.0},
}

STAGE_DEFAULTS = {"fps": 60, "quality": "high", "show_fps": False}

DEFAULT_CONFIG: dict[str, Any] = {"avatars": [], "stage": STAGE_DEFAULTS}


# --------------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------------

def _num(v, lo: float, hi: float, d: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return d
    if f != f:                                   # NaN
        return d
    return max(lo, min(hi, f))


def _color(v, d: str) -> str:
    s = str(v or "").strip()
    return s if re.fullmatch(r"#[0-9a-fA-F]{6}", s) else d


def _deep_merge(base: dict, override: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class CommandError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _err(exc) -> JSONResponse:
    return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status)


# --------------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------------

def _norm_face(d: Any) -> dict:
    out = {}
    if isinstance(d, dict):
        for k, (lo, hi) in FACE.items():
            if k in d and d[k] is not None:
                out[k] = round(_num(d[k], lo, hi, 0), 3)
    return out


def _norm_emotions(d: Any) -> dict:
    out = {}
    if isinstance(d, dict):
        for name, e in list(d.items())[:60]:
            key = str(name or "").strip().lower()[:32]
            if not key or not isinstance(e, dict):
                continue
            out[key] = {"face": _norm_face(e.get("face")),
                        "expressions": [str(x)[:80] for x in (e.get("expressions") or []) if str(x).strip()][:8],
                        "motion": str(e.get("motion") or "")[:80]}
    return out


def _norm_item(it: Any) -> dict | None:
    if not isinstance(it, dict) or not it.get("item"):
        return None
    pin = it.get("pin")
    if isinstance(pin, dict) and pin.get("mesh"):
        tri = [int(_num(v, 0, 1e6, 0)) for v in (pin.get("tri") or [])][:3]
        span = 1e5 if str(pin["mesh"]).startswith("layer:") else 2           # layer pins: a point in pixels
        bary = [float(_num(v, -span, span, 0)) for v in (pin.get("bary") or [])][:3]
        pin = {"mesh": str(pin["mesh"])[:120], "tri": tri, "bary": bary,
               "angle0": float(_num(pin.get("angle0"), -10, 10, 0)), "follow_angle": bool(pin.get("follow_angle", True))}
        if len(tri) != 3 or len(bary) != 3:
            pin = None
    else:
        pin = None
    return {
        "id": str(it.get("id") or uuid.uuid4().hex[:8])[:16],
        "item": library.slug(str(it["item"]), "item"),
        "x": _num(it.get("x"), -50, 150, 50), "y": _num(it.get("y"), -50, 150, 30),
        "scale": _num(it.get("scale"), 0.02, 20, 1), "rotation": _num(it.get("rotation"), -360, 360, 0),
        "flip": bool(it.get("flip")), "opacity": _num(it.get("opacity"), 0, 1, 1),
        "layer": "back" if it.get("layer") == "back" else "front", "order": int(_num(it.get("order"), -30, 30, 0)),
        "fps": _num(it.get("fps"), 1, 60, 12), "visible": bool(it.get("visible", True)), "pin": pin,
    }


def norm_avatar(d: dict, existing: dict | None = None) -> dict:
    a = _deep_merge(AVATAR_DEFAULTS, existing or {})
    a = _deep_merge(a, {k: v for k, v in (d or {}).items() if k not in ("emotions", "items")})
    out = {
        "name": str(a.get("name") or ""), "model": library.slug(str(a.get("model") or ""), "") if a.get("model") else "",
        "visible": bool(a.get("visible", True)), "locked": bool(a.get("locked")),
        "x": _num(a.get("x"), -100, 200, 50), "y": _num(a.get("y"), -100, 200, 55),
        "scale": _num(a.get("scale"), 0.02, 20, 1), "rotation": _num(a.get("rotation"), -360, 360, 0),
        "flip": bool(a.get("flip")),
    }
    i = a.get("idle") or {}
    out["idle"] = {"blink": bool(i.get("blink", True)), "breath": bool(i.get("breath", True)),
                   "sway": _num(i.get("sway"), 0, 100, 50), "gaze": _num(i.get("gaze"), 0, 100, 50),
                   "speech_motion": _num(i.get("speech_motion"), 0, 100, 50),
                   "physics": bool(i.get("physics", True)), "fps": int(_num(i.get("fps"), 5, 120, 60)),
                   "idle_motion": bool(i.get("idle_motion", True))}
    m = a.get("mouth") or {}
    src = m.get("source")
    cal = m.get("calibration")
    out["mouth"] = {"lipsync": bool(m.get("lipsync", True)),
                    "source": src if src in ("speech", "device", "none") else "speech",
                    "device": str(m.get("device") or "")[:200],
                    "gain": _num(m.get("gain"), 0.05, 20, 1), "cutoff": _num(m.get("cutoff"), 0, 0.9, 0.08),
                    "vowels": bool(m.get("vowels", True)), "smoothing": _num(m.get("smoothing"), 0, 100, 45),
                    "delay_ms": int(_num(m.get("delay_ms"), 0, 400, 60)), "open": _num(m.get("open"), 0.1, 2, 1),
                    "calibration": cal if isinstance(cal, dict) and len(json.dumps(cal)) < 20000 else None}
    emo = d.get("emotions") if d and "emotions" in d else (existing or {}).get("emotions")
    out["emotions"] = _norm_emotions(emo if emo is not None else {})
    items = d.get("items") if d and "items" in d else (existing or {}).get("items")
    out["items"] = [x for x in (_norm_item(it) for it in (items or [])[:60]) if x]
    lt = a.get("light") or {}
    out["light"] = {"enabled": bool(lt.get("enabled")),
                    "preset": lt.get("preset") if lt.get("preset") in LIGHT_PRESETS else "none",
                    "color": _color(lt.get("color"), "#ffd9a8"), "intensity": _num(lt.get("intensity"), 0, 2, 0.5),
                    "angle": _num(lt.get("angle"), -180, 180, -35),
                    "ambient": _color(lt.get("ambient"), "#ffffff"), "ambient_amount": _num(lt.get("ambient_amount"), 0, 1, 0),
                    "rim": _color(lt.get("rim"), "#7fb6ff"), "rim_amount": _num(lt.get("rim_amount"), 0, 2, 0),
                    "speed": _num(lt.get("speed"), 0.05, 10, 1)}
    return out


LIGHT_PRESETS = ["none", "pulse", "flicker", "fire", "police", "rainbow", "strobe", "lightning", "neon"]


def norm_stage(d: dict) -> dict:
    s = _deep_merge(STAGE_DEFAULTS, d or {})
    return {"fps": int(_num(s.get("fps"), 10, 144, 60)),
            "quality": s.get("quality") if s.get("quality") in ("low", "medium", "high") else "high",
            "show_fps": bool(s.get("show_fps"))}


def load_config() -> dict:
    raw: dict = {}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
    avatars, seen = [], set()
    for a in raw.get("avatars") or []:
        if isinstance(a, dict) and NAME_RE.match(str(a.get("name") or "")) and a["name"] not in seen:
            seen.add(a["name"])
            avatars.append(norm_avatar({}, a))
    return {"avatars": avatars, "stage": norm_stage(raw.get("stage") or {})}


def save_config() -> None:
    paths.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


CONFIG: dict = load_config()


def avatar(name: str) -> dict:
    for a in CONFIG["avatars"]:
        if a["name"] == name:
            return a
    raise CommandError(f"no avatar named {name!r} (avatars: {', '.join(x['name'] for x in CONFIG['avatars']) or 'none yet'})", 404)


def _targets(name: str) -> list[str]:
    """`*` (or `all`) addresses every avatar."""
    if name in ("*", "all"):
        return [a["name"] for a in CONFIG["avatars"]]
    return [avatar(name)["name"]]


def _model_type(a: dict) -> str:
    if not a.get("model"):
        return ""
    try:
        return library.model_meta(a["model"]).get("type", "live2d")
    except library.LibraryError:
        return ""


def _expression_names(a: dict, names: list[str]) -> list[str]:
    """The model's own spelling of each expression name (any case); an unknown one is an error that
    lists what the model has. Without a model to check against, the names pass as they are."""
    if not a.get("model") or not names:
        return names
    try:
        have = [e["name"] for e in library.model_meta(a["model"]).get("expressions", [])]
    except library.LibraryError:
        return names
    by_lower = {h.lower(): h for h in have}
    out, missing = [], []
    for n in names:
        real = n if n in have else by_lower.get(n.lower())
        if real is None:
            missing.append(n)
        elif real not in out:
            out.append(real)
    if missing:
        what = "state" if _model_type(a) == "png" else "expression"
        raise CommandError(f"{a['name']}'s model has no {what} {', '.join(map(repr, missing))} "
                           f"(it has: {', '.join(have) or 'none'})")
    return out


def suggest_emotions(model_id: str) -> dict:
    """An emotion -> expression table guessed from the model's expression names."""
    try:
        exprs = [e["name"] for e in library.model_meta(model_id).get("expressions", [])]
    except library.LibraryError:
        exprs = []
    out = {}
    for emo, base in DEFAULT_EMOTIONS.items():
        hits = [x for x in exprs if any(w in x.lower() for w in EMOTION_WORDS.get(emo, []))]
        out[emo] = {"face": dict(base["face"]), "expressions": hits[:1], "motion": ""}
    return out


# --------------------------------------------------------------------------------------------
# live state: what a renderer that (re)connects must be told
# --------------------------------------------------------------------------------------------

# LIVE[name] = {"params": {key: cmd}, "expressions": {name: until}, "emotion": cmd | None,
#               "face": cmd | None, "look": cmd | None, "light": dict | None, "transform": dict | None}
LIVE: dict[str, dict] = {}


def _live(name: str) -> dict:
    return LIVE.setdefault(name, {"params": {}, "parts": {}, "expressions": {}, "emotion": None, "face": None,
                                  "look": None, "light": None, "transform": None, "visible": None})


def _expired(until: float | None, now: float) -> bool:
    return bool(until) and until <= now


def live_snapshot() -> dict:
    now = time.time()
    out = {}
    for name, st in LIVE.items():
        st["params"] = {k: c for k, c in st["params"].items() if not _expired(c.get("until"), now)}
        st["parts"] = {k: c for k, c in st.setdefault("parts", {}).items() if not _expired(c.get("until"), now)}
        st["expressions"] = {k: u for k, u in st["expressions"].items() if not _expired(u, now)}
        for key in ("emotion", "face", "look"):
            if st[key] and _expired(st[key].get("until"), now):
                st[key] = None
        out[name] = st
    return out


# --------------------------------------------------------------------------------------------
# speech: audio a bot hands over, played (and lip-synced) by the overlay
# --------------------------------------------------------------------------------------------

SPEECH: dict[str, dict] = {}
SPEECH_MAX_BYTES = 60 * 1024 * 1024        # one clip
SPEECH_KEEP_BYTES = 300 * 1024 * 1024      # all clips held at once
SPEECH_TTL = 20 * 60
AUDIO_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg",
               ".opus": "audio/ogg", ".webm": "audio/webm", ".m4a": "audio/mp4", ".aac": "audio/aac",
               ".flac": "audio/flac"}
WAITERS: dict[str, asyncio.Future] = {}


def _sniff(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "audio/webm"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    return "application/octet-stream"


def _prune_speech() -> None:
    now = time.time()
    for sid in [s for s, v in SPEECH.items() if now - v["at"] > SPEECH_TTL]:
        SPEECH.pop(sid, None)
    total = sum(len(v["data"]) for v in SPEECH.values())
    for sid in sorted(SPEECH, key=lambda s: SPEECH[s]["at"]):
        if total <= SPEECH_KEEP_BYTES:
            break
        total -= len(SPEECH[sid]["data"])
        SPEECH.pop(sid, None)


async def _speech_bytes(request: Request) -> tuple[bytes, str, dict]:
    """The audio of a speak request: a multipart upload (field `audio` or `file`), raw audio as the
    body, or JSON with `url` (fetched by Hexcast, so the overlay never meets CORS) or `audio_b64`."""
    ctype = (request.headers.get("content-type") or "").lower()
    opts: dict = dict(request.query_params)
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        up = form.get("audio") or form.get("file")
        for k, v in form.items():
            if isinstance(v, str):
                opts[k] = v
        if up is None or isinstance(up, str):
            raise CommandError("send the audio as a file field named 'audio'")
        data = await up.read()
        mime = AUDIO_TYPES.get(Path(up.filename or "").suffix.lower()) or _sniff(data)
        return data, mime, opts
    if ctype.startswith("application/json"):
        try:
            body = await request.json()
        except ValueError:
            raise CommandError("the body is not valid JSON")
        if not isinstance(body, dict):
            raise CommandError("send a JSON object")
        opts.update({k: v for k, v in body.items() if k not in ("audio_b64", "base64")})
        b64 = body.get("audio_b64") or body.get("base64")
        if b64:
            try:
                data = base64.b64decode(str(b64).split(",")[-1], validate=False)
            except ValueError:
                raise CommandError("audio_b64 is not base64")
            return data, _sniff(data), opts
        url = str(body.get("url") or "")
        if not re.match(r"^https?://", url):
            raise CommandError("send `url` (http/https), `audio_b64`, a multipart file or the raw audio")
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                async with c.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise CommandError(f"{url} answered {r.status_code}", 502)
                    chunks, size = [], 0
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > SPEECH_MAX_BYTES:
                            raise CommandError("that audio is bigger than 60 MB", 413)
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    mime = (r.headers.get("content-type") or "").split(";")[0].strip()
        except httpx.HTTPError as exc:
            raise CommandError(f"couldn't fetch {url}: {exc}", 502)
        if not mime.startswith("audio/"):
            mime = _sniff(data)
        return data, mime, opts
    data = await request.body()
    return data, _sniff(data), opts


# --------------------------------------------------------------------------------------------
# websocket hub
# --------------------------------------------------------------------------------------------

class Client:
    def __init__(self, ws: WebSocket, kind: str, role: str = "", only: set[str] | None = None, audio: bool = True):
        self.ws, self.kind, self.role, self.only, self.audio = ws, kind, role, only, audio
        self.stats: dict = {}
        self.id = uuid.uuid4().hex[:8]

    def wants(self, name: str) -> bool:
        return not self.only or name in self.only


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self.captures = capture.Captures()
        self.loop: asyncio.AbstractEventLoop | None = None

    def of(self, kind: str) -> list[Client]:
        return [c for c in self.clients if c.kind == kind]

    def obs(self) -> list[Client]:
        return [c for c in self.clients if c.kind == "render" and c.role == "obs"]

    async def _send(self, clients, payload: dict | bytes) -> None:
        text = payload if isinstance(payload, bytes) else json.dumps(payload)
        for c in list(clients):
            try:
                if isinstance(text, bytes):
                    await c.ws.send_bytes(text)
                else:
                    await c.ws.send_text(text)
            except Exception:
                self.clients.discard(c)

    async def renderers(self, payload: dict, avatar_name: str | None = None) -> None:
        await self._send([c for c in self.of("render") if avatar_name is None or c.wants(avatar_name)], payload)

    async def panels(self, payload: dict) -> None:
        await self._send(self.of("panel"), payload)

    async def controls(self, payload: dict) -> None:
        await self._send(self.of("control"), payload)

    async def everyone(self, payload: dict) -> None:
        await self._send(self.clients - set(self.of("control")), payload)

    async def event(self, ev: dict) -> None:
        ev = {"type": "event", "at": time.time(), **ev}
        await self.controls(ev)
        await self.panels(ev)

    def sync_captures(self) -> None:
        """Record exactly the devices an avatar that some renderer shows listens to."""
        if self.loop is None:
            return
        shown = {n for c in self.of("render") for n in [a["name"] for a in CONFIG["avatars"]] if c.wants(n)}
        wanted = {a["mouth"]["device"] for a in CONFIG["avatars"]
                  if a["name"] in shown and a["visible"] and a["mouth"]["lipsync"]
                  and a["mouth"]["source"] == "device" and a["mouth"]["device"]}
        self.captures.sync(wanted, self.loop, self._pcm)

    def _pcm(self, dev: str, pcm: bytes) -> None:
        key = dev.encode("utf-8")[:255]
        frame = bytes([len(key)]) + key + pcm
        users = {a["name"] for a in CONFIG["avatars"] if a["mouth"]["source"] == "device" and a["mouth"]["device"] == dev}
        targets = [c for c in self.of("render") if any(c.wants(n) for n in users)]
        if targets and self.loop:
            self.loop.create_task(self._send(targets, frame))


HUB = Hub()
MODEL_INFO: dict[str, dict] = {}          # what a renderer found inside each model (parameters ...)


def models_payload() -> dict:
    return {m["id"]: m for m in library.list_models()}


def snapshot() -> dict:
    return {"type": "snapshot", "config": CONFIG, "models": models_payload(), "items": library.list_items(),
            "live": live_snapshot(), "time": time.time(), "inputs": INPUTS, "gestures": GESTURES,
            "light_presets": LIGHT_PRESETS, "face": list(FACE), "build": build()}


async def broadcast_config() -> None:
    await HUB.everyone({"type": "config", "config": CONFIG})
    HUB.sync_captures()


async def broadcast_library() -> None:
    await HUB.everyone({"type": "library", "models": models_payload(), "items": library.list_items()})


# --------------------------------------------------------------------------------------------
# commands: the one place every API call (REST or the control websocket) goes through
# --------------------------------------------------------------------------------------------

def _until(cmd: dict) -> float | None:
    f = cmd.get("for")
    if f is None or f == "":
        return None
    return time.time() + _num(f, 0, 86400 * 7, 0)


def _clean_params(cmd: dict) -> dict:
    vals = cmd.get("values")
    if not isinstance(vals, dict):
        # also {"id": "ParamMouthOpenY", "value": 1}
        if cmd.get("id") is not None:
            vals = {str(cmd["id"]): cmd.get("value")}
        else:
            raise CommandError("send `values`: {\"ParamMouthOpenY\": 1, \"MouthSmile\": 0.8, ...}")
    clean = {}
    for k, v in list(vals.items())[:512]:
        k = str(k)[:120]
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise CommandError(f"{k}: {v!r} is not a number")
        if f != f or abs(f) > 1e6:
            raise CommandError(f"{k}: {v!r} is not a usable number")
        clean[k] = f
    mode = cmd.get("mode", "set")
    if mode not in ("set", "add"):
        raise CommandError("mode is 'set' or 'add'")
    layer = cmd.get("layer", "input")
    if layer not in ("input", "final"):
        raise CommandError("layer is 'input' (before expressions and physics) or 'final' (over everything)")
    return {"values": clean, "mode": mode, "layer": layer,
            "weight": _num(cmd.get("weight", 1), 0, 1, 1), "duration": _num(cmd.get("duration", 0), 0, 600, 0),
            "ease": str(cmd.get("ease") or "smooth")[:12], "fade": _num(cmd.get("fade", 0.3), 0, 600, 0.3),
            "until": _until(cmd)}


async def command(name: str, cmd: dict, source: str = "api") -> dict:
    """Run one command on an avatar (or every avatar for name `*`)."""
    if not isinstance(cmd, dict):
        raise CommandError("a command is a JSON object")
    kind = str(cmd.get("cmd") or "")
    results = []
    for target in _targets(name):
        results.append(await _command_one(target, kind, cmd, source))
    if not results:
        raise CommandError("there are no avatars yet", 404)
    return results[0] if len(results) == 1 else {"ok": True, "avatars": [r.get("avatar") for r in results]}


async def _command_one(name: str, kind: str, cmd: dict, source: str) -> dict:
    a = avatar(name)
    st = _live(name)
    out: dict = {"cmd": kind}
    now = time.time()

    if kind == "params":
        p = _clean_params(cmd)
        out.update(p)
        hold = cmd.get("hold", True)
        if hold is not False:
            for k in p["values"]:
                st["params"][k] = {**p, "values": {k: p["values"][k]}, "at": now}
    elif kind == "release":
        ids = cmd.get("ids")
        if ids is None and cmd.get("id"):
            ids = [cmd["id"]]
        ids = [str(i) for i in ids] if isinstance(ids, list) else None
        out.update({"ids": ids, "fade": _num(cmd.get("fade", 0.3), 0, 600, 0.3)})
        if ids is None:
            st["params"].clear()
        else:
            for i in ids:
                st["params"].pop(i, None)
    elif kind == "parts":
        # show / hide whole parts of the model (a second arm set, a prop, a hat ...): 0..1 opacity
        vals = cmd.get("values")
        if not isinstance(vals, dict) or not vals:
            raise CommandError("send `values`: {\"PartArmA\": 0, \"PartArmB\": 1} (part ids: GET /avatar/api/avatars/<name>/info)")
        clean = {str(k)[:120]: round(_num(v, 0, 1, 1), 4) for k, v in list(vals.items())[:256]}
        known = {p.get("id") for p in model_info(a["model"]).get("parts", [])} if a.get("model") else set()
        missing = [k for k in clean if known and k not in known]
        if missing:
            raise CommandError(f"{a['name']}'s model has no part {', '.join(map(repr, missing))}")
        until, fade = _until(cmd), _num(cmd.get("fade", 0.3), 0, 30, 0.3)
        out.update({"values": clean, "until": until, "fade": fade})
        for k, v in clean.items():
            st["parts"][k] = {"values": {k: v}, "until": until, "fade": fade}
    elif kind == "release_parts":
        ids = cmd.get("ids")
        ids = [str(i) for i in ids] if isinstance(ids, list) else None
        out.update({"ids": ids, "fade": _num(cmd.get("fade", 0.3), 0, 30, 0.3)})
        if ids is None:
            st["parts"].clear()
        else:
            for i in ids:
                st["parts"].pop(i, None)
    elif kind == "expression":
        # one expression ("name": "Smile") or several at once ("names": ["Smile", "Blush"]);
        # "only": true switches every other expression off in the same step
        raw = cmd.get("names", cmd.get("name"))
        names = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
        names = [str(n).strip() for n in names if str(n).strip()][:32]
        only = bool(cmd.get("only"))
        if not names and not only:
            raise CommandError("send the expression `name` or `names` (see GET /avatar/api/avatars/<name>/info)")
        names = _expression_names(a, names)
        state = cmd.get("state", "on")
        if state not in ("on", "off", "toggle"):
            raise CommandError("state is 'on', 'off' or 'toggle'")
        until = _until(cmd)
        states = {}
        if _model_type(a) == "png" and names:            # one state at a time: the last one switched on wins
            last_on = None
            for n in names:
                s = ("off" if n in st["expressions"] else "on") if state == "toggle" else state
                if s == "on":
                    last_on = n
            names = [last_on] if last_on else names
            state = "on" if last_on else "off"
            only = only or bool(last_on)
        for n in names:
            s = ("off" if n in st["expressions"] else "on") if state == "toggle" else state
            states[n] = s
            if s == "on":
                st["expressions"][n] = until or 0
            else:
                st["expressions"].pop(n, None)
        off = []
        if only:
            off = [n for n in st["expressions"] if n not in states or states[n] == "off"]
            for n in off:
                st["expressions"].pop(n, None)
                states.setdefault(n, "off")
        out.update({"names": list(states), "states": states, "until": until, "only": only,
                    "fade": _num(cmd.get("fade", 0.4), 0, 30, 0.4), "active": sorted(st["expressions"])})
        if len(names) == 1:                       # the single-expression answer stays as it was
            out.update({"name": names[0], "state": states[names[0]]})
    elif kind == "clear_expressions":
        st["expressions"].clear()
        out["fade"] = _num(cmd.get("fade", 0.4), 0, 30, 0.4)
    elif kind == "motion":
        if _model_type(a) == "png":
            raise CommandError(f"{name} is a PNGtuber - it has no motions; switch its state with `expression` "
                               f"or `emotion`, or use a `gesture`")
        mname, group = str(cmd.get("name") or ""), cmd.get("group")
        if not mname and group is None:
            raise CommandError("send the motion `name` (or `group` and `index`)")
        out.update({"name": mname, "group": None if group is None else str(group),
                    "index": None if cmd.get("index") is None else int(_num(cmd.get("index"), 0, 9999, 0)),
                    "loop": bool(cmd.get("loop")), "priority": "force" if cmd.get("priority") == "force" else "normal",
                    "id": uuid.uuid4().hex[:10]})
    elif kind == "stop_motion":
        pass
    elif kind == "emotion":
        ename = str(cmd.get("name") or "neutral").strip().lower()
        table = a["emotions"] or {}
        if ename not in table and ename not in DEFAULT_EMOTIONS:
            raise CommandError(f"no emotion {ename!r} (this avatar has: {', '.join(sorted(set(table) | set(DEFAULT_EMOTIONS)))})")
        e = table.get(ename) or {"face": DEFAULT_EMOTIONS[ename]["face"], "expressions": [], "motion": ""}
        prev = st["emotion"]
        out.update({"name": ename, "face": e.get("face") or {}, "expressions": e.get("expressions") or [],
                    "motion": e.get("motion") or "", "intensity": _num(cmd.get("intensity", 1), 0, 1.5, 1),
                    "fade": _num(cmd.get("fade", 0.5), 0, 30, 0.5), "until": _until(cmd),
                    "previous": (prev or {}).get("expressions") or []})
        st["emotion"] = None if ename == "neutral" else {k: out[k] for k in ("name", "face", "expressions", "intensity", "until")}
    elif kind == "face":
        face = _norm_face(cmd.get("face") if isinstance(cmd.get("face"), dict) else cmd)
        out.update({"face": face, "fade": _num(cmd.get("fade", 0.3), 0, 30, 0.3), "until": _until(cmd),
                    "release": bool(cmd.get("release"))})
        st["face"] = None if cmd.get("release") else {"face": face, "until": out["until"]}
    elif kind == "look":
        if cmd.get("release"):
            out["release"] = True
            st["look"] = None
        else:
            at = str(cmd.get("at") or "")
            spots = {"camera": (0, 0), "center": (0, 0), "left": (-1, 0), "right": (1, 0), "up": (0, 1),
                     "down": (0, -1), "up_left": (-0.8, 0.7), "up_right": (0.8, 0.7), "down_left": (-0.8, -0.7),
                     "down_right": (0.8, -0.7)}
            if at and at not in spots:
                raise CommandError(f"at is one of: {', '.join(spots)}")
            look = {"x": spots[at][0], "y": spots[at][1]} if at else {}
            if "stage_x" in cmd or "stage_y" in cmd:
                look = {"stage_x": _num(cmd.get("stage_x"), -100, 200, 50), "stage_y": _num(cmd.get("stage_y"), -100, 200, 50)}
            elif not at:
                look = {"x": _num(cmd.get("x", 0), -1.5, 1.5, 0), "y": _num(cmd.get("y", 0), -1.5, 1.5, 0)}
            out.update({**look, "head": _num(cmd.get("head", 1), 0, 2, 1), "until": _until(cmd),
                        "speed": _num(cmd.get("speed", 1), 0.05, 10, 1)})
            st["look"] = {**look, "head": out["head"], "until": out["until"], "speed": out["speed"]}
    elif kind == "gesture":
        g = str(cmd.get("name") or "")
        if g not in GESTURES:
            raise CommandError(f"gesture is one of: {', '.join(GESTURES)}")
        out.update({"name": g, "amount": _num(cmd.get("amount", 1), 0, 3, 1),
                    "duration": _num(cmd.get("duration", 0), 0, 30, 0)})
    elif kind == "transform":
        t = {}
        for k, lo, hi in (("x", -100, 200), ("y", -100, 200), ("scale", 0.02, 20), ("rotation", -360, 360)):
            if cmd.get(k) is not None:
                t[k] = _num(cmd[k], lo, hi, a[k])
            if cmd.get("by_" + k) is not None:
                base = (st["transform"] or {}).get(k, a[k])
                t[k] = _num(base + _num(cmd["by_" + k], -1e4, 1e4, 0), lo, hi, a[k])
        if cmd.get("flip") is not None:
            t["flip"] = bool(cmd["flip"])
        if cmd.get("reset"):
            t = {k: a[k] for k in ("x", "y", "scale", "rotation", "flip")}
        out.update({"transform": t, "duration": _num(cmd.get("duration", 0), 0, 600, 0),
                    "ease": str(cmd.get("ease") or "smooth")[:12], "save": bool(cmd.get("save"))})
        if cmd.get("save"):
            a.update(t)
            st["transform"] = None
            save_config()
            await broadcast_config()
        elif cmd.get("reset"):
            st["transform"] = None
        else:
            st["transform"] = {**(st["transform"] or {}), **t}
    elif kind == "visible":
        vis = bool(cmd.get("visible", True))
        out.update({"visible": vis, "fade": _num(cmd.get("fade", 0.4), 0, 30, 0.4)})
        if cmd.get("save"):
            a["visible"] = vis
            st["visible"] = None
            save_config()
            await broadcast_config()
        else:
            st["visible"] = vis
    elif kind == "speak":
        out.update({k: cmd[k] for k in ("id", "url", "mime", "volume", "text", "interrupt") if k in cmd})
    elif kind == "stop_speaking":
        pass
    elif kind == "light":
        lt = norm_avatar({"light": {**a["light"], **(st["light"] or {}), **{k: v for k, v in cmd.items() if k != "cmd"}}})["light"]
        if cmd.get("save"):
            a["light"] = lt
            st["light"] = None
            save_config()
            await broadcast_config()
        elif cmd.get("reset"):
            st["light"] = None
            lt = a["light"]
        else:
            st["light"] = lt
        out.update({"light": lt, "fade": _num(cmd.get("fade", 0.6), 0, 30, 0.6)})
    elif kind in ("item_add", "item_remove", "item_update", "items_clear"):
        out.update(await _items_command(a, kind, cmd))
    elif kind == "reload":
        await broadcast_library()                 # files added to the model's folder show up
    else:
        raise CommandError(f"unknown command {kind!r}")

    msg = {"type": "cmd", "avatar": name, "source": source, "at": now, **out}
    await HUB.renderers(msg, name)
    if source != "panel":
        await HUB.panels({"type": "activity", "avatar": name, "cmd": kind, "at": now,
                          "summary": _summary(kind, out)})
    return {"ok": True, "avatar": name, **out}


async def _items_command(a: dict, kind: str, cmd: dict) -> dict:
    items = a["items"]
    if kind == "items_clear":
        a["items"] = []
    elif kind == "item_add":
        meta = library.item_meta(library.slug(str(cmd.get("item") or ""), "item"))
        it = _norm_item({**cmd, "item": meta["id"], "id": cmd.get("id") or uuid.uuid4().hex[:8]})
        if any(x["id"] == it["id"] for x in items):
            raise CommandError(f"this avatar already has an item with id {it['id']!r}")
        items.append(it)
    else:
        iid = str(cmd.get("id") or "")
        idx = next((i for i, x in enumerate(items) if x["id"] == iid), None)
        if idx is None:
            raise CommandError(f"no item with id {iid!r} on {a['name']} (ids: {', '.join(x['id'] for x in items) or 'none'})", 404)
        if kind == "item_remove":
            items.pop(idx)
        else:
            items[idx] = _norm_item({**items[idx], **{k: v for k, v in cmd.items() if k not in ("cmd", "id", "item")},
                                     "id": iid, "item": items[idx]["item"]})
    a["items"] = [x for x in a["items"] if x]
    save_config()
    await HUB.everyone({"type": "config", "config": CONFIG})
    return {"items": a["items"], "fade": _num(cmd.get("fade", 0.3), 0, 30, 0.3)}


def _summary(kind: str, out: dict) -> str:
    if kind == "params":
        vals = out.get("values") or {}
        s = ", ".join(f"{k}={v:g}" for k, v in list(vals.items())[:4])
        return s + (f" (+{len(vals) - 4})" if len(vals) > 4 else "")
    if kind == "expression" and len(out.get("states") or {}) > 1:
        return ", ".join(f"{n} {s}" for n, s in out["states"].items())
    for k in ("name", "visible"):
        if k in out:
            return f"{out[k]}" + (f" {out['state']}" if out.get("state") else "")
    if kind == "speak":
        return (out.get("text") or "")[:80] or "audio"
    if kind == "transform":
        return ", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v}" for k, v in out["transform"].items())
    return ""


async def speak(name: str, data: bytes, mime: str, opts: dict) -> dict:
    a = avatar(name)
    if not data:
        raise CommandError("no audio")
    if len(data) > SPEECH_MAX_BYTES:
        raise CommandError("that audio is bigger than 60 MB", 413)
    _prune_speech()
    sid = uuid.uuid4().hex[:12]
    SPEECH[sid] = {"data": data, "mime": mime, "avatar": name, "at": time.time()}
    listeners = [c for c in HUB.obs() if c.wants(name) and c.audio]
    SPEECH[sid]["listeners"] = {c.id for c in listeners}
    wait = str(opts.get("wait", "")).lower() in ("1", "true", "yes")
    fut = None
    if wait and listeners:
        fut = asyncio.get_running_loop().create_future()
        WAITERS[sid] = fut
    cmd = {"cmd": "speak", "id": sid, "url": f"/avatar/speech/{sid}", "mime": mime,
           "volume": _num(opts.get("volume", 1), 0, 2, 1), "text": str(opts.get("text") or "")[:2000],
           "interrupt": str(opts.get("interrupt", "")).lower() in ("1", "true", "yes")}
    await _command_one(a["name"], "speak", cmd, "api")
    res = {"ok": True, "avatar": name, "id": sid, "bytes": len(data), "mime": mime, "overlays": len(listeners)}
    if not listeners:
        res["warning"] = "no OBS overlay is showing this avatar, so nobody heard it (the tab's preview only moves the mouth)"
    if fut is not None:
        try:
            ev = await asyncio.wait_for(fut, timeout=_num(opts.get("timeout", 600), 1, 3600, 600))
            res.update({"finished": ev.get("event") == "speech_end", "duration": ev.get("duration"),
                        "result": ev.get("event"), **({"error": ev["error"]} if ev.get("error") else {})})
        except asyncio.TimeoutError:
            res.update({"finished": False, "warning": "timed out waiting for the speech to end"})
        finally:
            WAITERS.pop(sid, None)
    return res


async def on_render_message(c: Client, msg: dict) -> None:
    t = msg.get("type")
    if t == "event":
        ev = {k: v for k, v in msg.items() if k != "type"}
        ev["role"] = c.role
        if c.role != "obs" and ev.get("event") in ("speech_start", "speech_end", "speech_error", "motion_end"):
            return                               # the preview mirrors; OBS is what happened on stream
        if ev.get("event") in ("speech_end", "speech_error"):
            fut = WAITERS.get(str(ev.get("id")))
            if fut and not fut.done():
                fut.set_result(ev)
        await HUB.event(ev)
    elif t == "model_info":
        mid = str(msg.get("model") or "")
        info = msg.get("info")
        if mid and isinstance(info, dict) and len(json.dumps(info)) < 4_000_000:
            MODEL_INFO[mid] = info
            try:
                (library.model_dir(mid) / "hexcast.info.json").write_text(json.dumps(info), encoding="utf-8")
            except (OSError, library.LibraryError):
                pass
    elif t == "stats":
        c.stats = {k: msg.get(k) for k in ("fps", "frame_ms", "avatars", "gpu", "w", "h")}
        c.stats["at"] = time.time()
        await HUB.panels({"type": "stats", "client": c.id, "role": c.role, **c.stats})
    elif t == "live_params":
        rid = str(msg.get("rid") or "")
        fut = WAITERS.get("live:" + rid)
        if fut and not fut.done():
            fut.set_result(msg.get("values") or {})


def model_info(mid: str) -> dict:
    if mid in MODEL_INFO:
        return MODEL_INFO[mid]
    try:
        p = library.model_dir(mid) / "hexcast.info.json"
        if p.is_file():
            MODEL_INFO[mid] = json.loads(p.read_text(encoding="utf-8"))
            return MODEL_INFO[mid]
    except (OSError, ValueError, library.LibraryError):
        pass
    return {}


def avatar_info(name: str) -> dict:
    a = avatar(name)
    meta: dict = {}
    if a["model"]:
        try:
            meta = library.model_meta(a["model"])
        except library.LibraryError:
            meta = {}
    info = model_info(a["model"]) if a["model"] else {}
    emotions = sorted(set(a["emotions"]) | set(DEFAULT_EMOTIONS))
    return {
        "avatar": name, "model": a["model"], "model_name": meta.get("name", ""),
        "loaded": bool(info), "parameters": info.get("parameters", []), "parts": info.get("parts", []),
        "art_meshes": info.get("drawables", []), "hit_areas": info.get("hit_areas", []),
        "expressions": [e["name"] for e in meta.get("expressions", [])],
        "motions": [{"name": m["name"], "group": m["group"], "index": m["index"]} for m in meta.get("motions", [])],
        "emotions": emotions, "gestures": GESTURES, "inputs": INPUTS, "face": {k: list(v) for k, v in FACE.items()},
        "mappings": (library.model_settings(a["model"]).get("mappings", []) if meta else []),
        "note": "" if info else "parameters, parts and art meshes appear once an overlay or the Avatars tab has drawn this model",
    }


# --------------------------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------------------------

router = APIRouter(prefix="/avatar", tags=["avatar"])


def build() -> str:
    """Which renderer files a page is running: an open overlay that sees a different build after
    an update reloads itself (OBS would otherwise keep the old renderer until refreshed by hand)."""
    try:
        return "-".join(str(int((STATIC_DIR / f).stat().st_mtime))
                        for f in ("avatar_lipsync.js", "avatar_engine.js", "avatar_overlay.html", "avatar_panel.html"))
    except OSError:
        return ""


def _page(name: str) -> str:
    html = (STATIC_DIR / name).read_text(encoding="utf-8")
    scripts = "\n".join(f'<script src="{u}"></script>' for u in runtime.script_urls("/avatar/runtime"))
    if not runtime.status()["installed"]:
        scripts = "<!-- the Live2D runtime is not installed yet: see the Avatars tab -->"
    own = "\n".join(f'<script src="{STATIC_URL}/{f}?v={int((STATIC_DIR / f).stat().st_mtime)}"></script>'
                    for f in ("avatar_lipsync.js", "avatar_engine.js"))
    own += f'\n<script>window.HEXCAST_AVATAR_BUILD = {json.dumps(build())};</script>'
    return html.replace("<!--HEXCAST-RUNTIME-->", scripts + "\n" + own).replace("{{STATIC}}", STATIC_URL)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel_page():
    return HTMLResponse(_page("avatar_panel.html"), headers=_NOCACHE)


@router.get("/overlay", response_class=HTMLResponse)
async def overlay_page():
    return HTMLResponse(_page("avatar_overlay.html"), headers=_NOCACHE)


@router.get("/runtime/{name}")
async def runtime_file(name: str):
    p = runtime.file_path(name)
    if not p:
        return JSONResponse({"error": "not installed"}, status_code=404)
    return FileResponse(p, media_type="text/javascript", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/speech/{sid}")
async def speech_file(sid: str):
    s = SPEECH.get(sid)
    if not s:
        return JSONResponse({"error": "gone"}, status_code=404)
    return Response(s["data"], media_type=s["mime"], headers=_NOCACHE)


def guarded(fn):
    """CommandError / LibraryError -> the JSON error every Hexcast API answers with."""
    import functools

    @functools.wraps(fn)
    async def call(*a, **kw):
        try:
            return await fn(*a, **kw)
        except (CommandError, library.LibraryError) as exc:
            return _err(exc)
        except ApiError as exc:
            return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status)
    return call


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise CommandError("the body is not valid JSON")
    if not isinstance(body, dict):
        raise CommandError("send a JSON object")
    return body


@router.get("/api")
async def api_index():
    return {"name": "Hexcast Avatars API", "docs": "/help/avatar", "inputs": INPUTS, "gestures": GESTURES,
            "face": {k: list(v) for k, v in FACE.items()}, "emotions": list(DEFAULT_EMOTIONS),
            "light_presets": LIGHT_PRESETS, "routes": API_ROUTES}


@router.get("/api/status")
async def api_status():
    st = runtime.status()
    obs = HUB.obs()
    return {"connected": bool(obs), "overlays": len(obs), "previews": len([c for c in HUB.of("render") if c.role != "obs"]),
            "controls": len(HUB.of("control")), "avatars": [a["name"] for a in CONFIG["avatars"]],
            "runtime": st["installed"], "stats": [c.stats for c in obs if c.stats],
            "capture": {"available": capture.AVAILABLE, "running": HUB.captures.status()}}


# ---- runtime ----

@router.get("/api/runtime")
async def api_runtime():
    return runtime.status()


@router.post("/api/runtime/install")
@guarded
async def api_runtime_install(request: Request):
    check_same_origin(request)
    body = await _json(request)
    if body.get("accept_license") is not True:
        raise CommandError("read Live2D's license and send {\"accept_license\": true}")
    runtime.accept()
    runtime.start_download(force=bool(body.get("force")))
    for _ in range(400):                          # up to ~60 s, telling open pages as files land
        await asyncio.sleep(0.15)
        if not runtime.JOB["running"]:
            break
    st = runtime.status()
    if st["installed"]:
        await HUB.everyone({"type": "runtime", "installed": True})
    return {"ok": st["installed"] and not st["job"]["error"], **st}


@router.post("/api/runtime/core")
@guarded
async def api_runtime_core(request: Request):
    check_same_origin(request)
    form = await request.form()
    up = form.get("file")
    if up is None or isinstance(up, str):
        raise CommandError("send live2dcubismcore.min.js as the file field 'file'")
    try:
        runtime.save_core(await up.read())
    except ValueError as exc:
        raise CommandError(str(exc))
    runtime.accept()
    return {"ok": True, **runtime.status()}


# ---- library: models ----

@router.get("/api/models")
async def api_models():
    return {"models": library.list_models()}


@router.get("/api/models/{mid}")
@guarded
async def api_model(mid: str):
    return {**library.model_meta(mid), "settings": library.model_settings(mid), "info": model_info(mid)}


@router.get("/api/models/{mid}/engine")
@guarded
async def api_model_engine(mid: str):
    return JSONResponse(library.engine_settings(mid), headers=_NOCACHE)


@router.post("/api/models/{mid}/settings")
@guarded
async def api_model_save(mid: str, request: Request):
    s = library.save_model_settings(mid, await _json(request))
    await broadcast_library()
    await HUB.renderers({"type": "reload_model", "model": mid})
    return {"ok": True, "settings": s}


@router.post("/api/models/{mid}/delete")
@guarded
async def api_model_delete(mid: str, request: Request):
    check_same_origin(request)
    users = [a["name"] for a in CONFIG["avatars"] if a["model"] == mid]
    library.delete_model(mid)
    MODEL_INFO.pop(mid, None)
    for a in CONFIG["avatars"]:
        if a["model"] == mid:
            a["model"] = ""
    if users:
        save_config()
        await broadcast_config()
    await broadcast_library()
    return {"ok": True, "unassigned": users}


async def _upload_to_disk(up) -> Path:
    fd, tmp = tempfile.mkstemp(prefix="hx-upload-", suffix=".zip")
    size = 0
    with os.fdopen(fd, "wb") as f:
        while True:
            chunk = await up.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > library.MAX_ZIP_BYTES:
                f.close()
                os.unlink(tmp)
                raise CommandError("that file is bigger than 2 GB", 413)
            f.write(chunk)
    return Path(tmp)


async def _import(request: Request, kind: str) -> str:
    """A zip (field `file`), a folder (fields `files` + `paths`), or a single picture for an item."""
    form = await request.form(max_files=library.MAX_FILES + 10, max_fields=library.MAX_FILES + 10)
    ups = [v for k, v in form.multi_items() if k == "files" and not isinstance(v, str)]
    rels = [v for k, v in form.multi_items() if k == "paths" and isinstance(v, str)]
    single = form.get("file")
    if ups:
        if len(rels) != len(ups):
            rels = [u.filename or f"file{i}" for i, u in enumerate(ups)]
        files, total = [], 0
        for rel, up in zip(rels, ups):
            data = await up.read()
            total += len(data)
            if total > library.MAX_ZIP_BYTES:
                raise CommandError("that folder is bigger than 2 GB", 413)
            files.append((rel, data))
        return await asyncio.to_thread(library.import_files, files, kind)
    if single is None or isinstance(single, str):
        raise CommandError("send a zip as the field 'file' (or a folder as 'files' + 'paths')")
    fname = single.filename or "upload"
    if kind == "models" and fname.lower().endswith(".save"):          # a PNGTuber Plus avatar on its own
        data = await single.read()
        return await asyncio.to_thread(library.import_files, [(f"{Path(fname).stem}/{Path(fname).name}", data)], kind)
    if kind == "items" and Path(fname).suffix.lower() in library.IMAGE_EXTS:
        data = await single.read()
        return library.save_item_upload(fname, data)
    tmp = await _upload_to_disk(single)
    try:
        return await asyncio.to_thread(library.import_zip, tmp, fname, kind)
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/api/models/upload")
@guarded
async def api_models_upload(request: Request):
    check_same_origin(request)
    mid = await _import(request, "models")
    await broadcast_library()
    return {"ok": True, "model": library.model_meta(mid)}


@router.post("/api/models/import")
@guarded
async def api_models_import(request: Request):
    check_same_origin(request)
    body = await _json(request)
    mid = await asyncio.to_thread(library.import_path, str(body.get("path") or ""), "models")
    await broadcast_library()
    return {"ok": True, "model": library.model_meta(mid)}


@router.post("/api/models/{mid}/pictures")
@guarded
async def api_model_pictures(mid: str, request: Request):
    """Pictures for a PNGtuber's rig editor (multipart, field `files`)."""
    check_same_origin(request)
    form = await request.form(max_files=210, max_fields=420)
    files = []
    for k, v in form.multi_items():
        if k in ("files", "file") and not isinstance(v, str):
            files.append((v.filename or "picture.png", await v.read()))
    if not files:
        raise CommandError("send the pictures as `files`")
    names = await asyncio.to_thread(library.add_model_pictures, mid, files)
    await broadcast_library()
    return {"ok": True, "added": names, "model": library.model_meta(mid)}


@router.get("/api/vts")
async def api_vts():
    return await asyncio.to_thread(library.vts_listing)


# ---- library: items ----

@router.get("/api/items")
async def api_items():
    return {"items": library.list_items()}


@router.post("/api/items/upload")
@guarded
async def api_items_upload(request: Request):
    check_same_origin(request)
    iid = await _import(request, "items")
    await broadcast_library()
    return {"ok": True, "item": library.item_meta(iid)}


@router.post("/api/items/import")
@guarded
async def api_items_import(request: Request):
    check_same_origin(request)
    body = await _json(request)
    paths_ = body.get("paths") or ([body["path"]] if body.get("path") else [])
    done, errors = [], []
    for p in paths_[:200]:
        try:
            done.append(await asyncio.to_thread(library.import_path, str(p), "items"))
        except library.LibraryError as exc:
            errors.append({"path": p, "error": exc.message})
    await broadcast_library()
    return {"ok": not errors, "items": [library.item_meta(i) for i in done], "errors": errors}


@router.post("/api/items/{iid}/delete")
@guarded
async def api_item_delete(iid: str, request: Request):
    check_same_origin(request)
    library.delete_item(iid)
    changed = False
    for a in CONFIG["avatars"]:
        keep = [x for x in a["items"] if x["item"] != iid]
        if len(keep) != len(a["items"]):
            a["items"], changed = keep, True
    if changed:
        save_config()
        await broadcast_config()
    await broadcast_library()
    return {"ok": True}


@router.get("/api/devices")
async def api_devices():
    return {"available": capture.AVAILABLE, "error": capture.IMPORT_ERROR,
            "devices": await asyncio.to_thread(capture.devices)}


# ---- avatars ----

@router.get("/api/avatars")
async def api_avatars():
    return {"avatars": CONFIG["avatars"], "live": live_snapshot()}


@router.post("/api/avatars")
@guarded
async def api_avatar_create(request: Request):
    body = await _json(request)
    name = str(body.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise CommandError("a name is 1-32 lowercase letters, digits, _ or - (starting with a letter or digit)")
    if name in ("all",) or any(a["name"] == name for a in CONFIG["avatars"]):
        raise CommandError(f"there is already an avatar called {name!r}" if name != "all" else "'all' is reserved", 409)
    mid = str(body.get("model") or "")
    if mid:
        library.model_dir(mid)
    a = norm_avatar({**body, "name": name, "model": mid})
    if mid and not body.get("emotions"):
        a["emotions"] = suggest_emotions(mid)
    if "x" not in body and CONFIG["avatars"]:      # spread new avatars out
        a["x"] = [50, 25, 75, 15, 85][len(CONFIG["avatars"]) % 5]
    CONFIG["avatars"].append(a)
    save_config()
    await broadcast_config()
    return {"ok": True, "avatar": a}


@router.get("/api/avatars/{name}")
@guarded
async def api_avatar_get(name: str):
    return {"avatar": avatar(name), "live": live_snapshot().get(name)}


@router.post("/api/avatars/{name}")
@guarded
async def api_avatar_update(name: str, request: Request):
    body = await _json(request)
    a = avatar(name)
    if "model" in body and body["model"]:
        library.model_dir(str(body["model"]))
    new = norm_avatar({k: v for k, v in body.items() if k != "name"}, a)
    new["name"] = name
    if body.get("model") and body["model"] != a["model"] and "emotions" not in body:
        new["emotions"] = suggest_emotions(new["model"])
    a.clear()
    a.update(new)
    if any(k in body for k in ("x", "y", "scale", "rotation", "flip")):
        _live(name)["transform"] = None
    if "visible" in body:
        _live(name)["visible"] = None
    if "light" in body:
        _live(name)["light"] = None
    save_config()
    await broadcast_config()
    return {"ok": True, "avatar": a}


@router.post("/api/avatars/{name}/rename")
@guarded
async def api_avatar_rename(name: str, request: Request):
    body = await _json(request)
    new = str(body.get("to") or "").strip().lower()
    if not NAME_RE.match(new):
        raise CommandError("a name is 1-32 lowercase letters, digits, _ or -")
    if any(a["name"] == new for a in CONFIG["avatars"]) or new == "all":
        raise CommandError(f"{new!r} is taken", 409)
    a = avatar(name)
    a["name"] = new
    if name in LIVE:
        LIVE[new] = LIVE.pop(name)
    save_config()
    await broadcast_config()
    return {"ok": True, "avatar": a}


@router.post("/api/avatars/{name}/delete")
@guarded
async def api_avatar_delete(name: str):
    a = avatar(name)
    CONFIG["avatars"].remove(a)
    LIVE.pop(name, None)
    save_config()
    await broadcast_config()
    return {"ok": True}


@router.post("/api/order")
@guarded
async def api_order(request: Request):
    body = await _json(request)
    names = [str(n) for n in body.get("names") or []]
    by = {a["name"]: a for a in CONFIG["avatars"]}
    order = [by[n] for n in names if n in by] + [a for a in CONFIG["avatars"] if a["name"] not in names]
    CONFIG["avatars"] = order
    save_config()
    await broadcast_config()
    return {"ok": True, "order": [a["name"] for a in order]}


@router.post("/api/stage")
@guarded
async def api_stage(request: Request):
    CONFIG["stage"] = norm_stage({**CONFIG["stage"], **await _json(request)})
    save_config()
    await broadcast_config()
    return {"ok": True, "stage": CONFIG["stage"]}


@router.get("/api/avatars/{name}/info")
@guarded
async def api_avatar_info(name: str):
    return avatar_info(name)


@router.get("/api/avatars/{name}/params/live")
@guarded
async def api_avatar_live_params(name: str):
    """The model's parameter values right now, asked from the OBS overlay (else the preview)."""
    avatar(name)
    renderers = [c for c in HUB.obs() if c.wants(name)] or [c for c in HUB.of("render") if c.wants(name)]
    if not renderers:
        raise CommandError("no overlay or preview is drawing this avatar", 409)
    rid = uuid.uuid4().hex[:10]
    fut = asyncio.get_running_loop().create_future()
    WAITERS["live:" + rid] = fut
    try:
        await HUB._send(renderers[:1], {"type": "get_params", "avatar": name, "rid": rid})
        values = await asyncio.wait_for(fut, timeout=2)
    except asyncio.TimeoutError:
        raise CommandError("the overlay didn't answer", 504)
    finally:
        WAITERS.pop("live:" + rid, None)
    return {"avatar": name, "values": values}


# every command, also as its own route: POST /avatar/api/avatars/<name>/<command>
COMMANDS = ["params", "release", "parts", "release_parts", "expression", "clear_expressions", "motion", "stop_motion", "emotion", "face",
            "look", "gesture", "transform", "visible", "stop_speaking", "light", "item_add", "item_remove",
            "item_update", "items_clear", "reload"]


def _source(request: Request) -> str:
    """The Avatars tab marks its own calls, so they don't fill its activity log."""
    return "panel" if request.headers.get("x-hexcast-from") == "panel" else "api"


@router.post("/api/avatars/{name}/command")
@guarded
async def api_command(name: str, request: Request):
    return await command(name, await _json(request), _source(request))


@router.post("/api/avatars/{name}/speak")
@guarded
async def api_speak(name: str, request: Request):
    data, mime, opts = await _speech_bytes(request)
    if name in ("*", "all"):
        raise CommandError("speak to one avatar at a time")
    return await speak(name, data, mime, opts)


def _make_command_route(cmd_name: str):
    async def handler(name: str, request: Request):
        body = {}
        if (request.headers.get("content-length") or "0") != "0" or request.headers.get("transfer-encoding"):
            body = await _json(request)
        return await command(name, {**body, "cmd": cmd_name}, _source(request))
    handler.__name__ = f"api_{cmd_name}"
    return guarded(handler)


def _make_visibility_route(show: bool):
    async def handler(name: str, request: Request):
        body = {}
        if (request.headers.get("content-length") or "0") != "0" or request.headers.get("transfer-encoding"):
            body = await _json(request)
        return await command(name, {**body, "cmd": "visible", "visible": show}, _source(request))
    handler.__name__ = "api_show" if show else "api_hide"
    return guarded(handler)


for _c in COMMANDS:
    router.add_api_route(f"/api/avatars/{{name}}/{_c}", _make_command_route(_c), methods=["POST"])
router.add_api_route("/api/avatars/{name}/show", _make_visibility_route(True), methods=["POST"])
router.add_api_route("/api/avatars/{name}/hide", _make_visibility_route(False), methods=["POST"])


API_ROUTES = [
    "GET  /avatar/api/status",
    "GET  /avatar/api/avatars                      every avatar's settings + live state",
    "POST /avatar/api/avatars                      {name, model} - create",
    "GET  /avatar/api/avatars/<name>/info          the model's parameters, expressions, motions, art meshes",
    "GET  /avatar/api/avatars/<name>/params/live   parameter values right now",
    "POST /avatar/api/avatars/<name>/params        {values:{ParamMouthOpenY:1, MouthSmile:0.8}, weight, duration, for, layer}",
    "POST /avatar/api/avatars/<name>/release       {ids:[...]} or {} for all",
    "POST /avatar/api/avatars/<name>/parts         {values:{PartArmA:0, PartArmB:1}, fade, for} - show / hide parts",
    "POST /avatar/api/avatars/<name>/release_parts {ids:[...]} or {} for all",
    "POST /avatar/api/avatars/<name>/expression    {name} or {names:[...]}, state:on|off|toggle, only, for",
    "POST /avatar/api/avatars/<name>/motion        {name} or {group, index}, loop",
    "POST /avatar/api/avatars/<name>/emotion       {name:happy, intensity, for}",
    "POST /avatar/api/avatars/<name>/face          {smile, brows, eyes, mouth_open, cheek, mouth_x, tilt, wink_left, wink_right}",
    "POST /avatar/api/avatars/<name>/look          {x,y} -1..1, {at:camera|left|...}, {stage_x,stage_y} or {release:true}",
    "POST /avatar/api/avatars/<name>/gesture       {name:nod|shake|tilt|bounce|lean_left|lean_right|look_away|double_nod}",
    "POST /avatar/api/avatars/<name>/transform     {x,y,scale,rotation,flip, duration, save}",
    "POST /avatar/api/avatars/<name>/show | hide   {fade}",
    "POST /avatar/api/avatars/<name>/speak         audio (multipart / raw / {url} / {audio_b64}), ?wait=1",
    "POST /avatar/api/avatars/<name>/stop_speaking",
    "POST /avatar/api/avatars/<name>/item_add      {item, x, y, scale, layer, pin}",
    "POST /avatar/api/avatars/<name>/light         {preset, color, intensity, angle, rim, ambient}",
    "POST /avatar/api/avatars/<name>/command       {cmd: <any of the above>, ...}",
    "WS   /avatar/ws/control                       the same commands as JSON, plus events back",
]


# ---- websockets ----

@router.websocket("/ws/render")
async def ws_render(ws: WebSocket):
    await ws.accept()
    q = ws.query_params
    only = {n.strip() for n in (q.get("avatar") or "").split(",") if n.strip()} or None
    c = Client(ws, "render", "obs" if q.get("role", "obs") == "obs" else "preview", only, q.get("audio", "1") != "0")
    HUB.loop = asyncio.get_running_loop()
    HUB.clients.add(c)
    HUB.sync_captures()
    try:
        await ws.send_text(json.dumps(snapshot()))
        while True:
            m = await ws.receive()
            if m.get("type") == "websocket.disconnect":
                break
            if m.get("text"):
                try:
                    msg = json.loads(m["text"])
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await on_render_message(c, msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        HUB.clients.discard(c)
        HUB.sync_captures()
        _overlay_gone(c)


def _overlay_gone(c: Client) -> None:
    """A speak call waiting on speech that only this overlay was playing must not hang."""
    for sid, fut in list(WAITERS.items()):
        s = SPEECH.get(sid)
        if not s or fut.done():
            continue
        s.get("listeners", set()).discard(c.id)
        if not s.get("listeners"):
            fut.set_result({"event": "speech_error", "id": sid, "error": "the OBS overlay disconnected"})


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    c = Client(ws, "panel")
    HUB.clients.add(c)
    try:
        await ws.send_text(json.dumps({**snapshot(), "runtime": runtime.status()}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        HUB.clients.discard(c)


@router.websocket("/ws/control")
async def ws_control(ws: WebSocket):
    """For bots: send {"cmd": "params", "avatar": "main", "values": {...}} (any command of the REST
    API, plus "speak" with `url` or `audio_b64`); every reply carries the `rid` you sent, and
    events (speech_start / speech_end / motion_end ...) arrive as {"type": "event", ...}."""
    await ws.accept()
    c = Client(ws, "control")
    HUB.clients.add(c)
    try:
        await ws.send_text(json.dumps({"type": "hello", "avatars": [a["name"] for a in CONFIG["avatars"]],
                                       "inputs": INPUTS, "gestures": GESTURES}))
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
            except ValueError:
                await ws.send_text(json.dumps({"ok": False, "error": "not JSON"}))
                continue
            if not isinstance(msg, dict):
                continue
            rid = msg.get("rid")
            try:
                name = str(msg.get("avatar") or "")
                if msg.get("cmd") == "speak":
                    data, mime = b"", "application/octet-stream"
                    if msg.get("audio_b64"):
                        data = base64.b64decode(str(msg["audio_b64"]).split(",")[-1])
                        mime = _sniff(data)
                    elif msg.get("url"):
                        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cl:
                            r = await cl.get(str(msg["url"]))
                            data, mime = r.content, (r.headers.get("content-type") or "").split(";")[0] or _sniff(r.content)
                    res = await speak(name, data, mime, {k: v for k, v in msg.items() if k not in ("audio_b64", "wait")})
                elif msg.get("cmd") == "list":
                    res = {"ok": True, "avatars": CONFIG["avatars"]}
                elif msg.get("cmd") == "info":
                    res = {"ok": True, **avatar_info(name)}
                else:
                    res = await command(name, msg)
            except (CommandError, library.LibraryError) as exc:
                res = {"ok": False, "error": exc.message}
            except Exception as exc:
                res = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
            if rid is not None:
                res["rid"] = rid
            if rid is not None or not res.get("ok", True):
                await ws.send_text(json.dumps(res))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        HUB.clients.discard(c)


# --------------------------------------------------------------------------------------------
# attach / detach
# --------------------------------------------------------------------------------------------

def attach(ctx) -> None:
    global STATIC_URL
    library.ensure_dirs()
    STATIC_URL = str(ctx.static_url).rstrip("/")
    ctx.include_router(router)
    ctx.app.mount(library.URL, RevalidatingStaticFiles(directory=str(library.ROOT)), name="avatar-lib")
    print(f"  Avatars panel:       http://localhost:{ctx.port}/avatar", flush=True)
    print(f"  Avatars source:      http://localhost:{ctx.port}/avatar/overlay", flush=True)


async def detach() -> None:
    HUB.captures.stop_all(wait=1.0)
    for fut in list(WAITERS.values()):
        if not fut.done():
            fut.cancel()
    WAITERS.clear()
    for c in list(HUB.clients):
        try:
            await c.ws.close()
        except Exception:
            pass
    HUB.clients.clear()
