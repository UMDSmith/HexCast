"""
Diagnose the HexCast <-> YouTube Music Desktop connection.

Run from the hexcast folder with the venv active:

    python ytm_check.py

Tests each layer in order and prints the real error instead of swallowing it.
"""

import asyncio
import json
import sys
import traceback
from pathlib import Path

HOST = "127.0.0.1"
PORT = 9863

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def line(msg=""):
    print(msg, flush=True)


def ok(msg):
    line(f"  [ok]   {msg}")


def bad(msg):
    line(f"  [FAIL] {msg}")


def load_token() -> str:
    p = CONFIG_DIR / "ytmusic_secrets.json"
    if not p.exists():
        return ""
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("token", "")
    except Exception:
        return ""


def load_hostport():
    global HOST, PORT
    p = CONFIG_DIR / "ytmusic.json"
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            HOST = cfg.get("host", HOST)
            PORT = cfg.get("port", PORT)
        except Exception:
            pass


async def main():
    load_hostport()
    base = f"http://{HOST}:{PORT}"
    line("=" * 62)
    line(f"  YouTube Music Desktop check  ->  {base}")
    line("=" * 62)

    # ---- 1. packages -----------------------------------------------------
    line("\n1. Python packages")
    line(f"       interpreter: {sys.version.split()[0]}  ({sys.executable})")

    def pkgver(dist):
        """Read the installed version from metadata. Modules do not reliably
        expose __version__ - python-socketio in particular does not."""
        try:
            from importlib.metadata import version
            return version(dist)
        except Exception:
            return "?"

    try:
        import httpx
        ok(f"httpx {pkgver('httpx')}")
    except ImportError:
        bad("httpx is missing:  pip install httpx")
        return

    try:
        import socketio
    except ImportError:
        bad("python-socketio is missing:  pip install python-socketio")
        return

    if not hasattr(socketio, "AsyncClient"):
        bad("the 'socketio' module is present but has no AsyncClient.")
        bad(f"it resolved to: {getattr(socketio, '__file__', 'unknown')}")
        bad("you likely installed the abandoned 'socketio' package instead of")
        bad("'python-socketio'. Fix with:")
        bad("    pip uninstall -y socketio")
        bad("    pip install python-socketio")
        return
    ok(f"python-socketio {pkgver('python-socketio')}")

    try:
        import engineio  # noqa: F401
        ok(f"python-engineio {pkgver('python-engineio')}")
    except ImportError:
        bad("python-engineio is missing:  pip install python-engineio")
        return

    try:
        import aiohttp  # noqa: F401
        ok(f"aiohttp {pkgver('aiohttp')}")
    except ImportError:
        bad("aiohttp is missing - socketio.AsyncClient cannot connect without it")
        bad("    pip install aiohttp")
        if sys.version_info >= (3, 14):
            bad("")
            bad(f"note: you are on Python {sys.version_info.major}.{sys.version_info.minor}.")
            bad("aiohttp ships compiled wheels and may not have one for this")
            bad("version yet, in which case pip will try to build from source")
            bad("and fail without Visual C++ build tools. If that happens,")
            bad("rebuild the venv on Python 3.12 or 3.13.")
        return

    # ---- 2. token --------------------------------------------------------
    line("\n2. Stored token")
    token = load_token()
    if token:
        ok(f"found in config/ytmusic_secrets.json ({token[:8]}...)")
    else:
        bad("no token - pair from the panel first")
        return

    # ---- 3. metadata (no auth) -------------------------------------------
    line("\n3. GET /metadata  (public, no auth)")
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{base}/metadata")
        if r.status_code == 200:
            ok(f"{r.status_code}  {r.text.strip()[:120]}")
        else:
            bad(f"{r.status_code}  {r.text[:200]}")
            return
    except Exception as exc:
        bad(f"{type(exc).__name__}: {exc}")
        bad("the app is not running, the companion server is off, or the port is wrong")
        return

    # ---- 4. state (auth) -------------------------------------------------
    line("\n4. GET /api/v1/state  (needs the token)")
    state = None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/api/v1/state", headers={"Authorization": token})
        if r.status_code == 200:
            state = r.json()
            video = state.get("video") or {}
            player = state.get("player") or {}
            ok(f"{r.status_code}  trackState={player.get('trackState')} "
               f"progress={player.get('videoProgress')}")
            if video:
                ok(f"playing: {video.get('author')} - {video.get('title')}")
                thumbs = video.get("thumbnails") or []
                ok(f"artwork: {len(thumbs)} thumbnail(s), largest "
                   f"{max((t.get('width', 0) for t in thumbs), default=0)}px")
            else:
                ok("no video in state - start something playing for a fuller test")
        elif r.status_code in (401, 403):
            bad(f"{r.status_code} - the token was rejected. Pair again.")
            return
        else:
            bad(f"{r.status_code}  {r.text[:200]}")
    except Exception as exc:
        bad(f"{type(exc).__name__}: {exc}")

    # ---- 5. socket.io ----------------------------------------------------
    line("\n5. Socket.IO  /api/v1/realtime")
    got = asyncio.Event()
    received = []

    sio = socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)

    @sio.on("state-update")
    async def _on_state(data):
        received.append(data)
        got.set()

    try:
        await sio.connect(
            base,
            socketio_path="/api/v1/realtime",
            transports=["websocket"],
            auth={"token": token},
            wait_timeout=10,
        )
        ok(f"connected, sid={sio.sid}")
        line("       waiting up to 12s for a state-update ...")
        try:
            await asyncio.wait_for(got.wait(), timeout=12)
            d = received[0]
            v = (d.get("video") or {})
            p = (d.get("player") or {})
            ok(f"received state-update: trackState={p.get('trackState')} "
               f"'{v.get('title', '')}'")
        except asyncio.TimeoutError:
            bad("connected but no state-update arrived in 12s")
            bad("updates only fire when the player state changes - try pressing")
            bad("play/pause or skipping a track while this is running")
        await sio.disconnect()
    except Exception as exc:
        bad(f"{type(exc).__name__}: {exc}")
        line()
        traceback.print_exc()
        line()
        bad("common causes:")
        bad("  - aiohttp missing (see step 1)")
        bad("  - connecting to 'localhost' instead of 127.0.0.1 on Windows")
        bad("  - an old python-socketio that does not speak Engine.IO v4")

    line("\n" + "=" * 62)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
