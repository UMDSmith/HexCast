"""Hellhound Derby - hellhounds race on one track, chat bets on the winner (Games add-on).

A game is one race. The betting window is the lineup: the field (up to 6 hounds from the roster, each
wearing a race number), their odds and their form. Chat bets on a winner by its number; bets are against
the bank. When the window closes the server draws the result and writes the race script; the overlay
plays it (about 45 s of chaos), then the result card; winning bets are paid.

Fairness (the outcome is never in the show's hands)
--------------------------------------------------
The odds come from the FORM BOOK (games_derby_form.json): every hound's recent results. A hound's
strength is its recent win rate (its last 10 races, eased toward average while it has few), a lift for a
winning streak, a dip for a long losing run, and a little luck on the day (SystemRandom), all drawn when
the game starts and shown during the window:

    chance(i) = strength(i) / sum(strengths)
    multiplier(i) = max(1.01, floor_to_cents((1 - house_edge) / chance(i)))
    a winning bet pays floor(amount x multiplier) coins (capped by max_payout, never under the stake)

When the window closes the WINNER is drawn with exactly those chances (secrets.SystemRandom), then the
rest of the order with the same weights (each next place drawn from the hounds left). Only then is the
race dressed: finishing times, every hound's speed wobble, the tumbles, bites, slime, flaming arses, the
crowd's junk and the trash talk come from random.Random(seed) with a fresh seed, and every hound's run is
stretched to cross the line at its time - a hound that eats dirt was always finishing where it finishes.
The script reaches the overlay when the race starts; bets are closed by then.
"""

from __future__ import annotations

import json
import math
import random
import re
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from hexcast_plugins.games.core import (
    COMMANDS_TEXT_MAX,
    CONFIG_DIR,
    ROUND_PLAYERS_MAX,
    ROUND_TITLE_MAX,
    RoundGame,
    _RNG,
    _aggregate_credits,
    _as_float,
    _atomic_write_text,
    _clean_user,
    _debits,
    _ev,
    _flag,
    _ledger_reply,
    _round_common,
    _round_players,
    _round_request,
    _strict_int,
)

DERBY_PATH = CONFIG_DIR / "games_derby.json"        # the running game + history
FORM_PATH = CONFIG_DIR / "games_derby_form.json"    # the form book (kept apart: clearing history keeps it)
STATIC = Path(__file__).resolve().parent / "static"

DB_FIELD_RANGE = (3, 6)          # hounds in a race (the track has 6 rows)
DB_GATE_S = 3.2                  # the racing phase opens with the gate countdown
DB_PHOTO_S = 4.6                 # the race holds on the photo-finish print
DB_TAIL_S = 2.0                  # after the last hound crosses, before the result card
DB_V = 700.0                     # cruising speed, world px/s (100 px = 1 m)
DB_DT = 1 / 60                   # the race is stepped at 60 Hz (the overlay steps it the same way)
DB_PHOTO_PX = 120                # a photo finish: the chaser is within this of the line (about a head)
DB_FORM_KEEP = 10                # races in a hound's recent form
DB_EVENTS_MAX = 40
DB_NAME_MAX = 32

DB_OUTCOMES = {"raced": "Race run", "no_bets": "No bets - no race", "stopped": "Game stopped - every open stake returned",
               "restart": "Settled after a restart", "error": "Settled after an error"}


# --------------------------------------------------------------------------
# the roster and what they say (written by art/derby/scripts/export_assets.py)
# --------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def load_roster() -> list[dict]:
    out = []
    for h in _load_json(STATIC / "derby_hounds.json").get("hounds") or []:
        if isinstance(h, dict) and isinstance(h.get("id"), str) and isinstance(h.get("name"), str):
            out.append({"id": h["id"], "name": h["name"], "short": h.get("short") or h["name"],
                        "note": h.get("bio") or h.get("note") or ""})      # bio: the line chat sees on the lineup
    return out or [{"id": f"hound{i}", "name": f"Hound {i}", "short": f"Hound {i}", "note": ""} for i in range(1, 7)]


ROSTER = load_roster()
LINES = _load_json(STATIC / "derby_lines.json")

# Guest racers: now and then something that isn't a hound lines up too. The add-on's own guests are in
# static/derby_guests.json; your own (a local pack, never part of the add-on) go in
# config/games_derby_guests/: guests.json and the sprite sheets it names, written the same way.
GUESTS_DIR = CONFIG_DIR / "games_derby_guests"
GUEST_FILE = re.compile(r"^[A-Za-z0-9_-]{1,60}\.(?:webp|png)$")
_GUEST_ID = re.compile(r"^[a-z0-9_-]{1,40}$")
_guests: list = [None, []]                      # [what the files looked like, the guests]
# a guest's own lines ({t} = the other one): by (passing someone), at (said to it as it's passed), back (its answer
# when passed), ouch (hurt), dodge (a near miss, a leap), win, lose (dead last, or beaten in a photo), hello (its
# spotlight on the lineup), ticker (the commentary when it lines up, {a} = its name). Its own come first; the
# hounds' only where it has none.
GUEST_LINES = ("by", "at", "back", "ouch", "dodge", "win", "lose", "hello", "ticker")


def load_guests() -> list[dict]:
    """Every guest racer, the add-on's first, then the local pack's; read again whenever a file changes."""
    files = (STATIC / "derby_guests.json", GUESTS_DIR / "guests.json")
    key = tuple(p.stat().st_mtime_ns if p.is_file() else 0 for p in files)
    if key == _guests[0]:
        return _guests[1]
    seen, out = {h["id"] for h in ROSTER}, []
    for path, local in zip(files, (False, True)):
        for g in _load_json(path).get("guests") or []:
            if not (isinstance(g, dict) and isinstance(g.get("id"), str) and _GUEST_ID.match(g["id"])
                    and isinstance(g.get("name"), str) and g["name"].strip() and g["id"] not in seen):
                continue
            seen.add(g["id"])
            lines = g.get("lines") if isinstance(g.get("lines"), dict) else {}
            wob = _as_float(g.get("wobble"))
            out.append({"id": g["id"], "name": g["name"][:DB_NAME_MAX], "short": str(g.get("short") or g["name"])[:DB_NAME_MAX],
                        "note": str(g.get("bio") or "")[:160], "guest": True, "local": local,
                        "wobble": 1.0 if wob is None else min(3.0, max(0.0, wob)),
                        "lines": {k: [s[:120] for s in (lines.get(k) or []) if isinstance(s, str)][:60] for k in GUEST_LINES},
                        "art": {k: g[k] for k in ("sheet", "anchors", "cadence", "hue", "sat") if k in g}})
    _guests[:] = [key, out]
    return out


def everyone() -> list[dict]:
    """The hounds and the guests."""
    return ROSTER + load_guests()


def who(hid: str) -> dict:
    return next((x for x in everyone() if x["id"] == hid), {"id": hid, "name": hid, "short": hid, "note": ""})


def _own(gid: str, key: str) -> list[str]:
    """A guest's own lines of one kind (GUEST_LINES); none for a hound."""
    return next((g["lines"].get(key) or [] for g in load_guests() if g["id"] == gid), [])


