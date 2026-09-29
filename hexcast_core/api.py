"""The plugin web API: what the (+) tab, the top bar and the Games sub-store talk to.

    GET  /plugins                        the store page (the (+) tab)
    GET  /api/plugins                    what is installed / can be installed
                                         ?parent=games -> that plugin's add-ons; none -> top level
    GET  /api/plugins/nav                the top-bar tabs
    POST /api/plugins/{id}/install       -> {job}     (also installs what it requires)
    POST /api/plugins/{id}/update        -> {job}
    POST /api/plugins/{id}/repair        -> {job}     (re-install its Python packages)
    POST /api/plugins/{id}/uninstall     {cascade?}   (settings are kept)
    POST /api/plugins/{id}/enable | disable
    GET  /api/plugins/jobs/{job}         progress of an install / update / repair
    GET  /help                           the help page, assembled from the running plugins

Installing and updating run as background jobs (pip can take a while); the page polls
the job for its log. Only one job runs at a time.
"""

from __future__ import annotations

import asyncio
import functools
import html
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import requirements as reqs
from .host import PluginHost
from .installer import InstallError, Installer
from .manifest import ID_RE, Manifest

NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}
JOBS_KEPT = 30


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra


@dataclass
class Job:
    id: str
    title: str
    plugin: str
    state: str = "running"                  # running | done | error
    lines: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def log(self, line: str) -> None:
        self.lines.append(str(line)[:400])

    def view(self, since: int = 0) -> dict:
        return {"id": self.id, "title": self.title, "plugin": self.plugin, "state": self.state,
                "error": self.error, "result": self.result, "lines": self.lines[since:],
                "total": len(self.lines)}


def _needs_download(entry) -> bool:
    """Would installing this plugin have to pip-install anything (are its packages missing)?"""
    m = entry.manifest
    if not m.requirements:
        return False
    if entry.folder is None:
        return True                                   # remote plugin: cannot tell without unpacking it
    try:
        text = (entry.folder / m.requirements).read_text(encoding="utf-8")
    except OSError:
        return False
    return not all(reqs.satisfied(r) for r in reqs.parse(text))


def plugin_view(host: PluginHost, pid: str, catalog: dict, installed: dict) -> dict:
    """One plugin as the store shows it: catalog data + what is installed + what is running."""
    entry = catalog.get(pid)
    inst = installed.get(pid)
    m: Manifest | None = entry.manifest if entry else (inst.manifest if inst else None)
    out: dict[str, Any] = m.public() if m else {
        "id": pid, "name": pid, "version": "?", "description": "", "icon": "🧩", "color": "",
        "category": "Plugins", "parent": None, "requires": [], "recommends": [], "hidden": False,
        "order": 999, "has_requirements": False, "nav": None}
    is_installed = inst is not None
    out["installed"] = is_installed
    out["installed_version"] = inst.version if inst else None
    out["in_catalog"] = entry is not None
    out["source"] = (inst.meta.get("source") or "local") if inst else (entry.source if entry else "local")
    out["state"] = host.state(pid, inst) if inst else "available"
    out["enabled"] = is_installed and not host.settings.is_disabled(pid)
    out["running"] = pid in host.loaded
    out["error"] = (host.errors.get(pid) or (inst.error if inst else None)) if is_installed else None
    out["update_available"] = bool(inst and entry and inst.meta.get("content_hash")
                                   and inst.meta.get("content_hash") != entry.content_hash)
    out["latest_version"] = entry.manifest.version if entry else None
    out["needs_packages"] = bool(not is_installed and entry and _needs_download(entry))
    out["dependents"] = host.dependents(pid, installed) if is_installed else []

    def named(ids):
        """[{id, name, installed}] - so a card can say 'Also installs: yt-dlp helpers', not 'ytdlp'."""
        res = []
        for x in ids:
            other = catalog.get(x)
            nm = other.manifest.name if other else (installed[x].manifest.name if x in installed and installed[x].manifest else x)
            res.append({"id": x, "name": nm, "installed": x in installed})
        return res

    out["requires_info"] = named(out.get("requires") or [])
    out["recommends_info"] = named(out.get("recommends") or [])
    return out


