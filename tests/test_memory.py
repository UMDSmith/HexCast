"""The Memory page (/memory): what each module holds, the tasks running its code, its websockets and the
bytes waiting on them, the process totals and the history. Read-only."""

import asyncio
import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from conftest import write_plugin
from hexcast_core import memory

HOARDER = '''
from fastapi import APIRouter
router = APIRouter()
KEPT = []

class Cache:
    def __init__(self):
        self.items = {}

CACHE = Cache()

def keep(n, size):
    for _ in range(n):
        KEPT.append(bytes(size))

async def wait_for(ev):
    await ev.wait()

def setup(ctx):
    ctx.include_router(router)
'''

FRIEND = '''
from hexcast_core.host import running_plugin
BORROWED = []

def borrow():
    other = running_plugin("hoarder")
    BORROWED.append(other.KEPT)           # another module's list and object: still that module's memory
    BORROWED.append(other.CACHE)

def setup(ctx):
    pass
'''


def _start(world, pid, body):
    write_plugin(world.catalog, pid, setup_body=body, nav=False)
    world.installer.install(pid)
    assert world.host.load(pid)
    return world.host.module(pid)


def _count(world, tasks=None):
    owners = memory._Owners(world.plugins)
    order, stop, claimed = memory._roots(world.host, owners, tasks or {})
    return memory._walk(order, stop, owners, claimed)


def test_names_and_files_belong_to_their_module(tmp_path):
    owners = memory._Owners(tmp_path / "plugins")
    assert owners.of_name("hexcast_plugins.avatar.avatar") == "avatar"
    assert owners.of_name("hexcast_core.memory") == memory.CORE
    assert owners.of_name("hexcast") == memory.CORE
    assert owners.of_name("json") is None
    assert owners.of_file(str(tmp_path / "plugins" / "clips" / "clips.py")) == "clips"
    assert owners.of_file(memory.__file__) == memory.CORE
    assert owners.of_file("<frozen abc>") is None
    assert owners.of_file(asyncio.__file__) is None


def test_what_a_module_keeps_is_counted_under_it(world):
    mod = _start(world, "hoarder", HOARDER)
    before = _count(world)["held"]["hoarder"][0]
    mod.keep(40, 50_000)                                  # 2 MB
    mod.CACHE.items.update({i: str(i).rjust(1000, "x") for i in range(200)})     # an instance of its own class
    held, objects, kinds = _count(world)["held"]["hoarder"]
    assert held - before >= 2_000_000 + 200_000
    assert kinds[0][0] == "bytes" and kinds[0][1] >= 2_000_000 and kinds[0][2] >= 40


def test_another_modules_memory_stays_with_that_module(world):
    hoarder = _start(world, "hoarder", HOARDER)
    friend = _start(world, "friend", FRIEND)              # "friend" is counted before "hoarder"
    hoarder.keep(20, 100_000)
    friend.borrow()
    held = _count(world)["held"]
    assert held["hoarder"][0] >= 2_000_000
    assert held["friend"][0] < 200_000


def test_the_core_does_not_take_a_plugins_memory_through_the_host(world):
    hoarder = _start(world, "hoarder", HOARDER)
    hoarder.keep(20, 100_000)
    owners = memory._Owners(world.plugins)
    order, stop, claimed = memory._roots(world.host, owners, {})
    # the core reaches every plugin's record (its module, context, shutdown hooks and routes)
    order = [(k, v) for k, v in order if k != memory.CORE] + [(memory.CORE, [{"loaded": world.host.loaded}])]
    held = memory._walk(order, stop, owners, claimed)["held"]
    assert held["hoarder"][0] >= 2_000_000
    assert held[memory.CORE][0] < 500_000


def test_tasks_are_counted_under_the_module_whose_code_they_run(world):
    hoarder = _start(world, "hoarder", HOARDER)

    async def main():
        ev = asyncio.Event()
        mine = [asyncio.create_task(hoarder.wait_for(ev)) for _ in range(3)]

        async def library_style():                       # code of no module: counted as "everything else"
            await ev.wait()

        other = asyncio.create_task(library_style())
        await asyncio.sleep(0)
        owners = memory._Owners(world.plugins)
        by, labels, rest = memory._tasks(owners)
        ev.set()
        await asyncio.gather(*mine, other)
        return by, labels, rest

    by, labels, rest = asyncio.run(main())
    assert len(by["hoarder"]) == 3
    assert labels["hoarder"] == {"wait_for": 3}
    assert sum(rest.values()) >= 1 and "hoarder" not in rest


class _Transport:
    def __init__(self, waiting):
        self.waiting = waiting

    def get_write_buffer_size(self):
        return self.waiting


class _Server:
    def __init__(self):
        self.connections = set()


class _Proto:
    """Stands in for a uvicorn websocket connection: `send` is its method, its task starts with `run_asgi`."""

    def __init__(self, server, waiting, serve):
        self.transport = _Transport(waiting)
        self.server_state = server
        self.serve = serve
        server.connections.add(self)

    async def send(self, message):
        pass

    async def run_asgi(self):
        await self.serve()