def guests_view(static_url: str = "/plugins/games_derby/static/") -> list[dict]:
    """What the overlay needs to draw the guests: their sheets (an add-on guest's next to the hounds', a local
    one's from /games/api/derby/guests/file/...), anchors and lines."""
    out = []
    for g in load_guests():
        art = dict(g["art"])
        sheet = art.get("sheet") if isinstance(art.get("sheet"), dict) else None
        if not sheet or not isinstance(sheet.get("file"), str) or not isinstance(art.get("anchors"), dict):
            continue
        name = sheet["file"].rsplit("/", 1)[-1]
        if g["local"]:
            if not GUEST_FILE.match(name) or not (GUESTS_DIR / name).is_file():
                continue
            art["sheet"] = {**sheet, "file": "/games/api/derby/guests/file/" + name}
        out.append({"id": g["id"], "name": g["name"], "short": g["short"], "bio": g["note"], "guest": True,
                    "local": g["local"], "lines": g["lines"], **art})
    return out


_DOGGY = re.compile(r"hellhound|\bdog\b|labrador|\bfur\b|fleabag|kibble|my tail|\bpaws?\b", re.I)


def _pool(*path: str) -> list[str]:
    v: Any = LINES
    for k in path:
        v = v.get(k) if isinstance(v, dict) else None
    return [s for s in v if isinstance(s, str)] if isinstance(v, list) else []


def _names_ok(s: str, speaker: str, racing: set[str], own: bool = False) -> bool:
    """A line that names a hound is said only while that hound races, and never by the hound itself (unless it's
    its own line: a guest's own may say its own name)."""
    for h in everyone():
        if h["id"] in racing and (own or h["id"] != speaker):
            continue
        if any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", s, re.I) for n in {h["name"], h["short"]} if n):
            return False
    return True


def _fill(s: str, **o: Any) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(o[m.group(1)]) if o.get(m.group(1)) is not None else m.group(0), s)


# --------------------------------------------------------------------------
# the form book
# --------------------------------------------------------------------------

class FormBook:
    """Every hound's record: races, wins, last places (newest first), streak (+n wins in a row, -n without)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else FORM_PATH
        self.data: dict[str, dict] = {}
        raw = _load_json(self.path).get("hounds")
        if isinstance(raw, dict):
            for hid, f in raw.items():
                if isinstance(f, dict):
                    self.data[str(hid)[:40]] = self._clean(f)

    @staticmethod
    def _clean(f: dict) -> dict:
        last = [p for p in (f.get("last") or []) if _strict_int(p) is not None and 1 <= p <= 6][:DB_FORM_KEEP]
        return {"races": max(0, _strict_int(f.get("races")) or 0), "wins": max(0, _strict_int(f.get("wins")) or 0),
                "last": last, "streak": _strict_int(f.get("streak")) or 0}

    def get(self, hid: str) -> dict:
        return dict(self.data.get(hid) or {"races": 0, "wins": 0, "last": [], "streak": 0})

    def save(self) -> bool:
        return _atomic_write_text(self.path, json.dumps({"version": 1, "hounds": self.data}, indent=1))

    def record(self, order: list[str]) -> None:
        """A race finished in this order (hound ids, winner first)."""
        for i, hid in enumerate(order):
            f = self.get(hid)
            f["races"] += 1
            f["last"] = ([i + 1] + f["last"])[:DB_FORM_KEEP]
            if i == 0:
                f["wins"] += 1
                f["streak"] = max(0, f["streak"]) + 1
            else:
                f["streak"] = min(0, f["streak"]) - 1
            self.data[hid] = f
        self.save()

    def clear(self) -> None:
        self.data = {}
        self.save()

    def strength(self, hid: str, weight: float = 1.0) -> float:
        """How strong a hound looks going in (1 = an average hound), before the day's luck."""
        f = self.get(hid)
        recent = f["last"][:DB_FORM_KEEP]
        rate = (sum(1 for p in recent if p == 1) + 1) / (len(recent) + 6) * 6
        s = f["streak"]
        lift = min(0.6, 0.15 * s) if s > 0 else -min(0.3, 0.03 * -s)
        return max(0.05, rate ** (0.8 * weight) * (1 + lift * weight))

    def note(self, hid: str) -> str:
        f = self.get(hid)
        s = f["streak"]
        if s >= 2:
            return f"won the last {s}"
        if s == 1:
            return "won last time"
        if s <= -4:
            return f"{-s} races without a win"
        if f["races"]:
            return f"{f['wins']} win{'' if f['wins'] == 1 else 's'} in {f['races']}"
        return "first race"

    def view(self) -> list[dict]:
        rows = []
        for h in everyone():
            f = self.get(h["id"])
            if h.get("guest") and not f["races"]:
                continue
            rows.append({"id": h["id"], "name": h["name"], **f, "note": self.note(h["id"])})
        rows.sort(key=lambda r: (-r["wins"], r["races"], r["name"].lower()))
        return rows


# --------------------------------------------------------------------------
# odds
# --------------------------------------------------------------------------

def mult_cents(chance: float, edge_pct: float) -> int:
    """The multiplier in whole cents: (1 - edge) / chance rounded down, at least x1.01."""
    return max(101, math.floor((1 - edge_pct / 100) / max(chance, 1e-9) * 100 + 1e-9))


def db_pay(amount: int, cents: int, cap: int = 0) -> int:
    pay = amount * cents // 100
    if cap:
        pay = max(amount, min(pay, cap))
    return pay


def draw_order(chances: list[float], rng=None) -> list[int]:
    """Indexes in finishing order: the winner drawn with `chances`, each next place from the ones left
    with the same weights (SystemRandom: the outcome)."""
    rng = rng or _RNG
    left = list(range(len(chances)))
    out = []
    while left:
        tot = sum(chances[i] for i in left)
        x = rng.random() * tot
        pick = left[-1]
        for i in left:
            x -= chances[i]
            if x <= 0:
                pick = i
                break
        out.append(pick)
        left.remove(pick)
    return out


# --------------------------------------------------------------------------
# the race script (dressing only: the order above is already decided)
# --------------------------------------------------------------------------
# Speed curves through an event (u = 0..1): [kind, a, b]. The overlay has the same function (derby.js
# curve()) and is sent the curve with every event, so the two never disagree.

def curve(spec: list, u: float) -> float:
    k = spec[0]
    a = spec[1] if len(spec) > 1 else 0.0
    b = spec[2] if len(spec) > 2 else 0.0
    if k == "tumble":
        return 0.04 if u < 0.72 else 0.04 + (u - 0.72) / 0.28 * 0.96
    if k == "hold":                 # [hold, until, speed]: `speed` until u = until, then full speed
        return b if u < a else 1.0
    if k == "ramp":
        return a + (1 - a) * u
    if k == "ramp2":
        return a + (1 - a) * u * u
    if k == "const":
        return a
    if k == "femur":
        return -0.85 if u < 0.4 else 0.0 if u < 0.52 else 1.1
    if k == "scorch":
        return 0.45 if u < 0.15 else 1.35
    return 1.0


