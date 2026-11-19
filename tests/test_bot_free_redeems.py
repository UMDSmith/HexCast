"""Bot-Free Redeems: rows checked, which row a redeem / command / cheer plays, who may and the cooldowns, why a sound
did not play, the status the tab shows, the reward list, the tab's routes, and the Twitch alerts keeping their own
clip quiet for a redeem that plays here. Nothing talks to Twitch (Helix is replaced, the connection never starts)
and nothing reaches a soundboard: its routes are replaced by a fake one that records what played."""

import asyncio
import importlib
import re
import shutil
import subprocess
import types
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("websockets")

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "catalog" / "bot_free_redeems" / "static"
CONFIGS = ("bot_free_redeems.json", "twitch_core.json", "twitch_secrets.json", "twitch.json", "twitch_queue.json")
SCOPES = ["user:read:chat", "channel:read:redemptions", "bits:read"]
REDEEM = "channel.channel_points_custom_reward_redemption.add"


@pytest.fixture
def world(real_world):
    real_world.installer.install("bot_free_redeems")
    real_world.host.load_all()
    assert {"bot_free_redeems", "twitch_core"} == set(real_world.host.loaded), real_world.host.errors
    yield real_world
    for name in CONFIGS:
        (real_world.config / name).unlink(missing_ok=True)


class Board:
    """The soundboard's /api/list and /api/play, faked: `played` lists the clips, `reply` (code, body) answers
    every play instead."""

    def __init__(self):
        self.played, self.reply = [], None
        self.clips = {"audio": ["airhorn", "bonk", "ding"], "video": ["wow"]}

    async def call(self, path):
        if path == "/api/list":
            return 200, self.clips
        if self.reply is not None:
            return self.reply
        self.played.append(unquote(path.rsplit("/", 1)[1]))
        return 200, {"ok": True, "delivered": 1}


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now


def _quiet(tc, monkeypatch):
    async def nothing():
        pass
    monkeypatch.setattr(tc.RUNNER, "ensure_started", nothing)            # no connection to Twitch


@pytest.fixture
def rd(world, monkeypatch):
    mod = importlib.import_module("hexcast_plugins.bot_free_redeems.redeems")
    _quiet(mod.tc, monkeypatch)
    mod.board = Board()
    monkeypatch.setattr(mod, "soundboard", mod.board.call)
    mod.clock = Clock()
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(time=mod.clock.time))
    return mod


def watch(tc, channels=({"login": "hexacore_ai"},), account="hexacore_ai", scopes=SCOPES):
    tc.CONFIG["channels"] = tc.normalise_channels(list(channels))
    tc.SECRETS.accounts = {"11": tc.Account({"access_token": "t", "user_id": "11", "user_login": account,
                                              "scopes": list(scopes)})} if account else {}
    tc.STATE.sync_channels()
    for r in channels:
        tc.STATE.channel(r["login"]).connected = True


def rows(rd, *rows_):
    rd.CONFIG = rd.clean_config({"on": True, "triggers": list(rows_)})
    return rd.CONFIG["triggers"]


def run(coro):
    return asyncio.run(coro)


def chat(rd, text, who="viewer", channel="hexacore_ai", mid="", bits=0, **flags):
    msg = {"id": mid, "text": text, "bits": bits, "user": {"login": who, "name": who.title()},
           "flags": {"broadcaster": False, "mod": False, "vip": False, "sub": False, **flags}, "badges": []}
    run(rd.on_chat(msg, rd.tc.STATE.channel(channel)))


def redeem(rd, reward_id, title, rid="", who="Viewer", channel="hexacore_ai"):
    ev = {"id": rid, "user_name": who, "reward": {"id": reward_id, "title": title}, "user_input": ""}
    run(rd.on_event(REDEEM, ev, None, rd.tc.STATE.channel(channel)))


def last(rd):
    return rd.RECENT[-1]


# ---------------------------------------------------------------- the rows

