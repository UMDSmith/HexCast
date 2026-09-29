"""Regression tests for the plugin system's failure modes: hostile or odd input, half-finished
operations, races, and files that are not what they should be. Each test pins one bug that a
review found - a plugin must never end up half-installed, unremovable, or take the rest down."""

import asyncio
import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

import hexcast_core.installer as installer_mod
from conftest import ROOT, SESSION_CONFIG, write_plugin
from hexcast_core import PluginService, build_router
from hexcast_core.installer import InstallError, Installer, safe_extract
from hexcast_core.manifest import ManifestError, parse_manifest, valid_id
from hexcast_core.staticfiles import RevalidatingStaticFiles

BASE = {"id": "alpha", "name": "Alpha", "version": "1.0.0"}


# ---- helpers ---------------------------------------------------------------------------------------

def make_service(world):
    static = world.plugins.parent / "static"
    static.mkdir(exist_ok=True)
    (static / "plugins.html").write_text("<html>store</html>")
    (static / "help.html").write_text("<html><!--HELP_TOC--><!--HELP_SECTIONS--></html>")
    service = PluginService(world.host, world.installer, static)
    world.app.include_router(build_router(service))
    world.service = service
    return service


@pytest.fixture
def client(world):
    make_service(world)
    with TestClient(world.app) as c:
        yield c


def wait_job(c, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/plugins/jobs/{job_id}").json()
        if d["state"] != "running":
            return d
        time.sleep(0.03)
    raise AssertionError("job did not finish")


def install(c, pid):
    r = c.post(f"/api/plugins/{pid}/install")
    assert r.status_code == 200, r.text
    return wait_job(c, r.json()["job"])


def card(c, pid):
    return next((p for p in c.get("/api/plugins?parent=*").json()["plugins"] if p["id"] == pid), None)


@pytest.fixture(autouse=True)
def quick_and_offline(monkeypatch):
    monkeypatch.setattr(installer_mod, "MOVE_RETRY_SECONDS", 0.0)      # do not wait between rename retries
    monkeypatch.setenv("PIP_NO_INDEX", "1")                            # 'offline': pip fails fast, nothing is downloaded


BAD_PACKAGE = "definitely-not-a-real-package-xyz>=1\n"


# ---- archives can never write outside their folder --------------------------------------------------

@pytest.mark.parametrize("wrapped", [True, False])
@pytest.mark.parametrize("evil", ["/{outside}/pwned.txt", "\\{outside}\\pwned.txt", "../pwned.txt", "sub/../../pwned.txt",
                                  "C:/Users/x/pwned.txt", "sub/C:/pwned.txt", "a//b.txt", "./x.txt", "file.txt:stream"])
def test_zip_members_can_never_land_outside_the_destination(tmp_path, wrapped, evil):
    outside = tmp_path / "outside"
    outside.mkdir()
    member = evil.format(outside=str(outside).replace("\\", "/").lstrip("/"))
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(("top/" if wrapped else "") + "plugin.json", "{}")
        zf.writestr(("top/" if wrapped else "") + member, "x")
    dest = tmp_path / "stage" / "evil"
    with pytest.raises(InstallError):
        safe_extract(z, dest)
    assert not list(tmp_path.rglob("pwned.txt"))
    assert not dest.exists() or not any(dest.rglob("*.txt"))                # nothing half-written either


def test_a_normal_archive_still_extracts_with_and_without_a_wrapper_folder(tmp_path):
    for wrapper in ("", "myplugin/"):
        z = tmp_path / f"ok{len(wrapper)}.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(wrapper + "plugin.json", "{}")
            zf.writestr(wrapper + "static/a/b.js", "1")
        dest = tmp_path / f"out{len(wrapper)}"
        safe_extract(z, dest)
        assert (dest / "plugin.json").is_file() and (dest / "static" / "a" / "b.js").read_text() == "1"


# ---- one odd plugin.json never takes the rest down ---------------------------------------------------

@pytest.mark.parametrize("extra", [
    {"nav": {"label": "x", "href": "/x", "order": "first"}},
    {"nav": {"label": "x", "href": "/x", "order": [1]}},
    {"nav": {"label": "x", "href": "/x", "order": True}},
    {"help": {"file": "help.html", "toc": 5}},
    {"help": {"file": "help.html", "toc": True}},
    {"nav": {"label": "x", "href": "/\\evil.example"}},
    {"nav": {"label": "x", "href": "/\t/evil.example"}},
    {"nav": {"label": "x", "href": "/x y"}},
    {"requires": "beta"},
    {"order": 1.5},
    {"legacy_config": [1]},
], ids=lambda e: json.dumps(e)[:40])
def test_odd_manifest_values_are_a_manifest_error(extra):
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, **extra})


