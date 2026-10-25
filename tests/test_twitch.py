"""Twitch: the chat and alert overlays on the shared connection - tagging, routing, the alert queue, shoutouts,
status and the config API as bots know them, the overlay looks. (The connection itself - channels, accounts, the
supervisor - is tested in test_twitch_core.py.)

Nothing here talks to Twitch: Helix is replaced, and the connection's own start-up is switched off so a request
to a page or an overlay socket opens no connection."""

import asyncio
import importlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("websockets")

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "catalog" / "twitch" / "static"

SCOPES = ["user:read:chat", "moderator:manage:shoutouts", "user:write:chat"]
PRIMARY = {"login": "hexacore_ai", "label": "primary"}
BETA = {"login": "hexacoreai", "label": "beta"}


@pytest.fixture
def world(real_world):
    real_world.installer.install("twitch")
    real_world.host.load_all()
    assert {"twitch", "twitch_core"} <= set(real_world.host.loaded), real_world.host.errors
    yield real_world
    # these are the module's legacy configs: left in the shared test config folder they would make a later
    # test's Hexcast (test_core_app's "fresh download") install Twitch on its own
    for name in ("twitch.json", "twitch_core.json", "twitch_secrets.json", "twitch_queue.json"):
        (real_world.config / name).unlink(missing_ok=True)


@pytest.fixture
def tw(world):
    return importlib.import_module("hexcast_plugins.twitch.twitch")


@pytest.fixture
def quiet(tw, monkeypatch):
    """Nothing starts or connects: the connection's restarts (the Reconnect button) and its syncs (a change that
    only reconnects the channels it concerns) are counted instead."""
    calls = {"restart": 0, "sync": 0}

    async def restart():
        calls["restart"] += 1

    async def sync():
        calls["sync"] += 1

    async def ensure_started():
        pass

    monkeypatch.setattr(tw.tc.RUNNER, "restart", restart)
    monkeypatch.setattr(tw.tc.RUNNER, "sync", sync)
    monkeypatch.setattr(tw.tc.RUNNER, "ensure_started", ensure_started)
    monkeypatch.setattr(tw.LOOPS, "ensure_started", ensure_started)
    return calls


@pytest.fixture
def api(world, quiet):
    return TestClient(world.app)


def acct(tw, login, uid, scopes=SCOPES, token=None):
    return tw.tc.Account({"access_token": token or f"tok-{login}", "refresh_token": f"ref-{login}",
                          "user_id": uid, "user_login": login, "scopes": list(scopes)})


def setup(tw, channels, accounts=()):
    """Watch these channels (a list of {login, label}), signed in as these accounts."""
    tw.tc.CONFIG["channels"] = tw.tc.normalise_channels(channels)
    tw.tc.SECRETS.accounts = {a.user_id: a for a in accounts}
    tw.tc.STATE.sync_channels()


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- config

def test_the_config_api_takes_channels_and_restarts_only_when_they_change(api, tw, quiet):
    r = api.post("/twitch/api/config", json={"channels": [{**PRIMARY, "login": "Hexacore_AI"}, BETA]})
    cfg = r.json()["config"]
    assert r.status_code == 200 and [c["login"] for c in cfg["channels"]] == ["hexacore_ai", "hexacoreai"]
    assert cfg["channel"] == "hexacore_ai" and quiet["sync"] == 1
    api.post("/twitch/api/config", json={"channels": [PRIMARY, BETA]})                 # the same list again
    api.post("/twitch/api/config", json={"overlay_channels": "all", "forward_url": "http://bot"})
    assert quiet["sync"] == 1 and api.get("/twitch/api/config").json()["overlay_channels"] == "all"
    api.post("/twitch/api/config", json={"channels": [PRIMARY, {**BETA, "label": "test"}]})   # a label counts
    api.post("/twitch/api/config", json={"channels": [PRIMARY, {**BETA, "label": "test", "enabled": False}]})
    assert quiet["sync"] == 3


def test_the_old_channel_setting_still_changes_the_primary(api, tw, quiet):
    api.post("/twitch/api/config", json={"channels": [PRIMARY, BETA]})
    cfg = api.post("/twitch/api/config", json={"channel": "NewPrimary"}).json()["config"]
    assert [(c["login"], c["label"]) for c in cfg["channels"]] == [("newprimary", "primary"), ("hexacoreai", "beta")]
    assert cfg["channel"] == "newprimary" and quiet["sync"] == 2
    cfg = api.post("/twitch/api/config", json={"channel": "hexacoreai"}).json()["config"]     # a channel already listed
    assert [c["login"] for c in cfg["channels"]] == ["hexacoreai"] and cfg["channel"] == "hexacoreai"
    cfg = api.post("/twitch/api/config", json={"channel": ""}).json()["config"]
    assert cfg["channels"] == [] and cfg["channel"] == ""


def test_a_channel_that_is_not_one_is_refused_and_nothing_changes(api, tw, quiet):
    api.post("/twitch/api/config", json={"channels": [PRIMARY]})
    before = api.get("/twitch/api/config").json()
    for bad in ([{"login": "not a name"}], [{"login": "ab"}], "hexacoreai", [{"login": f"channel{i}"} for i in range(6)]):
        r = api.post("/twitch/api/config", json={"channels": bad})
        assert r.status_code == 400 and r.json()["error"], bad
    assert api.post("/twitch/api/config", json={"channel": "bad name!"}).status_code == 400
    assert api.get("/twitch/api/config").json() == before and quiet["sync"] == 1


