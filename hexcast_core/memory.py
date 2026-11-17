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
- Inside "everything else": who holds the whole process's memory (Python's allocator, the C allocator, the
  files mapped from disk) and every Python object by type, with what holds the biggest and fastest-growing
  types - for memory no module reaches (a library's cache, objects only a C extension or a stray frame holds).

A sampler notes the process totals and that split every 5 minutes, each module's share and every object by
type every 30 minutes, for 3 days, so the page shows growth even when nobody had it open. Once an hour it
prints one dated line to the console, so a log kept across restarts shows where an old process's memory went.
"""

from __future__ import annotations

import asyncio
import bisect
import gc
import logging
import os
import platform
import re
import sys
import threading
import time
import tracemalloc
import types
from collections import Counter, deque
from itertools import islice
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
WALK_SECONDS = 30.0                 # a count stops (and says it is partial) after this much work ...
WALK_OBJECTS = 5_000_000            # ... or this many objects
CENSUS_EVERY_SECONDS = 1800         # every Python object counted by type ...
CENSUS_FRESH_SECONDS = 120          # ... and again when the page asks, at most this often
CENSUS_SECONDS = 20.0               # a census stops (and says it is partial) after this much work
PACE_BURST = 0.002                  # a count in a worker thread works this long at a time ...
PACE_REST = 0.006                   # ... then sleeps this long, so the show keeps up (see _Pace)
CENSUS_SHOW = 25                    # the biggest types on the page (plus the fastest growing)
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


class _OutOfTime(Exception):
    """A paced count used up its time (see _Pace.check)."""


class _Pace:
    """Keeps a count in a worker thread from starving the event loop, and stops it when its work time is up.

    A worker thread that never waits takes the GIL back every time the event loop lets go of it (each wait
    for the network or a timer) and keeps it for up to sys.getswitchinterval(), 5 ms. A show wakes the loop
    hundreds of times a second (audio blocks every 16 ms to each overlay ...), so the loop falls further
    behind for as long as the count runs: with 7.5 M objects, a 10 s census left such a loop 8 s behind,
    on Python 3.12 and 3.14 alike. So a count works for `burst` seconds at a time and then sleeps for `rest`
    (a sleeping thread does not want the GIL), and its time limit counts only the work. With rest=0 (a
    count in the event loop's own thread) it never sleeps."""

    def __init__(self, seconds: float, rest: float = 0.0, burst: float = PACE_BURST) -> None:
        self.seconds, self.rest, self.burst = seconds, rest, burst
        self.done = 0.0                                  # the work in the bursts before this one
        self.since = time.perf_counter()                 # when this burst began (monotonic ticks every 15.6 ms on Windows)

    def worked(self) -> float:
        return self.done + time.perf_counter() - self.since

    def check(self) -> None:
        """Call this every few thousand small steps. Raises _OutOfTime once the work time is used up."""
        now = time.perf_counter()
        spent = now - self.since
        if self.done + spent >= self.seconds:
            raise _OutOfTime
        if spent >= self.burst:
            self.done += spent
            if self.rest > 0:
                time.sleep(self.rest)                    # (waiting for the GIL back after it is not work either)
                now = time.perf_counter()
            self.since = now


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

    out.update(rss=kb("VmRSS"), peak=kb("VmHWM"), swap=kb("VmSwap"), anon=kb("RssAnon"), file=kb("RssFile"),
               shmem=kb("RssShmem"))
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
        heap, _bases = _windows_heaps()
        if heap:
            out["c_heap"] = heap
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
    if not isinstance(name, str):                         # a metaclass's own descriptor, not a name
        name = type.__dict__["__qualname__"].__get__(t) if isinstance(t, type) else "?"
    if not isinstance(mod, str) or mod in ("builtins", ""):
        return name
    if mod.startswith("hexcast_plugins."):
        mod = mod.split(".", 2)[-1] if mod.count(".") >= 2 else mod
    return f"{mod}.{name}"


def _walk(order: list[tuple[str, list]], stop: set[int], owners: _Owners, claimed: dict[int, set] | None = None,
          seconds: float = WALK_SECONDS, limit: int = WALK_OBJECTS, rest: float = 0.0) -> dict:
    """Add up sys.getsizeof over everything each module reaches (see _roots). Never walks into another
    module's globals, classes or objects, nor into shared machinery (the event loop, threads, loggers,
    the server's connection objects) or code. Each object is counted once, under the first module.
    `seconds` of work at most, resting `rest` between bursts (see _Pace)."""
    getsizeof, referents, clock = sys.getsizeof, gc.get_referents, time.perf_counter
    claimed = claimed or {}
    seen = set(stop)
    rooted: set[int] = set()
    kinds: dict[type, Any] = {}
    of_name = owners.of_name
    started = clock()
    pace = _Pace(seconds, rest)
    check = pace.check
    steps = 0
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
        try:
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
                steps += 1
                if not steps & 0x3FF:
                    if visited + count > limit:
                        raise _OutOfTime
                    check()
                try:
                    refs = referents(o)
                except Exception:
                    continue
                for r in refs:
                    steps += 1                                # a big container is paced too, not one long burst
                    if not steps & 0x3FF:
                        check()
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
        except _OutOfTime:
            partial = True
        visited += count
        top = sorted(by_type.items(), key=lambda kv: -kv[1][0])[:6]
        held[key] = [size, count, [[_type_name(t), b, n] for t, (b, n) in top]]
        if partial:
            break
    return {"held": held, "objects": visited, "took": round(clock() - started, 3),
            "worked": round(pace.worked(), 3), "partial": partial}


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


def _allocations(owners: _Owners, rest: float = 0.0) -> dict:
    """Live memory by the code that allocated it: the most recent plugin frame of each allocation, else the
    core's, else 'everything else'; and the source lines holding the most. Imports and tracemalloc's own
    bookkeeping are left out. Rests `rest` between bursts (see _Pace), never stops early."""
    if not tracemalloc.is_tracing():
        return {"tracing": False}
    started = time.monotonic()
    raw = _raw_traces(tracemalloc.take_snapshot())
    own_file = tracemalloc.__file__
    by: Counter = Counter()
    lines: dict[tuple, list[int]] = {}
    owner_of: dict[int, str] = {}                  # identical tracebacks share one tuple: one look each
    check = _Pace(float("inf"), rest).check
    steps = 0
    for trace in raw:
        steps += 1
        if not steps & 0x3FF:
            check()
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


# ---- inside "everything else": where the memory sits ---------------------------------------------

_STAT = re.compile(r"^\s*(\S.*?)\s*=\s*([\d,]+)\s*$")
_STAT_OTHER = re.compile(r"^\s*$|^[-\s]+$|^Small block threshold|^class\s+size|^\s*\d+(\s+\d+){4}\s*$|^arena map counts\s*$")
_stats_lock = threading.Lock()


def _pymalloc_text() -> str | None:
    """What sys._debugmallocstats prints. It writes only to the C stderr, so for the few milliseconds it takes
    stderr is pointed at a temporary file; a line something else wrote meanwhile is passed on afterwards."""
    fn = getattr(sys, "_debugmallocstats", None)
    if fn is None:
        return None
    import tempfile
    with _stats_lock:
        try:
            if sys.stderr is not None:
                sys.stderr.flush()
        except Exception:
            pass
        try:
            tmp = tempfile.TemporaryFile()
        except OSError:
            return None
        with tmp:
            try:
                saved: int | None = os.dup(2)
            except OSError:
                saved = None                              # no stderr at all (pythonw, a service)
            try:
                os.dup2(tmp.fileno(), 2)
            except OSError:
                if saved is not None:
                    os.close(saved)
                return None
            try:
                fn()
            finally:
                try:
                    if saved is not None:
                        os.dup2(saved, 2)
                        os.close(saved)
                    else:
                        os.close(2)
                except OSError:
                    pass
            tmp.seek(0)
            text = tmp.read().decode("utf-8", "replace")
        foreign = [ln for ln in text.splitlines() if not (_STAT.match(ln) or _STAT_OTHER.match(ln))]
        if foreign and len(foreign) <= 3 and saved is not None:      # more is a format this code does not know
            try:
                os.write(2, ("\n".join(foreign) + "\n").encode("utf-8", "replace"))
            except OSError:
                pass
        return text


def _parse_pymalloc(text: str) -> dict | None:
    """The numbers of Python's own allocator for objects of 512 bytes or less (the first section of
    sys._debugmallocstats: up to its first 'Total')."""
    vals: dict[str, int] = {}
    arena_size = None
    for line in text.splitlines():
        m = _STAT.match(line)
        if not m:
            continue
        key, val = m.group(1), int(m.group(2).replace(",", ""))
        vals.setdefault(key, val)
        a = re.match(r"(\d+) arenas \* (\d+) bytes/arena", key)
        if a:
            arena_size = int(a.group(2))
        if key == "Total":
            break
    used, total = vals.get("# bytes in allocated blocks"), vals.get("Total")
    if used is None or total is None:
        return None
    return {"in_use": used, "reserved": total, "free": max(0, total - used), "arenas": vals.get("# arenas allocated current"),
            "highwater": vals.get("# arenas highwater mark"), "arena_size": arena_size}


def python_allocator() -> dict | None:
    """Python's small-object allocator: what its live objects use, and the room it keeps (pools freed by
    objects that are gone, held for new ones). None when it cannot be read (another allocator, another Python)."""
    try:
        text = _pymalloc_text()
        return _parse_pymalloc(text) if text else None
    except Exception:
        return None


WHERE = ("python", "python_free", "c", "c_free", "tracing", "files", "other")


def _where(proc: dict, py: dict | None) -> dict | None:
    """The process total split by who holds it: Python's allocator (in use / spare room), the C allocator
    (in use / freed but kept, Linux), tracemalloc's own records, the program and libraries mapped from disk
    (Linux), and the rest. A part this system cannot tell is None. The modules' share is inside the Python
    and C parts."""
    total = proc.get("total")
    if total is None:
        return None
    g: dict[str, int | None] = dict.fromkeys(WHERE)
    if py:
        g["python"], g["python_free"] = py["in_use"], py["free"]
    over = tracemalloc.get_tracemalloc_memory() if tracemalloc.is_tracing() else 0
    heap = proc.get("c_heap")
    if heap:
        g["c"], g["c_free"] = max(0, heap["in_use"] - over), heap["free_kept"]
    if over:
        g["tracing"] = over
    if proc.get("file") is not None:
        g["files"] = proc["file"] + (proc.get("shmem") or 0)
    g["other"] = max(0, total - sum(v for k, v in g.items() if v and k != "other"))
    return g


def _parse_smaps(lines: Any) -> tuple[dict[bytes, list[int]], list[list]]:
    """From /proc/<pid>/smaps lines (bytes): in RAM and in swap per mapped file, and every other mapping as
    [start, end, perms, name, in RAM, in swap]."""
    files: dict[bytes, list[int]] = {}
    anon: list[list] = []
    cur: list | None = None
    for line in lines:
        c = line[:1]
        if c.isdigit() or c.islower():                   # a mapping starts: range, perms, offset, dev, inode[, path]
            parts = line.split(None, 5)
            path = parts[5].strip() if len(parts) > 5 else b""
            if path.startswith(b"/"):
                cur = files.setdefault(path, [0, 0])
            else:
                lo, _, hi = parts[0].partition(b"-")
                cur = [int(lo, 16), int(hi, 16), parts[1], path, 0, 0]
                anon.append(cur)
        elif cur is not None:
            if line.startswith(b"Rss:"):
                cur[-2] += int(line.split()[1]) * 1024
            elif line.startswith(b"Swap:"):
                cur[-1] += int(line.split()[1]) * 1024
    return files, anon


_THREAD_ARENA = 64 << 20                                  # glibc's per-thread heap: 64 MB, aligned to it


def _linux_blocks(anon: list[list]) -> list[list]:
    """[kind, name, size, in RAM + swap] per memory mapping that is not a file. glibc's thread heaps are told
    by their 64 MB alignment and the reserved rest after them, thread stacks by the guard page before them."""
    out = []
    for i, (lo, hi, perms, name, rss, swap) in enumerate(anon):
        used = rss + swap
        if not used:
            continue
        size = hi - lo
        nm = name.decode("utf-8", "replace")
        prv = anon[i - 1] if i else None
        nxt = anon[i + 1] if i + 1 < len(anon) else None
        if nm == "[heap]":
            kind = "heap"
        elif nm.startswith("[stack"):
            kind = "stack"
        elif nm.startswith("[anon:"):
            kind, nm = "named", nm[6:-1].strip()
        elif nm:
            kind = "named"
        elif lo % _THREAD_ARENA == 0 and (size == _THREAD_ARENA or (
                nxt is not None and nxt[0] == hi and nxt[2].startswith(b"---") and size + nxt[1] - nxt[0] == _THREAD_ARENA)):
            kind = "thread_heap"
        elif (prv is not None and prv[1] == lo and prv[2].startswith(b"---") and prv[1] - prv[0] <= 1 << 16
              and perms.startswith(b"rw")):
            kind = "thread_stack"
        else:
            kind = "block"
        out.append([kind, nm, size, used])
    return out


_K32: Any = None


def _k32() -> Any:
    """kernel32 with the functions used here typed, once."""
    global _K32
    if _K32 is None:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetProcessHeaps.restype = wintypes.DWORD
        k32.GetProcessHeaps.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        k32.HeapSummary.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
        k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        k32.VirtualQuery.restype = ctypes.c_size_t
        _K32 = k32
    return _K32


def _windows_heaps() -> tuple[dict | None, set[int]]:
    """Windows: every heap of the process (the C runtime's malloc uses the default one) - bytes handed out and
    bytes committed; the difference is freed memory a heap keeps. Also the heaps' base addresses."""
    try:
        import ctypes
        from ctypes import wintypes

        class Summary(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("cbAllocated", ctypes.c_size_t), ("cbCommitted", ctypes.c_size_t),
                        ("cbReserved", ctypes.c_size_t), ("cbMaxReserve", ctypes.c_size_t)]

        k32 = _k32()
        n = k32.GetProcessHeaps(0, None) + 8
        handles = (wintypes.HANDLE * n)()
        got = k32.GetProcessHeaps(n, handles)
        if not got or got > n:
            return None, set()
        used = committed = 0
        bases: set[int] = set()
        for h in handles[:got]:
            summary = Summary()
            summary.cb = ctypes.sizeof(summary)
            if h and k32.HeapSummary(h, 0, ctypes.byref(summary)):
                used += summary.cbAllocated
                committed += summary.cbCommitted
                bases.add(h)
        return {"heap": committed, "in_use": used, "free_kept": max(0, committed - used), "big_blocks": None}, bases
    except Exception:
        return None, set()


def _windows_blocks(arena_size: int | None, heaps: set[int]) -> list[list]:
    """Windows: committed private memory in the pieces it was reserved in (one VirtualAlloc each), as
    [kind, '', size, committed]: Python's arenas, the heaps' first pieces, and blocks libraries reserved."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD), ("RegionSize", ctypes.c_size_t),
                    ("State", wintypes.DWORD), ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]

    k32 = _k32()
    info = Info()
    size_of = ctypes.sizeof(info)
    per: dict[int, int] = {}
    addr = 0
    for _ in range(200_000):
        if not k32.VirtualQuery(ctypes.c_void_p(addr), ctypes.byref(info), size_of):
            break
        if info.State == 0x1000 and info.Type == 0x20000:      # MEM_COMMIT, MEM_PRIVATE
            base = info.AllocationBase or 0
            per[base] = per.get(base, 0) + info.RegionSize
        nxt = (info.BaseAddress or 0) + info.RegionSize
        if nxt <= addr:
            break
        addr = nxt
    out = []
    for base, size in per.items():
        kind = "heap" if base in heaps else "python" if arena_size and size == arena_size else "block"
        out.append([kind, "", size, size])
    return out


_BLOCK_KINDS = ("python", "heap", "thread_heap", "stack", "thread_stack", "named", "block")


def _group_blocks(blocks: list[list], limit: int = 10) -> list[dict]:
    """Same kind (and, for plain blocks, the same size) together, the most memory first."""
    groups: dict[tuple, list[int]] = {}
    for kind, name, size, used in blocks:
        key = (kind, name, size if kind == "block" else 0)
        g = groups.get(key)
        if g is None:
            groups[key] = [1, used, size]
        else:
            g[0] += 1
            g[1] += used
            g[2] += size
    rows = sorted(groups.items(), key=lambda kv: -kv[1][1])
    return [{"kind": k, "name": n or None, "size": sz or None, "count": c, "used": u, "reserved": r}
            for (k, n, sz), (c, u, r) in rows if u >= 1 << 20][:limit]


def _mapped(owners: _Owners, arena_size: int | None) -> dict | None:
    """The files mapped into the process taking the most RAM (Linux: the Python program, the libraries' native
    code, fonts - shared with other programs, and the system can drop them when memory is short), and the
    biggest blocks of the rest of its memory."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/smaps", "rb") as f:
                files, anon = _parse_smaps(f)
            rows = sorted(files.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
            return {"files": {"count": len(files), "top": [{"file": _short(os.fsdecode(p), owners), "rss": r, "swap": s}
                                                            for p, (r, s) in rows[:8] if r + s]},
                    "blocks": _group_blocks(_linux_blocks(anon)), "measure": "in RAM + swap"}
        if sys.platform == "win32":
            _heap, bases = _windows_heaps()
            return {"blocks": _group_blocks(_windows_blocks(arena_size, bases)), "measure": "committed"}
    except Exception:
        log.debug("memory: reading the memory map failed", exc_info=True)
    return None


def _threads(native: int | None) -> dict:
    """Python's threads by name (numbers folded, so 8 workers are one line), and how many the system counts."""
    names: Counter = Counter()
    py = threading.enumerate()
    for t in py:
        names[re.sub(r"\d+", "N", t.name or "?")] += 1
    out: dict = {"python": len(py), "names": names.most_common(8)}
    if native is not None:
        out["native"] = native
    return out


def _gc_state() -> dict:
    try:
        stats = gc.get_stats()
    except Exception:
        stats = []
    return {"enabled": gc.isenabled(), "count": list(gc.get_count()), "threshold": list(gc.get_threshold()),
            "collections": [s.get("collections", 0) for s in stats],
            "collected": sum(s.get("collected", 0) for s in stats),
            "uncollectable": sum(s.get("uncollectable", 0) for s in stats), "garbage": len(gc.garbage),
            "frozen": gc.get_freeze_count() if hasattr(gc, "get_freeze_count") else 0}


# ---- inside "everything else": every Python object, by type -----------------------------------------

_IMMORTAL = 1 << 29                                      # refcounts this high are objects Python keeps for good
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ())) | {"builtins", "__main__"}