@pytest.mark.parametrize("pid", ["con", "prn", "aux", "nul", "com1", "lpt9"])
def test_windows_device_names_are_not_plugin_ids(pid):
    assert not valid_id(pid)
    with pytest.raises(ManifestError):
        parse_manifest({**BASE, "id": pid})


def test_a_bom_in_plugin_json_is_fine(world):
    d = write_plugin(world.plugins, "alpha")
    (d / "plugin.json").write_bytes(b"\xef\xbb\xbf" + (d / "plugin.json").read_bytes())
    assert world.host.scan()["alpha"].manifest is not None


@pytest.mark.parametrize("extra", [{"nav": {"label": "x", "href": "/x", "order": "first"}},
                                   {"help": {"file": "help.html", "toc": 5}}], ids=["nav.order", "help.toc"])
def test_a_broken_plugin_is_an_error_card_not_an_outage(world, client, extra):
    write_plugin(world.plugins, "good")
    write_plugin(world.plugins, "victim", extra=extra)
    inst = world.host.scan()
    assert inst["victim"].manifest is None and inst["victim"].error
    world.host.load_all()
    assert "good" in world.host.loaded and "victim" in world.host.errors
    assert client.get("/api/plugins").status_code == 200
    assert client.get("/api/plugins/nav").status_code == 200
    assert client.get("/help").status_code == 200


def test_a_broken_bundled_plugin_does_not_hide_the_others(world):
    write_plugin(world.catalog, "good")
    write_plugin(world.catalog, "victim", extra={"help": {"file": "help.html", "toc": 5}})
    assert set(world.host.catalog.entries()) == {"good"} and "victim" in world.host.catalog.errors


# ---- requires cycles ----------------------------------------------------------------------------------

def test_a_requires_cycle_does_not_stop_unrelated_plugins(world, client):
    write_plugin(world.plugins, "xxx")
    write_plugin(world.plugins, "aaa", requires=["xxx", "bbb"])
    write_plugin(world.plugins, "bbb", requires=["aaa"])
    write_plugin(world.plugins, "zzz")
    world.host.load_all()
    assert {"xxx", "zzz"} <= set(world.host.loaded)
    assert world.host.state("aaa") == "error" and "circular" in world.host.errors["aaa"].lower()
    assert "aaa" in world.host.dependents("xxx")                        # (used to recurse for ever)
    assert client.get("/api/plugins").status_code == 200                # the store still opens


def test_a_plugin_requiring_itself_through_a_chain_is_reported(world):
    write_plugin(world.plugins, "aa", requires=["bb"])
    write_plugin(world.plugins, "bb", requires=["cc"])
    write_plugin(world.plugins, "cc", requires=["aa"])
    world.host.load_all()
    assert world.host.loaded == {}
    assert all("circular" in world.host.errors[p].lower() for p in ("aa", "bb", "cc"))


# ---- a setup() that fails half-way leaves nothing behind --------------------------------------------------

def test_failed_setup_runs_the_cleanup_it_registered(world):
    import builtins
    write_plugin(world.plugins, "leaky", setup_body='''
import builtins
def setup(ctx):
    builtins.__hexcast_leak__ = getattr(builtins, "__hexcast_leak__", 0) + 1
    ctx.on_shutdown(lambda: setattr(builtins, "__hexcast_leak__", builtins.__hexcast_leak__ - 1))
    raise RuntimeError("boom")
''')
    builtins.__hexcast_leak__ = 0
    try:
        for _ in range(3):                               # every Install / Enable / Repair retries a broken plugin
            assert not world.host.load("leaky")
        assert builtins.__hexcast_leak__ == 0            # each try undid what it had set up
        assert "boom" in world.host.errors["leaky"]
    finally:
        del builtins.__hexcast_leak__


