"""The plugin web API: what the (+) tab, the top bar and the Games sub-store talk to.

    GET  /plugins                        the store page (the (+) tab)
    GET  /api/plugins                    what is installed / can be installed
                                         ?parent=games -> that plugin's add-ons; none -> top level
    GET  /api/plugins/nav                the top-bar tabs
    GET  /options                        the master Options page (the gear in the top bar)
    GET|POST /api/options                the global options (update checks, upstream repo / branch)
    POST /api/options/check-now          ask GitHub for the latest versions right now
    POST /api/plugins/{id}/install       -> {job}     (also installs what it requires)
    POST /api/plugins/{id}/update        -> {job}
    POST /api/plugins/{id}/repair        -> {job}     (re-install its Python packages)
    POST /api/plugins/{id}/uninstall     {cascade?}   (settings are kept)
    POST /api/plugins/{id}/enable | disable
    GET  /api/plugins/jobs/{job}         progress of an install / update / repair
    GET  /help  /help/{page}             the help: an index, and one page per plugin / game that is installed

Installing and updating run as background jobs (pip can take a while); the page polls
the job for its log. Only one job runs at a time.
"""

from __future__ import annotations

import asyncio
import functools
import html
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import options as opts
from . import requirements as reqs
from .host import PluginHost
from .installer import InstallError, Installer
from .manifest import Manifest, valid_id
from .versions import fmt_version, update_info

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
        lines = list(self.lines)                 # the worker thread appends while we read: one snapshot
        return {"id": self.id, "title": self.title, "plugin": self.plugin, "state": self.state,
                "error": self.error, "result": self.result, "lines": lines[since:],
                "total": len(lines)}


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


