"""
HexCast - Bot-Free Redeems
==========================

Twitch channel point redeems, chat commands (`!airhorn`) and bits play soundboard clips - straight from Twitch to
the soundboard, with no bot or AI in between. For a streamer who has no bot of their own: everything is set up in
the tab, and the clips play on the Soundboard overlay (/overlay) like any other soundboard clip.

A row can also (or only) say a line in the Twitch chat: `!discord` answers with the Discord address. That line is
posted by the account Hexcast is signed in as, so it needs a sign-in.

    http://localhost:4747/redeems     -> the tab

The connection to Twitch is the shared twitch_core library (the same sign-in and channels as the Twitch plugin).
Chat commands and bits come from the chat, so they work with only a channel name (anonymous chat); redeems need a
sign-in as the channel's own account. There is no bot API: the routes below are the tab's.

The Twitch plugin asks `claims()` before an alert fires its own clip, so a redeem or a cheer with a sound here never
plays two.
"""

from __future__ import annotations

import json
import math
import random
import re
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from hexcast_core import paths
try:
    from hexcast_core.sockets import send as ws_send          # Hexcast 2.3+: gives up on a client that stopped reading
except ImportError:                                          # an older Hexcast: a plain send, as before
    async def ws_send(ws, data):
        await (ws.send_bytes(data) if isinstance(data, bytes) else ws.send_text(data))
from hexcast_plugins.twitch_core import core as tc

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = paths.CONFIG_DIR / "bot_free_redeems.json"

REDEEM = "channel.channel_points_custom_reward_redemption.add"

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

MAX_TRIGGERS = 200
MAX_CLIPS = 10                  # sounds on one row (one of them plays, at random)
MAX_WORDS = 5                   # commands on one row ("!airhorn !horn")
MAX_MESSAGE = 500               # a chat line, as long as Twitch takes one
KINDS = ("redeem", "command", "bits")
WHO = ("everyone", "subs", "vips", "mods", "broadcaster")
RANK = {w: i for i, w in enumerate(WHO)}
WHO_SAYS = {"subs": "subscribers", "vips": "VIPs", "mods": "moderators", "broadcaster": "the broadcaster"}
DEFAULT_CONFIG: dict[str, Any] = {"on": True, "triggers": []}
TRIGGER_DEFAULTS: dict[str, Any] = {
    "id": "",
    "kind": "redeem",           # redeem | command | bits
    "on": True,
    "reward_id": "",            # redeem: the reward's Twitch id (set when it is picked from the list) ...
    "reward": "",               # ... and its title - a reward found by neither does not play
    "command": "",              # command: "!airhorn", or a few at once: "!airhorn !horn"
    "bits": 100,                # bits: a cheer of at least this many (the biggest row that fits wins)
    "clips": [],                # soundboard clip names; with several, one at random
    "message": "",              # a line said in the Twitch chat ({user}, {channel}, {uptime}, {bits}, {reward})
    "who": "everyone",          # command: who may use it (everyone | subs | vips | mods | broadcaster)
    "cooldown": 30,             # command: seconds before anyone can use it again
    "user_cooldown": 0,         # command: seconds before the same viewer can use it again
    "channel": "primary",       # primary | all | a channel's login
}
LIMITS = {"bits": (1, 1_000_000), "cooldown": (0, 86400), "user_cooldown": (0, 86400)}
TRIGGER_ID = re.compile(r"^[a-z0-9]{4,16}$")
REWARD_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
REWARD_TITLE_MAX = 45           # what Twitch allows for a reward's title


class Problem(Exception):
    """A setting that cannot be taken, said for the person who typed it."""


def _int(value: Any, key: str) -> int:
    lo, hi = LIMITS[key]
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise Problem(f"{key.replace('_', ' ')} is a number") from None
    if not lo <= n <= hi:
        raise Problem(f"{key.replace('_', ' ')} is {lo} to {hi}")
    return n