class PluginService:
    """Everything the API does, as methods - the routes below are thin wrappers."""

    def __init__(self, host: PluginHost, installer: Installer, static_dir: Path) -> None:
        self.host = host
        self.installer = installer
        self.static_dir = Path(static_dir)
        self.jobs: dict[str, Job] = {}
        self.lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    # ---- jobs ----------------------------------------------------------------------

    def running_job(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.state == "running"), None)

    def _busy(self) -> None:
        job = self.running_job()
        if job is not None:
            raise ApiError("another install is running - wait for it to finish", 409, job=job.id)

    def _start(self, title: str, pid: str, work: Callable[[Job], Awaitable[dict | None]]) -> Job:
        job = Job(secrets.token_hex(4), title, pid)
        self.jobs[job.id] = job
        finished = [k for k, j in self.jobs.items() if j.state != "running"]
        for k in finished[:max(0, len(self.jobs) - JOBS_KEPT)]:
            self.jobs.pop(k, None)

        async def run() -> None:
            async with self.lock:
                try:
                    job.result = (await work(job)) or {}
                    job.state = "done"
                except InstallError as exc:
                    job.state, job.error = "error", str(exc)
                    job.log(f"Failed: {exc}")
                except Exception as exc:                       # never leave a job hanging
                    job.state, job.error = "error", f"{type(exc).__name__}: {exc}"
                    job.log(f"Failed: {job.error}")
                finally:
                    job.finished = time.time()
                    self.host.revision += 1

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def _start_missing(self, order: list[str], ids: list[str] | None = None) -> list[str]:
        """Start everything installed that should be running but is not (dependencies first).
        Returns the ids of `ids` that failed to start."""
        failed = []
        for x in order:
            if x not in self.host.loaded and not self.host.settings.is_disabled(x):
                if not self.host.load(x) and ids and x in ids:
                    failed.append(x)
        return failed

    def _known(self, pid: str) -> None:
        if not ID_RE.match(pid) or not (pid in self.host.scan() or pid in self.host.catalog.entries()):
            raise ApiError("no such plugin", 404)

    # ---- listing -------------------------------------------------------------------

    def updates(self) -> list[str]:
        """Ids of installed plugins whose catalog copy differs from what was installed. Updates
        are manual - a git pull changes the catalog, never what is running - so the top bar
        puts a dot on the + tab to say there is something to press Update on."""
        catalog = self.host.catalog.entries()
        out = []
        for pid, inst in self.host.scan().items():
            entry = catalog.get(pid)
            if entry and inst.meta.get("content_hash") and inst.meta["content_hash"] != entry.content_hash:
                out.append(pid)
        return out

    async def listing(self, parent: str | None, refresh: bool) -> dict:
        host = self.host
        if host.catalog.remote_urls:
            await asyncio.to_thread(host.catalog.refresh_remote, 6.0, refresh)
        catalog, installed = host.catalog.entries(), host.scan()
        views = []
        for pid in set(catalog) | set(installed):
            v = plugin_view(host, pid, catalog, installed)
            if parent != "*":
                if (v["parent"] or None) != (parent or None):
                    continue
            if v["hidden"] and not v["installed"]:
                continue
            views.append(v)
        views.sort(key=lambda v: (v["order"], v["name"].lower()))
        job = self.running_job()
        return {"ok": True, "api": 1, "revision": host.revision, "plugins": views,
                "catalog_errors": host.catalog.errors, "busy": job.view(len(job.lines)) if job else None}

    # ---- actions -------------------------------------------------------------------

    def install(self, pid: str) -> Job:
        self._known(pid)
        self._busy()
        try:
            self.installer.plan_install(pid)
        except InstallError as exc:
            raise ApiError(str(exc)) from None
        host = self.host

        async def work(job: Job) -> dict:
            ids = await asyncio.to_thread(self.installer.install, pid, job.log)
            failed = self._start_missing(host.order(host.scan()), ids)
            for x in failed:
                job.log(f"{x} was installed but could not start: {host.errors.get(x, 'unknown error')}")
            if pid in failed:
                raise InstallError(f"installed, but it could not start: {host.errors.get(pid, 'unknown error')}")
            job.log("Done.")
            return {"installed": ids}

        return self._start(f"Install {pid}", pid, work)

    def update(self, pid: str) -> Job:
        self._known(pid)
        if pid not in self.host.scan():
            raise ApiError("not installed")
        self._busy()
        host = self.host

        async def work(job: Job) -> dict:
            was_running = [x for x in host.dependents(pid) + [pid] if x in host.loaded]
            stopped, _ = await host.unload(pid)
            job.log(("Stopped: " + ", ".join(stopped)) if stopped else "Nothing was running.")
            try:
                ids = await asyncio.to_thread(self.installer.update, pid, job.log)
            finally:
                for x in host.order(host.scan()):
                    if x in was_running or x in stopped:
                        host.load(x)
            if pid in was_running and pid not in host.loaded and not host.settings.is_disabled(pid):
                raise InstallError(f"updated, but it could not start: {host.errors.get(pid, 'unknown error')}")
            job.log("Done.")
            return {"updated": ids}

        return self._start(f"Update {pid}", pid, work)

    def repair(self, pid: str) -> Job:
        if pid not in self.host.scan():
            raise ApiError("not installed", 404)
        self._busy()
        host = self.host

        async def work(job: Job) -> dict:
            await asyncio.to_thread(self.installer.ensure_requirements, pid, job.log, force=True)
            host.errors.pop(pid, None)
            host.needs_deps.discard(pid)
            await host.unload(pid)
            self._start_missing(host.order(host.scan()))
            if pid not in host.loaded and not host.settings.is_disabled(pid):
                raise InstallError(f"could not start: {host.errors.get(pid, 'unknown error')}")
            job.log("Done.")
            return {}

        return self._start(f"Repair {pid}", pid, work)

    async def repair_pending(self) -> None:
        """At start-up: plugins whose Python packages went missing (a new virtualenv, a moved
        folder) are repaired in the background instead of staying broken until someone notices."""
        host = self.host
        pending = [p for p in host.order(host.scan()) if p in host.needs_deps]
        if not pending:
            return

        async def work(job: Job) -> dict:
            for pid in pending:
                job.log(f"--- {pid} ---")
                try:
                    await asyncio.to_thread(self.installer.ensure_requirements, pid, job.log, force=True)
                except InstallError as exc:
                    job.log(f"{pid}: {exc}")
                    continue
                host.errors.pop(pid, None)
                host.needs_deps.discard(pid)
            self._start_missing(host.order(host.scan()))
            return {}

        self._start("Restoring plugin packages", ",".join(pending), work)

    async def uninstall(self, pid: str, cascade: bool) -> dict:
        host = self.host
        installed = host.scan()
        if pid not in installed:
            raise ApiError("not installed", 404)
        self._busy()
        dependents = host.dependents(pid, installed)
        if dependents and not cascade:
            names = [installed[d].manifest.name if installed[d].manifest else d for d in dependents]
            raise ApiError("other plugins need this one", 409, dependents=dependents, names=names)
        async with self.lock:
            _, clean = await host.unload(pid)
            removed: list[str] = []
            try:
                for x in dependents + [pid]:
                    await asyncio.to_thread(self.installer.uninstall, x)
                    removed.append(x)
                    host.errors.pop(x, None)
                    host.needs_deps.discard(x)
            except InstallError as exc:
                raise ApiError(str(exc), 500, removed=removed) from None
            finally:
                host.revision += 1
        return {"ok": True, "removed": removed, "restart_recommended": not clean}

    def _save_disabled(self, pid: str, disabled: bool) -> None:
        try:
            self.host.settings.set_disabled(pid, disabled)
        except OSError as exc:
            raise ApiError(f"could not save the setting ({exc.strerror or exc}) - is config/ writable?", 500) from None

    async def enable(self, pid: str) -> dict:
        if pid not in self.host.scan():
            raise ApiError("not installed", 404)
        self._save_disabled(pid, False)
        self.host.errors.pop(pid, None)
        self._start_missing(self.host.order(self.host.scan()))
        self.host.revision += 1
        return {"ok": True, "running": pid in self.host.loaded, "error": self.host.errors.get(pid)}

    async def disable(self, pid: str) -> dict:
        if pid not in self.host.scan():
            raise ApiError("not installed", 404)
        self._save_disabled(pid, True)
        stopped, clean = await self.host.unload(pid)
        self.host.errors.pop(pid, None)
        self.host.revision += 1
        return {"ok": True, "stopped": stopped, "restart_recommended": not clean}


