"""Soul Climb (the games_climb add-on of Games), running from the real catalog/ folder: the survival model and
its multipliers, the cosmetic script (it can never go above the decided height), bets, settlement and the ledger,
config validation, the HTTP API and the game flow."""

import json
import math
import random
import secrets
import shutil
import subprocess
import sys
from fractions import Fraction
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT

CLIMB_DIR = ROOT / "catalog" / "games_climb"


# ---- helpers ---------------------------------------------------------------------------------------

@pytest.fixture
def w(real_world, tmp_path):
    """Games + Soul Climb installed and running; w.climb / w.core are the plugin modules."""
    real_world.installer.install("games")
    real_world.installer.install("games_climb")
    real_world.host.load_all()
    assert "games_climb" in real_world.host.loaded, real_world.host.errors
    ns = SimpleNamespace(world=real_world, app=real_world.app, tmp=tmp_path)
    ns.climb = sys.modules["hexcast_plugins.games_climb.climb"]
    ns.core = sys.modules["hexcast_plugins.games.core"]
    ns.client = TestClient(real_world.app)
    return ns


def new_game(w, **config):
    """A separate game object (own file, own ledger) so a test can look at everything it does."""
    w.core.CONFIG["climb"].update(config)
    return w.climb.SoulClimb(path=w.tmp / "games_climb.json", ledger=w.core.Ledger(w.tmp / "ledger.jsonl"))


def fix_outcome(w, monkeypatch, best):
    monkeypatch.setattr(w.climb, "sample_reached", lambda model, rng=None: best)


def keep_of(edge_pct):
    return 1 - Fraction(str(edge_pct)) / 100


def to_phase(g, phase, limit=12):
    for _ in range(limit):
        if g.phase == phase:
            return
        status, body = g.skip()
        assert status == 200, body
    raise AssertionError(f"never reached {phase}: stuck in {g.phase}")


def place(g, user, amount, height):
    status, body = g.place({"user": user, "amount": amount, "height": height})
    assert status == 200, body
    return body


def play_out(g):
    """Skip phases until the game is over and idle."""
    for _ in range(40):
        if g.g is None:
            return
        status, _body = g.skip()
        if status != 200:
            g._to_idle()


def check_script(sc, height, best, bets=()):
    """Everything a script must satisfy (the invariants the overlay relies on)."""
    beats = sc["beats"]
    assert sc["max"] == best and sc["height"] == height and sc["escaped"] == (best >= height)
    assert beats and beats[0]["ev"] == "ready" and beats[0]["t"] == 0 and beats[0]["lv"] == 0
    t, lv, peak = 0, 0, 0
    for i, b in enumerate(beats):
        assert b["t"] == t and b["d"] >= 1, (i, b)                    # beats follow each other
        assert b["lv"] == lv, (i, b)                                  # and the soul is where the last one left him
        assert 0 <= b["lv"] <= best and 0 <= b["to"] <= best, (i, b)  # nothing above the best height, nothing below the foot
        assert isinstance(b["s"], int) and 0 <= b["s"] < 2 ** 31
        peak = max(peak, b["lv"], b["to"], b.get("peak", 0))
        t, lv = t + b["d"], b["to"]
        if b["ev"] == "climb":
            assert b["to"] == b["lv"] + 1
        elif b["ev"] == "slip":
            assert 1 <= b["k"] <= 3 and b["to"] == b["lv"] - b["k"] and b["to"] >= 0           # a near fall: he lands on a ledge
        elif b["ev"] == "deadend":
            assert b["to"] == b["lv"] and b["lv"] < b["peak"] <= best                          # a backtrack: never above the best
        elif b["ev"] == "cheer":
            assert b["to"] == b["lv"] and b["flags"] and set(b["flags"]) <= set(bets) and max(b["flags"]) <= best
        elif b["ev"] in ("rest", "idle", "taunt", "bat", "geyser", "grab", "fray", "fatal", "escape"):
            assert b["to"] == b["lv"]
        elif b["ev"] == "fall":
            assert b["to"] == 0
        assert b["ev"] in ("ready", "climb", "cheer", "idle", "rest", "taunt", "slip", "bat", "geyser", "deadend", "grab",
                           "fray", "fatal", "fall", "escape")
    assert sc["duration_ms"] == t
    assert peak == best                                               # the highest level any beat mentions IS the outcome
    assert sc["duration_ms"] <= 110_000
    # how it ends
    last = beats[-1]
    if best >= height:
        assert last["ev"] == "escape" and last["lv"] == last["to"] == height and sc["cause"] is None and sc["style"] is None
        assert not any(b["ev"] in ("fatal", "fall") for b in beats)
    else:
        assert last["ev"] == "fall" and last["lv"] == best and last["to"] == 0 and last["style"] == sc["style"]
        assert beats[-2]["ev"] == "fatal" and beats[-2]["lv"] == best and beats[-2]["cause"] == sc["cause"]
        assert not any(b["ev"] == "escape" for b in beats)
    if best > 0:                                                      # the climb beat that gets there is the last one before the end
        top_climb = max(b["to"] for b in beats if b["ev"] == "climb")
        assert top_climb == best
    # route stretches cover 0..top without gaps
    routes = sc["routes"]
    assert routes[0]["from"] == 0
    if sc["cause"] is None or len(routes) > 1 and best >= routes[0]["to"]:
        assert routes[0]["kind"] == "ledge"                           # the first stretch is the left ledge (unless a cause rewrote it)
    for a, b in zip(routes, routes[1:]):
        assert a["to"] == b["from"]
    assert routes[-1]["to"] >= min(height, best + 1)
    assert all(r["kind"] in ("ledge", "chimney", "chains", "rope", "ribs") for r in routes)


# ---- the survival model ----------------------------------------------------------------------------

def test_model_is_a_proper_distribution(w):
    m = w.climb.climb_model(100, 2.0)
    assert m.height == 100 and len(m.fail) == 100 and len(m.nums) == 101
    assert m.survival(0) == 1
    probs = [m.fall_probability(h) for h in range(0, 101)]          # P(best height == h)
    assert all(p > 0 for p in probs) and sum(probs) == 1            # exact: the fractions add up to 1
    surv = [m.survival(h) for h in range(0, 101)]
    assert all(a > b for a, b in zip(surv, surv[1:]))               # S falls with every level
    for h in (1, 10, 50, 100):                                       # P(best >= h) is what S(h) says
        assert sum(probs[h:]) == surv[h]
    # the documented hazard: S(H) is the escape chance (to nine digits, never above it), and S(h) = exp(-Lambda(h / H))
    assert float(surv[100]) <= 0.02 and float(surv[100]) == pytest.approx(0.02, rel=1e-6)
    big_l = -math.log(0.02)
    for h in (1, 5, 33, 70, 99):
        t = h / 100
        assert float(surv[h]) == pytest.approx(math.exp(-big_l * (t + 0.9 * t * t) / 1.9), rel=1e-6)