def test_a_row_is_checked(rd):
    t = rd.clean_trigger({"kind": "command", "command": "Airhorn, !HORN  airhorn", "clips": ["airhorn", " ", "AIRHORN", "bonk"]})
    assert t["command"] == "!airhorn !horn" and t["clips"] == ["airhorn", "bonk"]
    assert re.match(r"^[a-z0-9]{8}$", t["id"]) and t["who"] == "everyone" and t["cooldown"] == 30
    assert rd.clean_trigger({"kind": "redeem", "reward": "  Big   Airhorn  "})["reward"] == "Big Airhorn"
    assert rd.clean_trigger({"kind": "bits", "channel": "#HexacoreAI"})["channel"] == "hexacoreai"
    for bad in ({"kind": "follow"}, {"kind": "command", "who": "friends"}, {"kind": "bits", "bits": 0},
                {"kind": "command", "cooldown": "soon"}, {"kind": "redeem", "reward_id": "no spaces!"},
                {"kind": "command", "command": "!a !b !c !d !e !f"}, {"kind": "bits", "clips": [str(i) for i in range(11)]},
                {"kind": "bits", "channel": "not a channel!"}):
        with pytest.raises(rd.Problem):
            rd.clean_trigger(bad)
    cfg = rd.clean_config({"triggers": [{"id": "abcd1234", "kind": "bits"}, {"id": "abcd1234", "kind": "bits"}]})
    assert len({t["id"] for t in cfg["triggers"]}) == 2                       # a copied row gets an id of its own


# ---------------------------------------------------------------- redeems

def test_a_redeem_plays_its_rewards_sound(rd):
    watch(rd.tc)
    rows(rd, {"kind": "redeem", "reward_id": "r-1", "reward": "Airhorn", "clips": ["airhorn"]},
         {"kind": "redeem", "reward": "Bonk", "clips": ["bonk"]})
    redeem(rd, "r-1", "Airhorn (renamed on Twitch)", rid="x1")
    assert rd.board.played == ["airhorn"] and last(rd)["result"] == "played" and last(rd)["user"] == "Viewer"
    redeem(rd, "r-9", "bonk", rid="x2")                                        # found by its name
    redeem(rd, "r-9", "bonk", rid="x2")                                        # Twitch sending it twice: once
    assert rd.board.played == ["airhorn", "bonk"]
    redeem(rd, "r-5", "Hydrate", rid="x3")
    assert rd.board.played == ["airhorn", "bonk"]
    assert last(rd)["result"] == "skipped" and "no sound set" in last(rd)["note"] and last(rd)["what"] == "Hydrate"
    assert rd.STATS[rd.CONFIG["triggers"][0]["id"]]["played"] == 1


def test_switched_off_nothing_plays(rd):
    watch(rd.tc)
    rows(rd, {"kind": "redeem", "reward_id": "r-1", "clips": ["airhorn"], "on": False},
         {"kind": "command", "command": "!airhorn", "clips": ["airhorn"]})
    redeem(rd, "r-1", "Airhorn")
    rd.CONFIG["on"] = False
    chat(rd, "!airhorn")
    assert rd.board.played == []
    rd.CONFIG["on"] = True
    chat(rd, "!airhorn")
    assert rd.board.played == ["airhorn"]


# ---------------------------------------------------------------- commands

def test_a_command_is_the_first_word(rd):
    watch(rd.tc)
    rows(rd, {"kind": "command", "command": "!airhorn !horn", "clips": ["airhorn"], "cooldown": 0})
    for text in ("!AIRHORN", "!horn lol", "@hexacore_ai !airhorn"):
        chat(rd, text)
    chat(rd, "lol !airhorn")
    chat(rd, "!airhorns")
    assert rd.board.played == ["airhorn"] * 3


def test_who_may_use_a_command(rd):
    watch(rd.tc)
    rows(rd, {"kind": "command", "command": "!sub", "who": "subs", "clips": ["airhorn"], "cooldown": 0},
         {"kind": "command", "command": "!mod", "who": "mods", "clips": ["bonk"], "cooldown": 0},
         {"kind": "command", "command": "!me", "who": "broadcaster", "clips": ["ding"], "cooldown": 0})
    chat(rd, "!sub")
    assert rd.board.played == [] and "only subscribers" in last(rd)["note"]
    chat(rd, "!sub", sub=True)
    chat(rd, "!sub", vip=True)
    chat(rd, "!mod", vip=True)
    chat(rd, "!mod", mod=True)
    chat(rd, "!me", mod=True)
    chat(rd, "!me", who="hexacore_ai")                                          # the channel's own login
    assert rd.board.played == ["airhorn", "airhorn", "bonk", "ding"]
    msg = {"text": "!sub", "user": {"login": "founder"}, "flags": {}, "badges": [{"set_id": "founder", "id": "0"}]}
    run(rd.on_chat(msg, rd.tc.STATE.channel("hexacore_ai")))                   # a founder is a subscriber
    assert rd.board.played[-1] == "airhorn" and len(rd.board.played) == 5


