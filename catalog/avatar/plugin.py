"""Avatars plugin: hands the module to Hexcast's plugin host."""

from . import avatar


def setup(ctx):
    avatar.attach(ctx)


async def teardown(ctx):
    await avatar.detach()                     # audio capture threads, waiting speak calls, open sockets
