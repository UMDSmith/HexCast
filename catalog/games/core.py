"""
HexCast - Games module
======================

A Hexcast plugin (see plugin.py): install it from the + tab and it adds:
    http://localhost:4747/games                      -> control panel (spin, history, settings, placement)
    http://localhost:4747/games/overlay              -> OBS browser source (?game=roulette to filter)
    http://localhost:4747/games/api                  -> bot API index
    http://localhost:4747/games/api/roulette/spin    -> spin the wheel (GET or POST, alias /play)
    http://localhost:4747/games/api/roulette/bet     -> put chat bets on the roulette table (the next spin)
    http://localhost:4747/games/api/craps/bet        -> put chat bets on the craps table (hexcoins)
    http://localhost:4747/games/api/craps/roll       -> throw the dice (GET or POST, alias /spin, /play)
    http://localhost:4747/games/api/{game}/timer     -> start the countdown now (spins / rolls at zero)
    http://localhost:4747/games/api/ledger           -> every coin movement, by seq (for the bank bot)
    http://localhost:4747/games/api/roulette/announce -> show the bot's own winners card (it did the math)
    http://localhost:4747/games/api/craps/board      -> show the bot's own "on the table" board
    http://localhost:4747/games/api/russian/start    -> start a Russian Roulette game (/bet, /cashout, /next ...)
    http://localhost:4747/games/api/trivia/start     -> start a Trivia game (/bet, /answer, /ride, /cashout ...)
    ws://localhost:4747/games/ws/overlay             -> overlay feed
    ws://localhost:4747/games/ws/panel               -> panel feed (+ "ledger" pushes, one per game)

A small framework for programmatic, bot-driven stream games. Every game is a
subclass of `Game` registered in `GAMES`; the generic /games/api/{game}/...
routes, the spin lifecycle (spinning -> result -> cooldown -> idle), history,
visibility, the countdown and the websocket feed are shared. Roulette is the
first game (an American double-zero wheel, with the full standard bet table
resolved server-side so a chat bot can run a points casino). Craps is the
second: a persistent bank-craps table (bets stay down across rolls), every bet
settled by standard casino rules. Russian Roulette and Trivia are ROUND games
(`RoundGame`: one multi-round game at a time, bets against the bank, see below).
Roulette and craps keep a table (`TableGame`: bets placed
ahead of the spin / roll, persisted) and share one sequenced, persisted ledger
that tells the external bank (the bot holding everyone's hexcoins) exactly
what to debit and credit - Hexcast itself never holds a balance.

Fairness: the SERVER picks the outcome with secrets.SystemRandom(), uniformly
over the wheel's pockets / the dice faces. The overlay only animates the
server's result (the `seed` it gets is for animation variety, not the
outcome), and nothing - API, panel or overlay - can force a result.

The overlay anchors to `elapsed_ms` (never absolute epoch), so clock skew
between the server and the OBS machine never matters.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
import secrets
import tempfile
import time
import unicodedata
import urllib.parse
from collections import deque
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from hexcast_core import paths

log = logging.getLogger("hexcast")

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent          # this plugin's folder
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = paths.CONFIG_DIR
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "games.json"
LEDGER_PATH = CONFIG_DIR / "games_ledger.jsonl"             # every coin movement (every game), one JSON per line


def _read_static(name: str) -> str:
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The Games plugin needs "
            f"games_panel.html and games_overlay.html in its static/ folder."
        )
    return path.read_text(encoding="utf-8")


# A script referenced by one of the pages (the /plugins/games/static/... files in a src
# attribute) gets ?v=<its mtime>: the pages themselves are sent no-store, but the scripts are
# plain static files that OBS / the browser may keep cached for hours - after an update they
# would keep running the old code. A changed file is a new URL. (The game scripts named in
# the registry are stamped the same way, see _asset_url.)
_PAGE_SCRIPT = re.compile(r"(/plugins/games/static/([\w./-]+\.js))(?=[\"'])")


def _versioned(html: str) -> str:
    def stamp(m: re.Match) -> str:
        try:
            return f"{m.group(1)}?v={int((STATIC_DIR / m.group(2)).stat().st_mtime)}"
        except OSError:
            return m.group(1)
    return _PAGE_SCRIPT.sub(stamp, html)


# --------------------------------------------------------------------------
# limits
# --------------------------------------------------------------------------
# Module-level so they're easy to find (and to shrink in tests).

SPIN_SECONDS_MIN = 4.0          # per-spin `duration` and config spin_seconds clamp
SPIN_SECONDS_MAX = 30.0
USER_MAX_CHARS = 40             # caption / bet user length
BET_TEXT_MAX_CHARS = 100        # stored bet string length
MAX_BETS = 200                  # bets per spin (extra entries are dropped)
MAX_AMOUNT = 1e12               # larger stakes are invalid (keeps payouts finite + exact)
HISTORY_MAX = 200               # committed spins kept in memory per game
STATE_HISTORY = 20              # RESULTs carried in every STATE message

COINS_MAX = 10 ** 12            # craps: largest single amount (whole coins)
LEDGER_MEMORY = 10_000          # ledger events kept in memory for /ledger
LEDGER_ROTATE_BYTES = 10 * 1024 * 1024   # games_ledger.jsonl -> .1 at this size
LEDGER_LIMIT_MAX = 5000         # /ledger?limit= cap
TIMER_SECONDS_MIN = 5.0         # /timer: countdown length clamp (config bet_window_seconds too)
TIMER_SECONDS_MAX = 300.0

ANNOUNCE_LINES_MAX = 50         # display: lines kept on a winners card (/announce)
BOARD_LINES_MAX = 100           # display: lines kept on an "on the table" board (/craps/board, /roulette/board)
DISPLAY_TEXT_MAX_CHARS = 60     # display: line text / empty_text length
DISPLAY_TITLE_MAX_CHARS = 40    # display: card / board title length
DISPLAY_CURRENCY_MAX_CHARS = 24
ANNOUNCE_SECONDS_MIN = 1.0      # display: how long a winners card stays up (clamp)
ANNOUNCE_SECONDS_MAX = 120.0

_RNG = secrets.SystemRandom()   # the outcome - OS entropy, never seeded


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

_INVALID = object()   # sentinel: value could not be interpreted at all


def _deep_merge(base: dict, override: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _num(x: float) -> int | float:
    """Tidy a number for JSON: integral floats become ints, others are rounded."""
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, int):
        return x
    x = round(float(x), 6)
    return int(x) if x.is_integer() else x


def _as_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(str(v).strip()) if isinstance(v, str) else float(v)
    except (TypeError, ValueError, OverflowError):   # OverflowError: a huge JSON int
        return None
    return f if math.isfinite(f) else None


def _as_bool(v: Any) -> Any:
    """True/False for anything bool-like, else the _INVALID sentinel."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on", "y", "t"):
            return True
        if s in ("0", "false", "no", "off", "n", "f", ""):
            return False
    return _INVALID


def _flag(v: Any) -> bool:
    """Request flag (wait/test): anything not clearly true is false."""
    b = _as_bool(v)
    return b if b is not _INVALID else False


# Control characters - and lone UTF-16 surrogates (half an emoji from a client that cut a
# string mid-character, sent as a "\ud83d" JSON escape): a str holding one can't be
# encoded as UTF-8, so every JSON answer / broadcast carrying it would fail.
_CTRL = re.compile(r"[\x00-\x1f\x7f\ud800-\udfff]")
_SURROGATES = re.compile(r"[\ud800-\udfff]")


def _joins(prev: str, ch: str) -> bool:
    """Does `ch` continue the character `prev` is part of? A combining mark / variation
    selector, the zero-width joiner, a skin-tone modifier, an emoji tag - or anything
    right after a joiner."""
    o = ord(ch)
    return (prev == "‍" or ch == "‍" or unicodedata.category(ch) in ("Mn", "Me", "Mc")
            or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F)


