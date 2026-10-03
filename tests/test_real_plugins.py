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
    assert {"twitch", "music", "avatar", "discord", "clips", "countdown", "games", "ticker", "ytdlp",
            "games_roulette", "games_craps", "games_russian", "games_trivia", "games_hexfall", "games_climb", "games_blackjack"} <= set(entries)
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
