"""The shared Twitch connection (twitch_core): channels, accounts, the supervisor, the hooks the modules listen on
and the calls they make - on its own, without the Twitch plugin.

Nothing here talks to Twitch: the connection functions and Helix are replaced, and the runner's own start-up is
switched off so a request opens no connection."""

import asyncio
import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("websockets")

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "catalog" / "twitch_core" / "static"

SCOPES = ["user:read:chat", "moderator:manage:shoutouts", "user:write:chat"]
PRIMARY = {"login": "hexacore_ai", "label": "primary"}
BETA = {"login": "hexacoreai", "label": "beta"}
CONFIGS = ("twitch.json", "twitch_core.json", "twitch_secrets.json", "twitch_queue.json")


@pytest.fixture
def world(real_world):
    real_world.installer.install("twitch_core")
    real_world.host.load_all()
    assert "twitch_core" in real_world.host.loaded, real_world.host.errors
    yield real_world
    # legacy configs of the Twitch plugin: left in the shared test config folder they would make a later test's
    # Hexcast (test_core_app's "fresh download") install Twitch on its own
    for name in CONFIGS:
        (real_world.config / name).unlink(missing_ok=True)


@pytest.fixture
def tc(world):
    return importlib.import_module("hexcast_plugins.twitch_core.core")


@pytest.fixture
def quiet(tc, monkeypatch):
    """The runner neither starts nor connects: its restarts (the Reconnect button) and its syncs (a change that only
    reconnects the channels it concerns) are counted instead."""
    calls = {"restart": 0, "sync": 0}

    async def restart():
        calls["restart"] += 1

    async def sync():
        calls["sync"] += 1

    async def ensure_started():
        pass

    monkeypatch.setattr(tc.RUNNER, "restart", restart)
    monkeypatch.setattr(tc.RUNNER, "sync", sync)
    monkeypatch.setattr(tc.RUNNER, "ensure_started", ensure_started)
    return calls


@pytest.fixture
def api(world, quiet):
    return TestClient(world.app)


def acct(tc, login, uid, scopes=SCOPES, token=None):
    return tc.Account({"access_token": token or f"tok-{login}", "refresh_token": f"ref-{login}",
                       "user_id": uid, "user_login": login, "scopes": list(scopes)})


def setup(tc, channels, accounts=()):
    """Watch these channels (a list of {login, label}), signed in as these accounts."""
    tc.CONFIG["channels"] = tc.normalise_channels(channels)
    tc.SECRETS.accounts = {a.user_id: a for a in accounts}
    tc.STATE.sync_channels()


def run(coro):
    return asyncio.run(coro)


class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body or {}

    def json(self):
        return self._body


# ---------------------------------------------------------------- channels

def test_a_config_with_only_the_old_channel_gets_a_list(tc):
    assert tc.normalise_channels(None, "#Hexacore_AI") == [{"login": "hexacore_ai", "label": "", "enabled": True}]
    assert tc.normalise_channels([], "") == [] and tc.normalise_channels("nonsense") == []


def test_channels_are_cleaned_and_the_first_is_the_primary(tc):
    rows = [{"login": " #Hexacoreai ", "label": "  beta  ", "enabled": False}, "HEXACORE_AI", {"login": "hexacoreai"},
            {"login": "no way"}, {"login": ""}, 7, {"login": "x" * 40}, {"login": "ok_name", "label": "L" * 60}]
    assert tc.normalise_channels(rows, "ignored_when_there_is_a_list") == [
        {"login": "hexacoreai", "label": "beta", "enabled": False},
        {"login": "hexacore_ai", "label": "", "enabled": True},
        {"login": "ok_name", "label": "L" * 24, "enabled": True}]
    many = [{"login": f"channel{i}"} for i in range(9)]
    assert len(tc.normalise_channels(many)) == tc.MAX_CHANNELS


def test_the_first_start_takes_the_channels_from_the_twitch_settings_and_keeps_them_apart(tc):
    tc.CONFIG_PATH.unlink(missing_ok=True)
    tc.LEGACY_CONFIG_PATH.write_text(json.dumps({"channels": [PRIMARY, BETA], "forward_url": "http://bot"}), encoding="utf-8")
    assert [r["login"] for r in tc.load_config()["channels"]] == ["hexacore_ai", "hexacoreai"]
    assert json.loads(tc.CONFIG_PATH.read_text(encoding="utf-8"))["channels"][1]["label"] == "beta"     # its own file now
    tc.LEGACY_CONFIG_PATH.write_text(json.dumps({"channel": "someone_else"}), encoding="utf-8")
    assert [r["login"] for r in tc.load_config()["channels"]] == ["hexacore_ai", "hexacoreai"]         # read only once
    tc.CONFIG_PATH.unlink()
    tc.LEGACY_CONFIG_PATH.write_text(json.dumps({"channel": "hexacore_ai"}), encoding="utf-8")         # a one-channel Hexcast
    assert tc.load_config()["channels"] == [{"login": "hexacore_ai", "label": "", "enabled": True}]
    tc.CONFIG_PATH.unlink()
    tc.LEGACY_CONFIG_PATH.unlink()
    assert tc.load_config() == {"channels": []} and not tc.CONFIG_PATH.exists()                       # a fresh one writes nothing


