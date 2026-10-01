"""Hexfall - a hex-themed Plinko (Games add-on).
"""

from __future__ import annotations

import re
import secrets
import time
from fractions import Fraction
from functools import lru_cache
from math import comb
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from hexcast_plugins.games.core import (
    COMMANDS_TEXT_MAX,
    ROUND_PLAYERS_MAX,
    ROUND_TITLE_MAX,
    RoundGame,
    _RNG,
    _aggregate_credits,
    _as_float,
    _clean_user,
    _debits,
    _ev,
    _flag,
    _floor,
    _ledger_reply,
    _num,
    _round_common,
    _round_players,
    _round_request,
    _strict_int,
)

# paths + limits that belong to this game
from hexcast_plugins.games.core import CONFIG_DIR

HEXFALL_PATH = CONFIG_DIR / "games_hexfall.json"          # the running game + history (hexfall)
ROWS_MIN, ROWS_MAX = 8, 16
DROPS_MAX = 10
MULT_MAX = 1000                  # the biggest multiplier a slot may carry
MULTIPLIERS_TEXT_MAX = 400       # the custom table as text (17 slots x "1000, " fits easily)
RISKS = ("low", "medium", "high")

# --------------------------------------------------------------------------
# hexfall
# --------------------------------------------------------------------------
# A hex-themed Plinko. A game is `drops` drops (3). Each drop has a betting window in which every
# player puts up an amount (per drop, min_bet / max_bet), then ONE glowing hex token falls through
# `rows` rows (8-16) of hexagonal pegs. At every peg the server flips a fair coin - left or right,
# secrets.SystemRandom - and the slot is the number of right bounces, so slot k has the binomial
# probability C(rows, k) / 2^rows. The overlay only animates the path it is sent.
#
# Every player's stake that drop is paid  stake x the landing slot's multiplier,  rounded down to
# whole coins (a x0 slot is a BUST). The multipliers are a table of rows + 1 numbers: a built-in
# preset for rows x risk (low / medium / high; symmetric, big at the edges, small or bust in the
# middle) or the streamer's own list (setting `multipliers`, which must have exactly rows + 1
# values). There is NO house-edge setting and nothing is nudged: the table IS the odds. Its RTP
# (the sum of probability x multiplier) and the house edge (100% - RTP) are computed exactly from
# the binomial probabilities and shown everywhere the table is.
#
# The table, the rows and the risk are copied into the game when it starts: changing the settings
# mid-game never changes the odds of a game that is running.

# Built-in tables, one multiplier per slot, slot k = k right bounces. Each is symmetric and has an
# RTP of about 95% (94.85% - 95.23%); GET /games/api/hexfall/bets lists the exact numbers.
# (static/hexfall.js carries the same tables for the Edit Mode editor's sample game; a test keeps them equal.)
PRESETS: dict[str, dict[int, tuple]] = {
    "low": {
        8: (6, 2.5, 1.2, 0.8, 0.5, 0.8, 1.2, 2.5, 6),
        9: (8, 5, 1.2, 1, 0.5, 0.5, 1, 1.2, 5, 8),
        10: (8, 2.5, 2, 1, 0.8, 0.6, 0.8, 1, 2, 2.5, 8),
        11: (10, 4, 2, 1.5, 1, 0.5, 0.5, 1, 1.5, 2, 4, 10),
        12: (10, 4, 3, 1.5, 1, 0.8, 0.5, 0.8, 1, 1.5, 3, 4, 10),
        13: (15, 5, 3, 2, 1.2, 1, 0.5, 0.5, 1, 1.2, 2, 3, 5, 15),
        14: (15, 8, 5, 2, 1.5, 1, 0.7, 0.5, 0.7, 1, 1.5, 2, 5, 8, 15),
        15: (20, 10, 5, 4, 2, 1.2, 0.7, 0.5, 0.5, 0.7, 1.2, 2, 4, 5, 10, 20),
        16: (20, 10, 5, 4, 2, 1.2, 1, 0.7, 0.5, 0.7, 1, 1.2, 2, 4, 5, 10, 20),
    },
    "medium": {
        8: (15, 5, 1, 0.5, 0.3, 0.5, 1, 5, 15),
        9: (20, 10, 3, 0.3, 0, 0, 0.3, 3, 10, 20),
        10: (25, 10, 2.5, 1.2, 0.5, 0, 0.5, 1.2, 2.5, 10, 25),
        11: (40, 15, 5, 2, 0.5, 0, 0, 0.5, 2, 5, 15, 40),
        12: (40, 15, 5, 3, 1, 0.3, 0, 0.3, 1, 3, 5, 15, 40),
        13: (60, 20, 10, 5, 1, 0.5, 0, 0, 0.5, 1, 5, 10, 20, 60),
        14: (75, 25, 5, 3, 2, 1, 0.6, 0, 0.6, 1, 2, 3, 5, 25, 75),
        15: (100, 50, 25, 10, 1.5, 1, 0.5, 0, 0, 0.5, 1, 1.5, 10, 25, 50, 100),
        16: (100, 50, 25, 5, 3, 1.2, 1, 0.5, 0, 0.5, 1, 1.2, 3, 5, 25, 50, 100),
    },
    "high": {
        8: (30, 2, 1.5, 0.6, 0, 0.6, 1.5, 2, 30),
        9: (50, 8, 1.5, 0.8, 0, 0, 0.8, 1.5, 8, 50),
        10: (75, 25, 2, 0.6, 0, 0, 0, 0.6, 2, 25, 75),
        11: (100, 50, 5, 0.3, 0, 0, 0, 0, 0.3, 5, 50, 100),
        12: (150, 30, 8, 3, 0.5, 0, 0, 0, 0.5, 3, 8, 30, 150),
        13: (250, 50, 10, 4, 1.5, 0, 0, 0, 0, 1.5, 4, 10, 50, 250),
        14: (400, 100, 25, 8, 0.8, 0, 0, 0, 0, 0, 0.8, 8, 25, 100, 400),
        15: (600, 100, 20, 10, 5, 0, 0, 0, 0, 0, 0, 5, 10, 20, 100, 600),
        16: (1000, 500, 100, 10, 2, 0.2, 0, 0, 0, 0, 0, 0.2, 2, 10, 100, 500, 1000),
    },
}
DEFAULT_ROWS = 12
DEFAULT_RISK = "medium"

