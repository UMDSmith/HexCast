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
    ws://localhost:4747/games/ws/overlay             -> overlay feed
    ws://localhost:4747/games/ws/panel               -> panel feed

A small framework for programmatic, bot-driven stream games. Every game is a
subclass of `Game` registered in `GAMES`; the generic /games/api/{game}/...
routes, the spin lifecycle (spinning -> result -> cooldown -> idle), history,
visibility and the websocket feed are shared. Roulette is the first game
(an American double-zero wheel, with the full standard bet table resolved
server-side so a chat bot can run a points casino).

Fairness: the SERVER picks the outcome with secrets.SystemRandom(), uniformly
over the wheel's pockets. The overlay only animates the server's result (the
`seed` it gets is for animation variety, not the outcome), and nothing - API,
panel or overlay - can force a result.

The overlay anchors to `elapsed_ms` (never absolute epoch), so clock skew
between the server and the OBS machine never matters.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
import time
import urllib.parse
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


class Game:
    key: str = "game"                 # URL segment + config section
    title: str = "Game"
    id_prefix: str = "g"              # spin ids look like "<prefix>-1a2b3c4d"
    DEFAULTS: dict[str, Any] = {}
    SCHEMA: dict[str, tuple] = {}
    APPEARANCE: tuple[str, ...] = ()  # the only keys a spin's `overrides` may carry
    has_bets: bool = False

    def __init__(self) -> None:
        self.state = "idle"             # idle | spinning | result | cooldown
        self.run: _Run | None = None
        self.shown = False              # /show: visible until hidden / next result ends
        self.hidden = False             # /hide or stop: forced hidden until /show / next spin
        self.history: list[dict] = []   # committed SPINs, newest first
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

    def reset_stats(self) -> None:
        self._spins = 0

    def record(self, result: dict) -> None:
        self._spins += 1

    def stats(self) -> dict:
        return {"spins": self._spins}

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
        return self.state == "result" or self.shown or not self.cfg.get("hide_when_idle", True)

    def last(self) -> dict | None:
        return self.history[0] if self.history else None

    def state_view(self) -> dict:
        """The STATE object (spec §5): spin carries elapsed_ms, history only landed spins."""
        self._heal()
        now = time.time()
        spin = None
        if self.state in ("spinning", "result") and self.run is not None:
            spin = dict(self.run.spin)
            spin["elapsed_ms"] = max(0, int(round((now - spin["started_at"]) * 1000)))
        return {
            "state": self.state,
            "visible": self.visible(),
            "spin": spin,
            "last": self.last(),
            "history": [h["result"] for h in self.history[:STATE_HISTORY]],
            "busy_ms": self.busy_ms(now),
        }

    def _commit(self, run: _Run) -> None:
        if run.committed:
            return
        run.committed = True
        if run.spin.get("test"):
            return
        self.history.insert(0, run.spin)
        del self.history[HISTORY_MAX:]
        self.record(run.spin["result"])

    def clear_history(self) -> None:
        self.history = []
        self.reset_stats()

    def build_spin(self, params: dict) -> dict:
        """Validate a spin request and build the SPIN object (no state change)."""
        cfg = self.cfg
        user = _clean_user(params.get("user"))
        dur = _as_float(params.get("duration"))
        if dur is None:
            dur = float(cfg.get("spin_seconds", 9))
        dur = min(SPIN_SECONDS_MAX, max(SPIN_SECONDS_MIN, dur))
        test = _flag(params.get("test"))

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

        # overrides: top-level appearance shorthand (x=&y=&scale=...), then `overrides`
        ov_in = {k: params[k] for k in self.APPEARANCE if k in params}
        ov_raw = params.get("overrides")
        if isinstance(ov_raw, str):
            try:
                ov_raw = json.loads(ov_raw)
            except ValueError:
                ov_raw = None
        if isinstance(ov_raw, dict):
            ov_in.update(ov_raw)
        overrides = self.validate_overrides(ov_in)

        result = self.pick(cfg)
        bets = [self.resolve_bet(b, result) for b in bets_in] if self.has_bets else []
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
            "bets": bets,
            "summary": bet_summary(bets),
            "overrides": overrides,
        })
        return spin

    def start(self, spin: dict) -> _Run:
        """Enter the spinning state and arm the timeline. Synchronous on purpose:
        the busy check and this must not be separated by an await."""
        cfg = self.cfg
        result_end = spin["lands_at"] + spin["result_ms"] / 1000.0
        idle_at = result_end + float(cfg.get("cooldown_seconds", 0) or 0)
        run = _Run(spin, result_end, idle_at)
        self.run = run
        self.state = "spinning"
        self.hidden = False
        run.task = asyncio.create_task(self._timeline(run))
        if not spin["test"] and cfg.get("spin_clip"):
            _background(_fire_clip(cfg["spin_clip"], f"{self.key} launch"))
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
        await HUB.broadcast_state(self)

    async def _on_idle(self, run: _Run) -> None:
        if self.run is not run:
            return
        self.state, self.run = "idle", None
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
# registry + config
# --------------------------------------------------------------------------

ROULETTE = Roulette()
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
        "config_set":     'POST /games/api/config   body {"roulette": {...partial}}',
        "spin":           "GET|POST /games/api/{game}/spin   (alias /play)",
        "spin_params":    "user, duration (4-30 s), wait, test, bet + amount | bets:[{user,bet,amount}], "
                          "overrides:{appearance} | x=&y=&scale= shorthand  (POST JSON body wins over query)",
        "last":           "GET /games/api/{game}/last",
        "history":        "GET /games/api/{game}/history?limit=20",
        "history_clear":  "GET|POST /games/api/{game}/history/clear",
        "bets":           "GET /games/api/{game}/bets   (bet types, syntax, odds)",
        "validate":       "GET /games/api/{game}/validate?bet=split:17/20",
        "show":           "GET|POST /games/api/{game}/show",
        "hide":           "GET|POST /games/api/{game}/hide   (409 while spinning)",
        "stop":           "GET|POST /games/api/stop   (or /games/api/{game}/stop)",
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


@router.api_route("/api/{game}/spin", methods=["GET", "POST"])
@router.api_route("/api/{game}/play", methods=["GET", "POST"])
async def api_spin(game: str, request: Request):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    params, err = await _params(request)
    if err is not None:
        return err
    busy = g.busy_response()
    if busy is not None:
        return busy
    spin = g.build_spin(params)
    run = g.start(spin)                 # no await between the busy check and here
    await HUB.broadcast_state(g)
    resp: dict[str, Any] = {"ok": True, **spin}
    if _flag(params.get("wait")):
        await run.event.wait()
        resp["landed"] = run.landed
        if run.stopped:
            resp["stopped"] = True
    return resp


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
    out: dict[str, Any] = {"ok": True, "game": g.key, **g.spin_fields()}
    out.update({
        "bets": g.bets_reference(),
        "rules": [
            "A bet is {user, bet, amount}; odds are net 'X to 1'.",
            "payout = win ? amount*odds : -amount; returned = win ? amount*(odds+1) : 0.",
            "Missing amount counts as 0. Invalid bets never block a spin: valid:false, "
            "payout 0, returned = amount (refund).",
            "Case-insensitive, spaces ignored. Bare number lists use / or , separators, any order.",
            "Even-money bets lose on 0/00.",
        ],
    })
    return out


@router.get("/api/{game}/validate")
async def api_validate(game: str, bet: str | None = None):
    g = get_game(game)
    if g is None:
        return _unknown_game()
    return {"ok": True, "bet": (bet or "")[:BET_TEXT_MAX_CHARS], **g.parse_bet(bet)}


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
