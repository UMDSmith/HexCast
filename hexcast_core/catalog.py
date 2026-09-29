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
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .manifest import MANIFEST_NAME, Manifest, ManifestError, load_manifest, parse_manifest

INDEX_MAX = 2 * 1024 * 1024            # a plugin index is a few kilobytes; more than this is not one
REFRESH_SECONDS = 3600                 # how long a fetched index is trusted
RETRY_SECONDS = 60                     # ... and how soon to try again after a failure
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _check_url(url: str, what: str) -> None:
    """Remote catalogs and their downloads travel over https (http only to this computer)."""
    u = urlparse(url)
    if u.scheme == "https" and u.hostname:
        return
    if u.scheme == "http" and u.hostname in _LOCAL_HOSTS:
        return
    raise ValueError(f"{what} must be an https:// address (http:// is only accepted for localhost)")


# What is never part of a plugin's identity (build noise + our own install record).
IGNORE_NAMES = {"__pycache__", ".hexcast-plugin.json", ".DS_Store", "Thumbs.db"}
IGNORE_SUFFIXES = (".pyc", ".pyo")


def _files(folder: Path) -> list[tuple[str, str, int, int]]:
    """Every file of a plugin folder that counts: (relative posix path, path, size, mtime_ns),
    a folder's own files first (sorted), then its sub-folders (sorted). One directory listing
    and one stat per file - this runs every few seconds while pages are open."""
    out: list[tuple[str, str, int, int]] = []

    def visit(directory: str, prefix: str) -> None:
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        subdirs = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in IGNORE_NAMES:
                        subdirs.append(e)
                elif e.name not in IGNORE_NAMES and not e.name.endswith(IGNORE_SUFFIXES) and e.is_file():
                    st = e.stat()
                    out.append((prefix + e.name, e.path, st.st_size, st.st_mtime_ns))
            except OSError:
                continue
        for e in subdirs:
            visit(e.path, prefix + e.name + "/")

    visit(str(folder), "")
    return out


_hash_cache: dict[str, tuple[tuple, str]] = {}


def content_hash(folder: Path) -> str:
    """A digest of a plugin's files: equal digests = the same plugin. Cached against a
    cheap (name, size, mtime) fingerprint so listing the catalog stays instant."""
    files = _files(folder)
    finger = tuple((rel, size, mtime) for rel, _path, size, mtime in files)
    key = str(folder)
    hit = _hash_cache.get(key)
    if hit and hit[0] == finger and finger:
        return hit[1]
    h = hashlib.sha256()
    for rel, path, _size, _mtime in files:
        h.update(rel.encode("utf-8") + b"\0")
        try:
            h.update(Path(path).read_bytes())
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
        self._by_url: dict[str, dict[str, CatalogEntry]] = {}     # index url -> its last good entries
        self._remote_at = 0.0                                     # when we last tried
        self._remote_ok = True                                    # ... and whether every index answered

    @property
    def _remote(self) -> dict[str, CatalogEntry]:
        """The entries of every configured index, the first index winning a clash."""
        out: dict[str, CatalogEntry] = {}
        for url in self.remote_urls:
            for pid, entry in self._by_url.get(url, {}).items():
                out.setdefault(pid, entry)
        return out

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
                    out[m.id] = CatalogEntry(m, "bundled", folder=child, content_hash=content_hash(child))
                except ManifestError as exc:
                    errors[child.name] = str(exc)
                except Exception as exc:                 # whatever else: still only this folder's problem
                    errors[child.name] = f"{type(exc).__name__}: {exc}"
        self.errors = {**{k: v for k, v in self.errors.items() if k.startswith("http")}, **errors}
        return out

    # ---- remote ------------------------------------------------------------------

    def refresh_remote(self, timeout: float = 6.0, force: bool = False) -> None:
        """Fetch the configured remote indexes (at most once an hour unless forced; a failed
        fetch is retried after a minute and meanwhile the last good entries stay). Blocking -
        call from a thread."""
        if not self.remote_urls:
            self._by_url = {}
            return
        wait = REFRESH_SECONDS if self._remote_ok else RETRY_SECONDS
        if not force and self._remote_at and time.time() - self._remote_at < wait:
            return
        import httpx                                   # already a core dependency
        self._by_url = {u: e for u, e in self._by_url.items() if u in self.remote_urls}
        ok = True
        for url in self.remote_urls:
            try:
                _check_url(url, "a catalog address")
                found: dict[str, CatalogEntry] = {}
                skipped: list[str] = []
                with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
                    r.raise_for_status()
                    _check_url(str(r.url), "a catalog address")
                    body = bytearray()
                    for chunk in r.iter_bytes(65536):
                        body += chunk
                        if len(body) > INDEX_MAX:
                            raise ValueError("the index is far larger than a plugin index should be")
                index = json.loads(bytes(body).decode("utf-8-sig"))
                if not isinstance(index, dict) or not isinstance(index.get("plugins"), list):
                    raise ValueError("not a Hexcast plugin index")
                for item in index["plugins"]:
                    try:
                        if not isinstance(item, dict):
                            raise ValueError("an entry is not an object")
                        m = parse_manifest(item.get("manifest"))
                        zip_url = urljoin(url, str(item.get("url") or ""))
                        _check_url(zip_url, f"{m.id}: the download address")
                        sha = str(item.get("sha256") or "")
                        if not _SHA256.match(sha):
                            raise ValueError(f"{m.id}: the index gives no sha256 checksum for the download")
                        found.setdefault(m.id, CatalogEntry(m, url, url=zip_url, sha256=sha.lower(),
                                                            content_hash=sha.lower()[:20]))
                    except (ManifestError, ValueError) as exc:
                        skipped.append(str(exc))
                self._by_url[url] = found
                if skipped:
                    self.errors[url] = f"{len(skipped)} plugin(s) skipped: " + "; ".join(skipped[:3])
                else:
                    self.errors.pop(url, None)
            except Exception as exc:                   # offline, bad JSON ... : keep what we had
                ok = False
                self.errors[url] = f"{type(exc).__name__}: {exc}"
        self._remote_ok = ok
        self._remote_at = time.time()

    # ---- both --------------------------------------------------------------------

    def entries(self) -> dict[str, CatalogEntry]:
        """Bundled first: a remote plugin never shadows one that ships with Hexcast."""
        out = dict(self._remote)
        out.update(self.bundled())
        return out

    def get(self, pid: str) -> CatalogEntry | None:
        return self.entries().get(pid)
