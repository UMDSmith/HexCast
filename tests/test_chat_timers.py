"""Chat Timers: settings, the schedule (interval, chat lines, live only, the gap between messages), posting, the API
and the socket - on the shared Twitch connection, without the Twitch plugin. Nothing talks to Twitch: posting is
replaced, and the connection never starts."""

import asyncio
import importlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("websockets")

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "catalog" / "chat_timers" / "static"
SCOPES = ["user:read:chat", "user:write:chat"]
CONFIGS = ("chat_timers.json", "twitch_core.json", "twitch_secrets.json")


@pytest.fixture
def world(real_world):
    real_world.installer.install("chat_timers")
    real_world.host.load_all()
    assert {"chat_timers", "twitch_core"} == set(real_world.host.loaded), real_world.host.errors
    yield real_world
    for name in CONFIGS:
        (real_world.config / name).unlink(missing_ok=True)


@pytest.fixture
def tm(world, monkeypatch):
    mod = importlib.import_module("hexcast_plugins.chat_timers.timers")
    tc = mod.tc

    async def nothing():
        pass

    monkeypatch.setattr(tc.RUNNER, "ensure_started", nothing)            # no connection to Twitch
    monkeypatch.setattr(mod.LOOPS, "ensure_started", nothing)            # the test drives tick() itself
    return mod


class Sent:
    """Chat lines 'posted': items = [(text, channel)]; a channel in `refuse` makes Twitch refuse it."""

    def __init__(self):
        self.items, self.refuse = [], {}


@pytest.fixture
def sent(tm, monkeypatch):
    s = Sent()

    async def send_chat(text, channel=""):
        if channel in s.refuse:
            raise tm.tc.TwitchError(s.refuse[channel])
        s.items.append((text, channel))
        return {"ok": True, "channel": channel, "account": "hexacore_ai"}

    monkeypatch.setattr(tm.tc, "send_chat", send_chat)
    return s


def watch(tm, channels, live=(), signed_in=True):
    tc = tm.tc
    tc.CONFIG["channels"] = tc.normalise_channels(channels)
    tc.SECRETS.accounts = {"11": tc.Account({"access_token": "t", "user_id": "11", "user_login": "hexacore_ai",
                                              "scopes": SCOPES})} if signed_in else {}
    tc.STATE.sync_channels()
    for login in live:
        tc.STATE.channel(login).live = True


def chat(tm, channel, n=1, who="viewer"):
    ch = tm.tc.STATE.channel(channel)
    for _ in range(n):
        asyncio.run(tm.on_chat({"user": {"login": who}, "text": "hi"}, ch))


def timer(tm, **kw):
    t = tm.clean_timer({"text": "Join the Discord: https://discord.gg/x", **kw})
    tm.CONFIG["timers"].append(t)
    return t


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- settings

def test_a_timer_is_checked_and_filled_in(tm):
    t = tm.clean_timer({"text": "  hello  ", "every": "20", "channel": "#HexaCoreAI"})
    assert (t["text"], t["every"], t["lines"], t["live_only"], t["channel"], t["on"]) == ("hello", 20, 5, True, "hexacoreai", True)
    assert re.match(r"^[a-z0-9]{8}$", t["id"])
    for bad, why in (({"every": 0}, "every"), ({"every": "soon"}, "number"), ({"lines": -1}, "lines"),
                     ({"text": "x" * 501}, "500"), ({"channel": "not a channel"}, "not a channel")):
        with pytest.raises(tm.Problem, match=why):
            tm.clean_timer(bad)
    kept = tm.clean_timer({"every": 30}, t)                                # a change keeps the rest
    assert kept["id"] == t["id"] and kept["text"] == "hello" and kept["every"] == 30


