"""
HexCast - Chat Timers
=====================

Messages Hexcast posts in your Twitch chat on a timer, as your account - the Discord link, the schedule, your
socials. Like StreamElements' or Nightbot's timers: each message has its own interval, waits for some chat in
between (so an empty chat is not spammed), and by default only runs while you are live.

    http://localhost:4747/chat_timers     -> the tab (no overlay: everything happens in chat)

The connection to Twitch is the shared twitch_core library - the same sign-in and channels as the Twitch plugin.
Bots get the same calls as REST and over the socket at /chat_timers/ws (SOCKET_CALLS).
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from hexcast_core import paths
from hexcast_plugins.twitch_core import core as tc

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = paths.CONFIG_DIR / "chat_timers.json"

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

MAX_TIMERS = 50
TICK_SECONDS = 5
DEFAULT_CONFIG: dict[str, Any] = {
    "on": True,                 # the master switch
    "gap": 60,                  # seconds at least between two timer messages in one channel
    "timers": [],
}
TIMER_DEFAULTS: dict[str, Any] = {
    "id": "",
    "name": "",                 # for the tab only
    "text": "",                 # what is posted; {channel} and {uptime} are filled in
    "every": 15,                # minutes
    "lines": 5,                 # chat lines (from anyone but Hexcast itself) since its last post
    "live_only": True,          # only while the channel is live
    "channel": "primary",       # primary | all | a channel's login
    "on": True,
}
LIMITS = {"every": (1, 1440), "lines": (0, 1000), "gap": (0, 3600)}
TIMER_ID = re.compile(r"^[a-z0-9]{4,16}$")


class Problem(Exception):
    """A setting that cannot be taken, said for the person who typed it."""


def _int(value: Any, key: str) -> int:
    lo, hi = LIMITS[key]
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise Problem(f"{key} is a number") from None
    if not lo <= n <= hi:
        raise Problem(f"{key} is {lo} to {hi}")
    return n


def clean_timer(raw: Any, base: dict | None = None) -> dict:
    """A timer's settings checked (and `base` - the timer as it was - filled in for what `raw` leaves out)."""
    if not isinstance(raw, dict):
        raise Problem("a timer is an object")
    t = {**TIMER_DEFAULTS, **(base or {})}
    if "id" in raw:
        t["id"] = str(raw["id"] or "").strip().lower()
    for key in ("name", "text", "channel"):
        if key in raw:
            t[key] = str(raw[key] if raw[key] is not None else "").strip()
    for key in ("every", "lines"):
        if key in raw:
            t[key] = _int(raw[key], key)
    for key in ("live_only", "on"):
        if key in raw:
            t[key] = bool(raw[key])
    t["name"] = t["name"][:40]
    if len(t["text"]) > tc.CHAT_LIMIT:
        raise Problem(f"a chat message is at most {tc.CHAT_LIMIT} characters")
    channel = tc.clean_login(t["channel"]) or "primary"
    if channel not in ("primary", "all") and not tc.CHANNEL_LOGIN.match(channel):
        raise Problem(f"'{t['channel']}' is not a channel (primary, all, or a channel name)")
    t["channel"] = channel
    if not TIMER_ID.match(str(t.get("id") or "")):
        t["id"] = secrets.token_hex(4)
    return {k: t[k] for k in TIMER_DEFAULTS}


