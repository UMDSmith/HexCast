"""
HexCast - Giveaways
===================

Prizes with a picture and a description; a draw from everyone in your Twitch chat right now - or only your
followers (for at least some time) and/or subscribers - with a countdown and a jumble of names on an OBS overlay
before the winner shows, then a chat message for them.

    http://localhost:4747/giveaways           -> the tab
    http://localhost:4747/giveaways/overlay   -> the OBS browser source (1920x1080)

The connection to Twitch is the shared twitch_core library: who is in chat is Twitch's own list (lurkers too), and
the follower and subscriber checks are Twitch's. Bots get every call as REST and over the socket /giveaways/ws.
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
from fastapi import APIRouter, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from hexcast_core import paths
from hexcast_plugins.twitch_core import core as tc

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = paths.CONFIG_DIR / "giveaways.json"
IMAGE_DIR = paths.MEDIA_DIR / "giveaways"           # prize pictures, served by the core at /media/giveaways/
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".apng"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

MAX_PRIZES = 100
MAX_CODES = 500
MAX_HISTORY = 200
MAX_FOLLOW_CHECKS = 500             # followers are checked one by one, in the order names are drawn
JUMBLE_NAMES = 40
BOTS = "nightbot, streamelements, streamlabs, moobot, fossabot, wizebot, sery_bot, soundalerts, commanderroot, " \
       "streamlootsbot, kofistreambot, botrixoficial"

DEFAULT_LOOK: dict[str, Any] = {
    "title": "GIVEAWAY",
    "accent": "#ff3b30",
    "accent2": "#a970ff",
    "text_color": "#ffffff",
    "font_title": "Bungee",
    "font_text": "Inter",
    "position": "center",           # center | top | bottom | left | right
    "scale": 100,                   # % of the card's size
    "confetti": True,
    "show_description": True,
}
DEFAULT_CONFIG: dict[str, Any] = {
    "channel": "primary",           # whose chat the winner comes from: primary | a channel's login
    "eligible": {"everyone": True, "followers": False, "follow_seconds": 0, "subs": False},
    "exclude": BOTS,                # never win (the broadcaster and Hexcast's own accounts never do either)
    "exclude_winners": True,        # someone already in the winners list cannot win again (until it is cleared)
    "countdown": 10,                # seconds of names jumbling before the winner shows
    "hold": 15,                     # seconds the winner stays on screen (0 = until Hide)
    "announce": True,
    "announce_delay": 5,            # seconds after the reveal: about your stream delay, so chat does not spoil it
    "message": "Congratulations @{winner}, you won {prize}!",
    "sound_start": "",              # soundboard clips: when the draw starts (a drumroll) ...
    "sound_win": "",                # ... and when the winner shows
    "look": DEFAULT_LOOK,
    "prizes": [],
    "history": [],
}
PRIZE_DEFAULTS: dict[str, Any] = {"id": "", "name": "", "description": "", "image": "", "quantity": 1, "codes": []}
NUMBERS = {"countdown": (3, 60), "hold": (0, 600), "announce_delay": (0, 60), "quantity": (0, 9999),
           "follow_seconds": (0, 10 * 365 * 86400), "scale": (40, 200)}
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
PRIZE_ID = re.compile(r"^[a-z0-9]{4,16}$")


class Problem(Exception):
    """Something that cannot be done, said for the person who asked."""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


def _num(value: Any, key: str) -> int:
    lo, hi = NUMBERS[key]
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise Problem(f"{key} is a number") from None
    if not lo <= n <= hi:
        raise Problem(f"{key} is {lo} to {hi}")
    return n


def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def clean_prize(raw: Any, base: dict | None = None) -> dict:
    if not isinstance(raw, dict):
        raise Problem("a prize is an object")
    p = {**PRIZE_DEFAULTS, **(base or {})}
    if "id" in raw:
        p["id"] = _text(raw["id"], 16).lower()
    if "name" in raw:
        p["name"] = _text(raw["name"], 80)
    if "description" in raw:
        p["description"] = _text(raw["description"], 400)
    if "image" in raw:
        p["image"] = _image_url(raw["image"])
    if "quantity" in raw:
        p["quantity"] = _num(raw["quantity"], "quantity")
    if "codes" in raw:
        codes = raw["codes"]
        if isinstance(codes, str):
            codes = codes.splitlines()
        if not isinstance(codes, list):
            raise Problem("codes is a list (or one per line)")
        codes = [c for c in (_text(x, 200) for x in codes) if c]
        if len(codes) > MAX_CODES:
            raise Problem(f"at most {MAX_CODES} codes for one prize")
        p["codes"] = codes
    if not PRIZE_ID.match(str(p.get("id") or "")):
        p["id"] = secrets.token_hex(4)
    return {k: p[k] for k in PRIZE_DEFAULTS}


def _image_url(value: Any) -> str:
    """A prize picture: one from the library (/media/giveaways/...) or any http(s) address; '' for none."""
    url = _text(value, 500)
    if not url:
        return ""
    if url.startswith("/media/") and ".." not in url:
        return url
    if re.match(r"^https?://", url):
        return url
    raise Problem("the picture is one from the library or an http(s) address")


def clean_eligible(raw: Any) -> dict:
    e = dict(DEFAULT_CONFIG["eligible"])
    if isinstance(raw, dict):
        for key in ("everyone", "followers", "subs"):
            if key in raw:
                e[key] = bool(raw[key])
        if "follow_seconds" in raw:
            e["follow_seconds"] = _num(raw["follow_seconds"], "follow_seconds")
    return e


def clean_look(raw: Any) -> dict:
    look = dict(DEFAULT_LOOK)
    if not isinstance(raw, dict):
        return look
    for key in ("accent", "accent2", "text_color"):
        if key in raw:
            if not COLOR.match(str(raw[key])):
                raise Problem(f"{key} is a colour like #ff3b30")
            look[key] = str(raw[key]).lower()
    for key in ("title", "font_title", "font_text"):
        if key in raw:
            look[key] = re.sub(r"[^\w '\-!?.&]+", "", _text(raw[key], 40))
    if "position" in raw:
        if raw["position"] not in ("center", "top", "bottom", "left", "right"):
            raise Problem("position is center, top, bottom, left or right")
        look["position"] = raw["position"]
    if "scale" in raw:
        look["scale"] = _num(raw["scale"], "scale")
    for key in ("confetti", "show_description"):
        if key in raw:
            look[key] = bool(raw[key])
    return look


def clean_config(raw: Any, keep_history: list | None = None) -> dict:
    """The settings checked. `history` is the server's own (only kept from `keep_history`, or the file)."""
    if not isinstance(raw, dict):
        raw = {}
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if "channel" in raw:
        ch = tc.clean_login(raw["channel"]) or "primary"
        if ch != "primary" and not tc.CHANNEL_LOGIN.match(ch):
            raise Problem(f"'{raw['channel']}' is not a channel")
        cfg["channel"] = ch
    cfg["eligible"] = clean_eligible(raw.get("eligible"))
    if "exclude" in raw:
        names = raw["exclude"] if isinstance(raw["exclude"], list) else str(raw["exclude"] or "").replace("\n", ",").split(",")
        cfg["exclude"] = ", ".join(dict.fromkeys(n for n in (tc.clean_login(x).lstrip("@") for x in names) if n))[:2000]
    for key in ("countdown", "hold", "announce_delay"):
        if key in raw:
            cfg[key] = _num(raw[key], key)
    for key in ("exclude_winners", "announce"):
        if key in raw:
            cfg[key] = bool(raw[key])
    if "message" in raw:
        cfg["message"] = _text(raw["message"], tc.CHAT_LIMIT)
    for key in ("sound_start", "sound_win"):
        if key in raw:
            cfg[key] = _text(raw[key], 120)
    cfg["look"] = clean_look(raw.get("look"))
    prizes = raw.get("prizes", [])
    if not isinstance(prizes, list):
        raise Problem("prizes is a list")
    if len(prizes) > MAX_PRIZES:
        raise Problem(f"at most {MAX_PRIZES} prizes")
    seen: set[str] = set()
    for row in prizes:
        p = clean_prize(row)
        while p["id"] in seen:
            p["id"] = secrets.token_hex(4)
        seen.add(p["id"])
        cfg["prizes"].append(p)
    history = keep_history if keep_history is not None else raw.get("history", [])
    cfg["history"] = [h for h in history if isinstance(h, dict)][:MAX_HISTORY] if isinstance(history, list) else []
    return cfg