def test_failed_setup_also_runs_async_cleanup_and_survives_a_failing_hook(world):
    write_plugin(world.plugins, "asyncleak", setup_body='''
import builtins
async def undo():
    builtins.__hexcast_async_undone__ = True
def bad():
    raise ValueError("cleanup itself failed")
def setup(ctx):
    ctx.on_shutdown(undo)
    ctx.on_shutdown(bad)
    raise RuntimeError("boom")
''')
    import builtins
    builtins.__hexcast_async_undone__ = False
    try:
        assert not world.host.load("asyncleak")
        assert builtins.__hexcast_async_undone__ is True                 # the failing hook did not stop the others
    finally:
        del builtins.__hexcast_async_undone__


# ---- failed operations leave a usable plugin --------------------------------------------------------------

def test_a_failed_install_leaves_nothing_on_disk(world, client):
    write_plugin(world.catalog, "needpip", requirements=BAD_PACKAGE)
    job = install(client, "needpip")
    assert job["state"] == "error" and "pip" in job["error"]
    assert not (world.plugins / "needpip").exists()                     # not half-installed ...
    c = card(client, "needpip")
    assert c["installed"] is False and c["state"] == "available"        # ... so the card offers Install again
    assert not list(world.plugins.glob(".stage-*"))


def test_dependencies_installed_before_a_failure_still_start(world, client):
    write_plugin(world.catalog, "libpart")
    write_plugin(world.catalog, "needpip", requires=["libpart"], requirements=BAD_PACKAGE)
    assert install(client, "needpip")["state"] == "error"
    assert "libpart" in world.host.loaded and not (world.plugins / "needpip").exists()


@pytest.mark.parametrize("failure", ["pip", "swap"])
def test_a_failed_update_keeps_the_previous_version_running(world, client, monkeypatch, failure):
    write_plugin(world.catalog, "alpha", version="1.0.0")
    assert install(client, "alpha")["state"] == "done"
    assert client.get("/alpha/api/status").status_code == 200
    if failure == "pip":
        write_plugin(world.catalog, "alpha", version="2.0.0", requirements=BAD_PACKAGE)
    else:
        write_plugin(world.catalog, "alpha", version="2.0.0")
        real = os.replace
        monkeypatch.setattr(installer_mod.os, "replace", lambda s, d, *a, **k: (_ for _ in ()).throw(PermissionError(5, "Access is denied"))
                            if ".stage-" in str(s) else real(s, d, *a, **k))
    r = client.post("/api/plugins/alpha/update")
    assert wait_job(client, r.json()["job"])["state"] == "error"
    assert json.loads((world.plugins / "alpha" / "plugin.json").read_text())["version"] == "1.0.0"
    assert client.get("/alpha/api/status").status_code == 200           # and still answering
    assert not list(world.plugins.glob(".stage-*")) and not list((world.plugins / ".swap").glob("*"))


def test_an_update_with_a_failing_package_never_stops_the_plugin(world, client):
    write_plugin(world.catalog, "alpha", version="1.0.0")
    install(client, "alpha")
    write_plugin(world.catalog, "alpha", version="2.0.0", requirements=BAD_PACKAGE)
    stops = []
    mod = world.host.module("alpha")
    mod.STOPPED = stops
    assert wait_job(client, client.post("/api/plugins/alpha/update").json()["job"])["state"] == "error"
    assert stops == []                                                  # prepared off to the side: teardown never ran


def test_a_successful_update_swaps_and_cleans_up(world, client):
    write_plugin(world.catalog, "alpha", version="1.0.0")
    install(client, "alpha")
    write_plugin(world.catalog, "alpha", version="2.0.0")
    assert wait_job(client, client.post("/api/plugins/alpha/update").json()["job"])["state"] == "done"
    assert json.loads((world.plugins / "alpha" / "plugin.json").read_text())["version"] == "2.0.0"
    assert world.host.module("alpha") is not None
    assert not list((world.plugins / ".swap").glob("*")) and not list(world.plugins.glob(".stage-*"))