# dur: seconds; me / them: the speed of the hound in it / of its victim. The big ones (a tumble, the
# demon hand, Bubba eating, Harry running back) cost a lot; slime, bites, zaps and Terry's arguing some; a
# bonk, a kick or yapping hardly anything; the flaming arse ends up a booster.
# who: any | pair (needs a victim) | a hound id (its own antic). hazard / thrown: something on the track.
# at: where it sits from the hound's nose when the event starts. says: (who talks, lines pool).
EV: dict[str, dict] = {
    "trip":     {"dur": 1.7, "who": "any", "me": ["tumble"], "says": ("a", "ouch")},
    "junk":     {"dur": 1.7, "who": "any", "thrown": "land", "at": 70, "me": ["tumble"], "says": ("a", "ouch")},
    "hand":     {"dur": 1.7, "who": "any", "hazard": "hand", "at": -250, "me": ["hold", 0.85, 0.0], "says": ("a", "hand")},
    "snack":    {"dur": 2.0, "who": "bubba", "hazard": "soul", "at": 20, "me": ["hold", 0.88, 0.0], "says": ("a", "snack")},
    "junkfood": {"dur": 2.0, "who": "bubba", "thrown": "land", "at": 30, "me": ["hold", 0.88, 0.0], "says": ("a", "snack")},
    "femur":    {"dur": 1.9, "who": "harry", "hazard": "bone", "at": -360, "me": ["femur"], "says": ("a", "femur")},
    "slime":    {"dur": 1.3, "who": "any", "hazard": "slime", "at": -40, "me": ["ramp2", 0.2], "says": ("a", "slime")},
    "fleas":    {"dur": 1.3, "who": "mange", "me": ["hold", 0.8, 0.05], "says": ("a", "fleas")},
    "argue":    {"dur": 1.6, "who": "terry", "me": ["const", 0.5], "says": ("a", "argue")},
    "bite":     {"dur": 0.9, "who": "pair", "me": ["const", 1.12], "them": ["ramp", 0.3], "behind": True, "says": ("b", "bitten")},
    "snap":     {"dur": 0.9, "who": "gnash", "pair": True, "me": ["const", 1.12], "them": ["ramp", 0.3], "behind": True, "says": ("b", "bitten")},
    "zap":      {"dur": 1.0, "who": "sparks", "pair": True, "me": ["const", 1.0], "them": ["hold", 0.55, 0.1], "behind": False, "says": ("b", "zapped")},
    "kick":     {"dur": 0.8, "who": "pair", "me": ["const", 1.0], "them": ["ramp", 0.6], "behind": False, "says": ("b", "kicked")},
    "flare":    {"dur": 0.7, "who": "cinder", "pair": True, "me": ["const", 1.0], "them": ["ramp", 0.6], "behind": False, "says": ("b", "flared")},
    "bonk":     {"dur": 0.8, "who": "any", "thrown": "hit", "me": ["ramp", 0.6], "says": ("a", "bonk")},
    "yap":      {"dur": 1.0, "who": "brimstone", "me": ["const", 0.85], "says": ("a", "yap")},
    "showoff":  {"dur": 1.0, "who": "ember", "me": ["const", 0.8], "says": ("a", "showoff")},
    "leap":     {"dur": 0.8, "who": "any", "hazard": "geyser", "at": 120, "me": ["const", 1.05], "says": ("a", "leap")},
    "scorch":   {"dur": 2.0, "who": "any", "hazard": "geyser", "at": -230, "me": ["scorch"], "says": ("a", "scorch")},
}
EV_WEIGHT = {"trip": 2.5, "slime": 2, "leap": 1.2, "scorch": 3.5, "hand": 1.5, "bite": 2, "kick": 1.5, "bonk": 3.5,
             "junk": 2.5, "junkfood": 2, "snack": 1.5, "femur": 2, "fleas": 1.5, "argue": 2, "yap": 1, "zap": 1.5,
             "showoff": 1, "flare": 1.5, "snap": 1.5}
PAINFUL = {"trip", "junk", "bonk", "hand", "slime", "scorch", "bite", "snap", "kick", "zap", "flare"}
JUNK = ("BONE", "SKULL", "TRASH BAG", "MUG OF BLOOD", "TOILET", "ROTTEN EYEBALL", "SEVERED HAND",
        "RED BARREL", "TV", "CHAIR", "WOOD AXE", "SWORD", "KATANA", "WAR HAMMER")   # (= JUNK_NAMES in derby.js)


def simulate(wob: list[float], evs: list[tuple[float, float, list]], length: float, stop_after: float = 10.0) -> tuple[list[float], float]:
    """One hound's raw run (before it is stretched to its finishing time): x every DB_DT seconds.
    wob = [a1, a2, w1, w2, p1, p2] (its own wobble); evs = [(start, length, curve)] in raw time.
    The overlay's simulate() is this, line for line."""
    a1, a2, w1, w2, p1, p2 = wob
    xs, x, t, R = [0.0], 0.0, 0.0, None
    while t < 120:
        t += DB_DT
        v = DB_V * (1 + a1 * math.sin(w1 * t + p1) + a2 * math.sin(w2 * t + p2)) * min(1.0, t / 1.3) ** 0.6
        for tau, ln, spec in evs:
            if tau <= t < tau + ln:
                v *= curve(spec, (t - tau) / ln)
        prev, x = x, x + v * DB_DT
        xs.append(x)
        if R is None and x >= length:            # the exact moment within the step (xs is read linearly)
            R = t - DB_DT + (length - prev) / (x - prev) * DB_DT
        if R is not None and t > R + stop_after:
            break
    return xs, (R if R is not None else t)


def sample(xs: list[float], t: float) -> float:
    i = t / DB_DT
    j = math.floor(i)
    if j < 0:
        return 0.0
    if j >= len(xs) - 1:
        return xs[-1]
    return xs[j] + (xs[j + 1] - xs[j]) * (i - j)


def replay(script: dict, i: int) -> tuple[list[float], float]:
    """Runner i's run the way the overlay plays it: (x every DB_DT of raw time, warp); its x at race time t
    (seconds after the gate) is sample(xs, t * warp), and it crosses the line exactly at its T. Event times
    are race times: they are moved onto the raw clock with the warp of a run without events."""
    r = script["runners"][i]
    length = script["length"]
    w0 = simulate(r["wob"], [], length)[1] / r["T"]
    evs = []
    for e in script["events"]:
        if e["a"] == i:
            evs.append((e["t"] * w0, e["dur"] * w0, e["me"]))
        if e.get("b") == i and e.get("them"):
            evs.append((e["t"] * w0, e["dur"] * w0, e["them"]))
    xs, R = simulate(r["wob"], evs, length)
    return xs, R / r["T"]


def margin_word(gap: float) -> str:
    return ("A WHISKER" if gap < 12 else "A NOSE" if gap < 35 else "A TONGUE" if gap < 70
            else "A HEAD" if gap < DB_PHOTO_PX else "A NECK")


