"""The avatar library: Live2D models and items, kept in media/avatars/.

    media/avatars/models/<id>/   one Live2D model folder each (the .model3.json, .moc3, textures,
                                 motions, expressions ... exactly as the model's author shipped it),
                                 plus hexcast.json - Hexcast's own settings for that model
    media/avatars/items/<id>/    one item each: a picture, an animated GIF, a folder of numbered
                                 frames (name_1.png, name_2.png ...) or a Live2D item

Everything is served at /avatar/lib/... (the plugin mounts media/avatars there). A model comes in
as a zip, as a folder picked in the browser, or copied from a folder on this PC (a VTube Studio
model folder works as it is: its .vtube.json is read for the parameter mappings, hotkeys and art mesh colours).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from hexcast_core import paths

from . import pngtuber

ROOT = paths.MEDIA_DIR / "avatars"
MODELS_DIR = ROOT / "models"
ITEMS_DIR = ROOT / "items"
URL = "/avatar/lib"
SETTINGS_FILE = "hexcast.json"
COLORS_FILE = "hexcast.colors.json"          # the model's colour presets (Live2D and PNGtuber alike)
ANCHORS_FILE = "hexcast.anchors.json"        # the model's named pin points (Live2D and PNGtuber alike)

# The newest .moc3 format the Cubism Core Hexcast downloads can read (Core 5.1 -> moc3 v5, which is
# every model saved by Cubism Editor 3.0 - 5.2). Cubism 5.3 saves v6 - the web engine cannot draw
# those yet, so they are refused with a clear message instead of a blank overlay.
MOC_SUPPORTED = 5
MOC_EDITOR = {1: "3.0", 2: "3.3", 3: "4.0", 4: "4.2", 5: "5.0", 6: "5.3"}

MAX_ZIP_BYTES = 2 * 1024 ** 3          # uncompressed, whole model
MAX_FILE_BYTES = 768 * 1024 ** 2       # any one file
MAX_FILES = 6000
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_SLUG = re.compile(r"[^a-z0-9_-]+")
_FRAME = re.compile(r"^(.*?)[_ -]?(\d+)$")


class LibraryError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def ensure_dirs() -> None:
    for d in (MODELS_DIR, ITEMS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def slug(name: str, fallback: str = "model") -> str:
    """A folder / id from a display name: lowercase letters, digits, _ and -."""
    s = _SLUG.sub("-", str(name or "").lower()).strip("-_")
    return (s or fallback)[:40].strip("-_") or fallback


def _unique(folder: Path, base: str) -> str:
    name, n = base, 2
    while (folder / name).exists():
        name = f"{base}-{n}"
        n += 1
    return name


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _url(*parts: str) -> str:
    from urllib.parse import quote
    return URL + "/" + "/".join(quote(p) for part in parts for p in str(part).split("/") if p)


# --------------------------------------------------------------------------------------------
# files of a Live2D model
# --------------------------------------------------------------------------------------------

def moc_version(path: Path) -> int | None:
    """The .moc3 format version: the byte after the 'MOC3' magic."""
    try:
        with open(path, "rb") as f:
            head = f.read(5)
    except OSError:
        return None
    return head[4] if len(head) == 5 and head[:4] == b"MOC3" else None


def find_model3(root: Path) -> Path | None:
    """The model's .model3.json: the shallowest one under `root` (ties: alphabetical)."""
    found = sorted((p for p in root.rglob("*.model3.json") if p.is_file()),
                   key=lambda p: (len(p.relative_to(root).parts), p.as_posix().lower()))
    return found[0] if found else None


def _stem(name: str, suffix: str) -> str:
    return name[: -len(suffix)] if name.lower().endswith(suffix) else Path(name).stem


def _vts_file(model3: Path) -> Path | None:
    near = sorted(model3.parent.glob("*.vtube.json"))
    return near[0] if near else None


# VTube Studio's own default setup, used for a model that comes without a .vtube.json. Each row is
# [name, input, output, in_lo, in_hi, out_lo, out_hi, smoothing, flags] - flags: b = blink, r = breath.
# A row whose output parameter the model doesn't have is skipped by the renderer.
DEFAULT_MAPPINGS = [
    ["Face Left/Right", "FaceAngleX", "ParamAngleX", -30, 30, -30, 30, 15, ""],
    ["Face Up/Down", "FaceAngleY", "ParamAngleY", -20, 20, -30, 30, 15, ""],
    ["Face Lean", "FaceAngleZ", "ParamAngleZ", -30, 30, -30, 30, 30, ""],
    ["Body Left/Right", "FaceAngleX", "ParamBodyAngleX", -30, 30, -10, 10, 20, ""],
    ["Body Up/Down", "FaceAngleY", "ParamBodyAngleY", -30, 30, -10, 10, 20, ""],
    ["Body Lean", "FaceAngleZ", "ParamBodyAngleZ", -30, 30, -10, 10, 20, ""],
    ["Eye Open Left", "EyeOpenLeft", "ParamEyeLOpen", 0, 1, 0, 1, 10, "b"],
    ["Eye Open Right", "EyeOpenRight", "ParamEyeROpen", 0, 1, 0, 1, 10, "b"],
    ["Eye Smile Left", "MouthSmile", "ParamEyeLSmile", 0, 1, 0, 1, 10, ""],
    ["Eye Smile Right", "MouthSmile", "ParamEyeRSmile", 0, 1, 0, 1, 10, ""],
    ["Eye X", "EyeRightX", "ParamEyeBallX", -1, 1, 1, -1, 8, ""],
    ["Eye Y", "EyeRightY", "ParamEyeBallY", -1, 1, -1, 1, 8, ""],
    ["Brow Height Left", "Brows", "ParamBrowLY", 0, 1, -1, 1, 10, ""],
    ["Brow Height Right", "Brows", "ParamBrowRY", 0, 1, -1, 1, 10, ""],
    ["Mouth Smile", "MouthSmile", "ParamMouthForm", 0, 1, -1, 1, 0, ""],
    ["Mouth Open", "MouthOpen", "ParamMouthOpenY", 0, 1, 0, 1, 0, ""],
    ["Mouth X", "MouthX", "ParamMouthX", -1, 1, -1, 1, 0, ""],
    ["Cheek Puff", "CheekPuff", "ParamCheek", 0, 1, 0, 1, 10, ""],
    ["Vowel A", "VoiceA", "ParamA", 0, 1, 0, 1, 0, ""],
    ["Vowel I", "VoiceI", "ParamI", 0, 1, 0, 1, 0, ""],
    ["Vowel U", "VoiceU", "ParamU", 0, 1, 0, 1, 0, ""],
    ["Vowel E", "VoiceE", "ParamE", 0, 1, 0, 1, 0, ""],
    ["Vowel O", "VoiceO", "ParamO", 0, 1, 0, 1, 0, ""],
    ["Auto Breath", "", "ParamBreath", 0, 1, 0, 1, 0, "r"],
]


