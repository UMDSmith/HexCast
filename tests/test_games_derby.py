"""Hellhound Derby (the games_derby add-on of Games), running from the real catalog/ folder: the odds and the
form book, the draw, the race script (it can never change the drawn order), bets, settlement and the ledger,
config validation, the HTTP API and the game flow."""

import json
import math
import random
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT

DERBY_DIR = ROOT / "catalog" / "games_derby"


# ---- helpers ---------------------------------------------------------------------------------------

@pytest.fixture
def w(real_world, tmp_path):
    """Games + Hellhound Derby installed and running; w.derby / w.core are the plugin modules."""
    real_world.installer.install("games")
    real_world.installer.install("games_derby")
    real_world.host.load_all()
    assert "games_derby" in real_world.host.loaded, real_world.host.errors
    ns = SimpleNamespace(world=real_world, app=real_world.app, tmp=tmp_path)
    ns.derby = sys.modules["hexcast_plugins.games_derby.derby"]
    ns.core = sys.modules["hexcast_plugins.games.core"]
    ns.client = TestClient(real_world.app)
    return ns


def new_game(w, **config):
    """A separate game object (own file, own ledger, own form book) so a test can look at everything it does.
    (No guest racers unless the test asks: one could take the place of a hound it looks for.)"""
    w.core.CONFIG["derby"].update({"guest_chance": 0, **config})
    return w.derby.Derby(path=w.tmp / "games_derby.json", ledger=w.core.Ledger(w.tmp / "ledger.jsonl"),
                         form=w.derby.FormBook(w.tmp / "form.json"))


def fix_order(w, monkeypatch, order):
    """The draw: runner indexes in finishing order (the winner first)."""
    monkeypatch.setattr(w.derby, "draw_order", lambda chances, rng=None: list(order))


def to_phase(g, phase, limit=8):
    for _ in range(limit):
        if g.phase == phase:
            return
        status, body = g.skip()
        assert status == 200, body
    raise AssertionError(f"never reached {phase}: stuck in {g.phase}")


def place(g, user, amount, number):
    status, body = g.place({"user": user, "amount": amount, "number": number})
    assert status == 200, body
    return body


def play_out(g):
    for _ in range(10):
        if g.g is None:
            return
        g.skip()
    raise AssertionError("the game never ended")


def field(n=6):
    return [{"num": i + 1, "id": f"h{i}", "name": f"Hound {i}", "short": f"H{i}"} for i in range(n)]


# ---- the roster ------------------------------------------------------------------------------------------

def test_the_roster_and_lines_ship_with_the_addon(w):
    ids = [h["id"] for h in w.derby.ROSTER]
    assert len(ids) >= 6 and len(set(ids)) == len(ids)
    assert {"cinder", "terry", "harry", "bubba"} <= set(ids)
    data = json.loads((DERBY_DIR / "static" / "derby_hounds.json").read_text(encoding="utf-8"))
    for h in data["hounds"]:
        assert (DERBY_DIR / "static" / h["sheet"]["file"]).is_file(), h["id"]
        assert len(h["anchors"]["frames"]) == h["sheet"]["frames"] and h["anchors"]["stride"] > 0
    assert w.derby.LINES["trash"] and w.derby.LINES["ticker"]["trip"]


# ---- odds ----------------------------------------------------------------------------------------------

def test_odds_maths(w):
    d = w.derby
    assert d.mult_cents(0.5, 5) == 190 and d.mult_cents(0.2, 5) == 475 and d.mult_cents(1 / 6, 5) == 570
    assert d.mult_cents(0.99, 5) == 101                                              # never under x1.01
    assert d.db_pay(100, 475) == 475 and d.db_pay(7, 333) == 23                      # rounded down
    assert d.db_pay(100, 475, cap=200) == 200 and d.db_pay(500, 475, cap=200) == 500  # a cap never takes the stake


def test_a_new_field_has_numbers_odds_and_form(w):
    g = new_game(w, luck=0)
    g.start_game({})
    f = g.g["field"]
    assert [h["num"] for h in f] == [1, 2, 3, 4, 5, 6] and len({h["id"] for h in f}) == 6
    assert len({h["row"] for h in f}) == 6 and all(0 <= h["row"] < 6 for h in f)
    assert abs(sum(h["chance"] for h in f) - 1) < 1e-4
    assert all(h["cents"] == w.derby.mult_cents(h["chance"], 5) for h in f)
    assert all(h["note"] == "first race" for h in f)
    # no form and no luck: every hound the same
    assert len({h["cents"] for h in f}) == 1


def test_form_moves_the_odds(w):
    g = new_game(w, luck=0, field_size=3, benched="")
    book = g.form
    for _ in range(4):
        book.record(["cinder", "gnash", "mange"])
    assert book.get("cinder") == {"races": 4, "wins": 4, "last": [1, 1, 1, 1], "streak": 4}
    assert book.get("mange")["streak"] == -4 and book.note("cinder") == "won the last 4" and book.note("mange") == "4 races without a win"
    assert book.strength("cinder") > book.strength("gnash") > 0
    g.cfg["benched"] = ",".join(h["id"] for h in w.derby.ROSTER if h["id"] not in ("cinder", "gnash", "mange"))
    g.start_game({})
    by = {h["id"]: h for h in g.g["field"]}
    assert by["cinder"]["chance"] > by["gnash"]["chance"] and by["cinder"]["cents"] < by["mange"]["cents"]
    # form_weight 0: form doesn't count
    g.stop()
    g.cfg["form_weight"] = 0
    g.start_game({})
    assert len({h["cents"] for h in g.g["field"]}) == 1
    # the book survives on disk and clears
    again = w.derby.FormBook(w.tmp / "form.json")
    assert again.get("cinder")["wins"] == 4
    again.clear()
    assert w.derby.FormBook(w.tmp / "form.json").get("cinder")["races"] == 0