def build_script(field: list[dict], order: list[int], race_seconds: float, seed: int) -> dict:
    """field: [{num, id, short}] (index = runner); order: runner indexes, winner first (already drawn).
    Everything here comes from random.Random(seed) and cannot change `order`."""
    rnd = random.Random(seed)
    n = len(field)
    length = DB_V * race_seconds
    T = [0.0] * n
    t = race_seconds * rnd.uniform(1.0, 1.04)
    for k, i in enumerate(order):                    # usually clear gaps; now and then a photo finish
        if k:
            close = (k == 1 and rnd.random() < 0.3) or (k == n - 1 and rnd.random() < 0.2)
            t += rnd.uniform(0.015, 0.08) if close else rnd.uniform(0.15, 0.75)
        T[i] = round(t, 4)                           # (what the script carries is exactly what is used)
    wob = [[round(rnd.uniform(*r), 6) for r in ((0.03, 0.06), (0.02, 0.04), (0.4, 0.8), (1.2, 2.0), (0, 6.3), (0, 6.3))]
           for _ in range(n)]
    for i, h in enumerate(field):                    # a guest runs its own way: steadier, or all over the place
        k = h.get("wobble", 1.0)
        if k != 1.0:
            wob[i][0], wob[i][1] = round(wob[i][0] * k, 6), round(wob[i][1] * k, 6)
    # a first run without events: roughly where everyone is when (to pick who bites whom)
    base = [simulate(wob[i], [], length) for i in range(n)]
    warp0 = [base[i][1] / T[i] for i in range(n)]

    def where(i: int, at: float) -> float:
        return sample(base[i][0], at * warp0[i])

    ids = [h["id"] for h in field]
    kinds = [k for k, e in EV.items() if e["who"] in ("any", "pair") or e["who"] in ids]
    events: list[dict] = []
    used: set[str] = set()
    te = rnd.uniform(2.2, 3.0)
    t_end = T[order[0]] - 2.2
    while te < t_end and len(events) < DB_EVENTS_MAX:
        ev = _plan_event(rnd, field, kinds, te, where, used)
        if ev:
            events.append(ev)
            used.add(ev["type"] + str(ev["a"]))
        te += rnd.uniform(2.4, 3.6)
    # the real run, events included, stretched so each hound crosses the line at its own time
    runners = [{"i": i, "num": field[i]["num"], "id": field[i]["id"], "T": T[i], "wob": wob[i]} for i in range(n)]
    plan = {"length": length, "runners": runners, "events": events}
    runs, warp = zip(*(replay(plan, i) for i in range(n)))
    # a photo finish: the runner-up within a head of the line when the winner crosses (or the last two)
    photo = None
    w, s2 = order[0], order[1]
    gap = length - sample(runs[s2], T[w] * warp[s2])
    if gap < DB_PHOTO_PX:
        photo = {"kind": "win", "a": w, "b": s2, "at": T[w], "label": margin_word(gap)}
    elif n > 2:
        last, prev = order[-1], order[-2]
        gap = length - sample(runs[last], T[prev] * warp[last])
        if gap < DB_PHOTO_PX:
            photo = {"kind": "last", "a": last, "b": prev, "at": T[prev], "label": margin_word(gap)}
    for e in events:                                   # what is said
        _say(rnd, e, field)
    racing = {h["id"] for h in field}

    def last_word(i: int, own: str, pool: str, none: str) -> str:   # (a guest's own, else the hounds' it can say)
        sp, guest = field[i], bool(field[i].get("guest"))
        ok = [s for s in (_own(sp["id"], own) if guest else []) if _names_ok(s, sp["id"], racing, True) and "{t}" not in s]
        ok = ok or [s for s in _pool(pool) if _names_ok(s, sp["id"], racing) and not (guest and _DOGGY.search(s))]
        return rnd.choice(ok or [none])
    loser = photo["b"] if photo and photo["kind"] == "win" else order[-1]   # (who says photo_lost)
    talk = {"win": last_word(order[0], "win", "win", "WINNER!"), "last": last_word(order[-1], "lose", "last", "..."),
            "photo_win": last_word(order[0], "win", "photo_win", "BY A NOSE!"),
            "photo_lost": last_word(loser, "lose", "photo_lost", "NO!")}
    finish = max(T)
    duration = DB_GATE_S + finish + (DB_PHOTO_S if photo else 0) + DB_TAIL_S
    return {"seed": seed, "length": round(length, 3), "v": DB_V, "dt": DB_DT, "gate_ms": round(DB_GATE_S * 1000),
            "photo_ms": round(DB_PHOTO_S * 1000), "duration_ms": round(duration * 1000),
            "runners": runners,
            "order": order, "events": events, "photo": photo, "talk": talk}


def _plan_event(rnd: random.Random, field: list[dict], kinds: list[str], t: float, where, used: set) -> dict | None:
    ids = [h["id"] for h in field]
    for _ in range(14):
        tot = sum(EV_WEIGHT[k] for k in kinds)
        x = rnd.random() * tot
        kind = kinds[-1]
        for k in kinds:
            x -= EV_WEIGHT[k]
            if x <= 0:
                kind = k
                break
        spec = EV[kind]
        if spec["who"] in ("any", "pair"):
            a = rnd.randrange(len(field))
        else:
            a = ids.index(spec["who"])
        if kind + str(a) in used:
            continue
        if kind == "junk" and ids[a] == "bubba":       # Bubba doesn't trip over food: he eats it (junkfood)
            continue
        if kind == "bite" and field[a].get("guest"):   # biting is for the hounds
            continue
        b = None
        if spec["who"] == "pair" or spec.get("pair"):
            ax = where(a, t)
            near = []
            for j in range(len(field)):
                if j == a:
                    continue
                d = where(j, t) - ax
                if (40 < d < 520) if spec["behind"] else (-560 < d < -40):
                    near.append((abs(d), j))
            if not near:
                continue
            b = min(near)[1]
        ev = {"type": kind, "t": round(t, 3), "dur": spec["dur"], "a": a, "b": b, "me": spec["me"]}
        if spec.get("them"):
            ev["them"] = spec["them"]
        if spec.get("hazard"):
            ev["hazard"], ev["at"] = spec["hazard"], spec["at"]
        if spec.get("thrown"):
            ev["thrown"], ev["obj"] = spec["thrown"], rnd.choice(JUNK)
            if spec.get("at") is not None:
                ev["at"] = spec["at"]
        return ev
    return None


def _say(rnd: random.Random, e: dict, field: list[dict]) -> None:
    """The line(s) said when an event starts, and the commentary: picked here so every overlay agrees."""
    who, pool = EV[e["type"]]["says"]
    a = field[e["a"]]
    b = field[e["b"]] if e.get("b") is not None else None
    tick = _pool("ticker", e["type"])
    if tick:
        e["ticker"] = _fill(rnd.choice(tick), a=a["name"], b=b and b["name"], o=e.get("obj")).upper()
    sp = b if who == "b" and b else a
    speaker, racing, guest = sp["id"], {h["id"] for h in field}, bool(sp.get("guest"))
    other = a if who == "b" else b                     # who {t} is: never the speaker (no one, when it's alone)

    def fit(v: list[str], own: bool = False) -> list[str]:   # (a guest doesn't say it's a hellhound, unless it's its own line)
        return [s for s in v if _names_ok(s, speaker, racing, own) and (other or "{t}" not in s)
                and not (guest and not own and _DOGGY.search(s))]
    if pool == "argue":
        lines = fit(_pool("react", "argue"))
        e["lines"] = rnd.sample(lines, 3) if len(lines) >= 3 else lines
        return
    mine = fit(_own(speaker, "ouch" if e["type"] in PAINFUL else "dodge"), own=True) if guest else []
    lines = fit(_pool("ouch") if pool == "ouch" else _pool("react", pool)) or mine
    if not lines or (e["type"] == "leap" and rnd.random() < 0.5):
        return
    own = fit(_pool("eaten" if e["type"] == "junkfood" else "thrown", e["obj"])) if e.get("obj") else []
    if mine and rnd.random() < 0.7:                    # a guest mostly in its own words
        lines = mine
    elif own and rnd.random() < 0.7:                   # mostly about what it was: "WHO THE HELL THROWS A SWORD?!"
        lines = own
    e["line"] = _fill(rnd.choice(lines), o=e.get("obj"), t=other and other["short"])
    e["by"] = who


