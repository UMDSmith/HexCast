"""Roulette - an American double-zero wheel (Games add-on).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from hexcast_plugins.games.core import (
    BET_TEXT_MAX_CHARS,
    COINS_MAX,
    MAX_AMOUNT,
    SPIN_SECONDS_MAX,
    SPIN_SECONDS_MIN,
    TIMER_SECONDS_MAX,
    TIMER_SECONDS_MIN,
    TableGame,
    _RNG,
    _THEMES,
    _aggregate_credits,
    _as_float,
    _bet_id,
    _bet_request,
    _bet_text,
    _board_clear_request,
    _board_request,
    _clean_user,
    _clear_request,
    _echo,
    _ev,
    _flag,
    _ledger_reply,
    _load_table_file,
    _num,
    _parse_coins,
    _remove_request,
    _strict_int,
    log,
)

# paths + limits that belong to this game
from hexcast_plugins.games.core import CONFIG_DIR

ROULETTE_TABLE_PATH = CONFIG_DIR / "games_roulette_table.json"   # roulette bets waiting for the next spin
ROULETTE_TABLE_MAX = 1000       # roulette: bet lines on the table (the next spin)

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
    bet_text = _bet_text(text)
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
# roulette
# --------------------------------------------------------------------------

_ROULETTE_BET_HINT = ("try red, black, odd, even, low, high, dozen2, col3, 17, 0, 00, split:17/20, street:13, "
                      "corner:17, line:13 or basket - GET /games/api/roulette/bets lists every form")


def roulette_bet_view(b: dict) -> dict:
    """Public BET object (BETVIEW) of a roulette table bet."""
    return {"id": b["id"], "user": b["user"], "bet": b["bet"], "label": b["label"], "type": b["type"],
            "numbers": list(b["numbers"]), "odds": b["odds"], "amount": b["amount"],
            "pays": b["amount"] * (b["odds"] + 1), "placed_at": b["placed_at"], "removable": True}


def roulette_settle(bets: list[dict], result: dict) -> list[dict]:
    """Every SETTLEMENT of a spin: one per table bet (all roulette bets are one-spin
    bets), resolved like resolve_bet: payout = win ? amount*odds : -amount (net),
    credit = win ? amount*(odds+1) : 0 (coins to pay back at landing)."""
    out = []
    for b in bets:
        a, o = b["amount"], b["odds"]
        win = result.get("number") in b["numbers"]
        out.append({"bet_id": b["id"], "user": b["user"], "bet": b["bet"], "label": b["label"], "type": b["type"],
                    "amount": a, "odds": o, "win": win, "payout": a * o if win else -a,
                    "credit": a * (o + 1) if win else 0})
    return out


def roulette_table_summary(settlements: list[dict]) -> dict:
    """The house's view of a spin's table bets: wagered - paid = net."""
    wagered = sum(s["amount"] for s in settlements)
    paid = sum(s["credit"] for s in settlements)
    return {"bets": len(settlements), "wagered": wagered, "paid": paid, "net": wagered - paid}


def roulette_apply(table: dict, spin: dict) -> list[dict]:
    """Commit a (non-test) spin to `table` in place: every settled bet comes down,
    winners get one credit (stake + winnings; a losing stake was debited at /bet).
    Returns the credit events (no seq yet)."""
    spin_id = spin.get("id")
    live = {b["id"]: b for b in table["bets"]}
    gone: set[str] = set()
    events: list[dict] = []
    for s in spin.get("settlements") or []:
        b = live.get(s["bet_id"])
        if (b is None or b["user"] != s["user"] or b["amount"] != s["amount"] or b["type"] != s["type"]
                or b["label"] != s["label"]):
            log.warning("[games] roulette spin %s: bet %s changed while the ball flew - not settled", spin_id,
                        s["bet_id"])
            continue
        gone.add(b["id"])
        if s["credit"] > 0:
            events.append(_ev("credit", b["user"], s["credit"], "win", b["id"], b["label"], spin_id))
    table["bets"] = [b for b in table["bets"] if b["id"] not in gone]
    return events


class Roulette(TableGame):
    key = "roulette"
    title = "Roulette"
    id_prefix = "r"
    has_bets = True                    # bets on the spin call (direct) - besides the table
    auto_key = "auto_spin"
    bet_id_prefix = "rb"

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
        "currency": "hexcoins",
        "min_bet": 1, "max_bet": 100000,                        # table bets only; max_bet 0 = no max
        "auto_spin": False, "bet_window_seconds": 20,
        "show_when_bets": True,         # stay visible while bets (or the bot's board) are down
        "show_table": True, "table_max": 6,                     # the "on the table" board before the spin
        "table_position": "right",      # right | left | below | above (of the wheel box)
        "show_rules": True,             # the "how to play" box while bets are open
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
        "currency": ("name", 24),
        "min_bet": ("int", 1, 1e9), "max_bet": ("int", 0, 1e12),
        "auto_spin": ("bool",), "bet_window_seconds": ("num", TIMER_SECONDS_MIN, TIMER_SECONDS_MAX),
        "show_when_bets": ("bool",),
        "show_table": ("bool",), "table_max": ("int", 1, 20),
        "table_position": ("enum", ("right", "left", "below", "above")),
        "show_rules": ("bool",),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "red_color", "black_color", "green_color",
                  "result_position", "result_details", "show_result", "show_history",
                  "history_count", "show_user", "show_bets", "bets_max", "sfx", "sfx_volume",
                  "show_table", "table_max", "table_position", "show_rules")

    # ---- table -----------------------------------------------------------
    # BET (internal, persisted): {id, user, bet (the text as placed), type, numbers,
    # label, odds, amount, placed_at}. One-spin bets: every bet on the table rides on
    # the next real spin and comes down when it lands. Always removable.

    def default_table_path(self) -> Path:
        return ROULETTE_TABLE_PATH

    def load_state(self, path: Path) -> tuple[dict, int, list[dict]]:
        return load_roulette_state(path)

    def apply_spin(self, table: dict, spin: dict) -> list[dict]:
        return roulette_apply(table, spin)

    def table_view(self) -> dict:
        """The roulette TABLE object, built fresh (auto_spin_in_ms is 'now')."""
        cfg = self.cfg
        exposure = self.exposure()
        return {"bets": [roulette_bet_view(b) for b in self.table["bets"]],
                "exposure": exposure, "total_on_table": sum(exposure.values()),
                "bets_open": not self.in_flight(), "auto_spin_in_ms": self.auto_in_ms(),
                "last_seq": self.ledger.last_seq, "currency": cfg["currency"],
                "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"], "display_board": self.display_board}

    def _find(self, user: str | None, btype: str, numbers: list[str]) -> dict | None:
        for b in self.table["bets"]:
            if b["user"] == user and b["type"] == btype and b["numbers"] == numbers:
                return b
        return None

    def plan_bet(self, entry: Any, default_user: Any = None) -> dict:
        """Check one bet {user, bet, amount} against the current table. ok -> the plan
        to apply (action new | add: the same user + the same bet adds to that line);
        else error (+ hint for bet text that doesn't parse)."""
        cfg = self.cfg
        cur = cfg["currency"]
        if isinstance(entry, str):
            entry = {"bet": entry}
        plan: dict[str, Any] = {"ok": False, "user": None, "bet": "", "amount_in": None, "amount": None}
        if not isinstance(entry, dict):
            plan["error"] = "bet entry must be an object"
            return plan
        user = _clean_user(entry.get("user")) or _clean_user(default_user)
        raw = entry.get("bet")
        text = _bet_text(raw)
        plan.update(user=user, bet=text, amount_in=entry.get("amount"))

        def fail(msg: str, **kw) -> dict:
            plan.update(kw)
            plan["error"] = msg
            return plan

        p = parse_bet(text)
        if not p["valid"]:
            return fail(p["error"], hint=_ROULETTE_BET_HINT)
        if not user:
            return fail("user required")
        amt, err = _parse_coins(entry.get("amount"))
        if err:
            return fail(err)
        if amt < cfg["min_bet"]:
            return fail(f"minimum bet is {cfg['min_bet']} {cur}")
        existing = self._find(user, p["type"], p["numbers"])
        total = (existing["amount"] if existing else 0) + amt
        if cfg["max_bet"] and total > cfg["max_bet"]:
            extra = f" ({existing['amount']} already on {p['label']})" if existing else ""
            return fail(f"max bet is {cfg['max_bet']} {cur}{extra}")
        if total > COINS_MAX:
            return fail("amount too large")
        if existing is None and len(self.table["bets"]) >= ROULETTE_TABLE_MAX:
            return fail(f"the table is full ({ROULETTE_TABLE_MAX} bets) - add to a bet already down, "
                        f"or wait for the next spin")
        plan.update(ok=True, amount=amt, parsed=p, action="add" if existing else "new", _bet=existing)
        plan.pop("error", None)
        return plan

    def _apply_plan(self, plan: dict) -> tuple[dict, dict]:
        """Mutate the table for an ok plan. Returns (bet, debit event)."""
        amt = plan["amount"]
        if plan["action"] == "new":
            p = plan["parsed"]
            b = {"id": self._new_bet_id(), "user": plan["user"], "bet": plan["bet"], "type": p["type"],
                 "numbers": list(p["numbers"]), "label": p["label"], "odds": p["odds"], "amount": amt,
                 "placed_at": round(time.time(), 3)}
            self.table["bets"].append(b)
            return b, _ev("debit", b["user"], amt, "bet", b["id"], b["label"], None)
        b = plan["_bet"]
        b["amount"] += amt
        return b, _ev("debit", b["user"], amt, "add", b["id"], b["label"], None)

    def place_bets(self, entries: list, default_user: Any = None) -> dict:
        """Place bets on the table (in order: later entries see earlier ones). Every
        accepted bet is one debit ledger event. Callers must check bets_closed first."""
        accepted, rejected, pending = [], [], []
        for entry in entries:
            plan = self.plan_bet(entry, default_user)
            if not plan["ok"]:
                rej = {"user": plan["user"], "bet": plan["bet"], "amount": _echo(plan["amount_in"]),
                       "error": plan["error"]}
                if plan.get("hint"):
                    rej["hint"] = plan["hint"]
                rejected.append(rej)
                continue
            bet, ev = self._apply_plan(plan)
            view = roulette_bet_view(bet)
            view.update(action=plan["action"], added=plan["amount"])
            accepted.append(view)
            pending.append(ev)
        debits = []
        if pending:
            logged = self._persist(pending)
            debits = [{"user": e["user"], "amount": e["amount"], "bet_id": e["bet_id"], "seq": e["seq"],
                       "reason": e["reason"]} for e in logged]
        return {"accepted": accepted, "rejected": rejected, "debits": debits}

    def remove(self, params: dict) -> tuple[int, dict]:
        """Take bets down: {bet_id} | {user, bet} | {user, all:true}. Returns (status, body)."""
        user = _clean_user(params.get("user"))
        bid = params.get("bet_id", params.get("id"))
        bid = _bet_id(bid)
        text = params.get("bet")
        text = _bet_text(text)
        if bid:
            b = self._bet_by_id(bid)
            if b is None:
                return 400, {"error": f"no bet with id {bid} on the table"}
            if user and user != b["user"]:
                return 400, {"error": f"bet {bid} belongs to @{b['user']}"}
            take = [b]
        elif user and _flag(params.get("all")):
            take = [b for b in self.table["bets"] if b["user"] == user]
            if not take:
                return 400, {"error": f"@{user} has no bets on the table"}
        elif user and text:
            p = parse_bet(text)
            if not p["valid"]:
                return 400, {"error": p["error"]}
            b = self._find(user, p["type"], p["numbers"])
            if b is None:
                return 400, {"error": f"@{user} has no {p['label']} bet on the table"}
            take = [b]
        else:
            return 400, {"error": "remove needs bet_id, or user + bet, or user + all=true"}

        events, removed = [], []
        for b in take:
            view = roulette_bet_view(b)
            view["refund"] = b["amount"]
            removed.append(view)
            events.append(_ev("credit", b["user"], b["amount"], "remove", b["id"], b["label"], None))
        gone = {b["id"] for b in take}
        self.table["bets"] = [b for b in self.table["bets"] if b["id"] not in gone]
        logged = self._persist(events)
        if not self.table["bets"]:
            self.cancel_auto("auto")                  # the table emptied (a /timer countdown keeps running)
        return 200, {"removed": removed, "credits": _aggregate_credits(logged, "amount"), "ledger": logged}

    def user_view(self, name: Any) -> dict:
        user = _clean_user(name)
        bets = [roulette_bet_view(b) for b in self.table["bets"] if b["user"] == user]
        return {"user": user, "bets": bets, "exposure": sum(b["amount"] for b in bets),
                "session": self.ledger.session(user, self.key)}

    # ---- spins -----------------------------------------------------------

    def spin_outcome(self, params: dict, result: dict, user: str | None, test: bool) -> dict:
        out = super().spin_outcome(params, result, user, test)     # the spin's own bets (direct, unchanged)
        # the table, frozen now; committed at landing (on_commit). Test spins leave it alone.
        settlements = [] if test else roulette_settle(self.table["bets"], result)
        out.update(settlements=settlements, credits=_aggregate_credits(settlements),
                   table_summary=roulette_table_summary(settlements))
        return out

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


def _clean_roulette_bet(raw: Any) -> dict | None:
    """A persisted roulette table bet, validated (None = unusable): its bet text is
    parsed again and must give the stored type and numbers; label and odds come
    from that parse."""
    if not isinstance(raw, dict):
        return None
    user = _clean_user(raw.get("user"))
    bid = raw.get("id")
    amount = _strict_int(raw.get("amount"))
    text = raw.get("bet")
    if (not user or not isinstance(bid, str) or not bid.strip() or amount is None or amount < 1
            or not isinstance(text, str)):
        return None
    text = text.strip()[:BET_TEXT_MAX_CHARS]
    p = parse_bet(text)
    if not p["valid"] or p["type"] != raw.get("type") or p["numbers"] != raw.get("numbers"):
        return None
    placed = _as_float(raw.get("placed_at")) or 0.0
    return {"id": bid.strip()[:64], "user": user, "bet": text, "type": p["type"], "numbers": p["numbers"],
            "label": p["label"], "odds": p["odds"], "amount": amount, "placed_at": round(placed, 3)}


def clean_roulette_table(raw: Any) -> tuple[dict, int]:
    """(table, last_seq) from a parsed games_roulette_table.json (tolerant)."""
    t: dict[str, Any] = {"bets": []}
    if not isinstance(raw, dict):
        return t, 0
    seen: set[str] = set()
    bets = raw.get("bets")
    for rb in bets if isinstance(bets, list) else []:
        b = _clean_roulette_bet(rb)
        if b is None or b["id"] in seen:
            log.error("[games] roulette table: dropped an unreadable bet %r", rb)
            continue
        seen.add(b["id"])
        t["bets"].append(b)
    last_seq = _strict_int(raw.get("last_seq"))
    return t, (last_seq if last_seq and last_seq > 0 else 0)


def load_roulette_state(path: Path) -> tuple[dict, int, list[dict]]:
    """(table, last_seq, journal) from games_roulette_table.json (a restart mid-spin
    loses that spin: the bets stay and ride on the next one)."""
    return _load_table_file(path, clean_roulette_table, lambda: {"bets": []})


ROULETTE = Roulette(recover=False)   # restores the table from games_roulette_table.json


# --------------------------------------------------------------------------
# what the Games pages need to load this game (see core.register_game)
# --------------------------------------------------------------------------

OVERLAY = {
    "script": "roulette.js",
    "stateful": False,
    "appearance": list(Roulette.APPEARANCE),
    # mirrors the defaults of the game's config section, for a renderer that runs before its
    # first config message arrives
    "defaults": {
        "x": 50,
        "y": 50,
        "scale": 1.25,
        "theme": "classic",
        "red_color": "",
        "black_color": "",
        "green_color": "",
        "spin_seconds": 9,
        "result_seconds": 6,
        "hide_when_idle": True,
        "show_result": True,
        "result_position": "center",
        "result_details": True,
        "show_history": True,
        "history_count": 10,
        "show_user": True,
        "show_bets": True,
        "bets_max": 5,
        "sfx": True,
        "sfx_volume": 0.5,
        "spin_clip": "",
        "land_clip": "",
        "cooldown_seconds": 0,
        "currency": "hexcoins",
        "min_bet": 1,
        "max_bet": 100000,
        "auto_spin": False,
        "bet_window_seconds": 20,
        "show_when_bets": True,
        "show_table": True,
        "table_max": 6,
        "table_position": "right",
        "show_rules": True,
    },
}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])