def test_the_connection_api_takes_channels_and_reconnects_only_when_they_change(api, tc, quiet):
    r = api.post("/twitch/api/connection", json={"channels": [{**PRIMARY, "login": "Hexacore_AI"}, BETA]})
    assert r.status_code == 200 and [c["login"] for c in r.json()["channels"]] == ["hexacore_ai", "hexacoreai"]
    assert quiet["sync"] == 1
    api.post("/twitch/api/connection", json={"channels": [PRIMARY, BETA]})               # the same list again
    assert quiet["sync"] == 1
    api.post("/twitch/api/connection", json={"channels": [PRIMARY, {**BETA, "label": "test"}]})   # a label counts
    assert quiet["sync"] == 2 and json.loads(tc.CONFIG_PATH.read_text())["channels"][1]["label"] == "test"
    for bad in ([{"login": "not a name"}], [{"login": "ab"}], "hexacoreai", [{"login": f"channel{i}"} for i in range(6)]):
        r = api.post("/twitch/api/connection", json={"channels": bad})
        assert r.status_code == 400 and r.json()["error"], bad
    assert quiet["sync"] == 2 and [c["label"] for c in tc.CONFIG["channels"]] == ["primary", "test"]


def test_only_the_reconnect_button_reconnects_everything(api, tc, quiet):
    api.post("/twitch/api/connection", json={"channels": [PRIMARY, BETA]})
    api.post("/twitch/auth/logout", json={"login": "nobody"})
    assert quiet["restart"] == 0 and quiet["sync"] == 2                      # a channel save and a sign-out: a sync each
    assert api.post("/twitch/api/reconnect").json() == {"ok": True}
    assert quiet["restart"] == 1 and quiet["sync"] == 2


# ---------------------------------------------------------------- accounts

def test_a_one_login_secrets_file_becomes_the_first_account(tc):
    tc.SECRETS_PATH.write_text(json.dumps({
        "client_id": "cid", "client_secret": "sec", "access_token": "a", "refresh_token": "r", "expires_at": 1,
        "user_id": "11", "user_login": "hexacore_ai", "scopes": ["user:read:chat"]}), encoding="utf-8")
    tc.SECRETS.load()
    (a,) = tc.SECRETS.authed()
    assert (a.user_id, a.user_login, a.access_token, a.scopes) == ("11", "hexacore_ai", "a", ["user:read:chat"])
    assert tc.SECRETS.client_id == "cid" and tc.SECRETS.client_secret == "sec"
    tc.SECRETS.save()
    saved = json.loads(tc.SECRETS_PATH.read_text(encoding="utf-8"))
    assert "access_token" not in saved and saved["accounts"]["11"]["user_login"] == "hexacore_ai"
    tc.SECRETS.load()                                                       # and it reads back the same
    assert [x.user_login for x in tc.SECRETS.authed()] == ["hexacore_ai"]


def test_the_twitch_plugins_secrets_file_is_read_as_it_is(tc):
    """Updating Twitch to 1.3 moves no file: the sign-ins in config/twitch_secrets.json keep working."""
    tc.SECRETS_PATH.write_text(json.dumps({"client_id": "cid", "client_secret": "sec", "accounts": {
        "11": {"access_token": "a", "refresh_token": "r", "user_id": "11", "user_login": "hexacore_ai", "scopes": SCOPES},
        "22": {"access_token": "b", "refresh_token": "s", "user_id": "22", "user_login": "hexacoreai", "scopes": SCOPES}}}),
        encoding="utf-8")
    tc.SECRETS.load()
    assert [a.user_login for a in tc.SECRETS.authed()] == ["hexacore_ai", "hexacoreai"]


def test_a_channel_uses_its_own_account_else_the_default_one(tc):
    a1, a2, bot = acct(tc, "hexacore_ai", "11"), acct(tc, "hexacoreai", "22"), acct(tc, "somebot", "33")
    setup(tc, [PRIMARY, BETA, {"login": "gamma_ch"}], [bot, a2, a1])
    assert tc.account_for("hexacore_ai") is a1 and tc.account_for("HexaCoreAI") is a2        # their own logins
    assert tc.account_for("gamma_ch") is a1 and tc.default_account() is a1                    # the primary's is the default
    setup(tc, [PRIMARY, BETA], [bot, a2])                                                     # primary not signed in
    assert tc.account_for("hexacore_ai") is bot and tc.account_for("hexacoreai") is a2        # first one signed in
    setup(tc, [PRIMARY, BETA], [bot])                                                         # one login, any channel
    assert tc.account_for("hexacore_ai") is bot and tc.account_for("hexacoreai") is bot
    setup(tc, [PRIMARY, BETA], [])
    assert tc.account_for("hexacoreai") is None and tc.default_account() is None