def test_cooldowns_hold_everyone_and_the_same_viewer_but_never_the_broadcaster(rd):
    watch(rd.tc)
    rows(rd, {"kind": "command", "command": "!airhorn", "clips": ["airhorn"], "cooldown": 30, "user_cooldown": 120})
    chat(rd, "!airhorn", who="alice")
    rd.clock.now += 10
    chat(rd, "!airhorn", who="bob")
    assert rd.board.played == ["airhorn"] and "on cooldown (20 s left)" in last(rd)["note"]
    chat(rd, "!airhorn", who="hexacore_ai", broadcaster=True)                  # never held up
    assert len(rd.board.played) == 2
    rd.clock.now += 31
    chat(rd, "!airhorn", who="alice")
    assert len(rd.board.played) == 2 and "Alice used it" in last(rd)["note"]
    chat(rd, "!airhorn", who="bob")
    assert len(rd.board.played) == 3


def test_a_sound_nobody_heard_does_not_start_the_cooldown(rd):
    watch(rd.tc)
    rows(rd, {"kind": "command", "command": "!airhorn", "clips": ["airhorn"], "cooldown": 300})
    rd.board.reply = (200, {"ok": True, "delivered": 0})
    chat(rd, "!airhorn")
    assert last(rd)["result"] == "failed" and "Soundboard overlay (/overlay) is not open" in last(rd)["note"]
    rd.board.reply = (404, {"detail": "no media named 'airhorn'"})
    chat(rd, "!airhorn")
    assert "no clip 'airhorn' on the Soundboard" in last(rd)["note"]
    rd.board.reply = (200, {"ok": True, "delivered": 0, "suppressed": True, "next_in_ms": 4200})
    chat(rd, "!airhorn")
    assert "Soundboard cooldown (5 s left)" in last(rd)["note"]
    rd.board.reply = None
    chat(rd, "!airhorn")
    chat(rd, "!airhorn")
    assert rd.board.played == ["airhorn"] and "on cooldown" in last(rd)["note"]


# ---------------------------------------------------------------- bits

def test_the_biggest_bits_amount_a_cheer_reaches_wins(rd):
    watch(rd.tc)
    rows(rd, {"kind": "bits", "bits": 100, "clips": ["bonk"]}, {"kind": "bits", "bits": 1000, "clips": ["airhorn"]})
    chat(rd, "Cheer500 nice", bits=500, mid="a")
    chat(rd, "Cheer1500", bits=1500, mid="b")
    chat(rd, "Cheer1500", bits=1500, mid="b")                                  # the same line twice: once
    chat(rd, "Cheer50", bits=50, mid="c")
    assert rd.board.played == ["bonk", "airhorn"]
    assert last(rd)["result"] == "skipped" and last(rd)["what"] == "50 bits"
    assert rd.claims("channel.cheer", {"bits": 100}, "hexacore_ai") and not rd.claims("channel.cheer", {"bits": 99}, "hexacore_ai")


def test_several_sounds_never_play_the_same_one_twice_running(rd):
    watch(rd.tc)
    rows(rd, {"kind": "command", "command": "!fart", "clips": ["airhorn", "bonk"], "cooldown": 0})
    for _ in range(8):
        chat(rd, "!fart")
    p = rd.board.played
    assert len(p) == 8 and all(a != b for a, b in zip(p, p[1:]))


def test_rows_listen_in_their_own_channel(rd):
    watch(rd.tc, [{"login": "hexacore_ai"}, {"login": "hexacoreai", "label": "beta"}])
    rows(rd, {"kind": "command", "command": "!p", "clips": ["airhorn"], "cooldown": 0},
         {"kind": "command", "command": "!b", "clips": ["bonk"], "cooldown": 0, "channel": "hexacoreai"},
         {"kind": "command", "command": "!all", "clips": ["ding"], "cooldown": 0, "channel": "all"})
    for ch in ("hexacore_ai", "hexacoreai"):
        for cmd in ("!p", "!b", "!all"):
            chat(rd, cmd, channel=ch)
    assert rd.board.played == ["airhorn", "ding", "bonk", "ding"]


# ---------------------------------------------------------------- what the tab shows

