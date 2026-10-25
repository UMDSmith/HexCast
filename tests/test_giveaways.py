"""Giveaways: prizes, who can win (everyone in chat, followers for long enough, subscribers - any ticked group, every
eligible viewer with the same chance), the exclusions, the show (draw -> reveal -> away), rerolls and codes, the API
and the sockets - on the shared Twitch connection, without the Twitch plugin. Nothing talks to Twitch: who is in
chat, the follower and subscriber checks and posting in chat are replaced."""

import asyncio
import collections
import importlib
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("websockets")

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "catalog" / "giveaways" / "static"
SCOPES = ["user:read:chat", "user:write:chat", "moderator:read:chatters", "moderator:read:followers",
          "channel:read:subscriptions"]
CONFIGS = ("giveaways.json", "twitch_core.json", "twitch_secrets.json")
NOW = time.time()
DAY = 86400


@pytest.fixture
def world(real_world):
    real_world.installer.install("giveaways")
    real_world.host.load_all()
    assert {"giveaways", "twitch_core"} == set(real_world.host.loaded), real_world.host.errors
    yield real_world
    for name in CONFIGS:
        (real_world.config / name).unlink(missing_ok=True)


class Twitch:
    """Who is in chat, who follows since when, who subscribes - and the chat lines posted."""

    def __init__(self):
        self.chat = [("hexacore_ai", "1"), ("alice", "2"), ("bob", "3"), ("carol", "4"), ("nightbot", "5"), ("dave", "6")]
        self.follows = {"2": NOW - 30 * DAY, "3": NOW - 2 * 3600, "6": NOW - 400 * DAY}     # alice 30 days, bob 2 hours, dave
        self.subs = {"4": "1000"}                                                         # carol
        self.sent = []
        self.follow_checks = 0


@pytest.fixture
def tw(world, monkeypatch):
    gw = importlib.import_module("hexcast_plugins.giveaways.giveaways")
    tc = gw.tc
    t = Twitch()

    async def chatters(channel=""):
        return [{"user_id": uid, "user_login": login, "user_name": login.title()} for login, uid in t.chat]

    async def followed_at(user_id, channel=""):
        t.follow_checks += 1
        return t.follows.get(user_id)

    async def subscribers(user_ids, channel=""):
        return {u: t.subs[u] for u in user_ids if u in t.subs}

    async def send_chat(text, channel=""):
        t.sent.append((text, channel))
        return {"ok": True}

    async def nothing():
        pass

    for name, fn in (("chatters", chatters), ("followed_at", followed_at), ("subscribers", subscribers),
                     ("send_chat", send_chat)):
        monkeypatch.setattr(tc, name, fn)
    monkeypatch.setattr(tc.RUNNER, "ensure_started", nothing)
    tc.CONFIG["channels"] = tc.normalise_channels([{"login": "hexacore_ai"}])
    tc.SECRETS.accounts = {"1": tc.Account({"access_token": "t", "user_id": "1", "user_login": "hexacore_ai", "scopes": SCOPES})}
    tc.STATE.sync_channels()
    # a quick show: no countdown, no hold, no chat delay (the real ones are seconds)
    monkeypatch.setitem(gw.NUMBERS, "countdown", (0, 60))
    gw.CONFIG.update({"countdown": 0, "hold": 0, "announce_delay": 0})
    t.gw = gw
    return t


def run(coro):
    return asyncio.run(coro)


def everyone():
    return {"everyone": True, "followers": False, "follow_seconds": 0, "subs": False}


def prize(gw, **kw):
    p = gw.clean_prize({"name": "Steam key: Hades", "description": "A roguelike", "quantity": 2, "codes": ["AAA", "BBB"], **kw})
    gw.CONFIG["prizes"].append(p)
    return p


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


# ---------------------------------------------------------------- prizes and settings

def test_a_prize_is_checked(tw):
    gw = tw.gw
    p = gw.clean_prize({"name": "  Hoodie ", "codes": "X1\n\n X2 \n", "image": "/media/giveaways/hoodie.png", "quantity": "3"})
    assert (p["name"], p["codes"], p["quantity"], p["image"]) == ("Hoodie", ["X1", "X2"], 3, "/media/giveaways/hoodie.png")
    for bad, why in (({"quantity": -1}, "quantity"), ({"image": "file:///etc/passwd"}, "picture"), ({"image": "/media/../x"}, "picture"),
                     ({"codes": 7}, "codes")):
        with pytest.raises(gw.Problem, match=why):
            gw.clean_prize(bad)
    assert gw.public_prize(p)["codes"] == 2                                   # what the overlay and bots see: a count