def test_a_hexcast_with_no_channel_set_watches_the_signed_in_users_own(tc):
    setup(tc, [], [acct(tc, "hexacore_ai", "11")])
    assert [r["login"] for r in tc.configured_channels()] == ["hexacore_ai"] and tc.primary_login() == "hexacore_ai"
    setup(tc, [], [])
    assert tc.configured_channels() == [] and tc.primary_login() == ""


def test_signing_out_one_account_leaves_the_others(api, tc, quiet):
    setup(tc, [PRIMARY, BETA], [acct(tc, "hexacore_ai", "11"), acct(tc, "hexacoreai", "22")])
    assert api.post("/twitch/auth/logout", json={"login": "HexaCoreAI"}).json() == {"ok": True}
    assert [a.user_login for a in tc.SECRETS.authed()] == ["hexacore_ai"] and quiet["sync"] == 1
    api.post("/twitch/auth/logout")                                                           # no body: everyone
    assert tc.SECRETS.authed() == [] and quiet["sync"] == 2


def test_a_second_sign_in_of_the_same_user_replaces_the_first(tc):
    setup(tc, [PRIMARY], [acct(tc, "hexacore_ai", "11", token="old")])
    kept = tc.SECRETS.adopt(acct(tc, "hexacore_ai", "11", token="new"))
    assert len(tc.SECRETS.accounts) == 1 and kept.access_token == "new"
    assert tc.SECRETS.adopt(acct(tc, "hexacoreai", "22")).user_login == "hexacoreai" and len(tc.SECRETS.accounts) == 2


def test_channels_that_share_a_login_refresh_its_token_once(tc, monkeypatch):
    a = acct(tc, "hexacore_ai", "11", token="old")
    setup(tc, [PRIMARY, BETA], [a])
    seen = {"refresh": 0, "requests": []}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, headers=None, params=None, json=None):
            token = headers["Authorization"].split()[-1]
            seen["requests"].append(token)
            await asyncio.sleep(0.01)                                       # the other channel's call is in flight too
            return Resp(401 if token == "old" else 200, {"data": []})

        async def post(self, url, data=None):
            seen["refresh"] += 1
            await asyncio.sleep(0.02)
            return Resp(200, {"access_token": "new", "refresh_token": "ref2", "expires_in": 3600})

    monkeypatch.setattr(tc.httpx, "AsyncClient", Client)

    async def both():
        return await asyncio.gather(tc.helix("GET", "/users", acct=a), tc.helix("GET", "/users", acct=a))

    r1, r2 = run(both())
    assert (r1.status_code, r2.status_code) == (200, 200)
    assert seen["refresh"] == 1 and a.access_token == "new" and a.refresh_token == "ref2"   # one refresh, not a race
    assert seen["requests"].count("new") == 2


# ---------------------------------------------------------------- what the modules hear

EV = {"chatter_user_id": "77", "chatter_user_login": "viewer", "chatter_user_name": "Viewer", "color": "#00ff00",
      "message_id": "m1", "message": {"text": "hey Kappa", "fragments": [
          {"type": "text", "text": "hey "}, {"type": "emote", "text": "Kappa", "emote": {"id": "25", "format": ["static"]}}]},
      "badges": [{"set_id": "moderator", "id": "1", "info": ""}, {"set_id": "subscriber", "id": "12", "info": "12"}]}
IRC = ("@badges=moderator/1;display-name=Viewer;id=abc;mod=1;user-id=77;emotes=25:4-8 "
       ":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #hexacoreai :hey Kappa")


def test_a_chat_line_looks_the_same_from_eventsub_and_from_anonymous_chat(tc):
    setup(tc, [PRIMARY, BETA])
    beta = tc.STATE.channel("hexacoreai")
    a, b = tc.chat_from_eventsub(EV, beta), tc.chat_from_irc(IRC, beta)
    for m in (a, b):
        assert (m["type"], m["channel"], m["channel_label"], m["text"]) == ("chat", "hexacoreai", "beta", "hey Kappa")
        assert m["user"] == {"id": "77", "login": "viewer", "name": "Viewer", "color": m["user"]["color"]}
        assert m["flags"]["mod"] is True and m["fragments"][-1]["t"] == "emote" and m["fragments"][-1]["name"] == "Kappa"
        assert {"set_id": "moderator", "id": "1"} in m["badges"]
    assert a["flags"]["sub"] is True and a["id"] == "m1" and b["id"] == "abc"
    assert tc.chat_from_irc(":tmi.twitch.tv 001 justinfan :Welcome", beta) is None


