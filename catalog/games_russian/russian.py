"""Russian Roulette - a revolver and a stuffed dummy (Games add-on).
"""

from __future__ import annotations

import re
import secrets
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from hexcast_plugins.games.core import (
    COMMANDS_TEXT_MAX,
    Ledger,
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
    _floor,
    _ledger_reply,
    _mult_view,
    _pct_frac,
    _round_common,
    _round_players,
    _round_request,
    _strict_int,
)

# paths + limits that belong to this game
from hexcast_plugins.games.core import CONFIG_DIR

RUSSIAN_PATH = CONFIG_DIR / "games_russian.json"          # the running game + history (russian roulette)
DUMMY_NAME_MAX = 24
RR_CHAMBERS = 6
RR_ROUNDS_MAX = 5               # round k loads k bullets: 6 would be certain

# --------------------------------------------------------------------------
# russian roulette
# --------------------------------------------------------------------------
# A revolver, a stuffed dummy with a name tag. Round k loads one more bullet (k of 6)
# and re-spins the cylinder: the dummy is shot with chance k/6. Up to `rounds` (3)
# pulls; a bang ends the game. Two bets, both against the bank:
#
#   survive  "it clicks": rides from pull to pull. The house edge is taken ONCE, when
#            the stake goes in: a stake that went in on pull j is worth, after the
#            dummy survived pull c,  stake * keep * alive(j-1) / alive(c)
#            (keep = 1 - edge, alive(c) = chance to survive pulls 1..c). Cash out
#            between pulls, or ride; riders still in after the last pull are cashed
#            out. A bang wipes every survive stake.
#   bang     "this pull fires": one pull only, pays stake * keep * 6 / k.
#
# The dummy's name is display; a dummy `user` (the volunteer) gets `volunteer_cut_pct`
# of the bank's net win for the game (nothing when the bank loses).

_RR_SIDES = {"survive": "survive", "survives": "survive", "live": "survive", "lives": "survive",
             "alive": "survive", "click": "survive", "safe": "survive",
             "bang": "bang", "die": "bang", "dies": "bang", "shot": "bang", "shoot": "bang", "fire": "bang",
             "fires": "bang", "dead": "bang", "death": "bang"}
_RR_THEMES = ("saloon", "noir", "neon")
_RR_OUTCOMES = {"bang": "BANG! The dummy is down", "survived": "The dummy survived every pull",
                "walked": "Everyone walked away - the dummy lives", "no_bets": "No bets - no game",
                "stopped": "Game stopped - every stake returned", "restart": "Settled after a restart",
                "error": "Settled after an error"}


def rr_alive(c: int) -> Fraction:
    """Chance the dummy survives pulls 1..c (pull k: k bullets of 6, re-spun)."""
    p = Fraction(1)
    for k in range(1, c + 1):
        p *= Fraction(RR_CHAMBERS - k, RR_CHAMBERS)
    return p


def rr_ride_mult(entered: int, survived: int, keep: Fraction) -> Fraction:
    """Worth of 1 coin staked on pull `entered` once the dummy survived pull `survived`."""
    if survived < entered:
        return Fraction(1)                  # not at risk yet
    return keep * rr_alive(entered - 1) / rr_alive(survived)


def rr_bang_mult(k: int, keep: Fraction) -> Fraction:
    return keep * RR_CHAMBERS / k


def rr_odds(rounds: int, keep: Fraction) -> list[dict]:
    return [{"round": k, "bullets": k, "fire_pct": round(100 * k / RR_CHAMBERS, 1),
             "survive": _mult_view(rr_ride_mult(k, k, keep)),       # a fresh stake on this pull
             "ride": _mult_view(rr_ride_mult(1, k, keep)),          # in since pull 1
             "bang": _mult_view(rr_bang_mult(k, keep))} for k in range(1, rounds + 1)]