def _is_ri(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF          # regional indicator: two make a flag


def _cut(s: str, limit: int) -> str:
    """s cut to at most `limit` characters (code points, like len(): a plain emoji is one;
    a flag, a skin-tone / keycap emoji or a joined (ZWJ) one is several), never through
    what shows as one character - half a flag, a family emoji without its last members,
    a letter without its accent: one that doesn't fit whole is dropped. The Games panel's
    cut() (static/round_common.js) keeps the same text."""
    i = max(0, limit)
    if len(s) <= i:
        return s
    while i > 0 and _joins(s[i - 1], s[i]):
        i -= 1
    j = i
    while j > 0 and _is_ri(s[j - 1]):              # flags pair up from the start of a run
        j -= 1
    if (i - j) % 2 and _is_ri(s[i]):
        i -= 1
    return s[:i]


def _clean_user(v: Any) -> str | None:
    """Caption / bet user: control chars removed, leading '@' dropped (the overlay
    adds its own), at most USER_MAX_CHARS. Empty -> None."""
    if v is None or isinstance(v, (dict, list)):
        return None
    s = _CTRL.sub("", str(v)).strip().lstrip("@").strip()[:USER_MAX_CHARS]
    return s or None


def _bet_text(v: Any) -> str:
    """A request's bet string (roulette / craps): control characters and lone surrogates
    removed - it is echoed in replies and kept on the table - trimmed, at most
    BET_TEXT_MAX_CHARS. Missing / an object / a list -> ""."""
    if v is None or isinstance(v, (dict, list)):
        return ""
    return _CTRL.sub("", str(v)).strip()[:BET_TEXT_MAX_CHARS]


def _bet_id(v: Any, limit: int = 64) -> str:
    """A request's bet id (bet_id / target), cleaned like _bet_text. Missing / not a
    scalar -> ""."""
    if v is None or isinstance(v, (dict, list, bool)):
        return ""
    return _CTRL.sub("", str(v)).strip()[:limit]


_HEX6 = re.compile(r"^#[0-9a-f]{6}$")
_HEX3 = re.compile(r"^#[0-9a-f]{3}$")


def _coerce(spec: tuple, value: Any) -> Any:
    """Validate one config value against its schema entry.

    Returns the clean value, or _INVALID if it can't be interpreted. Numbers are
    clamped into range (out-of-range is not "invalid")."""
    kind = spec[0]
    if kind == "bool":
        return _as_bool(value)
    if kind in ("num", "int"):
        f = _as_float(value)
        if f is None:
            return _INVALID
        f = min(float(spec[2]), max(float(spec[1]), f))
        return int(round(f)) if kind == "int" else _num(f)
    if kind == "enum":
        s = str(value).strip().lower() if value is not None else ""
        return s if s in spec[1] else _INVALID
    if kind == "color":
        s = str(value).strip().lower() if value is not None else ""
        if s == "" or _HEX6.match(s):
            return s
        if _HEX3.match(s):
            return "#" + "".join(ch * 2 for ch in s[1:])
        return _INVALID
    if kind == "str":
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return _INVALID
        return _cut(_SURROGATES.sub("", str(value)).strip(), spec[1])
    if kind == "name":
        # ("name", max_len): a short non-empty label (craps currency, a title); control chars
        # and lone surrogates removed, at most max_len characters (code points; _cut() never
        # leaves half an emoji / flag behind)
        if value is None or isinstance(value, (dict, list, bool)):
            return _INVALID
        s = _cut(_CTRL.sub("", str(value)).strip(), spec[1]).strip()
        return s if s else _INVALID
    if kind == "intenum":
        # ("intenum", (2, 3)): an integer from a fixed set ("2" / 2.0 accepted)
        f = _as_float(value)
        if f is None or not f.is_integer() or int(f) not in spec[1]:
            return _INVALID
        return int(f)
    if kind == "strenum":
        # ("strenum", ("345", "1", ...)): a string from a fixed set; integral numbers become strings
        if value is None or isinstance(value, (bool, dict, list)):
            return _INVALID
        if isinstance(value, (int, float)):
            f = _as_float(value)
            if f is None or not f.is_integer():
                return _INVALID
            s = str(int(f))
        else:
            s = str(value).strip().lower()
        return s if s in spec[1] else _INVALID
    return _INVALID


# --------------------------------------------------------------------------
# shared by every game's bet handling and ledger events
# --------------------------------------------------------------------------

# the wheel / table colour schemes the table games (roulette, craps) both offer
_THEMES = ("classic", "neon", "midnight", "royal")


def _parse_coins(v: Any) -> tuple[int | None, str | None]:
    """A whole-coin amount: (int, None) or (None, error)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, "amount required"
    if isinstance(v, bool):
        return None, "invalid amount"
    if isinstance(v, int):
        i = v
    else:
        f = _as_float(v)
        if f is None:
            return None, "invalid amount"
        if not f.is_integer():
            return None, "amounts are whole coins"
        i = int(f)
    if i <= 0:
        return None, "amount must be positive"
    if i > COINS_MAX:
        return None, "amount too large"
    return i, None


def _echo(v: Any) -> Any:
    """A request value safe to put back into a JSON response."""
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    return _CTRL.sub("", str(v))[:40]


def _aggregate_credits(items: list[dict], key: str = "credit") -> list[dict]:
    """[{user, amount}] per user (first-appearance order), amounts > 0 only."""
    by: dict[str, int] = {}
    for it in items:
        amt = it.get(key, 0)
        if amt > 0:
            by[it["user"]] = by.get(it["user"], 0) + amt
    return [{"user": u, "amount": a} for u, a in by.items()]


def _ev(etype: str, user: str, amount: int, reason: str, bet_id: str | None, label: str | None,
        roll_id: str | None) -> dict:
    """A ledger event before Ledger.append() stamps seq/ts/game on it."""
    return {"type": etype, "user": user, "amount": amount, "reason": reason, "bet_id": bet_id,
            "bet": label, "roll_id": roll_id}


def bet_summary(bets: list[dict]) -> dict:
    valid = [b for b in bets if b.get("valid")]
    return {
        "bets": len(bets),
        "winners": sum(1 for b in valid if b.get("win")),
        "invalid": len(bets) - len(valid),
        "total_wagered": _num(sum(b.get("amount", 0) for b in valid)),
        "total_payout": _num(sum(b.get("payout", 0) for b in valid)),
    }


# --------------------------------------------------------------------------
# soundboard cues
# --------------------------------------------------------------------------
# Clips fire through the soundboard's own /api/play/{name}, so they honour the
# clip's saved trim/volume/cooldown. The FastAPI app is called DIRECTLY
# in-process (ASGITransport) rather than over 127.0.0.1 - a loopback socket can
# be hijacked by port forwards (e.g. VS Code Remote-SSH auto-forwarding 4747),
# which silently routes the trigger to a different hexcast instance.
_PORT: int = 4747
_APP = None                               # FastAPI app, set by attach_games()
_BG_TASKS: set[asyncio.Task] = set()      # keep fire-and-forget tasks alive


def _client() -> httpx.AsyncClient | None:
    if _APP is None:
        return None       # never fall back to loopback (see above)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP),
                             base_url="http://hexcast.internal", timeout=5)


async def _fire_clip(name: str, why: str = "") -> None:
    """Trigger one soundboard clip (any kind) via its public /api/play endpoint."""
    name = (name or "").strip()
    if not name:
        return
    client = _client()
    if client is None:
        print(f"[games cue] skip {name!r}: attach_games() never ran", flush=True)
        return
    url = f"/api/play/{urllib.parse.quote(name, safe='')}"
    try:
        async with client as c:
            r = await c.get(url)
            print(f"[games cue] {why} /api/play/{name!r} -> {r.status_code} {r.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[games cue] {why} /api/play/{name!r} failed: {exc}", flush=True)


def _background(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def _soon(coro) -> None:
    """_background() from synchronous code that may run without an event loop
    (e.g. a commit reached from a unit test): no loop -> the coroutine is dropped."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    _background(coro)


# --------------------------------------------------------------------------
# display: "Hex does the math" (money mode B)
# --------------------------------------------------------------------------
# In mode B the bot keeps the bets and pays from its own bank. Hexcast still
# picks every outcome and animates it; the bot only tells the overlay what to
# SHOW: a winners card after a result (/announce) and the "on the table" board
# (/craps/board, /roulette/board) - table games only: a round game (russian,
# trivia) builds its own game-over card, and its /announce answers 400.
# Display calls never touch a table's bets, the ledger, history or stats.

def _display_text(v: Any, limit: int) -> str | None:
    """A display string: control chars removed, trimmed, at most `limit` chars (_cut:
    never half an emoji / flag). Empty or not text-like -> None."""
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = _cut(_CTRL.sub("", str(v)).strip(), limit).strip()
    return s or None


def _display_amount(v: Any, signed: bool) -> tuple[int | float | None, bool]:
    """(amount, ok). Missing / blank -> (None, True); a finite number with
    |amount| <= MAX_AMOUNT (and >= 0 unless `signed`) -> (tidy number, True);
    anything else (text, bool, inf/nan, too big) -> (None, False)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, True
    f = _as_float(v)
    if f is None or abs(f) > MAX_AMOUNT or (f < 0 and not signed):
        return None, False
    return _num(f), True


def _display_line(raw: Any, signed: bool) -> dict | None:
    """One card / board line {user, amount, text}, cleaned. None if unusable: not
    an object, a bad amount, or neither a user nor a text."""
    if not isinstance(raw, dict):
        return None
    user = _clean_user(raw.get("user"))
    text = _display_text(raw.get("text"), DISPLAY_TEXT_MAX_CHARS)
    amount, ok = _display_amount(raw.get("amount"), signed)
    if not ok or (user is None and text is None):
        return None
    return {"user": user, "amount": amount, "text": text}


def _display_entries(params: dict, key: str) -> tuple[list | None, str | None]:
    """(raw lines, error) of a display request: JSON `key` (a list, one object, or
    a JSON string of either) plus the one-line shorthand user=&amount=&text=.
    (None, None) = no lines were sent at all."""
    raw = params.get(key)
    if isinstance(raw, str):
        if not raw.strip():
            raw = []
        else:
            try:
                raw = json.loads(raw)
            except ValueError:
                return None, f"{key} must be a JSON list"
    if isinstance(raw, dict):
        raw = [raw]
    if raw is not None and not isinstance(raw, list):
        return None, f"{key} must be a list"
    entries = list(raw) if raw is not None else None
    if any(params.get(k) not in (None, "") for k in ("user", "amount", "text")):
        entries = (entries or []) + [{"user": params.get("user"), "amount": params.get("amount"),
                                      "text": params.get("text")}]
    return entries, None


def _display_lines(entries: list, limit: int, signed: bool) -> list[dict]:
    """The first `limit` usable lines (unusable ones are skipped)."""
    out: list[dict] = []
    for e in entries:
        line = _display_line(e, signed)
        if line is not None:
            out.append(line)
            if len(out) >= limit:
                break
    return out


# --------------------------------------------------------------------------
# game framework
# --------------------------------------------------------------------------
# A Game owns its config section, its spin lifecycle and its history. Subclasses
# provide the rules: DEFAULTS/SCHEMA/APPEARANCE, pick() (the outcome), extra
# SPIN fields, bet parsing and stats. Everything else - busy rule, timers,
# visibility, cues, wait=true, history, STATE - lives here.
#
# Timeline per spin (server-side, asyncio):
#   start      -> state "spinning", spin_clip fires
#   lands_at   -> commit to history (unless test), land_clip fires, state "result"
#   +result_s  -> state "cooldown" (or "idle"); /show flag cleared
#   +cooldown  -> state "idle"
#
# Hooks a game may override (TableGame below fills most of them in for the
# games with a table - roulette and craps):
#   duration_range()        clamp for a spin's `duration` (+ duration_key: config default)
#   spin_outcome()          game-specific SPIN fields computed at spin time (default: the spin's own bets)
#   state_extra()           merged into STATE (table games: {"table": TABLE})
#   table_view()            the TABLE object (table games), else None
#   extra_visible()         OR'ed into visible() unless hidden (table games: bets on the table)
#   on_commit(spin)         after a non-test spin is committed (land / stop / heal / crash)
#   on_idle()               after a run fully ends on its own (result/cooldown over, or healed):
#                           default = arm the auto countdown
#   on_config()             after a config save: default = arm / cancel the auto countdown
#   before_spin(params)     sync, between the busy check and the start (craps: place `bets`)
#   spin_response(...)      extra fields for the spin HTTP response
#   validate(params)        /validate body;  bets_payload()  /bets body
#   auto_key, has_table_bets(), auto_params()   the countdown (see below)
#
# Every table game also carries the bot's winners card (/announce, STATE.announce;
# a RoundGame's is always null and the route refuses it):
# display only, it keeps the game visible while it is up, expires on its own
# (server clears it + broadcasts) and a new spin takes it down.
#
# The countdown (craps' auto-roll, roulette's auto-spin, /timer) is shared too.
# Two kinds: "auto" - the bet window, armed when the config flag `auto_key` is
# on, bets are down and the game is idle (first bet, back to idle with bets
# still down, flag switched on); "manual" - /timer, one-shot, runs even with no
# bets and the flag off. At zero the game spins exactly like /spin (an "auto"
# one only if the flag is still on and bets are still down). Any spin (test ones
# too), /stop, /timer/cancel and /clear cancel either kind; the table emptying
# and the flag switching off cancel only an "auto" one. A running countdown
# keeps the game visible (unless hidden).


class _Run:
    """Runtime handle for one spin (task, landing event, phase deadlines)."""

    def __init__(self, spin: dict, result_end_at: float, idle_at: float) -> None:
        self.spin = spin
        self.result_end_at = result_end_at
        self.idle_at = idle_at
        self.landed = False
        self.committed = False
        self.stopped = False
        self.event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.extra: dict[str, Any] = {}     # per-game scratch (craps: the pre-roll table)


class Game:
    key: str = "game"                 # URL segment + config section
    title: str = "Game"
    id_prefix: str = "g"              # spin ids look like "<prefix>-1a2b3c4d"
    DEFAULTS: dict[str, Any] = {}
    SCHEMA: dict[str, tuple] = {}
    APPEARANCE: tuple[str, ...] = ()  # the only keys a spin's `overrides` may carry
    has_bets: bool = False
    duration_key: str = "spin_seconds"    # config key holding the default spin length
    launch_clip_key: str = "spin_clip"    # config key of the soundboard clip fired at launch
    auto_key: str | None = None           # config flag of the automatic countdown (craps auto_roll ...)

    def __init__(self) -> None:
        self.state = "idle"             # idle | spinning | result | cooldown
        self.run: _Run | None = None
        self.shown = False              # /show: visible until hidden / next result ends
        self.hidden = False             # /hide or stop: forced hidden until /show / next spin
        self.history: list[dict] = []   # committed SPINs, newest first
        self.announce: dict | None = None           # the bot's winners card (without expires_in_ms)
        self._announce_until: float | None = None   # epoch it expires at
        self._announce_task: asyncio.Task | None = None
        self._announce_token: object | None = None
        self.auto_at: float | None = None           # the countdown: epoch it fires at (None = not counting)
        self.auto_kind: str | None = None           # "auto" (bet window) | "manual" (/timer)
        self.auto_task: asyncio.Task | None = None
        self._auto_token: object | None = None
        self.reset_stats()

    # ---- config ----------------------------------------------------------

    def validate_config(self, raw: Any) -> dict:
        """Merge-free validation: every DEFAULTS key present, unknown keys dropped,
        bad values -> default, numbers clamped, bad colours -> ""."""
        raw = raw if isinstance(raw, dict) else {}
        out: dict[str, Any] = {}
        for k, default in self.DEFAULTS.items():
            v = _coerce(self.SCHEMA[k], raw[k]) if k in raw else _INVALID
            out[k] = json.loads(json.dumps(default)) if v is _INVALID else v
        return out

    def validate_overrides(self, raw: Any) -> dict:
        """Per-spin appearance overrides: non-appearance keys and uninterpretable
        values are dropped; numbers are clamped like the config."""
        out: dict[str, Any] = {}
        if not isinstance(raw, dict):
            return out
        for k in self.APPEARANCE:
            if k in raw:
                v = _coerce(self.SCHEMA[k], raw[k])
                if v is not _INVALID:
                    out[k] = v
        return out

    @property
    def cfg(self) -> dict:
        return CONFIG.get(self.key) or self.validate_config({})

    # ---- rules (override in subclasses) ----------------------------------

    def pick(self, cfg: dict) -> dict:
        """The outcome (a RESULT dict). Must use _RNG (secrets.SystemRandom)."""
        raise NotImplementedError

    def spin_fields(self) -> dict:
        """Extra constant fields for the SPIN object (roulette: {"wheel": "american"})."""
        return {}

    def parse_bet(self, bet: Any) -> dict:
        return {"valid": False, "type": "invalid", "label": str(bet or "?"), "numbers": [],
                "odds": 0, "error": f"{self.title} has no bets"}

    def resolve_bet(self, entry: Any, result: dict, default_user: str | None = None) -> dict:
        raise NotImplementedError

    def bets_reference(self) -> list[dict]:
        return []

    def bets_rules(self) -> list[str]:
        return []

    def bets_payload(self) -> dict:
        """Body of GET /games/api/{game}/bets."""
        out: dict[str, Any] = {"ok": True, "game": self.key, **self.spin_fields()}
        out.update({"bets": self.bets_reference(), "rules": self.bets_rules()})
        return out

    def validate(self, params: dict) -> dict:
        """Body of GET /games/api/{game}/validate (without "ok")."""
        bet = params.get("bet")
        text = _bet_text(bet)
        return {"bet": text, **self.parse_bet(text)}

    def reset_stats(self) -> None:
        self._spins = 0

    def record(self, result: dict) -> None:
        self._spins += 1

    def stats(self) -> dict:
        return {"spins": self._spins}

    # ---- hooks (defaults = roulette's behaviour) --------------------------

    def duration_range(self) -> tuple[float, float]:
        """Clamp for a spin's `duration` in seconds (read at call time)."""
        return SPIN_SECONDS_MIN, SPIN_SECONDS_MAX

    def state_extra(self) -> dict:
        return {}

    def table_view(self) -> dict | None:
        return None

    def extra_visible(self) -> bool:
        return False

    def on_commit(self, spin: dict) -> None:
        pass

    def on_idle(self) -> None:
        self.arm_auto()                   # bets still down after a spin -> next countdown

    def on_config(self) -> None:
        if self.auto_on():
            self.arm_auto()
        else:
            self.cancel_auto("auto")      # a /timer countdown keeps running

    def before_spin(self, params: dict) -> Any:
        return None

    def spin_response(self, run: _Run, pre: Any, waited: bool) -> dict:
        return {}

    def spin_overrides(self, params: dict) -> dict:
        """overrides: top-level appearance shorthand (x=&y=&scale=...), then `overrides`."""
        ov_in = {k: params[k] for k in self.APPEARANCE if k in params}
        ov_raw = params.get("overrides")
        if isinstance(ov_raw, str):
            try:
                ov_raw = json.loads(ov_raw)
            except ValueError:
                ov_raw = None
        if isinstance(ov_raw, dict):
            ov_in.update(ov_raw)
        return self.validate_overrides(ov_in)

    def spin_outcome(self, params: dict, result: dict, user: str | None, test: bool) -> dict:
        """Game-specific SPIN fields, computed when the spin is built. Default: the
        bets riding on this spin (roulette), resolved against the result."""
        # bets: JSON list (or a JSON string of one, e.g. ?bets=[...]), plus the
        # bet=&amount= shorthand. Only the shorthand bet takes the spin's `user`.
        bets_raw = params.get("bets")
        if isinstance(bets_raw, str):
            try:
                parsed = json.loads(bets_raw)
            except ValueError:
                parsed = None
            # ?bets=17 parses as the JSON number 17 - keep it as the bet string
            bets_raw = parsed if isinstance(parsed, (list, dict)) else ([bets_raw] if bets_raw.strip() else [])
        if isinstance(bets_raw, dict):
            bets_raw = [bets_raw]
        bets_in = list(bets_raw) if isinstance(bets_raw, list) else []
        if params.get("bet") not in (None, ""):
            bets_in.append({"user": params.get("user"), "bet": params.get("bet"),
                            "amount": params.get("amount")})
        bets_in = bets_in[:MAX_BETS]
        bets = [self.resolve_bet(b, result) for b in bets_in] if self.has_bets else []
        return {"bets": bets, "summary": bet_summary(bets)}

    # ---- lifecycle -------------------------------------------------------

    def _heal(self) -> None:
        """If a timeline task died (crash, event loop swapped) don't stay busy forever."""
        run = self.run
        if self.state != "idle" and run is not None and time.time() > run.idle_at + 5:
            log.warning("[games] %s timeline stalled in %r - forcing idle", self.key, self.state)
            if run.task is not None and not run.task.done():
                run.task.cancel()
            if not run.committed:
                self._commit(run)
            run.landed = True
            run.event.set()
            self.state, self.run = "idle", None
            self.shown = False
            self.on_idle()

    def busy_ms(self, now: float | None = None) -> int:
        if self.state == "idle" or self.run is None:
            return 0
        now = time.time() if now is None else now
        return max(0, int(round((self.run.idle_at - now) * 1000)))

    def busy_response(self, until: str = "idle") -> JSONResponse | None:
        """409 while spinning / result / cooldown. `until="land"` only while spinning
        (used by /hide), with retry_in_ms = time to landing."""
        self._heal()
        if until == "land":
            if self.state != "spinning" or self.run is None:
                return None
            ms = max(0, int(round((self.run.spin["lands_at"] - time.time()) * 1000)))
        else:
            if self.state == "idle":
                return None
            ms = self.busy_ms()
        return JSONResponse({"ok": False, "error": "busy", "retry_in_ms": ms, "state": self.state},
                            status_code=409)

    def visible(self) -> bool:
        if self.state == "spinning":
            return True
        if self.hidden:
            return False
        return (self.state == "result" or self.shown or not self.cfg.get("hide_when_idle", True)
                or self.auto_at is not None or self.extra_visible() or self.announce_active())

    def last(self) -> dict | None:
        return self.history[0] if self.history else None

    def current_spin(self) -> dict | None:
        """The in-flight / result-phase spin (STATE.spin without elapsed_ms), else None."""
        if self.state in ("spinning", "result") and self.run is not None:
            return self.run.spin
        return None

    # ---- display: the bot's winners card (money mode B) ---------------------

    def _announce_ms(self, now: float | None = None) -> int:
        """ms until the card expires (<= 0: none / expired)."""
        if self.announce is None or self._announce_until is None:
            return 0
        now = time.time() if now is None else now
        return int(round((self._announce_until - now) * 1000))

    def announce_active(self, now: float | None = None) -> bool:
        return self._announce_ms(now) > 0

    def announce_view(self, now: float | None = None) -> dict | None:
        """STATE.announce: the card with expires_in_ms as of `now`, or None. An expired
        card reads as None even before the expiry timer has cleared it."""
        ms = self._announce_ms(now)
        return {**self.announce, "expires_in_ms": ms} if ms > 0 else None

    def set_announce(self, params: dict) -> tuple[int, dict]:
        """/announce: validate and put up the bot's winners card. Returns (status, body).

        Allowed in every state; while the ball / dice are in the air the overlay holds
        it until landing, so its `seconds` count from the landing."""
        self._heal()          # a stalled timeline must not pin the card to a dead spin / phase
        entries, err = _display_entries(params, "lines")
        if err:
            return 400, {"error": err}
        empty_text = _display_text(params.get("empty_text"), DISPLAY_TEXT_MAX_CHARS)
        lines = _display_lines(entries or [], ANNOUNCE_LINES_MAX, signed=True)
        if not lines and empty_text is None:
            if entries is None:
                return 400, {"error": "announce needs lines:[{user, amount, text}] ([] = nobody won) "
                                      "or empty_text"}
            if entries:
                return 400, {"error": "no valid lines: each line needs a user or a text; amount must be a "
                                      "finite number with |amount| <= 1e12"}
        # the spin it belongs to: the one in flight / in its result phase, else the last committed
        attach = self.current_spin() or self.last()
        expected = attach["id"] if attach else None
        sid = params.get("spin_id")
        sid = None if sid is None else (_CTRL.sub("", str(sid)).strip()[:64] or None)
        if sid is not None and sid != expected:
            return 409, {"error": "stale", "spin_id": expected}

        cfg = self.cfg
        title = _display_text(params.get("title"), DISPLAY_TITLE_MAX_CHARS) or "WINNERS"
        cur = params.get("currency")
        if cur is None or isinstance(cur, (dict, list, bool)):
            currency = str(cfg.get("currency") or "")[:DISPLAY_CURRENCY_MAX_CHARS]    # craps: its currency
        else:
            currency = _display_text(cur, DISPLAY_CURRENCY_MAX_CHARS) or ""
        now = time.time()
        run = self.run
        start = now
        if self.state == "spinning" and run is not None:
            start = max(now, run.spin["lands_at"])        # held until landing: count from there
        secs = _as_float(params.get("seconds"))
        if secs is None:
            if self.state in ("spinning", "result") and run is not None:
                secs = run.result_end_at - start          # the rest of the result phase
            else:
                secs = float(cfg.get("result_seconds", 6))
        secs = min(ANNOUNCE_SECONDS_MAX, max(ANNOUNCE_SECONDS_MIN, secs))
        self.announce = {"id": f"a-{secrets.token_hex(4)}", "spin_id": expected, "title": title,
                         "lines": lines, "empty_text": empty_text or "No winners", "currency": currency}
        self._announce_until = start + secs
        self._arm_announce_timer()
        return 200, {"announce": self.announce_view(now)}

    def clear_announce(self) -> bool:
        """Take the card down (a new spin, /announce/clear). True if one was up."""
        was = self.announce_active()
        self.announce, self._announce_until = None, None
        self._cancel_announce_timer()
        return was

    def _arm_announce_timer(self) -> None:
        self._cancel_announce_timer()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return            # no loop (unit use): the card still reads as gone once expired
        token = self._announce_token = object()
        task = self._announce_task = loop.create_task(self._announce_expire(token, self._announce_until))
        _BG_TASKS.add(task)       # stays referenced while it broadcasts (it drops _announce_task first)
        task.add_done_callback(_BG_TASKS.discard)

    def _cancel_announce_timer(self) -> None:
        self._announce_token = None
        task, self._announce_task = self._announce_task, None
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()

    async def _announce_expire(self, token: object, at: float) -> None:
        try:
            await asyncio.sleep(max(0.0, at - time.time()))
        except asyncio.CancelledError:
            return
        if self._announce_token is not token:
            return
        self.announce, self._announce_until = None, None
        self._announce_token, self._announce_task = None, None
        try:
            await HUB.broadcast_state(self)       # visibility may have changed too
        except Exception:
            pass

    # ---- the countdown: auto-spin / auto-roll and /timer (see the top) ---------

    def auto_on(self) -> bool:
        """The automatic countdown's config flag is on."""
        return bool(self.auto_key and self.cfg.get(self.auto_key))

    def has_table_bets(self) -> bool:
        return False

    def auto_params(self) -> dict:
        """The params a spin started by the countdown is built with (craps: the shooter)."""
        return {}

    def auto_in_ms(self) -> int | None:
        if self.auto_at is None:
            return None
        return max(0, int(round((self.auto_at - time.time()) * 1000)))

    def arm_auto(self) -> bool:
        """Start the bet-window countdown if the flag is on, bets are down, the game
        is idle and no countdown is running."""
        if not self.auto_on() or not self.has_table_bets() or self.state != "idle" or self.auto_at is not None:
            return False
        return self._arm_countdown(float(self.cfg.get("bet_window_seconds", 20)), "auto")

    def start_timer(self, seconds: float) -> bool:
        """/timer: (re)start a one-shot countdown now, with or without bets, flag on or
        off. Callers check busy first. Like a spin, it takes a /hide down."""
        self.cancel_auto()
        self.hidden = False
        return self._arm_countdown(seconds, "manual")

    def _arm_countdown(self, seconds: float, kind: str) -> bool:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self.auto_at, self.auto_kind = time.time() + seconds, kind
        token = self._auto_token = object()
        self.auto_task = loop.create_task(self._auto_fire(token, self.auto_at))
        return True

    def cancel_auto(self, kind: str | None = None) -> bool:
        """Cancel the countdown - only if it is a `kind` one, when given. True if one was running."""
        if kind is not None and self.auto_kind != kind:
            return False
        was = self.auto_at is not None
        self.auto_at, self.auto_kind, self._auto_token = None, None, None
        task, self.auto_task = self.auto_task, None
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()
        return was

    async def _auto_fire(self, token: object, at: float) -> None:
        try:
            await asyncio.sleep(max(0.0, at - time.time()))
        except asyncio.CancelledError:
            return
        if self._auto_token is not token:
            return
        kind = self.auto_kind
        self.auto_at, self.auto_kind, self.auto_task, self._auto_token = None, None, None, None
        try:
            self._heal()
            if self.state == "idle" and (kind == "manual" or (self.has_table_bets() and self.auto_on())):
                spin = self.build_spin(self.auto_params())
                self.start(spin)          # exactly like /spin (no await since the idle check)
        except Exception:
            log.exception("[games] %s countdown spin failed", self.key)
        try:
            await HUB.broadcast_state(self)
        except Exception:
            pass

    def state_view(self) -> dict:
        """The STATE object (spec §5): spin carries elapsed_ms, history only landed spins."""
        self._heal()
        now = time.time()
        spin = None
        if self.state in ("spinning", "result") and self.run is not None:
            spin = dict(self.run.spin)
            spin["elapsed_ms"] = max(0, int(round((now - spin["started_at"]) * 1000)))
        view = {
            "state": self.state,
            "visible": self.visible(),
            "spin": spin,
            "last": self.last(),
            "history": [h["result"] for h in self.history[:STATE_HISTORY]],
            "busy_ms": self.busy_ms(now),
            "announce": self.announce_view(now),
        }
        view.update(self.state_extra())
        return view

    def _commit(self, run: _Run) -> None:
        """The single place a spin becomes real (land / stop / heal / crash)."""
        if run.committed:
            return
        run.committed = True
        if run.spin.get("test"):
            return
        self.history.insert(0, run.spin)
        del self.history[HISTORY_MAX:]
        self.record(run.spin["result"])
        try:
            self.on_commit(run.spin)
        except Exception:
            log.exception("[games] %s commit hook failed for %s", self.key, run.spin.get("id"))

    def clear_history(self) -> None:
        self.history = []
        self.reset_stats()

    def build_spin(self, params: dict) -> dict:
        """Validate a spin request and build the SPIN object (no state change)."""
        cfg = self.cfg
        user = _clean_user(params.get("user"))
        lo, hi = self.duration_range()
        dur = _as_float(params.get("duration"))
        if dur is None:
            dur = float(cfg.get(self.duration_key, 9))
        dur = min(hi, max(lo, dur))
        test = _flag(params.get("test"))
        overrides = self.spin_overrides(params)

        result = self.pick(cfg)
        outcome = self.spin_outcome(params, result, user, test)
        started = round(time.time(), 3)
        duration_ms = int(round(dur * 1000))
        spin: dict[str, Any] = {
            "id": f"{self.id_prefix}-{secrets.token_hex(4)}",
            "game": self.key,
            "result": result,
            "user": user,
            "test": test,
            "seed": secrets.randbits(31),
            **self.spin_fields(),
        }
        spin.update({
            "duration_ms": duration_ms,
            "result_ms": int(round(float(cfg.get("result_seconds", 6)) * 1000)),
            "started_at": started,
            "lands_at": round(started + duration_ms / 1000.0, 3),
            **outcome,
            "overrides": overrides,
        })
        return spin

    def start(self, spin: dict) -> _Run:
        """Enter the spinning state and arm the timeline. Synchronous on purpose:
        the busy check and this must not be separated by an await."""
        cfg = self.cfg
        self.cancel_auto()                  # any spin / roll (test ones too) cancels the countdown
        self.clear_announce()               # a new spin / roll takes any winners card down
        result_end = spin["lands_at"] + spin["result_ms"] / 1000.0
        idle_at = result_end + float(cfg.get("cooldown_seconds", 0) or 0)
        run = _Run(spin, result_end, idle_at)
        self.run = run
        self.state = "spinning"
        self.hidden = False
        run.task = asyncio.create_task(self._timeline(run))
        if not spin["test"] and cfg.get(self.launch_clip_key):
            _background(_fire_clip(cfg[self.launch_clip_key], f"{self.key} launch"))
        return run

    async def _timeline(self, run: _Run) -> None:
        try:
            await asyncio.sleep(max(0.0, run.spin["lands_at"] - time.time()))
            await self._on_land(run)
            await asyncio.sleep(max(0.0, run.result_end_at - time.time()))
            await self._on_result_end(run)
            if run.idle_at > run.result_end_at:
                await asyncio.sleep(max(0.0, run.idle_at - time.time()))
                await self._on_idle(run)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("[games] %s timeline crashed", self.key)
            if self.run is run:
                self._commit(run)
                run.landed = True
                run.event.set()
                self.state, self.run = "idle", None
                self.shown = False
                self.on_idle()
                try:
                    await HUB.broadcast_state(self)
                except Exception:
                    pass

    async def _on_land(self, run: _Run) -> None:
        if self.run is not run:
            return
        self._commit(run)
        run.landed = True
        self.state = "result"
        run.event.set()
        land_clip = self.cfg.get("land_clip")
        if not run.spin["test"] and land_clip:
            _background(_fire_clip(land_clip, f"{self.key} landing"))
        await HUB.broadcast_state(self)

    async def _on_result_end(self, run: _Run) -> None:
        if self.run is not run:
            return
        self.shown = False
        if run.idle_at > run.result_end_at:
            self.state = "cooldown"
        else:
            self.state, self.run = "idle", None
            self.on_idle()
        await HUB.broadcast_state(self)

    async def _on_idle(self, run: _Run) -> None:
        if self.run is not run:
            return
        self.state, self.run = "idle", None
        self.on_idle()
        await HUB.broadcast_state(self)

    def stop(self) -> bool:
        """Abort: cancel timers (the countdown too), commit an in-flight spin, go idle
        + hidden. Returns True if something was active."""
        self.cancel_auto()
        run = self.run
        active = self.state != "idle"
        if run is not None:
            if run.task is not None and not run.task.done():
                run.task.cancel()
            if not run.committed:
                self._commit(run)
            if not run.landed:
                run.stopped = True
            run.event.set()
        self.state, self.run = "idle", None
        self.shown = False
        self.hidden = True
        return active

    def show(self) -> None:
        self.shown = True
        self.hidden = False

    def hide(self) -> None:
        self.shown = False
        self.hidden = True


# --------------------------------------------------------------------------
# table games (roulette + craps)
# --------------------------------------------------------------------------
# A table game keeps the bets placed ahead of the spin / roll on a table that is
# persisted (bets survive restarts) and moves coins only through the shared
# LEDGER. The table is frozen while a real spin / roll is in flight (409
# bets_closed); its SETTLEMENTs are computed from it when the spin is built and
# committed at landing (on_commit, via Game._commit: land / stop / heal / crash).
#
# What a subclass provides: DEFAULTS/SCHEMA with currency, min_bet, max_bet,
# show_when_bets, bet_window_seconds (+ its auto_key flag); load_state(path) ->
# (table, last_seq, journal); fresh_table(); table_data() (what save_table
# writes); table_view(); _stake(b) / _label(b) (a bet's coins on the table / its
# ledger label); apply_spin(table, spin) -> credit events; place_bets / remove /
# user_view. Every table dict holds its bets in table["bets"].

class TableGame(Game):
    bet_id_prefix: str = "b"              # bet ids look like "<prefix>-1a2b3c4d"

    def __init__(self, table_path: Path | None = None, ledger: Ledger | None = None,
                 recover: bool = True) -> None:
        super().__init__()
        self.table_path = Path(table_path) if table_path else self.default_table_path()
        self.ledger = ledger if ledger is not None else LEDGER
        self.table, seq, journal = self.load_state(self.table_path)
        # for the start-up repair (Ledger.recover): the module repairs every table at once
        self._recovery = (self.key, self.table_path.name, journal, seq)
        if recover:
            self.ledger.recover([self._recovery])    # replay a half-written change; seq never goes backwards
        self._last_commit: tuple[str | None, list[dict]] = (None, [])
        # the bot's own "on the table" board (money mode B): memory only, never persisted;
        # replaced (never mutated) so views may share it
        self.display_board: dict | None = None

    # ---- per-game pieces ----------------------------------------------------

    def default_table_path(self) -> Path:
        raise NotImplementedError

    def load_state(self, path: Path) -> tuple[dict, int, list[dict]]:
        raise NotImplementedError

    def fresh_table(self) -> dict:
        return {"bets": []}

    def table_data(self) -> dict:
        return {"bets": self.table["bets"]}

    def _stake(self, b: dict) -> int:
        return b["amount"]

    def _label(self, b: dict) -> str:
        return b["label"]

    def apply_spin(self, table: dict, spin: dict) -> list[dict]:
        raise NotImplementedError

    # ---- config ----------------------------------------------------------

    def validate_config(self, raw: Any) -> dict:
        out = super().validate_config(raw)
        if out["max_bet"] and out["max_bet"] < out["min_bet"]:        # must be >= min_bet, else reset
            d = self.DEFAULTS["max_bet"]
            out["max_bet"] = d if d >= out["min_bet"] else 0
        return out

    # ---- table -----------------------------------------------------------

    def in_flight(self) -> bool:
        """A real (non-test) spin / roll is in the air: the table is frozen."""
        run = self.run
        return self.state == "spinning" and run is not None and not run.spin.get("test")

    def bets_closed_response(self) -> JSONResponse | None:
        self._heal()
        if not self.in_flight():
            return None
        ms = max(0, int(round((self.run.spin["lands_at"] - time.time()) * 1000)))
        return JSONResponse({"ok": False, "error": "bets_closed", "retry_in_ms": ms}, status_code=409)

    def exposure(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in self.table["bets"]:
            out[b["user"]] = out.get(b["user"], 0) + self._stake(b)
        return out

    def has_table_bets(self) -> bool:
        return bool(self.table["bets"])

    def set_board(self, params: dict) -> tuple[int, dict]:
        """/{game}/board: the bot's own "on the table" board, shown INSTEAD of the one
        computed from Hexcast's table bets. {"bets": []} = an empty board; clear:true
        = back to the computed board. Allowed any time (the overlay holds table
        updates while the ball / dice fly). Returns (status, body)."""
        if _flag(params.get("clear")):
            return 200, {"cleared": self.clear_board()}
        entries, err = _display_entries(params, "bets")
        if err:
            return 400, {"error": err}
        if entries is None:
            return 400, {"error": "board needs bets:[{user, text, amount}] ([] = an empty board) or clear:true"}
        lines = _display_lines(entries, BOARD_LINES_MAX, signed=False)
        if entries and not lines:
            return 400, {"error": "no valid bets: each line needs a user or a text; amount must be a finite "
                                  "number, 0..1e12"}
        title = _display_text(params.get("title"), DISPLAY_TITLE_MAX_CHARS) or "ON THE TABLE"
        self.display_board = {"title": title, "bets": lines,
                              "total": _num(sum(ln["amount"] or 0 for ln in lines))}
        return 200, {}

    def clear_board(self) -> bool:
        was = self.display_board is not None
        self.display_board = None
        return was

    def save_table(self, journal: list[dict] | None = None) -> bool:
        """Atomically write the table. `journal`: the ledger events of this change
        (stamped, not yet in the ledger file) - see _persist()."""
        data = {"version": 1, **self.table_data(), "last_seq": self.ledger.last_seq,
                "saved_at": round(time.time(), 3), "journal": journal or []}
        return _atomic_write_text(self.table_path, json.dumps(data, indent=1))

    def _persist(self, events: list[dict]) -> list[dict]:
        """Persist a table change + its ledger events, crash-safe: stamp the seqs ->
        save the table WITH the events as its journal -> append them to the ledger.
        A crash between the two writes is repaired at start-up (Ledger.recover
        replays the journals), so the table and the ledger never disagree - a settled
        bet can't be paid twice, a debit can't vanish. The journal also carries any
        earlier events (of either game) whose ledger write failed. Returns the logged events."""
        stamped = self.ledger.stamp([{"game": self.key, **e} for e in events])     # each event names its game
        self.save_table(journal=self.ledger.unwritten + stamped)
        return self.ledger.write(stamped)

    def _bet_by_id(self, bet_id: str) -> dict | None:
        for b in self.table["bets"]:
            if b["id"] == bet_id:
                return b
        return None

    def _new_bet_id(self) -> str:
        ids = {b["id"] for b in self.table["bets"]}
        while True:
            bid = f"{self.bet_id_prefix}-{secrets.token_hex(4)}"
            if bid not in ids:
                return bid

    def clear_table(self) -> dict:
        """Refund every bet; the table starts over. Cancels any countdown."""
        events = [_ev("credit", b["user"], self._stake(b), "refund", b["id"], self._label(b), None)
                  for b in self.table["bets"]]
        self.table = self.fresh_table()
        logged = self._persist(events)
        self.cancel_auto()
        return {"credits": _aggregate_credits(logged, "amount"), "ledger": logged, "refunded": len(logged)}

    # ---- spins -----------------------------------------------------------

    def start(self, spin: dict) -> _Run:
        run = super().start(spin)
        run.extra["table_before"] = self.table_view()   # ball / dice in the air: bets_open false
        return run

    def spin_response(self, run: _Run, pre: Any, waited: bool) -> dict:
        out: dict[str, Any] = {}
        if pre is not None:
            out["placed"] = pre
        if waited:
            out["table"] = self.table_view()
            sid, events = self._last_commit
            if run.committed and not run.spin.get("test"):
                out["ledger"] = events if sid == run.spin["id"] else []
        else:
            out["table"] = run.extra.get("table_before") or self.table_view()
        return out

    def on_commit(self, spin: dict) -> None:
        table = copy.deepcopy(self.table)
        events = self.apply_spin(table, spin)
        self.table = table
        logged = self._persist(events)
        self._last_commit = (spin["id"], logged)
        _soon(_flush_ledger())

    # ---- STATE / visibility ----------------------------------------------

    def state_extra(self) -> dict:
        return {"table": self.table_view()}

    def extra_visible(self) -> bool:
        board = self.display_board           # the bot's board counts too (mode B)
        return bool(self.cfg.get("show_when_bets", True)
                    and (self.table["bets"] or (board is not None and board["bets"])))


# --------------------------------------------------------------------------
# persistence: the table files (craps, roulette) + the shared ledger
# --------------------------------------------------------------------------

_FSYNC = True           # fsync the table file before the atomic replace (tests may turn it off)


def _atomic_write_text(path: Path, text: str) -> bool:
    """Write via a temp file + os.replace. Windows may briefly lock the target
    (indexer / antivirus): the replace is retried for ~1.3 s before giving up."""
    try:
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    except OSError:
        log.exception("[games] can't create a temp file next to %s", path)
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            if _FSYNC:
                os.fsync(f.fileno())
        for attempt in range(25):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(0.004 * (attempt + 1))
        log.error("[games] could not replace %s (file locked?) - change kept in memory only", path)
        return False
    except OSError:
        log.exception("[games] writing %s failed", path)
        return False
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _strict_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _clean_event(raw: Any) -> dict | None:
    """A ledger event from the table journal, validated (None = unusable)."""
    if not isinstance(raw, dict):
        return None
    seq, amount = _strict_int(raw.get("seq")), _strict_int(raw.get("amount"))
    if (seq is None or seq < 1 or amount is None or amount < 1 or raw.get("type") not in ("debit", "credit")
            or not isinstance(raw.get("user"), str) or not raw["user"]):
        return None
    keys = ("seq", "ts", "game", "type", "user", "amount", "reason", "bet_id", "bet", "roll_id")
    return {k: raw.get(k) for k in keys}


def _load_table_file(path: Path, clean, fresh) -> tuple[dict, int, list[dict]]:
    """(table, last_seq, journal) from a table file (tolerant): `clean(raw)` ->
    (table, last_seq), `fresh()` -> an empty table. The journal holds the ledger
    events of the table's last change (written BEFORE they're appended to the
    ledger, so a crash between the two writes can be repaired at start-up)."""
    if not path.exists():
        return fresh(), 0, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("[games] %s is unreadable - starting with an empty table (copy kept as .bad)", path)
        try:
            bad = path.with_name(path.name + ".bad")
            bad.write_bytes(path.read_bytes())
        except OSError:
            pass
        return fresh(), 0, []
    table, last_seq = clean(raw)
    journal = raw.get("journal") if isinstance(raw, dict) else None
    events = [e for e in map(_clean_event, journal if isinstance(journal, list) else []) if e is not None]
    return table, last_seq, sorted(events, key=lambda e: e["seq"])


def _tail_jsonl(path: Path, n: int) -> list[dict]:
    """The last n ledger events of a .jsonl file, reading backwards (torn / bad lines skipped)."""
    if n <= 0 or not path.exists():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            while pos > 0 and buf.count(b"\n") <= n:
                step = min(1 << 16, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
    except OSError:
        log.exception("[games] can't read %s", path)
        return []
    lines = buf.split(b"\n")
    if pos > 0:
        lines = lines[1:]            # the first piece may be a partial line
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        seq = _strict_int(ev.get("seq")) if isinstance(ev, dict) else None
        if seq is not None:
            ev["seq"] = seq                  # 42.0 in a hand-edited file must not make seqs floats
            out.append(ev)
    return out[-n:]


def _event_game(ev: dict) -> str:
    """The game a ledger event belongs to (events from before roulette had a table are craps')."""
    return ev.get("game") or "craps"


class Ledger:
    """Every coin movement of every table game (craps spec §4) - craps and roulette
    share it: one seq space, each event carries its own `game`. Sequenced, appended
    to games_ledger.jsonl (flushed per write, rotated to .1 at 10 MB), the last
    LEDGER_MEMORY events kept in memory. `seq` continues across restarts."""

    def __init__(self, path: Path, memory: int = LEDGER_MEMORY) -> None:
        self.path = Path(path)
        self.rotated = self.path.with_name(self.path.name + ".1")
        self.memory = memory
        self.events: deque[dict] = deque(maxlen=memory)
        self.pending: list[dict] = []        # not yet pushed to panel websockets
        self.unwritten: list[dict] = []      # stamped, but the file write failed (retried next time)
        self._torn = False                   # a failed append may have left a partial line at the end
        self.last_seq = 0
        self._load()

    def _load(self) -> None:
        evs = _tail_jsonl(self.path, self.memory)
        if len(evs) < self.memory:
            evs = _tail_jsonl(self.rotated, self.memory - len(evs)) + evs
        last = 0
        for ev in evs:
            if ev["seq"] > last:
                self.events.append(ev)
                last = ev["seq"]
        self.last_seq = last
        # a crash mid-write can leave a torn last line: terminate it so the next append starts clean
        try:
            if self.path.exists() and self.path.stat().st_size > 0:
                with open(self.path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    torn = f.read(1) != b"\n"
                if torn:
                    with open(self.path, "ab") as f:
                        f.write(b"\n")
        except OSError:
            pass

    def _rotate(self) -> None:
        for attempt in range(25):
            try:
                os.replace(self.path, self.rotated)
                return
            except PermissionError:
                time.sleep(0.004 * (attempt + 1))
        log.error("[games] could not rotate %s (file locked?)", self.path)

    def stamp(self, events: list[dict], game: str = "craps") -> list[dict]:
        """Give each event its seq/ts/game (memory only - write() persists them).
        An event that already names its own `game` keeps it."""
        ts = round(time.time(), 3)
        out = []
        for e in events:
            self.last_seq += 1
            out.append({"seq": self.last_seq, "ts": ts, "game": game, **e})
        return out

    def _cut_back(self, size: int | None) -> bool:
        """After a failed append: truncate the file back to its size before the append,
        so half a batch never stays in it (the whole batch is retried with the next
        write - without this the file would repeat those seqs). True = the file is back
        to how it was before the append."""
        if size is None:
            return False
        try:
            if self.path.exists() and self.path.stat().st_size > size:
                with open(self.path, "r+b") as f:
                    f.truncate(size)
            return True
        except OSError:
            return False

    def write(self, out: list[dict]) -> list[dict]:
        """Append stamped events to the .jsonl (+ any earlier ones whose write
        failed), keep them in memory, queue them for the panel push."""
        if not out:
            return []
        batch = self.unwritten + out
        size0: int | None = None
        try:
            if self.path.exists() and self.path.stat().st_size >= LEDGER_ROTATE_BYTES:
                self._rotate()
                if not self.path.exists():
                    self._torn = False              # rotated: a fresh file
            size0 = self.path.stat().st_size if self.path.exists() else 0
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                # a failed append that couldn't be cut back may have left a torn line: start a fresh one
                lead = "\n" if self._torn else ""
                f.write(lead + "".join(json.dumps(ev, separators=(",", ":")) + "\n" for ev in batch))
                f.flush()
            self.unwritten, self._torn = [], False
        except OSError:
            log.exception("[games] writing the ledger failed (kept in memory, retried with the next write)")
            self.unwritten = batch[-self.memory:]
            # cut back = the file is as it was before this append (torn only if it already was)
            self._torn = not self._cut_back(size0) or self._torn
        self.events.extend(out)
        self.pending.extend(out)
        del self.pending[:-self.memory]
        return out

    def append(self, events: list[dict], game: str = "craps") -> list[dict]:
        """Stamp seq/ts/game on each event, persist them, return the stamped events."""
        return self.write(self.stamp(events, game)) if events else []

    def recover(self, tables: list[tuple[str, str, list[dict], int]]) -> int:
        """Start-up repair from the table files: journal events that never reached the
        ledger (a crash between a table save and the ledger append) are appended now.
        `tables`: one (game, file name, journal, the table's last_seq) per table file -
        pass them ALL at once: a journal may also hold the other game's events (the ones
        still unwritten when it was saved), so every missing seq is replayed exactly once,
        in seq order, whichever journal has it. Then seq continues from whichever file is
        furthest ahead. Returns events replayed."""
        found: dict[int, dict] = {}
        for _game, _name, journal, _seq in tables:
            for e in journal:
                if e["seq"] > self.last_seq:
                    found.setdefault(e["seq"], e)
        missing = [found[s] for s in sorted(found)]
        if missing:
            expect = self.last_seq + 1
            for e in missing:
                if e["seq"] != expect:
                    log.error("[games] ledger gap: seq %s..%s are missing (neither the ledger nor a table "
                              "journal has them)", expect, e["seq"] - 1)
                expect = e["seq"] + 1
            log.warning("[games] replaying %d ledger event(s) (seq %s..%s) from the table journals",
                        len(missing), missing[0]["seq"], missing[-1]["seq"])
            self.last_seq = missing[-1]["seq"]
            self.write(missing)
        for game, name, _journal, table_seq in tables:
            # the table is always saved BEFORE its events are appended, so this only happens when a
            # table save failed (disk full / file locked) and Hexcast stopped before the next one.
            # Only THAT game's events count: the other game moves seq on without touching this table.
            newer = [e["seq"] for e in self.events if e["seq"] > table_seq and _event_game(e) == game]
            if table_seq and newer:
                log.error("[games] %s is older than the ledger (table at seq %s, ledger at %s): the bets moved "
                          "by seq %s..%s may be missing from - or still on - the table. Check them against the "
                          "ledger before the next %s round.", name, table_seq, self.last_seq, newer[0], newer[-1],
                          game)
        self.last_seq = max([self.last_seq] + [t[3] for t in tables])      # seq never goes backwards
        return len(missing)

    def oldest_seq(self) -> int | None:
        """The oldest seq still in memory (any game): older events are only in the files."""
        return self.events[0]["seq"] if self.events else None

    def query(self, since: int, limit: int, game: str | None = None) -> tuple[list[dict], bool]:
        """Events with seq > since (of one `game`, or every game), oldest first, at most
        `limit`. truncated = some such events may not be in this response (the limit cut
        it, or seqs after `since` are older than the in-memory window - read
        games_ledger.jsonl for those)."""
        if not self.events:
            return [], self.last_seq > since
        gap = since + 1 < self.events[0]["seq"]
        out = [e for e in self.events if e["seq"] > since and (game is None or _event_game(e) == game)]
        return out[:limit], gap or len(out) > limit

    def session(self, user: str | None, game: str | None = None) -> dict:
        d = c = n = 0
        for e in self.events:
            if e.get("user") == user and (game is None or _event_game(e) == game):
                n += 1
                if e.get("type") == "debit":
                    d += e.get("amount", 0)
                else:
                    c += e.get("amount", 0)
        return {"debits": d, "credits": c, "net": c - d, "events": n}


LEDGER = Ledger(LEDGER_PATH)


def _rejected_entry(entry: Any, default_user: Any, error: str) -> dict:
    """A `rejected[]` item: the request's user / bet / amount echoed back (cleaned) + why."""
    if isinstance(entry, str):
        entry = {"bet": entry}
    if not isinstance(entry, dict):
        return {"user": _clean_user(default_user), "bet": "", "amount": None, "error": error}
    raw = entry.get("bet")
    text = _bet_text(raw)
    return {"user": _clean_user(entry.get("user")) or _clean_user(default_user), "bet": text,
            "amount": _echo(entry.get("amount")), "error": error}


def _bet_entries(params: dict) -> list:
    """Bets of a /bet or /roll request: JSON `bets` (list, one object, or a JSON string
    of either) plus the single-bet shorthand {user, bet, amount, target}."""
    raw = params.get("bets")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        raw = parsed if isinstance(parsed, (list, dict)) else ([raw] if raw.strip() else [])
    if isinstance(raw, dict):
        raw = [raw]
    entries = list(raw) if isinstance(raw, list) else []
    if params.get("bet") not in (None, "") or params.get("target") not in (None, ""):
        entries.append({"user": params.get("user"), "bet": params.get("bet"), "amount": params.get("amount"),
                        "target": params.get("target")})
    return entries


# --------------------------------------------------------------------------
# round games: Russian Roulette + Trivia
# --------------------------------------------------------------------------
# Unlike roulette / craps (one spin at a time, bets on a table), a ROUND game runs
# one multi-round game at a time:
#
#   /start -> betting -> action -> result -> betting -> ... -> over -> idle
#
# driven by one server-side deadline per phase (the overlay counts down to it).
# Every player bets against the BANK (the bot's bank): a stake is a ledger DEBIT
# when it is placed, winnings are a ledger CREDIT when the player cashes out (or is
# cashed out when the game ends). Nothing moves while a stake rides. Each change is
# persisted crash-safe like the tables: the game file journals the change's ledger
# events before they reach the shared ledger (Ledger.recover replays a torn one).
#
# A game still running when Hexcast stops is settled at the next start-up (and by
# /stop): an outcome already decided counts (the trigger was pulled / the answers
# were locked), then every stake still on the line is refunded, and every ride is
# cashed out at its current value. `test` games (start with test=true) play exactly
# the same but never write to the ledger - their coin movements only show in the
# game's own log.
#
# A round game has no spin to carry appearance overrides, so the Edit Mode editor's
# "Test in OBS" is /preview: the game goes on screen for a few seconds with the
# editor's unsaved look on top of the saved config (STATE.preview), over whatever it
# shows (the idle scene between games). Display only - it never touches the game,
# bets or the ledger; it expires on its own (the server clears it + broadcasts), a new
# one replaces it, and /hide or /stop end it.


ROUND_HISTORY_MAX = 50          # finished games kept per round game
ROUND_PLAYERS_MAX = 500         # players with coins in one game
ROUND_LOG_MAX = 400             # coin movements kept in a game's own log
COMMANDS_TEXT_MAX = 120
ROUND_TITLE_MAX = 32            # the game's title on the overlay (your branding: title)
PREVIEW_SECONDS = 8.0           # /preview (the editor's Test in OBS): default + clamp
PREVIEW_SECONDS_MIN = 2.0
PREVIEW_SECONDS_MAX = 60.0


def _floor(x: Fraction) -> int:
    return int(math.floor(x))


def _mult_view(x: Fraction) -> float:
    """A multiplier for display, rounded DOWN to 2 decimals (never overstated)."""
    return math.floor(x * 100) / 100


def _pct_frac(v: Any) -> Fraction:
    f = _as_float(v)
    return Fraction(str(round(f, 4))) / 100 if f is not None and f > 0 else Fraction(0)


class RoundGame(Game):
    """A game of rounds, bets against the bank (see above). Subclasses provide the
    phases (advance), the money rules (open_settlement, summaries) and the views."""

    PHASES: tuple[str, ...] = ()
    STAT_KEYS: tuple[str, ...] = ()
    CLIP_KEYS: tuple[str, ...] = ()

    def __init__(self, path: Path | None = None, ledger: Ledger | None = None) -> None:
        super().__init__()
        self.path = Path(path) if path else self.default_path()
        self.ledger = ledger if ledger is not None else LEDGER
        self.g: dict | None = None              # the running game (None = idle)
        self._task: asyncio.Task | None = None  # the phase deadline
        self._token: object | None = None
        self._bc: asyncio.Task | None = None    # a coalesced broadcast (answers / bets)
        self.preview: dict | None = None        # Test in OBS: {id, overrides} (without expires_in_ms)
        self._preview_until: float | None = None
        self._preview_task: asyncio.Task | None = None
        self._preview_token: object | None = None
        journal, seq = self._load()
        self._recovery = (self.key, self.path.name, journal, seq)

    # ---- per-game pieces ------------------------------------------------------

    def default_path(self) -> Path:
        raise NotImplementedError

    def clean_game(self, raw: Any) -> dict | None:
        raise NotImplementedError

    def advance(self) -> None:
        """The current phase's deadline passed (or /next): move on."""
        raise NotImplementedError

    def open_settlement(self) -> list[dict]:
        """Abort: the ledger events that give back everything still on the line."""
        raise NotImplementedError

    def summary(self, outcome: str) -> dict:
        raise NotImplementedError

    def game_view(self, now: float) -> dict:
        raise NotImplementedError

    def table_bets(self) -> list[dict]:
        """[{user, amount}] of the coins on the line (hexbar / status)."""
        return []

    # ---- stats / history ------------------------------------------------------

    def reset_stats(self) -> None:
        self._spins = 0
        self._st: dict[str, Any] = {k: 0 for k in self.STAT_KEYS}

    def stats(self) -> dict:
        return dict(self._st)

    def clear_history(self) -> None:
        super().clear_history()
        self.save()

    def _record_game(self, summary: dict) -> None:
        g = self.g
        entry = {"id": g["id"], "game": self.key, "test": bool(g.get("test")),
                 "started_at": g.get("started_at"), "ended_at": round(time.time(), 3), "result": summary}
        self.history.insert(0, entry)
        del self.history[ROUND_HISTORY_MAX:]
        if not g.get("test"):
            try:
                self.record(summary)
            except Exception:
                log.exception("[games] %s stats failed", self.key)

    # ---- persistence ------------------------------------------------------------

    def _load(self) -> tuple[list[dict], int]:
        if not self.path.exists():
            return [], 0
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
        except Exception:
            log.exception("[games] %s is unreadable - starting fresh (copy kept as .bad)", self.path)
            try:
                self.path.with_name(self.path.name + ".bad").write_bytes(self.path.read_bytes())
            except OSError:
                pass
            return [], 0
        hist = raw.get("history")
        self.history = [h for h in hist if isinstance(h, dict) and isinstance(h.get("id"), str)
                        and isinstance(h.get("result"), dict)][:ROUND_HISTORY_MAX] if isinstance(hist, list) else []
        st = raw.get("stats")
        if isinstance(st, dict):
            for k in self.STAT_KEYS:
                v = st.get(k)
                if isinstance(v, dict):
                    self._st[k] = {str(a): b for a, b in v.items() if _strict_int(b) is not None}
                elif _strict_int(v) is not None:
                    self._st[k] = _strict_int(v)
        try:
            self.g = self.clean_game(raw.get("game"))
        except Exception:
            log.exception("[games] %s: the saved game is unreadable - dropped", self.key)
            self.g = None
        if self.g is not None:
            self.state = self.g["phase"]
        journal = raw.get("journal")
        events = [e for e in map(_clean_event, journal if isinstance(journal, list) else []) if e is not None]
        seq = _strict_int(raw.get("last_seq")) or 0
        return sorted(events, key=lambda e: e["seq"]), max(0, seq)

    def save(self, journal: list[dict] | None = None) -> bool:
        data = {"version": 1, "game": self.g, "history": self.history[:ROUND_HISTORY_MAX], "stats": self._st,
                "last_seq": self.ledger.last_seq, "saved_at": round(time.time(), 3), "journal": journal or []}
        return _atomic_write_text(self.path, json.dumps(data, indent=1))

    def resume(self) -> None:
        """Start-up: a game that was running when Hexcast stopped is settled now."""
        if self.g is None:
            return
        log.warning("[games] %s game %s was still running when Hexcast stopped (%s) - settling it",
                    self.key, self.g.get("id"), self.g.get("phase"))
        try:
            self._abort("restart")
        except Exception:
            log.exception("[games] %s: settling the interrupted game failed - dropped", self.key)
            self.g, self.state = None, "idle"
            self.save()

    def _persist(self, events: list[dict]) -> list[dict]:
        """Log coin movements of the running game: stamp -> save the game file WITH the
        events as its journal -> append them to the ledger (test games: log only)."""
        g = self.g
        if not events:
            self.save()
            return []
        if g is not None and g.get("test"):
            ts = round(time.time(), 3)
            out = [{"seq": None, "ts": ts, "game": self.key, "test": True, **e} for e in events]
            self._log(out)
            self.save()
            return out
        stamped = self.ledger.stamp([{"game": self.key, **e} for e in events])
        self._log(stamped)
        self.save(journal=self.ledger.unwritten + stamped)
        out = self.ledger.write(stamped)
        _soon(_flush_ledger())
        return out

    def _log(self, events: list[dict]) -> None:
        g = self.g
        if g is None:
            return
        tot = g.setdefault("totals", {})
        for e in events:
            amt = int(e.get("amount") or 0)
            if e.get("type") == "debit":
                g["debits"] = int(g.get("debits", 0)) + amt
                t = tot.setdefault(e["user"], {"bet": 0, "paid": 0})
                t["bet"] += amt
            else:
                g["credits"] = int(g.get("credits", 0)) + amt
                if e.get("reason") == "volunteer_cut":
                    continue
                t = tot.setdefault(e["user"], {"bet": 0, "paid": 0})
                t["paid"] += amt
        lg = g.setdefault("log", [])
        lg.extend(events)
        del lg[:-ROUND_LOG_MAX]

    def players_summary(self) -> list[dict]:
        tot = (self.g or {}).get("totals") or {}
        rows = [{"user": u, "bet": t["bet"], "paid": t["paid"], "net": t["paid"] - t["bet"]} for u, t in tot.items()]
        rows.sort(key=lambda r: (-r["net"], r["user"].lower()))
        return rows

    # ---- phases + the deadline timer -------------------------------------------

    @property
    def phase(self) -> str:
        return self.g["phase"] if self.g else "idle"

    def _set_phase(self, phase: str, seconds: float | None) -> None:
        g = self.g
        now = time.time()
        g["phase"] = phase
        g["phase_at"] = round(now, 3)
        g["ends_at"] = round(now + seconds, 3) if seconds is not None else None
        self.state = phase
        if seconds is not None:
            self._arm(g["ends_at"])
        else:
            self._disarm()

    def _arm(self, at: float) -> None:
        self._disarm()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return            # no loop (start-up / unit use): _heal() moves the game on
        token = self._token = object()
        self._task = loop.create_task(self._fire(token, at))

    def _disarm(self) -> None:
        self._token = None
        task, self._task = self._task, None
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()

    async def _fire(self, token: object, at: float) -> None:
        try:
            await asyncio.sleep(max(0.0, at - time.time()))
        except asyncio.CancelledError:
            return
        if token is not self._token:
            return
        self._task, self._token = None, None
        self.step()
        try:
            await _flush_ledger()
            await HUB.broadcast_state(self)
        except Exception:
            pass

    def step(self) -> None:
        """advance(), and never leave a game stuck if a rule throws: settle it."""
        try:
            self.advance()
        except Exception:
            log.exception("[games] %s: moving the game on failed - settling it", self.key)
            try:
                self._abort("error")
            except Exception:
                log.exception("[games] %s: settling after an error failed - dropped", self.key)
                self.g, self.state = None, "idle"
                self.save()

    def _heal(self) -> None:
        """A deadline that passed long ago with no timer behind it (a crashed task, no
        loop when it was armed) - move the game on now."""
        for _ in range(8):
            g = self.g
            if g is None or g.get("ends_at") is None:
                return
            if time.time() < g["ends_at"] + 2 or (self._task is not None and not self._task.done()):
                return
            log.warning("[games] %s: %s deadline passed without its timer - moving on", self.key, g["phase"])
            self.step()

    def timing(self, now: float) -> dict:
        g = self.g
        ends = g.get("ends_at")
        at = g.get("phase_at") or now
        return {"ends_in_ms": None if ends is None else max(0, int(round((ends - now) * 1000))),
                "phase_ms": None if ends is None else max(0, int(round((ends - at) * 1000))),
                "elapsed_ms": max(0, int(round((now - at) * 1000)))}

    def _to_idle(self) -> None:
        self._disarm()
        self.g, self.state = None, "idle"
        self.shown = False
        self.save()

    def _abort(self, reason: str) -> None:
        self._disarm()
        g = self.g
        if g is None:
            return
        events = self.open_settlement()
        if events:
            self._persist(events)
        if g["phase"] != "over":
            g["outcome"] = reason
            g["summary"] = self.summary(reason)
            self._record_game(g["summary"])
        self.g, self.state = None, "idle"
        self.save()

    def _clip(self, key: str) -> None:
        g = self.g
        name = self.cfg.get(key)
        if name and not (g and g.get("test")):
            _soon(_fire_clip(name, f"{self.key} {key}"))

    def skip(self) -> tuple[int, dict]:
        """/next: end the current phase now."""
        g = self.g
        if g is None:
            return 409, {"error": "no game running"}
        if g.get("ends_at") is None:
            return 409, {"error": f"nothing to skip in {g['phase']}"}
        was = g["phase"]
        self.step()
        return 200, {"skipped": was}

    # ---- money helpers --------------------------------------------------------

    def _coins(self, v: Any, already: int = 0) -> tuple[int | None, str | None]:
        amt, err = _parse_coins(v)
        if err:
            return None, err
        cfg = self.cfg
        if amt < cfg["min_bet"]:
            return None, f"minimum bet is {cfg['min_bet']} {cfg['currency']}"
        if cfg["max_bet"] and already + amt > cfg["max_bet"]:
            extra = f" ({already} already down this round)" if already else ""
            return None, f"max bet is {cfg['max_bet']} {cfg['currency']}{extra}"
        return amt, None

    def _closed(self, what: str = "bets") -> tuple[int, dict]:
        g = self.g
        if g is None:
            return 409, {"error": "no game running - start one with /start"}
        ms = max(0, int(round(((g.get("ends_at") or time.time()) - time.time()) * 1000)))
        return 409, {"error": f"{what}_closed", "phase": g["phase"], "retry_in_ms": ms}

    # ---- Game overrides (the spin lifecycle doesn't apply) ------------------------

    def busy_response(self, until: str = "idle") -> JSONResponse | None:
        if until == "land":
            return None           # /hide: allowed any time
        return _err(f"{self.title} has no /spin or /timer - start a game with POST /games/api/{self.key}/start "
                    f"(a look preview on the overlay: POST /games/api/{self.key}/preview)")

    def on_config(self) -> None:
        pass

    def on_idle(self) -> None:
        pass

    def visible(self) -> bool:
        if self.preview_active():
            return True               # Test in OBS: on screen for its seconds, even while hidden
        if self.hidden:
            return False
        if self.g is not None:
            return True
        return self.shown or not self.cfg.get("hide_when_idle", True)

    def stop(self) -> bool:
        active = self.g is not None
        if active:
            self._abort("stopped")
        self._disarm()
        self.clear_preview()
        self.shown = False
        self.hidden = True
        return active

    def hide(self) -> None:
        self.clear_preview()
        super().hide()

    # ---- Test in OBS: the editor's appearance preview (see the top) ---------------

    def _preview_ms(self, now: float | None = None) -> int:
        """ms until the preview ends (<= 0: none / over)."""
        if self.preview is None or self._preview_until is None:
            return 0
        now = time.time() if now is None else now
        return int(round((self._preview_until - now) * 1000))

    def preview_active(self, now: float | None = None) -> bool:
        return self._preview_ms(now) > 0

    def preview_view(self, now: float | None = None) -> dict | None:
        """STATE.preview: {id, overrides, expires_in_ms} as of `now`, or None."""
        ms = self._preview_ms(now)
        return {**self.preview, "expires_in_ms": ms} if ms > 0 else None

    def set_preview(self, params: dict) -> tuple[int, dict]:
        """/preview: show the game for `seconds` with appearance `overrides` (x=&y=&scale=
        shorthand too) on top of the saved config. Only this game's APPEARANCE keys are
        kept (validate_overrides); nothing else changes."""
        overrides = self.spin_overrides(params)
        secs = _as_float(params.get("seconds"))
        secs = PREVIEW_SECONDS if secs is None else min(PREVIEW_SECONDS_MAX, max(PREVIEW_SECONDS_MIN, secs))
        now = time.time()
        self.preview = {"id": f"pv-{secrets.token_hex(4)}", "overrides": overrides}
        self._preview_until = now + secs
        self._arm_preview_timer()
        return 200, {"preview": self.preview_view(now)}

    def clear_preview(self) -> bool:
        """End the preview now (/preview/clear, /hide, /stop). True if one was up."""
        was = self.preview_active()
        self.preview, self._preview_until = None, None
        self._cancel_preview_timer()
        return was

    def _arm_preview_timer(self) -> None:
        self._cancel_preview_timer()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return            # no loop (unit use): the preview still reads as over once expired
        token = self._preview_token = object()
        task = self._preview_task = loop.create_task(self._preview_expire(token, self._preview_until))
        _BG_TASKS.add(task)       # stays referenced while it broadcasts (it drops _preview_task first)
        task.add_done_callback(_BG_TASKS.discard)

    def _cancel_preview_timer(self) -> None:
        self._preview_token = None
        task, self._preview_task = self._preview_task, None
        if task is not None and not task.done():
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if task is not current:
                task.cancel()

    async def _preview_expire(self, token: object, at: float) -> None:
        try:
            await asyncio.sleep(max(0.0, at - time.time()))
        except asyncio.CancelledError:
            return
        if self._preview_token is not token:
            return
        self.preview, self._preview_until = None, None
        self._preview_token, self._preview_task = None, None
        try:
            await HUB.broadcast_state(self)       # the look goes back to the saved one (and maybe hides)
        except Exception:
            pass

    def table_view(self) -> dict:
        bets = self.table_bets()
        return {"bets": bets, "total_on_table": sum(b["amount"] for b in bets),
                "currency": self.cfg["currency"], "last_seq": self.ledger.last_seq}

    def state_view(self) -> dict:
        self._heal()
        now = time.time()
        return {"state": self.phase, "visible": self.visible(), "spin": None, "last": self.last(),
                "history": [], "busy_ms": 0, "announce": None, "table": self.table_view(),
                "game": self.game_view(now) if self.g is not None else None,
                "idle": self.idle_view(), "preview": self.preview_view(now)}

    def idle_view(self) -> dict:
        """What the overlay shows between games (next game's settings)."""
        return {}

    def broadcast_soon(self) -> None:
        """Coalesce bursts (a chat full of answers) into one STATE every 250 ms."""
        if self._bc is not None and not self._bc.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def later():
            await asyncio.sleep(0.25)
            try:
                await HUB.broadcast_state(self)
            except Exception:
                pass
        self._bc = loop.create_task(later())


def _round_players(raw: Any, clean_one) -> dict:
    out: dict[str, Any] = {}
    if isinstance(raw, dict):
        for u, v in list(raw.items())[:ROUND_PLAYERS_MAX]:
            user = _clean_user(u)
            val = clean_one(v)
            if user and val is not None:
                out[user] = val
    return out


def _round_common(raw: Any, phases: tuple[str, ...]) -> dict | None:
    """The fields every saved round game has, cleaned (None = unusable)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or raw.get("phase") not in phases:
        return None
    totals = {}
    if isinstance(raw.get("totals"), dict):
        for u, t in raw["totals"].items():
            if isinstance(t, dict) and _clean_user(u):
                totals[_clean_user(u)] = {"bet": max(0, _strict_int(t.get("bet")) or 0),
                                          "paid": max(0, _strict_int(t.get("paid")) or 0)}
    return {"id": raw["id"][:40], "test": bool(raw.get("test")), "phase": raw["phase"],
            "started_at": _as_float(raw.get("started_at")) or time.time(),
            "phase_at": _as_float(raw.get("phase_at")) or time.time(),
            "ends_at": _as_float(raw.get("ends_at")),
            "debits": max(0, _strict_int(raw.get("debits")) or 0),
            "credits": max(0, _strict_int(raw.get("credits")) or 0),
            "totals": totals, "log": [e for e in raw.get("log") or [] if isinstance(e, dict)][-ROUND_LOG_MAX:],
            "ever_bet": bool(raw.get("ever_bet")),
            "outcome": raw.get("outcome") if isinstance(raw.get("outcome"), str) else None,
            "summary": raw.get("summary") if isinstance(raw.get("summary"), dict) else None,
            "last": raw.get("last") if isinstance(raw.get("last"), dict) else None,
            "currency": _SURROGATES.sub("", str(raw.get("currency") or "coins"))[:24]}


def _debits(logged: list[dict]) -> list[dict]:
    return [{"user": e["user"], "amount": e["amount"], "seq": e.get("seq"), "reason": e["reason"]} for e in logged]


# --------------------------------------------------------------------------
# registry + config
# --------------------------------------------------------------------------

GAMES: dict[str, Game] = {}


# A game's state file journals its last change's ledger events BEFORE they reach the shared
# ledger, so a crash between the two writes can be repaired at the next start (Ledger.recover
# replays them). A journal may also hold ANOTHER game's events (ones still unwritten when it was
# saved), so every game's file is read at once - here, by scanning config/, so it does not matter
# which game add-ons happen to be installed. Called once by the plugin, before any game is made.
_STATE_FILE = re.compile(r"^games_([a-z0-9]+)(?:_table)?\.json$")


def recover_ledger() -> int:
    """Start-up repair of the shared ledger from every game's state file. Returns events replayed."""
    tables = []
    for path in sorted(CONFIG_DIR.glob("games_*.json")):
        m = _STATE_FILE.match(path.name)
        if m is None:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict) or "journal" not in raw:      # the question bank, lore ... hold no coins
            continue
        journal = raw["journal"] if isinstance(raw["journal"], list) else []
        events = sorted((e for e in map(_clean_event, journal) if e is not None), key=lambda e: e["seq"])
        seq = _strict_int(raw.get("last_seq"))
        tables.append((m.group(1), path.name, events, seq if seq and seq > 0 else 0))
    return LEDGER.recover(tables)


# What the pages need to know about each installed game. The panel builds one tab per entry
# and the OBS overlay loads one renderer per entry (GET /games/api/registry), so a game that
# is not installed has no tab and no renderer. An entry:
#   {"title": "Craps", "order": 20, "plugin": "games_craps",              # the add-on that provides it
#    "static_dir": Path, "static_url": "/plugins/games_craps/static",     # where its files are
#    "panel_js": "craps_panel.js",                                        # builds the tab
#    "overlay": {"script": "craps.js", "appearance": [...], "defaults": {...}, "stateful": False},
#    "api": {...}}                                                        # its part of GET /games/api
FRONTENDS: dict[str, dict] = {}
_REGISTRY_REV = 0


def register_game(game: Game, frontend: dict | None = None) -> Game:
    """Add a Game instance to the registry. A game registered after the config was
    loaded gets its section from config/games.json (or its defaults) right away."""
    GAMES[game.key] = game
    live = globals().get("CONFIG")
    if isinstance(live, dict) and game.key not in live:
        live[game.key] = game.validate_config(_read_config_file().get(game.key))
    if frontend is not None:
        FRONTENDS[game.key] = frontend
    _registry_changed()
    return game


def unregister_game(key: str) -> None:
    """Take a game out (its plugin is stopping): whatever it was doing is stopped, its tab and
    renderer disappear. Its settings stay in config/games.json for when it comes back."""
    game = GAMES.pop(key, None)
    FRONTENDS.pop(key, None)
    if game is not None:
        try:
            game.stop()
        except Exception:
            log.exception("[games] stopping %s failed", key)
    live = globals().get("CONFIG")
    if isinstance(live, dict):
        live.pop(key, None)
    _registry_changed()


def _registry_changed() -> None:
    """Open panels and overlays re-read the registry."""
    global _REGISTRY_REV
    _REGISTRY_REV += 1
    hub = globals().get("HUB")
    if hub is not None:
        _soon(hub.broadcast({"type": "registry", "revision": _REGISTRY_REV}))


def _asset_url(fe: dict, name: str) -> str:
    """A file of a game's plugin as a URL, stamped with its mtime (see _versioned)."""
    try:
        v = int((Path(fe["static_dir"]) / name).stat().st_mtime)
    except (OSError, KeyError):
        v = 0
    return f"{fe['static_url']}/{name}?v={v}"


def registry_view() -> dict:
    """GET /games/api/registry: the installed games, in tab order, with their scripts."""
    games = []
    for key, fe in FRONTENDS.items():
        if key not in GAMES:
            continue
        ov = fe.get("overlay") or {}
        games.append({
            "key": key, "plugin": fe.get("plugin"), "title": fe.get("title") or GAMES[key].title,
            "order": fe.get("order", 100),
            "panel_js": _asset_url(fe, fe["panel_js"]) if fe.get("panel_js") else None,
            "overlay": {"script": _asset_url(fe, ov["script"]) if ov.get("script") else None,
                        "appearance": list(ov.get("appearance") or GAMES[key].APPEARANCE),
                        "defaults": ov.get("defaults") or {}, "stateful": bool(ov.get("stateful"))},
        })
    games.sort(key=lambda g: (g["order"], g["key"]))
    return {"ok": True, "revision": _REGISTRY_REV, "games": games}


def get_game(key: str) -> Game | None:
    return GAMES.get(str(key or "").lower())


def validate_config(raw: Any) -> dict:
    """Full {game: {...}} config: every registered game's section, validated."""
    raw = raw if isinstance(raw, dict) else {}
    return {key: g.validate_config(raw.get(key)) for key, g in GAMES.items()}


def _read_config_file() -> dict:
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}
    return {}


def load_config() -> dict:
    return validate_config(_read_config_file())


def save_config(cfg: dict) -> dict:
    clean = validate_config(cfg)
    # the settings of games that are not installed right now stay in the file untouched
    kept = {k: v for k, v in _read_config_file().items() if k not in clean and isinstance(v, dict)}
    CONFIG_PATH.write_text(json.dumps({**kept, **clean}, indent=2), encoding="utf-8")
    return clean


CONFIG: dict[str, dict] = load_config()


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

    async def broadcast(self, payload: dict) -> None:
        await self._send(self.overlay, payload)
        await self._send(self.panel, payload)

    async def broadcast_panel(self, payload: dict) -> None:
        """Panel sockets only ("ledger" pushes - overlays never get money data)."""
        await self._send(self.panel, payload)

    async def broadcast_config(self) -> None:
        await self.broadcast(config_message())

    async def broadcast_state(self, game: Game) -> None:
        await self.broadcast(state_message(game))


def config_message() -> dict:
    return {"type": "config", "config": CONFIG}


def state_message(game: Game) -> dict:
    """Built at send time so spin.elapsed_ms is fresh."""
    return {"type": "state", "game": game.key, "state": game.state_view()}


HUB = Hub()


async def _flush_ledger() -> None:
    """Push ledger events not yet sent to the panel sockets: one message per game
    (so a game's panel tab only ever gets its own events), each in seq order."""
    if not LEDGER.pending:
        return
    events, LEDGER.pending = LEDGER.pending, []
    by_game: dict[str, list[dict]] = {}
    for e in events:
        by_game.setdefault(_event_game(e), []).append(e)
    for game, evs in by_game.items():
        await HUB.broadcast_panel({"type": "ledger", "game": game, "events": evs})


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])
_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


