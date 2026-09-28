"""
HexCast - Games module
======================

Drop this next to hexcast.py, then add two lines after the other modules:

    from games import attach_games
    attach_games(app, PORT)

Adds:
    http://localhost:4747/games                      -> control panel (spin, history, settings, placement)
    http://localhost:4747/games/overlay              -> OBS browser source (?game=roulette to filter)
    http://localhost:4747/games/api                  -> bot API index
    http://localhost:4747/games/api/roulette/spin    -> spin the wheel (GET or POST, alias /play)
    http://localhost:4747/games/api/craps/bet        -> put chat bets on the craps table (hexcoins)
    http://localhost:4747/games/api/craps/roll       -> throw the dice (GET or POST, alias /spin, /play)
    http://localhost:4747/games/api/craps/ledger     -> every coin movement, by seq (for the bank bot)
    http://localhost:4747/games/api/roulette/announce -> show the bot's own winners card (it did the math)
    http://localhost:4747/games/api/craps/board      -> show the bot's own "on the table" board
    ws://localhost:4747/games/ws/overlay             -> overlay feed
    ws://localhost:4747/games/ws/panel               -> panel feed (+ craps "ledger" pushes)

A small framework for programmatic, bot-driven stream games. Every game is a
subclass of `Game` registered in `GAMES`; the generic /games/api/{game}/...
routes, the spin lifecycle (spinning -> result -> cooldown -> idle), history,
visibility and the websocket feed are shared. Roulette is the first game
(an American double-zero wheel, with the full standard bet table resolved
server-side so a chat bot can run a points casino). Craps is the second: a
persistent bank-craps table (bets stay down across rolls), every bet settled
by standard casino rules, and a sequenced, persisted ledger that tells the
external bank (the bot holding everyone's hexcoins) exactly what to debit and
credit - Hexcast itself never holds a balance.

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
import urllib.parse
from collections import deque
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("hexcast")

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = Path(os.environ.get("HEXCAST_CONFIG_DIR", BASE_DIR / "config"))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "games.json"
CRAPS_TABLE_PATH = CONFIG_DIR / "games_craps_table.json"   # the craps table (bets survive restarts)
LEDGER_PATH = CONFIG_DIR / "games_ledger.jsonl"             # every coin movement, one JSON per line


def _read_static(name: str) -> str:
    path = STATIC_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Static file '{name}' not found at {path}. The Games module needs "
            f"games_panel.html, games_overlay.html and games/roulette.js in ./static/ "
            f"next to hexcast.py."
        )
    return path.read_text(encoding="utf-8")


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

ROLL_SECONDS_MIN = 2.5          # craps: per-roll `duration` clamp (config roll_seconds too)
ROLL_SECONDS_MAX = 10.0
COINS_MAX = 10 ** 12            # craps: largest single amount (whole coins)
LEDGER_MEMORY = 10_000          # ledger events kept in memory for /ledger
LEDGER_ROTATE_BYTES = 10 * 1024 * 1024   # games_ledger.jsonl -> .1 at this size
LEDGER_LIMIT_MAX = 5000         # /ledger?limit= cap

ANNOUNCE_LINES_MAX = 50         # display: lines kept on a winners card (/announce)
BOARD_LINES_MAX = 100           # display: lines kept on the craps "on the table" board (/craps/board)
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


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean_user(v: Any) -> str | None:
    """Caption / bet user: control chars removed, leading '@' dropped (the overlay
    adds its own), at most USER_MAX_CHARS. Empty -> None."""
    if v is None or isinstance(v, (dict, list)):
        return None
    s = _CTRL.sub("", str(v)).strip().lstrip("@").strip()[:USER_MAX_CHARS]
    return s or None


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
        return str(value).strip()[: spec[1]]
    if kind == "name":
        # ("name", max_len): a short non-empty label (craps currency); control chars removed
        if value is None or isinstance(value, (dict, list, bool)):
            return _INVALID
        s = _CTRL.sub("", str(value)).strip()[: spec[1]].strip()
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
# roulette wheel data (identical to static/games/roulette.js)
# --------------------------------------------------------------------------
# American double-zero wheel (38 pockets), clockwise from the zero pocket;
# index 0 sits at 12 o'clock at wheel angle 0.

WHEEL = "american"              # informational constant carried by RESULT / SPIN
AMERICAN = ["0", "28", "9", "26", "30", "11", "7", "20", "32", "17", "5", "22", "34", "15", "3", "24", "36", "13",
            "1", "00", "27", "10", "25", "29", "12", "8", "19", "31", "18", "6", "21", "33", "16", "4", "23", "35", "14", "2"]
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
_POCKET_INDEX = {lab: i for i, lab in enumerate(AMERICAN)}


def pocket_value(label: str) -> int:
    """0..36 for a pocket label, -1 for "00"."""
    return -1 if label == "00" else int(label)


def pocket_color(label: str) -> str:
    v = pocket_value(label)
    if v <= 0:
        return "green"
    return "red" if v in RED else "black"


def result_for(label: str) -> dict:
    """The RESULT object for a pocket label (spec §2)."""
    label = str(label)
    idx = _POCKET_INDEX.get(label)
    if idx is None:
        raise ValueError(f"pocket {label!r} is not on the wheel")
    v = pocket_value(label)
    if v <= 0:
        return {"number": label, "value": v, "color": "green", "index": idx, "wheel": WHEEL,
                "parity": None, "range": None, "dozen": None, "column": None}
    return {"number": label, "value": v, "color": "red" if v in RED else "black", "index": idx,
            "wheel": WHEEL, "parity": "odd" if v % 2 else "even",
            "range": "low" if v <= 18 else "high",
            "dozen": (v - 1) // 12 + 1, "column": (v - 1) % 3 + 1}


# --------------------------------------------------------------------------
# roulette bets (spec §3)
# --------------------------------------------------------------------------
# Table geometry for 1..36: row = (n-1)//3 (0..11), col = (n-1)%3 (0..2).

BET_ODDS: dict[str, int] = {
    "straight": 35, "split": 17, "street": 11, "trio": 11, "corner": 8,
    "basket": 6, "six_line": 5, "dozen": 2, "column": 2,
    "red": 1, "black": 1, "odd": 1, "even": 1, "low": 1, "high": 1,
}

_OUTSIDE: dict[str, tuple[str, list[int]]] = {
    "red":   ("Red", sorted(RED)),
    "black": ("Black", [n for n in range(1, 37) if n not in RED]),
    "odd":   ("Odd", [n for n in range(1, 37) if n % 2]),
    "even":  ("Even", [n for n in range(1, 37) if not n % 2]),
    "low":   ("Low 1-18", list(range(1, 19))),
    "high":  ("High 19-36", list(range(19, 37))),
}
_DOZEN_LABELS = {1: "1st 12", 2: "2nd 12", 3: "3rd 12"}

# Keyword bets, matched after lower-casing and stripping spaces and underscores.
_KEYWORDS: dict[str, tuple[str, int | None]] = {
    "red": ("red", None), "r": ("red", None),
    "black": ("black", None), "b": ("black", None),
    "odd": ("odd", None), "even": ("even", None),
    "low": ("low", None), "1-18": ("low", None), "manque": ("low", None),
    "high": ("high", None), "19-36": ("high", None), "passe": ("high", None),
    "basket": ("basket", None), "topline": ("basket", None), "five": ("basket", None),
}
for _d, _aliases in {1: ("dozen1", "d1", "1st12", "1-12"),
                     2: ("dozen2", "d2", "2nd12", "13-24"),
                     3: ("dozen3", "d3", "3rd12", "25-36")}.items():
    for _a in _aliases:
        _KEYWORDS[_a] = ("dozen", _d)
for _c in (1, 2, 3):
    for _a in (f"col{_c}", f"column{_c}", f"c{_c}"):
        _KEYWORDS[_a] = ("column", _c)

# "prefix:args" forms -> bet type
_PREFIXES: dict[str, str] = {
    "straight": "straight", "split": "split", "street": "street", "trio": "trio",
    "corner": "corner", "line": "six_line", "sixline": "six_line",
    "dozen": "dozen", "column": "column", "col": "column",
}

_F = frozenset
# The only bets that include 0 / 00 (besides the straights).
_ZERO_BETS: dict[frozenset, str] = {
    _F({"0", "1"}): "split", _F({"0", "2"}): "split", _F({"00", "2"}): "split",
    _F({"00", "3"}): "split", _F({"0", "00"}): "split",
    _F({"0", "1", "2"}): "trio", _F({"0", "00", "2"}): "trio", _F({"00", "2", "3"}): "trio",
    _F({"0", "00", "1", "2", "3"}): "basket",
}
_BASKET = _F({"0", "00", "1", "2", "3"})
# "first four" (0/1/2/3) is not a bet on this wheel: invalid, with a hint.
_NO_FIRST_FOUR = "there's no first four on a double-zero wheel - use basket (0/00/1/2/3)"
_FIRST_FOUR_WORDS = ("firstfour", "first4")
_FIRST_FOUR_SET = _F({"0", "1", "2", "3"})

_TYPE_NAMES = {
    "straight": "straight", "split": "split", "street": "street", "trio": "trio",
    "corner": "corner", "basket": "basket", "six_line": "six line",
    "dozen": "dozen", "column": "column",
}
_SIZE_HINT = {"straight": 1, "split": 2, "street": 3, "trio": 3, "corner": 4, "six_line": 6}


class BetError(ValueError):
    pass


def _sort_key(label: str) -> float:
    return 0.5 if label == "00" else float(int(label))


def _sorted_labels(labels) -> list[str]:
    return sorted(labels, key=_sort_key)


def _row(n: int) -> int:
    return (n - 1) // 3


def _col(n: int) -> int:
    return (n - 1) % 3


def _parse_number(tok: str) -> str:
    """One pocket token -> canonical label ("00" stays "00", "07" -> "7")."""
    if tok == "00":
        return "00"
    if not re.fullmatch(r"\d{1,2}", tok or ""):
        raise BetError(f"'{tok}' is not a roulette number" if tok else "missing number")
    n = int(tok)
    if n > 36:
        raise BetError(f"{n} is not a roulette number (0, 00, 1-36)")
    return str(n)


def _parse_numbers(text: str, seps: str) -> list[str]:
    toks = re.split(f"[{re.escape(seps)}]", text)
    labels = [_parse_number(t) for t in toks]
    if len(set(labels)) != len(labels):
        raise BetError("the same number appears twice")
    return labels


def _make_bet(btype: str, labels, label: str) -> dict:
    return {"valid": True, "type": btype, "label": label,
            "numbers": _sorted_labels({str(x) for x in labels}), "odds": BET_ODDS[btype]}


def _inside_bet(labels: list[str], want: str | None = None) -> dict:
    """Classify a set of pocket labels as an inside bet, enforcing table adjacency.

    `want` = the type the caller asked for with a prefix (split:, corner: ...);
    None = infer from the numbers (bare lists)."""
    s = _F(labels)

    if len(s) == 1:
        if want not in (None, "straight"):
            raise BetError(f"a {_TYPE_NAMES[want]} needs {_SIZE_HINT.get(want, 'more')} numbers")
        (lab,) = s
        return _make_bet("straight", s, f"Straight {lab}")

    if "0" in s or "00" in s:
        btype = _ZERO_BETS.get(s)
        if btype is None:
            if s == _FIRST_FOUR_SET:
                raise BetError(_NO_FIRST_FOUR)
            raise BetError(f"{'/'.join(_sorted_labels(s))} is not a bet (zero bets: 0/1 0/2 00/2 00/3 "
                           f"0/00, trios 0/1/2 0/00/2 00/2/3, basket 0/00/1/2/3)")
        if want is not None and want != btype:
            raise BetError(f"{'/'.join(_sorted_labels(s))} is a {_TYPE_NAMES[btype]}, "
                           f"not a {_TYPE_NAMES[want]}")
        nums = "/".join(_sorted_labels(s))
        label = {"split": f"Split {nums}", "trio": f"Trio {nums}", "basket": "Basket"}[btype]
        return _make_bet(btype, s, label)

    ns = sorted(int(x) for x in s)
    size = len(ns)
    expect = {2: "split", 3: "street", 4: "corner", 6: "six_line"}.get(size)
    if want == "trio":
        raise BetError("a trio includes 0 or 00: 0/1/2, 0/00/2 or 00/2/3")
    if want is not None and want != expect:
        raise BetError(f"a {_TYPE_NAMES[want]} takes {_SIZE_HINT.get(want, '?')} numbers, got {size}")
    if expect is None:
        raise BetError(f"{size} numbers don't form a bet (use 1, 2, 3, 4 or 6 adjacent numbers)")

    if expect == "split":
        a, b = ns
        if (b == a + 1 and _row(a) == _row(b)) or b == a + 3:
            return _make_bet("split", ns, f"Split {a}/{b}")
        raise BetError(f"{a} and {b} are not next to each other on the table")
    if expect == "street":
        a = ns[0]
        if _col(a) == 0 and ns == [a, a + 1, a + 2]:
            return _make_bet("street", ns, f"Street {a}-{a + 2}")
        raise BetError(f"{'/'.join(map(str, ns))} is not one row of the table")
    if expect == "corner":
        a = ns[0]
        if _col(a) != 2 and ns == [a, a + 1, a + 3, a + 4]:
            return _make_bet("corner", ns, f"Corner {a}/{a + 1}/{a + 3}/{a + 4}")
        raise BetError(f"{'/'.join(map(str, ns))} is not a 2x2 block on the table")
    # six line
    a = ns[0]
    if _col(a) == 0 and ns == list(range(a, a + 6)):
        return _make_bet("six_line", ns, f"Six Line {a}-{a + 5}")
    raise BetError(f"{'/'.join(map(str, ns))} is not two adjacent rows of the table")


def _outside_bet(btype: str, arg: int | None) -> dict:
    if btype in _OUTSIDE:
        label, nums = _OUTSIDE[btype]
        return _make_bet(btype, [str(n) for n in nums], label)
    if btype == "dozen":
        lo = (arg - 1) * 12 + 1
        return _make_bet("dozen", [str(n) for n in range(lo, lo + 12)], _DOZEN_LABELS[arg])
    if btype == "column":
        return _make_bet("column", [str(n) for n in range(arg, 37, 3)], f"Column {arg}")
    if btype == "basket":
        return _make_bet("basket", _BASKET, "Basket")
    raise BetError("unknown bet")


def _prefixed_bet(btype: str, arg: str) -> dict:
    if not arg:
        raise BetError(f"{btype}: needs numbers")
    if btype in ("dozen", "column"):
        if arg not in ("1", "2", "3"):
            raise BetError(f"{btype} must be 1, 2 or 3")
        return _outside_bet(btype, int(arg))

    labels = _parse_numbers(arg, "-/,")

    # single-number shorthands: street:13, corner:17, line:13
    if len(labels) == 1 and btype in ("street", "corner", "six_line"):
        lab = labels[0]
        n = pocket_value(lab)
        if btype == "street":
            if n < 1 or _col(n) != 0:
                raise BetError("street:N takes the first number of a row (1, 4, 7 ... 34)")
            labels = [str(n), str(n + 1), str(n + 2)]
        elif btype == "corner":
            if n < 1 or n > 32 or _col(n) == 2:
                raise BetError("corner:N takes the top-left (lowest) number of a 2x2 block, "
                               "not in column 3 and at most 32")
            labels = [str(n), str(n + 1), str(n + 3), str(n + 4)]
        else:
            if n < 1 or n > 31 or _col(n) != 0:
                raise BetError("line:N takes the first number of the upper row (1, 4, 7 ... 31)")
            labels = [str(n + i) for i in range(6)]
    # line:13-18 (first-last range)
    elif len(labels) == 2 and btype == "six_line":
        a, b = (pocket_value(x) for x in labels)
        a, b = min(a, b), max(a, b)
        if a < 1 or b != a + 5 or _col(a) != 0:
            raise BetError("line:A-B must span two adjacent rows, e.g. line:13-18")
        labels = [str(a + i) for i in range(6)]

    return _inside_bet(labels, want=btype)


def parse_bet(bet: Any) -> dict:
    """Parse one bet string.

    Returns {"valid": True, "type", "label", "numbers", "odds"} or
    {"valid": False, "type": "invalid", "label", "numbers": [], "odds": 0, "error"}."""
    raw = "" if bet is None else str(bet)
    s = re.sub(r"\s+", "", raw).lower()
    try:
        if not s:
            raise BetError("empty bet")
        key = s.replace("_", "")
        if key in _KEYWORDS:
            btype, arg = _KEYWORDS[key]
            return _outside_bet(btype, arg)
        if key in _FIRST_FOUR_WORDS:
            raise BetError(_NO_FIRST_FOUR)
        if ":" in key:
            prefix, _, arg = key.partition(":")
            btype = _PREFIXES.get(prefix)
            if btype is None:
                raise BetError(f"unknown bet type '{prefix}'")
            return _prefixed_bet(btype, arg)
        if re.fullmatch(r"[0-9/,]+", key):
            return _inside_bet(_parse_numbers(key, "/,"))
        raise BetError("unknown bet")
    except BetError as exc:
        return {"valid": False, "type": "invalid", "label": raw.strip()[:BET_TEXT_MAX_CHARS] or "?",
                "numbers": [], "odds": 0, "error": str(exc)}


def _parse_amount(v: Any) -> tuple[int | float, str | None]:
    """Wager amount: missing -> 0; negative / non-numeric / absurdly large -> (0, error)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return 0, None
    f = _as_float(v)
    if f is None or f < 0 or f > MAX_AMOUNT:
        return 0, "invalid amount"
    return _num(f), None