_HF_THEMES = ("coven", "ember", "frost")
_HF_OUTCOMES = {"complete": "Every drop has fallen", "walked": "Nobody bet on the next drop - game over",
                "no_bets": "No bets - no game", "stopped": "Game stopped - open bets refunded",
                "restart": "Settled after a restart", "error": "Settled after an error"}


# ---- the math (exact: Fractions, never floats, decide a payout) ------------------------------

def slot_ways(rows: int) -> list[int]:
    """How many of the 2^rows left/right paths end in each slot: C(rows, k)."""
    return [comb(rows, k) for k in range(rows + 1)]


def slot_probs(rows: int) -> list[Fraction]:
    return [Fraction(w, 2 ** rows) for w in slot_ways(rows)]


def mult_frac(m: Any) -> Fraction:
    """A multiplier as an exact fraction (a table keeps at most 4 decimals)."""
    return Fraction(str(round(float(m), 4)))


def table_rtp(rows: int, mults) -> Fraction:
    """Return to player: the sum over the slots of probability x multiplier (1 = 100%)."""
    return sum((p * mult_frac(m) for p, m in zip(slot_probs(rows), mults)), Fraction(0))


def rtp_pct(rtp: Fraction) -> float:
    """RTP in percent, rounded DOWN to 2 decimals: the return is never overstated."""
    return _floor(rtp * 10000) / 100


def edge_pct(rtp: Fraction) -> float:
    """The house edge in percent: 100 - the (rounded down) RTP, so the two always add up. Negative when
    the table pays back more than 100%."""
    return round(100 - rtp_pct(rtp), 2)


def roll_path(rows: int, rng=None) -> list[int]:
    """One drop: a fair left (0) / right (1) bounce at each of `rows` pegs. The slot is the sum."""
    rng = rng or _RNG
    return [rng.getrandbits(1) for _ in range(rows)]


def pays(stake: int, mult: Any) -> int:
    """What a stake is paid for a slot: stake x multiplier, rounded down to whole coins."""
    return _floor(stake * mult_frac(mult))


def preset(rows: int, risk: str) -> list:
    rows = min(ROWS_MAX, max(ROWS_MIN, int(rows)))
    return list(PRESETS[risk if risk in PRESETS else DEFAULT_RISK][rows])


_MULT_SPLIT = re.compile(r"[,;\s]+")


def parse_multipliers(raw: Any, rows: int) -> tuple[list | None, str | None]:
    """A multiplier table from a list of numbers or text ("25, 10, 2.5 ... bust ... x10"): ([values], None),
    or (None, why) when it can't be a table for `rows` rows - it needs exactly rows + 1 numbers, each from
    0 (a bust) to MULT_MAX. Values keep at most 4 decimals."""
    if isinstance(raw, str):
        items: list = [t for t in _MULT_SPLIT.split(raw.strip()) if t]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return None, "multipliers must be a list of numbers (or text like \"25, 10, 2.5, 0\")"
    if len(items) > 64:
        return None, "too many multipliers"
    out: list = []
    for it in items:
        if isinstance(it, str):
            s = it.strip().lower()
            s = "0" if s == "bust" else s.strip("x×* ")
            v = _as_float(s)
        else:
            v = _as_float(it)
        if v is None:
            return None, f"{str(it)[:20]!r} is not a number"
        if v < 0 or v > MULT_MAX:
            return None, f"a multiplier must be between 0 and {MULT_MAX}"
        out.append(_num(round(v, 4)))
    if len(out) != rows + 1:
        return None, f"{rows} rows have {rows + 1} slots: the table needs {rows + 1} multipliers, got {len(out)}"
    return out, None