def test_websockets_and_the_bytes_waiting_on_them_belong_to_the_module_serving_them(world):
    hoarder = _start(world, "hoarder", HOARDER)

    async def main():
        ev = asyncio.Event()
        server = _Server()
        stuck = _Proto(server, 300_000, lambda: hoarder.wait_for(ev))
        fine = _Proto(server, 0, lambda: hoarder.wait_for(ev))
        core_side = _Proto(server, 10, ev.wait)
        _Proto(server, 5, ev.wait)                       # an HTTP connection: no websocket
        for p in (stuck, fine, core_side):
            world.host._sockets.add(("/somewhere", p.send))
        tasks = [asyncio.create_task(p.run_asgi()) for p in (stuck, fine, core_side)]
        await asyncio.sleep(0)
        owners = memory._Owners(world.plugins)
        by, _labels, _rest = memory._tasks(owners)
        per, conns = memory._sockets(world.host, by)
        ev.set()
        await asyncio.gather(*tasks)
        return per, conns

    per, conns = asyncio.run(main())
    assert per["hoarder"] == {"sockets": 2, "waiting": 300_000, "waiting_max": 300_000, "stuck": 1}
    assert per[memory.CORE]["sockets"] == 1 and per[memory.CORE]["waiting"] == 10
    assert conns["open"] == 4 and conns["waiting"] == 300_015 and conns["websocket_impl"] == "_Proto"


def test_process_numbers_are_there_on_this_platform():
    p = memory.process_stats()
    assert p["pid"] > 0 and p["python_blocks"] > 0 and p["threads"] >= 1
    if sys.platform.startswith("linux") or sys.platform == "win32":
        assert p["total"] > 10_000_000 and p["rss"] > 10_000_000


def test_growth_is_a_trend_per_hour():
    mon = memory.Monitor(None)
    now = time.time()
    for i in range(13):                                   # an hour, 5 minutes apart, 12 MB more each time
        mon.samples.append((now - 3600 + i * 300, 100_000_000 + i * 12_000_000, None, None, 10, 0, 0))
    g = mon.growth()
    assert g["per_hour"] == pytest.approx(144_000_000, rel=0.01)
    assert g["since_start"] == 144_000_000
    mon.samples.clear()
    mon.samples.append((now, 1, None, None, 1, 0, 0))
    assert mon.growth()["per_hour"] is None


def test_the_log_line_is_dated_and_names_the_biggest_modules():
    mon = memory.Monitor(None)
    mon.last_modules = {"held": {"avatar": [48 << 20, 10, []], "core": [9 << 20, 5, []], "music": [12 << 20, 7, []]}, "at": time.time()}
    mon.samples.append((time.time(), 1 << 30, 1 << 30, None, 140, 13, 0))
    line = mon.log_line({"total": 1 << 30, "rss": 1 << 30})
    assert line[:4].isdigit() and "[memory] 1.00 GB in use" in line
    assert "140 tasks" in line and "13 websockets" in line
    assert line.index("avatar 48 MB") < line.index("music 12 MB") < line.index("core 9 MB")


def test_the_history_is_bounded():
    mon = memory.Monitor(None)
    assert mon.samples.maxlen == memory.KEEP_SECONDS // memory.SAMPLE_SECONDS + 1
    assert mon.module_samples.maxlen == memory.KEEP_SECONDS // memory.MODULES_SECONDS + 1


def test_allocations_are_attributed_when_tracing(world):
    import tracemalloc
    hoarder = _start(world, "hoarder", HOARDER)
    owners = memory._Owners(world.plugins)
    assert memory._allocations(owners) == {"tracing": False} or tracemalloc.is_tracing()
    started_here = not tracemalloc.is_tracing()
    if started_here:
        tracemalloc.start(10)
    try:
        hoarder.keep(10, 100_000)
        a = memory._allocations(owners)
    finally:
        if started_here:
            tracemalloc.stop()
    assert a["tracing"] and a["by_module"].get("hoarder", 0) >= 1_000_000
    top = a["top"][0]
    assert top["owner"] == "hoarder" and top["file"] == "hoarder/plugin.py" and top["size"] >= 1_000_000


def test_the_page_and_its_numbers_are_served_and_read_only(world):
    from hexcast_core.paths import STATIC_DIR
    hoarder = _start(world, "hoarder", HOARDER)
    hoarder.keep(5, 100_000)
    mon = memory.Monitor(world.host)
    world.app.include_router(memory.build_router(mon, STATIC_DIR))
    c = TestClient(world.app)
    page = c.get("/memory")
    assert page.status_code == 200 and 'id="hexbar"' in page.text and "/api/memory" in page.text
    d = c.get("/api/memory").json()
    assert d["ok"] and d["process"]["pid"] > 0 and d["versions"]["python"]
    rows = {r["key"]: r for r in d["modules"]["rows"]}
    assert rows["hoarder"]["held"] >= 500_000 and rows["hoarder"]["name"] == "Hoarder"
    assert rows[memory.CORE]["name"] == "Hexcast core"
    assert "held" in d["modules"]["rest"]
    for method in ("post", "put", "delete"):
        assert getattr(c, method)("/api/memory").status_code == 405


def test_the_sampler_notes_the_process_and_the_modules(world, monkeypatch):
    monkeypatch.setattr(memory, "FIRST_SAMPLE_SECONDS", 0)
    monkeypatch.setattr(memory, "SAMPLE_SECONDS", 0.05)
    monkeypatch.setattr(memory, "LOG_SECONDS", 3600)
    _start(world, "hoarder", HOARDER)
    mon = memory.Monitor(world.host)
    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))

    async def main():
        mon.start()
        for _ in range(100):
            await asyncio.sleep(0.05)
            if len(mon.samples) >= 3 and mon.module_samples:
                break
        await mon.stop()

    asyncio.run(main())
    assert len(mon.samples) >= 3 and len(mon.module_samples) == 1
    assert dict(mon.module_samples[0][1])["hoarder"] > 0
    assert len(printed) == 1 and "[memory]" in printed[0]
    assert mon._task is None
