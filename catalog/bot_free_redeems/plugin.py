"""Bot-Free Redeems plugin: hands the module to Hexcast's plugin host."""

from hexcast_plugins.twitch_core import core as tc

from . import redeems

# The Twitch plugin asks this before an alert plays its own clip (running_plugin("bot_free_redeems").claims).
claims = redeems.claims


def setup(ctx):
    redeems.attach(ctx.app, ctx.port)
    for stop in (tc.on_chat(redeems.on_chat), tc.on_event(redeems.on_event), tc.on_status(redeems.on_status)):
        ctx.on_shutdown(stop)


async def teardown(ctx):
    pass