def _ref_base() -> int:
    """The references sys.getrefcount sees besides the real ones when _census reads them: the list of
    referents and map's own. Read through map (C) on purpose - a count read in Python code depends on how
    that Python version compiled the loop (3.14 lends some locals without a reference)."""
    holder = [bytearray(1)]
    return list(map(sys.getrefcount, gc.get_referents(holder)))[0] - 1


def _defined_by(name: Any, owners: _Owners, cache: dict) -> str:
    """The module (a plugin id, the core) or top-level package a module name belongs to; 'python' for Python
    itself and its standard library."""
    got = cache.get(name)
    if got is None:
        if not isinstance(name, str) or not name:
            got = "python"
        else:
            got = owners.of_name(name) or name.split(".")[0]
            if got in _STDLIB or got.startswith("_") and got[1:] in _STDLIB:
                got = "python"
        cache[name] = got
    return got


def _resolve_type(name: str) -> type | None:
    """The class behind a _type_name, if it is still loaded."""
    import builtins
    if "." not in name:
        t = getattr(builtins, name, None)
        return t if isinstance(t, type) else None
    mod, _, qual = name.rpartition(".")
    obj: Any = sys.modules.get(mod) or sys.modules.get(f"hexcast_plugins.{mod}")
    while obj is None and "." in mod:                    # a nested class: module.Outer.Inner
        mod, _, outer = mod.rpartition(".")
        qual = f"{outer}.{qual}"
        obj = sys.modules.get(mod)
    for part in qual.split("."):
        obj = getattr(obj, part, None)
    return obj if isinstance(obj, type) else None


