"""Twitch connection: the library every Twitch module shares (see core.py)."""

import json

from hexcast_core import PluginError

from . import core


def _old_twitch(ctx) -> bool:
    """A Twitch plugin from before 1.3 runs a connection of its own: both would sign in to Twitch, answer the
    same sign-in pages and open twice the connections Twitch allows."""
    try:
        m = json.loads((ctx.plugin_dir.parent / "twitch" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(m, dict) and "twitch_core" not in (m.get("requires") or [])


def setup(ctx):
    if _old_twitch(ctx):
        raise PluginError("the Twitch plugin is older than 1.3 - update it first (+ tab): from 1.3 on it uses this "
                          "shared connection, until then both would connect to Twitch")
    ctx.include_router(core.router)
    when_serving = getattr(ctx, "when_serving", None)       # a Hexcast before 2.2 connects on the first request
    if when_serving is not None:
        when_serving(core.start)


async def teardown(ctx):
    # The per-channel EventSub / anonymous-chat connections: cancelling them leaves their
    # `async with websockets.connect(...)`, which closes the sockets to Twitch.
    await core.stop()
