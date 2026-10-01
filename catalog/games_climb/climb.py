"""Soul Climb - a damned soul tries to climb out of a hell pit (Games add-on).

Chat bets on HOW HIGH the soul gets. A bet is {amount, height}: if his best height reaches (or
passes) the guess the player is paid amount x multiplier(height), if he falls first the stake is
lost. Heights are whole "levels" 1..max_height; the top of the pit (max_height) is the escape.

The maths (everything below is exact integer / Fraction arithmetic, nothing is rounded early)
--------------------------------------------------------------------------------------------
The soul starts at level 0 and tries one level at a time. Trying to get from level i-1 to level i
he falls with chance q(i); the hazard rises with height (he tires, the wall gets worse):

    t = i / max_height                       relative height 0..1
    L = -ln(escape_pct / 100)                so that S(max_height) = escape_pct %
    Lambda(t) = L * (t + 0.9 t^2) / 1.9      cumulative hazard
    p(i) = exp(-(Lambda(i/H) - Lambda((i-1)/H)))      chance to make level i from level i-1

p(i) is turned into a whole number of parts per billion (fail[i] = round(1e9 * (1 - p(i)))) and
THAT is what the server rolls: it draws randrange(1e9) < fail[i] for level 1, 2, 3 ... with
secrets.SystemRandom until he falls or has made every level. So

    S(h) = prod_{i<=h} (1e9 - fail[i]) / 1e9     chance his best height is >= h   (exact)
    multiplier(h) = max(1.00, floor_to_cents((1 - house_edge) / S(h)))

and a winning bet pays floor(amount x multiplier(h)) coins (x multiplier in whole cents - the
multiplier shown is the multiplier paid). The 1.00 floor only matters for the first few levels,
where the fair multiplier would be under the stake: such a bet gives the stake back (the house
edge there is smaller than the setting, never negative: S(h) < 1 for every h >= 1).

The comedy is cosmetic
----------------------
build_script() turns the decided best height into a SCRIPT: a list of timed beats (level, event,
seed) that the overlay replays - route choices, rests, taunting demons, near falls (slips that
recover), bat attacks, lava geysers, a spiky dead end he backtracks from, a skeleton hand, a frayed
rope, and the final fall in one of several funny styles. None of it can change the outcome: no
beat ever goes above the best height, and the one that gets there is the last climb beat. The
script is built with random.Random(seed) from a fresh seed; the OUTCOME comes from SystemRandom
alone and is drawn before the script is made.
"""

from __future__ import annotations

import bisect
import functools
import math
import random
import re
import secrets
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

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
    _cut,
    _debits,
    _display_text,
    _ev,
    _flag,
    _ledger_reply,
    _pct_frac,
    _round_common,
    _round_players,
    _round_request,
    _strict_int,
)

# paths + limits that belong to this game
from hexcast_plugins.games.core import CONFIG_DIR

CLIMB_PATH = CONFIG_DIR / "games_climb.json"      # the running game + history (soul climb)
SOUL_NAME_MAX = 24
SOUL_NAMES_MAX = 16                # names kept from the soul_name setting / a /start
CL_HEIGHT_MIN = 10                 # max_height setting range
CL_HEIGHT_MAX = 300
CL_CLIMBS_MAX = 3                  # climbs (each with a betting window) in one game
CL_BETS_PER_PLAYER = 5             # bets (different heights) one player may have on one climb
CL_TAIL_S = 0.6                    # the climbing phase lasts the script + this
CL_SCRIPT_MAX_MS = 110_000         # a longer script is replayed faster (all of it, evenly)
CL_PPB = 10 ** 9                   # the hazard is rolled in parts per billion
CL_SKEW = 0.9                      # how much worse the wall gets with height (see the docstring)
CL_ESCAPE_RANGE = (0.1, 50.0)      # the escape_pct setting

CL_SOULS = ("Gary", "Kevin", "Brenda", "Steve", "Doug", "Trish", "Larry", "Maureen", "Chad", "Bob", "Karen",
            "Gerald", "Dennis", "Linda", "Barry", "Debbie", "Todd", "Nancy", "Walter", "Sheila")
CL_SKINS = 6                       # looks the overlay has (skin colour / gear): the soul carries indexes
CL_GEARS = 8

CL_OUTCOMES = {"complete": "Every climb played", "no_bets": "No bets - no climb",
               "stopped": "Game stopped - every open stake returned", "restart": "Settled after a restart",
               "error": "Settled after an error"}


# --------------------------------------------------------------------------
# the survival model (exact)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ClimbModel:
    """The pit for one (max_height, escape_pct): what the server rolls and what it pays."""
    height: int
    escape_pct: float
    fail: tuple[int, ...]            # fail[i-1]: parts per billion that he falls trying to make level i
    nums: tuple[int, ...]            # nums[h] = prod_{i<=h} (1e9 - fail[i-1]);  S(h) = nums[h] / 1e9**h

    def survival(self, h: int) -> Fraction:
        """S(h): the chance his best height is at least h (S(0) = 1)."""
        return Fraction(self.nums[h], CL_PPB ** h)

    def mult_cents(self, h: int, keep: Fraction) -> int:
        """The multiplier of height h in whole cents (x1.00 = 100): (1 - edge) / S(h), rounded
        down, never below the stake back."""
        cents = (100 * keep.numerator * CL_PPB ** h) // (keep.denominator * self.nums[h])
        return max(100, int(cents))

    def mult(self, h: int, keep: Fraction) -> float:
        return self.mult_cents(h, keep) / 100

    def chance(self, h: int) -> float:
        return float(self.survival(h))

    def table(self, keep: Fraction) -> list[dict]:
        out = []
        for h in range(1, self.height + 1):
            s = self.survival(h)
            out.append({"height": h, "chance": round(float(s), 6), "chance_pct": round(float(s) * 100, 4),
                        "mult": self.mult_cents(h, keep) / 100})
        return out

    def fall_probability(self, h: int) -> Fraction:
        """P(his best height is exactly h): S(h) - S(h+1) (S above the top is 0)."""
        nxt = self.survival(h + 1) if h < self.height else Fraction(0)
        return self.survival(h) - nxt


