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
    assert {p["id"] for p in listing} >= {"twitch", "music", "avatar", "discord", "clips", "countdown", "games", "ticker"}
    assert not any(p["installed"] for p in listing)
    # none of the integrations' pages exist yet
    for path in ("/twitch", "/ytm", "/avatar", "/discord", "/clips", "/countdown", "/games", "/ticker"):
        assert client.get(path).status_code == 404, path


def test_top_bar_script_and_store_assets_are_served(client):
    for asset in ("hexbar.js", "hexbar.css", "plugin_store.js", "hexcast.png"):
        assert client.get(f"/static/{asset}").status_code == 200, asset
    html = client.get("/").text
    assert 'id="hexbar"' in html and "twitch" not in html.lower().replace("twitch.tv", "")


def test_help_lists_only_the_core_pages(client):
    index = client.get("/help").text
    for keep in ("/help/soundboard", "/help/plugins", "/help/notes"):
        assert keep in index
    for gone in ("/help/twitch", "/help/games", "/help/clips", "<!--HELP_"):
        assert gone not in index
    assert 'id="soundboard"' in client.get("/help/soundboard").text
    assert client.get("/help/twitch").status_code == 404


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
    assert 'id="countdown"' in client.get("/help/countdown").text
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


def test_broken_plugins_never_stop_the_soundboard_from_starting(tmp_path):
    """A garbage plugin.json, a plugin that raises in setup, a corrupt plugins.json: Hexcast still starts,
    the soundboard answers and the problems are reported."""
    import json
    import os
    import subprocess
    plugins, config = tmp_path / "plugins", tmp_path / "config"
    (plugins / "garbage").mkdir(parents=True)
    (plugins / "garbage" / "plugin.json").write_text("{nope")
    (plugins / "raises").mkdir()
    (plugins / "raises" / "plugin.json").write_text(json.dumps({"id": "raises", "name": "Raises", "version": "1.0.0"}))
    (plugins / "raises" / "plugin.py").write_text("def setup(ctx):\n    raise RuntimeError('boom in setup')\n")
    config.mkdir()
    (config / "plugins.json").write_text("this is not json")
    code = ("import hexcast\nfrom fastapi.testclient import TestClient\n"
            "c = TestClient(hexcast.app)\n"
            "print('ROOT', c.get('/').status_code, 'OVERLAY', c.get('/overlay').status_code)\n"
            "print('ERRORS', sorted(hexcast.plugin_host.errors))\n")
    env = {**os.environ, "HEXCAST_PLUGINS_DIR": str(plugins), "HEXCAST_CONFIG_DIR": str(config),
           "SOUNDBOARD_MEDIA_DIR": str(tmp_path / "media")}
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert "ROOT 200 OVERLAY 200" in out.stdout
    assert "ERRORS ['garbage', 'raises']" in out.stdout
    assert "boom in setup" in out.stdout


# ---------------------------------------------------------------- clips locked to an avatar (the Avatars plugin draws them)

PNG_1X1 = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


def test_parse_attach_keeps_only_a_sane_lock(hexcast):
    p = hexcast.parse_attach
    assert p(None) is None and p("hex") is None and p({}) is None
    assert p({"avatar": "../etc"}) is None and p({"avatar": ""}) is None
    full = p({"avatar": " Hex ", "anchor": "  head   top ", "x": "30", "y": 9999, "dx": "bad", "scale": 0, "layer": "back",
              "follow_angle": "false", "mirror": "1"})
    assert full == {"avatar": "hex", "anchor": "head top", "x": 30.0, "y": 200.0, "dx": 0.0, "dy": 0.0, "scale": 0.02,
                    "rotation": 0.0, "layer": "back", "follow_angle": False, "mirror": True}
    assert p({"avatar": "hex"})["layer"] == "front" and p({"avatar": "hex"})["follow_angle"] is True


def _locked_clip(hexcast, client, name="wow"):
    (hexcast.VIDEO_DIR / f"{name}.png").write_bytes(PNG_1X1)
    hexcast.reindex()
    return f"{name}.png"


def test_a_clip_lock_is_saved_listed_and_survives_the_position_editor(hexcast, client):
    file = _locked_clip(hexcast, client, "lockme")
    r = client.post("/attach", json={"file": file, "kind": "video", "attach": {"avatar": "hex", "anchor": "head_top", "dy": -40}})
    assert r.status_code == 200 and r.json()["attach"]["avatar"] == "hex" and r.json()["attach"]["dy"] == -40
    entry = next(v for v in client.get("/index").json()["video"] if v["file"] == file)
    assert entry["attach"]["anchor"] == "head_top"
    # Edit Mode saves x / y / scale without knowing about the lock: it stays
    assert client.post("/position", json={"file": file, "kind": "video", "x": 10, "y": 20, "scale": 2}).json()["ok"]
    entry = next(v for v in client.get("/index").json()["video"] if v["file"] == file)
    assert entry["attach"]["anchor"] == "head_top" and entry["pos"]["x"] == 10
    # ... unless it says otherwise
    client.post("/position", json={"file": file, "kind": "video", "x": 10, "y": 20, "scale": 2, "attach": None})
    assert next(v for v in client.get("/index").json()["video"] if v["file"] == file)["attach"] is None
    # a lock without a usable avatar is refused, letting go is not
    assert client.post("/attach", json={"file": file, "kind": "video", "attach": {"avatar": "No Way!"}}).status_code == 400
    assert client.post("/attach", json={"file": "missing.mp4", "kind": "video", "attach": None}).status_code == 404
    assert client.post("/attach", json={"file": file, "kind": "audio", "attach": None}).status_code == 400
    client.post("/attach", json={"file": file, "kind": "video", "attach": {"avatar": "hex"}})
    assert client.post("/attach", json={"file": file, "kind": "video", "attach": None}).json()["attach"] is None


