"""
HexCast - Twitch module
=======================

A Hexcast plugin (see plugin.py): install it from the + tab and it adds:
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

from hexcast_core import paths, running_plugin

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
SECRETS_PATH = CONFIG_DIR / "twitch_secrets.json"

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

def _alert(on: bool, duration: int, title: str, body: str, **extra: Any) -> dict:
    """One alert's default rule. Besides what fires and what it says, each alert can look its own way: a colour
    for its label and edge (empty = the overlay's accent), its own entrance (empty = the overlay's), a picture
    shown with it (image_pos top | bottom | left | right, image_size px tall) and a different background picture."""
    return {"on": on, "duration": duration, "title": title, "body": body, "clip": "",
            "accent_color": "", "animation": "", "image": "", "image_pos": "top", "image_size": 120,
            "bg_image_url": "", **extra}


DEFAULT_CONFIG: dict[str, Any] = {
    # The channels to watch: [{"login": "mychannel", "label": "", "enabled": true}, ...], the first one the
    # primary. `channel` always mirrors the primary's login (older panels and bots read and write that).
    "channel": "",
    "channels": [],
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
        # More type options. An empty name_font_family means "same as the message"; name_scale is a percentage
        # of the message size; the shadow_* values draw the drop shadow when `shadow` is on.
        "name_font_family": "",
        "name_scale": 100,
        "name_transform": "none",           # none | uppercase | lowercase | capitalize
        "text_transform": "none",
        "letter_spacing": 0,                # px
        "badge_scale": 100,                 # % of the line height
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


MAX_CHANNELS = 5
CHANNEL_LOGIN = re.compile(r"^[a-z0-9_]{3,25}$")


def clean_login(value: Any) -> str:
    return str(value or "").strip().lstrip("#").lower()


def normalise_channels(cfg: dict) -> dict:
    """`channels` is the truth and `channel` mirrors its first entry. A config that only has the old
    `channel` string (every Hexcast before multi-channel) gets a one-entry list."""
    rows = cfg.get("channels") if isinstance(cfg.get("channels"), list) else []
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            row = {"login": row}
        if not isinstance(row, dict):
            continue
        login = clean_login(row.get("login"))
        if not CHANNEL_LOGIN.match(login) or login in seen:
            continue
        seen.add(login)
        out.append({"login": login, "label": str(row.get("label") or "").strip()[:24],
                    "enabled": row.get("enabled") is not False})
        if len(out) >= MAX_CHANNELS:
            break
    legacy = clean_login(cfg.get("channel"))
    if not out and CHANNEL_LOGIN.match(legacy):
        out.append({"login": legacy, "label": "", "enabled": True})
    cfg["channels"] = out
    cfg["channel"] = out[0]["login"] if out else ""
    if cfg.get("overlay_channels") not in ("primary", "all"):
        cfg["overlay_channels"] = "primary"
    return cfg


def load_config() -> dict:
    raw = {}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    return normalise_channels(_deep_merge(DEFAULT_CONFIG, raw))


def save_config(cfg: dict) -> dict:
    merged = normalise_channels(_deep_merge(DEFAULT_CONFIG, cfg))
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


CONFIG = load_config()


def configured_channels() -> list[dict]:
    """The channels to watch, primary first. A Hexcast with none set watches the signed-in user's own channel."""
    rows = [dict(r) for r in CONFIG.get("channels") or []]
    if not rows:
        own = next(iter(SECRETS.authed()), None)
        if own:
            rows = [{"login": own.user_login.lower(), "label": "", "enabled": True}]
    return rows


def primary_login() -> str:
    rows = configured_channels()
    return rows[0]["login"] if rows else ""


# --------------------------------------------------------------------------
# secrets / tokens
# --------------------------------------------------------------------------

TOKEN_KEYS = ("access_token", "refresh_token", "expires_at", "user_id", "user_login", "scopes")