def clean_command(value: Any) -> str:
    """'!Airhorn, horn' -> '!airhorn !horn': lower case, a `!` in front of a word that starts with a letter or digit."""
    words: list[str] = []
    for w in re.split(r"[\s,]+", str(value or "").strip().lower()):
        if not w:
            continue
        if w[0].isalnum():
            w = "!" + w
        w = w[:40]
        if len(w) > 1 and w not in words:
            words.append(w)
    if len(words) > MAX_WORDS:
        raise Problem(f"at most {MAX_WORDS} commands on one row")
    return " ".join(words)


def clean_clips(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise Problem("clips is a list of soundboard clip names")
    out: list[str] = []
    for c in value:
        c = str(c if c is not None else "").strip()[:120]
        if c and c.lower() not in (x.lower() for x in out):
            out.append(c)
    if len(out) > MAX_CLIPS:
        raise Problem(f"at most {MAX_CLIPS} sounds on one row")
    return out


def clean_trigger(raw: Any, base: dict | None = None) -> dict:
    """A row's settings checked (and `base` - the row as it was - filled in for what `raw` leaves out)."""
    if not isinstance(raw, dict):
        raise Problem("a row is an object")
    t = {**TRIGGER_DEFAULTS, **(base or {})}
    t["clips"] = list(t["clips"])
    if "id" in raw:
        t["id"] = str(raw["id"] or "").strip().lower()
    if "kind" in raw:
        t["kind"] = str(raw["kind"] or "").strip().lower()
    if t["kind"] not in KINDS:
        raise Problem(f"'{t['kind']}' is not a kind of row (redeem, command or bits)")
    if "on" in raw:
        t["on"] = bool(raw["on"])
    if "reward_id" in raw:
        t["reward_id"] = str(raw["reward_id"] or "").strip()
    if t["reward_id"] and not REWARD_ID.match(t["reward_id"]):
        raise Problem("that is not a Twitch reward id")
    if "reward" in raw:
        t["reward"] = " ".join(str(raw["reward"] or "").split())[:REWARD_TITLE_MAX]
    if "command" in raw:
        t["command"] = clean_command(raw["command"])
    for key in ("bits", "cooldown", "user_cooldown"):
        if key in raw:
            t[key] = _int(raw[key], key)
    if "clips" in raw:
        t["clips"] = clean_clips(raw["clips"])
    if "message" in raw:
        t["message"] = " ".join(str(raw["message"] if raw["message"] is not None else "").split())
    if len(t["message"]) > MAX_MESSAGE:
        raise Problem(f"a chat message is at most {MAX_MESSAGE} characters")
    if "who" in raw:
        t["who"] = str(raw["who"] or "everyone").strip().lower()
    if t["who"] not in WHO:
        raise Problem(f"'{t['who']}' is not one of {', '.join(WHO)}")
    if "channel" in raw:
        t["channel"] = str(raw["channel"] if raw["channel"] is not None else "").strip()
    channel = tc.clean_login(t["channel"]) or "primary"
    if channel not in ("primary", "all") and not tc.CHANNEL_LOGIN.match(channel):
        raise Problem(f"'{t['channel']}' is not a channel (primary, all, or a channel name)")
    t["channel"] = channel
    if not TRIGGER_ID.match(str(t.get("id") or "")):
        t["id"] = secrets.token_hex(4)
    return {k: t[k] for k in TRIGGER_DEFAULTS}


def clean_config(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    cfg = dict(DEFAULT_CONFIG)
    if "on" in raw:
        cfg["on"] = bool(raw["on"])
    rows = raw.get("triggers", [])
    if not isinstance(rows, list):
        raise Problem("triggers is a list")
    if len(rows) > MAX_TRIGGERS:
        raise Problem(f"at most {MAX_TRIGGERS} rows")
    seen: set[str] = set()
    cfg["triggers"] = []
    for row in rows:
        t = clean_trigger(row)
        while t["id"] in seen:
            t["id"] = secrets.token_hex(4)
        seen.add(t["id"])
        cfg["triggers"].append(t)
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


# --------------------------------------------------------------------------
# which row a redeem / command / cheer plays
# --------------------------------------------------------------------------

def _here(t: dict, login: str) -> bool:
    """A row listens in this channel: the primary, every channel, or the one it names."""
    if t["channel"] == "all":
        return True
    if t["channel"] == "primary":
        return login == tc.primary_login()
    return t["channel"] == login


def _live_rows(kind: str, login: str) -> list[dict]:
    """The rows of a kind that can play in a channel right now: switched on, with a sound or a chat message."""
    if not CONFIG["on"]:
        return []
    return [t for t in CONFIG["triggers"]
            if t["kind"] == kind and t["on"] and (t["clips"] or t["message"]) and _here(t, login)]


def match_redeem(reward_id: str, title: str, login: str) -> dict | None:
    """The row for a reward: by its Twitch id first, else by its title (a reward deleted and made again keeps
    working by name)."""
    title = " ".join(str(title or "").split()).lower()
    by_title = None
    for t in _live_rows("redeem", login):
        if t["reward_id"] and t["reward_id"] == reward_id:
            return t
        if by_title is None and title and t["reward"].lower() == title:
            by_title = t
    return by_title


def match_bits(bits: int, login: str) -> dict | None:
    """The row for a cheer: the biggest amount it reaches (100 and 1000 set, 500 bits plays the 100 row)."""
    best = None
    for t in _live_rows("bits", login):
        if bits >= t["bits"] and (best is None or t["bits"] > best["bits"]):
            best = t
    return best


def command_word(text: str) -> str:
    """The first word of a chat line, lower case - a reply's leading @name skipped."""
    words = str(text or "").split()
    if len(words) > 1 and words[0].startswith("@"):
        words = words[1:]
    return words[0].lower() if words else ""


def match_command(text: str, login: str) -> dict | None:
    word = command_word(text)
    if not word:
        return None
    return next((t for t in _live_rows("command", login) if word in t["command"].split()), None)


def rank(msg: dict, login: str) -> int:
    """Where a chatter stands: 0 everyone, 1 subscriber, 2 VIP, 3 moderator, 4 the broadcaster."""
    f = msg.get("flags") or {}
    who = str((msg.get("user") or {}).get("login") or "").lower()
    if f.get("broadcaster") or who == login:
        return RANK["broadcaster"]
    if f.get("mod"):
        return RANK["mods"]
    if f.get("vip"):
        return RANK["vips"]
    if f.get("sub") or any((b or {}).get("set_id") == "founder" for b in msg.get("badges") or []):
        return RANK["subs"]
    return RANK["everyone"]


def claims(kind: str, ev: dict, login: str) -> bool:
    """For the Twitch plugin: does this redeem / cheer play a sound here? Then its alert's own clip stays quiet, so
    one redeem never plays two sounds. `kind` is the EventSub type, `ev` the event as Twitch sent it. (A row that
    only says a line in chat plays no sound: the alert keeps its clip.)"""
    t = None
    try:
        if kind == REDEEM:
            reward = ev.get("reward") or {}
            t = match_redeem(str(reward.get("id") or ""), str(reward.get("title") or ""), login)
        elif kind == "channel.cheer":
            t = match_bits(int(ev.get("bits") or 0), login)
    except (TypeError, ValueError, AttributeError):
        pass
    return bool(t and t["clips"])


# --------------------------------------------------------------------------
# playing, cooldowns, what happened
# --------------------------------------------------------------------------

APP = None                                  # the app: the soundboard is called in-process, never over loopback


async def soundboard(path: str) -> tuple[int, dict]:
    """One of the soundboard's own routes, called in-process (a loopback socket can be taken over by a port forward
    and land on another Hexcast) -> (status code, its JSON or {})."""
    if APP is None:
        return 503, {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url="http://hexcast", timeout=6) as c:
        r = await c.get(path)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


async def soundboard_clips() -> list[str] | None:
    """The soundboard's clip names (sounds, then videos), or None when it cannot say."""
    try:
        code, body = await soundboard("/api/list")
    except Exception:
        return None
    if code != 200:
        return None
    return [str(n) for n in (body.get("audio") or [])] + [str(n) for n in (body.get("video") or [])]


LAST_CLIP: dict[str, str] = {}              # row id -> the clip it played last (not the same one twice running)


def pick(t: dict) -> str:
    clips = t["clips"]
    if len(clips) > 1:
        clips = [c for c in clips if c != LAST_CLIP.get(t["id"])] or clips
    return random.choice(clips)


async def play(clip: str) -> str:
    """Play a soundboard clip on the Soundboard overlay. '' when it played, else why nobody heard it."""
    try:
        code, body = await soundboard(f"/api/play/{quote(clip, safe='')}")
    except Exception as exc:
        return f"the soundboard did not answer ({exc})"
    if code == 404:
        return f"there is no clip '{clip}' on the Soundboard"
    if code >= 400:
        return f"the soundboard refused '{clip}' ({code})"
    if body.get("suppressed"):
        left = math.ceil(int(body.get("next_in_ms") or 0) / 1000)
        return f"'{clip}' is still in its Soundboard cooldown ({left} s left)"
    if not body.get("delivered"):
        return "nobody heard it - the Soundboard overlay (/overlay) is not open in OBS"
    return ""


class Seen:
    """The last few hundred chat lines / redemptions handled: Twitch can send one twice."""

    def __init__(self, size: int = 500) -> None:
        self.order: deque[str] = deque()
        self.ids: set[str] = set()
        self.size = size

    def first(self, key: str) -> bool:
        if not key or key.endswith(":"):
            return True
        if key in self.ids:
            return False
        self.ids.add(key)
        self.order.append(key)
        if len(self.order) > self.size:
            self.ids.discard(self.order.popleft())
        return True


SEEN = Seen()
RECENT: deque[dict] = deque(maxlen=40)      # what happened, newest last
STATS: dict[str, dict] = {}                 # row id -> {played, last, by}
LAST_USED: dict[tuple[str, str], float] = {}          # (row id, channel) -> when its command last played
LAST_BY: dict[tuple[str, str, str], float] = {}       # (row id, channel, viewer) -> when that viewer last played it
MAX_LAST_BY = 2000


def _forget_old_viewers(now: float) -> None:
    """Keep the per-viewer cooldowns small: drop the ones that ran out (or whose row is gone)."""
    if len(LAST_BY) <= MAX_LAST_BY:
        return
    waits = {t["id"]: t["user_cooldown"] for t in CONFIG["triggers"]}
    for key in [k for k, ts in LAST_BY.items() if now - ts >= waits.get(k[0], 0)]:
        LAST_BY.pop(key, None)


def log(kind: str, what: str, user: str, login: str, result: str, note: str = "", t: dict | None = None,
        clip: str = "", said: bool = False) -> dict:
    """One line of the tab's Recent list. result: played | skipped | failed. `clip` is the sound (played, or the
    one that failed), `said` whether its chat message went out."""
    entry = {"ts": time.time(), "kind": kind, "what": what, "user": user, "channel": login, "result": result,
             "note": note, "trigger": t["id"] if t else "", "clip": clip, "said": said}
    RECENT.append(entry)
    return entry


def _uptime(login: str) -> str:
    ch = tc.STATE.channel(login)
    if not ch or ch.live is not True or not ch.live_since:
        return "offline"
    mins = int((time.time() - ch.live_since) // 60)
    return f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def fill(text: str, login: str, **values: Any) -> str:
    """{user}, {channel}, {uptime} (and {bits}, {reward} where there is one) filled in; any other {...} is left
    as it is typed."""
    values = {"channel": login, "uptime": _uptime(login), **{k: str(v) for k, v in values.items()}}
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1).lower(), m.group(0)), text)


