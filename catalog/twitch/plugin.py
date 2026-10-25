"""Twitch plugin: hands the module to Hexcast's plugin host."""

from hexcast_core import tasks
from hexcast_plugins.twitch_core import core as tc

from . import twitch


def setup(ctx):
    twitch.attach_twitch(ctx.app, ctx.port)
    # what the shared connection (twitch_core) reports - each hook goes again when this plugin stops
    for stop in (tc.on_chat(twitch.on_chat), tc.on_event(twitch.on_event), tc.on_channel(twitch.on_channel),
                 tc.on_status(twitch.HUB.broadcast_status)):
        ctx.on_shutdown(stop)
    when_serving = getattr(ctx, "when_serving", None)       # a Hexcast before 2.2 starts on the first request
    if when_serving is not None:
        when_serving(twitch.start_twitch)


async def teardown(ctx):
    # The alert-queue loop (the connection to Twitch is twitch_core's, which closes its own sockets).
    await twitch.stop_twitch()
    # "!so" shoutouts are fire-and-forget tasks (twitch.py keeps no handle): stop any still running.
    await tasks.stop(tasks.running(twitch._do_shoutout))