def test_an_interrupted_update_is_undone_at_the_next_start(world):
    swap = world.plugins / ".swap"
    write_plugin(swap, "alpha-123")                                     # parked, the new copy never arrived
    write_plugin(swap, "beta-456")                                      # parked, but beta is installed again
    write_plugin(world.plugins, "beta", version="9.0.0")
    world.installer.clean_trash()
    assert (world.plugins / "alpha" / "plugin.json").is_file()
    assert json.loads((world.plugins / "beta" / "plugin.json").read_text())["version"] == "9.0.0"
    assert not swap.exists() or not list(swap.iterdir())


def test_a_failed_remove_leaves_the_plugin_running(world, client, monkeypatch):
    write_plugin(world.catalog, "alpha")
    install(client, "alpha")
    monkeypatch.setattr(Installer, "_trash", lambda self, folder: (_ for _ in ()).throw(InstallError("locked")))
    r = client.post("/api/plugins/alpha/uninstall", json={})
    assert r.status_code == 500 and "locked" in r.json()["error"]
    assert client.get("/alpha/api/status").status_code == 200


def test_a_locked_file_during_remove_is_a_message_not_a_crash(world, client, monkeypatch):
    write_plugin(world.catalog, "alpha")
    install(client, "alpha")

    def boom(self, pid, log=None):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(Installer, "uninstall", boom)
    r = client.post("/api/plugins/alpha/uninstall", json={})
    assert r.status_code == 500 and "Access is denied" in r.json()["error"]
    assert client.get("/alpha/api/status").status_code == 200


# ---- removing what nobody else has a copy of -----------------------------------------------------------

def test_removing_a_hand_made_plugin_keeps_its_files(world, client):
    write_plugin(world.plugins, "mydev", files={"notes.txt": "precious"})       # no install record: not from a catalog
    world.host.load("mydev")
    r = client.post("/api/plugins/mydev/uninstall", json={})
    d = r.json()
    assert r.status_code == 200 and d["removed"] == ["mydev"] and "mydev" in d["kept"]
    assert not (world.plugins / "mydev").exists()
    kept = list((world.plugins / ".removed").glob("mydev-*"))
    assert len(kept) == 1 and (kept[0] / "notes.txt").read_text() == "precious"
    assert "mydev" not in world.host.scan()                                     # (.removed is not scanned as a plugin)


def test_removing_a_catalog_plugin_deletes_it(world, client):
    write_plugin(world.catalog, "alpha")
    install(client, "alpha")
    d = client.post("/api/plugins/alpha/uninstall", json={}).json()
    assert d["ok"] and d["kept"] == {} and not (world.plugins / ".removed").exists()


def test_the_card_names_what_a_removal_would_take_with_it(world, client):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "kid", parent="base", nav=False)
    install(client, "kid")
    c = card(client, "base")
    assert c["dependents"] == ["kid"] and c["dependents_info"] == [{"id": "kid", "name": "Kid"}]
    r = client.post("/api/plugins/base/uninstall", json={})
    assert r.status_code == 409 and r.json()["dependents"] == ["kid"]
    assert (world.plugins / "base").exists() and (world.plugins / "kid").exists()


# ---- races between calls ----------------------------------------------------------------------------------

