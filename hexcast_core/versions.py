"""Versions and updates: what is installed, what is newer, and where the newer one comes from.

Two places can know about a newer version of an installed plugin:

  * the local catalog - the catalog/ folder, which a `git pull` (or a new ZIP) refreshes;
  * upstream - the project's GitHub repository. Hexcast asks it (cheaply, cached for an hour, in
    a background thread, silently when offline) for the plugin.json of each installed plugin, so
    people who never use git still learn that "Update to 1.1" exists. Applying such an update
    downloads the repository archive and unpacks ONLY catalog/<plugin id>/ from it.

Upstream is configured in config/plugins.json as "upstream": {"repo": "owner/name", "branch": "main"}
(the default is UMDSmith/hexcast, main) or `false` to never contact GitHub.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .catalog import CatalogEntry
from .manifest import Manifest, ManifestError, parse_manifest

DEFAULT_UPSTREAM = {"repo": "UMDSmith/hexcast", "branch": "main"}
RAW_TEMPLATE = "https://raw.githubusercontent.com/{repo}/{branch}/catalog/{id}/plugin.json"
ARCHIVE_TEMPLATE = "https://codeload.github.com/{repo}/zip/refs/heads/{branch}"

TTL_SECONDS = 3600            # how long a fetched version is trusted
RETRY_SECONDS = 300           # how soon to try again after the network failed
MANIFEST_MAX = 64 * 1024      # a plugin.json is a few hundred bytes; more than this is not one
FETCH_TIMEOUT = 5.0

_GITHUB_HOSTS = {"github.com", "raw.githubusercontent.com", "codeload.github.com"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,99}$")


# ---- version numbers -------------------------------------------------------------------

_NUMS = re.compile(r"^v?(\d+(?:\.\d+)*)")


def _pre_key(text: str) -> tuple:
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.-]", text) if p)


def version_key(version: str | None) -> tuple:
    """A sortable key: 1.10.0 > 1.9.0, 1.2 == 1.2.0, and 1.2.0-beta < 1.2.0. A string that is
    not a version at all sorts before every real one."""
    text = str(version or "").strip()
    m = _NUMS.match(text)
    if not m:
        return ((), -1, ())
    nums = [int(x) for x in m.group(1).split(".")]
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    rest = text[m.end():].split("+", 1)[0]
    if rest.startswith("-") and rest[1:]:
        return (tuple(nums), 0, _pre_key(rest[1:]))
    return (tuple(nums), 1, ())


def is_newer(candidate: str | None, than: str | None) -> bool:
    """True when `candidate` is a semver-greater version than `than`."""
    if not candidate or not _NUMS.match(str(candidate).strip()):
        return False
    return version_key(candidate) > version_key(than)


_X_Y_0 = re.compile(r"^(\d+)\.(\d+)\.0$")


def fmt_version(version: str | None) -> str:
    """How a version is shown to people: 'X.Y.0' as 'X.Y' (1.0.0 -> 1.0), anything else as is."""
    text = str(version or "").strip()
    m = _X_Y_0.match(text)
    return f"{m.group(1)}.{m.group(2)}" if m else text


# ---- configuration ----------------------------------------------------------------------

def check_url(url: str, what: str = "the address") -> None:
    """Upstream traffic is https to GitHub's hosts only (http only to this computer, for tests)."""
    u = urlparse(url)
    if u.scheme == "https" and u.hostname in _GITHUB_HOSTS:
        return
    if u.scheme == "http" and u.hostname in _LOCAL_HOSTS:
        return
    raise ValueError(f"{what} must be an https:// GitHub address (http:// is only accepted for localhost)")