def _hazard(t: float, big_l: float) -> float:
    """The cumulative hazard Lambda(t), t = height / max_height."""
    return big_l * (t + CL_SKEW * t * t) / (1 + CL_SKEW)


@functools.lru_cache(maxsize=24)
def climb_model(height: int, escape_pct: float) -> ClimbModel:
    height = int(min(CL_HEIGHT_MAX, max(2, height)))
    esc = min(CL_ESCAPE_RANGE[1], max(CL_ESCAPE_RANGE[0], float(escape_pct)))
    big_l = -math.log(esc / 100.0)
    fail: list[int] = []
    nums = [1]
    prev = 0.0
    for i in range(1, height + 1):
        cur = _hazard(i / height, big_l)
        f = int(math.ceil(-math.expm1(-(cur - prev)) * CL_PPB - 1e-6))   # 1 - exp(-(Lambda(i) - Lambda(i-1))), rounded up
        prev = cur
        f = min(CL_PPB - 1, max(1, f))
        fail.append(f)
        nums.append(nums[-1] * (CL_PPB - f))
    return ClimbModel(height, esc, tuple(fail), tuple(nums))


def sample_reached(model: ClimbModel, rng: Any = None) -> int:
    """Roll the climb: his best height, 0..max_height. rng: anything with randrange() - the
    server passes secrets.SystemRandom (the default); tests pass a seeded one."""
    rng = _RNG if rng is None else rng
    for i, f in enumerate(model.fail, 1):
        if rng.randrange(CL_PPB) < f:
            return i - 1
    return model.height


def cl_keep(edge_pct: Any) -> Fraction:
    return 1 - _pct_frac(edge_pct)


def cl_pay(amount: int, cents: int, cap: int = 0) -> int:
    """What a winning bet of `amount` coins pays at a multiplier of `cents` (x1.00 = 100): whole
    coins, rounded down; with a cap (max_payout, 0 = none) at most the cap - but never less than
    the stake, a win does not lose money."""
    pay = amount * cents // 100
    return max(amount, min(pay, cap)) if cap else pay


def cl_ladder(height: int, escape_pct: float, edge_pct: float) -> list[dict]:
    """A handful of heights with their chance and multiplier (the payout ladder on the overlay)."""
    model = climb_model(height, escape_pct)
    keep = cl_keep(edge_pct)
    hs = sorted({max(1, min(model.height, int(round(model.height * f)))) for f in
                 (0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.8, 1.0)})
    return [{"height": h, "chance_pct": round(model.chance(h) * 100, 2), "mult": model.mult(h, keep)} for h in hs]


def parse_names(raw: Any) -> list[str]:
    """The soul names of a soul_name setting / a /start: comma (or ; |) separated, each cut to
    SOUL_NAME_MAX characters, at most SOUL_NAMES_MAX."""
    if raw is None or isinstance(raw, (dict, list, bool)):
        return []
    out: list[str] = []
    for part in re.split(r"[,;|\n]", str(raw)):
        n = _display_text(part, SOUL_NAME_MAX)
        if n and n not in out:
            out.append(n)
        if len(out) >= SOUL_NAMES_MAX:
            break
    return out


# --------------------------------------------------------------------------
# the script (cosmetic: replayed by the overlay)
# --------------------------------------------------------------------------
# beats[i] = {t, d, ev, lv, to, s, ...}: starts at t ms, lasts d ms, the soul goes from level lv to
# level `to`, s = a seed for the beat's own randomness. Beats follow each other: beats[0].t = 0,
# beats[i].t = beats[i-1].t + beats[i-1].d, beats[i].lv = beats[i-1].to. Events:
#
#   ready   (0 -> 0)  at the foot of the wall: a stretch, a gulp, a look up
#   climb   (a -> a+1) one level; slower when a bet's flag is near, quick when they are far
#   cheer   (a -> a)  just landed on a flag's level (flags: [heights])
#   idle    (a -> a)  kind: shrug | wipe | pant | gulp | wave | flex
#   rest    (a -> a)  kind: sit | lean - catches his breath
#   taunt   (a -> a)  kind 0-2 (which demon), side -1 | 1 - a demon mocks him
#   slip    (a -> a-k) k = 1..3: a loose rock gives, he slides and catches himself (a NEAR fall)
#   bat     (a -> a)  n bats attack, he fights them off
#   geyser  (a -> a)  side: a lava vent erupts next to him
#   deadend (a -> a)  peak = a + j: a wrong turn up a spiky dead end, then backtracks (peak <= best height)
#   grab    (a -> a)  a skeleton hand grabs his ankle, he kicks it off
#   fray    (a -> a)  a strand of his rope snaps - it holds (only on a rope stretch)
#   fatal   (M -> M) cause: rock | hand | rope | chain | rib | bat | geyser | demon | tired
#   fall    (M -> 0) style: bonk | yelp | cauldron | flick | grinder | boing | umbrella (n = bonks)
#   escape  (H -> H) over the lip, victory pose
#
# No beat has a level above the best height M ("max"); the last climb beat reaches exactly M.

CL_ROUTES = ("ledge", "chimney", "chains", "rope", "ribs")
CL_CAUSES = ("rock", "hand", "rope", "chain", "rib", "bat", "geyser", "demon", "tired")
CL_CAUSE_WEIGHTS = (3, 2.5, 2, 1.5, 1.5, 2, 2, 2, 1.5)
CL_CAUSE_ROUTE = {"rock": "ledge", "rope": "rope", "chain": "chains", "rib": "ribs"}     # the stretch he falls from
CL_STYLES = ("bonk", "yelp", "cauldron", "flick", "grinder", "boing", "umbrella")
CL_STYLE_WEIGHTS = (3, 2.5, 2.5, 2, 2, 2, 1.5)
CL_IDLE_KINDS = ("shrug", "wipe", "pant", "gulp", "wave", "flex")
CL_EVENTS = ("rest", "idle", "taunt", "slip", "bat", "geyser", "deadend", "grab", "fray")
CL_EVENT_WEIGHTS = {"rest": 3, "idle": 3, "taunt": 3, "slip": 3, "bat": 1.5, "geyser": 2, "deadend": 1.5,
                    "grab": 2, "fray": 1.5}