def test_listeners_hear_every_line_and_event_and_one_that_fails_stops_nobody(tc):
    setup(tc, [PRIMARY, BETA])
    beta = tc.STATE.channel("hexacoreai")
    heard = []

    async def broken(msg, ch):
        raise RuntimeError("boom")

    async def chat(msg, ch):
        heard.append(("chat", msg["text"], ch.login))

    async def event(kind, ev, msg_id, ch):
        heard.append((kind, msg_id, ch.login))

    stops = [tc.on_chat(broken), tc.on_chat(chat), tc.on_event(event)]
    try:
        async def go():
            await tc.handle_notification("channel.chat.message", EV, "n1", beta)
            await tc.handle_notification("channel.follow", {"user_name": "Viewer"}, "n2", beta)
            await tc.handle_notification("channel.chat.clear", {}, "n3", beta)
        run(go())
    finally:
        for stop in stops:
            stop()
    assert heard == [("chat", "hey Kappa", "hexacoreai"), ("channel.follow", "n2", "hexacoreai"),
                     ("channel.chat.clear", "n3", "hexacoreai")]
    assert "boom" in "\n".join(tc.STATE.log)                                       # logged, not raised
    assert tc.HOOKS.chat == [] and tc.HOOKS.event == []                            # and the hooks went with their stops


def test_the_stream_going_live_and_ending_is_known(tc, monkeypatch):
    setup(tc, [PRIMARY], [acct(tc, "hexacore_ai", "11")])
    ch = tc.STATE.primary()
    assert tc.is_live() is None and tc.is_live("nobody_watched") is None
    run(tc.handle_notification("stream.online", {}, "x1", ch))
    assert tc.is_live() is True and tc.is_live("hexacore_ai") is True and ch.live_since > 0
    run(tc.handle_notification("stream.offline", {}, "x2", ch))
    assert tc.is_live() is False

    stop = asyncio.Event()

    async def helix(method, path, *, acct, params=None, json_body=None, retry=True):
        stop.set()                                                                 # one round is enough
        return Resp(200, {"data": [{"started_at": "2026-10-24T18:00:00Z"}]})

    monkeypatch.setattr(tc, "helix", helix)
    monkeypatch.setattr(tc, "LIVE_CHECK_SECONDS", 0.01)
    ch.id = "111"
    run(tc.live_loop(ch, tc.account_for("hexacore_ai"), stop))
    assert ch.live is True and ch.live_since == 1792864800.0                       # what /streams said, when it started


# ---------------------------------------------------------------- what the modules call

def _helix(tc, monkeypatch, answer):
    calls = []

    async def helix(method, path, *, acct, params=None, json_body=None, retry=True):
        calls.append((method, path, acct.user_login, params, json_body))
        return answer(method, path, params) if callable(answer) else answer

    monkeypatch.setattr(tc, "helix", helix)
    return calls


def test_send_chat_posts_as_the_account_of_that_channel(tc, monkeypatch, api):
    calls = _helix(tc, monkeypatch, Resp(200, {"data": [{"message_id": "x", "is_sent": True}]}))
    setup(tc, [PRIMARY, BETA], [acct(tc, "hexacore_ai", "11"), acct(tc, "hexacoreai", "22")])
    tc.STATE.channel("hexacore_ai").id, tc.STATE.channel("hexacoreai").id = "111", "222"
    assert run(tc.send_chat("  Join   the discord: https://discord.gg/x  ")) == {"ok": True, "channel": "hexacore_ai", "account": "hexacore_ai"}
    r = api.post("/twitch/api/chat/send", json={"text": "beta hello", "channel": "HexaCoreAI"})
    assert r.status_code == 200 and r.json()["account"] == "hexacoreai"
    assert [(c[1], c[2], c[4]) for c in calls] == [
        ("/chat/messages", "hexacore_ai", {"broadcaster_id": "111", "sender_id": "11", "message": "Join the discord: https://discord.gg/x"}),
        ("/chat/messages", "hexacoreai", {"broadcaster_id": "222", "sender_id": "22", "message": "beta hello"})]
    assert len(run(tc.send_chat("x" * 900))["channel"]) and len(calls[-1][4]["message"]) == tc.CHAT_LIMIT