def load_config() -> dict:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    try:
        return clean_config(raw)
    except Problem:
        return clean_config({"history": raw.get("history", []) if isinstance(raw, dict) else []})


def save_config(cfg: dict) -> dict:
    cfg = clean_config(cfg, cfg.get("history", []))
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


CONFIG = load_config()


def find_prize(prize_id: str) -> dict:
    for p in CONFIG["prizes"]:
        if p["id"] == str(prize_id):
            return p
    raise Problem(f"no prize '{prize_id}'", 404)


def public_prize(p: dict) -> dict:
    """A prize as the overlay and bots see it: never its codes."""
    return {"id": p.get("id", ""), "name": p.get("name", ""), "description": p.get("description", ""),
            "image": p.get("image", ""), "quantity": p.get("quantity", 0), "codes": len(p.get("codes") or [])}


# --------------------------------------------------------------------------
# who can win
# --------------------------------------------------------------------------

def draw_channel(channel: Any = "") -> str:
    """The channel a draw comes from: the one asked for, else the configured one, else the primary."""
    login = tc.clean_login(channel) or CONFIG["channel"]
    return tc.primary_login() if login == "primary" else login


def excluded(channel: str, extra: set[str] | None = None) -> set[str]:
    out = {channel} | tc.own_logins() | set(extra or ())
    out |= {n.strip().lower() for n in CONFIG["exclude"].split(",") if n.strip()}
    if CONFIG["exclude_winners"]:
        out |= {str((h.get("winner") or {}).get("login", "")).lower() for h in CONFIG["history"] if not h.get("rerolled")}
    return out - {""}