def test_the_api_keeps_prizes_and_settings_and_never_lets_the_winners_list_be_written(world, tw):
    gw = tw.gw
    c = TestClient(world.app)
    r = c.post("/giveaways/api/prizes", json={"name": "Hoodie", "codes": ["C1"], "id": "mine"})
    pid = r.json()["prize"]["id"]
    assert r.status_code == 200 and pid != "mine"
    assert c.post("/giveaways/api/prizes", json={"description": "no name"}).json()["error"] == "a prize needs its name"
    assert c.post(f"/giveaways/api/prizes/{pid}", json={"quantity": 5}).json()["prize"]["codes"] == ["C1"]
    assert c.post("/giveaways/api/prizes/nope", json={"quantity": 5}).status_code == 404
    r = c.post("/giveaways/api/config", json={"eligible": {"followers": True}, "look": {"accent": "#00ff00"},
                                              "history": [{"id": "fake"}], "exclude": "NightBot\n@StreamElements, nightbot"})
    cfg = r.json()["config"]
    assert cfg["eligible"]["followers"] is True and cfg["eligible"]["everyone"] is True       # merged, not replaced
    assert cfg["look"]["accent"] == "#00ff00" and cfg["look"]["title"] == "GIVEAWAY"
    assert cfg["history"] == [] and cfg["exclude"] == "nightbot, streamelements"
    for bad in ({"countdown": -1}, {"look": {"accent": "red"}}, {"look": {"position": "sideways"}}, {"channel": "no way"}):
        assert c.post("/giveaways/api/config", json=bad).status_code == 400, bad
    status = c.get("/giveaways/api/status").json()
    assert "C1" not in str(status) and status["prizes"][0]["codes"] == 1                     # never the codes
    assert c.get("/giveaways/api/config").json()["prizes"][0]["codes"] == ["C1"]             # (the tab's own call has them)
    assert c.post(f"/giveaways/api/prizes/{pid}/delete").json() == {"ok": True} and gw.CONFIG["prizes"] == []


# ---------------------------------------------------------------- who can win

def test_the_broadcaster_hexcast_and_bots_never_win(tw):
    gw = tw.gw
    tw.chat.append(("hexbot", "9"))
    gw.tc.SECRETS.accounts["9"] = gw.tc.Account({"access_token": "t", "user_id": "9", "user_login": "HexBot", "scopes": SCOPES})
    pool, in_chat = run(gw.pool("hexacore_ai"))
    assert in_chat == 7 and {u["login"] for u in pool} == {"alice", "bob", "carol", "dave"}


def test_everyone_in_chat_has_the_same_chance(tw):
    gw = tw.gw
    pool, _ = run(gw.pool("hexacore_ai"))
    seen = collections.Counter(run(gw.pick(pool, everyone(), "hexacore_ai"))["login"] for _ in range(2000))
    assert set(seen) == {"alice", "bob", "carol", "dave"} and min(seen.values()) > 380              # ~500 each


def test_followers_for_long_enough_or_subscribers(tw):
    gw = tw.gw
    pool, _ = run(gw.pool("hexacore_ai"))
    followers_week = {"everyone": False, "followers": True, "follow_seconds": 7 * DAY, "subs": False}
    wins = {run(gw.pick(pool, followers_week, "hexacore_ai"))["login"] for _ in range(200)}
    assert wins == {"alice", "dave"}                                                         # bob followed 2 hours ago
    any_follower = {**followers_week, "follow_seconds": 0}
    assert {run(gw.pick(pool, any_follower, "hexacore_ai"))["login"] for _ in range(200)} == {"alice", "bob", "dave"}
    subs = {"everyone": False, "followers": False, "follow_seconds": 0, "subs": True}
    assert {run(gw.pick(pool, subs, "hexacore_ai"))["login"] for _ in range(50)} == {"carol"}
    both = {**followers_week, "subs": True}
    seen = collections.Counter(run(gw.pick(pool, both, "hexacore_ai"))["login"] for _ in range(1500))
    assert set(seen) == {"alice", "carol", "dave"} and min(seen.values()) > 380              # the union, evenly
    with pytest.raises(gw.Problem, match="tick who can win"):
        run(gw.pick(pool, {"everyone": False, "followers": False, "follow_seconds": 0, "subs": False}, "hexacore_ai"))