# --------------------------------------------------------------------------
# the game
# --------------------------------------------------------------------------
# One game = one race: the betting window (the lineup) -> the race -> the result card. Bets are against
# the bank: a stake is a ledger DEBIT when it goes down, a win is a ledger CREDIT when the race ends (a
# losing stake is just gone), taking a bet back (/remove) or stopping a game in its window is a refund.
# The result is drawn when the window closes; a game stopped (or interrupted by a restart) during the
# race counts: it is settled.

_DB_NUM_WORDS = re.compile(r"^(?:#|no\.?\s*|number\s*|n)?\s*(\d{1,2})$", re.I)


class Derby(RoundGame):
    key = "derby"
    title = "Hellhound Derby"
    id_prefix = "db"
    PHASES = ("betting", "racing", "result")
    STAT_KEYS = ("games", "races", "no_bets", "stopped", "photo_finishes", "favourite_wins", "longshot_wins",
                 "total_bet", "total_paid", "house_net", "wins")

    DEFAULTS: dict[str, Any] = {
        # placement: the scene is the whole 1920x1080 stage at scale 1
        "x": 50, "y": 50, "scale": 1.0,
        "theme": "inferno",             # inferno | abyss | sulfur
        "title": "Hellhound Derby",     # the title on the overlay (your branding)
        "show_rules": True,             # how-to-bet box on the lineup
        "show_players": True, "players_max": 8,   # the backers on the lineup, who won/lost on the result card
        "sfx": True, "sfx_volume": 0.6,
        "show_talk": True,              # the hounds' speech bubbles
        "clean_language": False,        # their swearing becomes #@$%
        "show_crowd": True,             # the crowd throwing junk on the track (dressing)
        "hide_when_idle": True,
        "commands_text": "!derby <number> <coins>",   # your bot's bet command, shown on the lineup
        "field_size": 6,                # hounds in a race (from the roster)
        "benched": "",                  # hound (or guest) ids that don't race (comma separated)
        "guest_chance": 0.3,            # how often a race has a guest racer (0 = never, 1 = every race)
        "guests_max": 1,                # guests in one race at most
        "bet_seconds": 22,              # the lineup: the betting window
        "race_seconds": 45,             # roughly how long the race runs
        "result_seconds": 12,           # the result card
        "run_without_bets": True,       # race anyway when nobody bet (it's a show)
        "house_edge_pct": 5,
        "form_weight": 1.0,             # how much form moves the odds (0 = every hound the same, but luck)
        "luck": 0.35,                   # the day's luck: each strength x e^(+-luck)
        "currency": "coins",
        "min_bet": 1, "max_bet": 100000,  # per player per race (all their bets together); 0 = no max
        "max_payout": 0,                # one winning bet pays at most this; 0 = no cap
        "start_clip": "", "finish_clip": "",
    }
    SCHEMA: dict[str, tuple] = {
        "x": ("num", 0, 100), "y": ("num", 0, 100), "scale": ("num", 0.2, 5),
        "theme": ("enum", ("inferno", "abyss", "sulfur")), "title": ("name", ROUND_TITLE_MAX),
        "show_rules": ("bool",), "show_players": ("bool",), "players_max": ("int", 1, 20), "sfx": ("bool",), "sfx_volume": ("num", 0, 1),
        "show_talk": ("bool",), "clean_language": ("bool",), "show_crowd": ("bool",),
        "hide_when_idle": ("bool",), "commands_text": ("str", COMMANDS_TEXT_MAX),
        "field_size": ("int", DB_FIELD_RANGE[0], DB_FIELD_RANGE[1]), "benched": ("str", 200),
        "guest_chance": ("num", 0, 1), "guests_max": ("int", 0, 3),
        "bet_seconds": ("num", 5, 300), "race_seconds": ("num", 25, 90), "result_seconds": ("num", 3, 60),
        "run_without_bets": ("bool",), "house_edge_pct": ("num", 0, 25), "form_weight": ("num", 0, 2), "luck": ("num", 0, 1),
        "currency": ("name", 24), "min_bet": ("int", 1, 1e9), "max_bet": ("int", 0, 1e12), "max_payout": ("int", 0, 1e12),
        "start_clip": ("str", 200), "finish_clip": ("str", 200),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "title", "show_rules", "show_players", "players_max", "sfx", "sfx_volume",
                  "show_talk", "clean_language", "show_crowd")

    def __init__(self, path: Path | None = None, ledger=None, form: FormBook | None = None) -> None:
        self.form = form if form is not None else FormBook()
        super().__init__(path, ledger)

    def default_path(self) -> Path:
        return DERBY_PATH

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
        known = {h["id"] for h in everyone()}
        field = []
        for i, h in enumerate((raw.get("field") or [])[:DB_FIELD_RANGE[1]]):
            if isinstance(h, dict) and h.get("id") in known:
                field.append({"num": i + 1, "id": h["id"], "row": (_strict_int(h.get("row")) or 0) % DB_FIELD_RANGE[1],
                              "chance": _as_float(h.get("chance")) or 1 / 6, "cents": max(101, _strict_int(h.get("cents")) or 101),
                              "note": str(h.get("note") or "")[:60]})
        if len(field) < DB_FIELD_RANGE[0]:
            return None

        def bets(v):
            if not isinstance(v, list):
                return None
            out = []
            for b in v[:DB_FIELD_RANGE[1]]:
                if isinstance(b, dict):
                    num, a = _strict_int(b.get("num")), _strict_int(b.get("amount"))
                    if num and a and a > 0 and 1 <= num <= len(field):
                        out.append({"num": num, "amount": a})
            return out or None

        script = raw.get("script") if isinstance(raw.get("script"), dict) else None
        if script is not None and not (isinstance(script.get("runners"), list) and isinstance(script.get("order"), list)
                                       and _strict_int(script.get("duration_ms")) is not None):
            script = None
        g.update({"field": field, "bets": _round_players(raw.get("bets"), bets), "script": script,
                  "edge_pct": _as_float(raw.get("edge_pct")) or 0.0, "race_seconds": _as_float(raw.get("race_seconds")) or 45.0,
                  "max_payout": max(0, _strict_int(raw.get("max_payout")) or 0), "resolved": bool(raw.get("resolved")),
                  "marks": [m for m in raw.get("marks") or [] if isinstance(m, dict)][:DB_FIELD_RANGE[1]]})
        return g

    # ---- the field --------------------------------------------------------------

    def _benched(self) -> set[str]:
        return {s.strip().lower() for s in str(self.cfg["benched"]).split(",") if s.strip()}

    def _roster(self) -> list[dict]:
        benched = self._benched()
        live = [h for h in ROSTER if h["id"].lower() not in benched and h["name"].lower() not in benched]
        return live if len(live) >= DB_FIELD_RANGE[0] else list(ROSTER)

    def _guests(self) -> list[dict]:
        benched = self._benched()
        return [g for g in load_guests() if g["id"].lower() not in benched and g["name"].lower() not in benched]

    def _new_field(self) -> list[dict]:
        cfg = self.cfg
        pool = self._roster()
        n = max(DB_FIELD_RANGE[0], min(cfg["field_size"], len(pool), DB_FIELD_RANGE[1]))
        picked = _RNG.sample(pool, n)
        guests = self._guests()
        if guests and cfg["guests_max"] and _RNG.random() < float(cfg["guest_chance"]):
            k = _RNG.randint(1, min(int(cfg["guests_max"]), len(guests), n - 1))
            for i, g in zip(_RNG.sample(range(n), k), _RNG.sample(guests, k)):
                picked[i] = g                        # a guest takes that hound's place
        rows = list(range(DB_FIELD_RANGE[1]))
        _RNG.shuffle(rows)
        rows = sorted(rows[:n])
        _RNG.shuffle(rows)
        luck = float(cfg["luck"])
        strengths = [self.form.strength(h["id"], float(cfg["form_weight"])) * math.exp(_RNG.uniform(-luck, luck)) for h in picked]
        tot = sum(strengths)
        edge = float(cfg["house_edge_pct"])
        field = []
        for i, (h, s) in enumerate(zip(picked, strengths)):
            ch = round(s / tot, 6)                   # the chance shown is the chance drawn and paid on
            field.append({"num": i + 1, "id": h["id"], "row": rows[i], "chance": ch, "cents": mult_cents(ch, edge),
                          "note": self.form.note(h["id"])})
        return field

    def _hound(self, num: int) -> dict:
        h = self.g["field"][num - 1]
        r = who(h["id"])
        return {**h, "name": r["name"], "short": r["short"], "trait": r["note"], "guest": bool(r.get("guest"))}

    def _pick(self, params: dict) -> tuple[int | None, str | None]:
        g = self.g
        n = len(g["field"]) if g else self.cfg["field_size"]
        raw = next((params[k] for k in ("number", "num", "hound", "pick", "dog", "runner", "bet")
                    if params.get(k) not in (None, "")), None)
        if raw is None:
            return None, f"hound required: its number (1-{n})"
        if isinstance(raw, bool) or isinstance(raw, (dict, list)):
            return None, f"hound must be a number from 1 to {n}"
        if isinstance(raw, str):
            s = raw.strip()
            m = _DB_NUM_WORDS.match(s)
            if m:
                f = float(m.group(1))
            elif g:                       # a name: the hound's id, name or short name
                low = s.lower().lstrip("@")
                hit = next((i + 1 for i, h in enumerate(g["field"])
                            for r in [self._hound(i + 1)] if low in (h["id"].lower(), r["name"].lower(), r["short"].lower())), None)
                if hit is None:
                    return None, f"no hound called {s[:24]} in this race - bet on a number 1-{n}"
                f = float(hit)
            else:
                f = _as_float(s)
        else:
            f = _as_float(raw)
        if f is None or not f.is_integer():
            return None, f"hound must be a number from 1 to {n}"
        if not 1 <= f <= n:
            return None, f"hound must be from 1 to {n}"
        return int(f), None

    # ---- money ------------------------------------------------------------------

    def _mine(self, user: str) -> list[dict]:
        return self.g["bets"].get(user) or []

    @staticmethod
    def _bid(g: dict, num: int) -> str:
        return f"{g['id']}/n{num}"

    def _label(self, num: int, extra: str = "") -> str:
        h = self._hound(num)
        return f"#{num} {h['name']}" + (f" · {extra}" if extra else "")

    def _bet_view(self, b: dict) -> dict:
        h = self.g["field"][b["num"] - 1]
        return {"num": b["num"], "hound": h["id"], "amount": b["amount"], "mult": h["cents"] / 100,
                "pays": db_pay(b["amount"], h["cents"], self.g.get("max_payout") or 0)}

    def _player(self, user: str) -> dict:
        bets = [self._bet_view(b) for b in sorted(self._mine(user), key=lambda b: b["num"])]
        return {"user": user, "stake": sum(b["amount"] for b in bets), "bets": bets}

    def players_view(self) -> list[dict]:
        rows = [self._player(u) for u in self.g["bets"]]
        rows.sort(key=lambda p: (-p["stake"], p["user"].lower()))
        return rows

    def markers(self) -> list[dict]:
        """The money on each hound."""
        by = {h["num"]: {"num": h["num"], "total": 0, "users": []} for h in self.g["field"]}
        for u, bs in self.g["bets"].items():
            for b in bs:
                m = by[b["num"]]
                m["total"] += b["amount"]
                m["users"].append({"user": u, "amount": b["amount"]})
        for m in by.values():
            m["users"].sort(key=lambda x: (-x["amount"], x["user"].lower()))
        return [by[k] for k in sorted(by)]

    def table_bets(self) -> list[dict]:
        if self.g is None:
            return []
        rows = [{"user": u, "amount": sum(b["amount"] for b in bs)} for u, bs in self.g["bets"].items()]
        rows.sort(key=lambda r: (-r["amount"], r["user"].lower()))
        return rows

    # ---- API actions --------------------------------------------------------------

    def start_game(self, params: dict) -> tuple[int, dict]:
        if self.g is not None:
            return 409, {"error": "a game is already running", "phase": self.phase}
        cfg = self.cfg
        now = time.time()
        self.g = {
            "id": f"db-{secrets.token_hex(4)}", "test": _flag(params.get("test")), "phase": "betting",
            "started_at": round(now, 3), "phase_at": round(now, 3), "ends_at": None,
            "field": self._new_field(), "bets": {}, "script": None, "resolved": False, "marks": [],
            "edge_pct": float(cfg["house_edge_pct"]), "race_seconds": float(cfg["race_seconds"]),
            "max_payout": cfg["max_payout"], "debits": 0, "credits": 0, "totals": {}, "log": [], "ever_bet": False,
            "outcome": None, "summary": None, "last": None, "currency": cfg["currency"],
        }
        self.hidden = False
        secs = _as_float(params.get("seconds"))
        secs = min(300.0, max(5.0, secs)) if secs is not None else float(cfg["bet_seconds"])
        self._set_phase("betting", secs)
        self.save()
        return 200, {"started": self.g["id"], "field": self.field_view()}

    def place(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        num, err = self._pick(params)
        if err:
            return 400, {"error": err}
        if user not in g["bets"] and len(g["bets"]) >= ROUND_PLAYERS_MAX:
            return 400, {"error": f"the game is full ({ROUND_PLAYERS_MAX} players)"}
        mine = g["bets"].get(user) or []
        cur = next((b for b in mine if b["num"] == num), None)
        amt, err = self._coins(params.get("amount"), sum(b["amount"] for b in mine))
        if err:
            return 400, {"error": err}
        if cur is not None:
            cur["amount"] += amt
        else:
            mine.append({"num": num, "amount": amt})
            g["bets"][user] = mine
        g["ever_bet"] = True
        logged = self._persist([_ev("debit", user, amt, "add" if cur is not None else "bet", self._bid(g, num),
                                    self._label(num), g["id"])])
        bet = next(b for b in self._player(user)["bets"] if b["num"] == num)
        h = self._hound(num)
        return 200, {"num": num, "hound": h["id"], "name": h["name"], "side": f"#{num} {h['name']}", "amount": amt,
                     "total": bet["amount"], "mult": bet["mult"], "pays": bet["pays"], "debits": _debits(logged),
                     "player": self._player(user)}

    def remove(self, params: dict) -> tuple[int, dict]:
        """Take back what went down in this window (all of a player's bets, or the ones on one hound)."""
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        num = None
        if any(params.get(k) not in (None, "") for k in ("number", "num", "hound", "pick", "dog", "runner", "bet")):
            num, err = self._pick(params)
            if err:
                return 400, {"error": err}
        mine = self._mine(user)
        take = [b for b in mine if num is None or b["num"] == num]
        if not take:
            what = f"on #{num}" if num else "in this race"
            return 400, {"error": f"@{user} has nothing {what} to take back"}
        keep = [b for b in mine if b not in take]
        if keep:
            g["bets"][user] = keep
        else:
            g["bets"].pop(user, None)
        events = [_ev("credit", user, b["amount"], "refund", self._bid(g, b["num"]), self._label(b["num"], "taken back"), g["id"])
                  for b in take]
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
            self._race()
        elif ph == "racing":
            self._finish_race()
        else:
            self._to_idle()

    def _race(self) -> None:
        """The window closed: draw the result (SystemRandom, the shown odds), dress it as a race, go."""
        g = self.g
        if not g["bets"] and not self.cfg["run_without_bets"]:
            self._finish("no_bets")
            return
        field = g["field"]
        order = draw_order([h["chance"] for h in field])
        named = [{**h, **{k: v for k, v in self._hound(h["num"]).items() if k in ("name", "short", "guest")},
                  "wobble": who(h["id"]).get("wobble", 1.0)} for h in field]
        g["script"] = build_script(named, order, g["race_seconds"], secrets.randbits(31))
        g["resolved"], g["marks"] = False, self.markers()
        self._set_phase("racing", g["script"]["duration_ms"] / 1000.0)
        self._clip("start_clip")
        self.save()

    def _resolve(self) -> list[dict]:
        """Settle the race (once): bets on the winner are paid, the rest is lost."""
        g = self.g
        script = g.get("script")
        if script is None or g["resolved"]:
            return []
        g["resolved"] = True
        order = [g["field"][i]["num"] for i in script["order"]]
        win = order[0]
        h = g["field"][win - 1]
        cap = g.get("max_payout") or 0
        events, winners, losers = [], [], []
        for u, bs in g["bets"].items():
            for b in sorted(bs, key=lambda b: b["num"]):
                if b["num"] == win:
                    pay = db_pay(b["amount"], h["cents"], cap)
                    events.append(_ev("credit", u, pay, "win", self._bid(g, b["num"]), self._label(b["num"], f"won x{h['cents'] / 100:.2f}"), g["id"]))
                    winners.append({"user": u, "num": b["num"], "amount": b["amount"], "mult": h["cents"] / 100, "pays": pay})
                else:
                    losers.append({"user": u, "num": b["num"], "amount": b["amount"]})
        events = [e for e in events if e["amount"] > 0]
        g["bets"] = {}
        winners.sort(key=lambda w: (-w["pays"], w["user"].lower()))
        losers.sort(key=lambda w: (-w["amount"], w["user"].lower()))
        photo = script.get("photo")
        g["last"] = {"order": order, "winner": {"num": win, "id": h["id"], "name": self._hound(win)["name"], "mult": h["cents"] / 100},
                     "last": order[-1], "photo": {"kind": photo["kind"], "label": photo["label"]} if photo else None,
                     "winners": winners, "losers": losers}
        if not g.get("test"):
            self.form.record([g["field"][i]["id"] for i in script["order"]])
        return events

    def _finish_race(self) -> None:
        events = self._resolve()
        self._clip("finish_clip")
        self._finish("raced", events)

    def _finish(self, outcome: str, events: list[dict] | None = None) -> None:
        g = self.g
        if events:
            self._persist(events)
        g["outcome"] = outcome
        g["summary"] = self.summary(outcome)
        self._record_game(g["summary"])
        secs = float(self.cfg["result_seconds"])
        self._set_phase("result", min(secs, 6.0) if outcome == "no_bets" else secs)
        self.save()

    def open_settlement(self) -> list[dict]:
        g = self.g
        events = []
        if g["phase"] == "racing" and g.get("script") is not None:
            events += self._resolve()                 # the result was drawn when the window closed: it counts
        for u, bs in list(g["bets"].items()):         # a window still open: give it back
            for b in bs:
                events.append(_ev("credit", u, b["amount"], "refund", self._bid(g, b["num"]), self._label(b["num"], "game stopped"), g["id"]))
        g["bets"] = {}
        return events

    def summary(self, outcome: str) -> dict:
        g = self.g
        last = g.get("last") or {}
        text = DB_OUTCOMES.get(outcome, outcome)
        win = last.get("winner")
        if win:
            text = f"#{win['num']} {win['name']} wins" + (f" by {last['photo']['label'].lower()}" if (last.get("photo") or {}).get("kind") == "win" else "")
        field = g["field"]
        fav = max(field, key=lambda h: h["chance"])["num"] if field else None
        long = min(field, key=lambda h: h["chance"])["num"] if field else None
        return {"outcome": outcome, "text": text, "winner": win, "order": last.get("order") or [],
                "photo": last.get("photo"), "favourite": fav, "longshot": long,
                "field": [{"num": h["num"], "id": h["id"], "name": self._hound(h["num"])["name"], "mult": h["cents"] / 100} for h in field],
                "total_bet": g["debits"], "total_paid": g["credits"], "house_net": g["debits"] - g["credits"],
                "players": self.players_summary(), "test": bool(g.get("test")), "currency": g["currency"]}

    def record(self, s: dict) -> None:
        st = self._st
        st["games"] += 1
        if s["outcome"] == "raced" or s.get("winner"):
            st["races"] += 1
            if s.get("photo"):
                st["photo_finishes"] += 1
            win = (s.get("winner") or {}).get("num")
            if win is not None and win == s.get("favourite"):
                st["favourite_wins"] += 1
            if win is not None and win == s.get("longshot"):
                st["longshot_wins"] += 1
            hid = (s.get("winner") or {}).get("id")
            if hid:
                wins = st["wins"] if isinstance(st["wins"], dict) else {}
                wins[hid] = int(wins.get(hid, 0)) + 1
                st["wins"] = wins
        if s["outcome"] == "no_bets":
            st["no_bets"] += 1
        elif s["outcome"] not in ("raced",):
            st["stopped"] += 1
        st["total_bet"] += s["total_bet"]
        st["total_paid"] += s["total_paid"]
        st["house_net"] += s["house_net"]

    def reset_stats(self) -> None:
        super().reset_stats()
        self._st["wins"] = {}

    # ---- views --------------------------------------------------------------------

    def field_view(self) -> list[dict]:
        g = self.g
        out = []
        for h in g["field"]:
            r = self._hound(h["num"])
            f = self.form.get(h["id"])
            out.append({"num": h["num"], "id": h["id"], "name": r["name"], "short": r["short"], "trait": r["trait"],
                        "row": h["row"], "chance": h["chance"], "mult": h["cents"] / 100, "note": h["note"],
                        "streak": f["streak"], "guest": r["guest"]})
        return out

    def game_view(self, now: float) -> dict:
        g, cfg = self.g, self.cfg
        live = g["phase"] == "betting"
        players = self.players_view()
        script = g["script"] if g["phase"] in ("racing", "result") else None
        return {"id": g["id"], "test": g["test"], "phase": g["phase"], **self.timing(now),
                "field": self.field_view(), "edge_pct": g["edge_pct"], "script": script,
                "markers": self.markers() if live else g["marks"], "players": players,
                "at_risk": sum(p["stake"] for p in players), "last": g["last"], "outcome": g.get("outcome"),
                "summary": g.get("summary"), "currency": g["currency"], "min_bet": cfg["min_bet"],
                "max_bet": cfg["max_bet"], "commands_text": cfg["commands_text"]}

    def idle_view(self) -> dict:
        cfg = self.cfg
        return {"form": self.form.view(), "field_size": cfg["field_size"], "currency": cfg["currency"],
                "commands_text": cfg["commands_text"]}

    def bets_payload(self) -> dict:
        cfg = self.cfg
        edge = cfg["house_edge_pct"]
        g = self.g
        return {"ok": True, "game": self.key, "currency": cfg["currency"], "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"],
                "house_edge_pct": edge, "field_size": cfg["field_size"],
                "field": self.field_view() if g else None, "form": self.form.view(),
                "model": {"strength": "recent win rate (last 10, eased toward average) ^ (0.8 x form_weight) x (1 + streak lift x "
                                      "form_weight) x e^(uniform(-luck, luck))",
                          "chance": "strength / sum of the field's strengths",
                          "multiplier": "max(1.01, floor_to_cents((1 - house_edge) / chance)); a win pays floor(amount x multiplier)",
                          "result": "the winner is drawn with exactly those chances (SystemRandom) when bets close"},
                "rules": [
                    "Bet on the WINNER by its race number: POST /bet {user, amount, number}. If your hound wins you are paid "
                    "amount x its multiplier, rounded down to whole coins; any other result and the stake is lost.",
                    f"Odds come from the form book: hot hounds are favourites and pay less, cold ones pay more; the "
                    f"multiplier is (1 - {edge}% edge) / chance, rounded down to 2 decimals.",
                    "Back as many hounds as you like (betting a hound again adds to it). Bets only while bets are open "
                    "(409 bets_closed otherwise); /remove takes back what went down in this window (refund).",
                    "Every bet is against the bank: POST /bet debits, wins credit (the ledger)."]}

    def validate(self, params: dict) -> dict:
        g = self.g
        if g is None:
            return {"valid": False, "error": "no race running - start one with /start"}
        num, nerr = self._pick(params)
        amt, err = self._coins(params.get("amount")) if params.get("amount") not in (None, "") else (None, None)
        out: dict[str, Any] = {"valid": num is not None and err is None, "number": num, "amount": amt}
        if nerr:
            out["error"] = nerr
        elif err:
            out["error"] = err
        else:
            h = g["field"][num - 1]
            out.update({"hound": h["id"], "name": self._hound(num)["name"], "chance": h["chance"], "multiplier": h["cents"] / 100})
            if amt:
                out["pays"] = db_pay(amt, h["cents"], g.get("max_payout") or 0)
        return out


