"""Remote catalogs: an index.json somewhere on the web, plugins as zips."""

import hashlib
import http.server
import json
import subprocess
import sys
import threading
import zipfile
from functools import partial
from pathlib import Path

import pytest

from conftest import ROOT, write_plugin
from hexcast_core.installer import InstallError, safe_extract


@pytest.fixture
def remote(tmp_path):
    """Serve tmp/out over http and return (url of index.json, folder, helper to rebuild)."""
    out = tmp_path / "out"
    out.mkdir()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.json", out
    srv.shutdown()


def build(src_catalog: Path, out: Path):
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_catalog.py"), str(out), "--catalog", str(src_catalog)],
                   check=True, capture_output=True)


def test_install_from_a_remote_catalog(world, remote, tmp_path):
    url, out = remote
    src = tmp_path / "src"
    write_plugin(src, "farplugin", files={"static/x.txt": "far"})
    build(src, out)
    assert (out / "index.json").exists()

    world.host.catalog.remote_urls.append(url)
    world.host.catalog.refresh_remote(force=True)
    assert "farplugin" in world.host.catalog.entries()
    assert world.host.catalog.entries()["farplugin"].source == url

    assert world.installer.install("farplugin") == ["farplugin"]
    assert (world.plugins / "farplugin" / "static" / "x.txt").read_text() == "far"
    assert world.host.load("farplugin")
    meta = json.loads((world.plugins / "farplugin" / ".hexcast-plugin.json").read_text())
    assert meta["source"] == url


def test_bundled_plugins_win_over_remote_ones(world, remote, tmp_path):
    url, out = remote
    src = tmp_path / "src"
    write_plugin(src, "same", version="9.9.9")
    build(src, out)
    write_plugin(world.catalog, "same", version="1.0.0")
    world.host.catalog.remote_urls.append(url)
    world.host.catalog.refresh_remote(force=True)
    assert world.host.catalog.entries()["same"].manifest.version == "1.0.0"


def test_wrong_checksum_is_refused(world, remote, tmp_path):
    url, out = remote
    src = tmp_path / "src"
    write_plugin(src, "farplugin")
    build(src, out)
    index = json.loads((out / "index.json").read_text())
    index["plugins"][0]["sha256"] = "0" * 64
    (out / "index.json").write_text(json.dumps(index))
    world.host.catalog.remote_urls.append(url)
    world.host.catalog.refresh_remote(force=True)
    with pytest.raises(InstallError, match="checksum"):
        world.installer.install("farplugin")
    assert not (world.plugins / "farplugin").exists()


def test_unreachable_remote_is_reported_not_fatal(world):
    world.host.catalog.remote_urls.append("http://127.0.0.1:9/index.json")
    world.host.catalog.refresh_remote(force=True, timeout=1)
    assert world.host.catalog.errors
    assert world.host.catalog.entries() == {}


def test_zips_are_reproducible(tmp_path):
    src = tmp_path / "src"
    write_plugin(src, "repro")
    build(src, tmp_path / "a")
    build(src, tmp_path / "b")
    assert (tmp_path / "a" / "repro-1.0.0.zip").read_bytes() == (tmp_path / "b" / "repro-1.0.0.zip").read_bytes()


@pytest.mark.parametrize("member", ["../evil.txt", "/abs.txt", "a/../../evil.txt"])
def test_unsafe_zip_paths_are_refused(tmp_path, member):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("plugin.json", "{}")
        zf.writestr(member, "x")
    with pytest.raises(InstallError, match="unsafe"):
        safe_extract(z, tmp_path / "dest")
    assert not (tmp_path / "evil.txt").exists()


def test_zip_with_one_top_folder_is_accepted(tmp_path):
    z = tmp_path / "ok.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("thing/plugin.json", "{}")
        zf.writestr("thing/plugin.py", "x = 1")
    safe_extract(z, tmp_path / "dest")
    assert (tmp_path / "dest" / "plugin.py").exists()


def test_zip_without_manifest_is_refused(tmp_path):
    z = tmp_path / "no.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(InstallError, match="plugin.json"):
        safe_extract(z, tmp_path / "dest")