def test_send_chat_says_what_is_missing(tc, monkeypatch, api):
    _helix(tc, monkeypatch, Resp(200, {"data": [{"is_sent": False, "drop_reason": {"code": "msg_duplicate", "message": "duplicate message"}}]}))
    setup(tc, [PRIMARY], [])
    for text, channel, why in (("hi", "", "not signed in"), ("hi", "elsewhere", "not a channel Hexcast watches"), ("", "", "nothing to send")):
        r = api.post("/twitch/api/chat/send", json={"text": text, "channel": channel})
        assert r.status_code == 400 and why in r.json()["error"], why
    setup(tc, [PRIMARY], [acct(tc, "hexacore_ai", "11", scopes=["user:read:chat"])])
    assert "user:write:chat" in api.post("/twitch/api/chat/send", json={"text": "hi"}).json()["error"]
    setup(tc, [PRIMARY], [acct(tc, "hexacore_ai", "11")])
    tc.STATE.primary().id = "111"
    assert "duplicate message" in api.post("/twitch/api/chat/send", json={"text": "hi"}).json()["error"]
    setup(tc, [], [])
    assert "no Twitch channel set" in api.post("/twitch/api/chat/send", json={"text": "hi"}).json()["error"]


def test_who_is_in_chat_reads_every_page(tc, monkeypatch):
    pages = {"": ({"data": [{"user_id": "1", "user_login": "a", "user_name": "A"}], "pagination": {"cursor": "c2"}}),
             "c2": ({"data": [{"user_id": "2", "user_login": "b", "user_name": "B"}], "pagination": {}})}
    calls = _helix(tc, monkeypatch, lambda m, p, params: Resp(200, pages[params.get("after", "")]))
    setup(tc, [PRIMARY], [acct(tc, "hexacore_ai", "11", scopes=SCOPES + ["moderator:read:chatters"])])
    tc.STATE.primary().id = "111"
    assert [u["user_login"] for u in run(tc.chatters())] == ["a", "b"]
    assert calls[0][3] == {"broadcaster_id": "111", "moderator_id": "11", "first": 1000}
    setup(tc, [PRIMARY], [acct(tc, "hexacore_ai", "11")])                      # signed in before the scope existed
    with pytest.raises(tc.TwitchError, match="moderator:read:chatters"):
        run(tc.chatters())


def test_subscribers_need_the_channels_own_account_and_go_a_hundred_at_a_time(tc, monkeypatch):
    calls = _helix(tc, monkeypatch, lambda m, p, params: Resp(200, {"data": [
        {"user_id": v, "tier": "2000"} for k, v in params if k == "user_id" and int(v) % 50 == 0]}))
    scopes = SCOPES + ["channel:read:subscriptions"]
    setup(tc, [PRIMARY, BETA], [acct(tc, "hexacore_ai", "11", scopes=scopes)])
    tc.STATE.channel("hexacore_ai").id = "111"
    got = run(tc.subscribers([str(i) for i in range(1, 251)]))
    assert got == {"50": "2000", "100": "2000", "150": "2000", "200": "2000", "250": "2000"}
    assert [sum(1 for k, _ in c[3] if k == "user_id") for c in calls] == [100, 100, 50]
    with pytest.raises(tc.TwitchError, match="hexacoreai's own account"):           # beta has no login of its own
        run(tc.subscribers(["1"], "hexacoreai"))


def test_when_someone_followed(tc, monkeypatch):
    _helix(tc, monkeypatch, lambda m, p, params: Resp(200, {"data": [{"followed_at": "2026-01-01T00:00:00Z"}]}
                                                      if params["user_id"] == "7" else {"data": []}))
    setup(tc, [PRIMARY], [acct(tc, "hexacore_ai", "11", scopes=SCOPES + ["moderator:read:followers"])])
    tc.STATE.primary().id = "111"
    assert run(tc.followed_at("7")) == 1767225600.0 and run(tc.followed_at("8")) is None


# ---------------------------------------------------------------- the supervisor: the primary must always work

class Wire:
    """The connection functions replaced. A channel's 'connection' just stays up until it is torn down, and
    what happened is recorded: who connected as whom, and whose connection was cancelled."""

    def __init__(self, tc, monkeypatch):
        self.started, self.cancelled, self.validated, self.came_up = [], [], [], []
        self.hang = set()                  # channels whose lookup never answers
        self.fail = {}                     # channel -> how many more lookups raise (the network is down)
        self.validate_raises = set()       # accounts whose login check raises

        async def validate(a):
            self.validated.append(a.user_login)
            if a.user_login in self.validate_raises:
                raise RuntimeError("validate exploded")
            return True

        async def resolve(login, a):
            if login in self.hang:
                await asyncio.Event().wait()
            if self.fail.get(login):
                self.fail[login] -= 1
                raise ConnectionError("network down")
            return f"id-{login}"

        async def stays_up(ch, who):
            self.started.append((ch.login, who))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.append(ch.login)
                raise

        async def eventsub(ch, a, stop):
            await stays_up(ch, a.user_login)

        async def irc(ch, stop):
            await stays_up(ch, "anonymous")

        async def live(ch, a, stop):
            await asyncio.Event().wait()

        async def noop(*a, **k):
            pass

        async def came_up(ch, a):
            self.came_up.append((ch.login, a.user_login if a else "-"))

        monkeypatch.setattr(tc, "validate_token", validate)
        monkeypatch.setattr(tc, "resolve_channel_id", resolve)
        monkeypatch.setattr(tc, "eventsub_loop", eventsub)
        monkeypatch.setattr(tc, "irc_loop", irc)
        monkeypatch.setattr(tc, "live_loop", live)
        monkeypatch.setattr(tc, "broadcast_status", noop)
        monkeypatch.setattr(tc, "RETRY_MIN", 0.01)
        monkeypatch.setattr(tc, "RETRY_MAX", 0.02)
        self.stop_listening = tc.on_channel(came_up)


