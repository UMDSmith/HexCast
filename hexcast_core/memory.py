"""Where Hexcast's memory goes: the read-only Memory page (/memory) and GET /api/memory.

A leak can hide in three places, so the page shows all three:

- The process as the system sees it: memory in RAM and in swap, open sockets and files, threads.
- What each module holds right now: everything reachable from a plugin's (or the core's) module globals,
  its routes, its shutdown hooks and the asyncio tasks running its code, measured with sys.getsizeof.
  Something two modules share is counted once, under the first one that reaches it. Memory outside
  Python objects (TLS contexts, socket buffers in C, the allocator's free lists) is not in it: the gap
  between the process total and the modules is shown as "everything else". Bytes a websocket client has
  not taken yet are read from each connection's transport and shown next to the module that owns it.
- What each module's code allocated (tracemalloc) - only when Hexcast was started with PYTHONTRACEMALLOC.

A sampler notes the process totals every 5 minutes and each module's share every 30 minutes, for 3 days,
so the page shows growth even when nobody had it open. Once an hour it prints one dated line to the
console, so a log kept across restarts shows where an old process's memory went.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import platform
import sys
import threading
import time
import tracemalloc
import types
from collections import Counter, deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

log = logging.getLogger("hexcast")

CORE = "core"                       # the soundboard (hexcast.py) and hexcast_core
SAMPLE_SECONDS = 300                # process totals
MODULES_SECONDS = 1800              # each module's share
LOG_SECONDS = 3600                  # one console line
KEEP_SECONDS = 3 * 86400            # how far back the history goes
FRESH_SECONDS = 30                  # the page re-counts the modules at most this often
ALLOC_FRESH_SECONDS = 60            # ... and re-reads tracemalloc at most this often
FIRST_SAMPLE_SECONDS = 20           # the first count, once start-up has settled
WALK_SECONDS = 30.0                 # a count stops (and says it is partial) after this long ...
WALK_OBJECTS = 5_000_000            # ... or this many objects
CHART_POINTS = 400

_STARTED = time.time()
_PKG = Path(__file__).resolve().parent
_MAIN = _PKG.parent / "hexcast.py"
# Socket and server plumbing: shared by every connection, so it belongs to no module (the bytes a connection
# is holding are reported per socket instead).
_SHARED_PACKAGES = {"uvicorn", "uvloop", "websockets", "wsproto", "h11", "httptools", "watchdog"}
_SHARED_TYPES: tuple = (types.ModuleType, types.CodeType, asyncio.AbstractEventLoop, threading.Thread,
                        logging.Logger, logging.Handler, logging.Manager, logging.PlaceHolder)
_SHARED, _DATA = object(), object()


def _norm(p: Any) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


class _Owners:
    """Which module a module name, a source file or a class belongs to: a plugin id, CORE, or None
    (Python itself and the libraries)."""

    def __init__(self, plugins_dir: Path | None) -> None:
        self.plugins = _norm(plugins_dir) + os.sep if plugins_dir else None
        self.core_pkg = _norm(_PKG) + os.sep
        self.core_main = _norm(_MAIN)
        self._names: dict[str, str | None] = {}
        self._files: dict[str, str | None] = {}

    def of_name(self, name: Any) -> str | None:
        if not isinstance(name, str):
            return None
        try:
            return self._names[name]
        except KeyError:
            pass
        if name.startswith("hexcast_plugins."):
            owner = name.split(".")[1] or None
        elif name in ("hexcast", "hexcast_core") or name.startswith("hexcast_core."):
            owner = CORE
        elif name == "__main__":
            f = getattr(sys.modules.get("__main__"), "__file__", None)
            owner = CORE if f and _norm(f) == self.core_main else None
        else:
            owner = None
        self._names[name] = owner
        return owner

    def of_file(self, filename: Any) -> str | None:
        if not isinstance(filename, str):
            return None
        try:
            return self._files[filename]
        except KeyError:
            pass
        owner = None
        if not filename.startswith("<"):
            f = _norm(filename)
            if self.plugins and f.startswith(self.plugins):
                owner = f[len(self.plugins):].split(os.sep, 1)[0] or None
            elif f == self.core_main or f.startswith(self.core_pkg):
                owner = CORE
        self._files[filename] = owner
        return owner


# ---- the process as the system sees it ------------------------------------------------

def _linux_status() -> dict:
    out: dict = {}
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as f:
            fields = dict(line.split(":", 1) for line in f if ":" in line)
    except OSError:
        return out

    def kb(key: str) -> int | None:
        try:
            return int(fields[key].split()[0]) * 1024
        except (KeyError, ValueError, IndexError):
            return None

    out.update(rss=kb("VmRSS"), peak=kb("VmHWM"), swap=kb("VmSwap"), anon=kb("RssAnon"), file=kb("RssFile"))
    try:
        out["threads"] = int(fields["Threads"].split()[0])
    except (KeyError, ValueError, IndexError):
        pass
    try:
        fds = os.listdir("/proc/self/fd")
        out["files"] = len(fds)
        socks = 0
        for fd in fds:
            try:
                socks += os.readlink(f"/proc/self/fd/{fd}").startswith("socket:")
            except OSError:
                pass
        out["os_sockets"] = socks
    except OSError:
        pass
    return out


_mallinfo2: Any = None


def _glibc_heap() -> dict | None:
    """glibc's own view of the C heap (Linux): what is in use, and what was freed but is still kept. Free
    memory the allocator keeps growing while 'in use' stays flat is fragmentation, not a leak."""
    global _mallinfo2
    if not sys.platform.startswith("linux"):
        return None
    try:
        import ctypes
        if _mallinfo2 is None:
            class MallInfo2(ctypes.Structure):
                _fields_ = [(n, ctypes.c_size_t) for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks",
                                                           "fsmblks", "uordblks", "fordblks", "keepcost")]
            fn = getattr(ctypes.CDLL(None), "mallinfo2", None)        # glibc 2.33+; musl has none
            if fn is None:
                _mallinfo2 = False
                return None
            fn.restype = MallInfo2
            _mallinfo2 = fn
        if _mallinfo2 is False:
            return None
        m = _mallinfo2()
        return {"heap": m.arena + m.hblkhd, "in_use": m.uordblks + m.hblkhd, "free_kept": m.fordblks,
                "big_blocks": m.hblkhd}
    except Exception:
        _mallinfo2 = False
        return None


def _windows_status() -> dict:
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        proc = k32.GetCurrentProcess()
        c = Counters()
        c.cb = ctypes.sizeof(c)
        out: dict = {}
        if k32.K32GetProcessMemoryInfo(proc, ctypes.byref(c), c.cb):
            out.update(rss=c.WorkingSetSize, peak=c.PeakWorkingSetSize, private=c.PrivateUsage)
        handles = wintypes.DWORD()
        if k32.GetProcessHandleCount(proc, ctypes.byref(handles)):
            out["files"] = handles.value
        return out
    except Exception:
        return {}


def process_stats() -> dict:
    """Memory, swap, threads and open files/sockets of this process - whatever the platform can tell.
    `total` is the one number to watch: in RAM + in swap on Linux, private (committed) bytes on Windows."""
    out: dict = {"pid": os.getpid()}
    if sys.platform.startswith("linux"):
        out.update(_linux_status())
        heap = _glibc_heap()
        if heap:
            out["c_heap"] = heap
        if out.get("rss") is not None:
            out["total"] = out["rss"] + (out.get("swap") or 0)
    elif sys.platform == "win32":
        out.update(_windows_status())
        out["total"] = out.get("private", out.get("rss"))
    else:
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            out["peak"] = peak if sys.platform == "darwin" else peak * 1024
        except Exception:
            pass
        out["total"] = out.get("rss")
    out.setdefault("threads", threading.active_count())
    out["python_blocks"] = sys.getallocatedblocks()
    if tracemalloc.is_tracing():
        out["traced"] = tracemalloc.get_traced_memory()[0]
    return out


def versions() -> dict:
    out = {"python": platform.python_version(), "implementation": platform.python_implementation(),
           "os": f"{platform.system()} {platform.release()}".strip(), "pid": os.getpid()}
    for pkg in ("uvicorn", "websockets", "starlette", "fastapi", "httpx"):
        mod = sys.modules.get(pkg)
        if mod is not None and getattr(mod, "__version__", None):
            out[pkg] = str(mod.__version__)
    try:
        loop = asyncio.get_running_loop()
        out["loop"] = f"{type(loop).__module__}.{type(loop).__name__}"
    except RuntimeError:
        pass
    return out


# ---- tasks and sockets (read on the event loop) -------------------------------------------

def _task_owner(task: asyncio.Task, owners: _Owners) -> tuple[str | None, str]:
    """(owner, label): the outermost plugin code in the task's await chain, else the core's; a task running
    neither is labelled with the package it runs (uvicorn, httpx ...)."""
    coro: Any = task.get_coro()
    core_label = outer = None
    for _ in range(64):
        if coro is None:
            break
        code = getattr(coro, "cr_code", None) or getattr(coro, "gi_code", None) or getattr(coro, "ag_code", None)
        if code is not None:
            who = owners.of_file(code.co_filename)
            label = getattr(code, "co_qualname", code.co_name)
            if who is not None and who != CORE:
                return who, label
            if who == CORE and core_label is None:
                core_label = label
            if outer is None:
                frame = getattr(coro, "cr_frame", None) or getattr(coro, "gi_frame", None) or getattr(coro, "ag_frame", None)
                name = frame.f_globals.get("__name__", "") if frame is not None else ""
                outer = (name or os.path.basename(code.co_filename)).split(".")[0] or "?"
        coro = getattr(coro, "cr_await", None) or getattr(coro, "gi_yieldfrom", None) or getattr(coro, "ag_await", None)
    if core_label is not None:
        return CORE, core_label
    return None, outer or "?"


def _tasks(owners: _Owners) -> tuple[dict[str, list], dict[str, Counter], Counter]:
    """The running asyncio tasks, per module (see _task_owner), with what they run; and the count of the
    others by the package they run."""
    by_owner: dict[str, list] = {}
    labels: dict[str, Counter] = {}
    other: Counter = Counter()
    try:
        tasks = asyncio.all_tasks()
    except RuntimeError:
        return by_owner, labels, other
    for t in tasks:
        try:
            who, label = _task_owner(t, owners)
        except Exception:
            who, label = None, "?"
        if who is None:
            other[label] += 1
        else:
            by_owner.setdefault(who, []).append(t)
            labels.setdefault(who, Counter())[label] += 1
    return by_owner, labels, other


def _waiting(proto: Any) -> int | None:
    """Bytes queued in a connection's transport that the client has not taken yet."""
    fn = getattr(getattr(proto, "transport", None), "get_write_buffer_size", None)
    try:
        return int(fn()) if callable(fn) else None
    except Exception:
        return None