class Account:
    """One signed-in Twitch user: a view of its record in the secrets file."""

    def __init__(self, rec: dict) -> None:
        self.rec = rec
        self.lock = asyncio.Lock()          # one token refresh at a time: Twitch rotates the refresh token
        self.gen = 0                        # bumped when the user signs in again: a new token, maybe new scopes

    @property
    def access_token(self) -> str:
        return self.rec.get("access_token", "")

    @property
    def refresh_token(self) -> str:
        return self.rec.get("refresh_token", "")

    @property
    def user_id(self) -> str:
        return self.rec.get("user_id", "")

    @property
    def user_login(self) -> str:
        return self.rec.get("user_login", "")

    @property
    def scopes(self) -> list:
        return self.rec.get("scopes", [])

    def is_authed(self) -> bool:
        return bool(self.access_token and self.user_id)


class Secrets:
    """The Twitch app's client id / secret, and every account signed in (a Hexcast can be signed in as
    several users: the channel's own account gives the full alert set, another user is a chat reader)."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.accounts: dict[str, Account] = {}      # user id -> account, in the order they signed in
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
        recs = self.data.get("accounts")
        recs = {str(k): v for k, v in recs.items() if isinstance(v, dict)} if isinstance(recs, dict) else {}
        legacy = {k: self.data.pop(k) for k in TOKEN_KEYS if k in self.data}
        if legacy.get("access_token") and not recs:         # the secrets file of a one-login Hexcast
            recs = {str(legacy.get("user_id") or "legacy"): legacy}
        self.data["accounts"] = recs                        # (written in this shape the next time anything saves)
        self.accounts = {uid: Account(rec) for uid, rec in recs.items()}

    def save(self) -> None:
        self.data["accounts"] = {uid: a.rec for uid, a in self.accounts.items()}
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

    def authed(self) -> list[Account]:
        return [a for a in self.accounts.values() if a.is_authed()]

    def adopt(self, acct: Account) -> Account:
        """Keep a freshly signed-in account (a second sign-in of the same user replaces the first)."""
        have = self.accounts.get(acct.user_id)
        if have:
            have.rec.clear()
            have.rec.update(acct.rec)
            have.gen += 1
            acct = have
        else:
            self.accounts[acct.user_id] = acct
        self.save()
        return acct

    def clear_tokens(self, login: str = "") -> None:
        """Sign one user out (by login), or everyone."""
        login = login.strip().lower()
        for uid, a in list(self.accounts.items()):
            if not login or a.user_login.lower() == login:
                del self.accounts[uid]
        self.save()


SECRETS = Secrets()


def default_account() -> Account | None:
    """The login used for a channel that has none of its own: the primary channel's own login if it is
    signed in, else the first one that is."""
    authed = SECRETS.authed()
    primary = primary_login()
    return next((a for a in authed if a.user_login.lower() == primary), authed[0] if authed else None)


def account_for(channel: str) -> Account | None:
    """Who Hexcast acts as in a channel: the channel's own account (full alerts, shoutouts as the
    broadcaster), else the default one (its chat, and what a moderator may read there)."""
    channel = channel.lower()
    own = next((a for a in SECRETS.authed() if a.user_login.lower() == channel), None)
    return own or default_account()


async def exchange_code(code: str, redirect_uri: str) -> Account:
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
    acct = Account({"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
                    "expires_at": time.time() + tok.get("expires_in", 3600)})
    if not await validate_token(acct):
        raise RuntimeError("Twitch did not accept the new token")
    return SECRETS.adopt(acct)


async def refresh_token(acct: Account, stale: str = "") -> bool:
    """Refresh an account's token. `stale` is the token the caller was refused with: if another task has
    refreshed the account since (channels can share one), there is nothing left to do."""
    if not acct.refresh_token:
        return False
    async with acct.lock:
        if stale and acct.access_token != stale:
            return True
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{TWITCH_ID}/token",
                data={
                    "client_id": SECRETS.client_id,
                    "client_secret": SECRETS.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": acct.refresh_token,
                },
            )
        if r.status_code != 200:
            STATE.note(f"token refresh failed for {acct.user_login or 'an account'} ({r.status_code}) - "
                       f"reconnect Twitch in the panel")
            return False
        tok = r.json()
        acct.rec["access_token"] = tok["access_token"]
        acct.rec["refresh_token"] = tok.get("refresh_token", acct.refresh_token)
        acct.rec["expires_at"] = time.time() + tok.get("expires_in", 3600)
        SECRETS.save()
        return True


async def validate_token(acct: Account) -> bool:
    if not acct.access_token:
        return False
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{TWITCH_ID}/validate", headers={"Authorization": f"OAuth {acct.access_token}"})
    if r.status_code != 200:
        return False
    d = r.json()
    acct.rec["user_id"] = d.get("user_id", "")
    acct.rec["user_login"] = d.get("login", "")
    acct.rec["scopes"] = d.get("scopes", [])
    if acct in SECRETS.accounts.values():
        SECRETS.save()
    return True


async def helix(method: str, path: str, *, acct: Account, params=None, json_body=None, retry=True) -> httpx.Response:
    stale = acct.access_token
    headers = {
        "Client-Id": SECRETS.client_id,
        "Authorization": f"Bearer {stale}",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(method, f"{HELIX}{path}", headers=headers, params=params, json=json_body)
    if r.status_code == 401 and retry:
        if await refresh_token(acct, stale):
            return await helix(method, path, acct=acct, params=params, json_body=json_body, retry=False)
    return r


# --------------------------------------------------------------------------
# runtime state
# --------------------------------------------------------------------------

class ChannelRT:
    """A watched channel's live connection, and what it has reported."""

    def __init__(self, login: str, label: str = "", enabled: bool = True) -> None:
        self.login = login
        self.label = label
        self.enabled = enabled
        self.id = ""
        self.source = "none"          # none | eventsub | irc
        self.connected = False
        self.account_login = ""       # who Hexcast acts as here: the channel's own account, or the default one
        self.subs_ok: list[str] = []
        self.subs_failed: list[str] = []
        self.last_error = ""
        self.assets = Assets()        # this channel's third-party emotes and badges

    @property
    def name(self) -> str:
        """What a line from this channel is tagged with."""
        return self.label or self.login

    def snapshot(self, primary: bool = False) -> dict:
        acct = account_for(self.login)
        return {
            "login": self.login, "label": self.label, "name": self.name, "enabled": self.enabled,
            "primary": primary, "id": self.id, "source": self.source, "connected": self.connected,
            "account": acct.user_login if acct else "",
            "own_account": bool(acct and acct.user_login.lower() == self.login),
            "subs_ok": self.subs_ok, "subs_failed": self.subs_failed, "last_error": self.last_error,
        }