async def settle():
    await asyncio.sleep(0.15)


def watch(tc, channels):
    """Change the channel list the way saving it does."""
    tc.CONFIG["channels"] = tc.normalise_channels(channels)


def test_every_enabled_channel_runs_as_the_account_that_fits_it(tc, monkeypatch):
    setup(tc, [PRIMARY, BETA, {"login": "gamma_ch"}, {"login": "off_ch", "enabled": False}],
          [acct(tc, "hexacore_ai", "11"), acct(tc, "hexacoreai", "22")])
    w = Wire(tc, monkeypatch)

    async def go():
        await tc.RUNNER.restart()
        await settle()
        with_logins = sorted(w.started)
        await tc.RUNNER._stop()
        tc.SECRETS.accounts = {}                                                      # nobody signed in: anonymous chat for all
        w.started.clear()
        await tc.RUNNER.restart()
        await settle()
        anonymous = sorted(w.started)
        await tc.RUNNER._stop()
        return with_logins, anonymous

    with_logins, anonymous = run(go())
    w.stop_listening()
    assert with_logins == [("gamma_ch", "hexacore_ai"), ("hexacore_ai", "hexacore_ai"), ("hexacoreai", "hexacoreai")]
    assert tc.STATE.channel("off_ch").enabled is False and tc.STATE.channel("off_ch").source == "none"   # never ran
    assert anonymous == [("gamma_ch", "anonymous"), ("hexacore_ai", "anonymous"), ("hexacoreai", "anonymous")]
    # the modules heard each channel come up: as its account, then anonymously
    assert sorted(w.came_up)[:3] == [("gamma_ch", "-"), ("gamma_ch", "hexacore_ai"), ("hexacore_ai", "-")]


def test_a_channel_that_cannot_start_keeps_trying_and_the_others_run(tc, monkeypatch):
    setup(tc, [PRIMARY, BETA], [acct(tc, "hexacore_ai", "11"), acct(tc, "hexacoreai", "22")])
    w = Wire(tc, monkeypatch)
    w.fail["hexacore_ai"] = 3                                          # no network when the primary first tries

    async def go():
        await tc.RUNNER.restart()
        await settle()
        await settle()
        await tc.RUNNER._stop()

    run(go())
    w.stop_listening()
    assert w.started.count(("hexacore_ai", "hexacore_ai")) == 1 and w.started.count(("hexacoreai", "hexacoreai")) == 1
    log = "\n".join(tc.STATE.log)
    assert log.count("hexacore_ai could not start: network down") == 3 and "trying again" in log


def test_a_slow_or_broken_beta_does_not_hold_up_the_primary(tc, monkeypatch):
    setup(tc, [PRIMARY, BETA], [acct(tc, "hexacore_ai", "11"), acct(tc, "hexacoreai", "22")])
    w = Wire(tc, monkeypatch)
    w.hang.add("hexacoreai")                                           # beta's lookup never answers ...
    w.validate_raises.add("hexacoreai")                                # ... and checking its login raises

    async def go():
        await tc.RUNNER.restart()                                      # (returns at once, it only starts the tasks)
        await settle()
        up = list(w.started)
        beta_task = tc.RUNNER.tasks["hexacoreai"][1]
        await tc.RUNNER._stop()                                        # a hung channel is torn down cleanly too
        return up, beta_task

    up, beta_task = run(go())
    w.stop_listening()
    assert up == [("hexacore_ai", "hexacore_ai")]                      # the primary is up; beta is still waiting
    assert beta_task.done() and "could not check the login of hexacoreai" in "\n".join(tc.STATE.log)


def test_a_slow_listener_never_holds_a_connection_up(tc, monkeypatch):
    setup(tc, [PRIMARY], [acct(tc, "hexacore_ai", "11")])
    w = Wire(tc, monkeypatch)

    async def slow(ch, a):
        await asyncio.Event().wait()                                   # emotes from three sites that never answer

    stop = tc.on_channel(slow)

    async def go():
        await tc.RUNNER.restart()
        await settle()
        up = list(w.started)
        await tc.RUNNER._stop()
        return up, list(tc.RUNNER.side)

    up, left = run(go())
    stop()
    w.stop_listening()
    assert up == [("hexacore_ai", "hexacore_ai")] and left == []      # connected anyway; stopping ended the listener