def _sockets(host: Any, tasks: dict[str, list]) -> tuple[dict[str, dict], dict]:
    """Open websockets per module, with the bytes each is waiting to send; plus every connection uvicorn
    has open (HTTP keep-alive too), when it can be reached. A websocket belongs to the module whose task
    serves it: uvicorn runs each connection as one task, started by a method of the connection itself."""
    protos: dict[int, Any] = {}
    for _path, send in list(getattr(host, "_sockets", ()) or ()):
        proto = getattr(send, "__self__", None)
        if proto is not None:
            protos[id(proto)] = proto
    serving: dict[int, str] = {}
    for who, ts in tasks.items():
        for t in ts:
            frame = getattr(t.get_coro(), "cr_frame", None)
            try:
                me = frame.f_locals.get("self") if frame is not None else None
            except Exception:
                me = None
            if me is not None and id(me) in protos:
                serving[id(me)] = who
    per: dict[str, dict] = {}
    server: Any = None
    impl = None
    for key, proto in protos.items():
        row = per.setdefault(serving.get(key, CORE), {"sockets": 0, "waiting": 0, "waiting_max": 0, "stuck": 0})
        row["sockets"] += 1
        n = _waiting(proto)
        if n:
            row["waiting"] += n
            row["waiting_max"] = max(row["waiting_max"], n)
            row["stuck"] += n >= 65536
        impl = impl or type(proto).__name__
        server = server or getattr(proto, "server_state", None)
    conns: dict = {"websocket_impl": impl}
    pool = getattr(server, "connections", None)
    if pool is not None:
        items = list(pool)
        conns.update(open=len(items), waiting=sum(_waiting(p) or 0 for p in items))
    return per, conns