def test_the_status_says_what_stops_a_row(rd):
    watch(rd.tc, account="")                                                   # anonymous chat
    rows(rd, {"kind": "command", "command": "!a", "clips": ["airhorn"]},
         {"kind": "command", "command": "!a !b", "clips": ["bonk"]},
         {"kind": "command", "command": "", "clips": ["bonk"]},
         {"kind": "bits", "bits": 100, "clips": []},
         {"kind": "bits", "bits": 200, "clips": ["gone"]},
         {"kind": "redeem", "clips": ["airhorn"]},
         {"kind": "redeem", "reward": "Airhorn", "clips": ["airhorn"]})
    got = [t["problem"] for t in run(rd.status())["triggers"]]
    assert got[0] == ""                                                         # commands need no sign-in
    assert "a row above uses the same command" in got[1]
    assert got[2].startswith("type a command") and got[3] == "pick a sound"
    assert "not on the Soundboard" in got[4] and "gone" in got[4]
    assert got[5] == "pick a reward" and got[6] == "redeems: not signed in to Twitch - connect an account in the Twitch connection box"
    watch(rd.tc, account="somebody_else")
    assert "own account" in run(rd.status())["triggers"][6]["problem"]
    watch(rd.tc)
    s = run(rd.status())
    assert s["triggers"][6]["problem"] == "" and s["channels"][0]["redeems"] == ""
    rd.tc.STATE.channel("hexacore_ai").subs_failed = [f"{REDEEM} (403 subscription missing proper authorization)"]
    assert "Twitch refused the redeem events (403" in run(rd.status())["triggers"][6]["problem"]
    rd.tc.STATE.channel("hexacore_ai").connected = False
    assert "not connected to #hexacore_ai's chat" in run(rd.status())["triggers"][0]["problem"]
    rd.tc.CONFIG["channels"] = []
    rd.tc.SECRETS.accounts = {}
    rd.tc.STATE.sync_channels()
    assert run(rd.status())["triggers"][0]["problem"].startswith("no channel")


def test_the_tabs_routes(world, rd):
    watch(rd.tc)
    c = TestClient(world.app)
    r = c.post("/redeems/api/config", json={"triggers": [{"kind": "command", "command": "horn", "clips": ["airhorn"]}]})
    assert r.status_code == 200 and r.json()["config"]["triggers"][0]["command"] == "!horn"
    assert c.get("/redeems/api/config").json()["triggers"][0]["clips"] == ["airhorn"]
    assert (world.config / "bot_free_redeems.json").is_file()
    assert c.post("/redeems/api/config", json={"triggers": [{"kind": "raid"}]}).status_code == 400
    assert c.post("/redeems/api/test", json={"clips": ["wow"]}).json() == {"ok": True, "clip": "wow"}
    assert rd.board.played == ["wow"] and last(rd)["kind"] == "test"
    assert c.post("/redeems/api/test", json={"clips": []}).status_code == 400
    rd.board.reply = (200, {"delivered": 0})
    r = c.post("/redeems/api/test", json={"clips": ["wow"]})
    assert r.status_code == 400 and "not open in OBS" in r.json()["error"]
    s = c.get("/redeems/api/status").json()
    assert s["on"] and s["clips"] == ["airhorn", "bonk", "ding", "wow"] and s["recent"][0]["result"] == "failed"
    with c.websocket_connect("/redeems/ws") as ws:
        m = ws.receive_json()
        assert m["type"] == "status" and m["status"]["triggers"][0]["command"] == "!horn"


def test_the_reward_list_comes_from_twitch(world, rd, monkeypatch):
    watch(rd.tc)
    rd.tc.STATE.channel("hexacore_ai").id = "11"
    asked = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

    answer = [R(200, {"data": [
        {"id": "b", "title": "Hydrate", "cost": 500, "is_enabled": True, "is_paused": False, "image": None,
         "default_image": {"url_1x": "https://x/1.png"}, "background_color": "#00C7AC"},
        {"id": "a", "title": "Airhorn", "cost": 100, "is_enabled": False, "is_paused": False, "image": None,
         "default_image": {}, "background_color": ""}]})]

    async def helix(method, path, *, acct, params=None, json_body=None, retry=True):
        asked.append((method, path, params, acct.user_login))
        return answer[0]

    monkeypatch.setattr(rd.tc, "helix", helix)
    c = TestClient(world.app)
    d = c.get("/redeems/api/rewards").json()
    assert d["ok"] and [w["title"] for w in d["rewards"]] == ["Airhorn", "Hydrate"]           # cheapest first
    assert d["rewards"][0]["enabled"] is False and d["rewards"][1]["image"] == "https://x/1.png"
    assert asked == [("GET", "/channel_points/custom_rewards", {"broadcaster_id": "11"}, "hexacore_ai")]
    answer[0] = R(403, {"message": "The broadcaster must have partner or affiliate status."})
    d = c.get("/redeems/api/rewards").json()
    assert not d["ok"] and "Affiliate or Partner" in d["error"] and "partner or affiliate status" in d["error"]
    watch(rd.tc, account="somebody_else")
    assert "own account" in c.get("/redeems/api/rewards").json()["error"]