def plugin_view(host: PluginHost, pid: str, catalog: dict, installed: dict, info=None) -> dict:
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
    info = info or update_info(host.upstream, inst, entry)
    out.update(info.view())                       # installed_version, latest_version, *_label, update_available, update_source
    out["version_label"] = fmt_version(inst.version if inst else out.get("version"))
    if not inst:
        out["installed_version"] = None
        out["latest_version"] = entry.manifest.version if entry else info.latest
    out["needs_packages"] = bool(not is_installed and entry and _needs_download(entry))
    out["dependents"] = host.dependents(pid, installed) if is_installed else []
    out["dependents_info"] = [{"id": d, "name": installed[d].manifest.name if installed[d].manifest else d}
                              for d in out["dependents"]]

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
            raise ApiError("another install is running - wait for it to finish", 409,
                           job=job.id, plugin=job.plugin, title=job.title)

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
        if not valid_id(pid) or not (pid in self.host.scan() or pid in self.host.catalog.entries()):
            raise ApiError("no such plugin", 404)

    # ---- listing -------------------------------------------------------------------

    def updates(self) -> list[str]:
        """Ids of installed plugins with something newer: a catalog copy that differs from what was
        installed, or a newer upstream version. Updates are manual - neither a git pull nor the
        GitHub check changes what is running - so the top bar puts a dot on the + tab to say there
        is something to press Update on."""
        return [pid for pid, info in self.host.update_infos().items() if info.available]

    async def listing(self, parent: str | None, refresh: bool) -> dict:
        host = self.host
        if host.catalog.remote_urls:
            await asyncio.to_thread(host.catalog.refresh_remote, 6.0, refresh)
        catalog, installed = host.catalog.entries(), host.scan()
        infos = host.update_infos(installed)
        views = []
        for pid in set(catalog) | set(installed):
            v = plugin_view(host, pid, catalog, installed, infos.get(pid))
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
            ids: list[str] = []
            try:
                ids = await asyncio.to_thread(self.installer.install, pid, job.log)
            finally:
                # dependencies that were installed before something failed still start
                failed = self._start_missing(host.order(host.scan()), ids or None)
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
            raise ApiError("not installed", 404)
        self._busy()
        host = self.host

        async def work(job: Job) -> dict:
            # 1. build the new copy and get its packages while the old one keeps running: if that
            #    fails (offline pip ...) nothing has been touched and nothing was stopped
            prepared = await asyncio.to_thread(self.installer.prepare_update, pid, job.log)
            was_running = [x for x in host.dependents(pid) + [pid] if x in host.loaded]
            try:
                # 2. stop it just for the swap
                stopped, _ = await host.unload(pid)
                job.log(("Stopped: " + ", ".join(stopped)) if stopped else "Nothing was running.")
                try:
                    ids = await asyncio.to_thread(self.installer.commit, prepared, job.log)
                finally:
                    for x in host.order(host.scan()):
                        if x in was_running or x in stopped:
                            host.load(x)
            finally:
                prepared.discard()
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
        if pid not in host.scan():
            raise ApiError("not installed", 404)
        self._busy()
        async with self.lock:
            installed = host.scan()
            if pid not in installed:                   # somebody else removed it while we waited
                raise ApiError("not installed", 404)
            dependents = host.dependents(pid, installed)
            if dependents and not cascade:
                names = [installed[d].manifest.name if installed[d].manifest else d for d in dependents]
                raise ApiError("other plugins need this one", 409, dependents=dependents, names=names)
            _, clean = await host.unload(pid)
            removed: list[str] = []
            kept: dict[str, str] = {}
            try:
                for x in dependents + [pid]:
                    where = await asyncio.to_thread(self.installer.uninstall, x)
                    removed.append(x)
                    if where:
                        kept[x] = where
                    host.errors.pop(x, None)
                    host.needs_deps.discard(x)
            except (InstallError, OSError) as exc:
                why = str(exc) if isinstance(exc, InstallError) else f"could not remove {x}: {exc.strerror or exc}"
                raise ApiError(why, 500, removed=removed) from None
            finally:
                # whatever is still installed (a removal failed half-way) goes back to running
                self._start_missing(host.order(host.scan()))
                host.revision += 1
        return {"ok": True, "removed": removed, "kept": kept, "restart_recommended": not clean}

    def _save_disabled(self, pid: str, disabled: bool) -> None:
        try:
            self.host.settings.set_disabled(pid, disabled)
        except OSError as exc:
            raise ApiError(f"could not save the setting ({exc.strerror or exc}) - is config/ writable?", 500) from None

    async def enable(self, pid: str) -> dict:
        if pid not in self.host.scan():
            raise ApiError("not installed", 404)
        self._busy()
        async with self.lock:
            if pid not in self.host.scan():
                raise ApiError("not installed", 404)
            self._save_disabled(pid, False)
            self.host.errors.pop(pid, None)
            self._start_missing(self.host.order(self.host.scan()))
            inst = self.host.scan().get(pid)
            if inst is not None and inst.manifest is None:            # (order() leaves those out: say why)
                self.host.errors[pid] = inst.error or "plugin.json is unreadable"
            self.host.revision += 1
            return {"ok": True, "running": pid in self.host.loaded, "error": self.host.errors.get(pid)}

    async def disable(self, pid: str) -> dict:
        if pid not in self.host.scan():
            raise ApiError("not installed", 404)
        self._busy()
        async with self.lock:
            if pid not in self.host.scan():
                raise ApiError("not installed", 404)
            self._save_disabled(pid, True)
            stopped, clean = await self.host.unload(pid)
            self.host.errors.pop(pid, None)
            self.host.revision += 1
            return {"ok": True, "stopped": stopped, "restart_recommended": not clean}


def check_same_origin(request: Request) -> None:
    """Changing plugins is for the pages Hexcast itself serves (and for scripts, which send no
    Origin). A page on another site - or another port - that the streamer happens to have open
    must not be able to install, disable or remove plugins with a hidden form or fetch()."""
    site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    if site == "cross-site" or (origin and urlparse(origin).netloc != request.headers.get("host", "")):
        raise ApiError("refused: this request came from another web page", 403)