SENT: deque[tuple[str, str, float]] = deque(maxlen=20)    # (channel, text, when): the lines posted a moment ago
ECHO_FOR = 30.0


def own_echo(msg: dict, login: str) -> bool:
    """A chat line that is one Hexcast just posted itself, coming back from Twitch like anyone's: a message that
    starts with a command ("!discord is ...") must not set that command off again."""
    if str((msg.get("user") or {}).get("login") or "").lower() not in tc.own_logins():
        return False
    text, now = " ".join(str(msg.get("text") or "").split()), time.time()
    return any(c == login and s == text and now - ts < ECHO_FOR for c, s, ts in SENT)


async def say(text: str, login: str) -> str:
    """Post a line in a channel's chat. '' when Twitch took it, else why not."""
    text = " ".join(text.split())[:tc.CHAT_LIMIT]
    if not text:
        return "the chat message is empty"
    SENT.append((login, text, time.time()))
    try:
        await tc.send_chat(text, login)
    except tc.TwitchError as exc:
        return f"chat message: {exc}"
    except httpx.HTTPError as exc:
        return f"chat message: Twitch did not answer ({exc})"
    return ""


async def fire(t: dict, kind: str, what: str, user: str, login: str, **values: Any) -> str:
    """Play a row's sound and say its chat message, and note it. '' when either went out, else why not."""
    clip, played, said, errs = "", False, False, []
    if t["clips"]:
        clip = pick(t)
        LAST_CLIP[t["id"]] = clip
        err = await play(clip)
        played = not err
        if err:
            errs.append(err)
    if t["message"]:
        err = await say(fill(t["message"], login, user=user, **values), login)
        said = not err
        if err:
            errs.append(err)
    note = "; ".join(errs)
    if errs:
        tc.note(f"bot-free redeems: {what} in #{login}: {note}")
    if not (played or said):
        log(kind, what, user, login, "failed", note, t, clip)
    else:
        log(kind, what, user, login, "played", note, t, clip if played else "", said)
        s = STATS.setdefault(t["id"], {"played": 0, "last": 0.0, "by": ""})
        s["played"] += 1
        s["last"], s["by"] = time.time(), user
    await HUB.status()
    return "" if played or said else note


