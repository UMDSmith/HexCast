"""Upgrading from a Hexcast that had no plugins.

Before plugins, every integration shipped in the box. Someone who upgrades should not
wake up to a soundboard with all their tabs gone - so, once, the plugins whose settings
files already exist in config/ are installed for them. A fresh install has no such files
and starts with just the soundboard.

The `migrated` flag in config/plugins.json makes this a one-off: removing a plugin later
(its settings stay behind) never brings it back on the next start.
"""

from __future__ import annotations

from typing import Callable

from .host import PluginHost
from .installer import InstallError, Installer


def legacy_plugins(host: PluginHost) -> list[str]:
    """Bundled plugins whose old-style settings files are present, and that are not installed."""
    have = host.scan()
    found = []
    for pid, entry in host.catalog.bundled().items():
        if pid in have:
            continue
        if any((host.config_dir / name).exists() for name in entry.manifest.legacy_config):
            found.append(pid)
    return found


def run(host: PluginHost, installer: Installer, log: Callable[[str], None] = print) -> list[str]:
    """Install the plugins an existing setup was already using. Returns the ids installed.

    Only the files are copied here; a plugin whose Python packages are missing is restored
    in the background right after start-up (PluginService.repair_pending), so a slow or
    absent internet connection never delays the soundboard. One plugin failing does not stop
    the others; a plugin that failed can be installed from the + tab (its settings are kept)."""
    if host.settings.data.get("migrated"):
        return []
    if not host.catalog.bundled():
        log("  [!] the catalog/ folder is missing or empty, so the upgrade step was skipped - "
            "restore catalog/ (git pull, or unzip Hexcast again) and restart")
        return []                                   # try again next start: nothing was decided
    installed: list[str] = []
    wanted = legacy_plugins(host)
    if wanted:
        log("  Upgrading: keeping the tabs you already use as plugins - " + ", ".join(wanted))
    for pid in wanted:
        try:
            installed.extend(installer.install(pid, lambda s: None, packages=False))
        except Exception as exc:                    # InstallError, a locked file (Windows), a full disk ...
            reason = str(exc) if isinstance(exc, InstallError) else (getattr(exc, "strerror", None) or repr(exc))
            log(f"  [!] could not install {pid} during the upgrade: {reason} - install it from the + tab")
    host.settings.data["migrated"] = True
    host.settings.save()
    return installed
