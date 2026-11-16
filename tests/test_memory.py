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
    assert len(mon.where_samples) == len(mon.samples) and mon.census_first is mon.census_last is not None
    assert list(mon.census_hist) == [mon.census_first]


# ---- inside "everything else" ----------------------------------------------------------------

MALLOC_STATS = """Small block threshold = 512, in 32 size classes.

class   size   num pools   blocks in use  avail blocks
-----   ----   ---------   -------------  ------------
    0     16           1              77           944
    1     32         394          200849            91

# arenas allocated total           =                   61
# arenas reclaimed                 =                   12
# arenas highwater mark            =                   52
# arenas allocated current         =                   49
49 arenas * 1048576 bytes/arena    =           51,380,224

# bytes in allocated blocks        =           49,880,336
# bytes in available blocks        =              434,240
7 unused pools * 16384 bytes       =              114,688
# bytes lost to pool headers       =              150,912
# bytes lost to quantization       =              800,048
# bytes lost to arena alignment    =                    0
Total                              =           51,380,224

arena map counts
# arena map mid nodes              =                    1
Total                              =              655,360

            4 free PyDictObjects * 48 bytes each =                  192
"""


def test_pythons_allocator_numbers_are_read_from_its_stats():
    py = memory._parse_pymalloc(MALLOC_STATS)
    assert py == {"in_use": 49_880_336, "reserved": 51_380_224, "free": 1_499_888, "arenas": 49, "highwater": 52,
                  "arena_size": 1_048_576}
    assert memory._parse_pymalloc("mimalloc says something else\n") is None


def test_reading_pythons_allocator_leaves_stderr_working(capfd):
    py = memory.python_allocator()
    if py is None:
        pytest.skip("this Python's allocator is not pymalloc")
    assert py["in_use"] > 1_000_000 and py["reserved"] >= py["in_use"]
    print("still here", file=sys.stderr, flush=True)
    err = capfd.readouterr().err
    assert "still here" in err and "arenas allocated" not in err


def test_the_parts_add_up_to_the_process_total():
    mb = 1 << 20
    proc = {"total": 1000 * mb, "c_heap": {"in_use": 300 * mb, "free_kept": 100 * mb}, "file": 50 * mb, "shmem": 5 * mb}
    w = memory._where(proc, {"in_use": 200 * mb, "free": 20 * mb})
    assert w == {"python": 200 * mb, "python_free": 20 * mb, "c": 300 * mb, "c_free": 100 * mb, "tracing": None,
                 "files": 55 * mb, "other": 325 * mb}
    w = memory._where({"total": 400 * mb}, {"in_use": 100 * mb, "free": 10 * mb})          # no C heap numbers, no files
    assert w["c"] is None and w["files"] is None and w["other"] == 290 * mb
    assert memory._where({"total": None}, None) is None


SMAPS = b"""55d4c6a3e000-55d4c6a5e000 r-xp 00000000 fd:01 1234                       /usr/bin/python3.12
Size:                128 kB
Rss:                 120 kB
Swap:                  0 kB
VmFlags: rd ex mr mw me dw
55d4c6a5e000-55d4c6a6e000 rw-p 00020000 fd:01 1234                       /usr/bin/python3.12
Rss:                  60 kB
Swap:                  4 kB
55d4c8000000-55d4d0000000 rw-p 00000000 00:00 0                          [heap]
Rss:              120000 kB
Swap:              10000 kB
7f0000000000-7f0002000000 rw-p 00000000 00:00 0
Rss:               30000 kB
Swap:               2000 kB
7f0002000000-7f0004000000 ---p 00000000 00:00 0
Rss:                   0 kB
7f1000000000-7f1000001000 ---p 00000000 00:00 0
Rss:                   0 kB
7f1000001000-7f1000801000 rw-p 00000000 00:00 0
Rss:                1100 kB
7f2000000000-7f2002000000 rw-p 00000000 00:00 0
Rss:                3000 kB
7f2002000000-7f2004000000 rw-p 00000000 00:00 0                          [anon:cpython obmalloc]
Rss:                2048 kB
7ffd00000000-7ffd00021000 rw-p 00000000 00:00 0                          [stack]
Rss:                 132 kB
"""


def test_the_linux_memory_map_is_split_into_files_and_blocks():
    files, anon = memory._parse_smaps(SMAPS.splitlines(keepends=True))
    assert files == {b"/usr/bin/python3.12": [180 * 1024, 4 * 1024]}
    blocks = memory._linux_blocks(anon)
    assert [b[:2] for b in blocks] == [["heap", "[heap]"], ["thread_heap", ""], ["thread_stack", ""], ["block", ""],
                                       ["named", "cpython obmalloc"], ["stack", "[stack]"]]
    groups = memory._group_blocks(blocks)
    assert groups[0] == {"kind": "heap", "name": "[heap]", "size": None, "count": 1, "used": 130000 * 1024,
                         "reserved": 128 << 20}
    assert [g["kind"] for g in groups] == ["heap", "thread_heap", "block", "named", "thread_stack"]   # under 1 MB: left out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows heaps and VirtualQuery")