def _err(msg: str, status: int = 400, **extra) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg, **extra}, status_code=status)


def _unknown_game() -> JSONResponse:
    return _err("unknown game", 404)


async def _params(request: Request) -> tuple[dict, JSONResponse | None]:
    """Query params, overlaid by the JSON body on POST (body wins)."""
    params: dict[str, Any] = dict(request.query_params)
    if request.method == "POST":
        raw = await request.body()
        if raw.strip():
            try:
                body = json.loads(raw)
            except ValueError:
                return params, _err("body must be JSON")
            if not isinstance(body, dict):
                return params, _err("body must be a JSON object")
            params.update(body)
    return params, None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def panel():
    return HTMLResponse(_versioned(_read_static("games_panel.html")), headers=_NOCACHE)


@router.get("/overlay", response_class=HTMLResponse)
async def overlay():
    return HTMLResponse(_versioned(_read_static("games_overlay.html")), headers=_NOCACHE)


# curl examples for GET /games/api; the ones for a game are only listed while it is installed
_GENERIC_EXAMPLES: list[tuple[str | None, str]] = [
    ('roulette', 'curl http://host:4747/games/api/roulette/spin'),
    ('roulette', "curl 'http://host:4747/games/api/roulette/spin?user=bob&bet=red&amount=100'"),
    ('roulette', "curl 'http://host:4747/games/api/roulette/spin?user=bob&bet=split:17/20&amount=10&wait=true'"),
    ('roulette', 'curl -X POST http://host:4747/games/api/roulette/spin -H \'Content-Type: application/json\' -d \'{"user":"bob","bets":[{"user":"bob","bet":"17","amount":10},{"user":"amy","bet":"dozen2","amount":50}]}\''),
    ('roulette', "curl 'http://host:4747/games/api/roulette/validate?bet=corner:17'"),
    (None, 'curl http://host:4747/games/api/stop'),
]