def resolve_bet(entry: Any, result: dict, default_user: str | None = None) -> dict:
    """Resolve one raw bet ({user, bet, amount} or a bare string) against a RESULT.

    payout = net change (win: amount*odds, lose: -amount); returned = win ?
    amount*(odds+1) : 0. Invalid bets refund: payout 0, returned = amount."""
    if isinstance(entry, str):
        entry = {"bet": entry}
    if not isinstance(entry, dict):
        entry = {"bet": None, "_bad": "bet entry must be an object"}
    user = _clean_user(entry.get("user")) or _clean_user(default_user)
    text = entry.get("bet")
    bet_text = "" if text is None or isinstance(text, (dict, list)) else str(text).strip()[:BET_TEXT_MAX_CHARS]
    amount, amount_err = _parse_amount(entry.get("amount"))

    parsed = parse_bet(bet_text)
    if entry.get("_bad"):
        parsed = {"valid": False, "type": "invalid", "label": "?", "numbers": [], "odds": 0,
                  "error": entry["_bad"]}
    elif amount_err and parsed["valid"]:
        parsed = {**parsed, "valid": False, "error": amount_err}

    out = {"user": user, "bet": bet_text, "amount": amount, **parsed}
    if not parsed["valid"]:
        out.update(win=False, payout=0, returned=amount)
        return out
    win = result.get("number") in parsed["numbers"]
    odds = parsed["odds"]
    out.update(win=win,
               payout=_num(amount * odds) if win else _num(-amount),
               returned=_num(amount * (odds + 1)) if win else 0)
    return out


def bet_summary(bets: list[dict]) -> dict:
    valid = [b for b in bets if b.get("valid")]
    return {
        "bets": len(bets),
        "winners": sum(1 for b in valid if b.get("win")),
        "invalid": len(bets) - len(valid),
        "total_wagered": _num(sum(b.get("amount", 0) for b in valid)),
        "total_payout": _num(sum(b.get("payout", 0) for b in valid)),
    }


BET_REFERENCE: list[dict] = [
    {"type": "straight", "label": "Straight", "odds": 35, "covers": 1,
     "syntax": ["17", "0", "00", "straight:17"]},
    {"type": "split", "label": "Split", "odds": 17, "covers": 2,
     "syntax": ["split:17-20", "split:17/20", "17/20", "0/00"],
     "note": "adjacent on the table (horizontal or vertical); zero splits 0/1 0/2 00/2 00/3 0/00"},
    {"type": "street", "label": "Street", "odds": 11, "covers": 3,
     "syntax": ["street:13", "street:13-14-15", "13/14/15"], "note": "street:N = first number of the row"},
    {"type": "trio", "label": "Trio", "odds": 11, "covers": 3,
     "syntax": ["0/1/2", "0/00/2", "00/2/3", "trio:0/1/2"], "note": "0/1/2, 0/00/2 or 00/2/3"},
    {"type": "corner", "label": "Corner", "odds": 8, "covers": 4,
     "syntax": ["corner:17", "corner:17-18-20-21", "17/18/20/21"],
     "note": "corner:N = top-left (lowest) number, not in column 3"},
    {"type": "basket", "label": "Basket", "odds": 6, "covers": 5,
     "syntax": ["basket", "topline", "five", "0/00/1/2/3"], "note": "0, 00, 1, 2, 3 (house edge 7.89%)"},
    {"type": "six_line", "label": "Six Line", "odds": 5, "covers": 6,
     "syntax": ["line:13", "line:13-18", "sixline:13", "13/14/15/16/17/18"],
     "note": "line:N = first number of the upper row"},
    {"type": "dozen", "label": "Dozen", "odds": 2, "covers": 12,
     "syntax": ["dozen1", "d1", "1st12", "1-12", "dozen2", "d2", "2nd12", "13-24",
                "dozen3", "d3", "3rd12", "25-36"]},
    {"type": "column", "label": "Column", "odds": 2, "covers": 12,
     "syntax": ["col1", "column1", "c1", "col2", "col3"],
     "note": "column 1 = 1,4,7...34; column 2 = 2,5...35; column 3 = 3,6...36"},
    {"type": "red", "label": "Red", "odds": 1, "covers": 18, "syntax": ["red", "r"]},
    {"type": "black", "label": "Black", "odds": 1, "covers": 18, "syntax": ["black", "b"]},
    {"type": "odd", "label": "Odd", "odds": 1, "covers": 18, "syntax": ["odd"]},
    {"type": "even", "label": "Even", "odds": 1, "covers": 18, "syntax": ["even"]},
    {"type": "low", "label": "Low 1-18", "odds": 1, "covers": 18, "syntax": ["low", "1-18", "manque"]},
    {"type": "high", "label": "High 19-36", "odds": 1, "covers": 18, "syntax": ["high", "19-36", "passe"]},
]


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
# SHOW: a winners card after a result (/announce, every game) and, for craps,
# the "on the table" board (/craps/board). Display calls never touch a table's
# bets, the ledger, history or stats.

def _display_text(v: Any, limit: int) -> str | None:
    """A display string: control chars removed, trimmed, at most `limit` chars.
    Empty or not text-like -> None."""
    if v is None or isinstance(v, (dict, list, bool)):
        return None
    s = _CTRL.sub("", str(v)).strip()[:limit].strip()
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
# Hooks a game may override (the defaults are exactly roulette's behaviour):
#   duration_range()        clamp for a spin's `duration` (+ duration_key: config default)
#   spin_outcome()          game-specific SPIN fields computed at spin time (roulette: bets, summary)
#   state_extra()           merged into STATE (craps: {"table": TABLE})
#   extra_visible()         OR'ed into visible() unless hidden (craps: bets on the table)
#   on_commit(spin)         after a non-test spin is committed (land / stop / heal / crash)
#   on_idle()               after a run fully ends on its own (result/cooldown over, or healed)
#   on_config()             after a config save
#   before_spin(params)     sync, between the busy check and the start (craps: place `bets`)
#   spin_response(...)      extra fields for the spin HTTP response
#   validate(params)        /validate body;  bets_payload()  /bets body
#
# Every game also carries the bot's winners card (/announce, STATE.announce):
# display only, it keeps the game visible while it is up, expires on its own
# (server clears it + broadcasts) and a new spin takes it down.


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
        return {"bet": (bet or "")[:BET_TEXT_MAX_CHARS], **self.parse_bet(bet)}

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

    def extra_visible(self) -> bool:
        return False

    def on_commit(self, spin: dict) -> None:
        pass

    def on_idle(self) -> None:
        pass

    def on_config(self) -> None:
        pass

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
                or self.extra_visible() or self.announce_active())

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
        """Abort: cancel timers, commit an in-flight spin, go idle + hidden.
        Returns True if something was active."""
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
# roulette
# --------------------------------------------------------------------------

_THEMES = ("classic", "neon", "midnight", "royal")


