"""Roulette add-on: registers the game with the Games plugin."""

from hexcast_plugins.games import core

from . import roulette


def setup(ctx):
    core.register_game(roulette.ROULETTE, {
        "plugin": ctx.id, "title": "Roulette", "order": 10,
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "roulette_panel.js", "overlay": roulette.OVERLAY, "api": roulette.API_DOC,
    })
    ctx.include_router(roulette.router)
    ctx.on_shutdown(lambda: core.unregister_game("roulette"))