# ---------------------------------------------------------------- what a channel's lines look like

EV = {"chatter_user_login": "viewer", "chatter_user_name": "Viewer", "color": "#00ff00", "message_id": "m1",
      "message": {"text": "hey KEKW", "fragments": [{"type": "text", "text": "hey KEKW"}]},
      "badges": [{"set_id": "moderator", "id": "1"}]}
IRC = "@badges=moderator/1;display-name=Viewer;id=abc;mod=1 :viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #hexacoreai :hey KEKW"


def test_chat_lines_carry_their_channel_and_use_its_emotes(tw):
    setup(tw, [PRIMARY, BETA])
    primary, beta = tw.tc.STATE.channel("hexacore_ai"), tw.tc.STATE.channel("hexacoreai")
    tw.assets_for(beta).emotes = {"KEKW": "https://cdn.example/kekw.png"}         # only the beta channel has this emote
    tw.assets_for(beta).badges = {"moderator/1": {"url": "https://cdn.example/mod.png", "title": "Moderator"}}
    for make in (lambda c: tw.normalise_eventsub_chat(EV, c), lambda c: tw.normalise_irc_chat(IRC, c)):
        m = make(beta)
        assert (m["channel"], m["channel_label"]) == ("hexacoreai", "beta")
        assert m["fragments"][-1] == {"t": "emote", "url": "https://cdn.example/kekw.png", "name": "KEKW"}
        assert m["user"]["badges"] == [{"url": "https://cdn.example/mod.png", "title": "Moderator"}]
        p = make(primary)
        assert (p["channel"], p["channel_label"]) == ("hexacore_ai", "primary")
        assert p["fragments"] == [{"t": "text", "v": "hey KEKW"}] and p["user"]["badges"] == []


def test_a_channel_without_a_label_is_tagged_with_its_login(tw):
    setup(tw, [{"login": "hexacoreai"}])
    assert tw.normalise_irc_chat(IRC, tw.tc.STATE.primary())["channel_label"] == "hexacoreai"


def test_alerts_carry_their_channel_and_can_name_it(tw):
    setup(tw, [PRIMARY, BETA])
    tw.CONFIG["alerts"]["follow"]["body"] = "{user} followed {channel}"
    a = tw.build_alert("follow", user="Viewer", ch=tw.tc.STATE.channel("hexacoreai"))
    assert (a["channel"], a["channel_label"], a["body"]) == ("hexacoreai", "beta", "Viewer followed beta")
    assert tw.build_alert("follow", user="Viewer")["channel"] == ""              # no channel: still a working alert


def test_notifications_are_routed_to_the_channel_they_came_from(tw, monkeypatch):
    setup(tw, [PRIMARY, BETA])
    beta = tw.tc.STATE.channel("hexacoreai")
    alerts, chats, commands = [], [], []

    async def enqueue(alert, msg_id=None):
        alerts.append(alert)

    async def to_chat(payload):
        chats.append(payload)
        return 0, 0

    async def noop(*a, **k):
        pass

    async def command(login, text, is_bc, is_mod, ch):
        commands.append((login, text, ch.login))

    monkeypatch.setattr(tw.ALERT_QUEUE, "enqueue", enqueue)
    monkeypatch.setattr(tw.HUB, "to_chat", to_chat)
    monkeypatch.setattr(tw.HUB, "to_panel", noop)
    monkeypatch.setattr(tw, "forward_chat", noop)
    monkeypatch.setattr(tw, "handle_command", command)

    async def go():
        await tw.handle_notification("channel.chat.message", EV, "n1", beta)
        await tw.handle_notification("channel.chat.clear", {}, "n2", beta)
        await tw.handle_notification("channel.chat.message_delete", {"message_id": "m1"}, "n3", beta)
        await tw.handle_notification("channel.follow", {"user_name": "Viewer"}, "n4", beta)
        await tw.handle_notification("channel.raid", {"from_broadcaster_user_name": "Raider", "viewers": 9}, "n5", beta)

    run(go())
    assert [c["type"] for c in chats] == ["chat", "clear", "delete"] and {c["channel"] for c in chats} == {"hexacoreai"}
    assert commands == [("viewer", "hey KEKW", "hexacoreai")]                     # a command is typed in a channel
    assert [(a["kind"], a["channel"]) for a in alerts] == [("follow", "hexacoreai"), ("raid", "hexacoreai")]


# ---------------------------------------------------------------- which overlay gets what

def test_an_overlay_gets_the_channels_it_asked_for(tw):
    setup(tw, [PRIMARY, BETA])
    wants = tw.Hub.wants
    assert wants("", "hexacore_ai") and not wants("", "hexacoreai")                # the default: the primary only
    assert wants("primary", "hexacore_ai") and not wants("primary", "hexacoreai")
    assert wants("all", "hexacore_ai") and wants("all", "hexacoreai")
    assert wants("hexacoreai", "hexacoreai") and not wants("hexacoreai", "hexacore_ai")
    assert wants("hexacoreai", None) and wants("", None)                           # a settings update goes to everyone
    tw.CONFIG["overlay_channels"] = "all"                                          # the panel setting moves the default
    assert wants("", "hexacoreai") and wants("", "hexacore_ai") and not wants("hexacore_ai", "hexacoreai")
    tw.CONFIG["overlay_channels"] = "primary"
    setup(tw, [], [])
    assert not wants("", "hexacoreai")                                             # nothing is primary: nothing shown by default


