"""Clips (the clips plugin): renaming a queue item - the title the user gave, kept apart from yt-dlp's.

Driven through the real plugin host like the other plugin tests. Entries are added with add_links (no
yt-dlp, no network) and their 'resolved' metadata is set by hand."""

import importlib
import json
import re
import shutil
import subprocess
from pathlib import Path

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
    assert manifest["version"] == "1.1.0"


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