async def pool(channel: str, extra: set[str] | None = None) -> tuple[list[dict], int]:
    """Who is in chat right now, less who never wins: ([{id, login, name}], how many were in chat)."""
    roster = await tc.chatters(channel)
    skip = excluded(channel, extra)
    out = []
    for u in roster:
        login = str(u.get("user_login") or "").lower()
        if login and login not in skip:
            out.append({"id": str(u.get("user_id") or ""), "login": login, "name": u.get("user_name") or login})
    return out, len(roster)


def _groups(eligible: dict) -> str:
    if eligible["everyone"]:
        return "everyone in chat"
    parts = []
    if eligible["followers"]:
        parts.append("followers" + (f" (for {_span(eligible['follow_seconds'])})" if eligible["follow_seconds"] else ""))
    if eligible["subs"]:
        parts.append("subscribers")
    return " or ".join(parts)


def _span(seconds: int) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size and seconds % size == 0:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{seconds} seconds"


async def pick(candidates: list[dict], eligible: dict, channel: str) -> dict | None:
    """A winner, every eligible viewer with the same chance: shuffle, then take the first who qualifies (a
    subscriber, or a follower for long enough - whichever groups are ticked). Followers are looked up one at a
    time in that order, so a big chat costs a few lookups, not thousands."""
    order = list(candidates)
    secrets.SystemRandom().shuffle(order)
    if eligible["everyone"]:
        return order[0] if order else None
    if not (eligible["followers"] or eligible["subs"]):
        raise Problem("tick who can win: everyone in chat, followers or subscribers")
    subs = await tc.subscribers([u["id"] for u in order], channel) if eligible["subs"] and order else {}
    checks = 0
    now = time.time()
    for u in order:
        if u["id"] in subs:
            return u
        if eligible["followers"]:
            if checks >= MAX_FOLLOW_CHECKS:
                raise Problem(f"none of the first {MAX_FOLLOW_CHECKS} names drawn follows long enough - "
                              f"try again, or loosen who can win")
            checks += 1
            since = await tc.followed_at(u["id"], channel)
            if since is not None and now - since >= eligible["follow_seconds"]:
                return u
    return None


# --------------------------------------------------------------------------
# the show on the overlay
# --------------------------------------------------------------------------

SHOW: dict[str, Any] = {"state": "idle"}        # idle | showing {prize} | drawing / revealed {the draw}
TASK: asyncio.Task | None = None
LOCK = asyncio.Lock()
TEST_NAMES = ["PixelPanda", "NightOwl_42", "CaffeineQueen", "LagWizard", "MossyBoots", "TurboTurnip", "SirClutch",
              "GlitchGoblin", "Waffle_Knight", "LunaLurker", "BitBandit", "CosmicCrumb", "HypeHippo", "Snackrifice"]


def overlay_view(show: dict) -> dict:
    """What an overlay needs: never the codes, never who was excluded; how far a draw is (for one that joins late)."""
    out = {k: v for k, v in show.items() if k not in ("reroll_of", "entry")}
    if show.get("state") in ("drawing", "revealed"):
        out["elapsed"] = round(time.time() - show["started"], 3)
    return out


