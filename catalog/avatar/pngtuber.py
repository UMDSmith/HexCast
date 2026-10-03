"""PNGtubers: avatars made of pictures instead of a Live2D model.

A PNGtuber is a model folder in the library with the pictures and a `pngtuber.json` (the "rig")
describing them. Two styles, one renderer:

  simple   - per state ("neutral", "happy" ...), a picture for each situation:
             idle (quiet, eyes open), talk, blink, talk_blink, and optionally half (a half-open
             mouth for quiet talking) and A / I / U / E / O (a mouth per vowel - the same vowel
             detection Live2D models use). Most PNGtubers are 2 - 4 pictures of this kind.
  layered  - pieces stacked and parented like PNGTuber Plus: each layer has a position, a pivot
             offset, a depth, when it shows (always / only quiet / only talking, and the same for
             blinking), which states (costumes) it belongs to, wobble, follow-lag, rotation drag,
             squash & stretch, sprite-sheet frames and clipping to its parent.

A folder of pictures becomes a simple rig on import (the file names say which picture is which:
`idle.png`, `talk.png`, `blink.png`, `talk_blink.png`, `happy_talk.png` ...; anything that can't be
guessed is fixed in the Avatars tab). A PNGTuber Plus `.save` becomes a layered rig.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

RIG = "pngtuber.json"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ROLES = ["idle", "talk", "blink", "talk_blink", "half", "A", "I", "U", "E", "O"]
DEFAULTS = {"bounce": 250.0, "gravity": 1000.0, "threshold": 0.12, "hold": 0.22, "breathe": True}

# words in a file name -> what the picture is
_TALK = {"talk", "talking", "talks", "speak", "speaking", "speech", "loud", "on", "active", "yell", "yelling",
         "mouthopen", "openmouth", "open", "say", "saying", "voice", "sing", "singing"}
_QUIET = {"idle", "quiet", "neutral", "silent", "off", "default", "base", "still", "rest", "resting", "mute",
          "muted", "closed", "mouthclosed", "closedmouth", "normal", "calm", "silence"}
_BLINK = {"blink", "blinking", "blinks", "eyesclosed", "closedeyes", "eyeclosed", "closedeye", "sleep", "sleeping"}
_HALF = {"half", "halfopen", "mid", "medium", "soft", "whisper"}
_NOISE = {"png", "mouth", "eyes", "eye", "pngtuber", "tuber", "avatar", "model", "sprite", "img", "image", "pic",
          "and", "with", "the", "frame", "state"}


def _words(stem: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)                       # camelCase
    s = s.lower()
    # the phrases that matter before splitting: "mouth closed", "eyes closed", "open mouth" ...
    for a, b in (("mouth open", "mouthopen"), ("open mouth", "mouthopen"), ("mouth closed", "mouthclosed"),
                 ("closed mouth", "mouthclosed"), ("eyes closed", "eyesclosed"), ("eye closed", "eyesclosed"),
                 ("closed eyes", "eyesclosed"), ("closed eye", "eyesclosed"), ("eyes open", "eyesopen"),
                 ("open eyes", "eyesopen"), ("half open", "halfopen")):
        s = re.sub(r"\b" + a.replace(" ", r"[\s_\-.]*") + r"\b", b, s)
    return [w for w in re.split(r"[^a-z0-9]+", s) if w]


def classify(filename: str) -> tuple[str, str]:
    """(state, role) of a picture from its name; role '' when it can't be told."""
    words = _words(Path(filename).stem)
    talk = any(w in _TALK for w in words)
    blink = any(w in _BLINK for w in words)
    half = any(w in _HALF for w in words)
    vowel = next((w.upper() for w in words if w in ("a", "i", "u", "e", "o") and len(words) > 1), "")
    quiet = any(w in _QUIET for w in words)
    if "eyesopen" in words:
        blink = False
    if "mouthclosed" in words:
        talk = False
    rest = [w for w in words if w not in _TALK | _QUIET | _BLINK | _HALF | _NOISE
            and w not in ("eyesopen", "a", "i", "u", "e", "o") and not w.isdigit()]
    state = "_".join(rest)[:32] or "neutral"
    if vowel:
        return state, vowel
    if half:
        return state, "half"
    if talk and blink:
        return state, "talk_blink"
    if talk:
        return state, "talk"
    if blink:
        return state, "blink"
    if quiet:
        return state, "idle"
    return state, ""