def clean_config(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    cfg = dict(DEFAULT_CONFIG)
    if "on" in raw:
        cfg["on"] = bool(raw["on"])
    if "gap" in raw:
        cfg["gap"] = _int(raw["gap"], "gap")
    timers = raw.get("timers", [])
    if not isinstance(timers, list):
        raise Problem("timers is a list")
    if len(timers) > MAX_TIMERS:
        raise Problem(f"at most {MAX_TIMERS} timers")
    seen: set[str] = set()
    cfg["timers"] = []
    for row in timers:
        t = clean_timer(row)
        while t["id"] in seen:
            t["id"] = secrets.token_hex(4)
        seen.add(t["id"])
        cfg["timers"].append(t)
    return cfg


def load_config() -> dict:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    try:
        return clean_config(raw)
    except Problem:
        return clean_config({})


def save_config(cfg: dict) -> dict:
    cfg = clean_config(cfg)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


CONFIG = load_config()


def find(timer_id: str) -> dict:
    for t in CONFIG["timers"]:
        if t["id"] == timer_id:
            return t
    raise Problem(f"no timer '{timer_id}'")


# --------------------------------------------------------------------------
# the schedule
# --------------------------------------------------------------------------

class Slot:
    """One timer in one channel: when it last posted, and how much chat there was by then."""

    def __init__(self, now: float, mark: int) -> None:
        self.last_sent = now        # (a new timer waits a whole interval before its first message)
        self.mark = mark            # LINES of the channel at that moment
        self.sent = 0
        self.last_error = ""


SLOTS: dict[tuple[str, str], Slot] = {}     # (timer id, channel login) -> its schedule
LINES: dict[str, int] = {}                  # channel login -> chat lines seen (from anyone but Hexcast)
LAST_POST: dict[str, float] = {}            # channel login -> when a timer last posted there


def targets(t: dict) -> list[str]:
    """The channels a timer posts in: the primary, every enabled channel, or the one it names (if watched)."""
    rows = [r for r in tc.configured_channels() if r.get("enabled", True)]
    if t["channel"] == "all":
        return [r["login"] for r in rows]
    if t["channel"] == "primary":
        return [rows[0]["login"]] if rows and rows[0]["login"] == tc.primary_login() else []
    return [t["channel"]] if any(r["login"] == t["channel"] for r in rows) else []


def slot(t: dict, login: str, now: float | None = None) -> Slot:
    key = (t["id"], login)
    if key not in SLOTS:
        SLOTS[key] = Slot(time.time() if now is None else now, LINES.get(login, 0))
    return SLOTS[key]


def waiting_for(t: dict, login: str, now: float) -> str:
    """Why a timer is not posting in a channel right now: off | interval | chat | live | gap | '' (it is due)."""
    if not CONFIG["on"] or not t["on"] or not t["text"]:
        return "off"
    s = slot(t, login, now)
    if now - s.last_sent < t["every"] * 60:
        return "interval"
    if LINES.get(login, 0) - s.mark < t["lines"]:
        return "chat"
    if t["live_only"] and tc.is_live(login) is not True:
        return "live"
    if now - LAST_POST.get(login, 0.0) < CONFIG["gap"]:
        return "gap"
    return ""


def _uptime(login: str) -> str:
    ch = tc.STATE.channel(login)
    if not ch or ch.live is not True or not ch.live_since:
        return "offline"
    mins = int((time.time() - ch.live_since) // 60)
    return f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def fill(text: str, login: str) -> str:
    """{channel} and {uptime} filled in; any other {...} is left as it is typed."""
    values = {"channel": login, "uptime": _uptime(login)}
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1).lower(), m.group(0)), text)


async def post(t: dict, login: str, now: float | None = None) -> str:
    """Post a timer's message in a channel now. '' when Twitch took it, else why not."""
    now = time.time() if now is None else now
    s = slot(t, login, now)
    try:
        await tc.send_chat(fill(t["text"], login), login)
    except tc.TwitchError as exc:
        s.last_error = str(exc)
        s.last_sent = now                   # try again after another interval, not every few seconds
        tc.note(f"chat timer '{t['name'] or t['text'][:30]}' in #{login}: {exc}")
        return str(exc)
    s.last_sent, s.mark, s.last_error = now, LINES.get(login, 0), ""
    s.sent += 1
    LAST_POST[login] = now
    return ""


async def tick(now: float | None = None) -> list[tuple[str, str]]:
    """Post what is due: per channel the timer that has waited longest, and then nothing more there for `gap`
    seconds. Returns [(timer id, channel)] posted (or tried)."""
    now = time.time() if now is None else now
    done: list[tuple[str, str]] = []
    if not CONFIG["on"]:
        return done
    by_channel: dict[str, list[dict]] = {}
    for t in CONFIG["timers"]:
        for login in targets(t):
            by_channel.setdefault(login, []).append(t)
    for login, timers in by_channel.items():
        due = [t for t in timers if waiting_for(t, login, now) == ""]
        if due:
            t = min(due, key=lambda x: slot(x, login, now).last_sent)
            await post(t, login, now)
            done.append((t["id"], login))
    return done


async def on_chat(msg: dict, ch: tc.ChannelRT) -> None:
    """Count the chat - not Hexcast's own lines (its timers and shoutouts come back from Twitch like anyone's)."""
    if (msg.get("user") or {}).get("login", "").lower() in tc.own_logins():
        return
    LINES[ch.login] = LINES.get(ch.login, 0) + 1


async def on_event(kind: str, ev: dict, msg_id: str | None, ch: tc.ChannelRT) -> None:
    """A stream starting restarts its timers: the first message comes an interval into the stream."""
    if kind != "stream.online":
        return
    now = time.time()
    for (tid, login), s in SLOTS.items():
        if login == ch.login:
            s.last_sent, s.mark = now, LINES.get(login, 0)