async def play(clip: str) -> None:
    """A soundboard clip by name, the way a bot would trigger it (in-process); a missing one only logs."""
    if not clip or APP is None:
        return
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url="http://hexcast", timeout=6) as c:
            r = await c.get(f"/api/play/{quote(clip, safe='')}")
        if r.status_code >= 400:
            tc.note(f"giveaways: no soundboard clip '{clip}'")
    except Exception as exc:
        tc.note(f"giveaways: clip '{clip}' failed: {exc}")


def fill(text: str, values: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1).lower(), m.group(0))), text)


def record(draw: dict) -> dict:
    """The winner goes in the list, the prize has one fewer (and gives up a code, if it has any). A reroll first
    gives back what the winner it replaces got."""
    prize = next((p for p in CONFIG["prizes"] if p["id"] == draw["prize"]["id"]), None)
    old = next((h for h in CONFIG["history"] if h.get("id") == draw.get("reroll_of")), None)
    if old is not None and not old.get("rerolled"):
        old["rerolled"] = True
        if prize is not None:
            prize["quantity"] = min(NUMBERS["quantity"][1], prize["quantity"] + 1)
            if old.get("code"):
                prize["codes"].insert(0, old["code"])
    code = ""
    if prize is not None:
        prize["quantity"] = max(0, prize["quantity"] - 1)
        code = prize["codes"].pop(0) if prize["codes"] else ""
    entry = {"id": secrets.token_hex(4), "at": time.time(), "prize_id": draw["prize"]["id"], "prize": draw["prize"]["name"],
             "winner": draw["winner"], "channel": draw["channel"], "code": code, "rerolled": False,
             "groups": draw.get("groups", ""), "pool": draw.get("pool", 0)}
    CONFIG["history"].insert(0, entry)
    del CONFIG["history"][MAX_HISTORY:]
    try:
        save_config(CONFIG)
    except (OSError, Problem) as exc:               # the winner stands; only the file could not be written
        tc.note(f"giveaways: could not save the winners list: {exc}")
    return entry


async def announce(draw: dict) -> None:
    await asyncio.sleep(CONFIG["announce_delay"])
    text = fill(CONFIG["message"], {"winner": draw["winner"]["name"], "login": draw["winner"]["login"],
                                    "prize": draw["prize"]["name"], "channel": draw["channel"]})
    try:
        await tc.send_chat(text, draw["channel"])
    except tc.TwitchError as exc:
        tc.note(f"giveaways: could not announce the winner in chat: {exc}")


async def _sequence(draw: dict) -> None:
    try:
        await _show(draw)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                        # (a task nobody awaits: say what went wrong)
        tc.note(f"giveaways: the draw failed: {type(exc).__name__}: {exc}")


async def _show(draw: dict) -> None:
    """The countdown, the reveal (the winner recorded, announced, the sound), then - after `hold` - away again."""
    global SHOW
    await asyncio.sleep(draw["countdown"])
    if SHOW.get("id") != draw["id"]:
        return
    SHOW = {**SHOW, "state": "revealed", "revealed_at": time.time()}
    if not draw.get("test"):
        entry = record(draw)
        SHOW["entry"] = entry["id"]
        await HUB.event({"event": "winner", "winner": draw["winner"], "prize": draw["prize"],
                         "channel": draw["channel"], "entry": entry["id"]})
        if CONFIG["announce"] and CONFIG["message"]:
            asyncio.create_task(announce(draw))
        tc.note(f"giveaways: {draw['winner']['name']} won {draw['prize']['name']} (#{draw['channel']})")
    await play(CONFIG["sound_win"])
    await HUB.overlays()
    await HUB.status()
    if draw["hold"] > 0:
        await asyncio.sleep(draw["hold"])
        if SHOW.get("id") == draw["id"]:
            SHOW = {"state": "idle"}
            await HUB.overlays()
            await HUB.status()


def _jumble(order: list[dict], winner: dict) -> list[str]:
    names = [u["name"] for u in order if u["login"] != winner["login"]][:JUMBLE_NAMES]
    return names or [winner["name"]]


async def start_show(draw: dict) -> None:
    global SHOW, TASK
    await _stop_task()
    SHOW = draw
    TASK = asyncio.create_task(_sequence(draw))
    await play(CONFIG["sound_start"])
    await HUB.overlays()
    await HUB.status()


async def _stop_task() -> None:
    global TASK
    if TASK is not None and not TASK.done() and TASK is not asyncio.current_task():
        TASK.cancel()
        try:
            await TASK
        except (asyncio.CancelledError, Exception):
            pass
    TASK = None