# ---- roulette-only routes: the table (bets for the next spin). /spin with `bets`
#      (direct bets: on the spin, not on the table or in the ledger) is unchanged --

@router.api_route("/api/roulette/bet", methods=["GET", "POST"])
async def api_roulette_bet(request: Request):
    return await _bet_request(ROULETTE, request)


@router.api_route("/api/roulette/remove", methods=["GET", "POST"])
async def api_roulette_remove(request: Request):
    return await _remove_request(ROULETTE, request)


@router.get("/api/roulette/table")
async def api_roulette_table():
    ROULETTE._heal()
    return {"ok": True, "table": ROULETTE.table_view()}


@router.get("/api/roulette/user/{name}")
async def api_roulette_user(name: str):
    return {"ok": True, **ROULETTE.user_view(name)}


@router.get("/api/roulette/ledger")
async def api_roulette_ledger(since: str | None = None, limit: str | None = None):
    return _ledger_reply(since, limit, "roulette")


@router.api_route("/api/roulette/clear", methods=["GET", "POST"])
async def api_roulette_clear():
    return await _clear_request(ROULETTE)


# /board/clear before /board (display only: never touches bets, ledger, history)
@router.api_route("/api/roulette/board/clear", methods=["GET", "POST"])
async def api_roulette_board_clear():
    return await _board_clear_request(ROULETTE)