def test_the_hub_sends_each_line_only_where_it_is_wanted(tw):
    setup(tw, [PRIMARY, BETA])
    default, everything, only_beta = FakeWS(), FakeWS(), FakeWS()
    tw.HUB.chat.update({default: "", everything: "all", only_beta: "hexacoreai"})

    async def go():
        out = [await tw.HUB.to_chat({"type": "chat", "channel": "hexacore_ai", "text": "p"}),
               await tw.HUB.to_chat({"type": "chat", "channel": "hexacoreai", "text": "b"}),
               await tw.HUB.to_chat({"type": "clear", "channel": "hexacoreai"}),
               await tw.HUB.broadcast_config()]
        return out

    out = run(go())
    texts = lambda ws: [m.get("text") or m["type"] for m in ws.sent]                # noqa: E731
    assert texts(default) == ["p", "config"] and texts(everything) == ["p", "b", "clear", "config"]
    assert texts(only_beta) == ["b", "clear", "config"]
    assert out[0] == (3, 2) and out[1] == (3, 2) and out[2] == (3, 2)               # (connected, sent to)


def test_a_dead_overlay_socket_is_dropped(tw):
    class Dead:
        async def send_text(self, text):
            raise RuntimeError("gone")

    setup(tw, [PRIMARY])
    dead, live = Dead(), FakeWS()
    tw.HUB.chat.update({dead: "all", live: "all"})
    assert run(tw.HUB.to_chat({"type": "chat", "channel": "hexacore_ai"})) == (1, 1) and list(tw.HUB.chat) == [live]


# ---------------------------------------------------------------- the alert queue

def _play_alert(tw, channel, overlays):
    """Run the queue loop over one alert from `channel` with these overlay sockets connected; the alert's
    state (still playing?) half a second later, and whether the queue was done by `done_within` seconds."""
    setup(tw, [PRIMARY, BETA])
    tw.HUB.events.update(overlays)
    alert = tw.build_alert("follow", user="Viewer", ch=tw.tc.STATE.channel(channel))

    async def go():
        await tw.ALERT_QUEUE.enqueue(alert)
        stop = asyncio.Event()
        task = asyncio.create_task(tw.queue_loop(stop))
        await asyncio.sleep(0.9)                                                    # (the loop polls every 0.5 s)
        still_playing = tw.ALERT_QUEUE.playing is not None
        tw.ALERT_QUEUE.skip()                                                       # a show that waits for its ack ends here
        await asyncio.sleep(0.1)
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return still_playing

    return run(go())


def test_the_queue_does_not_wait_for_an_ack_no_overlay_can_give(tw):
    only_primary = FakeWS()
    assert _play_alert(tw, "hexacoreai", {only_primary: ""}) is False               # a beta alert, only a primary overlay up
    assert only_primary.sent == [] or all(m["type"] != "event" for m in only_primary.sent)


def test_the_queue_waits_for_the_overlay_that_shows_the_alert(tw):
    shows_it = FakeWS()
    assert _play_alert(tw, "hexacoreai", {shows_it: "hexacoreai"}) is True          # the matching overlay acks it
    assert [m["channel"] for m in shows_it.sent if m["type"] == "event"] == ["hexacoreai"]


def test_the_queue_waits_as_before_when_no_overlay_is_connected(tw):
    assert _play_alert(tw, "hexacore_ai", {}) is True


# ---------------------------------------------------------------- shoutouts

class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body or {}

    def json(self):
        return self._body


def _helix_recorder(tw, monkeypatch):
    calls = []

    async def helix(method, path, *, acct, params=None, json_body=None, retry=True):
        calls.append((method, path, acct.user_login, params, json_body))
        if path == "/users":
            return Resp(200, {"data": [{"id": "900", "display_name": "SomeStreamer"}]})
        return Resp(204 if path == "/chat/shoutouts" else 200)

    monkeypatch.setattr(tw.tc, "helix", helix)
    tw.CONFIG["shoutout"]["clip"] = False
    return calls


def test_a_shoutout_is_made_in_the_channel_it_was_typed_in_by_that_channels_account(tw, monkeypatch):
    calls = _helix_recorder(tw, monkeypatch)
    setup(tw, [PRIMARY, BETA, {"login": "gamma_ch"}], [acct(tw, "hexacore_ai", "11"), acct(tw, "hexacoreai", "22")])
    for login, cid in (("hexacore_ai", "111"), ("hexacoreai", "222"), ("gamma_ch", "333")):
        tw.tc.STATE.channel(login).id = cid

    run(tw._do_shoutout("somestreamer", tw.tc.STATE.channel("hexacoreai")))
    assert [(m, p, who) for m, p, who, *_ in calls] == [("GET", "/users", "hexacoreai"), ("POST", "/chat/shoutouts", "hexacoreai"),
                                                        ("POST", "/chat/messages", "hexacoreai")]
    assert calls[1][3] == {"from_broadcaster_id": "222", "to_broadcaster_id": "900", "moderator_id": "22"}
    assert calls[2][4]["broadcaster_id"] == "222" and calls[2][4]["sender_id"] == "22"
    assert "SomeStreamer" in calls[2][4]["message"] and "twitch.tv/somestreamer" in calls[2][4]["message"]

    calls.clear()                                                                   # a channel with no login of its own
    run(tw._do_shoutout("somestreamer", tw.tc.STATE.channel("gamma_ch")))             # is shouted out as the default account
    assert {who for _, _, who, *_ in calls} == {"hexacore_ai"}
    assert calls[1][3]["from_broadcaster_id"] == "333" and calls[1][3]["moderator_id"] == "11"
    assert calls[2][4]["broadcaster_id"] == "333" and calls[2][4]["sender_id"] == "11"