def _secs(n: float) -> str:
    n = max(1, math.ceil(n))
    return f"{n} s" if n < 120 else f"{math.ceil(n / 60)} min"


async def on_chat(msg: dict, ch: tc.ChannelRT) -> None:
    """A chat line: a cheer plays its bits row, a command its command row (who may, and the cooldowns, checked)."""
    if not CONFIG["on"]:
        return
    login = ch.login
    user = msg.get("user") or {}
    name = str(user.get("name") or user.get("login") or "someone")
    mid = str(msg.get("id") or "")
    try:
        bits = int(msg.get("bits") or 0)
    except (TypeError, ValueError):
        bits = 0
    if bits > 0 and SEEN.first(f"cheer:{mid}"):
        t = match_bits(bits, login)
        if t is not None:
            await fire(t, "bits", f"{bits} bits", name, login, bits=bits)
        elif _live_rows("bits", login):
            log("bits", f"{bits} bits", name, login, "skipped", "less than the smallest bits sound")
            await HUB.status()
    t = match_command(str(msg.get("text") or ""), login)
    if t is None or own_echo(msg, login) or not SEEN.first(f"chat:{mid}"):
        return
    word = command_word(str(msg.get("text") or ""))
    standing = rank(msg, login)
    if standing < RANK[t["who"]]:
        log("command", word, name, login, "skipped", f"only {WHO_SAYS[t['who']]} can use it", t)
        await HUB.status()
        return
    now = time.time()
    viewer = str(user.get("login") or name).lower()
    if standing < RANK["broadcaster"]:             # the broadcaster is never held up (testing on stream)
        wait = t["cooldown"] - (now - LAST_USED.get((t["id"], login), -1e18))
        if wait > 0:
            log("command", word, name, login, "skipped", f"on cooldown ({_secs(wait)} left)", t)
            await HUB.status()
            return
        wait = t["user_cooldown"] - (now - LAST_BY.get((t["id"], login, viewer), -1e18))
        if wait > 0:
            log("command", word, name, login, "skipped", f"{name} used it - wait {_secs(wait)}", t)
            await HUB.status()
            return
    if not await fire(t, "command", word, name, login):
        LAST_USED[(t["id"], login)] = now           # (a sound nobody heard does not start the cooldown)
        LAST_BY[(t["id"], login, viewer)] = now
        _forget_old_viewers(now)


