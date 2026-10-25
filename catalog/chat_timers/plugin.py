"""Chat Timers plugin: hands the module to Hexcast's plugin host."""

from hexcast_plugins.twitch_core import core as tc

from . import timers


def setup(ctx):
    timers.attach(ctx.app, ctx.port)
    for stop in (tc.on_chat(timers.on_chat), tc.on_event(timers.on_event)):
        ctx.on_shutdown(stop)
    when_serving = getattr(ctx, "when_serving", None)       # a Hexcast before 2.2 starts on the first request
    if when_serving is not None:
        when_serving(timers.ensure_started)


async def teardown(ctx):
    await timers.LOOPS.shutdown()