class RussianRoulette(RoundGame):
    key = "russian"
    title = "Russian Roulette"
    id_prefix = "rr"
    PHASES = ("betting", "pulling", "result", "over")
    STAT_KEYS = ("games", "bangs", "survived", "walked", "no_bets", "stopped", "pulls",
                 "total_bet", "total_paid", "house_net", "bang_rounds")

    DEFAULTS: dict[str, Any] = {
        # placement: scene centre in % of the 1920x1080 stage; scale x the 1100x560 scene
        "x": 50, "y": 50, "scale": 1.0,
        "theme": "saloon",              # saloon | noir | neon
        "title": "Russian Roulette",    # the title on the overlay (your branding)
        "show_rules": True,             # rules box while bets are open
        "show_players": True, "players_max": 8,
        "show_odds": True,              # the payout ladder
        "sfx": True, "sfx_volume": 0.6,
        "hide_when_idle": True,
        "commands_text": "",            # e.g. "!live 100 · !bang 50 · !cashout" (your bot's commands)
        "rounds": 3,                    # pulls per game (round k = k bullets)
        "house_edge_pct": 5,
        "open_bet_seconds": 30,         # the first betting window
        "between_seconds": 20,          # the window before every later pull
        "pull_seconds": 9,              # load, spin, cock, pull
        "result_seconds": 4,            # BANG / click stays up
        "summary_seconds": 10,          # game over card (+ the dummy's revive)
        "currency": "coins",            # your bot's coin name, shown after amounts
        "min_bet": 1, "max_bet": 100000,  # per player per pull (per side); 0 = no max
        "max_payout": 0,                # a survive stake worth this much is cashed out; 0 = no cap
        "volunteer_cut_pct": 5,
        "dummy_name": "Dummy",          # when a game starts without one
        "pull_clip": "", "click_clip": "", "bang_clip": "",   # soundboard clips
    }
    SCHEMA: dict[str, tuple] = {
        "x": ("num", 0, 100), "y": ("num", 0, 100), "scale": ("num", 0.2, 5),
        "theme": ("enum", _RR_THEMES), "title": ("name", ROUND_TITLE_MAX),
        "show_rules": ("bool",), "show_players": ("bool",), "players_max": ("int", 1, 20),
        "show_odds": ("bool",), "sfx": ("bool",), "sfx_volume": ("num", 0, 1),
        "hide_when_idle": ("bool",), "commands_text": ("str", COMMANDS_TEXT_MAX),
        "rounds": ("int", 1, RR_ROUNDS_MAX), "house_edge_pct": ("num", 0, 25),
        "open_bet_seconds": ("num", 5, 300), "between_seconds": ("num", 5, 300),
        "pull_seconds": ("num", 6, 20), "result_seconds": ("num", 2, 30), "summary_seconds": ("num", 4, 60),
        "currency": ("name", 24), "min_bet": ("int", 1, 1e9), "max_bet": ("int", 0, 1e12),
        "max_payout": ("int", 0, 1e12), "volunteer_cut_pct": ("num", 0, 50),
        "dummy_name": ("name", DUMMY_NAME_MAX),
        "pull_clip": ("str", 200), "click_clip": ("str", 200), "bang_clip": ("str", 200),
    }
    APPEARANCE = ("x", "y", "scale", "theme", "title", "show_rules", "show_players", "players_max", "show_odds",
                  "sfx", "sfx_volume")

    def __init__(self, path: Path | None = None, ledger: Ledger | None = None) -> None:
        self.next_dummy: dict | None = None     # /dummy while idle: the next game's dummy
        super().__init__(path, ledger)

    def default_path(self) -> Path:
        return RUSSIAN_PATH

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

        def tranches(v):
            if not isinstance(v, list):
                return None
            out = []
            for t in v:
                if isinstance(t, dict):
                    a, r = _strict_int(t.get("amount")), _strict_int(t.get("round"))
                    if a and a > 0 and r and 1 <= r <= RR_ROUNDS_MAX:
                        out.append({"amount": a, "round": r})
            return out or None

        def stake(v):
            a = _strict_int(v)
            return a if a and a > 0 else None

        rounds = min(RR_ROUNDS_MAX, max(1, _strict_int(raw.get("rounds")) or 3))
        loaded = sorted({c for c in (raw.get("loaded") or []) if _strict_int(c) is not None and 0 <= c < RR_CHAMBERS})
        pull = raw.get("pull") if isinstance(raw.get("pull"), dict) else None
        if pull is not None:
            pull = {"round": min(rounds, max(1, _strict_int(pull.get("round")) or 1)),
                    "fired": bool(pull.get("fired")), "resolved": bool(pull.get("resolved")),
                    "stop": _strict_int(pull.get("stop")) or 0, "new": _strict_int(pull.get("new")) or 0,
                    "loaded": [c for c in pull.get("loaded") or [] if _strict_int(c) is not None],
                    "seed": _strict_int(pull.get("seed")) or 0}
        dummy = raw.get("dummy") if isinstance(raw.get("dummy"), dict) else {}
        g.update({
            "rounds": rounds, "round": min(rounds, max(1, _strict_int(raw.get("round")) or 1)),
            "survived": min(rounds, max(0, _strict_int(raw.get("survived")) or 0)),
            "edge_pct": _as_float(raw.get("edge_pct")) or 0.0,
            "cut_pct": _as_float(raw.get("cut_pct")) or 0.0,
            "max_payout": max(0, _strict_int(raw.get("max_payout")) or 0),
            "dummy": {"name": _display_text(dummy.get("name"), DUMMY_NAME_MAX) or "Dummy",
                      "user": _clean_user(dummy.get("user"))},
            "loaded": loaded, "pull": pull,
            "pulls": [p for p in raw.get("pulls") or [] if isinstance(p, dict)],
            "positions": _round_players(raw.get("positions"), tranches),
            "bangs": _round_players(raw.get("bangs"), stake),
            "cut": raw.get("cut") if isinstance(raw.get("cut"), dict) else None,
        })
        return g

    # ---- money ----------------------------------------------------------------

    def _keep(self) -> Fraction:
        return 1 - _pct_frac(self.g["edge_pct"])

    def _value(self, trs: list[dict], survived: int) -> Fraction:
        """A player's survive stakes, worth now (after `survived` pulls), capped."""
        keep = self._keep()
        v = sum((t["amount"] * rr_ride_mult(t["round"], survived, keep) for t in trs), Fraction(0))
        cap = self.g.get("max_payout") or 0
        return min(v, Fraction(cap)) if cap else v

    def _player(self, user: str) -> dict:
        g = self.g
        trs = g["positions"].get(user) or []
        c, r = g["survived"], g["round"]
        keep = self._keep()
        bang = g["bangs"].get(user, 0)
        out = {"user": user, "stake": sum(t["amount"] for t in trs),
               "fresh": sum(t["amount"] for t in trs if t["round"] > c),
               "value": _floor(self._value(trs, c)) if trs else 0,
               "bang": bang, "bang_pays": _floor(bang * rr_bang_mult(r, keep)) if bang else 0}
        # what the survive stake is worth if the dummy survives the coming pull
        nxt = r if g["phase"] == "betting" else None
        out["if_survives"] = _floor(self._value(trs, nxt)) if trs and nxt else None
        return out

    def players_view(self) -> list[dict]:
        g = self.g
        users = list(dict.fromkeys(list(g["positions"]) + list(g["bangs"])))
        rows = [self._player(u) for u in users]
        rows.sort(key=lambda p: (-(p["value"] + p["bang"]), p["user"].lower()))
        return rows

    def table_bets(self) -> list[dict]:
        if self.g is None:
            return []
        return [{"user": p["user"], "amount": p["value"] + p["bang"]} for p in self.players_view()]

    def _cashout_events(self, users: list[str], reason: str = "cashout", label: str = "Cash out") -> list[dict]:
        g = self.g
        events = []
        for u in users:
            trs = g["positions"].pop(u, None)
            if not trs:
                continue
            amt = _floor(self._value(trs, g["survived"]))
            if amt > 0:
                at_risk = any(t["round"] <= g["survived"] for t in trs)
                events.append(_ev("credit", u, amt, reason if at_risk else "refund",
                                  f"{g['id']}/survive", label if at_risk else "Survive (taken back)", g["id"]))
        return events

    # ---- API actions -------------------------------------------------------------

    def start_game(self, params: dict) -> tuple[int, dict]:
        if self.g is not None:
            return 409, {"error": "a game is already running", "phase": self.phase}
        cfg = self.cfg
        nd = self.next_dummy or {}
        name = (_display_text(params.get("dummy"), DUMMY_NAME_MAX) or nd.get("name")
                or _clean_user(params.get("dummy_user")) or nd.get("user") or cfg["dummy_name"])
        duser = _clean_user(params.get("dummy_user")) if params.get("dummy_user") not in (None, "") else nd.get("user")
        r = _as_float(params.get("rounds"))
        rounds = int(min(RR_ROUNDS_MAX, max(1, r))) if r is not None else cfg["rounds"]
        now = time.time()
        self.g = {
            "id": f"rr-{secrets.token_hex(4)}", "test": _flag(params.get("test")), "phase": "betting",
            "started_at": round(now, 3), "phase_at": round(now, 3), "ends_at": None,
            "rounds": rounds, "round": 1, "survived": 0,
            "edge_pct": cfg["house_edge_pct"], "cut_pct": cfg["volunteer_cut_pct"], "max_payout": cfg["max_payout"],
            "dummy": {"name": _cut(name, DUMMY_NAME_MAX), "user": duser},
            "loaded": [], "pull": None, "pulls": [], "positions": {}, "bangs": {},
            "debits": 0, "credits": 0, "totals": {}, "log": [], "ever_bet": False,
            "outcome": None, "summary": None, "last": None, "cut": None, "currency": cfg["currency"],
        }
        self.next_dummy = None
        self.hidden = False
        secs = _as_float(params.get("seconds"))
        secs = min(300.0, max(5.0, secs)) if secs is not None else float(cfg["open_bet_seconds"])
        self._set_phase("betting", secs)
        self.save()
        return 200, {"started": self.g["id"]}

    def set_dummy(self, params: dict) -> tuple[int, dict]:
        name = _display_text(params.get("dummy", params.get("name")), DUMMY_NAME_MAX)
        user = _clean_user(params.get("dummy_user", params.get("user")))
        if not name and not user:
            return 400, {"error": "dummy needs a dummy (name) and/or dummy_user (the volunteer who gets the cut)"}
        d = {"name": name or user, "user": user}
        g = self.g
        if g is None:
            self.next_dummy = d
            return 200, {"dummy": d, "applies": "next game"}
        if g["phase"] != "betting" or g["round"] != 1:
            return 409, {"error": "the dummy can only change before the first pull"}
        g["dummy"] = d
        self.save()
        return 200, {"dummy": d, "applies": "this game"}

    def place(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        raw = params.get("side", params.get("bet"))
        side = _RR_SIDES.get(re.sub(r"[\s_\-!']", "", str(raw or "")).lower())
        if side is None:
            return 400, {"error": "side must be survive (live, click) or bang (die, shot)"}
        if user not in g["positions"] and user not in g["bangs"] and \
                len(set(g["positions"]) | set(g["bangs"])) >= ROUND_PLAYERS_MAX:
            return 400, {"error": f"the game is full ({ROUND_PLAYERS_MAX} players)"}
        r = g["round"]
        if side == "survive":
            trs = g["positions"].setdefault(user, [])
            cur = next((t for t in trs if t["round"] == r), None)
            amt, err = self._coins(params.get("amount"), cur["amount"] if cur else 0)
            if err:
                if not trs:
                    g["positions"].pop(user, None)
                return 400, {"error": err}
            if cur:
                cur["amount"] += amt
            else:
                trs.append({"amount": amt, "round": r})
            label = f"Survive · pull {r}"
        else:
            have = g["bangs"].get(user, 0)
            amt, err = self._coins(params.get("amount"), have)
            if err:
                return 400, {"error": err}
            g["bangs"][user] = have + amt
            label = f"Bang · pull {r}"
        added = (side == "survive" and cur is not None) or (side == "bang" and have > 0)
        g["ever_bet"] = True
        logged = self._persist([_ev("debit", user, amt, "add" if added else "bet", f"{g['id']}/{side}", label, g["id"])])
        return 200, {"side": side, "amount": amt, "debits": _debits(logged), "player": self._player(user)}

    def cashout(self, params: dict) -> tuple[int, dict]:
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed("cashout")
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        if user not in g["positions"]:
            return 400, {"error": f"@{user} has no survive stake in this game"}
        logged = self._persist(self._cashout_events([user]))
        return 200, {"credits": _aggregate_credits(logged, "amount"), "ledger": logged}

    def remove(self, params: dict) -> tuple[int, dict]:
        """Take back what went down in THIS betting window (not yet at risk)."""
        g = self.g
        if g is None or g["phase"] != "betting":
            return self._closed()
        user = _clean_user(params.get("user"))
        if not user:
            return 400, {"error": "user required"}
        raw = params.get("side", params.get("bet"))
        side = _RR_SIDES.get(re.sub(r"[\s_\-!']", "", str(raw or "")).lower()) if raw not in (None, "") else None
        events = []
        if side in (None, "bang") and g["bangs"].get(user):
            amt = g["bangs"].pop(user)
            events.append(_ev("credit", user, amt, "refund", f"{g['id']}/bang", "Bang (taken back)", g["id"]))
        if side in (None, "survive") and g["positions"].get(user):
            trs = g["positions"][user]
            fresh = [t for t in trs if t["round"] > g["survived"]]
            if fresh:
                amt = sum(t["amount"] for t in fresh)
                keep = [t for t in trs if t["round"] <= g["survived"]]
                if keep:
                    g["positions"][user] = keep
                else:
                    g["positions"].pop(user)
                events.append(_ev("credit", user, amt, "refund", f"{g['id']}/survive", "Survive (taken back)", g["id"]))
        if not events:
            return 400, {"error": f"@{user} has nothing placed this round to take back (riding stakes: /cashout)"}
        logged = self._persist(events)
        return 200, {"credits": _aggregate_credits(logged, "amount"), "ledger": logged}

    def user_view(self, name: Any) -> dict:
        user = _clean_user(name)
        g = self.g
        player = self._player(user) if g is not None and user and (user in g["positions"] or user in g["bangs"]) else None
        return {"user": user, "player": player, "session": self.ledger.session(user, self.key)}

    # ---- the game ---------------------------------------------------------------

    def advance(self) -> None:
        ph = self.g["phase"]
        if ph == "betting":
            self._pull()
        elif ph == "pulling":
            self._land()
        elif ph == "result":
            self._after_result()
        else:
            self._to_idle()

    def _pull(self) -> None:
        g = self.g
        if not g["positions"] and not g["bangs"]:
            self._finish("walked" if g["ever_bet"] else "no_bets")
            return
        r = g["round"]
        empty = [c for c in range(RR_CHAMBERS) if c not in g["loaded"]]
        new = _RNG.choice(empty)                    # one more bullet...
        loaded = sorted(g["loaded"] + [new])
        stop = _RNG.randrange(RR_CHAMBERS)          # ...and a fair re-spin: fires with chance r/6
        g["loaded"] = loaded
        g["pull"] = {"round": r, "new": new, "loaded": loaded, "stop": stop, "fired": stop in loaded,
                     "resolved": False, "seed": secrets.randbits(31)}
        self._set_phase("pulling", float(self.cfg["pull_seconds"]))
        self._clip("pull_clip")
        self.save()

    def _resolve(self) -> list[dict]:
        """Settle the pull (once): bang -> bang bets paid, survive stakes lost;
        click -> bang bets lost, survive stakes grow."""
        g = self.g
        p = g["pull"]
        if p is None or p["resolved"]:
            return []
        p["resolved"] = True
        r, keep, events = p["round"], self._keep(), []
        winners, losers = [], []
        if p["fired"]:
            for u, amt in g["bangs"].items():
                pay = _floor(amt * rr_bang_mult(r, keep))
                events.append(_ev("credit", u, pay, "win", f"{g['id']}/bang", f"Bang · pull {r}", g["id"]))
                winners.append({"user": u, "side": "bang", "amount": pay})
            for u, trs in g["positions"].items():
                losers.append({"user": u, "side": "survive", "amount": sum(t["amount"] for t in trs)})
            g["positions"] = {}
        else:
            for u, amt in g["bangs"].items():
                losers.append({"user": u, "side": "bang", "amount": amt})
            g["survived"] = r
            for u, trs in g["positions"].items():
                winners.append({"user": u, "side": "survive", "amount": _floor(self._value(trs, r))})
            cap = g.get("max_payout") or 0
            if cap:
                capped = [u for u, trs in g["positions"].items() if self._value(trs, r) >= cap]
                events += self._cashout_events(capped, "cashout", "Cash out · max payout")
        g["bangs"] = {}
        g["pulls"].append({"round": r, "bullets": r, "fired": p["fired"]})
        winners.sort(key=lambda w: -w["amount"])
        losers.sort(key=lambda w: -w["amount"])
        g["last"] = {"round": r, "fired": p["fired"], "winners": winners, "losers": losers}
        return events

    def _land(self) -> None:
        g = self.g
        events = self._resolve()
        self._set_phase("result", float(self.cfg["result_seconds"]))
        self._persist(events)
        self._clip("bang_clip" if g["pull"]["fired"] else "click_clip")

    def _after_result(self) -> None:
        g = self.g
        if g["pull"] and g["pull"]["fired"]:
            self._finish("bang")
        elif g["round"] >= g["rounds"]:
            self._finish("survived", self._cashout_events(list(g["positions"]), "cashout", "Cash out · survived"))
        else:
            g["round"] += 1
            g["pull"] = None
            self._set_phase("betting", float(self.cfg["between_seconds"]))
            self.save()

    def _finish(self, outcome: str, events: list[dict] | None = None) -> None:
        g = self.g
        if events:
            self._persist(events)
        net = g["debits"] - g["credits"]
        d = g["dummy"]
        pct = _pct_frac(g["cut_pct"])
        g["cut"] = None
        if outcome != "no_bets" and d.get("user") and pct > 0 and net > 0 and _floor(net * pct) >= 1:
            amt = _floor(net * pct)
            self._persist([_ev("credit", d["user"], amt, "volunteer_cut", f"{g['id']}/cut", "Volunteer cut", g["id"])])
            g["cut"] = {"user": d["user"], "amount": amt, "pct": g["cut_pct"]}
        g["outcome"] = outcome
        g["summary"] = self.summary(outcome)
        self._record_game(g["summary"])
        secs = float(self.cfg["summary_seconds"])
        self._set_phase("over", min(secs, 6.0) if outcome == "no_bets" else secs)
        self.save()

    def open_settlement(self) -> list[dict]:
        g = self.g
        events = []
        if g["phase"] == "pulling":
            events += self._resolve()                 # the trigger was pulled: it counts
        for u, amt in list(g["bangs"].items()):
            events.append(_ev("credit", u, amt, "refund", f"{g['id']}/bang", "Bang (game stopped)", g["id"]))
        g["bangs"] = {}
        events += self._cashout_events(list(g["positions"]), "cashout", "Cash out · game stopped")
        return events

    def summary(self, outcome: str) -> dict:
        g = self.g
        fired = next((p["round"] for p in g["pulls"] if p.get("fired")), None)
        return {"outcome": outcome, "text": _RR_OUTCOMES.get(outcome, outcome), "dummy": dict(g["dummy"]),
                "rounds": g["rounds"], "pulls": len(g["pulls"]), "fired_on": fired,
                "total_bet": g["debits"], "total_paid": g["credits"], "house_net": g["debits"] - g["credits"],
                "cut": g.get("cut"), "players": self.players_summary(), "test": bool(g.get("test")),
                "currency": g["currency"]}

    def record(self, s: dict) -> None:
        st = self._st
        st["games"] += 1
        key = {"bang": "bangs", "survived": "survived", "walked": "walked", "no_bets": "no_bets"}.get(
            s["outcome"], "stopped")
        st[key] += 1
        st["pulls"] += s["pulls"]
        st["total_bet"] += s["total_bet"]
        st["total_paid"] += s["total_paid"]
        st["house_net"] += s["house_net"]
        if s.get("fired_on"):
            br = st["bang_rounds"] if isinstance(st["bang_rounds"], dict) else {}
            br[str(s["fired_on"])] = br.get(str(s["fired_on"]), 0) + 1
            st["bang_rounds"] = br

    def reset_stats(self) -> None:
        super().reset_stats()
        self._st["bang_rounds"] = {}

    # ---- views --------------------------------------------------------------------

    def game_view(self, now: float) -> dict:
        g, cfg = self.g, self.cfg
        keep = self._keep()
        pull = None
        if g["pull"] is not None and g["phase"] in ("pulling", "result", "over"):
            pull = {k: g["pull"][k] for k in ("round", "new", "loaded", "stop", "fired", "seed")}
        players = self.players_view()
        return {"id": g["id"], "test": g["test"], "phase": g["phase"], **self.timing(now),
                "round": g["round"], "rounds": g["rounds"], "survived": g["survived"],
                "bullets": g["round"], "loaded": list(g["loaded"]), "pull": pull,
                "dummy": dict(g["dummy"]), "edge_pct": g["edge_pct"], "cut_pct": g["cut_pct"],
                "odds": rr_odds(g["rounds"], keep), "players": players,
                "at_risk": sum(p["value"] for p in players), "bang_total": sum(p["bang"] for p in players),
                "last": g["last"], "outcome": g.get("outcome"), "summary": g.get("summary"),
                "currency": g["currency"], "min_bet": cfg["min_bet"], "max_bet": cfg["max_bet"],
                "commands_text": cfg["commands_text"]}

    def idle_view(self) -> dict:
        cfg = self.cfg
        d = self.next_dummy or {"name": cfg["dummy_name"], "user": None}
        return {"dummy": d, "rounds": cfg["rounds"],
                "odds": rr_odds(cfg["rounds"], 1 - _pct_frac(cfg["house_edge_pct"])),
                "currency": cfg["currency"]}

    def bets_payload(self) -> dict:
        cfg = self.cfg
        keep = 1 - _pct_frac(cfg["house_edge_pct"])
        return {"ok": True, "game": self.key, "currency": cfg["currency"], "min_bet": cfg["min_bet"],
                "max_bet": cfg["max_bet"], "house_edge_pct": cfg["house_edge_pct"],
                "odds": rr_odds(cfg["rounds"], keep), "sides": {
                    "survive": ["survive", "live", "lives", "alive", "click", "safe"],
                    "bang": ["bang", "die", "dies", "shot", "fire", "dead"]},
                "rules": [
                    f"Round k loads k bullets of 6 and re-spins the cylinder: the dummy is shot with chance k/6. "
                    f"Up to {cfg['rounds']} pulls; a bang ends the game.",
                    "survive: rides from pull to pull and grows (the multipliers above); cash out between pulls "
                    "or let it ride. Riders still in after the last pull are cashed out. A bang loses it.",
                    "bang: this pull fires. One pull only.",
                    "Every bet is against the bank: POST /bet debits, cash-outs and wins credit (the ledger).",
                    "Bets and cash-outs only while bets are open (409 bets_closed otherwise). /remove takes back "
                    "what went down in the current window.",
                    "No bets when the window closes: the game ends.",
                    f"The volunteer (dummy_user) gets {cfg['volunteer_cut_pct']}% of the bank's net win for the game."]}

    def validate(self, params: dict) -> dict:
        raw = params.get("side", params.get("bet"))
        side = _RR_SIDES.get(re.sub(r"[\s_\-!']", "", str(raw or "")).lower())
        amt, err = self._coins(params.get("amount")) if params.get("amount") not in (None, "") else (None, None)
        g = self.g
        r = g["round"] if g else 1
        keep = self._keep() if g else 1 - _pct_frac(self.cfg["house_edge_pct"])
        out: dict[str, Any] = {"valid": side is not None and err is None, "side": side, "round": r, "amount": amt}
        if side is None:
            out["error"] = "side must be survive or bang"
        elif err:
            out["error"] = err
        else:
            m = rr_ride_mult(r, r, keep) if side == "survive" else rr_bang_mult(r, keep)
            out["multiplier"] = _mult_view(m)
            if amt:
                out["pays"] = _floor(amt * m)
        return out


RUSSIAN = RussianRoulette()          # restores a running game from games_russian.json (settled by the plugin)


# --------------------------------------------------------------------------
# what the Games pages need to load this game (see core.register_game)
# --------------------------------------------------------------------------

OVERLAY = {
    "script": "russian.js",
    "stateful": True,
    "appearance": list(RussianRoulette.APPEARANCE),
    # mirrors the defaults of the game's config section, for a renderer that runs before its
    # first config message arrives
    "defaults": {
        "x": 50,
        "y": 50,
        "scale": 1.0,
        "theme": "saloon",
        "title": "Russian Roulette",
        "show_rules": True,
        "show_players": True,
        "players_max": 8,
        "show_odds": True,
        "sfx": True,
        "sfx_volume": 0.6,
        "hide_when_idle": True,
        "commands_text": "",
        "rounds": 3,
        "pull_seconds": 9,
        "result_seconds": 4,
        "summary_seconds": 10,
        "currency": "coins",
        "dummy_name": "Dummy",
    },
}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

router = APIRouter(prefix="/games", tags=["games"])


@router.api_route("/api/russian/start", methods=["GET", "POST"])
async def api_rr_start(request: Request):
    return await _round_request(RUSSIAN, request, RUSSIAN.start_game)


@router.api_route("/api/russian/dummy", methods=["GET", "POST"])
async def api_rr_dummy(request: Request):
    return await _round_request(RUSSIAN, request, RUSSIAN.set_dummy)


@router.api_route("/api/russian/bet", methods=["GET", "POST"])
async def api_rr_bet(request: Request):
    return await _round_request(RUSSIAN, request, RUSSIAN.place)


@router.api_route("/api/russian/cashout", methods=["GET", "POST"])
async def api_rr_cashout(request: Request):
    return await _round_request(RUSSIAN, request, RUSSIAN.cashout)


@router.api_route("/api/russian/remove", methods=["GET", "POST"])
async def api_rr_remove(request: Request):
    return await _round_request(RUSSIAN, request, RUSSIAN.remove)


@router.api_route("/api/russian/next", methods=["GET", "POST"])
@router.api_route("/api/russian/pull", methods=["GET", "POST"])
async def api_rr_next(request: Request):
    return await _round_request(RUSSIAN, request, lambda _p: RUSSIAN.skip())


@router.get("/api/russian/table")
async def api_rr_table():
    return {"ok": True, **RUSSIAN.state_view()}


@router.get("/api/russian/user/{name}")
async def api_rr_user(name: str):
    return {"ok": True, **RUSSIAN.user_view(name)}


@router.get("/api/russian/ledger")
async def api_rr_ledger(since: str | None = None, limit: str | None = None):
    return _ledger_reply(since, limit, "russian")


API_DOC = {
    "about":    "one game at a time: betting -> pulling -> result -> ... -> over. Pull k loads k bullets of 6 "
                "and re-spins. Bets are against the bank; docs/russian_roulette.md",
    "start":    "GET|POST /games/api/russian/start   {dummy, dummy_user, rounds (1-5), seconds (first bet "
                "window), test} -> {started, state}; 409 while a game runs",
    "bet":      "GET|POST /games/api/russian/bet   {user, side: survive|bang, amount} -> {debits, player, state}; "
                "409 bets_closed outside the betting window",
    "cashout":  "GET|POST /games/api/russian/cashout   {user} -> {credits, ledger}: the survive stake's value now",
    "remove":   "GET|POST /games/api/russian/remove   {user, side?}: take back this window's bets (refund)",
    "dummy":    "GET|POST /games/api/russian/dummy   {dummy, dummy_user}: before pull 1, else the next game",
    "next":     "GET|POST /games/api/russian/next   (alias /pull) end the current phase now",
    "table":    "GET /games/api/russian/table   the STATE (game: phase, round, odds, players, pull, last, summary)",
    "user":     "GET /games/api/russian/user/{name}",
    "ledger":   "GET /games/api/russian/ledger?since=0   reasons: bet, add, win, cashout, refund, volunteer_cut",
    "stop":     "GET|POST /games/api/russian/stop   end the game: stakes refunded, survive stakes cashed out",
    "preview":  "GET|POST /games/api/russian/preview   {overrides:{appearance}, seconds (2-60, default 8)} | "
                "x=&y=&scale= shorthand: the Edit Mode editor's Test in OBS - on screen for `seconds` with that "
                "look (STATE.preview), never touches the game; /preview/clear ends it",
}
