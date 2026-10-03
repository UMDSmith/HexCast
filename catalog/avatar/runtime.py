"""The Live2D runtime: the scripts that draw a Live2D model in a browser.

None of it ships with Hexcast. Live2D's Cubism Core is Live2D's own code under the Live2D
Proprietary Software License, and the renderer around it embeds Live2D's Cubism Framework
(Live2D Open Software License) - so the streamer reads and accepts Live2D's terms in the
Avatars tab, and then Hexcast downloads, once:

    live2dcubismcore.min.js   Live2D Cubism Core for Web          (from cubism.live2d.com)
    pixi.min.js               PixiJS 8, the WebGL renderer          (npm, MIT)
    live2d-engine.min.js      untitled-pixi-live2d-engine (Cubism)  (npm, MIT + the Cubism Framework)
    pixi-gif.js               @pixi/gif, animated GIF items         (npm, MIT)

into config/avatar_runtime/ (kept across updates, never committed). The npm files are pinned
versions checked against their SHA-256. Live2D's Core is checked for being the Core (its own
header, a version Hexcast can use) - its address is Live2D's, and they may rebuild it.

Offline? Put the four files in that folder by hand (or upload the Core in the tab).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from hexcast_core import paths

DIR = paths.CONFIG_DIR / "avatar_runtime"
STATE_FILE = DIR / "runtime.json"
CORE = "live2dcubismcore.min.js"

LIVE2D_LICENSE_URL = "https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html"
LIVE2D_OPEN_LICENSE_URL = "https://www.live2d.com/eula/live2d-open-software-license-agreement_en.html"
LIVE2D_EXPANDABLE_URL = "https://www.live2d.com/en/sdk/license/expandable/"
LIVE2D_DOWNLOAD_PAGE = "https://www.live2d.com/en/sdk/download/web/"

# name -> (url, sha256 or None for Live2D's Core, what it is)
FILES: dict[str, tuple[str, str | None, str]] = {
    CORE: ("https://cubism.live2d.com/sdk-web/core/05/live2dcubismcore.min.js", None,
           "Live2D Cubism Core for Web 5"),
    "pixi.min.js": ("https://cdn.jsdelivr.net/npm/pixi.js@8.22.0/dist/pixi.min.js",
                    "06d9ef9823e743518793083c296d801e752db128cb1f519fbabe37e1259567ea", "PixiJS 8.22.0"),
    "live2d-engine.min.js": ("https://cdn.jsdelivr.net/npm/untitled-pixi-live2d-engine@1.4.0/dist/cubism.min.js",
                             "b7ae21b5f893247436dbd78736c13c6c197063445cfd74202dce1930afe5d227",
                             "untitled-pixi-live2d-engine 1.4.0"),
    "pixi-gif.js": ("https://cdn.jsdelivr.net/npm/@pixi/gif@3.0.1/dist/pixi-gif.js",
                    "63c298124aee25924355327dd194689a0dc59bf8a93268d0d8183e75dae6397d", "@pixi/gif 3.0.1"),
}
ORDER = [CORE, "pixi.min.js", "live2d-engine.min.js", "pixi-gif.js"]     # the order pages load them in
MAX_BYTES = 8 * 1024 * 1024
_CORE_VERSION = re.compile(rb"Live2D Cubism Core")

_lock = threading.Lock()
JOB: dict = {"running": False, "error": "", "done": [], "started": 0.0}


def _state() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def check_core(data: bytes) -> str | None:
    """None if `data` looks like Live2D's Cubism Core for Web, else what is wrong with it."""
    if len(data) < 50_000 or len(data) > MAX_BYTES:
        return "that file is not the Cubism Core (wrong size)"
    head = data[:2048]
    if not _CORE_VERSION.search(head) or b"Live2DCubismCore" not in data:
        return "that file is not Live2D's Cubism Core (live2dcubismcore.min.js)"
    if b"csmGetLatestMocVersion" not in data:
        return "that Cubism Core is too old - use the one from the Cubism 5 SDK for Web"
    return None


def _valid(name: str, data: bytes) -> str | None:
    if name == CORE:
        return check_core(data)
    want = FILES[name][1]
    got = hashlib.sha256(data).hexdigest()
    return None if got == want else f"{name}: checksum mismatch (got {got[:12]}..., want {want[:12]}...)"


def status() -> dict:
    st = _state()
    present = {n: (DIR / n).is_file() for n in FILES}
    return {
        "installed": all(present.values()),
        "accepted": bool(st.get("accepted_at")),
        "accepted_at": st.get("accepted_at", ""),
        "files": [{"name": n, "what": FILES[n][2], "present": present[n],
                   "size": (DIR / n).stat().st_size if present[n] else 0} for n in ORDER],
        "folder": str(DIR),
        "job": dict(JOB),
        "license_url": LIVE2D_LICENSE_URL,
        "open_license_url": LIVE2D_OPEN_LICENSE_URL,
        "expandable_url": LIVE2D_EXPANDABLE_URL,
        "download_page": LIVE2D_DOWNLOAD_PAGE,
    }


def script_urls(prefix: str) -> list[str]:
    """The runtime's scripts, in load order, each with ?v=<mtime> so a new download is a new URL."""
    out = []
    for n in ORDER:
        p = DIR / n
        try:
            out.append(f"{prefix}/{n}?v={int(p.stat().st_mtime)}")
        except OSError:
            out.append(f"{prefix}/{n}")
    return out


def file_path(name: str) -> Path | None:
    if name not in FILES:
        return None
    p = DIR / name
    return p if p.is_file() else None


def accept() -> None:
    st = _state()
    st["accepted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st["license"] = LIVE2D_LICENSE_URL
    _save_state(st)


def save_core(data: bytes) -> None:
    """The Core uploaded by hand (from Live2D's download page)."""
    err = check_core(data)
    if err:
        raise ValueError(err)
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = DIR / (CORE + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, DIR / CORE)


def download(force: bool = False) -> None:
    """Fetch every missing file (all of them with force); see JOB. start_download() runs it in a
    thread, after marking the job as running (so a caller polling JOB never sees it idle first)."""
    with _lock:
        if JOB["running"]:
            return
        JOB.update(running=True, error="", done=[], started=time.time())
    _download(force)


def _download(force: bool) -> None:
    import httpx

    try:
        DIR.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "Hexcast-Avatars"}) as c:
            for name in ORDER:
                target = DIR / name
                if target.is_file() and not force and _valid(name, target.read_bytes()) is None:
                    JOB["done"].append(name)
                    continue
                url = FILES[name][0]
                r = c.get(url)
                if r.status_code != 200:
                    raise RuntimeError(f"{FILES[name][2]}: {url} answered {r.status_code}")
                data = r.content
                if len(data) > MAX_BYTES:
                    raise RuntimeError(f"{name}: too big")
                err = _valid(name, data)
                if err:
                    raise RuntimeError(err)
                part = DIR / (name + ".part")
                part.write_bytes(data)
                os.replace(part, target)
                JOB["done"].append(name)
    except Exception as exc:                                    # shown in the tab, nothing half-written stays
        JOB["error"] = str(exc) or exc.__class__.__name__
    finally:
        JOB["running"] = False


def start_download(force: bool = False) -> None:
    with _lock:
        if JOB["running"]:
            return
        JOB.update(running=True, error="", done=[], started=time.time())
    threading.Thread(target=_download, args=(force,), name="avatar-runtime", daemon=True).start()