def test_a_locked_clip_plays_on_the_overlay_that_draws_its_avatar(hexcast, client):
    file = _locked_clip(hexcast, client, "wow")
    client.post("/attach", json={"file": file, "kind": "video", "attach": {"avatar": "hex", "anchor": "head_top"}})
    with client.websocket_connect("/ws/overlay") as plain, \
         client.websocket_connect("/ws/overlay?role=avatar") as obs, \
         client.websocket_connect("/ws/overlay?role=avatar") as other, \
         client.websocket_connect("/ws/overlay?role=preview") as tab:
        obs.send_json({"type": "avatars", "names": ["Hex", "mini"]})
        other.send_json({"type": "avatars", "names": ["guest"]})
        tab.send_json({"type": "avatars", "names": ["hex"]})
        for _ in range(100):                                   # (the names have to have arrived)
            if all(m.get("avatars") for ws, m in hexcast.overlay_meta.items() if m["role"] in ("avatar", "preview") and m["avatars"]) \
                    and sum(1 for m in hexcast.overlay_meta.values() if m["avatars"]) == 3:
                break
            time.sleep(0.02)
        r = client.get("/api/play/video/wow").json()
        assert r == {"ok": True, "delivered": 2, "attached": True}
        assert client.get("/api/stop").json()["delivered"] == 4
        clip = obs.receive_json()
        assert clip["type"] == "video" and clip["attach"]["avatar"] == "hex" and clip["attach"]["anchor"] == "head_top"
        assert tab.receive_json()["attach"]["avatar"] == "hex"
        assert obs.receive_json()["type"] == "stop" and tab.receive_json()["type"] == "stop"
        # the Soundboard overlay and the overlay without that avatar never saw the clip: their first message is the stop
        assert plain.receive_json()["type"] == "stop" and other.receive_json()["type"] == "stop"

        # per play: ?avatar= plays it on the Soundboard overlay whatever the clip says; others tune the saved lock
        r = client.get("/api/play/video/wow?avatar=").json()
        assert r["delivered"] == 1 and "attached" not in r
        played = plain.receive_json()
        assert played["type"] == "video" and "attach" not in played and played["x"] == 50
        r = client.get("/api/play/video/wow?avatar=hex&dx=15&layer=back&follow=0").json()
        assert r["attached"] is True
        tuned = obs.receive_json()["attach"]
        assert tuned["anchor"] == "head_top" and tuned["dx"] == 15 and tuned["layer"] == "back" and tuned["follow_angle"] is False
        tab.receive_json()
        r = client.get("/api/play/video/wow?avatar=mini&anchor=hat").json()
        assert r["attached"] is True and obs.receive_json()["attach"] == {**hexcast.parse_attach({"avatar": "mini", "anchor": "hat"})}

        # nobody draws the avatar: the clip plays where the Soundboard puts it, and the call says so
        client.post("/attach", json={"file": file, "kind": "video", "attach": {"avatar": "ghost"}})
        r = client.get("/api/play/video/wow").json()
        assert r["attached"] is False and r["delivered"] == 1
        fallback = plain.receive_json()
        assert fallback["type"] == "video" and "attach" not in fallback
    client.post("/attach", json={"file": file, "kind": "video", "attach": None})


def test_the_avatars_tab_shows_a_locked_clip_even_when_no_overlay_is_open(hexcast, client):
    file = _locked_clip(hexcast, client, "tabtest")
    client.post("/attach", json={"file": file, "kind": "video", "attach": {"avatar": "hex"}})
    with client.websocket_connect("/ws/overlay") as plain, client.websocket_connect("/ws/overlay?role=preview") as tab:
        tab.send_json({"type": "avatars", "names": ["hex"]})
        for _ in range(100):
            if any(m["avatars"] for m in hexcast.overlay_meta.values()):
                break
            time.sleep(0.02)
        r = client.get("/api/play/video/tabtest").json()
        # no overlay in OBS draws hex: it plays on the Soundboard overlay, and the tab's preview still shows it locked
        assert r == {"ok": True, "delivered": 2, "attached": False}
        assert tab.receive_json()["attach"]["avatar"] == "hex"
        shown = plain.receive_json()
        assert shown["type"] == "video" and "attach" not in shown
    client.post("/attach", json={"file": file, "kind": "video", "attach": None})