async def on_event(kind: str, ev: dict, msg_id: str | None, ch: tc.ChannelRT) -> None:
    """A channel point redemption plays its reward's row. Cooldowns and limits are the reward's own, on Twitch:
    the viewer has spent the points by now, so nothing here holds a redeem back."""
    if kind != REDEEM or not CONFIG["on"]:
        return
    if not SEEN.first(f"redeem:{ev.get('id') or msg_id or ''}"):
        return
    reward = ev.get("reward") or {}
    title = str(reward.get("title") or "a reward")
    name = str(ev.get("user_name") or ev.get("user_login") or "someone")
    t = match_redeem(str(reward.get("id") or ""), title, ch.login)
    if t is None:
        log("redeem", title, name, ch.login, "skipped", "no sound set for this reward")
        await HUB.status()
        return
    await fire(t, "redeem", title, name, ch.login, reward=title)


async def on_status() -> None:
    """The connection changed (a channel connected, a sign-in...): the tab's problems may have too."""
    await HUB.status()


# --------------------------------------------------------------------------
# what the tab shows
# --------------------------------------------------------------------------

def _redeem_problem(login: str) -> str:
    """Why redeems cannot reach Hexcast in a channel, or ''."""
    ch = tc.STATE.channel(login)
    if ch is None:
        return f"#{login} is not a channel Hexcast watches - add it in the Twitch connection box"
    try:
        tc.acting_account(ch, "channel:read:redemptions", own=True)
    except tc.TwitchError as exc:
        return f"redeems: {exc}"
    failed = next((f for f in ch.subs_failed if f.startswith(REDEEM)), "")
    if failed:
        return (f"Twitch refused the redeem events ({failed[len(REDEEM):].strip(' ()')}). "
                "Channel points need an Affiliate or Partner channel.")
    return ""


