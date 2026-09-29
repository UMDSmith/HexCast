"""Music plugin: hands the modules to Hexcast's plugin host."""

from hexcast_core import tasks

from . import localmusic, ytmusic


def setup(ctx):
    ytmusic.attach_ytm(ctx.app, ctx.port)      # also mounts the local-file player (localmusic)


async def teardown(ctx):
    # A song that is being held back for its video carries on instead of staying paused.
    hold = ytmusic._HOLD.get("event")
    if hold is not None:
        hold.set()
    # Music-video lookups that are still waiting on yt-dlp (the prefetch tasks that wait on
    # them end with them), and the yt-dlp children they started.
    await tasks.stop(list(ytmusic._VID_TASKS.values()))
    # localmusic's only background worker is the library-scan thread. It cannot be interrupted
    # mid-walk, but once the index root is cleared it ends as "superseded" instead of applying
    # and caching a scan nobody asked for.
    with localmusic.INDEX.lock:
        localmusic.INDEX.root = None
    await ytmusic.stop_ytm()                    # the loopback-capture thread + the YouTube Music Desktop feed
