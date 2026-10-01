"""Hexfall (the games_hexfall add-on): the Plinko math, the paths, the settlement through the ledger and the API.

The game logic is driven directly on a private Hexfall (own state file + ledger, no event loop: the tests
move it on with skip()); the HTTP part goes through the real plugin host like the other game tests."""

import asyncio
import importlib
import json
import re
import shutil
import subprocess
from fractions import Fraction
from math import comb
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
ROWS = range(8, 17)
RISKS = ("low", "medium", "high")


@pytest.fixture
def world(real_world):
    real_world.installer.install("games")
    real_world.installer.install("games_hexfall")
    real_world.host.load_all()
    assert "games_hexfall" in real_world.host.loaded, real_world.host.errors
    return real_world


@pytest.fixture
def hf(world):
    return importlib.import_module("hexcast_plugins.games_hexfall.hexfall")


@pytest.fixture
def core(world):
    return importlib.import_module("hexcast_plugins.games.core")


@pytest.fixture
def game(hf, core, tmp_path):
    """A private Hexfall: its own state file and ledger, default settings, no event loop."""
    g = hf.Hexfall(path=tmp_path / "hexfall.json", ledger=core.Ledger(tmp_path / "ledger.jsonl"))
    core.CONFIG["hexfall"] = g.validate_config({})
    return g


def settings(core, game, **kw):
    core.CONFIG["hexfall"] = game.validate_config({**core.CONFIG["hexfall"], **kw})


def fix_path(monkeypatch, hf, *paths):
    """The coin flips of the next drops (in order) instead of the server's RNG."""
    todo = list(paths)
    monkeypatch.setattr(hf, "roll_path", lambda rows, rng=None: list(todo.pop(0)))


def ledger(game):
    return [(e["type"], e["user"], e["amount"], e["reason"]) for e in game.ledger.events]


# ---------------------------------------------------------------- the math

