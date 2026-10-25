"""
HexCast - Twitch connection (the twitch_core library)
=====================================================

A plugin with no tab of its own, shared by every Twitch module - Twitch (the chat and alert overlays), Chat
Timers, Giveaways. It is installed with whichever of them comes first, and holds:

    * the Twitch app's client id / secret and every account signed in      config/twitch_secrets.json
    * the channels Hexcast watches                                          config/twitch_core.json
    * one connection per channel: EventSub over WebSocket when an account may read the channel, anonymous IRC
      (chat only) when nobody is signed in - supervised, so a channel that breaks never touches another
    * hooks the modules listen on (chat lines, events, a channel coming up, a status change) and the calls they
      make (send a chat line, is the channel live, who is in chat, does this viewer follow or subscribe)
    * the sign-in pages, and the "Twitch connection" box every module's tab shows (static/twitch_connect.js)

The sign-in and settings routes keep the paths the Twitch plugin gave them (/twitch/auth/..., /twitch/api/credentials,
/twitch/api/reconnect): the OAuth redirect URL is registered in every streamer's Twitch app, byte for byte.

Security note: the client secret and the tokens live in config/twitch_secrets.json. HexCast has no auth, so treat
that box as trusted. The API never returns the secret or the tokens.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
import urllib.parse
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx
import websockets
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from hexcast_core import paths, running_plugin

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

CONFIG_DIR = paths.CONFIG_DIR
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "twitch_core.json"
LEGACY_CONFIG_PATH = CONFIG_DIR / "twitch.json"          # where the channels lived before this library (Twitch < 1.3)
SECRETS_PATH = CONFIG_DIR / "twitch_secrets.json"

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
    "user:write:chat",              # posting chat lines (shoutouts, timers, giveaway winners)
    "moderator:read:chatters",      # who is in chat right now (giveaways)
]

CHAT_LIMIT = 500                    # characters Twitch takes in one chat message

# --------------------------------------------------------------------------
# config: the channels to watch
# --------------------------------------------------------------------------

MAX_CHANNELS = 5
CHANNEL_LOGIN = re.compile(r"^[a-z0-9_]{3,25}$")


def clean_login(value: Any) -> str:
    return str(value or "").strip().lstrip("#").lower()


def normalise_channels(rows: Any, legacy: Any = "") -> list[dict]:
    """The channel list cleaned: [{login, label, enabled}], no repeats, at most MAX_CHANNELS, the first the primary.
    `legacy` is the old single `channel` string, used when there is no list (every Hexcast before multi-channel)."""
    rows = rows if isinstance(rows, list) else []
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
    legacy = clean_login(legacy)
    if not out and CHANNEL_LOGIN.match(legacy):
        out.append({"login": legacy, "label": "", "enabled": True})
    return out


def channel_problem(rows: Any) -> str:
    """Why a channel list cannot be taken (a name that is not one, too many channels), or ''."""
    if not isinstance(rows, list):
        return "channels is a list of {login, label, enabled}"
    if len(rows) > MAX_CHANNELS:
        return f"at most {MAX_CHANNELS} channels"
    for row in rows:
        login = clean_login(row.get("login") if isinstance(row, dict) else row)
        if not CHANNEL_LOGIN.match(login):
            return f"'{login or row}' is not a Twitch channel name (3-25 letters, digits or _)"
    return ""


def _read_json(path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_config() -> dict:
    """{"channels": [...]}. The first start of this library takes the list from the Twitch plugin's settings,
    where it lived before, and keeps it in a file of its own from then on."""
    if CONFIG_PATH.exists():
        raw = _read_json(CONFIG_PATH)
        return {"channels": normalise_channels(raw.get("channels"), raw.get("channel"))}
    legacy = _read_json(LEGACY_CONFIG_PATH)
    cfg = {"channels": normalise_channels(legacy.get("channels"), legacy.get("channel"))}
    if cfg["channels"]:
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> dict:
    merged = {"channels": normalise_channels(cfg.get("channels"))}
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


def own_logins() -> set[str]:
    """Every login Hexcast is signed in as - its own chat lines come back from Twitch like anyone's."""
    return {a.user_login.lower() for a in SECRETS.authed()}


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
# hooks: what the modules listen on
# --------------------------------------------------------------------------

