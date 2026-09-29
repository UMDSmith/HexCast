"""Craps - a bank-craps table with persistent bets (Games add-on).
"""

from __future__ import annotations

import math
import re
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from hexcast_plugins.games.core import (
    MAX_BETS,
    TableGame,
    _RNG,
    _THEMES,
    _aggregate_credits,
    _as_float,
    _bet_entries,
    _bet_id,
    _bet_request,
    _bet_text,
    _board_clear_request,
    _board_request,
    _clean_user,
    _clear_request,
    _echo,
    _err,
    _ev,
    _flag,
    _flush_ledger,
    _ledger_reply,
    _load_table_file,
    _parse_coins,
    _rejected_entry,
    _remove_request,
    _soon,
    _spin_request,
    _strict_int,
    log,
)

# paths + limits that belong to this game
from hexcast_plugins.games.core import CONFIG_DIR

CRAPS_TABLE_PATH = CONFIG_DIR / "games_craps_table.json"   # the craps table (bets survive restarts)
ROLL_SECONDS_MIN = 2.5          # craps: per-roll `duration` clamp (config roll_seconds too)
ROLL_SECONDS_MAX = 10.0

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


def craps_summary(settlements: list[dict]) -> dict:
    return {
        "bets_settled": sum(1 for s in settlements if s["outcome"] != "travel"),
        "winners": sum(1 for s in settlements if s["outcome"] == "win"),
        "total_won": sum(s["won"] for s in settlements),
        "total_lost": sum(s["lost"] for s in settlements),
        "total_credited": sum(s["credit"] for s in settlements),
    }


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


def load_craps_state(path: Path) -> tuple[dict, int, list[dict]]:
    """(table, last_seq, journal) from games_craps_table.json."""
    return _load_table_file(path, clean_table, _fresh_table)


def load_craps_table(path: Path) -> tuple[dict, int]:
    """Restore the table (a restart mid-roll simply loses that roll: bets stay)."""
    table, last_seq, _ = load_craps_state(path)
    return table, last_seq


# --------------------------------------------------------------------------
# craps: the game
# --------------------------------------------------------------------------

_DICE_STYLES = ("red", "white", "black", "gold")


class Craps(TableGame):
    key = "craps"
    title = "Craps"
    id_prefix = "c"
    has_bets = False                   # bets live on the table, not on a roll
    duration_key = "roll_seconds"
    launch_clip_key = "roll_clip"
    auto_key = "auto_roll"

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
        "show_rules": True,             # the "how to play" box while bets are open
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
        "show_rules": ("bool",),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "dice_style", "show_point", "show_history", "history_count",
                  "show_user", "show_bets", "bets_max", "show_payouts", "payouts_max", "sfx", "sfx_volume",
                  "show_rules")

    def duration_range(self) -> tuple[float, float]:
        return ROLL_SECONDS_MIN, ROLL_SECONDS_MAX

    # ---- table -----------------------------------------------------------

    def default_table_path(self) -> Path:
        return CRAPS_TABLE_PATH

    def load_state(self, path: Path) -> tuple[dict, int, list[dict]]:
        return load_craps_state(path)

    def fresh_table(self) -> dict:
        return _fresh_table()

    def table_data(self) -> dict:
        t = self.table
        return {"phase": t["phase"], "point": t["point"], "shooter": t["shooter"], "hand_rolls": t["hand_rolls"],
                "bets": t["bets"]}

    def _stake(self, b: dict) -> int:
        return b["amount"] + b["odds"]

    def _label(self, b: dict) -> str:
        return bet_label(b["type"], b["number"])

    def apply_spin(self, table: dict, spin: dict) -> list[dict]:
        return craps_apply(table, spin)

    def table_view(self) -> dict:
        """The TABLE object (craps spec §3), built fresh (auto_roll_in_ms is 'now')."""
        t, cfg = self.table, self.cfg
        exposure = self.exposure()
        return {"phase": t["phase"], "point": t["point"], "shooter": t["shooter"], "hand_rolls": t["hand_rolls"],
                "bets": [bet_view(b, t["phase"]) for b in t["bets"]],
                "exposure": exposure, "total_on_table": sum(exposure.values()),
                "bets_open": not self.in_flight(), "auto_roll_in_ms": self.auto_roll_in_ms(),
                "last_seq": self.ledger.last_seq, "currency": cfg["currency"],
                "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"], "odds_rule": cfg["odds_rule"],
                "field_12_pays": cfg["field_12_pays"], "display_board": self.display_board}

    def _find(self, user: str | None, btype: str, number: int | None) -> dict | None:
        for b in self.table["bets"]:
            if b["user"] == user and b["type"] == btype and b["number"] == number:
                return b
        return None

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
        text = _bet_text(raw)
        target = entry.get("target")
        target = _bet_id(target) or None
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
        bid = _bet_id(bid)
        text = params.get("bet")
        text = _bet_text(text)
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
            self.cancel_auto("auto")                  # the table emptied (a /timer countdown keeps running)
        return 200, {"removed": removed, "credits": _aggregate_credits(logged, "amount"), "ledger": logged}

    def user_view(self, name: Any) -> dict:
        user = _clean_user(name)
        phase = self.table["phase"]
        bets = [bet_view(b, phase) for b in self.table["bets"] if b["user"] == user]
        return {"user": user, "bets": bets, "exposure": sum(b["amount"] + b["odds"] for b in bets),
                "session": self.ledger.session(user, self.key)}

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

    # ---- auto-roll (the countdown itself lives in Game) --------------------------

    def auto_params(self) -> dict:
        return {"user": self.table["shooter"]}        # the current shooter throws

    def auto_roll_in_ms(self) -> int | None:
        return self.auto_in_ms()

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


