"""Discord plugin: hands the module to Hexcast's plugin host."""

from . import discord_reactive


def setup(ctx):
    discord_reactive.attach_discord(ctx.app, ctx.port)


async def teardown(ctx):
    await discord_reactive.stop_discord()      # closes the RPC connection to the Discord desktop app