class Roulette(Game):
    key = "roulette"
    title = "Roulette"
    id_prefix = "r"
    has_bets = True

    # No `wheel` key: the wheel is always American double-zero. A legacy
    # "wheel" in a saved config is an unknown key and is dropped on load.
    DEFAULTS: dict[str, Any] = {
        # placement: wheel centre in % of the 1920x1080 stage; scale x 400px
        "x": 50, "y": 50, "scale": 1.25,
        "theme": "classic",             # classic | neon | midnight | royal
        "red_color": "", "black_color": "", "green_color": "",   # "" = theme default
        "spin_seconds": 9,              # launch -> ball settled
        "result_seconds": 6,            # result stays up this long after landing
        "hide_when_idle": True,         # False = wheel always on screen
        "show_result": True, "result_position": "center",       # center | below | above
        "result_details": True,         # ODD . LOW . 2ND 12 line
        "show_history": True, "history_count": 10,
        "show_user": True,              # "@user spins" caption
        "show_bets": True, "bets_max": 5,
        "sfx": True, "sfx_volume": 0.5, # overlay's synthesized ball sounds
        "spin_clip": "", "land_clip": "",                        # soundboard clips
        "cooldown_seconds": 0,          # extra lockout after the result phase
    }
    SCHEMA: dict[str, tuple] = {
        "x": ("num", 0, 100), "y": ("num", 0, 100), "scale": ("num", 0.2, 5),
        "theme": ("enum", _THEMES),
        "red_color": ("color",), "black_color": ("color",), "green_color": ("color",),
        "spin_seconds": ("num", SPIN_SECONDS_MIN, SPIN_SECONDS_MAX),
        "result_seconds": ("num", 1, 120),
        "hide_when_idle": ("bool",),
        "show_result": ("bool",), "result_position": ("enum", ("center", "below", "above")),
        "result_details": ("bool",),
        "show_history": ("bool",), "history_count": ("int", 1, 20),
        "show_user": ("bool",),
        "show_bets": ("bool",), "bets_max": ("int", 1, 20),
        "sfx": ("bool",), "sfx_volume": ("num", 0, 1),
        "spin_clip": ("str", 200), "land_clip": ("str", 200),
        "cooldown_seconds": ("num", 0, 3600),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "red_color", "black_color", "green_color",
                  "result_position", "result_details", "show_result", "show_history",
                  "history_count", "show_user", "show_bets", "bets_max", "sfx", "sfx_volume")

    def pick(self, cfg: dict) -> dict:
        # uniform over the 38 pockets, OS entropy - nothing can steer this
        return result_for(AMERICAN[_RNG.randrange(len(AMERICAN))])

    def spin_fields(self) -> dict:
        return {"wheel": WHEEL}

    def parse_bet(self, bet: Any) -> dict:
        return parse_bet(bet)

    def resolve_bet(self, entry: Any, result: dict, default_user: str | None = None) -> dict:
        return resolve_bet(entry, result, default_user)

    def bets_reference(self) -> list[dict]:
        return json.loads(json.dumps(BET_REFERENCE))

    def bets_rules(self) -> list[str]:
        return [
            "A bet is {user, bet, amount}; odds are net 'X to 1'.",
            "payout = win ? amount*odds : -amount; returned = win ? amount*(odds+1) : 0.",
            "Missing amount counts as 0. Invalid bets never block a spin: valid:false, "
            "payout 0, returned = amount (refund).",
            "Case-insensitive, spaces ignored. Bare number lists use / or , separators, any order.",
            "Even-money bets lose on 0/00.",
        ]

    def reset_stats(self) -> None:
        self._spins = 0
        self._tally = {k: 0 for k in ("red", "black", "green", "odd", "even", "low", "high")}
        self._counts: dict[str, int] = {}
        self._seen: dict[str, int] = {}     # label -> spin number it was last seen on
        self._streak = {"color": None, "length": 0}

    def record(self, result: dict) -> None:
        self._spins += 1
        label, color = result["number"], result["color"]
        self._tally[color] += 1
        if result.get("parity"):
            self._tally[result["parity"]] += 1
        if result.get("range"):
            self._tally[result["range"]] += 1
        self._counts[label] = self._counts.get(label, 0) + 1
        self._seen[label] = self._spins
        if self._streak["color"] == color:
            self._streak["length"] += 1
        else:
            self._streak = {"color": color, "length": 1}

    def stats(self) -> dict:
        seen = [lab for lab, n in self._counts.items() if n > 0]
        hot = sorted(seen, key=lambda lab: (-self._counts[lab], -self._seen.get(lab, 0)))[:5]
        cold = sorted(AMERICAN,
                      key=lambda lab: (self._counts.get(lab, 0), self._seen.get(lab, 0), _sort_key(lab)))[:5]
        return {"spins": self._spins, **self._tally,
                "counts": dict(sorted(self._counts.items(), key=lambda kv: _sort_key(kv[0]))),
                "hot": hot, "cold": cold, "streak": dict(self._streak)}


# --------------------------------------------------------------------------
# craps: dice + RESULT (craps spec §2)
# --------------------------------------------------------------------------
# Two fair dice from _RNG. The RESULT is built from the table state BEFORE the
# roll (phase/point) and carries the stickman's call. The server owns the call
# strings; renderers only display them.

POINTS = (4, 5, 6, 8, 9, 10)            # box numbers: the point, place bets, come travel
HARDWAYS = (4, 6, 8, 10)
_NUM_WORDS = {4: "four", 5: "five", 6: "six", 8: "eight", 9: "nine", 10: "ten"}
_PROP_CALLS = {2: "ACES", 3: "ACE-DEUCE", 11: "YO-LEVEN", 12: "BOXCARS"}


def _stickman(total: int, pair: bool, event: str, point: int | None) -> tuple[str, str]:
    """(call, sub): the stickman's big call (<= 24 chars, uppercase) and the short line under it."""
    word = _NUM_WORDS.get(total, "")
    if event == "natural":
        return ("SEVEN · WINNER" if total == 7 else "YO-LEVEN"), "Front line winner"
    if event == "craps":
        return _PROP_CALLS[total], ("Craps · bar the 12" if total == 12 else "Craps · line away")
    if event == "point_set":
        way = "" if total in (5, 9) else ("hard " if pair else "easy ")
        return f"POINT IS {total}", f"{way}{word} · mark it".capitalize()
    if event == "point_made":
        return f"WINNER {total}", "Pay the line"
    if event == "seven_out":
        return "SEVEN OUT", "Line away · don't pass wins"
    # any other roll while a point is on
    if total in _PROP_CALLS:
        call = _PROP_CALLS[total]
    elif total in HARDWAYS:
        call = ("HARD " if pair else "EASY ") + word.upper()
    else:
        call = word.upper()
    return call, f"Point is {point}"


def craps_result(d1: int, d2: int, phase: str = "come_out", point: int | None = None) -> dict:
    """The craps RESULT for dice (d1, d2) thrown while the table is in (phase, point)."""
    d1, d2 = int(d1), int(d2)
    if not (1 <= d1 <= 6 and 1 <= d2 <= 6):
        raise ValueError("dice show 1..6")
    if phase != "point" or point not in POINTS:
        phase, point = "come_out", None
    total = d1 + d2
    pair = d1 == d2
    if phase == "come_out":
        event = "natural" if total in (7, 11) else "craps" if total in (2, 3, 12) else "point_set"
    else:
        event = "point_made" if total == point else "seven_out" if total == 7 else "roll"
    if event == "point_set":
        after: tuple[str, int | None] = ("point", total)
    elif event in ("point_made", "seven_out"):
        after = ("come_out", None)
    else:
        after = (phase, point)
    call, sub = _stickman(total, pair, event, point)
    return {"dice": [d1, d2], "total": total, "pair": pair, "hard": pair and total in HARDWAYS,
            "number": str(total), "label": f"{d1}-{d2}",
            "event": event, "call": call, "sub": sub,
            "phase_before": phase, "point_before": point,
            "phase_after": after[0], "point_after": after[1]}


# --------------------------------------------------------------------------
# craps: bets (craps spec §3.1 / §3.2)
# --------------------------------------------------------------------------
# BET (internal, persisted): {id, user, type, number, amount, odds, placed_at}.
# The public BET view adds label / working / removable / one_roll.

CRAPS_TYPES = ("pass", "dont_pass", "come", "dont_come", "place", "hard", "field", "any7", "any_craps",
               "aces", "ace_deuce", "yo", "boxcars", "horn", "ce")
LINE_TYPES = frozenset(("pass", "dont_pass", "come", "dont_come"))    # the bets that take odds
DONT_TYPES = frozenset(("dont_pass", "dont_come"))                     # ... as lay odds
ONE_ROLL = frozenset(("field", "any7", "any_craps", "aces", "ace_deuce", "yo", "boxcars", "horn", "ce"))
_CRAPS_LABELS = {
    "pass": "Pass line", "dont_pass": "Don't pass", "come": "Come", "dont_come": "Don't come",
    "place": "Place", "hard": "Hard", "field": "Field", "any7": "Any 7", "any_craps": "Any craps",
    "aces": "Aces", "ace_deuce": "Ace-deuce", "yo": "Yo 11", "boxcars": "Boxcars", "horn": "Horn", "ce": "C&E",
}

# net "X to 1" payouts (Fractions: exact; winnings are rounded DOWN to whole coins)
TRUE_ODDS = {4: Fraction(2), 10: Fraction(2), 5: Fraction(3, 2), 9: Fraction(3, 2),
             6: Fraction(6, 5), 8: Fraction(6, 5)}
LAY_ODDS = {n: 1 / r for n, r in TRUE_ODDS.items()}                 # 1:2, 2:3, 5:6
PLACE_PAYS = {4: Fraction(9, 5), 10: Fraction(9, 5), 5: Fraction(7, 5), 9: Fraction(7, 5),
              6: Fraction(7, 6), 8: Fraction(7, 6)}
HARD_PAYS = {4: 7, 10: 7, 6: 9, 8: 9}
PROP_PAYS = {"any7": 4, "any_craps": 7, "aces": 30, "ace_deuce": 15, "yo": 15, "boxcars": 30}
_PROP_WINS = {"any7": (7,), "any_craps": (2, 3, 12), "aces": (2,), "ace_deuce": (3,), "yo": (11,),
              "boxcars": (12,)}
HORN_PAYS = {2: Fraction(27, 4), 12: Fraction(27, 4), 3: Fraction(3), 11: Fraction(3)}   # net, whole bet
CE_PAYS = {2: 3, 3: 3, 12: 3, 11: 7}                                                   # net, whole bet
ODDS_RULES = ("345", "1", "2", "3", "5", "10", "20", "100")
_ODDS_345 = {4: 3, 10: 3, 5: 4, 9: 4, 6: 5, 8: 5}
_ODDS_EXACT = {4: 1, 10: 1, 5: 2, 9: 2, 6: 5, 8: 5}        # odds amounts that pay exactly
_LAY_EXACT = {4: 2, 10: 2, 5: 3, 9: 3, 6: 6, 8: 6}


def _rt(r: Fraction | int) -> str:
    r = Fraction(r)
    return f"{r.numerator}:{r.denominator}"


def _pay(amount: int, ratio: Fraction | int) -> int:
    """Winnings on `amount` at net `ratio`:1, rounded DOWN to whole coins."""
    r = Fraction(ratio)
    return (amount * r.numerator) // r.denominator


def bet_label(btype: str, number: int | None = None) -> str:
    base = _CRAPS_LABELS.get(btype, btype)
    if number is not None and btype in ("come", "dont_come", "place", "hard"):
        return f"{base} {number}"
    return base


def odds_label(b: dict) -> str:
    """Ledger/display label of the odds behind a line bet ("Come 6 odds", "Don't pass lay odds")."""
    return f"{bet_label(b['type'], b['number'])} {'lay odds' if b['type'] in DONT_TYPES else 'odds'}"


def bet_view(b: dict, phase: str) -> dict:
    """Public BET object for an internal bet on a table in `phase`."""
    t, n = b["type"], b["number"]
    come_out = phase != "point"
    odds_working = not (come_out and t == "come")          # come odds are OFF on a come-out roll
    if t in ("place", "hard"):
        working = not come_out
    elif t == "come" and b["odds"] > 0:
        working = odds_working
    else:
        working = True
    removable = not ((t == "pass" and not come_out) or (t == "come" and n is not None))
    return {"id": b["id"], "user": b["user"], "type": t, "number": n, "amount": b["amount"],
            "odds": b["odds"], "label": bet_label(t, n), "working": working, "removable": removable,
            "one_roll": t in ONE_ROLL, "placed_at": b["placed_at"],
            "odds_working": odds_working if t in LINE_TYPES else None}


def odds_limit(flat: int, number: int, lay: bool, rule: str) -> int:
    """Most odds allowed behind a `flat` line bet on `number` (spec §3.2), whole coins."""
    if rule == "345":
        return flat * (6 if lay else _ODDS_345[number])
    n = int(rule)
    if not lay:
        return flat * n
    # lay: the amount whose win is <= N x flat -> x2 on 4/10, x1.5 on 5/9, x1.2 on 6/8, floored
    return math.floor(n * flat / LAY_ODDS[number])


def craps_odds_text(btype: str, number: int | None = None, field_12: int = 3) -> str:
    if btype in ("pass", "come"):
        return "1:1"
    if btype in ("dont_pass", "dont_come"):
        return "1:1 (bar 12)"
    if btype == "place":
        return _rt(PLACE_PAYS[number]) if number in PLACE_PAYS else "4/10 9:5 · 5/9 7:5 · 6/8 7:6"
    if btype == "hard":
        return f"{HARD_PAYS[number]}:1" if number in HARD_PAYS else "4/10 7:1 · 6/8 9:1"
    if btype == "field":
        return f"1:1 · 2 pays 2:1 · 12 pays {field_12}:1"
    if btype in PROP_PAYS:
        return f"{PROP_PAYS[btype]}:1"
    if btype == "horn":
        return "2/12 27:4 · 3/11 3:1 (net)"
    if btype == "ce":
        return "craps 3:1 · 11 7:1 (net)"
    if btype == "odds":
        return _rt(TRUE_ODDS[number]) if number in TRUE_ODDS else "4/10 2:1 · 5/9 3:2 · 6/8 6:5"
    if btype == "lay_odds":
        return _rt(LAY_ODDS[number]) if number in LAY_ODDS else "4/10 1:2 · 5/9 2:3 · 6/8 5:6"
    return ""


def _flat_hint(btype: str, number: int | None, total: int) -> str | None:
    """A hint when a flat amount doesn't pay exactly (winnings round down)."""
    if btype == "place" and number in PLACE_PAYS:
        m = 6 if number in (6, 8) else 5
        if total % m:
            return (f"Place {number} pays {_rt(PLACE_PAYS[number])}: bet multiples of {m} to be paid in full "
                    f"({total} wins {_pay(total, PLACE_PAYS[number])} - winnings round down)")
    if btype == "horn" and total % 4:
        return "a horn bet is 4 units (2, 3, 11, 12): use multiples of 4"
    if btype == "ce" and total % 2:
        return "C&E is 2 units (any craps + eleven): use even amounts"
    return None


def _odds_hint(number: int, lay: bool, total: int) -> str | None:
    m = (_LAY_EXACT if lay else _ODDS_EXACT)[number]
    if m > 1 and total % m:
        ratio = (LAY_ODDS if lay else TRUE_ODDS)[number]
        what = "lay odds" if lay else "odds"
        return (f"{what} on {number} pay {_rt(ratio)}: use multiples of {m} to be paid in full "
                f"({total} wins {_pay(total, ratio)} - winnings round down)")
    return None


