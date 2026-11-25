"""Clips (the clips plugin): renaming a queue item - the title the user gave, kept apart from yt-dlp's.

Driven through the real plugin host like the other plugin tests. Entries are added with add_links (no
yt-dlp, no network) and their 'resolved' metadata is set by hand."""

import asyncio
import importlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "catalog" / "clips" / "static"


@pytest.fixture
def world(real_world):
    real_world.installer.install("clips")
    real_world.host.load_all()
    assert "clips" in real_world.host.loaded, real_world.host.errors
    yield real_world
    # clips.json is a legacy config: left in the shared test config folder it would make a later test's
    # Hexcast (test_core_app's "fresh download") install Clips on its own
    (real_world.config / "clips.json").unlink(missing_ok=True)


@pytest.fixture
def clips(world):
    return importlib.import_module("hexcast_plugins.clips.clips")


@pytest.fixture
def api(world):
    return TestClient(world.app)


@pytest.fixture
def entry(clips):
    """One queued clip whose yt-dlp title has arrived."""
    added, _ = clips.add_links(clips.extract_links("https://clips.twitch.tv/SomeSlug"), "test")
    e = added[0]
    e["title"] = "Original title"
    return e


def rename(api, ref, title):
    return api.post("/clips/api/title", json={"ref": ref, "title": title})


def queued(api):
    return api.get("/clips/api/queue").json()["queue"]


