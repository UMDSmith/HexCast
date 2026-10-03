"""The Avatars plugin: its library (models, items, VTube Studio settings) and its API - no browser."""

import base64
import io
import json
import struct
import zipfile
import zlib

import pytest
from fastapi.testclient import TestClient


def _png(w=8, h=6) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _model_zip(moc_version=3, vts=True, nested="My Model/runtime/") -> bytes:
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
                                       "OutputRangeUpper": 2.1, "Smoothing": 0}],
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
    avatar.CONFIG.update({"avatars": [], "stage": avatar.norm_stage({})})
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