async def draw(prize_id: str, channel: Any = "", eligible: Any = None, reroll_of: str = "") -> dict:
    """Draw a winner for a prize from who is in chat now, and start the show. Raises Problem."""
    if SHOW.get("state") == "drawing":
        raise Problem("a draw is running - wait for it, or cancel it", 409)
    prize = find_prize(prize_id)
    if prize["quantity"] <= 0 and not reroll_of:
        raise Problem(f"no {prize['name'] or 'prize'} left - raise its quantity first")
    login = draw_channel(channel)
    if not login:
        raise Problem("no Twitch channel set - add your channel in the Twitch connection box")
    elig = clean_eligible({**CONFIG["eligible"], **(eligible if isinstance(eligible, dict) else {})})
    extra: set[str] = set()
    if reroll_of:
        old = next((h for h in CONFIG["history"] if h.get("id") == reroll_of), None)
        if old:
            extra.add(str((old.get("winner") or {}).get("login", "")).lower())
    try:
        candidates, in_chat = await pool(login, extra)
        winner = await pick(candidates, elig, login)
    except tc.TwitchError as exc:
        raise Problem(str(exc)) from None
    if winner is None:
        who = _groups(elig)
        raise Problem(f"nobody can win: {in_chat} in chat, {len(candidates)} after the exclusions, none of them {who}"
                      if candidates else f"nobody to draw from: {in_chat} in chat, none left after the exclusions")
    order = list(candidates)
    secrets.SystemRandom().shuffle(order)
    d = {"state": "drawing", "id": secrets.token_hex(6), "test": False, "prize": public_prize(prize),
         "winner": winner, "names": _jumble(order, winner), "countdown": CONFIG["countdown"], "hold": CONFIG["hold"],
         "started": time.time(), "channel": login, "pool": len(candidates), "in_chat": in_chat,
         "groups": _groups(elig), "reroll_of": reroll_of}
    await start_show(d)
    return d


async def test_draw(prize_id: str = "", where: str = "stream") -> dict:
    """The show with made-up names: nothing is drawn from chat, recorded or announced. `where`: stream (every
    overlay) or preview (only the tab's preview)."""
    prize = public_prize(find_prize(prize_id)) if prize_id else (
        public_prize(CONFIG["prizes"][0]) if CONFIG["prizes"] else
        {"id": "", "name": "Mystery prize", "description": "A sample prize, to try the look", "image": "", "quantity": 1, "codes": 0})
    names = list(TEST_NAMES)
    secrets.SystemRandom().shuffle(names)
    winner = {"id": "", "login": names[0].lower(), "name": names[0]}
    d = {"state": "drawing", "id": secrets.token_hex(6), "test": True, "prize": prize, "winner": winner,
         "names": names[1:], "countdown": CONFIG["countdown"], "hold": CONFIG["hold"] or 8, "started": time.time(),
         "channel": tc.primary_login(), "pool": len(names), "in_chat": len(names), "groups": "a test"}
    if where == "preview":
        await HUB.overlays(d, preview_only=True)
    else:
        if SHOW.get("state") == "drawing":
            raise Problem("a draw is running - wait for it, or cancel it", 409)
        await start_show(d)
    return d


# --------------------------------------------------------------------------
# status, the sockets
# --------------------------------------------------------------------------

def connection_problem() -> str:
    """Why a draw cannot work in the configured channel, or ''."""
    try:
        ch = tc.channel_for("" if CONFIG["channel"] == "primary" else CONFIG["channel"])
        tc.acting_account(ch, "moderator:read:chatters")
        if CONFIG["eligible"]["subs"] and not CONFIG["eligible"]["everyone"]:
            tc.acting_account(ch, "channel:read:subscriptions", own=True)
    except tc.TwitchError as exc:
        return str(exc)
    return ""


def status() -> dict:
    hist = [{k: v for k, v in h.items() if k != "code"} | {"has_code": bool(h.get("code"))} for h in CONFIG["history"][:25]]
    return {"show": overlay_view(SHOW), "prizes": [public_prize(p) for p in CONFIG["prizes"]],
            "eligible": CONFIG["eligible"], "channel": draw_channel(), "problem": connection_problem(),
            "history": hist, "overlays": len(HUB.overlay), "channels": [r["login"] for r in tc.configured_channels()]}