def normalize_upstream(raw: Any) -> tuple[dict | None, str | None]:
    """config/plugins.json "upstream" -> (the settings to use or None when switched off, a
    warning when the value was unusable and the default is used instead).

    absent / true / {} -> the default repository;  false -> off;
    {"repo": "owner/name", "branch": "main"} -> that repository.
    Two more keys exist for testing against a local server: "raw_url" and "archive_url" (templates
    with {repo} {branch} {id}); like everything here they must be https GitHub or http localhost."""
    if raw is False:
        return None, None
    if raw is None or raw is True:
        return dict(DEFAULT_UPSTREAM), None
    if not isinstance(raw, dict):
        return dict(DEFAULT_UPSTREAM), '"upstream" must be {"repo": ..., "branch": ...} or false'
    repo = raw.get("repo", DEFAULT_UPSTREAM["repo"])
    branch = raw.get("branch", DEFAULT_UPSTREAM["branch"])
    out = {"repo": repo, "branch": branch}
    try:
        if not isinstance(repo, str) or not _REPO_RE.match(repo) or ".." in repo:
            raise ValueError('"repo" must look like owner/name')
        if not isinstance(branch, str) or not _BRANCH_RE.match(branch) or ".." in branch or branch.endswith("/"):
            raise ValueError('"branch" must be a branch name such as main')
        for key, sample in (("raw_url", RAW_TEMPLATE), ("archive_url", ARCHIVE_TEMPLATE)):
            if raw.get(key) is not None:
                tpl = raw[key]
                if not isinstance(tpl, str):
                    raise ValueError(f'"{key}" must be text')
                check_url(tpl.format(repo=repo, branch=branch, id="x"), f'"{key}"')
                out[key] = tpl
    except (ValueError, KeyError, IndexError) as exc:
        return dict(DEFAULT_UPSTREAM), f'"upstream" ignored ({exc}); using {DEFAULT_UPSTREAM["repo"]}'
    return out, None


# ---- the upstream source ------------------------------------------------------------------

class NotFound(Exception):
    """The server answered, but has no such file (a plugin that is not in the repository)."""


class Upstream:
    """The latest manifests of installed plugins, as published in the GitHub repository.

    Never blocks: `kick()` starts a background thread when something is due, `latest()` and
    `entry()` only read the cache. A network failure is silent (it only delays the next try)."""

    def __init__(self, config: dict | None) -> None:
        self.config = config
        self._manifests: dict[str, Manifest | None] = {}     # plugin id -> manifest (None: not in the repo)
        self._at: dict[str, float] = {}                      # plugin id -> when it was fetched
        self._retry_at = 0.0                                 # after a network failure: not before
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.checked_at = 0.0                                # when a round last finished

    @property
    def enabled(self) -> bool:
        return self.config is not None

    @property
    def repo(self) -> str:
        return (self.config or DEFAULT_UPSTREAM)["repo"]

    @property
    def branch(self) -> str:
        return (self.config or DEFAULT_UPSTREAM)["branch"]

    def _fill(self, template: str, pid: str = "") -> str:
        return template.format(repo=self.repo, branch=self.branch, id=pid)

    def raw_url(self, pid: str) -> str:
        return self._fill((self.config or {}).get("raw_url") or RAW_TEMPLATE, pid)

    def archive_url(self) -> str:
        return self._fill((self.config or {}).get("archive_url") or ARCHIVE_TEMPLATE)

    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}"

    def download_url(self) -> str:
        """Where a person gets the whole new Hexcast (the repository's ZIP)."""
        return f"https://github.com/{self.repo}/archive/refs/heads/{self.branch}.zip"

    # ---- reading the cache ----

    def manifest(self, pid: str) -> Manifest | None:
        return self._manifests.get(pid) if self.enabled else None

    def latest(self, pid: str) -> str | None:
        m = self.manifest(pid)
        return m.version if m else None

    def entry(self, pid: str) -> CatalogEntry | None:
        """The upstream copy as an installable entry (its files come from the repository archive)."""
        m = self.manifest(pid)
        if m is None:
            return None
        return CatalogEntry(m, "upstream", url=self.archive_url(), subdir=f"catalog/{pid}")

    # ---- refreshing ----

    def due(self, pids: list[str], now: float | None = None) -> list[str]:
        if not self.enabled:
            return []
        now = time.time() if now is None else now
        if now < self._retry_at:
            return []
        return [p for p in pids if now - self._at.get(p, 0.0) >= TTL_SECONDS]

    def kick(self, pids: list[str], force: bool = False) -> bool:
        """Start a background refresh if any of `pids` is due. Returns at once."""
        if not self.enabled or not pids:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            todo = list(pids) if force else self.due(list(pids))
            if not todo:
                return False
            t = threading.Thread(target=self.refresh, args=(todo, force), name="hexcast-upstream", daemon=True)
            self._thread = t
        t.start()
        return True

    def refresh(self, pids: list[str], force: bool = False) -> None:
        """Fetch the manifests of `pids` (blocking: call from a thread). Whatever goes wrong is
        remembered quietly and the old values stay."""
        if not self.enabled:
            return
        todo = list(pids) if force else self.due(list(pids))
        for pid in todo:
            try:
                body = self._get(self.raw_url(pid), MANIFEST_MAX, FETCH_TIMEOUT)
                try:
                    m = parse_manifest(json.loads(body.decode("utf-8-sig")))
                    if m.id != pid:
                        m = None
                except (ManifestError, ValueError, RecursionError):
                    m = None                                  # not a plugin we can read: ignore it
                self._manifests[pid] = m
                self._at[pid] = time.time()
                self.last_error = None
            except NotFound:
                self._manifests[pid] = None                   # a plugin that is not in the repository
                self._at[pid] = time.time()
            except Exception as exc:                          # offline, rate-limited, DNS ...: try later, say nothing
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._retry_at = time.time() + RETRY_SECONDS
                break
        self.checked_at = time.time()

    def _get(self, url: str, limit: int, timeout: float) -> bytes:
        """GET `url` (https GitHub / http localhost only, also after redirects), at most `limit` bytes."""
        import httpx
        check_url(url, "an upstream address")
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "Hexcast-update-check"}) as r:
            for hop in list(r.history) + [r]:
                check_url(str(hop.url), "an upstream address")
            if r.status_code == 404:
                raise NotFound(url)
            r.raise_for_status()
            body = bytearray()
            for chunk in r.iter_bytes(16384):
                body += chunk
                if len(body) > limit:
                    raise ValueError("the answer is far larger than expected")
            return bytes(body)