def resolve_table(rows: int, risk: str, custom: Any = None) -> tuple[list, str]:
    """(multipliers, "custom" | "preset"): the custom list when it fits `rows`, else the risk's preset."""
    if custom:
        vals, _why = parse_multipliers(custom, rows)
        if vals is not None:
            return vals, "custom"
    return preset(rows, risk), "preset"


@lru_cache(maxsize=64)
def table_view(rows: int, mults: tuple) -> tuple[tuple[dict, ...], float, float]:
    """(slots, rtp %, house edge %) of a table: every slot with its exact odds (`ways` of `of` paths)."""
    total = 2 ** rows
    slots = []
    for k, (w, m) in enumerate(zip(slot_ways(rows), mults)):
        p = Fraction(w, total)
        slots.append({"slot": k, "mult": _num(m), "bust": mult_frac(m) == 0, "ways": w, "of": total,
                      "probability": round(float(p), 8), "pct": round(float(p) * 100, 4),
                      "rtp_pct": round(float(p * mult_frac(m)) * 100, 4)})
    rtp = table_rtp(rows, mults)
    return tuple(slots), rtp_pct(rtp), edge_pct(rtp)


def mult_label(m: Any) -> str:
    """x25 / x0.5 / BUST (for ledger labels and messages)."""
    f = mult_frac(m)
    if f == 0:
        return "BUST"
    return "×" + (str(int(f)) if f.denominator == 1 else f"{float(f):g}")