class Hooks:
    """The listeners, each an async function. A module adds its own in setup() and removes them when it stops
    (the function each on_* call returns does that)."""

    def __init__(self) -> None:
        self.chat: list[Callable[..., Awaitable]] = []      # fn(msg, ch): every chat line, nothing hidden
        self.event: list[Callable[..., Awaitable]] = []     # fn(kind, ev, msg_id, ch): every other notification
        self.channel: list[Callable[..., Awaitable]] = []   # fn(ch, acct): a channel connected (acct None: anonymous)
        self.status: list[Callable[..., Awaitable]] = []    # fn(): something in the status changed


HOOKS = Hooks()


def _listen(group: list, fn: Callable[..., Awaitable]) -> Callable[[], None]:
    group.append(fn)

    def stop() -> None:
        if fn in group:
            group.remove(fn)
    return stop


def on_chat(fn: Callable[..., Awaitable]) -> Callable[[], None]:
    """fn(msg, ch) for every chat line of every watched channel (commands, lines from bots - nothing is hidden).
    msg: {type: "chat", channel, channel_label, id, ts, user: {id, login, name, color}, badges: [{set_id, id}],
    flags: {broadcaster, mod, vip, sub, first}, bits, reply, text, fragments}."""
    return _listen(HOOKS.chat, fn)


def on_event(fn: Callable[..., Awaitable]) -> Callable[[], None]:
    """fn(kind, ev, msg_id, ch) for every EventSub notification that is not a chat line: kind is its type
    ("channel.follow", "stream.online", "channel.chat.clear" ...) and ev its event, as Twitch sent it."""
    return _listen(HOOKS.event, fn)


def on_channel(fn: Callable[..., Awaitable]) -> Callable[[], None]:
    """fn(ch, acct) when a channel's connection comes up (acct is None for anonymous chat; ch.id is known
    when signed in)."""
    return _listen(HOOKS.channel, fn)


def on_status(fn: Callable[..., Awaitable]) -> Callable[[], None]:
    """fn() whenever something the status shows changed (a channel connected or dropped, went live ...)."""
    return _listen(HOOKS.status, fn)


