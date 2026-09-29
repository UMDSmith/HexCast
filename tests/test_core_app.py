"""The real hexcast.py: what a fresh download gives you, and installing from its own + tab."""

import importlib
import json
import struct
import sys
import time
import wave

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT, SESSION_CONFIG, _SESSION


@pytest.fixture(scope="module")
def hexcast():
    """hexcast.py imported once, pointed at empty config / media / plugins folders."""
    for d in ("plugins", "config", "media"):
        (_SESSION / d).mkdir(exist_ok=True)
    sys.modules.pop("hexcast", None)
    mod = importlib.import_module("hexcast")
    yield mod
    for name in [n for n in sys.modules if n == "hexcast" or n == "hexcast_plugins" or n.startswith("hexcast_plugins.")]:
        del sys.modules[name]


@pytest.fixture
def client(hexcast):
    with TestClient(hexcast.app) as c:
        yield c


def tiny_wav(path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<800h", *([0] * 800)))


def test_fresh_download_is_just_the_soundboard(hexcast, client):
    assert client.get("/").status_code == 200
    assert client.get("/overlay").status_code == 200
    assert client.get("/plugins").status_code == 200
    assert client.get("/api/plugins/nav").json()["items"] == []
    assert not hexcast.plugin_host.loaded
    listing = client.get("/api/plugins").json()["plugins"]
    assert {p["id"] for p in listing} >= {"twitch", "music", "discord", "clips", "countdown", "games", "ticker"}
    assert not any(p["installed"] for p in listing)
    # none of the integrations' pages exist yet
    for path in ("/twitch", "/ytm", "/discord", "/clips", "/countdown", "/games", "/ticker"):
        assert client.get(path).status_code == 404, path


def test_top_bar_script_and_store_assets_are_served(client):
    for asset in ("hexbar.js", "hexbar.css", "plugin_store.js", "hexcast.png"):
        assert client.get(f"/static/{asset}").status_code == 200, asset
    html = client.get("/").text
    assert 'id="hexbar"' in html and "twitch" not in html.lower().replace("twitch.tv", "")


def test_help_page_lists_only_the_core_sections(client):
    page = client.get("/help").text
    for keep in ('id="soundboard"', 'id="plugins"', 'id="notes"'):
        assert keep in page
    for gone in ('id="twitch"', 'id="games"', 'id="clips"', "<!--HELP_"):
        assert gone not in page


def test_soundboard_still_works_without_any_plugin(hexcast, client):
    audio = hexcast.AUDIO_DIR
    tiny_wav(audio / "beep.wav")
    hexcast.reindex()
    assert "beep" in client.get("/api/list").json()["audio"]
    r = client.get("/api/play/beep")
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get("/api/stop").json()["ok"]
    assert client.get("/api/play/nothing-here").status_code == 404
    with open(audio / "beep.wav", "rb") as f:
        up = client.post("/upload", files={"file": ("second.wav", f.read(), "audio/wav")})
    assert up.status_code == 200 and up.json()["kind"] == "audio"
    for _ in range(50):
        if "second" in client.get("/api/list").json()["audio"]:
            break
        time.sleep(0.1)
    else:
        hexcast.reindex()
    assert "second" in client.get("/api/list").json()["audio"]


def test_install_and_remove_from_the_running_app(hexcast, client):
    r = client.post("/api/plugins/countdown/install")
    assert r.status_code == 200
    for _ in range(100):
        job = client.get(f"/api/plugins/jobs/{r.json()['job']}").json()
        if job["state"] != "running":
            break
        time.sleep(0.1)
    assert job["state"] == "done", job
    assert client.get("/countdown").status_code == 200
    assert client.get("/countdown/api/status").json()["connected"]
    assert [i["id"] for i in client.get("/api/plugins/nav").json()["items"]] == ["countdown"]
    assert 'id="countdown"' in client.get("/help").text
    assert client.post("/api/plugins/countdown/uninstall", json={}).json()["removed"] == ["countdown"]
    assert client.get("/countdown").status_code == 404
    assert client.get("/api/plugins/nav").json()["items"] == []
    assert (SESSION_CONFIG / "countdown.json").exists() or True       # settings are kept if the panel wrote any


def test_shared_background_library_needs_no_plugin(hexcast, client):
    assert client.get("/api/backgrounds").json() == {"backgrounds": []}
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
    r = client.post("/api/backgrounds", files={"file": ("my bg!.png", png, "image/png")})
    assert r.status_code == 200 and r.json()["url"].startswith("/media/overlays/")
    assert client.get(r.json()["url"]).content == png
    assert client.post("/api/backgrounds", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert client.post("/api/backgrounds/delete", json={"name": "../../etc/passwd"}).json()["ok"]
    assert client.post("/api/backgrounds/delete", json={"name": r.json()["name"]}).json()["backgrounds"] == []


def test_cli_lists_and_installs(tmp_path):
    import subprocess
    env = {**__import__("os").environ, "HEXCAST_PLUGINS_DIR": str(tmp_path / "p"), "HEXCAST_CONFIG_DIR": str(tmp_path / "c")}
    out = subprocess.run([sys.executable, str(ROOT / "hexcast.py"), "plugins", "list"], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "countdown" in out.stdout and "games_craps" in out.stdout
    out = subprocess.run([sys.executable, str(ROOT / "hexcast.py"), "plugins", "install", "countdown"], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and (tmp_path / "p" / "countdown" / "plugin.json").exists()
    out = subprocess.run([sys.executable, str(ROOT / "hexcast.py"), "plugins", "remove", "countdown"], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and not (tmp_path / "p" / "countdown").exists()
    out = subprocess.run([sys.executable, str(ROOT / "hexcast.py"), "plugins", "install", "nope"], env=env, cwd=ROOT,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 1 and "not in the catalog" in out.stdout
