"""Lipsync from an audio device: a microphone, a virtual cable, or a loopback tap on speakers.

For a bot that plays its voice somewhere itself (into a virtual cable, through the speakers ...)
instead of handing the audio to Hexcast. A browser source in OBS can't open audio devices, so
Hexcast records the device here and streams it, 16 kHz mono, to every renderer that shows an
avatar listening to it; the renderer runs the same vowel analysis as for speech it plays itself.

Two recorders. `soundcard` first (it also taps what a playback device plays). Its Windows backend
only understands devices whose shared-mode format is 32-bit float "extensible" - which a virtual
cable such as Voicemeeter is, and a plain USB microphone often is not (a mono USB mic reports plain
PCM and soundcard fails with a bare AssertionError). So when soundcard can't open an input device,
the same device is recorded through PortAudio (`sounddevice`), which takes any format and lets
Windows convert it to 16 kHz mono.

Optional: needs numpy and soundcard / sounddevice (in requirements.txt). Without them the device
choice says why and everything else works.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from typing import Callable

_errors: list[str] = []
try:
    import numpy as _np
except Exception as _exc:          # pragma: no cover - depends on the host
    _np = None
    _errors.append(f"numpy: {_exc.__class__.__name__}: {_exc}")
try:
    import warnings as _warnings

    import soundcard as _sc

    try:
        _warnings.filterwarnings("ignore", category=_sc.SoundcardRuntimeWarning)
    except Exception:
        _warnings.filterwarnings("ignore", message="data discontinuity in recording")
except Exception as _exc:          # pragma: no cover - depends on the host
    _sc = None
    _errors.append(f"soundcard: {_exc.__class__.__name__}: {_exc}")
try:
    import sounddevice as _sd
except Exception as _exc:          # pragma: no cover - not installed, or no PortAudio library on this system
    _sd = None
    _errors.append(f"sounddevice: {_exc.__class__.__name__}: {_exc}")

AVAILABLE = _np is not None and (_sc is not None or _sd is not None)
IMPORT_ERROR = "" if AVAILABLE else "; ".join(_errors)

RATE = 16000
BLOCK = 256                         # 16 ms
_PA_RANK = {"Windows WASAPI": 0, "MME": 1, "Windows DirectSound": 2}      # (WDM-KS is left out: it opens exclusively; DirectSound gave silence next to the others)


def _pa_inputs() -> list[dict]:
    """PortAudio's input devices as {name, index, api}, each name once: WASAPI first (it takes any rate and channel count
    in shared mode), then MME (which cuts names at 31 characters), then DirectSound."""
    apis = _sd.query_hostapis()
    best: dict[str, tuple[int, int, str]] = {}
    for i, d in enumerate(_sd.query_devices()):
        if d["max_input_channels"] < 1:
            continue
        api = str(apis[d["hostapi"]]["name"])
        if sys.platform.startswith("win") and api not in _PA_RANK:
            continue
        name = str(d["name"])
        if api == "MME" and len(name) >= 30:
            for full in [str(x["name"]) for x in _sd.query_devices() if x["max_input_channels"] > 0 and x["hostapi"] != d["hostapi"]]:
                if full.startswith(name):                      # the full name of a name MME cut short
                    name = full
                    break
        rank = _PA_RANK.get(api, 3)
        if name not in best or rank < best[name][0]:
            best[name] = (rank, i, api)
    return [{"name": n, "index": v[1], "api": v[2]} for n, v in best.items()]


def devices() -> list[dict]:
    """Recording devices and loopback taps of playback devices. `id` is what config stores."""
    if not AVAILABLE:
        return []
    out: list[dict] = []
    if _sc is not None:
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
    if not out and _sd is not None:                            # no soundcard: PortAudio's inputs (no loopback taps)
        try:
            out = [{"id": "input:" + d["name"], "label": d["name"], "kind": "input"} for d in _pa_inputs()]
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


def _explain(exc: Exception) -> str:
    """What went wrong, in words (soundcard's own failure on an unusual device is a bare AssertionError)."""
    if isinstance(exc, AssertionError) and not str(exc):
        return ("AssertionError: this device's Windows audio format is not one the recorder understands "
                "(a mono USB microphone often reports one)")
    return f"{exc.__class__.__name__}: {exc}"


class Capture:
    """One recording thread per device, shared by every avatar listening to it."""

    def __init__(self, dev: str, loop: asyncio.AbstractEventLoop, sink: Callable[[str, bytes], object]):
        self.dev, self.loop, self.sink = dev, loop, sink
        self.stop_evt = threading.Event()
        self.error = ""
        self.level = 0.0                                       # the loudest sample of the last second or so (0..1)
        self.backend = "soundcard" if _sc is not None else "sounddevice"
        self.note = ""
        self._tried: dict[str, str] = {}                       # the recorders that failed to open the device in this round, and why
        self.thread = threading.Thread(target=self._run, name=f"avatar-capture {dev}", daemon=True)
        self.thread.start()

    @property
    def running(self) -> bool:
        return self.thread.is_alive()

    def stop(self) -> None:
        self.stop_evt.set()

    def _emit(self, mono) -> bool:
        """One block of mono float samples to every renderer; False when there is nobody left to send to."""
        peak = float(_np.abs(mono).max()) if len(mono) else 0.0
        self.level = max(peak, self.level * 0.97)
        pcm = (_np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        if self.loop.is_closed():
            return False
        self.loop.call_soon_threadsafe(self.sink, self.dev, pcm)
        return True

    def _record_soundcard(self) -> None:
        mic = _open(self.dev)
        with mic.recorder(samplerate=RATE, channels=1, blocksize=BLOCK) as rec:
            self.error = ""
            self._tried.clear()
            while not self.stop_evt.is_set():
                data = rec.record(numframes=BLOCK)
                if not self._emit(data[:, 0] if data.ndim > 1 else data):
                    return

    def _record_portaudio(self) -> None:
        if not self.dev.startswith("input:"):
            raise RuntimeError("only the recorder that taps playback devices can record what a speaker plays")
        name = self.dev[6:]
        found = next((d for d in _pa_inputs() if d["name"] == name), None)
        if found is None:
            raise RuntimeError(f"recording device '{name}' is not there")
        kw = dict(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK, device=found["index"])
        if found["api"] == "Windows WASAPI":
            kw["extra_settings"] = _sd.WasapiSettings(auto_convert=True)       # Windows converts any format to 16 kHz mono
        with _sd.InputStream(**kw) as stream:
            self.error = ""
            self._tried.clear()
            while not self.stop_evt.is_set():
                data, _overflow = stream.read(BLOCK)
                if not self._emit(data[:, 0] if data.ndim > 1 else data):
                    return

    def _others(self) -> list[str]:
        """The recorders that can still be tried for this device in this round."""
        have = (["soundcard"] if _sc is not None else []) + (["sounddevice"] if _sd is not None and self.dev.startswith("input:") else [])
        return [b for b in have if b not in self._tried]

    def _run(self) -> None:
        backoff = 1.0
        while not self.stop_evt.is_set():
            try:
                (self._record_portaudio if self.backend == "sounddevice" else self._record_soundcard)()
                return
            except Exception as exc:                           # device unplugged, renamed, a format the recorder can't open ...
                self.level = 0.0
                if self.backend == "sounddevice":
                    try:                                       # PortAudio only lists the devices it saw at start: look again
                        _sd._terminate()
                        _sd._initialize()
                    except Exception:
                        pass
                why = _explain(exc)
                self._tried[self.backend] = why
                left = self._others()
                if left:                                       # the other recorder may open it: try it at once
                    self.note = f"recorded through {'PortAudio' if left[0] == 'sounddevice' else 'soundcard'}: the other recorder could not open it ({why})"
                    self.backend = left[0]
                    continue
                self.error = " | ".join(f"{b}: {w}" for b, w in self._tried.items()) if len(self._tried) > 1 else why
                self._tried.clear()
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
        return {dev: {"error": c.error, "level": round(c.level, 4), "backend": c.backend, "note": c.note}
                for dev, c in self.running.items()}
