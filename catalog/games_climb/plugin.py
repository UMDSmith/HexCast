"""Soul Climb add-on: registers the game with the Games plugin."""

from hexcast_plugins.games import core

from . import climb


def setup(ctx):
    core.register_game(climb.CLIMB, {
        "plugin": ctx.id, "title": "Soul Climb", "order": 60,
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "climb_panel.js", "overlay": climb.OVERLAY, "api": climb.API_DOC,
    })
    climb.CLIMB.resume()          # a game that was running when Hexcast stopped is settled now
    ctx.include_router(climb.router)
    ctx.on_shutdown(lambda: core.unregister_game("climb"))