# ---- what each module holds ---------------------------------------------------------------

def _roots(host: Any, owners: _Owners, tasks: dict[str, list]) -> tuple[list[tuple[str, list]], set[int], dict[int, set]]:
    """Per module, where its memory hangs from; the objects no module is walked through (every module's
    globals but its own, the app, the host); and which module(s) have each global value - another module
    holding one of them (a plugin keeping its friend's cache) stops there, so it stays its owner's. Plugins
    come first and the core last: the core reaches every plugin through the host, which must not make
    their memory the core's."""
    by_owner: dict[str, list] = {}
    stop: set[int] = set()
    claimed: dict[int, set] = {}
    for name, mod in list(sys.modules.items()):
        d = getattr(mod, "__dict__", None)
        if not isinstance(d, dict):
            continue
        stop.add(id(d))
        who = owners.of_name(name)
        if who is not None:
            by_owner.setdefault(who, []).append(d)
            for v in list(d.values()):
                claimed.setdefault(id(v), set()).add(who)
    for pid, rec in list(getattr(host, "loaded", {}).items()):
        extra = [getattr(rec, "routes", None), getattr(getattr(rec, "ctx", None), "_shutdown", None)]
        by_owner.setdefault(pid, []).extend(x for x in extra if x is not None)
    for who, ts in tasks.items():
        by_owner.setdefault(who, []).extend(ts)
    app = getattr(host, "app", None)
    router = getattr(app, "router", None)
    shared = [host, app, router, getattr(app, "state", None), getattr(host, "_sockets", None), sys.modules]
    for obj in (app, router):                       # the app's route list, middleware ...: a request's frames point at them
        try:
            shared.extend(v for v in vars(obj).values() if isinstance(v, (list, dict, set, tuple)))
        except TypeError:
            pass
    for obj in shared:
        if obj is not None:
            stop.add(id(obj))
    order = sorted((k for k in by_owner if k != CORE)) + ([CORE] if CORE in by_owner else [])
    return [(k, by_owner[k]) for k in order], stop, claimed


