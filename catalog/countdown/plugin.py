"""Countdown plugin: hands the module to Hexcast's plugin host."""

from . import countdown


def setup(ctx):
    countdown.attach_countdown(ctx.app, ctx.port)


def teardown(ctx):
    countdown._cancel_media_cues()             # pending "fire this clip at 0:10" timers
