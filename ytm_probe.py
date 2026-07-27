"""
Confirm the Socket.IO connection strategy for the YTMD companion server.

    python ytm_probe.py

/api/v1/realtime is a NAMESPACE, not the transport path. The JS client infers
that from the URL; python-socketio needs it passed explicitly.
"""

import asyncio
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "config"
HOST, PORT = "127.0.0.1", 9863
NS = "/api/v1/realtime"


def load():
    global HOST, PORT
    p = CONFIG_DIR / "ytmusic.json"
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            HOST, PORT = cfg.get("host", HOST), cfg.get("port", PORT)
        except Exception:
            pass
    t = CONFIG_DIR / "ytmusic_secrets.json"
    if t.exists():
        try:
            return json.loads(t.read_text(encoding="utf-8")).get("token", "")
        except Exception:
            pass
    return ""


async def raw_paths():
    """Which URL actually serves an Engine.IO handshake? A healthy one replies
    with a body starting 0{"sid":..."""
    import httpx
    for path in ("/socket.io/", "/api/v1/realtime/", "/api/v1/socket.io/"):
        url = f"http://{HOST}:{PORT}{path}?EIO=4&transport=polling"
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(url)
            body = r.text[:100].replace("\n", " ")
            tag = "HANDSHAKE OK" if body.startswith("0{") else "not here"
            print(f"  [{tag:12}] {path:22} -> {r.status_code}  {body}")
        except Exception as exc:
            print(f"  [{'error':12}] {path:22} -> {type(exc).__name__}: {exc}")


async def attempt(label, token, **kw):
    import socketio
    sio = socketio.AsyncClient(reconnection=False)
    got = asyncio.Event()
    box = {}

    @sio.on("state-update", namespace=kw.get("namespaces", [None])[0])
    async def _(data):
        box["d"] = data
        got.set()

    try:
        await sio.connect(f"http://{HOST}:{PORT}", wait_timeout=8, **kw)
        print(f"  [WORKS      ] {label}")
        print(f"                 sid={sio.sid}")
        try:
            await asyncio.wait_for(got.wait(), timeout=10)
            v = (box["d"].get("video") or {})
            p = (box["d"].get("player") or {})
            print(f"                 state-update: trackState={p.get('trackState')} "
                  f"'{v.get('title', '')}' by {v.get('author', '')}")
        except asyncio.TimeoutError:
            print("                 connected, but no update in 10s "
                  "(press play/pause to force one)")
        await sio.disconnect()
        return True
    except Exception as exc:
        print(f"  [fails      ] {label}  {type(exc).__name__}: {exc}")
        try:
            await sio.disconnect()
        except Exception:
            pass
        return False


async def main():
    token = load()
    if not token:
        print("No token - pair from the panel first.")
        return

    print("=" * 74)
    print(f"  Socket.IO probe  ->  http://{HOST}:{PORT}")
    print("=" * 74)

    print("\nA. Where does the Engine.IO handshake actually live?")
    await raw_paths()

    print("\nB. Client configurations")
    combos = [
        ("default path + namespace, websocket only",
         dict(namespaces=[NS], transports=["websocket"], auth={"token": token})),
        ("default path + namespace, polling then upgrade",
         dict(namespaces=[NS], transports=["polling", "websocket"], auth={"token": token})),
        ("default path + namespace, no auth payload",
         dict(namespaces=[NS], transports=["websocket"])),
    ]
    winners = [label for label, kw in combos if await attempt(label, token, **kw)]

    print("\n" + "=" * 74)
    if winners:
        print("  Working:")
        for w in winners:
            print(f"    - {w}")
    else:
        print("  Still nothing. Note the YTMD version from Settings > About.")
    print("=" * 74)


if __name__ == "__main__":
    asyncio.run(main())
