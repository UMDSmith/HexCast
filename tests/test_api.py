"""The plugin web API (what the + tab and the Games sub-store call)."""

import time

import pytest
from fastapi.testclient import TestClient

from conftest import write_plugin
from hexcast_core import PluginService, build_router


@pytest.fixture
def client(world):
    static = world.plugins.parent / "static"
    static.mkdir()
    (static / "plugins.html").write_text("<html>store</html>")
    (static / "help.html").write_text("<html><!--HELP_TOC-->|<!--HELP_SECTIONS--></html>")
    service = PluginService(world.host, world.installer, static)
    world.app.include_router(build_router(service))
    world.service = service
    with TestClient(world.app) as c:      # one event loop for the whole test: jobs keep running between calls
        world.client = c
        yield c


def wait_job(c, job_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/plugins/jobs/{job_id}").json()
        if d["state"] != "running":
            return d
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def install(c, pid):
    r = c.post(f"/api/plugins/{pid}/install")
    assert r.status_code == 200, r.text
    return wait_job(c, r.json()["job"])


def test_empty_world(client):
    d = client.get("/api/plugins").json()
    assert d["ok"] and d["plugins"] == [] and d["busy"] is None
    assert client.get("/api/plugins/nav").json()["items"] == []
    assert client.get("/plugins").status_code == 200


def test_listing_shows_the_catalog_and_filters_by_parent(world, client):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "kid", parent="base", nav=False)
    write_plugin(world.catalog, "lib", nav=False, extra={"hidden": True})
    top = {p["id"] for p in client.get("/api/plugins").json()["plugins"]}
    assert top == {"base"}                                   # add-ons and hidden libraries are not on the main page
    kids = client.get("/api/plugins?parent=base").json()["plugins"]
    assert [p["id"] for p in kids] == ["kid"] and kids[0]["parent"] == "base"
    everything = {p["id"] for p in client.get("/api/plugins?parent=*").json()["plugins"]}
    assert everything == {"base", "kid"}                     # hidden library only appears once installed


def test_install_job_installs_starts_and_shows_in_nav(world, client):
    write_plugin(world.catalog, "alpha")
    job = install(client, "alpha")
    assert job["state"] == "done", job
    assert any("Done." in ln for ln in job["lines"])
    assert client.get("/alpha/api/status").json()["plugin"] == "alpha"
    nav = client.get("/api/plugins/nav").json()["items"]
    assert [i["id"] for i in nav] == ["alpha"] and nav[0]["href"] == "/alpha" and nav[0]["state"] == "running"
    v = client.get("/api/plugins").json()["plugins"][0]
    assert v["installed"] and v["running"] and v["state"] == "running" and not v["update_available"]


def test_install_errors(world, client):
    assert client.post("/api/plugins/ghost/install").status_code == 404
    assert client.post("/api/plugins/BAD!/install").status_code == 404
    write_plugin(world.catalog, "addon", requires=["missing"])
    r = client.post("/api/plugins/addon/install")
    assert r.status_code == 400 and "missing" in r.json()["error"]


def test_a_plugin_that_cannot_start_reports_why(world, client):
    write_plugin(world.catalog, "bad", setup_body="def setup(ctx):\n    raise RuntimeError('nope')\n")
    job = install(client, "bad")
    assert job["state"] == "error" and "nope" in job["error"]
    v = [p for p in client.get("/api/plugins").json()["plugins"] if p["id"] == "bad"][0]
    assert v["installed"] and v["state"] == "error" and "nope" in v["error"]


def test_uninstall_with_dependents_needs_a_cascade(world, client):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "kid", parent="base", nav=False)
    assert install(client, "kid")["state"] == "done"                 # installs base first
    r = client.post("/api/plugins/base/uninstall", json={})
    assert r.status_code == 409 and r.json()["dependents"] == ["kid"] and r.json()["names"] == ["Kid"]
    assert (world.plugins / "base").exists()
    r = client.post("/api/plugins/base/uninstall", json={"cascade": True})
    assert r.status_code == 200 and r.json()["removed"] == ["kid", "base"]
    assert not (world.plugins / "base").exists() and not (world.plugins / "kid").exists()
    assert client.get("/base/api/status").status_code == 404


def test_disable_hides_the_tab_and_enable_brings_it_back(world, client):
    write_plugin(world.catalog, "alpha")
    install(client, "alpha")
    assert client.post("/api/plugins/alpha/disable").json()["ok"]
    assert client.get("/alpha/api/status").status_code == 404
    assert client.get("/api/plugins/nav").json()["items"] == []
    v = client.get("/api/plugins").json()["plugins"][0]
    assert v["installed"] and v["state"] == "disabled" and not v["enabled"]
    r = client.post("/api/plugins/alpha/enable").json()
    assert r["ok"] and r["running"]
    assert client.get("/alpha/api/status").status_code == 200