@router.get("/api")
async def api_root():
    return {
        "ok": True,
        "games": list(GAMES),
        "status":         "GET /games/api/status",
        "config_get":     "GET /games/api/config",
        "config_set":     'POST /games/api/config   body {"roulette": {...partial}, "craps": {...partial}}',
        "spin":           "GET|POST /games/api/{game}/spin   (alias /play)",
        "spin_params":    "user, duration (roulette 4-30 s, craps 2.5-10 s), wait, test, bet + amount | "
                          "bets:[{user,bet,amount}], overrides:{appearance} | x=&y=&scale= shorthand  "
                          "(POST JSON body wins over query)",
        "last":           "GET /games/api/{game}/last",
        "history":        "GET /games/api/{game}/history?limit=20",
        "history_clear":  "GET|POST /games/api/{game}/history/clear",
        "bets":           "GET /games/api/{game}/bets   (bet types, syntax, odds)",
        "validate":       "GET /games/api/{game}/validate?bet=split:17/20",
        "show":           "GET|POST /games/api/{game}/show",
        "hide":           "GET|POST /games/api/{game}/hide   (409 while spinning)",
        "stop":           "GET|POST /games/api/stop   (or /games/api/{game}/stop)",
        "announce":       "GET|POST /games/api/{game}/announce   the bot's own winners card (it did the math): "
                          "{title, lines:[{user, amount, text}] (<=50), empty_text, seconds (1-120), spin_id, "
                          "currency} | user=&amount=&text= shorthand -> {announce, state}; a new spin clears it; "
                          '409 {"error":"stale","spin_id"} when spin_id is not the current / last spin; '
                          "roulette and craps only (russian / trivia: 400 - their game-over card is built in)",
        "announce_clear": "GET|POST /games/api/{game}/announce/clear",
        "timer":          "GET|POST /games/api/{game}/timer   seconds (5-300, default bet_window_seconds): start (or "
                          "restart) the countdown now, with or without bets, auto_spin / auto_roll on or off; at zero "
                          "the game spins / rolls by itself (even with no bets) -> {auto_in_ms, table, state}; "
                          "409 busy unless idle",
        "timer_cancel":   "GET|POST /games/api/{game}/timer/cancel   cancel any countdown -> {cancelled, table, state}",
        "ledger":         "GET /games/api/ledger?since=0&limit=500&game=   every game's coin movements (each event "
                          "carries its game; game= for one) -> {events, last_seq, truncated, oldest_seq} (limit <= 5000)",
        "ledger_ws":      'panel sockets get {"type":"ledger","game":"craps"|"roulette","events":[...]} '
                          "(one message per game)",
        "busy":           '409 {"ok":false,"error":"busy","retry_in_ms":n,"state":"spinning|result|cooldown"}',
        "websockets":     "WS /games/ws/overlay, WS /games/ws/panel",
        "appearance_keys": {key: list(g.APPEARANCE) for key, g in GAMES.items()},
        "examples": [text for game, text in _GENERIC_EXAMPLES if game is None or game in GAMES],
        **{key: fe["api"] for key, fe in sorted(FRONTENDS.items(), key=lambda kv: kv[1].get("order", 100))
           if fe.get("api") and key in GAMES},      # each installed game documents its own routes
    }