def _global_names(wanted: set[int], seconds: float = 3.0, budget: int = 1_500_000, rest: float = 0.0) -> dict[int, str]:
    """Where a few objects hang in the module globals, up to three steps down: 'module.NAME',
    'module.NAME.attr', 'module.NAME[key]'. `seconds` of work at most, resting `rest` between bursts
    (see _Pace)."""
    found: dict[int, str] = {}
    check = _Pace(seconds, rest).check
    visits = 0
    skip = (types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType, str, bytes, int, float)
    seqs = (list, tuple, deque, set, frozenset)

    def label(path: tuple) -> str:
        out = path[0]
        for kind, key in path[1:]:
            out += f".{key}" if kind == "." else f"[{key!r}]" if isinstance(key, (str, int)) else f"[{type(key).__name__}]"
        return out

    def look(v: Any, path: tuple, depth: int) -> None:
        nonlocal visits
        visits += 1
        if not visits & 0x3FF:
            check()
        i = id(v)
        if i in wanted and i not in found:
            found[i] = label(path)
        tv = type(v)
        if depth == 0 or issubclass(tv, skip) or visits > budget:
            return
        try:                                               # plain storage only: no library's own attribute code runs
            if issubclass(tv, dict):
                items: list = list(islice(dict.items(v), 2000))
                kind = "["
            elif tv in seqs:
                items = list(islice(enumerate(v), 2000))
                kind = "["
            else:
                d = object.__getattribute__(v, "__dict__")
                if type(d) is dict:
                    items = list(islice(dict.items(d), 2000))
                elif type(d) is types.MappingProxyType:
                    items = list(islice(d.items(), 2000))
                else:
                    return
                kind = "."
        except Exception:
            return
        for key, x in items:
            look(x, path + ((kind, key),), depth - 1)

    mods = sorted(list(sys.modules.items()), key=lambda kv: not kv[0].startswith(("hexcast", "__main__")))
    try:
        for depth in range(4):                           # one step deeper each round: the shortest names first
            for name, mod in mods:
                d = getattr(mod, "__dict__", None)
                if type(d) is not dict:
                    continue
                for key, v in list(dict.items(d)):
                    look(v, (f"{name}.{key}",), depth)
                if len(found) == len(wanted) or visits > budget:
                    return found
                check()
    except _OutOfTime:
        pass
    return found