def test_disabling_a_parent_stops_its_addons_and_enabling_restores_them(world, client):
    write_plugin(world.catalog, "base")
    write_plugin(world.catalog, "kid", parent="base", nav=False)
    install(client, "kid")
    assert set(world.host.loaded) == {"base", "kid"}
    r = client.post("/api/plugins/base/disable").json()
    assert r["stopped"] == ["kid", "base"]
    assert world.host.loaded == {}
    client.post("/api/plugins/base/enable")
    assert set(world.host.loaded) == {"base", "kid"}


def test_update_reports_and_applies_a_newer_catalog_copy(world, client):
    write_plugin(world.catalog, "alpha", version="1.0.0")
    install(client, "alpha")
    write_plugin(world.catalog, "alpha", version="1.1.0", files={"new.txt": "hi"})
    v = client.get("/api/plugins").json()["plugins"][0]
    assert v["update_available"] and v["latest_version"] == "1.1.0" and v["installed_version"] == "1.0.0"
    r = client.post("/api/plugins/alpha/update")
    job = wait_job(client, r.json()["job"])
    assert job["state"] == "done", job
    assert (world.plugins / "alpha" / "new.txt").read_text() == "hi"
    v = client.get("/api/plugins").json()["plugins"][0]
    assert not v["update_available"] and v["installed_version"] == "1.1.0" and v["running"]
    assert client.get("/alpha/api/status").status_code == 200


def test_update_restarts_the_addons_that_were_running(world, client):
    write_plugin(world.catalog, "base", version="1.0.0")
    write_plugin(world.catalog, "kid", parent="base", nav=False)
    install(client, "kid")
    write_plugin(world.catalog, "base", version="1.0.1")
    assert wait_job(client, client.post("/api/plugins/base/update").json()["job"])["state"] == "done"
    assert set(world.host.loaded) == {"base", "kid"}


def test_repair_reinstalls_packages(world, client):
    write_plugin(world.catalog, "alpha", requirements="fastapi>=0.1\n")
    install(client, "alpha")
    job = wait_job(client, client.post("/api/plugins/alpha/repair").json()["job"])
    assert job["state"] == "done", job
    assert any("pip" in ln.lower() or "requirement" in ln.lower() for ln in job["lines"])


def test_only_one_install_at_a_time(world, client):
    write_plugin(world.catalog, "alpha")
    write_plugin(world.catalog, "beta")
    world.service.jobs["j"] = __import__("hexcast_core.api", fromlist=["Job"]).Job("j", "Install x", "x")
    r = client.post("/api/plugins/alpha/install")
    assert r.status_code == 409 and r.json()["job"] == "j"
    world.service.jobs["j"].state = "done"
    assert client.post("/api/plugins/alpha/install").status_code == 200


def test_help_page_is_assembled_from_running_plugins(world, client):
    write_plugin(world.catalog, "alpha", files={"help.html": '<section id="alpha">Alpha docs</section>'},
                 extra={"help": {"file": "help.html", "toc": [{"id": "alpha", "label": "Alpha"}]}})
    assert client.get("/help").text == "<html>|</html>"
    install(client, "alpha")
    page = client.get("/help").text
    assert '<a href="#alpha">Alpha</a>' in page and "Alpha docs" in page
    client.post("/api/plugins/alpha/disable")
    assert "Alpha docs" not in client.get("/help").text


def test_status_dot_script_is_served_from_the_plugin(world, client):
    write_plugin(world.catalog, "alpha", files={"static/nav.js": "/* dot */"},
                 extra={"nav": {"label": "Alpha", "href": "/alpha", "status_url": "/alpha/api/status", "status_js": "nav.js"}})
    install(client, "alpha")
    item = client.get("/api/plugins/nav").json()["items"][0]
    assert item["status_js"].startswith("/plugins/alpha/static/nav.js")
    assert client.get(item["status_js"].split("?")[0]).text == "/* dot */"


def test_missing_packages_show_as_needs_deps_and_repair_button_state(world, client):
    write_plugin(world.catalog, "alpha", requirements="definitely-not-a-real-package-xyz>=1.0\n")
    world.installer.copy_in(world.host.catalog.get("alpha"))
    assert not world.host.load("alpha")
    v = client.get("/api/plugins").json()["plugins"][0]
    assert v["state"] == "needs_deps"
    nav = client.get("/api/plugins/nav").json()["items"]
    assert nav[0]["state"] == "needs_deps"
    assert v["needs_packages"] is False          # installed already: the chip is only for plugins not yet installed


def test_nav_reports_plugins_with_updates(world, client):
    write_plugin(world.catalog, "alpha", version="1.0.0")
    install(client, "alpha")
    assert client.get("/api/plugins/nav").json()["updates"] == []
    write_plugin(world.catalog, "alpha", version="1.0.1")
    assert client.get("/api/plugins/nav").json()["updates"] == ["alpha"]
    assert wait_job(client, client.post("/api/plugins/alpha/update").json()["job"])["state"] == "done"
    assert client.get("/api/plugins/nav").json()["updates"] == []


def test_leftover_stage_folders_are_swept_at_start(world):
    (world.plugins / ".stage-abc123").mkdir()
    (world.plugins / ".stage-abc123" / "x").write_text("half copied")
    world.installer.clean_trash()
    assert not (world.plugins / ".stage-abc123").exists()