def _type_name(t: type) -> str:
    mod = getattr(t, "__module__", "") or ""
    name = getattr(t, "__qualname__", None) or getattr(t, "__name__", "?")
    if mod in ("builtins", ""):
        return name
    if mod.startswith("hexcast_plugins."):
        mod = mod.split(".", 2)[-1] if mod.count(".") >= 2 else mod
    return f"{mod}.{name}"


def _walk(order: list[tuple[str, list]], stop: set[int], owners: _Owners, claimed: dict[int, set] | None = None,
          seconds: float = WALK_SECONDS, limit: int = WALK_OBJECTS) -> dict:
    """Add up sys.getsizeof over everything each module reaches (see _roots). Never walks into another
    module's globals, classes or objects, nor into shared machinery (the event loop, threads, loggers,
    the server's connection objects) or code. Each object is counted once, under the first module."""
    getsizeof, referents, monotonic = sys.getsizeof, gc.get_referents, time.monotonic
    claimed = claimed or {}
    seen = set(stop)
    rooted: set[int] = set()
    kinds: dict[type, Any] = {}
    of_name = owners.of_name
    started = monotonic()
    deadline = started + seconds
    held: dict[str, list[int]] = {}
    visited = 0
    partial = False

    def kind(t: type) -> Any:
        k = kinds.get(t)
        if k is None:
            mod = getattr(t, "__module__", None) or ""
            if issubclass(t, _SHARED_TYPES) or (isinstance(mod, str) and mod.split(".")[0] in _SHARED_PACKAGES):
                k = _SHARED
            else:
                k = of_name(mod) or _DATA
            kinds[t] = k
        return k

    for key, roots in order:
        size = count = 0
        stack = []
        by_type: dict[type, list[int]] = {}
        for r in roots:
            if id(r) not in rooted:
                rooted.add(id(r))
                seen.add(id(r))
                stack.append(r)
        while stack:
            o = stack.pop()
            try:
                s = getsizeof(o)
            except Exception:
                s = 0
            size += s
            count += 1
            bt = by_type.get(type(o))
            if bt is None:
                by_type[type(o)] = [s, 1]
            else:
                bt[0] += s
                bt[1] += 1
            if not count & 0x1FFF and (monotonic() > deadline or visited + count > limit):
                partial = True
                break
            try:
                refs = referents(o)
            except Exception:
                continue
            for r in refs:
                i = id(r)
                if i in seen:
                    continue
                c = claimed.get(i)
                if c is not None and key not in c:            # another module's global
                    continue
                t = type(r)
                if isinstance(r, type):                       # a class: only its own module walks into it
                    if of_name(getattr(r, "__module__", None)) != key:
                        continue
                elif t is types.FunctionType:                 # a function: likewise (its globals are a stop anyway)
                    who = of_name(getattr(r, "__module__", None))
                    if who is not None and who != key:
                        continue
                else:
                    k = kind(t)
                    if k is _SHARED or (k is not _DATA and k != key):
                        continue
                seen.add(i)
                stack.append(r)
        visited += count
        top = sorted(by_type.items(), key=lambda kv: -kv[1][0])[:6]
        held[key] = [size, count, [[_type_name(t), b, n] for t, (b, n) in top]]
        if partial:
            break
    return {"held": held, "objects": visited, "took": round(monotonic() - started, 3), "partial": partial}