def test_slot_probabilities_are_the_binomial(hf):
    for rows in ROWS:
        p = hf.slot_probs(rows)
        assert len(p) == rows + 1 and sum(p) == 1
        assert p == p[::-1]                                          # symmetric
        assert p[0] == Fraction(1, 2 ** rows) and p[rows // 2] == Fraction(comb(rows, rows // 2), 2 ** rows)
        assert hf.slot_ways(rows) == [comb(rows, k) for k in range(rows + 1)]
    assert hf.slot_probs(10)[3] == Fraction(120, 1024)


def test_every_preset_is_symmetric_and_pays_about_95_percent(hf):
    assert set(hf.PRESETS) == set(RISKS)
    for risk in RISKS:
        assert set(hf.PRESETS[risk]) == set(ROWS)
        for rows in ROWS:
            t = list(hf.PRESETS[risk][rows])
            assert len(t) == rows + 1, (risk, rows)
            assert t == t[::-1], (risk, rows)                        # symmetric
            assert t[0] == max(t) and t[rows // 2] == min(t), (risk, rows)   # big edges, the smallest in the middle
            assert all(a >= b for a, b in zip(t[:rows // 2 + 1], t[1:rows // 2 + 1])), (risk, rows)   # never rises toward the middle
            rtp = hf.table_rtp(rows, t)
            assert Fraction(9450, 10000) <= rtp <= Fraction(9550, 10000), (risk, rows, float(rtp))
            if risk == "low":
                assert 0 not in t                                    # low risk never busts
            if risk == "high":
                assert t.count(0) >= 1 and t[0] >= 30


def test_default_table_has_busts_in_the_middle_and_big_edges(hf, game):
    t, source = hf.resolve_table(game.cfg["rows"], game.cfg["risk"], game.cfg["multipliers"])
    assert source == "preset" and (game.cfg["rows"], game.cfg["risk"]) == (12, "medium")
    assert t[6] == 0 and t[0] == t[-1] >= 25 and min(t[5], t[7]) <= 0.5
    slots, rtp, edge = hf.table_view(12, tuple(t))
    assert slots[6]["bust"] and not slots[0]["bust"]
    assert rtp == 94.85 and edge == 5.15 and rtp + edge == 100


def test_rtp_is_computed_exactly_and_never_overstated(hf):
    assert hf.table_rtp(8, [1] * 9) == 1                              # pays the stake back: exactly 100%
    assert hf.table_rtp(8, [0] * 9) == 0
    # rows 10: only the centre slot (252 of 1024 paths) pays x4 -> exactly 252 * 4 / 1024
    t = [0] * 11
    t[5] = 4
    assert hf.table_rtp(10, t) == Fraction(252 * 4, 1024)
    assert hf.rtp_pct(Fraction(94999, 100000)) == 94.99               # rounded DOWN
    assert hf.rtp_pct(Fraction(95999, 100000)) == 95.99
    assert hf.edge_pct(Fraction(94999, 100000)) == 5.01 and hf.edge_pct(Fraction(11, 10)) == -10.0
    slots, rtp, edge = hf.table_view(8, tuple(hf.preset(8, "low")))
    assert [s["slot"] for s in slots] == list(range(9)) and sum(s["probability"] for s in slots) == pytest.approx(1)
    assert sum(s["rtp_pct"] for s in slots) == pytest.approx(rtp, abs=0.01)


def test_payouts_are_exact_and_round_down(hf):
    assert hf.pays(100, 2.5) == 250 and hf.pays(7, 0.5) == 3 and hf.pays(3, 1.2) == 3 and hf.pays(100, 0) == 0
    assert hf.pays(100, 1.15) == 115 and hf.pays(100, 0.29) == 29 and hf.pays(100, 4.35) == 435   # floats give 114 / 28 / 434
    assert hf.pays(10 ** 12, 0.3) == 3 * 10 ** 11
    assert hf.pays(5, 1000) == 5000 and hf.pays(1, 0.2) == 0
    assert hf.mult_label(0) == "BUST" and hf.mult_label(25) == "×25" and hf.mult_label(0.5) == "×0.5"


def test_roll_path_is_one_fair_coin_per_row(hf):
    class Bits:
        def __init__(self, bits):
            self.bits = list(bits)

        def getrandbits(self, k):
            assert k == 1
            return self.bits.pop(0)
    assert hf.roll_path(5, Bits([1, 0, 1, 1, 0])) == [1, 0, 1, 1, 0]
    # the real thing: every path is `rows` flips, and the slots come out binomially
    rows, n = 8, 6000
    counts = [0] * (rows + 1)
    for _ in range(n):
        path = hf.roll_path(rows)
        assert len(path) == rows and set(path) <= {0, 1}
        counts[sum(path)] += 1
    for k, w in enumerate(hf.slot_ways(rows)):
        p = w / 2 ** rows
        assert abs(counts[k] - n * p) <= 6 * (n * p * (1 - p)) ** 0.5 + 1, (k, counts)


def test_parse_multipliers(hf):
    ok, err = hf.parse_multipliers([5, 1, 0.5, 1, 5], 4)
    assert ok == [5, 1, 0.5, 1, 5] and err is None
    ok, _ = hf.parse_multipliers("x5, 1 0.5; bust ×1 , 5", 5)
    assert ok == [5, 1, 0.5, 0, 1, 5]
    ok, _ = hf.parse_multipliers([1.23456, "2.5"], 1)
    assert ok == [1.2346, 2.5]                                        # at most 4 decimals
    for bad, why in (([1, 2, 3], "needs 5"), ("1 2 3 4 5 6", "needs 5"), ([1, 2, "x", 4, 5], "not a number"),
                     ([1, -1, 1, 1, 1], "between 0 and"), ([1, 1001, 1, 1, 1], "between 0 and"),
                     ([1, 1, float("inf"), 1, 1], "not a number"), (None, "list of numbers"), ({"a": 1}, "list of numbers")):
        vals, err = hf.parse_multipliers(bad, 4)
        assert vals is None and why in err, (bad, err)
    assert hf.parse_multipliers(list(range(70)), 4)[0] is None


# ---------------------------------------------------------------- settings

def test_config_validation(hf, game):
    cfg = game.validate_config({})
    assert (cfg["title"], cfg["drops"], cfg["rows"], cfg["risk"], cfg["multipliers"], cfg["theme"]) == ("Hexfall", 3, 12, "medium", [], "coven")
    assert set(cfg) == set(game.DEFAULTS) and set(game.SCHEMA) == set(game.DEFAULTS)
    v = game.validate_config({"rows": 3, "drops": 99, "risk": "wild", "theme": "nope", "title": "  ", "min_bet": 50, "max_bet": 10})
    assert (v["rows"], v["drops"], v["risk"], v["theme"], v["title"]) == (8, 10, "medium", "coven", "Hexfall")
    assert v["max_bet"] == 0                                          # a max below the min is no max
    assert game.validate_config({"rows": 40})["rows"] == 16
    assert game.validate_config({"title": "x" * 80})["title"] == "x" * 32
    assert game.validate_config({"risk": "HIGH"})["risk"] == "high"


def test_a_custom_table_must_have_rows_plus_one_numbers(hf, game):
    table = [40, 15, 5, 3, 1, 0.3, 0, 0.3, 1, 3, 5, 15, 40]
    assert game.validate_config({"rows": 12, "multipliers": table})["multipliers"] == table
    assert game.validate_config({"rows": 12, "multipliers": "40 15 5 3 1 0.3 bust 0.3 1 3 5 15 40"})["multipliers"] == [
        40, 15, 5, 3, 1, 0.3, 0, 0.3, 1, 3, 5, 15, 40]
    assert game.validate_config({"rows": 10, "multipliers": table})["multipliers"] == []       # 13 numbers for 11 slots
    assert game.validate_config({"rows": 12, "multipliers": table[:-1]})["multipliers"] == []
    assert game.validate_config({"rows": 12, "multipliers": [-1] + table[1:]})["multipliers"] == []
    assert game.validate_config({"rows": 12, "multipliers": "junk"})["multipliers"] == []
    assert game.validate_config({"rows": 12, "multipliers": ""})["multipliers"] == []
    # changing the rows drops a list that no longer fits (the config is merged, then validated)
    merged = {**game.validate_config({"rows": 12, "multipliers": table}), "rows": 10}
    assert game.validate_config(merged)["multipliers"] == []
    # a custom table is used (and its own RTP reported) instead of the preset
    cfg = game.validate_config({"rows": 8, "multipliers": [2] * 9})
    t, source = hf.resolve_table(cfg["rows"], cfg["risk"], cfg["multipliers"])
    assert source == "custom" and t == [2] * 9 and hf.table_view(8, tuple(t))[1] == 200.0


# ---------------------------------------------------------------- a game

def test_a_full_game_settles_through_the_ledger(hf, game, core, monkeypatch):
    settings(core, game, drops=2)
    rows = 12
    fix_path(monkeypatch, hf, [1] * rows, [0, 1] * 6)                 # drop 1: the far edge (x40), drop 2: the middle (a bust)
    status, body = game.start_game({"seconds": 30})
    assert status == 200 and body == {"started": game.g["id"], "drops": 2, "rows": 12, "risk": "medium", "source": "preset", "rtp_pct": 94.85}
    assert game.start_game({})[0] == 409
    for user, amt in (("alice", 100), ("bob", 50), ("carol", 3)):
        status, body = game.place({"user": user, "amount": amt})
        assert status == 200 and body["drop"] == 1 and body["total"] == amt
        assert body["debits"][0]["reason"] == "bet" and body["debits"][0]["amount"] == amt
    status, body = game.place({"user": "alice", "amount": 20})        # betting again adds to this drop's bet
    assert body["total"] == 120 and body["debits"][0]["reason"] == "add"
    assert game.g["phase"] == "betting" and game.g["fall"] is None

    assert game.skip() == (200, {"skipped": "betting"})
    g = game.g
    assert g["phase"] == "dropping" and g["fall"]["slot"] == 12 and g["fall"]["mult"] == 40 and g["fall"]["path"] == [1] * rows
    assert game.place({"user": "dave", "amount": 5})[0] == 409        # bets are closed while the token falls
    assert game.g["bets"] == {"alice": 120, "bob": 50, "carol": 3}

    game.skip()                                                       # it lands: every stake x40
    assert g["phase"] == "result"
    assert [e for e in ledger(game) if e[0] == "credit"] == [
        ("credit", "alice", 4800, "payout"), ("credit", "bob", 2000, "payout"), ("credit", "carol", 120, "payout")]
    last = g["last"]
    assert (last["drop"], last["slot"], last["mult"], last["bust"]) == (1, 12, 40, False)
    assert [(w["user"], w["bet"], w["paid"], w["net"]) for w in last["winners"]] == [
        ("alice", 120, 4800, 4680), ("bob", 50, 2000, 1950), ("carol", 3, 120, 117)]
    assert last["losers"] == [] and last["total_bet"] == 173 and last["total_paid"] == 6920
    assert g["bets"] == {}

    game.skip()                                                       # drop 2: a new window, the old bets are spent
    assert g["phase"] == "betting" and g["drop"] == 2 and g["fall"] is None and g["bets"] == {}
    game.place({"user": "alice", "amount": 10})
    game.skip()
    assert g["fall"]["slot"] == 6 and g["fall"]["mult"] == 0
    game.skip()                                                       # a bust: nothing is credited
    assert g["last"]["bust"] and g["last"]["losers"][0]["user"] == "alice" and g["last"]["winners"] == []
    assert len([e for e in ledger(game) if e[0] == "credit"]) == 3
    game.skip()
    assert g["phase"] == "over" and g["outcome"] == "complete"
    s = g["summary"]
    assert (s["total_bet"], s["total_paid"], s["house_net"]) == (183, 6920, 183 - 6920)
    assert s["drops"][0]["mult"] == 40 and s["drops"][1]["mult"] == 0 and s["best"] == {"user": "alice", "paid": 4800, "net": 4680, "drop": 1, "mult": 40}
    nets = {p["user"]: p["net"] for p in s["players"]}
    assert nets == {"alice": 4800 - 130, "bob": 1950, "carol": 117}
    # the ledger is the shared kind: one debit per bet, one credit per payout, every event names the game
    assert sum(e[2] for e in ledger(game) if e[0] == "debit") == 183 and sum(e[2] for e in ledger(game) if e[0] == "credit") == 6920
    assert {e["game"] for e in game.ledger.events} == {"hexfall"} and {e["roll_id"] for e in game.ledger.events} == {g["id"]}
    st = game.stats()
    assert (st["games"], st["complete"], st["drops"], st["busts"], st["total_bet"], st["total_paid"], st["house_net"]) == (
        1, 1, 2, 1, 183, 6920, 183 - 6920)
    assert game.history[0]["result"]["outcome"] == "complete" and game.history[0]["game"] == "hexfall"
    game.skip()
    assert game.g is None and game.phase == "idle"
    assert [r["mult"] for r in game._recent()] == [0, 40]             # the strip of the last hits, newest first


def test_a_partial_return_is_a_payout_not_a_win(hf, game, monkeypatch):
    fix_path(monkeypatch, hf, [0, 1] * 5 + [0, 0])                    # slot 5: x0.3 (rows 12 medium)
    game.start_game({"drops": 1})
    game.place({"user": "bob", "amount": 100})
    game.skip()
    assert game.g["fall"]["slot"] == 5 and game.g["fall"]["mult"] == 0.3
    game.skip()
    assert ledger(game)[-1] == ("credit", "bob", 30, "payout")
    assert game.g["last"]["losers"][0] == {"user": "bob", "bet": 100, "paid": 30, "net": -70}
    assert game.g["last"]["winners"] == [] and game.g["last"]["even"] == []
    assert game.g["best"] is None                                      # a partial return is no win


def test_x1_is_an_even_result(hf, game, monkeypatch):
    fix_path(monkeypatch, hf, [1] * 4 + [0] * 8)                      # slot 4: x1
    game.start_game({"drops": 1})
    game.place({"user": "bob", "amount": 77})
    game.skip(); game.skip()
    assert ledger(game)[-1] == ("credit", "bob", 77, "payout")
    assert game.g["last"]["even"] == [{"user": "bob", "bet": 77, "paid": 77, "net": 0}]


def test_no_bets_ends_the_game_and_a_missing_second_window_too(hf, game, monkeypatch):
    game.start_game({})
    assert game.skip() == (200, {"skipped": "betting"})
    assert game.g["phase"] == "over" and game.g["outcome"] == "no_bets" and game.ledger.last_seq == 0
    game.skip()
    assert game.g is None
    assert game.stats()["no_bets"] == 1

    fix_path(monkeypatch, hf, [1] * 12)
    game.start_game({})
    game.place({"user": "bob", "amount": 10})
    game.skip(); game.skip(); game.skip()                             # drop 1 plays out ...
    assert game.g["phase"] == "betting" and game.g["drop"] == 2
    game.skip()                                                       # ... nobody bets on drop 2
    assert game.g["phase"] == "over" and game.g["outcome"] == "walked"
    assert game.stats()["walked"] == 1 and game.g["summary"]["total_bet"] == 10


def test_bet_validation_and_limits(hf, game, core):
    settings(core, game, min_bet=10, max_bet=100)
    assert game.place({"user": "a", "amount": 10})[0] == 409          # no game running
    game.start_game({})
    for params, text in (({"amount": 10}, "user required"), ({"user": "a"}, "amount required"),
                         ({"user": "a", "amount": "x"}, "invalid amount"), ({"user": "a", "amount": 0}, "positive"),
                         ({"user": "a", "amount": -5}, "positive"), ({"user": "a", "amount": 10.5}, "whole coins"),
                         ({"user": "a", "amount": True}, "invalid amount"), ({"user": "a", "amount": 9}, "minimum bet is 10"),
                         ({"user": "a", "amount": 101}, "max bet is 100")):
        status, body = game.place(params)
        assert status == 400 and text in body["error"], (params, body)
    assert game.ledger.last_seq == 0 and game.g["bets"] == {}          # a rejected bet moves no coins
    assert game.place({"user": "@Alice", "amount": "60"})[1]["debits"][0]["user"] == "Alice"
    status, body = game.place({"user": "Alice", "amount": 50})
    assert status == 400 and "max bet is 100" in body["error"] and "60 already down" in body["error"]
    assert game.place({"user": "Alice", "amount": 40})[0] == 200 and game.g["bets"] == {"Alice": 100}
    assert game.validate({"amount": "50"})["pays"][0] == {"slot": 0, "mult": 40, "pays": 2000}
    assert game.validate({"amount": "5"})["valid"] is False and game.validate({})["error"] == "amount required"


def test_remove_takes_the_bet_back(game):
    game.start_game({})
    game.place({"user": "bob", "amount": 40}); game.place({"user": "amy", "amount": 5})
    assert game.remove({"user": "zed"})[0] == 400 and game.remove({})[0] == 400
    status, body = game.remove({"user": "bob"})
    assert status == 200 and body["credits"] == [{"user": "bob", "amount": 40}]
    assert ledger(game)[-1] == ("credit", "bob", 40, "refund") and game.g["bets"] == {"amy": 5}
    game.skip()
    assert game.remove({"user": "amy"})[0] == 409                      # not while the token falls


def test_stop_refunds_open_bets_but_pays_a_falling_token(hf, game, monkeypatch):
    game.start_game({})
    game.place({"user": "bob", "amount": 40})
    assert game.stop() is True and game.g is None
    assert ledger(game) == [("debit", "bob", 40, "bet"), ("credit", "bob", 40, "refund")]
    assert game.history[0]["result"]["outcome"] == "stopped" and game.stats()["stopped"] == 1

    fix_path(monkeypatch, hf, [1] * 12)
    game.start_game({})
    game.place({"user": "amy", "amount": 10})
    game.skip()                                                       # the token is falling: its slot is decided
    assert game.g["phase"] == "dropping" and game.stop() is True
    assert ledger(game)[-2:] == [("debit", "amy", 10, "bet"), ("credit", "amy", 400, "payout")]   # x40, not a refund
    assert game.history[0]["result"]["outcome"] == "stopped" and game.history[0]["result"]["drops"][0]["mult"] == 40


def test_a_restart_settles_the_game_that_was_running(hf, core, game, tmp_path, monkeypatch):
    fix_path(monkeypatch, hf, [1] * 12)
    game.start_game({})
    game.place({"user": "bob", "amount": 25})
    saved = json.loads(game.path.read_text())
    assert saved["game"]["bets"] == {"bob": 25} and saved["game"]["phase"] == "betting"
    again = hf.Hexfall(path=game.path, ledger=game.ledger)            # Hexcast starts again with the same files
    assert again.g and again.g["bets"] == {"bob": 25} and again.g["rows"] == 12
    again.resume()
    assert again.g is None and ledger(again)[-1] == ("credit", "bob", 25, "refund")
    assert again.history[0]["result"]["outcome"] == "restart"

    game.start_game({})
    game.place({"user": "amy", "amount": 10})
    game.skip()                                                       # restarted while the token fell
    third = hf.Hexfall(path=game.path, ledger=game.ledger)
    assert third.g["phase"] == "dropping" and third.g["fall"]["slot"] == 12
    third.resume()
    assert ledger(third)[-1] == ("credit", "amy", 400, "payout")


def test_test_games_write_nothing_to_the_ledger(hf, game, monkeypatch):
    fix_path(monkeypatch, hf, [1] * 12)
    game.start_game({"test": True, "drops": 1})
    game.place({"user": "bob", "amount": 25})
    game.skip(); game.skip()
    assert game.g["log"][-1]["amount"] == 4 * 250 and game.g["log"][-1]["seq"] is None
    assert list(game.ledger.events) == [] and game.ledger.last_seq == 0
    game.skip()
    assert game.stats()["games"] == 0 and game.history[0]["test"] is True    # test games are not in the stats


def test_a_running_game_keeps_the_odds_it_started_with(hf, game, core, monkeypatch):
    game.start_game({"drops": 2})
    before = list(game.g["mults"])
    settings(core, game, rows=8, risk="high", multipliers=[1] * 9)
    assert game.g["mults"] == before and game.state_view()["game"]["rows"] == 12 and len(game.state_view()["game"]["slots"]) == 13
    fix_path(monkeypatch, hf, [1] * 12)
    game.place({"user": "bob", "amount": 10})
    game.skip(); game.skip()
    assert ledger(game)[-1] == ("credit", "bob", 400, "payout")       # paid by the table the game began with
    assert game.idle_view()["rows"] == 8 and game.idle_view()["source"] == "custom"   # while the next game follows the settings


def test_start_can_pick_drops_rows_risk_and_its_own_table(hf, game, core):
    ok = game.start_game({"drops": "5", "rows": 9, "risk": "HIGH", "seconds": 12})
    assert ok[0] == 200 and (ok[1]["drops"], ok[1]["rows"], ok[1]["risk"], ok[1]["source"]) == (5, 9, "high", "preset")
    assert game.g["mults"] == list(hf.preset(9, "high")) and game.g["drops"] == 5
    assert 11 <= game.state_view()["game"]["ends_in_ms"] / 1000 <= 12
    game.stop()
    assert game.start_game({"rows": 99, "drops": 0})[1]["rows"] == 16 and game.g["drops"] == 1
    game.stop()
    status, body = game.start_game({"risk": "extreme"})
    assert status == 400 and "risk" in body["error"] and game.g is None
    status, body = game.start_game({"rows": 10, "multipliers": [1, 2, 3]})
    assert status == 400 and "needs 11" in body["error"] and game.g is None
    status, body = game.start_game({"rows": 10, "multipliers": "5 2 1 .5 0 0 0 .5 1 2 5"})
    assert status == 200 and body["source"] == "custom" and game.g["mults"] == [5, 2, 1, 0.5, 0, 0, 0, 0.5, 1, 2, 5]
    assert body["rtp_pct"] == hf.table_view(10, tuple(game.g["mults"]))[1]
    game.stop()
    settings(core, game, rows=10, multipliers=[3] * 11)
    assert game.start_game({})[1]["source"] == "custom"                # the settings' own table
    game.stop()
    assert game.start_game({"risk": "low"})[1]["source"] == "preset"   # asking for a risk means the preset


def test_the_path_is_only_sent_once_the_token_is_released(hf, game, monkeypatch):
    fix_path(monkeypatch, hf, [0, 1] * 6)
    game.start_game({"drops": 2})
    game.place({"user": "bob", "amount": 10})
    v = game.state_view()
    assert v["state"] == "betting" and v["game"]["fall"] is None and v["game"]["last"] is None
    game.skip()
    fall = game.state_view()["game"]["fall"]
    assert fall["path"] == [0, 1] * 6 and fall["slot"] == 6 and fall["mult"] == 0 and fall["ms"] == 9000 and fall["drop"] == 1
    game.skip(); game.skip()                                          # result, then the next window: nothing of drop 1 is left to see
    v = game.state_view()
    assert v["game"]["phase"] == "betting" and v["game"]["fall"] is None
    assert [h["slot"] for h in v["game"]["hits"]] == [6]


def test_state_views(hf, game):
    idle = game.state_view()
    assert idle["game"] is None and idle["idle"]["rows"] == 12 and len(idle["idle"]["slots"]) == 13 and idle["idle"]["rtp_pct"] == 94.85
    game.start_game({})
    game.place({"user": "bob", "amount": 10}); game.place({"user": "amy", "amount": 30})
    g = game.state_view()["game"]
    assert [p["user"] for p in g["players"]] == ["amy", "bob"] and g["on_the_line"] == 40 and g["players"][0]["max_win"] == 1200
    assert g["rtp_pct"] == 94.85 and g["house_edge_pct"] == 5.15 and g["drop"] == 1 and g["drops"] == 3
    assert g["slots"][0]["ways"] == 1 and g["slots"][0]["of"] == 4096 and g["slots"][6]["bust"]
    assert game.table_view() == {"bets": [{"user": "bob", "amount": 10}, {"user": "amy", "amount": 30}], "total_on_table": 40,
                                 "currency": "coins", "last_seq": game.ledger.last_seq}
    assert game.user_view("@bob")["player"]["bet"] == 10 and game.user_view("zed")["player"] is None
    assert game.user_view("bob")["session"]["debits"] == 10


def test_the_game_loads_from_a_damaged_file(hf, core, tmp_path):
    path = tmp_path / "hf.json"
    path.write_text(json.dumps({"game": {"id": "hf-1", "phase": "dropping", "rows": 99, "mults": [1, 2], "bets": {"bob": 10, "amy": -4, "": 3},
                                         "fall": {"path": [1, 1], "slot": 99, "mult": 5000, "ms": 1}, "drops": 50, "drop": 77, "risk": "x",
                                         "hits": [{"slot": 400}, {"slot": 3, "bet": 9}], "best": {"user": "bob", "paid": -1}},
                                "history": [], "stats": {"games": 2, "house_net": "oops"}, "last_seq": 0, "journal": []}))
    g = hf.Hexfall(path=path, ledger=core.Ledger(tmp_path / "l.jsonl"))
    assert g.g["rows"] == 16 and g.g["mults"] == list(hf.preset(16, "medium")) and g.g["drops"] == 10 and g.g["drop"] == 10
    assert g.g["bets"] == {"bob": 10} and g.g["fall"] is None and [h["slot"] for h in g.g["hits"]] == [3] and g.g["best"] is None
    assert g.stats()["games"] == 2 and g.stats()["house_net"] == 0
    g.resume()                                                        # no path to pay: the open bet is refunded
    assert [e["reason"] for e in g.ledger.events] == ["refund"]
    path.write_text("{ not json")
    assert hf.Hexfall(path=path, ledger=core.Ledger(tmp_path / "l2.jsonl")).g is None and path.with_name("hf.json.bad").exists()


# ---------------------------------------------------------------- the add-on in the real host

def test_the_add_on_registers_with_games(world):
    c = TestClient(world.app)
    reg = c.get("/games/api/registry").json()["games"]
    assert [g["key"] for g in reg] == ["hexfall"]
    g = reg[0]
    assert g["title"] == "Hexfall" and g["order"] == 50 and g["plugin"] == "games_hexfall" and g["overlay"]["stateful"] is True
    assert g["overlay"]["script"].startswith("/plugins/games_hexfall/static/hexfall.js")
    assert g["panel_js"].startswith("/plugins/games_hexfall/static/hexfall_panel.js")
    assert g["overlay"]["defaults"]["title"] == "Hexfall" and "theme" in g["overlay"]["appearance"]
    for url in (g["overlay"]["script"], g["panel_js"]):
        r = c.get(url)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"] and len(r.text) > 2000
    assert c.get("/games/api").json()["hexfall"]["start"].startswith("GET|POST /games/api/hexfall/start")
    assert "hexfall" in c.get("/games/api/status").json()["games"]
    assert c.get("/games/api/config").json()["config"]["hexfall"]["title"] == "Hexfall"


def test_the_api_plays_a_game(world, hf):
    c = TestClient(world.app)
    assert c.get("/games/api/hexfall/table").json()["game"] is None
    assert c.post("/games/api/hexfall/bet", json={"user": "amy", "amount": 5}).status_code == 409
    r = c.post("/games/api/hexfall/start", json={"seconds": 60, "drops": 1, "test": True})
    assert r.status_code == 200 and r.json()["state"]["game"]["phase"] == "betting" and r.json()["started"].startswith("hf-")
    assert c.post("/games/api/hexfall/start", json={}).status_code == 409
    r = c.post("/games/api/hexfall/bet", json={"user": "amy", "amount": 40})
    assert r.status_code == 200 and r.json()["total"] == 40 and r.json()["player"]["bet"] == 40
    assert c.get("/games/api/hexfall/bet?user=bob&amount=60").json()["ok"]
    assert c.post("/games/api/hexfall/bet", json={"user": "amy", "amount": "lots"}).status_code == 400
    assert c.get("/games/api/hexfall/user/amy").json()["player"]["bet"] == 40
    t = c.get("/games/api/hexfall/table").json()
    assert t["game"]["on_the_line"] == 100 and t["table"]["total_on_table"] == 100 and t["game"]["fall"] is None
    assert c.post("/games/api/hexfall/remove", json={"user": "bob"}).json()["credits"] == [{"user": "bob", "amount": 60}]
    r = c.post("/games/api/hexfall/next")
    fall = r.json()["state"]["game"]["fall"]
    assert r.json()["skipped"] == "betting" and len(fall["path"]) == 12 and fall["slot"] == sum(fall["path"])
    assert fall["mult"] == hf.preset(12, "medium")[fall["slot"]]
    assert c.post("/games/api/hexfall/drop").json()["skipped"] == "dropping"        # the alias
    g = c.get("/games/api/hexfall/table").json()["game"]
    assert g["phase"] == "result" and g["last"]["slot"] == fall["slot"]
    assert c.post("/games/api/hexfall/next").json()["state"]["game"]["phase"] == "over"
    h = c.get("/games/api/hexfall/history").json()
    assert h["history"][0]["test"] is True and h["stats"]["games"] == 0
    assert c.get("/games/api/hexfall/ledger").json()["events"] == []                # a test game: no coins
    assert c.post("/games/api/hexfall/stop").json()["stopped"] == ["hexfall"]
    assert c.get("/games/api/hexfall/table").json()["game"] is None


def test_the_api_settles_real_coins_into_the_shared_ledger(world):
    c = TestClient(world.app)
    c.post("/games/api/config", json={"hexfall": {"drops": 1}})
    c.post("/games/api/hexfall/start", json={"seconds": 60})
    c.post("/games/api/hexfall/bet", json={"user": "amy", "amount": 100})
    c.post("/games/api/hexfall/next"); c.post("/games/api/hexfall/next")
    led = c.get("/games/api/ledger?game=hexfall").json()
    assert [(e["type"], e["user"], e["reason"]) for e in led["events"][:1]] == [("debit", "amy", "bet")]
    assert {e["game"] for e in led["events"]} == {"hexfall"} and all(e["roll_id"] for e in led["events"])
    assert c.get("/games/api/hexfall/ledger?since=0").json()["events"] == led["events"]
    saved = json.loads((world.config / "games_hexfall.json").read_text())
    assert saved["game"]["phase"] == "result" and saved["last_seq"] == led["last_seq"]


def test_bets_reference_lists_the_table_odds_and_rtp(world, hf):
    c = TestClient(world.app)
    b = c.get("/games/api/hexfall/bets").json()
    assert b["ok"] and b["game"] == "hexfall" and (b["rows"], b["risk"], b["source"]) == (12, "medium", "preset")
    assert b["multipliers"] == list(hf.preset(12, "medium")) and len(b["slots"]) == 13
    assert sum(s["ways"] for s in b["slots"]) == 4096 and all(s["of"] == 4096 for s in b["slots"])
    assert sum(s["probability"] for s in b["slots"]) == pytest.approx(1, abs=1e-6)
    assert b["rtp_pct"] == 94.85 and b["house_edge_pct"] == 5.15 and sum(s["rtp_pct"] for s in b["slots"]) == pytest.approx(94.85, abs=0.01)
    assert [s["mult"] for s in b["slots"]] == b["multipliers"] and b["slots"][6]["bust"] is True
    assert set(b["presets"]) == {"low", "medium", "high"} and b["presets"]["medium"]["rtp_pct"] == 94.85 and b["rules"] and b["notes"] == []
    # the settings change the table, honestly
    c.post("/games/api/config", json={"hexfall": {"rows": 8, "risk": "high"}})
    b = c.get("/games/api/hexfall/bets").json()
    assert b["rows"] == 8 and len(b["slots"]) == 9 and b["multipliers"] == list(hf.preset(8, "high")) and b["rtp_pct"] == 95.0
    c.post("/games/api/config", json={"hexfall": {"multipliers": "5 4 3 2 1 2 3 4 5"}})
    b = c.get("/games/api/hexfall/bets").json()
    assert b["source"] == "custom" and b["multipliers"] == [5, 4, 3, 2, 1, 2, 3, 4, 5]
    assert b["rtp_pct"] == hf.table_view(8, tuple(b["multipliers"]))[1] and b["rtp_pct"] > 100 and b["notes"]
    assert b["house_edge_pct"] < 0                                    # shown as it is: this table loses money for the house
    c.post("/games/api/config", json={"hexfall": {"rows": 10}})        # no longer fits: back to the preset
    assert c.get("/games/api/hexfall/bets").json()["source"] == "preset"
    v = c.get("/games/api/hexfall/validate?amount=50").json()
    assert v["valid"] and len(v["pays"]) == 11 and v["pays"][0]["pays"] == 50 * hf.preset(10, "high")[0]


def test_a_round_game_has_no_spin_or_announce(world):
    c = TestClient(world.app)
    assert c.post("/games/api/hexfall/spin").status_code == 400
    assert c.post("/games/api/hexfall/announce", json={"lines": []}).status_code == 400
    r = c.post("/games/api/hexfall/preview", json={"overrides": {"theme": "ember", "scale": 0.5, "rows": 9}, "seconds": 5})
    pv = r.json()["state"]["preview"]
    assert r.status_code == 200 and pv["overrides"] == {"theme": "ember", "scale": 0.5} and r.json()["state"]["visible"] is True
    assert c.post("/games/api/hexfall/preview/clear").json()["cleared"] is True


def test_the_js_tables_are_the_backends_tables(hf):
    js = (ROOT / "catalog" / "games_hexfall" / "static" / "hexfall.js").read_text(encoding="utf-8")
    m = re.search(r"var PRESETS = (\{.*?\});\n", js, re.S)
    assert m, "hexfall.js has no PRESETS table"
    tables = json.loads(m.group(1))
    assert {r: {int(k): v for k, v in t.items()} for r, t in tables.items()} == {r: {k: list(v) for k, v in t.items()} for r, t in hf.PRESETS.items()}
    defaults = json.loads(json.dumps(hf.OVERLAY["defaults"]))
    for key in ("theme", "title", "rows", "risk", "drops", "scale"):
        assert re.search(r"\b" + key + r": " + (r"'" + str(defaults[key]) + r"'" if isinstance(defaults[key], str) else str(defaults[key])), js), key


def test_the_manifest_and_help(world):
    entries = world.host.catalog.entries()
    m = entries["games_hexfall"].manifest
    assert (m.parent, m.order, m.category, m.name) == ("games", 50, "Games", "Hexfall") and m.help["toc"][0]["id"] == "hexfall"
    assert (entries["games_hexfall"].folder / "help.html").is_file() and "/games/api/hexfall/start" in (
        entries["games_hexfall"].folder / "help.html").read_text(encoding="utf-8")


def test_removing_and_adding_the_add_on_live(world):
    c = TestClient(world.app)
    c.post("/games/api/config", json={"hexfall": {"title": "Plinko Night", "rows": 9}})
    c.post("/games/api/hexfall/start", json={"seconds": 60})
    c.post("/games/api/hexfall/bet", json={"user": "amy", "amount": 25})
    stopped, clean = asyncio.run(world.host.unload("games_hexfall"))
    assert stopped == ["games_hexfall"] and clean
    assert c.get("/games/api/registry").json()["games"] == []
    assert c.get("/games/api/hexfall/table").status_code == 404 and "hexfall" not in c.get("/games/api/status").json()["games"]
    # the bet that was only on the line comes back to its owner ...
    led = c.get("/games/api/ledger").json()["events"]
    assert [(e["game"], e["type"], e["reason"], e["amount"]) for e in led] == [("hexfall", "debit", "bet", 25), ("hexfall", "credit", "refund", 25)]
    # ... and the settings stay in the file for when the add-on comes back
    assert json.loads((world.config / "games.json").read_text())["hexfall"]["rows"] == 9
    assert world.host.load("games_hexfall"), world.host.errors
    assert c.get("/games/api/config").json()["config"]["hexfall"]["title"] == "Plinko Night"
    assert c.get("/games/api/hexfall/table").json()["game"] is None and [g["key"] for g in c.get("/games/api/registry").json()["games"]] == ["hexfall"]


def test_ledger_recovery_replays_an_event_only_hexfalls_file_has(real_world):
    """A crash between 'save the game' and 'append to the ledger' leaves the event only in the game's journal."""
    ev = {"seq": 1, "ts": 1.0, "game": "hexfall", "type": "debit", "user": "cy", "amount": 10, "reason": "bet", "bet_id": "hf-1/d1",
          "bet": "Drop 1", "roll_id": "hf-1"}
    (real_world.config / "games_hexfall.json").write_text(json.dumps({"game": None, "last_seq": 1, "journal": [ev]}))
    real_world.installer.install("games")                              # note: the add-on itself is NOT installed
    real_world.host.load_all()
    led = TestClient(real_world.app).get("/games/api/ledger").json()
    assert [(e["seq"], e["game"], e["user"], e["amount"]) for e in led["events"]] == [(1, "hexfall", "cy", 10)] and led["last_seq"] == 1


# ---------------------------------------------------------------- the browser scripts (checked with node when it is installed)

def node_json(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


STATIC = ROOT / "catalog" / "games_hexfall" / "static"


def test_the_overlay_animates_exactly_the_servers_path():
    """Whatever the seed (cosmetic jitter) and the fall time: the token touches the pegs of the path and ends in its slot."""
    out = node_json("""
      const hf = require(%s);
      let a = 12345; const rnd = () => { a = (a * 1664525 + 1013904223) >>> 0; return a / 4294967296; };
      const bad = []; let checked = 0;
      for (const rows of [8, 9, 12, 15, 16]) for (let n = 0; n < 40; n++) {
        const path = Array.from({length: rows}, () => rnd() < 0.5 ? 0 : 1), slot = path.reduce((x, y) => x + y, 0);
        for (const seed of [0, 1, 7, 4242, 999999, 2147483647]) for (const D of [6, 9, 20]) {
          const p = hf.plan(rows, path, seed, D); checked++;
          if (p.slot !== slot) bad.push(['slot', rows, seed, D]);
          let j = 0;
          p.pegs.forEach((pg, i) => { if (pg[0] !== i || pg[1] !== j) bad.push(['peg', rows, i, seed]); j += path[i]; });
          for (let i = 0; i < rows; i++) {
            const q = p.at(p.impacts[i] + 1e-9);
            if (Math.abs(q.x - p.pegs[i][2]) > 1e-6 || !(q.y < p.pegs[i][3])) bad.push(['impact', rows, i, seed, D]);
            if (!(p.impacts[i + 1] > p.impacts[i])) bad.push(['order', rows, i]);
          }
          if (Math.abs(p.at(D - 0.01).x - p.slotX) > 1e-6) bad.push(['end', rows, seed, D]);
          if (!(p.impacts[rows] < D && p.impacts[rows] > D - 1.5)) bad.push(['lands', rows, p.impacts[rows], D]);
          for (let t = 0; t < D; t += 0.05) { const q = p.at(t); if (!isFinite(q.x) || !isFinite(q.y)) bad.push(['finite', rows, t]); }
        }
      }
      console.log(JSON.stringify({checked, bad: bad.slice(0, 5)}));
    """ % json.dumps(str(STATIC / "hexfall.js")))
    assert out["checked"] == 5 * 40 * 18 and out["bad"] == []


def test_the_renderer_and_the_backend_agree_on_defaults_and_themes(hf):
    out = node_json("""
      const hf = require(%s);
      console.log(JSON.stringify({w: hf.BASE_W, h: hf.BASE_H, appearance: hf.APPEARANCE, defaults: hf.DEFAULTS, themes: Object.keys(hf.THEMES),
        tiers: Object.keys(hf.THEMES).map(t => Object.keys(hf.THEMES[t].tier).sort())}));
    """ % json.dumps(str(STATIC / "hexfall.js")))
    assert (out["w"], out["h"]) == (1280, 860)
    assert out["appearance"] == list(hf.Hexfall.APPEARANCE) == hf.OVERLAY["appearance"]
    assert out["defaults"] == hf.OVERLAY["defaults"] and out["themes"] == list(hf._HF_THEMES)
    assert all(t == ["bust", "good", "hot", "jack", "low", "mid"] for t in out["tiers"])
    for key, value in hf.OVERLAY["defaults"].items():                  # the overlay's defaults are the settings' defaults
        assert hf.Hexfall.DEFAULTS[key] == value, key


def test_the_panel_settings_match_the_backend_schema(hf):
    out = node_json("""
      const vm = require('vm'), fs = require('fs');
      let def = null;
      const RC = {register: d => { def = d; }, num: (v, d) => { v = parseFloat(v); return isFinite(v) ? v : d; }, clamp: (v, a, b) => Math.max(a, Math.min(b, v)),
                  fmt: String, signed: String, sum: () => 0, roundState: (g, i) => ({game: g, idle: i})};
      const sandbox = {window: {GamesPage: {round: RC, esc: s => String(s), toast() {}}}, console};
      vm.createContext(sandbox);
      vm.runInContext(fs.readFileSync(%s, 'utf8'), sandbox);
      console.log(JSON.stringify({key: def.key, appearance: def.appearance, look: def.look, base: def.base, settings: def.settings, themes: def.themes,
        reasons: def.reasons, extraChecks: def.extraChecks, fieldMax: def.fieldMax, doc: def.doc}));
    """ % json.dumps(str(STATIC / "hexfall_panel.js")))
    cls = hf.Hexfall
    assert out["key"] == "hexfall" and out["doc"] == "hexfall" and out["base"] == [1280, 860]
    assert out["appearance"] == list(cls.APPEARANCE)
    assert out["look"] == {k: cls.DEFAULTS[k] for k in cls.APPEARANCE}                       # the editor's Reset = the settings' defaults
    assert [t[0] for t in out["themes"]] == list(hf._HF_THEMES) and set(out["extraChecks"]) == {"show_odds", "show_history"}
    keys = [s[0] for s in out["settings"]]
    assert len(keys) == len(set(keys)) and set(keys) <= set(cls.DEFAULTS)
    assert set(cls.DEFAULTS) - set(cls.APPEARANCE) <= set(keys)                              # every non-look setting has a field
    for key, label, kind, *extra in out["settings"]:
        spec, extra = cls.SCHEMA[key], (extra[0] if extra else None)
        if kind == "bool":
            assert spec == ("bool",), key
        elif kind in ("int", "num"):
            assert spec[0] == kind and extra == [spec[1], spec[2]], key
        elif kind == "text":
            assert spec[0] in ("str", "name") and extra == spec[1], key
        elif kind == "select":
            assert spec[0] == "enum" and [o[0] for o in extra] == list(spec[1]), key
        elif kind == "clip":
            assert spec[0] == "str", key
        else:
            raise AssertionError((key, kind))