@router.get("/api/registry")
async def api_registry():
    return registry_view()


@router.get("/api/status")
async def api_status():
    return {"ok": True, "connected": True, "overlays": len(HUB.overlay),
            "games": {key: g.state_view() for key, g in GAMES.items()}}


@router.get("/api/config")
async def api_get_config():
    return {"ok": True, "config": CONFIG}


@router.post("/api/config")
async def api_set_config(request: Request):
    global CONFIG
    try:
        incoming = json.loads(await request.body() or b"{}")
    except ValueError:
        return _err("body must be JSON")
    if not isinstance(incoming, dict):
        return _err("body must be a JSON object")
    # only known games' sections merge; unknown keys inside them are dropped by validation
    parts = {key: incoming[key] for key in GAMES if isinstance(incoming.get(key), dict)}
    CONFIG = save_config(_deep_merge(CONFIG, parts))
    for g in GAMES.values():
        g.on_config()                   # auto_roll / auto_spin switched on/off
    await HUB.broadcast_config()
    # hide_when_idle may have changed what "visible" means right now
    for g in GAMES.values():
        await HUB.broadcast_state(g)
    return {"ok": True, "config": CONFIG}


async def _stop_all() -> dict:
    stopped = [key for key, g in GAMES.items() if g.stop()]
    await HUB.broadcast({"type": "stop"})
    for g in GAMES.values():
        await HUB.broadcast_state(g)
    return {"ok": True, "stopped": stopped,
            "games": {key: g.state_view() for key, g in GAMES.items()}}