def test_enable_cannot_resurrect_a_plugin_that_is_being_removed(world):
    write_plugin(world.catalog, "slow", setup_body='''
import asyncio
from fastapi import APIRouter
router = APIRouter()
@router.get("/slow/api/status")
async def status():
    return {"ok": True}
def setup(ctx):
    ctx.include_router(router)
async def teardown(ctx):
    await asyncio.sleep(0.3)
''')
    world.installer.install("slow")
    service = make_service(world)
    assert world.host.load("slow")

    async def scenario():
        t1 = asyncio.create_task(service.uninstall("slow", False))
        await asyncio.sleep(0.1)                                        # the removal is inside the slow teardown
        t2 = asyncio.create_task(service.enable("slow"))
        t3 = asyncio.create_task(service.disable("slow"))
        return await asyncio.gather(t1, t2, t3, return_exceptions=True)

    r = asyncio.run(scenario())
    assert isinstance(r[0], dict) and r[0]["removed"] == ["slow"]
    assert all(type(x).__name__ == "ApiError" for x in r[1:]), r         # the others found it gone
    assert not (world.plugins / "slow").exists() and "slow" not in world.host.loaded
    assert TestClient(world.app).get("/slow/api/status").status_code == 404


def test_lifecycle_calls_are_refused_while_a_job_runs(world, client):
    write_plugin(world.catalog, "alpha")
    install(client, "alpha")
    from hexcast_core.api import Job
    world.service.jobs["fake"] = Job("fake", "Install other", "other")
    for path in ("disable", "enable"):
        r = client.post(f"/api/plugins/alpha/{path}")
        assert r.status_code == 409 and r.json()["plugin"] == "other" and r.json()["title"] == "Install other"
    assert client.post("/api/plugins/alpha/uninstall", json={}).status_code == 409


def test_an_unload_that_finishes_late_does_not_forget_a_newer_start(world):
    write_plugin(world.plugins, "slow", setup_body='''
import asyncio
from fastapi import APIRouter
router = APIRouter()
@router.get("/slow/api/status")
async def status():
    return {"ok": True}
def setup(ctx):
    ctx.include_router(router)
async def teardown(ctx):
    await asyncio.sleep(0.25)
''')
    assert world.host.load("slow")
    c = TestClient(world.app)

    async def scenario():
        a = asyncio.create_task(world.host.unload("slow"))
        await asyncio.sleep(0.1)
        b = asyncio.create_task(world.host.unload("slow"))            # a second stop of the same run, a little behind
        await asyncio.wait_for(a, 5)
        assert "slow" not in world.host.loaded
        assert world.host.load("slow")                                 # started again while b is still stopping the OLD run
        await asyncio.wait_for(b, 5)

    asyncio.run(scenario())
    # b must not have "stopped" the new run in the books while its routes stayed live
    assert "slow" in world.host.loaded
    assert c.get("/slow/api/status").status_code == 200


# ---- settings, upgrade and catalogs ----------------------------------------------------------------------

def test_an_unreadable_settings_file_stops_the_upgrade_step_and_is_kept(world):
    from hexcast_core import migrate
    write_plugin(world.catalog, "countdown", extra={"legacy_config": ["countdown.json"]})
    (world.config / "countdown.json").write_text("{}")
    path = world.config / "plugins.json"
    path.write_text('{"disabled": ["ticker"], "migrated": true,}')
    from hexcast_core.host import Settings
    world.host.settings = Settings(path)
    assert migrate.run(world.host, world.installer, lambda s: None) == []
    assert not (world.plugins / "countdown").exists()
    assert path.read_text().startswith('{"disabled": ["ticker"]')        # not overwritten
    assert (world.config / "plugins.json.bad").exists()


def test_the_upgrade_carries_on_after_one_plugin_fails_and_never_runs_pip(world, monkeypatch):
    from hexcast_core import migrate
    for pid in ("aaa", "bbb"):
        write_plugin(world.catalog, pid, requirements=BAD_PACKAGE, extra={"legacy_config": [f"{pid}.json"]})
        (world.config / f"{pid}.json").write_text("{}")
    real = Installer.copy_in

    def flaky(self, entry, log=None):
        if entry.id == "aaa":
            raise PermissionError(13, "Access is denied")
        return real(self, entry, log) if log else real(self, entry)
    monkeypatch.setattr(Installer, "copy_in", flaky)
    monkeypatch.setattr(Installer, "_pip_install", lambda *a, **k: (_ for _ in ()).throw(AssertionError("pip ran at start-up")))
    out = []
    got = migrate.run(world.host, world.installer, out.append)
    assert got == ["bbb"] and (world.plugins / "bbb").is_dir()
    assert any("aaa" in line and "Access is denied" in line for line in out)
    assert world.host.settings.data["migrated"] is True


