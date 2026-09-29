"""The catalog: every plugin that can be installed from the (+) tab.

Two kinds of source, both optional:

  * the bundled catalog - the `catalog/` folder that ships with Hexcast, one folder
    per plugin. Works offline and with a plain ZIP download.
  * remote catalogs - an index.json somewhere on the web (see docs/plugins.md). They are
    only used when their URL is listed under "catalogs" in config/plugins.json.

Installing always COPIES a plugin out of the catalog into plugins/. Updating the catalog
(git pull) therefore never changes what is running until the user presses Update.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from .manifest import MANIFEST_NAME, Manifest, ManifestError, load_manifest, parse_manifest

# What is never part of a plugin's identity (build noise + our own install record).
IGNORE_NAMES = {"__pycache__", ".hexcast-plugin.json", ".DS_Store", "Thumbs.db"}
IGNORE_SUFFIXES = (".pyc", ".pyo")


def _files(folder: Path):
    """Every file of a plugin folder that counts, as (relative posix path, Path), sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_NAMES)
        for fn in sorted(filenames):
            if fn in IGNORE_NAMES or fn.endswith(IGNORE_SUFFIXES):
                continue
            p = Path(dirpath) / fn
            out.append((p.relative_to(folder).as_posix(), p))
    return out


_hash_cache: dict[str, tuple[tuple, str]] = {}


def content_hash(folder: Path) -> str:
    """A digest of a plugin's files: equal digests = the same plugin. Cached against a
    cheap (name, size, mtime) fingerprint so listing the catalog stays instant."""
    files = _files(folder)
    try:
        finger = tuple((rel, p.stat().st_size, p.stat().st_mtime_ns) for rel, p in files)
    except OSError:
        finger = ()
    key = str(folder)
    hit = _hash_cache.get(key)
    if hit and hit[0] == finger and finger:
        return hit[1]
    h = hashlib.sha256()
    for rel, p in files:
        h.update(rel.encode("utf-8") + b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
        h.update(b"\0")
    digest = h.hexdigest()[:20]
    _hash_cache[key] = (finger, digest)
    return digest


@dataclass
class CatalogEntry:
    manifest: Manifest
    source: str                       # "bundled" or the URL of the index it came from
    folder: Path | None = None        # bundled: where its files are
    url: str | None = None            # remote: the plugin's .zip
    sha256: str | None = None         # remote: the .zip's expected digest
    content_hash: str = ""            # what "changed since installed" is judged by

    @property
    def id(self) -> str:
        return self.manifest.id


class Catalog:
    def __init__(self, bundled_dir: Path, remote_urls: list[str] | None = None) -> None:
        self.bundled_dir = Path(bundled_dir)
        self.remote_urls = list(remote_urls or [])
        self.errors: dict[str, str] = {}          # folder / url -> why it was skipped
        self._remote: dict[str, CatalogEntry] = {}
        self._remote_at = 0.0

    # ---- bundled -----------------------------------------------------------------

    def bundled(self) -> dict[str, CatalogEntry]:
        out: dict[str, CatalogEntry] = {}
        errors: dict[str, str] = {}
        if self.bundled_dir.is_dir():
            for child in sorted(self.bundled_dir.iterdir()):
                if not child.is_dir() or child.name.startswith((".", "_")):
                    continue
                try:
                    m = load_manifest(child)
                except ManifestError as exc:
                    errors[child.name] = str(exc)
                    continue
                out[m.id] = CatalogEntry(m, "bundled", folder=child, content_hash=content_hash(child))
        self.errors = {**{k: v for k, v in self.errors.items() if k.startswith("http")}, **errors}
        return out

    # ---- remote ------------------------------------------------------------------

    def refresh_remote(self, timeout: float = 6.0, force: bool = False) -> None:
        """Fetch the configured remote indexes (at most once an hour unless forced).
        Blocking - call from a thread."""
        if not self.remote_urls:
            self._remote = {}
            return
        if not force and self._remote and time.time() - self._remote_at < 3600:
            return
        import httpx                                   # already a core dependency
        found: dict[str, CatalogEntry] = {}
        for url in self.remote_urls:
            try:
                r = httpx.get(url, timeout=timeout, follow_redirects=True)
                r.raise_for_status()
                index = r.json()
                if not isinstance(index, dict) or not isinstance(index.get("plugins"), list):
                    raise ValueError("not a Hexcast plugin index")
                for item in index["plugins"]:
                    m = parse_manifest(item.get("manifest"))
                    zip_url = urljoin(url, str(item.get("url") or ""))
                    if not zip_url.startswith(("http://", "https://")):
                        raise ValueError(f"{m.id}: the plugin needs an http(s) url")
                    sha = item.get("sha256")
                    found.setdefault(m.id, CatalogEntry(
                        m, url, url=zip_url, sha256=str(sha).lower() if sha else None,
                        content_hash=(str(sha).lower()[:20] if sha else m.version)))
                self.errors.pop(url, None)
            except Exception as exc:                   # offline, bad JSON, bad manifest ...
                self.errors[url] = f"{type(exc).__name__}: {exc}"
        self._remote = found
        self._remote_at = time.time()

    # ---- both --------------------------------------------------------------------

    def entries(self) -> dict[str, CatalogEntry]:
        """Bundled first: a remote plugin never shadows one that ships with Hexcast."""
        out = dict(self._remote)
        out.update(self.bundled())
        return out

    def get(self, pid: str) -> CatalogEntry | None:
        return self.entries().get(pid)
