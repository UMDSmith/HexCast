"""yt-dlp helpers plugin: a library with no routes or tab.

Clips and Music `require` it and import the helpers as `hexcast_plugins.ytdlp.lib`.
"""

from . import lib          # noqa: F401  (imported here so a broken lib fails this plugin at start-up)


def setup(ctx):
    pass
