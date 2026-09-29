"""Clips plugin: hands the module to Hexcast's plugin host."""

from hexcast_core import tasks

from . import clips
from .clips import play_shoutout        # noqa: F401  - the Twitch plugin's !so calls running_plugin("clips").play_shoutout(...)


def setup(ctx):
    clips.attach_clips(ctx.app, ctx.port)


async def teardown(ctx):
    # Background work first, so nothing new starts while the player is being stopped: the
    # start-up pass, metadata lookups and pre-downloads, loudness measurements, the shoutout
    # chain, an overlay re-sync - all started with a bare asyncio.create_task() and not tracked
    # by the module - and the yt-dlp / ffmpeg children they wait on. (A running "Update yt-dlp"
    # is a request, not a task of the module, and is left to finish rather than kill pip half-way.)
    await tasks.stop(tasks.in_module(clips))
    await clips.stop_clips()                # the "embed has finished" timer
    await clips.stop_playback()             # tell the overlay to stop; drops queued shoutouts
