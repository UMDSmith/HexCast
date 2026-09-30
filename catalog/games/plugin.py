"""Games plugin: the framework the individual games (add-ons) plug into."""

from . import core


def setup(ctx):
    core.attach_games(ctx.app, ctx.port)
    # Replay any coin movement a crash left only in a game's state file. Before any game is made,
    # and whichever game add-ons are installed (it reads every game's file in config/).
    core.recover_ledger()
