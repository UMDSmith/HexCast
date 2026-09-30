"""The plugin host: install, start, stop and remove plugins in a running app."""

import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient

from conftest import write_plugin
from hexcast_core.installer import InstallError


def test_fresh_world_has_no_plugins(world):
    assert world.host.scan() == {}
    assert world.host.nav_items() == []
    world.host.load_all()
    assert world.host.loaded == {}


def test_install_starts_and_serves_routes(world):
    write_plugin(world.catalog, "alpha")
    assert world.installer.install("alpha") == ["alpha"]
    assert (world.plugins / "alpha" / "plugin.json").is_file()
    assert world.host.load("alpha")
    c = TestClient(world.app)
    assert c.get("/alpha/api/status").json() == {"ok": True, "plugin": "alpha"}
    assert [i["id"] for i in world.host.nav_items()] == ["alpha"]


def test_install_record_and_no_pycache_copied(world):
    d = write_plugin(world.catalog, "alpha")
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "x.pyc").write_bytes(b"x")
    world.installer.install("alpha")
    assert not (world.plugins / "alpha" / "__pycache__").exists()
    meta = json.loads((world.plugins / "alpha" / ".hexcast-plugin.json").read_text())
    assert meta["id"] == "alpha" and meta["source"] == "bundled" and meta["content_hash"]


def test_removing_a_plugin_removes_its_routes(world):
    write_plugin(world.catalog, "alpha")
    world.installer.install("alpha")
    world.host.load("alpha")
    c = TestClient(world.app)
    assert c.get("/alpha/api/status").status_code == 200
    stopped, clean = asyncio.run(world.host.unload("alpha"))
    assert stopped == ["alpha"] and clean
    assert c.get("/alpha/api/status").status_code == 404
    assert "hexcast_plugins.alpha.plugin" not in sys.modules
    # and it can come back
    assert world.host.load("alpha")
    assert c.get("/alpha/api/status").status_code == 200


def test_teardown_hook_runs(world):
    write_plugin(world.catalog, "alpha")
    world.installer.install("alpha")
    world.host.load("alpha")
    mod = world.host.module("alpha")
    asyncio.run(world.host.unload("alpha"))
    assert mod.STOPPED == ["alpha"]


def test_requires_installs_dependencies_first(world):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "addon", requires=["base"])
    assert world.installer.install("addon") == ["base", "addon"]
    world.host.load_all()
    assert set(world.host.loaded) == {"base", "addon"}


def test_addon_does_not_start_without_parent(world):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "addon", requires=["base"])
    world.installer.install("addon")
    world.host.settings.set_disabled("base", True)
    world.host.load_all()
    assert world.host.loaded == {}
    assert "base" in world.host.errors["addon"]


def test_unload_stops_dependents_first(world):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "addon", requires=["base"])
    world.installer.install("addon")
    world.host.load_all()
    stopped, _ = asyncio.run(world.host.unload("base"))
    assert stopped == ["addon", "base"]
    assert world.host.loaded == {}


def test_broken_plugin_is_reported_not_fatal(world):
    write_plugin(world.catalog, "good")
    write_plugin(world.catalog, "bad", setup_body="def setup(ctx):\n    raise RuntimeError('boom')\n")
    world.installer.install("good")
    world.installer.install("bad")
    world.host.load_all()
    assert "good" in world.host.loaded and "bad" not in world.host.loaded
    assert "boom" in world.host.errors["bad"]
    assert world.host.state("bad") == "error"


def test_failed_setup_leaves_no_routes_behind(world):
    body = """
from fastapi import APIRouter
r = APIRouter()
@r.get('/half')
async def half(): return {}
def setup(ctx):
    ctx.include_router(r)
    raise RuntimeError('after routes')
"""
    write_plugin(world.catalog, "half", setup_body=body)
    world.installer.install("half")
    assert not world.host.load("half")
    assert TestClient(world.app).get("/half").status_code == 404


def test_async_setup_is_refused(world):
    write_plugin(world.catalog, "asy", setup_body="async def setup(ctx):\n    pass\n")
    world.installer.install("asy")
    assert not world.host.load("asy")
    assert "plain function" in world.host.errors["asy"]


def test_missing_setup_is_reported(world):
    write_plugin(world.catalog, "nos", setup_body="x = 1\n")
    world.installer.install("nos")
    assert not world.host.load("nos")
    assert "setup" in world.host.errors["nos"]