def test_the_api_adds_changes_and_removes_timers(world, tm):
    c = TestClient(world.app)
    r = c.post("/chat_timers/api/timers", json={"text": "Follow on socials", "every": 10, "name": "Socials"})
    assert r.status_code == 200
    tid = r.json()["timer"]["id"]
    assert c.post("/chat_timers/api/timers", json={"every": 10}).json()["error"] == "a timer needs its text"
    r = c.post(f"/chat_timers/api/timers/{tid}", json={"lines": 0, "live_only": False, "id": "hijack"})
    assert r.json()["timer"]["lines"] == 0 and r.json()["timer"]["id"] == tid and r.json()["timer"]["every"] == 10
    assert c.post("/chat_timers/api/timers/nope", json={"lines": 1}).status_code == 404
    assert c.post("/chat_timers/api/config", json={"gap": 99999}).status_code == 400
    assert c.post("/chat_timers/api/config", json={"gap": 30, "on": False}).json()["config"]["gap"] == 30
    saved = tm.json.loads(tm.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["on"] is False and saved["timers"][0]["name"] == "Socials"
    assert c.post(f"/chat_timers/api/timers/{tid}/delete").json() == {"ok": True}
    assert tm.CONFIG["timers"] == [] and c.post(f"/chat_timers/api/timers/{tid}/delete").status_code == 404


def test_where_a_timer_posts(tm):
    watch(tm, [{"login": "hexacore_ai"}, {"login": "hexacoreai", "label": "beta"}, {"login": "off_ch", "enabled": False}])
    assert tm.targets(tm.clean_timer({"channel": "primary"})) == ["hexacore_ai"]
    assert tm.targets(tm.clean_timer({"channel": "all"})) == ["hexacore_ai", "hexacoreai"]           # not the one switched off
    assert tm.targets(tm.clean_timer({"channel": "hexacoreai"})) == ["hexacoreai"]
    assert tm.targets(tm.clean_timer({"channel": "off_ch"})) == [] and tm.targets(tm.clean_timer({"channel": "stranger"})) == []
    watch(tm, [], signed_in=False)
    assert tm.targets(tm.clean_timer({"channel": "primary"})) == []


# ---------------------------------------------------------------- the schedule

def test_a_timer_waits_for_its_interval_and_for_chat(tm, sent):
    watch(tm, [{"login": "hexacore_ai"}], live=["hexacore_ai"])
    t = timer(tm, every=10, lines=3)
    start = 1000.0
    assert run(tm.tick(start)) == [] and tm.waiting_for(t, "hexacore_ai", start) == "interval"   # a new timer waits a whole interval
    assert run(tm.tick(start + 599)) == []
    assert tm.waiting_for(t, "hexacore_ai", start + 600) == "chat"                             # time's up, chat is quiet
    chat(tm, "hexacore_ai", 5, who="HexaCore_AI")                                              # Hexcast's own lines do not count
    assert tm.waiting_for(t, "hexacore_ai", start + 600) == "chat"
    chat(tm, "hexacore_ai", 3)
    assert run(tm.tick(start + 600)) == [(t["id"], "hexacore_ai")]
    assert sent.items == [("Join the Discord: https://discord.gg/x", "hexacore_ai")]
    assert tm.waiting_for(t, "hexacore_ai", start + 601) == "interval"                         # and it starts over
    st = tm.status()["timers"][0]["targets"][0]
    assert st["sent"] == 1 and st["lines_needed"] == 3 and st["last_error"] == ""


def test_live_only_holds_while_offline_and_a_stream_start_restarts_the_clock(tm, sent):
    watch(tm, [{"login": "hexacore_ai"}])
    t = timer(tm, every=5, lines=0)
    any_time = timer(tm, every=5, lines=0, live_only=False, text="any time")
    run(tm.tick(0.0))
    assert run(tm.tick(300.0)) == [(any_time["id"], "hexacore_ai")]                            # the offline one only
    assert tm.waiting_for(t, "hexacore_ai", 10000.0) == "live"
    ch = tm.tc.STATE.primary()
    ch.live = True
    run(tm.on_event("stream.online", {}, "e1", ch))                                            # the stream starts: from now
    now = tm.time.time()
    assert tm.waiting_for(t, "hexacore_ai", now + 1) == "interval"
    assert tm.waiting_for(t, "hexacore_ai", now + 301) == ""


def test_due_together_they_take_turns(tm, sent):
    watch(tm, [{"login": "hexacore_ai"}], live=["hexacore_ai"])
    tm.CONFIG["gap"] = 60
    a = timer(tm, every=10, lines=0, text="first")
    b = timer(tm, every=5, lines=0, text="second")
    run(tm.tick(0.0))
    assert run(tm.tick(600.0)) == [(a["id"], "hexacore_ai")]                                  # both due: one of them
    assert tm.waiting_for(b, "hexacore_ai", 630.0) == "gap" and run(tm.tick(630.0)) == []
    assert run(tm.tick(660.0)) == [(b["id"], "hexacore_ai")]
    assert [x[0] for x in sent.items] == ["first", "second"]


def test_every_channel_counts_its_own_chat(tm, sent):
    watch(tm, [{"login": "hexacore_ai"}, {"login": "hexacoreai"}], live=["hexacore_ai", "hexacoreai"])
    t = timer(tm, every=1, lines=2, channel="all", text="hello {channel}, live for {uptime}")
    run(tm.tick(0.0))
    chat(tm, "hexacoreai", 2)
    assert run(tm.tick(60.0)) == [(t["id"], "hexacoreai")]
    assert sent.items[0][0].startswith("hello hexacoreai, live for ")
    assert tm.fill("{channel} {nope} {UPTIME}", "hexacore_ai") == "hexacore_ai {nope} offline"   # (no start time known)


def test_a_refused_message_is_shown_and_waits_another_interval(tm, sent):
    watch(tm, [{"login": "hexacore_ai"}], live=["hexacore_ai"])
    t = timer(tm, every=1, lines=0)
    sent.refuse["hexacore_ai"] = "Twitch dropped the chat message: duplicate"
    run(tm.tick(0.0))
    assert run(tm.tick(60.0)) == [(t["id"], "hexacore_ai")] and sent.items == []
    st = tm.status()["timers"][0]["targets"][0]
    assert "duplicate" in st["last_error"] and st["sent"] == 0
    assert run(tm.tick(70.0)) == []                                                            # not every few seconds
    assert "duplicate" in "\n".join(tm.tc.STATE.log)


def test_switched_off_nothing_posts(tm, sent):
    watch(tm, [{"login": "hexacore_ai"}], live=["hexacore_ai"])
    t = timer(tm, every=1, lines=0)
    run(tm.tick(0.0))
    tm.CONFIG["on"] = False
    assert run(tm.tick(120.0)) == [] and tm.waiting_for(t, "hexacore_ai", 120.0) == "off"
    tm.CONFIG["on"], t["on"] = True, False
    assert run(tm.tick(120.0)) == []


# ---------------------------------------------------------------- send now, status, the socket

def test_send_now_posts_whether_due_or_not(world, tm, sent):
    watch(tm, [{"login": "hexacore_ai"}, {"login": "hexacoreai"}])                            # offline, no chat
    c = TestClient(world.app)
    tid = c.post("/chat_timers/api/timers", json={"text": "Schedule: !schedule", "channel": "all"}).json()["timer"]["id"]
    r = c.post(f"/chat_timers/api/timers/{tid}/send")
    assert r.json()["sent"] == ["hexacore_ai", "hexacoreai"] and len(sent.items) == 2
    r = c.post(f"/chat_timers/api/timers/{tid}/send", json={"channel": "HexaCoreAI"})
    assert r.json()["sent"] == ["hexacoreai"]
    sent.refuse["hexacore_ai"] = "not signed in to Twitch"
    sent.refuse["hexacoreai"] = "not signed in to Twitch"
    r = c.post(f"/chat_timers/api/timers/{tid}/send")
    assert r.status_code == 400 and "not signed in" in r.json()["error"]


def test_status_says_why_posting_cannot_work(world, tm):
    c = TestClient(world.app)
    watch(tm, [{"login": "hexacore_ai"}], signed_in=False)
    assert "not signed in" in c.get("/chat_timers/api/status").json()["problem"]
    watch(tm, [{"login": "hexacore_ai"}])
    s = c.get("/chat_timers/api/status").json()
    assert s["problem"] == "" and s["channels"][0]["login"] == "hexacore_ai"


def test_the_socket_does_what_the_api_does(world, tm, sent):
    watch(tm, [{"login": "hexacore_ai"}])
    with TestClient(world.app) as c, c.websocket_connect("/chat_timers/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and "send" in hello["commands"]
        assert ws.receive_json()["type"] == "status"
        ws.send_json({"cmd": "add", "text": "From a bot", "every": 30, "rid": 1})
        r = next(m for m in iter(ws.receive_json, None) if m.get("type") == "result")
        assert r["ok"] and r["rid"] == 1 and r["timer"]["every"] == 30
        ws.send_json({"cmd": "send", "id": r["timer"]["id"], "rid": 2})
        r2 = next(m for m in iter(ws.receive_json, None) if m.get("type") == "result")
        assert r2["ok"] and r2["sent"] == ["hexacore_ai"] and sent.items[-1][0] == "From a bot"
        ws.send_json({"cmd": "update", "rid": 3, "every": 5})
        r3 = next(m for m in iter(ws.receive_json, None) if m.get("type") == "result")
        assert r3["ok"] is False and "needs `id`" in r3["error"]
        ws.send_json({"cmd": "explode", "rid": 4})
        r4 = next(m for m in iter(ws.receive_json, None) if m.get("type") == "result")
        assert r4["ok"] is False and "unknown cmd" in r4["error"]


def test_the_tab_and_its_dot(world, tm):
    c = TestClient(world.app)
    page = c.get("/chat_timers").text
    assert "/plugins/twitch_core/static/twitch_connect.js" in page and "TwitchConnect.mount" in page
    nav = [i for i in world.host.nav_items() if i["id"] == "chat_timers"]
    assert nav and nav[0]["href"] == "/chat_timers" and nav[0]["state"] == "running"


def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def test_the_tab_script_is_valid_javascript(tmp_path):
    html = (STATIC / "timers_panel.html").read_text(encoding="utf-8")
    js = tmp_path / "panel.js"
    js.write_text("\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)), encoding="utf-8")
    for f in (js, STATIC / "nav.js"):
        r = subprocess.run([_node(), "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