@router.api_route("/api/stop", methods=["GET", "POST"])
async def api_stop():
    return await _stop_all()


async def _spin_request(g: Game, request: Request):
    """Start a spin / roll (shared by /spin, /play and craps' /roll)."""
    params, err = await _params(request)
    if err is not None:
        return err
    busy = g.busy_response()
    if busy is not None:
        return busy
    pre = g.before_spin(params)         # craps: places `bets` first (sync, no await)
    if isinstance(pre, JSONResponse):
        return pre
    spin = g.build_spin(params)
    run = g.start(spin)                 # no await between the busy check and here
    await HUB.broadcast_state(g)
    resp: dict[str, Any] = {"ok": True, **spin}
    waited = _flag(params.get("wait"))
    if waited:
        await run.event.wait()
        resp["landed"] = run.landed
        if run.stopped:
            resp["stopped"] = True
    resp.update(g.spin_response(run, pre, waited))
    return resp


# ---- table routes (shared by the craps and roulette add-ons' own routes) -----

async def _table_changed(g: Game) -> None:
    await _flush_ledger()
    await HUB.broadcast_state(g)


async def _bet_request(g: TableGame, request: Request):
    """/bet: put bets on the table (every accepted bet = one ledger debit)."""
    params, err = await _params(request)
    if err is not None:
        return err
    closed = g.bets_closed_response()
    if closed is not None:
        return closed
    entries = _bet_entries(params)
    if len(entries) > MAX_BETS:
        return _err(f"too many bets in one request (max {MAX_BETS})")
    res = g.place_bets(entries, default_user=params.get("user"))
    if res["accepted"]:
        g.arm_auto()                    # first bet while idle starts the auto countdown
        await _table_changed(g)
    ok = bool(res["accepted"]) or not entries
    body: dict[str, Any] = {"ok": ok, **res, "table": g.table_view()}
    if not ok:
        body["error"] = res["rejected"][0]["error"] if len(res["rejected"]) == 1 else "every bet was rejected"
    return JSONResponse(body, status_code=200 if ok else 400)