# ---- what an installed plugin could be updated to -------------------------------------------

@dataclass
class UpdateInfo:
    installed: str | None
    latest: str | None
    available: bool = False
    source: str | None = None                  # "catalog" | "upstream" when available
    entry: CatalogEntry | None = None          # what an update installs from

    def view(self) -> dict:
        return {"installed_version": self.installed, "latest_version": self.latest,
                "installed_label": fmt_version(self.installed) if self.installed else None,
                "latest_label": fmt_version(self.latest) if self.latest else None,
                "update_available": self.available, "update_source": self.source if self.available else None}


UPSTREAM_SOURCES = ("bundled", "upstream")      # plugins that came from the project itself (a hand-made one is never replaced)


def update_info(upstream: Upstream | None, inst, catalog_entry: CatalogEntry | None) -> UpdateInfo:
    """Is there something newer than the installed copy `inst`? Looks at the local catalog (its
    version, or - when the version was not bumped - a digest that differs) and at upstream (its
    version), and picks the newest; on a tie the local catalog wins (no download needed).
    Never offers a downgrade."""
    if inst is None:
        return UpdateInfo(None, catalog_entry.manifest.version if catalog_entry else None)
    installed = inst.version
    cat_ver = catalog_entry.manifest.version if catalog_entry else None
    up_entry = None
    if upstream is not None and upstream.enabled and (inst.meta.get("source") in UPSTREAM_SOURCES):
        up_entry = upstream.entry(inst.id)
    up_ver = up_entry.manifest.version if up_entry else None

    cat_newer = bool(catalog_entry and is_newer(cat_ver, installed))
    cat_differs = bool(catalog_entry and inst.meta.get("content_hash")
                       and inst.meta.get("content_hash") != catalog_entry.content_hash
                       and not is_newer(installed, cat_ver))          # (an older catalog is not an "update")
    up_newer = bool(up_entry and is_newer(up_ver, installed))

    latest = max([v for v in (installed, cat_ver, up_ver) if v], key=version_key)
    if up_newer and not (catalog_entry and not is_newer(up_ver, cat_ver)):
        return UpdateInfo(installed, up_ver, True, "upstream", up_entry)
    if cat_newer or cat_differs:
        return UpdateInfo(installed, cat_ver if cat_newer or cat_differs else latest, True, "catalog", catalog_entry)
    return UpdateInfo(installed, latest)
