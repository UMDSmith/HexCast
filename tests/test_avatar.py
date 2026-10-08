"""The Avatars plugin: its library (models, items, VTube Studio settings) and its API - no browser."""

import asyncio
import base64
import io
import json
import re
import shutil
import struct
import subprocess
import time
import zipfile
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _png(w=8, h=6) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _model_zip(moc_version=3, vts=True, nested="My Model/runtime/", extra_rows=()) -> bytes:
    model3 = {"Version": 3, "FileReferences": {"Moc": "m.moc3", "Textures": ["m.2048/texture_00.png"],
                                               "Expressions": [{"Name": "Smile", "File": "smile.exp3.json"}],
                                               "Motions": {"Idle": [{"File": "motions/idle.motion3.json"}]}}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(nested + "m.model3.json", json.dumps(model3))
        z.writestr(nested + "m.moc3", b"MOC3" + bytes([moc_version]) + b"\x00" * 64)
        z.writestr(nested + "m.2048/texture_00.png", _png())
        z.writestr(nested + "smile.exp3.json", json.dumps({"Type": "Live2D Expression", "Parameters": [
            {"Id": "ParamMouthForm", "Value": 1, "Blend": "Add"}]}))
        z.writestr(nested + "angry.exp3.json", json.dumps({"Type": "Live2D Expression", "Parameters": []}))
        z.writestr(nested + "motions/idle.motion3.json", json.dumps({"Version": 3, "Curves": []}))
        z.writestr(nested + "extra/wave.motion3.json", json.dumps({"Version": 3, "Curves": []}))
        z.writestr("../evil.txt", b"outside")                       # must never land outside the model
        if vts:
            z.writestr(nested + "m.vtube.json", json.dumps({
                "Name": "Test Model",
                "FileReferences": {"Model": "m.model3.json", "IdleAnimation": "motions/idle.motion3.json"},
                "PhysicsSettings": {"Use": False},
                "ParameterSettings": [{"Name": "Mouth Open", "Input": "MouthOpen", "OutputLive2D": "ParamMouthOpenY",
                                       "InputRangeLower": 0, "InputRangeUpper": 1, "OutputRangeLower": 0,
                                       "OutputRangeUpper": 2.1, "Smoothing": 0}, *extra_rows],
                "Hotkeys": [{"Name": "Angry", "Action": "ToggleExpression", "File": "angry.exp3.json"}],
            }))
    return buf.getvalue()


@pytest.fixture
def av(real_world):
    real_world.installer.install("avatar")
    real_world.host.load_all()
    assert "avatar" in real_world.host.loaded, real_world.host.errors
    from hexcast_plugins.avatar import avatar, library
    import shutil
    shutil.rmtree(library.ROOT, ignore_errors=True)               # a fresh library for every test
    library.ensure_dirs()
    avatar.CONFIG.update({"avatars": [], "overlays": ["main"], "stage": avatar.norm_stage({})})
    avatar.LIVE.clear()
    c = TestClient(real_world.app)
    c.mod, c.lib = avatar, library
    yield c


def _upload(c, data=None, name="model.zip"):
    r = c.post("/avatar/api/models/upload", files={"file": (name, data or _model_zip(), "application/zip")})
    assert r.status_code == 200, r.text
    return r.json()["model"]


def test_status_and_pages(av):
    s = av.get("/avatar/api/status").json()
    assert s["connected"] is False and s["avatars"] == [] and s["runtime"] is False
    assert av.get("/avatar").status_code == 200
    assert "the Live2D runtime is not installed yet" in av.get("/avatar/overlay").text
    assert "routes" in av.get("/avatar/api").json()


def test_zip_import_keeps_the_model_folder_and_reads_vtube_studio(av):
    m = _upload(av)
    assert m["id"] == "test-model" and m["name"] == "Test Model" and m["ok"] and m["moc_version"] == 3
    folder = av.lib.MODELS_DIR / m["id"]
    assert (folder / "m.model3.json").is_file()
    assert not any(p.name == "evil.txt" for p in av.lib.ROOT.parent.rglob("evil.txt"))
    assert {e["name"] for e in m["expressions"]} == {"Smile", "angry"}       # declared + found in the folder
    assert {x["name"] for x in m["motions"]} == {"idle", "wave"}
    assert m["mappings_from"] == "vts" and m["hotkeys"][0]["action"] == "expression"
    st = av.lib.model_settings(m["id"])
    assert st["mappings"][0]["out"] == [0.0, 2.1] and st["physics"] is False
    eng = av.get(f"/avatar/api/models/{m['id']}/engine").json()
    assert eng["url"].endswith("/avatar/lib/models/test-model/m.model3.json")
    assert "Physics" not in eng["FileReferences"]
    assert {e["Name"] for e in eng["FileReferences"]["Expressions"]} == {"Smile", "angry"}
    assert eng["FileReferences"]["Motions"]["wave"] == [{"File": "extra/wave.motion3.json"}]


def test_model_without_vts_settings_gets_the_default_mappings(av):
    m = _upload(av, _model_zip(vts=False, nested=""), "plain.zip")
    assert m["mappings_from"] == "default"
    outs = {x["output"] for x in av.lib.model_settings(m["id"])["mappings"]}
    assert {"ParamAngleX", "ParamEyeLOpen", "ParamMouthOpenY", "ParamA", "ParamBreath"} <= outs


def test_cubism_53_models_are_refused_with_a_reason(av):
    m = _upload(av, _model_zip(moc_version=6), "new.zip")
    assert m["ok"] is False and "5.3" in m["problem"]


def test_not_a_model(av):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
    r = av.post("/avatar/api/models/upload", files={"file": ("x.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 400 and "model3.json" in r.json()["error"]


def test_import_refuses_other_web_pages(av):
    r = av.post("/avatar/api/models/import", json={"path": "C:/"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_avatars_and_commands(av):
    m = _upload(av)
    r = av.post("/avatar/api/avatars", json={"name": "Main!", "model": m["id"]})
    assert r.status_code == 400
    r = av.post("/avatar/api/avatars", json={"name": "main", "model": m["id"]})
    assert r.status_code == 200
    a = r.json()["avatar"]
    assert a["emotions"]["angry"]["expressions"] == ["angry"]              # guessed from the expression name
    assert av.post("/avatar/api/avatars", json={"name": "main"}).status_code == 409

    ok = av.post("/avatar/api/avatars/main/params", json={"values": {"ParamMouthOpenY": "0.8", "MouthSmile": 1}, "for": 2})
    assert ok.status_code == 200 and ok.json()["values"] == {"ParamMouthOpenY": 0.8, "MouthSmile": 1.0}
    live = av.get("/avatar/api/avatars").json()["live"]["main"]
    assert set(live["params"]) == {"ParamMouthOpenY", "MouthSmile"}
    assert av.post("/avatar/api/avatars/main/params", json={"values": {"X": "loud"}}).status_code == 400
    assert av.post("/avatar/api/avatars/main/params", json={"values": {"X": 1}, "layer": "top"}).status_code == 400
    av.post("/avatar/api/avatars/main/release", json={})
    assert av.get("/avatar/api/avatars").json()["live"]["main"]["params"] == {}

    assert av.post("/avatar/api/avatars/main/expression", json={"name": "Smile", "state": "toggle"}).json()["state"] == "on"
    assert av.post("/avatar/api/avatars/main/expression", json={"name": "Smile", "state": "toggle"}).json()["state"] == "off"
    e = av.post("/avatar/api/avatars/main/emotion", json={"name": "angry", "for": 3}).json()
    assert e["expressions"] == ["angry"] and e["face"]["brows"] == -1.0
    assert av.post("/avatar/api/avatars/main/emotion", json={"name": "furious"}).status_code == 400
    assert av.post("/avatar/api/avatars/main/look", json={"at": "chat"}).status_code == 400
    assert av.post("/avatar/api/avatars/main/look", json={"stage_x": 90, "stage_y": 20}).json()["stage_x"] == 90
    assert av.post("/avatar/api/avatars/main/gesture", json={"name": "nod"}).status_code == 200
    assert av.post("/avatar/api/avatars/main/gesture", json={"name": "dab"}).status_code == 400
    t = av.post("/avatar/api/avatars/main/transform", json={"x": 70, "duration": 1}).json()
    assert t["transform"] == {"x": 70.0}
    assert av.get("/avatar/api/avatars/main").json()["avatar"]["x"] == 50     # not saved ...
    av.post("/avatar/api/avatars/main/transform", json={"x": 70, "save": True})
    assert av.get("/avatar/api/avatars/main").json()["avatar"]["x"] == 70     # ... unless asked
    assert av.post("/avatar/api/avatars/ghost/params", json={"values": {"A": 1}}).status_code == 404
    assert av.post("/avatar/api/avatars/main/dance", json={}).status_code in (404, 405)
    assert av.post("/avatar/api/avatars/main/command", json={"cmd": "dance"}).status_code == 400


def test_several_expressions_in_one_call(av):
    m = _upload(av)
    av.post("/avatar/api/avatars", json={"name": "main", "model": m["id"]})
    r = av.post("/avatar/api/avatars/main/expression", json={"names": ["smile", "ANGRY"]}).json()   # any case
    assert r["states"] == {"Smile": "on", "angry": "on"} and r["active"] == ["Smile", "angry"]
    r = av.post("/avatar/api/avatars/main/expression", json={"names": ["angry"], "only": True}).json()
    assert r["states"] == {"angry": "on", "Smile": "off"} and r["active"] == ["angry"]
    r = av.post("/avatar/api/avatars/main/expression", json={"names": ["Smile", "angry"], "state": "toggle"}).json()
    assert r["states"] == {"Smile": "on", "angry": "off"}
    bad = av.post("/avatar/api/avatars/main/expression", json={"names": ["Smile", "Wink"]})
    assert bad.status_code == 400 and "'Wink'" in bad.json()["error"] and "Smile" in bad.json()["error"]
    assert av.get("/avatar/api/avatars").json()["live"]["main"]["expressions"] == {"Smile": 0}     # a failed call changes nothing
    # an emotion can bring several expressions
    av.post("/avatar/api/avatars/main", json={"emotions": {"happy": {"face": {"smile": 1}, "expressions": ["Smile", "angry"]}}})
    assert av.post("/avatar/api/avatars/main/emotion", json={"name": "happy"}).json()["expressions"] == ["Smile", "angry"]


def test_parts(av):
    av.post("/avatar/api/avatars", json={"name": "main"})
    r = av.post("/avatar/api/avatars/main/parts", json={"values": {"PartArmA": 0, "PartArmB": "1"}, "for": 5})
    assert r.status_code == 200 and r.json()["values"] == {"PartArmA": 0.0, "PartArmB": 1.0}
    assert set(av.get("/avatar/api/avatars").json()["live"]["main"]["parts"]) == {"PartArmA", "PartArmB"}
    av.post("/avatar/api/avatars/main/release_parts", json={"ids": ["PartArmA"]})
    assert set(av.get("/avatar/api/avatars").json()["live"]["main"]["parts"]) == {"PartArmB"}
    assert av.post("/avatar/api/avatars/main/parts", json={"values": {}}).status_code == 400


def _colors(av, name="main"):
    return av.get(f"/avatar/api/avatars/{name}").json()["avatar"]["colors"]


def _live(av, kind, name="main"):
    return av.get("/avatar/api/avatars").json()["live"][name][kind]


def _held(av, name="main"):
    return _live(av, "colors", name)


def _with_parts(av):
    """An avatar `main` whose model is known to have a few parts and art meshes (Hexcast learns them once the model is drawn)."""
    m = _upload(av)
    av.post("/avatar/api/avatars", json={"name": "main", "model": m["id"]})
    av.mod.MODEL_INFO[m["id"]] = {"parts": [{"id": "PartHair", "name": "Hair"}, {"id": "PartEyes"}], "drawables": ["ArtHair1", "ArtEye1"],
                                  "drawable_parts": ["PartHair", "PartEyes"]}
    return m["id"]


def test_colors_recolour_parts_art_meshes_and_the_whole_model(av):
    _with_parts(av)
    r = av.post("/avatar/api/avatars/main/colors", json={
        "parts": {"PartHair": "FF8800"}, "meshes": {"ArtEye1": {"alpha": 0, "overlay": "#abc"}}, "all": {"multiply": "#808080"}, "for": 5, "fade": 1})
    assert r.status_code == 200 and r.json()["colors"] == {                     # any way of spelling a colour; a bare colour is a multiply
        "all": {"multiply": "#808080"}, "parts": {"PartHair": {"multiply": "#ff8800"}}, "meshes": {"ArtEye1": {"overlay": "#aabbcc", "alpha": 0.0}}}
    assert set(_held(av)) == {"all", "parts:PartHair", "meshes:ArtEye1"} and _held(av)["parts:PartHair"]["spec"] == {"multiply": "#ff8800"}
    assert _colors(av) == {}                                                    # for now: not saved
    # a field set to null goes; the rest of the target stays
    av.post("/avatar/api/avatars/main/colors", json={"meshes": {"ArtEye1": {"alpha": None}}})
    assert _held(av)["meshes:ArtEye1"]["spec"] == {"overlay": "#aabbcc"}
    av.post("/avatar/api/avatars/main/colors", json={"all": None, "meshes": {"ArtEye1": None}})              # a whole target goes
    assert set(_held(av)) == {"parts:PartHair"}
    assert av.post("/avatar/api/avatars/main/colors", json={"meshes": {"ArtHair1": {"hidden": True}}}).json()["colors"]["meshes"]["ArtHair1"] == {"alpha": 0.0}
    av.post("/avatar/api/avatars/main/release_colors", json={"meshes": ["ArtHair1"]})
    assert set(_held(av)) == {"parts:PartHair"}
    av.post("/avatar/api/avatars/main/release_colors", json={})
    assert _held(av) == {}


def test_colors_mistakes_change_nothing_and_say_what_is_wrong(av):
    _with_parts(av)
    post = lambda body: av.post("/avatar/api/avatars/main/colors", json=body)
    assert post({}).status_code == 400 and post({"parts": {}}).status_code == 400
    bad = post({"parts": {"PartHair": "#00ff00", "PartEyes": {"multiply": "green"}}})
    assert bad.status_code == 400 and "PartEyes" in bad.json()["error"] and "multiply" in bad.json()["error"] and _held(av) == {}
    assert "alpha" in post({"meshes": {"ArtEye1": {"alpha": "clear"}}}).json()["error"]
    assert "'PartTail'" in post({"parts": {"PartTail": "#00ff00"}}).json()["error"]
    assert "'ArtTail'" in post({"meshes": {"ArtTail": {"alpha": 0}}}).json()["error"]
    assert _held(av) == {}


def test_saved_colors_are_merged_field_by_field_and_kept_clean(av):
    _with_parts(av)
    save = lambda body: av.post("/avatar/api/avatars/main/colors", json={**body, "save": True})
    av.post("/avatar/api/avatars/main/colors", json={"parts": {"PartHair": "#ff0000"}})                      # held ...
    assert save({"parts": {"PartHair": {"overlay": "#202020"}, "PartEyes": "#112233"}}).json()["save"] is True
    assert _colors(av) == {"parts": {"PartHair": {"overlay": "#202020"}, "PartEyes": {"multiply": "#112233"}}} and _held(av) == {}      # ... and saved: no longer held
    save({"parts": {"PartHair": {"multiply": "#334455"}}, "all": {"alpha": 0.5}})
    assert _colors(av)["parts"]["PartHair"] == {"overlay": "#202020", "multiply": "#334455"} and _colors(av)["all"] == {"alpha": 0.5}
    save({"parts": {"PartHair": {"overlay": None}, "PartEyes": None}, "all": None})                           # null takes a field, a target, away
    assert _colors(av) == {"parts": {"PartHair": {"multiply": "#334455"}}}
    # the tab saves its set as a whole: junk and empty targets are not kept, other settings leave it alone
    av.post("/avatar/api/avatars/main", json={"colors": {"parts": {"PartHair": {"multiply": "nope", "alpha": 2}, "": "#123456", "PartEyes": {}}, "meshes": {"ArtEye1": "#FFFFFF"}}})
    assert _colors(av) == {"parts": {"PartHair": {"alpha": 1.0}}, "meshes": {"ArtEye1": {"multiply": "#ffffff"}}}     # (white is a real choice: it can undo a preset)
    av.post("/avatar/api/avatars/main", json={"x": 40})
    assert _colors(av)["parts"] == {"PartHair": {"alpha": 1.0}}
    av.post("/avatar/api/avatars/main", json={"colors": {}})
    assert _colors(av) == {}


def test_a_colour_changed_in_the_tab_wins_over_one_a_bot_holds_for_the_same_target(av, renderers):
    _with_parts(av)
    obs = renderers("main")
    av.post("/avatar/api/avatars/main/colors", json={"parts": {"PartHair": "#ff0000", "PartEyes": "#00ff00"}, "all": {"alpha": 0.5}})
    obs.ws.sent.clear()
    av.post("/avatar/api/avatars/main", json={"colors": {"parts": {"PartHair": {"multiply": "#0000ff"}}}})      # the tab recolours the hair only
    assert set(_held(av)) == {"all", "parts:PartEyes"}                                                          # the bot keeps the rest
    told = [m for m in obs.ws.sent if isinstance(m, dict) and m.get("cmd") == "release_colors"]
    assert len(told) == 1 and told[0]["parts"] == ["PartHair"] and told[0]["meshes"] == [] and not told[0]["all"] and not told[0]["everything"]
    obs.ws.sent.clear()
    av.post("/avatar/api/avatars/main", json={"colors": {"parts": {"PartHair": {"multiply": "#0000ff"}}}})      # nothing changed: nothing released
    assert not [m for m in obs.ws.sent if isinstance(m, dict) and m.get("cmd") == "release_colors"]


def test_colour_presets_are_saved_on_the_model_and_switched_like_hotkeys(av, renderers):
    mid = _with_parts(av)
    obs = renderers("main")
    path = f"/avatar/api/models/{mid}/color_presets"
    assert av.post(path, json={"name": "Night", "avatar": "main"}).status_code == 400                          # nothing to record yet
    av.post("/avatar/api/avatars/main/colors", json={"parts": {"PartHair": "#334466"}, "meshes": {"ArtEye1": {"overlay": "#6688ff"}}, "save": True})
    r = av.post(path, json={"name": "Night", "avatar": "main"})
    night = _colors(av)
    assert r.status_code == 200 and r.json()["look"] == night and r.json()["presets"] == ["Night"]
    assert (av.lib.model_dir(mid) / "hexcast.colors.json").is_file()                                           # in the model's own folder: it goes where the model goes
    assert av.get(path).json()["presets"] == {"Night": night}
    assert [m for m in av.get("/avatar/api/models").json()["models"] if m["id"] == mid][0]["color_presets"] == ["Night"]
    assert av.get("/avatar/api/avatars/main/info").json()["color_presets"] == ["Night"]
    av.post("/avatar/api/avatars/main", json={"colors": {"parts": {"PartEyes": {"alpha": 0}}}})
    assert av.post(path, json={"name": "night", "avatar": "main"}).json()["presets"] == ["night"]              # a name is one preset, whatever its case
    av.post(path, json={"name": "Blink", "look": {"parts": {"PartEyes": {"alpha": 1}}}})
    assert av.post(path, json={"name": "Bad", "look": {"parts": {"PartEyes": "pink"}}}).status_code == 400
    assert sorted(av.get(path).json()["presets"], key=str.lower) == ["Blink", "night"]

    # switched on and off like hotkeys: any case, several at once, toggle, only, for
    obs.ws.sent.clear()
    r = av.post("/avatar/api/avatars/main/color_preset", json={"name": "NIGHT"}).json()
    assert r["states"] == {"night": "on"} and r["active"] == ["night"] and r["rules"]["night"]["parts"] == {"PartEyes": {"alpha": 0.0}}
    told = [m for m in obs.ws.sent if isinstance(m, dict) and m.get("cmd") == "color_preset"]
    assert len(told) == 1 and told[0]["states"] == {"night": "on"} and told[0]["rules"] == r["rules"] and told[0]["orders"]["night"] == r["orders"]["night"]
    assert set(_live(av, "color_presets")) == {"night"}
    r = av.post("/avatar/api/avatars/main/color_preset", json={"names": ["night", "blink"], "state": "toggle"}).json()
    assert r["states"] == {"night": "off", "Blink": "on"} and r["active"] == ["Blink"]
    r = av.post("/avatar/api/avatars/main/color_preset", json={"names": ["night", "Blink"]}).json()
    assert r["active"] == ["Blink", "night"] and r["orders"]["Blink"] < r["orders"]["night"]                   # a later one is drawn over an earlier; Blink keeps its place
    r = av.post("/avatar/api/avatars/main/color_preset", json={"name": "night", "only": True}).json()
    assert r["active"] == ["night"] and r["states"] == {"night": "on", "Blink": "off"}
    assert av.post("/avatar/api/avatars/main/color_preset", json={"name": "night", "state": "off"}).json()["active"] == []
    av.post("/avatar/api/avatars/main/color_preset", json={"name": "Blink", "for": 30})
    assert _live(av, "color_presets")["Blink"]["until"] and av.post("/avatar/api/avatars/main/clear_color_presets", json={}).json()["fade"] == 0.4
    assert _live(av, "color_presets") == {}
    bad = av.post("/avatar/api/avatars/main/color_preset", json={"name": "Day"})
    assert bad.status_code == 400 and "'Day'" in bad.json()["error"] and "Blink, night" in bad.json()["error"]
    assert av.post("/avatar/api/avatars/main/color_preset", json={}).status_code == 400
    assert av.post("/avatar/api/avatars/main/color_preset", json={"name": "night", "state": "maybe"}).status_code == 400

    # recording over a preset that is on brings it up to date; deleting it switches it off
    av.post("/avatar/api/avatars/main/color_preset", json={"name": "Blink"})
    obs.ws.sent.clear()
    av.post(path, json={"name": "blink", "look": {"all": {"multiply": "#101010"}}})
    told = [m for m in obs.ws.sent if isinstance(m, dict) and m.get("cmd") == "color_preset"]
    assert told and told[0]["states"] == {"Blink": "off", "blink": "on"} and told[0]["rules"] == {"blink": {"all": {"multiply": "#101010"}}}
    assert set(_live(av, "color_presets")) == {"blink"} and _live(av, "color_presets")["blink"]["rules"] == {"all": {"multiply": "#101010"}}
    obs.ws.sent.clear()
    assert av.post(f"{path}/blink/delete").json()["presets"] == ["night"]
    assert _live(av, "color_presets") == {}
    assert [m for m in obs.ws.sent if isinstance(m, dict) and m.get("cmd") == "color_preset"][0]["states"] == {"blink": "off"}
    assert av.post(f"{path}/blink/delete").status_code == 404


def test_a_model_remembers_a_default_look_for_every_avatar_that_loads_it(av, renderers):
    mid = _with_parts(av)
    other = _upload(av)["id"]
    path = f"/avatar/api/models/{mid}/color_presets"
    night, neon = {"parts": {"PartHair": {"multiply": "#334466"}}}, {"all": {"overlay": "#220044"}}
    assert av.post(path, json={"name": "Night", "look": night}).json()["default"] == ""                       # not the default unless asked
    assert av.post(f"{path}/night/default").json()["default"] == "Night"                                      # (any case)
    assert [m for m in av.get("/avatar/api/models").json()["models"] if m["id"] == mid][0]["color_default"] == "Night"
    assert av.get("/avatar/api/avatars/main/info").json()["color_default"] == "Night"
    assert (av.lib.model_dir(mid) / "hexcast.colors.json").read_text(encoding="utf-8").count('"default": "Night"') == 1

    # an avatar that loads the model starts in it; colours given with it win; one already there is left alone
    assert _avatar(av, "b", model=mid)["colors"] == night
    assert _avatar(av, "c", model=mid, colors={"all": {"alpha": 0.5}})["colors"] == {"all": {"alpha": 0.5}}
    assert _colors(av, "main") == {}
    # the same preset under another spelling is still the default; recording over it keeps it
    av.post(path, json={"name": "NIGHT", "look": {"parts": {"PartHair": {"multiply": "#112233"}}}})
    assert av.get(path).json()["presets"].keys() == {"NIGHT"} and av.post(f"{path}/night/default", json={}).json()["default"] == "NIGHT"
    assert _avatar(av, "d", model=mid)["colors"] == {"parts": {"PartHair": {"multiply": "#112233"}}}

    # switching an avatar to another model: colours are of one model's parts, so the new model's default (or none) takes their place
    obs = renderers("main")
    av.post("/avatar/api/avatars/main/colors", json={"parts": {"PartHair": "#ff0000"}})
    av.post("/avatar/api/avatars/main/color_preset", json={"name": "night"})
    av.post("/avatar/api/avatars/b", json={"model": other})
    assert _colors(av, "b") == {}                                                                              # `other` has no default
    av.post(f"/avatar/api/models/{other}/color_presets", json={"name": "Neon", "look": neon, "default": True})
    obs.ws.sent.clear()
    av.post("/avatar/api/avatars/main", json={"model": other})
    assert _colors(av, "main") == neon and _held(av) == {} and _live(av, "color_presets") == {}               # and what a bot held / switched on is gone
    cmds = [m["cmd"] for m in obs.ws.sent if isinstance(m, dict) and m.get("type") == "cmd"]
    assert "release_colors" in cmds and "clear_color_presets" in cmds
    av.post("/avatar/api/avatars/main", json={"model": mid})
    assert _colors(av, "main") == {"parts": {"PartHair": {"multiply": "#112233"}}}                             # back: the first model's default again
    av.post("/avatar/api/avatars/main", json={"model": mid, "x": 30})                                          # not a change of model: nothing replaced
    av.post("/avatar/api/avatars/main/colors", json={"all": {"alpha": 0.4}, "save": True})
    av.post("/avatar/api/avatars/main", json={"model": mid, "x": 31})
    assert _colors(av, "main")["all"] == {"alpha": 0.4}

    # no longer the default: a new avatar starts bare; deleting the default preset clears the flag too
    assert av.post(f"{path}/night/default", json={"default": False}).json()["default"] == ""
    assert _avatar(av, "e", model=mid)["colors"] == {}
    av.post(f"{path}/night/default")
    assert av.post(f"{path}/night/delete").json()["default"] == "" and av.lib.color_default(mid) == ""
    assert av.post(f"{path}/night/default").status_code == 404


def test_a_preset_saved_with_its_model_is_a_new_model_that_starts_in_it(av):
    mid = _with_parts(av)
    path = f"/avatar/api/models/{mid}/color_presets"
    night = {"parts": {"PartHair": {"multiply": "#334466"}}, "meshes": {"ArtEye1": {"overlay": "#6688ff"}}}
    av.post(path, json={"name": "Night", "look": night, "default": True})
    av.post(path, json={"name": "Neon", "look": {"all": {"overlay": "#220044"}}})
    assert _avatar(av, "plain", model=mid)["colors"] == night                                       # (the original starts in Night for now)

    r = av.post(f"{path}/night/as_model", json={"name": "Test Model - Night"})
    assert r.status_code == 200, r.text
    new = r.json()["model"]
    assert new["id"] == "test-model-night" and new["name"] == "Test Model - Night" and new["ok"] and new["type"] == "live2d"
    # a full copy of its own: the files, the expressions and motions, the settings - and it starts in Night, with Neon still there
    folder = av.lib.model_dir(new["id"])
    assert (folder / "m.moc3").is_file() and (folder / "hexcast.json").is_file() and (folder / "hexcast.colors.json").is_file()
    assert [e["name"] for e in new["expressions"]] == ["Smile", "angry"] and new["color_presets"] == ["Neon", "Night"] and new["color_default"] == "Night"
    assert (av.lib.model_dir(mid) / "m.moc3").is_file()                                             # the original is still there ...
    assert av.lib.color_presets(mid)["Night"] == night and av.lib.color_default(mid) == ""         # ... with its presets, but it no longer starts in Night
    assert _avatar(av, "old", model=mid)["colors"] == {} and _avatar(av, "copy", model=new["id"])["colors"] == night
    assert [m["name"] for m in av.get("/avatar/api/models").json()["models"] if m["id"] in (mid, new["id"])] == ["Test Model", "Test Model - Night"]
    # the copy is independent: changing it leaves the original alone, and deleting the original leaves the copy
    av.post(f"/avatar/api/models/{new['id']}/color_presets/neon/delete")
    assert "Neon" in av.lib.color_presets(mid) and "Neon" not in av.lib.color_presets(new["id"])
    av.post(f"/avatar/api/models/{mid}/delete")
    assert (av.lib.model_dir(new["id"]) / "m.moc3").is_file()

    # a preset that was not the original's default leaves the original's default alone; the same name again gets its own id
    other = _upload(av)["id"]
    av.post(f"/avatar/api/models/{other}/color_presets", json={"name": "A", "look": night, "default": True})
    av.post(f"/avatar/api/models/{other}/color_presets", json={"name": "B", "look": {"all": {"alpha": 0.5}}})
    first = av.post(f"/avatar/api/models/{other}/color_presets/b/as_model", json={"name": "Variant"}).json()["model"]
    second = av.post(f"/avatar/api/models/{other}/color_presets/b/as_model", json={"name": "Variant"}).json()["model"]
    assert (first["id"], second["id"]) == ("variant", "variant-2") and first["name"] == second["name"] == "Variant"
    assert av.lib.color_default(other) == "A" and av.lib.color_default(first["id"]) == "B"
    # mistakes change nothing
    before = sorted(m["id"] for m in av.get("/avatar/api/models").json()["models"])
    assert av.post(f"/avatar/api/models/{other}/color_presets/b/as_model", json={"name": "  "}).status_code == 400
    assert av.post(f"/avatar/api/models/{other}/color_presets/nope/as_model", json={"name": "X"}).status_code == 404
    assert sorted(m["id"] for m in av.get("/avatar/api/models").json()["models"]) == before


def test_a_pngtuber_preset_can_be_saved_as_a_new_pngtuber(av):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in ("idle.png", "talk.png"):
            z.writestr("Tuber/" + n, _png(20, 30))
    m = _upload(av, buf.getvalue(), "tuber.zip")
    look = {"all": {"multiply": "#ff8844", "alpha": 0.9}}
    av.post(f"/avatar/api/models/{m['id']}/color_presets", json={"name": "Warm", "look": look})
    new = av.post(f"/avatar/api/models/{m['id']}/color_presets/warm/as_model", json={"name": "Warm Tuber"}).json()["model"]
    assert new["type"] == "png" and new["ok"] and new["name"] == "Warm Tuber" and new["color_default"] == "Warm"
    assert av.get(f"/avatar/api/models/{new['id']}/engine").json()["states"]["neutral"]["idle"] == "idle.png"
    assert av.lib.color_presets(new["id"])["Warm"] == look and av.lib.color_default(m["id"]) == ""
    assert _avatar(av, "w", model=new["id"])["colors"] == look


def _capture_world(av, monkeypatch, soundcard_opens, portaudio_opens=True, mme_name_cut=False):
    """The recording libraries replaced by fakes: a mono USB mic that soundcard can(not) open and PortAudio can(not)."""
    import types
    import numpy as np
    from hexcast_plugins.avatar import capture
    name = "Microphone (Mono USB Microphone)"

    class Rec:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def record(self, numframes):
            time.sleep(0.004)
            return np.full((numframes, 1), 0.1, dtype="float32")

    class Mic:
        def __init__(self):
            self.name = name

        def recorder(self, **kw):
            if not soundcard_opens:
                raise AssertionError()                    # what soundcard does for a device whose Windows format it does not know
            return Rec()

    opened = []

    class Stream:
        def __init__(self, **kw):
            if not portaudio_opens:
                raise RuntimeError("Invalid device [PaErrorCode -9996]")
            opened.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            time.sleep(0.004)
            return np.full((n, 1), 0.25, dtype="float32"), False

    cut = name[:31] if mme_name_cut else name
    sd = types.SimpleNamespace(
        query_hostapis=lambda: [{"name": "MME"}, {"name": "Windows WASAPI"}, {"name": "Windows DirectSound"}, {"name": "Windows WDM-KS"}],
        query_devices=lambda: [{"name": cut, "max_input_channels": 1, "hostapi": 0}, {"name": name, "max_input_channels": 2, "hostapi": 1},
                               {"name": name, "max_input_channels": 1, "hostapi": 2}, {"name": name, "max_input_channels": 1, "hostapi": 3},
                               {"name": "Speakers", "max_input_channels": 0, "hostapi": 1}],
        InputStream=Stream, WasapiSettings=lambda **kw: ("wasapi", kw), _terminate=lambda: None, _initialize=lambda: None)
    sc = types.SimpleNamespace(all_microphones=lambda include_loopback=False: [Mic()], all_speakers=lambda: [])
    for attr, val in (("_sc", sc), ("_sd", sd), ("_np", np), ("AVAILABLE", True)):
        monkeypatch.setattr(capture, attr, val)
    return capture, "input:" + name, opened


def _record_for(capture, dev, seconds=0.7):
    loop = asyncio.new_event_loop()
    got = []
    cap = capture.Capture(dev, loop, lambda d, pcm: got.append(len(pcm)))
    loop.run_until_complete(asyncio.sleep(seconds))
    cap.stop()
    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()
    return cap, got


def test_a_mic_the_default_recorder_cannot_open_is_recorded_through_portaudio(av, monkeypatch):
    capture, dev, opened = _capture_world(av, monkeypatch, soundcard_opens=False)
    cap, got = _record_for(capture, dev)
    assert got and all(n == 512 for n in got)                              # 256 samples of 16-bit audio per block, streaming
    assert cap.backend == "sounddevice" and cap.error == "" and "AssertionError" in cap.note
    assert opened[0]["samplerate"] == 16000 and opened[0]["channels"] == 1 and opened[0]["device"] == 1       # the WASAPI entry, not MME / DirectSound / WDM-KS
    assert opened[0]["extra_settings"] == ("wasapi", {"auto_convert": True})                                  # Windows converts whatever the device gives
    assert cap.level > 0.2 and capture.Captures().status() == {}
    st = capture.Captures()
    st.running[dev] = cap
    assert st.status()[dev]["backend"] == "sounddevice" and "PortAudio" in st.status()[dev]["note"]


def test_a_device_the_default_recorder_opens_is_left_to_it(av, monkeypatch):
    capture, dev, opened = _capture_world(av, monkeypatch, soundcard_opens=True)
    cap, got = _record_for(capture, dev)
    assert got and cap.backend == "soundcard" and cap.error == "" and cap.note == "" and not opened


def test_a_device_neither_recorder_opens_says_why_for_both(av, monkeypatch):
    capture, dev, _ = _capture_world(av, monkeypatch, soundcard_opens=False, portaudio_opens=False)
    cap, got = _record_for(capture, dev, 0.4)
    assert not got and cap.level == 0
    assert "soundcard: AssertionError" in cap.error and "sounddevice: RuntimeError: Invalid device" in cap.error
    # what a speaker plays can only be tapped by soundcard: PortAudio is not asked
    capture2, _, opened = _capture_world(av, monkeypatch, soundcard_opens=False)
    cap2, got2 = _record_for(capture2, "loopback:Speakers", 0.4)
    assert not got2 and not opened and cap2.backend == "soundcard" and "is not there" in cap2.error


def test_portaudio_finds_a_mic_by_its_full_name_even_where_mme_cuts_it_short(av, monkeypatch):
    capture, dev, opened = _capture_world(av, monkeypatch, soundcard_opens=False, mme_name_cut=True)
    assert [d["name"] for d in capture._pa_inputs()] == ["Microphone (Mono USB Microphone)"]                  # one entry; the cut MME name is not a device of its own
    assert capture._pa_inputs()[0]["api"] == "Windows WASAPI"
    cap, got = _record_for(capture, dev, 0.4)
    assert got and opened[0]["device"] == 1


def test_a_pngtuber_has_layers_not_art_meshes(av):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in ("idle.png", "talk.png"):
            z.writestr("Tuber/" + n, _png(20, 30))
    m = _upload(av, buf.getvalue(), "tuber.zip")
    av.post("/avatar/api/avatars", json={"name": "t", "model": m["id"]})
    err = av.post("/avatar/api/avatars/t/colors", json={"meshes": {"ArtMesh1": {"alpha": 0}}})
    assert err.status_code == 400 and "PNGtuber" in err.json()["error"] and "parts" in err.json()["error"]
    assert av.post("/avatar/api/avatars/t/colors", json={"parts": {"neutral:idle": "#ff8800"}, "all": {"alpha": 0.8}}).status_code == 200
    assert av.post(f"/avatar/api/models/{m['id']}/color_presets", json={"name": "Warm", "look": {"parts": {"neutral:idle": "#ff8800"}}}).status_code == 200
    assert av.post("/avatar/api/avatars/t/color_preset", json={"name": "warm"}).json()["states"] == {"Warm": "on"}


def test_held_colours_and_presets_are_given_to_a_renderer_that_joins_late(av):
    mid = _with_parts(av)
    av.post(f"/avatar/api/models/{mid}/color_presets", json={"name": "Night", "look": {"all": {"multiply": "#334466"}}})
    av.post("/avatar/api/avatars/main/colors", json={"parts": {"PartHair": "#ff0000"}, "for": 60})
    av.post("/avatar/api/avatars/main/colors", json={"parts": {"PartEyes": "#00ff00"}, "for": 0.0001})
    av.post("/avatar/api/avatars/main/color_preset", json={"name": "night"})
    with av.websocket_connect("/avatar/ws/render?role=obs") as ws:
        live = ws.receive_json()["live"]["main"]
    assert set(live["colors"]) == {"parts:PartHair"} and live["colors"]["parts:PartHair"]["spec"] == {"multiply": "#ff0000"}   # the one that ran out is not sent
    assert live["color_presets"]["Night"]["rules"] == {"all": {"multiply": "#334466"}} and live["color_presets"]["Night"]["order"]


def test_pngtuber_from_pictures(av):
    from hexcast_plugins.avatar import pngtuber
    assert pngtuber.classify("Open Mouth Blink.png") == ("neutral", "talk_blink")
    assert pngtuber.classify("happy_talk.png") == ("happy", "talk")
    assert pngtuber.classify("mouth closed eyes open.png") == ("neutral", "idle")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in ("idle.png", "talk.png", "blink.png", "talk_blink.png", "happy_idle.png", "happy_talk.png"):
            z.writestr("Tuber/" + n, _png(20, 30))
    m = _upload(av, buf.getvalue(), "tuber.zip")
    assert m["type"] == "png" and m["style"] == "simple" and m["ok"] and m["states"] == ["neutral", "happy"]
    rig = av.get(f"/avatar/api/models/{m['id']}/engine").json()
    assert rig["states"]["neutral"] == {"idle": "idle.png", "talk": "talk.png", "blink": "blink.png", "talk_blink": "talk_blink.png"}
    assert rig["base"].endswith(f"/models/{m['id']}/")
    # a PNGtuber's states are its expressions: one at a time
    av.post("/avatar/api/avatars", json={"name": "t", "model": m["id"]})
    assert av.get("/avatar/api/avatars/t").json()["avatar"]["emotions"]["happy"]["expressions"] == ["happy"]
    r = av.post("/avatar/api/avatars/t/expression", json={"names": ["neutral", "happy"]}).json()
    assert r["active"] == ["happy"]
    assert av.post("/avatar/api/avatars/t/expression", json={"name": "sad"}).json()["error"].startswith("t's model has no state")
    assert av.post("/avatar/api/avatars/t/motion", json={"name": "wave"}).status_code == 400
    # the rig editor: pictures added, a role changed, saved
    r = av.post(f"/avatar/api/models/{m['id']}/pictures", files=[("files", ("Extra Mouth.png", _png(), "image/png"))]).json()
    assert r["added"] == ["Extra Mouth.png"]
    rig["states"]["neutral"]["half"] = "Extra Mouth.png"
    rig["states"]["neutral"]["talk"] = "../../evil.png"                       # not a picture of this model: dropped
    saved = av.post(f"/avatar/api/models/{m['id']}/settings", json={"rig": rig}).json()["settings"]["rig"]
    assert saved["states"]["neutral"]["half"] == "Extra Mouth.png" and "talk" not in saved["states"]["neutral"]


def test_pngtuber_plus_save_import(av):
    pic = base64.b64encode(_png(16, 16)).decode()
    save = {"0": {"type": "sprite", "identification": 11, "parentId": None, "imageData": pic, "path": "user://body.png",
                  "pos": "Vector2(0, 0)", "offset": "Vector2(0, 0)", "zindex": -1, "drag": 0, "xFrq": 0.004, "xAmp": 9,
                  "yFrq": 0.008, "yAmp": 11, "rotDrag": 0, "showTalk": 0, "showBlink": 0,
                  "costumeLayers": "[1, 1, 1, 1, 1, 1, 1, 1, 1, 1]"},
            "1": {"type": "sprite", "identification": 22, "parentId": 11, "imageData": pic, "path": "user://mouth.png",
                  "pos": "Vector2(3.5, -12)", "offset": "Vector2(-74, 92)", "zindex": 2, "drag": 4, "xFrq": 0, "xAmp": 0,
                  "yFrq": 0, "yAmp": 0, "rotDrag": -1, "rLimitMin": -14, "rLimitMax": 17, "showTalk": 2, "showBlink": 1,
                  "costumeLayers": "[1, 0, 1, 1, 1, 1, 1, 1, 1, 1]", "stretchAmount": 0.25, "frames": 1, "animSpeed": 0}}
    r = av.post("/avatar/api/models/upload", files={"file": ("bob.save", json.dumps(save).encode(), "application/json")})
    m = r.json()["model"]
    assert m["type"] == "png" and m["style"] == "layered" and m["layers"] == 2 and m["source"] == "PNGTuber Plus"
    rig = av.get(f"/avatar/api/models/{m['id']}/engine").json()
    mouth = [l for l in rig["layers"] if l["id"] == "22"][0]
    assert mouth["parent"] == "11" and (mouth["x"], mouth["y"], mouth["ox"], mouth["oy"]) == (3.5, -12, -74, 92)
    assert mouth["talk"] == 2 and mouth["blink"] == 1 and mouth["rot_min"] == -14 and mouth["drag"] == 4
    assert "costume2" not in mouth["states"] and "costume1" in mouth["states"]          # hidden in costume 2
    assert rig["states"][0] == "costume1"
    assert (av.lib.MODELS_DIR / m["id"] / mouth["image"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_pngtuber_rig_follows_the_voice_unless_told_to_hold_and_keeps_its_numbers_in_range(av):
    from hexcast_plugins.avatar import pngtuber
    old = {"version": 1, "type": "png", "style": "simple", "states": {"neutral": {"idle": "i.png"}},
           "bounce": 250, "gravity": 1000, "threshold": 0.12, "hold": 0.22, "breathe": True}   # a rig saved before the voice settings
    rig = pngtuber.normalize(old, files=["i.png"])
    assert rig["mouth"] == "follow" and rig["snap"] == 0.6 and rig["beat"] == 120               # it follows the words now, with a small bounce
    assert rig["hold"] == 0.22 and rig["bounce"] == 250                                         # what it had is kept
    odd = pngtuber.normalize({**old, "mouth": "sideways", "snap": 7, "beat": -5, "hold": 99}, files=["i.png"])
    assert odd["mouth"] == "follow" and odd["snap"] == 1 and odd["beat"] == 0 and odd["hold"] == 2
    held = pngtuber.normalize({**old, "mouth": "hold", "snap": 0.2, "beat": 40}, files=["i.png"])
    assert (held["mouth"], held["snap"], held["beat"]) == ("hold", 0.2, 40)
    layered = pngtuber.normalize({"style": "layered", "layers": [{"id": "a", "image": "i.png"}]}, files=["i.png"])
    assert layered["mouth"] == "follow" and layered["beat"] == 120                              # layered rigs too


def test_the_voice_settings_of_a_pngtuber_are_saved_and_served_to_the_overlay(av):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in ("idle.png", "talk.png"):
            z.writestr("Tuber/" + n, _png(20, 30))
    m = _upload(av, buf.getvalue(), "tuber.zip")
    rig = av.get(f"/avatar/api/models/{m['id']}/engine").json()
    assert rig["mouth"] == "follow" and rig["beat"] == 120 and rig["snap"] == 0.6               # a new PNGtuber follows the voice
    saved = av.post(f"/avatar/api/models/{m['id']}/settings", json={"rig": {**rig, "mouth": "hold", "snap": 0.9, "beat": 0}}).json()["settings"]["rig"]
    assert (saved["mouth"], saved["snap"], saved["beat"]) == ("hold", 0.9, 0)
    again = av.get(f"/avatar/api/models/{m['id']}/engine").json()
    assert (again["mouth"], again["snap"], again["beat"]) == ("hold", 0.9, 0)                   # the overlay is served what was saved


def test_star_addresses_every_avatar(av):
    av.post("/avatar/api/avatars", json={"name": "a"})
    av.post("/avatar/api/avatars", json={"name": "b"})
    r = av.post("/avatar/api/avatars/*/gesture", json={"name": "nod"}).json()
    assert r["avatars"] == ["a", "b"]


def test_speak_without_an_overlay_says_nobody_heard_it(av):
    av.post("/avatar/api/avatars", json={"name": "main"})
    wav = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", 0)
    r = av.post("/avatar/api/avatars/main/speak?wait=1", files={"audio": ("hi.wav", wav, "audio/wav")})
    j = r.json()
    assert r.status_code == 200 and j["overlays"] == 0 and "warning" in j and j["mime"] == "audio/wav"
    assert av.get(f"/avatar/speech/{j['id']}").content == wav
    assert av.post("/avatar/api/avatars/main/speak", json={"url": "file:///etc/passwd"}).status_code == 400
    assert av.post("/avatar/api/avatars/*/speak", content=wav).status_code == 400


# ---------------------------------------------------------------- overlays: one OBS source each

def _wav() -> bytes:
    return b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", 0)


def _overlay(av, name):
    r = av.post("/avatar/api/overlays", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["overlay"]


def _avatar(av, name, **kw):
    r = av.post("/avatar/api/avatars", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()["avatar"]


class _FakeWS:
    """A renderer's end of the socket: everything the hub sent it."""
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def send_bytes(self, data):
        self.sent.append(data)


@pytest.fixture
def renderers(av):
    """Renderers as the hub sees them: renderers(overlay="guest", audio=False), or role="preview"."""
    hub = av.mod.HUB

    def add(overlay="main", role="obs", only=None, audio=True):
        c = av.mod.Client(_FakeWS(), "render", role, only, audio, overlay if role == "obs" else None)
        hub.clients.add(c)
        return c

    yield add
    hub.clients.clear()


def _cmds(c):
    return [m["avatar"] for m in c.ws.sent if isinstance(m, dict) and m.get("type") == "cmd"]


def test_overlays_start_with_main_and_are_made_and_deleted(av):
    assert av.get("/avatar/api/overlays").json()["overlays"] == [{"id": "main", "path": "/avatar/overlay", "avatars": [], "sources": 0}]
    made = _overlay(av, "Guest")                                          # any case, stored lower
    assert made["id"] == "guest" and made["path"] == "/avatar/overlay/guest"
    assert av.post("/avatar/api/overlays", json={"name": "guest"}).status_code == 409
    assert av.post("/avatar/api/overlays", json={"name": "main"}).status_code == 409
    assert av.post("/avatar/api/overlays", json={"name": "No Good!"}).status_code == 400
    assert av.post("/avatar/api/overlays", json={}).status_code == 400
    for i in range(av.mod.MAX_OVERLAYS - 2):
        _overlay(av, f"o{i}")
    assert av.post("/avatar/api/overlays", json={"name": "one-too-many"}).status_code == 400
    assert len(av.get("/avatar/api/overlays").json()["overlays"]) == av.mod.MAX_OVERLAYS
    assert av.post("/avatar/api/overlays/main/delete").status_code == 400            # main is every avatar's home
    assert av.post("/avatar/api/overlays/nope/delete").status_code == 404
    assert av.post("/avatar/api/overlays/guest/delete").json() == {"ok": True, "moved": []}
    assert "guest" not in av.mod.CONFIG["overlays"] and "o0" in av.mod.CONFIG["overlays"]
    saved = json.loads(av.mod.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["overlays"] == av.mod.CONFIG["overlays"]                 # kept across a restart


def test_an_avatar_is_on_one_overlay_and_can_be_moved(av):
    _overlay(av, "guest")
    assert _avatar(av, "a")["overlay"] == "main"                          # the default
    assert _avatar(av, "g", overlay="guest")["overlay"] == "guest"
    assert av.post("/avatar/api/avatars", json={"name": "x", "overlay": "nope"}).status_code == 404
    assert av.get("/avatar/api/avatars/x").status_code == 404             # not created
    assert av.post("/avatar/api/avatars/a", json={"overlay": "guest"}).json()["avatar"]["overlay"] == "guest"
    assert av.post("/avatar/api/avatars/a", json={"overlay": "nope"}).status_code == 404
    assert av.get("/avatar/api/avatars/a").json()["avatar"]["overlay"] == "guest"     # the refusal changed nothing
    assert av.post("/avatar/api/avatars/a", json={"visible": False}).json()["avatar"]["overlay"] == "guest"   # other edits keep it
    assert av.post("/avatar/api/avatars/a", json={"overlay": ""}).json()["avatar"]["overlay"] == "main"
    assert [o["avatars"] for o in av.get("/avatar/api/overlays").json()["overlays"]] == [["a"], ["g"]]
    saved = json.loads(av.mod.CONFIG_PATH.read_text(encoding="utf-8"))
    assert {a["name"]: a["overlay"] for a in saved["avatars"]} == {"a": "main", "g": "guest"}


def test_a_new_avatar_is_spread_out_among_those_on_its_own_overlay(av):
    _overlay(av, "guest")
    _avatar(av, "a")
    _avatar(av, "b")
    assert [a["x"] for a in av.mod.CONFIG["avatars"]] == [50, 25]
    assert _avatar(av, "g", overlay="guest")["x"] == 50                   # first on a fresh overlay: the middle
    assert _avatar(av, "h", overlay="guest")["x"] == 25


def test_deleting_an_overlay_moves_its_avatars_to_main(av):
    _overlay(av, "guest")
    _avatar(av, "g", overlay="guest")
    _avatar(av, "h", overlay="guest")
    _avatar(av, "a")
    assert av.post("/avatar/api/overlays/guest/delete").json() == {"ok": True, "moved": ["g", "h"]}
    assert {a["name"]: a["overlay"] for a in av.mod.CONFIG["avatars"]} == {"a": "main", "g": "main", "h": "main"}
    assert av.mod.CONFIG["overlays"] == ["main"]


def test_a_config_from_before_overlays_loads_onto_main(av, monkeypatch, tmp_path):
    p = tmp_path / "avatar.json"
    monkeypatch.setattr(av.mod, "CONFIG_PATH", p)
    p.write_text(json.dumps({"avatars": [{"name": "a"}, {"name": "b", "x": 20}]}), encoding="utf-8")        # an old file
    cfg = av.mod.load_config()
    assert cfg["overlays"] == ["main"] and [a["overlay"] for a in cfg["avatars"]] == ["main", "main"]
    p.write_text(json.dumps({"overlays": ["guest", "Guest", "bad name", "main", None, "-x", "second"],
                             "avatars": [{"name": "a"}, {"name": "b", "overlay": "gone"}, {"name": "c", "overlay": "Guest"}]}), encoding="utf-8")
    cfg = av.mod.load_config()
    assert cfg["overlays"] == ["main", "guest", "second"]                 # main first, junk and repeats dropped
    assert {a["name"]: a["overlay"] for a in cfg["avatars"]} == {"a": "main", "b": "main", "c": "guest"}    # an unknown overlay -> main


def test_the_overlay_page_is_the_same_page_for_every_name(av):
    main, named, typo = (av.get(u) for u in ("/avatar/overlay", "/avatar/overlay/guest", "/avatar/overlay/not-made-yet"))
    assert main.status_code == named.status_code == typo.status_code == 200      # a typo is not an error page: OBS would show its text
    assert main.text == named.text and "role=obs&overlay=" in main.text


def test_commands_go_only_to_the_overlays_that_show_the_avatar(av, renderers):
    _overlay(av, "guest")
    _avatar(av, "a")
    _avatar(av, "g", overlay="guest")
    on_main, on_guest, preview = renderers("main"), renderers("guest"), renderers(role="preview")
    narrowed = renderers("main", only={"g"})                              # ?avatar=g on the main overlay: g is not there
    assert av.post("/avatar/api/avatars/a/gesture", json={"name": "nod"}).status_code == 200
    assert av.post("/avatar/api/avatars/g/gesture", json={"name": "nod"}).status_code == 200
    assert av.post("/avatar/api/avatars/*/gesture", json={"name": "shake"}).status_code == 200
    assert _cmds(on_main) == ["a", "a"] and _cmds(on_guest) == ["g", "g"]
    assert _cmds(preview) == ["a", "g", "a", "g"] and _cmds(narrowed) == []
    av.post("/avatar/api/avatars/g", json={"overlay": "main"})            # moved: the commands follow it
    av.post("/avatar/api/avatars/g/gesture", json={"name": "nod"})
    assert _cmds(on_main)[-1] == "g" and _cmds(on_guest)[-1] == "g" and len(_cmds(on_guest)) == 2   # ... and not one more to guest
    assert _cmds(narrowed) == ["g"]


def test_speech_plays_only_through_a_source_that_shows_the_avatar(av, renderers):
    _overlay(av, "guest")
    _avatar(av, "a")
    _avatar(av, "g", overlay="guest")
    renderers("main"), renderers("guest")

    def say(name):
        return av.post(f"/avatar/api/avatars/{name}/speak", files={"audio": ("hi.wav", _wav(), "audio/wav")}).json()

    assert say("a")["overlays"] == 1 and say("g")["overlays"] == 1       # each heard by the one source that shows it
    for c in list(av.mod.HUB.obs()):
        c.audio = False                                                   # both sources muted (?audio=0)
    quiet = say("g")
    assert quiet["overlays"] == 0 and "warning" in quiet
    av.mod.HUB.clients.clear()
    assert say("a")["overlays"] == 0                                      # no source at all


def test_the_render_socket_takes_its_overlay_from_the_url(av):
    _overlay(av, "guest")
    with av.websocket_connect("/avatar/ws/render?role=obs&overlay=Guest") as ws:
        snap = ws.receive_json()
        assert snap["config"]["overlays"] == ["main", "guest"]
        assert [c.overlay for c in av.mod.HUB.obs()] == ["guest"]
        with av.websocket_connect("/avatar/ws/render?role=obs") as ws2:   # no overlay in the URL: main
            ws2.receive_json()
            assert sorted(c.overlay for c in av.mod.HUB.obs()) == ["guest", "main"]
            with av.websocket_connect("/avatar/ws/render?role=preview&overlay=guest") as ws3:
                ws3.receive_json()                                        # the tab's preview draws any overlay, whatever the URL says
                assert [c.overlay for c in av.mod.HUB.of("render") if c.role == "preview"] == [None]
                st = av.get("/avatar/api/status").json()
                assert st["by_overlay"] == {"main": 1, "guest": 1} and st["overlays"] == 2 and st["previews"] == 1
                assert {o["id"]: o["sources"] for o in av.get("/avatar/api/overlays").json()["overlays"]} == {"main": 1, "guest": 1}


def test_a_config_message_carries_the_live_state_for_an_avatar_that_arrives(av):
    _overlay(av, "guest")
    _avatar(av, "g")
    assert av.post("/avatar/api/avatars/g/emotion", json={"name": "happy", "for": 60}).status_code == 200
    with av.websocket_connect("/avatar/ws/render?role=obs&overlay=guest") as ws:
        ws.receive_json()
        av.post("/avatar/api/avatars/g", json={"overlay": "guest"})       # g appears here, mid-emotion
        msg = ws.receive_json()
        assert msg["type"] == "config" and msg["live"]["g"]["emotion"]["name"] == "happy"
        assert msg["config"]["avatars"][0]["overlay"] == "guest"


def test_items(av):
    r = av.post("/avatar/api/items/upload", files={"file": ("Cat Hat.png", _png(40, 20), "image/png")})
    it = r.json()["item"]
    assert it["kind"] == "image" and (it["w"], it["h"]) == (40, 20)
    av.post("/avatar/api/avatars", json={"name": "main"})
    r = av.post("/avatar/api/avatars/main/item_add", json={"item": it["id"], "id": "hat", "y": 5, "layer": "back"})
    assert r.status_code == 200 and r.json()["items"][0]["layer"] == "back"
    r = av.post("/avatar/api/avatars/main/item_update", json={"id": "hat", "pin": {"mesh": "ArtMesh3", "tri": [1, 2, 3], "bary": [0.2, 0.3, 0.5]}})
    assert r.json()["items"][0]["pin"]["mesh"] == "ArtMesh3"
    assert av.post("/avatar/api/avatars/main/item_update", json={"id": "nope"}).status_code == 404
    av.post(f"/avatar/api/items/{it['id']}/delete")
    assert av.get("/avatar/api/avatars/main").json()["avatar"]["items"] == []    # deleting the item takes it off


def test_runtime_check_refuses_other_files(av):
    from hexcast_plugins.avatar import runtime
    assert runtime.check_core(b"console.log('hi')" * 5000) is not None
    assert av.post("/avatar/api/runtime/install", json={}).status_code == 400  # the license has to be accepted


# ---------------------------------------------------------------- custom inputs (VTube Studio's custom parameters)

def _row(name, inp, out, ilo, ihi, olo, ohi, **kw):
    return {"Name": name, "Input": inp, "OutputLive2D": out, "InputRangeLower": ilo, "InputRangeUpper": ihi,
            "OutputRangeLower": olo, "OutputRangeUpper": ohi, **kw}


# a model whose mappings take inputs of their own (My...), one of them twice, and one that is also a parameter id
CUSTOM_ROWS = [
    _row("Custom head X", "MyHeadX", "ParamAngleX", -30, 30, -30, 30, Smoothing=20),
    _row("Custom eye L", "MyEyeOpenL", "ParamEyeLOpen", 0, 1.3, 0, 1.3, UseBlinking=True),
    _row("Custom head X again", "MyHeadX", "ParamAngleZ", -30, 30, -30, 30),
    _row("Body", "ParamBodyAngleX", "ParamBodyAngleX", -30, 30, -10, 10),
]


def _custom_avatars(av, *names):
    m = _upload(av, _model_zip(extra_rows=CUSTOM_ROWS))
    for name in names or ("main",):
        assert av.post("/avatar/api/avatars", json={"name": name, "model": m["id"]}).status_code == 200
    return m


def test_info_lists_the_models_custom_inputs(av):
    _custom_avatars(av)
    info = av.get("/avatar/api/avatars/main/info").json()
    assert info["custom_inputs"] == ["MyHeadX", "MyEyeOpenL", "ParamBodyAngleX"]      # once each, in the mappings' order
    assert info["inputs"] == av.mod.INPUTS + info["custom_inputs"]                    # VTube Studio's, then the model's
    assert "MouthOpen" not in info["custom_inputs"]                                   # the model's standard row
    assert any(r["input"] == "MyEyeOpenL" and r["blink"] for r in info["mappings"])
    assert av.get("/avatar/api").json()["inputs"] == av.mod.INPUTS                    # the general list stays the standard one


def test_a_model_without_custom_inputs_lists_none(av):
    m = _upload(av)
    av.post("/avatar/api/avatars", json={"name": "main", "model": m["id"]})
    av.post("/avatar/api/avatars", json={"name": "bare"})                             # no model at all
    plain = _upload(av, _model_zip(vts=False, nested=""), "plain.zip")
    av.post("/avatar/api/avatars", json={"name": "plain", "model": plain["id"]})      # the default mappings
    for name in ("main", "bare", "plain"):
        info = av.get(f"/avatar/api/avatars/{name}/info").json()
        assert info["custom_inputs"] == [] and info["inputs"] == av.mod.INPUTS, name


def test_params_for_a_custom_input_are_held_like_any_other(av):
    _custom_avatars(av)
    r = av.post("/avatar/api/avatars/main/params", json={"values": {"MyHeadX": 28, "ParamAngleZ": 10}, "for": 3})
    assert r.status_code == 200 and r.json()["values"] == {"MyHeadX": 28.0, "ParamAngleZ": 10.0}
    live = av.get("/avatar/api/avatars").json()["live"]["main"]
    assert set(live["params"]) == {"MyHeadX", "ParamAngleZ"} and live["params"]["MyHeadX"]["until"]
    av.post("/avatar/api/avatars/main/release", json={"ids": ["MyHeadX"]})
    assert set(av.get("/avatar/api/avatars").json()["live"]["main"]["params"]) == {"ParamAngleZ"}


# ---------------------------------------------------------------- Activity: a stream of params is not a line each

@pytest.fixture
def activity(av, monkeypatch):
    """The Activity lines the panels would be sent (a short window) - and every command the renderers got."""
    class Lines(list):
        rendered: list = []

    mod = av.mod
    lines, rendered = Lines(), []

    async def panels(payload):
        lines.append(payload)

    async def renderers(payload, avatar_name=None):
        rendered.append(payload)

    monkeypatch.setattr(mod.HUB, "panels", panels)
    monkeypatch.setattr(mod.HUB, "renderers", renderers)
    monkeypatch.setattr(mod, "ACTIVITY_WINDOW", 0.4)
    mod.BURSTS.clear()
    _custom_avatars(av, "main", "guest")
    lines.clear()
    rendered.clear()
    lines.rendered = rendered
    yield lines
    mod.BURSTS.clear()


def _commands_in(line):
    m = re.match(r"x(\d+)", line["summary"])
    return int(m.group(1)) if m else 1


def _params(i=0):
    return {"cmd": "params", "values": {"MyHeadX": i % 30, "MyEyeOpenL": 1}, "hold": False}


def test_a_stream_of_params_is_a_line_or_two_not_a_line_each(av, activity):
    async def stream():
        for i in range(300):
            await av.mod.command("main", _params(i), "ws")
        first = list(activity)
        await asyncio.sleep(0.8)                          # the window ends: the rest comes as one line
        return first

    first = asyncio.run(stream())
    assert len(first) == 1 and first[0]["summary"] == "MyHeadX=0, MyEyeOpenL=1"     # a lone (first) command reads as ever
    lines = [ln for ln in activity if ln["cmd"] == "params"]
    assert 2 <= len(lines) <= 4                           # (a very slow machine may cross a window)
    assert sum(_commands_in(ln) for ln in lines) == 300   # none is lost from the count
    assert lines[1]["summary"].startswith("x") and "MyHeadX" in lines[1]["summary"] and "MyEyeOpenL" in lines[1]["summary"]
    assert {ln["avatar"] for ln in lines} == {"main"}
    assert len(activity.rendered) == 300                  # the renderers still get every single one


def test_a_stream_that_runs_for_seconds_reports_a_rate(av, activity, monkeypatch):
    monkeypatch.setattr(av.mod, "ACTIVITY_WINDOW", 1.5)

    async def stream():
        for i in range(60):                               # ~60 a second for ~1.2 s
            await av.mod.command("main", _params(i), "ws")
            await asyncio.sleep(0.02)
        await asyncio.sleep(1.2)

    asyncio.run(stream())
    summaries = [ln["summary"] for ln in activity if ln["cmd"] == "params"]
    assert len(summaries) <= 4 and sum(_commands_in({"summary": s}) for s in summaries) == 60
    # a window's worth of stream (a slow timer may leave a short tail of its own) says how fast it ran
    assert any(re.match(r"x\d+ \(\d+/s\): MyHeadX, MyEyeOpenL$", s) for s in summaries), summaries


def test_other_commands_keep_a_line_each_and_the_panel_has_none(av, activity):
    async def go():
        for name in ("nod", "shake", "tilt"):
            await av.mod.command("main", {"cmd": "gesture", "name": name}, "api")
        await av.mod.command("main", {"cmd": "emotion", "name": "happy"}, "api")
        for i in range(20):                               # what the panel itself sends is not Activity at all
            await av.mod.command("main", _params(i), "panel")
        await av.mod.command("main", {"cmd": "release"}, "panel")

    asyncio.run(go())
    assert [ln["cmd"] for ln in activity] == ["gesture", "gesture", "gesture", "emotion"]
    assert [ln["summary"] for ln in activity][:3] == ["nod", "shake", "tilt"]


def test_release_is_coalesced_too(av, activity):
    async def go():
        for _ in range(100):
            await av.mod.command("main", {"cmd": "release", "ids": ["MyHeadX"]}, "ws")
        await asyncio.sleep(0.8)

    asyncio.run(go())
    lines = [ln for ln in activity if ln["cmd"] == "release"]
    assert 2 <= len(lines) <= 4 and sum(_commands_in(ln) for ln in lines) == 100
    assert "MyHeadX" in lines[1]["summary"]


def test_a_websocket_stream_of_params_does_not_flood_the_panel(av, activity):
    """The same through the real /avatar/ws/control route, the way a bot streams a model's motion."""
    with av.websocket_connect("/avatar/ws/control") as ws:
        for i in range(500):
            ws.send_json({**_params(i), "avatar": "main"})
        time.sleep(1.0)                                       # the window ends, the rest is reported
    lines = [ln for ln in activity if ln["cmd"] == "params"]
    assert 2 <= len(lines) <= 5 and sum(_commands_in(ln) for ln in lines) == 500
    assert len(activity.rendered) == 500


def test_every_avatar_has_its_own_window_and_a_quiet_spell_starts_over(av, activity):
    async def go():
        await av.mod.command("main", _params(), "ws")
        await av.mod.command("guest", _params(), "ws")    # its own first line, not folded into main's
        await av.mod.command("main", _params(), "ws")     # folded: main's window is open
        await asyncio.sleep(1.0)                          # ... and reported when it ends (0.4 s); then a whole window of quiet
        mark = len(activity)
        await av.mod.command("main", _params(), "ws")     # after a quiet spell: a line at once again
        return mark

    mark = asyncio.run(go())
    assert [(ln["avatar"], _commands_in(ln)) for ln in activity[:2]] == [("main", 1), ("guest", 1)]
    assert [(ln["avatar"], _commands_in(ln)) for ln in activity[2:mark]] == [("main", 1)]     # the one that was folded
    assert [(ln["avatar"], _commands_in(ln)) for ln in activity[mark:]] == [("main", 1)]
    assert activity[mark]["summary"] == "MyHeadX=0, MyEyeOpenL=1"


def test_unloading_the_plugin_drops_the_pending_activity_line(av, activity):
    async def go():
        for i in range(5):
            await av.mod.command("main", _params(i), "ws")
        assert av.mod.BURSTS[("main", "params")]["timer"] is not None        # the tail is waiting for its window
        await av.mod.detach()
        await asyncio.sleep(0.7)

    asyncio.run(go())
    assert len(activity) == 1 and not av.mod.BURSTS


# ---------------------------------------------------------------- the renderer: what a params key means (needs node)

@pytest.fixture(scope="module")
def engine():
    """The real avatar_engine.js driven frame by frame under node (tests/avatar_engine_*.js): the numbers it produced."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(HERE / "avatar_engine_scenarios.js"),
                        str(ROOT / "catalog" / "avatar" / "static" / "avatar_engine.js")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_custom_input_goes_through_its_mapping_and_eases(engine):
    assert engine["rest"] == 0
    assert 0 < engine["firstFrame"] < 14                                  # it eases (smoothing 20) ...
    assert engine["after05"] == pytest.approx(28, abs=0.5)                # ... to the value
    assert engine["after27"] == pytest.approx(28, abs=0.01)
    assert 0 < engine["after32"] < 20 and abs(engine["after42"]) < 0.5    # `for` 3 s, then it lets go


def test_mapping_inputs_win_over_parameters_and_other_keys_stay_raw(engine):
    assert engine["eyeL"] == pytest.approx(0.5) and engine["eyeRUntouched"] == 1
    assert engine["bodyAngle"] == pytest.approx(10)                       # the mapping's out [-10, 10], not the raw 30
    assert engine["toe"] == pytest.approx(10)                             # MyTap is mapped to itself: input 1 -> out 10
    assert engine["angleZ"] == pytest.approx(10)                          # not a mapping input: a raw override, as ever
    assert engine["kinds"] == [True, True, False, True]


def test_held_params_that_arrive_before_the_model_still_land_as_inputs(engine):
    assert engine["beforeModel"] == [False, False]                        # nothing is known yet ...
    assert engine["afterModel"] == [True, False]                          # ... the model's mappings decide when they compile
    assert engine["restoredHead"] == pytest.approx(20, abs=0.01) and engine["restoredZ"] == pytest.approx(7, abs=0.01)
    assert engine["afterOtherModel"] == [False, False]                    # another model: plain keys again


def test_blinking_reaches_custom_eye_rows(engine):
    on = engine["blinkOn"]
    assert on["min"] < 0.05 and on["max"] > 0.99 and on["dips"] >= 3      # held open, it dips to shut every few seconds (30 s)
    assert engine["blinkOff"] == {"min": 1, "max": 1, "dips": 0}          # idle.blink off: no blink
    assert engine["notMarked"] == {"min": 1, "max": 1, "dips": 0}         # a row not marked "use blinking": none


def test_release_lets_a_custom_input_go_and_odd_keys_do_no_harm(engine):
    assert engine["held"] == pytest.approx(20, abs=0.01) and abs(engine["released"]) < 0.5 and engine["cleared"]
    assert engine["oddKeysFinite"]


def test_a_stage_draws_the_avatars_of_its_own_overlay(engine):
    assert engine["ovMain"] == ["a", "b"] and engine["ovGuest"] == ["c"]  # `a` has no overlay (an old config): it is on main
    assert engine["ovEvery"] == ["a", "b", "c"]                           # a stage with no overlay (the tab) draws them all
    assert engine["ovNarrow"] == ["a"]                                    # ?avatar= narrows an overlay, it does not reach into another
    assert engine["ovMovedMain"] == ["a"] and engine["ovMovedGuest"] == ["b", "c"]       # moving b: it leaves one stage, joins the other
    assert engine["ovPreviewMain"] == ["a"] and engine["ovPreviewGuest"] == ["b", "c"] and engine["ovPreviewBack"] == ["a"]
    assert engine["ovLeavesTheSelectionOutline"]                          # `stage.overlay` is the selection graphics: switching overlays must not touch it
    assert engine["ovSameOverlayKeepsAvatars"]                            # switching to the overlay it is on redraws nothing
    assert engine["ovArrivedEmotion"] == "happy"                          # an avatar that arrives starts in its live state


def test_standard_inputs_and_raw_parameters_are_unchanged(engine):
    assert engine["stdAngleX"] == pytest.approx(12, abs=0.01) and engine["stdMouth"] == pytest.approx(0.6)
    assert 0 < engine["stdCheekEasing"] < 0.7 and engine["stdCheek"] == pytest.approx(0.7)
    assert engine["stdKinds"] == [True, True, False]
    assert engine["stdBlinkMin"] < 0.5                                    # the standard eye rows still blink (through EyeOpen*)


def test_a_pngtubers_mouth_opens_and_shuts_with_the_words_instead_of_staying_open(engine):
    follow, hold = engine["pngFollow"], engine["pngHold"]
    assert follow["opens"] >= 16                                          # 20 syllables: it opens for (nearly) each one ...
    assert 25 <= follow["openPct"] <= 65                                  # ... and is shut a good part of the time
    assert hold["opens"] <= 3 and hold["openPct"] >= 65                   # the old behaviour: open once, held open through the 4 s of speech
    assert follow["openPct"] + 20 < hold["openPct"]
    assert not follow["stuckOpenAfter"] and not hold["stuckOpenAfter"]    # it shuts when the voice ends, either way


def test_a_steady_voice_keeps_the_mouth_open_and_silence_or_a_whisper_never_opens_it(engine):
    assert engine["pngSteady"]["opens"] == 1                              # one long vowel: one opening, no flicker
    assert engine["pngSilent"]["opens"] == 0 and engine["pngLoudLevel"] == 0
    assert engine["pngGate"] == {"openSteady": True, "shutWithinThirdOfASecond": True}


def test_word_detail_decides_how_deep_a_dip_has_to_be_to_shut_the_mouth(engine):
    assert engine["pngLazy"]["opens"] < engine["pngFollow"]["opens"] <= engine["pngSnappy"]["opens"]
    assert engine["pngLazy"]["opens"] <= 6                                # lazy: only the pauses between words shut it


def test_a_pngtuber_hops_with_the_words_as_the_audio_comes_in(engine):
    assert engine["pngFollowNoBeat"]["kicks"] == 1                        # with no voice bounce: just the hop when speech starts
    assert engine["pngFollow"]["kicks"] >= 10                             # with it: a hop for most words
    assert engine["pngFollow"]["maxBy"] >= -45                            # (the small hops stack on nothing: no floating off)
    assert engine["pngNoBounce"]["kicks"] == 0                            # bounce 0 and beat 0: it stays put


def test_multiply_goes_over_the_models_own_colours_and_a_folders_reaches_what_is_inside(engine):
    assert engine["mulHair"] == [[1, 1, 1], [1, 0.502, 0], [1, 0.502, 0], [0.5, 0.5, 0.5], [1, 1, 1]]     # Hair and the Fringe inside it - nothing else
    assert engine["mulHairFlags"] == [False, True, True, False, False]                                    # only the meshes it colours are taken over
    assert engine["mulWhole"] == [[0.502] * 3, [0.502] * 3, [0.502] * 3, [0.251] * 3, [0.502] * 3]       # `all`: every mesh; the eye's own grey (.5) stays in
    assert engine["mulBoth"] == [0.502, 0, 0]                                                             # a part's multiply x the whole model's
    assert engine["mulFolders"] == [[0, 0, 1], [0, 0, 0], [0, 0, 0]]                                      # Body blue: the hair inside it is blue x red
    assert engine["mulMesh"] == [[1, 1, 1], [1, 0.502, 0], [0, 0.502, 0]]                                 # one mesh: its own, on top of its part's


def test_overlay_lightens_with_a_screen_colour_that_stacks(engine):
    assert engine["overlay"] == [[0, 0, 0], [0.251] * 3, [0.251] * 3, [0.602] * 3, [0, 0, 0]]            # the eye's own screen colour (.2) screens with the new .5
    assert engine["overlayLeavesMultiply"] == [[1, 1, 1], [1, 1, 1], [1, 1, 1], [0.5, 0.5, 0.5], [1, 1, 1]]
    assert engine["overlayStacks"] == [0.752] * 3                                                         # two screens of .5: 1 - .5 x .5


def test_alpha_hides_or_fades_meshes_and_does_not_compound(engine):
    assert engine["alphaMesh"] == [1, 1, 0, 1, 1]                                                         # invisible: one mesh, nothing else
    assert engine["alphaStacks"] == [1, 0.5, 0.25, 1, 1]                                                  # a part's alpha x the mesh's own
    assert engine["alphaDoesNotCompound"] == engine["alphaStacks"]                                        # the model refreshes its opacities every frame
    assert engine["alphaWhole"] == [0.2] * 5
    assert engine["alphaStillModel"] == [1, 0.5, 0.5, 1, 1]                                               # a still model rewrites nothing: the alpha holds, it does not shrink
    assert engine["alphaOverTheModelsValue"] == 0.4                                                       # the model sets a mesh to .8 itself: .8 x .5
    assert engine["alphaHandedBack"] == [1, 0.8, 1, 1, 1]                                                 # and the model's own value is what comes back


def test_the_preview_flashes_what_is_selected_and_a_hidden_mesh_shows_through(engine):
    assert engine["flashIgnoredOnStream"]                                                                 # only the tab's preview flashes - never OBS
    assert engine["flash"] == {"hairLit": True, "ghostShowsThrough": True, "bodyUntouched": True, "ended": True}
    assert engine["flashLeavesNothingBehind"] == [[0, 0, 0], 0, 1]                                        # hidden again, the hair as it was
    assert engine["flashOneMesh"] == [True, True] and engine["flashOneMeshEnded"] == [0.2] * 3            # one mesh: only that one


def test_a_colour_eases_in_and_out_and_hands_the_model_back(engine):
    assert engine["nothingTouchesNothing"]
    assert 0.8 < engine["easeFirst"] < 1 and 0.1 < engine["easeMid"] < 0.5 and engine["easeEnd"] == 0   # fade 0.6 s: white -> black
    assert 0.5 < engine["releaseMid"] < 1 and engine["releaseEnd"] == 1
    assert engine["releaseHandsBack"]                                                                    # no mesh is left taken over
    assert engine["heldAlpha"] == 0 and engine["heldAlphaReleased"] == 1
    assert engine["savedCleared"]                                                                        # the same when a saved colour is cleared in the tab


def test_colours_come_in_layers_a_later_one_winning_field_by_field(engine):
    assert engine["layerBlue"] == [0, 0, 1] and engine["layerRed"] == [1, 0, 0] and engine["layerRedOverlay"] == [0.125] * 3
    assert engine["layerRedOff"] == [[0, 0, 1], [0, 0, 0]] and engine["layerSaved"] == [0, 1, 0]        # a preset off: the layer under it is back
    assert engine["heldWinsOverPreset"] == [[1, 1, 0], [0.125] * 3]                                      # the held multiply, the preset's overlay
    assert engine["nullGoesBack"] == [1, 0, 0] and engine["clearedPresets"] == [[0, 1, 0], [0, 0, 0]]
    assert engine["untilHolds"] == [1, 0, 0] and engine["untilEnds"] == [0, 1, 0]
    assert engine["presetUntilHolds"] == [1, 0, 0] and engine["presetUntilEnds"] == [0, 1, 0]
    assert engine["restoredAtOnce"] == [[1, 0, 0], 0.5]                                                  # a renderer that joins late does not fade it in


def test_colours_survive_a_new_model_and_an_old_runtime_and_reach_pngtuber_layers(engine):
    assert engine["reloadColoursTheNewModel"] == [1, 0, 0] and engine["oldRuntimeIsHarmless"]
    assert engine["pngLayer"] == [0x804000, 0x808080, 0xFFFFFF, 0.25, 0.5, 1]                            # a layer: its own x `all` (tint and alpha; no overlay)


# ---------------------------------------------------------------- pin points, items locked / pinned by name, avatars hanging from avatars

PIN = {"mesh": "ArtHead", "tri": [3, 4, 5], "bary": [0.2, 0.3, 0.5], "angle0": 0.1}


def _two(av):
    """A model with a pin point, an avatar `hex` showing it and a free avatar `mini` (no model)."""
    m = _upload(av)
    av.post("/avatar/api/avatars", json={"name": "hex", "model": m["id"]})
    av.post("/avatar/api/avatars", json={"name": "mini"})
    return m["id"]


def test_pin_points_are_saved_on_the_model_and_listed(av):
    mid = _two(av)
    path = f"/avatar/api/models/{mid}/anchors"
    assert av.get(path).json() == {"anchors": {}}
    r = av.post(path, json={"name": "  head   top ", "pin": PIN}).json()
    assert r["name"] == "head top" and r["anchors"] == ["head top"]
    assert av.get(path).json()["anchors"]["head top"] == {**PIN, "follow_angle": True}
    assert [m for m in av.get("/avatar/api/models").json()["models"] if m["id"] == mid][0]["anchors"]["head top"]["mesh"] == "ArtHead"
    assert av.get("/avatar/api/avatars/hex/info").json()["anchors"] == ["head top"]
    assert av.post(path, json={"name": "x"}).status_code == 400                        # no pin
    assert av.post(path, json={"name": "x", "pin": {"mesh": "A", "tri": [1, 2], "bary": [1, 0, 0]}}).status_code == 400
    assert av.post(path, json={"pin": PIN}).status_code == 400                         # no name
    av.post(path, json={"name": "HEAD TOP", "pin": {**PIN, "mesh": "ArtHead2"}})       # a name is one pin point, whatever its case
    assert list(av.lib.anchors(mid)) == ["HEAD TOP"] and av.lib.anchors(mid)["HEAD TOP"]["mesh"] == "ArtHead2"
    assert av.post(path + "/head top/delete").json()["anchors"] == []
    assert av.post(path + "/nope/delete").status_code == 404
    assert not (av.lib.model_dir(mid) / "hexcast.anchors.json").exists()                # nothing left, no file left


def test_pin_points_go_with_a_copy_of_the_model(av):
    mid = _two(av)
    av.post(f"/avatar/api/models/{mid}/anchors", json={"name": "hand", "pin": PIN})
    av.post(f"/avatar/api/models/{mid}/color_presets", json={"name": "Night", "look": {"all": {"multiply": "#334466"}}})
    new = av.post(f"/avatar/api/models/{mid}/color_presets/night/as_model", json={"name": "Night Copy"}).json()["model"]
    assert new["anchors"]["hand"]["mesh"] == "ArtHead"


def test_items_take_a_pin_point_by_name_and_can_be_locked(av):
    mid = _two(av)
    item = av.post("/avatar/api/items/upload", files={"file": ("Cat Hat.png", _png(40, 20), "image/png")}).json()["item"]
    av.post(f"/avatar/api/models/{mid}/anchors", json={"name": "Head Top", "pin": PIN})
    r = av.post("/avatar/api/avatars/hex/item_add", json={"item": item["id"], "id": "hat", "anchor": "head top", "locked": True})
    got = r.json()["items"][0]
    assert got["anchor"] == "Head Top" and got["locked"] is True and got["pin"] is None       # (the model's own spelling)
    bad = av.post("/avatar/api/avatars/hex/item_update", json={"id": "hat", "anchor": "tail"})
    assert bad.status_code == 404 and "Head Top" in bad.json()["error"]                      # an error that lists them
    assert av.post("/avatar/api/avatars/mini/item_add", json={"item": item["id"], "anchor": "x"}).status_code == 404       # no model: no pin points
    r = av.post("/avatar/api/avatars/hex/item_update", json={"id": "hat", "anchor": "", "locked": False, "pin": PIN})
    got = r.json()["items"][0]
    assert got["anchor"] == "" and got["locked"] is False and got["pin"]["mesh"] == "ArtHead"
    assert av.get("/avatar/api/avatars/hex").json()["avatar"]["items"][0]["pin"]["bary"] == [0.2, 0.3, 0.5]


def test_an_avatar_can_hang_from_another(av):
    mid = _two(av)
    av.post(f"/avatar/api/models/{mid}/anchors", json={"name": "Head Top", "pin": PIN})
    r = av.post("/avatar/api/avatars/mini/attach", json={"to": "HEX", "anchor": "head top", "dy": -45, "layer": "back"}).json()
    at = r["attach"]
    assert at["to"] == "hex" and at["anchor"] == "Head Top" and at["dy"] == -45 and at["layer"] == "back" and at["follow_angle"] is True
    assert r["note"] == ""
    assert av.get("/avatar/api/avatars/mini").json()["avatar"]["attach"]["anchor"] == "Head Top"
    assert av.get("/avatar/api/avatars/mini/info").json()["attach"]["to"] == "hex"
    # later calls change what they name and keep the rest
    at = av.post("/avatar/api/avatars/mini/attach", json={"dx": 12, "mirror": True}).json()["attach"]
    assert at["to"] == "hex" and at["anchor"] == "Head Top" and at["dy"] == -45 and at["dx"] == 12 and at["mirror"] is True
    at = av.post("/avatar/api/avatars/mini/attach", json={"anchor": "", "pin": PIN, "x": 40}).json()["attach"]
    assert at["anchor"] == "" and at["pin"]["mesh"] == "ArtHead" and at["x"] == 40
    # another parent starts afresh
    av.post("/avatar/api/avatars", json={"name": "third"})
    at = av.post("/avatar/api/avatars/mini/attach", json={"to": "third"}).json()["attach"]
    assert at["to"] == "third" and at["anchor"] == "" and at["pin"] is None and at["dy"] == 0
    # mistakes are named
    assert av.post("/avatar/api/avatars/mini/attach", json={"to": "hex", "anchor": "tail"}).status_code == 404
    assert av.post("/avatar/api/avatars/mini/attach", json={"to": "ghost"}).status_code == 404
    assert av.post("/avatar/api/avatars/mini/attach", json={"to": "mini"}).status_code == 400
    assert av.post("/avatar/api/avatars/mini/attach", json={"to": "Not A Name!"}).status_code == 400
    assert av.post("/avatar/api/avatars/hex/attach", json={"dx": 5}).status_code == 400                       # nothing to hang from
    assert av.get("/avatar/api/avatars/mini").json()["avatar"]["attach"]["to"] == "third"                     # failed calls changed nothing


def test_avatars_cannot_hang_in_a_loop_or_too_deep(av):
    for n in "abcdef":
        av.post("/avatar/api/avatars", json={"name": n})
    assert av.post("/avatar/api/avatars/b/attach", json={"to": "a"}).status_code == 200
    assert av.post("/avatar/api/avatars/c/attach", json={"to": "b"}).status_code == 200
    r = av.post("/avatar/api/avatars/a/attach", json={"to": "c"})                                             # a on c on b on a
    assert r.status_code == 400 and "loop" in r.json()["error"]
    assert av.post("/avatar/api/avatars/d/attach", json={"to": "c"}).status_code == 200
    assert av.post("/avatar/api/avatars/e/attach", json={"to": "d"}).status_code == 200
    r = av.post("/avatar/api/avatars/f/attach", json={"to": "e"})                                             # five deep
    assert r.status_code == 400 and "deep" in r.json()["error"]
    # the settings call checks the same
    assert av.post("/avatar/api/avatars/a", json={"attach": {"to": "e"}}).status_code == 400
    assert av.post("/avatar/api/avatars/a", json={"attach": {"to": "nobody"}}).status_code == 404


def test_detach_lets_go_and_can_place_the_avatar(av):
    _two(av)
    av.post("/avatar/api/avatars/mini/attach", json={"to": "hex"})
    r = av.post("/avatar/api/avatars/mini/detach", json={}).json()
    assert r["attach"] is None and av.get("/avatar/api/avatars/mini").json()["avatar"]["attach"] is None
    av.post("/avatar/api/avatars/mini/attach", json={"to": "hex"})
    av.post("/avatar/api/avatars/mini/detach", json={"x": 61.5, "y": 70, "scale": 0.4, "rotation": 12, "flip": True})
    a = av.get("/avatar/api/avatars/mini").json()["avatar"]
    assert a["attach"] is None and (a["x"], a["y"], a["scale"], a["rotation"], a["flip"]) == (61.5, 70.0, 0.4, 12.0, True)
    # the avatar's own settings call can hang it too, and let it go with null
    assert av.post("/avatar/api/avatars/mini", json={"attach": {"to": "hex", "dy": -20}}).json()["avatar"]["attach"]["dy"] == -20
    assert av.post("/avatar/api/avatars/mini", json={"x": 30}).json()["avatar"]["attach"]["to"] == "hex"       # other settings leave it
    assert av.post("/avatar/api/avatars/mini", json={"attach": None}).json()["avatar"]["attach"] is None


def test_what_hangs_from_an_avatar_follows_its_rename_and_lets_go_when_it_is_deleted(av):
    _two(av)
    av.post("/avatar/api/avatars/mini/attach", json={"to": "hex", "dy": -30})
    assert av.post("/avatar/api/avatars/hex/rename", json={"to": "boss"}).status_code == 200
    assert av.get("/avatar/api/avatars/mini").json()["avatar"]["attach"]["to"] == "boss"
    av.post("/avatar/api/avatars/boss/delete")
    assert av.get("/avatar/api/avatars/mini").json()["avatar"]["attach"] is None
    # a saved config that names itself (or nothing usable) loads as free-standing
    assert av.mod.norm_attach({"to": "mini"}, "mini") is None and av.mod.norm_attach("hex") is None and av.mod.norm_attach({}) is None


def test_attach_reads_in_the_activity_line_and_warns_about_another_overlay(av):
    _two(av)
    r = av.post("/avatar/api/avatars/mini/attach", json={"to": "hex"}).json()
    assert r["cmd"] == "attach" and av.mod._summary("attach", r) == "to hex"
    assert av.mod._summary("detach", {}) == "let go"
    av.post("/avatar/api/overlays", json={"name": "guest"})
    av.post("/avatar/api/avatars/mini", json={"overlay": "guest"})
    assert "only hangs from it while they share an overlay" in av.post("/avatar/api/avatars/mini/attach", json={"dx": 1}).json()["note"]


# ---------------------------------------------------------------- the renderer: things glued to an avatar (needs node)

@pytest.fixture(scope="module")
def mount():
    """avatar_engine.js with a fake parent model (tests/avatar_engine_mount.js): where an avatar hanging from it, an item and a clip end up."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(HERE / "avatar_engine_mount.js"), str(ROOT / "catalog" / "avatar" / "static" / "avatar_engine.js")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_an_avatar_hangs_from_the_pin_point_and_goes_where_the_part_goes(mount):
    # the parent is a triangle at the middle of the stage; its pin is the triangle's middle, (33.3, 33.3) from its centre
    assert mount["onPin"] == {"x": 993.333, "y": 573.333, "rot": 0, "sx": 0.5, "sy": 0.5}
    # the head turns a quarter: the point swings round with it, and the child turns with the part ...
    assert mount["turned"]["x"] == pytest.approx(926.667, abs=0.01) and mount["turned"]["rot"] == pytest.approx(1.571, abs=0.001)
    assert mount["upright"]["rot"] == 0 and mount["upright"]["x"] == mount["turned"]["x"]            # ... unless it should stay upright
    # the whole parent moved, doubled and turned a quarter carries the child along (its size doubles with it)
    assert mount["carried"] == {"x": 33.333, "y": 266.667, "rot": 1.571, "sx": 1, "sy": 1}
    # the nudge is in the child's own size (a quarter of its 600 wide box, half its 1080 height up), scaled with it
    assert mount["nudged"]["x"] == pytest.approx(993.333 + 75, abs=0.01) and mount["nudged"]["y"] == pytest.approx(573.333 - 270, abs=0.01)


def test_a_flipped_parent_mirrors_the_spot_but_not_the_childs_art_unless_asked(mount):
    assert mount["flippedParent"]["x"] == pytest.approx(926.667, abs=0.01) and mount["flippedParent"]["mirrored"] is False
    assert mount["flippedParentMirror"]["mirrored"] is True
    assert mount["ownFlip"]["mirrored"] is True and mount["ownFlip"]["x"] == pytest.approx(993.333, abs=0.01)    # (its own flip mirrors the art, not the spot)


def test_the_child_falls_back_to_a_free_spot_or_to_its_own_place(mount):
    assert mount["freeSpot"]["x"] == pytest.approx(1020) and mount["freeSpot"]["y"] == pytest.approx(324)       # % of the parent's box, around its centre
    assert mount["noParent"] == {"x": 480, "y": 810, "parent": None}


def test_pin_points_are_found_by_name_in_any_case(mount):
    a = mount["anchor"]
    assert a["exact"]["pinned"] is True and a["exact"]["x"] == 0 and a["exact"]["y"] == 0               # the pin point's own pin, not the raw one given
    assert a["anyCase"] == pytest.approx(33.333, abs=0.01)
    assert a["unknown"] == pytest.approx(33.333, abs=0.01) and a["none"] is None                          # a name the model lacks: the raw pin
    assert a["freeBox"] == {"x": 300, "y": -540, "rot": 0, "pinned": False}


def test_parents_update_first_children_draw_beside_them_and_loops_come_apart(mount):
    m = mount["mounts"]
    assert m["update"][:3] == ["a", "b", "c"]                                  # a parent before what hangs from it, whatever the list order
    assert m["parents"] == ["b", "a", None, None, None, None, None]            # c on b on a; a loop (x, y) and a missing parent hang from nothing
    assert m["z"]["b"] < m["z"]["c"] < m["z"]["a"]                              # b behind a; c in front of b but still behind a
    assert m["draw"].index("b") < m["draw"].index("c") < m["draw"].index("a")


def test_letting_go_keeps_the_place_the_avatar_stood_in(mount):
    p = mount["placed"]
    assert (p["x"], p["y"], p["scale"], p["rotation"], p["flip"]) == (1.7, 24.7, 1, 90, False)
    assert mount["placedBesideFlipped"] == {"x": 48.3, "y": 53.1, "scale": 0.5, "rotation": 0, "flip": False}
    assert mount["placedMirrored"]["flip"] is True


def test_an_item_rides_its_pin_point_by_name(mount):
    assert mount["item"]["x"] == pytest.approx(-33.333, abs=0.01) and mount["item"]["y"] == pytest.approx(33.333, abs=0.01)
    assert mount["item"]["rot"] == pytest.approx(3.142, abs=0.001)             # its own 90 degrees plus the quarter turn of the part
    assert mount["itemFree"] == {"x": 60, "y": 0, "rot": 0}                    # no pin point: % of the model's box


def test_a_clip_locked_to_an_avatar_is_put_where_its_pin_point_is_on_the_page(mount):
    c = mount["clip"]
    assert c["matrix"] == [0.5, 0, 0, 0.5, 506.6667, 306.6667] and c["shown"] == "visible"      # (half size, 10 px and 20 px in)
    assert c["rest"] == "rotate(15deg) scale(2) translate(5%,-10%) scaleX(1) translate(-50%,-50%)"
    assert mount["clipHidden"] == "hidden" and mount["clipFlipped"] is True
