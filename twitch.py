"""
HexCast - Twitch module
=======================

Drop this next to soundboard.py, then add two lines to soundboard.py after the
FastAPI app is created:

    from twitch import attach_twitch
    attach_twitch(app)

Adds:
    http://localhost:4747/twitch          -> control panel (login + settings)
    http://localhost:4747/twitch/chat     -> chat overlay (OBS browser source)
    http://localhost:4747/twitch/events   -> alert/event overlay (OBS browser source)

Everything lives under /twitch/* so it cannot collide with existing routes.

Data sources:
    * EventSub over WebSocket (needs OAuth) - chat + follows/subs/raids/bits/redeems
    * Anonymous Twitch IRC (needs nothing)  - chat only, used as fallback so you
      can see chat on screen before doing the OAuth dance.

Security note: this file stores your Twitch client secret and user tokens in
config/twitch_secrets.json. HexCast has no auth, so treat that box as trusted.
The API never returns the secret or the tokens.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import websockets
from fastapi import APIRouter, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = Path(os.environ.get("HEXCAST_CONFIG_DIR", BASE_DIR / "config"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_static(name: str) -> str:
    """Read a static file on every request, so edits show up without a restart."""
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The Twitch module needs "
            f"twitch_panel.html, twitch_chat.html, twitch_events.html and "
            f"twitch_boot.js in ./static/ next to hexcast.py."
        )
    return path.read_text(encoding="utf-8")

CONFIG_PATH = CONFIG_DIR / "twitch.json"
SECRETS_PATH = CONFIG_DIR / "twitch_secrets.json"

# Uploaded chat/alert background images live here and are served by hexcast's
# existing /media StaticFiles mount, so the overlays can reach them by URL.
# SOUNDBOARD_MEDIA_DIR matches the env var hexcast.py uses for its media root.
MEDIA_ROOT = Path(os.getenv("SOUNDBOARD_MEDIA_DIR", str(BASE_DIR / "media"))).expanduser().resolve()
OVERLAY_BG_DIR = MEDIA_ROOT / "overlays"
OVERLAY_BG_DIR.mkdir(parents=True, exist_ok=True)
BG_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".apng"}
_SAFE_BG_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_bg_name(name: str) -> str:
    """Sanitise an uploaded filename to a safe, flat name inside OVERLAY_BG_DIR."""
    stem = _SAFE_BG_NAME.sub("_", Path(name).stem).strip("._-") or "background"
    return stem[:60] + Path(name).suffix.lower()


def _list_backgrounds() -> list[dict]:
    out = []
    for p in sorted(OVERLAY_BG_DIR.glob("*")):
        if p.is_file() and p.suffix.lower() in BG_IMAGE_EXTS:
            out.append({"name": p.name, "url": f"/media/overlays/{p.name}"})
    return out

HELIX = "https://api.twitch.tv/helix"
TWITCH_ID = "https://id.twitch.tv/oauth2"
EVENTSUB_WS = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
IRC_WS = "wss://irc-ws.chat.twitch.tv:443"

SCOPES = [
    "user:read:chat",
    "channel:read:subscriptions",
    "moderator:read:followers",
    "bits:read",
    "channel:read:redemptions",
    "channel:read:hype_train",
    # The !so shoutout command. Added later than the rest - if these are
    # missing from an existing login, reconnect in the panel to grant them.
    "moderator:manage:shoutouts",   # the official /shoutout banner
    "user:write:chat",              # posting the shoutout line in chat
]

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "channel": "",
    "hexcast_url": "http://localhost:4747",
    "forward_url": "",
    "chat": {
        "font_family": "Inter",
        "font_size": 28,
        "font_weight": 600,
        "name_weight": 800,
        "line_height": 1.35,
        "text_color": "#f2f2f7",
        "name_color_mode": "twitch",
        "name_color": "#ff3b30",
        "layout": "inline",
        "bubble": True,
        "bubble_color": "#0b0b10",
        "bubble_opacity": 0.72,
        "bubble_radius": 14,
        # Fancy message backgrounds. bg_style: solid | gradient | animated |
        # glass | frame | glow | image | slice. The others feed whichever
        # style is on. "slice" is a 9-slice frame image: corners stay crisp
        # while the edges and middle stretch to fit the message.
        "bg_style": "solid",
        "bg_color2": "#1a1a2e",
        "bg_gradient_angle": 135,
        "bg_image_url": "",
        "bg_blur": 8,
        "bg_border_color": "#ff3b30",
        "bg_border_width": 2,
        "bg_slice": 32,
        "bg_slice_width": 24,
        "bg_slice_repeat": "stretch",
        # Extra breathing room inside image/frame backgrounds so words don't
        # sit right on the artwork edge.
        "bg_pad": 20,
        # Placement. The overlay is always the full OBS canvas; these lock the
        # chat and its background to boxes *inside* it (percent of the source),
        # so you size things in Hexcast at full fidelity instead of scaling the
        # OBS source. box_* is the chat text box; bg_box_* is the background
        # panel box (or the whole screen when bg_full is on).
        "box_enabled": False,
        "box_x": 55, "box_y": 8, "box_w": 42, "box_h": 84,
        "bg_full": False,
        "bg_box_x": 55, "bg_box_y": 8, "bg_box_w": 42, "bg_box_h": 84,
        "padding": 12,
        "gap": 8,
        "outline": True,
        "outline_color": "#000000",
        "outline_width": 2,
        "shadow": True,
        "max_messages": 25,
        "fade_after": 0,
        "fade_duration": 0.5,
        "direction": "bottom",
        "align": "left",
        "show_badges": True,
        "show_timestamps": False,
        "emote_size": 34,
        "third_party_emotes": True,
        "hide_commands": True,
        "hide_users": "nightbot, streamelements, streamlabs, moobot, fossabot",
        "animation": "slide",
        "width_percent": 100,
        "highlight_first": True,
        "highlight_color": "#ff3b30",
    },
    "events": {
        "font_family": "Inter",
        "font_size": 40,
        "sub_font_size": 26,
        "text_color": "#ffffff",
        "accent_color": "#ff3b30",
        "bubble_color": "#0b0b10",
        "bubble_opacity": 0.85,
        "bubble_radius": 18,
        # Fancy alert backgrounds - same vocabulary as chat above.
        "bg_style": "solid",
        "bg_color2": "#1a1a2e",
        "bg_gradient_angle": 135,
        "bg_image_url": "",
        "bg_blur": 8,
        "bg_border_color": "#ff3b30",
        "bg_border_width": 2,
        "bg_slice": 32,
        "bg_slice_width": 24,
        "bg_slice_repeat": "stretch",
        "bg_pad": 20,
        "outline": True,
        "outline_color": "#000000",
        "align": "center",
        "valign": "middle",
        "animation": "pop",
        "default_duration": 6,
        "gap_between": 0.6,
        "show_user_message": True,
    },
    # !so <channel>: official Twitch shoutout + a chat line + a random clip of
    # theirs fired at the Clips overlay (via /clips/api/shoutout, ephemeral -
    # nothing is queued or saved).
    "shoutout": {
        "on": True,
        "command": "!so",
        "who": "mods",              # broadcaster | mods (broadcaster always may)
        "native": True,             # attempt the official /shoutout banner
        "message": "Go show {name} some love at {url} - they're worth the follow!",
        "clip": True,               # play random clips from their channel
        "clip_count": 2,            # how many, chained back to back (1-5)
    },
    "alerts": {
        "follow": {"on": True, "duration": 5, "title": "New follower", "body": "{user}", "clip": ""},
        "subscribe": {"on": True, "duration": 7, "title": "New sub", "body": "{user} - tier {tier}", "clip": ""},
        "resub": {"on": True, "duration": 8, "title": "Resub", "body": "{user} - {months} months", "clip": ""},
        "subgift": {"on": True, "duration": 7, "title": "Gifted subs", "body": "{user} gifted {amount}", "clip": ""},
        "cheer": {"on": True, "duration": 7, "title": "Bits", "body": "{user} cheered {amount}", "clip": "", "min_amount": 1},
        "raid": {"on": True, "duration": 9, "title": "Raid", "body": "{user} raided with {amount}", "clip": ""},
        "redeem": {"on": True, "duration": 6, "title": "Redeemed", "body": "{user}: {reward}", "clip": ""},
        "hypetrain": {"on": True, "duration": 6, "title": "Hype train", "body": "Level {amount}", "clip": ""},
        "online": {"on": False, "duration": 5, "title": "Live", "body": "Stream started", "clip": ""},
        "offline": {"on": False, "duration": 5, "title": "Offline", "body": "Stream ended", "clip": ""},
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


# --------------------------------------------------------------------------
# secrets / tokens
# --------------------------------------------------------------------------

class Secrets:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if SECRETS_PATH.exists():
            try:
                self.data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        # env vars win if set and nothing stored yet
        self.data.setdefault("client_id", os.environ.get("TWITCH_CLIENT_ID", ""))
        self.data.setdefault("client_secret", os.environ.get("TWITCH_CLIENT_SECRET", ""))

    def save(self) -> None:
        SECRETS_PATH.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        try:
            os.chmod(SECRETS_PATH, 0o600)
        except Exception:
            pass

    @property
    def client_id(self) -> str:
        return self.data.get("client_id", "")

    @property
    def client_secret(self) -> str:
        return self.data.get("client_secret", "")

    @property
    def access_token(self) -> str:
        return self.data.get("access_token", "")

    @property
    def refresh_token(self) -> str:
        return self.data.get("refresh_token", "")

    @property
    def user_id(self) -> str:
        return self.data.get("user_id", "")

    @property
    def user_login(self) -> str:
        return self.data.get("user_login", "")

    def clear_tokens(self) -> None:
        for k in ("access_token", "refresh_token", "expires_at", "user_id", "user_login", "scopes"):
            self.data.pop(k, None)
        self.save()

    def is_authed(self) -> bool:
        return bool(self.access_token and self.user_id)


SECRETS = Secrets()


async def exchange_code(code: str, redirect_uri: str) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{TWITCH_ID}/token",
            data={
                "client_id": SECRETS.client_id,
                "client_secret": SECRETS.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        r.raise_for_status()
        tok = r.json()
    SECRETS.data["access_token"] = tok["access_token"]
    SECRETS.data["refresh_token"] = tok.get("refresh_token", "")
    SECRETS.data["expires_at"] = time.time() + tok.get("expires_in", 3600)
    SECRETS.save()
    await validate_token()


async def refresh_token() -> bool:
    if not SECRETS.refresh_token:
        return False
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{TWITCH_ID}/token",
            data={
                "client_id": SECRETS.client_id,
                "client_secret": SECRETS.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": SECRETS.refresh_token,
            },
        )
    if r.status_code != 200:
        STATE.note(f"token refresh failed ({r.status_code}) - reconnect Twitch in the panel")
        return False
    tok = r.json()
    SECRETS.data["access_token"] = tok["access_token"]
    SECRETS.data["refresh_token"] = tok.get("refresh_token", SECRETS.refresh_token)
    SECRETS.data["expires_at"] = time.time() + tok.get("expires_in", 3600)
    SECRETS.save()
    return True


async def validate_token() -> bool:
    if not SECRETS.access_token:
        return False
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{TWITCH_ID}/validate", headers={"Authorization": f"OAuth {SECRETS.access_token}"})
    if r.status_code != 200:
        return False
    d = r.json()
    SECRETS.data["user_id"] = d.get("user_id", "")
    SECRETS.data["user_login"] = d.get("login", "")
    SECRETS.data["scopes"] = d.get("scopes", [])
    SECRETS.save()
    return True


async def helix(method: str, path: str, *, params=None, json_body=None, retry=True) -> httpx.Response:
    headers = {
        "Client-Id": SECRETS.client_id,
        "Authorization": f"Bearer {SECRETS.access_token}",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(method, f"{HELIX}{path}", headers=headers, params=params, json=json_body)
    if r.status_code == 401 and retry:
        if await refresh_token():
            return await helix(method, path, params=params, json_body=json_body, retry=False)
    return r


# --------------------------------------------------------------------------
# runtime state
# --------------------------------------------------------------------------

class State:
    def __init__(self) -> None:
        self.source = "none"          # none | eventsub | irc
        self.connected = False
        self.channel_id = ""
        self.channel_login = ""
        self.subs_ok: list[str] = []
        self.subs_failed: list[str] = []
        self.last_error = ""
        self.log: list[str] = []

    def note(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        self.log.append(line)
        del self.log[:-60]
        print(f"[twitch] {msg}", flush=True)

    def snapshot(self) -> dict:
        return {
            "source": self.source,
            "connected": self.connected,
            "channel_id": self.channel_id,
            "channel_login": self.channel_login,
            "authed": SECRETS.is_authed(),
            "has_credentials": bool(SECRETS.client_id and SECRETS.client_secret),
            "bot_login": SECRETS.user_login,
            "scopes": SECRETS.data.get("scopes", []),
            "subs_ok": self.subs_ok,
            "subs_failed": self.subs_failed,
            "last_error": self.last_error,
            "log": self.log[-25:],
        }


STATE = State()


# --------------------------------------------------------------------------
# broadcast hub
# --------------------------------------------------------------------------


class AlertQueue:
    def __init__(self) -> None:
        self.queue: list[dict] = []
        self.playing: dict | None = None
        self.seen_msg_ids: dict[str, None] = {}
        self.ack_event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.load()

    def load(self) -> None:
        if (Path("config") / "twitch_queue.json").exists():
            try:
                data = json.loads((Path("config") / "twitch_queue.json").read_text())
                self.queue = data.get("queue", [])
                self.seen_msg_ids = {k: None for k in data.get("seen", [])}
            except Exception:
                self.queue = []
                self.seen_msg_ids = {}

    def save(self) -> None:
        try:
            (Path("config")).mkdir(exist_ok=True)
            seen_list = list(self.seen_msg_ids.keys())[-1000:]
            (Path("config") / "twitch_queue.json").write_text(json.dumps({
                "queue": self.queue,
                "seen": seen_list
            }, indent=2))
        except Exception:
            pass

    async def enqueue(self, alert: dict, msg_id: str | None = None) -> bool:
        async with self.lock:
            if msg_id:
                if msg_id in self.seen_msg_ids:
                    return False
                self.seen_msg_ids[msg_id] = None
            self.queue.append(alert)
            self.save()
        await HUB.to_panel({"type": "queue", "queue": self.snapshot()})
        return True

    async def remove(self, alert_id: str) -> bool:
        async with self.lock:
            for i, a in enumerate(self.queue):
                if a.get("id") == alert_id:
                    self.queue.pop(i)
                    self.save()
                    return True
        return False

    async def clear(self) -> None:
        async with self.lock:
            self.queue.clear()
            self.save()

    def skip(self) -> None:
        self.ack_event.set()

    def snapshot(self) -> dict:
        return {
            "playing": self.playing,
            "queue": self.queue
        }

ALERT_QUEUE = AlertQueue()


class Hub:
    def __init__(self) -> None:
        self.chat: set[WebSocket] = set()
        self.events: set[WebSocket] = set()
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

    async def to_chat(self, payload: dict) -> None:
        await self._send(self.chat, payload)

    async def to_events(self, payload: dict) -> None:
        await self._send(self.events, payload)

    async def to_panel(self, payload: dict) -> None:
        await self._send(self.panel, payload)

    async def broadcast_config(self) -> None:
        await self.to_chat({"type": "config", "config": CONFIG})
        await self.to_events({"type": "config", "config": CONFIG})

    async def broadcast_status(self) -> None:
        await self.to_panel({"type": "status", "status": STATE.snapshot()})


HUB = Hub()


# --------------------------------------------------------------------------
# emotes + badges
# --------------------------------------------------------------------------

class Assets:
    """Third-party emotes (7TV / BTTV / FFZ) and Twitch chat badges."""

    def __init__(self) -> None:
        self.emotes: dict[str, str] = {}      # emote code -> image url
        self.badges: dict[str, dict] = {}     # "set_id/version" -> {url, title}
        self.loaded_for = ""

    async def load(self, channel_id: str) -> None:
        self.emotes = {}
        self.badges = {}
        await asyncio.gather(
            self._seventv(channel_id),
            self._bttv(channel_id),
            self._ffz(channel_id),
            self._badges(channel_id),
            return_exceptions=True,
        )
        self.loaded_for = channel_id
        STATE.note(f"assets loaded: {len(self.emotes)} 3rd-party emotes, {len(self.badges)} badges")

    async def _get(self, client: httpx.AsyncClient, url: str):
        try:
            r = await client.get(url, timeout=12)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    async def _seventv(self, channel_id: str) -> None:
        async with httpx.AsyncClient() as c:
            g = await self._get(c, "https://7tv.io/v3/emote-sets/global")
            u = await self._get(c, f"https://7tv.io/v3/users/twitch/{channel_id}")
        sets = []
        if g:
            sets.append(g)
        if u and isinstance(u.get("emote_set"), dict):
            sets.append(u["emote_set"])
        for s in sets:
            for e in s.get("emotes", []) or []:
                eid = e.get("id")
                name = e.get("name")
                if eid and name:
                    self.emotes[name] = f"https://cdn.7tv.app/emote/{eid}/2x.webp"

    async def _bttv(self, channel_id: str) -> None:
        async with httpx.AsyncClient() as c:
            g = await self._get(c, "https://api.betterttv.net/3/cached/emotes/global")
            u = await self._get(c, f"https://api.betterttv.net/3/cached/users/twitch/{channel_id}")
        items = list(g or [])
        if u:
            items += (u.get("channelEmotes") or []) + (u.get("sharedEmotes") or [])
        for e in items:
            eid, name = e.get("id"), e.get("code")
            if eid and name:
                self.emotes[name] = f"https://cdn.betterttv.net/emote/{eid}/2x"

    async def _ffz(self, channel_id: str) -> None:
        async with httpx.AsyncClient() as c:
            g = await self._get(c, "https://api.frankerfacez.com/v1/set/global")
            u = await self._get(c, f"https://api.frankerfacez.com/v1/room/id/{channel_id}")
        for blob in (g, u):
            if not blob:
                continue
            for s in (blob.get("sets") or {}).values():
                for e in s.get("emoticons", []) or []:
                    urls = e.get("urls") or {}
                    url = urls.get("2") or urls.get("1")
                    if e.get("name") and url:
                        if url.startswith("//"):
                            url = "https:" + url
                        self.emotes[e["name"]] = url

    async def _badges(self, channel_id: str) -> None:
        if not SECRETS.is_authed():
            return
        for path, params in (
            ("/chat/badges/global", None),
            ("/chat/badges", {"broadcaster_id": channel_id}),
        ):
            r = await helix("GET", path, params=params)
            if r.status_code != 200:
                continue
            for s in r.json().get("data", []):
                for v in s.get("versions", []):
                    key = f"{s['set_id']}/{v['id']}"
                    self.badges[key] = {
                        "url": v.get("image_url_2x") or v.get("image_url_1x"),
                        "title": v.get("title") or s["set_id"],
                    }


ASSETS = Assets()


def apply_third_party(fragments: list[dict]) -> list[dict]:
    """Split plain-text fragments on whitespace and swap in 3rd-party emotes."""
    if not CONFIG["chat"].get("third_party_emotes") or not ASSETS.emotes:
        return fragments
    out: list[dict] = []
    for frag in fragments:
        if frag.get("t") != "text":
            out.append(frag)
            continue
        buf: list[str] = []
        for word in re.split(r"(\s+)", frag.get("v", "")):
            url = ASSETS.emotes.get(word)
            if url:
                if buf:
                    out.append({"t": "text", "v": "".join(buf)})
                    buf = []
                out.append({"t": "emote", "url": url, "name": word})
            else:
                buf.append(word)
        if buf:
            out.append({"t": "text", "v": "".join(buf)})
    return out


def badge_list(badges: list[dict]) -> list[dict]:
    out = []
    for b in badges or []:
        key = f"{b.get('set_id')}/{b.get('id')}"
        hit = ASSETS.badges.get(key)
        if hit and hit.get("url"):
            out.append(hit)
    return out


# --------------------------------------------------------------------------
# message normalisation
# --------------------------------------------------------------------------

def twitch_emote_url(emote_id: str, animated: bool = False) -> str:
    fmt = "animated" if animated else "default"
    return f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/{fmt}/dark/2.0"


def _should_hide(login: str, text: str) -> bool:
    cc = CONFIG["chat"]
    hidden = {u.strip().lower() for u in str(cc.get("hide_users", "")).split(",") if u.strip()}
    if login.lower() in hidden:
        return True
    if cc.get("hide_commands") and text.strip().startswith("!"):
        return True
    return False


def normalise_eventsub_chat(ev: dict) -> dict | None:
    msg = ev.get("message", {}) or {}
    text = msg.get("text", "")
    login = ev.get("chatter_user_login", "")
    if _should_hide(login, text):
        return None

    frags: list[dict] = []
    for f in msg.get("fragments", []) or []:
        ftype = f.get("type")
        if ftype == "emote" and f.get("emote"):
            emote = f["emote"]
            animated = "animated" in (emote.get("format") or [])
            frags.append({"t": "emote", "url": twitch_emote_url(emote["id"], animated), "name": f.get("text", "")})
        elif ftype == "cheermote" and f.get("cheermote"):
            frags.append({"t": "cheer", "v": f.get("text", "")})
        elif ftype == "mention":
            frags.append({"t": "mention", "v": f.get("text", "")})
        else:
            frags.append({"t": "text", "v": f.get("text", "")})

    badges = ev.get("badges") or []
    sets = {b.get("set_id") for b in badges}
    reply = None
    if ev.get("reply"):
        reply = {
            "user": ev["reply"].get("parent_user_name", ""),
            "text": ev["reply"].get("parent_message_body", ""),
        }

    return {
        "type": "chat",
        "id": ev.get("message_id") or secrets.token_hex(8),
        "ts": time.time(),
        "user": {
            "login": login,
            "name": ev.get("chatter_user_name") or login,
            "color": ev.get("color") or "",
            "badges": badge_list(badges),
        },
        "flags": {
            "broadcaster": "broadcaster" in sets,
            "mod": "moderator" in sets,
            "vip": "vip" in sets,
            "sub": "subscriber" in sets,
            "first": ev.get("message_type") == "user_intro",
        },
        "bits": (ev.get("cheer") or {}).get("bits", 0),
        "reply": reply,
        "text": text,
        "fragments": apply_third_party(frags),
    }


IRC_LINE = re.compile(r"^(?:@(?P<tags>[^ ]+) )?:(?P<nick>[^!]+)![^ ]+ PRIVMSG #[^ ]+ :(?P<text>.*)$")


def parse_irc_tags(raw: str) -> dict[str, str]:
    out = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v.replace(r"\s", " ").replace(r"\:", ";").replace(r"\\", "\\")
    return out


def normalise_irc_chat(line: str) -> dict | None:
    m = IRC_LINE.match(line)
    if not m:
        return None
    tags = parse_irc_tags(m.group("tags") or "")
    text = m.group("text")
    login = m.group("nick")
    if _should_hide(login, text):
        return None

    # twitch emotes come as "id:start-end,start-end/id:start-end", codepoint indexed
    chars = list(text)
    spans: list[tuple[int, int, str]] = []
    for chunk in (tags.get("emotes") or "").split("/"):
        if ":" not in chunk:
            continue
        eid, ranges = chunk.split(":", 1)
        for rng in ranges.split(","):
            if "-" in rng:
                a, b = rng.split("-", 1)
                try:
                    spans.append((int(a), int(b), eid))
                except ValueError:
                    pass
    spans.sort()

    frags: list[dict] = []
    cursor = 0
    for start, end, eid in spans:
        if start > cursor:
            frags.append({"t": "text", "v": "".join(chars[cursor:start])})
        name = "".join(chars[start:end + 1])
        frags.append({"t": "emote", "url": twitch_emote_url(eid), "name": name})
        cursor = end + 1
    if cursor < len(chars):
        frags.append({"t": "text", "v": "".join(chars[cursor:])})
    if not frags:
        frags = [{"t": "text", "v": text}]

    badge_pairs = []
    for b in (tags.get("badges") or "").split(","):
        if "/" in b:
            sid, ver = b.split("/", 1)
            badge_pairs.append({"set_id": sid, "id": ver})
    sets = {b["set_id"] for b in badge_pairs}

    return {
        "type": "chat",
        "id": tags.get("id") or secrets.token_hex(8),
        "ts": time.time(),
        "user": {
            "login": login,
            "name": tags.get("display-name") or login,
            "color": tags.get("color") or "",
            "badges": badge_list(badge_pairs),
        },
        "flags": {
            "broadcaster": "broadcaster" in sets,
            "mod": tags.get("mod") == "1" or "moderator" in sets,
            "vip": "vip" in sets,
            "sub": tags.get("subscriber") == "1",
            "first": tags.get("first-msg") == "1",
        },
        "bits": int(tags.get("bits") or 0),
        "reply": None,
        "text": text,
        "fragments": apply_third_party(frags),
    }


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------

def build_alert(kind: str, *, user="", amount="", tier="", months="", reward="", message="") -> dict | None:
    rule = CONFIG["alerts"].get(kind)
    if not rule or not rule.get("on"):
        return None
    if kind == "cheer":
        try:
            if int(amount or 0) < int(rule.get("min_amount", 1)):
                return None
        except (TypeError, ValueError):
            pass
    fields = {
        "user": user,
        "amount": amount,
        "tier": tier,
        "months": months,
        "reward": reward,
        "message": message,
    }

    def fill(tpl: str) -> str:
        try:
            return tpl.format(**fields)
        except Exception:
            return tpl

    return {
        "type": "event",
        "kind": kind,
        "id": secrets.token_hex(8),
        "ts": time.time(),
        "title": fill(rule.get("title", kind)),
        "body": fill(rule.get("body", "")),
        "message": message if CONFIG["events"].get("show_user_message") else "",
        "duration": float(rule.get("duration") or CONFIG["events"]["default_duration"]),
        "clip": rule.get("clip", ""),
        "user": user,
        "amount": amount,
    }




async def dispatch_alert(alert: dict | None, msg_id: str | None = None) -> None:
    if not alert:
        return
    await ALERT_QUEUE.enqueue(alert, msg_id)


async def forward_chat(msg: dict) -> None:
    fwd = (CONFIG.get("forward_url") or "").strip()
    if not fwd:
        return
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            await c.post(fwd, json=msg)
    except Exception:
        pass


# --------------------------------------------------------------------------
# !so shoutout command
# --------------------------------------------------------------------------

SHOUTOUT_TARGET = re.compile(r"^[A-Za-z0-9_]{2,25}$")


async def lookup_user(login: str) -> dict | None:
    r = await helix("GET", "/users", params={"login": login})
    if r.status_code != 200:
        return None
    data = r.json().get("data", [])
    return data[0] if data else None


async def handle_command(login: str, text: str, is_broadcaster: bool, is_mod: bool) -> None:
    """Called for every raw chat line (both sources), even ones the overlay
    hides as commands. Cheap parse; the real work runs as a task."""
    so = CONFIG.get("shoutout") or {}
    if not so.get("on"):
        return
    parts = (text or "").strip().split()
    if len(parts) < 2 or parts[0].lower() != str(so.get("command") or "!so").lower():
        return
    if not (is_broadcaster or (so.get("who", "mods") == "mods" and is_mod)):
        return
    target = parts[1].strip().lstrip("@").rstrip(",").lower()
    if not SHOUTOUT_TARGET.match(target):
        return
    STATE.note(f"shoutout: {login} -> {target}")
    asyncio.create_task(_do_shoutout(target))


async def _do_shoutout(target: str) -> None:
    so = CONFIG.get("shoutout") or {}
    scopes = SECRETS.data.get("scopes", [])
    display, target_id = target, ""

    if SECRETS.is_authed():
        user = await lookup_user(target)
        if user:
            target_id = user.get("id", "")
            display = user.get("display_name") or target

    # 1) the official shoutout banner (needs the channel to be live)
    if so.get("native", True) and SECRETS.is_authed():
        if "moderator:manage:shoutouts" not in scopes:
            STATE.note("shoutout: token lacks moderator:manage:shoutouts - reconnect Twitch in the panel to grant it")
        elif target_id and STATE.channel_id:
            r = await helix("POST", "/chat/shoutouts", params={
                "from_broadcaster_id": STATE.channel_id,
                "to_broadcaster_id": target_id,
                "moderator_id": SECRETS.user_id,
            })
            if r.status_code == 204:
                STATE.note(f"shoutout: official /shoutout sent for {display}")
            else:
                reason = ""
                try:
                    reason = r.json().get("message", "")
                except Exception:
                    pass
                STATE.note(f"shoutout: official /shoutout failed ({r.status_code} {reason}) - usually means the stream is offline")

    # 2) a plain chat line, so there's something visible even without the banner
    template = (so.get("message") or "").strip()
    if template and SECRETS.is_authed() and STATE.channel_id:
        if "user:write:chat" not in scopes:
            STATE.note("shoutout: token lacks user:write:chat - reconnect Twitch in the panel to grant it")
        else:
            try:
                text = template.format(name=display, login=target,
                                       url=f"https://twitch.tv/{target}")
            except Exception:
                text = template
            r = await helix("POST", "/chat/messages", json_body={
                "broadcaster_id": STATE.channel_id,
                "sender_id": SECRETS.user_id,
                "message": text,
            })
            if r.status_code not in (200, 204):
                STATE.note(f"shoutout: chat message failed ({r.status_code})")
    elif template and not SECRETS.is_authed():
        STATE.note("shoutout: not signed in, skipping the chat message (clip still plays)")

    # 3) a random clip of theirs on the Clips overlay - ephemeral, not queued.
    # Called in-process (both modules live in the same app), so it works even
    # when something else is squatting on localhost.
    if so.get("clip", True):
        try:
            from clips import play_shoutout
        except ImportError:
            STATE.note("shoutout: Clips module not installed - no clip to play")
            return
        try:
            d = await play_shoutout(target, int(so.get("clip_count") or 2))
            if d.get("ok"):
                STATE.note(f"shoutout: playing {len(d.get('clips') or [1])} random {display} clip(s)")
            else:
                STATE.note(f"shoutout: clip playback skipped ({d.get('error')})")
        except Exception as exc:
            STATE.note(f"shoutout: clip playback failed: {exc}")


# --------------------------------------------------------------------------
# EventSub client
# --------------------------------------------------------------------------

SUB_PLAN = [
    ("channel.chat.message", "1", "chat"),
    ("channel.chat.clear", "1", "chat"),
    ("channel.chat.message_delete", "1", "chat"),
    ("channel.follow", "2", "mod"),
    ("channel.subscribe", "1", "bc"),
    ("channel.subscription.message", "1", "bc"),
    ("channel.subscription.gift", "1", "bc"),
    ("channel.cheer", "1", "bc"),
    ("channel.raid", "1", "raid"),
    ("channel.channel_points_custom_reward_redemption.add", "1", "bc"),
    ("channel.hype_train.begin", "2", "bc"),
    ("stream.online", "1", "bc"),
    ("stream.offline", "1", "bc"),
]


def _condition(shape: str, channel_id: str) -> dict:
    if shape == "chat":
        return {"broadcaster_user_id": channel_id, "user_id": SECRETS.user_id}
    if shape == "mod":
        return {"broadcaster_user_id": channel_id, "moderator_user_id": SECRETS.user_id}
    if shape == "raid":
        return {"to_broadcaster_user_id": channel_id}
    return {"broadcaster_user_id": channel_id}


async def resolve_channel_id(login: str) -> str:
    if not login:
        return ""
    r = await helix("GET", "/users", params={"login": login})
    if r.status_code != 200:
        STATE.note(f"could not look up channel '{login}' ({r.status_code})")
        return ""
    data = r.json().get("data", [])
    return data[0]["id"] if data else ""


async def subscribe_all(session_id: str, channel_id: str) -> None:
    STATE.subs_ok, STATE.subs_failed = [], []
    for stype, version, shape in SUB_PLAN:
        body = {
            "type": stype,
            "version": version,
            "condition": _condition(shape, channel_id),
            "transport": {"method": "websocket", "session_id": session_id},
        }
        r = await helix("POST", "/eventsub/subscriptions", json_body=body)
        if r.status_code in (200, 202):
            STATE.subs_ok.append(stype)
        else:
            reason = ""
            try:
                reason = r.json().get("message", "")
            except Exception:
                pass
            STATE.subs_failed.append(f"{stype} ({r.status_code} {reason})".strip())
    STATE.note(f"eventsub: {len(STATE.subs_ok)} ok, {len(STATE.subs_failed)} failed")
    await HUB.broadcast_status()


async def handle_notification(stype: str, ev: dict, msg_id: str | None = None) -> None:
    if stype == "channel.chat.message":
        # Command detection runs on the raw event: normalisation returns None
        # for "!" messages when the overlay hides commands.
        sets = {b.get("set_id") for b in (ev.get("badges") or [])}
        await handle_command(ev.get("chatter_user_login", ""),
                             (ev.get("message") or {}).get("text", ""),
                             "broadcaster" in sets, "moderator" in sets)
        msg = normalise_eventsub_chat(ev)
        if msg:
            await HUB.to_chat(msg)
            await HUB.to_panel({"type": "chat_preview", "message": msg})
            await forward_chat(msg)
        return

    if stype == "channel.chat.clear":
        await HUB.to_chat({"type": "clear"})
        return

    if stype == "channel.chat.message_delete":
        await HUB.to_chat({"type": "delete", "id": ev.get("message_id")})
        return

    user = ev.get("user_name") or ev.get("from_broadcaster_user_name") or ""

    if stype == "channel.follow":
        await dispatch_alert(build_alert("follow", user=user), msg_id)

    elif stype == "channel.subscribe":
        await dispatch_alert(build_alert("subscribe", user=user, tier=str(int(ev.get("tier", "1000")) // 1000)), msg_id)

    elif stype == "channel.subscription.message":
        await dispatch_alert(build_alert(
            "resub",
            user=user,
            tier=str(int(ev.get("tier", "1000")) // 1000),
            months=str(ev.get("cumulative_months", "")),
            message=(ev.get("message") or {}).get("text", ""),
        ), msg_id)

    elif stype == "channel.subscription.gift":
        await dispatch_alert(build_alert(
            "subgift",
            user="Anonymous" if ev.get("is_anonymous") else user,
            amount=str(ev.get("total", 1)),
            tier=str(int(ev.get("tier", "1000")) // 1000),
        ), msg_id)

    elif stype == "channel.cheer":
        await dispatch_alert(build_alert(
            "cheer",
            user="Anonymous" if ev.get("is_anonymous") else user,
            amount=str(ev.get("bits", 0)),
            message=ev.get("message", ""),
        ), msg_id)

    elif stype == "channel.raid":
        await dispatch_alert(build_alert(
            "raid",
            user=ev.get("from_broadcaster_user_name", ""),
            amount=str(ev.get("viewers", 0)),
        ), msg_id)

    elif stype == "channel.channel_points_custom_reward_redemption.add":
        await dispatch_alert(build_alert(
            "redeem",
            user=user,
            reward=(ev.get("reward") or {}).get("title", ""),
            message=ev.get("user_input", ""),
        ), msg_id)

    elif stype == "channel.hype_train.begin":
        await dispatch_alert(build_alert("hypetrain", amount=str(ev.get("level", 1))), msg_id)

    elif stype == "stream.online":
        await dispatch_alert(build_alert("online"), msg_id)

    elif stype == "stream.offline":
        await dispatch_alert(build_alert("offline"), msg_id)



async def queue_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        if not ALERT_QUEUE.queue:
            await asyncio.sleep(0.5)
            continue

        async with ALERT_QUEUE.lock:
            alert = ALERT_QUEUE.queue.pop(0)
            ALERT_QUEUE.playing = alert
            ALERT_QUEUE.save()

        await HUB.to_panel({"type": "queue", "queue": ALERT_QUEUE.snapshot()})

        # Clear the ack event *before* dispatching to avoid race conditions with short clips
        ALERT_QUEUE.ack_event.clear()

        await HUB.to_events(alert)
        await HUB.to_panel({"type": "event", "event": alert})

        clip = (alert.get("clip") or "").strip()
        if clip:
            base = CONFIG.get("hexcast_url", "http://localhost:4747").rstrip("/")
            try:
                async with httpx.AsyncClient(timeout=6) as c:
                    await c.get(f"{base}/api/play/{urllib.parse.quote(clip)}")
            except Exception as exc:
                STATE.note(f"clip trigger failed for '{clip}': {exc}")

        fwd = (CONFIG.get("forward_url") or "").strip()
        if fwd:
            try:
                async with httpx.AsyncClient(timeout=6) as c:
                    await c.post(fwd, json=alert)
            except Exception:
                pass

        # Wait for ack from overlay with timeout
        try:
            await asyncio.wait_for(ALERT_QUEUE.ack_event.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass

        async with ALERT_QUEUE.lock:
            ALERT_QUEUE.playing = None

        await HUB.to_panel({"type": "queue", "queue": ALERT_QUEUE.snapshot()})


async def eventsub_loop(stop: asyncio.Event) -> None:
    url = EVENTSUB_WS
    backoff = 2
    while not stop.is_set():
        try:
            async with websockets.connect(url, max_size=2 ** 22) as ws:
                backoff = 2
                async for raw in ws:
                    if stop.is_set():
                        break
                    data = json.loads(raw)
                    meta = data.get("metadata", {})
                    payload = data.get("payload", {})
                    mtype = meta.get("message_type")

                    if mtype == "session_welcome":
                        session_id = payload["session"]["id"]
                        STATE.source = "eventsub"
                        STATE.connected = True
                        STATE.last_error = ""
                        STATE.note("eventsub connected")
                        await subscribe_all(session_id, STATE.channel_id)
                        await HUB.broadcast_status()

                    elif mtype == "notification":
                        stype = meta.get("subscription_type") or payload.get("subscription", {}).get("type", "")
                        await handle_notification(stype, payload.get("event", {}) or {}, meta.get("message_id"))

                    elif mtype == "session_reconnect":
                        url = payload["session"]["reconnect_url"]
                        STATE.note("eventsub reconnect requested")
                        break

                    elif mtype == "revocation":
                        sub = payload.get("subscription", {})
                        STATE.note(f"subscription revoked: {sub.get('type')} ({sub.get('status')})")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            STATE.connected = False
            STATE.last_error = str(exc)
            STATE.note(f"eventsub dropped: {exc}")
            await HUB.broadcast_status()
            url = EVENTSUB_WS
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# --------------------------------------------------------------------------
# anonymous IRC fallback (chat only, no auth needed)
# --------------------------------------------------------------------------

async def irc_loop(channel: str, stop: asyncio.Event) -> None:
    backoff = 2
    while not stop.is_set():
        try:
            async with websockets.connect(IRC_WS) as ws:
                await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await ws.send(f"NICK justinfan{secrets.randbelow(90000) + 10000}")
                await ws.send(f"JOIN #{channel.lower()}")
                STATE.source = "irc"
                STATE.connected = True
                STATE.last_error = ""
                STATE.note(f"anonymous chat connected to #{channel}")
                await HUB.broadcast_status()
                backoff = 2

                async for raw in ws:
                    if stop.is_set():
                        break
                    for line in str(raw).split("\r\n"):
                        if not line:
                            continue
                        if line.startswith("PING"):
                            await ws.send("PONG :tmi.twitch.tv")
                            continue
                        if "PRIVMSG" in line:
                            mm = IRC_LINE.match(line)
                            if mm:
                                irc_tags = parse_irc_tags(mm.group("tags") or "")
                                irc_sets = {b.split("/", 1)[0]
                                            for b in (irc_tags.get("badges") or "").split(",") if b}
                                await handle_command(
                                    mm.group("nick"), mm.group("text"),
                                    "broadcaster" in irc_sets,
                                    irc_tags.get("mod") == "1" or "moderator" in irc_sets)
                            msg = normalise_irc_chat(line)
                            if msg:
                                await HUB.to_chat(msg)
                                await HUB.to_panel({"type": "chat_preview", "message": msg})
                                await forward_chat(msg)
                        elif "CLEARCHAT" in line:
                            await HUB.to_chat({"type": "clear"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            STATE.connected = False
            STATE.last_error = str(exc)
            STATE.note(f"anonymous chat dropped: {exc}")
            await HUB.broadcast_status()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# --------------------------------------------------------------------------
# supervisor
# --------------------------------------------------------------------------

class Runner:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.task_queue: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self.started = False

    async def restart(self) -> None:
        async with self.lock:
            await self._stop()
            self.stop = asyncio.Event()
            self.task = asyncio.create_task(self._run(self.stop))
            self.task_queue = asyncio.create_task(queue_loop(self.stop))
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
        if self.task_queue:
            self.task_queue.cancel()
            try:
                await self.task_queue
            except (asyncio.CancelledError, Exception):
                pass
            self.task_queue = None
        STATE.connected = False
        STATE.source = "none"

    async def _run(self, stop: asyncio.Event) -> None:
        channel = (CONFIG.get("channel") or SECRETS.user_login or "").strip().lstrip("#")
        if not channel:
            STATE.note("no channel set - open the Twitch panel and enter your channel name")
            await HUB.broadcast_status()
            return

        STATE.channel_login = channel

        if SECRETS.is_authed():
            if not await validate_token():
                await refresh_token()
                await validate_token()

        if SECRETS.is_authed():
            cid = await resolve_channel_id(channel)
            if cid:
                STATE.channel_id = cid
                await ASSETS.load(cid)
                await HUB.broadcast_status()
                await eventsub_loop(stop)
                return
            STATE.note("channel lookup failed, falling back to anonymous chat")

        # no auth (or lookup failed): chat-only via anonymous IRC.
        # Channel-specific emote/badge lookups need a numeric id, so only the
        # global 7TV/BTTV/FFZ sets load here. Channel emotes arrive after sign-in.
        await ASSETS.load(STATE.channel_id or "")
        await HUB.broadcast_status()
        await irc_loop(channel, stop)

    async def ensure_started(self) -> None:
        if not self.started:
            await self.restart()


RUNNER = Runner()


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/twitch", tags=["twitch"])
_oauth_states: dict[str, float] = {}


def _redirect_uri(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    # Twitch only allows plain http for the literal host "localhost" — the
    # loopback IP gets rejected, so normalise it before handing it over.
    for loopback in ("://127.0.0.1", "://[::1]"):
        base = base.replace(loopback, "://localhost")
    return base + "/twitch/auth/callback"


# The panel and overlays are read from disk on every request, so edits go live
# on refresh - but only if the browser (and OBS's CEF) doesn't serve a cached
# copy. Send no-store so a plain refresh always gets the current file.
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel(request: Request):
    await RUNNER.ensure_started()
    return HTMLResponse(_read_static("twitch_panel.html").replace("__REDIRECT_URI__", _redirect_uri(request)),
                        headers=_NOCACHE)


@router.get("/chat", response_class=HTMLResponse)
async def chat_overlay():
    await RUNNER.ensure_started()
    return HTMLResponse(_read_static("twitch_chat.html"), headers=_NOCACHE)


@router.get("/events", response_class=HTMLResponse)
async def events_overlay():
    await RUNNER.ensure_started()
    return HTMLResponse(_read_static("twitch_events.html"), headers=_NOCACHE)


@router.get("/boot.js")
async def boot_js():
    return Response(_read_static("twitch_boot.js"), media_type="application/javascript",
                    headers=_NOCACHE)



@router.get("/api/queue")
async def api_get_queue():
    return ALERT_QUEUE.snapshot()

@router.post("/api/queue/skip")
async def api_skip_queue():
    ALERT_QUEUE.skip()
    return {"ok": True}

@router.post("/api/queue/remove")
async def api_remove_queue(request: Request):
    body = await request.json()
    alert_id = body.get("id")
    if not alert_id:
        return JSONResponse({"error": "id required"}, status_code=400)
    removed = await ALERT_QUEUE.remove(alert_id)
    return {"ok": removed}

@router.post("/api/queue/clear")
async def api_clear_queue():
    await ALERT_QUEUE.clear()
    return {"ok": True}


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
    old_channel = CONFIG.get("channel")
    CONFIG = save_config(_deep_merge(CONFIG, incoming))
    await HUB.broadcast_config()
    if CONFIG.get("channel") != old_channel:
        await RUNNER.restart()
    return {"ok": True, "config": CONFIG}


@router.get("/api/backgrounds")
async def api_backgrounds_list():
    """The library of uploaded chat/alert background images."""
    return {"backgrounds": _list_backgrounds()}


@router.post("/api/backgrounds")
async def api_backgrounds_upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in BG_IMAGE_EXTS:
        return JSONResponse(
            {"error": f"use png, jpg, gif, webp or apng (got {ext or 'no extension'})"},
            status_code=400,
        )
    content = await file.read()
    if len(content) > 25 * 1024 * 1024:
        return JSONResponse({"error": "file too big (max 25 MB)"}, status_code=400)
    name = _safe_bg_name(file.filename or "background")
    (OVERLAY_BG_DIR / name).write_bytes(content)
    return {"ok": True, "name": name, "url": f"/media/overlays/{name}",
            "backgrounds": _list_backgrounds()}


@router.post("/api/backgrounds/delete")
async def api_backgrounds_delete(request: Request):
    body = await request.json()
    # .name strips any path, so a crafted "name" can't escape the folder.
    name = Path(str(body.get("name") or "")).name
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    (OVERLAY_BG_DIR / name).unlink(missing_ok=True)
    return {"ok": True, "backgrounds": _list_backgrounds()}


@router.post("/api/credentials")
async def api_credentials(request: Request):
    body = await request.json()
    cid = (body.get("client_id") or "").strip()
    csec = (body.get("client_secret") or "").strip()
    if cid:
        SECRETS.data["client_id"] = cid
    if csec:
        SECRETS.data["client_secret"] = csec
    SECRETS.save()
    return {"ok": True, "has_credentials": bool(SECRETS.client_id and SECRETS.client_secret)}


@router.get("/auth/login")
async def auth_login(request: Request):
    if not (SECRETS.client_id and SECRETS.client_secret):
        return JSONResponse({"error": "Add your Twitch client ID and secret first."}, status_code=400)
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = time.time()
    params = {
        "client_id": SECRETS.client_id,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "force_verify": "true",
    }
    return RedirectResponse(f"{TWITCH_ID}/authorize?{urllib.parse.urlencode(params)}")


@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return HTMLResponse(f"<body style='font:16px system-ui;padding:40px'>Twitch returned an error: {error} - {error_description}<br><a href='/twitch'>Back to the panel</a></body>")
    if state not in _oauth_states:
        return HTMLResponse("<body style='font:16px system-ui;padding:40px'>That login link expired. <a href='/twitch'>Start again</a></body>")
    _oauth_states.pop(state, None)
    try:
        await exchange_code(code, _redirect_uri(request))
    except Exception as exc:
        return HTMLResponse(f"<body style='font:16px system-ui;padding:40px'>Token exchange failed: {exc}<br><a href='/twitch'>Back to the panel</a></body>")

    if not CONFIG.get("channel"):
        CONFIG["channel"] = SECRETS.user_login
        save_config(CONFIG)
    STATE.note(f"signed in as {SECRETS.user_login}")
    await RUNNER.restart()
    return HTMLResponse("<body style='font:16px system-ui;padding:40px;background:#0b0b10;color:#eee'>Connected. <a style='color:#ff3b30' href='/twitch'>Back to the panel</a><script>setTimeout(()=>location.href='/twitch',900)</script></body>")


@router.post("/auth/logout")
async def auth_logout():
    SECRETS.clear_tokens()
    await RUNNER.restart()
    return {"ok": True}


@router.post("/api/reconnect")
async def api_reconnect():
    await RUNNER.restart()
    return {"ok": True}


@router.post("/api/test/chat")
async def api_test_chat(request: Request):
    body = await request.json() if await request.body() else {}
    text = body.get("text") or "Testing the overlay Kappa"
    # Test messages count as the broadcaster, so "!so somechannel" here
    # exercises the shoutout end to end without needing live chat.
    await handle_command("hexcast", text, True, False)
    msg = {
        "type": "chat",
        "id": secrets.token_hex(8),
        "ts": time.time(),
        "user": {"login": "hexcast", "name": body.get("user") or "HexCast", "color": "#ff3b30", "badges": []},
        "flags": {"broadcaster": True, "mod": False, "vip": False, "sub": False, "first": False},
        "bits": 0,
        "reply": None,
        "text": text,
        "fragments": apply_third_party([{"t": "text", "v": text}]),
    }
    await HUB.to_chat(msg)
    await HUB.to_panel({"type": "chat_preview", "message": msg})
    return {"ok": True}


@router.post("/api/test/event")
async def api_test_event(request: Request):
    body = await request.json() if await request.body() else {}
    kind = body.get("kind", "follow")
    samples = {
        "follow": dict(user="TestViewer"),
        "subscribe": dict(user="TestViewer", tier="1"),
        "resub": dict(user="TestViewer", tier="1", months="12", message="Love the stream"),
        "subgift": dict(user="TestViewer", amount="5", tier="1"),
        "cheer": dict(user="TestViewer", amount="500", message="Take my bits"),
        "raid": dict(user="TestStreamer", amount="42"),
        "redeem": dict(user="TestViewer", reward="Hex sing a song", message="please"),
        "hypetrain": dict(amount="3"),
        "online": dict(),
        "offline": dict(),
    }
    alert = build_alert(kind, **samples.get(kind, {}))
    if not alert:
        return JSONResponse({"error": f"'{kind}' alerts are switched off."}, status_code=400)
    await dispatch_alert(alert, None)
    return {"ok": True}


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    HUB.chat.add(ws)
    await RUNNER.ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.chat.discard(ws)


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    HUB.events.add(ws)
    await RUNNER.ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        while True:
            text = await ws.receive_text()
            try:
                msg = json.loads(text)
                if msg.get("type") == "alert_complete":
                    alert_id = msg.get("id")
                    if ALERT_QUEUE.playing and ALERT_QUEUE.playing.get("id") == alert_id:
                        ALERT_QUEUE.ack_event.set()
            except Exception:
                pass
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.events.discard(ws)


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    HUB.panel.add(ws)
    await RUNNER.ensure_started()
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

async def start_twitch() -> None:
    """Call from inside hexcast's lifespan startup."""
    await RUNNER.ensure_started()


async def stop_twitch() -> None:
    """Call from inside hexcast's lifespan shutdown."""
    await RUNNER._stop()


def attach_twitch(app, port: int = 4747) -> None:
    """Mount the Twitch routes onto an existing FastAPI app.

    Note: hexcast.py builds its app with FastAPI(lifespan=...), which makes
    Starlette ignore add_event_handler("startup"). The connection therefore
    starts lazily on the first panel or overlay request. To connect at boot
    instead, call start_twitch()/stop_twitch() from inside that lifespan.
    """
    app.include_router(router)
    print(f"  Twitch panel:        http://localhost:{port}/twitch", flush=True)
    print(f"  Twitch chat source:  http://localhost:{port}/twitch/chat", flush=True)
    print(f"  Twitch alert source: http://localhost:{port}/twitch/events", flush=True)