def test_the_upgrade_waits_when_the_catalog_is_missing(world):
    from hexcast_core import migrate
    out = []
    assert migrate.run(world.host, world.installer, out.append) == []
    assert not world.host.settings.data["migrated"] and any("catalog" in line for line in out)


def test_migrated_plugins_missing_packages_are_left_for_the_background_repair(world):
    from hexcast_core import migrate
    write_plugin(world.catalog, "aaa", requirements=BAD_PACKAGE, extra={"legacy_config": ["aaa.json"]})
    (world.config / "aaa.json").write_text("{}")
    assert migrate.run(world.host, world.installer, lambda s: None) == ["aaa"]
    world.host.load_all()
    assert "aaa" in world.host.needs_deps and world.host.state("aaa") == "needs_deps"


def test_remote_index_rules(world, monkeypatch):
    """https only (localhost may use http); a download must come with its sha256; one bad entry does not hide the rest."""
    import hexcast_core.catalog as cat
    good = {"manifest": {**BASE, "id": "goodone"}, "url": "https://plugins.example/good.zip", "sha256": "a" * 64}
    nosum = {"manifest": {**BASE, "id": "nosum"}, "url": "https://plugins.example/nosum.zip"}
    plain = {"manifest": {**BASE, "id": "plainhttp"}, "url": "http://plugins.example/p.zip", "sha256": "b" * 64}
    bad = {"manifest": {**BASE, "id": "Bad Id"}, "url": "https://plugins.example/x.zip", "sha256": "c" * 64}
    served = {"https://plugins.example/index.json": {"plugins": [good, nosum, plain, bad]}}

    class Resp:
        def __init__(self, url):
            self.url = url
            self.body = json.dumps(served[url]).encode()
        def raise_for_status(self): pass
        def iter_bytes(self, n): yield self.body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    import httpx
    monkeypatch.setattr(httpx, "stream", lambda method, url, **kw: Resp(url))
    world.host.catalog.remote_urls[:] = ["https://plugins.example/index.json"]
    world.host.catalog.refresh_remote(force=True)
    entries = world.host.catalog.entries()
    assert set(entries) == {"goodone"}
    assert "skipped" in world.host.catalog.errors["https://plugins.example/index.json"]
    world.host.catalog.remote_urls[:] = ["http://plugins.example/index.json"]
    world.host.catalog.refresh_remote(force=True)
    assert world.host.catalog.entries() == {} and "https" in world.host.catalog.errors["http://plugins.example/index.json"]


def test_a_failed_refresh_keeps_the_last_good_entries_and_does_not_hammer_the_network(world, monkeypatch):
    import httpx
    url = "https://plugins.example/index.json"
    world.host.catalog.remote_urls[:] = [url]
    calls = []
    index = {"plugins": [{"manifest": {**BASE, "id": "goodone"}, "url": "https://plugins.example/g.zip", "sha256": "a" * 64}]}

    class Resp:
        def __init__(self, ok):
            self.ok, self.url = ok, url
        def raise_for_status(self):
            if not self.ok:
                raise httpx.ConnectError("offline")
        def iter_bytes(self, n): yield json.dumps(index).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    state = {"ok": True}
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: (calls.append(1), Resp(state["ok"]))[1])
    world.host.catalog.refresh_remote(force=True)
    assert "goodone" in world.host.catalog.entries()
    state["ok"] = False
    world.host.catalog.refresh_remote(force=True)
    assert "goodone" in world.host.catalog.entries()                      # the last good copy stays
    n = len(calls)
    for _ in range(5):
        world.host.catalog.refresh_remote()                               # polls in the next minute cost nothing
    assert len(calls) == n


def test_a_download_without_a_checksum_is_refused_even_if_the_index_lied(world, tmp_path):
    from hexcast_core.catalog import CatalogEntry
    m = parse_manifest({**BASE, "id": "nosum"})
    entry = CatalogEntry(m, "https://x/index.json", url="https://x/nosum.zip", sha256=None)
    with pytest.raises(InstallError, match="checksum"):
        world.installer.install_entry(entry)