def test_static_folder_is_served_per_plugin(world):
    write_plugin(world.catalog, "alpha", files={"static/hello.txt": "hi there"})
    world.installer.install("alpha")
    world.host.load("alpha")
    r = TestClient(world.app).get("/plugins/alpha/static/hello.txt")
    assert r.status_code == 200 and r.text == "hi there"
    asyncio.run(world.host.unload("alpha"))
    assert TestClient(world.app).get("/plugins/alpha/static/hello.txt").status_code == 404


def test_disable_persists_and_blocks_start(world):
    write_plugin(world.catalog, "alpha")
    world.installer.install("alpha")
    world.host.settings.set_disabled("alpha", True)
    world.host.load_all()
    assert world.host.state("alpha") == "disabled" and not world.host.nav_items()
    # a fresh host reads the same file
    from hexcast_core import PluginHost
    again = PluginHost(world.app, root=world.plugins.parent, plugins_dir=world.plugins,
                       catalog_dir=world.catalog, config_dir=world.config, media_dir=world.media)
    assert again.settings.is_disabled("alpha")


def test_uninstall_removes_files_and_keeps_settings(world):
    write_plugin(world.catalog, "alpha")
    world.installer.install("alpha")
    (world.config / "alpha.json").write_text("{}")
    world.installer.uninstall("alpha")
    assert not (world.plugins / "alpha").exists()
    assert (world.config / "alpha.json").exists()
    with pytest.raises(InstallError):
        world.installer.uninstall("alpha")


def test_update_replaces_files_and_detects_changes(world):
    d = write_plugin(world.catalog, "alpha", version="1.0.0")
    world.installer.install("alpha")
    before = world.host.catalog.get("alpha").content_hash
    assert json.loads((world.plugins / "alpha" / ".hexcast-plugin.json").read_text())["content_hash"] == before
    write_plugin(world.catalog, "alpha", version="1.1.0", files={"extra.txt": "new"})
    after = world.host.catalog.get("alpha").content_hash
    assert after != before
    world.installer.update("alpha")
    assert (world.plugins / "alpha" / "extra.txt").read_text() == "new"
    assert world.host.scan()["alpha"].version == "1.1.0"


def test_unknown_plugin_and_missing_dependency(world):
    with pytest.raises(InstallError, match="not in the catalog"):
        world.installer.install("nope")
    write_plugin(world.catalog, "addon", requires=["ghost"])
    with pytest.raises(InstallError, match="ghost"):
        world.installer.install("addon")
    assert not (world.plugins / "addon").exists()


def test_circular_requirements_are_refused(world):
    write_plugin(world.catalog, "aa", requires=["bb"])
    write_plugin(world.catalog, "bb", requires=["aa"])
    with pytest.raises(InstallError, match="circular"):
        world.installer.install("aa")


def test_manually_placed_plugin_is_picked_up(world):
    write_plugin(world.plugins, "hand")
    assert "hand" in world.host.scan()
    world.host.load_all()
    assert "hand" in world.host.loaded


def test_broken_manifest_shows_as_error(world):
    d = world.plugins / "broken"
    d.mkdir()
    (d / "plugin.json").write_text("{not json")
    inst = world.host.scan()["broken"]
    assert inst.manifest is None and "JSON" in inst.error
    world.host.load_all()
    assert world.host.state("broken", inst) == "error"


def test_nav_only_lists_top_level_plugins(world):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "kid", parent="base", nav=False)
    write_plugin(world.catalog, "lib", nav=False)
    for p in ("base", "kid", "lib"):
        world.installer.install(p)
    world.host.load_all()
    assert [i["id"] for i in world.host.nav_items()] == ["base"]


def test_missing_requirements_mark_needs_deps(world):
    write_plugin(world.catalog, "alpha", requirements="definitely-not-a-real-package-xyz>=1.0\n")
    # copy without pip (there is no network in tests)
    world.installer.copy_in(world.host.catalog.get("alpha"))
    assert not world.host.load("alpha")
    assert world.host.state("alpha") == "needs_deps"
    assert [i["state"] for i in world.host.nav_items()] == ["needs_deps"]


def test_satisfied_requirements_skip_pip(world):
    write_plugin(world.catalog, "alpha", requirements="fastapi>=0.1\nhttpx>=0.1  # comment\n")
    log = []
    world.installer.install("alpha", log.append)
    assert "Python packages already in place" in log
    assert world.host.load("alpha")


