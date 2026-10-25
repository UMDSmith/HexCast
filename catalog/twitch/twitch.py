"""
HexCast - Twitch module
=======================

A Hexcast plugin (see plugin.py): install it from the + tab and it adds:
    http://localhost:4747/twitch          -> control panel (connection + overlay settings)
    http://localhost:4747/twitch/chat     -> chat overlay (OBS browser source)
    http://localhost:4747/twitch/events   -> alert/event overlay (OBS browser source)

Everything lives under /twitch/* so it cannot collide with existing routes.

The connection to Twitch - the sign-in, the channels, EventSub and the anonymous chat fallback - is the
shared twitch_core library (Chat Timers and Giveaways use it too). This module listens on it: chat lines go
to the chat overlay (and the !so command), events become alerts in a queue the alert overlay plays.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response

from hexcast_core import paths, running_plugin
from hexcast_plugins.twitch_core import core as tc

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent          # this plugin's folder
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = paths.CONFIG_DIR
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _read_static(name: str) -> str:
    """Read a static file on every request, so edits show up without a restart."""
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The Twitch module needs "
            f"twitch_panel.html, twitch_chat.html, twitch_events.html, "
            f"twitch_boot.js and twitch_overlay.css in the plugin's static/ folder."
        )
    return path.read_text(encoding="utf-8")

CONFIG_PATH = CONFIG_DIR / "twitch.json"

# Uploaded chat/alert background images live here and are served by hexcast's
# existing /media StaticFiles mount, so the overlays can reach them by URL.
# The media root is the core's (hexcast_core.paths honours SOUNDBOARD_MEDIA_DIR).
MEDIA_ROOT = paths.MEDIA_DIR
OVERLAY_BG_DIR = MEDIA_ROOT / "overlays"
OVERLAY_BG_DIR.mkdir(parents=True, exist_ok=True)
BG_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".apng"}
_SAFE_BG_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_bg_name(name: str, fallback: str = "background") -> str:
    """Sanitise an uploaded filename to a safe, flat name inside OVERLAY_BG_DIR."""
    stem = _SAFE_BG_NAME.sub("_", Path(name).stem).strip("._-") or fallback
    return stem[:60] + Path(name).suffix.lower()


def _list_backgrounds() -> list[dict]:
    out = []
    for p in sorted(OVERLAY_BG_DIR.glob("*")):
        if p.is_file() and p.suffix.lower() in BG_IMAGE_EXTS:
            out.append({"name": p.name, "url": f"/media/overlays/{p.name}"})
    return out


# Fonts the streamer uploads for the overlays (a font that is not on Google Fonts, or that must work with no
# internet). They sit in a folder of their own inside the overlay library, so the picture listing above never
# sees them, and the overlays reach them by URL like the pictures.
OVERLAY_FONT_DIR = OVERLAY_BG_DIR / "fonts"
FONT_FORMATS = {".ttf": "truetype", ".otf": "opentype", ".woff": "woff", ".woff2": "woff2"}
FONT_MAGIC = {b"\x00\x01\x00\x00", b"true", b"OTTO", b"wOFF", b"wOF2"}      # first four bytes of a real font file
MAX_FONT_BYTES = 10 * 1024 * 1024
MAX_CUSTOM_CSS = 20000


def _font_family(file_name: str) -> str:
    """The name a font is picked by: its file name without the extension, '_' and '-' read as spaces."""
    return " ".join(re.split(r"[_\-\s]+", Path(file_name).stem)).strip() or Path(file_name).stem


def _list_fonts() -> list[dict]:
    out = []
    if OVERLAY_FONT_DIR.is_dir():
        for p in sorted(OVERLAY_FONT_DIR.glob("*")):
            fmt = FONT_FORMATS.get(p.suffix.lower())
            if p.is_file() and fmt:
                out.append({"name": _font_family(p.name), "file": p.name, "format": fmt,
                            "url": f"/media/overlays/fonts/{p.name}"})
    return out

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def _alert(on: bool, duration: int, title: str, body: str, **extra: Any) -> dict:
    """One alert's default rule. Besides what fires and what it says, each alert can look its own way: a colour
    for its label and edge (empty = the overlay's accent), its own entrance (empty = the overlay's), a picture
    shown with it (image_pos top | bottom | left | right, image_size px tall) and a different background picture."""
    return {"on": on, "duration": duration, "title": title, "body": body, "clip": "",
            "accent_color": "", "animation": "", "image": "", "image_pos": "top", "image_size": 120,
            "bg_image_url": "", **extra}


DEFAULT_CONFIG: dict[str, Any] = {
    # What a chat / alert source shows when its URL has no ?channel=...: just the primary, or every channel.
    "overlay_channels": "primary",
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
        # How the picture of the "image" style sits in its box: bg_fit cover | contain | stretch | tile | center,
        # bg_position center | top | bottom | left | right, bg_image_opacity 0-1 (the colour shows through),
        # bg_image_dim 0-1 (darkens it so text stays readable), bg_shadow a soft shadow under the box (px, 0 = none).
        "bg_fit": "cover",
        "bg_position": "center",
        "bg_image_opacity": 1,
        "bg_image_dim": 0,
        "bg_shadow": 0,
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
        "show_channel": False,          # a tag with the channel's label before each line (when several channels show)
        "emote_size": 34,
        "third_party_emotes": True,
        "hide_commands": True,
        "hide_users": "nightbot, streamelements, streamlabs, moobot, fossabot",
        "animation": "slide",
        "width_percent": 100,
        "highlight_first": True,
        "highlight_color": "#ff3b30",
        # More type options. The username has its own font (empty = "same as the message"), size and letter
        # spacing, apart from the message text; the shadow_* values draw the drop shadow when `shadow` is on.
        "name_font_family": "",
        "name_size": 28,                    # px
        "name_letter_spacing": 0,           # px
        "name_transform": "none",           # none | uppercase | lowercase | capitalize
        "text_transform": "none",
        "letter_spacing": 0,                # px, the message text
        "badge_scale": 100,                 # % of the name size
        "shadow_color": "#000000",
        "shadow_opacity": 0.85,
        "shadow_blur": 6,
        "shadow_x": 0,
        "shadow_y": 2,
        # A coloured bar down the left of each line from a broadcaster, mod, VIP or subscriber.
        "mark_roles": False,
        "broadcaster_color": "#e91916",
        "mod_color": "#00ad03",
        "vip_color": "#e005b9",
        "sub_color": "#8205ff",
        "role_bar_width": 4,
        "fade_edge": 0,                     # px: the oldest lines fade out over this much of the chat box's edge
        "anim_duration": 0.3,               # seconds for a line's entrance
        "animation_out": "fade",            # fade | zoom | up | down | none (how a line leaves)
        "custom_css": "",                   # the streamer's own CSS, added to the overlay page
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
        "bg_fit": "cover",
        "bg_position": "center",
        "bg_image_opacity": 1,
        "bg_image_dim": 0,
        "bg_shadow": 0,
        # Placement: same scaled-1920x1080 model as chat. box_* positions/sizes
        # the alert box; bg_box_* positions/sizes the background (width snaps to
        # the image when an image style is used). The alert - and its background -
        # only appears when an event fires, then fades per fade_in / fade_out.
        "box_enabled": False,
        "box_x": 33, "box_y": 30, "box_w": 34, "box_h": 34,
        "bg_full": False,
        "bg_box_x": 30, "bg_box_y": 26, "bg_box_w": 40, "bg_box_h": 42,
        "fade_in": 0.45,
        "fade_out": 0.5,
        "outline": True,
        "outline_color": "#000000",
        "align": "center",
        "valign": "middle",
        "animation": "pop",
        "default_duration": 6,
        "gap_between": 0.6,
        "show_user_message": True,
        # More type options. The label (title) can have its own font; an empty colour means the accent (title) or
        # the text colour (body, note).
        "title_font_family": "",
        "title_transform": "uppercase",     # none | uppercase | lowercase | capitalize
        "title_spacing": 0.14,              # em
        "title_weight": 800,
        "title_color": "",
        "body_weight": 800,
        "body_color": "",
        "text_transform": "none",           # the body line
        "letter_spacing": 0,                # px
        "note_color": "",
        "note_italic": True,
        "note_font_family": "",             # the viewer's message: empty = the alert's font
        "note_size": 26,                    # px
        "outline_width": 2,
        "shadow": True,
        "shadow_color": "#000000",
        "shadow_opacity": 0.6,
        "shadow_blur": 10,
        "shadow_x": 0,
        "shadow_y": 3,
        "pad_x": 34,
        "pad_y": 22,
        "accent_bar": "auto",               # auto | top | bottom | left | none: the coloured edge on an alert
                                            # (auto = a bar on top in the classic mode, none in placement mode)
        "accent_bar_width": 3,
        "animation_out": "fade",            # fade | zoom | up | down | none
        "custom_css": "",
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
        "follow": _alert(True, 5, "New follower", "{user}"),
        "subscribe": _alert(True, 7, "New sub", "{user} - tier {tier}"),
        "resub": _alert(True, 8, "Resub", "{user} - {months} months"),
        "subgift": _alert(True, 7, "Gifted subs", "{user} gifted {amount}", min_amount=1),
        "cheer": _alert(True, 7, "Bits", "{user} cheered {amount}", min_amount=1),
        "raid": _alert(True, 9, "Raid", "{user} raided with {amount}", min_amount=1),
        "redeem": _alert(True, 6, "Redeemed", "{user}: {reward}"),
        "hypetrain": _alert(True, 6, "Hype train", "Level {amount}"),
        "online": _alert(False, 5, "Live", "Stream started"),
        "offline": _alert(False, 5, "Offline", "Stream ended"),
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


def _overlay_setting(cfg: dict) -> dict:
    """What a chat / alert source with no ?channel= in its URL shows: the primary channel, or every channel."""
    if cfg.get("overlay_channels") not in ("primary", "all"):
        cfg["overlay_channels"] = "primary"
    return cfg


def _upgrade_text(raw: dict) -> dict:
    """A config saved before the username and the message text were sized apart: the name was a percentage
    of the message size and shared its letter spacing, and an alert's viewer message used the label's size.
    The new settings start at what those drew, so the overlays look the same."""
    chat = raw.get("chat")
    if isinstance(chat, dict) and "name_size" not in chat:
        size = _number(chat.get("font_size"), DEFAULT_CONFIG["chat"]["font_size"])
        chat["name_size"] = round(size * _number(chat.pop("name_scale", 100), 100) / 100)
        chat.setdefault("name_letter_spacing", chat.get("letter_spacing", 0))
    events = raw.get("events")
    if isinstance(events, dict) and "note_size" not in events:
        events["note_size"] = round(_number(events.get("sub_font_size"), DEFAULT_CONFIG["events"]["sub_font_size"]))
    return raw


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_config() -> dict:
    raw = {}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    for key in ("channel", "channels"):             # the channels live in the shared connection now (twitch_core)
        raw.pop(key, None)
    return _overlay_setting(_deep_merge(DEFAULT_CONFIG, _upgrade_text(raw)))


def save_config(cfg: dict) -> dict:
    merged = _overlay_setting(_deep_merge(DEFAULT_CONFIG, cfg))
    for key in ("channel", "channels"):
        merged.pop(key, None)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


CONFIG = load_config()


def public_config() -> dict:
    """The settings as the API shows them, with the channels (the shared connection's): `channels` is the list,
    `channel` the primary's login - what older panels and bots read and write."""
    rows = [dict(r) for r in tc.CONFIG.get("channels") or []]
    return {**CONFIG, "channel": rows[0]["login"] if rows else "", "channels": rows}


def status() -> dict:
    """The connection as this module always reported it: the primary channel at the top level (what Hexcast
    showed before it could watch several - the top-bar dot and bots read these), then every channel, the
    accounts signed in and the log."""
    conn = tc.STATE.snapshot()
    rows = conn["channels"]
    p = rows[0] if rows else {}
    dflt = tc.default_account()
    return {
        "source": p.get("source", "none"),
        "connected": bool(p.get("connected")),
        "channel_id": p.get("id", ""),
        "channel_login": p.get("login", ""),
        "authed": dflt is not None,
        "has_credentials": conn["has_credentials"],
        "bot_login": dflt.user_login if dflt else "",
        "scopes": dflt.scopes if dflt else [],
        "subs_ok": p.get("subs_ok", []),
        "subs_failed": p.get("subs_failed", []),
        "last_error": p.get("last_error", ""),
        "channels": rows,
        "accounts": [{"login": a["login"], "scopes": a["scopes"]} for a in conn["accounts"]],
        "overlay_channels": CONFIG.get("overlay_channels", "primary"),
        "log": conn["log"],
    }


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
        if (CONFIG_DIR / "twitch_queue.json").exists():
            try:
                data = json.loads((CONFIG_DIR / "twitch_queue.json").read_text())
                self.queue = data.get("queue", [])
                self.seen_msg_ids = {k: None for k in data.get("seen", [])}
            except Exception:
                self.queue = []
                self.seen_msg_ids = {}

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            seen_list = list(self.seen_msg_ids.keys())[-1000:]
            (CONFIG_DIR / "twitch_queue.json").write_text(json.dumps({
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
        # overlay socket -> the ?channel= its page asked for ("" = whatever the panel's overlay setting says)
        self.chat: dict[WebSocket, str] = {}
        self.events: dict[WebSocket, str] = {}
        self.panel: set[WebSocket] = set()

    @staticmethod
    def wants(flt: str, channel: str | None) -> bool:
        """Does an overlay that asked for `flt` ("all", "primary" or a login) get something from `channel`?
        (None: the message is not about one channel - a settings update - and goes to everyone.)"""
        if channel is None:
            return True
        want = (flt or CONFIG.get("overlay_channels") or "primary").lower()
        if want == "all":
            return True
        if want == "primary":
            want = tc.primary_login()
        return want == channel.lower()

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

    async def _send_overlays(self, group: dict[WebSocket, str], payload: dict) -> tuple[int, int]:
        """To the overlays that want this payload's channel. Returns (overlays connected, overlays sent to)."""
        dead = []
        text = json.dumps(payload)
        channel = payload.get("channel") or None        # a payload with no channel is not about one
        matched = 0
        for ws, flt in list(group.items()):
            if not self.wants(flt, channel):
                continue
            try:
                await ws.send_text(text)
                matched += 1
            except Exception:
                dead.append(ws)
        for ws in dead:
            group.pop(ws, None)
        return len(group), matched

    async def to_chat(self, payload: dict) -> tuple[int, int]:
        return await self._send_overlays(self.chat, payload)

    async def to_events(self, payload: dict) -> tuple[int, int]:
        return await self._send_overlays(self.events, payload)

    async def to_panel(self, payload: dict) -> None:
        await self._send(self.panel, payload)

    async def broadcast_config(self) -> None:
        await self.to_chat({"type": "config", "config": CONFIG})
        await self.to_events({"type": "config", "config": CONFIG})

    async def broadcast_status(self) -> None:
        await self.to_panel({"type": "status", "status": status()})


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

    async def load(self, channel_id: str, acct: tc.Account | None = None, name: str = "") -> None:
        self.emotes = {}
        self.badges = {}
        await asyncio.gather(
            self._seventv(channel_id),
            self._bttv(channel_id),
            self._ffz(channel_id),
            self._badges(channel_id, acct),
            return_exceptions=True,
        )
        self.loaded_for = channel_id
        tc.note(f"assets loaded{' for ' + name if name else ''}: "
                   f"{len(self.emotes)} 3rd-party emotes, {len(self.badges)} badges")

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

    async def _badges(self, channel_id: str, acct: tc.Account | None) -> None:
        if acct is None or not acct.is_authed():
            return
        for path, params in (
            ("/chat/badges/global", None),
            ("/chat/badges", {"broadcaster_id": channel_id}),
        ):
            r = await tc.helix("GET", path, acct=acct, params=params)
            if r.status_code != 200:
                continue
            for s in r.json().get("data", []):
                for v in s.get("versions", []):
                    key = f"{s['set_id']}/{v['id']}"
                    self.badges[key] = {
                        "url": v.get("image_url_2x") or v.get("image_url_1x"),
                        "title": v.get("title") or s["set_id"],
                    }


ASSETS: dict[str, Assets] = {}          # channel login -> its emotes and badges


def assets_for(ch: tc.ChannelRT) -> Assets:
    return ASSETS.setdefault(ch.login, Assets())


async def on_channel(ch: tc.ChannelRT, acct: tc.Account | None) -> None:
    """A channel connected: load its emotes and badges. Without a login only the global 7TV/BTTV/FFZ sets load
    (the channel's own need its numeric id); they arrive after sign-in."""
    await assets_for(ch).load(ch.id or "", acct, ch.name if len(tc.STATE.channels) > 1 else "")


def apply_third_party(fragments: list[dict], assets: Assets) -> list[dict]:
    """Split plain-text fragments on whitespace and swap in a channel's 3rd-party emotes."""
    if not CONFIG["chat"].get("third_party_emotes") or not assets.emotes:
        return fragments
    out: list[dict] = []
    for frag in fragments:
        if frag.get("t") != "text":
            out.append(frag)
            continue
        buf: list[str] = []
        for word in re.split(r"(\s+)", frag.get("v", "")):
            url = assets.emotes.get(word)
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


def badge_list(badges: list[dict], assets: Assets) -> list[dict]:
    out = []
    for b in badges or []:
        key = f"{b.get('set_id')}/{b.get('id')}"
        hit = assets.badges.get(key)
        if hit and hit.get("url"):
            out.append(hit)
    return out


# --------------------------------------------------------------------------
# chat lines on the overlay
# --------------------------------------------------------------------------

def _should_hide(login: str, text: str) -> bool:
    cc = CONFIG["chat"]
    hidden = {u.strip().lower() for u in str(cc.get("hide_users", "")).split(",") if u.strip()}
    if login.lower() in hidden:
        return True
    if cc.get("hide_commands") and text.strip().startswith("!"):
        return True
    return False


def overlay_line(msg: dict, ch: tc.ChannelRT) -> dict | None:
    """A chat line (as twitch_core hands it over) the way the chat overlay draws it and the Forward URL gets it:
    hidden users and commands left out, badges as pictures, the channel's 3rd-party emotes swapped in."""
    user = msg.get("user") or {}
    if _should_hide(user.get("login", ""), msg.get("text", "")):
        return None
    assets = assets_for(ch)
    return {
        "type": "chat",
        "channel": msg.get("channel", ch.login),
        "channel_label": msg.get("channel_label", ch.name),
        "id": msg.get("id") or secrets.token_hex(8),
        "ts": msg.get("ts") or time.time(),
        "user": {
            "login": user.get("login", ""),
            "name": user.get("name") or user.get("login", ""),
            "color": user.get("color", ""),
            "badges": badge_list(msg.get("badges") or [], assets),
        },
        "flags": msg.get("flags") or {},
        "bits": msg.get("bits", 0),
        "reply": msg.get("reply"),
        "text": msg.get("text", ""),
        "fragments": apply_third_party(msg.get("fragments") or [], assets),
    }


def normalise_eventsub_chat(ev: dict, ch: tc.ChannelRT) -> dict | None:
    return overlay_line(tc.chat_from_eventsub(ev, ch), ch)


def normalise_irc_chat(line: str, ch: tc.ChannelRT) -> dict | None:
    msg = tc.chat_from_irc(line, ch)
    return overlay_line(msg, ch) if msg else None


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------

def build_alert(kind: str, *, user="", amount="", tier="", months="", reward="", message="",
                ch: tc.ChannelRT | None = None) -> dict | None:
    rule = CONFIG["alerts"].get(kind)
    if not rule or not rule.get("on"):
        return None
    if kind in ("cheer", "subgift", "raid"):      # bits, gifted subs, raiders: ignore the small ones
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
        "channel": ch.name if ch else "",
    }

    def fill(tpl: str) -> str:
        try:
            return tpl.format(**fields)
        except Exception:
            return tpl

    return {
        "type": "event",
        "kind": kind,
        "channel": ch.login if ch else "",
        "channel_label": ch.name if ch else "",
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


def _where(ch: tc.ChannelRT) -> str:
    return tc.where(ch)


async def handle_command(login: str, text: str, is_broadcaster: bool, is_mod: bool, ch: tc.ChannelRT) -> None:
    """Called for every raw chat line (both sources), even ones the overlay hides as commands, with the
    channel it was typed in. Cheap parse; the real work runs as a task."""
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
    tc.note(f"shoutout{_where(ch)}: {login} -> {target}")
    asyncio.create_task(_do_shoutout(target, ch))


async def _do_shoutout(target: str, ch: tc.ChannelRT) -> None:
    """A shoutout typed in `ch`: the banner and the chat line go to that channel, from the account Hexcast
    acts as there (the channel's own, or the default one - which then has to moderate it)."""
    so = CONFIG.get("shoutout") or {}
    acct = tc.account_for(ch.login)
    scopes = acct.scopes if acct else []
    where = _where(ch)
    display, target_id = target, ""

    if acct:
        user = await tc.lookup_user(target, acct)
        if user:
            target_id = user.get("id", "")
            display = user.get("display_name") or target

    # 1) the official shoutout banner (needs the channel to be live)
    if so.get("native", True) and acct:
        if "moderator:manage:shoutouts" not in scopes:
            tc.note(f"shoutout{where}: token lacks moderator:manage:shoutouts - reconnect Twitch in the panel to grant it")
        elif target_id and ch.id:
            r = await tc.helix("POST", "/chat/shoutouts", acct=acct, params={
                "from_broadcaster_id": ch.id,
                "to_broadcaster_id": target_id,
                "moderator_id": acct.user_id,
            })
            if r.status_code == 204:
                tc.note(f"shoutout{where}: official /shoutout sent for {display}")
            else:
                reason = ""
                try:
                    reason = r.json().get("message", "")
                except Exception:
                    pass
                tc.note(f"shoutout{where}: official /shoutout failed ({r.status_code} {reason}) - usually means the stream is offline")

    # 2) a plain chat line, so there's something visible even without the banner
    template = (so.get("message") or "").strip()
    if template and acct and ch.id:
        if "user:write:chat" not in scopes:
            tc.note(f"shoutout{where}: token lacks user:write:chat - reconnect Twitch in the panel to grant it")
        else:
            try:
                text = template.format(name=display, login=target,
                                       url=f"https://twitch.tv/{target}")
            except Exception:
                text = template
            r = await tc.helix("POST", "/chat/messages", acct=acct, json_body={
                "broadcaster_id": ch.id,
                "sender_id": acct.user_id,
                "message": text,
            })
            if r.status_code not in (200, 204):
                tc.note(f"shoutout{where}: chat message failed ({r.status_code})")
    elif template and not acct:
        tc.note(f"shoutout{where}: not signed in, skipping the chat message (clip still plays)")

    # 3) a random clip of theirs on the Clips overlay - ephemeral, not queued.
    # Called in-process (both plugins live in the same app), so it works even
    # when something else is squatting on localhost. Clips is an optional
    # plugin: when it is not running there is simply no clip to play.
    if so.get("clip", True):
        clips = running_plugin("clips")
        play_shoutout = getattr(clips, "play_shoutout", None)
        if play_shoutout is None:
            tc.note(f"shoutout{where}: Clips plugin not installed - no clip to play")
            return
        try:
            d = await play_shoutout(target, int(so.get("clip_count") or 2))
            if d.get("ok"):
                tc.note(f"shoutout{where}: playing {len(d.get('clips') or [1])} random {display} clip(s)")
            else:
                tc.note(f"shoutout{where}: clip playback skipped ({d.get('error')})")
        except Exception as exc:
            tc.note(f"shoutout{where}: clip playback failed: {exc}")


# --------------------------------------------------------------------------
# what the connection reports
# --------------------------------------------------------------------------

async def on_chat(msg: dict, ch: tc.ChannelRT) -> None:
    """Every chat line of every watched channel. Commands are looked for on the raw line, before the overlay
    drops "!" lines (when it hides commands)."""
    flags = msg.get("flags") or {}
    await handle_command((msg.get("user") or {}).get("login", ""), msg.get("text", ""),
                         bool(flags.get("broadcaster")), bool(flags.get("mod")), ch)
    line = overlay_line(msg, ch)
    if line:
        await HUB.to_chat(line)
        await HUB.to_panel({"type": "chat_preview", "message": line})
        await forward_chat(line)


async def on_event(stype: str, ev: dict, msg_id: str | None, ch: tc.ChannelRT) -> None:
    """Every other notification: chat clears and deletes for the overlay, the rest become alerts."""
    if stype == "channel.chat.clear":
        await HUB.to_chat({"type": "clear", "channel": ch.login})
        return

    if stype == "channel.chat.message_delete":
        await HUB.to_chat({"type": "delete", "id": ev.get("message_id"), "channel": ch.login})
        return

    user = ev.get("user_name") or ev.get("from_broadcaster_user_name") or ""

    if stype == "channel.follow":
        await dispatch_alert(build_alert("follow", user=user, ch=ch), msg_id)

    elif stype == "channel.subscribe":
        await dispatch_alert(build_alert("subscribe", user=user, tier=str(int(ev.get("tier", "1000")) // 1000), ch=ch), msg_id)

    elif stype == "channel.subscription.message":
        await dispatch_alert(build_alert(
            "resub",
            user=user,
            tier=str(int(ev.get("tier", "1000")) // 1000),
            months=str(ev.get("cumulative_months", "")),
            message=(ev.get("message") or {}).get("text", ""),
            ch=ch,
        ), msg_id)

    elif stype == "channel.subscription.gift":
        await dispatch_alert(build_alert(
            "subgift",
            user="Anonymous" if ev.get("is_anonymous") else user,
            amount=str(ev.get("total", 1)),
            tier=str(int(ev.get("tier", "1000")) // 1000),
            ch=ch,
        ), msg_id)

    elif stype == "channel.cheer":
        await dispatch_alert(build_alert(
            "cheer",
            user="Anonymous" if ev.get("is_anonymous") else user,
            amount=str(ev.get("bits", 0)),
            message=ev.get("message", ""),
            ch=ch,
        ), msg_id)

    elif stype == "channel.raid":
        await dispatch_alert(build_alert(
            "raid",
            user=ev.get("from_broadcaster_user_name", ""),
            amount=str(ev.get("viewers", 0)),
            ch=ch,
        ), msg_id)

    elif stype == "channel.channel_points_custom_reward_redemption.add":
        await dispatch_alert(build_alert(
            "redeem",
            user=user,
            reward=(ev.get("reward") or {}).get("title", ""),
            message=ev.get("user_input", ""),
            ch=ch,
        ), msg_id)

    elif stype == "channel.hype_train.begin":
        await dispatch_alert(build_alert("hypetrain", amount=str(ev.get("level", 1)), ch=ch), msg_id)

    elif stype == "stream.online":
        await dispatch_alert(build_alert("online", ch=ch), msg_id)

    elif stype == "stream.offline":
        await dispatch_alert(build_alert("offline", ch=ch), msg_id)


async def handle_notification(stype: str, ev: dict, msg_id: str | None, ch: tc.ChannelRT) -> None:
    """An EventSub notification as Twitch sends it, the way twitch_core passes it on."""
    if stype == "channel.chat.message":
        await on_chat(tc.chat_from_eventsub(ev, ch), ch)
    else:
        await on_event(stype, ev, msg_id, ch)


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

        clients, shown = await HUB.to_events(alert)
        await HUB.to_panel({"type": "event", "event": alert})

        clip = (alert.get("clip") or "").strip()
        if clip:
            base = CONFIG.get("hexcast_url", "http://localhost:4747").rstrip("/")
            try:
                async with httpx.AsyncClient(timeout=6) as c:
                    await c.get(f"{base}/api/play/{urllib.parse.quote(clip)}")
            except Exception as exc:
                tc.note(f"clip trigger failed for '{clip}': {exc}")

        fwd = (CONFIG.get("forward_url") or "").strip()
        if fwd:
            try:
                async with httpx.AsyncClient(timeout=6) as c:
                    await c.post(fwd, json=alert)
            except Exception:
                pass

        # Wait for ack from overlay with timeout - unless overlays are connected and none of them shows
        # this alert's channel (sources filtered to other channels): nothing will ever ack it
        if not clients or shown:
            try:
                await asyncio.wait_for(ALERT_QUEUE.ack_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                pass

        async with ALERT_QUEUE.lock:
            ALERT_QUEUE.playing = None

        await HUB.to_panel({"type": "queue", "queue": ALERT_QUEUE.snapshot()})



# --------------------------------------------------------------------------
# start / stop
# --------------------------------------------------------------------------

class Loops:
    """The alert queue's player. (The connection itself is twitch_core's.)"""

    def __init__(self) -> None:
        self.queue: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.assets: set[asyncio.Task] = set()

    async def ensure_started(self) -> None:
        await tc.ensure_started()
        if self.queue is None or self.queue.done():
            self.stop = asyncio.Event()
            self.queue = asyncio.create_task(queue_loop(self.stop))
            # channels the connection had up before this module started (it was updated or enabled on its own)
            # came up unheard: load their emotes and badges now
            for ch in list(tc.STATE.channels.values()):
                have = ASSETS.get(ch.login)
                if ch.connected and not (have and (have.emotes or have.badges)):
                    acct = tc.account_for(ch.login) if ch.source == "eventsub" else None
                    task = asyncio.create_task(on_channel(ch, acct))
                    self.assets.add(task)
                    task.add_done_callback(self.assets.discard)

    async def shutdown(self) -> None:
        self.stop.set()
        for task in list(self.assets):
            task.cancel()
        if self.queue is not None:
            self.queue.cancel()
            try:
                await self.queue
            except (asyncio.CancelledError, Exception):
                pass
            self.queue = None


LOOPS = Loops()


async def ensure_started() -> None:
    """Connect (if nothing has yet) and run the alert queue."""
    await LOOPS.ensure_started()


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/twitch", tags=["twitch"])
# (the sign-in pages, /twitch/api/credentials and /twitch/api/reconnect are twitch_core's: the same paths as ever)


# The panel and overlays are read from disk on every request, so edits go live
# on refresh - but only if the browser (and OBS's CEF) doesn't serve a cached
# copy. Send no-store so a plain refresh always gets the current file.
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel(request: Request):
    await ensure_started()
    return HTMLResponse(_read_static("twitch_panel.html"), headers=_NOCACHE)


@router.get("/chat", response_class=HTMLResponse)
async def chat_overlay():
    await ensure_started()
    return HTMLResponse(_read_static("twitch_chat.html"), headers=_NOCACHE)


@router.get("/events", response_class=HTMLResponse)
async def events_overlay():
    await ensure_started()
    return HTMLResponse(_read_static("twitch_events.html"), headers=_NOCACHE)


@router.get("/boot.js")
async def boot_js():
    return Response(_read_static("twitch_boot.js"), media_type="application/javascript",
                    headers=_NOCACHE)


@router.get("/overlay.css")
async def overlay_css():
    """The look both overlays share: the background styles, the entrance and exit animations."""
    return Response(_read_static("twitch_overlay.css"), media_type="text/css", headers=_NOCACHE)



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
    return status()


@router.get("/api/config")
async def api_get_config():
    return public_config()


def _with_primary(rows: list[dict], login: Any) -> list[dict]:
    """The channel list with its first entry's login replaced (the old `channel` setting); empty drops it."""
    login = tc.clean_login(login)
    rows = [dict(r) for r in rows]
    if not login:
        return rows[1:]
    if rows:
        rows[0]["login"] = login
    else:
        rows = [{"login": login}]
    return rows


def _style_problem(incoming: dict) -> str:
    """Why an overlay style update cannot be taken (custom CSS that is not text or is far too long), or ''."""
    for scope in ("chat", "events"):
        css = (incoming.get(scope) or {}).get("custom_css") if isinstance(incoming.get(scope), dict) else None
        if css is None:
            continue
        if not isinstance(css, str):
            return "custom_css is text"
        if len(css) > MAX_CUSTOM_CSS:
            return f"custom CSS is limited to {MAX_CUSTOM_CSS} characters"
    return ""


@router.post("/api/config")
async def api_set_config(request: Request):
    """The overlay settings; `channels` (or the old `channel`) goes on to the shared connection."""
    global CONFIG
    incoming = await request.json()
    if not isinstance(incoming, dict):
        return JSONResponse({"error": "send a JSON object"}, status_code=400)
    if "channel" in incoming and "channels" not in incoming:
        # the old single-channel way of asking: it changes the primary
        incoming = {**incoming, "channels": _with_primary(tc.CONFIG["channels"], incoming["channel"])}
    rows = incoming.pop("channels", None)
    incoming.pop("channel", None)
    problem = (tc.channel_problem(rows) if rows is not None else "") or _style_problem(incoming)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)
    CONFIG = save_config(_deep_merge(CONFIG, incoming))
    await HUB.broadcast_config()
    if rows is not None:
        await tc.set_channels(rows)                  # only the channels that changed reconnect
    await HUB.broadcast_status()
    return {"ok": True, "config": public_config()}


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


@router.get("/api/fonts")
async def api_fonts_list():
    """The fonts uploaded for the overlays: [{name, file, format, url}]. `name` is what a font is picked by."""
    return {"fonts": _list_fonts()}


@router.post("/api/fonts")
async def api_fonts_upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in FONT_FORMATS:
        return JSONResponse({"error": f"use ttf, otf, woff or woff2 (got {ext or 'no extension'})"},
                            status_code=400)
    content = await file.read()
    if len(content) > MAX_FONT_BYTES:
        return JSONResponse({"error": "file too big (max 10 MB)"}, status_code=400)
    if content[:4] not in FONT_MAGIC:
        return JSONResponse({"error": "that does not look like a font file"}, status_code=400)
    name = _safe_bg_name(file.filename or "font", fallback="font")
    OVERLAY_FONT_DIR.mkdir(parents=True, exist_ok=True)
    (OVERLAY_FONT_DIR / name).write_bytes(content)
    return {"ok": True, "name": _font_family(name), "file": name, "fonts": _list_fonts()}


@router.post("/api/fonts/delete")
async def api_fonts_delete(request: Request):
    body = await request.json()
    name = Path(str(body.get("file") or body.get("name") or "")).name       # .name strips any path
    if not name:
        return JSONResponse({"error": "file required"}, status_code=400)
    (OVERLAY_FONT_DIR / name).unlink(missing_ok=True)
    return {"ok": True, "fonts": _list_fonts()}


def _test_channel(login: Any) -> tc.ChannelRT | None:
    """The channel a test message or alert is for: a watched one by login, else the primary. Before any
    channel is set up the tests still work, as a channel of their own. None: that login is not watched."""
    if tc.clean_login(login):
        return tc.STATE.channel(login)
    return tc.STATE.primary() or tc.ChannelRT(tc.primary_login() or "hexcast")


@router.post("/api/test/chat")
async def api_test_chat(request: Request):
    body = await request.json() if await request.body() else {}
    ch = _test_channel(body.get("channel"))
    if ch is None:
        return JSONResponse({"error": f"'{body.get('channel')}' is not a channel Hexcast watches"}, status_code=400)
    text = body.get("text") or "Testing the overlay Kappa"
    # Test messages count as the broadcaster, so "!so somechannel" here
    # exercises the shoutout end to end without needing live chat.
    await handle_command("hexcast", text, True, False, ch)
    msg = {
        "type": "chat",
        "channel": ch.login,
        "channel_label": ch.name,
        "id": secrets.token_hex(8),
        "ts": time.time(),
        "user": {"login": "hexcast", "name": body.get("user") or "HexCast", "color": "#ff3b30", "badges": []},
        "flags": {"broadcaster": True, "mod": False, "vip": False, "sub": False, "first": False},
        "bits": 0,
        "reply": None,
        "text": text,
        "fragments": apply_third_party([{"t": "text", "v": text}], assets_for(ch)),
    }
    await HUB.to_chat(msg)
    await HUB.to_panel({"type": "chat_preview", "message": msg})
    return {"ok": True}


@router.post("/api/test/event")
async def api_test_event(request: Request):
    body = await request.json() if await request.body() else {}
    ch = _test_channel(body.get("channel"))
    if ch is None:
        return JSONResponse({"error": f"'{body.get('channel')}' is not a channel Hexcast watches"}, status_code=400)
    kind = body.get("kind", "follow")
    samples = {
        "follow": dict(user="TestViewer"),
        "subscribe": dict(user="TestViewer", tier="1"),
        "resub": dict(user="TestViewer", tier="1", months="12", message="Love the stream"),
        "subgift": dict(user="TestViewer", amount="5", tier="1"),
        "cheer": dict(user="TestViewer", amount="500", message="Take my bits"),
        "raid": dict(user="TestStreamer", amount="42"),
        "redeem": dict(user="TestViewer", reward="Hex sing a song", message="a sea shanty about the cat who runs this stream"),
        "hypetrain": dict(amount="3"),
        "online": dict(),
        "offline": dict(),
    }
    alert = build_alert(kind, ch=ch, **samples.get(kind, {}))
    if not alert:
        return JSONResponse({"error": f"'{kind}' alerts are switched off."}, status_code=400)
    await dispatch_alert(alert, None)
    return {"ok": True}


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, channel: str = ""):
    """The chat overlay's socket. ?channel=login shows one channel, ?channel=all every channel; without
    it the panel's "overlays show" setting decides."""
    await ws.accept()
    HUB.chat[ws] = tc.clean_login(channel)[:25]
    await ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "config", "config": CONFIG}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.chat.pop(ws, None)


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket, channel: str = ""):
    """The alert overlay's socket; ?channel= works as on the chat overlay's."""
    await ws.accept()
    HUB.events[ws] = tc.clean_login(channel)[:25]
    await ensure_started()
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
        HUB.events.pop(ws, None)


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await ws.accept()
    HUB.panel.add(ws)
    await ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "status", "status": status()}))
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
    """Connect and run the alert queue (plugin.py runs this once Hexcast is serving)."""
    await ensure_started()


async def stop_twitch() -> None:
    """Stop the alert queue. (The connection is twitch_core's: it stops with that plugin.)"""
    await LOOPS.shutdown()


def attach_twitch(app, port: int = 4747) -> None:
    """Mount the Twitch routes onto an existing FastAPI app."""
    app.include_router(router)
    print(f"  Twitch panel:        http://localhost:{port}/twitch", flush=True)
    print(f"  Twitch chat source:  http://localhost:{port}/twitch/chat", flush=True)
    print(f"  Twitch alert source: http://localhost:{port}/twitch/events", flush=True)