async def _remove_request(g: TableGame, request: Request):
    params, err = await _params(request)
    if err is not None:
        return err
    closed = g.bets_closed_response()
    if closed is not None:
        return closed
    status, body = g.remove(params)
    if status != 200:
        return _err(body["error"], status)
    await _table_changed(g)
    return {"ok": True, **body, "table": g.table_view()}


async def _clear_request(g: TableGame):
    closed = g.bets_closed_response()
    if closed is not None:
        return closed
    res = g.clear_table()
    await _table_changed(g)
    return {"ok": True, **res, "table": g.table_view()}


async def _board_request(g: TableGame, request: Request):
    params, err = await _params(request)
    if err is not None:
        return err
    status, body = g.set_board(params)
    if status != 200:
        return _err(body["error"], status)
    await HUB.broadcast_state(g)
    return {"ok": True, **body, "table": g.table_view()}


async def _board_clear_request(g: TableGame):
    cleared = g.clear_board()
    await HUB.broadcast_state(g)
    return {"ok": True, "cleared": cleared, "table": g.table_view()}


def _ledger_reply(since: str | None, limit: str | None, game: str | None = None) -> dict:
    """/ledger body: events with seq > since (of one game, or all), oldest first. last_seq
    is always the ledger's own (every game): seq gaps in one game's events are normal."""
    s = _as_float(since)
    s = int(s) if s is not None and s > 0 else 0
    n = _as_float(limit)
    n = 500 if n is None else int(min(LEDGER_LIMIT_MAX, max(1, n)))
    events, truncated = LEDGER.query(s, n, game)
    return {"ok": True, "events": events, "last_seq": LEDGER.last_seq, "truncated": truncated,
            "oldest_seq": LEDGER.oldest_seq()}