def test_a_shoutout_says_when_the_account_lacks_a_scope(tw, monkeypatch):
    calls = _helix_recorder(tw, monkeypatch)
    setup(tw, [PRIMARY, BETA], [acct(tw, "hexacore_ai", "11", scopes=["user:read:chat"]), acct(tw, "hexacoreai", "22")])
    tw.tc.STATE.channel("hexacore_ai").id = "111"
    run(tw._do_shoutout("somestreamer", tw.tc.STATE.channel("hexacore_ai")))
    assert [p for _, p, *_ in calls] == ["/users"]                                  # neither the banner nor the line
    log = "\n".join(tw.tc.STATE.log)
    assert "[primary]" in log and "moderator:manage:shoutouts" in log and "user:write:chat" in log


def test_a_command_is_handled_for_the_channel_it_was_typed_in(tw, monkeypatch):
    setup(tw, [PRIMARY, BETA])
    seen = []

    async def shoutout(target, ch):
        seen.append((target, ch.login))

    monkeypatch.setattr(tw, "_do_shoutout", shoutout)

    async def go():
        await tw.handle_command("mod", "!so @SomeStreamer", False, True, tw.tc.STATE.channel("hexacoreai"))
        await tw.handle_command("viewer", "!so other_one", False, False, tw.tc.STATE.channel("hexacoreai"))   # not a mod
        await tw.handle_command("boss", "!so third_one", True, False, tw.tc.STATE.channel("hexacore_ai"))
        await asyncio.sleep(0)

    run(go())
    assert seen == [("somestreamer", "hexacoreai"), ("third_one", "hexacore_ai")]


def test_channels_already_up_get_their_emotes_when_twitch_starts_after_the_connection(tw, monkeypatch):
    """Twitch updated (or enabled) on its own: the shared connection kept running, so the channels came up before
    Twitch listened - their emotes and badges load when it starts."""
    setup(tw, [PRIMARY, BETA], [acct(tw, "hexacore_ai", "11")])
    p, b = tw.tc.STATE.channel("hexacore_ai"), tw.tc.STATE.channel("hexacoreai")
    p.connected, p.source, p.id = True, "eventsub", "111"
    b.connected, b.source = True, "irc"
    loaded = []

    async def load(self, channel_id, acct=None, name=""):
        loaded.append((channel_id, acct.user_login if acct else None))
        self.emotes = {"KEKW": "x"}

    async def nothing():
        pass

    monkeypatch.setattr(tw.Assets, "load", load)
    monkeypatch.setattr(tw.tc.RUNNER, "ensure_started", nothing)

    async def go():
        await tw.LOOPS.ensure_started()
        await asyncio.sleep(0.05)
        await tw.LOOPS.ensure_started()                                             # already running: nothing again
        await asyncio.sleep(0.05)
        await tw.LOOPS.shutdown()

    run(go())
    assert sorted(loaded, key=str) == [("", None), ("111", "hexacore_ai")]


# ---------------------------------------------------------------- status and the test endpoints

def test_the_status_keeps_its_top_level_keys_and_lists_every_channel(api, tw):
    setup(tw, [PRIMARY, BETA, {"login": "gamma_ch", "enabled": False}], [acct(tw, "hexacore_ai", "11"), acct(tw, "hexacoreai", "22")])
    p, b = tw.tc.STATE.channel("hexacore_ai"), tw.tc.STATE.channel("hexacoreai")
    p.id, p.source, p.connected, p.subs_ok = "111", "eventsub", True, ["channel.follow"]
    b.source, b.connected, b.last_error = "irc", False, "boom"
    s = api.get("/twitch/api/status").json()
    for key in ("source", "connected", "channel_id", "channel_login", "authed", "has_credentials", "bot_login", "scopes",
                "subs_ok", "subs_failed", "last_error", "log"):
        assert key in s, key
    assert (s["source"], s["connected"], s["channel_id"], s["channel_login"], s["subs_ok"]) == \
           ("eventsub", True, "111", "hexacore_ai", ["channel.follow"])              # the primary, as ever
    assert s["authed"] is True and s["bot_login"] == "hexacore_ai" and s["overlay_channels"] == "primary"
    rows = {c["login"]: c for c in s["channels"]}
    assert list(rows) == ["hexacore_ai", "hexacoreai", "gamma_ch"] and rows["hexacore_ai"]["primary"] is True
    assert rows["hexacoreai"]["last_error"] == "boom" and rows["hexacoreai"]["account"] == "hexacoreai"
    assert rows["hexacoreai"]["own_account"] is True and rows["gamma_ch"]["enabled"] is False
    assert rows["gamma_ch"]["own_account"] is False and rows["gamma_ch"]["account"] == "hexacore_ai"
    assert [a["login"] for a in s["accounts"]] == ["hexacore_ai", "hexacoreai"]
    assert "token" not in json.dumps(s)                                             # never the tokens