def test_editing_beta_never_reconnects_the_primary(tc, monkeypatch):
    setup(tc, [PRIMARY, BETA], [acct(tc, "hexacore_ai", "11"), acct(tc, "hexacoreai", "22")])
    w = Wire(tc, monkeypatch)

    async def go():
        await tc.RUNNER.restart()
        await settle()
        primary_task = tc.RUNNER.tasks["hexacore_ai"][1]
        steps = {}

        async def change(name, channels):
            watch(tc, channels)
            await tc.RUNNER.sync()
            await settle()
            steps[name] = (list(w.started), list(w.cancelled))

        await change("label", [PRIMARY, {**BETA, "label": "test"}])
        await change("off", [PRIMARY, {**BETA, "label": "test", "enabled": False}])
        await change("on", [PRIMARY, {**BETA, "label": "test"}])
        await change("another", [PRIMARY, {**BETA, "label": "test"}, {"login": "gamma_ch"}])
        await change("beta first", [{**BETA, "label": "test"}, PRIMARY, {"login": "gamma_ch"}])      # make beta the primary
        await change("beta gone", [PRIMARY])
        same_task = tc.RUNNER.tasks["hexacore_ai"][1] is primary_task
        await tc.RUNNER._stop()
        return steps, same_task

    steps, same_task = run(go())
    w.stop_listening()
    primary = ("hexacore_ai", "hexacore_ai")
    assert same_task and all("hexacore_ai" not in cancelled for _, cancelled in steps.values())      # never torn down
    assert all(started.count(primary) == 1 for started, _ in steps.values())                          # never reconnected
    assert steps["label"][1] == [] and len(steps["label"][0]) == 2                                    # a label needs no reconnect ...
    assert steps["off"][1] == ["hexacoreai"]                                                          # ... switching beta off ends beta only
    assert steps["on"][0].count(("hexacoreai", "hexacoreai")) == 2                                    # ... on starts it again
    assert steps["another"][0][-1] == ("gamma_ch", "hexacore_ai") and steps["another"][1] == ["hexacoreai"]
    assert sorted(steps["beta gone"][1][-2:]) == ["gamma_ch", "hexacoreai"]                           # beta and gamma removed


def test_a_label_change_shows_in_the_tags_without_a_reconnect(tc, monkeypatch):
    setup(tc, [PRIMARY, BETA], [acct(tc, "hexacore_ai", "11")])
    w = Wire(tc, monkeypatch)

    async def go():
        await tc.RUNNER.restart()
        await settle()
        before = tc.chat_from_irc(IRC, tc.STATE.channel("hexacoreai"))["channel_label"]
        watch(tc, [PRIMARY, {**BETA, "label": "renamed"}])
        await tc.RUNNER.sync()
        after = tc.chat_from_irc(IRC, tc.STATE.channel("hexacoreai"))["channel_label"]
        await tc.RUNNER._stop()
        return before, after

    assert run(go()) == ("beta", "renamed")
    w.stop_listening()


def test_signing_in_or_out_reconnects_only_the_channels_that_run_as_that_account(tc, monkeypatch):
    a1 = acct(tc, "hexacore_ai", "11")
    setup(tc, [PRIMARY, BETA], [a1])                                   # both run as the primary's account
    w = Wire(tc, monkeypatch)

    async def go():
        await tc.RUNNER.restart()
        await settle()
        out = {"start": list(w.started)}
        tc.SECRETS.accounts["22"] = acct(tc, "hexacoreai", "22")       # sign in as beta's own account
        await tc.RUNNER.sync()
        await settle()
        out["beta signed in"] = (list(w.started), list(w.cancelled))
        w.cancelled.clear()
        tc.SECRETS.adopt(acct(tc, "hexacore_ai", "11", token="newer"))  # sign in again as the primary's account
        await tc.RUNNER.sync()
        await settle()
        out["primary again"] = (list(w.started), list(w.cancelled))
        w.cancelled.clear()
        tc.SECRETS.clear_tokens("hexacoreai")                          # sign beta's account out again
        await tc.RUNNER.sync()
        await settle()
        out["beta out"] = (list(w.started), list(w.cancelled))
        await tc.RUNNER._stop()
        return out

    out = run(go())
    w.stop_listening()
    assert out["start"] == [("hexacore_ai", "hexacore_ai"), ("hexacoreai", "hexacore_ai")]
    started, cancelled = out["beta signed in"]
    assert cancelled == ["hexacoreai"] and started[-1] == ("hexacoreai", "hexacoreai")              # only beta, now as itself
    started, cancelled = out["primary again"]
    assert cancelled == ["hexacore_ai"] and started[-1] == ("hexacore_ai", "hexacore_ai")           # beta runs as its own: untouched
    started, cancelled = out["beta out"]
    assert cancelled == ["hexacoreai"] and started[-1] == ("hexacoreai", "hexacore_ai")             # beta back on the default; the primary untouched