# ---- what each module's code allocated (tracemalloc) ------------------------------------------

def _short(filename: str, owners: _Owners) -> str:
    if filename.startswith("<"):
        return filename
    f = _norm(filename)
    if owners.plugins and f.startswith(owners.plugins):
        return f[len(owners.plugins):].replace(os.sep, "/")
    if f.startswith(owners.core_pkg):
        return "hexcast_core/" + f[len(owners.core_pkg):].replace(os.sep, "/")
    if f == owners.core_main:
        return "hexcast.py"
    parts = f.replace(os.sep, "/").split("/")
    for marker in ("site-packages", "dist-packages", "lib"):
        if marker in parts:
            i = len(parts) - 1 - parts[::-1].index(marker)
            if i + 1 < len(parts):
                return "/".join(parts[i + 1:])
    return "/".join(parts[-2:])


def _raw_traces(snap: Any) -> list:
    """(domain, size, frames) per live allocation, frames as ((filename, lineno), ...) most recent first.
    Read straight from the snapshot when its layout is the known one (one pass over a few hundred thousand
    tuples); Snapshot.filter_traces / statistics take a minute on a process this size."""
    raw = getattr(getattr(snap, "traces", None), "_traces", None)
    if isinstance(raw, list) and (not raw or (isinstance(raw[0], tuple) and len(raw[0]) >= 3 and isinstance(raw[0][2], tuple))):
        return raw
    return [(t.domain, t.size, tuple((f.filename, f.lineno) for f in reversed(t.traceback))) for t in snap.traces]