def test_followers_are_looked_up_only_as_names_come_up(tw):
    gw = tw.gw
    tw.chat = [(f"viewer{i}", str(100 + i)) for i in range(3000)]
    tw.follows = {str(100 + i): NOW - DAY for i in range(3000)}                            # everyone follows
    pool, _ = run(gw.pool("hexacore_ai"))
    run(gw.pick(pool, {"everyone": False, "followers": True, "follow_seconds": 0, "subs": False}, "hexacore_ai"))
    assert tw.follow_checks == 1                                                            # not 3000
    tw.follows = {}
    with pytest.raises(gw.Problem, match="first 500"):
        run(gw.pick(pool, {"everyone": False, "followers": True, "follow_seconds": 0, "subs": False}, "hexacore_ai"))


# ---------------------------------------------------------------- the show

def test_a_draw_reveals_records_announces_and_hands_out_a_code(world, tw):
    gw = tw.gw
    p = prize(gw)
    gw.CONFIG["message"] = "GG @{winner} ({login}) - {prize}!"
    with TestClient(world.app) as c, c.websocket_connect("/giveaways/ws/overlay") as ov, c.websocket_connect("/giveaways/ws") as panel:
        assert ov.receive_json()["show"]["state"] == "idle"
        panel.receive_json(), panel.receive_json()                                            # hello, status
        r = c.post("/giveaways/api/draw", json={"prize": p["id"]})
        assert r.status_code == 200
        d = r.json()["draw"]
        winner = d["winner"]["login"]
        assert winner in {"alice", "bob", "carol", "dave"} and d["pool"] == 4 and d["in_chat"] == 6
        assert d["winner"]["name"] not in d["names"] or len(d["names"]) == 1                 # the jumble is everyone else
        drawing = ov.receive_json()["show"]
        assert drawing["state"] == "drawing" and "AAA" not in str(drawing) and drawing["prize"]["codes"] == 2
        wait_for(lambda: gw.SHOW.get("state") == "revealed" or gw.SHOW.get("state") == "idle")
        event = next(m for m in iter(panel.receive_json, None) if m.get("type") == "event")
        assert event["event"] == "winner" and event["winner"]["login"] == winner
        wait_for(lambda: tw.sent)
    entry = gw.CONFIG["history"][0]
    assert entry["winner"]["login"] == winner and entry["code"] == "AAA" and entry["prize"] == "Steam key: Hades"
    assert gw.CONFIG["prizes"][0]["quantity"] == 1 and gw.CONFIG["prizes"][0]["codes"] == ["BBB"]
    assert tw.sent == [(f"GG @{winner.title()} ({winner}) - Steam key: Hades!", "hexacore_ai")]


def test_a_reroll_gives_the_prize_back_and_draws_without_that_winner(world, tw):
    gw = tw.gw
    p = prize(gw, quantity=1, codes=["ONLY"])
    gw.CONFIG["exclude_winners"] = False
    with TestClient(world.app) as c:
        first = c.post("/giveaways/api/draw", json={"prize": p["id"]}).json()["draw"]["winner"]["login"]
        wait_for(lambda: gw.CONFIG["history"])
        assert gw.CONFIG["prizes"][0]["quantity"] == 0
        assert "no Steam key: Hades left" in c.post("/giveaways/api/draw", json={"prize": p["id"]}).json()["error"]
        r = c.post("/giveaways/api/reroll")                                                 # allowed with none left
        assert r.status_code == 200
        second = r.json()["draw"]["winner"]["login"]
        wait_for(lambda: len(gw.CONFIG["history"]) == 2)
    assert second != first
    new, old = gw.CONFIG["history"]
    assert old["rerolled"] is True and new["code"] == "ONLY" and new["winner"]["login"] == second
    assert gw.CONFIG["prizes"][0]["quantity"] == 0 and gw.CONFIG["prizes"][0]["codes"] == []
    with TestClient(world.app) as c:
        assert "already drawn again" in c.post("/giveaways/api/reroll", json={"entry": old["id"]}).json()["error"]


def test_past_winners_sit_out_until_the_list_is_cleared(world, tw):
    gw = tw.gw
    p = prize(gw, quantity=10, codes=[])
    tw.chat = [("hexacore_ai", "1"), ("alice", "2"), ("bob", "3")]
    with TestClient(world.app) as c:
        winners = []
        for _ in range(2):
            winners.append(c.post("/giveaways/api/draw", json={"prize": p["id"]}).json()["draw"]["winner"]["login"])
            wait_for(lambda: gw.SHOW.get("state") != "drawing")
        assert sorted(winners) == ["alice", "bob"]
        r = c.post("/giveaways/api/draw", json={"prize": p["id"]})
        assert r.status_code == 400 and "none left after the exclusions" in r.json()["error"]
        c.post("/giveaways/api/history/clear")
        assert c.post("/giveaways/api/draw", json={"prize": p["id"]}).status_code == 200