def test_the_per_level_fall_chance_rises_with_height(w):
    for height, esc in ((100, 2.0), (30, 0.5), (300, 10.0), (10, 25.0)):
        m = w.climb.climb_model(height, esc)
        f = m.fail
        assert all(b >= a - 1 for a, b in zip(f, f[1:])), (height, esc)   # (rounded to whole parts per billion)
        assert f[-1] > f[0] * 2                                            # steeper at the top than at the foot
        assert 0 < f[0] and f[-1] < w.climb.CL_PPB
    assert w.climb.climb_model(100, 2.0).fail[0] == pytest.approx(0.0206 * 1e9, rel=0.02)


def test_escape_pct_sets_the_top_and_the_ends_of_the_range(w):
    for esc in (0.1, 1, 5, 20, 50):
        m = w.climb.climb_model(100, esc)
        assert float(m.survival(100)) == pytest.approx(esc / 100, rel=1e-6)
    assert w.climb.climb_model(100, 999).escape_pct == 50.0 and w.climb.climb_model(100, -3).escape_pct == 0.1
    assert w.climb.climb_model(5000, 2).height == 300                                 # max_height is clamped
    for height in (10, 300):                                                         # the extremes are fine and quick
        m = w.climb.climb_model(height, 2)
        assert len(m.table(Fraction(95, 100))) == height


def test_multiplier_is_edge_over_chance_floored_to_cents(w):
    climb = w.climb
    m = climb.climb_model(100, 2.0)
    for edge in (0, 5, 12.5, 25):
        keep = keep_of(edge)
        for h in range(1, 101):
            s = Fraction(1)                                                          # S(h) from the fail table, by hand
            for f in m.fail[:h]:
                s *= Fraction(10 ** 9 - f, 10 ** 9)
            want = max(100, math.floor(100 * keep / s))
            assert m.mult_cents(h, keep) == want, (edge, h)
            # a win never beats the edge: the bank keeps at least `edge` of every height (never below the stake back)
            ev = s * Fraction(want, 100)
            assert ev <= max(keep, s), (edge, h)
    keep = keep_of(5)
    mults = [m.mult_cents(h, keep) for h in range(1, 101)]
    assert mults == sorted(mults)                                                    # a higher guess never pays less
    assert mults[0] == 100 and mults[-1] == 4750                                     # the stake back at level 1; the jackpot x47.50
    assert m.mult(40, keep) == 2.91 and m.mult(100, keep) == 47.5


def test_the_bets_table_lists_every_height(w):
    body = w.client.get("/games/api/climb/bets").json()
    assert body["ok"] and body["game"] == "climb" and body["max_height"] == 100 and body["max_bets_per_player"] == 5
    rows = body["table"]
    assert [r["height"] for r in rows] == list(range(1, 101))
    assert all(set(r) >= {"height", "chance", "chance_pct", "mult"} and r["mult"] >= 1 for r in rows)
    assert rows[0]["mult"] == 1.0 and rows[-1]["mult"] == 47.5 and rows[-1]["chance_pct"] == pytest.approx(2.0, abs=0.001)
    assert all(a["chance"] > b["chance"] for a, b in zip(rows, rows[1:]))
    assert body["rules"] and "model" in body
    # it follows the settings
    w.client.post("/games/api/config", json={"climb": {"max_height": 20, "house_edge_pct": 0, "escape_pct": 10}})
    t2 = w.client.get("/games/api/climb/bets").json()["table"]
    assert len(t2) == 20 and t2[-1]["mult"] == 10.0                                  # (1 - 0) / 0.10


def test_pay_rounds_down_to_whole_coins_and_a_cap_never_cuts_below_the_stake(w):
    pay = w.climb.cl_pay
    assert pay(100, 291) == 291 and pay(37, 291) == 107 and pay(1, 291) == 2 and pay(1, 100) == 1
    assert pay(100, 4750, 1000) == 1000 and pay(100, 4750) == 4750
    assert pay(100, 120, 50) == 100                                                  # a cap below the stake: the stake comes back


def test_the_climb_is_rolled_from_the_model_with_secrets(w, monkeypatch):
    climb, core = w.climb, w.core
    assert isinstance(core._RNG, secrets.SystemRandom) and climb._RNG is core._RNG
    m = climb.climb_model(100, 2.0)

    class Stub:
        def __init__(self, value):
            self.value, self.calls = value, []

        def randrange(self, n):
            self.calls.append(n)
            return self.value

    low = Stub(0)                                                                    # every roll is "he falls": level 1 fails
    assert climb.sample_reached(m, low) == 0 and low.calls == [10 ** 9]
    high = Stub(10 ** 9 - 1)                                                         # every roll is "he holds": the escape
    assert climb.sample_reached(m, high) == 100 and high.calls == [10 ** 9] * 100
    # no rng given: it is the module's SystemRandom that is asked
    stub = Stub(0)
    monkeypatch.setattr(climb, "_RNG", stub)
    assert climb.sample_reached(m) == 0 and stub.calls == [10 ** 9]


def test_sampling_matches_the_published_chances(w):
    m = w.climb.climb_model(100, 2.0)
    rng = random.Random(20240601)
    n = 30000
    best = [w.climb.sample_reached(m, rng) for _ in range(n)]
    assert 0 <= min(best) and max(best) <= 100
    for h in (1, 10, 25, 50, 75):
        p = float(m.survival(h))
        got = sum(1 for b in best if b >= h) / n
        assert abs(got - p) < 5 * math.sqrt(p * (1 - p) / n) + 1e-9, (h, got, p)
    assert abs(sum(best) / n - sum(float(m.survival(h)) for h in range(1, 101))) < 0.5   # E[best] = sum S(h)


# ---- the script: all the comedy is cosmetic ----------------------------------------------------------

@pytest.mark.parametrize("best", [0, 1, 2, 3, 7, 14, 25, 40, 77, 99, 100])
def test_script_never_goes_above_the_decided_height(w, best):
    rng = random.Random(best * 7919 + 1)
    for seed in range(60):
        bets = sorted(rng.sample(range(1, 101), rng.randint(0, 6)))
        sc = w.climb.build_script(100, best, bets, rng.getrandbits(31), speed=rng.choice([0.5, 1.0, 1.0, 2.0]))
        check_script(sc, 100, best, bets)
        assert w.climb.script_peak(sc) == best
        json.dumps(sc)                                                               # plain data: it goes over the websocket