def _allocations(owners: _Owners) -> dict:
    """Live memory by the code that allocated it: the most recent plugin frame of each allocation, else the
    core's, else 'everything else'; and the source lines holding the most. Imports and tracemalloc's own
    bookkeeping are left out."""
    if not tracemalloc.is_tracing():
        return {"tracing": False}
    started = time.monotonic()
    raw = _raw_traces(tracemalloc.take_snapshot())
    own_file = tracemalloc.__file__
    by: Counter = Counter()
    lines: dict[tuple, list[int]] = {}
    owner_of: dict[int, str] = {}                  # identical tracebacks share one tuple: one look each
    for trace in raw:
        size, frames = trace[1], trace[2]
        if not frames:
            continue
        top = frames[0]
        if top[0] == own_file or top[0].startswith(("<frozen importlib", "<unknown>")):
            continue
        who = owner_of.get(id(frames))
        if who is None:
            who = ""
            for filename, _line in frames:
                o = owners.of_file(filename)
                if o is not None and o != CORE:
                    who = o
                    break
                if o == CORE and not who:
                    who = CORE
            owner_of[id(frames)] = who
        by[who] += size
        e = lines.get(top)
        if e is None:
            lines[top] = [size, 1]
        else:
            e[0] += size
            e[1] += 1
    del raw, owner_of
    top_lines = sorted(lines.items(), key=lambda kv: -kv[1][0])[:15]
    top = [{"file": _short(f, owners), "line": ln, "size": s, "count": n, "owner": owners.of_file(f)}
           for (f, ln), (s, n) in top_lines]
    cur, peak = tracemalloc.get_traced_memory()
    return {"tracing": True, "frames": tracemalloc.get_traceback_limit(), "traced": cur, "peak": peak,
            "overhead": tracemalloc.get_tracemalloc_memory(), "by_module": dict(by), "top": top,
            "took": round(time.monotonic() - started, 3), "at": time.time()}


# ---- the monitor ----------------------------------------------------------------------------

def _fmt(n: float | None) -> str:
    if n is None:
        return "?"
    sign = "-" if n < 0 else ""
    n = abs(n)
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= size:
            return f"{sign}{n / size:.2f} {unit}" if unit == "GB" else f"{sign}{n / size:.0f} {unit}"
    return f"{sign}{n:.0f} B"


def _slope(points: list[tuple[float, float]]) -> float | None:
    """Least-squares growth per hour."""
    if len(points) < 3 or points[-1][0] - points[0][0] < 600:
        return None
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    if not den:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in points) / den * 3600


def _thin(rows: list, most: int) -> list:
    if len(rows) <= most:
        return rows
    step = len(rows) / most
    out = [rows[int(i * step)] for i in range(most)]
    if out[-1] is not rows[-1]:
        out[-1] = rows[-1]
    return out