def _chat_problem(login: str) -> str:
    ch = tc.STATE.channel(login)
    if ch is None:
        return f"#{login} is not a channel Hexcast watches - add it in the Twitch connection box"
    if not ch.enabled:
        return f"#{login} is switched off in the Twitch connection box"
    if not ch.connected:
        return f"not connected to #{login}'s chat yet"
    return ""


def where(t: dict) -> list[str]:
    """The watched channels a row listens in."""
    rows = [r for r in tc.configured_channels() if r.get("enabled", True)]
    if t["channel"] == "all":
        return [r["login"] for r in rows]
    if t["channel"] == "primary":
        p = tc.primary_login()
        return [p] if p else []
    return [t["channel"]]


def _say_problem(login: str) -> str:
    """Why Hexcast cannot post in a channel's chat, or ''."""
    ch = tc.STATE.channel(login)
    if ch is None:
        return f"#{login} is not a channel Hexcast watches - add it in the Twitch connection box"
    try:
        tc.acting_account(ch, "user:write:chat")
    except tc.TwitchError as exc:
        return f"chat message: {exc}"
    return ""


def problem(t: dict, earlier: list[dict], clips: list[str] | None) -> str:
    """What stops a row from playing, said for the streamer, or ''."""
    if t["kind"] == "redeem" and not (t["reward_id"] or t["reward"]):
        return "pick a reward"
    if t["kind"] == "command" and not t["command"]:
        return "type a command, like !airhorn"
    if not t["clips"] and not t["message"]:
        return "pick a sound or type a chat message"
    if clips is not None:
        names = {c.lower() for c in clips}
        gone = [c for c in t["clips"] if c.lower() not in names]
        if gone:
            return "not on the Soundboard (renamed or deleted?): " + ", ".join(gone)
    for e in earlier:                               # the same trigger on a row above wins
        if (e["kind"] != t["kind"] or not e["on"] or not (e["clips"] or e["message"])
                or not set(where(e)) & set(where(t))):
            continue
        if t["kind"] == "command" and set(e["command"].split()) & set(t["command"].split()):
            return "a row above uses the same command - that one plays"
        if t["kind"] == "redeem" and ((t["reward_id"] and e["reward_id"] == t["reward_id"])
                                      or (t["reward"] and e["reward"].lower() == t["reward"].lower())):
            return "a row above has the same reward - that one plays"
        if t["kind"] == "bits" and e["bits"] == t["bits"]:
            return "a row above has the same bits amount - that one plays"
    logins = where(t)
    if not logins:
        return "no channel - add yours in the Twitch connection box"
    why = _redeem_problem(logins[0]) if t["kind"] == "redeem" else _chat_problem(logins[0])
    if not why and t["message"]:
        why = next((w for w in map(_say_problem, logins) if w), "")
    return why


