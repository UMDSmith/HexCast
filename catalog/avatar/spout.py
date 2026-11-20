"""
Hexcast Avatars - Spout output
==============================

Spout (https://spout.zeal.co) is how Windows apps hand each other video frames with transparency: a sender
shares a Direct3D 11 texture and writes its name and handle into named shared memory, and any receiver
(Shoost, VTube Studio's background, Resolume, TouchDesigner, OBS with the Spout2 plugin ...) opens the
same texture. This is a sender written straight against Windows - d3d11.dll and kernel32 through ctypes -
so it needs no package and no install (the Spout SDK's Python bindings are not built for every Python).

What it does, as the Spout SDK (2.007) does it:
  - a shared texture (D3D11_RESOURCE_MISC_SHARED, B8G8R8A8), its handle in a 280-byte block of shared
    memory named after the sender: {handle, width, height, format, usage, description, partner}
  - the sender's name in the list of senders ("SpoutSenderNames": 256-byte names one after the other,
    ended by an empty one), and as the active sender ("ActiveSenderName") when there is none
  - "<name>_SpoutAccessMutex", held while a frame is written, so a receiver never copies half of one
Frames come in as top-down B8G8R8A8 bytes (straight alpha) and are copied into the texture.

Output (below) is the rest of it: a hidden Chrome / Edge draws the overlay page and sends its frames back to Hexcast.
"""

from __future__ import annotations

import asyncio
import collections
import ctypes
import logging
import mmap
import os
import subprocess
import sys
import threading
import time

AVAILABLE = sys.platform == "win32"
if AVAILABLE:
    from ctypes import wintypes as wt

MAX_SENDERS = 64                  # the SDK's default (HKCU\Software\Leading Edge\Spout\MaxSenders)
NAME_LEN = 256
INFO_SIZE = 280
DXGI_FORMAT_B8G8R8A8_UNORM = 87

if AVAILABLE:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    _k32.CreateMutexW.restype = wt.HANDLE
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _k32.WaitForSingleObject.restype = wt.DWORD
    _k32.ReleaseMutex.argtypes = [wt.HANDLE]
    _k32.CloseHandle.argtypes = [wt.HANDLE]


class SpoutError(Exception):
    pass


# ---- Direct3D 11 through its COM vtables -------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, s: str) -> "_GUID":
        h = s.replace("-", "")
        return cls(int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16), (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))


class _SampleDesc(ctypes.Structure):
    _fields_ = [("Count", ctypes.c_uint), ("Quality", ctypes.c_uint)]


class _Box(ctypes.Structure):
    _fields_ = [("left", ctypes.c_uint), ("top", ctypes.c_uint), ("front", ctypes.c_uint),
                ("right", ctypes.c_uint), ("bottom", ctypes.c_uint), ("back", ctypes.c_uint)]


class _Tex2DDesc(ctypes.Structure):
    _fields_ = [("Width", ctypes.c_uint), ("Height", ctypes.c_uint), ("MipLevels", ctypes.c_uint), ("ArraySize", ctypes.c_uint),
                ("Format", ctypes.c_uint), ("SampleDesc", _SampleDesc), ("Usage", ctypes.c_uint), ("BindFlags", ctypes.c_uint),
                ("CPUAccessFlags", ctypes.c_uint), ("MiscFlags", ctypes.c_uint)]


IID_IDXGIResource = _GUID.of("035f3ab4-482e-4e50-b41f-8a7f8bd8960b")

# vtable slots: IUnknown 0-2; ID3D11Device CreateTexture2D 5; IDXGIResource GetSharedHandle 8;
# ID3D11DeviceContext UpdateSubresource 48, Flush 111
_QI, _RELEASE, _CREATE_TEX2D, _GET_SHARED_HANDLE, _UPDATE_SUBRESOURCE, _FLUSH = 0, 2, 5, 8, 48, 111


def _call(obj: ctypes.c_void_p, slot: int, restype, argtypes: list, *args):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[slot])
    return fn(obj, *args)


def _release(obj: ctypes.c_void_p | None) -> None:
    if obj:
        _call(obj, _RELEASE, ctypes.c_ulong, [])


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise SpoutError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")


