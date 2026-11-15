"""Sending to a websocket client that may have stopped reading.

From uvicorn 0.52.1 a send waits until the client has taken the bytes ahead of it (write flow control). A
client that stays connected but stops reading - a frozen or throttled OBS browser source, say - then holds
every send to it for as long as the connection lasts, which can be forever: a broadcast loop stalls for every
other client too, and whatever is queued behind it piles up in memory.

`send` gives up on such a client after SEND_TIMEOUT seconds; the caller drops it, as it does a client whose
send failed. The client is also asked to close. That close can only go out behind what is already stuck, so
it is not waited for: when the page reads again it gets the close and reconnects (every Hexcast page does),
rather than sitting on an open socket nothing is sent to any more.
"""

from __future__ import annotations

import asyncio
from typing import Any

SEND_TIMEOUT = 10.0

_closing: dict[Any, asyncio.Task] = {}          # client -> the close waiting to go out behind its stuck bytes


async def send(ws: Any, data: str | bytes, timeout: float | None = None) -> None:
    """Send text (str) or binary (bytes) to one client. Raises like a plain send when the send fails, and
    TimeoutError when it has not gone out within `timeout` seconds (SEND_TIMEOUT when not given)."""
    coro = ws.send_bytes(data) if isinstance(data, (bytes, bytearray)) else ws.send_text(data)
    try:
        await asyncio.wait_for(coro, SEND_TIMEOUT if timeout is None else timeout)
    except asyncio.TimeoutError:
        close_later(ws)
        raise


def close_later(ws: Any, code: int = 1011) -> None:
    """Close a client without waiting for the close to go out. Once per client, however many sends gave up."""
    if ws in _closing:
        return
    task = asyncio.get_running_loop().create_task(_close(ws, code))
    _closing[ws] = task
    task.add_done_callback(lambda _t: _closing.pop(ws, None))


async def _close(ws: Any, code: int) -> None:
    try:
        await ws.close(code=code)         # done when the client reads again, or when the connection is lost
    except Exception:
        pass