def simple_rig(files: list[str], name: str) -> dict:
    """A simple rig from the pictures in a folder (names relative to it)."""
    pics = sorted(f for f in files if Path(f).suffix.lower() in IMAGE_EXTS and not Path(f).name.startswith("."))
    states: dict[str, dict] = {}
    unknown = []
    for f in pics:
        state, role = classify(f)
        slot = states.setdefault(state, {})
        if role and role not in slot:
            slot[role] = f
        else:
            unknown.append(f)
    # pictures nobody could place: fill the empty roles in order (idle, talk, blink, talk_blink),
    # so 4 pictures named 1.png .. 4.png still make a working PNGtuber to fix up in the tab
    if unknown:
        target = states.setdefault("neutral", {}) if not states or "neutral" in states else next(iter(states.values()))
        for f in unknown:
            free = next((r for r in ("idle", "talk", "blink", "talk_blink") if r not in target), None)
            if free is None:
                break
            target[free] = f
    for s in list(states):                         # a state needs at least something to show
        if not states[s]:
            del states[s]
    order = sorted(states, key=lambda s: (s != "neutral", s))
    return normalize({"version": 1, "type": "png", "style": "simple", "name": name,
                      "states": {s: states[s] for s in order}, "default_state": order[0] if order else "neutral"},
                     files=pics)


# ------------------------------------------------------------------ PNGTuber Plus

_VEC = re.compile(r"Vector2i?\(\s*(-?[\d.eE+-]+)\s*,\s*(-?[\d.eE+-]+)\s*\)")


def _vec(v: Any) -> tuple[float, float]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return float(v[0]), float(v[1])
    m = _VEC.search(str(v or ""))
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def from_pngtuber_plus(data: dict, folder: Path, name: str) -> dict:
    """A layered rig from a PNGTuber Plus `.save` (JSON: one entry per sprite, the picture inside as
    base64 PNG). The pictures are written into `folder`."""
    if not isinstance(data, dict) or not data:
        raise ValueError("that .save has no sprites")
    layers, used_costumes = [], set()
    for key, sp in data.items():
        if not isinstance(sp, dict) or sp.get("type", "sprite") != "sprite":
            continue
        lid = str(sp.get("identification", key))
        img = sp.get("imageData")
        fname = ""
        if img:
            raw = base64.b64decode(img)
            stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(str(sp.get("path") or f"layer_{lid}")).stem)[:40] or f"layer_{lid}"
            fname = f"{stem}_{lid[-6:]}.png"
            (folder / fname).write_bytes(raw)
        else:
            p = Path(str(sp.get("path") or ""))
            if p.is_file():
                fname = re.sub(r"[^A-Za-z0-9._-]+", "_", p.name)
                (folder / fname).write_bytes(p.read_bytes())
        if not fname:
            continue
        flags = sp.get("costumeLayers") or "[1, 1, 1, 1, 1, 1, 1, 1, 1, 1]"
        try:
            costumes = json.loads(flags) if isinstance(flags, str) else list(flags)
        except ValueError:
            costumes = [1] * 10
        costumes = (list(costumes) + [1] * 10)[:10]
        on = [f"costume{i + 1}" for i, c in enumerate(costumes) if int(c) == 1]
        used_costumes.update(on)
        x, y = _vec(sp.get("pos"))
        ox, oy = _vec(sp.get("offset"))
        layers.append({
            "id": lid, "name": Path(str(sp.get("path") or fname)).stem[:60], "image": fname,
            "parent": None if sp.get("parentId") in (None, "") else str(sp.get("parentId")),
            "x": x, "y": y, "ox": ox, "oy": oy, "z": int(sp.get("zindex") or 0),
            "talk": int(sp.get("showTalk") or 0), "blink": int(sp.get("showBlink") or 0),
            "states": [] if len(on) == 10 else on,
            "wobble": [float(sp.get("xAmp") or 0), float(sp.get("xFrq") or 0), float(sp.get("yAmp") or 0), float(sp.get("yFrq") or 0)],
            "drag": float(sp.get("drag") or 0), "rot_drag": float(sp.get("rotDrag") or 0),
            "rot_min": float(sp.get("rLimitMin", -180)), "rot_max": float(sp.get("rLimitMax", 180)),
            "stretch": float(sp.get("stretchAmount") or 0), "ignore_bounce": bool(sp.get("ignoreBounce")),
            "frames": int(sp.get("frames") or 1), "fps": float(sp.get("animSpeed") or 0) / 6.0,
            "clip": bool(sp.get("clipped")), "toggle": "" if sp.get("toggle") in (None, "null") else str(sp.get("toggle")),
        })
    if not layers:
        raise ValueError("that .save has no pictures in it")
    multi = [c for c in (f"costume{i}" for i in range(1, 11)) if c in used_costumes and any(l["states"] for l in layers)]
    states = multi if multi else ["default"]
    return normalize({"version": 1, "type": "png", "style": "layered", "name": name, "layers": layers,
                      "states": states, "default_state": states[0], "source": "PNGTuber Plus"},
                     files=[l["image"] for l in layers])


# ------------------------------------------------------------------ checking a rig

def _f(v, lo, hi, d):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return d
    return d if f != f else max(lo, min(hi, f))


def _pic(v, files: set[str] | None) -> str:
    s = str(v or "").replace("\\", "/").strip()
    if not s or ".." in s.split("/") or s.startswith("/") or ":" in s:
        return ""
    if files is not None and s not in files:
        return ""
    return s


