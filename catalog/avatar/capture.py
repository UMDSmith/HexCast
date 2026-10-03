"""Lipsync from an audio device: a microphone, a virtual cable, or a loopback tap on speakers.

For a bot that plays its voice somewhere itself (into a virtual cable, through the speakers ...)
instead of handing the audio to Hexcast. A browser source in OBS can't open audio devices, so
Hexcast records the device here and streams it, 16 kHz mono, to every renderer that shows an
avatar listening to it; the renderer runs the same vowel analysis as for speech it plays itself.

Optional: needs numpy and soundcard (in requirements.txt). Without them the device choice says
why and everything else works.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

try:
    import warnings as _warnings

    import numpy as _np
    import soundcard as _sc

    try:
        _warnings.filterwarnings("ignore", category=_sc.SoundcardRuntimeWarning)
    except Exception:
        _warnings.filterwarnings("ignore", message="data discontinuity in recording")
    AVAILABLE, IMPORT_ERROR = True, ""
except Exception as _exc:          # pragma: no cover - depends on the host
    _np = _sc = None
    AVAILABLE, IMPORT_ERROR = False, f"{_exc.__class__.__name__}: {_exc}"

RATE = 16000
BLOCK = 256                         # 16 ms


def devices() -> list[dict]:
    """Recording devices and loopback taps of playback devices. `id` is what config stores."""
    if not AVAILABLE:
        return []
    out: list[dict] = []
    try:
        for m in _sc.all_microphones(include_loopback=False):
            out.append({"id": "input:" + str(m.name), "label": str(m.name), "kind": "input"})
    except Exception:
        pass
    try:
        for sp in _sc.all_speakers():
            out.append({"id": "loopback:" + str(sp.name), "label": str(sp.name) + " (what it plays)", "kind": "loopback"})
    except Exception:
        pass
    return out


def _open(dev: str):
    if dev.startswith("input:"):
        name = dev[6:]
        for m in _sc.all_microphones(include_loopback=False):
            if str(m.name) == name:
                return m
        raise RuntimeError(f"recording device '{name}' is not there")
    name = dev[9:] if dev.startswith("loopback:") else dev
    if not name:
        return _sc.get_microphone(str(_sc.default_speaker().name), include_loopback=True)
    for sp in _sc.all_speakers():
        if str(sp.name) == name:
            return _sc.get_microphone(str(sp.name), include_loopback=True)
    raise RuntimeError(f"playback device '{name}' is not there")


class Capture:
    """One recording thread per device, shared by every avatar listening to it."""

    def __init__(self, dev: str, loop: asyncio.AbstractEventLoop, sink: Callable[[str, bytes], object]):
        self.dev, self.loop, self.sink = dev, loop, sink
        self.stop_evt = threading.Event()
        self.error = ""
        self.level = 0.0
        self.thread = threading.Thread(target=self._run, name=f"avatar-capture {dev}", daemon=True)
        self.thread.start()

    @property
    def running(self) -> bool:
        return self.thread.is_alive()

    def stop(self) -> None:
        self.stop_evt.set()

    def _run(self) -> None:
        backoff = 1.0
        while not self.stop_evt.is_set():
            try:
                mic = _open(self.dev)
                self.error = ""
                with mic.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as rec:
                    backoff = 1.0
                    while not self.stop_evt.is_set():
                        data = rec.record(numframes=BLOCK)
                        mono = data[:, 0] if data.ndim > 1 else data
                        self.level = float(_np.abs(mono).max()) if len(mono) else 0.0
                        pcm = (_np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                        if self.loop.is_closed():
                            return
                        self.loop.call_soon_threadsafe(self.sink, self.dev, pcm)
            except Exception as exc:                       # device unplugged, renamed ... retry slowly
                self.error = f"{exc.__class__.__name__}: {exc}"
                self.level = 0.0
                if self.stop_evt.wait(backoff):
                    return
                backoff = min(10.0, backoff * 2)


class Captures:
    """Keeps exactly the devices that some shown avatar listens to running."""

    def __init__(self) -> None:
        self.running: dict[str, Capture] = {}

    def sync(self, wanted: set[str], loop: asyncio.AbstractEventLoop, sink) -> None:
        if not AVAILABLE:
            return
        for dev in list(self.running):
            if dev not in wanted or not self.running[dev].running:
                self.running.pop(dev).stop()
        for dev in wanted:
            if dev and dev not in self.running:
                self.running[dev] = Capture(dev, loop, sink)

    def stop_all(self, wait: float = 0.0) -> None:
        caps = list(self.running.values())
        self.running.clear()
        for c in caps:
            c.stop()
        end = time.monotonic() + wait
        for c in caps:
            c.thread.join(max(0.0, end - time.monotonic()))

    def status(self) -> dict:
        return {dev: {"error": c.error, "level": round(c.level, 3)} for dev, c in self.running.items()}

