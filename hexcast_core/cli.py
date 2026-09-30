"""Manage plugins from a terminal - for scripts, Docker builds and people who prefer it.

    python hexcast.py plugins list
    python hexcast.py plugins install twitch music games games_roulette
    python hexcast.py plugins install --all
    python hexcast.py plugins update [--all | ids ...]
    python hexcast.py plugins repair ids ...
    python hexcast.py plugins remove ids ...        (settings are kept)

Works on the files only; a running Hexcast picks the change up after a restart (or just
use the (+) tab, which does the same thing live).
"""

from __future__ import annotations

import argparse
import sys

from .host import PluginHost
from .installer import InstallError, Installer


def _say(msg: str) -> None:
    print("  " + msg, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hexcast plugins", description="Install and manage Hexcast plugins.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show what is installed and what can be installed")
    for name, help_ in (("install", "install plugins (and what they require)"),
                        ("update", "update installed plugins from the catalog"),
                        ("repair", "re-install a plugin's Python packages"),
                        ("remove", "remove plugins (their settings stay in config/)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("ids", nargs="*", metavar="id")
        if name in ("install", "update"):
            p.add_argument("--all", action="store_true", help="every plugin in the catalog" if name == "install"
                           else "every installed plugin")
        if name == "remove":
            p.add_argument("--with-dependents", action="store_true",
                           help="also remove the installed plugins that need them")
    args = ap.parse_args(argv)

    host = PluginHost(None)
    installer = Installer(host)
    # (no clean_trash() here: a running Hexcast may be in the middle of an install - it cleans up when it starts)
    if host.settings.problem:
        _say("[!] " + host.settings.problem)
    if host.catalog.remote_urls:
        host.catalog.refresh_remote(force=True)
        for url, why in host.catalog.errors.items():
            if url in host.catalog.remote_urls:
                _say(f"[!] catalog {url}: {why}")
    catalog = host.catalog.entries()
    installed = host.scan()

    if args.cmd == "list":
        rows = sorted(set(catalog) | set(installed))
        print(f"\n  {'id':<18}{'name':<22}{'installed':<12}{'catalog':<10}")
        for pid in rows:
            inst, entry = installed.get(pid), catalog.get(pid)
            name = (entry.manifest.name if entry else inst.manifest.name if inst and inst.manifest else "?")
            print(f"  {pid:<18}{name:<22}{(inst.version if inst else '-'):<12}"
                  f"{(entry.manifest.version if entry else '-'):<10}")
        print()
        return 0

    ids = list(args.ids)
    if getattr(args, "all", False):
        ids = sorted(e.id for e in catalog.values() if not e.manifest.hidden) if args.cmd == "install" \
            else sorted(installed)
    if not ids:
        ap.error(f"{args.cmd}: give at least one plugin id (or --all)")
    status = 0
    for pid in ids:
        try:
            if args.cmd == "install":
                installer.install(pid, _say)
            elif args.cmd == "update":
                installer.update(pid, _say)
            elif args.cmd == "repair":
                installer.ensure_requirements(pid, _say, force=True)
            elif args.cmd == "remove":
                deps = host.dependents(pid, installed)
                if deps and not args.with_dependents:
                    raise InstallError(f"{', '.join(deps)} need it - add --with-dependents to remove them too")
                for x in deps + [pid]:
                    installer.uninstall(x, _say)
        except InstallError as exc:
            _say(f"[!] {pid}: {exc}")
            status = 1
        except OSError as exc:                       # a locked file, a full disk ...
            _say(f"[!] {pid}: {exc.strerror or exc}")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
