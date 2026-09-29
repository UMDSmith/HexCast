"""Installing, updating and removing plugins.

Everything here is plain blocking code (file copies and pip), so the web API runs it in
a worker thread and the command line just calls it. Starting and stopping the plugin in
the running app is the host's job and stays out of this file.

Layout on disk:

    catalog/<id>/            what can be installed (ships with Hexcast)
    plugins/<id>/            an installed copy - this is what runs
    plugins/<id>/.hexcast-plugin.json   where it came from, and the digest it was copied at
    plugins/.trash/          removed folders waiting to be deleted (Windows may hold files open)
    plugins/.swap/           the previous copy while an update swaps in the new one
    plugins/.removed/        hand-made plugins that were removed - nothing else has a copy of them

An install or update never touches what is installed until everything the new copy needs
(its files, its Python packages) is ready; the last step is two renames, and if the second
fails the first is undone.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import requirements
from .catalog import IGNORE_NAMES, IGNORE_SUFFIXES, CatalogEntry
from .host import META_NAME, PluginHost
from .manifest import MANIFEST_NAME, ManifestError, load_manifest

Log = Callable[[str], None]

DOWNLOAD_MAX = 50 * 1024 * 1024
UNZIPPED_MAX = 200 * 1024 * 1024
FILES_MAX = 5000
PIP_SECONDS = 20 * 60                 # a stalled package index must not hold the install queue for ever
MOVE_RETRY_SECONDS = 0.15             # Windows: antivirus / indexers hold new files for a moment - wait and retry


class InstallError(RuntimeError):
    """Something went wrong while installing; str(exc) is fit to show a person."""


@dataclass
class Prepared:
    """New copies that are built and have their Python packages, but are not swapped in yet:
    [(plugin id, scratch root, the new copy)], dependencies first."""
    items: list[tuple[str, Path, Path]] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [pid for pid, _, _ in self.items]

    def discard(self) -> None:
        for _, root, _ in self.items:
            shutil.rmtree(root, ignore_errors=True)
        self.items = []


def _noop(_: str) -> None:
    pass


def _ignore(_dir: str, names: list[str]) -> list[str]:
    return [n for n in names if n in IGNORE_NAMES or n.endswith(IGNORE_SUFFIXES)]


class Installer:
    def __init__(self, host: PluginHost) -> None:
        self.host = host
        self.plugins_dir = host.plugins_dir
        self.trash_dir = self.plugins_dir / ".trash"
        self.swap_dir = self.plugins_dir / ".swap"
        self.removed_dir = self.plugins_dir / ".removed"

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

    def install(self, pid: str, log: Log = _noop, *, force: bool = False, packages: bool = True) -> list[str]:
        """Copy `pid` (and what it requires) into plugins/ and get their packages installed.
        Returns the ids that were copied in, dependencies first. Does NOT start them.
        If a package cannot be installed nothing of that plugin is left on disk.
        `packages=False` only copies: the host then finds the missing packages and restores
        them in the background (what the upgrade migration does, so start-up never waits on pip)."""
        here = self.host.scan().get(pid)
        if here is not None and here.manifest is not None and not force:
            log(f"{pid} is already installed")
            return []
        plan = self.plan_install(pid)
        done: list[str] = []
        for entry in plan:
            log(f"Installing {entry.manifest.name} {entry.manifest.version} ...")
            if packages:
                self.install_entry(entry, log)
            else:
                self.copy_in(entry, log)
            done.append(entry.id)
        return done

    def update(self, pid: str, log: Log = _noop) -> list[str]:
        """Replace the installed copy with the catalog's. Keeps the enabled/disabled choice
        and every setting (settings live in config/, not in the plugin folder). If the new
        version cannot be prepared (a package will not install ...) the old one stays as it was."""
        return self.commit(self.prepare_update(pid, log), log)

    def prepare_update(self, pid: str, log: Log = _noop) -> Prepared:
        """Everything an update needs - the new files and their packages - WITHOUT touching the
        installed copy, so the plugin can keep running until commit() swaps it."""
        entry = self.host.catalog.get(pid)
        if entry is None:
            raise InstallError(f"'{pid}' is not in the catalog any more")
        prepared = Prepared()
        try:
            # anything new that the updated version requires comes first
            for dep in self.plan_install_missing(entry):
                log(f"Installing {dep.manifest.name} (needed by the new version) ...")
                prepared.items.append(self._prepare(dep, log))
            log(f"Updating {entry.manifest.name} to {entry.manifest.version} ...")
            prepared.items.append(self._prepare(entry, log))
        except BaseException:
            prepared.discard()
            raise
        return prepared

    def commit(self, prepared: Prepared, log: Log = _noop) -> list[str]:
        """Swap prepared copies in (quick: renames). Returns the ids swapped."""
        done: list[str] = []
        try:
            for pid, _root, stage in prepared.items:
                self._swap_in(stage, pid, log)
                done.append(pid)
        finally:
            prepared.discard()
        return done

    def plan_install_missing(self, entry: CatalogEntry) -> list[CatalogEntry]:
        """The plugins `entry` requires that are not installed yet (what an update must add
        first). Ones that are already there are left alone - and running."""
        installed = self.host.scan()
        out: list[CatalogEntry] = []
        for dep in entry.manifest.requires:
            here = installed.get(dep)
            if here is not None and here.manifest is not None:
                continue
            for e in self.plan_install(dep):
                if all(x.id != e.id for x in out):
                    out.append(e)
        return out

    def install_entry(self, entry: CatalogEntry, log: Log = _noop) -> Path:
        """Build the new copy next to the old one, get its Python packages, and only then swap
        it in. Any failure leaves what was installed before exactly as it was."""
        prepared = Prepared([self._prepare(entry, log)])
        try:
            return self._swap_in(prepared.items[0][2], entry.id, log)
        finally:
            prepared.discard()

    def _prepare(self, entry: CatalogEntry, log: Log) -> tuple[str, Path, Path]:
        stage_root, stage = self._build_stage(entry, log)
        try:
            req = self._staged_requirements(stage)
            if req is not None:
                self._pip_install(entry.id, req, log)
        except BaseException:
            shutil.rmtree(stage_root, ignore_errors=True)
            raise
        return entry.id, stage_root, stage

    def copy_in(self, entry: CatalogEntry, log: Log = _noop) -> Path:
        """Put the catalog entry's files at plugins/<id>/, replacing what was there (no pip)."""
        stage_root, stage = self._build_stage(entry, log)
        try:
            return self._swap_in(stage, entry.id, log)
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)

    def _build_stage(self, entry: CatalogEntry, log: Log) -> tuple[Path, Path]:
        """A complete, validated copy of the entry in a scratch folder -> (scratch root, the copy)."""
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
        except BaseException:
            shutil.rmtree(stage_root, ignore_errors=True)
            raise
        return stage_root, stage

    @staticmethod
    def _staged_requirements(stage: Path) -> Path | None:
        try:
            m = load_manifest(stage)
        except ManifestError:
            return None
        if not m.requirements:
            return None
        req = stage / m.requirements
        return req if req.is_file() else None

    def _swap_in(self, stage: Path, pid: str, log: Log = _noop) -> Path:
        """plugins/<pid> := stage. The old copy is parked in .swap/ first and put back if the
        new one cannot be moved into place (an antivirus scanner or indexer may hold the fresh
        files on Windows for a moment)."""
        dest = self.plugins_dir / pid
        parked: Path | None = None
        if dest.exists():
            parked = self._park(dest, self.swap_dir)
        try:
            self._move(stage, dest)
        except OSError as exc:
            if parked is not None:
                try:
                    self._move(parked, dest)
                    parked = None
                except OSError:
                    log(f"Could not put the previous version of {pid} back: {parked}")
            raise InstallError(f"could not move the new {pid}/ into place ({exc.strerror or exc}) - "
                               "close whatever is using the plugins folder and try again") from None
        if parked is not None:
            shutil.rmtree(parked, ignore_errors=True)
        return dest

    @staticmethod
    def _move(src: Path, dst: Path) -> None:
        for attempt in range(6):
            try:
                os.replace(src, dst)
                return
            except OSError:
                if attempt == 5:
                    raise
                time.sleep(MOVE_RETRY_SECONDS * (attempt + 1))

    def _park(self, folder: Path, root: Path) -> Path:
        """Move `folder` into root/<name>-<time> (retrying while Windows holds a file open)."""
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{folder.name}-{int(time.time() * 1000)}"
        try:
            self._move(folder, target)
        except OSError:
            raise InstallError(f"could not move {folder.name}/ out of the way - close whatever is using "
                               "its files and try again") from None
        return target

    # ---- packages ----------------------------------------------------------------------

    def ensure_requirements(self, pid: str, log: Log = _noop, *, force: bool = False) -> None:
        """pip install the installed plugin's requirements.txt - unless this Python already has them."""
        inst = self.host.scan().get(pid)
        if inst is None or inst.manifest is None or not inst.manifest.requirements:
            return
        req_file = inst.folder / inst.manifest.requirements
        if not req_file.is_file():
            return
        self._pip_install(pid, req_file, log, force=force)

    def _pip_install(self, pid: str, req_file: Path, log: Log, *, force: bool = False) -> None:
        if not force and requirements.deps_ok(pid, req_file):
            log("Python packages already in place")
            return
        log("Installing Python packages (this can take a minute) ...")
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
               "--timeout", "30", "-r", str(req_file)]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as exc:
            raise InstallError(f"could not run pip: {exc}") from None
        assert proc.stdout is not None
        timed_out = threading.Event()

        def stop() -> None:
            timed_out.set()
            try:
                proc.kill()
            except OSError:
                pass

        watchdog = threading.Timer(PIP_SECONDS, stop)
        watchdog.daemon = True
        watchdog.start()
        tail: list[str] = []
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    log(line)
                    tail.append(line)
                    del tail[:-6]
            code = proc.wait()
        finally:
            watchdog.cancel()
        if timed_out.is_set():
            raise InstallError("pip took too long and was stopped - check the internet connection and try again")
        if code != 0:
            hint = next((ln for ln in reversed(tail) if "ERROR" in ln), tail[-1] if tail else "")
            raise InstallError("pip could not install the packages" + (f": {hint}" if hint else "")
                               + " - check the internet connection and try again")
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
                self.install_entry(entry, log)
                done.append(pid)
        return done

    # ---- remove ------------------------------------------------------------------------

    def uninstall(self, pid: str, log: Log = _noop) -> str | None:
        """Delete plugins/<id>/. Settings and secrets in config/ are kept on purpose: putting
        the plugin back brings everything back. Callers stop the plugin first.

        A plugin that did not come from a catalog (one somebody wrote or copied in by hand) has
        no other copy, so its folder is moved to plugins/.removed/ instead of being deleted -
        that path is returned."""
        dest = self.plugins_dir / pid
        if not dest.is_dir():
            raise InstallError(f"'{pid}' is not installed")
        try:
            meta = json.loads((dest / META_NAME).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            meta = {}
        has_copy_elsewhere = isinstance(meta, dict) and bool(meta.get("source"))
        kept: str | None = None
        log(f"Removing {pid} ...")
        if has_copy_elsewhere:
            self._trash(dest)
        else:
            target = self._park(dest, self.removed_dir)
            try:
                kept = os.path.relpath(target, self.plugins_dir.parent)
            except ValueError:                                  # another drive (Windows)
                kept = str(target)
            log(f"{pid} was not installed from a catalog: its files were kept in {kept}")
        requirements.forget(pid)
        self.host.settings.set_disabled(pid, False)
        return kept

    def clean_trash(self) -> None:
        """At start-up: delete what earlier removals could not (files Windows had open), the
        half-built folders of an install that was interrupted, and finish an interrupted update
        (the previous copy is put back if the new one never arrived). Never run this while
        another Hexcast process may be installing something."""
        if self.swap_dir.is_dir():
            for parked in sorted(self.swap_dir.iterdir()):
                pid = parked.name.rsplit("-", 1)[0]
                if parked.is_dir() and not (self.plugins_dir / pid).exists():
                    try:
                        os.replace(parked, self.plugins_dir / pid)      # the update died half-way: undo it
                        continue
                    except OSError:
                        pass
                shutil.rmtree(parked, ignore_errors=True)
            try:
                self.swap_dir.rmdir()
            except OSError:
                pass
        shutil.rmtree(self.trash_dir, ignore_errors=True)
        if self.plugins_dir.is_dir():
            for leftover in self.plugins_dir.glob(".stage-*"):
                shutil.rmtree(leftover, ignore_errors=True)

    def _trash(self, folder: Path) -> None:
        """Move a folder out of the way, then try to delete it. If Windows still has a file
        open the leftovers sit in .trash/ until the next start - the plugin is gone either way."""
        target = self._park(folder, self.trash_dir)
        shutil.rmtree(target, ignore_errors=True)

    # ---- downloads ---------------------------------------------------------------------

    def _download(self, entry: CatalogEntry, stage: Path, log: Log) -> None:
        import httpx
        assert entry.url
        if not entry.sha256:
            raise InstallError("the plugin index gives no checksum (sha256) for this download - not installed")
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
            if h.hexdigest() != entry.sha256:
                raise InstallError("the download does not match the checksum in the plugin index - not installed")
            safe_extract(Path(tmp), stage)
        except httpx.HTTPError as exc:
            raise InstallError(f"download failed: {exc}") from None
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _clean_parts(name: str) -> list[str]:
    """The path parts of an archive member, or InstallError if it could point anywhere but
    down into the destination: absolute, drive letters / alternate streams (':'), '..', '.',
    empty parts ('a//b', '/a', 'a/\\b'), control characters."""
    parts = name.replace("\\", "/").split("/")
    for p in parts:
        if p in ("", ".", "..") or ":" in p or any(ord(c) < 32 for c in p):
            raise InstallError(f"the archive has an unsafe path: {name!r}")
    return parts


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
        for i in infos:
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise InstallError("the archive contains a symbolic link")
        files = [i for i in infos if not i.is_dir()]
        parts_of = {i.filename: _clean_parts(i.filename) for i in files}
        names = ["/".join(p) for p in parts_of.values()]
        strip = 0
        if MANIFEST_NAME not in names:
            tops = {p[0] for p in parts_of.values()}
            if len(tops) == 1 and f"{next(iter(tops))}/{MANIFEST_NAME}" in names:
                strip = 1                           # everything lives in one wrapper folder: drop it
            else:
                raise InstallError(f"the archive has no {MANIFEST_NAME}")
        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        targets: list[tuple[zipfile.ZipInfo, Path]] = []
        for i in files:
            rel = parts_of[i.filename][strip:]
            if not rel:
                continue
            target = root.joinpath(*rel)
            if root not in target.resolve().parents:       # belt and braces: whatever the names said
                raise InstallError(f"the archive has an unsafe path: {i.filename!r}")
            targets.append((i, target))
        for i, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(i) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