def test_the_manifest_version_was_bumped():
    manifest = json.loads((ROOT / "catalog" / "clips" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.2.0"


def test_a_rename_is_what_the_queue_and_the_player_show(api, clips, entry):
    d = rename(api, entry["id"], "  My   own\ttitle ").json()
    assert d["ok"] and d["entry"]["title"] == "My own title"          # whitespace collapsed
    assert d["entry"]["original_title"] == "Original title"
    (row,) = queued(api)
    assert row["title"] == "My own title" and row["original_title"] == "Original title"
    assert row["custom_title"] == "My own title"
    clips.PLAYER.update(state="playing", item_id=entry["id"])
    assert api.get("/clips/api/status").json()["player"]["item"]["title"] == "My own title"
    assert entry["title"] == "Original title"                          # yt-dlp's title is never touched


def test_an_item_can_be_renamed_by_number_or_slug_too(api, entry):
    assert rename(api, str(entry["num"]), "By number").json()["entry"]["title"] == "By number"
    assert rename(api, entry["slug"], "By slug").json()["entry"]["title"] == "By slug"
    assert rename(api, "next", "By next").json()["entry"]["title"] == "By next"


def test_an_empty_title_or_the_original_one_removes_the_rename(api, entry):
    rename(api, entry["id"], "Mine")
    d = rename(api, entry["id"], "   ").json()
    assert d["ok"] and d["entry"]["title"] == "Original title"
    assert "custom_title" not in d["entry"] and "original_title" not in d["entry"]
    rename(api, entry["id"], "Mine")
    d = rename(api, entry["id"], "Original title").json()               # typing the original back is a reset
    assert "custom_title" not in d["entry"] and d["entry"]["title"] == "Original title"
    assert "custom_title" not in entry


def test_a_rename_survives_a_re_resolve(api, clips, entry):
    rename(api, entry["id"], "Mine")
    clips._apply_info(entry, {"title": "A fresh yt-dlp title", "channel": "somebody"})
    assert entry["title"] == "A fresh yt-dlp title"
    (row,) = queued(api)
    assert row["title"] == "Mine" and row["original_title"] == "A fresh yt-dlp title"
    rename(api, entry["id"], "")
    assert queued(api)[0]["title"] == "A fresh yt-dlp title"


def test_a_clip_can_be_renamed_before_it_has_resolved(api, clips):
    added, _ = clips.add_links(clips.extract_links("https://clips.twitch.tv/NotResolvedYet"), "test")
    e = added[0]
    assert e["title"] == ""
    assert rename(api, e["id"], "Mine").json()["entry"]["title"] == "Mine"
    clips._apply_info(e, {"title": "Resolved"})
    assert queued(api)[0]["title"] == "Mine"


def test_a_rename_is_saved_and_loaded_back(api, clips, entry):
    rename(api, entry["id"], "Kept")
    saved = json.loads(clips.STORE_PATH.read_text(encoding="utf-8"))
    assert saved["queue"][0]["custom_title"] == "Kept" and saved["queue"][0]["title"] == "Original title"
    assert clips.load_store()["queue"][0]["custom_title"] == "Kept"
    rename(api, entry["id"], "")
    assert "custom_title" not in json.loads(clips.STORE_PATH.read_text(encoding="utf-8"))["queue"][0]


def test_a_title_is_capped(api, clips, entry):
    d = rename(api, entry["id"], "x" * 5000).json()
    assert d["ok"] and len(d["entry"]["title"]) == clips.TITLE_MAX


def test_renaming_something_that_is_not_there_is_a_404(api, entry):
    r = rename(api, "nope", "Mine")
    assert r.status_code == 404 and r.json() == {"ok": False, "error": "no such item"}
    assert rename(api, "", "Mine").status_code == 404
    assert "custom_title" not in queued(api)[0]


def test_renaming_the_playing_item_updates_the_overlay_live(api, clips, entry, monkeypatch):
    sent = []

    async def to_overlay(msg):
        sent.append(msg)

    monkeypatch.setattr(clips.HUB, "to_overlay", to_overlay)
    rename(api, entry["id"], "Not playing yet")
    assert sent == []                                                  # nothing on the overlay to update
    clips.PLAYER.update(state="playing", item_id=entry["id"])
    rename(api, entry["id"], "Live title")
    assert [m["type"] for m in sent] == ["item"]
    assert sent[0]["item"]["id"] == entry["id"] and sent[0]["item"]["title"] == "Live title"


def test_public_entry_and_entry_title_agree_on_the_effective_title(clips, entry):
    assert clips.entry_title(entry) == clips.public_entry(entry)["title"] == "Original title"
    entry["custom_title"] = "Shown on the overlay"                      # what play_entry sends the overlay
    assert clips.entry_title(entry) == clips.public_entry(entry)["title"] == "Shown on the overlay"


# ---------------------------------------------------------------- a playback error only moves that overlay to the embed

class Overlay:
    """A connected overlay socket: records what the server sends it."""

    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


@pytest.fixture
def playing(clips, entry, monkeypatch):
    """The entry playing directly (an MP4) on two overlays: OBS and a browser tab."""
    obs, tab = Overlay(), Overlay()
    monkeypatch.setattr(clips.HUB, "overlay", {obs, tab})
    monkeypatch.setattr(clips, "GAVE_UP", set())
    entry["duration"] = 26.0
    clips.PLAYER.update(state="playing", item_id=entry["id"], mode="mp4")
    yield obs, tab
    clips._cancel_iframe_timer()
    clips.PLAYER.update(state="idle", item_id="", mode="")


def error(clips, entry, ws):
    asyncio.run(clips._handle_overlay_msg({"type": "error", "id": entry["id"], "message": "media error 4"}, ws))


def test_an_overlay_that_cannot_play_a_clip_gets_the_embed_alone(clips, entry, playing):
    obs, tab = playing
    error(clips, entry, tab)
    assert [(m["type"], m["src"]["mode"]) for m in tab.sent] == [("play", "iframe")]
    assert obs.sent == []                                               # OBS keeps the direct video
    assert clips.PLAYER["mode"] == "mp4" and clips._IFRAME_TIMER is None  # OBS reports progress and the end
    assert entry["error"] == "media error 4"
    error(clips, entry, tab)                                            # it can only give up once
    assert len(tab.sent) == 1
    asyncio.run(clips._handle_overlay_msg({"type": "ended", "id": entry["id"]}, obs))
    assert tab.sent[-1] == {"type": "stop"} and obs.sent == []          # the embed can't tell it ended
    assert clips.PLAYER["state"] == "idle" and entry["status"] == "played" and not clips.GAVE_UP


def test_the_item_goes_to_the_embed_when_no_overlay_can_play_it(clips, entry, playing):
    obs, tab = playing

    async def both_fail():
        await clips._handle_overlay_msg({"type": "error", "id": entry["id"], "message": "a"}, tab)
        await clips._handle_overlay_msg({"type": "error", "id": entry["id"], "message": "b"}, obs)
        timer = clips._IFRAME_TIMER
        clips._cancel_iframe_timer()
        return timer

    assert asyncio.run(both_fail()) is not None                         # the embed's end is timed
    assert clips.PLAYER["mode"] == "iframe" and clips.PLAYER["state"] == "playing"
    assert [m["type"] for m in tab.sent] == [m["type"] for m in obs.sent] == ["play"]   # one embed each


def test_over_the_overlay_socket_only_the_failing_overlay_gets_the_embed(api, clips, entry, monkeypatch):
    async def build_source(e):
        return {"mode": "mp4", "url": "https://cdn.example/clip.mp4", "start": 0.0}

    monkeypatch.setattr(clips, "build_source", build_source)
    monkeypatch.setattr(clips.STATE, "started", True)                  # no yt-dlp startup (network)
    entry["duration"] = 26.0
    with api.websocket_connect("/clips/ws/overlay") as obs, api.websocket_connect("/clips/ws/overlay") as tab:
        assert obs.receive_json()["type"] == tab.receive_json()["type"] == "config"
        assert api.post(f"/clips/api/play/{entry['id']}").json()["ok"]
        assert obs.receive_json()["src"]["mode"] == tab.receive_json()["src"]["mode"] == "mp4"
        tab.send_json({"type": "error", "id": entry["id"], "message": "media error 4"})
        assert tab.receive_json()["src"]["mode"] == "iframe"
        assert api.post("/clips/api/pause").json()["ok"]                # still a direct video, so it pauses
        assert obs.receive_json() == {"type": "pause"}                  # and OBS got nothing before it
        assert tab.receive_json() == {"type": "pause"}
        obs.send_json({"type": "ended", "id": entry["id"]})
        assert tab.receive_json() == {"type": "stop"}
    assert clips.PLAYER["state"] == "idle" and not clips.GAVE_UP


def test_without_the_embed_only_the_failing_overlay_stops(clips, entry, playing, monkeypatch):
    obs, tab = playing
    monkeypatch.setitem(clips.settings(), "iframe_fallback", False)
    error(clips, entry, tab)
    assert tab.sent == [{"type": "stop"}] and obs.sent == [] and clips.PLAYER["state"] == "playing"
    error(clips, entry, obs)
    assert clips.PLAYER["state"] == "idle" and obs.sent == [{"type": "stop"}, {"type": "stop"}]


# ---------------------------------------------------------------- HEVC clips: play the H.264 copy instead

CDN = "https://cdn.example/nauth/abc"
QUERY = "sig=f00&token=" + quote(json.dumps({"clip_uri": CDN + "/landscape/h264/720/index.mp4"}))


def hevc_info():
    """What yt-dlp now returns for a Twitch clip: HEVC links only, portrait copies first (seen 2026-10)."""
    fmts = [{"format_id": f"{side}{h}", "height": h, "ext": "mp4", "protocol": "https",
             "url": f"{CDN}/{folder}/hevc/{q}/index.mp4?{QUERY}"}
            for side, folder in (("portrait-", "portrait"), ("", "landscape"))
            for h, q in ((720, "720"), (1080, "source"))]
    return {"title": "A clip", "formats": fmts}


def h264(folder):
    return f"{CDN}/landscape/h264/{folder}/index.mp4?{QUERY}"


def test_the_h264_copies_of_an_hevc_clip_are_tried_best_first(clips):
    # 1080's HEVC folder is "source", which has no H.264 twin; the token's 720p link is already in the list
    assert clips._h264_twins(hevc_info()) == [h264("1080"), h264("source"), h264("720")]


def test_an_hevc_clip_plays_its_h264_copy(clips, entry, monkeypatch):
    tried = []

    async def reachable(url):
        tried.append(url)
        return url == h264("1080")

    monkeypatch.setattr(clips, "_reachable", reachable)
    info = hevc_info()
    clips._apply_info(entry, info)
    assert "/hevc/source/" in clips._SRC_CACHE[entry["id"]][0]
    asyncio.run(clips._prefer_h264(entry, info))
    assert clips._SRC_CACHE[entry["id"]][:2] == (h264("1080"), "mp4") and tried == [h264("1080")]


def test_an_hevc_clip_without_an_h264_copy_keeps_hevc_and_an_h264_clip_is_left_alone(clips, entry, monkeypatch):
    tried = []

    async def reachable(url):
        tried.append(url)
        return False

    monkeypatch.setattr(clips, "_reachable", reachable)
    info = hevc_info()
    clips._apply_info(entry, info)
    asyncio.run(clips._prefer_h264(entry, info))
    assert "/hevc/source/" in clips._SRC_CACHE[entry["id"]][0] and len(tried) == 3
    tried.clear()
    old = {"formats": [{"format_id": "1080", "height": 1080, "ext": "mp4", "protocol": "https",
                        "url": "https://cdn.example/nauth/old/landscape/avc/1080/index.mp4"}]}
    clips._apply_info(entry, old)
    asyncio.run(clips._prefer_h264(entry, old))
    assert "/avc/1080/" in clips._SRC_CACHE[entry["id"]][0] and tried == []


def test_a_pre_download_fetches_the_h264_copy(clips, entry, monkeypatch):
    calls = []

    async def ytdlp(*args, **kw):
        calls.append(args)
        return b""

    monkeypatch.setattr(clips, "_ytdlp", ytdlp)
    clips._SRC_CACHE[entry["id"]] = (h264("1080"), "mp4", 0.0)
    asyncio.run(clips.download_clip(entry))
    assert calls[0][-1] == h264("1080") and "-f" not in calls[0]


def test_the_overlay_reports_sound_without_a_picture(clips):
    # OBS without an HEVC decoder plays the sound alone and raises no error; the overlay must say so
    html = (STATIC / "clips_overlay.html").read_text(encoding="utf-8")
    check = html[html.index("addEventListener('loadedmetadata', function(){\n      if (!v.videoWidth"):]
    assert "fail(id" in check.split("\n")[1] and "kind !== 'media'" in html


# ---------------------------------------------------------------- shoutouts wait their turn

@pytest.fixture
def shout(clips, monkeypatch):
    """Shoutouts with no yt-dlp: every channel has clips, every clip plays. One overlay is connected."""
    async def random_clips(channel, count=1):
        return [f"https://clips.twitch.tv/{channel}Clip{i}" for i in range(count)]

    async def build_source(e):
        return {"mode": "mp4", "url": "https://cdn.example/" + e["slug"] + ".mp4", "start": 0.0}

    obs = Overlay()
    monkeypatch.setattr(clips, "_random_channel_clips", random_clips)
    monkeypatch.setattr(clips, "build_source", build_source)
    monkeypatch.setattr(clips.STATE, "started", True)                  # no yt-dlp startup (network)
    monkeypatch.setattr(clips.HUB, "overlay", {obs})
    monkeypatch.setattr(clips, "_SO_LOCK", asyncio.Lock())
    monkeypatch.setitem(clips.settings(), "level_enabled", False)
    monkeypatch.setattr(clips, "SHOUTOUTS", [])
    monkeypatch.setitem(clips.STORE, "shoutout_counts", {})
    yield obs
    clips._drop_pending_shoutouts()
    clips.EPHEMERAL.clear()
    clips.PLAYER.update(state="idle", item_id="", mode="")


def played(obs):
    return [m["item"]["slug"] for m in obs.sent if m["type"] == "play"]


async def end_current(clips, obs):
    await clips._handle_overlay_msg({"type": "ended", "id": clips.PLAYER["item_id"]}, obs)
    await asyncio.sleep(0)                                              # the next clip is started by a task
    await asyncio.sleep(0)


def test_a_second_shoutout_waits_for_the_first_instead_of_cutting_it_off(clips, shout):
    async def go():
        a = await clips.play_shoutout("alice", 2)
        b = await clips.play_shoutout("bob", 2)
        c = await clips.play_shoutout("@Alice", 2)                      # another mod, same channel
        assert a["ok"] and not a["queued"] and b["ok"] and b["queued"] and b["ahead"] == 2
        assert c["ok"] and c["already"] and c["clips"] == []
        status = clips.status_snapshot()
        assert played(shout) == ["aliceClip0"] and status["shoutout_clips_waiting"] == 3
        assert status["shoutouts_waiting"] == 1                         # bob; alice is on screen
        for _ in range(3):
            await end_current(clips, shout)
        assert played(shout) == ["aliceClip0", "aliceClip1", "bobClip0", "bobClip1"]
        await end_current(clips, shout)
        assert clips.PLAYER["state"] == "idle" and not clips.SHOUTOUT_PENDING and not clips.EPHEMERAL

    asyncio.run(go())


def test_shoutouts_typed_at_once_keep_their_order(clips, shout, monkeypatch):
    async def slow_then_fast(channel, count=1):
        await asyncio.sleep(0.05 if channel == "alice" else 0)
        return [f"https://clips.twitch.tv/{channel}Clip{i}" for i in range(count)]

    monkeypatch.setattr(clips, "_random_channel_clips", slow_then_fast)

    async def go():
        a, b = await asyncio.gather(clips.play_shoutout("alice", 2), clips.play_shoutout("bob", 1))
        assert not a["queued"] and b["queued"]
        for _ in range(2):
            await end_current(clips, shout)
        assert played(shout) == ["aliceClip0", "aliceClip1", "bobClip0"]

    asyncio.run(go())


def test_stop_and_a_manual_play_clear_the_waiting_shoutouts(clips, shout, entry):
    async def go():
        await clips.play_shoutout("alice", 2)
        await clips.play_shoutout("bob", 2)
        await clips.stop_playback()
        assert not clips.SHOUTOUT_PENDING and not clips.EPHEMERAL and clips.PLAYER["state"] == "idle"
        d = await clips.play_shoutout("carol", 1)                       # nothing to wait behind any more
        assert not d["queued"] and played(shout) == ["aliceClip0", "carolClip0"]

    asyncio.run(go())


def test_a_shoutout_does_not_wait_behind_one_that_nothing_will_end(clips, shout, monkeypatch):
    async def go():
        await clips.play_shoutout("alice", 1)
        monkeypatch.setattr(clips, "_SO_STARTED", clips._SO_STARTED - 60 - clips.SO_STUCK_GRACE - 1)
        d = await clips.play_shoutout("bob", 1)                         # the overlay never said alice's clip ended
        assert not d["queued"] and played(shout) == ["aliceClip0", "bobClip0"]
        monkeypatch.setattr(clips.HUB, "overlay", set())                # and with no overlay connected at all
        d = await clips.play_shoutout("carol", 1)
        assert not d["queued"] and clips.PLAYER["item_id"] in clips.EPHEMERAL
        assert clips.EPHEMERAL[clips.PLAYER["item_id"]]["channel"] == "carol"

    asyncio.run(go())


def test_a_waiting_clip_that_cannot_play_is_skipped(clips, shout, monkeypatch):
    async def build_source(e):
        if e["slug"] == "aliceClip1":
            raise RuntimeError("no playable format found")
        return {"mode": "mp4", "url": "https://cdn.example/x.mp4", "start": 0.0}

    monkeypatch.setattr(clips, "build_source", build_source)
    monkeypatch.setitem(clips.settings(), "iframe_fallback", False)

    async def go():
        await clips.play_shoutout("alice", 2)
        await clips.play_shoutout("bob", 1)
        await end_current(clips, shout)
        assert played(shout) == ["aliceClip0", "bobClip0"]              # the line did not stall on the bad clip

    asyncio.run(go())


def rows(clips):
    return [(s["channel"], s["state"], s["started"], s["left"], s["count"]) for s in clips.shoutouts_snapshot()["shoutouts"]]


def test_the_shoutout_list_follows_the_line_and_counts_each_channel(clips, shout, api):
    async def go():
        await clips.play_shoutout("alice", 2)
        await clips.play_shoutout("bob", 1)
        await clips.play_shoutout("alice", 2)                           # a repeat while it plays: no row, no count
        assert rows(clips) == [("alice", "playing", 1, 1, 1), ("bob", "waiting", 0, 1, 0)]
        await end_current(clips, shout)
        await end_current(clips, shout)
        assert rows(clips) == [("alice", "played", 2, 0, 1), ("bob", "playing", 1, 0, 1)]
        await end_current(clips, shout)
        await clips.play_shoutout("alice", 1)                           # shouted out again later: counted again
        assert rows(clips)[-1] == ("alice", "playing", 1, 0, 2)
        assert clips.shoutouts_snapshot()["counts"] == [["alice", 2], ["bob", 1]]
        assert shout.sent[-1]["type"] == "play"

    asyncio.run(go())
    saved = json.loads(clips.STORE_PATH.read_text(encoding="utf-8"))
    assert saved["shoutout_counts"] == {"alice": 2, "bob": 1}           # the counts outlive a restart
    assert clips.load_store()["shoutout_counts"] == {"alice": 2, "bob": 1}
    assert api.get("/clips/api/shoutouts").json()["counts"] == [["alice", 2], ["bob", 1]]


def test_a_cleared_shoutout_is_listed_as_not_played_and_not_counted(clips, shout):
    async def go():
        await clips.play_shoutout("alice", 2)
        await clips.play_shoutout("bob", 1)
        await clips.stop_playback()
        assert rows(clips) == [("alice", "played", 1, 0, 1), ("bob", "skipped", 0, 0, 0)]

    asyncio.run(go())


def test_a_waiting_shoutout_can_be_removed_and_the_list_cleared(clips, shout):
    async def go():
        await clips.play_shoutout("alice", 2)
        await clips.play_shoutout("bob", 1)
        await clips.play_shoutout("carol", 1)
        bob = clips.shoutouts_snapshot()["shoutouts"][1]["id"]
        assert (await clips.api_shoutout_remove(bob))["ok"]
        assert [r[0] for r in rows(clips)] == ["alice", "carol"]
        for _ in range(2):
            await end_current(clips, shout)
        assert played(shout) == ["aliceClip0", "aliceClip1", "carolClip0"]

    asyncio.run(go())


def test_clearing_keeps_the_line_and_only_zeroes_counts_when_asked(clips, shout, api):
    async def go():
        await clips.play_shoutout("alice", 1)
        await end_current(clips, shout)
        await clips.play_shoutout("bob", 1)
        await clips.play_shoutout("carol", 1)

    asyncio.run(go())
    assert api.post("/clips/api/shoutouts/clear").json()["ok"]
    assert [r[:2] for r in rows(clips)] == [("bob", "playing"), ("carol", "waiting")]
    assert clips.STORE["shoutout_counts"] == {"alice": 1, "bob": 1}
    assert api.post("/clips/api/shoutouts/clear", json={"counts": True}).json()["ok"]
    assert clips.STORE["shoutout_counts"] == {} and len(rows(clips)) == 2
    assert api.post("/clips/api/shoutouts/remove/nope").status_code == 404


def test_the_page_lists_the_shoutouts():
    html = (STATIC / "clips_panel.html").read_text(encoding="utf-8")
    assert 'id="solist"' in html and "m.type==='shoutouts'" in html and "/clips/api/shoutouts/clear" in html


# ---------------------------------------------------------------- the browser scripts (checked with node when it is installed)

@pytest.mark.parametrize("page", ["clips_panel.html", "clips_overlay.html"])
def test_the_page_scripts_are_valid_javascript(page, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    html = (STATIC / page).read_text(encoding="utf-8")
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert scripts, page
    js = tmp_path / (page + ".js")
    js.write_text("\n".join(scripts), encoding="utf-8")
    r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