async def status() -> dict:
    clips = await soundboard_clips()
    rows = []
    for i, t in enumerate(CONFIG["triggers"]):
        s = STATS.get(t["id"]) or {"played": 0, "last": 0.0, "by": ""}
        rows.append({**t, "problem": problem(t, CONFIG["triggers"][:i], clips), "played": s["played"],
                     "last": s["last"] or None, "by": s["by"]})
    channels = []
    for r in tc.configured_channels():
        channels.append({"login": r["login"], "label": r.get("label", ""), "enabled": r.get("enabled", True),
                         "redeems": _redeem_problem(r["login"]), "chat": _chat_problem(r["login"])})
    return {"on": CONFIG["on"], "triggers": rows, "recent": list(RECENT)[::-1], "channels": channels,
            "clips": clips, "panels": len(HUB.panels), "now": time.time()}


class Hub:
    def __init__(self) -> None:
        self.panels: set[WebSocket] = set()

    async def status(self) -> None:
        if not self.panels:
            return
        text = json.dumps({"type": "status", "status": await status()})
        for ws in list(self.panels):
            try:
                await ws_send(ws, text)
            except Exception:
                self.panels.discard(ws)


HUB = Hub()


async def fetch_rewards(login: str = "") -> list[dict]:
    """The channel's custom rewards, as Twitch lists them to its own account. Raises TwitchError."""
    ch = tc.channel_for(login)
    acct = tc.acting_account(ch, "channel:read:redemptions", own=True)
    broadcaster = await tc._channel_id(ch, acct)
    r = await tc.helix("GET", "/channel_points/custom_rewards", acct=acct, params={"broadcaster_id": broadcaster})
    if r.status_code == 403:
        raise tc.TwitchError(tc._refusal(r, "Twitch would not list the rewards") +
                             " - channel points need an Affiliate or Partner channel")
    if r.status_code != 200:
        raise tc.TwitchError(tc._refusal(r, "Twitch would not list the rewards"))
    out = []
    for w in r.json().get("data") or []:
        img = w.get("image") or w.get("default_image") or {}
        out.append({"id": w.get("id", ""), "title": w.get("title", ""), "cost": w.get("cost", 0),
                    "enabled": bool(w.get("is_enabled", True)), "paused": bool(w.get("is_paused", False)),
                    "image": img.get("url_1x", ""), "color": w.get("background_color", "")})
    out.sort(key=lambda w: (w["cost"], w["title"].lower()))
    return out