class _Device:
    """A Direct3D 11 device on the default graphics card, and a shared texture on it."""

    def __init__(self) -> None:
        d3d = ctypes.WinDLL("d3d11")
        create = d3d.D3D11CreateDevice
        create.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                           ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_long
        self.dev, self.ctx = ctypes.c_void_p(), ctypes.c_void_p()
        level = ctypes.c_uint()
        hr = -1
        for levels in ((0xB100, 0xB000), (0xB000,)):               # 11.1 where the runtime knows it, else 11.0
            arr = (ctypes.c_uint * len(levels))(*levels)
            # hardware driver, BGRA support, SDK version 7
            hr = create(None, 1, None, 0x20, arr, len(levels), 7, ctypes.byref(self.dev), ctypes.byref(level), ctypes.byref(self.ctx))
            if hr >= 0:
                break
        _check(hr, "D3D11CreateDevice")
        self.tex = ctypes.c_void_p()
        self.handle = 0
        self.width = self.height = 0
        self.frames_written = 0

    def texture(self, width: int, height: int) -> None:
        self.free_texture()
        desc = _Tex2DDesc(width, height, 1, 1, DXGI_FORMAT_B8G8R8A8_UNORM, _SampleDesc(1, 0),
                          0, 0x8 | 0x20, 0, 0x2)                    # default usage; shader resource + render target; shared
        _check(_call(self.dev, _CREATE_TEX2D, ctypes.c_long, [ctypes.POINTER(_Tex2DDesc), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                     ctypes.byref(desc), None, ctypes.byref(self.tex)), "CreateTexture2D")
        res = ctypes.c_void_p()
        _check(_call(self.tex, _QI, ctypes.c_long, [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)],
                     ctypes.byref(IID_IDXGIResource), ctypes.byref(res)), "QueryInterface(IDXGIResource)")
        handle = ctypes.c_void_p()
        try:
            _check(_call(res, _GET_SHARED_HANDLE, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(handle)), "GetSharedHandle")
        finally:
            _release(res)
        self.handle = (handle.value or 0) & 0xFFFFFFFF                 # (a shared handle fits 32 bits: the info block keeps 32)
        self.width, self.height = width, height
        self.frames_written = 0

    def write(self, data, rects: list[tuple[int, int, int, int, int]]) -> None:
        """Each (x, y, w, h, offset): the rectangle's pixels at `offset` in data, rows of w * 4 bytes."""
        keep = (ctypes.c_char * len(data)).from_buffer(data) if isinstance(data, bytearray) else ctypes.c_char_p(bytes(data))
        base = ctypes.cast(keep, ctypes.c_void_p).value
        for x, y, w, h, off in rects:
            box = _Box(x, y, 0, x + w, y + h, 1)
            _call(self.ctx, _UPDATE_SUBRESOURCE, None, [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(_Box), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint],
                  self.tex, 0, ctypes.byref(box), ctypes.c_void_p(base + off), w * 4, 0)
        _call(self.ctx, _FLUSH, None, [])
        self.frames_written += 1

    def free_texture(self) -> None:
        _release(self.tex)
        self.tex = ctypes.c_void_p()

    def close(self) -> None:
        self.free_texture()
        _release(self.ctx)
        _release(self.dev)
        self.ctx = self.dev = ctypes.c_void_p()


# ---- the shared memory the SDK reads --------------------------------------------------------------

class _Mutex:
    def __init__(self, name: str) -> None:
        self.h = _k32.CreateMutexW(None, False, name)

    def __enter__(self):
        if self.h:
            _k32.WaitForSingleObject(self.h, 67)                     # (the SDK's wait; it carries on after it, as the SDK does)
        return self

    def __exit__(self, *exc):
        if self.h:
            _k32.ReleaseMutex(self.h)

    def close(self) -> None:
        if self.h:
            _k32.CloseHandle(self.h)
            self.h = None


def _name_bytes(name: str) -> bytes:
    return name.encode("utf-8")[:NAME_LEN - 1]


def _read_names(m: mmap.mmap) -> list[bytes]:
    out = []
    for i in range(MAX_SENDERS):
        raw = m[i * NAME_LEN:(i + 1) * NAME_LEN]
        if not raw[:1] or raw[0] == 0:
            break
        out.append(raw.split(b"\0", 1)[0])
    return out


def _write_names(m: mmap.mmap, names: list[bytes]) -> None:
    block = bytearray(MAX_SENDERS * NAME_LEN)
    for i, n in enumerate(names[:MAX_SENDERS]):
        block[i * NAME_LEN:i * NAME_LEN + len(n)] = n
    m[:len(block)] = bytes(block)


def sender_names() -> list[str]:
    """The Spout senders running on this PC now (as a receiver lists them)."""
    if not AVAILABLE:
        return []
    m = mmap.mmap(-1, MAX_SENDERS * NAME_LEN, tagname="SpoutSenderNames")
    try:
        return [n.decode("utf-8", "replace") for n in _read_names(m)]
    finally:
        m.close()


