"""Blackjack add-on: registers the game with the Games plugin."""

from hexcast_plugins.games import core

from . import blackjack


def setup(ctx):
    core.register_game(blackjack.BLACKJACK, {
        "plugin": ctx.id, "title": "Blackjack", "order": 70,
        "static_dir": ctx.static_dir, "static_url": ctx.static_url,
        "panel_js": "blackjack_panel.js", "overlay": blackjack.OVERLAY, "api": blackjack.API_DOC,
    })
    blackjack.BLACKJACK.resume()      # a table that was open when Hexcast stopped is settled now (stakes refunded)
    ctx.include_router(blackjack.router)
    ctx.on_shutdown(lambda: core.unregister_game("blackjack"))
