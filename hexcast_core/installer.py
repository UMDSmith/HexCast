"""Installing, updating and removing plugins.

Everything here is plain blocking code (file copies and pip), so the web API runs it in
a worker thread and the command line just calls it. Starting and stopping the plugin in
the running app is the host's job and stays out of this file.

Layout on disk:

    catalog/<id>/            what can be installed (ships with Hexcast)
    plugins/<id>/            an installed copy - this is what runs
    plugins/<id>/.hexcast-plugin.json   where it came from, and the digest it was copied at
    plugins/.trash/          folders waiting to be deleted (Windows may hold files open)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable

from . import requirements
from .catalog import IGNORE_NAMES, IGNORE_SUFFIXES, CatalogEntry
from .host import META_NAME, Installed, PluginHost
from .manifest import MANIFEST_NAME, ManifestError, load_manifest

Log = Callable[[str], None]

DOWNLOAD_MAX = 50 * 1024 * 1024
UNZIPPED_MAX = 200 * 1024 * 1024
FILES_MAX = 5000


class InstallError(RuntimeError):
    """Something went wrong while installing; str(exc) is fit to show a person."""


def _noop(_: str) -> None:
    pass


def _ignore(_dir: str, names: list[str]) -> list[str]:
    return [n for n in names if n in IGNORE_NAMES or n.endswith(IGNORE_SUFFIXES)]


class Installer:
    def __init__(self, host: PluginHost) -> None:
        self.host = host
        self.plugins_dir = host.plugins_dir
        self.trash_dir = self.plugins_dir / ".trash"

    # ---- planning ------------------------------------------------------------------

    def plan_install(self, pid: str) -> list[CatalogEntry]:
        """What has to be copied in to get `pid` running: the plugins it requires that are
        not installed yet, then `pid` itself, dependencies first."""
        entries = self.host.catalog.entries()
        installed = self.host.scan()
        plan: list[CatalogEntry] = []

        def visit(x: str, stack: tuple[str, ...]) -> None:
            if x in stack:
                raise InstallError("circular requirement: " + " -> ".join(stack + (x,)))
            here = installed.get(x)
            if here is not None and here.manifest is not None and x != pid:
                return                                   # a dependency that is already there
            entry = entries.get(x)
            if entry is None:
                if x == pid:
                    raise InstallError(f"'{x}' is not in the catalog")
                raise InstallError(f"'{pid}' needs '{x}', which is not in the catalog")
            for dep in entry.manifest.requires:
                visit(dep, stack + (x,))
            if all(e.id != x for e in plan):
                plan.append(entry)

        visit(pid, ())
        return plan

    # ---- install / update ------------------------------------------------------------

    def install(self, pid: str, log: Log = _noop, *, force: bool = False) -> list[str]:
        """Copy `pid` (and what it requires) into plugins/ and get their packages installed.
        Returns the ids that were copied in, dependencies first. Does NOT start them."""
        here = self.host.scan().get(pid)
        if here is not None and here.manifest is not None and not force:
            log(f"{pid} is already installed")
            return []
        plan = self.plan_install(pid)
        done: list[str] = []
        for entry in plan:
            log(f"Installing {entry.manifest.name} {entry.manifest.version} ...")
            self.copy_in(entry, log)
            done.append(entry.id)
            self.ensure_requirements(entry.id, log)
        return done

    def update(self, pid: str, log: Log = _noop) -> list[str]:
        """Replace the installed copy with the catalog's. Keeps the enabled/disabled choice
        and every setting (settings live in config/, not in the plugin folder)."""
        entry = self.host.catalog.get(pid)
        if entry is None:
            raise InstallError(f"'{pid}' is not in the catalog any more")
        done: list[str] = []
        # anything new that the updated version requires comes first
        for dep in self.plan_install_missing(entry):
            log(f"Installing {dep.manifest.name} (needed by the new version) ...")
            self.copy_in(dep, log)
            self.ensure_requirements(dep.id, log)
            done.append(dep.id)
        log(f"Updating {entry.manifest.name} to {entry.manifest.version} ...")
        self.copy_in(entry, log)
        self.ensure_requirements(pid, log)
        done.append(pid)
        return done

    def plan_install_missing(self, entry: CatalogEntry) -> list[CatalogEntry]:
        out: list[CatalogEntry] = []
        for dep in entry.manifest.requires:
            for e in self.plan_install(dep):
                if all(x.id != e.id for x in out):
                    out.append(e)
        return out

    def copy_in(self, entry: CatalogEntry, log: Log = _noop) -> Path:
        """Put the catalog entry's files at plugins/<id>/, replacing what was there. The new
        copy is built next to it first, so a failure leaves the old one untouched."""
        pid = entry.id
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        stage_root = Path(tempfile.mkdtemp(prefix=".stage-", dir=str(self.plugins_dir)))
        stage = stage_root / pid                 # named like the plugin, so its manifest validates
        try:
            if entry.folder is not None:
                shutil.copytree(entry.folder, stage, ignore=_ignore)
            elif entry.url:
                self._download(entry, stage, log)
            else:
                raise InstallError(f"'{pid}' has nothing to install from")
            try:
                m = load_manifest(stage)
            except ManifestError as exc:
                raise InstallError(f"'{pid}' is not a valid plugin: {exc}") from None
            meta = {"id": pid, "version": m.version, "source": entry.source,
                    "content_hash": entry.content_hash, "installed_at": round(time.time(), 3)}
            (stage / META_NAME).write_text(json.dumps(meta, indent=1), encoding="utf-8")
            dest = self.plugins_dir / pid
            if dest.exists():
                self._trash(dest)
            os.replace(stage, dest)
            return dest
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    def ensure_requirements(self, pid: str, log: Log = _noop, *, force: bool = False) -> None:
        """pip install the plugin's requirements.txt - unless this Python already has them."""
        inst = self.host.scan().get(pid)
        if inst is None or inst.manifest is None or not inst.manifest.requirements:
            return
        req_file = inst.folder / inst.manifest.requirements
        if not req_file.is_file():
            return
        if not force and requirements.deps_ok(pid, req_file):
            log("Python packages already in place")
            return
        log("Installing Python packages (this can take a minute) ...")
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req_file)]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1)
        except OSError as exc:
            raise InstallError(f"could not run pip: {exc}") from None
        assert proc.stdout is not None
        tail: list[str] = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(line)
                tail.append(line)
                del tail[:-6]
        if proc.wait() != 0:
            hint = next((ln for ln in reversed(tail) if "ERROR" in ln), tail[-1] if tail else "")
            raise InstallError("pip could not install the packages" + (f": {hint}" if hint else "")
                               + " - check the internet connection and try Repair")
        requirements.mark_installed(pid, req_file.read_text(encoding="utf-8"))
        log("Python packages installed")

    def sync_from_catalog(self, log: Log = _noop) -> list[str]:
        """Developer mode: re-copy every installed bundled plugin whose catalog files changed."""
        catalog = self.host.catalog.bundled()
        done = []
        for pid, inst in self.host.scan().items():
            entry = catalog.get(pid)
            if entry and inst.meta.get("source") == "bundled" and inst.meta.get("content_hash") != entry.content_hash:
                log(f"catalog/{pid} changed - re-installing")
                self.copy_in(entry, log)
                self.ensure_requirements(pid, log)
                done.append(pid)
        return done

    # ---- remove ------------------------------------------------------------------------

    def uninstall(self, pid: str, log: Log = _noop) -> None:
        """Delete plugins/<id>/. Settings and secrets in config/ are kept on purpose: putting
        the plugin back brings everything back. Callers stop the plugin first."""
        dest = self.plugins_dir / pid
        if not dest.is_dir():
            raise InstallError(f"'{pid}' is not installed")
        log(f"Removing {pid} ...")
        self._trash(dest)
        requirements.forget(pid)
        self.host.settings.set_disabled(pid, False)

    def clean_trash(self) -> None:
        """Delete what earlier removals could not (files Windows had open) and the half-built
        folders of an install that was interrupted."""
        shutil.rmtree(self.trash_dir, ignore_errors=True)
        if self.plugins_dir.is_dir():
            for leftover in self.plugins_dir.glob(".stage-*"):
                shutil.rmtree(leftover, ignore_errors=True)

    def _trash(self, folder: Path) -> None:
        """Move a folder out of the way, then try to delete it. If Windows still has a file
        open the leftovers sit in .trash/ until the next start - the plugin is gone either way."""
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        target = self.trash_dir / f"{folder.name}-{int(time.time() * 1000)}"
        for attempt in range(6):
            try:
                os.replace(folder, target)
                break
            except OSError:
                if attempt == 5:
                    raise InstallError(f"could not move {folder.name}/ out of the way - close whatever is using "
                                       "its files and try again") from None
                time.sleep(0.15 * (attempt + 1))
        shutil.rmtree(target, ignore_errors=True)

    # ---- downloads ---------------------------------------------------------------------

    def _download(self, entry: CatalogEntry, stage: Path, log: Log) -> None:
        import httpx
        assert entry.url
        log(f"Downloading {entry.url} ...")
        fd, tmp = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        try:
            h = hashlib.sha256()
            size = 0
            with httpx.stream("GET", entry.url, follow_redirects=True, timeout=30) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(65536):
                        size += len(chunk)
                        if size > DOWNLOAD_MAX:
                            raise InstallError("the download is far larger than a plugin should be - stopped")
                        h.update(chunk)
                        f.write(chunk)
            if entry.sha256 and h.hexdigest() != entry.sha256:
                raise InstallError("the download does not match the checksum in the plugin index - not installed")
            if not entry.sha256:
                log("Warning: the index gave no checksum for this download")
            safe_extract(Path(tmp), stage)
        except httpx.HTTPError as exc:
            raise InstallError(f"download failed: {exc}") from None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


def safe_extract(zip_path: Path, dest: Path) -> None:
    """Unzip a plugin archive into `dest`, refusing anything that could write outside it.
    The plugin may sit at the archive's root or inside one top-level folder."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise InstallError("the download is not a zip file") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > FILES_MAX or sum(i.file_size for i in infos) > UNZIPPED_MAX:
            raise InstallError("the archive is far larger than a plugin should be")
        names = [i.filename for i in infos if not i.is_dir()]
        for n in names:
            parts = n.replace("\\", "/").split("/")
            if n.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
                raise InstallError(f"the archive has an unsafe path: {n!r}")
        for i in infos:
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise InstallError("the archive contains a symbolic link")
        prefix = ""
        if MANIFEST_NAME not in names:
            tops = {n.replace("\\", "/").split("/", 1)[0] for n in names}
            if len(tops) == 1 and f"{next(iter(tops))}/{MANIFEST_NAME}" in [n.replace("\\", "/") for n in names]:
                prefix = next(iter(tops)) + "/"
            else:
                raise InstallError(f"the archive has no {MANIFEST_NAME}")
        dest.mkdir(parents=True, exist_ok=True)
        for i in infos:
            name = i.filename.replace("\\", "/")
            if i.is_dir() or not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(i) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