def test_the_status_before_anything_has_started_still_lists_the_configured_channels(api, tw):
    tw.tc.CONFIG["channels"] = tw.tc.normalise_channels([PRIMARY, BETA])
    s = api.get("/twitch/api/status").json()
    assert [c["login"] for c in s["channels"]] == ["hexacore_ai", "hexacoreai"] and s["connected"] is False
    assert s["channel_login"] == "hexacore_ai" and s["authed"] is False and s["accounts"] == []


def test_test_chat_and_alerts_can_go_to_any_watched_channel(api, tw, monkeypatch):
    setup(tw, [PRIMARY, BETA])
    overlay, beta_overlay, panel = FakeWS(), FakeWS(), FakeWS()
    tw.HUB.chat.update({overlay: "all", beta_overlay: "hexacoreai"})
    tw.HUB.panel.add(panel)
    got, shouted = [], []

    async def enqueue(alert, msg_id=None):
        got.append(alert)

    async def shoutout(target, ch):
        shouted.append((target, ch.login))

    monkeypatch.setattr(tw.ALERT_QUEUE, "enqueue", enqueue)
    monkeypatch.setattr(tw, "_do_shoutout", shoutout)
    assert api.post("/twitch/api/test/chat", json={"text": "to the primary"}).status_code == 200
    assert api.post("/twitch/api/test/chat", json={"text": "!so friend", "channel": "HexaCoreAI"}).status_code == 200
    assert [(m["text"], m["channel"]) for m in overlay.sent] == [("to the primary", "hexacore_ai"), ("!so friend", "hexacoreai")]
    assert [m["text"] for m in beta_overlay.sent] == ["!so friend"]
    assert [m["message"]["channel_label"] for m in panel.sent if m["type"] == "chat_preview"] == ["primary", "beta"]
    assert shouted == [("friend", "hexacoreai")]                                    # the test counts as the broadcaster there
    assert api.post("/twitch/api/test/event", json={"kind": "follow", "channel": "hexacoreai"}).status_code == 200
    assert api.post("/twitch/api/test/event", json={"kind": "raid"}).status_code == 200
    assert [(a["kind"], a["channel"]) for a in got] == [("follow", "hexacoreai"), ("raid", "hexacore_ai")]
    for path in ("chat", "event"):
        r = api.post(f"/twitch/api/test/{path}", json={"channel": "nobody_here"})
        assert r.status_code == 400 and "nobody_here" in r.json()["error"]


def test_tests_work_before_any_channel_is_set_up(api, tw):
    setup(tw, [], [])
    overlay = FakeWS()
    tw.HUB.chat[overlay] = "all"
    assert api.post("/twitch/api/test/chat", json={"text": "hi"}).status_code == 200
    assert overlay.sent[0]["channel"] == "hexacast" or overlay.sent[0]["channel"] == "hexcast"


def test_an_overlay_socket_gets_only_the_channel_in_its_url(world, quiet, tw):
    setup(tw, [PRIMARY, BETA])
    with TestClient(world.app) as c:
        with c.websocket_connect("/twitch/ws/chat?channel=HexaCoreAI") as beta, c.websocket_connect("/twitch/ws/chat") as default:
            assert beta.receive_json()["type"] == "config" and default.receive_json()["type"] == "config"
            assert list(tw.HUB.chat.values()) == ["hexacoreai", ""]                  # the page's ?channel=, lowercased
            c.post("/twitch/api/test/chat", json={"text": "for the primary"})
            c.post("/twitch/api/test/chat", json={"text": "for beta", "channel": "hexacoreai"})
            assert beta.receive_json()["text"] == "for beta"                         # the primary's line never arrived
            assert default.receive_json()["text"] == "for the primary"
        assert tw.HUB.chat == {}                                                     # both are forgotten when they close
        with c.websocket_connect("/twitch/ws/events?channel=all"):
            assert list(tw.HUB.events.values()) == ["all"]


# ---------------------------------------------------------------- the browser scripts (node)

def _node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