def _census(owners: _Owners, grew: dict[str, int] | None = None, seconds: float = 20.0,
            keep: int = 400, rest: float = 0.0) -> dict:
    """Every Python object by type. The objects the garbage collector tracks (dicts, lists, instances, functions,
    frames ...) are each counted once. The others (strings, bytes, numbers, tuples of them, arrays) are counted
    through the objects that hold them: one with n references counts 1/n at each holder, so it adds up to one
    without a list of everything seen. Objects Python keeps for good (interned names, small numbers) are left out.
    For the types holding the most, and the ones that grew most since the first census (`grew`: type name ->
    bytes), it also notes what holds them, and where the biggest holders hang in the module globals.
    `seconds` of work at most, resting `rest` between bursts (see _Pace)."""
    getsizeof, referents, is_tracked, getrefcount = sys.getsizeof, gc.get_referents, gc.is_tracked, sys.getrefcount
    clock = time.perf_counter
    base = _ref_base()
    started = clock()
    pace = _Pace(seconds, rest)
    check = pace.check
    objs = gc.get_objects()                              # the one step that cannot rest: a single C call
    tracked: dict[type, list] = {}
    packages: dict[str, list] = {}
    pkg_cache: dict = {}
    type_pkg: dict[type, str] = {}
    partial = False
    n = 0
    FunctionType, ModuleType = types.FunctionType, types.ModuleType
    try:
        for o in objs:
            t = type(o)
            try:
                s = getsizeof(o)
            except Exception:
                s = 0
            e = tracked.get(t)
            if e is None:
                tracked[t] = [1, s]
            else:
                e[0] += 1
                e[1] += s
            if t is FunctionType or issubclass(t, type):
                p = _defined_by(getattr(o, "__module__", None), owners, pkg_cache)
            elif t is ModuleType:
                p = _defined_by(getattr(o, "__name__", None), owners, pkg_cache)
            else:
                p = type_pkg.get(t)
                if p is None:
                    p = type_pkg[t] = _defined_by(getattr(t, "__module__", None), owners, pkg_cache)
            pe = packages.get(p)
            if pe is None:
                packages[p] = [1, s]
            else:
                pe[0] += 1
                pe[1] += s
            n += 1
            if not n & 0x1FF:
                check()
    except _OutOfTime:
        partial = True

    names = {t: _type_name(t) for t in tracked}
    targets: set[type] = {bytes, bytearray}
    targets.update(sorted(tracked, key=lambda t: -tracked[t][1])[:6])
    by_name = {v: k for k, v in names.items()}
    for name, d in sorted((grew or {}).items(), key=lambda kv: -kv[1])[:6]:
        t = by_name.get(name) or _resolve_type(name)
        if d > 0 and t is not None:
            targets.add(t)
    loose: dict[type, list] = {}
    hold: dict[type, dict[type, list]] = {t: {} for t in targets}      # target -> holder type -> [refs, bytes, holders]
    biggest: dict[type, list] = {t: [] for t in targets}              # target -> [[bytes, refs, holder], ...] (3)
    stack: list = []
    refs: Any = None
    counts: Any = None
    r: Any = None
    x: Any = None
    steps = 0

    def note(mine: dict, holder: Any) -> None:
        ht = type(holder)
        for rt, (w, s) in mine.items():
            h = hold[rt].get(ht)
            if h is None:
                hold[rt][ht] = [w, s, 1]
            else:
                h[0] += w
                h[1] += s
                h[2] += 1
            top = biggest[rt]
            if len(top) < 3 or s > top[-1][0]:
                top.append([s, w, holder])
                top.sort(key=lambda x: -x[0])
                del top[3:]

    if not partial:
        try:
            for o in objs:
                steps += 1
                if not steps & 0x3FF:
                    check()
                try:
                    refs = referents(o)
                    counts = list(map(getrefcount, refs))
                except Exception:
                    continue
                mine: dict | None = None
                for r, raw in zip(refs, counts):
                    steps += 1                                   # a big container is paced too, not one long burst
                    if not steps & 0x3FF:
                        check()
                    rt = type(r)
                    if is_tracked(r):
                        if rt in hold:
                            rc = raw - base - 1                      # the list of every object holds one more
                            if rc < _IMMORTAL:
                                w = 1.0 / rc if rc > 1 else 1.0
                                if mine is None:
                                    mine = {}
                                m = mine.get(rt)
                                if m is None:
                                    mine[rt] = [w, getsizeof(r) * w]
                                else:
                                    m[0] += w
                                    m[1] += getsizeof(r) * w
                        continue
                    rc = raw - base
                    if rc >= _IMMORTAL:
                        continue
                    w = 1.0 / rc if rc > 1 else 1.0
                    s = getsizeof(r) * w
                    e = loose.get(rt)
                    if e is None:
                        loose[rt] = [w, s]
                    else:
                        e[0] += w
                        e[1] += s
                    if rt in hold:
                        if mine is None:
                            mine = {}
                        m = mine.get(rt)
                        if m is None:
                            mine[rt] = [w, s]
                        else:
                            m[0] += w
                            m[1] += s
                    if rt is tuple or rt is dict:                # Python <= 3.13 untracks a dict of only strings and
                        stack.append((r, w))                     # numbers (a parsed JSON message): count what it holds
                r = None                                         # a loop variable left holding the last one would count as a holder
                if mine:
                    note(mine, o)
                while stack:                                   # what untracked tuples and dicts hold (strings, numbers, tuples)
                    x, xw = stack.pop()
                    refs = referents(x)
                    counts = list(map(getrefcount, refs))
                    inner: dict | None = None
                    for r, raw in zip(refs, counts):
                        steps += 1
                        if not steps & 0x3FF:
                            check()
                        if is_tracked(r):
                            continue
                        rc = raw - base
                        if rc >= _IMMORTAL:
                            continue
                        rt = type(r)
                        w = xw / rc if rc > 1 else xw
                        s = getsizeof(r) * w
                        e = loose.get(rt)
                        if e is None:
                            loose[rt] = [w, s]
                        else:
                            e[0] += w
                            e[1] += s
                        if rt in hold:
                            if inner is None:
                                inner = {}
                            m = inner.get(rt)
                            if m is None:
                                inner[rt] = [w, s]
                            else:
                                m[0] += w
                                m[1] += s
                        if rt is tuple:
                            stack.append((r, w))
                    r = None
                    if inner:
                        note(inner, x)
                    x = None
        except _OutOfTime:
            partial = True
            stack.clear()
    del objs, refs, counts, r, x

    rows: dict[str, list] = {}
    for src in (tracked, loose):
        for t, (c, s) in src.items():
            name = names.get(t) or _type_name(t)
            r = rows.get(name)
            if r is None:
                rows[name] = [name, c, s, type_pkg.get(t) or _defined_by(getattr(t, "__module__", None), owners, pkg_cache)]
            else:
                r[1] += c
                r[2] += s
    for t, (c, s) in loose.items():                     # strings, bytes ... belong to no package
        p = type_pkg.get(t) or _defined_by(getattr(t, "__module__", None), owners, pkg_cache)
        pe = packages.setdefault(p, [0, 0])
        pe[0] += c
        pe[1] += s
    ordered = sorted(rows.values(), key=lambda r: -r[2])
    for r in ordered:
        r[1], r[2] = round(r[1]), round(r[2])
    beyond = ordered[keep:]
    held_by: dict[str, dict] = {}
    want: set[int] = set()
    for rt, by in hold.items():
        if not by:
            continue
        tops = sorted(by.items(), key=lambda kv: -kv[1][1])[:5]
        held_by[_type_name(rt)] = {
            "by": [[_type_name(ht), round(w), round(s), k] for ht, (w, s, k) in tops],
            "biggest": [{"type": _type_name(type(h)), "size": round(s), "refs": round(w), "id": id(h)}
                        for s, w, h in biggest[rt] if s >= 65536]}
        want.update(id(h) for s, _w, h in biggest[rt] if s >= 1 << 20)
    biggest.clear()
    where = _global_names(want, rest=rest) if want else {}
    for info in held_by.values():
        for b in info["biggest"]:
            b["where"] = where.get(b.pop("id"))
    return {"types": ordered[:keep], "rest": [len(beyond), sum(r[1] for r in beyond), sum(r[2] for r in beyond)],
            "size": sum(r[2] for r in ordered), "objects": n, "partial": partial,
            "packages": sorted(([p, round(c), round(s)] for p, (c, s) in packages.items()), key=lambda r: -r[2])[:30],
            "held_by": held_by, "took": round(clock() - started, 3), "worked": round(pace.worked(), 3)}


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