def test_the_tab_and_its_dot(world, rd):
    c = TestClient(world.app)
    page = c.get("/redeems").text
    assert "/plugins/twitch_core/static/twitch_connect.js" in page and "TwitchConnect.mount" in page
    assert "/overlay" in page and "Add New Custom Reward" in page                              # the set-up steps
    nav = [i for i in world.host.nav_items() if i["id"] == "bot_free_redeems"]
    assert nav and nav[0]["href"] == "/redeems" and nav[0]["label"] == "Redeems" and nav[0]["state"] == "running"


def test_stopping_it_takes_its_listeners_along(world, rd):
    hooks = rd.tc.HOOKS
    before = (len(hooks.chat), len(hooks.event), len(hooks.status))
    asyncio.run(world.host.unload("bot_free_redeems"))
    assert (len(hooks.chat), len(hooks.event), len(hooks.status)) == tuple(n - 1 for n in before)
    assert "twitch_core" in world.host.loaded


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def test_the_tab_script_is_valid_javascript(tmp_path):
    html = (STATIC / "redeems_panel.html").read_text(encoding="utf-8")
    js = tmp_path / "panel.js"
    js.write_text("\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)), encoding="utf-8")
    for f in (js, STATIC / "nav.js"):
        r = subprocess.run([_node(), "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- with the Twitch plugin

def test_a_twitch_alert_keeps_its_clip_quiet_for_a_sound_played_here(real_world, monkeypatch):
    real_world.installer.install("twitch")
    real_world.installer.install("bot_free_redeems")
    real_world.host.load_all()
    assert {"twitch", "bot_free_redeems", "twitch_core"} <= set(real_world.host.loaded), real_world.host.errors
    try:
        tw = importlib.import_module("hexcast_plugins.twitch.twitch")
        rd = importlib.import_module("hexcast_plugins.bot_free_redeems.redeems")
        _quiet(rd.tc, monkeypatch)
        watch(rd.tc)
        rows(rd, {"kind": "redeem", "reward_id": "r-1", "clips": ["airhorn"]}, {"kind": "bits", "bits": 100, "clips": ["bonk"]})
        tw.CONFIG["alerts"]["redeem"].update(on=True, clip="ding")
        tw.CONFIG["alerts"]["cheer"].update(on=True, clip="ding", min_amount=1)
        alerts = []

        async def enqueue(alert, msg_id=None):
            alerts.append(alert)

        monkeypatch.setattr(tw.ALERT_QUEUE, "enqueue", enqueue)
        ch = rd.tc.STATE.channel("hexacore_ai")

        async def go():
            await tw.on_event(REDEEM, {"user_name": "V", "reward": {"id": "r-1", "title": "Airhorn"}}, "n1", ch)
            await tw.on_event(REDEEM, {"user_name": "V", "reward": {"id": "r-2", "title": "Hydrate"}}, "n2", ch)
            await tw.on_event("channel.cheer", {"user_name": "V", "bits": 500}, "n3", ch)
            await tw.on_event("channel.cheer", {"user_name": "V", "bits": 50}, "n4", ch)
            rd.CONFIG["on"] = False                                             # switched off: the alerts play theirs
            await tw.on_event(REDEEM, {"user_name": "V", "reward": {"id": "r-1", "title": "Airhorn"}}, "n5", ch)

        run(go())
        assert [(a["kind"], a["clip"]) for a in alerts] == [("redeem", ""), ("redeem", "ding"), ("cheer", ""),
                                                              ("cheer", "ding"), ("redeem", "ding")]
    finally:
        for name in CONFIGS:
            (real_world.config / name).unlink(missing_ok=True)
