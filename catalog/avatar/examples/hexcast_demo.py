"""
Hexcast avatar demo - the smallest working example of connecting to a Hexcast
avatar and moving it.

Purpose: a copy-and-run starting point for anyone driving a Hexcast avatar from
their own bot. Standalone: it imports nothing from Hexcast, so the file can be
handed to someone as-is. The commands it sends are the same ones the HTTP API
takes (docs/avatar.md, "API"); the WebSocket is the way to go when you send a lot.

What it does, in order: opens Hexcast's avatar control WebSocket, prints the
avatars Hexcast has and the chosen avatar's model, then makes the avatar happy,
looks left, nods, turns its head with a stream of frames, and lets everything go.

Data: sends JSON commands over ws://<host>:4747/avatar/ws/control and prints
Hexcast's replies and events. Changes nothing on disk.

Needs: Hexcast running with the Avatars plugin set up, an avatar made with
"+ Avatar", and something drawing it (the OBS browser source, or the Avatars
tab in front) - commands only show on a screen that is drawing the avatar.
`pip install websockets`.

Run: `python hexcast_demo.py [avatar_name]` (default: the first avatar Hexcast has).
"""

import itertools
import json
import math
import sys
import time

from websockets.exceptions import InvalidHandshake
from websockets.sync.client import connect

# Swap localhost for the Hexcast PC's address when the bot runs on another PC.
HEXCAST_WS = "ws://localhost:4747/avatar/ws/control"


class Avatar:
    """One avatar on Hexcast's control WebSocket. `ws` is a connected
    websockets client; `name` is the avatar's name in the Avatars tab."""

    def __init__(self, ws, name):
        self.ws, self.name = ws, name
        self._rids = itertools.count(1)

    def send(self, cmd, fields=None):
        """Send one command and wait for Hexcast's reply to it. Hexcast only
        replies when the command carries a `rid`; events that arrive meanwhile
        are printed."""
        rid = next(self._rids)
        self.ws.send(json.dumps({"cmd": cmd, "avatar": self.name, "rid": rid, **(fields or {})}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("ok") is False:
                what = cmd if msg.get("rid") == rid else "a streamed command"
                raise RuntimeError(f"Hexcast refused {what}: {msg.get('error')}")
            if msg.get("rid") == rid:
                return msg
            print("  event:", msg)

    def stream(self, cmd, fields):
        """Fire-and-forget, for many messages a second. No `rid` means no reply;
        if it fails, the error shows up at the next send()."""
        self.ws.send(json.dumps({"cmd": cmd, "avatar": self.name, **fields}))


def run(ws, name=None):
    hello = json.loads(ws.recv())  # Hexcast greets every new connection
    print("Avatars in Hexcast:", hello["avatars"])
    if name is None:
        if not hello["avatars"]:
            raise RuntimeError('Hexcast has no avatar yet - make one with "+ Avatar" in the Avatars tab')
        name = hello["avatars"][0]
    avatar = Avatar(ws, name)

    info = avatar.send("info")
    print(f"Driving {name!r} - model {info['model_name'] or info['model']!r}")
    print("  expressions:", info["expressions"])
    print("  emotions:   ", info["emotions"])
    print("  gestures:   ", info["gestures"])

    print("Happy for 3 s")
    avatar.send("emotion", {"name": "happy", "for": 3})
    time.sleep(1)

    print("Look left, then back")
    avatar.send("look", {"at": "left"})
    time.sleep(1.5)
    avatar.send("look", {"release": True})

    print("Nod")
    avatar.send("gesture", {"name": "nod"})
    time.sleep(1.5)

    # Frame by frame, like your own head tracking or lipsync would. FaceAngleX
    # is a VTube Studio input (-30..30); it reaches the model through the
    # model's mappings. "hold": False = a pulse: Hexcast lets go of it right
    # after, so the model's own motion takes back over when the frames stop.
    print("Head turn (2 s of frames)")
    for frame in range(60):
        angle = 25 * math.sin(frame / 60 * 2 * math.pi)
        avatar.stream("params", {"values": {"FaceAngleX": angle}, "hold": False})
        time.sleep(1 / 30)

    avatar.send("release", {})  # let go of anything still held
    print("Done")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        with connect(HEXCAST_WS) as ws:
            run(ws, name)
    except OSError as exc:
        sys.exit(f"Could not reach Hexcast at {HEXCAST_WS} ({exc}). Is it running?")
    except InvalidHandshake as exc:
        sys.exit(f"Hexcast answered, but not as the avatar socket ({exc}). Is the Avatars plugin installed?")
    except RuntimeError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
