"""Where things live - one place, so no plugin has to guess.

Every folder can be moved with an environment variable; the defaults sit next to
hexcast.py. This module reads the variables once, at import time, exactly like the
integrations used to do on their own.
"""

from __future__ import annotations

import os
from pathlib import Path

# The Hexcast install folder (the one holding hexcast.py).
ROOT = Path(os.environ.get("HEXCAST_ROOT") or Path(__file__).resolve().parent.parent).resolve()

# Settings and secrets of the core and of every plugin. Never committed.
CONFIG_DIR = Path(os.environ.get("HEXCAST_CONFIG_DIR") or ROOT / "config")

# The media library (SOUNDBOARD_MEDIA_DIR is the variable hexcast.py always honoured).
MEDIA_DIR = Path(os.getenv("SOUNDBOARD_MEDIA_DIR", str(ROOT / "media"))).expanduser().resolve()

# Installed plugins live here (one folder each); the contents are git-ignored.
PLUGINS_DIR = Path(os.environ.get("HEXCAST_PLUGINS_DIR") or ROOT / "plugins")

# Plugins that ship with Hexcast and can be installed from the (+) tab.
CATALOG_DIR = Path(os.environ.get("HEXCAST_CATALOG_DIR") or ROOT / "catalog")

# The core's own web files (control panel, top bar, store page).
STATIC_DIR = ROOT / "static"