def _mapping(name, inp, out, ilo, ihi, olo, ohi, smooth, flags="", clamp_in=True, clamp_out=True) -> dict:
    def num(v, d=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d
    return {"name": str(name or out), "input": str(inp or ""), "output": str(out or ""),
            "in": [num(ilo), num(ihi, 1)], "out": [num(olo), num(ohi, 1)],
            "clamp_in": bool(clamp_in), "clamp_out": bool(clamp_out),
            "smoothing": max(0, min(100, int(num(smooth)))),
            "blink": "b" in flags, "breath": "r" in flags}


def default_mappings() -> list[dict]:
    return [_mapping(*row) for row in DEFAULT_MAPPINGS]


def read_vts(path: Path) -> dict:
    """What Hexcast takes from a VTube Studio .vtube.json: the parameter mappings (with the
    model author's ranges and smoothing), the hotkeys that toggle expressions or play
    animations, the idle animation and the physics switch."""
    d = _read_json(path) or {}
    out: dict = {"name": str(d.get("Name") or ""), "mappings": [], "hotkeys": [],
                 "idle_motion": "", "physics": True}
    for p in d.get("ParameterSettings") or []:
        if not isinstance(p, dict) or not p.get("OutputLive2D"):
            continue
        flags = ("b" if p.get("UseBlinking") else "") + ("r" if p.get("UseBreathing") else "")
        out["mappings"].append(_mapping(p.get("Name"), p.get("Input"), p.get("OutputLive2D"),
                                        p.get("InputRangeLower", 0), p.get("InputRangeUpper", 1),
                                        p.get("OutputRangeLower", 0), p.get("OutputRangeUpper", 1),
                                        p.get("Smoothing", 0), flags,
                                        p.get("ClampInput", True), p.get("ClampOutput", True)))
    kinds = {"ToggleExpression": "expression", "TriggerAnimation": "motion",
             "ChangeIdleAnimation": "idle", "RemoveAllExpressions": "clear_expressions"}
    for h in d.get("Hotkeys") or []:
        if isinstance(h, dict) and h.get("Action") in kinds:
            out["hotkeys"].append({"name": str(h.get("Name") or ""), "action": kinds[h["Action"]],
                                   "file": str(h.get("File") or "")})
    refs = d.get("FileReferences") or {}
    out["idle_motion"] = str(refs.get("IdleAnimation") or "")
    out["icon"] = str(refs.get("Icon") or "")
    phys = d.get("PhysicsSettings") or {}
    out["physics"] = bool(phys.get("Use", True))
    return out


# VTube Studio's art mesh colours. Its .vtube.json keeps them in two places: the model's own tints
# (ArtMeshDetails.ArtMeshMultiplyAndScreenColors) and, on a colour-preset hotkey, that preset
# (Hotkeys[].ColorScreenMultiplyPreset.ArtMeshMultiplyAndScreenColors). The file is not documented, so an entry is read
# loosely: the art mesh id(s) and a multiply and a screen colour, each as {r,g,b,a}, [r,g,b,a] or "#rrggbbaa" (0-1 or
# 0-255); the multiply colour's alpha is the mesh's alpha (its "A" slider). What comes out is a Hexcast look (see
# avatar.norm_look): {"meshes": {id: {"multiply", "overlay", "alpha"}}}.

VTS_COLORS_NAME = "VTube Studio"                  # the preset the model's own tints become
_NO_TINT = {"multiply": "#ffffff", "overlay": "#000000", "alpha": 1.0}
_CHANNELS = {"r": 0, "red": 0, "g": 1, "green": 1, "b": 2, "blue": 2, "a": 3, "alpha": 3}
_ID_KEYS = {"id", "ids", "name", "names", "mesh", "meshes", "artmesh", "artmeshes"}
MAX_COLOR_PRESETS = 100                           # a model keeps up to this many (the API enforces it as well)


def _letters(k) -> str:
    return re.sub(r"[^a-z]", "", str(k).lower())


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _vts_rgba(v) -> tuple[str, float | None] | None:
    """A colour as VTube Studio might write it -> ("#rrggbb", alpha or None), or None when `v` is not a colour."""
    numeric, ch = True, None
    if isinstance(v, str):
        m = re.fullmatch(r"#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})?", v.strip())
        if m:
            numeric, ch = False, [int(x, 16) / 255 for x in m.groups() if x]
    elif isinstance(v, (list, tuple)) and 3 <= len(v) <= 4 and all(_is_num(x) for x in v):
        ch = [float(x) for x in v]
    elif isinstance(v, dict):
        got = {_CHANNELS[_letters(k)]: float(x) for k, x in v.items() if _letters(k) in _CHANNELS and _is_num(x)}
        if all(i in got for i in (0, 1, 2)):
            ch = [got[0], got[1], got[2]] + ([got[3]] if 3 in got else [])
    if ch is None:
        return None
    rgb, a = ch[:3], (ch[3] if len(ch) > 3 else None)
    if numeric and max(rgb) > 1.0:                # Unity writes 0-1 floats; whole numbers up to 255 are read as bytes
        rgb = [x / 255 for x in rgb]
    if numeric and a is not None and a > 1.0:
        a /= 255
    return ("#" + "".join(f"{round(max(0.0, min(1.0, x)) * 255):02x}" for x in rgb),
            None if a is None else round(max(0.0, min(1.0, a)), 3))


def _vts_flat(node: dict, token: str):
    """A colour written as separate numbers on the entry (MultiplyR, MultiplyG ...)."""
    got = {}
    for k, x in node.items():
        n = _letters(k)
        if token in n and _is_num(x):
            i = _CHANNELS.get(n.replace(token, "").replace("colour", "").replace("color", ""))
            if i is not None:
                got["rgba"[i]] = float(x)
    return _vts_rgba(got) if got else None


def _vts_find(node, token: str, depth: int = 3):
    """The colour under the first key that names `token` ("multiply", "screen"; "" = any key), a few levels down."""
    if not isinstance(node, dict) or depth < 0:
        return None
    c = _vts_flat(node, token) if token else None
    if c:
        return c
    for k, v in node.items():
        if token in _letters(k):
            c = _vts_rgba(v) or _vts_find(v, "", depth - 1)
            if c:
                return c
    for k, v in node.items():                     # a box around both: {"Colors": {"Multiply": ..., "Screen": ...}}
        if isinstance(v, dict) and token not in _letters(k):
            c = _vts_find(v, token, depth - 1)
            if c:
                return c
    return None


def _vts_alpha(entry: dict) -> float | None:
    for k, v in entry.items():
        if _letters(k) in ("alpha", "opacity", "multiplyalpha", "coloralpha") and _is_num(v):
            f = float(v)
            return round(max(0.0, min(1.0, f / 255 if f > 1 else f)), 3)
    return None


def _vts_ids(entry: dict) -> list[str]:
    ids: list[str] = []
    for k, v in entry.items():
        n = _letters(k)
        if n in _ID_KEYS or ("mesh" in n and ("id" in n or "name" in n)):
            for x in v if isinstance(v, list) else [v]:
                if isinstance(x, str) and x.strip() and x.strip() not in ids:
                    ids.append(x.strip())
    return ids


def _vts_entries(container) -> list[dict]:
    if isinstance(container, dict):               # {"ArtMesh12": {...}}: the id is the key
        return [{"ID": k, **v} for k, v in container.items() if isinstance(v, dict)]
    return [e for e in container if isinstance(e, dict)] if isinstance(container, list) else []


def _vts_look(container, keep_neutral: bool) -> tuple[dict, list[dict], int]:
    """(look, the entries that could not be read, how many entries there were). A model's own tints leave out what changes
    nothing (white multiply, black screen, alpha 1); a preset keeps it, because there it undoes what is under it."""
    meshes: dict[str, dict] = {}
    unread: list[dict] = []
    entries = _vts_entries(container)
    for e in entries:
        ids = _vts_ids(e)
        m, s = _vts_find(e, "multiply"), _vts_find(e, "screen") or _vts_find(e, "overlay")
        spec: dict = {}
        if m:
            spec["multiply"] = m[0]
        if s:
            spec["overlay"] = s[0]
        a = m[1] if m and m[1] is not None else _vts_alpha(e)
        if a is not None:
            spec["alpha"] = a
        if not ids or not spec:
            unread.append(e)
            continue
        if not keep_neutral:
            spec = {k: v for k, v in spec.items() if v != _NO_TINT[k]}
        for i in ids:
            if spec and len(meshes) < 4096:
                meshes[i[:120]] = dict(spec)
    return ({"meshes": meshes} if meshes else {}), unread, len(entries)


def read_vts_colors(d: Any) -> dict:
    """The art mesh colours in a parsed .vtube.json: {"current": look (the model's own tints), "presets": {hotkey name:
    look}, "entries": how many colour entries the file has, "unread": how many of them could not be understood,
    "sample": the keys of the first one that could not}."""
    d = d if isinstance(d, dict) else {}
    out: dict = {"current": {}, "presets": {}, "entries": 0, "unread": 0, "sample": []}

    def note(unread: list[dict], n: int) -> None:
        out["entries"] += n
        out["unread"] += len(unread)
        if unread and not out["sample"]:
            out["sample"] = [str(k) for k in unread[0]][:12]

    details = d.get("ArtMeshDetails")
    out["current"], bad, n = _vts_look(details.get("ArtMeshMultiplyAndScreenColors") if isinstance(details, dict) else None, False)
    note(bad, n)
    taken = {VTS_COLORS_NAME.lower()}
    for i, h in enumerate(d.get("Hotkeys") or []):
        box = h.get("ColorScreenMultiplyPreset") if isinstance(h, dict) else None
        cont = box.get("ArtMeshMultiplyAndScreenColors") if isinstance(box, dict) else None
        if not cont:                              # (every hotkey has the field; an empty one is no preset)
            continue
        look, bad, n = _vts_look(cont, True)
        note(bad, n)
        if not look or len(out["presets"]) >= MAX_COLOR_PRESETS - 1:
            continue
        base = (str(h.get("Name") or "").strip() or f"Colours {i + 1}")[:36]
        name, k = base, 2
        while name.lower() in taken:
            name, k = f"{base} {k}", k + 1
        taken.add(name.lower())
        out["presets"][name] = look
    return out


# --------------------------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------------------------

def model_dir(mid: str) -> Path:
    mid = str(mid or "")
    if not mid or slug(mid) != mid:
        raise LibraryError(f"no model {mid!r}", 404)
    d = MODELS_DIR / mid
    if not d.is_dir():
        raise LibraryError(f"no model {mid!r}", 404)
    return d


def _model_settings(folder: Path, model3: Path) -> dict:
    """hexcast.json of a model, created on first sight (from the .vtube.json when there is one)."""
    path = folder / SETTINGS_FILE
    s = _read_json(path)
    if isinstance(s, dict) and s.get("version") == 1:
        return s
    vts = _vts_file(model3)
    if vts:
        v = read_vts(vts)
        s = {"version": 1, "name": v["name"] or folder.name, "mappings": v["mappings"] or default_mappings(),
             "mappings_from": "vts" if v["mappings"] else "default", "hotkeys": v["hotkeys"],
             "idle_motion": v["idle_motion"], "physics": v["physics"], "icon": v.get("icon", "")}
        _first_colors(folder, vts)
    else:
        s = {"version": 1, "name": _stem(model3.name, ".model3.json"), "mappings": default_mappings(),
             "mappings_from": "default", "hotkeys": [], "idle_motion": "", "physics": True, "icon": ""}
    try:
        _write_json(path, s)
    except OSError:
        pass
    return s


def _discover(folder: Path, model3: Path, spec: dict) -> tuple[list[dict], list[dict]]:
    """Expressions and motions: those model3.json lists plus every .exp3.json / .motion3.json in
    the folder (VTube Studio keeps them beside the model and lists them only in its hotkeys)."""
    base = model3.parent
    refs = spec.get("FileReferences") or {}
    exprs, seen = [], set()
    for e in refs.get("Expressions") or []:
        f = str(e.get("File") or "")
        if f:
            exprs.append({"name": str(e.get("Name") or _stem(PurePosixPath(f).name, ".exp3.json")), "file": f})
            seen.add(PurePosixPath(f).as_posix().lower())
    for p in sorted(folder.rglob("*.exp3.json")):
        rel = os.path.relpath(p, base).replace(os.sep, "/")
        if rel.lower() not in seen:
            exprs.append({"name": _stem(p.name, ".exp3.json"), "file": rel})
            seen.add(rel.lower())
    motions, mseen = [], set()
    for group, lst in (refs.get("Motions") or {}).items():
        for i, m in enumerate(lst or []):
            f = str((m or {}).get("File") or "")
            if f:
                motions.append({"name": _stem(PurePosixPath(f).name, ".motion3.json"), "group": str(group),
                                "index": i, "file": f})
                mseen.add(PurePosixPath(f).as_posix().lower())
    for p in sorted(folder.rglob("*.motion3.json")):
        rel = os.path.relpath(p, base).replace(os.sep, "/")
        if rel.lower() not in mseen:
            name = _stem(p.name, ".motion3.json")
            motions.append({"name": name, "group": name, "index": 0, "file": rel})
            mseen.add(rel.lower())
    names: dict[str, int] = {}
    for lst in (exprs, motions):                  # unique names, so the API can address them by name
        names.clear()
        for x in lst:
            n = x["name"]
            if n in names:
                names[n] += 1
                x["name"] = f"{n} {names[n]}"
            else:
                names[n] = 1
    return exprs, motions


def _rig_path(folder: Path) -> Path | None:
    p = folder / pngtuber.RIG
    return p if p.is_file() else None


def _folder_pictures(folder: Path) -> list[str]:
    return sorted(_rel(p, folder) for p in folder.rglob("*")
                  if p.is_file() and p.suffix.lower() in pngtuber.IMAGE_EXTS)


def png_rig(mid: str) -> dict:
    folder = model_dir(mid)
    rp = _rig_path(folder)
    if not rp:
        raise LibraryError("not a PNGtuber", 404)
    return pngtuber.normalize(_read_json(rp) or {}, files=_folder_pictures(folder))


def _png_meta(mid: str, folder: Path) -> dict:
    rig = png_rig(mid)
    first = pngtuber.first_picture(rig)
    try:
        size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    except OSError:
        size = 0
    problem = pngtuber.problems(rig)
    return {
        "id": mid, "type": "png", "style": rig["style"], "name": rig.get("name") or mid, "ok": problem is None,
        "problem": problem, "url": _url("models", mid, pngtuber.RIG), "icon": _url("models", mid, first) if first else "",
        "expressions": [{"name": s, "file": ""} for s in pngtuber.state_names(rig)], "motions": [],
        "states": pngtuber.state_names(rig), "default_state": rig.get("default_state"),
        "pictures": _folder_pictures(folder), "layers": len(rig.get("layers") or []), "size": size,
        "mappings_from": "", "hotkeys": [], "idle_motion": "", "use_physics": False, "editor": "PNG",
        "source": rig.get("source", ""), "color_presets": sorted(color_presets(mid), key=str.lower),
        "color_default": color_default(mid), "anchors": anchors(mid),
    }


def model_meta(mid: str) -> dict:
    folder = model_dir(mid)
    model3 = find_model3(folder)
    if not model3 and _rig_path(folder):
        return _png_meta(mid, folder)
    if not model3:
        return {"id": mid, "name": mid, "ok": False,
                "problem": "no .model3.json (Live2D) or pngtuber.json (PNGtuber) in the folder"}
    spec = _read_json(model3) or {}
    refs = spec.get("FileReferences") or {}
    moc = model3.parent / str(refs.get("Moc") or "")
    ver = moc_version(moc) if refs.get("Moc") else None
    problem = None
    if ver is None:
        problem = "the .moc3 file is missing or not a Cubism 3+ model"
    elif ver > MOC_SUPPORTED:
        problem = (f"saved by Cubism Editor {MOC_EDITOR.get(ver, '5.3+')}, which the web renderer can't draw yet - "
                   f"export it again for Cubism 5.0 - 5.2 (File > Export > moc3, choose the 5.0 format)")
    settings = _model_settings(folder, model3)
    exprs, motions = _discover(folder, model3, spec)
    icon = ""
    for cand in [settings.get("icon"), "icon.png", "icon.jpg"]:
        if cand and (model3.parent / cand).is_file():
            icon = _url("models", mid, _rel(model3.parent / cand, folder))
            break
    try:
        size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    except OSError:
        size = 0
    return {
        "id": mid, "type": "live2d", "name": settings.get("name") or mid, "ok": problem is None, "problem": problem,
        "model3": _rel(model3, folder), "url": _url("models", mid, _rel(model3, folder)),
        "moc_version": ver, "editor": MOC_EDITOR.get(ver or 0, ""), "textures": len(refs.get("Textures") or []),
        "physics": bool(refs.get("Physics")), "display_info": bool(refs.get("DisplayInfo")),
        "expressions": exprs, "motions": motions, "icon": icon, "size": size,
        "mappings_from": settings.get("mappings_from", "default"), "hotkeys": settings.get("hotkeys", []),
        "idle_motion": settings.get("idle_motion", ""), "use_physics": bool(settings.get("physics", True)),
        "color_presets": sorted(color_presets(mid), key=str.lower), "color_default": color_default(mid),
        "anchors": anchors(mid),
    }


def list_models() -> list[dict]:
    ensure_dirs()
    out = []
    for d in sorted(MODELS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if d.is_dir() and slug(d.name) == d.name and not d.name.startswith("."):
            try:
                out.append(model_meta(d.name))
            except LibraryError:
                pass
    return out


def _colors_file(mid: str) -> dict:
    d = _read_json(model_dir(mid) / COLORS_FILE)
    return d if isinstance(d, dict) else {}


def color_presets(mid: str) -> dict:
    """The model's saved colour presets (name -> look, see avatar.norm_look). They are kept in the model's own folder,
    so they go wherever the model goes - and every avatar that shows the model can use them."""
    p = _colors_file(mid).get("presets")
    return p if isinstance(p, dict) else {}


def color_default(mid: str) -> str:
    """The name of the preset that is the model's default - what an avatar starts in when it loads the model - or ''."""
    d = _colors_file(mid).get("default")
    return d if isinstance(d, str) and d in color_presets(mid) else ""


def save_color_presets(mid: str, presets: dict, default: str = "") -> None:
    path = model_dir(mid) / COLORS_FILE
    if presets:
        _write_json(path, {"version": 1, "presets": presets, **({"default": default} if default in presets else {})})
    else:
        try:
            path.unlink()
        except OSError:
            pass


def _first_colors(folder: Path, vts: Path) -> None:
    """A model that comes in with colours saved by VTube Studio keeps them: its own tints become the preset "VTube Studio"
    - the model's default, so every avatar that loads it starts in them - and each colour hotkey a preset of its name.
    Only when the model has no presets of its own yet; a model already in the library is left alone (the Colors tab can
    import again)."""
    path = folder / COLORS_FILE
    if path.exists():
        return
    try:
        found = read_vts_colors(_read_json(vts))
        table = {**({VTS_COLORS_NAME: found["current"]} if found["current"] else {}), **found["presets"]}
        if table:
            _write_json(path, {"version": 1, "presets": table, **({"default": VTS_COLORS_NAME} if found["current"] else {})})
    except (OSError, ValueError):
        pass


def model_vts_colors(mid: str, data: Any = None) -> dict:
    """read_vts_colors of the .vtube.json that sits with the model - or of `data`, a parsed one the streamer chose."""
    model3 = find_model3(model_dir(mid))
    if not model3:
        raise LibraryError("only a Live2D model comes with a VTube Studio file")
    if data is None:
        vts = _vts_file(model3)
        if not vts:
            raise LibraryError("this model has no .vtube.json beside its .model3.json - choose one from your VTube Studio folder", 404)
        data = _read_json(vts)
    if not isinstance(data, dict):
        raise LibraryError("that .vtube.json can't be read", 422)
    return read_vts_colors(data)


def import_vts_colors(mid: str, found: dict, default: bool | None = None) -> dict:
    """Put what read_vts_colors found into the model's presets: the model's own tints as "VTube Studio", each colour hotkey
    under its name; a preset of the same name is replaced (re-importing brings it back to VTube Studio's). `default`: make
    "VTube Studio" the model's default - None: only when the model has no default yet. Returns what happened."""
    table = color_presets(mid)
    dflt = color_default(mid)
    incoming = {**({VTS_COLORS_NAME: found["current"]} if found["current"] else {}), **found["presets"]}
    added, replaced, unchanged = [], [], []
    for name, look in incoming.items():
        old = next((k for k in table if k.lower() == name.lower()), None)
        if old is not None and table[old] == look and old == name:
            unchanged.append(name)
            continue
        if old is not None:
            if old == dflt:
                dflt = name
            del table[old]
            replaced.append(name)
        else:
            added.append(name)
        table[name] = look
    if len(table) > MAX_COLOR_PRESETS:
        raise LibraryError(f"a model keeps up to {MAX_COLOR_PRESETS} colour presets - delete some first")
    if found["current"] and (default is True or (default is None and not dflt)):
        dflt = VTS_COLORS_NAME
    if added or replaced or dflt != color_default(mid):
        save_color_presets(mid, table, dflt)
    return {"added": added, "replaced": replaced, "unchanged": unchanged, "default": dflt if dflt in table else ""}


def anchors(mid: str) -> dict:
    """The model's named pin points (name -> pin, see avatar._norm_pin): a spot on the art that things can be glued to -
    an item, a soundboard clip, another model. Kept in the model's own folder, so they go wherever the model goes."""
    d = _read_json(model_dir(mid) / ANCHORS_FILE)
    a = d.get("anchors") if isinstance(d, dict) else None
    return a if isinstance(a, dict) else {}


def save_anchors(mid: str, table: dict) -> None:
    path = model_dir(mid) / ANCHORS_FILE
    if table:
        _write_json(path, {"version": 1, "anchors": table})
    else:
        try:
            path.unlink()
        except OSError:
            pass


def copy_model(mid: str, name: str, preset: str = "") -> dict:
    """A full, independent copy of a model under a new name - a model of its own in the library. With `preset` (one of
    its colour presets) the copy's default colours are that preset, so it loads in them, and the original stops
    starting in it (its other presets, and the preset itself, stay) - the original still loads plain."""
    src = model_dir(mid)
    name = str(name or "").strip()[:60]
    if not name:
        raise LibraryError("give the new model a name")
    presets = color_presets(mid)
    real = next((k for k in presets if k.lower() == str(preset or "").strip().lower()), None) if preset else None
    if preset and real is None:
        raise LibraryError(f"no colour preset {preset!r} on this model", 404)
    ensure_dirs()
    new = _unique(MODELS_DIR, re.sub(r"-{2,}", "-", slug(name)))          # ("Hiyori - Night" is hiyori-night, not hiyori---night)
    dest = MODELS_DIR / new
    try:
        shutil.copytree(src, dest, symlinks=False)
        save_model_settings(new, {"name": name})                       # (its own name; a PNGtuber's is in its rig)
        if real:
            save_color_presets(new, presets, real)
    except (OSError, LibraryError) as exc:
        _rmtree(dest)
        raise LibraryError(f"could not copy the model: {getattr(exc, 'message', exc)}", 500)
    if real and color_default(mid) == real:
        save_color_presets(mid, presets, "")
    return model_meta(new)


def model_settings(mid: str) -> dict:
    folder = model_dir(mid)
    model3 = find_model3(folder)
    if not model3 and _rig_path(folder):
        return {"rig": png_rig(mid)}
    if not model3:
        raise LibraryError("no .model3.json in the folder", 404)
    return _model_settings(folder, model3)


def save_model_settings(mid: str, patch: dict) -> dict:
    folder = model_dir(mid)
    if not find_model3(folder) and _rig_path(folder):           # a PNGtuber: the rig (and its name)
        rig = png_rig(mid)
        if isinstance(patch.get("rig"), dict):
            rig = pngtuber.normalize({**patch["rig"], "source": rig.get("source", "")}, files=_folder_pictures(folder))
        if "name" in patch:
            rig["name"] = str(patch["name"] or "").strip()[:60] or mid
        _write_json(folder / pngtuber.RIG, rig)
        return {"rig": rig}
    s = model_settings(mid)
    if "name" in patch:
        s["name"] = str(patch["name"] or "").strip()[:60] or mid
    if "mappings" in patch and isinstance(patch["mappings"], list):
        rows = []
        for m in patch["mappings"][:400]:
            if isinstance(m, dict) and m.get("output"):
                rin, rout = m.get("in") or [0, 1], m.get("out") or [0, 1]
                rows.append(_mapping(m.get("name"), m.get("input"), m.get("output"), rin[0], rin[1], rout[0], rout[1],
                                     m.get("smoothing", 0), ("b" if m.get("blink") else "") + ("r" if m.get("breath") else ""),
                                     m.get("clamp_in", True), m.get("clamp_out", True)))
        s["mappings"], s["mappings_from"] = rows, "custom"
    if patch.get("reset_mappings"):
        model3 = find_model3(model_dir(mid))
        vts = _vts_file(model3) if model3 else None
        v = read_vts(vts) if vts else None
        s["mappings"] = (v and v["mappings"]) or default_mappings()
        s["mappings_from"] = "vts" if v and v["mappings"] else "default"
    if "physics" in patch:
        s["physics"] = bool(patch["physics"])
    if "idle_motion" in patch:
        s["idle_motion"] = str(patch["idle_motion"] or "")
    _write_json(model_dir(mid) / SETTINGS_FILE, s)
    return s


def engine_settings(mid: str) -> dict:
    """The model3.json the renderer loads: the file as shipped, with every expression and motion
    of the folder added (each extra motion in a group of its own name) and physics left out when
    switched off. `url` points at the real file, so textures and the rest resolve next to it."""
    folder = model_dir(mid)
    model3 = find_model3(folder)
    if not model3 and _rig_path(folder):
        return {**png_rig(mid), "url": _url("models", mid, pngtuber.RIG), "base": _url("models", mid) + "/"}
    if not model3:
        raise LibraryError("no .model3.json in the folder", 404)
    spec = _read_json(model3)
    if not isinstance(spec, dict):
        raise LibraryError("the .model3.json can't be read", 422)
    settings = _model_settings(folder, model3)
    exprs, motions = _discover(folder, model3, spec)
    refs = dict(spec.get("FileReferences") or {})
    refs["Expressions"] = [{"Name": e["name"], "File": e["file"]} for e in exprs]
    groups: dict[str, list] = {}
    for m in motions:
        groups.setdefault(m["group"], []).append({"File": m["file"]})
    refs["Motions"] = groups
    if not settings.get("physics", True):
        refs.pop("Physics", None)
    spec["FileReferences"] = refs
    spec["url"] = _url("models", mid, _rel(model3, folder))
    return spec


def add_model_pictures(mid: str, files: list[tuple[str, bytes]]) -> list[str]:
    """Pictures uploaded into a PNGtuber's folder (for the rig editor). Returns their names."""
    folder = model_dir(mid)
    if find_model3(folder):
        raise LibraryError("pictures can only be added to a PNGtuber")
    _check_sizes(len(b) for _, b in files)
    out = []
    for name, data in files[:200]:
        ext = Path(name).suffix.lower()
        if ext not in pngtuber.IMAGE_EXTS:
            continue
        base = re.sub(r"[^A-Za-z0-9 _.-]+", "_", Path(name).stem).strip(" ._-")[:60] or "picture"
        fn, n = base + ext, 2
        while (folder / fn).exists():
            fn, n = f"{base}-{n}{ext}", n + 1
        (folder / fn).write_bytes(data)
        out.append(fn)
    return out


def delete_model(mid: str) -> None:
    _rmtree(model_dir(mid))


def _rmtree(path: Path) -> None:
    def retry(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=retry) if sys.version_info < (3, 12) else shutil.rmtree(path, onexc=retry)


# --------------------------------------------------------------------------------------------
# bringing models in
# --------------------------------------------------------------------------------------------

def _safe_member(name: str) -> PurePosixPath | None:
    p = PurePosixPath(name.replace("\\", "/"))
    if not p.parts or p.is_absolute() or ":" in p.parts[0] or p.parts[0] == "__MACOSX":
        return None
    if any(part in ("..", "") for part in p.parts):
        return None
    return p


def _check_sizes(sizes: Iterable[int]) -> None:
    total = n = 0
    for s in sizes:
        n += 1
        total += s
        if s > MAX_FILE_BYTES:
            raise LibraryError("a file in it is bigger than 768 MB")
        if n > MAX_FILES:
            raise LibraryError(f"too many files (more than {MAX_FILES})")
        if total > MAX_ZIP_BYTES:
            raise LibraryError("too big (more than 2 GB unpacked)")


def _install_tree(staged: Path, kind: str, name_hint: str) -> str:
    """Move a staged folder into models/ or items/ under a fresh id."""
    dest_root = MODELS_DIR if kind == "models" else ITEMS_DIR
    ensure_dirs()
    mid = _unique(dest_root, slug(name_hint, "model" if kind == "models" else "item"))
    shutil.move(str(staged), str(dest_root / mid))
    return mid


def _model_root_in(tmp: Path) -> Path:
    m3 = find_model3(tmp)
    if not m3:
        raise LibraryError("no .model3.json inside - that isn't a Live2D (Cubism 3, 4 or 5) model. "
                           "Cubism 2 models (.model.json / .moc) aren't supported.")
    return m3.parent


def _png_root(tmp: Path, name_hint: str) -> Path:
    """No Live2D model in it: a PNGtuber. A PNGTuber Plus .save becomes a layered rig, a folder
    with a pngtuber.json is taken as it is, and a folder of pictures becomes a simple rig."""
    saves = sorted(p for p in tmp.rglob("*.save") if p.is_file())
    if saves:
        try:
            data = json.loads(saves[0].read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise LibraryError("that .save file can't be read (is it a PNGTuber Plus avatar?)")
        out = tmp / "hx-png"
        out.mkdir()
        try:
            rig = pngtuber.from_pngtuber_plus(data, out, saves[0].stem)
        except (ValueError, TypeError) as exc:
            raise LibraryError(f"that .save can't be imported: {exc}")
        _write_json(out / pngtuber.RIG, rig)
        return out
    rigs = sorted(tmp.rglob(pngtuber.RIG), key=lambda p: len(p.parts))
    if rigs:
        return rigs[0].parent
    pics = [p for p in tmp.rglob("*") if p.is_file() and p.suffix.lower() in pngtuber.IMAGE_EXTS]
    if not pics:
        raise LibraryError("no Live2D model (.model3.json), PNGTuber Plus avatar (.save) or pictures in it")
    root = min((p.parent for p in pics), key=lambda d: len(d.parts))
    names = [_rel(p, root) for p in pics if p.parent == root or root in p.parents]
    rig = pngtuber.simple_rig(names, name_hint)
    _write_json(root / pngtuber.RIG, rig)
    return root


def _any_model_root(tmp: Path, name_hint: str) -> tuple[Path, str]:
    """(the folder to keep, its name) for a Live2D model or else a PNGtuber."""
    if find_model3(tmp):
        root = _model_root_in(tmp)
        return root, _name_for(root, name_hint)
    root = _png_root(tmp, name_hint)
    rig = _read_json(root / pngtuber.RIG) or {}
    return root, (rig.get("name") or name_hint)


def _name_for(root: Path, fallback: str) -> str:
    m3 = find_model3(root)
    vts = _vts_file(m3) if m3 else None
    if vts and read_vts(vts)["name"]:
        return read_vts(vts)["name"]
    if root.name and not root.name.startswith("hx-"):
        return root.name
    return _stem(m3.name, ".model3.json") if m3 else fallback


def import_zip(zip_path: Path, filename: str, kind: str = "models") -> str:
    """Unpack an uploaded zip. For a model, only the folder holding its .model3.json (and below)
    is kept; for an item, the whole zip (a Live2D item or a folder of frames)."""
    ensure_dirs()
    tmp = Path(tempfile.mkdtemp(prefix="hx-", dir=ROOT))
    try:
        try:
            zf = zipfile.ZipFile(zip_path)
        except zipfile.BadZipFile:
            raise LibraryError("that isn't a zip file")
        with zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            _check_sizes(i.file_size for i in infos)
            for info in infos:
                rel = _safe_member(info.filename)
                if rel is None:
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:      # a symlink
                    continue
                target = tmp.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        stem = Path(filename or "upload").stem
        if kind == "models":
            root, name = _any_model_root(tmp, stem)
            return _install_tree(root, "models", name)
        return _install_item_tree(tmp, stem)
    finally:
        if tmp.exists():
            _rmtree(tmp)


def import_files(files: list[tuple[str, bytes]], kind: str = "models") -> str:
    """A folder picked in the browser: (relative path, content) for every file in it."""
    ensure_dirs()
    _check_sizes(len(b) for _, b in files)
    tmp = Path(tempfile.mkdtemp(prefix="hx-", dir=ROOT))
    try:
        top = ""
        for rel_name, content in files:
            rel = _safe_member(rel_name)
            if rel is None:
                continue
            top = top or rel.parts[0]
            target = tmp.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if kind == "models":
            root, name = _any_model_root(tmp, Path(top).stem if top else "pngtuber")
            return _install_tree(root, "models", name)
        return _install_item_tree(tmp, top or "item")
    finally:
        if tmp.exists():
            _rmtree(tmp)


def import_path(src: str, kind: str = "models") -> str:
    """Copy a folder on this PC (e.g. a VTube Studio model folder) into the library."""
    p = Path(os.path.expanduser(str(src or "").strip().strip('"'))).resolve()
    if not p.exists():
        raise LibraryError(f"not found: {p}", 404)
    ensure_dirs()
    if kind == "models":
        if p.is_file() and p.name.lower().endswith(".model3.json"):
            p = p.parent
        tmp = Path(tempfile.mkdtemp(prefix="hx-", dir=ROOT))
        try:
            if p.is_file() and p.suffix.lower() == ".save":            # a PNGTuber Plus avatar
                (tmp / "m").mkdir()
                shutil.copy2(p, tmp / "m" / p.name)
                root, name = _any_model_root(tmp / "m", p.stem)
                return _install_tree(root, "models", name)
            if not p.is_dir():
                raise LibraryError("pick the model's folder (a .model3.json, a PNGTuber Plus .save, or pictures)")
            src = find_model3(p).parent if find_model3(p) else p
            files = [f for f in src.rglob("*") if f.is_file()]
            _check_sizes(f.stat().st_size for f in files)
            dest = tmp / "m"
            shutil.copytree(src, dest, symlinks=False, ignore=shutil.ignore_patterns("*.cmo3", "*.psd", "*.can3"))
            root, name = _any_model_root(dest, src.name)
            return _install_tree(root, "models", name)
        finally:
            if tmp.exists():
                _rmtree(tmp)
    tmp = Path(tempfile.mkdtemp(prefix="hx-", dir=ROOT))
    try:
        if p.is_file():
            if p.suffix.lower() not in IMAGE_EXTS:
                raise LibraryError("an item is a png, jpg, webp or gif - or a folder")
            (tmp / "i").mkdir()
            shutil.copy2(p, tmp / "i" / p.name)
            return _install_item_tree(tmp / "i", p.stem)
        files = [f for f in p.rglob("*") if f.is_file()]
        _check_sizes(f.stat().st_size for f in files)
        shutil.copytree(p, tmp / "i", symlinks=False)
        return _install_item_tree(tmp / "i", p.name)
    finally:
        if tmp.exists():
            _rmtree(tmp)


# --------------------------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------------------------

def image_size(path: Path) -> tuple[int, int] | None:
    """Width and height of a png / gif / jpeg / webp, from its header."""
    try:
        with open(path, "rb") as f:
            head = f.read(64 * 1024)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", head[6:10])
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        chunk = head[12:16]
        if chunk == b"VP8 " and len(head) >= 30:
            w, h = struct.unpack("<HH", head[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L" and len(head) >= 25:
            b = head[21:25]
            return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        if chunk == b"VP8X" and len(head) >= 30:
            return 1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little")
        return None
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", head[i + 5:i + 9])
                return w, h
            seg = struct.unpack(">H", head[i + 2:i + 4])[0]
            i += 2 + seg
    return None


def _frames(folder: Path) -> list[Path]:
    """Numbered frames (name_1.png ... name_12.png) of a sequence item, in order."""
    pics = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    keyed = []
    for p in pics:
        m = _FRAME.match(p.stem)
        if m:
            keyed.append((m.group(1).lower(), int(m.group(2)), p))
    if len(keyed) < 2:
        return []
    prefix = max({k[0] for k in keyed}, key=lambda k: sum(1 for x in keyed if x[0] == k))
    return [p for _, _, p in sorted((x for x in keyed if x[0] == prefix), key=lambda x: x[1])]


def _item_kind(folder: Path) -> tuple[str, dict]:
    m3 = find_model3(folder)
    if m3:
        spec = _read_json(m3) or {}
        moc = m3.parent / str((spec.get("FileReferences") or {}).get("Moc") or "")
        ver = moc_version(moc)
        return "live2d", {"model3": _rel(m3, folder), "moc_version": ver,
                          "ok": ver is not None and ver <= MOC_SUPPORTED}
    frames = _frames(folder)
    if frames:
        return "sequence", {"frames": [_rel(p, folder) for p in frames]}
    pics = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    if pics:
        p = pics[0]
        return ("gif" if p.suffix.lower() == ".gif" else "image"), {"file": _rel(p, folder)}
    return "", {}


def _install_item_tree(staged: Path, name_hint: str) -> str:
    kind, _ = _item_kind(staged)
    if not kind:
        raise LibraryError("no picture, frames or Live2D item in it")
    return _install_tree(staged, "items", name_hint)


def save_item_upload(filename: str, content: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        raise LibraryError("an item is a png, jpg, webp or gif (or a zip of a Live2D item / numbered frames)")
    _check_sizes([len(content)])
    ensure_dirs()
    stem = Path(filename).stem
    iid = _unique(ITEMS_DIR, slug(stem, "item"))
    d = ITEMS_DIR / iid
    d.mkdir(parents=True)
    safe = slug(stem, "item") + ext
    (d / safe).write_bytes(content)
    return iid


def item_meta(iid: str) -> dict:
    if not iid or slug(iid) != iid or not (ITEMS_DIR / iid).is_dir():
        raise LibraryError(f"no item {iid!r}", 404)
    folder = ITEMS_DIR / iid
    kind, info = _item_kind(folder)
    meta: dict = {"id": iid, "name": iid.replace("-", " "), "kind": kind or "broken", "ok": bool(kind)}
    names = _read_json(folder / SETTINGS_FILE)
    if isinstance(names, dict) and names.get("name"):
        meta["name"] = str(names["name"])
    if kind in ("image", "gif"):
        meta["url"] = _url("items", iid, info["file"])
        size = image_size(folder / info["file"])
        if size:
            meta["w"], meta["h"] = size
    elif kind == "sequence":
        meta["frames"] = [_url("items", iid, f) for f in info["frames"]]
        meta["url"] = meta["frames"][0]
        size = image_size(folder / info["frames"][0])
        if size:
            meta["w"], meta["h"] = size
        meta["fps"] = int((names or {}).get("fps") or 12) if isinstance(names, dict) else 12
    elif kind == "live2d":
        meta["url"] = _url("items", iid, info["model3"])
        meta["ok"] = info["ok"]
        if not info["ok"]:
            meta["problem"] = "made with Cubism 5.3 - not supported yet"
        icon = sorted(p for p in folder.glob("*.png") if p.is_file())
        if icon:
            meta["icon"] = _url("items", iid, _rel(icon[0], folder))
    return meta


def list_items() -> list[dict]:
    ensure_dirs()
    out = []
    for d in sorted(ITEMS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if d.is_dir() and slug(d.name) == d.name and not d.name.startswith("."):
            try:
                out.append(item_meta(d.name))
            except LibraryError:
                pass
    return out


def delete_item(iid: str) -> None:
    item_meta(iid)
    _rmtree(ITEMS_DIR / iid)


# --------------------------------------------------------------------------------------------
# VTube Studio on this PC (so moving over is one click per model)
# --------------------------------------------------------------------------------------------

def _steam_libraries() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(Path(os.environ[env]) / "Steam")
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for val in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(Path(winreg.QueryValueEx(k, val)[0]))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
    else:
        home = Path.home()
        roots += [home / ".steam" / "steam", home / ".local" / "share" / "Steam",
                  home / "Library" / "Application Support" / "Steam"]
    libs: list[Path] = []
    for r in roots:
        vdf = r / "steamapps" / "libraryfolders.vdf"
        cands = [r]
        try:
            cands += [Path(m.replace("\\\\", "\\")) for m in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace"))]
        except OSError:
            pass
        for c in cands:
            try:
                c = c.resolve()
            except OSError:
                continue
            if c not in libs and (c / "steamapps").is_dir():
                libs.append(c)
    return libs


def vts_assets() -> Path | None:
    for lib in _steam_libraries():
        p = lib / "steamapps" / "common" / "VTube Studio" / "VTube Studio_Data" / "StreamingAssets"
        if p.is_dir():
            return p
    return None


def vts_listing() -> dict:
    """The models and items of a VTube Studio install on this PC, if there is one."""
    base = vts_assets()
    if not base:
        return {"found": False, "models": [], "items": []}
    models = []
    mdir = base / "Live2DModels"
    if mdir.is_dir():
        for d in sorted(mdir.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir():
                continue
            m3 = find_model3(d)
            if not m3:
                continue
            vts = _vts_file(m3)
            name = (read_vts(vts)["name"] if vts else "") or d.name
            moc = moc_version(m3.parent / str(((_read_json(m3) or {}).get("FileReferences") or {}).get("Moc") or ""))
            models.append({"name": name, "path": str(m3.parent), "folder": d.name,
                           "ok": moc is not None and moc <= MOC_SUPPORTED})
    items = []
    idir = base / "Items"
    if idir.is_dir():
        for p in sorted(idir.iterdir(), key=lambda p: p.name.lower()):
            if (p.is_file() and p.suffix.lower() in IMAGE_EXTS) or p.is_dir():
                items.append({"name": p.stem if p.is_file() else p.name, "path": str(p)})
    return {"found": True, "path": str(base), "models": models, "items": items}
