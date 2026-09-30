"""Tearing down a plugin's background work.

The modules that became plugins start fire-and-forget asyncio tasks - and yt-dlp / ffmpeg
child processes - without keeping a handle, because until now the whole process simply ended
with them. A plugin that is stopped, updated or removed while Hexcast keeps running must not
leave them behind, so its `teardown` finds them by the code they run and ends them here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable


def running(*functions: Callable) -> list[asyncio.Task]:
    """Tasks whose coroutine is currently executing one of these coroutine functions."""
    codes = {getattr(f, "__code__", None) for f in functions} - {None}
    return [t for t in asyncio.all_tasks()
            if not t.done() and getattr(t.get_coro(), "cr_code", None) in codes]


def in_module(module: Any) -> list[asyncio.Task]:
    """Tasks executing any code of `module` (found by the frame's globals), except the caller."""
    me = asyncio.current_task()
    scope = vars(module)
    found = []
    for task in asyncio.all_tasks():
        frame = getattr(task.get_coro(), "cr_frame", None)
        if task is not me and not task.done() and frame is not None and frame.f_globals is scope:
            found.append(task)
    return found


def children_of(tasks: Iterable[asyncio.Task]) -> list[asyncio.subprocess.Process]:
    """The asyncio subprocesses these tasks are waiting on. Cancelling a task only ends the
    waiting - the child keeps running - so they are found by following each task's await
    chain for a `Process` held in a frame. Best effort: never raises."""
    procs: dict[int, asyncio.subprocess.Process] = {}       # by identity: one Process shows up in several frames
    try:
        for task in tasks:
            coro = task.get_coro()
            while coro is not None:
                frame = getattr(coro, "cr_frame", None) or getattr(coro, "gi_frame", None)
                if frame is None:
                    break
                for v in frame.f_locals.values():
                    if isinstance(v, asyncio.subprocess.Process):
                        procs[id(v)] = v
                coro = getattr(coro, "cr_await", None) or getattr(coro, "gi_yieldfrom", None)
    except Exception:
        pass
    return list(procs.values())


async def stop(tasks: Iterable[asyncio.Task], *, timeout: float = 2.0) -> None:
    """Cancel the tasks, kill the child processes they were waiting on, and wait (briefly) for
    them to finish."""
    tasks = [t for t in tasks if not t.done()]
    procs = children_of(tasks)
    for task in tasks:
        task.cancel()
    for proc in procs:
        try:
            if proc.returncode is None:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