class Hexfall(RoundGame):
    key = "hexfall"
    title = "Hexfall"
    id_prefix = "hf"
    PHASES = ("betting", "dropping", "result", "over")
    STAT_KEYS = ("games", "complete", "walked", "no_bets", "stopped", "drops", "busts",
                 "total_bet", "total_paid", "house_net")

    DEFAULTS: dict[str, Any] = {
        # placement: scene centre in % of the 1920x1080 stage; scale x the 1280x860 scene
        "x": 50, "y": 50, "scale": 0.85,
        "theme": "coven",               # coven | ember | frost
        "title": "Hexfall",             # the title on the overlay (your branding)
        "show_rules": True,             # rules box while bets are open
        "show_players": True, "players_max": 8,
        "show_odds": True,              # the payout ladder
        "show_history": True,           # the strip of the last slot hits
        "sfx": True, "sfx_volume": 0.6,
        "hide_when_idle": True,
        "commands_text": "",            # e.g. "!drop 100" (your bot's commands)
        "drops": 3,                     # drops per game
        "rows": DEFAULT_ROWS,           # rows of pegs, 8-16 (rows + 1 slots)
        "risk": DEFAULT_RISK,           # low | medium | high: which built-in multiplier table
        "multipliers": [],              # your own table: rows + 1 numbers (empty = the risk preset)
        "open_bet_seconds": 30,         # the first betting window
        "between_seconds": 20,          # the window before every later drop
        "drop_seconds": 9,              # the token falls
        "result_seconds": 5,            # the landing slot + payouts stay up
        "summary_seconds": 10,          # game over card
        "currency": "coins",            # your bot's coin name, shown after amounts
        "min_bet": 1, "max_bet": 100000,  # per player per drop; 0 = no max
        "drop_clip": "", "win_clip": "", "bust_clip": "",   # soundboard clips
    }
    SCHEMA: dict[str, tuple] = {
        "x": ("num", 0, 100), "y": ("num", 0, 100), "scale": ("num", 0.2, 5),
        "theme": ("enum", _HF_THEMES), "title": ("name", ROUND_TITLE_MAX),
        "show_rules": ("bool",), "show_players": ("bool",), "players_max": ("int", 1, 20),
        "show_odds": ("bool",), "show_history": ("bool",), "sfx": ("bool",), "sfx_volume": ("num", 0, 1),
        "hide_when_idle": ("bool",), "commands_text": ("str", COMMANDS_TEXT_MAX),
        "drops": ("int", 1, DROPS_MAX), "rows": ("int", ROWS_MIN, ROWS_MAX), "risk": ("enum", RISKS),
        "multipliers": ("str", MULTIPLIERS_TEXT_MAX),       # (read by validate_config, which keeps it as a list)
        "open_bet_seconds": ("num", 5, 300), "between_seconds": ("num", 5, 300),
        "drop_seconds": ("num", 6, 20), "result_seconds": ("num", 2, 30), "summary_seconds": ("num", 4, 60),
        "currency": ("name", 24), "min_bet": ("int", 1, 1e9), "max_bet": ("int", 0, 1e12),
        "drop_clip": ("str", 200), "win_clip": ("str", 200), "bust_clip": ("str", 200),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "title", "show_rules", "show_players", "players_max", "show_odds",
                  "show_history", "sfx", "sfx_volume")

    def default_path(self) -> Path:
        return HEXFALL_PATH

    def validate_config(self, raw: Any) -> dict:
        raw = dict(raw) if isinstance(raw, dict) else {}
        custom = raw.pop("multipliers", None)            # a list (or text): checked against `rows` below
        out = super().validate_config(raw)
        if out["max_bet"] and out["max_bet"] < out["min_bet"]:
            out["max_bet"] = 0
        vals = None
        if custom not in (None, "", []):
            vals, _why = parse_multipliers(custom, out["rows"])
        out["multipliers"] = vals or []                  # a list that does not fit `rows` is dropped
        return out

    # ---- saved game -----------------------------------------------------------

    def clean_game(self, raw: Any) -> dict | None:
        if isinstance(raw, dict):
            raw = dict(raw)                              # (a hand-edited file: the shared reader wants a list and a dict here)
            if not isinstance(raw.get("log"), list):
                raw["log"] = []
            if not isinstance(raw.get("totals"), dict):
                raw["totals"] = {}
        g = _round_common(raw, self.PHASES)
        if g is None:
            return None

        def stake(v):
            a = _strict_int(v)
            return a if a and a > 0 else None

        rows = min(ROWS_MAX, max(ROWS_MIN, _strict_int(raw.get("rows")) or DEFAULT_ROWS))
        risk = raw.get("risk") if raw.get("risk") in RISKS else DEFAULT_RISK
        mults, _why = parse_multipliers(raw.get("mults"), rows) if raw.get("mults") is not None else (None, None)
        source = raw.get("source") if raw.get("source") in ("preset", "custom") else "preset"
        if mults is None:                                 # unreadable table: the preset still pays something sane
            mults, source = preset(rows, risk), "preset"
        drops = min(DROPS_MAX, max(1, _strict_int(raw.get("drops")) or 3))
        fall = None
        rf = raw.get("fall")
        if isinstance(rf, dict) and isinstance(rf.get("path"), list) and len(rf["path"]) == rows \
                and all(p in (0, 1) and not isinstance(p, bool) for p in rf["path"]):
            path = [int(p) for p in rf["path"]]
            slot = sum(path)                              # the slot is always the path's own
            fall = {"drop": min(drops, max(1, _strict_int(rf.get("drop")) or 1)), "path": path, "slot": slot,
                    "mult": mults[slot], "seed": _strict_int(rf.get("seed")) or 0,
                    "ms": min(20000, max(1000, _strict_int(rf.get("ms")) or 9000)), "resolved": bool(rf.get("resolved"))}
        hits = []
        for h in raw.get("hits") if isinstance(raw.get("hits"), list) else []:
            if isinstance(h, dict) and _strict_int(h.get("slot")) is not None and 0 <= _strict_int(h["slot"]) <= rows:
                slot = _strict_int(h["slot"])
                hits.append({"drop": max(1, _strict_int(h.get("drop")) or len(hits) + 1), "slot": slot,
                             "mult": mults[slot], "bet": max(0, _strict_int(h.get("bet")) or 0),
                             "paid": max(0, _strict_int(h.get("paid")) or 0)})
        rb = raw.get("best") if isinstance(raw.get("best"), dict) else None
        best = None
        if rb and _clean_user(rb.get("user")) and (_strict_int(rb.get("net")) or 0) > 0:
            best = {"user": _clean_user(rb["user"]), "paid": max(0, _strict_int(rb.get("paid")) or 0), "net": _strict_int(rb["net"]),
                    "drop": max(1, _strict_int(rb.get("drop")) or 1), "mult": _as_float(rb.get("mult")) or 0}
        g.update({
            "drops": drops, "drop": min(drops, max(1, _strict_int(raw.get("drop")) or 1)),
            "rows": rows, "risk": risk, "source": source, "mults": mults,
            "bets": _round_players(raw.get("bets"), stake), "fall": fall, "hits": hits[:DROPS_MAX], "best": best,
        })
        return g

    # ---- money ----------------------------------------------------------------

    def _table(self) -> tuple[list, str]:
        """The running game's table, or the settings' when idle."""
        g = self.g
        if g is not None:
            return g["mults"], g["source"]
        cfg = self.cfg
        return resolve_table(cfg["rows"], cfg["risk"], cfg["multipliers"])

    def _player(self, user: str) -> dict:
        g = self.g
        bet = g["bets"].get(user, 0) if g else 0
        top = max((mult_frac(m) for m in g["mults"]), default=Fraction(0)) if g else Fraction(0)
        return {"user": user, "bet": bet, "max_win": _floor(bet * top)}

    def players_view(self) -> list[dict]:
        g = self.g
        rows = [self._player(u) for u in g["bets"]]
        rows.sort(key=lambda p: (-p["bet"], p["user"].lower()))
        return rows

    def table_bets(self) -> list[dict]:
        if self.g is None:
            return []
        return [{"user": u, "amount": a} for u, a in self.g["bets"].items()]

    # ---- API actions -------------------------------------------------------------

    def start_game(self, params: dict) -> tuple[int, dict]:
        if self.g is not None:
            return 409, {"error": "a game is already running", "phase": self.phase}
        cfg = self.cfg
        d = _as_float(params.get("drops"))
        drops = int(min(DROPS_MAX, max(1, d))) if d is not None else cfg["drops"]
        r = _as_float(params.get("rows"))
        rows = int(min(ROWS_MAX, max(ROWS_MIN, r))) if r is not None else cfg["rows"]
        risk_p = params.get("risk")
        if risk_p not in (None, ""):
            risk = str(risk_p).strip().lower()
            if risk not in RISKS:
                return 400, {"error": "risk must be low, medium or high"}
        else:
            risk = cfg["risk"]
        custom = params.get("multipliers")
        if custom not in (None, "", []):
            mults, why = parse_multipliers(custom, rows)         # asked for in this request: wrong = an error, not ignored
            if mults is None:
                return 400, {"error": why}
            source = "custom"
        elif risk_p in (None, "") and rows == cfg["rows"] and cfg["multipliers"]:
            mults, source = resolve_table(rows, risk, cfg["multipliers"])
        else:
            mults, source = preset(rows, risk), "preset"          # another rows / risk than the settings': a preset
        now = time.time()
        self.g = {
            "id": f"hf-{secrets.token_hex(4)}", "test": _flag(params.get("test")), "phase": "betting",
            "started_at": round(now, 3), "phase_at": round(now, 3), "ends_at": None,
            "drops": drops, "drop": 1, "rows": rows, "risk": risk, "source": source, "mults": mults,
            "bets": {}, "fall": None, "hits": [], "best": None,
            "debits": 0, "credits": 0, "totals": {}, "log": [], "ever_bet": False,
            "outcome": None, "summary": None, "last": None, "currency": cfg["currency"],
        }
        self.hidden = False
        secs = _as_float(params.get("seconds"))
        secs = min(300.0, max(5.0, secs)) if secs is not None else float(cfg["open_bet_seconds"])
        self._set_phase("betting", secs)
        self.save()
        return 200, {"started": self.g["id"], "drops": drops, "rows": rows, "risk": risk, "source": source,
                     "rtp_pct": table_view(rows, tuple(mults))[1]}

    def place(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        if user not in g["bets"] and len(g["bets"]) >= ROUND_PLAYERS_MAX:
            return 400, {"error": f"the game is full ({ROUND_PLAYERS_MAX} players)"}
        have = g["bets"].get(user, 0)
        amt, err = self._coins(params.get("amount"), have)
        if err:
            return 400, {"error": err}
        n = g["drop"]
        g["bets"][user] = have + amt
        g["ever_bet"] = True
        logged = self._persist([_ev("debit", user, amt, "add" if have else "bet", f"{g['id']}/d{n}",
                                    f"Drop {n}", g["id"])])
        return 200, {"amount": amt, "total": g["bets"][user], "drop": n, "debits": _debits(logged),
                     "player": self._player(user)}

    def remove(self, params: dict) -> tuple[int, dict]:
        """Take back what went down in THIS betting window."""
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        amt = g["bets"].pop(user, 0)
        if not amt:
            return 400, {"error": f"@{user} has no bet on this drop to take back"}
        logged = self._persist([_ev("credit", user, amt, "refund", f"{g['id']}/d{g['drop']}",
                                    "Bet (taken back)", g["id"])])
        return 200, {"credits": _aggregate_credits(logged, "amount"), "ledger": logged}

    def user_view(self, name: Any) -> dict:
        user = _clean_user(name)
        g = self.g
        player = self._player(user) if g is not None and user and user in g["bets"] else None
        return {"user": user, "player": player, "session": self.ledger.session(user, self.key)}

    # ---- the game ---------------------------------------------------------------

    def advance(self) -> None:
        ph = self.g["phase"]
        if ph == "betting":
            self._drop()
        elif ph == "dropping":
            self._land()
        elif ph == "result":
            self._after_result()
        else:
            self._to_idle()

    def _drop(self) -> None:
        """The window closed: with bets on it, the server flips every peg's coin and the token falls."""
        g = self.g
        if not g["bets"]:
            self._finish("walked" if g["ever_bet"] else "no_bets")
            return
        path = roll_path(g["rows"])
        slot = sum(path)
        secs = float(self.cfg["drop_seconds"])
        g["fall"] = {"drop": g["drop"], "path": path, "slot": slot, "mult": g["mults"][slot],
                     "seed": secrets.randbits(31), "ms": int(round(secs * 1000)), "resolved": False}
        self._set_phase("dropping", secs)
        self._clip("drop_clip")
        self.save()

    def _resolve(self) -> list[dict]:
        """Settle the drop (once): every stake is paid stake x the slot's multiplier, rounded down."""
        g = self.g
        f = g["fall"]
        if f is None or f["resolved"]:
            return []
        f["resolved"] = True
        n, slot, m = f["drop"], f["slot"], f["mult"]
        events, results = [], []
        for u, stake in g["bets"].items():
            pay = pays(stake, m)
            if pay > 0:
                events.append(_ev("credit", u, pay, "payout", f"{g['id']}/d{n}", f"Drop {n} · {mult_label(m)}", g["id"]))
            results.append({"user": u, "bet": stake, "paid": pay, "net": pay - stake})
        total_bet, total_paid = sum(r["bet"] for r in results), sum(r["paid"] for r in results)
        g["bets"] = {}
        results.sort(key=lambda r: (-r["paid"], -r["bet"], r["user"].lower()))
        top = max(results, key=lambda r: (r["net"], r["paid"]), default=None)
        if top is not None and top["net"] > (g["best"] or {}).get("net", 0):          # the game's biggest single win (net of the bet)
            g["best"] = {"user": top["user"], "paid": top["paid"], "net": top["net"], "drop": n, "mult": m}
        g["hits"].append({"drop": n, "slot": slot, "mult": m, "bet": total_bet, "paid": total_paid})
        g["last"] = {"drop": n, "slot": slot, "mult": m, "bust": mult_frac(m) == 0,
                     "winners": [r for r in results if r["net"] > 0], "even": [r for r in results if r["net"] == 0],
                     "losers": [r for r in results if r["net"] < 0], "total_bet": total_bet, "total_paid": total_paid}
        return events

    def _land(self) -> None:
        g = self.g
        events = self._resolve() if g["fall"] is not None else self.open_settlement()   # (no token to land: a damaged file - stakes go back)
        self._set_phase("result", float(self.cfg["result_seconds"]))
        self._persist(events)
        if g["fall"] is not None:
            self._clip("win_clip" if mult_frac(g["fall"]["mult"]) >= 1 else "bust_clip")

    def _after_result(self) -> None:
        g = self.g
        if g["drop"] >= g["drops"]:
            self._finish("complete")
        else:
            g["drop"] += 1
            g["fall"] = None
            self._set_phase("betting", float(self.cfg["between_seconds"]))
            self.save()

    def _finish(self, outcome: str, events: list[dict] | None = None) -> None:
        g = self.g
        if events:
            self._persist(events)
        g["outcome"] = outcome
        g["summary"] = self.summary(outcome)
        self._record_game(g["summary"])
        secs = float(self.cfg["summary_seconds"])
        self._set_phase("over", min(secs, 6.0) if outcome == "no_bets" else secs)
        self.save()

    def open_settlement(self) -> list[dict]:
        g = self.g
        events = []
        if g["phase"] == "dropping":
            events += self._resolve()                 # the token is already falling: its slot counts
        for u, amt in list(g["bets"].items()):
            events.append(_ev("credit", u, amt, "refund", f"{g['id']}/d{g['drop']}", "Bet (game stopped)", g["id"]))
        g["bets"] = {}
        return events

    def summary(self, outcome: str) -> dict:
        g = self.g
        drops = [{"drop": h["drop"], "slot": h["slot"], "mult": h["mult"], "rows": g["rows"], "bet": h["bet"],
                  "paid": h["paid"]} for h in g["hits"]]
        return {"outcome": outcome, "text": _HF_OUTCOMES.get(outcome, outcome), "planned": g["drops"],
                "rows": g["rows"], "risk": g["risk"], "source": g["source"],
                "rtp_pct": table_view(g["rows"], tuple(g["mults"]))[1], "drops": drops,
                "total_bet": g["debits"], "total_paid": g["credits"], "house_net": g["debits"] - g["credits"],
                "best": g.get("best"), "players": self.players_summary(), "test": bool(g.get("test")), "currency": g["currency"]}

    def record(self, s: dict) -> None:
        st = self._st
        st["games"] += 1
        key = {"complete": "complete", "walked": "walked", "no_bets": "no_bets"}.get(s["outcome"], "stopped")
        st[key] += 1
        st["drops"] += len(s["drops"])
        st["busts"] += sum(1 for d in s["drops"] if mult_frac(d["mult"]) == 0)
        st["total_bet"] += s["total_bet"]
        st["total_paid"] += s["total_paid"]
        st["house_net"] += s["house_net"]

    # ---- views --------------------------------------------------------------------

    def _recent(self, limit: int = 14) -> list[dict]:
        """The last slot hits, newest first: this game's, then the finished games'."""
        out: list[dict] = []
        if self.g is not None:
            for h in reversed(self.g["hits"]):
                out.append({"slot": h["slot"], "rows": self.g["rows"], "mult": h["mult"]})
        for e in self.history:
            for d in reversed((e.get("result") or {}).get("drops") or []):
                if len(out) >= limit:
                    return out
                out.append({"slot": d.get("slot"), "rows": d.get("rows"), "mult": d.get("mult")})
        return out[:limit]

    def game_view(self, now: float) -> dict:
        g, cfg = self.g, self.cfg
        slots, rtp, edge = table_view(g["rows"], tuple(g["mults"]))
        fall = None
        if g["fall"] is not None and g["phase"] in ("dropping", "result", "over"):
            fall = {k: g["fall"][k] for k in ("drop", "path", "slot", "mult", "seed", "ms")}
        players = self.players_view()
        return {"id": g["id"], "test": g["test"], "phase": g["phase"], **self.timing(now),
                "drop": g["drop"], "drops": g["drops"], "rows": g["rows"], "risk": g["risk"], "source": g["source"],
                "slots": slots, "rtp_pct": rtp, "house_edge_pct": edge, "fall": fall,
                "hits": [{"drop": h["drop"], "slot": h["slot"], "mult": h["mult"]} for h in g["hits"]],
                "recent": self._recent(), "players": players, "on_the_line": sum(p["bet"] for p in players),
                "last": g["last"], "outcome": g.get("outcome"), "summary": g.get("summary"),
                "currency": g["currency"], "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"],
                "commands_text": cfg["commands_text"]}

    def idle_view(self) -> dict:
        cfg = self.cfg
        mults, source = resolve_table(cfg["rows"], cfg["risk"], cfg["multipliers"])
        slots, rtp, edge = table_view(cfg["rows"], tuple(mults))
        return {"rows": cfg["rows"], "risk": cfg["risk"], "source": source, "drops": cfg["drops"], "slots": slots,
                "rtp_pct": rtp, "house_edge_pct": edge, "recent": self._recent(), "currency": cfg["currency"]}

    def bets_payload(self) -> dict:
        cfg = self.cfg
        mults, source = resolve_table(cfg["rows"], cfg["risk"], cfg["multipliers"])
        rows = cfg["rows"]
        slots, rtp, edge = table_view(rows, tuple(mults))
        presets = {}
        for risk in RISKS:
            pm = preset(rows, risk)
            _s, prtp, pedge = table_view(rows, tuple(pm))
            presets[risk] = {"multipliers": pm, "rtp_pct": prtp, "house_edge_pct": pedge}
        notes = []
        if rtp > 100:
            notes.append("this table pays back more than 100%: the house loses on average")
        return {"ok": True, "game": self.key, "currency": cfg["currency"], "min_bet": cfg["min_bet"],
                "max_bet": cfg["max_bet"], "drops": cfg["drops"], "rows": rows, "risk": cfg["risk"],
                "source": source, "multipliers": mults, "rtp_pct": rtp, "house_edge_pct": edge,
                "slots": list(slots), "presets": presets, "notes": notes,
                "rules": [
                    f"A game is {cfg['drops']} drops. Each drop has a betting window, then ONE hex token falls "
                    f"through {rows} rows of hexagonal pegs into one of {rows + 1} slots.",
                    f"At every peg the server flips a fair coin (secrets.SystemRandom): left or right. The slot is "
                    f"the number of right bounces, so slot k has probability C({rows}, k) / 2^{rows} - the "
                    f"`slots` list has every slot's exact odds (ways of of).",
                    "Every player's stake on the drop is paid stake x the landing slot's multiplier, rounded "
                    "down to whole coins. x0 is a BUST: the stake is lost.",
                    f"RTP is the sum of probability x multiplier = {rtp}%, so the house edge is {edge}%. There is "
                    "no hidden setting: the table above IS the odds (the multipliers setting replaces it with "
                    f"your own list of exactly {rows + 1} numbers).",
                    "Every bet is against the bank: POST /bet debits, the landing credits (reason payout; "
                    "nothing on a bust). A drop's bet is its own: put coins down again for the next drop.",
                    "Bets only while bets are open (409 bets_closed otherwise). /remove takes back this window's "
                    "bet. No bets when the window closes: the game ends."]}

    def validate(self, params: dict) -> dict:
        amt, err = self._coins(params.get("amount")) if params.get("amount") not in (None, "") else (None, None)
        g = self.g
        mults, _source = self._table()
        rows = g["rows"] if g else self.cfg["rows"]
        out: dict[str, Any] = {"valid": amt is not None and err is None, "drop": g["drop"] if g else 1,
                               "amount": amt, "rows": rows,
                               "rtp_pct": table_view(rows, tuple(mults))[1]}
        if amt is None and err is None:
            out["valid"], out["error"] = False, "amount required"
        elif err:
            out["error"] = err
        else:
            out["pays"] = [{"slot": k, "mult": _num(m), "pays": pays(amt, m)} for k, m in enumerate(mults)]
        return out


