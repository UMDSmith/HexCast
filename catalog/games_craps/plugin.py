"""Craps add-on: registers the game with the Games plugin."""

from hexcast_plugins.games import core

from . import craps


def setup(ctx):
    core.register_game(craps.CRAPS, {
        "title": "Craps", "order": 20,
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "craps_panel.js", "overlay": craps.OVERLAY, "api": craps.API_DOC,
    })
    ctx.include_router(craps.router)
    ctx.on_shutdown(lambda: core.unregister_game("craps"))