class State:
    def __init__(self) -> None:
        self.channels: dict[str, ChannelRT] = {}      # login -> runtime, in config order: the first is the primary
        self.log: list[str] = []

    def sync_channels(self, keep: set[str] | frozenset = frozenset()) -> None:
        """A runtime for every configured channel, in config order. The channels in `keep` (their connection
        is running and stays up) keep theirs, with the label and switch refreshed; the rest get a fresh one."""
        new: dict[str, ChannelRT] = {}
        for r in configured_channels():
            rt = self.channels.get(r["login"]) if r["login"] in keep else None
            if rt is None:
                rt = ChannelRT(r["login"], r.get("label", ""), r.get("enabled", True))
            else:
                rt.label, rt.enabled = r.get("label", ""), r.get("enabled", True)
            new[r["login"]] = rt
        self.channels = new

    def primary(self) -> ChannelRT | None:
        return next(iter(self.channels.values()), None)

    def channel(self, login: str = "") -> ChannelRT | None:
        """A watched channel by login (the primary when none is named)."""
        login = clean_login(login)
        return self.channels.get(login) if login else self.primary()

    def note(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        self.log.append(line)
        del self.log[:-60]
        print(f"[twitch] {msg}", flush=True)

    def snapshot(self) -> dict:
        dflt = default_account()
        rows = []
        for i, r in enumerate(configured_channels()):
            rt = self.channels.get(r["login"]) or ChannelRT(r["login"], r.get("label", ""), r.get("enabled", True))
            rows.append(rt.snapshot(primary=i == 0))
        p = rows[0] if rows else {}
        return {
            # the primary channel at the top level: what Hexcast reported before it could watch several
            # (the top-bar dot and bots read these)
            "source": p.get("source", "none"),
            "connected": bool(p.get("connected")),
            "channel_id": p.get("id", ""),
            "channel_login": p.get("login", ""),
            "authed": dflt is not None,
            "has_credentials": bool(SECRETS.client_id and SECRETS.client_secret),
            "bot_login": dflt.user_login if dflt else "",
            "scopes": dflt.scopes if dflt else [],
            "subs_ok": p.get("subs_ok", []),
            "subs_failed": p.get("subs_failed", []),
            "last_error": p.get("last_error", ""),
            # every channel, and every login Hexcast holds
            "channels": rows,
            "accounts": [{"login": a.user_login, "scopes": a.scopes} for a in SECRETS.authed()],
            "overlay_channels": CONFIG.get("overlay_channels", "primary"),
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
            want = primary_login()
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

    async def load(self, channel_id: str, acct: Account | None = None, name: str = "") -> None:
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
        STATE.note(f"assets loaded{' for ' + name if name else ''}: "
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

    async def _badges(self, channel_id: str, acct: Account | None) -> None:
        if acct is None or not acct.is_authed():
            return
        for path, params in (
            ("/chat/badges/global", None),
            ("/chat/badges", {"broadcaster_id": channel_id}),
        ):
            r = await helix("GET", path, acct=acct, params=params)
            if r.status_code != 200:
                continue
            for s in r.json().get("data", []):
                for v in s.get("versions", []):
                    key = f"{s['set_id']}/{v['id']}"
                    self.badges[key] = {
                        "url": v.get("image_url_2x") or v.get("image_url_1x"),
                        "title": v.get("title") or s["set_id"],
                    }


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


def normalise_eventsub_chat(ev: dict, ch: ChannelRT) -> dict | None:
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
        "channel": ch.login,
        "channel_label": ch.name,
        "id": ev.get("message_id") or secrets.token_hex(8),
        "ts": time.time(),
        "user": {
            "login": login,
            "name": ev.get("chatter_user_name") or login,
            "color": ev.get("color") or "",
            "badges": badge_list(badges, ch.assets),
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
        "fragments": apply_third_party(frags, ch.assets),
    }


IRC_LINE = re.compile(r"^(?:@(?P<tags>[^ ]+) )?:(?P<nick>[^!]+)![^ ]+ PRIVMSG #[^ ]+ :(?P<text>.*)$")


def parse_irc_tags(raw: str) -> dict[str, str]:
    out = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v.replace(r"\s", " ").replace(r"\:", ";").replace(r"\\", "\\")
    return out


def normalise_irc_chat(line: str, ch: ChannelRT) -> dict | None:
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
        "channel": ch.login,
        "channel_label": ch.name,
        "id": tags.get("id") or secrets.token_hex(8),
        "ts": time.time(),
        "user": {
            "login": login,
            "name": tags.get("display-name") or login,
            "color": tags.get("color") or "",
            "badges": badge_list(badge_pairs, ch.assets),
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
        "fragments": apply_third_party(frags, ch.assets),
    }


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------

def build_alert(kind: str, *, user="", amount="", tier="", months="", reward="", message="",
                ch: ChannelRT | None = None) -> dict | None:
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


def _where(ch: ChannelRT) -> str:
    """' [beta]' in a log line when Hexcast watches more than one channel, else nothing."""
    return f" [{ch.name}]" if len(STATE.channels) > 1 else ""


async def lookup_user(login: str, acct: Account) -> dict | None:
    r = await helix("GET", "/users", acct=acct, params={"login": login})
    if r.status_code != 200:
        return None
    data = r.json().get("data", [])
    return data[0] if data else None


async def handle_command(login: str, text: str, is_broadcaster: bool, is_mod: bool, ch: ChannelRT) -> None:
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
    STATE.note(f"shoutout{_where(ch)}: {login} -> {target}")
    asyncio.create_task(_do_shoutout(target, ch))


async def _do_shoutout(target: str, ch: ChannelRT) -> None:
    """A shoutout typed in `ch`: the banner and the chat line go to that channel, from the account Hexcast
    acts as there (the channel's own, or the default one - which then has to moderate it)."""
    so = CONFIG.get("shoutout") or {}
    acct = account_for(ch.login)
    scopes = acct.scopes if acct else []
    where = _where(ch)
    display, target_id = target, ""

    if acct:
        user = await lookup_user(target, acct)
        if user:
            target_id = user.get("id", "")
            display = user.get("display_name") or target

    # 1) the official shoutout banner (needs the channel to be live)
    if so.get("native", True) and acct:
        if "moderator:manage:shoutouts" not in scopes:
            STATE.note(f"shoutout{where}: token lacks moderator:manage:shoutouts - reconnect Twitch in the panel to grant it")
        elif target_id and ch.id:
            r = await helix("POST", "/chat/shoutouts", acct=acct, params={
                "from_broadcaster_id": ch.id,
                "to_broadcaster_id": target_id,
                "moderator_id": acct.user_id,
            })
            if r.status_code == 204:
                STATE.note(f"shoutout{where}: official /shoutout sent for {display}")
            else:
                reason = ""
                try:
                    reason = r.json().get("message", "")
                except Exception:
                    pass
                STATE.note(f"shoutout{where}: official /shoutout failed ({r.status_code} {reason}) - usually means the stream is offline")

    # 2) a plain chat line, so there's something visible even without the banner
    template = (so.get("message") or "").strip()
    if template and acct and ch.id:
        if "user:write:chat" not in scopes:
            STATE.note(f"shoutout{where}: token lacks user:write:chat - reconnect Twitch in the panel to grant it")
        else:
            try:
                text = template.format(name=display, login=target,
                                       url=f"https://twitch.tv/{target}")
            except Exception:
                text = template
            r = await helix("POST", "/chat/messages", acct=acct, json_body={
                "broadcaster_id": ch.id,
                "sender_id": acct.user_id,
                "message": text,
            })
            if r.status_code not in (200, 204):
                STATE.note(f"shoutout{where}: chat message failed ({r.status_code})")
    elif template and not acct:
        STATE.note(f"shoutout{where}: not signed in, skipping the chat message (clip still plays)")

    # 3) a random clip of theirs on the Clips overlay - ephemeral, not queued.
    # Called in-process (both plugins live in the same app), so it works even
    # when something else is squatting on localhost. Clips is an optional
    # plugin: when it is not running there is simply no clip to play.
    if so.get("clip", True):
        clips = running_plugin("clips")
        play_shoutout = getattr(clips, "play_shoutout", None)
        if play_shoutout is None:
            STATE.note(f"shoutout{where}: Clips plugin not installed - no clip to play")
            return
        try:
            d = await play_shoutout(target, int(so.get("clip_count") or 2))
            if d.get("ok"):
                STATE.note(f"shoutout{where}: playing {len(d.get('clips') or [1])} random {display} clip(s)")
            else:
                STATE.note(f"shoutout{where}: clip playback skipped ({d.get('error')})")
        except Exception as exc:
            STATE.note(f"shoutout{where}: clip playback failed: {exc}")


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


def _condition(shape: str, channel_id: str, user_id: str) -> dict:
    if shape == "chat":
        return {"broadcaster_user_id": channel_id, "user_id": user_id}
    if shape == "mod":
        return {"broadcaster_user_id": channel_id, "moderator_user_id": user_id}
    if shape == "raid":
        return {"to_broadcaster_user_id": channel_id}
    return {"broadcaster_user_id": channel_id}


async def resolve_channel_id(login: str, acct: Account) -> str:
    if not login:
        return ""
    r = await helix("GET", "/users", acct=acct, params={"login": login})
    if r.status_code != 200:
        STATE.note(f"could not look up channel '{login}' ({r.status_code})")
        return ""
    data = r.json().get("data", [])
    return data[0]["id"] if data else ""


async def subscribe_all(session_id: str, ch: ChannelRT, acct: Account) -> None:
    ch.subs_ok, ch.subs_failed = [], []
    for stype, version, shape in SUB_PLAN:
        body = {
            "type": stype,
            "version": version,
            "condition": _condition(shape, ch.id, acct.user_id),
            "transport": {"method": "websocket", "session_id": session_id},
        }
        r = await helix("POST", "/eventsub/subscriptions", acct=acct, json_body=body)
        if r.status_code in (200, 202):
            ch.subs_ok.append(stype)
        else:
            reason = ""
            try:
                reason = r.json().get("message", "")
            except Exception:
                pass
            ch.subs_failed.append(f"{stype} ({r.status_code} {reason})".strip())
    STATE.note(f"eventsub{_where(ch)}: {len(ch.subs_ok)} ok, {len(ch.subs_failed)} failed")
    await HUB.broadcast_status()


async def handle_notification(stype: str, ev: dict, msg_id: str | None, ch: ChannelRT) -> None:
    if stype == "channel.chat.message":
        # Command detection runs on the raw event: normalisation returns None
        # for "!" messages when the overlay hides commands.
        sets = {b.get("set_id") for b in (ev.get("badges") or [])}
        await handle_command(ev.get("chatter_user_login", ""),
                             (ev.get("message") or {}).get("text", ""),
                             "broadcaster" in sets, "moderator" in sets, ch)
        msg = normalise_eventsub_chat(ev, ch)
        if msg:
            await HUB.to_chat(msg)
            await HUB.to_panel({"type": "chat_preview", "message": msg})
            await forward_chat(msg)
        return

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
                STATE.note(f"clip trigger failed for '{clip}': {exc}")

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


async def eventsub_loop(ch: ChannelRT, acct: Account, stop: asyncio.Event) -> None:
    """One EventSub connection for one channel, as `acct` (Twitch allows three per login)."""
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
                        ch.source = "eventsub"
                        ch.connected = True
                        ch.last_error = ""
                        STATE.note(f"eventsub connected{_where(ch)}")
                        await subscribe_all(session_id, ch, acct)
                        await HUB.broadcast_status()

                    elif mtype == "notification":
                        stype = meta.get("subscription_type") or payload.get("subscription", {}).get("type", "")
                        await handle_notification(stype, payload.get("event", {}) or {}, meta.get("message_id"), ch)

                    elif mtype == "session_reconnect":
                        url = payload["session"]["reconnect_url"]
                        STATE.note(f"eventsub reconnect requested{_where(ch)}")
                        break

                    elif mtype == "revocation":
                        sub = payload.get("subscription", {})
                        STATE.note(f"subscription revoked{_where(ch)}: {sub.get('type')} ({sub.get('status')})")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ch.connected = False
            ch.last_error = str(exc)
            STATE.note(f"eventsub dropped{_where(ch)}: {exc}")
            await HUB.broadcast_status()
            url = EVENTSUB_WS
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# --------------------------------------------------------------------------
# anonymous IRC fallback (chat only, no auth needed)
# --------------------------------------------------------------------------

async def irc_loop(ch: ChannelRT, stop: asyncio.Event) -> None:
    backoff = 2
    while not stop.is_set():
        try:
            async with websockets.connect(IRC_WS) as ws:
                await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                await ws.send(f"NICK justinfan{secrets.randbelow(90000) + 10000}")
                await ws.send(f"JOIN #{ch.login.lower()}")
                ch.source = "irc"
                ch.connected = True
                ch.last_error = ""
                STATE.note(f"anonymous chat connected to #{ch.login}")
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
                                    irc_tags.get("mod") == "1" or "moderator" in irc_sets, ch)
                            msg = normalise_irc_chat(line, ch)
                            if msg:
                                await HUB.to_chat(msg)
                                await HUB.to_panel({"type": "chat_preview", "message": msg})
                                await forward_chat(msg)
                        elif "CLEARCHAT" in line:
                            await HUB.to_chat({"type": "clear", "channel": ch.login})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ch.connected = False
            ch.last_error = str(exc)
            STATE.note(f"anonymous chat dropped (#{ch.login}): {exc}")
            await HUB.broadcast_status()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# --------------------------------------------------------------------------
# supervisor
# --------------------------------------------------------------------------

RETRY_MIN, RETRY_MAX = 2, 60        # seconds a channel waits before trying again when it could not even start


class Runner:
    """One connection task per enabled channel. A channel is only (re)started when something that concerns
    it changed, so editing the beta channel - or signing in as its account - never reconnects the primary.
    restart() is the big hammer: the Reconnect button, and the first start."""

    def __init__(self) -> None:
        self.tasks: dict[str, tuple[tuple, asyncio.Task]] = {}     # login -> (what it was started with, its task)
        self.task_queue: asyncio.Task | None = None
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self.started = False

    @staticmethod
    def _wanted() -> dict[str, tuple]:
        """login -> what a running channel depends on, for every enabled channel: the account it runs as and
        that account's sign-in generation (a new sign-in is a new token, maybe with new scopes)."""
        out: dict[str, tuple] = {}
        for r in configured_channels():
            if r.get("enabled", True):
                a = account_for(r["login"])
                out[r["login"]] = (a.user_id, a.gen) if a else ("", 0)
        return out

    @staticmethod
    async def _cancel(*tasks: asyncio.Task) -> None:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    async def restart(self) -> None:
        async with self.lock:
            await self._stop()
            self.stop = asyncio.Event()
            await self._sync()

    async def sync(self) -> None:
        """Bring the connections in line with the config and the signed-in accounts, touching only the
        channels that changed."""
        async with self.lock:
            await self._sync()

    async def _sync(self) -> None:
        wanted = self._wanted()
        gone = [login for login, (sig, task) in self.tasks.items() if wanted.get(login) != sig or task.done()]
        await self._cancel(*(self.tasks.pop(login)[1] for login in gone))
        STATE.sync_channels(keep=set(self.tasks))
        if self.task_queue is None or self.task_queue.done():
            self.task_queue = asyncio.create_task(queue_loop(self.stop))
        for login, sig in wanted.items():
            if login not in self.tasks:
                self.tasks[login] = (sig, asyncio.create_task(self._watch(STATE.channels[login], self.stop)))
        self.started = True
        if not wanted:
            STATE.note("no channel set - open the Twitch panel and enter your channel name")
        await HUB.broadcast_status()

    async def _stop(self) -> None:
        self.stop.set()
        await self._cancel(*(task for _, task in self.tasks.values()))
        self.tasks.clear()
        if self.task_queue:
            await self._cancel(self.task_queue)
            self.task_queue = None
        for ch in STATE.channels.values():
            ch.connected = False
            ch.source = "none"

    async def _check(self, acct: Account) -> None:
        """Make sure an account's token is good before a channel connects with it (a stale one is refreshed).
        Trouble here is logged and the channel carries on with what it has - it must not stop anything."""
        stale = acct.access_token
        try:
            if not await validate_token(acct):
                await refresh_token(acct, stale)
                await validate_token(acct)
        except Exception as exc:
            STATE.note(f"could not check the login of {acct.user_login or 'an account'}: {exc}")

    async def _watch(self, ch: ChannelRT, stop: asyncio.Event) -> None:
        """One channel, for as long as it runs. If it cannot even start (no network at boot, say) it tries
        again with a growing pause; it never gives up, and nothing it does reaches another channel."""
        wait = RETRY_MIN
        while not stop.is_set():
            try:
                await self._connect(ch, stop)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                ch.connected = False
                ch.last_error = str(exc)
                STATE.note(f"{ch.login} could not start: {exc} - trying again in {wait} s")
                await HUB.broadcast_status()
                await asyncio.sleep(wait)
                wait = min(wait * 2, RETRY_MAX)

    async def _connect(self, ch: ChannelRT, stop: asyncio.Event) -> None:
        """EventSub when there is a login for the channel, else anonymous chat. Runs until `stop`."""
        name = ch.name if len(STATE.channels) > 1 else ""
        acct = account_for(ch.login)
        ch.account_login = acct.user_login if acct else ""
        if acct:
            await self._check(acct)
            cid = await resolve_channel_id(ch.login, acct)
            if cid:
                ch.id = cid
                await ch.assets.load(cid, acct, name)
                await HUB.broadcast_status()
                await eventsub_loop(ch, acct, stop)
                return
            STATE.note(f"channel lookup failed for {ch.login}, falling back to anonymous chat")

        # no login (or the lookup failed): chat-only via anonymous IRC.
        # Channel-specific emote/badge lookups need a numeric id, so only the
        # global 7TV/BTTV/FFZ sets load here. Channel emotes arrive after sign-in.
        await ch.assets.load(ch.id or "", None, name)
        await HUB.broadcast_status()
        await irc_loop(ch, stop)

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
    return STATE.snapshot()


@router.get("/api/config")
async def api_get_config():
    return CONFIG


def _with_primary(rows: list[dict], login: Any) -> list[dict]:
    """The channel list with its first entry's login replaced (the old `channel` setting); empty drops it."""
    login = clean_login(login)
    rows = [dict(r) for r in rows]
    if not login:
        return rows[1:]
    if rows:
        rows[0]["login"] = login
    else:
        rows = [{"login": login}]
    return rows


def _channel_problem(incoming: dict) -> str:
    """Why a config update cannot be taken (a channel name that is not one, too many channels), or ''."""
    rows = incoming.get("channels")
    if rows is None:
        return ""
    if not isinstance(rows, list):
        return "channels is a list of {login, label, enabled}"
    if len(rows) > MAX_CHANNELS:
        return f"at most {MAX_CHANNELS} channels"
    for row in rows:
        login = clean_login(row.get("login") if isinstance(row, dict) else row)
        if not CHANNEL_LOGIN.match(login):
            return f"'{login or row}' is not a Twitch channel name (3-25 letters, digits or _)"
    return ""


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
    global CONFIG
    incoming = await request.json()
    if "channel" in incoming and "channels" not in incoming:
        # the old single-channel way of asking: it changes the primary
        incoming = {**incoming, "channels": _with_primary(CONFIG["channels"], incoming["channel"])}
    problem = _channel_problem(incoming) or _style_problem(incoming)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)
    old =[(r["login"], r["label"], r["enabled"]) for r in CONFIG["channels"]]
    CONFIG = save_config(_deep_merge(CONFIG, incoming))
    await HUB.broadcast_config()
    if [(r["login"], r["label"], r["enabled"]) for r in CONFIG["channels"]] != old:
        await RUNNER.sync()                          # only the channels that changed reconnect
    await HUB.broadcast_status()
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
    global CONFIG
    if error:
        return HTMLResponse(f"<body style='font:16px system-ui;padding:40px'>Twitch returned an error: {error} - {error_description}<br><a href='/twitch'>Back to the panel</a></body>")
    if state not in _oauth_states:
        return HTMLResponse("<body style='font:16px system-ui;padding:40px'>That login link expired. <a href='/twitch'>Start again</a></body>")
    _oauth_states.pop(state, None)
    try:
        acct = await exchange_code(code, _redirect_uri(request))
    except Exception as exc:
        return HTMLResponse(f"<body style='font:16px system-ui;padding:40px'>Token exchange failed: {exc}<br><a href='/twitch'>Back to the panel</a></body>")

    if not CONFIG.get("channels"):                       # the first sign-in sets the channel to watch
        CONFIG = save_config({**CONFIG, "channels": [{"login": acct.user_login.lower()}]})
    STATE.note(f"signed in as {acct.user_login}")
    await RUNNER.sync()                              # only the channels that now run as this account reconnect
    return HTMLResponse("<body style='font:16px system-ui;padding:40px;background:#0b0b10;color:#eee'>Connected. <a style='color:#ff3b30' href='/twitch'>Back to the panel</a><script>setTimeout(()=>location.href='/twitch',900)</script></body>")


@router.post("/auth/logout")
async def auth_logout(request: Request):
    """Sign one account out (body {"login": "name"}), or all of them (no body)."""
    body = await request.json() if await request.body() else {}
    SECRETS.clear_tokens(str((body or {}).get("login") or ""))
    await RUNNER.sync()
    return {"ok": True}


@router.post("/api/reconnect")
async def api_reconnect():
    await RUNNER.restart()
    return {"ok": True}


def _test_channel(login: Any) -> ChannelRT | None:
    """The channel a test message or alert is for: a watched one by login, else the primary. Before any
    channel is set up the tests still work, as a channel of their own. None: that login is not watched."""
    if clean_login(login):
        return STATE.channel(login)
    return STATE.primary() or ChannelRT(primary_login() or "hexcast")


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
        "fragments": apply_third_party([{"t": "text", "v": text}], ch.assets),
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
        "redeem": dict(user="TestViewer", reward="Hex sing a song", message="please"),
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
    HUB.chat[ws] = clean_login(channel)[:25]
    await RUNNER.ensure_started()
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
    HUB.events[ws] = clean_login(channel)[:25]
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
        HUB.events.pop(ws, None)


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