HEXFALL = Hexfall()          # restores a running game from games_hexfall.json (settled by the plugin)


# --------------------------------------------------------------------------
# what the Games pages need to load this game (see core.register_game)
# --------------------------------------------------------------------------

OVERLAY = {
    "script": "hexfall.js",
    "stateful": True,
    "appearance": list(Hexfall.APPEARANCE),
    # mirrors the defaults of the game's config section, for a renderer that runs before its
    # first config message arrives
    "defaults": {
        "x": 50,
        "y": 50,
        "scale": 0.85,
        "theme": "coven",
        "title": "Hexfall",
        "show_rules": True,
        "show_players": True,
        "players_max": 8,
        "show_odds": True,
        "show_history": True,
        "sfx": True,
        "sfx_volume": 0.6,
        "hide_when_idle": True,
        "commands_text": "",
        "drops": 3,
        "rows": DEFAULT_ROWS,
        "risk": DEFAULT_RISK,
        "drop_seconds": 9,
        "result_seconds": 5,
        "summary_seconds": 10,
        "currency": "coins",
    },
}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])


@router.api_route("/api/hexfall/start", methods=["GET", "POST"])
async def api_hf_start(request: Request):
    return await _round_request(HEXFALL, request, HEXFALL.start_game)


@router.api_route("/api/hexfall/bet", methods=["GET", "POST"])
async def api_hf_bet(request: Request):
    return await _round_request(HEXFALL, request, HEXFALL.place)


