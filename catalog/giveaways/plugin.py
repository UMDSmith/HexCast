"""Giveaways plugin: hands the module to Hexcast's plugin host."""

from hexcast_core import tasks

from . import giveaways


def setup(ctx):
    giveaways.attach(ctx.app, ctx.port)


async def teardown(ctx):
    # a draw counting down (or a winner on screen), and a chat announcement still waiting out its delay
    await giveaways.shutdown()
    await tasks.stop(tasks.running(giveaways.announce))