DERBY = Derby()          # restores a running game from games_derby.json (settled by the plugin)


# --------------------------------------------------------------------------
# what the Games pages need to load this game (see core.register_game)
# --------------------------------------------------------------------------

OVERLAY = {
    "script": "derby.js",
    "stateful": True,
    "appearance": list(Derby.APPEARANCE),
    # mirrors the defaults of the game's config section, for a renderer that runs before its first
    # config message arrives
    "defaults": {
        "x": 50, "y": 50, "scale": 1.0, "theme": "inferno", "title": "Hellhound Derby", "show_rules": True,
        "show_players": True, "players_max": 8, "sfx": True, "sfx_volume": 0.6, "show_talk": True, "clean_language": False,
        "show_crowd": True, "hide_when_idle": True, "commands_text": "!derby <number> <coins>", "field_size": 6,
        "bet_seconds": 22, "race_seconds": 45, "result_seconds": 12, "house_edge_pct": 5, "currency": "coins",
    },
}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])


@router.api_route("/api/derby/start", methods=["GET", "POST"])
async def api_db_start(request: Request):
    return await _round_request(DERBY, request, DERBY.start_game)


@router.api_route("/api/derby/bet", methods=["GET", "POST"])
async def api_db_bet(request: Request):
    return await _round_request(DERBY, request, DERBY.place)