@router.api_route("/api/hexfall/remove", methods=["GET", "POST"])
async def api_hf_remove(request: Request):
    return await _round_request(HEXFALL, request, HEXFALL.remove)


@router.api_route("/api/hexfall/next", methods=["GET", "POST"])
@router.api_route("/api/hexfall/drop", methods=["GET", "POST"])
async def api_hf_next(request: Request):
    return await _round_request(HEXFALL, request, lambda _p: HEXFALL.skip())


@router.get("/api/hexfall/table")
async def api_hf_table():
    return {"ok": True, **HEXFALL.state_view()}


@router.get("/api/hexfall/user/{name}")
async def api_hf_user(name: str):
    return {"ok": True, **HEXFALL.user_view(name)}


@router.get("/api/hexfall/ledger")
async def api_hf_ledger(since: str | None = None, limit: str | None = None):
    return _ledger_reply(since, limit, "hexfall")


API_DOC = {
    "about":    "one game at a time: betting -> dropping -> result -> ... -> over. A hex-themed Plinko: one token "
                "falls through rows of pegs (a fair coin at each one, server side) into a slot that pays stake x "
                "its multiplier. Bets are against the bank; docs/hexfall.md",
    "start":    "GET|POST /games/api/hexfall/start   {drops (1-10), rows (8-16), risk (low|medium|high), multipliers "
                "(rows+1 numbers: a list or \"25,10,2.5,0,...\"), seconds (first bet window), test} -> {started, "
                "rtp_pct, state}; 409 while a game runs; 400 for a bad risk or a multipliers list that does not "
                "have rows+1 numbers",
    "bet":      "GET|POST /games/api/hexfall/bet   {user, amount} -> {amount, total, drop, debits, player, state}; "
                "betting again adds to this drop's bet; 409 bets_closed outside the betting window",
    "remove":   "GET|POST /games/api/hexfall/remove   {user}: take back this window's bet (refund)",
    "next":     "GET|POST /games/api/hexfall/next   (alias /drop) end the current phase now",
    "table":    "GET /games/api/hexfall/table   the STATE (game: phase, drop, slots + odds, rtp_pct, players, fall, "
                "last, summary)",
    "user":     "GET /games/api/hexfall/user/{name}",
    "ledger":   "GET /games/api/hexfall/ledger?since=0   reasons: bet, add, payout, refund",
    "bets":     "GET /games/api/hexfall/bets   the table: every slot's multiplier, exact odds and RTP share, the "
                "RTP + house edge, the presets for the current rows, the rules",
    "stop":     "GET|POST /games/api/hexfall/stop   end the game: a drop already falling counts (its slot pays), "
                "open bets are refunded",
    "preview":  "GET|POST /games/api/hexfall/preview   {overrides:{appearance}, seconds (2-60, default 8)} | "
                "x=&y=&scale= shorthand: the Edit Mode editor's Test in OBS - on screen for `seconds` with that "
                "look (STATE.preview), never touches the game; /preview/clear ends it",
}