CRAPS = Craps(recover=False)         # restores the table from games_craps_table.json


# --------------------------------------------------------------------------
# what the Games pages need to load this game (see core.register_game)
# --------------------------------------------------------------------------

OVERLAY = {
    "script": "craps.js",
    "stateful": False,
    "appearance": list(Craps.APPEARANCE),
    # mirrors the defaults of the game's config section, for a renderer that runs before its
    # first config message arrives
    "defaults": {
        "x": 50,
        "y": 50,
        "scale": 1.0,
        "theme": "classic",
        "dice_style": "red",
        "roll_seconds": 4,
        "result_seconds": 5,
        "cooldown_seconds": 0,
        "hide_when_idle": True,
        "show_when_bets": True,
        "show_point": True,
        "show_history": True,
        "history_count": 10,
        "show_user": True,
        "show_bets": True,
        "bets_max": 6,
        "show_payouts": True,
        "payouts_max": 5,
        "sfx": True,
        "sfx_volume": 0.5,
        "roll_clip": "",
        "land_clip": "",
        "currency": "hexcoins",
        "min_bet": 1,
        "max_bet": 100000,
        "odds_rule": "345",
        "field_12_pays": 3,
        "auto_roll": False,
        "bet_window_seconds": 20,
        "show_rules": True,
    },
}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])


# ---- craps-only routes (registered before the generic /api/{game}/... ones;
#      /spin, /play, /validate, /bets, /last, /history, /show, /hide, /stop,
#      /timer for craps are the generic routes, dispatched through the Game hooks) --

@router.api_route("/api/craps/roll", methods=["GET", "POST"])
async def api_craps_roll(request: Request):
    return await _spin_request(CRAPS, request)


@router.api_route("/api/craps/bet", methods=["GET", "POST"])
async def api_craps_bet(request: Request):
    return await _bet_request(CRAPS, request)


@router.api_route("/api/craps/remove", methods=["GET", "POST"])
async def api_craps_remove(request: Request):
    return await _remove_request(CRAPS, request)


@router.get("/api/craps/table")
async def api_craps_table():
    CRAPS._heal()
    return {"ok": True, "table": CRAPS.table_view()}


@router.get("/api/craps/user/{name}")
async def api_craps_user(name: str):
    return {"ok": True, **CRAPS.user_view(name)}


@router.get("/api/craps/ledger")
async def api_craps_ledger(since: str | None = None, limit: str | None = None):
    return _ledger_reply(since, limit, "craps")


@router.api_route("/api/craps/clear", methods=["GET", "POST"])
async def api_craps_clear():
    return await _clear_request(CRAPS)


# /board/clear before /board (display only: never touches bets, ledger, history)
@router.api_route("/api/craps/board/clear", methods=["GET", "POST"])
async def api_craps_board_clear():
    return await _board_clear_request(CRAPS)


@router.api_route("/api/craps/board", methods=["GET", "POST"])
async def api_craps_board(request: Request):
    return await _board_request(CRAPS, request)


API_DOC = {
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
    "user":       "GET /games/api/craps/user/{name}   bets, exposure, session {debits, credits, net} "
                  "(craps only)",
    "ledger":     "GET /games/api/craps/ledger?since=0&limit=500   {events, last_seq, truncated} "
                  "(limit <= 5000) - craps events only (last_seq is the ledger's own: seq gaps are other "
                  "games' events)",
    "clear":      "GET|POST /games/api/craps/clear   refund every bet, reset to the come-out",
    "validate":   "GET /games/api/craps/validate?bet=&user=&amount=&target=   dry run: valid, type, label, "
                  "odds_text, error, hint, max_odds",
    "bets":       "GET /games/api/craps/bets   bet reference + rules",
    "board":      "GET|POST /games/api/craps/board   the bot's own board {title, bets:[{user, text, amount}] "
                  "(<=100)} shown instead of the computed one ({bets:[]} = empty board, clear:true = computed "
                  "again) -> {table} (table.display_board); allowed while the dice fly",
    "board_clear": "GET|POST /games/api/craps/board/clear",
    "timer":      "GET|POST /games/api/craps/timer?seconds=20   (+ /timer/cancel) the countdown to the "
                  "next roll; with auto_roll on it starts by itself on the first bet",
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
}
