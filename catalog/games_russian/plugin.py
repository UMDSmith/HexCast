"""Russian Roulette add-on: registers the game with the Games plugin."""

from hexcast_plugins.games import core

from . import russian


def setup(ctx):
    core.register_game(russian.RUSSIAN, {
        "plugin": ctx.id, "title": "Russian Roulette", "order": 30,
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "russian_panel.js", "overlay": russian.OVERLAY, "api": russian.API_DOC,
    })
    russian.RUSSIAN.resume()      # a game that was running when Hexcast stopped is settled now
    ctx.include_router(russian.router)
    ctx.on_shutdown(lambda: core.unregister_game("russian"))
