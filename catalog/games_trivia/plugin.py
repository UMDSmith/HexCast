"""Trivia add-on: registers the game with the Games plugin."""

from hexcast_plugins.games import core

from . import trivia


def setup(ctx):
    core.register_game(trivia.TRIVIA, {
        "plugin": ctx.id, "title": "Trivia", "order": 40,
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "trivia_panel.js", "overlay": trivia.OVERLAY, "api": trivia.API_DOC,
    })
    trivia.TRIVIA.resume()      # a game that was running when Hexcast stopped is settled now
    ctx.include_router(trivia.router)
    ctx.on_shutdown(lambda: core.unregister_game("trivia"))