class Hub:
    def __init__(self) -> None:
        self.overlay: dict[WebSocket, bool] = {}     # overlay socket -> is it the tab's preview?
        self.panels: set[WebSocket] = set()

    @staticmethod
    async def _send(ws: WebSocket, text: str) -> bool:
        try:
            await ws.send_text(text)
            return True
        except Exception:
            return False

    async def overlays(self, show: dict | None = None, preview_only: bool = False) -> None:
        text = json.dumps({"type": "show", "show": overlay_view(SHOW if show is None else show), "look": CONFIG["look"],
                           "preview_only": preview_only})
        for ws, preview in list(self.overlay.items()):
            if (preview or not preview_only) and not await self._send(ws, text):
                self.overlay.pop(ws, None)

    async def look(self) -> None:
        text = json.dumps({"type": "look", "look": CONFIG["look"]})
        for ws in list(self.overlay):
            if not await self._send(ws, text):
                self.overlay.pop(ws, None)

    async def _panels(self, payload: dict) -> None:
        text = json.dumps(payload)
        for ws in list(self.panels):
            if not await self._send(ws, text):
                self.panels.discard(ws)

    async def status(self) -> None:
        if self.panels:
            await self._panels({"type": "status", "status": status()})

    async def event(self, payload: dict) -> None:
        await self._panels({"type": "event", **payload})


HUB = Hub()


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/giveaways", tags=["giveaways"])
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
APP = None                                  # the app: soundboard clips and socket commands run in-process


def _bad(exc: Problem) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=getattr(exc, "code", 400))


async def _body(request: Request) -> dict:
    if not await request.body():
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise Problem("send a JSON object")
    return data


def _page(name: str) -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / name).read_text(encoding="utf-8"), headers=_NOCACHE)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    await tc.ensure_started()
    return _page("giveaways_panel.html")


@router.get("/overlay", response_class=HTMLResponse)
async def overlay():
    return _page("giveaways_overlay.html")


@router.get("/api/status")
async def api_status():
    """What the overlay shows now, the prizes (without their codes), who can win, the latest winners, and why a
    draw cannot work (if so)."""
    return status()


@router.get("/api/config")
async def api_config():
    """Every setting, the prizes WITH their codes, and the winners list (with the codes they got)."""
    return CONFIG


@router.post("/api/config")
async def api_set_config(request: Request):
    """Any settings ({channel, eligible, exclude, exclude_winners, countdown, hold, announce, announce_delay, message,
    sound_start, sound_win, look, prizes}); what is left out stays. The winners list is not set here."""
    global CONFIG
    try:
        body = await _body(request)
        merged = {**CONFIG, **{k: v for k, v in body.items() if k in DEFAULT_CONFIG and k != "history"}}
        if isinstance(body.get("eligible"), dict):
            merged["eligible"] = {**CONFIG["eligible"], **body["eligible"]}
        if isinstance(body.get("look"), dict):
            merged["look"] = {**CONFIG["look"], **body["look"]}
        CONFIG = save_config(merged)
    except Problem as exc:
        return _bad(exc)
    await HUB.look()
    await HUB.status()
    return {"ok": True, "config": CONFIG}


@router.post("/api/prizes")
async def api_add_prize(request: Request):
    """A new prize: {name, description?, image?, quantity?, codes?}."""
    global CONFIG
    try:
        body = await _body(request)
        if len(CONFIG["prizes"]) >= MAX_PRIZES:
            raise Problem(f"at most {MAX_PRIZES} prizes")
        p = clean_prize({k: v for k, v in body.items() if k != "id"})
        if not p["name"]:
            raise Problem("a prize needs its name")
        CONFIG = save_config({**CONFIG, "prizes": CONFIG["prizes"] + [p]})
    except Problem as exc:
        return _bad(exc)
    await HUB.status()
    return {"ok": True, "prize": CONFIG["prizes"][-1]}


@router.post("/api/prizes/{prize_id}")
async def api_update_prize(prize_id: str, request: Request):
    """Change some of a prize's settings (only the fields you send)."""
    global CONFIG
    try:
        p = clean_prize({k: v for k, v in (await _body(request)).items() if k != "id"}, find_prize(prize_id))
        CONFIG = save_config({**CONFIG, "prizes": [p if x["id"] == prize_id else x for x in CONFIG["prizes"]]})
    except Problem as exc:
        return _bad(exc)
    await HUB.status()
    return {"ok": True, "prize": find_prize(prize_id)}