# ---- parser -----------------------------------------------------------------
# Case-insensitive; spaces, underscores, hyphens and apostrophes are ignored.

_CRAPS_WORDS: dict[str, str] = {}
for _t, _aliases in {
    "pass": ("pass", "passline", "line", "pl"),
    "dont_pass": ("dontpass", "dontpassline", "dp"),
    "come": ("come", "comebet"),
    "dont_come": ("dontcome", "dc"),
    "field": ("field",),
    "any7": ("any7", "anyseven", "seven", "bigred", "7"),
    "any_craps": ("anycraps", "craps", "ac"),
    "aces": ("aces", "snakeeyes", "2", "two"),
    "ace_deuce": ("acedeuce", "3", "three"),
    "yo": ("yo", "eleven", "11", "yoleven", "yo11"),
    "boxcars": ("boxcars", "midnight", "12", "twelve"),
    "horn": ("horn",),
    "ce": ("ce", "c&e", "crapseleven", "crapsandeleven"),
}.items():
    for _a in _aliases:
        _CRAPS_WORDS[_a] = _t

_ODDS_WORDS: dict[str, str] = {"odds": "pass", "passodds": "pass", "layodds": "dont_pass", "dpodds": "dont_pass"}
for _w, _on in {"odds:pass": "pass", "odds:passline": "pass", "odds:line": "pass",
                "odds:dontpass": "dont_pass", "odds:dp": "dont_pass",
                "layodds:dontpass": "dont_pass", "layodds:dp": "dont_pass"}.items():
    _ODDS_WORDS[_w] = _ODDS_WORDS[_w.replace(":", "")] = _on    # colon optional: "odds pass" == "oddspass"
_N = r"(4|5|6|8|9|10|four|five|six|eight|nine|ten)"
_RE_PLACE = re.compile(rf"^(?:place:?{_N}|p(4|5|6|8|9|10)|(4|5|6|8|9|10))$")
_RE_HARD = re.compile(rf"^(?:hard:?{_N}|h(4|5|6|8|9|10))$")
# odds:N / odds N (spaces are ignored, so the colon is optional like every other form)
_RE_COME_ODDS = re.compile(rf"^(?:odds:?(?:come)?|comeodds:?){_N}$")
_RE_DC_ODDS = re.compile(rf"^(?:odds:?(?:dontcome|dc)|layodds:?|dcodds:?){_N}$")
_RE_COME_N = re.compile(rf"^come:?{_N}$")                 # a travelled come bet (lookups / removal)
_RE_DC_N = re.compile(rf"^(?:dontcome|dc):?{_N}$")
_RE_PLACE_BAD = re.compile(r"^(?:place:?|p)\d+$")
_RE_HARD_BAD = re.compile(r"^(?:hard:?|h)(?:\d+|one|two|three|five|seven|nine|eleven|twelve)$")
_RE_ODDS_BAD = re.compile(r"^(?:odds|comeodds|layodds|dcodds)(?::|:?come|:?dontcome|:?dc)?\d+$")


def _num_token(tok: str) -> int:
    return int(tok) if tok.isdigit() else {w: n for n, w in _NUM_WORDS.items()}[tok]


def _craps_key(text: Any) -> str:
    return re.sub(r"[\s_\-'’`]+", "", "" if text is None else str(text)).lower()


def parse_craps_bet(text: Any) -> dict:
    """One craps bet string -> {"valid": True, "kind": "flat", "type", "number"} |
    {"valid": True, "kind": "odds", "on": <line type>, "number"} | {"valid": False, "error"}."""
    key = _craps_key(text)
    if not key:
        return {"valid": False, "error": "empty bet"}
    if key in _CRAPS_WORDS:
        return {"valid": True, "kind": "flat", "type": _CRAPS_WORDS[key], "number": None}
    if key in _ODDS_WORDS:
        return {"valid": True, "kind": "odds", "on": _ODDS_WORDS[key], "number": None}
    m = _RE_PLACE.match(key)
    if m:
        return {"valid": True, "kind": "flat", "type": "place", "number": _num_token(next(g for g in m.groups() if g))}
    m = _RE_HARD.match(key)
    if m:
        n = _num_token(next(g for g in m.groups() if g))
        if n not in HARDWAYS:
            return {"valid": False, "error": "hard ways are 4, 6, 8 and 10"}
        return {"valid": True, "kind": "flat", "type": "hard", "number": n}
    m = _RE_DC_ODDS.match(key)
    if m:
        return {"valid": True, "kind": "odds", "on": "dont_come", "number": _num_token(m.group(1))}
    m = _RE_COME_ODDS.match(key)
    if m:
        return {"valid": True, "kind": "odds", "on": "come", "number": _num_token(m.group(1))}
    m = _RE_DC_N.match(key)
    if m:
        return {"valid": True, "kind": "flat", "type": "dont_come", "number": _num_token(m.group(1))}
    m = _RE_COME_N.match(key)
    if m:
        return {"valid": True, "kind": "flat", "type": "come", "number": _num_token(m.group(1))}
    if _RE_PLACE_BAD.match(key):
        return {"valid": False, "error": "place bets go on 4, 5, 6, 8, 9 or 10"}
    if _RE_HARD_BAD.match(key):
        return {"valid": False, "error": "hard ways are 4, 6, 8 and 10"}
    if _RE_ODDS_BAD.match(key):
        return {"valid": False, "error": "odds go on 4, 5, 6, 8, 9 or 10"}
    shown = re.sub(r"\s+", " ", str(text)).strip()[:40]
    return {"valid": False, "error": f"unknown bet '{shown}' (try pass, dontpass, come, dontcome, odds, "
                                     f"place6, hard8, field, any7, anycraps, yo, horn, ce)"}


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


# --------------------------------------------------------------------------
# craps: resolution engine (craps spec §3.3)
# --------------------------------------------------------------------------
# Pure functions. craps_settle() computes every SETTLEMENT of a roll from the
# frozen table (at roll time); craps_apply() applies them to a table and returns
# the ledger credits (at landing, via Game._commit -> Craps.on_commit).

def _settlement(b: dict, outcome: str, *, won: int = 0, credit: int = 0, stays: bool = False, lost: int = 0,
                odds_returned: int = 0, note: str = "", travel_to: int | None = None) -> dict:
    s = {"bet_id": b["id"], "user": b["user"], "type": b["type"], "label": bet_label(b["type"], b["number"]),
         "number": b["number"], "amount": b["amount"], "odds": b["odds"], "outcome": outcome,
         "won": won, "credit": credit, "stays": stays, "note": note,
         "lost": lost, "odds_returned": odds_returned}
    if travel_to is not None:
        s["travel_to"] = travel_to
    return s


def _settle_do(b: dict, total: int, come_out: bool, point: int | None) -> dict | None:
    """pass / come."""
    t, a, o, lab = b["type"], b["amount"], b["odds"], bet_label(b["type"], b["number"])
    own_come_out = come_out if t == "pass" else b["number"] is None
    if own_come_out:
        # (odds can't be behind a bet on its own come-out; if they ever are, they're returned)
        if total in (7, 11):
            return _settlement(b, "win", won=a, credit=2 * a + o, odds_returned=o,
                               note=f"{lab} 1:1 on {total}" + (" · odds returned" if o else ""))
        if total in (2, 3, 12):
            if o:
                return _settlement(b, "returned", credit=o, lost=a, odds_returned=o,
                                   note=f"{lab} loses on craps {total} · odds returned")
            return _settlement(b, "lose", lost=a, note=f"{lab} loses on craps {total}")
        if t == "come":
            return _settlement(b, "travel", travel_to=total, note=f"Come → {total}")
        return None                                  # pass: the point is set, the bet stays (contract)
    num = point if t == "pass" else b["number"]
    odds_off = t == "come" and come_out               # come odds are OFF on a come-out roll
    if total == num:
        if o and odds_off:
            return _settlement(b, "win", won=a, credit=2 * a + o, odds_returned=o,
                               note=f"{lab} 1:1 · odds returned (off on the come-out)")
        ow = _pay(o, TRUE_ODDS[num])
        return _settlement(b, "win", won=a + ow, credit=2 * a + o + ow,
                           note=f"{lab} 1:1" + (f" + odds {_rt(TRUE_ODDS[num])}" if o else ""))
    if total == 7:
        if o and odds_off:
            return _settlement(b, "returned", credit=o, lost=a, odds_returned=o,
                               note=f"{lab} loses on 7 · odds returned (off on the come-out)")
        return _settlement(b, "lose", lost=a + o, note="Seven out · line away" if t == "pass" else f"{lab} loses on 7")
    return None


def _settle_dont(b: dict, total: int, come_out: bool, point: int | None) -> dict | None:
    """don't pass / don't come (lay odds always work)."""
    t, a, o, lab = b["type"], b["amount"], b["odds"], bet_label(b["type"], b["number"])
    own_come_out = come_out if t == "dont_pass" else b["number"] is None
    if own_come_out:
        if total in (2, 3):
            return _settlement(b, "win", won=a, credit=2 * a + o, odds_returned=o,
                               note=f"{lab} 1:1 on craps {total}")
        if total == 12:
            return _settlement(b, "push", credit=a + o, odds_returned=o, note=f"{lab} · bar 12, push")
        if total in (7, 11):
            if o:
                return _settlement(b, "returned", credit=o, lost=a, odds_returned=o,
                                   note=f"{lab} loses on {total} · odds returned")
            return _settlement(b, "lose", lost=a, note=f"{lab} loses on {total}")
        if t == "dont_come":
            return _settlement(b, "travel", travel_to=total, note=f"Don't come → {total}")
        return None                                  # don't pass: behind the point now
    num = point if t == "dont_pass" else b["number"]
    if total == 7:
        ow = _pay(o, LAY_ODDS[num])
        return _settlement(b, "win", won=a + ow, credit=2 * a + o + ow,
                           note=f"{lab} 1:1 on 7" + (f" + lay odds {_rt(LAY_ODDS[num])}" if o else ""))
    if total == num:
        return _settlement(b, "lose", lost=a + o, note=f"{lab} loses on {num}")
    return None


def _settle_bet(b: dict, total: int, pair: bool, come_out: bool, point: int | None, field_12: int) -> dict | None:
    t, a, n = b["type"], b["amount"], b["number"]
    if t in ("pass", "come"):
        return _settle_do(b, total, come_out, point)
    if t in DONT_TYPES:
        return _settle_dont(b, total, come_out, point)
    lab = bet_label(t, n)
    if t == "place":
        if come_out:
            return None                              # OFF on the come-out
        if total == n:
            w = _pay(a, PLACE_PAYS[n])
            return _settlement(b, "win", won=w, credit=w, stays=True, note=f"{lab} {_rt(PLACE_PAYS[n])} · stays up")
        if total == 7:
            return _settlement(b, "lose", lost=a, note=f"{lab} loses on 7")
        return None
    if t == "hard":
        if come_out:
            return None                              # OFF on the come-out
        if total == n:
            if pair:
                w = a * HARD_PAYS[n]
                return _settlement(b, "win", won=w, credit=w, stays=True, note=f"{lab} {HARD_PAYS[n]}:1 · stays up")
            return _settlement(b, "lose", lost=a, note=f"Easy {n} · {lab} loses")
        if total == 7:
            return _settlement(b, "lose", lost=a, note=f"{lab} loses on 7")
        return None
    # one-roll bets: always decided, always come down
    ratio: Fraction | None = None                    # net X:1 when this roll wins the bet
    if t == "field":
        ratio = {2: Fraction(2), 12: Fraction(field_12)}.get(
            total, Fraction(1) if total in (3, 4, 9, 10, 11) else None)
    elif t in PROP_PAYS:
        ratio = Fraction(PROP_PAYS[t]) if total in _PROP_WINS[t] else None
    elif t == "horn":
        ratio = HORN_PAYS.get(total)
    elif t == "ce":
        ratio = Fraction(CE_PAYS[total]) if total in CE_PAYS else None
    else:
        return None
    if ratio is not None:
        w = _pay(a, ratio)
        return _settlement(b, "win", won=w, credit=a + w, note=f"{lab} {_rt(ratio)} on {total}")
    return _settlement(b, "lose", lost=a, note=f"{lab} loses on {total}")


def craps_settle(bets: list[dict], result: dict, field_12: int = 3) -> list[dict]:
    """Every SETTLEMENT (bets with action) of a roll, in table order."""
    total, pair = result["total"], result["pair"]
    come_out = result["phase_before"] != "point"
    point = result["point_before"]
    out = []
    for b in bets:
        s = _settle_bet(b, total, pair, come_out, point, field_12)
        if s is not None:
            out.append(s)
    return out


def _aggregate_credits(items: list[dict], key: str = "credit") -> list[dict]:
    """[{user, amount}] per user (first-appearance order), amounts > 0 only."""
    by: dict[str, int] = {}
    for it in items:
        amt = it.get(key, 0)
        if amt > 0:
            by[it["user"]] = by.get(it["user"], 0) + amt
    return [{"user": u, "amount": a} for u, a in by.items()]


def craps_summary(settlements: list[dict]) -> dict:
    return {
        "bets_settled": sum(1 for s in settlements if s["outcome"] != "travel"),
        "winners": sum(1 for s in settlements if s["outcome"] == "win"),
        "total_won": sum(s["won"] for s in settlements),
        "total_lost": sum(s["lost"] for s in settlements),
        "total_credited": sum(s["credit"] for s in settlements),
    }