# --------------------------------------------------------------------------
# routes - the tab's (there is no bot API)
# --------------------------------------------------------------------------

router = APIRouter(prefix="/redeems", tags=["bot_free_redeems"])
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


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
    await tc.ensure_started()                       # (an older Hexcast connects on the first request)
    return HTMLResponse((STATIC_DIR / "redeems_panel.html").read_text(encoding="utf-8"), headers=_NOCACHE)


@router.get("/api/status")
async def api_status():
    return await status()


@router.get("/api/config")
async def api_config():
    return CONFIG


@router.post("/api/config")
async def api_set_config(request: Request):
    """{on?, triggers?} - what is left out stays; `triggers` replaces the whole list."""
    global CONFIG
    try:
        body = await _body(request)
        CONFIG = save_config({**CONFIG, **{k: body[k] for k in ("on", "triggers") if k in body}})
    except Problem as exc:
        return _bad(exc)
    keep = {t["id"] for t in CONFIG["triggers"]}
    for d in (STATS, LAST_CLIP):
        for k in [k for k in d if k not in keep]:
            d.pop(k, None)
    await HUB.status()
    return {"ok": True, "config": CONFIG}


@router.get("/api/rewards")
async def api_rewards(channel: str = ""):
    """The rewards of a channel (the primary by default), for the tab's reward picker."""
    try:
        rewards = await fetch_rewards(channel)
    except tc.TwitchError as exc:
        return {"ok": False, "error": str(exc), "rewards": []}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Twitch did not answer ({exc})", "rewards": []}
    return {"ok": True, "rewards": rewards}


@router.post("/api/test")
async def api_test(request: Request):
    """{"clips": [...], "message": "...", "channel": "primary"} - play one of the clips now on the Soundboard
    overlay, and post the message in the channel's chat, the way a redeem would."""
    try:
        t = clean_trigger({k: v for k, v in (await _body(request)).items() if k in ("clips", "message", "channel")})
    except Problem as exc:
        return _bad(exc)
    if not t["clips"] and not t["message"]:
        return _bad(Problem("pick a sound or type a chat message first"))
    logins = where(t)
    login = logins[0] if logins else tc.primary_login()
    clip, said, errs = "", False, []
    if t["clips"]:
        clip = random.choice(t["clips"])
        err = await play(clip)
        if err:
            errs.append(err)
            clip = ""
    if t["message"]:
        err = await say(fill(t["message"], login, user=login, bits=100, reward="a reward"), login) if login \
            else "chat message: add your channel in the Twitch connection box first"
        said = not err
        if err:
            errs.append(err)
    note = "; ".join(errs)
    log("test", clip or t["message"][:40], "you (Test button)", login, "played" if clip or said else "failed", note,
        clip=clip, said=said)
    await HUB.status()
    if errs:
        return JSONResponse({"ok": False, "error": note, "clip": clip, "said": said}, status_code=400)
    return {"ok": True, "clip": clip, "said": said}


@router.websocket("/ws")
async def ws_status(ws: WebSocket):
    """The tab's live status: {"type": "status", "status": {...}} now and after anything changes."""
    await ws.accept()
    HUB.panels.add(ws)
    await tc.ensure_started()
    try:
        await ws.send_text(json.dumps({"type": "status", "status": await status()}))
        while True:
            await ws.receive_text()                 # (nothing to answer - this only keeps the socket open)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        HUB.panels.discard(ws)


def attach(app, port: int = 4747) -> None:
    global APP
    APP = app
    app.include_router(router)
    print(f"  Bot-Free Redeems:    http://localhost:{port}/redeems", flush=True)