# ---- the command line ------------------------------------------------------------------------------------

def test_the_command_line_does_not_clean_up_after_a_running_hexcast(capsys):
    from hexcast_core import cli, paths
    live = paths.PLUGINS_DIR / ".stage-live123"
    live.mkdir(parents=True, exist_ok=True)
    (live / "partial.txt").write_text("a running Hexcast is copying this")
    try:
        assert cli.main(["list"]) == 0
        assert (live / "partial.txt").exists()
    finally:
        import shutil
        shutil.rmtree(live, ignore_errors=True)


# ---- cross-site requests -----------------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Origin": "http://localhost:9999"},
                                     {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}])
@pytest.mark.parametrize("path,body", [("install", None), ("update", None), ("repair", None), ("enable", None),
                                       ("disable", None), ("uninstall", {})])
def test_other_web_pages_cannot_change_plugins(world, client, headers, path, body):
    write_plugin(world.catalog, "alpha")
    install(client, "alpha")
    kw = {"json": body} if body is not None else {}
    r = client.post(f"/api/plugins/alpha/{path}", headers=headers, **kw)
    assert r.status_code == 403
    assert "alpha" in world.host.loaded and (world.plugins / "alpha").exists()


def test_the_pages_hexcast_serves_may_change_plugins(world, client):
    write_plugin(world.catalog, "alpha")
    r = client.post("/api/plugins/alpha/install", headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200
    assert wait_job(client, r.json()["job"])["state"] == "done"
    assert client.post("/api/plugins/alpha/disable", headers={"Origin": "http://testserver"}).status_code == 200


# ---- caching -------------------------------------------------------------------------------------------------

def test_static_files_are_revalidated_not_reused_blindly(world, tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "hexbar.js").write_text("1")
    world.app.mount("/static", RevalidatingStaticFiles(directory=str(tmp_path / "s")))
    write_plugin(world.plugins, "alpha", files={"static/nav.js": "x"})
    world.host.load("alpha")
    c = TestClient(world.app)
    for url in ("/static/hexbar.js", "/plugins/alpha/static/nav.js"):
        r = c.get(url)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag"), url
        again = c.get(url, headers={"If-None-Match": r.headers["etag"]})
        assert again.status_code == 304                                   # cheap to re-check
    assert "no-cache" in [h for h in TestClient(world.app).get("/static/hexbar.js").headers.get("cache-control", "").split(",")]


def test_job_view_is_consistent_while_lines_arrive():
    from hexcast_core.api import Job
    j = Job("x", "t", "p")
    for i in range(5):
        j.log(str(i))
    v = j.view(2)
    assert v["lines"] == ["2", "3", "4"] and v["total"] == 5


def test_every_page_of_every_shipped_plugin_finds_its_assets(real_world):
    """Every script/style/image a page of a shipped plugin names is actually served."""
    import re
    real_world.app.mount("/static", StaticFiles(directory=str(ROOT / "static")))          # what hexcast.py mounts
    for pid in ("games", "games_roulette", "games_craps", "games_russian", "games_trivia",
                "countdown", "ticker", "twitch", "clips", "discord", "music"):
        real_world.installer.install(pid, packages=False)
    real_world.host.load_all()
    c = TestClient(real_world.app)
    pages = [i["href"] for i in real_world.host.nav_items()]
    pages += [h.rstrip("/") + "/overlay" for h in pages] + ["/twitch/chat", "/twitch/events", "/ytm/overlay",
                                                              "/games/overlay?game=roulette", "/plugins", "/help"]
    missing, seen = [], 0
    for page in dict.fromkeys(pages):
        r = c.get(page)
        if r.status_code != 200:
            continue                                                       # not a page this plugin has
        seen += 1
        for url in re.findall(r"""(?:src|href)=["'](/[^"'#?]+\.(?:js|css|png|jpg|svg|webp|gif))""", r.text):
            if c.get(url).status_code != 200:
                missing.append(f"{page} -> {url}")
    assert seen >= 8 and not missing, missing