def _ev(etype: str, user: str, amount: int, reason: str, bet_id: str | None, label: str | None,
        roll_id: str | None) -> dict:
    """A ledger event before Ledger.append() stamps seq/ts/game on it."""
    return {"type": etype, "user": user, "amount": amount, "reason": reason, "bet_id": bet_id,
            "bet": label, "roll_id": roll_id}


def _fresh_table() -> dict:
    return {"phase": "come_out", "point": None, "shooter": None, "hand_rolls": 0, "bets": []}


def craps_apply(table: dict, spin: dict) -> list[dict]:
    """Commit a (non-test) roll to `table` in place: settle bets, move come bets,
    advance phase/point/shooter/hand. Returns the credit events (no seq yet)."""
    result = spin["result"]
    roll_id = spin.get("id")
    if (result["phase_before"], result["point_before"]) != (table["phase"], table["point"]):
        log.warning("[games] craps roll %s was thrown on %s/%s but the table is %s/%s", roll_id,
                    result["phase_before"], result["point_before"], table["phase"], table["point"])
    live = {b["id"]: b for b in table["bets"]}
    gone: set[str] = set()
    events: list[dict] = []
    for s in spin.get("settlements") or []:
        b = live.get(s["bet_id"])
        if b is None or b["amount"] != s["amount"] or b["odds"] != s["odds"] or b["user"] != s["user"]:
            log.warning("[games] craps roll %s: bet %s changed while the dice flew - not settled", roll_id, s["bet_id"])
            continue
        if s["outcome"] == "travel":
            b["number"] = s["travel_to"]
            continue
        if not s["stays"]:
            gone.add(b["id"])
        main = s["credit"] - s["odds_returned"]
        if main > 0:
            reason = "win_stays" if s["stays"] else ("push" if s["outcome"] == "push" else "win")
            events.append(_ev("credit", b["user"], main, reason, b["id"], s["label"], roll_id))
        if s["odds_returned"] > 0:
            events.append(_ev("credit", b["user"], s["odds_returned"], "returned", b["id"], odds_label(b), roll_id))
    table["bets"] = [b for b in table["bets"] if b["id"] not in gone]
    table["hand_rolls"] = int(table.get("hand_rolls") or 0) + 1
    if not table.get("shooter") and spin.get("shooter"):
        table["shooter"] = spin["shooter"]
    table["phase"], table["point"] = result["phase_after"], result["point_after"]
    if result["event"] == "seven_out":                 # the hand is over: next roller shoots
        table["shooter"], table["hand_rolls"] = None, 0
    return events


# --------------------------------------------------------------------------
# craps: persistence (table file + ledger)
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


def _clean_bet(raw: Any) -> dict | None:
    """A persisted bet, validated (None = unusable)."""
    if not isinstance(raw, dict):
        return None
    t = raw.get("type")
    user = _clean_user(raw.get("user"))
    bid = raw.get("id")
    amount = _strict_int(raw.get("amount"))
    odds = _strict_int(raw.get("odds", 0))
    if (t not in CRAPS_TYPES or not user or not isinstance(bid, str) or not bid.strip()
            or amount is None or amount < 1 or odds is None or odds < 0):
        return None
    n = raw.get("number")
    n = None if n is None else _strict_int(n)
    if t == "place" and n not in POINTS:
        return None
    if t == "hard" and n not in HARDWAYS:
        return None
    if t in ("come", "dont_come") and n is not None and n not in POINTS:
        return None
    if t not in ("place", "hard", "come", "dont_come"):
        n = None
    if t not in LINE_TYPES and odds:
        return None
    placed = _as_float(raw.get("placed_at")) or 0.0
    return {"id": bid.strip()[:64], "user": user, "type": t, "number": n, "amount": amount, "odds": odds,
            "placed_at": round(placed, 3)}


def clean_table(raw: Any) -> tuple[dict, int]:
    """(table, last_seq) from a parsed games_craps_table.json (tolerant)."""
    t = _fresh_table()
    if not isinstance(raw, dict):
        return t, 0
    point = _strict_int(raw.get("point"))
    if raw.get("phase") == "point" and point in POINTS:
        t["phase"], t["point"] = "point", point
    t["shooter"] = _clean_user(raw.get("shooter"))
    hr = _strict_int(raw.get("hand_rolls"))
    t["hand_rolls"] = hr if hr is not None and hr > 0 else 0
    seen: set[str] = set()
    bets = raw.get("bets")
    for rb in bets if isinstance(bets, list) else []:
        b = _clean_bet(rb)
        if b is None or b["id"] in seen:
            log.error("[games] craps table: dropped an unreadable bet %r", rb)
            continue
        seen.add(b["id"])
        t["bets"].append(b)
    last_seq = _strict_int(raw.get("last_seq"))
    return t, (last_seq if last_seq and last_seq > 0 else 0)


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


def load_craps_state(path: Path) -> tuple[dict, int, list[dict]]:
    """(table, last_seq, journal) from games_craps_table.json. The journal holds the
    ledger events of the table's last change (written BEFORE they're appended to
    the ledger, so a crash between the two writes can be repaired at start-up)."""
    if not path.exists():
        return _fresh_table(), 0, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("[games] %s is unreadable - starting with an empty table (copy kept as .bad)", path)
        try:
            bad = path.with_name(path.name + ".bad")
            bad.write_bytes(path.read_bytes())
        except OSError:
            pass
        return _fresh_table(), 0, []
    table, last_seq = clean_table(raw)
    journal = raw.get("journal") if isinstance(raw, dict) else None
    events = [e for e in map(_clean_event, journal if isinstance(journal, list) else []) if e is not None]
    return table, last_seq, sorted(events, key=lambda e: e["seq"])


def load_craps_table(path: Path) -> tuple[dict, int]:
    """Restore the table (a restart mid-roll simply loses that roll: bets stay)."""
    table, last_seq, _ = load_craps_state(path)
    return table, last_seq


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


