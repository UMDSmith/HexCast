import asyncio
import sys

from hexcast_core import tasks


async def _sleeper():
    await asyncio.sleep(30)


async def _other():
    await asyncio.sleep(30)


def test_running_finds_tasks_by_the_code_they_run():
    async def main():
        a = asyncio.ensure_future(_sleeper())
        b = asyncio.ensure_future(_other())
        await asyncio.sleep(0)
        found = tasks.running(_sleeper)
        assert found == [a]
        await tasks.stop(tasks.running(_sleeper, _other))
        assert a.cancelled() and b.cancelled()
    asyncio.run(main())


def test_in_module_finds_tasks_running_that_modules_code():
    async def main():
        t = asyncio.ensure_future(_sleeper())
        await asyncio.sleep(0)
        assert t in tasks.in_module(sys.modules[__name__])
        await tasks.stop([t])
    asyncio.run(main())


def test_stop_kills_the_child_process_a_task_is_waiting_on():
    async def waits_on_child():
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")
        await proc.communicate()

    async def main():
        t = asyncio.ensure_future(waits_on_child())
        for _ in range(100):                      # let the child start and the task reach communicate()
            await asyncio.sleep(0.02)
            if tasks.children_of([t]):
                break
        procs = tasks.children_of([t])
        assert len(procs) == 1 and procs[0].returncode is None
        await tasks.stop([t])
        await asyncio.sleep(0.2)
        assert t.cancelled() and procs[0].returncode is not None
    asyncio.run(main())
