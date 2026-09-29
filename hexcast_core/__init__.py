"""Hexcast's plugin system.

hexcast.py is the soundboard and this package. Everything else - Twitch, Music, Games and
so on - is a plugin: a folder with a plugin.json that is installed from the (+) tab.
See docs/plugins.md.
"""

from .api import ApiError, PluginService, build_router, render_help
from .host import PluginContext, PluginError, PluginHost, running_plugin
from .installer import InstallError, Installer
from .manifest import API_VERSION, Manifest, ManifestError

__all__ = ["API_VERSION", "ApiError", "PluginService", "InstallError", "Installer", "Manifest", "ManifestError", "PluginContext",
           "PluginError", "PluginHost", "build_router", "render_help", "running_plugin"]