def test_nobody_who_qualifies_is_said_in_numbers(world, tw):
    gw = tw.gw
    p = prize(gw)
    tw.subs = {}
    c = TestClient(world.app)
    r = c.post("/giveaways/api/draw", json={"prize": p["id"], "eligible": {"everyone": False, "subs": True}})
    assert r.status_code == 400 and r.json()["error"] == "nobody can win: 6 in chat, 4 after the exclusions, none of them subscribers"
    assert gw.SHOW == {"state": "idle"} and gw.CONFIG["eligible"]["everyone"] is True       # a one-off, settings untouched


def test_one_draw_at_a_time_and_cancel_means_nobody_wins(world, tw):
    gw = tw.gw
    p = prize(gw)
    gw.CONFIG["countdown"] = 30
    with TestClient(world.app) as c:
        assert c.post("/giveaways/api/draw", json={"prize": p["id"]}).status_code == 200
        assert c.post("/giveaways/api/draw", json={"prize": p["id"]}).status_code == 409
        assert c.post("/giveaways/api/hide").status_code == 409                              # a countdown is cancelled, not hidden
        assert c.post("/giveaways/api/cancel").json() == {"ok": True}
        assert gw.SHOW == {"state": "idle"} and c.post("/giveaways/api/cancel").status_code == 400
    assert gw.CONFIG["history"] == [] and gw.CONFIG["prizes"][0]["quantity"] == 2 and tw.sent == []


def test_show_the_prize_then_hide_it(world, tw):
    gw = tw.gw
    p = prize(gw)
    with TestClient(world.app) as c, c.websocket_connect("/giveaways/ws/overlay") as ov:
        ov.receive_json()
        assert c.post("/giveaways/api/show", json={"prize": p["id"]}).status_code == 200
        shown = ov.receive_json()["show"]
        assert shown["state"] == "showing" and shown["prize"]["name"] == "Steam key: Hades" and "AAA" not in str(shown)
        assert c.post("/giveaways/api/hide").json() == {"ok": True}
        assert ov.receive_json()["show"] == {"state": "idle"}


def test_a_test_in_the_preview_reaches_only_the_preview(world, tw):
    gw = tw.gw
    prize(gw)
    with TestClient(world.app) as c, c.websocket_connect("/giveaways/ws/overlay") as obs, \
            c.websocket_connect("/giveaways/ws/overlay?preview=1") as pv:
        obs.receive_json(), pv.receive_json()
        r = c.post("/giveaways/api/test", json={"where": "preview"})
        assert r.status_code == 200 and r.json()["draw"]["test"] is True
        m = pv.receive_json()
        assert m["preview_only"] is True and m["show"]["state"] == "drawing" and m["show"]["winner"]["name"] in gw.TEST_NAMES
        assert gw.SHOW == {"state": "idle"}                                                # the stream's overlay never saw it
        c.post("/giveaways/api/show", json={"prize": gw.CONFIG["prizes"][0]["id"]})
        assert obs.receive_json()["show"]["state"] == "showing"                            # (the first thing OBS got)
    assert gw.CONFIG["history"] == [] and tw.sent == []


def test_who_can_win_right_now(world, tw):
    gw = tw.gw
    c = TestClient(world.app)
    assert c.get("/giveaways/api/pool").json() == {"ok": True, "channel": "hexacore_ai", "in_chat": 6, "pool": 4,
                                                   "subs": None, "groups": "everyone in chat"}
    gw.CONFIG["eligible"] = {"everyone": False, "followers": True, "follow_seconds": 3 * DAY, "subs": True}
    d = c.get("/giveaways/api/pool").json()
    assert d["subs"] == 1 and d["groups"] == "followers (for 3 days) or subscribers"


def test_status_says_why_a_draw_cannot_work(world, tw):
    gw = tw.gw
    c = TestClient(world.app)
    assert c.get("/giveaways/api/status").json()["problem"] == ""
    gw.tc.SECRETS.accounts["1"].rec["scopes"] = ["user:read:chat"]                          # signed in before Giveaways
    assert "moderator:read:chatters" in c.get("/giveaways/api/status").json()["problem"]