def normalize(rig: dict, files: list[str] | None = None) -> dict:
    """A clean rig: known fields only, numbers in range, pictures that exist in the folder."""
    have = set(files) if files is not None else None
    style = "layered" if rig.get("style") == "layered" else "simple"
    out: dict = {"version": 1, "type": "png", "style": style, "name": str(rig.get("name") or "")[:60]}
    for k, d in DEFAULTS.items():
        out[k] = bool(rig.get(k, d)) if isinstance(d, bool) else _f(rig.get(k, d), 0, 20000, d)
    out["threshold"] = _f(out["threshold"], 0.01, 0.9, DEFAULTS["threshold"])
    out["hold"] = _f(out["hold"], 0, 2, DEFAULTS["hold"])
    if rig.get("source"):
        out["source"] = str(rig["source"])[:40]
    if style == "simple":
        states = {}
        for s, roles in list((rig.get("states") or {}).items())[:40]:
            key = re.sub(r"[^a-z0-9_-]+", "_", str(s).lower()).strip("_")[:32]
            if not key or not isinstance(roles, dict):
                continue
            clean = {r: _pic(roles.get(r), have) for r in ROLES if _pic(roles.get(r), have)}
            states[key] = clean
        if not states:
            states = {"neutral": {}}
        out["states"] = states
        ds = str(rig.get("default_state") or "")
        out["default_state"] = ds if ds in states else next(iter(states))
        return out
    layers, ids = [], set()
    for l in (rig.get("layers") or [])[:300]:
        if not isinstance(l, dict):
            continue
        lid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(l.get("id") or ""))[:40] or f"layer{len(layers) + 1}"
        while lid in ids:
            lid += "_"
        ids.add(lid)
        wob = list(l.get("wobble") or [0, 0, 0, 0]) + [0, 0, 0, 0]
        layers.append({
            "id": lid, "name": str(l.get("name") or lid)[:60], "image": _pic(l.get("image"), have),
            "parent": None if not l.get("parent") else re.sub(r"[^A-Za-z0-9_-]+", "_", str(l["parent"]))[:40],
            "x": _f(l.get("x"), -1e5, 1e5, 0), "y": _f(l.get("y"), -1e5, 1e5, 0),
            "ox": _f(l.get("ox"), -1e5, 1e5, 0), "oy": _f(l.get("oy"), -1e5, 1e5, 0),
            "z": int(_f(l.get("z"), -100, 100, 0)),
            "talk": int(_f(l.get("talk"), 0, 3, 0)), "blink": int(_f(l.get("blink"), 0, 2, 0)),
            "vowel": l.get("vowel") if l.get("vowel") in ("A", "I", "U", "E", "O") else "",
            "states": [str(s)[:32] for s in (l.get("states") or [])][:20],
            "wobble": [_f(wob[0], 0, 1e4, 0), _f(wob[1], 0, 10, 0), _f(wob[2], 0, 1e4, 0), _f(wob[3], 0, 10, 0)],
            "drag": _f(l.get("drag"), 0, 1000, 0), "rot_drag": _f(l.get("rot_drag"), -1000, 1000, 0),
            "rot_min": _f(l.get("rot_min"), -360, 360, -180), "rot_max": _f(l.get("rot_max"), -360, 360, 180),
            "stretch": _f(l.get("stretch"), -100, 100, 0), "ignore_bounce": bool(l.get("ignore_bounce")),
            "frames": int(_f(l.get("frames"), 1, 256, 1)), "fps": _f(l.get("fps"), 0, 120, 0),
            "clip": bool(l.get("clip")), "toggle": str(l.get("toggle") or "")[:40],
        })
    for l in layers:                                    # a parent that isn't there: the layer is a root
        if l["parent"] and l["parent"] not in ids:
            l["parent"] = None
    out["layers"] = layers
    states = [re.sub(r"[^A-Za-z0-9_-]+", "_", str(s))[:32] for s in (rig.get("states") or []) if str(s).strip()][:20]
    out["states"] = states or ["default"]
    ds = str(rig.get("default_state") or "")
    out["default_state"] = ds if ds in out["states"] else out["states"][0]
    return out


def state_names(rig: dict) -> list[str]:
    return list(rig["states"]) if rig.get("style") == "simple" else list(rig.get("states") or [])


def first_picture(rig: dict) -> str:
    if rig.get("style") == "simple":
        st = (rig.get("states") or {}).get(rig.get("default_state"), {}) or next(iter((rig.get("states") or {}).values()), {})
        return st.get("idle") or next(iter(st.values()), "")
    return next((l["image"] for l in rig.get("layers") or [] if l.get("image")), "")


def problems(rig: dict) -> str | None:
    if rig.get("style") == "simple":
        if not any(v for st in rig["states"].values() for v in st.values()):
            return "no pictures assigned yet - pick them on the PNGtuber tab"
        return None
    if not any(l.get("image") for l in rig.get("layers") or []):
        return "no layers with a picture"
    return None