class Sender:
    """One Spout sender. send() takes a frame (top-down B8G8R8A8 bytes, straight alpha) - from one thread at a time."""

    def __init__(self, name: str) -> None:
        if not AVAILABLE:
            raise SpoutError("Spout is Windows only")
        self.name = name
        self.key = _name_bytes(name)
        self._lock = threading.Lock()
        self._names = mmap.mmap(-1, MAX_SENDERS * NAME_LEN, tagname="SpoutSenderNames")
        self._names_mutex = _Mutex("SpoutSenderNames_mutex")
        self._active = mmap.mmap(-1, NAME_LEN, tagname="ActiveSenderName")
        self._info = None
        self._access = None
        self._dev: _Device | None = None
        self.frames = 0
        self._register()

    def _register(self) -> None:
        with self._names_mutex:
            names = _read_names(self._names)
            if self.key in names:
                raise SpoutError(f"there is already a Spout sender called {self.name!r}")
            if len(names) >= MAX_SENDERS:
                raise SpoutError("too many Spout senders on this PC")
            _write_names(self._names, names + [self.key])
            active = self._active[:NAME_LEN].split(b"\0", 1)[0]
            if not active or active not in names:                      # no active sender (or a gone one): this one
                self._active[:NAME_LEN] = self.key + b"\0" * (NAME_LEN - len(self.key))
        self._info = mmap.mmap(-1, INFO_SIZE, tagname=self.name)
        self._access = _Mutex(self.name + "_SpoutAccessMutex")

    def _write_info(self) -> None:
        d = self._dev
        exe = os.path.abspath(sys.executable).encode("utf-8")[:255]
        block = bytearray(INFO_SIZE)
        block[0:20] = b"".join(int(v).to_bytes(4, "little") for v in (d.handle, d.width, d.height, DXGI_FORMAT_B8G8R8A8_UNORM, 0))
        block[20:20 + len(exe)] = exe                                   # (the SDK keeps the sender's program there)
        self._info[:INFO_SIZE] = bytes(block)

    def send(self, frame, width: int, height: int) -> None:
        """A whole frame."""
        if len(frame) < width * height * 4:
            raise SpoutError(f"a {width}x{height} frame is {width * height * 4} bytes, not {len(frame)}")
        self.update(frame, width, height, [(0, 0, width, height, 0)])

    def update(self, data, width: int, height: int, rects: list[tuple[int, int, int, int, int]]) -> None:
        """Parts of a frame of that size: (x, y, w, h, offset of its pixels in data). The texture keeps the rest as it was
        (a new size starts a new texture: the first frame of a size has to be whole)."""
        with self._lock:
            if self._info is None:
                raise SpoutError("the sender is closed")
            if self._dev is None:
                self._dev = _Device()
            if (self._dev.width, self._dev.height) != (width, height):
                with self._access:
                    self._dev.texture(width, height)
                    self._write_info()
            with self._access:
                self._dev.write(data, rects)
            self.frames += 1

    @property
    def fresh(self) -> bool:
        """No texture yet (or a new one): the next frame has to be whole."""
        return self._dev is None or self._dev.frames_written == 0

    def close(self) -> None:
        with self._lock:
            if self._info is None:
                return
            with self._names_mutex:
                names = [n for n in _read_names(self._names) if n != self.key]
                _write_names(self._names, names)
                if self._active[:NAME_LEN].split(b"\0", 1)[0] == self.key:
                    nxt = names[0] if names else b""
                    self._active[:NAME_LEN] = nxt + b"\0" * (NAME_LEN - len(nxt))
            for m in (self._info, self._names, self._active):
                m.close()
            self._info = None
            for mx in (self._access, self._names_mutex):
                if mx:
                    mx.close()
            if self._dev:
                self._dev.close()
                self._dev = None


# ---- the output: a hidden browser draws the overlay; its frames go out as a Spout sender ------------------

log = logging.getLogger("hexcast.avatar.spout")


def parse_message(m: bytes, width: int, height: int) -> tuple[list[tuple[int, int, int, int, int]], bool]:
    """A frame message from the page (avatar_engine.js SpoutPacker): "HXS1", count (u16), 0 (u16), then x, y, w, h (u16 each)
    per rectangle, then each rectangle's pixels. -> ([(x, y, w, h, offset)], whether it is a whole frame). Anything that
    doesn't add up is an error (nothing of it is used)."""
    if len(m) < 8 or m[:4] != b"HXS1":
        raise SpoutError("not a frame")
    n = int.from_bytes(m[4:6], "little")
    off = 8 + 8 * n
    if not n or len(m) < off:
        raise SpoutError("a frame without its rectangles")
    out = []
    for i in range(n):
        x, y, w, h = (int.from_bytes(m[8 + i * 8 + k * 2:10 + i * 8 + k * 2], "little") for k in range(4))
        if not w or not h or x + w > width or y + h > height:
            raise SpoutError(f"a rectangle off the frame ({x}, {y}, {w}x{h})")
        out.append((x, y, w, h, off))
        off += w * h * 4
    if off != len(m):
        raise SpoutError(f"{len(m)} bytes, not {off}")
    return out, n == 1 and out[0][:4] == (0, 0, width, height)