class Ledger:
    """Every coin movement of the craps table (craps spec §4): sequenced, appended
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

    def stamp(self, events: list[dict]) -> list[dict]:
        """Give each event its seq/ts/game (memory only - write() persists them)."""
        ts = round(time.time(), 3)
        out = []
        for e in events:
            self.last_seq += 1
            out.append({"seq": self.last_seq, "ts": ts, "game": "craps", **e})
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

    def append(self, events: list[dict]) -> list[dict]:
        """Stamp seq/ts/game on each event, persist them, return the stamped events."""
        return self.write(self.stamp(events)) if events else []

    def recover(self, journal: list[dict], table_seq: int) -> int:
        """Start-up repair from the craps table file: its journal events that never
        reached the ledger (a crash between the two writes) are appended now. Then
        seq continues from whichever file is further ahead. Returns events replayed."""
        missing = [e for e in journal if e["seq"] > self.last_seq]
        if missing:
            if missing[0]["seq"] != self.last_seq + 1:
                log.error("[games] ledger gap: seq %s..%s are missing (neither the ledger nor the table "
                          "journal has them)", self.last_seq + 1, missing[0]["seq"] - 1)
            log.warning("[games] replaying %d ledger event(s) (seq %s..%s) from the craps table journal",
                        len(missing), missing[0]["seq"], missing[-1]["seq"])
            self.last_seq = missing[-1]["seq"]
            self.write(missing)
        if table_seq and self.last_seq > table_seq:
            # the table is always saved BEFORE its events are appended, so this only happens when a
            # table save failed (disk full / file locked) and Hexcast stopped before the next one
            log.error("[games] games_craps_table.json is older than the ledger (table at seq %s, ledger at %s): "
                      "the bets moved by seq %s..%s may be missing from - or still on - the table. Check them "
                      "against the ledger before the next roll.", table_seq, self.last_seq, table_seq + 1,
                      self.last_seq)
        self.last_seq = max(self.last_seq, table_seq)       # seq never goes backwards
        return len(missing)

    def oldest_seq(self) -> int | None:
        return self.events[0]["seq"] if self.events else None

    def query(self, since: int, limit: int) -> tuple[list[dict], bool]:
        """Events with seq > since, oldest first, at most `limit`. truncated = some
        such events are not in this response (the limit cut it, or they are older
        than the in-memory window - read games_ledger.jsonl for those)."""
        if not self.events:
            return [], self.last_seq > since
        gap = since + 1 < self.events[0]["seq"]
        out = [e for e in self.events if e["seq"] > since]
        return out[:limit], gap or len(out) > limit

    def session(self, user: str | None) -> dict:
        d = c = n = 0
        for e in self.events:
            if e.get("user") == user:
                n += 1
                if e.get("type") == "debit":
                    d += e.get("amount", 0)
                else:
                    c += e.get("amount", 0)
        return {"debits": d, "credits": c, "net": c - d, "events": n}


LEDGER = Ledger(LEDGER_PATH)


# --------------------------------------------------------------------------
# craps: the game
# --------------------------------------------------------------------------

_DICE_STYLES = ("red", "white", "black", "gold")


class Craps(Game):
    key = "craps"
    title = "Craps"
    id_prefix = "c"
    has_bets = False                   # bets live on the table, not on a roll
    duration_key = "roll_seconds"
    launch_clip_key = "roll_clip"

    DEFAULTS: dict[str, Any] = {
        # placement: tray centre in % of the 1920x1080 stage; scale x the 720x405 tray
        "x": 50, "y": 50, "scale": 1.0,
        "theme": "classic",             # classic | neon | midnight | royal
        "dice_style": "red",            # red | white | black | gold
        "roll_seconds": 4,              # throw -> dice at rest
        "result_seconds": 5,            # call + payouts stay up this long
        "cooldown_seconds": 0,          # extra lockout after the result phase
        "hide_when_idle": True,
        "show_when_bets": True,         # stay visible while any bet is on the table
        "show_point": True,
        "show_history": True, "history_count": 10,
        "show_user": True,
        "show_bets": True, "bets_max": 6,
        "show_payouts": True, "payouts_max": 5,
        "sfx": True, "sfx_volume": 0.5,
        "roll_clip": "", "land_clip": "",                        # soundboard clips
        "currency": "hexcoins",
        "min_bet": 1, "max_bet": 100000,                        # max_bet 0 = no max
        "odds_rule": "345",             # 345 | 1 | 2 | 3 | 5 | 10 | 20 | 100
        "field_12_pays": 3,             # 2 | 3
        "auto_roll": False, "bet_window_seconds": 20,
    }
    SCHEMA: dict[str, tuple] = {
        "x": ("num", 0, 100), "y": ("num", 0, 100), "scale": ("num", 0.2, 5),
        "theme": ("enum", _THEMES), "dice_style": ("enum", _DICE_STYLES),
        "roll_seconds": ("num", ROLL_SECONDS_MIN, ROLL_SECONDS_MAX),
        "result_seconds": ("num", 1, 120),
        "cooldown_seconds": ("num", 0, 3600),
        "hide_when_idle": ("bool",), "show_when_bets": ("bool",), "show_point": ("bool",),
        "show_history": ("bool",), "history_count": ("int", 1, 20),
        "show_user": ("bool",),
        "show_bets": ("bool",), "bets_max": ("int", 1, 20),
        "show_payouts": ("bool",), "payouts_max": ("int", 1, 20),
        "sfx": ("bool",), "sfx_volume": ("num", 0, 1),
        "roll_clip": ("str", 200), "land_clip": ("str", 200),
        "currency": ("name", 24),
        "min_bet": ("int", 1, 1e9), "max_bet": ("int", 0, 1e12),
        "odds_rule": ("strenum", ODDS_RULES),
        "field_12_pays": ("intenum", (2, 3)),
        "auto_roll": ("bool",), "bet_window_seconds": ("num", 5, 300),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "dice_style", "show_point", "show_history", "history_count",
                  "show_user", "show_bets", "bets_max", "show_payouts", "payouts_max", "sfx", "sfx_volume")

    def __init__(self, table_path: Path | None = None, ledger: Ledger | None = None) -> None:
        super().__init__()
        self.table_path = Path(table_path) if table_path else CRAPS_TABLE_PATH
        self.ledger = ledger if ledger is not None else LEDGER
        self.table, seq, journal = load_craps_state(self.table_path)
        self.ledger.recover(journal, seq)     # replay a half-written change; seq never goes backwards
        self.auto_at: float | None = None
        self.auto_task: asyncio.Task | None = None
        self._auto_token: object | None = None
        self._last_commit: tuple[str | None, list[dict]] = (None, [])
        # the bot's own "on the table" board (money mode B): memory only, never persisted;
        # replaced (never mutated) so views may share it
        self.display_board: dict | None = None

    # ---- config ----------------------------------------------------------

    def validate_config(self, raw: Any) -> dict:
        out = super().validate_config(raw)
        if out["max_bet"] and out["max_bet"] < out["min_bet"]:        # must be >= min_bet, else reset
            d = self.DEFAULTS["max_bet"]
            out["max_bet"] = d if d >= out["min_bet"] else 0
        return out

    def duration_range(self) -> tuple[float, float]:
        return ROLL_SECONDS_MIN, ROLL_SECONDS_MAX

    # ---- table -----------------------------------------------------------

    def dice_in_air(self) -> bool:
        """A real (non-test) roll is in flight: the table is frozen."""
        run = self.run
        return self.state == "spinning" and run is not None and not run.spin.get("test")

    def bets_closed_response(self) -> JSONResponse | None:
        self._heal()
        if not self.dice_in_air():
            return None
        ms = max(0, int(round((self.run.spin["lands_at"] - time.time()) * 1000)))
        return JSONResponse({"ok": False, "error": "bets_closed", "retry_in_ms": ms}, status_code=409)

    def exposure(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in self.table["bets"]:
            out[b["user"]] = out.get(b["user"], 0) + b["amount"] + b["odds"]
        return out

    def table_view(self) -> dict:
        """The TABLE object (craps spec §3), built fresh (auto_roll_in_ms is 'now')."""
        t, cfg = self.table, self.cfg
        exposure = self.exposure()
        return {"phase": t["phase"], "point": t["point"], "shooter": t["shooter"], "hand_rolls": t["hand_rolls"],
                "bets": [bet_view(b, t["phase"]) for b in t["bets"]],
                "exposure": exposure, "total_on_table": sum(exposure.values()),
                "bets_open": not self.dice_in_air(), "auto_roll_in_ms": self.auto_roll_in_ms(),
                "last_seq": self.ledger.last_seq, "currency": cfg["currency"],
                "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"], "odds_rule": cfg["odds_rule"],
                "field_12_pays": cfg["field_12_pays"], "display_board": self.display_board}

    def set_board(self, params: dict) -> tuple[int, dict]:
        """/craps/board: the bot's own "on the table" board, shown INSTEAD of the one
        computed from Hexcast's table bets. {"bets": []} = an empty board; clear:true
        = back to the computed board. Allowed any time (the overlay holds table
        updates while the dice fly). Returns (status, body)."""
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
        t = self.table
        data = {"version": 1, "phase": t["phase"], "point": t["point"], "shooter": t["shooter"],
                "hand_rolls": t["hand_rolls"], "bets": t["bets"], "last_seq": self.ledger.last_seq,
                "saved_at": round(time.time(), 3), "journal": journal or []}
        return _atomic_write_text(self.table_path, json.dumps(data, indent=1))

    def _persist(self, events: list[dict]) -> list[dict]:
        """Persist a table change + its ledger events, crash-safe: stamp the seqs ->
        save the table WITH the events as its journal -> append them to the ledger.
        A crash between the two writes is repaired at start-up (Ledger.recover
        replays the journal), so the table and the ledger never disagree - a settled
        bet can't be paid twice, a debit can't vanish. Returns the logged events."""
        stamped = self.ledger.stamp(events)
        self.save_table(journal=self.ledger.unwritten + stamped)
        return self.ledger.write(stamped)

    def _find(self, user: str | None, btype: str, number: int | None) -> dict | None:
        for b in self.table["bets"]:
            if b["user"] == user and b["type"] == btype and b["number"] == number:
                return b
        return None

    def _bet_by_id(self, bet_id: str) -> dict | None:
        for b in self.table["bets"]:
            if b["id"] == bet_id:
                return b
        return None

    def _new_bet_id(self) -> str:
        ids = {b["id"] for b in self.table["bets"]}
        while True:
            bid = f"b-{secrets.token_hex(4)}"
            if bid not in ids:
                return bid

    def _removable(self, b: dict) -> bool:
        return bet_view(b, self.table["phase"])["removable"]

    def _contract_msg(self, b: dict) -> str:
        lab = bet_label(b["type"], b["number"])
        if b["type"] == "pass":
            msg = f"{lab} is a contract bet once the point is set - it can't come down"
            return msg + (" (its odds can: bet=odds)" if b["odds"] else "")
        msg = f"{lab} is a contract bet once it has travelled - it can't come down"
        return msg + (f" (its odds can: bet=odds:{b['number']})" if b["odds"] else "")

    # ---- bets ------------------------------------------------------------

    def plan_bet(self, entry: Any, default_user: Any = None, *, dry: bool = False) -> dict:
        """Check one bet {user, bet, amount, target?} against the current table.
        ok -> the plan to apply (action new | add | odds); else error. `dry`
        (/validate) tolerates a missing user / amount."""
        cfg, t = self.cfg, self.table
        cur = cfg["currency"]
        if isinstance(entry, str):
            entry = {"bet": entry}
        plan: dict[str, Any] = {"ok": False, "user": None, "bet": "", "amount_in": None, "amount": None,
                                "type": "invalid", "label": "?", "number": None, "odds_text": None}
        if not isinstance(entry, dict):
            plan["error"] = "bet entry must be an object"
            return plan
        user = _clean_user(entry.get("user")) or _clean_user(default_user)
        raw = entry.get("bet")
        text = "" if raw is None or isinstance(raw, (dict, list)) else str(raw).strip()[:BET_TEXT_MAX_CHARS]
        target = entry.get("target")
        target = (str(target).strip()[:64] or None) if target is not None and not isinstance(
            target, (dict, list, bool)) else None
        plan.update(user=user, bet=text, amount_in=entry.get("amount"), label=text or "?")

        def fail(msg: str, **kw) -> dict:
            plan.update(kw)
            plan["error"] = msg
            return plan

        # ---- what is it -------------------------------------------------
        if target:
            base = self._bet_by_id(target)
            if base is None:
                return fail(f"no bet with id {target} on the table", target=target)
            if base["type"] not in LINE_TYPES:
                return fail(f"odds go behind pass, don't pass, come or don't come bets - {target} is "
                            f"{bet_label(base['type'], base['number'])}", target=target)
            if user and user != base["user"]:
                return fail(f"bet {target} belongs to @{base['user']}", target=target)
            user = plan["user"] = base["user"]
            spec: dict[str, Any] = {"valid": True, "kind": "odds", "on": base["type"], "number": base["number"],
                                    "base": base}
        else:
            spec = parse_craps_bet(text)
            if not spec["valid"]:
                return fail(spec["error"])

        amount_raw = entry.get("amount")
        skip_amount = dry and (amount_raw is None or (isinstance(amount_raw, str) and not amount_raw.strip()))

        def coins() -> tuple[int | None, str | None]:
            if skip_amount:
                return None, None
            amt, err = _parse_coins(amount_raw)
            if err:
                return None, err
            if amt < cfg["min_bet"]:
                return None, f"minimum bet is {cfg['min_bet']} {cur}"
            return amt, None

        hint = None
        if spec["kind"] == "flat":
            btype, n = spec["type"], spec["number"]
            label = bet_label(btype, n)
            plan.update(type=btype, number=n, label=label,
                        odds_text=craps_odds_text(btype, n, cfg["field_12_pays"]))
            if btype in ("come", "dont_come") and n is not None:
                return fail(f"come bets start in the come box: bet '{'come' if btype == 'come' else 'dontcome'}'"
                            f" and it travels to the next box number rolled")
            if btype in ("pass", "dont_pass") and t["phase"] != "come_out":
                return fail(f"{label} bets are made on the come-out roll - the point is {t['point']}; "
                            f"bet come / dontcome, or odds behind your line bet")
            if btype in ("come", "dont_come") and t["phase"] != "point":
                return fail(f"{label} bets are made once a point is set - on the come-out bet pass / dontpass")
            if not user and not dry:
                return fail("user required")
            existing = self._find(user, btype, n) if user else None
            amt, err = coins()
            if err:
                return fail(err, hint=_flat_hint(btype, n, 1) if btype in ("horn", "ce") else None)
            if amt is not None:
                if btype == "horn" and amt % 4:
                    return fail("a horn bet is 4 units (one each on 2, 3, 11, 12): the amount must be a "
                                "multiple of 4", hint=_flat_hint("horn", None, amt))
                if btype == "ce" and amt % 2:
                    return fail("C&E is 2 units (any craps + eleven): the amount must be even",
                                hint=_flat_hint("ce", None, amt))
                total = (existing["amount"] if existing else 0) + amt
                if cfg["max_bet"] and total > cfg["max_bet"]:
                    extra = f" ({existing['amount']} already on {label})" if existing else ""
                    return fail(f"max bet is {cfg['max_bet']} {cur}{extra}")
                hint = _flat_hint(btype, n, total)
            plan.update(action="add" if existing else "new", _bet=existing)
        else:
            on = spec["on"]
            lay = on in DONT_TYPES
            plan.update(type="lay_odds" if lay else "odds", on=on,
                        label=f"{bet_label(on, spec['number'])} {'lay odds' if lay else 'odds'}",
                        odds_text=craps_odds_text("lay_odds" if lay else "odds", spec["number"]))
            base = spec.get("base")
            if base is None:
                if not user:
                    return fail("odds need a user - whose bet do they go behind?")
                n = spec["number"]
                if on in ("pass", "dont_pass"):
                    base = self._find(user, on, None)
                    if base is None:
                        return fail(f"@{user} has no {bet_label(on)} bet to put odds behind")
                else:
                    base = self._find(user, on, n)
                    if base is None:
                        if self._find(user, on, None):
                            return fail(f"@{user}'s {bet_label(on)} bet hasn't travelled to a number yet")
                        return fail(f"@{user} has no {bet_label(on, n)} bet to put odds behind")
            num = t["point"] if base["type"] in ("pass", "dont_pass") else base["number"]
            plan.update(label=odds_label(base), number=num, target=base["id"], on=base["type"],
                        type="lay_odds" if base["type"] in DONT_TYPES else "odds")
            lay = base["type"] in DONT_TYPES
            if base["type"] in ("pass", "dont_pass") and (t["phase"] != "point" or num is None):
                return fail("odds go behind the line once a point is set")
            if num is None:
                return fail(f"that {bet_label(base['type'])} bet hasn't travelled to a number yet")
            plan["odds_text"] = craps_odds_text("lay_odds" if lay else "odds", num)
            limit = odds_limit(base["amount"], num, lay, cfg["odds_rule"])
            room = max(0, limit - base["odds"])
            plan.update(max_odds=room, odds_limit=limit)
            amt, err = coins()
            if err:
                return fail(err)
            if amt is not None:
                if amt > room:
                    rule = "3-4-5x" if cfg["odds_rule"] == "345" else f"{cfg['odds_rule']}x"
                    return fail(f"odds limit ({rule}): at most {room} more - {limit} max behind "
                                f"{base['amount']} on {num}, {base['odds']} already down")
                hint = _odds_hint(num, lay, base["odds"] + amt)
            plan.update(action="odds", _bet=base)
        plan.update(ok=True, amount=amt, hint=hint)
        plan.pop("error", None)
        return plan

    def _apply_plan(self, plan: dict) -> tuple[dict, dict]:
        """Mutate the table for an ok plan. Returns (bet, debit event)."""
        amt = plan["amount"]
        if plan["action"] == "new":
            b = {"id": self._new_bet_id(), "user": plan["user"], "type": plan["type"], "number": plan["number"],
                 "amount": amt, "odds": 0, "placed_at": round(time.time(), 3)}
            self.table["bets"].append(b)
            return b, _ev("debit", b["user"], amt, "bet", b["id"], bet_label(b["type"], b["number"]), None)
        b = plan["_bet"]
        if plan["action"] == "add":
            b["amount"] += amt
            return b, _ev("debit", b["user"], amt, "add", b["id"], bet_label(b["type"], b["number"]), None)
        b["odds"] += amt
        return b, _ev("debit", b["user"], amt, "odds", b["id"], odds_label(b), None)

    def place_bets(self, entries: list, default_user: Any = None) -> dict:
        """Place bets (in order: later entries see earlier ones). Every accepted bet
        is one debit ledger event. Callers must check bets_closed first."""
        accepted, rejected, pending = [], [], []
        for entry in entries:
            plan = self.plan_bet(entry, default_user)
            if not plan["ok"]:
                rejected.append({"user": plan["user"], "bet": plan["bet"], "amount": _echo(plan["amount_in"]),
                                 "error": plan["error"]})
                continue
            bet, ev = self._apply_plan(plan)
            view = bet_view(bet, self.table["phase"])
            if plan.get("hint"):
                view["hint"] = plan["hint"]
            accepted.append(view)
            pending.append(ev)
        debits = []
        if pending:
            logged = self._persist(pending)
            debits = [{"user": e["user"], "amount": e["amount"], "bet_id": e["bet_id"], "seq": e["seq"],
                       "reason": e["reason"]} for e in logged]
        return {"accepted": accepted, "rejected": rejected, "debits": debits}

    def remove(self, params: dict) -> tuple[int, dict]:
        """Take bets down: {bet_id} | {user, bet} | {user, all:true}. Contract bets -> 400
        (their odds can still come down). Returns (status, body)."""
        user = _clean_user(params.get("user"))
        bid = params.get("bet_id", params.get("id"))
        bid = str(bid).strip() if bid is not None and not isinstance(bid, (dict, list, bool)) else ""
        text = params.get("bet")
        text = "" if text is None or isinstance(text, (dict, list)) else str(text).strip()[:BET_TEXT_MAX_CHARS]
        take: list[tuple[dict, bool]] = []            # (bet, odds only)
        if bid:
            b = self._bet_by_id(bid)
            if b is None:
                return 400, {"error": f"no bet with id {bid} on the table"}
            if user and user != b["user"]:
                return 400, {"error": f"bet {bid} belongs to @{b['user']}"}
            if not self._removable(b):
                return 400, {"error": self._contract_msg(b)}
            take.append((b, False))
        elif user and _flag(params.get("all")):
            mine = [b for b in self.table["bets"] if b["user"] == user]
            if not mine:
                return 400, {"error": f"@{user} has no bets on the table"}
            for b in mine:
                if self._removable(b):
                    take.append((b, False))
                elif b["odds"]:
                    take.append((b, True))
            if not take:
                return 400, {"error": f"@{user}'s bets are all contract bets (pass after the point / "
                                      f"travelled come) - they can't come down"}
        elif user and text:
            spec = parse_craps_bet(text)
            if not spec["valid"]:
                return 400, {"error": spec["error"]}
            if spec["kind"] == "odds":
                on = spec["on"]
                base = self._find(user, on, None if on in ("pass", "dont_pass") else spec["number"])
                if base is None or not base["odds"]:
                    what = bet_label(on, spec["number"] if on in ("come", "dont_come") else None)
                    return 400, {"error": f"@{user} has no odds behind {what} to take down"}
                take.append((base, True))
            else:
                b = self._find(user, spec["type"], spec["number"])
                if b is None:
                    return 400, {"error": f"@{user} has no {bet_label(spec['type'], spec['number'])} bet"}
                if not self._removable(b):
                    return 400, {"error": self._contract_msg(b)}
                take.append((b, False))
        else:
            return 400, {"error": "remove needs bet_id, or user + bet, or user + all=true"}

        phase = self.table["phase"]
        events, removed, gone = [], [], set()
        for b, odds_only in take:
            view = bet_view(b, phase)
            if odds_only:
                amt, b["odds"] = b["odds"], 0
                events.append(_ev("credit", b["user"], amt, "remove", b["id"], odds_label(b), None))
            else:
                amt = b["amount"] + b["odds"]
                gone.add(b["id"])
                events.append(_ev("credit", b["user"], amt, "remove", b["id"], bet_label(b["type"], b["number"]),
                                  None))
            view.update(refund=amt, odds_only=odds_only)
            removed.append(view)
        self.table["bets"] = [b for b in self.table["bets"] if b["id"] not in gone]
        logged = self._persist(events)
        if not self.table["bets"]:
            self.cancel_auto()                        # the table emptied
        return 200, {"removed": removed, "credits": _aggregate_credits(logged, "amount"), "ledger": logged}

    def clear_table(self) -> dict:
        """Refund every bet, back to the come-out, no shooter."""
        events = [_ev("credit", b["user"], b["amount"] + b["odds"], "refund", b["id"],
                      bet_label(b["type"], b["number"]), None) for b in self.table["bets"]]
        self.table = _fresh_table()
        logged = self._persist(events)
        self.cancel_auto()
        return {"credits": _aggregate_credits(logged, "amount"), "ledger": logged, "refunded": len(logged)}

    def user_view(self, name: Any) -> dict:
        user = _clean_user(name)
        phase = self.table["phase"]
        bets = [bet_view(b, phase) for b in self.table["bets"] if b["user"] == user]
        return {"user": user, "bets": bets, "exposure": sum(b["amount"] + b["odds"] for b in bets),
                "session": self.ledger.session(user)}

    # ---- rolls -----------------------------------------------------------

    def pick(self, cfg: dict) -> dict:
        # two fair dice, OS entropy - nothing can steer this
        d1, d2 = _RNG.randint(1, 6), _RNG.randint(1, 6)
        return craps_result(d1, d2, self.table["phase"], self.table["point"])

    def spin_outcome(self, params: dict, result: dict, user: str | None, test: bool) -> dict:
        # computed now from the frozen table; committed at landing (on_commit)
        settlements = [] if test else craps_settle(self.table["bets"], result,
                                                   int(self.cfg.get("field_12_pays", 3)))
        return {"settlements": settlements, "credits": _aggregate_credits(settlements),
                "summary": craps_summary(settlements), "shooter": self.table["shooter"] or user}

    def before_spin(self, params: dict) -> Any:
        entries = _bet_entries(params)
        if len(entries) > MAX_BETS:
            return _err(f"too many bets in one request (max {MAX_BETS})")
        self.cancel_auto()                             # a manual roll cancels the countdown
        if not entries:
            return None
        if _flag(params.get("test")):
            # a test roll is animation only: it never touches the table or the ledger
            return {"accepted": [], "debits": [], "rejected": [
                _rejected_entry(e, params.get("user"), "test roll - bets are not placed (use /bet)")
                for e in entries]}
        placed = self.place_bets(entries, default_user=params.get("user"))
        _soon(_flush_ledger())
        return placed

    def start(self, spin: dict) -> _Run:
        self.cancel_auto()
        run = super().start(spin)
        run.extra["table_before"] = self.table_view()   # dice in the air: bets_open false
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
        events = craps_apply(table, spin)
        self.table = table
        logged = self._persist(events)
        self._last_commit = (spin["id"], logged)
        _soon(_flush_ledger())

    def stop(self) -> bool:
        self.cancel_auto()
        return super().stop()

    # ---- STATE / visibility ----------------------------------------------

    def state_extra(self) -> dict:
        return {"table": self.table_view()}

    def extra_visible(self) -> bool:
        board = self.display_board           # the bot's board counts too (mode B)
        return bool(self.cfg.get("show_when_bets", True)
                    and (self.table["bets"] or (board is not None and board["bets"])))

    # ---- auto-roll ---------------------------------------------------------

    def auto_roll_in_ms(self) -> int | None:
        if self.auto_at is None:
            return None
        return max(0, int(round((self.auto_at - time.time()) * 1000)))

    def arm_auto(self) -> bool:
        """Start the bet-window countdown if auto_roll is on, bets are down, the game
        is idle and no countdown is running."""
        if (not self.cfg.get("auto_roll") or not self.table["bets"] or self.state != "idle"
                or self.auto_at is not None):
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self.auto_at = time.time() + float(self.cfg.get("bet_window_seconds", 20))
        token = self._auto_token = object()
        self.auto_task = loop.create_task(self._auto_fire(token, self.auto_at))
        return True

    def cancel_auto(self) -> bool:
        was = self.auto_at is not None
        self.auto_at, self._auto_token = None, None
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
        self.auto_at, self.auto_task, self._auto_token = None, None, None
        try:
            self._heal()
            if self.state == "idle" and self.table["bets"] and self.cfg.get("auto_roll"):
                spin = self.build_spin({"user": self.table["shooter"]})
                self.start(spin)          # exactly like /roll (no await since the idle check)
        except Exception:
            log.exception("[games] craps auto-roll failed")
        try:
            await HUB.broadcast_state(self)
        except Exception:
            pass

    def on_idle(self) -> None:
        self.arm_auto()                   # bets still down after a roll -> next countdown

    def on_config(self) -> None:
        if self.cfg.get("auto_roll"):
            self.arm_auto()
        else:
            self.cancel_auto()

    # ---- reference / validate / stats --------------------------------------

    def validate(self, params: dict) -> dict:
        entry = {"user": params.get("user"), "bet": params.get("bet"), "amount": params.get("amount"),
                 "target": params.get("target")}
        plan = self.plan_bet(entry, dry=True)
        out: dict[str, Any] = {"bet": plan["bet"], "valid": plan["ok"], "type": plan["type"],
                               "label": plan["label"], "number": plan["number"], "odds_text": plan["odds_text"]}
        for k in ("error", "hint", "max_odds", "odds_limit", "on", "target", "action"):
            if plan.get(k) is not None:
                out[k] = plan[k]
        out["user"] = plan["user"]
        out["amount"] = plan["amount"]
        return out

    def bets_payload(self) -> dict:
        cfg = self.cfg
        return {"ok": True, "game": self.key, "currency": cfg["currency"], "min_bet": cfg["min_bet"],
                "max_bet": cfg["max_bet"], "odds_rule": cfg["odds_rule"], "field_12_pays": cfg["field_12_pays"],
                "bets": self.bets_reference(), "rules": self.bets_rules()}

    def bets_reference(self) -> list[dict]:
        f12 = self.cfg.get("field_12_pays", 3)
        rule = self.cfg.get("odds_rule", "345")
        limit = ("3x on 4/10, 4x on 5/9, 5x on 6/8" if rule == "345" else f"{rule}x the flat bet")
        lay_limit = ("6x the flat bet" if rule == "345" else f"up to a win of {rule}x the flat bet")
        return [
            {"type": "pass", "label": "Pass line", "syntax": ["pass", "passline", "line", "pl"], "pays": "1:1",
             "when": "come-out roll only", "one_roll": False,
             "note": "Come-out: 7/11 win, 2/3/12 lose, 4-10 sets the point; then it wins if the point rolls "
                     "before a 7. A contract bet once the point is set (can't come down). House edge 1.41%."},
            {"type": "dont_pass", "label": "Don't pass", "syntax": ["dontpass", "don't pass", "dp"], "pays": "1:1",
             "when": "come-out roll only", "one_roll": False,
             "note": "Come-out: 2/3 win, 12 is a push (bar 12), 7/11 lose; then it wins if a 7 rolls before the "
                     "point. Can be taken down. House edge 1.36%."},
            {"type": "come", "label": "Come", "syntax": ["come"], "pays": "1:1",
             "when": "once a point is set", "one_roll": False,
             "note": "Its next roll is its own come-out: 7/11 win, 2/3/12 lose, 4-10 travels the bet to that "
                     "number; then it wins if its number rolls before a 7 (contract once travelled; stays through "
                     "the next come-out). House edge 1.41%."},
            {"type": "dont_come", "label": "Don't come", "syntax": ["dontcome", "dc"], "pays": "1:1",
             "when": "once a point is set", "one_roll": False,
             "note": "Its next roll: 2/3 win, 12 pushes, 7/11 lose, 4-10 travels; then it wins if a 7 rolls "
                     "before its number. Can be taken down. House edge 1.36%."},
            {"type": "odds", "label": "Odds (pass / come)",
             "syntax": ["odds", "odds:pass", "passodds", "odds:6", "odds:come6", "comeodds:6"],
             "pays": "true odds: 4/10 2:1, 5/9 3:2, 6/8 6:5",
             "when": "behind your pass bet once the point is set, or behind a come bet that travelled",
             "one_roll": False,
             "note": f"No house edge. Limit: {limit}. Come odds are OFF on a come-out roll (returned if the come "
                     f"bet is decided then). Exact pays: 6/8 multiples of 5, 5/9 even amounts. Can come down "
                     f"anytime. Or send target=<bet_id>."},
            {"type": "lay_odds", "label": "Lay odds (don't pass / don't come)",
             "syntax": ["odds:dontpass", "layodds", "dpodds", "odds:dontcome6", "layodds:6", "dcodds:6"],
             "pays": "4/10 1:2, 5/9 2:3, 6/8 5:6",
             "when": "behind your don't pass once the point is set, or behind a don't come that travelled",
             "one_roll": False,
             "note": f"No house edge; always working. Limit: {lay_limit}. Exact pays: 4/10 even amounts, 5/9 "
                     f"multiples of 3, 6/8 multiples of 6."},
            {"type": "place", "label": "Place", "syntax": ["place6", "place:6", "p6", "6", "place 4", "p10"],
             "pays": "4/10 9:5, 5/9 7:5, 6/8 7:6", "when": "anytime (off on the come-out)", "one_roll": False,
             "note": "Wins when its number rolls before a 7 - the winnings are paid and the bet stays up. Bet "
                     "multiples of 5 (4/5/9/10) or 6 (6/8) to be paid exactly. Edge 6/8 1.52%, 5/9 4.0%, "
                     "4/10 6.67%."},
            {"type": "hard", "label": "Hard way", "syntax": ["hard6", "hard:6", "h6", "hardsix", "hard 10"],
             "pays": "hard 4/10 7:1, hard 6/8 9:1", "when": "anytime (off on the come-out)", "one_roll": False,
             "note": "Wins when the number rolls as a pair (the hard way) - paid, the bet stays up; loses to the "
                     "easy way or a 7. Edge 6/8 9.09%, 4/10 11.1%."},
            {"type": "field", "label": "Field", "syntax": ["field"],
             "pays": f"3/4/9/10/11 1:1, 2 pays 2:1, 12 pays {f12}:1", "when": "anytime", "one_roll": True,
             "note": f"One roll; 5/6/7/8 lose. Edge {'2.78' if f12 == 3 else '5.56'}%."},
            {"type": "any7", "label": "Any 7", "syntax": ["any7", "anyseven", "seven", "big red", "7"],
             "pays": "4:1", "when": "anytime", "one_roll": True, "note": "One roll: any 7. Edge 16.7%."},
            {"type": "any_craps", "label": "Any craps", "syntax": ["anycraps", "craps", "ac"], "pays": "7:1",
             "when": "anytime", "one_roll": True, "note": "One roll: 2, 3 or 12. Edge 11.1%."},
            {"type": "aces", "label": "Aces", "syntax": ["aces", "snakeeyes", "snake eyes", "2", "two"],
             "pays": "30:1", "when": "anytime", "one_roll": True, "note": "One roll: 2. Edge 13.9%."},
            {"type": "ace_deuce", "label": "Ace-deuce", "syntax": ["acedeuce", "ace-deuce", "3", "three"],
             "pays": "15:1", "when": "anytime", "one_roll": True, "note": "One roll: 3. Edge 11.1%."},
            {"type": "yo", "label": "Yo 11", "syntax": ["yo", "eleven", "11"], "pays": "15:1",
             "when": "anytime", "one_roll": True, "note": "One roll: 11. Edge 11.1%."},
            {"type": "boxcars", "label": "Boxcars", "syntax": ["boxcars", "midnight", "12", "twelve"],
             "pays": "30:1", "when": "anytime", "one_roll": True, "note": "One roll: 12. Edge 13.9%."},
            {"type": "horn", "label": "Horn", "syntax": ["horn"], "pays": "2/12 27:4, 3/11 3:1 (net, whole bet)",
             "when": "anytime", "one_roll": True,
             "note": "One roll, 4 units (one each on 2, 3, 11, 12): the amount must be a multiple of 4. "
                     "Edge 12.5%."},
            {"type": "ce", "label": "C&E", "syntax": ["ce", "c&e", "crapseleven"],
             "pays": "craps 3:1, 11 7:1 (net, whole bet)", "when": "anytime", "one_roll": True,
             "note": "One roll, 2 units (any craps + eleven): the amount must be even. Edge 11.1%."},
        ]

    def bets_rules(self) -> list[str]:
        cfg = self.cfg
        cur = cfg["currency"]
        mx = f"{cfg['max_bet']}" if cfg["max_bet"] else "no max"
        return [
            f"A bet is {{user, bet, amount}} in whole {cur}: amount >= min_bet ({cfg['min_bet']}); a bet's flat "
            f"amount <= max_bet ({mx}).",
            "Bets stay on the table across rolls until they win, lose or are taken down. POST /bet debits "
            "(debits[] + ledger); a roll credits when the dice LAND (credits[] + ledger).",
            "Winnings are rounded DOWN to whole coins (the house pays no change) - /validate hints at amounts "
            "that pay exactly.",
            "Place and hard-way bets are OFF on the come-out (no action). Come odds are OFF on the come-out "
            "(returned). Lay odds always work.",
            "Betting the same bet again adds to it; pass / come can't be increased once they are contract bets. "
            "Odds: odds / odds:N / layodds / layodds:N, or target=<bet_id>.",
            "One-roll bets (field, any 7, any craps, aces, ace-deuce, yo, boxcars, horn, C&E) settle on the very "
            "next roll and come down.",
            "No bets while the dice are in the air: 409 bets_closed with retry_in_ms.",
            "Case-insensitive; spaces, underscores, hyphens and apostrophes are ignored.",
        ]

    def reset_stats(self) -> None:
        self._spins = 0
        self._counts = {n: 0 for n in range(2, 13)}
        self._tally = {"points_set": 0, "points_made": 0, "seven_outs": 0, "naturals": 0, "craps": 0,
                       "hard_ways": 0}
        self._hand = 0
        self._longest = 0

    def record(self, result: dict) -> None:
        self._spins += 1
        self._counts[result["total"]] += 1
        key = {"point_set": "points_set", "point_made": "points_made", "seven_out": "seven_outs",
               "natural": "naturals", "craps": "craps"}.get(result["event"])
        if key:
            self._tally[key] += 1
        if result.get("hard"):
            self._tally["hard_ways"] += 1
        self._hand += 1
        self._longest = max(self._longest, self._hand)
        if result["event"] == "seven_out":
            self._hand = 0

    def stats(self) -> dict:
        return {"rolls": self._spins, "counts": {str(n): c for n, c in self._counts.items()}, **self._tally,
                "hands": self._tally["seven_outs"], "longest_hand": self._longest, "current_hand": self._hand}


def _rejected_entry(entry: Any, default_user: Any, error: str) -> dict:
    """A `rejected[]` item: the request's user / bet / amount echoed back (cleaned) + why."""
    if isinstance(entry, str):
        entry = {"bet": entry}
    if not isinstance(entry, dict):
        return {"user": _clean_user(default_user), "bet": "", "amount": None, "error": error}
    raw = entry.get("bet")
    text = "" if raw is None or isinstance(raw, (dict, list)) else str(raw).strip()[:BET_TEXT_MAX_CHARS]
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
# registry + config
# --------------------------------------------------------------------------