def test_the_winner_is_drawn_with_the_shown_chances(w):
    rng = random.Random(7)
    chances = [0.5, 0.3, 0.2]
    wins = [0, 0, 0]
    for _ in range(20000):
        order = w.derby.draw_order(chances, rng)
        assert sorted(order) == [0, 1, 2]
        wins[order[0]] += 1
    for i, c in enumerate(chances):
        assert abs(wins[i] / 20000 - c) < 0.015, wins


# ---- the race script ------------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3, 42, 777, 2024])
def test_the_script_keeps_the_drawn_order(w, seed):
    d = w.derby
    order = [3, 0, 5, 1, 4, 2]
    s = d.build_script(field(), order, 45, seed)
    T = {r["i"]: r["T"] for r in s["runners"]}
    assert [T[i] for i in order] == sorted(T[i] for i in order) and len(set(T.values())) == 6
    assert s["order"] == order and 45 <= T[order[0]] <= 45 * 1.05
    # each hound's run, replayed the way the overlay does (events included), crosses the line exactly at its
    # time - so the order on screen is the drawn order
    at_line = {}
    for r in s["runners"]:
        xs, warp = d.replay(s, r["i"])
        assert d.sample(xs, r["T"] * warp) == pytest.approx(s["length"], abs=1e-6)
        assert d.sample(xs, (r["T"] - 0.05) * warp) < s["length"]
        at_line[r["i"]] = r["T"]
    assert sorted(at_line, key=at_line.get) == order
    # every event is inside the race, names real runners and carries its speed curve
    for e in s["events"]:
        assert 2 <= e["t"] <= T[order[0]] and 0 <= e["a"] < 6 and e["type"] in d.EV and e["me"] == d.EV[e["type"]]["me"]
        if d.EV[e["type"]].get("them"):
            assert e["b"] is not None and e["b"] != e["a"]
        assert e.get("ticker")
    assert s["duration_ms"] == round((d.DB_GATE_S + max(T.values()) + (d.DB_PHOTO_S if s["photo"] else 0) + d.DB_TAIL_S) * 1000)
    # the same seed, the same race
    assert d.build_script(field(), order, 45, seed) == s


def test_photo_finishes_happen_and_name_the_margin(w):
    d = w.derby
    photos = [d.build_script(field(), [0, 1, 2, 3, 4, 5], 45, seed)["photo"] for seed in range(60)]
    got = [p for p in photos if p]
    assert got, "no photo finish in 60 races"
    for p in got:
        assert p["kind"] in ("win", "last") and p["label"] in ("A WHISKER", "A NOSE", "A TONGUE", "A HEAD")
        if p["kind"] == "win":
            assert (p["a"], p["b"]) == (0, 1)
        else:
            assert (p["a"], p["b"]) == (5, 4)


def test_the_speed_curves(w):
    c = w.derby.curve
    assert c(["tumble"], 0.1) == 0.04 and c(["tumble"], 1.0) == pytest.approx(1.0)
    assert c(["hold", 0.8, 0.05], 0.5) == 0.05 and c(["hold", 0.8, 0.05], 0.9) == 1
    assert c(["ramp", 0.3], 0) == 0.3 and c(["ramp", 0.3], 1) == pytest.approx(1)
    assert c(["femur"], 0.1) < 0 and c(["scorch"], 0.5) > 1 and c(["const", 0.5], 0.3) == 0.5


# ---- a game, step by step ----------------------------------------------------------------------------------

