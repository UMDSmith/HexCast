"""Twitch plugin: hands the module to Hexcast's plugin host."""

from hexcast_core import tasks

from . import twitch


def setup(ctx):
    twitch.attach_twitch(ctx.app, ctx.port)


async def teardown(ctx):
    # The EventSub / anonymous-chat reader and the alert-queue loop: cancelling them
    # leaves their `async with websockets.connect(...)`, which closes the sockets to Twitch.
    await twitch.stop_twitch()
    # "!so" shoutouts are fire-and-forget tasks (twitch.py keeps no handle): stop any still running.
    await tasks.stop(tasks.running(twitch._do_shoutout))
