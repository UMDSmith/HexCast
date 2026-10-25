"""Every plugin that ships: it installs, starts, answers, stops cleanly and comes back."""

import asyncio

import pytest
from fastapi.testclient import TestClient

# (plugin id, a route that must answer 200 once it runs, packages the plugin needs to import)
PLUGINS = [
    ("countdown", "/countdown/api/status", []),
    ("ticker", "/ticker/api/status", []),
    ("discord", "/discord/api/status", ["websockets"]),
    ("twitch", "/twitch/api/status", ["websockets"]),
    ("clips", "/clips/api/status", []),
    ("music", "/ytm/api/status", ["socketio", "aiohttp"]),
    ("avatar", "/avatar/api/status", []),
    ("chat_timers", "/chat_timers/api/status", ["websockets"]),
    ("giveaways", "/giveaways/api/status", ["websockets"]),
]


@pytest.mark.parametrize("pid,route,needs", PLUGINS, ids=[p[0] for p in PLUGINS])
def test_plugin_lifecycle(real_world, pid, route, needs):
    for mod in needs:
        pytest.importorskip(mod)
    real_world.installer.install(pid)
    real_world.host.load_all()
    assert pid in real_world.host.loaded, real_world.host.errors
    c = TestClient(real_world.app)
    assert c.get(route).status_code == 200
    nav = [i for i in real_world.host.nav_items() if i["id"] == pid]
    assert nav and nav[0]["state"] == "running" and nav[0]["href"]
    for _ in range(2):                                    # stop -> start -> stop: it must be repeatable
        stopped, clean = asyncio.run(real_world.host.unload(pid))
        assert pid in stopped and clean
        assert c.get(route).status_code == 404
        assert real_world.host.load(pid), real_world.host.errors
        assert c.get(route).status_code == 200
    asyncio.run(real_world.host.shutdown())


def test_music_and_clips_share_the_ytdlp_library(real_world):
    pytest.importorskip("socketio")
    real_world.installer.install("music")
    real_world.installer.install("clips")
    real_world.host.load_all()
    assert {"ytdlp", "music", "clips"} <= set(real_world.host.loaded)
    assert real_world.host.dependents("ytdlp") == ["music", "clips"] or set(real_world.host.dependents("ytdlp")) == {"music", "clips"}
    # music keeps working when clips is removed, because the helpers are not clips'
    asyncio.run(real_world.host.unload("clips"))
    c = TestClient(real_world.app)
    assert c.get("/ytm/api/login").json()["browser"] == ""
    assert c.get("/clips/api/status").status_code == 404


def test_the_twitch_modules_share_one_connection(real_world):
    """Twitch, Chat Timers and Giveaways each install on their own and bring the connection with them; with all
    three there is one connection, and stopping one module leaves the others (and the connection) running."""
    pytest.importorskip("websockets")
    real_world.installer.install("giveaways")
    real_world.host.load_all()
    assert set(real_world.host.loaded) == {"twitch_core", "giveaways"}                  # no Twitch tab came with it
    real_world.installer.install("chat_timers")
    real_world.installer.install("twitch")
    real_world.host.load_all()
    assert set(real_world.host.dependents("twitch_core")) == {"giveaways", "chat_timers", "twitch"}
    from hexcast_core import running_plugin
    core = running_plugin("twitch_core").core
    assert len(core.HOOKS.chat) == 2 and len(core.HOOKS.event) == 2                     # Twitch's and Timers' listeners
    asyncio.run(real_world.host.unload("chat_timers"))
    assert len(core.HOOKS.chat) == 1 and "twitch_core" in real_world.host.loaded       # Timers' listeners went with it
    c = TestClient(real_world.app)
    assert c.get("/twitch/api/connection").status_code == 200 and c.get("/twitch/api/status").status_code == 200
    stopped, clean = asyncio.run(real_world.host.unload("twitch_core"))                 # the library takes its users along
    assert set(stopped) == {"twitch_core", "giveaways", "twitch"} and clean
    for name in ("twitch.json", "twitch_core.json", "twitch_secrets.json", "twitch_queue.json", "chat_timers.json", "giveaways.json"):
        (real_world.config / name).unlink(missing_ok=True)


def test_twitch_shoutout_finds_clips_only_when_installed(real_world):
    from hexcast_core import running_plugin
    real_world.installer.install("twitch")
    real_world.host.load_all()
    assert running_plugin("clips") is None
    real_world.installer.install("clips")
    real_world.host.load("clips")
    assert callable(getattr(running_plugin("clips"), "play_shoutout", None))


def test_every_catalog_plugin_has_a_valid_manifest_and_help(real_world):
    entries = real_world.host.catalog.entries()
    assert real_world.host.catalog.errors == {}
    assert {"twitch", "twitch_core", "chat_timers", "giveaways", "music", "avatar", "discord", "clips", "countdown", "games", "ticker", "ytdlp",
            "games_roulette", "games_craps", "games_russian", "games_trivia", "games_hexfall", "games_climb", "games_blackjack", "games_derby"} <= set(entries)
    for pid, e in entries.items():
        m = e.manifest
        assert m.description and m.name and m.version, pid
        if m.help:
            assert (e.folder / m.help["file"]).is_file(), pid
        if m.nav:
            assert m.nav["href"].startswith("/") and m.nav.get("status_url"), pid
            if m.nav.get("status_js"):
                assert (e.folder / "static" / m.nav["status_js"]).is_file(), pid
        for dep in m.requires:
            assert dep in entries, (pid, dep)
        assert (m.parent is None) or m.parent in entries