async def _emit(group: list, *args: Any) -> None:
    """Call every listener in turn; one that fails is logged and never stops the others."""
    for fn in list(group):
        try:
            await fn(*args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            STATE.note(f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')} failed: {exc}")


async def broadcast_status() -> None:
    await _emit(HOOKS.status)


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
        self.live: bool | None = None  # None: not known (nobody signed in for it)
        self.live_since = 0.0

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
            "live": self.live, "live_since": self.live_since or None,
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

    def rows(self) -> list[dict]:
        out = []
        for i, r in enumerate(configured_channels()):
            rt = self.channels.get(r["login"]) or ChannelRT(r["login"], r.get("label", ""), r.get("enabled", True))
            out.append(rt.snapshot(primary=i == 0))
        return out

    def snapshot(self) -> dict:
        """The connection as the "Twitch connection" box shows it. Never the secret or a token."""
        dflt = default_account()
        return {
            "has_credentials": bool(SECRETS.client_id and SECRETS.client_secret),
            "client_id": SECRETS.client_id,
            "channels": self.rows(),
            "accounts": [{"login": a.user_login, "scopes": a.scopes,
                          "missing": [s for s in SCOPES if s not in a.scopes]} for a in SECRETS.authed()],
            "default_account": dflt.user_login if dflt else "",
            "scopes": SCOPES,
            "max_channels": MAX_CHANNELS,
            "log": self.log[-25:],
        }


STATE = State()


def note(msg: str) -> None:
    """A line in the shared Twitch log (every Twitch module's Status shows it, and the console)."""
    STATE.note(msg)


def where(ch: ChannelRT) -> str:
    """' [beta]' in a log line when Hexcast watches more than one channel, else nothing."""
    return f" [{ch.name}]" if len(STATE.channels) > 1 else ""


# --------------------------------------------------------------------------
# chat lines, as every module gets them
# --------------------------------------------------------------------------

def twitch_emote_url(emote_id: str, animated: bool = False) -> str:
    fmt = "animated" if animated else "default"
    return f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/{fmt}/dark/2.0"


def chat_from_eventsub(ev: dict, ch: ChannelRT) -> dict:
    msg = ev.get("message", {}) or {}
    login = ev.get("chatter_user_login", "")
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
    badges = [{"set_id": b.get("set_id", ""), "id": b.get("id", "")} for b in ev.get("badges") or []]
    sets = {b["set_id"] for b in badges}
    reply = None
    if ev.get("reply"):
        reply = {"user": ev["reply"].get("parent_user_name", ""), "text": ev["reply"].get("parent_message_body", "")}
    return {
        "type": "chat",
        "channel": ch.login,
        "channel_label": ch.name,
        "id": ev.get("message_id") or secrets.token_hex(8),
        "ts": time.time(),
        "user": {"id": ev.get("chatter_user_id", ""), "login": login,
                 "name": ev.get("chatter_user_name") or login, "color": ev.get("color") or ""},
        "badges": badges,
        "flags": {
            "broadcaster": "broadcaster" in sets,
            "mod": "moderator" in sets,
            "vip": "vip" in sets,
            "sub": "subscriber" in sets,
            "first": ev.get("message_type") == "user_intro",
        },
        "bits": (ev.get("cheer") or {}).get("bits", 0),
        "reply": reply,
        "text": msg.get("text", ""),
        "fragments": frags,
    }


IRC_LINE = re.compile(r"^(?:@(?P<tags>[^ ]+) )?:(?P<nick>[^!]+)![^ ]+ PRIVMSG #[^ ]+ :(?P<text>.*)$")


def parse_irc_tags(raw: str) -> dict[str, str]:
    out = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v.replace(r"\s", " ").replace(r"\:", ";").replace(r"\\", "\\")
    return out


def chat_from_irc(line: str, ch: ChannelRT) -> dict | None:
    m = IRC_LINE.match(line)
    if not m:
        return None
    tags = parse_irc_tags(m.group("tags") or "")
    text = m.group("text")
    login = m.group("nick")

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

    badges = []
    for b in (tags.get("badges") or "").split(","):
        if "/" in b:
            sid, ver = b.split("/", 1)
            badges.append({"set_id": sid, "id": ver})
    sets = {b["set_id"] for b in badges}

    return {
        "type": "chat",
        "channel": ch.login,
        "channel_label": ch.name,
        "id": tags.get("id") or secrets.token_hex(8),
        "ts": time.time(),
        "user": {"id": tags.get("user-id", ""), "login": login, "name": tags.get("display-name") or login,
                 "color": tags.get("color") or ""},
        "badges": badges,
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
        "fragments": frags,
    }


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
    STATE.note(f"eventsub{where(ch)}: {len(ch.subs_ok)} ok, {len(ch.subs_failed)} failed")
    await broadcast_status()


async def handle_notification(stype: str, ev: dict, msg_id: str | None, ch: ChannelRT) -> None:
    if stype == "channel.chat.message":
        await _emit(HOOKS.chat, chat_from_eventsub(ev, ch), ch)
        return
    went = stype in ("stream.online", "stream.offline")
    if stype == "stream.online":
        ch.live, ch.live_since = True, time.time()
    elif stype == "stream.offline":
        ch.live, ch.live_since = False, 0.0
    await _emit(HOOKS.event, stype, ev, msg_id, ch)
    if went:
        await broadcast_status()


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
                        STATE.note(f"eventsub connected{where(ch)}")
                        await subscribe_all(session_id, ch, acct)
                        await broadcast_status()

                    elif mtype == "notification":
                        stype = meta.get("subscription_type") or payload.get("subscription", {}).get("type", "")
                        await handle_notification(stype, payload.get("event", {}) or {}, meta.get("message_id"), ch)

                    elif mtype == "session_reconnect":
                        url = payload["session"]["reconnect_url"]
                        STATE.note(f"eventsub reconnect requested{where(ch)}")
                        break

                    elif mtype == "revocation":
                        sub = payload.get("subscription", {})
                        STATE.note(f"subscription revoked{where(ch)}: {sub.get('type')} ({sub.get('status')})")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ch.connected = False
            ch.last_error = str(exc)
            STATE.note(f"eventsub dropped{where(ch)}: {exc}")
            await broadcast_status()
            url = EVENTSUB_WS
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


LIVE_CHECK_SECONDS = 300            # EventSub says when a stream starts or ends; this catches what it missed


async def live_loop(ch: ChannelRT, acct: Account, stop: asyncio.Event) -> None:
    """Is the channel live? Asked when it connects (the stream may be running already) and every few minutes."""
    while not stop.is_set():
        try:
            r = await helix("GET", "/streams", acct=acct, params={"user_id": ch.id})
            if r.status_code == 200:
                data = r.json().get("data") or []
                live = bool(data)
                if live != ch.live:
                    ch.live = live
                    ch.live_since = _epoch(data[0].get("started_at")) if live else 0.0
                    await broadcast_status()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass                                    # offline for a moment: the next round asks again
        await asyncio.sleep(LIVE_CHECK_SECONDS)


def _epoch(iso: Any) -> float:
    """Twitch's '2026-10-24T18:02:11Z' as a unix time (0 when it is not one)."""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


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
                await broadcast_status()
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
                            msg = chat_from_irc(line, ch)
                            if msg:
                                await _emit(HOOKS.chat, msg, ch)
                        elif "CLEARCHAT" in line:
                            await _emit(HOOKS.event, "channel.chat.clear", {}, None, ch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ch.connected = False
            ch.last_error = str(exc)
            STATE.note(f"anonymous chat dropped (#{ch.login}): {exc}")
            await broadcast_status()
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
        self.side: set[asyncio.Task] = set()                       # listeners told that a channel came up
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
        for login, sig in wanted.items():
            if login not in self.tasks:
                self.tasks[login] = (sig, asyncio.create_task(self._watch(STATE.channels[login], self.stop)))
        self.started = True
        if not wanted:
            STATE.note("no channel set - add your channel in the Twitch connection box")
        await broadcast_status()

    async def _stop(self) -> None:
        self.stop.set()
        await self._cancel(*(task for _, task in self.tasks.values()), *self.side)
        self.tasks.clear()
        self.side.clear()
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
                await broadcast_status()
                await asyncio.sleep(wait)
                wait = min(wait * 2, RETRY_MAX)

    def _came_up(self, ch: ChannelRT, acct: Account | None) -> None:
        """Tell the listeners a channel connected - in a task of its own, so a slow one (emotes loading from
        three sites) never holds the connection up."""
        task = asyncio.create_task(_emit(HOOKS.channel, ch, acct))
        self.side.add(task)
        task.add_done_callback(self.side.discard)

    async def _connect(self, ch: ChannelRT, stop: asyncio.Event) -> None:
        """EventSub when there is a login for the channel, else anonymous chat. Runs until `stop`."""
        acct = account_for(ch.login)
        ch.account_login = acct.user_login if acct else ""
        if acct:
            await self._check(acct)
            cid = await resolve_channel_id(ch.login, acct)
            if cid:
                ch.id = cid
                self._came_up(ch, acct)
                await broadcast_status()
                live = asyncio.create_task(live_loop(ch, acct, stop))
                try:
                    await eventsub_loop(ch, acct, stop)
                finally:
                    await self._cancel(live)
                return
            STATE.note(f"channel lookup failed for {ch.login}, falling back to anonymous chat")

        # no login (or the lookup failed): chat-only via anonymous IRC
        ch.live = None
        self._came_up(ch, None)
        await broadcast_status()
        await irc_loop(ch, stop)

    async def ensure_started(self) -> None:
        if not self.started:
            await self.restart()


RUNNER = Runner()


async def ensure_started() -> None:
    """Connect if nothing has yet (the first request, on a Hexcast whose core cannot start plugins at boot)."""
    await RUNNER.ensure_started()


# --------------------------------------------------------------------------
# what the modules call
# --------------------------------------------------------------------------

class TwitchError(Exception):
    """Something Twitch (or the setup) refused, said in words the streamer can act on."""


def channel_for(channel: str = "") -> ChannelRT:
    """A watched channel by login, or the primary when none is named."""
    ch = STATE.channel(channel)
    if ch is None:
        if clean_login(channel):
            raise TwitchError(f"'{clean_login(channel)}' is not a channel Hexcast watches")
        raise TwitchError("no Twitch channel set - add your channel in the Twitch connection box")
    return ch


def acting_account(ch: ChannelRT, scope: str = "", *, own: bool = False) -> Account:
    """The account Hexcast acts as in `ch`, checked for what a call needs: a scope its sign-in must carry, and
    (own=True) that it is the channel's own account - Twitch only shows a channel's subscribers to its owner."""
    acct = account_for(ch.login)
    if acct is None:
        raise TwitchError("not signed in to Twitch - connect an account in the Twitch connection box")
    if own and acct.user_login.lower() != ch.login:
        raise TwitchError(f"this needs {ch.login}'s own account - sign in as {ch.login} in the Twitch connection box")
    if scope and scope not in acct.scopes:
        raise TwitchError(f"{acct.user_login}'s sign-in does not allow {scope} yet - "
                          f"connect it again in the Twitch connection box to grant it")
    return acct


async def _channel_id(ch: ChannelRT, acct: Account) -> str:
    if not ch.id:
        ch.id = await resolve_channel_id(ch.login, acct)
    if not ch.id:
        raise TwitchError(f"could not look up the channel '{ch.login}'")
    return ch.id


def _refusal(r: httpx.Response, what: str) -> str:
    try:
        why = r.json().get("message", "")
    except Exception:
        why = ""
    return f"{what} ({r.status_code}{' ' + why if why else ''})"


def is_live(channel: str = "") -> bool | None:
    """True / False, or None when it is not known (nobody signed in for that channel, or not watched)."""
    ch = STATE.channel(channel)
    return ch.live if ch else None


async def send_chat(text: str, channel: str = "") -> dict:
    """Post a chat line in a watched channel (the primary by default) as the account Hexcast acts as there.
    Returns {"ok": True, "channel", "account"}; raises TwitchError when Twitch would not take it."""
    text = " ".join(str(text or "").split())[:CHAT_LIMIT]
    if not text:
        raise TwitchError("nothing to send")
    ch = channel_for(channel)
    acct = acting_account(ch, "user:write:chat")
    cid = await _channel_id(ch, acct)
    r = await helix("POST", "/chat/messages", acct=acct,
                    json_body={"broadcaster_id": cid, "sender_id": acct.user_id, "message": text})
    if r.status_code not in (200, 204):
        raise TwitchError(_refusal(r, "Twitch did not take the chat message"))
    try:
        sent = (r.json().get("data") or [{}])[0]
    except Exception:
        sent = {}
    if sent.get("is_sent") is False:
        drop = sent.get("drop_reason") or {}
        raise TwitchError(f"Twitch dropped the chat message: {drop.get('message') or drop.get('code') or 'no reason given'}")
    return {"ok": True, "channel": ch.login, "account": acct.user_login}


async def chatters(channel: str = "") -> list[dict]:
    """Everyone connected to a channel's chat right now (lurkers too, not people watching with chat closed):
    [{"user_id", "user_login", "user_name"}]. Twitch's own list, which can lag a minute or two."""
    ch = channel_for(channel)
    acct = acting_account(ch, "moderator:read:chatters")
    cid = await _channel_id(ch, acct)
    out: list[dict] = []
    cursor = ""
    while True:
        params = {"broadcaster_id": cid, "moderator_id": acct.user_id, "first": 1000}
        if cursor:
            params["after"] = cursor
        r = await helix("GET", "/chat/chatters", acct=acct, params=params)
        if r.status_code != 200:
            raise TwitchError(_refusal(r, "Twitch would not say who is in chat"))
        d = r.json()
        out += d.get("data") or []
        cursor = (d.get("pagination") or {}).get("cursor") or ""
        if not cursor or len(out) >= 100000:
            return out


async def subscribers(user_ids: list[str], channel: str = "") -> dict[str, str]:
    """Which of these users subscribe to the channel: {user_id: tier ("1000", "2000", "3000")}. Needs the
    channel's own account (Twitch shows subscribers only to the broadcaster)."""
    ch = channel_for(channel)
    acct = acting_account(ch, "channel:read:subscriptions", own=True)
    cid = await _channel_id(ch, acct)
    ids = [str(u) for u in dict.fromkeys(user_ids) if u]
    out: dict[str, str] = {}
    for i in range(0, len(ids), 100):
        params = [("broadcaster_id", cid)] + [("user_id", u) for u in ids[i:i + 100]]
        r = await helix("GET", "/subscriptions", acct=acct, params=params)
        if r.status_code != 200:
            raise TwitchError(_refusal(r, "Twitch would not say who subscribes"))
        for row in r.json().get("data") or []:
            out[str(row.get("user_id"))] = str(row.get("tier") or "1000")
    return out


async def followed_at(user_id: str, channel: str = "") -> float | None:
    """When a user followed the channel (unix time), or None when they do not follow it."""
    ch = channel_for(channel)
    acct = acting_account(ch, "moderator:read:followers")
    cid = await _channel_id(ch, acct)
    r = await helix("GET", "/channels/followers", acct=acct, params={"broadcaster_id": cid, "user_id": str(user_id)})
    if r.status_code != 200:
        raise TwitchError(_refusal(r, "Twitch would not say who follows"))
    data = r.json().get("data") or []
    if not data:
        return None
    return _epoch(data[0].get("followed_at")) or time.time()     # following, the date unreadable: since now


async def lookup_user(login: str, acct: Account) -> dict | None:
    r = await helix("GET", "/users", acct=acct, params={"login": login})
    if r.status_code != 200:
        return None
    data = r.json().get("data", [])
    return data[0] if data else None


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/twitch", tags=["twitch"])
_oauth_states: dict[str, tuple[float, str]] = {}        # state -> (when, the page to go back to)
STATE_SECONDS = 900


def _redirect_uri(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    # Twitch only allows plain http for the literal host "localhost" — the
    # loopback IP gets rejected, so normalise it before handing it over.
    for loopback in ("://127.0.0.1", "://[::1]"):
        base = base.replace(loopback, "://localhost")
    return base + "/twitch/auth/callback"


def _back(page: Any) -> str:
    """Where a sign-in returns to: a page of this Hexcast (the module whose tab started it), else the
    Twitch tab when that module runs, else the control panel."""
    page = str(page or "")
    if page.startswith("/") and not page.startswith("//") and "\\" not in page and len(page) <= 200:
        return page
    return "/twitch" if running_plugin("twitch") is not None else "/"


def _page(text: str, back: str) -> str:
    return (f"<body style='font:16px system-ui;padding:40px;background:#0b0b10;color:#eee'>{text}<br>"
            f"<a style='color:#ff3b30' href='{back}'>Back</a></body>")


def connection(request: Request | None = None) -> dict:
    out = STATE.snapshot()
    if request is not None:
        out["redirect_uri"] = _redirect_uri(request)
    out["started"] = RUNNER.started
    return out


@router.get("/auth/login")
async def auth_login(request: Request, next: str = ""):
    if not (SECRETS.client_id and SECRETS.client_secret):
        return JSONResponse({"error": "Add your Twitch client ID and secret first."}, status_code=400)
    now = time.time()
    for old in [s for s, (t, _) in _oauth_states.items() if now - t > STATE_SECONDS]:
        _oauth_states.pop(old, None)
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = (now, _back(next))
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
    known = state in _oauth_states
    back = _oauth_states.pop(state, (0.0, ""))[1] or _back("")
    if error:
        return HTMLResponse(_page(f"Twitch returned an error: {error} - {error_description}", back))
    if not known or not code:
        return HTMLResponse(_page("That login link expired. Start again.", back))
    try:
        acct = await exchange_code(code, _redirect_uri(request))
    except Exception as exc:
        return HTMLResponse(_page(f"Token exchange failed: {exc}", back))

    if not CONFIG.get("channels"):                       # the first sign-in sets the channel to watch
        CONFIG = save_config({"channels": [{"login": acct.user_login.lower()}]})
    STATE.note(f"signed in as {acct.user_login}")
    await RUNNER.sync()                              # only the channels that now run as this account reconnect
    return HTMLResponse(_page("Connected.", back).replace(
        "</body>", f"<script>setTimeout(()=>location.href={json.dumps(back)},900)</script></body>"))


@router.post("/auth/logout")
async def auth_logout(request: Request):
    """Sign one account out (body {"login": "name"}), or all of them (no body)."""
    body = await request.json() if await request.body() else {}
    SECRETS.clear_tokens(str((body or {}).get("login") or ""))
    await RUNNER.sync()
    return {"ok": True}


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
    await broadcast_status()
    return {"ok": True, "has_credentials": bool(SECRETS.client_id and SECRETS.client_secret)}


@router.post("/api/reconnect")
async def api_reconnect():
    await RUNNER.restart()
    return {"ok": True}


async def set_channels(rows: Any) -> str:
    """Save a new channel list ('' when taken, else why not); only the channels that changed reconnect."""
    global CONFIG
    problem = channel_problem(rows)
    if problem:
        return problem
    old = [(r["login"], r["label"], r["enabled"]) for r in CONFIG["channels"]]
    CONFIG = save_config({"channels": rows})
    if [(r["login"], r["label"], r["enabled"]) for r in CONFIG["channels"]] != old:
        await RUNNER.sync()
    await broadcast_status()
    return ""


@router.get("/api/connection")
async def api_connection(request: Request):
    """The connection as the "Twitch connection" box shows it: channels, accounts, what is missing, the log."""
    await RUNNER.ensure_started()
    return connection(request)


@router.post("/api/connection")
async def api_set_connection(request: Request):
    """{"channels": [{login, label, enabled}, ...]} - the channels to watch, the first the primary."""
    body = await request.json()
    problem = await set_channels((body or {}).get("channels"))
    if problem:
        return JSONResponse({"error": problem}, status_code=400)
    return {"ok": True, **connection(request)}


@router.post("/api/chat/send")
async def api_chat_send(request: Request):
    """{"text": "...", "channel": "login"} - a chat line as the account Hexcast acts as there (the primary when
    no channel is named). Needs the user:write:chat scope."""
    body = await request.json() if await request.body() else {}
    try:
        return await send_chat((body or {}).get("text", ""), (body or {}).get("channel", ""))
    except TwitchError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


# --------------------------------------------------------------------------
# start / stop
# --------------------------------------------------------------------------

async def start() -> None:
    await RUNNER.ensure_started()


async def stop() -> None:
    await RUNNER._stop()