# the browsers that can draw the overlay without a window (Chromium: WebGL 2 on the graphics card)
BROWSERS = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
]


def find_browser(custom: str = "") -> str | None:
    """Chrome, Edge or Brave on this PC (a path the streamer gave, or HEXCAST_BROWSER, first)."""
    for p in (custom, os.environ.get("HEXCAST_BROWSER", "")):
        if p and os.path.isfile(p):
            return p
    for b in BROWSERS:
        p = os.path.expandvars(b)
        if "%" not in p and os.path.isfile(p):
            return p
    if AVAILABLE:
        import winreg
        for exe in ("chrome.exe", "msedge.exe", "brave.exe"):
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        p = winreg.QueryValue(k, None)
                except OSError:
                    continue
                if p and os.path.isfile(p.strip('"')):
                    return p.strip('"')
    return None


def browser_args(browser: str, url: str, profile: str, audio: bool) -> list[str]:
    args = [browser, "--headless=new", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--disable-background-networking", "--disable-component-update",
            "--window-size=1920,1080", "--force-device-scale-factor=1", "--hide-scrollbars",
            "--autoplay-policy=no-user-gesture-required",
            # draw on the graphics card (a headless browser falls back to drawing on the processor otherwise)
            "--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist",
            # never slow down: nobody looks at this page, and it must keep its frame rate
            "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows", "--disable-features=CalculateNativeWinOcclusion"]
    if not audio:
        args.append("--mute-audio")
    return args + [url]


class _Job:
    """A Windows job that takes the browsers down with Hexcast: when Hexcast's process ends - however it ends - Windows
    closes the job and ends every process in it (the browsers and the processes they start)."""

    def __init__(self) -> None:
        self.h = None
        if not AVAILABLE:
            return

        class _Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wt.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

        class _Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        _k32.CreateJobObjectW.restype = wt.HANDLE
        _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        _k32.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        _k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        _k32.OpenProcess.restype = wt.HANDLE
        _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        h = _k32.CreateJobObjectW(None, None)
        if not h:
            return
        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000              # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if _k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):   # JobObjectExtendedLimitInformation
            self.h = h
        else:
            _k32.CloseHandle(h)

    def add(self, pid: int) -> None:
        if not self.h:
            return
        ph = _k32.OpenProcess(0x0100 | 0x0001, False, pid)             # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if ph:
            _k32.AssignProcessToJobObject(self.h, ph)
            _k32.CloseHandle(ph)


_JOB: _Job | None = None