# --------------------------------------------------------------------------
# status, the panels
# --------------------------------------------------------------------------

def connection_problem() -> str:
    """Why timers cannot post in the primary channel, or ''."""
    try:
        tc.acting_account(tc.channel_for(), "user:write:chat")
    except tc.TwitchError as exc:
        return str(exc)
    return ""


def status() -> dict:
    now = time.time()
    rows = []
    for t in CONFIG["timers"]:
        where = []
        for login in targets(t):
            s = slot(t, login, now)
            where.append({
                "channel": login,
                "waiting_for": waiting_for(t, login, now),
                "next_in": max(0, round(s.last_sent + t["every"] * 60 - now)),
                "lines_needed": max(0, t["lines"] - (LINES.get(login, 0) - s.mark)),
                "sent": s.sent,
                "last_sent": s.last_sent if s.sent else None,
                "last_error": s.last_error,
            })
        rows.append({**t, "targets": where})
    channels = [{"login": r["login"], "label": r.get("label", ""), "enabled": r.get("enabled", True),
                 "live": tc.is_live(r["login"])} for r in tc.configured_channels()]
    return {"on": CONFIG["on"], "gap": CONFIG["gap"], "timers": rows, "channels": channels,
            "problem": connection_problem(), "panels": len(HUB.panels), "now": now}


class Hub:
    def __init__(self) -> None:
        self.panels: set[WebSocket] = set()

    async def send(self, payload: dict) -> None:
        text = json.dumps(payload)
        for ws in list(self.panels):
            try:
                await ws.send_text(text)
            except Exception:
                self.panels.discard(ws)

    async def status(self) -> None:
        if self.panels:
            await self.send({"type": "status", "status": status()})


HUB = Hub()


async def loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await tick()
            await HUB.status()              # (the tab's countdowns)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            tc.note(f"chat timers: {exc}")
        await asyncio.sleep(TICK_SECONDS)


class Loops:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop = asyncio.Event()

    async def ensure_started(self) -> None:
        await tc.ensure_started()
        if self.task is None or self.task.done():
            self.stop = asyncio.Event()
            self.task = asyncio.create_task(loop(self.stop))

    async def shutdown(self) -> None:
        self.stop.set()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None


LOOPS = Loops()


async def ensure_started() -> None:
    await LOOPS.ensure_started()


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/chat_timers", tags=["chat_timers"])
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
APP = None                                  # the app, for socket commands run as their REST calls


def _bad(exc: Exception, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=code)


async def _body(request: Request) -> dict:
    if not await request.body():
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise Problem("send a JSON object")
    return data


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    await ensure_started()
    return HTMLResponse((STATIC_DIR / "timers_panel.html").read_text(encoding="utf-8"), headers=_NOCACHE)


@router.get("/api/status")
async def api_status():
    """Every timer with where it posts and what it waits for, the channels, and why posting cannot work (if so)."""
    return status()


@router.get("/api/config")
async def api_config():
    return CONFIG


@router.post("/api/config")
async def api_set_config(request: Request):
    """{on?, gap?, timers?} - what is left out stays as it is; `timers` replaces the whole list."""
    global CONFIG
    try:
        body = await _body(request)
        CONFIG = save_config({**CONFIG, **{k: body[k] for k in ("on", "gap", "timers") if k in body}})
    except Problem as exc:
        return _bad(exc)
    await HUB.status()
    return {"ok": True, "config": CONFIG}


@router.post("/api/timers")
async def api_add(request: Request):
    """A new timer: {text, every?, lines?, live_only?, channel?, name?, on?}."""
    global CONFIG
    try:
        body = await _body(request)
        if len(CONFIG["timers"]) >= MAX_TIMERS:
            raise Problem(f"at most {MAX_TIMERS} timers")
        t = clean_timer({k: v for k, v in body.items() if k != "id"})
        if not t["text"]:
            raise Problem("a timer needs its text")
        CONFIG = save_config({**CONFIG, "timers": CONFIG["timers"] + [t]})
    except Problem as exc:
        return _bad(exc)
    await HUB.status()
    return {"ok": True, "timer": CONFIG["timers"][-1]}


@router.post("/api/timers/{timer_id}")
async def api_update(timer_id: str, request: Request):
    """Change some of a timer's settings: only the fields you send."""
    global CONFIG
    try:
        old = find(timer_id)
        t = clean_timer({k: v for k, v in (await _body(request)).items() if k != "id"}, old)
        CONFIG = save_config({**CONFIG, "timers": [t if x["id"] == timer_id else x for x in CONFIG["timers"]]})
    except Problem as exc:
        return _bad(exc, 404 if "no timer" in str(exc) else 400)
    await HUB.status()
    return {"ok": True, "timer": find(timer_id)}


