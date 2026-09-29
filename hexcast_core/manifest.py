"""plugin.json - what a plugin says about itself.

Every plugin, installed or in the catalog, is a folder with a plugin.json next to
its code. The manifest is validated the same way everywhere, so a broken plugin
shows up as a readable error in the (+) tab instead of a crash at start-up.

Fields (all but id / name / version are optional):

    id            lowercase letters, digits and underscores; also the folder name
    name          what the tab and the store call it
    version       "1.2.0" - shown in the store; a newer catalog version offers an update
    api           the plugin API version it was written for (this Hexcast speaks API_VERSION)
    description   one or two sentences for the store card
    icon, color   an emoji and an accent colour for the store card / tab
    category      store grouping ("Integrations", "Overlays", "Games" ...)
    author, homepage
    parent        another plugin's id: this plugin is an add-on of it (a game of Games).
                  Add-ons are listed in their parent's own (+), not in the main one.
    requires      plugin ids that must be installed first (installed together, automatically)
    recommends    plugin ids that make this one better, offered but never forced
    hidden        a library: no tab, not listed in the store until something needs it
    entry         python module (inside the folder) that has setup(ctx); default "plugin"
    requirements  pip requirements file inside the folder; default "requirements.txt" if present
    nav           {label, href, order, color, key, status_url, status_js}: the top-bar tab
    help          {file, toc: [{id, label}]}: a fragment of the assembled /help page
    legacy_config config/ file names that show the plugin was already in use before Hexcast
                  had plugins (used once, to keep an old install's tabs on upgrade)
    order         sort position among the plugins of the same level
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API_VERSION = 1                                # bump on an incompatible change to ctx / manifest

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}([.+-][0-9A-Za-z.+-]*)?$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
MANIFEST_NAME = "plugin.json"


class ManifestError(ValueError):
    """The manifest is missing, unreadable or wrong. str(exc) is fit for the store card."""


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    api: int = API_VERSION
    description: str = ""
    icon: str = "🧩"
    color: str = ""
    category: str = "Plugins"
    author: str = ""
    homepage: str = ""
    parent: str | None = None
    requires: tuple[str, ...] = ()
    recommends: tuple[str, ...] = ()
    hidden: bool = False
    entry: str = "plugin"
    requirements: str | None = None
    nav: dict | None = None
    help: dict | None = None
    legacy_config: tuple[str, ...] = ()
    order: int = 100
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def package(self) -> str:
        """The python package name the plugin is imported as (its id)."""
        return self.id

    def public(self) -> dict:
        """The manifest as the store / API shows it."""
        return {
            "id": self.id, "name": self.name, "version": self.version, "description": self.description,
            "icon": self.icon, "color": self.color, "category": self.category, "author": self.author,
            "homepage": self.homepage, "parent": self.parent, "requires": list(self.requires),
            "recommends": list(self.recommends), "hidden": self.hidden, "order": self.order,
            "has_requirements": bool(self.requirements),
            "nav": ({k: v for k, v in self.nav.items()} if self.nav else None),
        }


def _text(d: dict, key: str, default: str = "", limit: int = 400) -> str:
    v = d.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        raise ManifestError(f"'{key}' must be text")
    return v.strip()[:limit]


def _ids(d: dict, key: str) -> tuple[str, ...]:
    v = d.get(key) or []
    if not isinstance(v, list):
        raise ManifestError(f"'{key}' must be a list of plugin ids")
    out: list[str] = []
    for item in v:
        if not isinstance(item, str) or not ID_RE.match(item):
            raise ManifestError(f"'{key}' has an invalid plugin id: {item!r}")
        if item not in out:
            out.append(item)
    return tuple(out)


def _rel_file(v: Any, what: str) -> str:
    """A path inside the plugin folder: relative, forward slashes, no '..'."""
    if not isinstance(v, str) or not v.strip():
        raise ManifestError(f"'{what}' must be a file name")
    s = v.strip().replace("\\", "/")
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s) or ".." in s.split("/"):
        raise ManifestError(f"'{what}' must stay inside the plugin folder: {v!r}")
    return s


def _url_path(v: Any, what: str) -> str:
    if not isinstance(v, str) or not v.startswith("/") or v.startswith("//") or "\n" in v:
        raise ManifestError(f"'{what}' must be a path on this server, like /twitch")
    return v


def _nav(raw: Any, name: str, color: str, order: int) -> dict | None:
    if raw in (None, False):
        return None
    if not isinstance(raw, dict):
        raise ManifestError("'nav' must be an object")
    nav: dict[str, Any] = {
        "label": _text(raw, "label", name, 24) or name,
        "href": _url_path(raw.get("href"), "nav.href"),
        "order": int(raw.get("order", order)),
        "color": _text(raw, "color", color, 16),
        "key": _text(raw, "key", "", 32),
    }
    if nav["color"] and not COLOR_RE.match(nav["color"]):
        raise ManifestError(f"'nav.color' must be a hex colour: {nav['color']!r}")
    if raw.get("status_url"):
        nav["status_url"] = _url_path(raw["status_url"], "nav.status_url")
    if raw.get("status_js"):
        nav["status_js"] = _rel_file(raw["status_js"], "nav.status_js")
    return nav


def _help(raw: Any) -> dict | None:
    if raw in (None, False):
        return None
    if not isinstance(raw, dict):
        raise ManifestError("'help' must be an object")
    toc = []
    for item in raw.get("toc") or []:
        if not isinstance(item, dict) or not re.match(r"^[A-Za-z][\w-]*$", str(item.get("id", ""))):
            raise ManifestError("'help.toc' entries need an id and a label")
        toc.append({"id": item["id"], "label": _text(item, "label", item["id"], 40)})
    return {"file": _rel_file(raw.get("file", "help.html"), "help.file"), "toc": toc}


def parse_manifest(data: Any, folder: Path | None = None) -> Manifest:
    """Validate a decoded plugin.json. `folder` (when given) must be named after the id
    and is where the requirements file is looked for."""
    if not isinstance(data, dict):
        raise ManifestError("plugin.json must be a JSON object")
    pid = data.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        raise ManifestError("'id' must be 2-32 lowercase letters, digits or underscores, starting with a letter")
    if folder is not None and folder.name != pid:
        raise ManifestError(f"the folder is called '{folder.name}' but the plugin id is '{pid}'")
    name = _text(data, "name", "", 40)
    if not name:
        raise ManifestError("'name' is required")
    version = _text(data, "version", "", 32)
    if not VERSION_RE.match(version):
        raise ManifestError(f"'version' must look like 1.2.0, not {version!r}")
    api = data.get("api", API_VERSION)
    if not isinstance(api, int) or isinstance(api, bool):
        raise ManifestError("'api' must be a whole number")
    if api != API_VERSION:
        which = "newer" if api > API_VERSION else "older"
        raise ManifestError(f"written for a {which} plugin API (v{api}); this Hexcast speaks v{API_VERSION}")
    parent = data.get("parent")
    if parent is not None and (not isinstance(parent, str) or not ID_RE.match(parent) or parent == pid):
        raise ManifestError("'parent' must be another plugin's id")
    requires = _ids(data, "requires")
    if parent and parent not in requires:
        requires = (parent,) + requires            # an add-on can never run without its parent
    if pid in requires:
        raise ManifestError("a plugin cannot require itself")
    color = _text(data, "color", "", 16)
    if color and not COLOR_RE.match(color):
        raise ManifestError(f"'color' must be a hex colour, not {color!r}")
    order = data.get("order", 100)
    if not isinstance(order, int) or isinstance(order, bool):
        raise ManifestError("'order' must be a whole number")
    entry = _text(data, "entry", "plugin", 60) or "plugin"
    if not re.match(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)*$", entry):
        raise ManifestError(f"'entry' must be a python module name, not {entry!r}")
    req = data.get("requirements")
    if req is None and folder is not None and (folder / "requirements.txt").is_file():
        req = "requirements.txt"
    req = _rel_file(req, "requirements") if req else None
    legacy = data.get("legacy_config") or []
    if not isinstance(legacy, list) or not all(isinstance(x, str) and x and "/" not in x and "\\" not in x
                                               for x in legacy):
        raise ManifestError("'legacy_config' must be a list of file names")
    return Manifest(
        id=pid, name=name, version=version, api=api,
        description=_text(data, "description", "", 600),
        icon=_text(data, "icon", "🧩", 8) or "🧩", color=color,
        category=_text(data, "category", "Plugins", 40) or "Plugins",
        author=_text(data, "author", "", 60), homepage=_text(data, "homepage", "", 200),
        parent=parent, requires=requires, recommends=_ids(data, "recommends"),
        hidden=bool(data.get("hidden", False)), entry=entry, requirements=req,
        nav=_nav(data.get("nav"), name, color, order), help=_help(data.get("help")),
        legacy_config=tuple(legacy), order=order, raw=data,
    )


def load_manifest(folder: Path) -> Manifest:
    """Read and validate <folder>/plugin.json."""
    path = folder / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"no {MANIFEST_NAME} in {folder.name}/") from None
    except (OSError, ValueError) as exc:
        raise ManifestError(f"{MANIFEST_NAME} is not readable JSON: {exc}") from None
    return parse_manifest(data, folder)