class Monitor:
    """Reads the numbers for the page and keeps a short history (see the module docstring)."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self.samples: deque = deque(maxlen=int(KEEP_SECONDS // SAMPLE_SECONDS) + 1)     # (t, total, rss, swap, tasks, sockets, waiting)
        self.module_samples: deque = deque(maxlen=int(KEEP_SECONDS // MODULES_SECONDS) + 1)   # (t, ((key, bytes), ...))
        self.last_modules: dict | None = None
        self.last_alloc: dict | None = None
        self._lock: asyncio.Lock | None = None
        self._alloc_lock: asyncio.Lock | None = None
        self._task: asyncio.Task | None = None
        self.started = _STARTED

    def _owners(self) -> _Owners:
        return _Owners(getattr(self.host, "plugins_dir", None))

    # -- counting --

    async def modules(self, max_age: float = FRESH_SECONDS) -> dict:
        """Each module's share, counted again when the last count is older than max_age seconds. Counting
        runs in a worker thread, so the event loop (audio, overlays) keeps going meanwhile."""
        if self.last_modules and time.time() - self.last_modules["at"] < max_age:
            return self.last_modules
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.last_modules and time.time() - self.last_modules["at"] < max_age:
                return self.last_modules
            owners = self._owners()
            tasks, _labels, _other = _tasks(owners)
            order, stop, claimed = _roots(self.host, owners, tasks)
            del tasks
            gil = getattr(sys, "_is_gil_enabled", lambda: True)()
            if gil:
                result = await asyncio.to_thread(_walk, order, stop, owners, claimed)
            else:                                             # free-threaded Python: never walk objects other threads change
                result = _walk(order, stop, owners, claimed, seconds=5.0)
            result["at"] = time.time()
            self.last_modules = result
            return result

    async def allocations(self, max_age: float = ALLOC_FRESH_SECONDS) -> dict:
        if not tracemalloc.is_tracing():
            return {"tracing": False}
        if self.last_alloc and time.time() - self.last_alloc["at"] < max_age:
            return self.last_alloc
        if self._alloc_lock is None:
            self._alloc_lock = asyncio.Lock()
        async with self._alloc_lock:
            if not (self.last_alloc and time.time() - self.last_alloc["at"] < max_age):
                self.last_alloc = await asyncio.to_thread(_allocations, self._owners())
            return self.last_alloc

    # -- the history --

    def sample(self) -> dict:
        proc = process_stats()
        tasks, _labels, other = _tasks(self._owners())
        n_tasks = sum(len(v) for v in tasks.values()) + sum(other.values())
        per, _conns = _sockets(self.host, tasks)
        del tasks
        socks = sum(r["sockets"] for r in per.values())
        waiting = sum(r["waiting"] for r in per.values())
        self.samples.append((round(time.time()), proc.get("total"), proc.get("rss"), proc.get("swap"), n_tasks, socks, waiting))
        return proc

    def note_modules(self, result: dict) -> None:
        self.module_samples.append((round(result["at"]), tuple((k, v[0]) for k, v in result["held"].items())))

    def growth(self, hours: float = 6.0) -> dict:
        now = time.time()
        pts = [(s[0], s[1]) for s in self.samples if s[1] is not None and s[0] >= now - hours * 3600]
        allpts = [(s[0], s[1]) for s in self.samples if s[1] is not None]
        return {"per_hour": _slope(pts), "window_hours": round((pts[-1][0] - pts[0][0]) / 3600, 2) if len(pts) > 1 else 0,
                "since_start": (allpts[-1][1] - allpts[0][1]) if len(allpts) > 1 else None,
                "first_at": allpts[0][0] if allpts else None}

    def log_line(self, proc: dict | None = None) -> str:
        proc = proc or process_stats()
        g = self.growth()
        parts = [f"{_fmt(proc.get('total'))} in use"]
        if proc.get("swap") is not None:
            parts[0] += f" (rss {_fmt(proc.get('rss'))} + swap {_fmt(proc.get('swap'))})"
        if g["per_hour"] is not None:
            parts.append(f"{'+' if g['per_hour'] >= 0 else ''}{_fmt(g['per_hour'])}/h over {g['window_hours']:.1f} h")
        last = self.samples[-1] if self.samples else None
        if last is not None:
            parts.append(f"{last[4]} tasks")
            parts.append(f"{last[5]} websockets, {_fmt(last[6])} waiting")
        line = "; ".join(parts)
        mods = self.last_modules
        if mods:
            top = sorted(mods["held"].items(), key=lambda kv: -kv[1][0])[:5]
            line += " | " + ", ".join(f"{k} {_fmt(v[0])}" for k, v in top)
        return f"{time.strftime('%Y-%m-%d %H:%M:%S')} [memory] {line}"

    async def _run(self) -> None:
        await asyncio.sleep(FIRST_SAMPLE_SECONDS)
        next_modules = next_log = 0.0
        while True:
            try:
                proc = self.sample()
                now = time.monotonic()
                if now >= next_modules:
                    self.note_modules(await self.modules(max_age=60))
                    next_modules = now + MODULES_SECONDS
                if now >= next_log:
                    print(self.log_line(proc), flush=True)
                    next_log = now + LOG_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("memory: sampling failed")
            await asyncio.sleep(SAMPLE_SECONDS)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run(), name="hexcast-memory-sampler")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=2)

    # -- the page's data --

    def _names(self) -> dict[str, dict]:
        out: dict[str, dict] = {CORE: {"name": "Hexcast core", "kind": "core", "version": None, "running": True}}
        for pid, rec in list(getattr(self.host, "loaded", {}).items()):
            m = getattr(getattr(rec, "ctx", None), "manifest", None)
            out[pid] = {"name": getattr(m, "name", None) or pid, "kind": "plugin", "version": getattr(m, "version", None),
                        "running": True}
        return out

    def _change(self, key: str, now_bytes: int) -> dict:
        hist = [(t, dict(rows).get(key)) for t, rows in self.module_samples]
        hist = [(t, b) for t, b in hist if b is not None]
        out: dict = {"since": None, "since_at": None, "hour": None}
        if hist:
            out["since"], out["since_at"] = now_bytes - hist[0][1], hist[0][0]
            ago = [(t, b) for t, b in hist if t <= time.time() - 3000]
            if ago:
                out["hour"] = now_bytes - ago[-1][1]
        out["history"] = [[t, b] for t, b in _thin(hist, 120)]
        return out

    async def view(self) -> dict:
        proc = process_stats()
        owners = self._owners()
        tasks, labels, other_tasks = _tasks(owners)
        n_tasks = sum(len(v) for v in tasks.values()) + sum(other_tasks.values())
        per_sock, conns = _sockets(self.host, tasks)
        del tasks
        mods = await self.modules()
        alloc = await self.allocations()
        names = self._names()
        keys = set(names) | set(mods["held"]) | set(labels) | set(per_sock)
        rows = []
        for key in keys:
            info = names.get(key) or {"name": key, "kind": "plugin", "version": None, "running": False}
            size, count, kinds = (mods["held"].get(key) or [None, None, []])[:3]
            s = per_sock.get(key, {})
            row = {"key": key, **info, "held": size, "objects": count, "types": kinds,
                   "tasks": sum(labels.get(key, Counter()).values()),
                   "task_top": labels.get(key, Counter()).most_common(4),
                   "sockets": s.get("sockets", 0), "waiting": s.get("waiting", 0),
                   "waiting_max": s.get("waiting_max", 0), "stuck": s.get("stuck", 0)}
            if size is not None:
                row.update(change=self._change(key, size))
            if alloc.get("tracing"):
                row["allocated"] = alloc["by_module"].get(key, 0)
            rows.append(row)
        rows.sort(key=lambda r: (-(r["held"] or 0), r["name"].lower()))
        held_sum = sum(r["held"] or 0 for r in rows)
        total = proc.get("total")
        rest = {"held": max(0, total - held_sum) if total is not None else None,
                "tasks": sum(other_tasks.values()), "task_top": other_tasks.most_common(5)}
        if alloc.get("tracing"):
            rest["allocated"] = alloc["by_module"].get("", 0)
        chart = [list(s) for s in _thin(list(self.samples), CHART_POINTS)]
        return {"ok": True, "now": time.time(), "started": self.started, "process": proc, "growth": self.growth(),
                "tasks": n_tasks, "connections": conns, "versions": versions(),
                "modules": {"at": mods["at"], "took": mods["took"], "objects": mods["objects"], "partial": mods["partial"],
                            "rows": rows, "rest": rest},
                "allocations": alloc, "history": chart,
                "every": {"sample": SAMPLE_SECONDS, "modules": MODULES_SECONDS, "fresh": FRESH_SECONDS, "keep": KEEP_SECONDS}}


def build_router(monitor: Monitor, static_dir: Path) -> APIRouter:
    """GET /memory (the page) and GET /api/memory (its numbers). Read-only: nothing here changes anything."""
    router = APIRouter()
    nocache = {"Cache-Control": "no-store"}

    @router.get("/memory", response_class=HTMLResponse)
    async def memory_page():
        return HTMLResponse((Path(static_dir) / "memory.html").read_text(encoding="utf-8"), headers=nocache)

    @router.get("/api/memory")
    async def api_memory():
        return await monitor.view()

    return router