def build_router(service: PluginService) -> APIRouter:
    router = APIRouter()
    host = service.host

    def guard(fn):
        """Turn an ApiError into the JSON error every Hexcast API answers with."""
        @functools.wraps(fn)                      # FastAPI reads the handler's parameters from it
        async def call(*a, **kw):
            try:
                return await fn(*a, **kw)
            except ApiError as exc:
                return JSONResponse({"ok": False, "error": exc.message, **exc.extra}, status_code=exc.status)
        return call

    @router.get("/api/plugins")
    async def api_list(parent: str | None = None, refresh: bool = False):
        return await service.listing(parent, refresh)

    @router.get("/api/plugins/nav")
    async def api_nav():
        return {"ok": True, "revision": host.revision, "items": host.nav_items(), "updates": service.updates()}

    @router.get("/api/plugins/jobs/{job_id}")
    async def api_job(job_id: str, since: int = 0):
        job = service.jobs.get(job_id)
        if job is None:
            return JSONResponse({"ok": False, "error": "no such job"}, status_code=404)
        return {"ok": True, **job.view(max(0, since))}

    @router.post("/api/plugins/{pid}/install")
    @guard
    async def api_install(pid: str):
        return {"ok": True, "job": service.install(pid).id}

    @router.post("/api/plugins/{pid}/update")
    @guard
    async def api_update(pid: str):
        return {"ok": True, "job": service.update(pid).id}

    @router.post("/api/plugins/{pid}/repair")
    @guard
    async def api_repair(pid: str):
        return {"ok": True, "job": service.repair(pid).id}

    @router.post("/api/plugins/{pid}/uninstall")
    @guard
    async def api_uninstall(pid: str, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        return await service.uninstall(pid, bool(body.get("cascade")) if isinstance(body, dict) else False)

    @router.post("/api/plugins/{pid}/enable")
    @guard
    async def api_enable(pid: str):
        return await service.enable(pid)

    @router.post("/api/plugins/{pid}/disable")
    @guard
    async def api_disable(pid: str):
        return await service.disable(pid)

    @router.get("/plugins", response_class=HTMLResponse)
    async def store_page():
        return HTMLResponse((service.static_dir / "plugins.html").read_text(encoding="utf-8"), headers=NOCACHE)

    @router.get("/help", response_class=HTMLResponse)
    async def help_page():
        return HTMLResponse(render_help(host, service.static_dir))

    return router


def render_help(host: PluginHost, static_dir: Path) -> str:
    """static/help.html (soundboard + notes) with the running plugins' help sections spliced in."""
    page = (Path(static_dir) / "help.html").read_text(encoding="utf-8")
    toc, sections = [], []
    for manifest, fragment in host.help_fragments():
        for t in (manifest.help or {}).get("toc", []):
            toc.append(f'  <a href="#{html.escape(t["id"])}">{html.escape(t["label"])}</a>')
        sections.append(fragment)
    page = page.replace("<!--HELP_TOC-->", "\n".join(toc))
    return page.replace("<!--HELP_SECTIONS-->", "\n".join(sections))