ROULETTE = Roulette()
CRAPS = Craps()          # restores the table from games_craps_table.json
GAMES: dict[str, Game] = {}


def register_game(game: Game) -> Game:
    """Add a Game instance to the registry. A game registered after the config was
    loaded gets its section from config/games.json (or its defaults) right away."""
    GAMES[game.key] = game
    live = globals().get("CONFIG")
    if isinstance(live, dict) and game.key not in live:
        live[game.key] = game.validate_config(_read_config_file().get(game.key))
    return game


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


register_game(ROULETTE)
register_game(CRAPS)


def save_config(cfg: dict) -> dict:
    clean = validate_config(cfg)
    CONFIG_PATH.write_text(json.dumps(clean, indent=2), encoding="utf-8")
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
        """Panel sockets only (craps "ledger" pushes - overlays never get money data)."""
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
    """Push ledger events not yet sent to the panel sockets, as one message."""
    if not LEDGER.pending:
        return
    events, LEDGER.pending = LEDGER.pending, []
    await HUB.broadcast_panel({"type": "ledger", "game": "craps", "events": events})


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
    return HTMLResponse(_read_static("games_panel.html"), headers=_NOCACHE)


@router.get("/overlay", response_class=HTMLResponse)
async def overlay():
    return HTMLResponse(_read_static("games_overlay.html"), headers=_NOCACHE)


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
                          '409 {"error":"stale","spin_id"} when spin_id is not the current / last spin',
        "announce_clear": "GET|POST /games/api/{game}/announce/clear",
        "busy":           '409 {"ok":false,"error":"busy","retry_in_ms":n,"state":"spinning|result|cooldown"}',
        "websockets":     "WS /games/ws/overlay, WS /games/ws/panel",
        "appearance_keys": {key: list(g.APPEARANCE) for key, g in GAMES.items()},
        "examples": [
            "curl http://host:4747/games/api/roulette/spin",
            "curl 'http://host:4747/games/api/roulette/spin?user=bob&bet=red&amount=100'",
            "curl 'http://host:4747/games/api/roulette/spin?user=bob&bet=split:17/20&amount=10&wait=true'",
            "curl -X POST http://host:4747/games/api/roulette/spin -H 'Content-Type: application/json' "
            "-d '{\"user\":\"bob\",\"bets\":[{\"user\":\"bob\",\"bet\":\"17\",\"amount\":10},"
            "{\"user\":\"amy\",\"bet\":\"dozen2\",\"amount\":50}]}'",
            "curl 'http://host:4747/games/api/roulette/validate?bet=corner:17'",
            "curl http://host:4747/games/api/stop",
        ],
        "craps": {
            "roll":       "GET|POST /games/api/craps/roll   (alias /spin, /play)  user (shooter if none), "
                          "duration (2.5-10 s), wait, test, bets:[...] placed first, overrides | x=&y=&scale=",
            "roll_reply": "SPIN + settlements, credits [{user,amount}], summary, shooter, table "
                          "(after landing when wait=true, else the pre-roll table), placed (if bets were sent), "
                          "ledger (wait=true)",
            "bet":        "GET|POST /games/api/craps/bet   {user, bet, amount, target?} | {bets:[...]} (<=200) -> "
                          "{accepted, rejected, debits:[{user,amount,bet_id,seq}], table}; 400 if all rejected, "
                          "409 bets_closed while the dice are in the air",
            "remove":     "GET|POST /games/api/craps/remove   {bet_id} | {user, bet} | {user, all:true} -> "
                          "{removed, credits, table}; contract bets 400",
            "table":      "GET /games/api/craps/table",
            "user":       "GET /games/api/craps/user/{name}   bets, exposure, session {debits, credits, net}",
            "ledger":     "GET /games/api/craps/ledger?since=0&limit=500   {events, last_seq, truncated} "
                          "(limit <= 5000)",
            "clear":      "GET|POST /games/api/craps/clear   refund every bet, reset to the come-out",
            "validate":   "GET /games/api/craps/validate?bet=&user=&amount=&target=   dry run: valid, type, label, "
                          "odds_text, error, hint, max_odds",
            "bets":       "GET /games/api/craps/bets   bet reference + rules",
            "board":      "GET|POST /games/api/craps/board   the bot's own board {title, bets:[{user, text, amount}] "
                          "(<=100)} shown instead of the computed one ({bets:[]} = empty board, clear:true = computed "
                          "again) -> {table} (table.display_board); allowed while the dice fly",
            "board_clear": "GET|POST /games/api/craps/board/clear",
            "ledger_ws":  'panel sockets also get {"type":"ledger","game":"craps","events":[...]}',
            "examples": [
                "curl 'http://host:4747/games/api/craps/bet?user=alice&bet=pass&amount=100'",
                "curl -X POST http://host:4747/games/api/craps/bet -H 'Content-Type: application/json' "
                "-d '{\"bets\":[{\"user\":\"alice\",\"bet\":\"odds\",\"amount\":200},"
                "{\"user\":\"bob\",\"bet\":\"place6\",\"amount\":60}]}'",
                "curl 'http://host:4747/games/api/craps/roll?user=alice&wait=true'",
                "curl 'http://host:4747/games/api/craps/validate?bet=odds&user=alice&amount=250'",
                "curl 'http://host:4747/games/api/craps/ledger?since=0'",
                "curl 'http://host:4747/games/api/craps/remove?user=bob&bet=place6'",
            ],
        },
    }


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
        g.on_config()                   # craps: auto_roll switched on/off
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


