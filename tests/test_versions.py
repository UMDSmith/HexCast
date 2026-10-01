"""Versions and updates: number formatting / comparison, the upstream (GitHub) source with fakes and a local
server (never the real internet), update_available, safe extraction, applying an upstream update, the API,
the nav items, the Games registry, the CLI and the config validation."""

import functools
import http.server
import importlib
import io
import json
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import _SESSION, write_plugin
from hexcast_core import InstallError, PluginService, build_router
from hexcast_core.catalog import CatalogEntry
from hexcast_core.host import Settings
from hexcast_core.installer import safe_extract_subdir
from hexcast_core.manifest import parse_manifest
from hexcast_core import versions
from hexcast_core.versions import (DEFAULT_UPSTREAM, NotFound, Upstream, fmt_version, is_newer, normalize_upstream,
                                   update_info)


# ---- helpers ------------------------------------------------------------------------------------------

def manifest(pid="alpha", version="1.1.0"):
    return parse_manifest({"id": pid, "name": pid.title(), "version": version})


class FakeUpstream(Upstream):
    """Upstream whose network is a dict: url -> bytes | Exception."""

    def __init__(self, answers=None, config=None):
        super().__init__(config if config is not None else dict(DEFAULT_UPSTREAM))
        self.answers = answers or {}
        self.calls = []

    def _get(self, url, limit, timeout):
        self.calls.append(url)
        a = self.answers.get(url, NotFound(url))
        if isinstance(a, Exception):
            raise a
        return a


def raw(up, pid, version):
    return {up.raw_url(pid): json.dumps({"id": pid, "name": pid.title(), "version": version}).encode()}