@router.post("/api/timers/{timer_id}/delete")
async def api_delete(timer_id: str):
    global CONFIG
    try:
        find(timer_id)
    except Problem as exc:
        return _bad(exc, 404)
    CONFIG = save_config({**CONFIG, "timers": [x for x in CONFIG["timers"] if x["id"] != timer_id]})
    for key in [k for k in SLOTS if k[0] == timer_id]:
        SLOTS.pop(key, None)
    await HUB.status()
    return {"ok": True}


@router.post("/api/timers/{timer_id}/send")
async def api_send(timer_id: str, request: Request):
    """Post it now - in its channels, or {"channel": "name"} - whether it is due, live or switched on or not.
    Its schedule starts again from now."""
    try:
        t = find(timer_id)
        body = await _body(request)
    except Problem as exc:
        return _bad(exc, 404 if "no timer" in str(exc) else 400)
    if not t["text"]:
        return _bad(Problem("this timer has no text"))
    channel = tc.clean_login(body.get("channel"))
    logins = [channel] if channel else targets(t)
    if not logins:
        return _bad(Problem("no channel to post in - check the timer's channel and the Twitch connection"))
    errors = {login: err for login in logins if (err := await post(t, login))}
    await HUB.status()
    if errors and len(errors) == len(logins):
        return JSONResponse({"ok": False, "error": next(iter(errors.values())), "errors": errors}, status_code=400)
    return {"ok": True, "sent": [x for x in logins if x not in errors], "errors": errors}


# The socket does what the REST API does: each of these IS its REST call, run in-process ({id} comes from the
# message's `id`; the rest of the message is the body).
SOCKET_CALLS: dict[str, tuple[str, str]] = {
    "status": ("GET", "/api/status"),
    "config": ("GET", "/api/config"),
    "set_config": ("POST", "/api/config"),
    "add": ("POST", "/api/timers"),
    "update": ("POST", "/api/timers/{id}"),
    "delete": ("POST", "/api/timers/{id}/delete"),
    "send": ("POST", "/api/timers/{id}/send"),
}


async def socket_call(kind: str, msg: dict) -> dict:
    method, route = SOCKET_CALLS[kind]
    fields = {k: v for k, v in msg.items() if k not in ("cmd", "rid")}
    path = route
    for p in re.findall(r"\{(\w+)\}", route):
        v = str(fields.pop(p, "") or "").strip()
        if not v:
            return {"ok": False, "error": f"`{kind}` needs `{p}`"}
        path = path.replace("{" + p + "}", quote(v, safe=""))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url="http://hexcast") as cl:
        r = await cl.request(method, router.prefix + path, json=fields if method == "POST" else None)
    try:
        j = r.json()
    except ValueError:
        j = None
    if r.status_code >= 400:
        err = (j.get("error") or j.get("detail")) if isinstance(j, dict) else None
        return {"ok": False, "error": str(err or f"{kind} failed ({r.status_code})")}
    return {**j, "ok": True} if isinstance(j, dict) else {"ok": True, "result": j}


@router.websocket("/ws")
async def ws_timers(ws: WebSocket):
    """The tab's live status, and for bots every REST call by name: {"cmd": "send", "id": "...", "rid": 1}.
    Replies carry the `rid` you sent; status updates arrive as {"type": "status", ...}."""
    await ws.accept()
    HUB.panels.add(ws)
    await ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "hello", "commands": list(SOCKET_CALLS)}))
        await ws.send_text(json.dumps({"type": "status", "status": status()}))
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
            except ValueError:
                await ws.send_text(json.dumps({"ok": False, "error": "not JSON"}))
                continue
            if not isinstance(msg, dict):
                continue
            kind = str(msg.get("cmd") or "")
            if kind in SOCKET_CALLS:
                res = await socket_call(kind, msg)
            else:
                res = {"ok": False, "error": f"unknown cmd '{kind}' - one of {', '.join(SOCKET_CALLS)}"}
            if msg.get("rid") is not None:
                res["rid"] = msg["rid"]
            await ws.send_text(json.dumps({"type": "result", "cmd": kind, **res}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        HUB.panels.discard(ws)


def attach(app, port: int = 4747) -> None:
    global APP
    APP = app
    app.include_router(router)
    print(f"  Chat timers:         http://localhost:{port}/chat_timers", flush=True)