def test_windows_heaps_and_blocks_are_read():
    heap, bases = memory._windows_heaps()
    assert heap["in_use"] > 0 and heap["heap"] >= heap["in_use"] and bases
    py = memory.python_allocator()
    m = memory._mapped(memory._Owners(None), py and py["arena_size"])
    assert m["measure"] == "committed" and m["blocks"]
    if py:
        assert any(b["kind"] == "python" for b in m["blocks"])


GROWER = '''
from fastapi import APIRouter
router = APIRouter()
KEPT = []
SHARED = []

class Item:
    def __init__(self, n):
        self.n = n

def keep(n, size):
    for _ in range(n):
        KEPT.append(bytes(size))

def items(n):
    KEPT.append([Item(i) for i in range(n)])

def share(size):
    big = "z" * size
    SHARED.extend([[big], [big], [big]])     # one string, three holders

def setup(ctx):
    ctx.include_router(router)
'''


def _rows(c):
    return {r[0]: r for r in c["types"]}


def test_the_census_counts_every_object_by_type_and_says_what_holds_the_big_ones(world):
    mod = _start(world, "grower", GROWER)
    owners = memory._Owners(world.plugins)
    before = _rows(memory._census(owners))
    mod.keep(30, 100_000)                                  # 3 MB of bytes in one module list
    mod.items(5000)
    mod.share(4_000_000)
    c = memory._census(owners, grew={"bytes": 3_000_000})
    after = _rows(c)
    assert not c["partial"] and c["objects"] > 1000
    assert 2_900_000 <= after["bytes"][2] - before["bytes"][2] <= 3_300_000
    assert after["plugin.Item"][1] == 5000 and after["plugin.Item"][3] == "grower"
    assert 3_800_000 <= after["str"][2] - before["str"][2] <= 4_600_000      # one string, counted once
    held = c["held_by"]["bytes"]
    assert held["by"][0][0] == "list" and held["by"][0][2] >= 2_900_000
    big = held["biggest"][0]
    assert big["type"] == "list" and big["size"] >= 2_900_000 and big["where"] == "hexcast_plugins.grower.plugin.KEPT"
    packages = {p[0]: p for p in c["packages"]}
    assert packages["grower"][1] >= 5000 and packages["python"][2] > packages["grower"][2]


def test_the_census_stops_when_out_of_time(world):
    assert memory._census(memory._Owners(world.plugins), seconds=0)["partial"]


def test_global_names_find_where_an_object_hangs(world):
    mod = _start(world, "grower", GROWER)
    mod.KEPT.append({"inner": [1, 2, 3]})
    inner = mod.KEPT[-1]["inner"]
    found = memory._global_names({id(inner), id(mod.KEPT)})
    assert found[id(mod.KEPT)] == "hexcast_plugins.grower.plugin.KEPT"
    assert found[id(inner)] == "hexcast_plugins.grower.plugin.KEPT[0]['inner']"


def test_the_page_shows_what_grew_since_the_first_census(world):
    from hexcast_core.paths import STATIC_DIR
    mod = _start(world, "grower", GROWER)
    mon = memory.Monitor(world.host)
    world.app.include_router(memory.build_router(mon, STATIC_DIR))
    c = TestClient(world.app)
    rest = c.get("/api/memory").json()["modules"]["rest"]
    assert "rows" not in rest["census"] and rest["gc"]["enabled"] in (True, False) and rest["threads"]["python"] >= 1
    assert rest["where"]["other"] is not None
    first = c.get("/api/memory?census=1").json()["modules"]["rest"]["census"]
    assert first["rows"] and first["rows"][0]["since"] is None
    mod.keep(40, 100_000)
    mon.census_last["at"] -= memory.CENSUS_FRESH_SECONDS + 1           # old enough to count again
    again = c.get("/api/memory?census=1").json()["modules"]["rest"]["census"]
    rows = {r["name"]: r for r in again["rows"]}
    assert rows["bytes"]["since"][1] >= 3_900_000 and rows["bytes"]["since"][0] >= 40
    assert again["first_at"] == first["at"] - memory.CENSUS_FRESH_SECONDS - 1 and again["at"] > again["first_at"]
    assert rows["bytes"]["held_by"]["biggest"][0]["where"] == "hexcast_plugins.grower.plugin.KEPT"
    mon.census_last["at"] = time.time()
    same = c.get("/api/memory?census=1").json()["modules"]["rest"]["census"]
    assert same["at"] == mon.census_last["at"]                          # too soon: the same count


def test_the_log_line_says_who_holds_the_memory_and_what_grew():
    mon = memory.Monitor(None)
    mb = 1 << 20
    mon.last_where = {"python": 300 * mb, "python_free": 20 * mb, "c": 90 * mb, "c_free": 40 * mb, "tracing": None,
                      "files": 35 * mb, "other": 15 * mb}
    mon.census_first = {"at": time.time() - 7200, "types": [["dict", 1000, 1 * mb, "python"], ["bytes", 10, 1000, "python"]]}
    mon.census_last = {"at": time.time(), "types": [["dict", 121_000, 31 * mb, "python"], ["bytes", 10, 1000, "python"]]}
    line = mon.log_line({"total": 500 * mb, "rss": 500 * mb})
    assert "python objects 300 MB (+20 MB spare), C 90 MB (+40 MB freed, kept), files 35 MB, other 15 MB" in line
    assert "dict +120,000 (30 MB)" in line and "bytes" not in line.split("grew since")[1]
