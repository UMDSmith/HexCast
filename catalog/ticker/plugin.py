"""Ticker plugin: hands the module to Hexcast's plugin host."""

from hexcast_core import tasks

from . import ticker


def setup(ctx):
    ticker.attach_ticker(ctx.app, ctx.port)


async def teardown(ctx):
    # ticker.ensure_started() starts the background loop (feeds-file watcher, expiry sweep,
    # URL-source poller) on the first request and keeps no handle to it, and every due URL
    # poll is a fire-and-forget task as well - so find them by the code they run. Left alone
    # they would outlive the plugin and keep rewriting config/ticker_feeds.json from stale state.
    await tasks.stop(tasks.running(ticker._background, ticker.poll_source))
    ticker._STARTED = False