def test_module_state_is_fresh_after_reload(world):
    write_plugin(world.catalog, "alpha", setup_body="COUNT = [0]\ndef setup(ctx):\n    COUNT[0] += 1\n")
    world.installer.install("alpha")
    world.host.load("alpha")
    assert world.host.module("alpha").COUNT == [1]
    asyncio.run(world.host.unload("alpha"))
    world.host.load("alpha")
    assert world.host.module("alpha").COUNT == [1]


def test_open_websockets_are_closed_when_a_plugin_stops(world):
    body = """
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
r = APIRouter()

@r.websocket('/wsdemo/live')
async def live(ws: WebSocket):
    await ws.accept()
    await ws.send_text('hello')
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass

def setup(ctx):
    ctx.include_router(r)
"""
    write_plugin(world.catalog, "wsdemo", setup_body=body)
    world.installer.install("wsdemo")
    world.host.load("wsdemo")
    from starlette.websockets import WebSocketDisconnect as Gone
    c = TestClient(world.app)
    with c.websocket_connect("/wsdemo/live") as ws:
        assert ws.receive_text() == "hello"
        # stop the plugin from another thread of control (TestClient runs the app in a portal)
        stopped, _ = c.portal.call(world.host.unload, "wsdemo") if hasattr(c, "portal") and c.portal else asyncio.run(world.host.unload("wsdemo"))
        assert stopped == ["wsdemo"]
        try:
            ws.receive_text()
            closed = False
        except Gone as exc:
            closed = exc.code == 1012
        assert closed


def test_updating_an_addon_leaves_its_running_parent_alone(world):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "kid", parent="base", nav=False, version="1.0.0")
    world.installer.install("kid")
    world.host.load_all()
    meta = world.plugins / "base" / ".hexcast-plugin.json"
    before = meta.read_text()
    write_plugin(world.catalog, "kid", parent="base", nav=False, version="1.0.1")
    assert world.installer.update("kid") == ["kid"]                    # not ["base", "kid"]
    assert meta.read_text() == before
    assert "base" in world.host.loaded


def test_updating_installs_a_new_requirement_first(world):
    write_plugin(world.catalog, "alpha", version="1.0.0")
    write_plugin(world.catalog, "newdep")
    world.installer.install("alpha")
    write_plugin(world.catalog, "alpha", version="1.1.0", requires=["newdep"])
    assert world.installer.update("alpha") == ["newdep", "alpha"]
    assert (world.plugins / "newdep").exists()


def test_corrupt_settings_file_is_kept_aside_and_not_migrated_over(world):
    path = world.config / "plugins.json"
    path.write_text('{"disabled": ["ticker"], "migrated": true, "catalogs": [],}')       # a trailing comma
    from hexcast_core.host import Settings
    s = Settings(path)
    assert s.data["disabled"] == [] and s.data["migrated"] is True       # defaults, but never "upgrade" on top of it
    assert s.problem and "plugins.json.bad" in s.problem
    assert (world.config / "plugins.json.bad").read_text().startswith('{"disabled": ["ticker"]')   # the user's text survives
    s.set_disabled("x", True)                                           # and the file can be written afresh
    assert Settings(path).is_disabled("x") and Settings(path).problem is None


def test_settings_accept_a_bom_and_keep_unknown_keys(world):
    path = world.config / "plugins.json"
    path.write_bytes(b'\xef\xbb\xbf' + json.dumps({"disabled": ["a"], "migrated": True, "catalogs": ["https://x/y.json"],
                                                  "note": {"keep": "me"}}).encode())
    from hexcast_core.host import Settings
    s = Settings(path)
    assert s.problem is None and s.is_disabled("a") and s.catalogs == ["https://x/y.json"] and s.data["migrated"]
    s.set_disabled("b", True)
    assert json.loads(path.read_text())["note"] == {"keep": "me"}


def test_catalog_hash_changes_with_content_but_not_with_pycache(world):
    d = write_plugin(world.catalog, "alpha")
    h1 = world.host.catalog.get("alpha").content_hash
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"1")
    assert world.host.catalog.get("alpha").content_hash == h1
    (d / "plugin.py").write_text((d / "plugin.py").read_text() + "\n# edit\n")
    assert world.host.catalog.get("alpha").content_hash != h1