CL_TENSE_WEIGHTS = {"slip": 4, "taunt": 2, "bat": 2, "grab": 2, "geyser": 2, "deadend": 0.7}   # just before a flag


def _plan_routes(rng: random.Random, top: int, reached: int, cause: str | None) -> list[dict]:
    """The stretches of wall (left ledge, chimney, chains, rope, rib-bone ladder) covering levels
    0..top: [{from, to, kind}]. The stretch he falls from suits the cause (a rope for a snapped rope)."""
    segs: list[dict] = []
    a, prev = 0, None
    while a < top:
        b = min(top, a + rng.randint(7, 14))
        if top - b < 4:
            b = top
        kind = "ledge" if not segs else rng.choice([k for k in CL_ROUTES if k != prev])
        segs.append({"from": a, "to": b, "kind": kind})
        prev, a = kind, b
    want = CL_CAUSE_ROUTE.get(cause or "")
    if want:
        for s in segs:
            if s["from"] <= reached < s["to"] or (reached >= top and s is segs[-1]):
                s["kind"] = want
                break
    return segs


def route_at(routes: list[dict], level: int) -> str:
    """The kind of wall he climbs on going from `level` to level+1."""
    for s in routes:
        if s["from"] <= level < s["to"]:
            return s["kind"]
    return routes[-1]["kind"] if routes else "ledge"


def _wchoice(rng: random.Random, weights: dict[str, float], allowed: Iterable[str]) -> str:
    keys = [k for k in weights if k in allowed]
    return rng.choices(keys, [weights[k] for k in keys])[0]


def _plan_events(rng: random.Random, reached: int, bets: list[int], routes: list[dict]) -> dict[int, str]:
    """Which level gets which cosmetic event (about one in ten levels), plus a bit of drama just
    below some flags he is about to pass."""
    slots: dict[int, str] = {}
    bag: list[str] = []

    def draw(a: int, tense: bool) -> str:
        allowed = set(CL_TENSE_WEIGHTS if tense else CL_EVENTS)
        if route_at(routes, a) != "rope":
            allowed.discard("fray")
        if reached - a < 3:
            allowed.discard("deadend")
        if a < 2:
            allowed.discard("slip")
        # not the same thing twice in a row if there is a choice
        pool = {k for k in allowed if k not in bag[-1:]} or allowed
        kind = _wchoice(rng, CL_TENSE_WEIGHTS if tense else CL_EVENT_WEIGHTS, pool)
        bag.append(kind)
        return kind

    a = rng.randint(2, 5)
    while a <= reached - 2:
        slots[a] = draw(a, False)
        a += rng.randint(5, 10)
    for b in bets:
        if b <= reached and rng.random() < 0.5:
            at = b - rng.randint(1, 3)
            if at >= 2 and all(abs(at - s) >= 3 for s in slots):
                slots[at] = draw(at, True)
    return dict(sorted(slots.items()))


