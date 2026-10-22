"""Hellhound Derby add-on: registers the game with the Games plugin."""

from hexcast_plugins.games import core

from . import derby


def setup(ctx):
    core.register_game(derby.DERBY, {
        "plugin": ctx.id, "title": "Hellhound Derby", "order": 80,
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "derby_panel.js", "overlay": derby.OVERLAY, "api": derby.API_DOC,
    })
    derby.DERBY.resume()          # a game that was running when Hexcast stopped is settled now
    ctx.include_router(derby.router)
    ctx.on_shutdown(lambda: core.unregister_game("derby"))