# ---- round games: russian roulette + trivia (before the generic /api/{game}/...
#      routes; /spin, /play and /timer answer 400 for them - a game starts with /start) --

async def _round_request(g: RoundGame, request: Request, fn, *, quick: bool = False):
    """Run one round-game action fn(params) -> (status, body). `quick` (answers): the
    STATE broadcast is coalesced and the reply stays small."""
    params, err = await _params(request)
    if err is not None:
        return err
    status, body = fn(params)
    if status != 200:
        return _err(body.pop("error"), status, **body)
    if quick:
        g.broadcast_soon()
        return {"ok": True, **body}
    await _flush_ledger()
    await HUB.broadcast_state(g)
    return {"ok": True, **body, "state": g.state_view()}


# ---- the shared ledger (every game) ----------------------------------------------

@router.get("/api/ledger")
async def api_ledger(since: str | None = None, limit: str | None = None, game: str | None = None):
    g = None
    if game not in (None, ""):
        g = get_game(game)
        if g is None:
            return _unknown_game()
    return _ledger_reply(since, limit, g.key if g else None)


# ---- generic per-game routes ---------------------------------------------------

@router.api_route("/api/{game}/spin", methods=["GET", "POST"])
@router.api_route("/api/{game}/play", methods=["GET", "POST"])
async def api_spin(game: str, request: Request):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    return await _spin_request(g, request)


@router.get("/api/{game}/last")
async def api_last(game: str):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    last = g.last()
    return {"ok": True, "result": last["result"] if last else None, "spin": last}


@router.get("/api/{game}/history")
async def api_history(game: str, limit: str | None = None):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    n = _as_float(limit)
    n = STATE_HISTORY if n is None else int(min(HISTORY_MAX, max(1, n)))
    return {"ok": True, "history": g.history[:n], "stats": g.stats()}


@router.api_route("/api/{game}/history/clear", methods=["GET", "POST"])
async def api_history_clear(game: str):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    g.clear_history()
    await HUB.broadcast_state(g)
    return {"ok": True, "history": [], "stats": g.stats()}


@router.get("/api/{game}/bets")
async def api_bets(game: str):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    return g.bets_payload()


@router.get("/api/{game}/validate")
async def api_validate(game: str, request: Request):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    return {"ok": True, **g.validate(dict(request.query_params))}


@router.api_route("/api/{game}/show", methods=["GET", "POST"])
async def api_show(game: str):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    g.show()
    await HUB.broadcast_state(g)
    return {"ok": True, "state": g.state_view()}


@router.api_route("/api/{game}/hide", methods=["GET", "POST"])
async def api_hide(game: str):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    busy = g.busy_response(until="land")
    if busy is not None:
        return busy
    g.hide()
    await HUB.broadcast_state(g)
    return {"ok": True, "state": g.state_view()}


@router.api_route("/api/{game}/stop", methods=["GET", "POST"])
async def api_game_stop(game: str):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    stopped = g.stop()
    await HUB.broadcast({"type": "stop", "game": g.key})   # overlays hide on stop
    for other in GAMES.values():
        await HUB.broadcast_state(other)
    return {"ok": True, "stopped": [g.key] if stopped else [], "state": g.state_view()}


# /timer/cancel before /timer
@router.api_route("/api/{game}/timer/cancel", methods=["GET", "POST"])
async def api_timer_cancel(game: str):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    cancelled = g.cancel_auto()
    await HUB.broadcast_state(g)
    return {"ok": True, "cancelled": cancelled, "table": g.table_view(), "state": g.state_view()}


@router.api_route("/api/{game}/timer", methods=["GET", "POST"])
async def api_timer(game: str, request: Request):
    """Start (or restart) the countdown now, with or without bets, auto flag on or off.
    At zero the game spins / rolls exactly like an automatic one."""
    g = get_game(game)
    if g is None:
        return _unknown_game()
    params, err = await _params(request)
    if err is not None:
        return err
    busy = g.busy_response()
    if busy is not None:
        return busy
    secs = _as_float(params.get("seconds"))
    if secs is None:
        secs = float(g.cfg.get("bet_window_seconds", 20))
    g.start_timer(min(TIMER_SECONDS_MAX, max(TIMER_SECONDS_MIN, secs)))
    await HUB.broadcast_state(g)
    return {"ok": True, "auto_in_ms": g.auto_in_ms(), "table": g.table_view(), "state": g.state_view()}


# /preview/clear before /preview. Round games only (display only: never touches the game,
# bets or the ledger); a table game's Test in OBS is a test spin / roll with `overrides`.
def _preview_game(game: str) -> RoundGame | JSONResponse:
    g = get_game(game)
    if g is None:
        return _unknown_game()
    if not isinstance(g, RoundGame):
        return _err(f"{g.title} has no /preview - preview a look with a test spin: POST /games/api/{g.key}/spin "
                    '{"test": true, "overrides": {...}}')
    return g


@router.api_route("/api/{game}/preview/clear", methods=["GET", "POST"])
async def api_preview_clear(game: str):
    g = _preview_game(game)
    if isinstance(g, JSONResponse):
        return g
    cleared = g.clear_preview()
    await HUB.broadcast_state(g)
    return {"ok": True, "cleared": cleared, "preview": None, "state": g.state_view()}


@router.api_route("/api/{game}/preview", methods=["GET", "POST"])
async def api_preview(game: str, request: Request):
    g = _preview_game(game)
    if isinstance(g, JSONResponse):
        return g
    params, err = await _params(request)
    if err is not None:
        return err
    status, body = g.set_preview(params)
    if status != 200:
        return _err(body.pop("error"), status, **body)
    await HUB.broadcast_state(g)
    return {"ok": True, **body, "state": g.state_view()}


# /announce/clear before /announce (display only: never touches bets, ledger, history).
# Table games only: a round game's overlay has no announce card (STATE.announce is always
# null) - its game-over card is built in.
def _announce_game(game: str) -> Game | JSONResponse:
    g = get_game(game)
    if g is None:
        return _unknown_game()
    if isinstance(g, RoundGame):
        return _err(f"{g.title} has no /announce - its game-over card is built in "
                    f"(the winners are in STATE.game.summary: GET /games/api/{g.key}/table)")
    return g


@router.api_route("/api/{game}/announce/clear", methods=["GET", "POST"])
async def api_announce_clear(game: str):
    g = _announce_game(game)
    if isinstance(g, JSONResponse):
        return g
    cleared = g.clear_announce()
    await HUB.broadcast_state(g)
    return {"ok": True, "cleared": cleared, "announce": None, "state": g.state_view()}


@router.api_route("/api/{game}/announce", methods=["GET", "POST"])
async def api_announce(game: str, request: Request):
    g = _announce_game(game)
    if isinstance(g, JSONResponse):
        return g
    params, err = await _params(request)
    if err is not None:
        return err
    status, body = g.set_announce(params)
    if status != 200:
        return _err(body.pop("error"), status, **body)
    await HUB.broadcast_state(g)
    return {"ok": True, "announce": body["announce"], "state": g.state_view()}


def _client_message(kind: str, text: str) -> None:
    """Overlay/panel -> server. Only `error` does anything (it's logged)."""
    try:
        msg = json.loads(text)
    except ValueError:
        return
    if not isinstance(msg, dict):
        return
    if msg.get("type") == "error":
        text = _CTRL.sub(" ", str(msg.get("message", "")))[:1000]   # one log line per report
        log.warning("[games %s] client error: %s", kind, text)


async def _ws_session(ws: WebSocket, group: set[WebSocket], kind: str) -> None:
    await ws.accept()
    group.add(ws)
    try:
        await ws.send_text(json.dumps(config_message()))
        for g in GAMES.values():
            await ws.send_text(json.dumps(state_message(g)))
        while True:
            # receive() rather than receive_text(): a stray binary frame must not
            # end the session (the socket would stay open but stop getting updates)
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("text") is not None:
                _client_message(kind, msg["text"])
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        group.discard(ws)


@router.websocket("/ws/overlay")
async def ws_overlay(ws: WebSocket):
    await _ws_session(ws, HUB.overlay, "overlay")


@router.websocket("/ws/panel")
async def ws_panel(ws: WebSocket):
    await _ws_session(ws, HUB.panel, "panel")


# --------------------------------------------------------------------------
# attach
# --------------------------------------------------------------------------

def attach_games(app, port: int = 4747) -> None:
    """Mount the Games routes onto an existing FastAPI app."""
    global _PORT, _APP
    _PORT = port
    _APP = app
    app.include_router(router)
    print(f"  Games panel:         http://localhost:{port}/games", flush=True)
    print(f"  Games source:        http://localhost:{port}/games/overlay", flush=True)