class Output:
    """One overlay sent out as one Spout sender: its hidden browser (started again if it stops), the frames it sends back
    (the newest one wins: a frame that comes while the last is still being copied replaces the waiting one), and the sender."""

    def __init__(self, overlay: str, name: str) -> None:
        self.overlay, self.name = overlay, name
        self.fps, self.audio = 30, True
        self.enabled = False
        self.state, self.error = "off", ""
        self.browser = ""
        self.proc: subprocess.Popen | None = None
        self.task: asyncio.Task | None = None
        self.sender: Sender | None = None
        self.frames_in = self.frames_out = 0
        self.rate = 0.0                       # frames a second that reach Spout
        self._rate_n, self._rate_t = 0, time.time()
        self.last_frame = 0.0
        self.size = (0, 0)
        self._queue: collections.deque = collections.deque()
        self.need_full = False                # parts of frames are dropped until a whole one comes (the sender lost track)
        self.asked, self._asked_at = False, 0.0
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._quit = False

    # -- messages from the page (on the event loop) -> Spout (its own thread: the copy into the graphics card takes a few ms).
    # They are applied in order - each one changes the frame before it - and a page that gets too far ahead (or a sender that
    # had to start again) is asked for a whole frame instead.
    def push(self, message: bytes, width: int, height: int) -> None:
        if not self.enabled:
            return
        try:
            rects, whole = parse_message(message, width, height)
        except SpoutError as exc:
            self.error = f"the page: {exc}"
            return
        self.frames_in += 1
        if len(self._queue) >= 4:                                     # far behind: start again from a whole frame
            self._lost()
        if self.need_full and not whole:
            return
        self.need_full = self.asked = False
        self._queue.append((message, width, height, rects, whole))
        self._wake.set()
        if self._thread is None:
            self._quit = False
            self._thread = threading.Thread(target=self._pump, name=f"spout-{self.overlay}", daemon=True)
            self._thread.start()

    def _pump(self) -> None:
        while not self._quit:
            if not self._wake.wait(0.5):
                continue
            self._wake.clear()
            while self._queue and not self._quit:
                message, w, h, rects, whole = self._queue.popleft()
                self._apply(message, w, h, rects, whole)
        if self.sender is not None:
            self.sender.close()
            self.sender = None

    def _apply(self, message: bytes, w: int, h: int, rects: list, whole: bool) -> None:
        try:
            if self.sender is None:
                self.sender = Sender(self.name)
            if not whole and (self.sender.fresh or self.size != (w, h)):
                self._lost()                                            # (a part of a frame it never had)
                return
            self.sender.update(message, w, h, rects)
            self.frames_out += 1
            self._rate_n += 1
            self.size = (w, h)
            self.last_frame = time.time()
            if self.error.startswith("Spout:"):
                self.error = ""
        except Exception as exc:                                       # (a name taken by another program, a lost graphics card ...)
            self.error = f"Spout: {exc}"
            log.warning("spout output %s: %s", self.overlay, exc)
            if self.sender is not None:
                self.sender.close()
                self.sender = None
            self._lost()
            time.sleep(2)

    def _lost(self) -> None:
        """Parts of frames are no use now: drop them until a whole frame comes (the page is asked for one)."""
        self._queue.clear()
        self.need_full = True
        self.asked = False

    def ask_full(self) -> bool:
        """Should the page be asked for a whole frame now? (once, then again after 2 s if none came)"""
        if not self.need_full or (self.asked and time.time() - self._asked_at < 2):
            return False
        self.asked, self._asked_at = True, time.time()
        return True

    def status(self) -> dict:
        now = time.time()
        if now - self._rate_t >= 1:
            self.rate = self._rate_n / (now - self._rate_t)
            self._rate_n, self._rate_t = 0, now
        live = now - self.last_frame < 2
        state = self.state
        if self.enabled and state == "running" and not live:
            state = "starting"
        return {"overlay": self.overlay, "name": self.name, "enabled": self.enabled, "state": state, "error": self.error,
                "fps": round(self.rate, 1) if live else 0, "frames": self.frames_out, "size": list(self.size),
                "browser": self.browser, "audio": self.audio, "target_fps": self.fps}

    # -- the browser
    def start(self, url: str, profile: str, browser_path: str) -> None:
        self.enabled = True
        if self.state in ("off", ""):
            self.state = "starting"
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self._supervise(url, profile, browser_path))

    async def _supervise(self, url: str, profile: str, browser_path: str) -> None:
        global _JOB
        wait = 2.0
        while self.enabled:
            browser = find_browser(browser_path)
            if not browser:
                self.state, self.error = "error", "no Chrome, Edge or Brave found on this PC - install one, or give its path"
                await asyncio.sleep(10)
                continue
            self.browser = browser
            os.makedirs(profile, exist_ok=True)
            try:
                self.proc = subprocess.Popen(browser_args(browser, url, profile, self.audio), stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                self.state, self.error = "error", f"the browser would not start: {exc}"
                await asyncio.sleep(10)
                continue
            if _JOB is None:
                _JOB = _Job()
            _JOB.add(self.proc.pid)
            self.state, self.error = "running", ""
            started = time.time()
            while self.enabled and self.proc.poll() is None:
                await asyncio.sleep(0.5)
            if not self.enabled:
                break
            code = self.proc.returncode
            self.proc = None
            wait = 2.0 if time.time() - started > 60 else min(60.0, wait * 2)
            self.state, self.error = "error", f"the browser stopped (exit code {code}) - starting it again in {wait:.0f} s"
            log.warning("spout output %s: browser exited with %s", self.overlay, code)
            await asyncio.sleep(wait)
        self._kill()

    def _kill(self) -> None:
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

    async def stop(self) -> None:
        self.enabled = False
        self.state, self.error = "off", ""
        t, self.task = self.task, None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await asyncio.to_thread(self._kill)
        self._quit = True
        self._wake.set()
        th, self._thread = self._thread, None
        if th is not None:
            await asyncio.to_thread(th.join, 3)
        self._queue.clear()
