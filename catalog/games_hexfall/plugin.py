"""Hexfall add-on: registers the game with the Games plugin."""

from hexcast_plugins.games import core

from . import hexfall


def setup(ctx):
    core.register_game(hexfall.HEXFALL, {
        "plugin": ctx.id, "title": "Hexfall", "order": 50,
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "hexfall_panel.js", "overlay": hexfall.OVERLAY, "api": hexfall.API_DOC,
    })
    hexfall.HEXFALL.resume()      # a game that was running when Hexcast stopped is settled now
    ctx.include_router(hexfall.router)
    ctx.on_shutdown(lambda: core.unregister_game("hexfall"))