@router.post("/api/prizes/{prize_id}/delete")
async def api_delete_prize(prize_id: str):
    global CONFIG
    try:
        find_prize(prize_id)
    except Problem as exc:
        return _bad(exc)
    CONFIG = save_config({**CONFIG, "prizes": [x for x in CONFIG["prizes"] if x["id"] != prize_id]})
    await HUB.status()
    return {"ok": True}


@router.post("/api/show")
async def api_show(request: Request):
    """{"prize": id} - put the prize on the overlay (up for grabs) until a draw or Hide."""
    global SHOW
    try:
        body = await _body(request)
        prize = find_prize(str(body.get("prize") or ""))
        if SHOW.get("state") == "drawing":
            raise Problem("a draw is running - wait for it, or cancel it", 409)
    except Problem as exc:
        return _bad(exc)
    await _stop_task()
    SHOW = {"state": "showing", "id": secrets.token_hex(6), "prize": public_prize(prize)}
    await HUB.overlays()
    await HUB.status()
    return {"ok": True, "show": overlay_view(SHOW)}


@router.post("/api/hide")
async def api_hide():
    """Take the overlay down (the prize shown, or a winner on screen). A draw still counting down: use cancel."""
    global SHOW
    if SHOW.get("state") == "drawing":
        return _bad(Problem("a draw is running - cancel it to stop it", 409))
    await _stop_task()
    SHOW = {"state": "idle"}
    await HUB.overlays()
    await HUB.status()
    return {"ok": True}


@router.post("/api/draw")
async def api_draw(request: Request):
    """{"prize": id, "channel"?: login, "eligible"?: {everyone, followers, follow_seconds, subs}} - draw a winner from
    who is in chat now (the settings' groups unless `eligible` says otherwise for this draw) and start the show.
    The winner comes back at once; the overlay reveals them after the countdown."""
    try:
        body = await _body(request)
        async with LOCK:
            d = await draw(str(body.get("prize") or ""), body.get("channel"), body.get("eligible"))
    except Problem as exc:
        return _bad(exc)
    return {"ok": True, "draw": overlay_view(d)}


@router.post("/api/reroll")
async def api_reroll(request: Request):
    """Draw again for the last winner's prize (or {"entry": id} for an earlier one), without them: the winner
    replaced gives the prize (and its code) back when the new one shows."""
    try:
        body = await _body(request)
        entry_id = str(body.get("entry") or SHOW.get("entry") or "")
        entry = next((h for h in CONFIG["history"] if h.get("id") == entry_id), None) if entry_id else \
            next((h for h in CONFIG["history"] if not h.get("rerolled")), None)
        if entry is None:
            raise Problem("no winner to draw again for")
        if entry.get("rerolled"):
            raise Problem("that winner was already drawn again")
        async with LOCK:
            d = await draw(entry["prize_id"], entry.get("channel"), body.get("eligible"), reroll_of=entry["id"])
    except Problem as exc:
        return _bad(exc)
    return {"ok": True, "draw": overlay_view(d)}


@router.post("/api/cancel")
async def api_cancel():
    """Stop a draw that is counting down: nobody wins, the overlay clears."""
    global SHOW
    if SHOW.get("state") != "drawing":
        return _bad(Problem("no draw is counting down"))
    await _stop_task()
    SHOW = {"state": "idle"}
    await HUB.overlays()
    await HUB.status()
    return {"ok": True}


@router.post("/api/test")
async def api_test(request: Request):
    """{"prize"?: id, "where"?: "stream" | "preview"} - the whole show with made-up names, to try the look."""
    try:
        body = await _body(request)
        d = await test_draw(str(body.get("prize") or ""), "preview" if body.get("where") == "preview" else "stream")
    except Problem as exc:
        return _bad(exc)
    return {"ok": True, "draw": overlay_view(d)}


@router.get("/api/pool")
async def api_pool(channel: str = ""):
    """How many could win right now: in chat, and after the exclusions (and how many subscribe, when subscribers
    are one of the groups). Followers are only checked as names are drawn."""
    login = draw_channel(channel)
    try:
        if not login:
            raise Problem("no Twitch channel set - add your channel in the Twitch connection box")
        candidates, in_chat = await pool(login)
        e = CONFIG["eligible"]
        subs = None
        if e["subs"] and not e["everyone"]:
            subs = len(await tc.subscribers([u["id"] for u in candidates], login))
    except tc.TwitchError as exc:
        return _bad(Problem(str(exc)))
    except Problem as exc:
        return _bad(exc)
    return {"ok": True, "channel": login, "in_chat": in_chat, "pool": len(candidates), "subs": subs,
            "groups": _groups(CONFIG["eligible"])}


