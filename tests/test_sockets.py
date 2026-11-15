import asyncio

import pytest

from hexcast_core import sockets


class WS:
    """A client's end of the socket. stuck: it stopped reading - nothing goes out until `gate` is set."""
    def __init__(self, stuck=False):
        self.text, self.binary, self.closed = [], [], []
        self.stuck = stuck
        self.gate = asyncio.Event()

    async def _wait(self):
        if self.stuck:
            await self.gate.wait()

    async def send_text(self, text):
        await self._wait()
        self.text.append(text)

    async def send_bytes(self, data):
        await self._wait()
        self.binary.append(data)

    async def close(self, code=1000):
        await self._wait()
        self.closed.append(code)


def test_a_send_goes_out_as_text_or_binary():
    async def main():
        ws = WS()
        await sockets.send(ws, "hi")
        await sockets.send(ws, b"\x01")
        assert ws.text == ["hi"] and ws.binary == [b"\x01"] and not ws.closed
    asyncio.run(main())


def test_a_client_that_stopped_reading_is_given_up_on_and_closed_once_it_reads_again():
    async def main():
        ws = WS(stuck=True)
        for _ in range(3):
            with pytest.raises(asyncio.TimeoutError):
                await sockets.send(ws, "x", timeout=0.05)
        assert len(sockets._closing) == 1                  # one close waits behind the stuck bytes, not three
        assert ws.closed == []
        ws.gate.set()                                       # the page reads again: it gets the close, and reconnects
        await asyncio.sleep(0.01)
        assert ws.closed == [1011] and not sockets._closing
        assert ws.text == []                                # a send that gave up never goes out later
    asyncio.run(main())


def test_the_default_wait_is_send_timeout(monkeypatch):
    monkeypatch.setattr(sockets, "SEND_TIMEOUT", 0.05)

    async def main():
        ws = WS(stuck=True)
        with pytest.raises(asyncio.TimeoutError):
            await sockets.send(ws, "x")
        ws.gate.set()
        await asyncio.sleep(0.01)
    asyncio.run(main())


def test_a_send_that_fails_raises_as_before_and_is_not_closed():
    class Gone(WS):
        async def send_text(self, text):
            raise RuntimeError("gone")

    async def main():
        with pytest.raises(RuntimeError):
            await sockets.send(Gone(), "x")
        assert not sockets._closing                         # only a send that hangs gets a close
    asyncio.run(main())