@router.api_route("/api/roulette/board", methods=["GET", "POST"])
async def api_roulette_board(request: Request):
    return await _board_request(ROULETTE, request)


API_DOC = {
    "spin_reply": "SPIN + bets, summary (the spin's own bets: not on the table, not in the ledger) + "
                  "settlements, credits [{user,amount}], table_summary (the table's bets, settled at "
                  "landing), table (after landing when wait=true, else the pre-spin table), ledger (wait=true)",
    "bet":        "GET|POST /games/api/roulette/bet   {user, bet, amount} | {bets:[...]} (<=200) -> "
                  "{accepted, rejected, debits:[{user,amount,bet_id,seq,reason}], table}; the same user + "
                  "bet adds to that line; bets ride on the next spin; 400 if all rejected, 409 bets_closed "
                  "while the ball is in the air",
    "remove":     "GET|POST /games/api/roulette/remove   {bet_id} | {user, bet} | {user, all:true} -> "
                  "{removed, credits, ledger, table}",
    "table":      "GET /games/api/roulette/table   bets, exposure, bets_open, auto_spin_in_ms, last_seq",
    "user":       "GET /games/api/roulette/user/{name}   bets, exposure, session {debits, credits, net, "
                  "events} (roulette only)",
    "clear":      "GET|POST /games/api/roulette/clear   refund every bet on the table",
    "board":      "GET|POST /games/api/roulette/board   the bot's own board {title, bets:[{user, text, "
                  "amount}] (<=100)} shown instead of the computed one ({bets:[]} = empty board, clear:true "
                  "= computed again) -> {table} (table.display_board)",
    "board_clear": "GET|POST /games/api/roulette/board/clear",
    "ledger":     "GET /games/api/roulette/ledger?since=0&limit=500   roulette events only (last_seq is the "
                  "ledger's own: seq gaps are other games' events)",
    "timer":      "GET|POST /games/api/roulette/timer?seconds=20   (+ /timer/cancel) the countdown to the "
                  "next spin; with auto_spin on it starts by itself on the first bet",
    "examples": [
        "curl 'http://host:4747/games/api/roulette/bet?user=alice&bet=red&amount=100'",
        "curl -X POST http://host:4747/games/api/roulette/bet -H 'Content-Type: application/json' "
        "-d '{\"bets\":[{\"user\":\"alice\",\"bet\":\"17\",\"amount\":10},"
        "{\"user\":\"bob\",\"bet\":\"split:17/20\",\"amount\":25}]}'",
        "curl 'http://host:4747/games/api/roulette/timer?seconds=30'",
        "curl 'http://host:4747/games/api/roulette/ledger?since=0'",
        "curl 'http://host:4747/games/api/roulette/remove?user=bob&bet=split:17/20'",
    ],
}
