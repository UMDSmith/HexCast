"""
Hexcast avatar demo - a working example of driving a Hexcast avatar from a bot
over the control WebSocket.

Purpose: a copy-and-run starting point for anyone driving a Hexcast avatar from
their own bot. Standalone: it imports nothing from Hexcast, so the file can be
handed to someone as-is. The commands it sends are the same ones the HTTP API
takes (docs/avatar.md, "API"); the socket also takes the rest of the API by
name (`get`, `status`, `settings` ...), so a bot needs nothing else.

What it does, in order: opens Hexcast's avatar control WebSocket, prints the
avatars Hexcast has and the chosen avatar's model, checks whether an OBS source
is showing it, then makes the avatar happy, looks left, nods, switches two of
its expressions, plays its motions back to back (waiting for `motion_end`),
speaks a short hum with the lipsync switched off and on again mid-line (waiting
for `speech_end`), turns its head with a stream of frames, and lets everything
go.

Data: sends JSON commands over ws://<host>:4747/avatar/ws/control and prints
Hexcast's replies and events. The lipsync tick box is flipped twice, so it ends
as it was; nothing else is saved.

Needs: Hexcast running with the Avatars plugin set up, an avatar made with
"+ Avatar", and something drawing it (the OBS browser source, or the Avatars
tab in front) - commands only show on a screen that is drawing the avatar.
Events (speech_end, motion_end) come from the OBS source; without one the demo
waits a fixed time instead. `pip install websockets`.

Run: `python hexcast_demo.py [avatar_name]` (default: the first avatar Hexcast has).
"""

import base64
import io
import itertools
import json
import math
import struct
import sys
import time
import wave

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
        self.events = []  # events not waited for yet

    def send(self, cmd, fields=None):
        """Send one command and wait for Hexcast's reply to it. Hexcast only
        replies when the command carries a `rid`; events that arrive meanwhile
        are kept for wait_for(). Calls that don't need `avatar` (status ...)
        ignore it."""
        rid = next(self._rids)
        self.ws.send(json.dumps({"cmd": cmd, "avatar": self.name, "rid": rid, **(fields or {})}))
        while True:
            msg = self._next()
            if msg.get("rid") == rid:
                if msg.get("ok") is False:
                    raise RuntimeError(f"Hexcast refused {cmd}: {msg.get('error')}")
                return msg

    def stream(self, cmd, fields):
        """Fire-and-forget, for many messages a second. No `rid` means no reply;
        if it fails, the error shows up at the next send()."""
        self.ws.send(json.dumps({"cmd": cmd, "avatar": self.name, **fields}))

    def wait_for(self, events, id=None, timeout=15.0):
        """Wait for one of `events` (a name or a tuple of names) about this
        avatar - with `id`, the one a command's answer named. Returns the event,
        or None when it hasn't come within `timeout` seconds."""
        events = (events,) if isinstance(events, str) else tuple(events)
        deadline = time.monotonic() + timeout
        while True:
            for ev in self.events:
                if ev.get("event") in events and ev.get("avatar") == self.name and (id is None or ev.get("id") == id):
                    self.events.remove(ev)
                    return ev
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                self._next(left)
            except TimeoutError:
                return None

    def _next(self, timeout=None):
        """The next message. Events are kept (and printed); an error without a
        `rid` is a streamed command Hexcast refused."""
        msg = json.loads(self.ws.recv(timeout=timeout))
        if msg.get("type") == "event":
            print("  event:", {k: v for k, v in msg.items() if k not in ("type", "at")})
            self.events.append(msg)
        elif msg.get("ok") is False and msg.get("rid") is None:
            raise RuntimeError(f"Hexcast refused a streamed command: {msg.get('error')}")
        return msg


def hum(seconds=2.5, rate=16000):
    """A WAV of a voice-like hum in syllables, standing in for your TTS."""
    samples = []
    for i in range(int(seconds * rate)):
        t = i / rate
        syllables = abs(math.sin(math.pi * 3.5 * t)) ** 0.6  # 3.5 a second
        tone = sum(math.sin(2 * math.pi * 140 * k * t) / k for k in range(1, 6))  # 140 Hz and 4 harmonics
        samples.append(int(9000 * syllables * tone / 2.3))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


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
    print("  motions:    ", [m["name"] for m in info["motions"]])
    print("  emotions:   ", info["emotions"])
    print("  gestures:   ", info["gestures"])

    # The rest of the API is on the socket too: `get` is GET /avatar/api/avatars/<name>,
    # `status` is GET /avatar/api/status. Events come from the OBS source that shows the
    # avatar's overlay, so without one there is nothing to wait for.
    settings = avatar.send("get")["avatar"]
    on_stream = avatar.send("status")["by_overlay"].get(settings["overlay"], 0) > 0
    print(f"  overlay {settings['overlay']!r}, " + ("shown in OBS" if on_stream else
          "no OBS source shows it - the demo won't wait for events"))

    print("Happy for 3 s")
    avatar.send("emotion", {"name": "happy", "for": 3})
    time.sleep(1)

    print("Look left, then back")
    avatar.send("look", {"at": "left"})
    time.sleep(1.5)
    avatar.send("look", {"release": True})

    print("Nod")
    avatar.send("gesture", {"name": "nod"})
    time.sleep(2)

    # With the avatar's "A new expression replaces the others" setting on, the second
    # fades the first out; off, they stack. `"only": true / false` decides per call.
    exprs = info["expressions"][:2]
    if exprs:
        how = "replaces" if settings["replace_expressions"] else "stacks on"
        print(f"Expressions {' then '.join(exprs)}" + (f" (the second {how} the first)" if len(exprs) > 1 else ""))
        for x in exprs:
            print("  on now:", avatar.send("expression", {"name": x, "state": "on"})["active"])
            time.sleep(1.5)
        avatar.send("clear_expressions", {})

    # A motion takes over from the one playing, no stop needed. motion_end says why it
    # ended: replaced (the next one took over), finished or stopped.
    motions = [m for m in info["motions"] if "idle" not in (m["name"] + m["group"]).lower()][:2]
    if motions:
        print("Motions " + " then ".join(m["name"] for m in motions))
        started = []
        for m in motions:
            started.append(avatar.send("motion", {"group": m["group"], "index": m["index"]})["id"])
            time.sleep(1.5)
        for mid in started:
            end = avatar.wait_for("motion_end", mid, timeout=20) if on_stream else None
            if end:
                print(f"  {end['name']!r} ended: {end['reason']}")
        if on_stream and end is None:
            avatar.send("stop_motion", {})  # a long or looping one

    # Speech: hand over the audio (here as base64; `url` makes Hexcast fetch it). Over the
    # socket `speak` answers at once - speech_end, with the answer's id, says it has played.
    print("Speak (a hum stands in for your TTS); the lipsync goes off and on mid-line")
    said = avatar.send("speak", {"audio_b64": base64.b64encode(hum()).decode(), "text": "demo hum", "volume": 0.5})
    if said.get("warning"):
        print("  " + said["warning"])
    for _ in range(2):  # the Lipsync tick box, flipped and flipped back
        time.sleep(0.8)
        print("  lipsync", avatar.send("lipsync", {"state": "toggle"})["state"])
    if said["overlays"]:
        end = avatar.wait_for(("speech_end", "speech_error"), said["id"], timeout=15)
        print("  " + (f"{end['event']} after {end['duration']} s" if end else "no speech_end"))
    else:
        time.sleep(1)

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