@router.api_route("/api/derby/remove", methods=["GET", "POST"])
async def api_db_remove(request: Request):
    return await _round_request(DERBY, request, DERBY.remove)


@router.api_route("/api/derby/next", methods=["GET", "POST"])
async def api_db_next(request: Request):
    return await _round_request(DERBY, request, lambda _p: DERBY.skip())


@router.get("/api/derby/table")
async def api_db_table():
    return {"ok": True, **DERBY.state_view()}


@router.get("/api/derby/user/{name}")
async def api_db_user(name: str):
    return {"ok": True, **DERBY.user_view(name)}


@router.get("/api/derby/ledger")
async def api_db_ledger(since: str | None = None, limit: str | None = None):
    return _ledger_reply(since, limit, "derby")


@router.get("/api/derby/form")
async def api_db_form():
    return {"ok": True, "form": DERBY.form.view()}


@router.get("/api/derby/guests")
async def api_db_guests():
    """The guest racers the overlay can draw: the add-on's and your local pack's (sheets, anchors, lines)."""
    return {"ok": True, "guests": guests_view()}


@router.get("/api/derby/guests/file/{name}")
async def api_db_guest_file(name: str):
    """A local guest's sprite sheet, from config/games_derby_guests/."""
    path = GUESTS_DIR / name
    if not GUEST_FILE.match(name) or not path.is_file():
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return FileResponse(path, media_type="image/webp" if name.endswith(".webp") else "image/png")