def test_reconnect_restarts_everything_and_stopping_leaves_nothing_running(tc, monkeypatch):
    setup(tc, [PRIMARY, BETA], [acct(tc, "hexacore_ai", "11"), acct(tc, "hexacoreai", "22")])
    w = Wire(tc, monkeypatch)

    async def go():
        await tc.RUNNER.restart()
        await settle()
        tasks = [t for _, t in tc.RUNNER.tasks.values()]
        await tc.RUNNER.restart()                                      # the Reconnect button
        await settle()
        cancelled_by_reconnect = sorted(w.cancelled)
        await tc.RUNNER._stop()
        return tasks, cancelled_by_reconnect, [t for _, t in tc.RUNNER.tasks.values()]

    tasks, cancelled, left = run(go())
    w.stop_listening()
    assert cancelled[:2] == ["hexacore_ai", "hexacoreai"] and all(t.done() for t in tasks)            # all of it, on purpose
    assert left == []
    assert not tc.STATE.channel("hexacore_ai").connected and tc.STATE.channel("hexacore_ai").source == "none"


# ---------------------------------------------------------------- the connection as the box shows it

def test_the_connection_lists_channels_accounts_and_what_each_sign_in_lacks(api, tc):
    tc.SECRETS.data.update({"client_id": "cid", "client_secret": "very-secret"})
    setup(tc, [PRIMARY, BETA, {"login": "gamma_ch", "enabled": False}],
          [acct(tc, "hexacore_ai", "11", scopes=tc.SCOPES), acct(tc, "hexacoreai", "22", scopes=["user:read:chat"])])
    p = tc.STATE.channel("hexacore_ai")
    p.id, p.source, p.connected, p.live = "111", "eventsub", True, True
    s = api.get("/twitch/api/connection").json()
    assert s["has_credentials"] is True and s["client_id"] == "cid" and s["default_account"] == "hexacore_ai"
    assert s["redirect_uri"] == "http://testserver/twitch/auth/callback"
    rows = {c["login"]: c for c in s["channels"]}
    assert list(rows) == ["hexacore_ai", "hexacoreai", "gamma_ch"] and rows["hexacore_ai"]["primary"] is True
    assert rows["hexacore_ai"]["live"] is True and rows["hexacoreai"]["live"] is None
    accounts = {a["login"]: a for a in s["accounts"]}
    assert accounts["hexacore_ai"]["missing"] == [] and "moderator:read:chatters" in accounts["hexacoreai"]["missing"]
    text = json.dumps(s)
    assert "very-secret" not in text and "tok-" not in text and "ref-" not in text           # never the secret or a token


def test_the_library_runs_on_its_own(world, tc, quiet):
    """Chat Timers or Giveaways without the Twitch plugin: the connection and its pages are there, the overlays not."""
    c = TestClient(world.app)
    assert set(world.host.loaded) == {"twitch_core"} and world.host.nav_items() == []        # no tab of its own
    assert c.get("/twitch/api/connection").status_code == 200
    assert c.get("/twitch/api/status").status_code == 404 and c.get("/twitch").status_code == 404
    assert c.get("/plugins/twitch_core/static/twitch_connect.js").status_code == 200


def test_a_sign_in_returns_to_the_page_that_started_it(api, tc):
    assert api.get("/twitch/auth/login", follow_redirects=False).status_code == 400            # no app yet
    tc.SECRETS.data.update({"client_id": "cid", "client_secret": "sec"})
    r = api.get("/twitch/auth/login?next=/giveaways", follow_redirects=False)
    assert r.status_code in (302, 307) and "moderator%3Aread%3Achatters" in r.headers["location"]
    state = r.headers["location"].split("state=")[1].split("&")[0]
    assert tc._oauth_states[state][1] == "/giveaways"
    for bad in ("//evil.example", "https://evil.example", "\\\\evil", ""):
        assert tc._back(bad) == "/"                                                         # not the Twitch tab: it is not installed here
    page = api.get("/twitch/auth/callback?code=x&state=unknown").text
    assert "expired" in page


def test_an_old_twitch_plugin_keeps_the_library_from_starting(real_world):
    """A Twitch plugin from before 1.3 has a connection of its own: both would sign in and connect."""
    old = real_world.plugins / "twitch"
    shutil.copytree(ROOT / "catalog" / "twitch", old)
    m = json.loads((old / "plugin.json").read_text(encoding="utf-8"))
    m.pop("requires")
    (old / "plugin.json").write_text(json.dumps(m), encoding="utf-8")
    real_world.installer.install("twitch_core")
    assert real_world.host.load("twitch_core") is False
    assert "update it first" in real_world.host.errors["twitch_core"]


# ---------------------------------------------------------------- the box (node)

def test_the_connection_box_is_valid_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "--check", str(STATIC / "twitch_connect.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