@pytest.mark.parametrize("height", [10, 37, 300])
def test_script_works_for_every_pit_height(w, height):
    for seed in range(25):
        for best in sorted({0, 1, height // 3, height - 1, height}):
            check_script(w.climb.build_script(height, best, [1, height // 2, height], seed), height, best, [1, height // 2, height])


def test_script_is_a_pure_function_of_its_seed(w):
    a = w.climb.build_script(100, 40, [10, 30, 55], 12345)
    assert a == w.climb.build_script(100, 40, [10, 30, 55], 12345)
    assert a != w.climb.build_script(100, 40, [10, 30, 55], 12346)
    assert [b["ev"] for b in a["beats"]] != [b["ev"] for b in w.climb.build_script(100, 40, [10, 30, 55], 99)["beats"]] \
        or a["cause"] != w.climb.build_script(100, 40, [10, 30, 55], 99)["cause"]


def test_scripts_have_variety_and_every_gag(w):
    climb = w.climb
    events, causes, styles, kinds = set(), set(), set(), set()
    flagged = 0
    for seed in range(400):
        best = random.Random(seed).choice([12, 25, 40, 60])
        sc = climb.build_script(100, best, [8, 20, 33, 50], seed)
        events |= {b["ev"] for b in sc["beats"]}
        causes.add(sc["cause"])
        styles.add(sc["style"])
        kinds |= {r["kind"] for r in sc["routes"]}
        flagged += any(b["ev"] == "cheer" for b in sc["beats"])
        for b in sc["beats"]:                                                         # the gags only where they make sense
            if b["ev"] == "fray":
                assert climb.route_at(sc["routes"], b["lv"]) == "rope"
        want = climb.CL_CAUSE_ROUTE.get(sc["cause"] or "")
        if want:
            assert climb.route_at(sc["routes"], sc["max"]) == want                    # a snapped rope falls from a rope
    assert {"climb", "rest", "idle", "taunt", "slip", "bat", "geyser", "deadend", "grab", "fray", "cheer", "fatal", "fall"} <= events
    assert set(climb.CL_CAUSES) <= causes and set(climb.CL_STYLES) <= styles and set(climb.CL_ROUTES) <= kinds
    assert flagged > 100
    esc = climb.build_script(100, 100, [10, 100], 5)
    assert esc["beats"][-1]["ev"] == "escape" and esc["escaped"]
    assert {"ready", "climb", "escape"} <= {b["ev"] for b in esc["beats"]}


def test_a_soul_that_hardly_left_the_ground_falls_in_a_sensible_way(w):
    climb = w.climb
    seen = {0: ([], []), 2: ([], []), 3: ([], [])}
    for seed in range(600):
        for best in seen:
            sc = climb.build_script(100, best, [1, 2, 5], seed)
            seen[best][0].append(sc["cause"])
            seen[best][1].append(sc["style"])
            assert sc["max"] == best and sc["beats"][-1]["ev"] == "fall"
    assert "tired" not in seen[0][0] + seen[2][0] and "tired" in seen[3][0]                 # not tired before he has climbed anything
    assert "umbrella" not in seen[0][1] + seen[2][1] + seen[3][1]                           # nothing to float down from (up to 3 levels)
    assert set(seen[0][1]) == set(climb.CL_STYLES) - {"umbrella"}                           # every other fall works from the ground
    assert {c for c in climb.CL_CAUSES if c != "tired"} <= set(seen[0][0])
    high = {climb.build_script(100, 4, [1], seed)["style"] for seed in range(600)}
    assert "umbrella" in high


def test_the_climb_slows_near_flags_and_hurries_when_they_are_far(w):
    near, far = [], []
    for seed in range(120):
        sc = w.climb.build_script(100, 70, [30, 60], seed)
        for b in sc["beats"]:
            if b["ev"] != "climb" or b["lv"] + 1 > 70:
                continue
            ahead = [h for h in (30, 60) if h >= b["lv"] + 1]
            dist = ahead[0] - (b["lv"] + 1) if ahead else None
            if dist is not None and dist <= 1:
                near.append(b["d"])
            elif dist is None or dist >= 12:
                far.append(b["d"])
    assert near and far and sum(near) / len(near) > 1.9 * sum(far) / len(far)


def test_the_speed_setting_and_the_length_cap(w):
    slow = w.climb.build_script(100, 40, [], 7, speed=0.5)["duration_ms"]
    fast = w.climb.build_script(100, 40, [], 7, speed=2.0)["duration_ms"]
    assert slow == pytest.approx(4 * fast, rel=0.02)
    sc = w.climb.build_script(300, 300, [100, 200], 3, speed=0.5)                    # a very long one is replayed faster
    assert sc["duration_ms"] <= w.climb.CL_SCRIPT_MAX_MS
    check_script(sc, 300, 300, [100, 200])


# ---- the overlay replays it without ever going above it (the renderer's own timeline, run under node) ----

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_renderers_timeline_stays_inside_the_script(w):
    climb = w.climb
    scripts = []
    rng = random.Random(5)
    for i in range(80):
        best = rng.choice([0, 1, 4, 11, 19, 33, 52, 100])
        bets = sorted(rng.sample(range(1, 101), rng.randint(0, 5)))
        scripts.append({"script": climb.build_script(100, best, bets, rng.getrandbits(31)), "bets": bets})
    runner = r"""
      const API = require(process.argv[1]);
      const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));
      let checked = 0, bad = [];
      for (const {script, bets} of data) {
        const tl = new API.Timeline(script, 'Gary');
        let lastReached = 0;
        for (let t = 0; t <= tl.D + 800; t += 41) {
          const lvl = tl.level(Math.min(t, tl.D - 0.5)), tt = Math.min(t, tl.D - 0.5);
          if (!(lvl >= -1e-9 && lvl <= script.max + 1e-9)) bad.push(['level', script.max, t, lvl]);
          const r = tl.reached(t);
          if (r < lastReached || r > script.max) bad.push(['reached', script.max, t, r]);
          lastReached = r;
          const p = API.soulPose(tl, tt, t);
          for (const v of [p.x, p.y]) if (!Number.isFinite(v)) bad.push(['pose', script.max, t, p.x, p.y]);
          for (const h of p.hands.concat(p.feet)) if (h && !(Number.isFinite(h.x) && Number.isFinite(h.y))) bad.push(['limb', script.max, t]);
          if (p.y > (script.max * API.LV + API.FOOT) + 400 && !(p.fx && ['fall', 'escape'].includes(p.fx.type))) bad.push(['pose too high', script.max, t, p.y]);
          checked++;
        }
        if (tl.reached(tl.D) !== script.max) bad.push(['end', script.max, tl.reached(tl.D)]);
        for (const h of bets) {
          const st = tl.flag(h, tl.D + 1000);
          if ((h <= script.max) !== (st === 'passed')) bad.push(['flag', script.max, h, st]);
          if (h > script.max && st !== 'lost') bad.push(['lost', script.max, h, st]);
        }
        if (tl.flag(1, -1) !== 'open') bad.push(['open']);
        // the wall's dressing: real props are at or below the best height, decoys (waiting props that never act) above it
        for (const pr of tl.props) {
          if (pr.decoy ? !(pr.lv > script.max && pr.lv < script.height) : pr.lv > script.max) bad.push(['prop', script.max, pr.lv, !!pr.decoy]);
        }
        if (script.max < script.height - 12 && !tl.props.some(pr => pr.decoy)) bad.push(['no decoys', script.max]);
      }
      console.log(JSON.stringify({checked, bad: bad.slice(0, 10)}));
    """
    out = subprocess.run([NODE, "-e", runner, str(CLIMB_DIR / "static" / "climb.js")], input=json.dumps(scripts),
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["checked"] > 5000 and res["bad"] == [], res


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_scripts_of_the_overlay_parse():
    for name in ("climb.js", "climb_panel.js"):
        out = subprocess.run([NODE, "--check", str(CLIMB_DIR / "static" / name)], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr


# ---- config ------------------------------------------------------------------------------------------

def test_config_defaults_and_validation(w):
    g = w.climb.CLIMB
    cfg = g.validate_config({})
    assert cfg["theme"] == "inferno" and cfg["title"] == "Soul Climb" and cfg["climbs"] == 1 and cfg["max_height"] == 100
    assert cfg["escape_pct"] == 2 and cfg["house_edge_pct"] == 5 and cfg["currency"] == "coins" and cfg["soul_name"] == ""
    assert set(cfg) == set(g.DEFAULTS) and set(g.SCHEMA) == set(g.DEFAULTS)
    bad = g.validate_config({"climbs": 9, "max_height": 5, "escape_pct": 77, "house_edge_pct": -1, "theme": "pink", "scale": 99,
                             "players_max": 0, "open_bet_seconds": 1, "climb_speed": 9, "title": "  ", "currency": None,
                             "min_bet": 5, "max_bet": 2, "soul_name": ["x"], "sfx": "maybe", "bogus": 1, "result_seconds": "abc"})
    assert bad["climbs"] == 3 and bad["max_height"] == 10 and bad["escape_pct"] == 50 and bad["house_edge_pct"] == 0
    assert bad["theme"] == "inferno" and bad["scale"] == 5 and bad["players_max"] == 1 and bad["open_bet_seconds"] == 5
    assert bad["climb_speed"] == 2 and bad["title"] == "Soul Climb" and bad["currency"] == "coins"
    assert bad["max_bet"] == 0 and bad["soul_name"] == "" and bad["sfx"] is True and "bogus" not in bad
    assert bad["result_seconds"] == 8                                                  # unreadable: the default
    ok = g.validate_config({"theme": "ABYSS", "max_height": "250", "escape_pct": "1.5", "soul_name": " Zed , Yan ", "sfx": "off",
                            "max_bet": 0, "min_bet": 3})
    assert ok["theme"] == "abyss" and ok["max_height"] == 250 and ok["escape_pct"] == 1.5 and ok["soul_name"] == "Zed , Yan"
    assert ok["sfx"] is False and ok["max_bet"] == 0 and ok["min_bet"] == 3
    assert g.APPEARANCE == tuple(w.climb.OVERLAY["appearance"])


def test_config_is_saved_per_game_and_restyles_the_overlay_keys_only(w):
    r = w.client.post("/games/api/config", json={"climb": {"theme": "sulfur", "max_height": 60, "soul_name": "Ned", "x": 12}})
    assert r.status_code == 200 and r.json()["config"]["climb"]["theme"] == "sulfur"
    saved = json.loads((w.world.config / "games.json").read_text())
    assert saved["climb"]["max_height"] == 60 and saved["climb"]["x"] == 12 and saved["climb"]["soul_name"] == "Ned"
    # a preview only takes appearance keys
    r = w.client.post("/games/api/climb/preview", json={"overrides": {"theme": "abyss", "max_height": 5, "title": "Pit"}, "seconds": 3})
    assert r.status_code == 200
    ov = r.json()["state"]["preview"]["overrides"]
    assert ov == {"theme": "abyss", "title": "Pit"}
    assert w.client.post("/games/api/climb/preview/clear").json()["cleared"] is True


# ---- the add-on itself ------------------------------------------------------------------------------

def test_the_addon_registers_like_the_other_games(w):
    reg = {g["key"]: g for g in w.client.get("/games/api/registry").json()["games"]}
    g = reg["climb"]
    assert g["plugin"] == "games_climb" and g["order"] == 60 and g["title"] == "Soul Climb"
    assert g["panel_js"].startswith("/plugins/games_climb/static/climb_panel.js")
    assert g["overlay"]["script"].startswith("/plugins/games_climb/static/climb.js")
    assert g["overlay"]["stateful"] is True and g["overlay"]["appearance"] == list(w.climb.SoulClimb.APPEARANCE)
    assert g["overlay"]["defaults"]["theme"] == "inferno"
    for url in (g["panel_js"], g["overlay"]["script"]):
        r = w.client.get(url)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"] and len(r.text) > 2000, url
    api = w.client.get("/games/api").json()
    assert "climb" in api["games"] and "climb" in api and "start" in api["climb"] and "bet" in api["climb"]
    assert api["appearance_keys"]["climb"] == list(w.climb.SoulClimb.APPEARANCE)
    assert "climb" in w.client.get("/games/api/status").json()["games"]
    assert w.client.get("/games/api/climb/spin").status_code == 400                       # a round game starts with /start
    assert w.client.get("/games/api/climb/announce").status_code == 400
    assert w.client.get("/games/api/climb/bets").status_code == 200
    # a removed game takes its tab with it
    import asyncio
    stopped, clean = asyncio.run(w.world.host.unload("games_climb"))
    assert stopped == ["games_climb"] and clean
    assert "climb" not in [x["key"] for x in w.client.get("/games/api/registry").json()["games"]]


def test_manifest_help_and_docs(w):
    manifest = json.loads((CLIMB_DIR / "plugin.json").read_text())
    assert manifest["id"] == "games_climb" and manifest["parent"] == "games" and manifest["order"] == 60
    assert manifest["category"] == "Games" and manifest["description"] and manifest["help"]["toc"][0]["id"] == "climb"
    assert 'id="climb"' in (CLIMB_DIR / "help.html").read_text()
    docs = (ROOT / "docs" / "climb.md").read_text()
    assert "/games/api/climb/bet" in docs and "S(h)" in docs and "escape" in docs.lower()
    entry = w.world.host.catalog.entries()["games_climb"]
    assert entry.manifest.parent == "games" and entry.manifest.order == 60
    assert "games" in entry.manifest.requires


# ---- a game, step by step ----------------------------------------------------------------------------

def test_a_full_game_pays_exactly_what_the_table_says(w, monkeypatch):
    g = new_game(w)
    keep = keep_of(5)
    model = w.climb.climb_model(100, 2)
    fix_outcome(w, monkeypatch, 40)
    assert g.start_game({"soul": "Gary", "seconds": 20})[0] == 200
    assert g.start_game({})[0] == 409                                                    # one at a time
    place(g, "alice", 100, 10)
    place(g, "alice", 50, 40)
    place(g, "bob", 200, 40)
    place(g, "carol", 10, 100)
    place(g, "dave", 7, 41)                                                              # one level too high
    assert g.g["debits"] == 367
    to_phase(g, "climbing")
    script = g.g["script"]
    assert script["max"] == 40 and g.g["marks"] and g.phase == "climbing"
    assert g.place({"user": "erin", "amount": 5, "height": 5})[1]["error"] == "bets_closed"      # the window is closed
    assert g.g["ends_at"] == pytest.approx(g.g["phase_at"] + script["duration_ms"] / 1000 + w.climb.CL_TAIL_S, abs=0.01)
    assert len(list(g.ledger.events)) == 5                                                 # the five stakes; the refused bet moved nothing
    to_phase(g, "result")
    last = g.g["last"]
    assert last["max"] == 40 and last["escaped"] is False and last["style"] == script["style"] and last["cause"] == script["cause"]
    won = {(x["user"], x["height"]): x for x in last["winners"]}
    lost = {(x["user"], x["height"]): x for x in last["losers"]}
    assert set(won) == {("alice", 10), ("alice", 40), ("bob", 40)} and set(lost) == {("carol", 100), ("dave", 41)}
    for (user, h), amount in {("alice", 10): 100, ("alice", 40): 50, ("bob", 40): 200}.items():
        cents = model.mult_cents(h, keep)
        assert won[(user, h)]["pays"] == amount * cents // 100 and won[(user, h)]["mult"] == cents / 100
    # the ledger: one debit per bet, one credit per winning bet, a lost stake is just gone
    ev = list(g.ledger.events)
    debits = [e for e in ev if e["type"] == "debit"]
    credits = [e for e in ev if e["type"] == "credit"]
    assert [(e["user"], e["amount"], e["reason"]) for e in debits] == [("alice", 100, "bet"), ("alice", 50, "bet"), ("bob", 200, "bet"),
                                                                       ("carol", 10, "bet"), ("dave", 7, "bet")]
    assert sorted((e["user"], e["amount"], e["reason"]) for e in credits) == sorted(
        (u, x["pays"], "win") for (u, h), x in won.items())
    assert all(e["game"] == "climb" and e["roll_id"] == g.g["id"] and e["bet_id"].startswith(g.g["id"] + "/c1/h") for e in ev)
    assert {e["bet_id"] for e in credits} <= {e["bet_id"] for e in debits}
    assert all("Height" in e["bet"] for e in ev) and any("x2.91" in e["bet"] for e in credits)
    paid = sum(e["amount"] for e in credits)
    assert g.g["debits"] == 367 and g.g["credits"] == paid and g.g["bets"] == {}
    to_phase(g, "over")
    s = g.g["summary"]
    assert s["outcome"] == "complete" and s["played"] == 1 and s["best"] == 40 and s["total_bet"] == 367 and s["total_paid"] == paid
    assert s["house_net"] == 367 - paid and s["text"] == "Gary reached level 40" and not s["test"]
    by = {p["user"]: p for p in s["players"]}
    assert by["alice"]["bet"] == 150 and by["alice"]["net"] == by["alice"]["paid"] - 150 and by["carol"]["net"] == -10
    play_out(g)
    assert g.g is None and g.phase == "idle"
    st = g.stats()
    assert st["games"] == 1 and st["climbs"] == 1 and st["falls"] == 1 and st["escapes"] == 0 and st["best_height"] == 40
    assert st["total_bet"] == 367 and st["total_paid"] == paid and st["house_net"] == 367 - paid and st["total_height"] == 40
    # what the game wrote to disk is a clean state with nothing running
    saved = json.loads((w.tmp / "games_climb.json").read_text())
    assert saved["game"] is None and saved["history"][0]["result"]["best"] == 40


def test_an_escape_pays_the_jackpot(w, monkeypatch):
    g = new_game(w)
    fix_outcome(w, monkeypatch, 100)
    g.start_game({"soul": "Zed"})
    place(g, "al", 100, 100)
    place(g, "bo", 100, 99)
    place(g, "cy", 100, 1)
    to_phase(g, "result")
    last = g.g["last"]
    assert last["escaped"] is True and last["cause"] is None and last["style"] is None and last["soul"] == "Zed"
    assert g.g["script"]["beats"][-1]["ev"] == "escape"
    pays = {x["user"]: x["pays"] for x in last["winners"]}
    assert pays["al"] == 4750 and pays["cy"] == 100 and pays["bo"] == 100 * w.climb.climb_model(100, 2).mult_cents(99, keep_of(5)) // 100
    to_phase(g, "over")
    assert g.g["summary"]["text"] == "Zed escaped the pit!"
    play_out(g)
    assert g.stats()["escapes"] == 1 and g.stats()["falls"] == 0


def test_bet_rules(w):
    g = new_game(w, min_bet=5, max_bet=300)
    assert g.place({"user": "a", "amount": 5, "height": 5})[0] == 409                    # no game yet
    g.start_game({})
    cases = [({"amount": 10, "height": 5}, "user required"), ({"user": "a", "amount": 10}, "height required"),
             ({"user": "a", "amount": 10, "height": 0}, "from 1 to 100"), ({"user": "a", "amount": 10, "height": 101}, "from 1 to 100"),
             ({"user": "a", "amount": 10, "height": 12.5}, "whole number"), ({"user": "a", "amount": 10, "height": "high"}, "whole number"),
             ({"user": "a", "amount": 10, "height": True}, "whole number"), ({"user": "a", "height": 9}, "amount required"),
             ({"user": "a", "amount": 4, "height": 9}, "minimum bet is 5"), ({"user": "a", "amount": 2.5, "height": 9}, "whole coins"),
             ({"user": "a", "amount": -3, "height": 9}, "positive"), ({"user": "a", "amount": "x", "height": 9}, "invalid amount")]
    for params, text in cases:
        status, body = g.place(params)
        assert status == 400 and text in body["error"], (params, body)
    assert g.g["bets"] == {} and g.g["debits"] == 0                                      # nothing moved
    # forgiving about how a bot spells it
    for height in (7, "7", 7.0, "L7", "level 7", "lvl7", "h7"):
        g.g["bets"].clear()
        assert g.place({"user": "bot", "amount": 5, "height": height})[0] == 200, height
    assert g.place({"user": "bot", "amount": 5, "level": 8})[0] == 200
    g.g["bets"].clear()
    # the same height again adds to the bet; max_bet is per player per climb, all heights together
    b1 = place(g, "amy", 100, 20)
    assert b1["total"] == 100 and b1["mult"] == w.climb.climb_model(100, 2).mult(20, keep_of(5)) and b1["side"] == "level 20"
    b2 = place(g, "amy", 100, 20)
    assert b2["total"] == 200 and b2["amount"] == 100 and len(g.g["bets"]["amy"]) == 1
    status, body = g.place({"user": "amy", "amount": 150, "height": 30})
    assert status == 400 and "max bet is 300" in body["error"] and "already down" in body["error"]
    place(g, "amy", 100, 30)
    assert [(x["height"], x["amount"]) for x in g._player("amy")["bets"]] == [(20, 200), (30, 100)]
    # at most five bets per player (different heights)
    g.cfg["max_bet"] = 0
    for h in (40, 50, 60):
        place(g, "amy", 5, h)
    status, body = g.place({"user": "amy", "amount": 5, "height": 70})
    assert status == 400 and "at most 5 bets" in body["error"] and "20, 30, 40, 50, 60" in body["error"]
    assert place(g, "amy", 5, 60)["total"] == 10                                         # adding to one of them is fine
    assert place(g, "ben", 5, 70)["debits"][0]["reason"] == "bet"                         # others have their own five
    # debit reasons: bet, then add
    assert [e["reason"] for e in g.ledger.events if e["user"] == "amy"] == ["bet", "add", "bet", "bet", "bet", "bet", "add"]
    # take-backs
    status, body = g.remove({"user": "amy", "height": 30})
    assert status == 200 and body["credits"] == [{"user": "amy", "amount": 100}]
    assert [x["height"] for x in g._player("amy")["bets"]] == [20, 40, 50, 60]
    assert g.remove({"user": "amy", "height": 30})[0] == 400 and g.remove({"user": "zed"})[0] == 400
    status, body = g.remove({"user": "amy"})
    assert status == 200 and body["credits"] == [{"user": "amy", "amount": 200 + 5 + 5 + 10}] and "amy" not in g.g["bets"]
    refunds = [e for e in g.ledger.events if e["reason"] == "refund"]
    assert sum(e["amount"] for e in refunds) == 100 + 200 + 5 + 5 + 10 and all(e["type"] == "credit" for e in refunds)
    assert all(e["bet_id"].startswith(g.g["id"] + "/c1/h") for e in refunds)


def test_game_is_full_at_500_players(w):
    g = new_game(w)
    g.start_game({})
    for i in range(w.core.ROUND_PLAYERS_MAX):
        assert g.place({"user": f"u{i}", "amount": 1, "height": 5})[0] == 200
    status, body = g.place({"user": "one-more", "amount": 1, "height": 5})
    assert status == 400 and "full" in body["error"]
    assert g.place({"user": "u3", "amount": 1, "height": 6})[0] == 200                    # the ones in can still bet


def test_no_bets_ends_the_game_and_a_quiet_second_window_too(w, monkeypatch):
    g = new_game(w)
    g.start_game({})
    to_phase(g, "over")
    assert g.g["outcome"] == "no_bets" and g.g["summary"]["text"] == "No bets - no climb" and g.g["script"] is None
    play_out(g)
    assert g.stats()["no_bets"] == 1 and list(g.ledger.events) == []
    # two climbs, nobody bets in the second window: the game is complete after the first
    fix_outcome(w, monkeypatch, 12)
    g.start_game({"climbs": 2})
    place(g, "al", 10, 5)
    g.skip()
    g.skip()
    g.skip()                                                                              # betting -> climbing -> result -> betting 2
    assert g.phase == "betting" and g.g["climb"] == 2 and g.g["bets"] == {} and g.g["script"] is None
    g.skip()
    assert g.phase == "over" and g.g["outcome"] == "complete" and g.g["summary"]["played"] == 1


def test_several_climbs_each_with_its_own_window_and_soul(w, monkeypatch):
    g = new_game(w, soul_name="Ann, Bob, Cid")
    outcomes = iter([5, 100, 20])
    monkeypatch.setattr(w.climb, "sample_reached", lambda model, rng=None: next(outcomes))
    assert g.start_game({"climbs": 9})[0] == 200 and g.g["climbs"] == 3 and g.g["names"] == ["Ann", "Bob", "Cid"]
    souls = []
    for n in (1, 2, 3):
        assert g.phase == "betting" and g.g["climb"] == n
        souls.append(g.g["soul"]["name"])
        assert g.g["soul"]["name"] in ("Ann", "Bob", "Cid") and 0 <= g.g["soul"]["skin"] < w.climb.CL_SKINS
        place(g, "al", 10, 10 * n)
        if n == 2:
            place(g, "al", 10, 100)
        assert g.g["debits"] == {1: 10, 2: 30, 3: 40}[n]
        to_phase(g, "climbing")
        assert g.g["script"]["max"] == {1: 5, 2: 100, 3: 20}[n]
        to_phase(g, "result")
        assert g.g["last"]["climb"] == n and g.g["last"]["soul"] == souls[-1]
        g.skip()
    assert g.phase == "over"
    s = g.g["summary"]
    assert [r["max"] for r in s["results"]] == [5, 100, 20] and [r["escaped"] for r in s["results"]] == [False, True, False]
    assert s["played"] == 3 and s["best"] == 100 and s["text"].startswith("3 climbs, best level 100 (1 escaped)")
    ids = {e["bet_id"].split("/")[1] for e in g.ledger.events}
    assert ids == {"c1", "c2", "c3"}
    play_out(g)
    assert g.stats()["climbs"] == 3 and g.stats()["escapes"] == 1 and g.stats()["falls"] == 2 and g.stats()["total_height"] == 125


def test_stop_in_a_betting_window_refunds_everything(w):
    g = new_game(w)
    g.start_game({})
    place(g, "al", 100, 10)
    place(g, "bo", 50, 20)
    assert g.stop() is True and g.g is None and g.phase == "idle" and g.hidden
    refunds = [e for e in g.ledger.events if e["reason"] == "refund"]
    assert sorted((e["user"], e["amount"]) for e in refunds) == [("al", 100), ("bo", 50)]
    assert g.history[0]["result"]["outcome"] == "stopped" and g.history[0]["result"]["total_paid"] == 150
    assert g.history[0]["result"]["text"] == "Game stopped - every open stake returned"
    assert g.stats()["stopped"] == 1


def test_stop_while_he_climbs_settles_the_decided_climb(w, monkeypatch):
    g = new_game(w)
    fix_outcome(w, monkeypatch, 30)
    g.start_game({})
    place(g, "al", 100, 20)
    place(g, "bo", 100, 50)
    to_phase(g, "climbing")
    assert g.g["script"]["max"] == 30
    g.stop()
    credits = [e for e in g.ledger.events if e["type"] == "credit"]
    assert [(e["user"], e["reason"]) for e in credits] == [("al", "win")]                  # decided: al won, bo lost, nothing refunded
    assert credits[0]["amount"] == 100 * w.climb.climb_model(100, 2).mult_cents(20, keep_of(5)) // 100
    assert g.history[0]["result"]["outcome"] == "stopped" and g.history[0]["result"]["best"] == 30
    assert g.history[0]["result"]["text"] == "Game stopped after 1 climb, best level 30"           # the decided climb counted


def test_a_game_interrupted_by_a_restart_is_settled_at_the_next_start(w, monkeypatch):
    g = new_game(w)
    fix_outcome(w, monkeypatch, 30)
    g.start_game({})
    place(g, "al", 100, 20)
    ledger = g.ledger
    # restart while the window is open: refunded
    g2 = w.climb.SoulClimb(path=w.tmp / "games_climb.json", ledger=ledger)
    assert g2.g is not None and g2.phase == "betting" and g2.g["bets"]["al"][0] == {"height": 20, "amount": 100}
    g2.resume()
    assert g2.g is None and [e["reason"] for e in ledger.events if e["type"] == "credit"] == ["refund"]
    assert g2.history[0]["result"]["outcome"] == "restart"
    # restart while he climbs: the decided climb counts
    g2.start_game({})
    place(g2, "al", 100, 20)
    place(g2, "bo", 100, 60)
    to_phase(g2, "climbing")
    g3 = w.climb.SoulClimb(path=w.tmp / "games_climb.json", ledger=ledger)
    assert g3.phase == "climbing" and g3.g["script"]["max"] == 30
    g3.resume()
    wins = [e for e in ledger.events if e["reason"] == "win"]
    assert [(e["user"]) for e in wins] == ["al"] and g3.g is None
    seqs = [e["seq"] for e in ledger.events]
    assert seqs == sorted(set(seqs))                                                       # never twice, never out of order


def test_settling_twice_pays_once(w, monkeypatch):
    g = new_game(w)
    fix_outcome(w, monkeypatch, 50)
    g.start_game({})
    place(g, "al", 100, 20)
    to_phase(g, "climbing")
    first = g._resolve()
    again = g._resolve()
    assert len(first) == 1 and again == []


def test_test_games_never_touch_the_ledger(w, monkeypatch):
    g = new_game(w)
    fix_outcome(w, monkeypatch, 30)
    g.start_game({"test": True})
    body = place(g, "al", 100, 20)
    assert body["debits"][0]["seq"] is None
    to_phase(g, "over")
    assert list(g.ledger.events) == [] and g.ledger.last_seq == 0
    assert g.g["log"] and any(e["reason"] == "win" for e in g.g["log"]) and g.g["summary"]["test"] is True
    play_out(g)
    assert g.stats()["games"] == 0 and g.history[0]["test"] is True                          # shown in the history, not in the stats


def test_max_payout_caps_a_win_but_never_below_the_stake(w, monkeypatch):
    g = new_game(w, max_payout=500)
    fix_outcome(w, monkeypatch, 100)
    g.start_game({})
    place(g, "al", 100, 100)                                                                # would pay 4750
    place(g, "bo", 1000, 3)                                                                 # x1.01: pays 1010, capped to... never below 1000
    to_phase(g, "result")
    pays = {x["user"]: x["pays"] for x in g.g["last"]["winners"]}
    assert pays["al"] == 500 and pays["bo"] == 1000


def test_states_show_what_the_overlay_needs(w, monkeypatch):
    g = new_game(w)
    fix_outcome(w, monkeypatch, 33)
    assert g.state_view()["game"] is None and g.state_view()["idle"]["height"] == 100 and g.state_view()["idle"]["odds"]
    g.start_game({"soul": "Gary"})
    place(g, "al", 100, 40)
    place(g, "bo", 20, 40)
    place(g, "bo", 5, 12)
    gv = g.state_view()["game"]
    assert gv["phase"] == "betting" and gv["script"] is None and gv["climb"] == 1 and gv["climbs"] == 1 and gv["height"] == 100
    assert gv["soul"]["name"] == "Gary" and gv["ends_in_ms"] > 25_000 and gv["at_risk"] == 125 and gv["max_bets"] == 5
    assert [m["height"] for m in gv["markers"]] == [12, 40]
    m40 = gv["markers"][1]
    assert m40["total"] == 120 and [u["user"] for u in m40["users"]] == ["al", "bo"] and m40["mult"] == 2.91 and 0 < m40["chance"] < 1
    assert [p["user"] for p in gv["players"]] == ["al", "bo"] and gv["players"][1]["bets"][0] == {"height": 12, "amount": 5, "mult": 1.24, "pays": 6}
    assert gv["odds"][0]["height"] < gv["odds"][-1]["height"] == 100 and gv["odds"][-1]["mult"] == 47.5
    assert g.table_bets() == [{"user": "al", "amount": 100}, {"user": "bo", "amount": 25}]
    table = {row["height"]: row["mult"] for row in g.bets_payload()["table"]}                 # the same numbers three ways
    assert gv["mults"] == [table[h] for h in range(1, 101)] and gv["mults"][39] == m40["mult"] and gv["mults"][99] == 47.5
    assert g.state_view()["idle"]["mults"] == gv["mults"]
    to_phase(g, "climbing")
    gv = g.state_view()["game"]
    assert gv["phase"] == "climbing" and gv["script"]["max"] == 33 and [m["height"] for m in gv["markers"]] == [12, 40]
    assert gv["players"] and gv["last"] is None
    to_phase(g, "result")
    gv = g.state_view()["game"]
    assert gv["script"] and gv["players"] == [] and gv["markers"] and gv["last"]["max"] == 33
    to_phase(g, "over")
    gv = g.state_view()["game"]
    assert gv["summary"]["best"] == 33 and gv["script"]
    assert json.dumps(g.state_view())                                                         # everything is plain data


def test_user_view_and_validate(w):
    g = new_game(w)
    v = g.validate({"height": "40", "amount": "100"})
    assert v["valid"] and v["multiplier"] == 2.91 and v["pays"] == 291 and v["chance"] == pytest.approx(0.326, abs=0.001)
    assert not g.validate({"height": 0})["valid"] and not g.validate({"height": 5, "amount": 0.5})["valid"]
    g.start_game({})
    place(g, "al", 100, 40)
    u = g.user_view("@al")
    assert u["user"] == "al" and u["player"]["stake"] == 100 and u["session"]["debits"] == 100
    assert g.user_view("nobody")["player"] is None


def test_clean_game_survives_garbage(w):
    g = new_game(w)
    for raw in (None, 5, "x", [], {}, {"id": "x", "phase": "nope"}, {"id": "x", "phase": "betting", "bets": "bad", "script": "bad"},
                {"id": "x", "phase": "climbing", "height": 99999, "climbs": 99, "bets": {"al": [{"height": 500, "amount": 5}, {"height": 3, "amount": -1},
                 {"height": 3, "amount": 7}, 7, None]}, "script": {"beats": 5}, "soul": {"name": 5, "skin": 99}}):
        got = g.clean_game(raw)
        if got is not None:
            assert got["height"] <= 300 and got["climbs"] <= 3 and got["script"] is None
            assert got["bets"] == ({"al": [{"height": 3, "amount": 7}]} if raw and "bets" in raw and isinstance(raw["bets"], dict) else {})
            assert got["soul"]["skin"] < w.climb.CL_SKINS
    # a script that was cut off during the climb: nothing to settle with, so the stakes come back
    g.g = g.clean_game({"id": "cl-x", "phase": "climbing", "bets": {"al": [{"height": 3, "amount": 7}]}, "ends_at": 1})
    assert g.open_settlement()[0]["reason"] == "refund"


# ---- over HTTP, the way a bot uses it --------------------------------------------------------------------

def test_http_flow(w, monkeypatch):
    c = w.client
    fix_outcome(w, monkeypatch, 25)
    assert c.get("/games/api/climb/table").json()["game"] is None
    r = c.post("/games/api/climb/start", json={"soul": "Hank", "seconds": 60})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["state"]["game"]["soul"]["name"] == "Hank"
    assert c.post("/games/api/climb/start", json={}).status_code == 409
    r = c.get("/games/api/climb/bet", params={"user": "alice", "height": 20, "amount": 100})                  # GET works too
    assert r.status_code == 200 and r.json()["pays"] == 154 and r.json()["debits"][0]["seq"] == 1
    assert c.post("/games/api/climb/bet", json={"user": "bob", "height": 30, "amount": 10}).status_code == 200
    r = c.post("/games/api/climb/bet", json={"user": "bob", "height": 300, "amount": 10})
    assert r.status_code == 400 and r.json()["ok"] is False and "from 1 to 100" in r.json()["error"]
    r = c.post("/games/api/climb/remove", json={"user": "bob"})
    assert r.status_code == 200 and r.json()["credits"] == [{"user": "bob", "amount": 10}]
    assert c.get("/games/api/climb/user/alice").json()["player"]["stake"] == 100
    r = c.post("/games/api/climb/next")
    assert r.status_code == 200 and r.json()["skipped"] == "betting" and r.json()["state"]["game"]["phase"] == "climbing"
    assert c.post("/games/api/climb/bet", json={"user": "x", "height": 3, "amount": 1}).json()["error"] == "bets_closed"
    c.post("/games/api/climb/next")
    st = c.get("/games/api/climb/table").json()
    assert st["game"]["phase"] == "result" and st["game"]["last"]["winners"][0]["user"] == "alice"
    ledger = c.get("/games/api/climb/ledger").json()
    assert [e["reason"] for e in ledger["events"]] == ["bet", "bet", "refund", "win"] and ledger["last_seq"] == 4
    assert all(e["game"] == "climb" for e in ledger["events"])
    assert c.get("/games/api/ledger?game=climb").json()["events"] == ledger["events"]
    assert c.get("/games/api/climb/history").json()["history"] == []                                          # still running
    c.post("/games/api/climb/next")
    c.post("/games/api/climb/next")
    hist = c.get("/games/api/climb/history").json()
    assert hist["history"][0]["result"]["best"] == 25 and hist["stats"]["games"] == 1 and hist["stats"]["best_height"] == 25
    assert c.post("/games/api/climb/next").status_code == 409                                                  # no game
    r = c.post("/games/api/climb/stop")
    assert r.status_code == 200
    assert c.get("/games/api/climb/validate?height=40&amount=100").json()["pays"] == 291


def test_the_state_reaches_overlays_over_the_websocket(w, monkeypatch):
    fix_outcome(w, monkeypatch, 12)
    with TestClient(w.app) as c:
        with c.websocket_connect("/games/ws/overlay") as ws:
            msgs = [json.loads(ws.receive_text()) for _ in range(1 + len(w.core.GAMES))]            # config + one state per game
            assert msgs[0]["type"] == "config" and any(m["type"] == "state" and m["game"] == "climb" for m in msgs)
            c.post("/games/api/climb/start", json={})
            c.post("/games/api/climb/bet", json={"user": "al", "height": 5, "amount": 10})
            seen = []
            for _ in range(6):
                m = json.loads(ws.receive_text())
                if m.get("type") == "state" and m.get("game") == "climb":
                    seen.append(m["state"]["game"])
                if seen and seen[-1] and seen[-1]["players"]:
                    break
            assert seen and seen[-1]["phase"] == "betting" and seen[-1]["players"][0]["user"] == "al"


def test_bets_text_never_breaks_the_state(w):
    g = new_game(w)
    g.start_game({"soul": "Zoë 🧗, " + "x" * 80})
    assert g.g["names"][0] == "Zoë 🧗" and len(g.g["names"][1]) == w.climb.SOUL_NAME_MAX
    place(g, "@alice\x00", 5, 5)
    assert list(g.g["bets"]) == ["alice"]
    assert json.dumps(g.state_view(), ensure_ascii=False)