@router.post("/api/history/clear")
async def api_clear_history():
    """Empty the winners list (and with it who counts as a past winner)."""
    global CONFIG
    CONFIG = save_config({**CONFIG, "history": []})
    await HUB.status()
    return {"ok": True}


def _list_images() -> list[dict]:
    if not IMAGE_DIR.is_dir():
        return []
    return [{"name": p.name, "url": f"/media/giveaways/{p.name}"} for p in sorted(IMAGE_DIR.iterdir())
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS]


@router.get("/api/images")
async def api_images():
    """The prize pictures uploaded so far."""
    return {"images": _list_images()}


@router.post("/api/images")
async def api_upload_image(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        return _bad(Problem(f"use png, jpg, gif, webp or apng (got {ext or 'no extension'})"))
    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        return _bad(Problem("file too big (max 25 MB)"))
    stem = _SAFE_NAME.sub("_", Path(file.filename or "prize").stem).strip("._-") or "prize"
    name = stem[:60] + ext
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    (IMAGE_DIR / name).write_bytes(content)
    return {"ok": True, "name": name, "url": f"/media/giveaways/{name}", "images": _list_images()}


@router.post("/api/images/delete")
async def api_delete_image(request: Request):
    try:
        body = await _body(request)
    except Problem as exc:
        return _bad(exc)
    name = Path(str(body.get("name") or "")).name           # .name: a crafted path cannot leave the folder
    if not name:
        return _bad(Problem("name required"))
    (IMAGE_DIR / name).unlink(missing_ok=True)
    return {"ok": True, "images": _list_images()}


# The socket does what the REST API does: each of these IS its REST call, run in-process ({id} comes from the
# message's `id`; the rest of the message is the body).
SOCKET_CALLS: dict[str, tuple[str, str]] = {
    "status": ("GET", "/api/status"),
    "config": ("GET", "/api/config"),
    "set_config": ("POST", "/api/config"),
    "add_prize": ("POST", "/api/prizes"),
    "update_prize": ("POST", "/api/prizes/{id}"),
    "delete_prize": ("POST", "/api/prizes/{id}/delete"),
    "show": ("POST", "/api/show"),
    "hide": ("POST", "/api/hide"),
    "draw": ("POST", "/api/draw"),
    "reroll": ("POST", "/api/reroll"),
    "cancel": ("POST", "/api/cancel"),
    "test": ("POST", "/api/test"),
    "pool": ("GET", "/api/pool"),
    "clear_history": ("POST", "/api/history/clear"),
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
    params = {k: v for k, v in fields.items() if isinstance(v, (str, int, float))} if method == "GET" else None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=APP), base_url="http://hexcast", timeout=60) as cl:
        r = await cl.request(method, router.prefix + path, json=fields if method == "POST" else None, params=params)
    try:
        j = r.json()
    except ValueError:
        j = None
    if r.status_code >= 400:
        err = (j.get("error") or j.get("detail")) if isinstance(j, dict) else None
        return {"ok": False, "error": str(err or f"{kind} failed ({r.status_code})")}
    return {**j, "ok": True} if isinstance(j, dict) else {"ok": True, "result": j}


@router.websocket("/ws/overlay")
async def ws_overlay(ws: WebSocket, preview: str = ""):
    """The overlay's socket (?preview=1: the tab's preview, which also gets the tab's preview-only tests)."""
    await ws.accept()
    HUB.overlay[ws] = preview in ("1", "true", "yes")
    try:
        await ws.send_text(json.dumps({"type": "show", "show": overlay_view(SHOW), "look": CONFIG["look"]}))
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.overlay.pop(ws, None)


@router.websocket("/ws")
async def ws_giveaways(ws: WebSocket):
    """The tab's live status and winner events, and for bots every REST call by name:
    {"cmd": "draw", "prize": "...", "rid": 1}. Replies carry the `rid` you sent; {"type": "event", "event":
    "winner", ...} arrives when a winner shows."""
    await ws.accept()
    HUB.panels.add(ws)
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


async def shutdown() -> None:
    global SHOW
    await _stop_task()
    SHOW = {"state": "idle"}


def attach(app, port: int = 4747) -> None:
    global APP
    APP = app
    app.include_router(router)
    print(f"  Giveaways:           http://localhost:{port}/giveaways", flush=True)
    print(f"  Giveaway source:     http://localhost:{port}/giveaways/overlay", flush=True)