def _fall_plan(style: str, reached: int, rng: random.Random) -> tuple[float, int]:
    """(duration ms, bonks) of the final fall: the long ones are compressed (the camera races down)."""
    drop = min(reached, 60)
    bonks = 0
    if style == "bonk":
        bonks = max(1, min(8, 1 + reached // 8))
        d = 600 + 430 * bonks + 1300
    elif style == "yelp":
        d = 1100 + drop * 40 + 1500
    elif style == "cauldron":
        d = 1000 + drop * 30 + 2600
    elif style == "flick":
        d = 1000 + drop * 30 + 2900
    elif style == "grinder":
        d = 1000 + drop * 26 + 3800
    elif style == "boing":
        d = 1000 + drop * 28 + 3400
    else:                                   # umbrella: a slow float down
        d = 1500 + drop * 65 + 2600
    return d + rng.uniform(0, 200), bonks


def build_script(height: int, reached: int, bet_heights: Iterable[int], seed: int, speed: float = 1.0) -> dict:
    """The climb as a script for the overlay (see the table above). `reached` is the OUTCOME,
    already decided; this only dresses it. Deterministic for a given seed."""
    rng = random.Random(seed)
    big_h = int(height)
    best = max(0, min(big_h, int(reached)))
    escaped = best >= big_h
    bets = sorted({int(h) for h in bet_heights if 1 <= int(h) <= big_h})
    bet_set = set(bets)
    cause = None if escaped else rng.choices(CL_CAUSES, CL_CAUSE_WEIGHTS)[0]
    style = None if escaped else rng.choices(CL_STYLES, CL_STYLE_WEIGHTS)[0]
    top = big_h if escaped else min(big_h, best + 14)
    routes = _plan_routes(rng, top, best, cause)
    slots = _plan_events(rng, best, bets, routes)
    beats: list[dict] = []

    def add(ev: str, lv: int, to: int, d: float, **extra: Any) -> None:
        beats.append({"t": 0, "d": d, "ev": ev, "lv": lv, "to": to, "s": rng.getrandbits(31), **extra})

    def step_ms(a: int) -> float:
        """Climbing a -> a+1: slow near the flags ahead, quick when they are far (or there are none)."""
        i = bisect.bisect_left(bets, a + 1)
        if i >= len(bets):
            pace = 0.85
        else:
            dist = bets[i] - (a + 1)
            pace = 0.8 if dist >= 12 else 0.8 + 1.8 * math.exp(-dist / 2.5)
        return rng.uniform(300, 400) * pace

    add("ready", 0, 0, rng.uniform(1200, 1700))
    a = top_seen = 0
    done: set[int] = set()
    while a < best:
        ev = slots.get(a)
        if ev is not None and a not in done:
            done.add(a)
            if ev == "rest":
                add("rest", a, a, rng.uniform(1500, 2600), kind=rng.choice(("sit", "lean")))
            elif ev == "idle":
                add("idle", a, a, rng.uniform(650, 1100), kind=rng.choice(CL_IDLE_KINDS))
            elif ev == "taunt":
                add("taunt", a, a, rng.uniform(1700, 2400), kind=rng.randrange(3), side=rng.choice((-1, 1)))
            elif ev == "slip":
                k = rng.randint(1, min(3, a))
                add("slip", a, a - k, 900 + 170 * k + rng.uniform(0, 250), k=k)
                a -= k
            elif ev == "bat":
                add("bat", a, a, rng.uniform(1800, 2500), n=rng.randint(2, 5))
            elif ev == "geyser":
                add("geyser", a, a, rng.uniform(1300, 1800), side=rng.choice((-1, 1)))
            elif ev == "deadend":
                j = min(rng.randint(2, 4), best - a)
                if j >= 2:
                    add("deadend", a, a, 2400 + 380 * j + rng.uniform(0, 400), peak=a + j, side=rng.choice((-1, 1)))
                    top_seen = max(top_seen, a + j)
                else:
                    add("rest", a, a, rng.uniform(1500, 2400), kind="sit")
            elif ev == "grab":
                add("grab", a, a, rng.uniform(1900, 2600))
            elif ev == "fray":
                add("fray", a, a, rng.uniform(1500, 2100))
            continue
        add("climb", a, a + 1, step_ms(a))
        a += 1
        if a > top_seen:
            top_seen = a
            if a in bet_set:
                add("cheer", a, a, rng.uniform(600, 800), flags=[a])
    if escaped:
        add("escape", big_h, big_h, rng.uniform(3600, 4200))
    else:
        add("fatal", best, best, rng.uniform(1000, 1500), cause=cause)
        d, bonks = _fall_plan(style, best, rng)
        extra = {"n": bonks} if bonks else {}
        add("fall", best, 0, d, style=style, **extra)

    # time: the speed setting, and a cap on a very long climb (everything is replayed evenly faster)
    total = sum(b["d"] for b in beats)
    scale = 1.0 / max(0.25, float(speed or 1.0))
    if total * scale > CL_SCRIPT_MAX_MS:
        scale = CL_SCRIPT_MAX_MS / total
    t = 0
    for b in beats:
        d = max(1, int(round(b["d"] * scale)))
        b["t"], b["d"] = t, d
        t += d
    return {"v": 1, "seed": int(seed), "height": big_h, "max": best, "escaped": escaped, "cause": cause,
            "style": style, "routes": routes, "beats": beats, "duration_ms": t}


def script_peak(script: dict) -> int:
    """The highest level any beat of the script mentions (it must equal script["max"])."""
    top = 0
    for b in script["beats"]:
        top = max(top, b["lv"], b["to"], b.get("peak", 0))
    return top


# --------------------------------------------------------------------------
# the game
# --------------------------------------------------------------------------
# One game = `climbs` (1-3) climbs. Each: a betting window -> the climb (the script's length) -> the
# result card -> the next window, then the game-over card. Bets are against the bank: a stake is a
# ledger DEBIT when it goes down, a win is a ledger CREDIT when the climb ends (a lost stake is just
# gone), taking a bet back (/remove) or stopping a game in its window is a refund. The climb's
# outcome is drawn when the betting window closes and its script is sent to the overlay; bets are
# closed by then, so knowing it early is worth nothing. A game stopped (or interrupted by a restart)
# while the soul is climbing counts: the outcome is decided, so it is settled; a window still open is
# refunded.

_CL_HEIGHT_WORDS = re.compile(r"^(?:l(?:v|vl|evel)?|h(?:eight)?)?\s*(\d{1,4})$", re.I)


class SoulClimb(RoundGame):
    key = "climb"
    title = "Soul Climb"
    id_prefix = "cl"
    PHASES = ("betting", "climbing", "result", "over")
    STAT_KEYS = ("games", "climbs", "falls", "escapes", "no_bets", "stopped", "total_height", "best_height",
                 "total_bet", "total_paid", "house_net")

    DEFAULTS: dict[str, Any] = {
        # placement: scene centre in % of the 1920x1080 stage; scale x the 880x960 scene
        "x": 50, "y": 50, "scale": 1.0,
        "theme": "inferno",             # inferno | abyss | sulfur
        "title": "Soul Climb",          # the title on the overlay (your branding)
        "show_rules": True,             # rules box while bets are open
        "show_players": True, "players_max": 8,
        "show_odds": True,              # the payout ladder
        "sfx": True, "sfx_volume": 0.6,
        "hide_when_idle": True,
        "commands_text": "",            # e.g. "!climb 100 40" (your bot's command)
        "climbs": 1,                    # climbs per game (each with its own betting window)
        "max_height": 100,              # levels; the top of the pit is the escape
        "escape_pct": 2,                # chance the soul makes it all the way out
        "house_edge_pct": 5,
        "open_bet_seconds": 30,         # the first betting window
        "between_seconds": 20,          # the window before every later climb
        "result_seconds": 8,            # the result card (best height, winners)
        "summary_seconds": 12,          # game over card
        "climb_speed": 1.0,             # 0.5 = twice as slow, 2 = twice as fast
        "currency": "coins",            # your bot's coin name, shown after amounts
        "min_bet": 1, "max_bet": 100000,  # per player per climb (all their bets together); 0 = no max
        "max_payout": 0,                # one winning bet pays at most this; 0 = no cap
        "soul_name": "",                # name(s) of the soul, comma separated: one per climb; empty = built-in names
        "climb_clip": "", "fall_clip": "", "escape_clip": "",   # soundboard clips
    }
    SCHEMA: dict[str, tuple] = {
        "x": ("num", 0, 100), "y": ("num", 0, 100), "scale": ("num", 0.2, 5),
        "theme": ("enum", ("inferno", "abyss", "sulfur")), "title": ("name", ROUND_TITLE_MAX),
        "show_rules": ("bool",), "show_players": ("bool",), "players_max": ("int", 1, 20),
        "show_odds": ("bool",), "sfx": ("bool",), "sfx_volume": ("num", 0, 1),
        "hide_when_idle": ("bool",), "commands_text": ("str", COMMANDS_TEXT_MAX),
        "climbs": ("int", 1, CL_CLIMBS_MAX), "max_height": ("int", CL_HEIGHT_MIN, CL_HEIGHT_MAX),
        "escape_pct": ("num", CL_ESCAPE_RANGE[0], CL_ESCAPE_RANGE[1]), "house_edge_pct": ("num", 0, 25),
        "open_bet_seconds": ("num", 5, 300), "between_seconds": ("num", 5, 300),
        "result_seconds": ("num", 2, 30), "summary_seconds": ("num", 4, 60), "climb_speed": ("num", 0.5, 2),
        "currency": ("name", 24), "min_bet": ("int", 1, 1e9), "max_bet": ("int", 0, 1e12),
        "max_payout": ("int", 0, 1e12), "soul_name": ("str", 120),
        "climb_clip": ("str", 200), "fall_clip": ("str", 200), "escape_clip": ("str", 200),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "title", "show_rules", "show_players", "players_max", "show_odds",
                  "sfx", "sfx_volume")

    def default_path(self) -> Path:
        return CLIMB_PATH

    def validate_config(self, raw: Any) -> dict:
        out = super().validate_config(raw)
        if out["max_bet"] and out["max_bet"] < out["min_bet"]:
            out["max_bet"] = 0
        return out

    # ---- saved game -----------------------------------------------------------

    def clean_game(self, raw: Any) -> dict | None:
        g = _round_common(raw, self.PHASES)
        if g is None:
            return None
        height = min(CL_HEIGHT_MAX, max(2, _strict_int(raw.get("height")) or 100))
        climbs = min(CL_CLIMBS_MAX, max(1, _strict_int(raw.get("climbs")) or 1))

        def bets(v):
            if not isinstance(v, list):
                return None
            out = []
            for b in v[:CL_BETS_PER_PLAYER]:
                if isinstance(b, dict):
                    h, a = _strict_int(b.get("height")), _strict_int(b.get("amount"))
                    if h and a and a > 0 and 1 <= h <= height:
                        out.append({"height": h, "amount": a})
            return out or None

        def soul(v):
            v = v if isinstance(v, dict) else {}
            return {"name": _display_text(v.get("name"), SOUL_NAME_MAX) or "Gary",
                    "seed": (_strict_int(v.get("seed")) or 0) & 0x7FFFFFFF,
                    "skin": (_strict_int(v.get("skin")) or 0) % CL_SKINS,
                    "gear": (_strict_int(v.get("gear")) or 0) % CL_GEARS}

        script = raw.get("script") if isinstance(raw.get("script"), dict) else None
        if script is not None and not (isinstance(script.get("beats"), list) and len(script["beats"]) <= 4000
                                       and _strict_int(script.get("max")) is not None
                                       and _strict_int(script.get("duration_ms")) is not None):
            script = None
        names = [n for n in (raw.get("names") or []) if isinstance(n, str)][:SOUL_NAMES_MAX]
        g.update({
            "climbs": climbs, "climb": min(climbs, max(1, _strict_int(raw.get("climb")) or 1)),
            "height": height, "speed": min(2.0, max(0.5, _as_float(raw.get("speed")) or 1.0)),
            "edge_pct": _as_float(raw.get("edge_pct")) or 0.0,
            "escape_pct": min(CL_ESCAPE_RANGE[1], max(CL_ESCAPE_RANGE[0], _as_float(raw.get("escape_pct")) or 2.0)),
            "max_payout": max(0, _strict_int(raw.get("max_payout")) or 0),
            "names": [_cut(n, SOUL_NAME_MAX) for n in names], "soul": soul(raw.get("soul")),
            "bets": _round_players(raw.get("bets"), bets), "script": script,
            "marks": [m for m in raw.get("marks") or [] if isinstance(m, dict)][:200],
            "resolved": bool(raw.get("resolved")),
            "results": [r for r in raw.get("results") or [] if isinstance(r, dict)][:CL_CLIMBS_MAX],
        })
        return g

    # ---- the pit ---------------------------------------------------------------

    def _model(self) -> ClimbModel:
        g = self.g
        return climb_model(g["height"], g["escape_pct"])

    def _keep(self) -> Fraction:
        return cl_keep(self.g["edge_pct"])

    @staticmethod
    def _bid(g: dict, h: int) -> str:
        return f"{g['id']}/c{g['climb']}/h{h}"

    @staticmethod
    def _label(g: dict, h: int, extra: str = "") -> str:
        return f"Height {h} · climb {g['climb']}" + (f" · {extra}" if extra else "")

    def _new_soul(self) -> dict:
        names = self.g.get("names") or list(CL_SOULS)
        return {"name": _RNG.choice(names), "seed": secrets.randbits(31), "skin": _RNG.randrange(CL_SKINS),
                "gear": _RNG.randrange(CL_GEARS)}

    def _height(self, params: dict) -> tuple[int | None, str | None]:
        top = self.g["height"] if self.g else self.cfg["max_height"]
        raw = next((params[k] for k in ("height", "level", "guess", "bet") if params.get(k) not in (None, "")), None)
        if raw is None:
            return None, f"height required (1-{top})"
        if isinstance(raw, bool) or isinstance(raw, (dict, list)):
            return None, f"height must be a whole number from 1 to {top}"
        if isinstance(raw, str):
            m = _CL_HEIGHT_WORDS.match(raw.strip())
            f = float(m.group(1)) if m else _as_float(raw)
        else:
            f = _as_float(raw)
        if f is None or not f.is_integer():
            return None, f"height must be a whole number from 1 to {top}"
        if not 1 <= f <= top:
            return None, f"height must be from 1 to {top}"
        return int(f), None

    # ---- money ----------------------------------------------------------------

    def _mine(self, user: str) -> list[dict]:
        return self.g["bets"].get(user) or []

    def _bet_view(self, model: ClimbModel, keep: Fraction, b: dict) -> dict:
        cents = model.mult_cents(b["height"], keep)
        return {"height": b["height"], "amount": b["amount"], "mult": cents / 100,
                "pays": cl_pay(b["amount"], cents, self.g.get("max_payout") or 0)}

    def _player(self, user: str) -> dict:
        model, keep = self._model(), self._keep()
        bets = [self._bet_view(model, keep, b) for b in sorted(self._mine(user), key=lambda b: b["height"])]
        return {"user": user, "stake": sum(b["amount"] for b in bets), "bets": bets}

    def players_view(self) -> list[dict]:
        rows = [self._player(u) for u in self.g["bets"]]
        rows.sort(key=lambda p: (-p["stake"], p["user"].lower()))
        return rows

    def markers(self) -> list[dict]:
        """The bets grouped by height - the flags on the wall."""
        model, keep = self._model(), self._keep()
        by: dict[int, dict] = {}
        for u, bs in self.g["bets"].items():
            for b in bs:
                m = by.setdefault(b["height"], {"height": b["height"], "total": 0, "users": []})
                m["total"] += b["amount"]
                m["users"].append({"user": u, "amount": b["amount"]})
        rows = sorted(by.values(), key=lambda m: m["height"])
        for m in rows:
            m["mult"] = model.mult(m["height"], keep)
            m["chance"] = round(model.chance(m["height"]), 6)
            m["users"].sort(key=lambda x: (-x["amount"], x["user"].lower()))
        return rows

    def table_bets(self) -> list[dict]:
        if self.g is None:
            return []
        return [{"user": p["user"], "amount": p["stake"]} for p in self.players_view()]

    # ---- API actions -------------------------------------------------------------

    def start_game(self, params: dict) -> tuple[int, dict]:
        if self.g is not None:
            return 409, {"error": "a game is already running", "phase": self.phase}
        cfg = self.cfg
        n = _as_float(params.get("climbs"))
        climbs = int(min(CL_CLIMBS_MAX, max(1, n))) if n is not None else cfg["climbs"]
        names = parse_names(params.get("soul", params.get("name"))) or parse_names(cfg["soul_name"]) or list(CL_SOULS)
        now = time.time()
        self.g = {
            "id": f"cl-{secrets.token_hex(4)}", "test": _flag(params.get("test")), "phase": "betting",
            "started_at": round(now, 3), "phase_at": round(now, 3), "ends_at": None,
            "climbs": climbs, "climb": 1, "height": cfg["max_height"], "speed": cfg["climb_speed"],
            "edge_pct": cfg["house_edge_pct"], "escape_pct": cfg["escape_pct"], "max_payout": cfg["max_payout"],
            "names": names, "soul": None, "bets": {}, "script": None, "marks": [], "resolved": False, "results": [],
            "debits": 0, "credits": 0, "totals": {}, "log": [], "ever_bet": False,
            "outcome": None, "summary": None, "last": None, "currency": cfg["currency"],
        }
        self.g["soul"] = self._new_soul()
        self.hidden = False
        secs = _as_float(params.get("seconds"))
        secs = min(300.0, max(5.0, secs)) if secs is not None else float(cfg["open_bet_seconds"])
        self._set_phase("betting", secs)
        self.save()
        return 200, {"started": self.g["id"]}

    def place(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        h, err = self._height(params)
        if err:
            return 400, {"error": err}
        if user not in g["bets"] and len(g["bets"]) >= ROUND_PLAYERS_MAX:
            return 400, {"error": f"the game is full ({ROUND_PLAYERS_MAX} players)"}
        mine = g["bets"].get(user) or []
        cur = next((b for b in mine if b["height"] == h), None)
        if cur is None and len(mine) >= CL_BETS_PER_PLAYER:
            hs = ", ".join(str(b["height"]) for b in sorted(mine, key=lambda b: b["height"]))
            return 400, {"error": f"at most {CL_BETS_PER_PLAYER} bets per player per climb (heights {hs}) - "
                                  f"bet on one of those again, or take one back"}
        amt, err = self._coins(params.get("amount"), sum(b["amount"] for b in mine))
        if err:
            return 400, {"error": err}
        if cur is not None:
            cur["amount"] += amt
        else:
            mine.append({"height": h, "amount": amt})
            g["bets"][user] = mine
        g["ever_bet"] = True
        logged = self._persist([_ev("debit", user, amt, "add" if cur is not None else "bet", self._bid(g, h),
                                    self._label(g, h), g["id"])])
        bet = next(b for b in self._player(user)["bets"] if b["height"] == h)
        return 200, {"height": h, "side": f"level {h}", "amount": amt, "total": bet["amount"], "mult": bet["mult"],
                     "pays": bet["pays"], "debits": _debits(logged), "player": self._player(user)}

    def remove(self, params: dict) -> tuple[int, dict]:
        """Take back what went down in THIS betting window (all of a player's bets, or one height)."""
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        h = None
        if any(params.get(k) not in (None, "") for k in ("height", "level", "guess", "bet")):
            h, err = self._height(params)
            if err:
                return 400, {"error": err}
        mine = self._mine(user)
        take = [b for b in mine if h is None or b["height"] == h]
        if not take:
            what = f"on height {h}" if h else "in this climb"
            return 400, {"error": f"@{user} has nothing {what} to take back"}
        keep = [b for b in mine if b not in take]
        if keep:
            g["bets"][user] = keep
        else:
            g["bets"].pop(user, None)
        events = [_ev("credit", user, b["amount"], "refund", self._bid(g, b["height"]),
                      self._label(g, b["height"], "taken back"), g["id"]) for b in take]
        logged = self._persist(events)
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
            self._climb()
        elif ph == "climbing":
            self._land()
        elif ph == "result":
            self._after_result()
        else:
            self._to_idle()

    def _climb(self) -> None:
        """The window closed: decide the climb (SystemRandom), dress it as a script, send the soul up."""
        g = self.g
        if not g["bets"]:
            self._finish("complete" if g["results"] else "no_bets")
            return
        reached = sample_reached(self._model())
        heights = sorted({b["height"] for bs in g["bets"].values() for b in bs})
        script = build_script(g["height"], reached, heights, secrets.randbits(31), g.get("speed") or 1.0)
        g["script"], g["resolved"], g["marks"] = script, False, self.markers()
        self._set_phase("climbing", script["duration_ms"] / 1000.0 + CL_TAIL_S)
        self._clip("climb_clip")
        self.save()

    def _resolve(self) -> list[dict]:
        """Settle the climb (once): every bet at or below his best height is paid, the rest is lost."""
        g = self.g
        script = g.get("script")
        if script is None or g["resolved"]:
            return []
        g["resolved"] = True
        best, model, keep = script["max"], self._model(), self._keep()
        cap = g.get("max_payout") or 0
        events, winners, losers = [], [], []
        for u, bs in g["bets"].items():
            for b in sorted(bs, key=lambda b: b["height"]):
                if b["height"] <= best:
                    cents = model.mult_cents(b["height"], keep)
                    pay = cl_pay(b["amount"], cents, cap)
                    events.append(_ev("credit", u, pay, "win", self._bid(g, b["height"]),
                                      self._label(g, b["height"], f"x{cents / 100:.2f}"), g["id"]))
                    winners.append({"user": u, "height": b["height"], "amount": b["amount"], "mult": cents / 100,
                                    "pays": pay})
                else:
                    losers.append({"user": u, "height": b["height"], "amount": b["amount"]})
        events = [e for e in events if e["amount"] > 0]
        g["bets"] = {}
        winners.sort(key=lambda w: (-w["pays"], w["user"].lower()))
        losers.sort(key=lambda w: (-w["amount"], w["user"].lower()))
        res = {"climb": g["climb"], "max": best, "escaped": bool(script["escaped"]), "cause": script.get("cause"),
               "style": script.get("style"), "soul": g["soul"]["name"]}
        g["results"].append(res)
        g["last"] = {**res, "winners": winners, "losers": losers}
        return events

    def _land(self) -> None:
        g = self.g
        events = self._resolve()
        self._set_phase("result", float(self.cfg["result_seconds"]))
        self._persist(events)
        self._clip("escape_clip" if g["last"] and g["last"]["escaped"] else "fall_clip")

    def _after_result(self) -> None:
        g = self.g
        if g["climb"] >= g["climbs"]:
            self._finish("complete")
            return
        g["climb"] += 1
        g["script"], g["resolved"], g["marks"], g["bets"] = None, False, [], {}
        g["soul"] = self._new_soul()
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
        if g["phase"] == "climbing" and g.get("script") is not None:
            events += self._resolve()                 # the climb was decided when the window closed: it counts
        for u, bs in list(g["bets"].items()):         # a window still open (or a climb with no script): give it back
            for b in bs:
                events.append(_ev("credit", u, b["amount"], "refund", self._bid(g, b["height"]),
                                  self._label(g, b["height"], "game stopped"), g["id"]))
        g["bets"] = {}
        return events

    def summary(self, outcome: str) -> dict:
        g = self.g
        res = [dict(r) for r in g["results"]]
        best = max([r["max"] for r in res] or [0])
        text = CL_OUTCOMES.get(outcome, outcome)
        if outcome == "complete" and res:
            if len(res) == 1:
                text = (f"{res[0]['soul']} escaped the pit!" if res[0]["escaped"]
                        else f"{res[0]['soul']} reached level {res[0]['max']}")
            else:
                out = sum(1 for r in res if r["escaped"])
                text = f"{len(res)} climbs, best level {best}" + (f" ({out} escaped)" if out else "")
        return {"outcome": outcome, "text": text, "soul": dict(g["soul"] or {}),
                "climbs": g["climbs"], "played": len(res), "results": res,
                "best": best, "height": g["height"],
                "total_bet": g["debits"], "total_paid": g["credits"], "house_net": g["debits"] - g["credits"],
                "players": self.players_summary(), "test": bool(g.get("test")), "currency": g["currency"]}

    def record(self, s: dict) -> None:
        st = self._st
        st["games"] += 1
        res = s.get("results") or []
        st["climbs"] += len(res)
        st["escapes"] += sum(1 for r in res if r.get("escaped"))
        st["falls"] += sum(1 for r in res if not r.get("escaped"))
        st["total_height"] += sum(int(r.get("max") or 0) for r in res)
        st["best_height"] = max(int(st["best_height"] or 0), int(s.get("best") or 0))
        if s["outcome"] == "no_bets":
            st["no_bets"] += 1
        elif s["outcome"] != "complete":
            st["stopped"] += 1
        st["total_bet"] += s["total_bet"]
        st["total_paid"] += s["total_paid"]
        st["house_net"] += s["house_net"]

    # ---- views --------------------------------------------------------------------

    def game_view(self, now: float) -> dict:
        g, cfg = self.g, self.cfg
        live = g["phase"] == "betting"
        players = self.players_view()
        script = g["script"] if g["phase"] in ("climbing", "result", "over") else None
        return {"id": g["id"], "test": g["test"], "phase": g["phase"], **self.timing(now),
                "climb": g["climb"], "climbs": g["climbs"], "height": g["height"], "soul": dict(g["soul"]),
                "edge_pct": g["edge_pct"], "escape_pct": g["escape_pct"], "script": script,
                "markers": self.markers() if live else g["marks"],
                "players": players, "at_risk": sum(p["stake"] for p in players),
                "odds": cl_ladder(g["height"], g["escape_pct"], g["edge_pct"]),
                "last": g["last"], "results": list(g["results"]), "outcome": g.get("outcome"),
                "summary": g.get("summary"), "currency": g["currency"], "min_bet": cfg["min_bet"],
                "max_bet": cfg["max_bet"], "max_bets": CL_BETS_PER_PLAYER, "commands_text": cfg["commands_text"]}

    def idle_view(self) -> dict:
        cfg = self.cfg
        names = parse_names(cfg["soul_name"]) or list(CL_SOULS)
        return {"height": cfg["max_height"], "climbs": cfg["climbs"], "soul": {"name": names[0]},
                "odds": cl_ladder(cfg["max_height"], cfg["escape_pct"], cfg["house_edge_pct"]),
                "currency": cfg["currency"]}

    def bets_payload(self) -> dict:
        cfg = self.cfg
        top = cfg["max_height"]
        model = climb_model(top, cfg["escape_pct"])
        keep = cl_keep(cfg["house_edge_pct"])
        edge = cfg["house_edge_pct"]
        return {"ok": True, "game": self.key, "currency": cfg["currency"], "min_bet": cfg["min_bet"],
                "max_bet": cfg["max_bet"], "max_height": top, "escape_pct": cfg["escape_pct"],
                "house_edge_pct": edge, "climbs": cfg["climbs"], "max_bets_per_player": CL_BETS_PER_PLAYER,
                "model": {"hazard": "Lambda(t) = L (t + 0.9 t^2) / 1.9, t = height / max_height, "
                                    "L = -ln(escape_pct / 100)",
                          "level_fall_chance": "1 - exp(-(Lambda(i / H) - Lambda((i - 1) / H))), rolled in parts per "
                                               "billion for level 1, 2, 3 ... until he falls",
                          "chance": "S(h) = chance his best height is at least h",
                          "multiplier": "max(1.00, floor_to_cents((1 - house_edge) / S(h))); a win pays "
                                        "floor(amount x multiplier) coins"},
                "table": model.table(keep),
                "rules": [
                    f"Bet on HOW HIGH the soul climbs: POST /bet {{user, amount, height}}, height 1-{top}. If his best "
                    f"height reaches (or passes) your height you are paid amount x the multiplier of that height, "
                    f"rounded down to whole coins; if he falls first the stake is lost. Height {top} is the escape "
                    f"out of the pit: the jackpot.",
                    f"Every level has a fall chance that rises with height. S(h) in the table is the chance he gets "
                    f"at least that high; the multiplier is (1 - {edge}% edge) / S(h) rounded down to 2 decimals, "
                    f"never below x1.00 (the stake back).",
                    f"Up to {CL_BETS_PER_PLAYER} bets per player per climb, at different heights (betting a height "
                    f"again adds to it).",
                    "Every bet is against the bank: POST /bet debits, wins credit (the ledger). A lost stake is "
                    "just gone.",
                    "Bets only while bets are open (409 bets_closed otherwise). /remove takes back what went down "
                    "in the current window (refund).",
                    "No bets when the window closes: the game ends. A game has up to "
                    f"{CL_CLIMBS_MAX} climbs, each with its own betting window."]}

    def validate(self, params: dict) -> dict:
        h, herr = self._height(params)
        amt, err = self._coins(params.get("amount")) if params.get("amount") not in (None, "") else (None, None)
        g = self.g
        cfg = self.cfg
        top = g["height"] if g else cfg["max_height"]
        out: dict[str, Any] = {"valid": h is not None and err is None, "height": h, "amount": amt, "max_height": top}
        if herr:
            out["error"] = herr
        elif err:
            out["error"] = err
        else:
            model = self._model() if g else climb_model(top, cfg["escape_pct"])
            keep = self._keep() if g else cl_keep(cfg["house_edge_pct"])
            cents = model.mult_cents(h, keep)
            out["chance"] = round(model.chance(h), 6)
            out["multiplier"] = cents / 100
            if amt:
                out["pays"] = cl_pay(amt, cents, (g.get("max_payout") if g else cfg["max_payout"]) or 0)
        return out


CLIMB = SoulClimb()          # restores a running game from games_climb.json (settled by the plugin)


# --------------------------------------------------------------------------
# what the Games pages need to load this game (see core.register_game)
# --------------------------------------------------------------------------

OVERLAY = {
    "script": "climb.js",
    "stateful": True,
    "appearance": list(SoulClimb.APPEARANCE),
    # mirrors the defaults of the game's config section, for a renderer that runs before its
    # first config message arrives
    "defaults": {
        "x": 50,
        "y": 50,
        "scale": 1.0,
        "theme": "inferno",
        "title": "Soul Climb",
        "show_rules": True,
        "show_players": True,
        "players_max": 8,
        "show_odds": True,
        "sfx": True,
        "sfx_volume": 0.6,
        "hide_when_idle": True,
        "commands_text": "",
        "climbs": 1,
        "max_height": 100,
        "escape_pct": 2,
        "house_edge_pct": 5,
        "result_seconds": 8,
        "summary_seconds": 12,
        "climb_speed": 1.0,
        "currency": "coins",
        "soul_name": "",
    },
}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])


@router.api_route("/api/climb/start", methods=["GET", "POST"])
async def api_cl_start(request: Request):
    return await _round_request(CLIMB, request, CLIMB.start_game)


@router.api_route("/api/climb/bet", methods=["GET", "POST"])
async def api_cl_bet(request: Request):
    return await _round_request(CLIMB, request, CLIMB.place)


@router.api_route("/api/climb/remove", methods=["GET", "POST"])
async def api_cl_remove(request: Request):
    return await _round_request(CLIMB, request, CLIMB.remove)


@router.api_route("/api/climb/next", methods=["GET", "POST"])
async def api_cl_next(request: Request):
    return await _round_request(CLIMB, request, lambda _p: CLIMB.skip())


@router.get("/api/climb/table")
async def api_cl_table():
    return {"ok": True, **CLIMB.state_view()}


@router.get("/api/climb/user/{name}")
async def api_cl_user(name: str):
    return {"ok": True, **CLIMB.user_view(name)}


@router.get("/api/climb/ledger")
async def api_cl_ledger(since: str | None = None, limit: str | None = None):
    return _ledger_reply(since, limit, "climb")


API_DOC = {
    "about":    "one game at a time: betting -> climbing -> result -> ... -> over. Bet on how HIGH a damned soul "
                "climbs out of a pit (heights 1-max_height); bets are against the bank; docs/climb.md",
    "start":    "GET|POST /games/api/climb/start   {soul (name[s]), climbs (1-3), seconds (first bet window), "
                "test} -> {started, state}; 409 while a game runs",
    "bet":      "GET|POST /games/api/climb/bet   {user, amount, height} -> {height, amount, total, mult, pays, "
                "debits, player, state}; up to 5 bets per player per climb (the same height again adds); "
                "409 bets_closed outside the betting window",
    "remove":   "GET|POST /games/api/climb/remove   {user, height?}: take back this window's bets (refund)",
    "next":     "GET|POST /games/api/climb/next   end the current phase now (close the bets, finish the climb)",
    "table":    "GET /games/api/climb/table   the STATE (game: phase, climb, markers, players, script, last, summary)",
    "user":     "GET /games/api/climb/user/{name}",
    "ledger":   "GET /games/api/climb/ledger?since=0   reasons: bet, add, win, refund",
    "bets":     "GET /games/api/climb/bets   rules + the table of every height: chance S(h) and multiplier",
    "validate": "GET /games/api/climb/validate?height=40&amount=100   dry-run a bet",
    "stop":     "GET|POST /games/api/climb/stop   end the game: a climb already decided counts, an open window "
                "is refunded",
    "preview":  "GET|POST /games/api/climb/preview   {overrides:{appearance}, seconds (2-60, default 8)} | "
                "x=&y=&scale= shorthand: the Edit Mode editor's Test in OBS - on screen for `seconds` with that "
                "look (STATE.preview), never touches the game; /preview/clear ends it",
}