def test_a_full_game_pays_exactly_what_the_odds_say(w, monkeypatch):
    g = new_game(w)
    g.start_game({})
    f = g.g["field"]
    fix_order(w, monkeypatch, [2, 0, 1, 3, 4, 5])               # #3 wins
    a = place(g, "alice", 100, 3)
    assert a["mult"] == f[2]["cents"] / 100 and a["pays"] == 100 * f[2]["cents"] // 100 and a["debits"][0]["reason"] == "bet"
    place(g, "alice", 50, "#3")                                 # the same hound again adds
    place(g, "alice", 20, 1)
    place(g, "bob", 10, f[2]["id"])                             # by name
    to_phase(g, "racing")
    assert g.g["script"]["order"] == [2, 0, 1, 3, 4, 5]
    to_phase(g, "result")
    last = g.g["last"]
    assert last["winner"]["num"] == 3 and last["order"] == [3, 1, 2, 4, 5, 6]
    pays = {wn["user"]: wn["pays"] for wn in last["winners"]}
    assert pays == {"alice": 150 * f[2]["cents"] // 100, "bob": 10 * f[2]["cents"] // 100}
    assert last["losers"] == [{"user": "alice", "num": 1, "amount": 20}]
    reasons = [(e["user"], e["reason"], e["amount"]) for e in g.ledger.query(0, 500)[0]]
    assert reasons == [("alice", "bet", 100), ("alice", "add", 50), ("alice", "bet", 20), ("bob", "bet", 10),
                       ("alice", "win", pays["alice"]), ("bob", "win", pays["bob"])]
    winner_id = f[2]["id"]
    assert g.form.get(winner_id)["wins"] == 1 and g.form.get(winner_id)["streak"] == 1
    assert g.form.get(f[5]["id"])["last"] == [6]
    s = g.history[0]["result"]
    assert s["winner"]["num"] == 3 and s["total_bet"] == 180 and s["total_paid"] == sum(pays.values())
    assert g.stats()["races"] == 1 and g.stats()["wins"] == {winner_id: 1}
    play_out(g)
    assert g.g is None


def test_a_test_game_writes_no_ledger_and_no_form(w, monkeypatch):
    g = new_game(w)
    g.start_game({"test": True})
    fix_order(w, monkeypatch, [0, 1, 2, 3, 4, 5])
    place(g, "al", 10, 1)
    to_phase(g, "result")
    assert g.ledger.query(0, 500)[0] == [] and all(e["seq"] is None and e["test"] for e in g.g["log"])
    assert g.form.data == {} and g.stats()["races"] == 0


def test_stopping_refunds_an_open_window_and_settles_a_drawn_race(w, monkeypatch):
    g = new_game(w)
    g.start_game({})
    place(g, "al", 30, 2)
    assert g.stop()
    ev = g.ledger.query(0, 500)[0]
    assert [(e["reason"], e["amount"]) for e in ev] == [("bet", 30), ("refund", 30)]
    g.start_game({})
    fix_order(w, monkeypatch, [1, 0, 2, 3, 4, 5])
    place(g, "al", 30, 2)
    to_phase(g, "racing")
    assert g.stop()
    ev = g.ledger.query(0, 500)[0]
    cents = round(g.history[0]["result"]["field"][1]["mult"] * 100)
    assert ev[-1]["reason"] == "win" and ev[-1]["amount"] == 30 * cents // 100     # a drawn race counts
    assert g.history[0]["result"]["winner"]["num"] == 2


def test_no_bets(w):
    g = new_game(w, run_without_bets=False)
    g.start_game({})
    g.skip()
    assert g.phase == "result" and g.g["outcome"] == "no_bets"
    play_out(g)
    g2 = new_game(w, run_without_bets=True)
    g2.start_game({})
    g2.skip()
    assert g2.phase == "racing" and g2.g["script"]


def test_bets_are_checked(w):
    g = new_game(w, min_bet=5, max_bet=100)
    assert g.place({"user": "al", "amount": 10, "number": 1})[0] == 409            # no game
    g.start_game({})
    bad = [({"amount": 10, "number": 1}, "user required"), ({"user": "al", "amount": 10}, "hound required"),
           ({"user": "al", "amount": 10, "number": 9}, "from 1 to 6"), ({"user": "al", "amount": 10, "number": "nobody"}, "no hound called"),
           ({"user": "al", "amount": 2, "number": 1}, "5"), ({"user": "al", "amount": 101, "number": 1}, "100")]
    for params, words in bad:
        status, body = g.place(params)
        assert status == 400 and words in body["error"], (params, body)
    assert place(g, "al", 60, "no. 4")["num"] == 4
    assert g.place({"user": "al", "amount": 50, "number": 5})[0] == 400              # over the max per player
    status, body = g.remove({"user": "al", "number": 4})
    assert status == 200 and body["credits"] == [{"user": "al", "amount": 60}]
    assert g.remove({"user": "al"})[0] == 400
    g.skip()
    assert g.place({"user": "al", "amount": 10, "number": 1})[1]["error"] == "bets_closed"


def test_a_saved_game_survives_garbage(w):
    g = new_game(w)
    assert g.clean_game({"id": "db-x", "phase": "betting", "field": "nope"}) is None
    raw = {"id": "db-x", "phase": "racing", "field": [{"id": "cinder", "row": 9, "chance": 0.4, "cents": 237},
                                                     {"id": "gnash"}, {"id": "terry"}, {"id": "who?"}],
           "bets": {"@al": [{"num": 2, "amount": 5}, {"num": 99, "amount": 5}, "x"], "": [{"num": 1, "amount": 1}]},
           "script": {"runners": "bad"}}
    got = g.clean_game(raw)
    assert [h["id"] for h in got["field"]] == ["cinder", "gnash", "terry"] and got["field"][0]["row"] == 3
    assert got["bets"] == {"al": [{"num": 2, "amount": 5}]} and got["script"] is None
    g.g = got
    assert g.open_settlement()[0]["reason"] == "refund"         # no script to settle with: the stakes come back


def test_config_is_validated(w):
    g = new_game(w)
    out = g.validate_config({"field_size": 12, "race_seconds": 2, "house_edge_pct": -3, "min_bet": 50, "max_bet": 10, "nope": 1})
    assert out["field_size"] == 6 and out["race_seconds"] == 25 and out["house_edge_pct"] == 0 and out["max_bet"] == 0
    assert "nope" not in out
    d = w.derby
    cfg = g.validate_config({})
    assert set(cfg) == set(d.Derby.DEFAULTS) and set(d.Derby.SCHEMA) == set(d.Derby.DEFAULTS)
    assert cfg["theme"] == "inferno" and cfg["title"] == "Hellhound Derby" and cfg["show_talk"] is True and cfg["clean_language"] is False
    bad = g.validate_config({"theme": "pink", "players_max": 0, "sfx_volume": 3, "show_crowd": "maybe", "title": "  "})
    assert bad["theme"] == "inferno" and bad["players_max"] == 1 and bad["sfx_volume"] == 1 and bad["show_crowd"] is True
    assert bad["title"] == "Hellhound Derby"
    assert g.validate_config({"theme": "ABYSS", "clean_language": "on"})["theme"] == "abyss"
    # the overlay's own copy of the look: the same keys, the same defaults
    assert d.Derby.APPEARANCE == tuple(d.OVERLAY["appearance"])
    for k, v in d.OVERLAY["defaults"].items():
        assert d.Derby.DEFAULTS[k] == v, k


def test_config_is_saved_per_game_and_a_preview_takes_the_look_only(w):
    r = w.client.post("/games/api/config", json={"derby": {"theme": "sulfur", "field_size": 4, "x": 12}})
    assert r.status_code == 200 and r.json()["config"]["derby"]["theme"] == "sulfur"
    saved = json.loads((w.world.config / "games.json").read_text(encoding="utf-8"))
    assert saved["derby"]["field_size"] == 4 and saved["derby"]["x"] == 12
    r = w.client.post("/games/api/derby/preview", json={"overrides": {"theme": "abyss", "field_size": 3, "clean_language": True}, "seconds": 3})
    assert r.status_code == 200 and r.json()["state"]["preview"]["overrides"] == {"theme": "abyss", "clean_language": True}
    assert w.client.post("/games/api/derby/preview/clear").json()["cleared"] is True


# ---- the add-on itself ------------------------------------------------------------------------------

def test_the_addon_registers_like_the_other_games(w):
    reg = {g["key"]: g for g in w.client.get("/games/api/registry").json()["games"]}
    g = reg["derby"]
    assert g["plugin"] == "games_derby" and g["order"] == 80 and g["title"] == "Hellhound Derby"
    assert g["panel_js"].startswith("/plugins/games_derby/static/derby_panel.js")
    assert g["overlay"]["script"].startswith("/plugins/games_derby/static/derby.js")
    assert g["overlay"]["stateful"] is True and g["overlay"]["appearance"] == list(w.derby.Derby.APPEARANCE)
    for url in (g["panel_js"], g["overlay"]["script"], "/plugins/games_derby/static/derby_hounds.json",
                "/plugins/games_derby/static/derby_lines.json", "/plugins/games_derby/static/hounds/cinder.webp",
                "/plugins/games_derby/static/crowd/warrok.webp", "/plugins/games_derby/static/guests/hamageddon.webp"):
        r = w.client.get(url)
        assert r.status_code == 200 and len(r.content) > 2000, url
    assert w.client.get("/plugins/games_derby/static/derby_credits.json").json()["credits"]      # the Credits card's
    crowd = w.client.get("/plugins/games_derby/static/derby_crowd.json").json()
    assert len(crowd["people"]) >= 6 and [m[0] for m in crowd["moves"]][-1] == "throw"
    for p in crowd["people"]:                                   # every sheet the crowd file names is there
        assert w.client.get("/plugins/games_derby/static/" + p["file"]).status_code == 200, p["id"]
    api = w.client.get("/games/api").json()
    assert "derby" in api["games"] and "start" in api["derby"] and "bet" in api["derby"] and "form" in api["derby"]
    assert api["appearance_keys"]["derby"] == list(w.derby.Derby.APPEARANCE)
    assert "derby" in w.client.get("/games/api/status").json()["games"]
    assert w.client.get("/games/api/derby/spin").status_code == 400                       # a round game starts with /start
    assert w.client.get("/games/api/derby/bets").json()["rules"]
    # a removed game takes its tab with it
    import asyncio
    stopped, clean = asyncio.run(w.world.host.unload("games_derby"))
    assert stopped == ["games_derby"] and clean
    assert "derby" not in [x["key"] for x in w.client.get("/games/api/registry").json()["games"]]


def test_manifest_help_docs_and_credits(w):
    manifest = json.loads((DERBY_DIR / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "games_derby" and manifest["parent"] == "games" and manifest["order"] == 80
    assert manifest["category"] == "Games" and manifest["description"] and manifest["help"]["toc"][0]["id"] == "derby"
    help_html = (DERBY_DIR / "help.html").read_text(encoding="utf-8")
    assert 'id="derby"' in help_html and "/games/api/derby/bet" in help_html
    docs = (ROOT / "docs" / "derby.md").read_text(encoding="utf-8")
    assert "/games/api/derby/bet" in docs and "form" in docs.lower() and "photo finish" in docs.lower()
    # the models are CC-BY: the credits ship with the add-on (a file, and the Credits card at the bottom of the tab)
    credits = json.loads((DERBY_DIR / "static" / "derby_credits.json").read_text(encoding="utf-8"))["credits"]
    for text in (json.dumps(credits), (DERBY_DIR / "static" / "CREDITS.md").read_text(encoding="utf-8")):
        assert "shadow798" in text and "University of Edinburgh" in text and "CC BY 4.0" in text and "Mixamo" in text
        assert "Lexington Dath" in text                          # the guest racer Hamageddon
    shipped = json.loads((DERBY_DIR / "static" / "derby_guests.json").read_text(encoding="utf-8"))["guests"]
    assert {"Guest racer: " + g["name"] for g in shipped} <= {c["for"] for c in credits}     # every guest racer's model
    assert all(c["for"] and c["title"] and c["author"] and c["url"].startswith("https://") and c["licence"] for c in credits)
    assert all(c.get("licence_url") for c in credits if c["licence"].startswith("CC"))
    panel = (DERBY_DIR / "static" / "derby_panel.js").read_text(encoding="utf-8")
    assert "footer: creditsCard" in panel and "derby_credits.json" in panel and "Credits" in help_html
    entry = w.world.host.catalog.entries()["games_derby"]
    assert entry.manifest.parent == "games" and "games" in entry.manifest.requires


# ---- the overlay (node) ---------------------------------------------------------------------------------------

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_scripts_of_the_overlay_parse():
    for name in ("derby.js", "derby_panel.js"):
        out = subprocess.run([NODE, "--check", str(DERBY_DIR / "static" / name)], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_overlay_replays_exactly_the_servers_race(w):
    """derby.js re-runs every hound's run from the script: it must be the server's run to the last digit, or a
    hound could cross the line on screen in a different place than the order that was paid."""
    d = w.derby
    rng = random.Random(11)
    races = []
    for _ in range(12):
        order = rng.sample(range(6), 6)
        s = d.build_script(field(), order, rng.choice([25, 45, 90]), rng.getrandbits(31))
        races.append({"script": s, "py": [[d.sample(xs, t * warp) for t in (0.5, 5, 13.7, s["runners"][i]["T"])]
                                          for i, (xs, warp) in enumerate(d.replay(s, i) for i in range(6))]})
    runner = r"""
      const API = require(process.argv[1]);
      const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));
      let worst = 0, checked = 0, bad = [];
      for (const {script, py} of data) {
        const crossed = [];
        script.runners.forEach((r, i) => {
          const rp = API.replay(script, i);
          [0.5, 5, 13.7, r.T].forEach((t, k) => { worst = Math.max(worst, Math.abs(API.sample(rp.xs, t * rp.warp, script.dt) - py[i][k])); checked++; });
          crossed.push([r.T, i]);
        });
        crossed.sort((a, b) => a[0] - b[0]);
        if (JSON.stringify(crossed.map(c => c[1])) !== JSON.stringify(script.order)) bad.push(['order', script.seed]);
        // the whole race plays without a broken number (no art in node: the fallbacks too)
        const R = API.buildRace({ id: 'db-test', field: [], script });
        for (let t = -3.2; t < Math.max(...script.runners.map(r => r.T)) + 2; t += 0.1) {
          API.raceAt(R, t);
          for (const r of R.runners) if (!Number.isFinite(r.x) || !Number.isFinite(r.f) || !Number.isFinite(R.cam)) { bad.push(['nan', script.seed, t]); break; }
        }
        if (!R.talk.length || !R.ticker.length) bad.push(['quiet', script.seed]);
      }
      console.log(JSON.stringify({ worst, checked, bad: bad.slice(0, 10) }));
    """
    out = subprocess.run([NODE, "-e", runner, str(DERBY_DIR / "static" / "derby.js")], input=json.dumps(races),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["checked"] == 12 * 6 * 4 and res["bad"] == [] and res["worst"] < 1e-6, res


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_crowd_throws_the_junk(w):
    """Every piece of junk (the lobs and the scripted hits) is thrown by a monster in the front row: it plays its
    throw with the release frame at the moment the junk leaves, and the junk starts at its hand, in the stands."""
    d = w.derby
    rng = random.Random(5)
    scripts = [d.build_script(field(), rng.sample(range(6), 6), 45, rng.getrandbits(31)) for _ in range(6)]
    runner = r"""
      const API = require(process.argv[1]), fs = require('fs'), path = require('path');
      const C = JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]), 'derby_crowd.json'), 'utf8'));
      C.row = {}; C.moves.forEach((m, i) => { C.row[m[0]] = i; });
      API.ASSETS.crowd = C;
      const K = API.crowd, cfg = Object.assign({}, API.DEFAULTS), bad = [];
      let n = 0;
      for (const script of JSON.parse(fs.readFileSync(0, 'utf8'))) {
        const R = API.buildRace({ id: 'crowd-test', field: [], script });
        const junk = R.lobs.concat(R.hazards.filter(z => z.kind === 'thrown'));
        for (const j of junk) {
          n++;
          const mv = K.folkMove(R, K.FRONT, j.th.m, j.t0 + 0.01, 'race', cfg);
          if (mv[0] !== 'throw' || mv[1] !== C.release) bad.push(['move', script.seed, j.t0, mv]);
          const early = K.folkMove(R, K.FRONT, j.th.m, j.t0 - 0.2, 'race', cfg);
          if (early[0] !== 'throw' || early[1] >= C.release) bad.push(['windup', script.seed, j.t0, early]);
          API.raceAt(R, j.t0);
          const at = K.throwFrom(j.th, R.cam);
          if (!(at[0] > -60 && at[0] < API.BASE_W + 60 && at[1] > 440 && at[1] < 600)) bad.push(['hand', script.seed, j.t0, at]);
        }
        cfg.show_crowd = false;     // lobs hidden: nobody mimes a throw for them (a scripted hit still is thrown)
        for (const j of R.lobs) {
          const mv = K.folkMove(R, K.FRONT, j.th.m, j.t0 + 0.01, 'race', cfg);
          if (mv[0] === 'throw' && !(R.throws[j.th.m.i] || []).some(x => !x.lob && Math.abs(x.t0 - j.t0) < 0.6)) bad.push(['hidden', script.seed, j.t0]);
        }
        cfg.show_crowd = true;
      }
      console.log(JSON.stringify({ n, bad: bad.slice(0, 10) }));
    """
    out = subprocess.run([NODE, "-e", runner, str(DERBY_DIR / "static" / "derby.js")], input=json.dumps(scripts),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["n"] > 30 and res["bad"] == [], res


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_junk_has_sprites_lines_and_its_own_comments(w):
    """Everything the crowd throws is junk the overlay knows, with a sprite, a credit and its own lines; the hound
    it hits usually says something about what it was ("WHO THE HELL THROWS A SWORD?!"), and so do near misses."""
    d = w.derby
    js = (DERBY_DIR / "static" / "derby.js").read_text(encoding="utf-8")
    assert re.findall(r"'([^']+)'", re.search(r"var JUNK_NAMES = \[(.*?)\];", js, re.S).group(1)) == list(d.JUNK)
    props = {p["name"]: p for p in json.loads((DERBY_DIR / "static" / "derby_props.json").read_text(encoding="utf-8"))["props"]}
    credits = {c["for"] for c in json.loads((DERBY_DIR / "static" / "derby_credits.json").read_text(encoding="utf-8"))["credits"]}
    for name in d.JUNK:
        assert name in props and (DERBY_DIR / "static" / props[name]["file"]).is_file(), name
        assert "Junk: " + name.lower() in credits, name
        assert d.LINES["thrown"][name] and d.LINES["eaten"][name], name
    rng = random.Random(9)
    scripts = [d.build_script(field(), rng.sample(range(6), 6), 45, rng.getrandbits(31)) for _ in range(40)]
    said = [(e["obj"], e["line"]) for s in scripts for e in s["events"] if e.get("obj") and e.get("line")]
    own = sum(line in d.LINES["thrown"][obj] for obj, line in said)
    assert len(said) > 30 and 0.5 < own / len(said) < 0.9, (own, len(said))
    runner = r"""
      const API = require(process.argv[1]), fs = require('fs'), path = require('path');
      API.ASSETS.lines = JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]), 'derby_lines.json'), 'utf8'));
      const said = [];
      for (const script of JSON.parse(fs.readFileSync(0, 'utf8'))) {
        const R = API.buildRace({ id: 'junk-test', field: [], script });
        R.lobs.forEach(j => { if (j.says) said.push([j.obj, j.says]); });
      }
      console.log(JSON.stringify(said));
    """
    out = subprocess.run([NODE, "-e", runner, str(DERBY_DIR / "static" / "derby.js")], input=json.dumps(scripts[:10]),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    misses = json.loads(out.stdout.strip().splitlines()[-1])
    own = sum(line in d.LINES["thrown"][obj] for obj, line in misses)
    rest = [line for obj, line in misses if line not in d.LINES["thrown"][obj]]
    assert len(misses) > 20 and own > len(misses) / 3 and set(rest) <= set(d.LINES["react"]["miss"]), (own, len(misses))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_no_hound_talks_to_itself(w):
    """A line that names a hound is said only while that hound races, and never by the hound itself: not in the
    events (the server), not in the trash talk, retorts or near misses (the overlay). ("GNASH, THIS IS YOUR
    SPIT, ISN'T IT?!" used to come out of Gnash.)"""
    d = w.derby

    def named(text):
        return {h["id"] for h in d.ROSTER
                if any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text, re.I) for n in {h["name"], h["short"]})}
    rng = random.Random(11)
    races, checked = [], 0
    for _ in range(60):
        f = [{"num": i + 1, "id": h["id"], "name": h["name"], "short": h["short"], "streak": rng.randint(-6, 4)}
             for i, h in enumerate(rng.sample(d.ROSTER, 6))]
        s = d.build_script(f, rng.sample(range(6), 6), 45, rng.getrandbits(31))
        races.append({"field": f, "script": s})
        for e in s["events"]:
            by = f[e["b"] if e.get("by") == "b" else e["a"]]["id"]
            for text in ([e["line"]] if e.get("line") else []) + (e.get("lines") or []):
                checked += 1
                assert by not in named(text) and named(text) <= {h["id"] for h in f} and "{t}" not in text, (by, text)
    assert checked > 100
    runner = r"""
      const API = require(process.argv[1]), fs = require('fs'), path = require('path'), dir = path.dirname(process.argv[1]);
      API.ASSETS.lines = JSON.parse(fs.readFileSync(path.join(dir, 'derby_lines.json'), 'utf8'));
      API.ASSETS.byId = {};
      JSON.parse(fs.readFileSync(path.join(dir, 'derby_hounds.json'), 'utf8')).hounds.forEach(h => { API.ASSETS.byId[h.id] = h; });
      const said = [];
      for (const race of JSON.parse(fs.readFileSync(0, 'utf8'))) {
        const R = API.buildRace({ id: 'talk-test', field: race.field, script: race.script });
        R.talk.forEach(b => said.push([b.r.id, b.text, race.field.map(f => f.id)]));
      }
      console.log(JSON.stringify(said));
    """
    out = subprocess.run([NODE, "-e", runner, str(DERBY_DIR / "static" / "derby.js")], input=json.dumps(races),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    said = json.loads(out.stdout.strip().splitlines()[-1])
    assert len(said) > 300
    for by, text, ids in said:
        assert by not in named(text) and named(text) <= set(ids) and "{t}" not in text, (by, text)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_guest_racers_line_up_now_and_then(w, monkeypatch, tmp_path):
    """Now and then a guest takes a hound's place: the add-on's own (Hamageddon and co.) or a local pack's
    (config/games_derby_guests/: guests.json and its sheets, served by /guests/file/). A guest never bites, never
    says the hounds' dog lines, talks in its own words first (passing, hurt, winning, losing) and runs its own way."""
    d = w.derby
    pack = tmp_path / "pack"
    pack.mkdir()
    shipped = json.loads((DERBY_DIR / "static" / "derby_guests.json").read_text(encoding="utf-8"))["guests"]
    ham = next(g for g in shipped if g["id"] == "hamageddon")
    (pack / "lola.webp").write_bytes((DERBY_DIR / "static" / ham["sheet"]["file"]).read_bytes())
    lola = {"id": "lola", "name": "Lola Lightning", "short": "Lola", "wobble": 0.5, "anchors": ham["anchors"],
            "sheet": {**ham["sheet"], "file": "lola.webp"},
            "lines": {"by": ["Bye, {t}!"], "at": ["Slow down, {t}!"], "ouch": ["LOLA OUCH!", "LOLA OW!"], "back": ["Lola says no, {t}!"],
                      "dodge": ["LOLA DODGES!"], "win": ["LOLA WINS!"], "lose": ["LOLA LOSES!"], "hello": ["Hi, I'm Lola!"],
                      "ticker": ["{a} IS HERE!"], "nonsense": ["dropped"]}}
    (pack / "guests.json").write_text(json.dumps({"guests": [lola, {"id": "cinder", "name": "Fake Cinder"},
                                                             {"id": "../x", "name": "Bad"}]}), encoding="utf-8")
    monkeypatch.setattr(d, "GUESTS_DIR", pack)
    guests = {g["id"]: g for g in d.load_guests()}
    assert len(shipped) >= 6 and set(guests) == {g["id"] for g in shipped} | {"lola"}   # (a hound's id or a bad one is skipped)
    assert not guests["hamageddon"]["local"] and guests["lola"]["local"] and guests["lola"]["wobble"] == 0.5
    view = {g["id"]: g for g in w.client.get("/games/api/derby/guests").json()["guests"]}
    assert view["lola"]["sheet"]["file"] == "/games/api/derby/guests/file/lola.webp" and view["lola"]["lines"]["at"]
    assert set(view["lola"]["lines"]) == set(d.GUEST_LINES)
    assert w.client.get("/games/api/derby/guests/file/lola.webp").status_code == 200
    for g in shipped:                                           # the add-on's own: every kind of line, a sheet that's there
        assert all(view[g["id"]]["lines"][k] for k in d.GUEST_LINES), g["id"]
        assert view[g["id"]]["sheet"]["file"] == f"guests/{g['id']}.webp" and view[g["id"]]["anchors"]["stride"] > 0
        assert w.client.get("/plugins/games_derby/static/guests/" + g["id"] + ".webp").status_code == 200, g["id"]
    for bad in ("guests.json", "nope.webp", "..%2Fguests.json"):
        assert w.client.get("/games/api/derby/guests/file/" + bad).status_code == 404, bad
    # how often: every race at 1 (two at most here), never at 0
    g = new_game(w, guest_chance=1, guests_max=2)
    counts = [sum(h["id"] in guests for h in g._new_field()) for _ in range(40)]
    assert all(1 <= k <= 2 for k in counts) and 2 in counts
    g = new_game(w, guest_chance=0)
    assert not any(h["id"] in guests for _ in range(20) for h in g._new_field())
    # the race: no biting, no dog lines (but its own), its own ouches, last words and wobble
    rng, ouch, ends, races = random.Random(3), 0, 0, []
    for _ in range(40):
        f = [{"num": i + 1, "id": h["id"], "name": h["name"], "short": h["short"]} for i, h in enumerate(rng.sample(d.ROSTER, 4))]
        f += [{"num": 5, "id": "lola", "name": "Lola Lightning", "short": "Lola", "guest": True, "wobble": 0.5},
              {"num": 6, "id": "hamageddon", "name": "Hamageddon", "short": "Ham", "guest": True, "wobble": 1.6}]
        s = d.build_script(f, rng.sample(range(6), 6), 45, rng.getrandbits(31))
        races.append({"field": f, "script": s})
        assert s["runners"][4]["wob"][0] <= 0.03 + 1e-9 and s["runners"][5]["wob"][0] >= 0.048 - 1e-9
        for e in s["events"]:
            assert not (e["type"] == "bite" and f[e["a"]].get("guest")), e
            by = f[e["b"] if e.get("by") == "b" else e["a"]]
            if by.get("guest") and e.get("line"):
                mine = guests[by["id"]]["lines"]
                assert not d._DOGGY.search(e["line"]) or e["line"] in mine["ouch"] + mine["dodge"], e["line"]
                ouch += e["line"] in mine["ouch"]
        first, last = f[s["order"][0]], f[s["order"][-1]]
        loser = f[s["photo"]["b"]] if s["photo"] and s["photo"]["kind"] == "win" else last
        for who, key, said in ((first, "win", "win"), (first, "win", "photo_win"), (last, "lose", "last"), (loser, "lose", "photo_lost")):
            if who.get("guest"):
                assert s["talk"][said] in guests[who["id"]]["lines"][key], (who["id"], said, s["talk"][said])
                ends += 1
    assert ouch >= 3 and ends >= 8
    # the overlay plays a race with a guest in it, and the guest's pass talk is its own kind
    runner = r"""
      const API = require(process.argv[1]), fs = require('fs'), path = require('path'), dir = path.dirname(process.argv[1]);
      const L = API.ASSETS.lines = JSON.parse(fs.readFileSync(path.join(dir, 'derby_lines.json'), 'utf8'));
      API.ASSETS.byId = {};
      JSON.parse(fs.readFileSync(path.join(dir, 'derby_hounds.json'), 'utf8')).hounds.forEach(h => { API.ASSETS.byId[h.id] = h; });
      JSON.parse(fs.readFileSync(path.join(dir, 'derby_guests.json'), 'utf8')).guests.forEach(g => {
        g.guest = true; API.ASSETS.byId[g.id] = g; L.by[g.id] = g.lines.by; L.at[g.id] = g.lines.at; });
      const said = [];
      for (const race of JSON.parse(fs.readFileSync(0, 'utf8'))) {
        race.field[4].id = 'hamageddon';                       // (lola isn't in this overlay: two of Hamageddon)
        race.script.runners[4].id = 'hamageddon';
        const R = API.buildRace({ id: 'guest-test', field: race.field, script: race.script });
        for (let t = 0; t < R.lastT; t += 0.25) API.raceAt(R, t);
        R.talk.forEach(b => said.push([b.r.id, b.text]));
      }
      console.log(JSON.stringify(said));
    """
    out = subprocess.run([NODE, "-e", runner, str(DERBY_DIR / "static" / "derby.js")], input=json.dumps(races[:12]),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    said = json.loads(out.stdout.strip().splitlines()[-1])
    ham_said = [text for who, text in said if who == "hamageddon"]
    own = [re.compile("^" + re.escape(x).replace(re.escape("{t}"), ".+") + "$") for k in d.GUEST_LINES for x in guests["hamageddon"]["lines"][k]]
    mine = [t for t in ham_said if any(r.match(t) for r in own)]
    assert ham_said and not any(d._DOGGY.search(t) for t in ham_said if t not in mine), ham_said
    assert len(mine) >= 0.6 * len(ham_said), (len(mine), ham_said)                # (mostly its own)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_editors_sample_and_demo_race(w):
    runner = r"""
      const API = require(process.argv[1]);
      const c = Object.assign({}, API.DEFAULTS);
      const s = API.sampleState(c, 'derby-sample', 30000), g = s.game;
      const steps = API.demoSteps(c, 'derby-preview');
      const race = steps[1].state.game.script;
      console.log(JSON.stringify({ phase: g.phase, field: g.field.length, players: g.players.length, markers: g.markers.length,
        phases: steps.map(x => x.state.game.phase), ms: steps.map(x => x.ms), order: race.order, duration: race.duration_ms,
        winner: steps[2].state.game.last.winner.num, first: g.field[race.order[0]].num }));
    """
    out = subprocess.run([NODE, "-e", runner, str(DERBY_DIR / "static" / "derby.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["phase"] == "betting" and res["field"] == 6 and res["players"] > 0 and res["markers"] == 6
    assert res["phases"] == ["betting", "racing", "result"] and res["ms"][1] == res["duration"]
    assert sorted(res["order"]) == list(range(6)) and res["winner"] == res["first"]


# ---- over HTTP, the way a bot uses it ----------------------------------------------------------------------

def test_http_flow(w, monkeypatch):
    c = w.client
    assert c.get("/games/api/derby/table").json()["game"] is None
    r = c.post("/games/api/derby/start", json={"seconds": 60})
    assert r.status_code == 200 and r.json()["ok"] and len(r.json()["field"]) == 6
    assert c.post("/games/api/derby/start", json={}).status_code == 409
    fix_order(w, monkeypatch, [3, 0, 1, 2, 4, 5])
    st = r.json()["state"]["game"]
    assert st["phase"] == "betting" and st["script"] is None and st["field"][3]["num"] == 4
    r = c.get("/games/api/derby/bet", params={"user": "alice", "number": 4, "amount": 100})       # GET works too
    assert r.status_code == 200 and r.json()["num"] == 4 and r.json()["debits"][0]["seq"] == 1
    assert c.post("/games/api/derby/bet", json={"user": "bob", "number": 1, "amount": 10}).status_code == 200
    r = c.post("/games/api/derby/bet", json={"user": "bob", "number": 8, "amount": 10})
    assert r.status_code == 400 and r.json()["ok"] is False
    v = c.get("/games/api/derby/validate?number=4&amount=100").json()
    assert v["valid"] and v["pays"] == 100 * round(v["multiplier"] * 100) // 100
    assert c.get("/games/api/derby/bets").json()["field"][0]["num"] == 1
    assert c.get("/games/api/derby/user/alice").json()["player"]["stake"] == 100
    r = c.post("/games/api/derby/next")
    assert r.status_code == 200 and r.json()["state"]["game"]["phase"] == "racing"
    assert r.json()["state"]["game"]["script"]["order"] == [3, 0, 1, 2, 4, 5]
    assert c.post("/games/api/derby/bet", json={"user": "x", "number": 1, "amount": 1}).json()["error"] == "bets_closed"
    c.post("/games/api/derby/next")
    st = c.get("/games/api/derby/table").json()
    assert st["game"]["phase"] == "result" and st["game"]["last"]["winners"][0]["user"] == "alice"
    ledger = c.get("/games/api/derby/ledger").json()
    assert [e["reason"] for e in ledger["events"]] == ["bet", "bet", "win"] and all(e["game"] == "derby" for e in ledger["events"])
    form = c.get("/games/api/derby/form").json()["form"]
    assert sum(f["races"] for f in form) == 6 and sum(f["wins"] for f in form) == 1
    c.post("/games/api/derby/next")
    assert c.get("/games/api/derby/history").json()["stats"]["races"] == 1
    assert c.post("/games/api/derby/form/clear").json()["form"][0]["races"] == 0
    assert c.post("/games/api/derby/next").status_code == 409                                       # no game