# ---- craps-only routes (registered before the generic /api/{game}/... ones;
#      /spin, /play, /validate, /bets, /last, /history, /show, /hide, /stop for
#      craps are the generic routes, dispatched through the Game hooks) ----------

async def _table_changed(g: Game) -> None:
    await _flush_ledger()
    await HUB.broadcast_state(g)


@router.api_route("/api/craps/roll", methods=["GET", "POST"])
async def api_craps_roll(request: Request):
    return await _spin_request(CRAPS, request)


@router.api_route("/api/craps/bet", methods=["GET", "POST"])
async def api_craps_bet(request: Request):
    params, err = await _params(request)
    if err is not None:
        return err
    closed = CRAPS.bets_closed_response()
    if closed is not None:
        return closed
    entries = _bet_entries(params)
    if len(entries) > MAX_BETS:
        return _err(f"too many bets in one request (max {MAX_BETS})")
    res = CRAPS.place_bets(entries, default_user=params.get("user"))
    if res["accepted"]:
        CRAPS.arm_auto()                # first bet while idle starts the auto-roll countdown
        await _table_changed(CRAPS)
    ok = bool(res["accepted"]) or not entries
    body: dict[str, Any] = {"ok": ok, **res, "table": CRAPS.table_view()}
    if not ok:
        body["error"] = res["rejected"][0]["error"] if len(res["rejected"]) == 1 else "every bet was rejected"
    return JSONResponse(body, status_code=200 if ok else 400)


@router.api_route("/api/craps/remove", methods=["GET", "POST"])
async def api_craps_remove(request: Request):
    params, err = await _params(request)
    if err is not None:
        return err
    closed = CRAPS.bets_closed_response()
    if closed is not None:
        return closed
    status, body = CRAPS.remove(params)
    if status != 200:
        return _err(body["error"], status)
    await _table_changed(CRAPS)
    return {"ok": True, **body, "table": CRAPS.table_view()}


@router.get("/api/craps/table")
async def api_craps_table():
    CRAPS._heal()
    return {"ok": True, "table": CRAPS.table_view()}


@router.get("/api/craps/user/{name}")
async def api_craps_user(name: str):
    return {"ok": True, **CRAPS.user_view(name)}


@router.get("/api/craps/ledger")
async def api_craps_ledger(since: str | None = None, limit: str | None = None):
    s = _as_float(since)
    s = int(s) if s is not None and s > 0 else 0
    n = _as_float(limit)
    n = 500 if n is None else int(min(LEDGER_LIMIT_MAX, max(1, n)))
    events, truncated = LEDGER.query(s, n)
    return {"ok": True, "events": events, "last_seq": LEDGER.last_seq, "truncated": truncated,
            "oldest_seq": LEDGER.oldest_seq()}


@router.api_route("/api/craps/clear", methods=["GET", "POST"])
async def api_craps_clear():
    closed = CRAPS.bets_closed_response()
    if closed is not None:
        return closed
    res = CRAPS.clear_table()
    await _table_changed(CRAPS)
    return {"ok": True, **res, "table": CRAPS.table_view()}


# /board/clear before /board (display only: never touches bets, ledger, history)
@router.api_route("/api/craps/board/clear", methods=["GET", "POST"])
async def api_craps_board_clear():
    cleared = CRAPS.clear_board()
    await HUB.broadcast_state(CRAPS)
    return {"ok": True, "cleared": cleared, "table": CRAPS.table_view()}


@router.api_route("/api/craps/board", methods=["GET", "POST"])
async def api_craps_board(request: Request):
    params, err = await _params(request)
    if err is not None:
        return err
    status, body = CRAPS.set_board(params)
    if status != 200:
        return _err(body["error"], status)
    await HUB.broadcast_state(CRAPS)
    return {"ok": True, **body, "table": CRAPS.table_view()}


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


# /announce/clear before /announce (display only: never touches bets, ledger, history)
@router.api_route("/api/{game}/announce/clear", methods=["GET", "POST"])
async def api_announce_clear(game: str):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    cleared = g.clear_announce()
    await HUB.broadcast_state(g)
    return {"ok": True, "cleared": cleared, "announce": None, "state": g.state_view()}


@router.api_route("/api/{game}/announce", methods=["GET", "POST"])
async def api_announce(game: str, request: Request):
    g = get_game(game)
    if g is None:
        return _unknown_game()
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