@router.api_route("/api/derby/form/clear", methods=["POST"])
async def api_db_form_clear():
    DERBY.form.clear()
    DERBY.broadcast_soon()
    return {"ok": True, "form": DERBY.form.view()}


API_DOC = {
    "about":    "one race at a time: betting (the lineup) -> racing -> result. Bet on the WINNER by its race number; "
                "odds come from the form book; bets are against the bank; docs/derby.md",
    "start":    "GET|POST /games/api/derby/start   {seconds (bet window), test} -> {started, field, state}; 409 while a "
                "game runs",
    "bet":      "GET|POST /games/api/derby/bet   {user, amount, number (1-6; or the hound's name)} -> {num, hound, name, "
                "amount, total, mult, pays, debits, player, state}; 409 bets_closed outside the betting window",
    "remove":   "GET|POST /games/api/derby/remove   {user, number?}: take back this window's bets (refund)",
    "next":     "GET|POST /games/api/derby/next   end the current phase now (close the bets, finish the race)",
    "table":    "GET /games/api/derby/table   the STATE (game: phase, field with odds, markers, players, script, last, "
                "summary)",
    "user":     "GET /games/api/derby/user/{name}",
    "ledger":   "GET /games/api/derby/ledger?since=0   reasons: bet, add, win, refund",
    "bets":     "GET /games/api/derby/bets   rules, the odds model, the current field and the form book",
    "validate": "GET /games/api/derby/validate?number=3&amount=100   dry-run a bet",
    "form":     "GET /games/api/derby/form   the form book; POST /games/api/derby/form/clear resets it",
    "guests":   "GET /games/api/derby/guests   the guest racers (the add-on's and your local pack's in "
                "config/games_derby_guests/): names, lines, sprite sheets; /guests/file/{name} serves a local sheet",
    "stop":     "GET|POST /games/api/derby/stop   end the game: a race already drawn counts, an open window is refunded",
    "preview":  "GET|POST /games/api/derby/preview   {overrides:{appearance}, seconds (2-60, default 8)}: the Edit Mode "
                "editor's Test in OBS - on screen for `seconds` with that look (STATE.preview); /preview/clear ends it",
}