def repo_zip(plugins, wrapper="hexcast-main", extra=None):
    """A GitHub-style repository archive: {wrapper}/catalog/<id>/<files>, plus other repo stuff."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{wrapper}/README.md", "hello")
        z.writestr(f"{wrapper}/hexcast.py", "print('core')")
        for pid, (version, files) in plugins.items():
            z.writestr(f"{wrapper}/catalog/{pid}/plugin.json", json.dumps(
                {"id": pid, "name": pid.title(), "version": version, "nav": {"label": pid.title(), "href": f"/{pid}"}}))
            z.writestr(f"{wrapper}/catalog/{pid}/plugin.py", "def setup(ctx):\n    pass\n")
            for name, text in files.items():
                z.writestr(f"{wrapper}/catalog/{pid}/{name}", text)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def server():
    """A tiny local HTTP server: server.files maps a path to bytes; server.hits records requests."""
    files, hits = {}, []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    class S:
        pass

    s = S()
    s.files, s.hits = files, hits
    s.base = f"http://localhost:{httpd.server_address[1]}"
    s.config = {"repo": "UMDSmith/hexcast", "branch": "main",
                "raw_url": s.base + "/raw/{repo}/{branch}/catalog/{id}/plugin.json",
                "archive_url": s.base + "/zip/{repo}/{branch}.zip"}
    yield s
    httpd.shutdown()


@pytest.fixture
def client(world):
    static = world.plugins.parent / "static"
    static.mkdir()
    (static / "plugins.html").write_text("<html>store</html>")
    (static / "help.html").write_text("<html></html>")
    world.service = PluginService(world.host, world.installer, static)
    world.app.include_router(build_router(world.service))
    with TestClient(world.app) as c:
        yield c


def wait_job(c, job_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/plugins/jobs/{job_id}").json()
        if d["state"] != "running":
            return d
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def installed_alpha(world, version="1.0.0", **kw):
    write_plugin(world.catalog, "alpha", version=version, **kw)
    world.installer.install("alpha")
    return world.host.scan()["alpha"]


# ---- formatting and comparing ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw_v,shown", [("1.0.0", "1.0"), ("2.0", "2.0"), ("1.2.3", "1.2.3"), ("1.10.0", "1.10"),
                                         ("1.0.1", "1.0.1"), ("1.0.0-beta", "1.0.0-beta"), ("1.0.0.0", "1.0.0.0"),
                                         ("", ""), (None, ""), ("dev", "dev")])
def test_version_display(raw_v, shown):
    assert fmt_version(raw_v) == shown


@pytest.mark.parametrize("a,b,newer", [("1.1.0", "1.0.0", True), ("1.0.0", "1.0.0", False), ("1.0.0", "1.1.0", False),
                                       ("1.10.0", "1.9.0", True), ("2.0", "1.9.9", True), ("1.0", "1.0.0", False),
                                       ("1.0.0", "1.0.0-beta", True), ("1.0.0-beta", "1.0.0", False),
                                       ("1.0.0-beta.2", "1.0.0-beta.1", True), ("1.0.0+build5", "1.0.0", False),
                                       ("garbage", "1.0.0", False), ("", "1.0.0", False), (None, "1.0.0", False),
                                       ("1.0.1", "1.0.0.9", True), ("v1.2.0", "1.1.0", True)])
def test_is_newer(a, b, newer):
    assert is_newer(a, b) is newer


# ---- config validation -----------------------------------------------------------------------------------

def test_upstream_setting_validation():
    assert normalize_upstream(None) == (DEFAULT_UPSTREAM, None)
    assert normalize_upstream(True)[0] == DEFAULT_UPSTREAM
    assert normalize_upstream({})[0] == DEFAULT_UPSTREAM
    assert normalize_upstream(False) == (None, None)
    assert normalize_upstream({"repo": "me/fork", "branch": "dev/x"})[0] == {"repo": "me/fork", "branch": "dev/x"}
    assert normalize_upstream({"repo": "me/fork"})[0] == {"repo": "me/fork", "branch": "main"}
    for bad in ("yes", 5, [], {"repo": "nope"}, {"repo": "a/b/c"}, {"repo": "../x"}, {"repo": "a/..", "branch": "main"},
                {"branch": "../../etc"}, {"branch": ""}, {"branch": "-rf"}, {"repo": 5},
                {"raw_url": "https://evil.example/{id}"}, {"archive_url": "http://github.com/x.zip"},
                {"raw_url": "ftp://localhost/x"}):
        got, why = normalize_upstream(bad)
        assert got == DEFAULT_UPSTREAM and why, bad                  # unusable: the default is used, and it says so
    ok = {"raw_url": "http://localhost:9/{repo}/{id}.json", "archive_url": "https://codeload.github.com/{repo}/zip/{branch}"}
    assert normalize_upstream(ok)[0]["raw_url"] == ok["raw_url"]


def test_upstream_in_plugins_json_is_read_and_kept(tmp_path):
    f = tmp_path / "plugins.json"
    assert Settings(f).upstream == DEFAULT_UPSTREAM                  # nothing written: the default
    f.write_text(json.dumps({"upstream": False, "disabled": ["x"]}))
    s = Settings(f)
    assert s.upstream is None
    s.set_disabled("y", True)                                        # saving keeps the key as the user wrote it
    assert json.loads(f.read_text())["upstream"] is False
    f.write_text(json.dumps({"upstream": {"repo": "me/fork", "branch": "dev"}}))
    s = Settings(f)
    assert s.upstream == {"repo": "me/fork", "branch": "dev"} and s.upstream_problem is None
    f.write_text(json.dumps({"upstream": {"repo": "not a repo"}}))
    s = Settings(f)
    assert s.upstream == DEFAULT_UPSTREAM and s.upstream_problem
    assert s.problem is None                                         # a bad value is not a damaged file


def test_urls_are_github_https_only():
    up = Upstream({"repo": "me/fork", "branch": "dev"})
    assert up.raw_url("twitch") == "https://raw.githubusercontent.com/me/fork/dev/catalog/twitch/plugin.json"
    assert up.archive_url() == "https://codeload.github.com/me/fork/zip/refs/heads/dev"
    assert up.download_url() == "https://github.com/me/fork/archive/refs/heads/dev.zip"
    for good in ("https://github.com/a/b", "https://raw.githubusercontent.com/a", "http://localhost:80/x", "http://127.0.0.1/x"):
        versions.check_url(good)
    for bad in ("http://github.com/a", "https://evil.example/a", "https://github.com.evil.example/a", "ftp://github.com/a",
                "https://localhost/x", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            versions.check_url(bad)


# ---- the upstream cache --------------------------------------------------------------------------------

def test_upstream_caches_for_an_hour_and_only_asks_about_what_is_due():
    up = FakeUpstream()
    up.answers = {**raw(up, "alpha", "1.1.0"), **raw(up, "beta", "2.0.0")}
    up.refresh(["alpha", "beta"])
    assert up.latest("alpha") == "1.1.0" and up.latest("beta") == "2.0.0" and len(up.calls) == 2
    up.refresh(["alpha", "beta"])
    assert len(up.calls) == 2                                        # cached
    assert up.due(["alpha", "gamma"]) == ["gamma"]                   # only the new one is due
    assert up.due(["alpha"], now=time.time() + versions.TTL_SECONDS + 5) == ["alpha"]
    up.refresh(["alpha"], force=True)
    assert len(up.calls) == 3


def test_upstream_failures_are_silent_and_back_off():
    up = FakeUpstream({})
    up.answers = {**raw(up, "alpha", "1.1.0"), up.raw_url("beta"): ConnectionError("offline")}
    up.refresh(["alpha"])
    up.refresh(["beta", "alpha"], force=True)                        # no exception, whatever the network does
    assert up.latest("alpha") == "1.1.0"                             # what we knew stays
    assert "offline" in up.last_error
    n = len(up.calls)
    up.refresh(["beta", "gamma"])                                    # inside the back-off: nothing is asked
    assert len(up.calls) == n and up.due(["beta"]) == []
    assert up.due(["beta"], now=time.time() + versions.RETRY_SECONDS + 5) == ["beta"]


def test_upstream_ignores_what_is_not_a_usable_manifest():
    up = FakeUpstream()
    up.answers = {up.raw_url("a1"): b"<html>not json", up.raw_url("a2"): b'{"id": "other", "name": "X", "version": "1.0.0"}',
                  up.raw_url("a3"): b'{"id": "a3", "name": "X", "version": "banana"}'}
    up.refresh(["a1", "a2", "a3", "a4"])                             # a4: 404
    assert [up.latest(p) for p in ("a1", "a2", "a3", "a4")] == [None] * 4 and up.last_error is None
    assert up.due(["a1", "a4"]) == []                                # and not asked again for an hour


def test_disabled_upstream_never_asks():
    up = FakeUpstream(config=None)
    up.config = None
    assert not up.enabled and not up.kick(["alpha"], force=True)
    up.refresh(["alpha"])
    assert up.calls == [] and up.latest("alpha") is None and up.entry("alpha") is None


def test_kick_does_not_block_and_runs_in_a_thread():
    up = FakeUpstream()
    gate = threading.Event()
    answers = raw(up, "alpha", "1.2.0")
    orig = up._get

    def slow(url, limit, timeout):
        gate.wait(5)
        return orig(url, limit, timeout)

    up.answers = answers
    up._get = slow
    t0 = time.time()
    assert up.kick(["alpha"]) is True
    assert time.time() - t0 < 1 and up.latest("alpha") is None       # returned at once, nothing known yet
    assert up.kick(["alpha"]) is False                               # one round at a time
    gate.set()
    up._thread.join(5)
    assert up.latest("alpha") == "1.2.0"


def test_the_real_fetch_reads_a_local_server_and_refuses_oversized_answers(server):
    server.files["/raw/UMDSmith/hexcast/main/catalog/alpha/plugin.json"] = json.dumps(
        {"id": "alpha", "name": "Alpha", "version": "3.1.0"}).encode()
    server.files["/raw/UMDSmith/hexcast/main/catalog/big/plugin.json"] = b"x" * (versions.MANIFEST_MAX + 10)
    up = Upstream(server.config)
    up.refresh(["alpha", "missing", "big"])
    assert up.latest("alpha") == "3.1.0" and up.latest("missing") is None
    assert up.last_error and "larger" in up.last_error                # the oversized one is an error, not stored
    up2 = Upstream({"repo": "UMDSmith/hexcast", "branch": "main", "raw_url": "http://localhost:1/{id}"})
    up2.refresh(["alpha"])                                           # connection refused: silent
    assert up2.latest("alpha") is None and up2.last_error


# ---- is an update available? -------------------------------------------------------------------------------

def test_update_info_table(world):
    inst = installed_alpha(world, "1.0.0")
    cat = world.host.catalog.get("alpha")
    up = FakeUpstream()

    def info(catalog_entry=cat, upstream_version=None):
        up._manifests = {"alpha": manifest("alpha", upstream_version)} if upstream_version else {}
        up._at = {"alpha": time.time()}
        return update_info(up, world.host.scan()["alpha"], catalog_entry)

    i = info()
    assert not i.available and i.latest == "1.0.0" and i.source is None
    i = info(upstream_version="1.1.0")
    assert i.available and i.source == "upstream" and i.latest == "1.1.0" and i.entry.source == "upstream"
    assert i.entry.subdir == "catalog/alpha" and i.entry.url.startswith("https://codeload.github.com/")
    assert not info(upstream_version="1.0.0").available               # equal
    assert not info(upstream_version="0.9.0").available               # never a downgrade
    assert not update_info(None, inst, cat).available                 # no upstream at all
    # a newer catalog (a git pull) wins on a tie, and upstream when it is newer than the catalog
    write_plugin(world.catalog, "alpha", version="1.1.0")
    cat = world.host.catalog.get("alpha")
    i = info(cat, "1.1.0")
    assert i.available and i.source == "catalog" and i.latest == "1.1.0"
    i = info(cat, "1.2.0")
    assert i.available and i.source == "upstream" and i.latest == "1.2.0"
    # no catalog copy at all: upstream still works
    assert info(None, "1.3.0").source == "upstream"


def test_digest_difference_still_means_an_update_but_never_a_downgrade(world):
    installed_alpha(world, "1.0.0")
    write_plugin(world.catalog, "alpha", version="1.0.0", files={"edited.txt": "x"})     # edited without a version bump
    i = update_info(None, world.host.scan()["alpha"], world.host.catalog.get("alpha"))
    assert i.available and i.source == "catalog" and i.latest == "1.0.0"
    write_plugin(world.plugins, "alpha", version="1.5.0")                                  # installed copy is newer than the catalog's
    (world.plugins / "alpha" / ".hexcast-plugin.json").write_text(json.dumps({"source": "bundled", "content_hash": "other", "version": "1.5.0"}))
    i = update_info(None, world.host.scan()["alpha"], world.host.catalog.get("alpha"))
    assert not i.available and i.latest == "1.5.0"


def test_upstream_only_applies_to_plugins_that_came_from_the_project(world):
    write_plugin(world.plugins, "mine", version="1.0.0")                                   # copied in by hand: no install record
    up = FakeUpstream()
    up._manifests = {"mine": manifest("mine", "9.0.0")}
    assert not update_info(up, world.host.scan()["mine"], None).available


# ---- safe extraction of one plugin from a repository zip ---------------------------------------------------

def test_extract_only_the_wanted_plugin(tmp_path):
    data = repo_zip({"alpha": ("1.1.0", {"static/a.js": "js"}), "beta": ("1.0.0", {})},
                    extra={"hexcast-main/catalog/alphabet/plugin.json": "{}", "hexcast-main/../evil.txt": "x"})
    z = tmp_path / "r.zip"
    z.write_bytes(data)
    dest = tmp_path / "out" / "alpha"
    safe_extract_subdir(z, dest, "catalog/alpha")
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()) == \
        ["plugin.json", "plugin.py", "static/a.js"]
    assert not (tmp_path / "out" / "evil.txt").exists() and not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("bad_name", ["hexcast-main/catalog/alpha/../../../evil.txt", "hexcast-main/catalog/alpha//x.txt",
                                      "hexcast-main/catalog/alpha/C:evil.txt", "hexcast-main/catalog/alpha/./x.txt"])
def test_extract_refuses_unsafe_paths_inside_the_plugin(tmp_path, bad_name):
    z = tmp_path / "r.zip"
    z.write_bytes(repo_zip({"alpha": ("1.0.0", {})}, extra={bad_name: "boom"}))
    with pytest.raises(InstallError, match="unsafe"):
        safe_extract_subdir(z, tmp_path / "d", "catalog/alpha")
    assert not (tmp_path / "evil.txt").exists()


def test_extract_refuses_symlinks_missing_plugins_and_junk(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("w/catalog/alpha/plugin.json", "{}")
        link = zipfile.ZipInfo("w/catalog/alpha/link")
        link.external_attr = 0o120777 << 16
        zf.writestr(link, "/etc/passwd")
    z = tmp_path / "s.zip"
    z.write_bytes(buf.getvalue())
    with pytest.raises(InstallError, match="symbolic"):
        safe_extract_subdir(z, tmp_path / "d1", "catalog/alpha")
    z.write_bytes(repo_zip({"beta": ("1.0.0", {})}))
    with pytest.raises(InstallError, match="no catalog/alpha"):
        safe_extract_subdir(z, tmp_path / "d2", "catalog/alpha")
    z.write_bytes(b"not a zip")
    with pytest.raises(InstallError, match="not a zip"):
        safe_extract_subdir(z, tmp_path / "d3", "catalog/alpha")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("w/catalog/alpha/plugin.py", "x")
    z.write_bytes(buf.getvalue())
    with pytest.raises(InstallError, match="plugin.json"):
        safe_extract_subdir(z, tmp_path / "d4", "catalog/alpha")


# ---- applying an upstream update through the installer / the API ---------------------------------------------

def publish(server, version, plugins=None, rawfor=("alpha",)):
    files = {"static/new.txt": f"v{version}"}
    server.files["/zip/UMDSmith/hexcast/main.zip"] = repo_zip(plugins or {"alpha": (version, files)})
    for pid in rawfor:
        server.files[f"/raw/UMDSmith/hexcast/main/catalog/{pid}/plugin.json"] = json.dumps(
            {"id": pid, "name": pid.title(), "version": version}).encode()


def test_an_upstream_update_applies_end_to_end_and_keeps_the_config(world, client, server):
    installed_alpha(world, "1.0.0")
    world.host.load_all()
    (world.config / "alpha.json").write_text('{"keep": "me"}')
    world.host.upstream = Upstream(server.config)
    # nothing newer yet
    nav = client.get("/api/plugins/nav").json()
    assert nav["updates"] == [] and nav["items"][0]["update_available"] is False
    world.host.upstream.refresh(["alpha"], force=True)
    # GitHub now has 1.1.0
    publish(server, "1.1.0")
    world.host.upstream.refresh(["alpha"], force=True)
    nav = client.get("/api/plugins/nav").json()
    item = nav["items"][0]
    assert nav["updates"] == ["alpha"]
    assert (item["installed_version"], item["latest_version"], item["update_available"], item["update_source"]) == \
        ("1.0.0", "1.1.0", True, "upstream")
    assert item["version_label"] == "1.0" and item["latest_label"] == "1.1"
    v = client.get("/api/plugins").json()["plugins"][0]
    assert v["update_available"] and v["update_source"] == "upstream" and v["latest_version"] == "1.1.0" and v["version_label"] == "1.0"

    r = client.post("/api/plugins/alpha/update")
    job = wait_job(client, r.json()["job"])
    assert job["state"] == "done", job
    assert any("GitHub" in ln for ln in job["lines"])
    assert (world.plugins / "alpha" / "static" / "new.txt").read_text() == "v1.1.0"
    assert json.loads((world.plugins / "alpha" / "plugin.json").read_text())["version"] == "1.1.0"
    assert (world.config / "alpha.json").read_text() == '{"keep": "me"}'
    assert "alpha" in world.host.loaded and client.get("/alpha/api/status").status_code == 200
    meta = world.host.scan()["alpha"].meta
    assert meta["source"] == "upstream" and meta["version"] == "1.1.0" and meta["content_hash"]
    v = client.get("/api/plugins").json()["plugins"][0]
    assert not v["update_available"] and v["installed_version"] == "1.1.0" and v["version_label"] == "1.1" and v["running"]
    assert client.get("/api/plugins/nav").json()["updates"] == []


def test_a_broken_upstream_download_leaves_the_old_version_running(world, client, server):
    installed_alpha(world, "1.0.0")
    world.host.load_all()
    world.host.upstream = Upstream(server.config)
    publish(server, "1.1.0")
    world.host.upstream.refresh(["alpha"], force=True)
    server.files["/zip/UMDSmith/hexcast/main.zip"] = b"definitely not a zip"
    job = wait_job(client, client.post("/api/plugins/alpha/update").json()["job"])
    assert job["state"] == "error" and "zip" in job["error"]
    assert world.host.scan()["alpha"].version == "1.0.0" and "alpha" in world.host.loaded
    assert not list(world.plugins.glob(".stage-*"))                  # no half-built leftovers
    server.files.pop("/zip/UMDSmith/hexcast/main.zip")               # a 404 from the server
    job = wait_job(client, client.post("/api/plugins/alpha/update").json()["job"])
    assert job["state"] == "error" and world.host.scan()["alpha"].version == "1.0.0"


def test_updating_an_add_on_touches_only_that_add_on(world, client, server):
    write_plugin(world.catalog, "base", version="1.0.0")
    write_plugin(world.catalog, "kid", version="1.0.0", parent="base", nav=False, requires=["base"])
    write_plugin(world.catalog, "kid2", version="1.0.0", parent="base", nav=False, requires=["base"])
    world.installer.install("kid")
    world.installer.install("kid2")
    world.host.load_all()
    base_loaded = world.host.loaded["base"]
    kid2_loaded = world.host.loaded["kid2"]
    world.host.upstream = Upstream(server.config)
    server.files["/zip/UMDSmith/hexcast/main.zip"] = repo_zip({"kid": ("1.0.1", {"static/n.txt": "n"})})
    for pid, ver in (("base", "1.0.0"), ("kid", "1.0.1"), ("kid2", "1.0.0")):
        server.files[f"/raw/UMDSmith/hexcast/main/catalog/{pid}/plugin.json"] = json.dumps(
            {"id": pid, "name": pid, "version": ver}).encode()
    world.host.upstream.refresh(["base", "kid", "kid2"], force=True)
    kids = {p["id"]: p for p in client.get("/api/plugins?parent=base").json()["plugins"]}
    assert kids["kid"]["update_available"] and not kids["kid2"]["update_available"]
    assert not client.get("/api/plugins").json()["plugins"][0]["update_available"]
    job = wait_job(client, client.post("/api/plugins/kid/update").json()["job"])
    assert job["state"] == "done", job
    assert world.host.scan()["kid"].version == "1.0.1" and world.host.scan()["base"].version == "1.0.0"
    assert world.host.loaded["base"] is base_loaded and world.host.loaded["kid2"] is kid2_loaded and "kid" in world.host.loaded


def test_a_local_catalog_update_still_works_without_upstream(world, client):
    installed_alpha(world, "1.0.0")
    world.host.load_all()
    write_plugin(world.catalog, "alpha", version="1.2.0", files={"new.txt": "hi"})
    v = client.get("/api/plugins").json()["plugins"][0]
    assert v["update_available"] and v["update_source"] == "catalog" and v["latest_version"] == "1.2.0"
    assert client.get("/api/plugins/nav").json()["updates"] == ["alpha"]
    job = wait_job(client, client.post("/api/plugins/alpha/update").json()["job"])
    assert job["state"] == "done" and (world.plugins / "alpha" / "new.txt").read_text() == "hi"
    assert world.host.scan()["alpha"].meta["source"] == "bundled"


# ---- API fields on a plain listing --------------------------------------------------------------------------

def test_listing_fields_for_not_installed_installed_and_up_to_date(world, client):
    write_plugin(world.catalog, "alpha", version="1.0.0")
    write_plugin(world.catalog, "beta", version="2.1.0")
    world.installer.install("alpha")
    plugins = {p["id"]: p for p in client.get("/api/plugins").json()["plugins"]}
    a, b = plugins["alpha"], plugins["beta"]
    assert (a["installed_version"], a["latest_version"], a["update_available"], a["update_source"]) == ("1.0.0", "1.0.0", False, None)
    assert a["version"] == "1.0.0" and a["version_label"] == "1.0"                  # the old fields are still there
    assert (b["installed"], b["installed_version"], b["latest_version"], b["update_available"], b["version_label"]) == \
        (False, None, "2.1.0", False, "2.1")


# ---- the Games registry and /api/version ---------------------------------------------------------------------

def test_registry_carries_version_and_update_info(real_world):
    real_world.installer.install("games")
    real_world.installer.install("games_craps")
    real_world.installer.install("games_roulette")
    real_world.host.load_all()
    c = TestClient(real_world.app)
    games = {g["key"]: g for g in c.get("/games/api/registry").json()["games"]}
    craps = games["craps"]
    assert craps["version"] == "1.0.0" and craps["version_label"] == "1.0"
    assert craps["update_available"] is False and craps["update_source"] is None and craps["latest_label"] == "1.0"
    up = FakeUpstream()
    up._manifests = {"games_craps": manifest("games_craps", "1.1.0")}
    up._at = {"games_craps": time.time()}
    real_world.host.upstream = up
    games = {g["key"]: g for g in c.get("/games/api/registry").json()["games"]}
    assert games["craps"]["update_available"] and games["craps"]["latest_version"] == "1.1.0" \
        and games["craps"]["latest_label"] == "1.1" and games["craps"]["update_source"] == "upstream"
    assert not games["roulette"]["update_available"]                    # only the add-on that has news
    for g in games.values():                                            # nothing that was there is gone
        assert {"key", "plugin", "title", "order", "panel_js", "overlay"} <= set(g)


def test_nav_items_carry_the_module_version(real_world):
    real_world.installer.install("games")
    real_world.host.load_all()
    item = [i for i in real_world.host.nav_items() if i["id"] == "games"][0]
    assert item["version_label"] == "1.0" and item["installed_version"] == "1.0.0" and item["update_available"] is False


@pytest.fixture(scope="module")
def hexcast():
    for d in ("plugins", "config", "media"):
        (_SESSION / d).mkdir(exist_ok=True)
    sys.modules.pop("hexcast", None)
    mod = importlib.import_module("hexcast")
    yield mod
    for name in [n for n in sys.modules if n == "hexcast" or n == "hexcast_plugins" or n.startswith("hexcast_plugins.")]:
        del sys.modules[name]


def test_core_version_says_where_the_download_is(hexcast):
    c = TestClient(hexcast.app)
    d = c.get("/api/version").json()
    assert d["version"] == hexcast.VERSION and d["check_enabled"] is False and d["update_available"] is False
    assert d["repo_url"] == "https://github.com/UMDSmith/hexcast"
    hexcast.plugin_host.upstream = Upstream({"repo": "me/fork", "branch": "dev"})
    hexcast._version_cache.update({"latest": "99.1", "checked_at": time.time(), "running": False})
    try:
        d = c.get("/api/version").json()
        assert d["update_available"] and d["latest"] == "99.1" and d["check_enabled"] and not d["checking"]
        assert d["download_url"] == "https://github.com/me/fork/archive/refs/heads/dev.zip"
        assert d["repo_url"] == "https://github.com/me/fork"
    finally:
        hexcast._version_cache.update({"latest": None, "checked_at": 0.0})
        hexcast.plugin_host.upstream = Upstream(None)


# ---- the command line -----------------------------------------------------------------------------------------

def test_cli_list_and_update_use_upstream(world, server, monkeypatch, capsys):
    from hexcast_core import cli, host as host_mod
    installed_alpha(world, "1.0.0")
    world.config.joinpath("plugins.json").write_text(json.dumps({"upstream": server.config}))
    publish(server, "1.4.0")
    monkeypatch.delenv("HEXCAST_NO_UPSTREAM", raising=False)

    class H(host_mod.PluginHost):
        def __init__(self, app):
            super().__init__(app, 4747, root=world.plugins.parent, plugins_dir=world.plugins,
                             catalog_dir=world.catalog, config_dir=world.config, media_dir=world.media)

    monkeypatch.setattr(cli, "PluginHost", H)
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    row = [ln for ln in out.splitlines() if ln.strip().startswith("alpha")][0]
    assert "1.0" in row and "1.4" in row and "upstream" in row
    assert cli.main(["update", "--all"]) == 0
    assert world.host.scan()["alpha"].version == "1.4.0"
    assert cli.main(["update", "--all"]) == 0
    assert "up to date" in capsys.readouterr().out