def _change_of(hist: list[tuple[float, int]], now_bytes: int) -> dict:
    """Change since the first point and since the point about an hour ago, plus a thinned history."""
    out: dict = {"since": None, "since_at": None, "hour": None}
    if hist:
        out["since"], out["since_at"] = now_bytes - hist[0][1], hist[0][0]
        ago = [(t, b) for t, b in hist if t <= time.time() - 3000]
        if ago:
            out["hour"] = now_bytes - ago[-1][1]
    out["history"] = [[t, b] for t, b in _thin(hist, 120)]
    return out


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
        self.where_samples: deque = deque(maxlen=int(KEEP_SECONDS // SAMPLE_SECONDS) + 1)    # (t, (bytes per WHERE part))
        self.last_modules: dict | None = None
        self.last_alloc: dict | None = None
        self.last_where: dict | None = None
        self.census_first: dict | None = None
        self.census_last: dict | None = None
        self.census_hist: deque = deque(maxlen=4)          # the scheduled ones, for "in the last hour"
        self._lock: asyncio.Lock | None = None
        self._alloc_lock: asyncio.Lock | None = None
        self._census_lock: asyncio.Lock | None = None
        self._py: tuple[float, dict | None] | None = None
        self._files: tuple[float, dict | None] | None = None
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
            if gil:                                           # resting between bursts, so the show keeps up (see _Pace)
                result = await asyncio.to_thread(_walk, order, stop, owners, claimed, rest=PACE_REST)
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
                self.last_alloc = await asyncio.to_thread(_allocations, self._owners(), PACE_REST)
            return self.last_alloc

    def python_stats(self, max_age: float = FRESH_SECONDS) -> dict | None:
        if self._py is None or time.monotonic() - self._py[0] >= max_age:
            self._py = (time.monotonic(), python_allocator())
        return self._py[1]

    def mapped(self, owners: _Owners, arena_size: int | None) -> dict | None:
        if self._files is None or time.monotonic() - self._files[0] >= FRESH_SECONDS:
            self._files = (time.monotonic(), _mapped(owners, arena_size))
        return self._files[1]

    def _grew(self) -> dict[str, int]:
        first, last = self.census_first, self.census_last
        if not first or not last or first is last:
            return {}
        before = {r[0]: r[2] for r in first["types"]}
        return {r[0]: r[2] - before.get(r[0], 0) for r in last["types"]}

    async def census(self, max_age: float | None = None, scheduled: bool = False) -> dict:
        """Every Python object by type (see _census), counted again when the last count is older than max_age
        (default CENSUS_FRESH_SECONDS), in a worker thread. The first count is kept, so the page can show what
        grew since."""
        if max_age is None:
            max_age = CENSUS_FRESH_SECONDS

        def fresh() -> bool:
            return bool(self.census_last) and time.time() - self.census_last["at"] < max_age

        if self._census_lock is None:
            self._census_lock = asyncio.Lock()
        async with self._census_lock:
            if not fresh():
                owners, grew = self._owners(), self._grew()
                if getattr(sys, "_is_gil_enabled", lambda: True)():
                    result = await asyncio.to_thread(_census, owners, grew, CENSUS_SECONDS, rest=PACE_REST)
                else:                                       # free-threaded Python: see modules()
                    result = _census(owners, grew, 5.0)
                result["at"] = time.time()
                if self.census_first is None:
                    self.census_first = result
                self.census_last = result
            if scheduled and (not self.census_hist or self.census_hist[-1] is not self.census_last):
                self.census_hist.append(self.census_last)
            return self.census_last

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
        where = _where(proc, self.python_stats(0))
        if where:
            self.where_samples.append((round(time.time()), tuple(where[k] for k in WHERE)))
            self.last_where = where
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
        w = self.last_where
        if w:
            bits = []
            if w.get("python") is not None:
                bits.append(f"python objects {_fmt(w['python'])} (+{_fmt(w['python_free'])} spare)")
            if w.get("c") is not None:
                bits.append(f"C {_fmt(w['c'])} (+{_fmt(w['c_free'])} freed, kept)")
            if w.get("tracing"):
                bits.append(f"tracemalloc {_fmt(w['tracing'])}")
            if w.get("files") is not None:
                bits.append(f"files {_fmt(w['files'])}")
            bits.append(f"other {_fmt(w['other'])}")
            line += " | " + ", ".join(bits)
        first, last = self.census_first, self.census_last
        if first and last and first is not last:
            before = {r[0]: r for r in first["types"]}
            grew = sorted(((r[2] - (before[r[0]][2] if r[0] in before else 0), r[1] - (before[r[0]][1] if r[0] in before else 0), r[0])
                           for r in last["types"]), reverse=True)[:3]
            grew = [g for g in grew if g[0] >= 1 << 20]
            if grew:
                line += f" | grew since {time.strftime('%H:%M', time.localtime(first['at']))}: " + ", ".join(
                    f"{name} {dn:+,} ({_fmt(db)})" for db, dn, name in grew)
        return f"{time.strftime('%Y-%m-%d %H:%M:%S')} [memory] {line}"

    async def _run(self) -> None:
        await asyncio.sleep(FIRST_SAMPLE_SECONDS)
        next_modules = next_census = next_log = 0.0
        while True:
            try:
                proc = self.sample()
                now = time.monotonic()
                if now >= next_modules:
                    self.note_modules(await self.modules(max_age=60))
                    next_modules = now + MODULES_SECONDS
                if now >= next_census:
                    next_census = now + CENSUS_EVERY_SECONDS
                    try:
                        await self.census(max_age=60, scheduled=True)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("memory: counting objects failed")
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
        return _change_of([(t, b) for t, b in hist if b is not None], now_bytes)

    def _rest_change(self, now_bytes: int) -> dict:
        """Everything else over time: each module count's process total (the note taken with it) minus the modules."""
        times = [s[0] for s in self.samples]
        hist = []
        for t, rows in self.module_samples:
            i = bisect.bisect_right(times, t + 5) - 1
            if i >= 0 and self.samples[i][1] is not None:
                hist.append((t, max(0, self.samples[i][1] - sum(b for _k, b in rows))))
        return _change_of(hist, now_bytes)

    def _where_change(self, key: str, now_bytes: int) -> dict:
        i = WHERE.index(key)
        return _change_of([(t, v[i]) for t, v in self.where_samples if v[i] is not None], now_bytes)

    def _census_view(self) -> dict:
        out: dict = {"every": CENSUS_EVERY_SECONDS, "fresh": CENSUS_FRESH_SECONDS,
                     "running": bool(self._census_lock and self._census_lock.locked())}
        last, first = self.census_last, self.census_first
        if not last or not first:
            return out
        older = [c for c in self.census_hist if c["at"] <= last["at"] - 3000]
        hour = older[-1] if older else None
        f_map = {r[0]: r for r in first["types"]} if first is not last else None
        h_map = {r[0]: r for r in hour["types"]} if hour is not None and hour is not last else None

        def diff(m: dict | None, r: list) -> list | None:
            if m is None:
                return None
            o = m.get(r[0])
            return [r[1] - (o[1] if o else 0), r[2] - (o[2] if o else 0)]

        top = last["types"][:CENSUS_SHOW]
        grown: list = []
        if f_map is not None:
            shown = {r[0] for r in top}
            grown = sorted((r for r in last["types"] if r[0] not in shown), key=lambda r: -diff(f_map, r)[1])[:8]
            grown = [r for r in grown if diff(f_map, r)[1] >= 1 << 20]
        rows = [{"name": r[0], "count": r[1], "size": r[2], "defined": r[3], "since": diff(f_map, r), "hour": diff(h_map, r),
                 "held_by": last["held_by"].get(r[0])} for r in top + grown]
        p_first = {p[0]: p for p in first["packages"]} if first is not last else None
        packages = [{"name": p[0], "count": p[1], "size": p[2], "since": diff(p_first, p)} for p in last["packages"][:15]]
        out.update(at=last["at"], took=last["took"], worked=last.get("worked"), objects=last["objects"], size=last["size"], partial=last["partial"],
                   first_at=first["at"], hour_at=hour["at"] if h_map is not None else None, rows=rows, packages=packages,
                   rest=last["rest"])
        return out

    async def view(self, census: bool = False) -> dict:
        """The page's numbers. census=True counts every object again first (when the last count is older than
        CENSUS_FRESH_SECONDS); otherwise the page shows the last scheduled one."""
        if census:
            await self.census()
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
                "tasks": sum(other_tasks.values()), "task_top": other_tasks.most_common(8)}
        if rest["held"] is not None:
            rest["change"] = self._rest_change(rest["held"])
        if alloc.get("tracing"):
            rest["allocated"] = alloc["by_module"].get("", 0)
        py = self.python_stats()
        where = _where(proc, py)
        if where:
            rest["where"] = where
            rest["where_change"] = {k: self._where_change(k, v) for k, v in where.items() if v is not None}
        rest.update(python=py, mapped=self.mapped(owners, (py or {}).get("arena_size")), gc=_gc_state(), census=self._census_view(),
                    threads=_threads(proc.get("threads") if sys.platform.startswith("linux") else None))
        chart = [list(s) for s in _thin(list(self.samples), CHART_POINTS)]
        return {"ok": True, "now": time.time(), "started": self.started, "process": proc, "growth": self.growth(),
                "tasks": n_tasks, "connections": conns, "versions": versions(),
                "modules": {"at": mods["at"], "took": mods["took"], "worked": mods.get("worked"), "objects": mods["objects"],
                            "partial": mods["partial"],
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
    async def api_memory(census: int = 0):
        """?census=1 counts every Python object again first (at most every 2 minutes) - it takes a few seconds."""
        return await monitor.view(census=bool(census))

    return router