def _node_json(script):
    r = subprocess.run([_node(), "-e", script], capture_output=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("page", ["twitch_panel.html", "twitch_chat.html", "twitch_events.html"])
def test_the_page_scripts_are_valid_javascript(page, tmp_path):
    html = (STATIC / page).read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert scripts, page
    js = tmp_path / (page + ".js")
    js.write_text("\n".join(scripts), encoding="utf-8")
    r = subprocess.run([_node(), "--check", str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_an_overlay_page_passes_its_channel_on_to_the_socket():
    out = _node_json("""
      const fs = require('fs'), vm = require('vm');
      const run = (search) => {
        const urls = [];
        class WS { constructor(u) { urls.push(u); } }
        const sb = { location: { protocol: 'http:', host: 'hexcast:4747', search }, WebSocket: WS, URLSearchParams,
                     document: {}, setTimeout() {} };
        vm.createContext(sb);
        vm.runInContext(fs.readFileSync(%s, 'utf8'), sb);
        sb.connect('/twitch/ws/chat', () => {});
        sb.connect('/twitch/ws/events', () => {});
        return urls;
      };
      console.log(JSON.stringify({none: run(''), one: run('?x=1&channel=hexacoreai'), all: run('?channel=all'), odd: run('?channel=a%%20b&y')}));
    """ % json.dumps(str(STATIC / "twitch_boot.js")))
    assert out["none"] == ["ws://hexcast:4747/twitch/ws/chat", "ws://hexcast:4747/twitch/ws/events"]
    assert out["one"][0] == "ws://hexcast:4747/twitch/ws/chat?channel=hexacoreai"
    assert out["all"][1] == "ws://hexcast:4747/twitch/ws/events?channel=all"
    assert out["odd"][0] == "ws://hexcast:4747/twitch/ws/chat?channel=a%20b"


def test_the_top_bar_dot_reports_several_channels():
    out = _node_json("""
      const fs = require('fs'), vm = require('vm');
      const sb = { window: {} };
      vm.createContext(sb);
      vm.runInContext(fs.readFileSync(%s, 'utf8'), sb);
      const dot = sb.window.HexbarStatus.twitch;
      const ch = (login, enabled, connected) => ({ login, enabled, connected });
      console.log(JSON.stringify({
        one: dot({ connected: true, source: 'eventsub', channels: [ch('a', true, true)] }),
        old: dot({ connected: true, source: 'irc' }),
        all: dot({ connected: true, channels: [ch('a', true, true), ch('b', true, true)] }),
        some: dot({ connected: true, channels: [ch('a', true, true), ch('b', true, false), ch('c', false, false)] }),
        off: dot({ connected: false, channels: [ch('a', true, false), ch('b', true, false)] }),
        none: dot(null) }));
    """ % json.dumps(str(STATIC / "nav.js")))
    assert out["one"]["on"] is True and "chat and events" in out["one"]["title"]
    assert out["old"]["on"] is True and "chat only" in out["old"]["title"]                  # a status without channels: as ever
    assert out["all"]["on"] is True and out["all"]["warn"] is False and "2 of 2 channels connected" in out["all"]["title"]
    assert out["some"]["on"] is False and out["some"]["warn"] is True and "1 of 2" in out["some"]["title"]
    assert out["off"]["on"] is False and out["off"]["warn"] is True and out["none"]["on"] is False


# ---------------------------------------------------------------- how the overlays look: fonts, pictures, styles

def test_a_config_from_before_the_new_look_settings_keeps_its_look_and_gains_the_new_ones(tw):
    tw.CONFIG_PATH.write_text(json.dumps({"chat": {"font_family": "Anton", "bubble_opacity": 0.5},
                                          "events": {"font_size": 50},
                                          "alerts": {"follow": {"title": "Hi", "duration": 3}}}), encoding="utf-8")
    cfg = tw.load_config()
    assert cfg["chat"]["font_family"] == "Anton" and cfg["chat"]["bubble_opacity"] == 0.5     # what was saved wins
    assert cfg["events"]["font_size"] == 50 and cfg["alerts"]["follow"]["title"] == "Hi"
    # the new settings come in at values that draw what the overlays always drew
    chat, events, follow = cfg["chat"], cfg["events"], cfg["alerts"]["follow"]
    assert (chat["bg_fit"], chat["bg_image_opacity"], chat["bg_image_dim"], chat["bg_shadow"]) == ("cover", 1, 0, 0)
    assert chat["name_font_family"] == "" and chat["mark_roles"] is False and chat["fade_edge"] == 0
    assert chat["text_transform"] == "none" and chat["letter_spacing"] == 0 and chat["custom_css"] == ""
    assert events["accent_bar"] == "auto" and events["title_transform"] == "uppercase" and events["shadow"] is True
    assert (events["pad_x"], events["pad_y"], events["outline_width"]) == (34, 22, 2)
    assert (follow["accent_color"], follow["animation"], follow["image"], follow["image_pos"]) == ("", "", "", "top")
    assert follow["duration"] == 3 and follow["bg_image_url"] == ""


def test_a_config_from_before_the_username_had_its_own_size_keeps_its_look(tw):
    # the name was a percentage of the message size and shared its letter spacing; an alert's viewer message
    # used the label's size
    tw.CONFIG_PATH.write_text(json.dumps({"chat": {"font_size": 20, "name_scale": 150, "letter_spacing": 1.5},
                                          "events": {"sub_font_size": 30}}), encoding="utf-8")
    cfg = tw.load_config()
    chat, events = cfg["chat"], cfg["events"]
    assert (chat["name_size"], chat["name_letter_spacing"], chat["letter_spacing"]) == (30, 1.5, 1.5)
    assert "name_scale" not in chat and events["note_size"] == 30 and events["note_font_family"] == ""
    # a config without the old settings gets the defaults, and one saved since keeps what it says
    tw.CONFIG_PATH.write_text(json.dumps({"chat": {"font_size": 20}}), encoding="utf-8")
    assert tw.load_config()["chat"]["name_size"] == 20
    tw.CONFIG_PATH.write_text(json.dumps({"chat": {"font_size": 20, "name_size": 44, "name_letter_spacing": 0},
                                          "events": {"note_size": 18}}), encoding="utf-8")
    cfg = tw.load_config()
    assert (cfg["chat"]["name_size"], cfg["chat"]["name_letter_spacing"], cfg["events"]["note_size"]) == (44, 0, 18)
    tw.CONFIG_PATH.write_text("{}", encoding="utf-8")
    cfg = tw.load_config()
    assert cfg["chat"]["name_size"] == cfg["chat"]["font_size"] and cfg["events"]["note_size"] == cfg["events"]["sub_font_size"]


def test_the_chat_line_splits_the_username_from_the_message(api):
    page = api.get("/twitch/chat").text
    for bit in ('class="who"', 'class="text"', "--name-size", "--name-ls", ".msg.stacked .who"):
        assert bit in page, bit
    assert "--name-scale" not in page
    alerts = api.get("/twitch/events").text
    assert "--note-size" in alerts and "--note-font" in alerts


def test_the_overlays_share_their_styles_and_helpers_from_the_plugin(api):
    css = api.get("/twitch/overlay.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    for name in ("bgs-slice", "bgs-image::before", "anim-bounce", ".leaving.out-zoom"):
        assert name in css.text, name
    assert "no-store" in css.headers["cache-control"]
    for page in ("chat", "events"):
        assert "/twitch/overlay.css" in api.get(f"/twitch/{page}").text, page            # both pages link it
    assert "function bgVars" in api.get("/twitch/boot.js").text


def test_custom_css_is_text_and_has_a_limit(api, tw):
    ok = api.post("/twitch/api/config", json={"chat": {"custom_css": ".msg{color:red}"}})
    assert ok.status_code == 200 and tw.CONFIG["chat"]["custom_css"] == ".msg{color:red}"
    for scope in ("chat", "events"):
        too_long = api.post("/twitch/api/config", json={scope: {"custom_css": "a" * (tw.MAX_CUSTOM_CSS + 1)}})
        assert too_long.status_code == 400 and "limited" in too_long.json()["error"]
        assert api.post("/twitch/api/config", json={scope: {"custom_css": ["not", "text"]}}).status_code == 400
    assert tw.CONFIG["chat"]["custom_css"] == ".msg{color:red}"                          # a refused update changes nothing
    assert api.post("/twitch/api/config", json={"events": {"custom_css": "a" * tw.MAX_CUSTOM_CSS}}).status_code == 200


def test_an_uploaded_font_is_listed_by_the_name_it_is_picked_by_and_can_be_deleted(api, tw, monkeypatch, tmp_path):
    monkeypatch.setattr(tw, "OVERLAY_FONT_DIR", tmp_path / "fonts")
    assert api.get("/twitch/api/fonts").json() == {"fonts": []}                         # no folder yet is no fonts
    up = api.post("/twitch/api/fonts", files={"file": ("My_Cool-Font.woff2", b"wOF2" + b"\0" * 64, "font/woff2")})
    assert up.status_code == 200
    body = up.json()
    assert body["name"] == "My Cool Font" and body["file"] == "My_Cool-Font.woff2"
    assert body["fonts"] == [{"name": "My Cool Font", "file": "My_Cool-Font.woff2", "format": "woff2",
                              "url": "/media/overlays/fonts/My_Cool-Font.woff2"}]
    assert (tmp_path / "fonts" / "My_Cool-Font.woff2").is_file()
    assert api.get("/twitch/api/fonts").json()["fonts"][0]["name"] == "My Cool Font"
    pictures = api.get("/twitch/api/backgrounds").json()["backgrounds"]
    assert all(not b["name"].endswith(".woff2") for b in pictures)                       # the picture library never lists fonts
    gone = api.post("/twitch/api/fonts/delete", json={"file": "../../My_Cool-Font.woff2"})   # only the file name counts
    assert gone.status_code == 200 and gone.json()["fonts"] == []
    assert not (tmp_path / "fonts" / "My_Cool-Font.woff2").exists()
    assert api.post("/twitch/api/fonts/delete", json={}).status_code == 400


def test_a_font_upload_must_be_a_real_font_of_a_known_kind_and_not_too_big(api, tw, monkeypatch, tmp_path):
    monkeypatch.setattr(tw, "OVERLAY_FONT_DIR", tmp_path / "fonts")
    wrong_kind = api.post("/twitch/api/fonts", files={"file": ("logo.png", b"\x89PNG....", "image/png")})
    assert wrong_kind.status_code == 400 and "ttf, otf, woff or woff2" in wrong_kind.json()["error"]
    not_a_font = api.post("/twitch/api/fonts", files={"file": ("fake.ttf", b"this is just text", "font/ttf")})
    assert not_a_font.status_code == 400 and "does not look like a font" in not_a_font.json()["error"]
    monkeypatch.setattr(tw, "MAX_FONT_BYTES", 100)
    big = api.post("/twitch/api/fonts", files={"file": ("big.otf", b"OTTO" + b"\0" * 200, "font/otf")})
    assert big.status_code == 400 and "too big" in big.json()["error"]
    assert not (tmp_path / "fonts").exists() or not list((tmp_path / "fonts").iterdir())    # nothing was kept
    odd = api.post("/twitch/api/fonts", files={"file": ("..\\..\\Evil Name!.TTF", b"\x00\x01\x00\x00" + b"\0" * 20, "font/ttf")})
    assert odd.status_code == 200 and odd.json()["file"] == "Evil_Name.ttf"                  # a flat, safe name
    assert [p.name for p in (tmp_path / "fonts").iterdir()] == ["Evil_Name.ttf"]


def test_small_bits_gifts_and_raids_can_be_ignored(tw, monkeypatch):
    for kind in ("cheer", "subgift", "raid"):
        monkeypatch.setitem(tw.CONFIG["alerts"][kind], "min_amount", 5)
        assert tw.build_alert(kind, user="v", amount="4") is None, kind
        assert tw.build_alert(kind, user="v", amount="5")["kind"] == kind, kind
    assert tw.build_alert("follow", user="v")["kind"] == "follow"                         # nothing else has an amount
    monkeypatch.setitem(tw.CONFIG["alerts"]["raid"], "min_amount", 1)
    assert tw.build_alert("raid", user="v", amount="1")["kind"] == "raid"                 # the default ignores nothing


def test_every_overlay_setting_can_be_edited_in_the_panel(tw):
    """A setting the overlay reads but the panel has no field for cannot be changed except by editing the JSON, so
    a new key must come with its field (or be listed here as deliberately not one)."""
    panel = (STATIC / "twitch_panel.html").read_text(encoding="utf-8")
    fields = set(re.findall(r"\['([a-z_0-9]+)',", panel))
    placement = {"box_enabled", "box_x", "box_y", "box_w", "box_h", "bg_full", "bg_box_x", "bg_box_y", "bg_box_w", "bg_box_h"}
    placement.add("custom_css")                      # edited in the Custom CSS tab, which is built by its own helper
    for scope in ("chat", "events"):
        missing = set(tw.DEFAULT_CONFIG[scope]) - fields - placement
        assert not missing, f"{scope} settings with no field in the panel: {sorted(missing)}"
    alert_keys = set(tw.DEFAULT_CONFIG["alerts"]["raid"]) - {"on"}                        # the switch is the row's own
    assert not alert_keys - fields, sorted(alert_keys - fields)


def test_the_overlay_helpers_clean_fonts_and_build_the_background_variables():
    out = _node_json("""
      const fs = require('fs'), vm = require('vm');
      const sb = { location: { protocol: 'http:', host: 'h', search: '' }, URLSearchParams, document: {}, window: {}, setTimeout() {} };
      vm.createContext(sb);
      vm.runInContext(fs.readFileSync(%s, 'utf8'), sb);
      const set = {};
      sb.bgVars({ setProperty: (k, v) => { set[k] = v; } },
        { bubble_color: '#112233', bubble_opacity: 0.5, bg_fit: 'tile', bg_position: 'top', bg_image_url: '/media/overlays/a"b.png',
          bg_shadow: 20, bg_image_dim: 0.3, bg_image_opacity: 0.8 });
      const plain = {};
      sb.bgVars({ setProperty: (k, v) => { plain[k] = v; } }, { bubble_color: '#000', bubble_opacity: 1, bg_fit: 'nonsense', bg_position: 'sideways' });
      console.log(JSON.stringify({
        font: sb.cleanFont('  My "Font";{x}  Two '), blank: sb.fontVar(''), quoted: sb.fontVar('Pacifico'),
        set, plain,
        styles: [sb.bgStyleFor('slice', ''), sb.bgStyleFor('slice', '/x.png'), sb.bgStyleFor('', 'x'), sb.bgStyleFor('image', '')],
        shadow: sb.shadowCss({ shadow_x: 1, shadow_y: 2, shadow_blur: 3, shadow_color: '#ff0000', shadow_opacity: 0.5 }),
        cls: [sb.bgClass('none'), sb.bgClass('glass'), sb.bgClass('')],
        face: sb.uploadedFaceCss({ name: 'My Font', url: '/media/overlays/fonts/My_Font.woff2', format: 'woff2' }) }));
    """ % json.dumps(str(STATIC / "twitch_boot.js")))
    assert out["font"] == "My Fontx Two" and out["blank"] == "inherit" and out["quoted"] == '"Pacifico"'
    s = out["set"]
    assert s["--bg-color"] == "rgba(17,34,51,0.5)" and s["--bg-size"] == "auto" and s["--bg-repeat"] == "repeat"
    assert s["--bg-pos"] == "top" and s["--bg-dim"] == 0.3 and s["--bg-img-opacity"] == 0.8
    assert s["--bg-image"] == 'url("/media/overlays/ab.png")'                              # a quote cannot end the url
    assert s["--bg-filter"] == "drop-shadow(0 10px 20px rgba(0,0,0,.55))"
    p = out["plain"]
    assert p["--bg-size"] == "cover" and p["--bg-pos"] == "center" and p["--bg-filter"] == "none" and p["--bg-image"] == "none"
    assert out["styles"] == ["solid", "slice", "solid", "image"]                           # an empty frame is drawn solid; no style is solid
    assert out["shadow"] == "1px 2px 3px rgba(255,0,0,0.5)"
    assert out["cls"] == ["", "bgs bgs-glass", ""]
    assert out["face"] == '@font-face{font-family:"My Font";src:url("/media/overlays/fonts/My_Font.woff2") format("woff2");font-weight:100 900;font-display:swap}'