def test_the_socket_draws_for_a_bot(world, tw):
    gw = tw.gw
    p = prize(gw)
    with TestClient(world.app) as c, c.websocket_connect("/giveaways/ws") as ws:
        assert "draw" in ws.receive_json()["commands"]
        ws.receive_json()
        ws.send_json({"cmd": "draw", "prize": p["id"], "rid": 7})
        r = next(m for m in iter(ws.receive_json, None) if m.get("type") == "result")
        assert r["ok"] and r["rid"] == 7 and r["draw"]["winner"]["login"] in {"alice", "bob", "carol", "dave"}
        ws.send_json({"cmd": "pool", "rid": 8})
        r = next(m for m in iter(ws.receive_json, None) if m.get("type") == "result" and m.get("rid") == 8)
        assert r["ok"] and r["in_chat"] == 6
        ws.send_json({"cmd": "update_prize", "rid": 9, "quantity": 3})
        r = next(m for m in iter(ws.receive_json, None) if m.get("type") == "result" and m.get("rid") == 9)
        assert r["ok"] is False and "needs `id`" in r["error"]


def test_prize_pictures_are_uploaded_listed_and_deleted(world, tw, monkeypatch, tmp_path):
    gw = tw.gw
    monkeypatch.setattr(gw, "IMAGE_DIR", tmp_path / "giveaways")
    c = TestClient(world.app)
    assert c.get("/giveaways/api/images").json() == {"images": []}
    up = c.post("/giveaways/api/images", files={"file": ("../My Prize!.PNG", b"\x89PNG....", "image/png")})
    assert up.status_code == 200 and up.json()["url"] == "/media/giveaways/My_Prize.png"
    assert c.post("/giveaways/api/images", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert c.post("/giveaways/api/images/delete", json={"name": "../../My_Prize.png"}).json()["images"] == []


def test_the_tab_overlay_and_dot(world, tw):
    c = TestClient(world.app)
    page = c.get("/giveaways").text
    assert "TwitchConnect.mount" in page and "moderator:read:chatters" in page
    assert "/giveaways/ws/overlay" in c.get("/giveaways/overlay").text
    nav = [i for i in world.host.nav_items() if i["id"] == "giveaways"]
    assert nav and nav[0]["href"] == "/giveaways"


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


@pytest.mark.parametrize("page", ["giveaways_panel.html", "giveaways_overlay.html"])
def test_the_page_scripts_are_valid_javascript(page, tmp_path):
    html = (STATIC / page).read_text(encoding="utf-8")
    js = tmp_path / (page + ".js")
    js.write_text("\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)), encoding="utf-8")
    for f in (js, STATIC / "nav.js"):
        r = subprocess.run([_node(), "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr


def test_the_overlay_lands_the_reel_on_the_winner():
    """The overlay's own logic in node: the strip ends with the winner, and the reveal shows them."""
    out = subprocess.run([_node(), "-e", r"""
      const fs = require('fs'), vm = require('vm');
      const html = fs.readFileSync(%s, 'utf8');
      const js = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
      const els = {};
      const el = (id) => els[id] || (els[id] = { id, style: { setProperty(){}, }, classList: { add(){}, remove(){}, toggle(){} },
        setAttribute(k, v){ this[k] = v; }, getAttribute(k){ return this[k]; }, innerHTML: '', textContent: '', className: '',
        children: [], getContext(){ return { clearRect(){}, save(){}, restore(){}, translate(){}, rotate(){}, fillRect(){} }; },
        getBoundingClientRect(){ return { left: 0, top: 0, width: 100, height: 100 }; } });
      const sb = { document: { getElementById: el }, window: { innerWidth: 1920, innerHeight: 1080, addEventListener(){} },
        location: { search: '', protocol: 'http:', host: 'h', origin: 'http://h' }, performance: { now: () => 0 },
        requestAnimationFrame(){ return 1; }, cancelAnimationFrame(){}, setTimeout(){ return 1; }, clearTimeout(){},
        WebSocket: function(){ }, Math, JSON, String, encodeURIComponent, parseInt, console };
      sb.window.addEventListener = () => {}; sb.addEventListener = () => {};
      vm.createContext(sb);
      vm.runInContext(js.replace("window.addEventListener('message'", "(()=>{})('message'"), sb);
      const show = { state: 'drawing', id: 'x', countdown: 5, elapsed: 0, hold: 0, names: ['A', 'B', 'C'],
                     winner: { name: 'Winner', login: 'winner' }, prize: { name: 'P' } };
      const n = sb.buildStrip(show);
      const strip = els['strip'].innerHTML;
      sb.reveal(show, false);
      console.log(JSON.stringify({ n, last: strip.split('<div>').pop(), foot: els['foot'].textContent, cls: els['card'].className }));
    """ % repr(str(STATIC / "giveaways_overlay.html"))], capture_output=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    import json
    r = json.loads(out.stdout)
    assert r["n"] == 35 and r["last"] == "Winner</div>" and r["foot"] == "@Winner wins!" and "revealed" in r["cls"]