def build_router(service: PluginService) -> APIRouter:
    router = APIRouter()
    host = service.host

    def guard(fn):
        """Turn an ApiError into the JSON error every Hexcast API answers with, and refuse
        cross-site requests (every handler using it takes `request: Request`)."""
        @functools.wraps(fn)                      # FastAPI reads the handler's parameters from it
        async def call(*a, **kw):
            try:
                check_same_origin(kw["request"])
                return await fn(*a, **kw)
            except ApiError as exc:
                return JSONResponse({"ok": False, "error": exc.message, **exc.extra}, status_code=exc.status)
        return call

    @router.get("/api/plugins")
    async def api_list(parent: str | None = None, refresh: bool = False):
        return await service.listing(parent, refresh)

    @router.get("/api/plugins/nav")
    async def api_nav():
        return {"ok": True, "revision": host.revision, "items": host.nav_items(), "updates": service.updates(),
                "upstream": {"enabled": host.upstream.enabled, "repo": host.upstream.repo if host.upstream.enabled else None}}

    @router.get("/api/plugins/jobs/{job_id}")
    async def api_job(job_id: str, since: int = 0):
        job = service.jobs.get(job_id)
        if job is None:
            return JSONResponse({"ok": False, "error": "no such job"}, status_code=404)
        return {"ok": True, **job.view(max(0, since))}

    @router.post("/api/plugins/{pid}/install")
    @guard
    async def api_install(pid: str, request: Request):
        return {"ok": True, "job": service.install(pid).id}

    @router.post("/api/plugins/{pid}/update")
    @guard
    async def api_update(pid: str, request: Request):
        return {"ok": True, "job": service.update(pid).id}

    @router.post("/api/plugins/{pid}/repair")
    @guard
    async def api_repair(pid: str, request: Request):
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
    async def api_enable(pid: str, request: Request):
        return await service.enable(pid)

    @router.post("/api/plugins/{pid}/disable")
    @guard
    async def api_disable(pid: str, request: Request):
        return await service.disable(pid)

    def options_view(extra: dict | None = None) -> dict:
        view = opts.read_all(host)
        scanned = host.scan()
        view["available"] = [
            {"id": pid, "name": scanned[pid].manifest.name if scanned[pid].manifest else pid,
             "installed": i.view()["installed_label"], "latest": i.view()["latest_label"], "source": i.source}
            for pid, i in host.update_infos(scanned).items() if i.available]
        return {"ok": True, **view, **(extra or {})}

    @router.get("/api/options")
    async def api_options():
        return options_view()

    @router.post("/api/options")
    @guard
    async def api_options_set(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = None
        try:
            opts.apply(host, body)
        except opts.OptionError as exc:
            raise ApiError(str(exc), 400, errors=getattr(exc, "errors", {})) from None
        except OSError as exc:
            raise ApiError(f"could not save the setting ({exc.strerror or exc}) - is config/ writable?", 500) from None
        return options_view()

    @router.post("/api/options/check-now")
    @guard
    async def api_options_check_now(request: Request):
        up = host.upstream
        if not up.enabled:
            raise ApiError("Update checks are switched off - nothing was contacted", 409)
        installed = [p for p, i in host.scan().items() if i.meta.get("source") in ("bundled", "upstream")]
        await asyncio.to_thread(up.refresh, installed, True)
        return options_view({"checked": True})

    @router.get("/options", response_class=HTMLResponse)
    async def options_page():
        return HTMLResponse((service.static_dir / "options.html").read_text(encoding="utf-8"), headers=NOCACHE)

    @router.get("/plugins", response_class=HTMLResponse)
    async def store_page():
        return HTMLResponse((service.static_dir / "plugins.html").read_text(encoding="utf-8"), headers=NOCACHE)

    @router.get("/help", response_class=HTMLResponse)
    async def help_index():
        return HTMLResponse(render_help(host, service.static_dir), headers=NOCACHE)

    @router.get("/help/{slug}", response_class=HTMLResponse)
    async def help_page(slug: str):
        page = render_help(host, service.static_dir, slug)
        if page is None:                                   # not installed (or a typo): show the index instead
            return HTMLResponse(render_help(host, service.static_dir), status_code=404, headers=NOCACHE)
        return HTMLResponse(page, headers=NOCACHE)

    return router


CORE_HELP = [
    ("soundboard", "Soundboard", "Play, stop and list your clips with one GET - the base of every bot.", "/api"),
    ("plugins", "Plugins", "Install, update and remove plugins from a script or a bot.", "/plugins"),
    ("notes", "Browser & OBS notes", "Autoplay, OBS browser-source settings, and why there is no login.", ""),
]


def help_entries(host: PluginHost, static_dir: Path) -> list[dict]:
    """Every page of the help: the core ones, then one per running plugin that documents itself,
    grouped (Core / Integrations / the plugin an add-on belongs to, e.g. Games)."""
    entries: list[dict] = []
    for slug, title, blurb, tag in CORE_HELP:
        f = Path(static_dir) / "help" / f"{slug}.html"
        try:
            body = f.read_text(encoding="utf-8")
        except OSError:
            continue
        entries.append({"slug": slug, "title": title, "group": "Core", "blurb": blurb, "tag": tag, "html": body, "ids": [slug]})
    scan = host.scan()
    rest = []
    fragments = host.help_fragments()
    parents = {m.parent for m, _ in fragments if m.parent}
    for manifest, fragment in fragments:
        toc = (manifest.help or {}).get("toc") or []
        ids = [t["id"] for t in toc] or [manifest.id]
        if manifest.parent:
            pm = scan.get(manifest.parent)
            group = pm.manifest.name if pm and pm.manifest else manifest.parent.title()
        elif manifest.id in parents:
            group = manifest.name                         # Games heads its own group, its games follow
        else:
            group = "Integrations"
        blurb = (manifest.description or "").strip()
        if len(blurb) > 150:
            blurb = blurb[:147].rsplit(" ", 1)[0] + "..."
        tag = (manifest.nav or {}).get("href", "")
        if manifest.parent and ids:
            tag = f"{(scan[manifest.parent].manifest.nav or {}).get('href', '')}#{ids[0]}" if manifest.parent in scan and scan[manifest.parent].manifest else tag
        rest.append((manifest, {"slug": ids[0], "title": manifest.name, "group": group, "blurb": blurb, "tag": tag,
                                "html": fragment, "ids": ids}))
    # a parent's own page (Games) comes before its add-ons; add-ons keep their manifest order
    rest.sort(key=lambda mr: (1 if mr[0].parent else 0, mr[0].order, mr[0].name.lower()))
    entries += [e for _, e in rest]
    return entries


def _group_order(entries: list[dict]) -> list[str]:
    groups: list[str] = []
    for e in entries:
        if e["group"] not in groups:
            groups.append(e["group"])
    return groups


def render_help(host: PluginHost, static_dir: Path, slug: str | None = None) -> str | None:
    """/help (the index) or /help/<slug> (one page). None if there is no such page."""
    entries = help_entries(host, static_dir)
    current = next((e for e in entries if e["slug"] == slug), None) if slug else None
    if slug and current is None:
        return None
    esc = html.escape
    groups = _group_order(entries)
    nav = [f'<a href="/help"{" class=sel" if current is None else ""}>All help</a>']
    for g in groups:
        nav.append(f"<h4>{esc(g)}</h4>")
        for e in entries:
            if e["group"] == g:
                sel = ' class="sel"' if current is e else ""
                nav.append(f'<a href="/help/{esc(e["slug"])}"{sel}>{esc(e["title"])}</a>')
    anchors = {i: e["slug"] for e in entries for i in e["ids"]}
    if current is None:
        cards = []
        for g in groups:
            cards.append(f'<div class="grp">{esc(g)}</div><div class="cards">')
            for e in entries:
                if e["group"] == g:
                    tag = f'<span class="tag">{esc(e["tag"])}</span>' if e["tag"] else ""
                    cards.append(f'<a class="hc" href="/help/{esc(e["slug"])}"><b>{esc(e["title"])}</b>{tag}'
                                 f'<p>{esc(e["blurb"])}</p></a>')
            cards.append("</div>")
        body = ('<h1>Help &amp; API reference</h1>'
                '<p class="lead">Everything Hexcast does is a URL, so a bot, a hotkey deck or a channel-point redeem only needs '
                'to call it. Pick a page: each has the endpoints, the parameters and copy-paste examples. Calls are plain '
                '<code>GET</code>s (or a <code>POST</code> with the same fields as JSON) and answer '
                '<code>{"ok": true/false, ...}</code>. Only what you have installed is listed; add more from the '
                '<b>+</b> tab.</p>' + "\n".join(cards))
        title, crumbs = "Help", ""
    else:
        title = current["title"]
        crumbs = (f'<p class="crumbs"><a href="/help">Help</a> / {esc(current["group"])} / {esc(title)}</p>'
                  if current["group"] != "Core" else f'<p class="crumbs"><a href="/help">Help</a> / {esc(title)}</p>')
        body = current["html"]
    page = (Path(static_dir) / "help.html").read_text(encoding="utf-8")
    page = page.replace("/*HELP_ANCHORS*/{}", json.dumps(anchors))
    for key, val in (("<!--HELP_TITLE-->", esc(title)), ("<!--HELP_NAV-->", "\n".join(nav)),
                     ("<!--HELP_CRUMBS-->", crumbs), ("<!--HELP_BODY-->", body)):
        page = page.replace(key, val)
    return page
