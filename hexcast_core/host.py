"""The plugin host: finds installed plugins, starts and stops them, and keeps track of
what each one added to the web app so it can be taken away again cleanly.

A plugin is a folder in plugins/ with a plugin.json and a python module that has
`setup(ctx)`. The host imports it as `hexcast_plugins.<id>.<entry>`, calls setup, and
notes every route the plugin added (by comparing the app's route list before and
after) - so disabling, updating or removing a plugin needs no help from the plugin.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.machinery
import inspect
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import paths, requirements
from .catalog import Catalog
from .versions import Upstream, normalize_upstream, update_info, fmt_version
from .manifest import MANIFEST_NAME, Manifest, ManifestError, load_manifest
from .staticfiles import RevalidatingStaticFiles

NAMESPACE = "hexcast_plugins"
META_NAME = ".hexcast-plugin.json"          # written by the installer next to plugin.json
TEARDOWN_SECONDS = 5.0

log = logging.getLogger("hexcast")


class PluginError(RuntimeError):
    """A plugin could not be started (or asked for something it may not have)."""


_current: "PluginHost | None" = None            # the host of this process (the last one made)


def running_plugin(pid: str) -> types.ModuleType | None:
    """The entry module of a RUNNING plugin, or None. For code that has no ctx at hand
    (a plugin's request handlers asking an optional friend for something)."""
    return _current.module(pid) if _current is not None else None


def plugin_versions(pid: str) -> dict | None:
    """Version / update fields of an installed plugin of this process's host (None if unknown) -
    for code that has no ctx at hand, like a game's registry."""
    try:
        return _current.version_info(pid) if _current is not None else None
    except Exception:
        return None


# ---- settings ----------------------------------------------------------------------

class Settings:
    """config/plugins.json: which installed plugins are switched off, whether the one-time
    upgrade migration has run, extra catalog URLs and the `upstream` update source. Tiny, and safe to hand-edit.

    A file that cannot be read is never silently replaced: it is copied to plugins.json.bad,
    `problem` says what happened (the console prints it), and the upgrade migration is not
    run again on top of it. Keys this version does not know are kept when saving."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {"disabled": [], "migrated": False, "catalogs": []}
        self.problem: str | None = None
        self.upstream, self.upstream_problem = normalize_upstream(None)
        try:
            text = self.path.read_text(encoding="utf-8-sig")         # -sig: Notepad / PowerShell add a BOM
        except FileNotFoundError:
            return
        except OSError as exc:
            self._damaged(f"cannot be read ({exc})")
            return
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("it is not a JSON object")
        except (ValueError, RecursionError) as exc:
            self._damaged(f"is not valid JSON ({exc})")
            return
        self.data.update({k: v for k, v in raw.items() if k not in ("disabled", "migrated", "catalogs")})
        if isinstance(raw.get("disabled"), list):
            self.data["disabled"] = [str(x) for x in raw["disabled"]]
        self.data["migrated"] = bool(raw.get("migrated", False))
        if isinstance(raw.get("catalogs"), list):
            self.data["catalogs"] = [str(x) for x in raw["catalogs"] if isinstance(x, str)]
        if "upstream" in raw:            # kept as written in `data` (saving never rewrites it); this is what is used
            self.upstream, self.upstream_problem = normalize_upstream(raw["upstream"])
            if self.upstream_problem:
                log.warning("[plugins] %s: %s", self.path.name, self.upstream_problem)

    def _damaged(self, why: str) -> None:
        self.data["migrated"] = True                                  # do not "upgrade" on top of a file we cannot read
        keep = self.path.with_name(self.path.name + ".bad")
        try:
            shutil.copy2(self.path, keep)
            where = f"a copy was kept as {keep.name}"
        except OSError:
            where = "it could not be copied aside"
        self.problem = f"{self.path.name} {why}; {where}. Starting with the defaults - fix the file to bring your settings back."
        log.warning("[plugins] %s", self.problem)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def is_disabled(self, pid: str) -> bool:
        return pid in self.data["disabled"]

    def set_disabled(self, pid: str, disabled: bool) -> None:
        cur = [x for x in self.data["disabled"] if x != pid]
        if disabled:
            cur.append(pid)
        if cur != self.data["disabled"]:
            self.data["disabled"] = cur
            self.save()

    @property
    def catalogs(self) -> list[str]:
        return list(self.data["catalogs"])


# ---- what is installed ----------------------------------------------------------------

@dataclass
class Installed:
    id: str
    folder: Path
    manifest: Manifest | None          # None: plugin.json is broken (see error)
    error: str | None
    meta: dict = field(default_factory=dict)      # the installer's record (.hexcast-plugin.json)

    @property
    def version(self) -> str:
        return self.manifest.version if self.manifest else str(self.meta.get("version") or "?")


@dataclass
class Loaded:
    module: types.ModuleType
    ctx: "PluginContext"
    routes: list
    loaded_at: float


class PluginContext:
    """What setup(ctx) gets. Everything a plugin may need from Hexcast is here."""

    def __init__(self, host: "PluginHost", inst: Installed) -> None:
        self._host = host
        self.id = inst.id
        self.manifest = inst.manifest
        self.app = host.app                    # the FastAPI app
        self.port = host.port
        self.root = host.root                  # the Hexcast folder
        self.plugin_dir = inst.folder          # this plugin's own folder
        self.config_dir = host.config_dir      # settings + secrets (shared, git-ignored)
        self.media_dir = host.media_dir        # the media library
        self.static_dir = inst.folder / "static"
        self.static_url = f"/plugins/{inst.id}/static"     # where static_dir is served
        self.log = logging.getLogger(f"hexcast.plugin.{inst.id}")
        self._shutdown: list[Callable[[], Any]] = []

    def include_router(self, router) -> None:
        self.app.include_router(router)

    def plugin(self, pid: str) -> types.ModuleType | None:
        """The entry module of another RUNNING plugin, else None (an optional friend)."""
        return self._host.module(pid)

    def require(self, pid: str) -> types.ModuleType:
        mod = self._host.module(pid)
        if mod is None:
            raise PluginError(f"needs the '{pid}' plugin, which is not running")
        return mod

    def on_shutdown(self, fn: Callable[[], Any]) -> None:
        """Run fn (plain or async, no arguments) when the plugin stops - shutdown, disable,
        update or removal. Called in reverse order of registration."""
        self._shutdown.append(fn)

    def changed(self) -> None:
        """Tell open pages that what this plugin contributes changed (a game was added ...)."""
        self._host.revision += 1


async def _await_quietly(awaitable, pid: str) -> None:
    try:
        await awaitable
    except Exception as exc:
        log.warning("plugin '%s' cleanup failed: %s: %s", pid, type(exc).__name__, exc)


# ---- open websockets ---------------------------------------------------------------------

class _SocketTracker:
    """Pure ASGI middleware that remembers every accepted websocket, so a plugin that is
    stopped, updated or removed can have ITS sockets closed. Without it an OBS overlay that
    stays connected keeps talking to the removed module and never hears the new one; closed
    with code 1012 ("service restart") every Hexcast page simply reconnects a moment later."""

    def __init__(self, app, sockets: set) -> None:
        self.app = app
        self.sockets = sockets

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return
        entry = (scope.get("path", ""), send)

        async def tracked(message):
            if message["type"] == "websocket.accept":
                self.sockets.add(entry)
            elif message["type"] == "websocket.close":
                self.sockets.discard(entry)
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        finally:
            self.sockets.discard(entry)


# ---- the host ---------------------------------------------------------------------------

class PluginHost:
    def __init__(self, app, port: int = 4747, *, root: Path | None = None,
                 plugins_dir: Path | None = None, catalog_dir: Path | None = None,
                 config_dir: Path | None = None, media_dir: Path | None = None) -> None:
        self.app = app
        self.port = port
        self.root = Path(root) if root else paths.ROOT
        self.plugins_dir = Path(plugins_dir) if plugins_dir else paths.PLUGINS_DIR
        self.config_dir = Path(config_dir) if config_dir else paths.CONFIG_DIR
        self.media_dir = Path(media_dir) if media_dir else paths.MEDIA_DIR
        self.settings = Settings(self.config_dir / "plugins.json")
        self.catalog = Catalog(Path(catalog_dir) if catalog_dir else paths.CATALOG_DIR, self.settings.catalogs)
        # HEXCAST_NO_UPSTREAM=1 never contacts GitHub (tests, offline machines, Docker builds)
        self.upstream = Upstream(None if os.environ.get("HEXCAST_NO_UPSTREAM") else self.settings.upstream)
        self.loaded: dict[str, Loaded] = {}
        self.errors: dict[str, str] = {}              # plugin id -> why it is not running
        self.needs_deps: set[str] = set()             # installed, but packages are missing
        self.revision = 0                             # bumps whenever the set of plugins changes
        self._sockets: set = set()                    # (path, send) of every open websocket
        self._loading: set[str] = set()               # plugins being started right now (cycle guard)
        try:
            self.plugins_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:                        # read-only disk, bad HEXCAST_PLUGINS_DIR ...
            log.warning("[plugins] cannot create %s: %s - no plugins will be available", self.plugins_dir, exc)
        if app is not None:
            try:
                app.add_middleware(_SocketTracker, sockets=self._sockets)
            except RuntimeError:
                pass                                  # the app is already serving: sockets simply go untracked
        global _current
        _current = self

    # ---- discovery -------------------------------------------------------------------

    def scan(self) -> dict[str, Installed]:
        """Every folder in plugins/ that looks like a plugin, broken ones included."""
        out: dict[str, Installed] = {}
        if not self.plugins_dir.is_dir():
            return out
        for child in sorted(self.plugins_dir.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            try:
                manifest, error = load_manifest(child), None
            except ManifestError as exc:
                manifest, error = None, str(exc)
            except Exception as exc:                  # whatever else: still only this folder's problem
                manifest, error = None, f"plugin.json could not be read: {type(exc).__name__}: {exc}"
            meta: dict = {}
            try:
                raw = json.loads((child / META_NAME).read_text(encoding="utf-8-sig"))
                if isinstance(raw, dict):
                    meta = raw
            except (OSError, ValueError, RecursionError):
                pass
            out[child.name] = Installed(child.name, child, manifest, error, meta)
        return out

    def installed(self, pid: str) -> Installed | None:
        return self.scan().get(pid)

    def order(self, installed: dict[str, Installed]) -> list[str]:
        """Plugin ids, each after everything it requires (dependencies first)."""
        out: list[str] = []
        seen: set[str] = set()

        def visit(pid: str, stack: tuple[str, ...]) -> None:
            if pid in seen:
                return
            inst = installed.get(pid)
            if inst is None or inst.manifest is None or pid in stack:
                return
            for dep in inst.manifest.requires:
                visit(dep, stack + (pid,))
            seen.add(pid)
            out.append(pid)

        for pid in sorted(installed, key=lambda p: (installed[p].manifest.order if installed[p].manifest else 999, p)):
            visit(pid, ())
        return out

    def dependents(self, pid: str, installed: dict[str, Installed] | None = None) -> list[str]:
        """Installed plugins that need `pid` (directly or not), the deepest dependents first."""
        installed = installed if installed is not None else self.scan()
        out: list[str] = []
        seen = {pid}

        def walk(target: str) -> None:
            for other, inst in installed.items():
                if inst.manifest and target in inst.manifest.requires and other not in seen:
                    seen.add(other)
                    walk(other)
                    out.append(other)

        walk(pid)
        return out

    @staticmethod
    def cycle_through(pid: str, installed: dict[str, Installed]) -> list[str] | None:
        """['a', 'b', 'a'] when `pid` requires itself through other plugins, else None."""
        def walk(cur: str, path: list[str]) -> list[str] | None:
            inst = installed.get(cur)
            if inst is None or inst.manifest is None:
                return None
            for dep in inst.manifest.requires:
                if dep == pid:
                    return path + [dep]
                if dep not in path:
                    found = walk(dep, path + [dep])
                    if found:
                        return found
            return None

        return walk(pid, [pid])

    # ---- namespace -------------------------------------------------------------------

    def _ensure_namespace(self) -> None:
        pkg = sys.modules.get(NAMESPACE)
        want = [str(self.plugins_dir)]
        if pkg is not None and list(getattr(pkg, "__path__", [])) == want:
            return
        for name in [n for n in sys.modules if n == NAMESPACE or n.startswith(NAMESPACE + ".")]:
            del sys.modules[name]                   # another host / folder: start clean
        pkg = types.ModuleType(NAMESPACE)
        pkg.__path__ = want                          # type: ignore[attr-defined]
        pkg.__package__ = NAMESPACE
        spec = importlib.machinery.ModuleSpec(NAMESPACE, None, is_package=True)
        spec.submodule_search_locations = want
        pkg.__spec__ = spec
        sys.modules[NAMESPACE] = pkg
        importlib.invalidate_caches()

    @staticmethod
    def _purge_modules(pid: str) -> None:
        prefix = f"{NAMESPACE}.{pid}"
        for name in [n for n in sys.modules if n == prefix or n.startswith(prefix + ".")]:
            del sys.modules[name]
        importlib.invalidate_caches()

    # ---- starting --------------------------------------------------------------------

    def load_all(self) -> None:
        """Start every enabled plugin, dependencies first. A broken plugin is skipped and
        reported - it never stops Hexcast (or the other plugins) from starting."""
        installed = self.scan()
        for pid in self.order(installed):
            self.load(pid, installed)
        for pid, inst in installed.items():
            if inst.manifest is None:
                self.errors[pid] = inst.error or "plugin.json is unreadable"

    def load(self, pid: str, installed: dict[str, Installed] | None = None) -> bool:
        """Start one plugin. True if it is running afterwards. Synchronous, and safe to call
        from a request handler: it only touches the route list."""
        if pid in self.loaded:
            return True
        installed = installed if installed is not None else self.scan()
        inst = installed.get(pid)
        if inst is None:
            return False
        if inst.manifest is None:
            self.errors[pid] = inst.error or "plugin.json is unreadable"
            return False
        m = inst.manifest
        if self.settings.is_disabled(pid):
            self.errors.pop(pid, None)
            return False
        cycle = self.cycle_through(pid, installed)
        if cycle is not None or pid in self._loading:
            self.errors[pid] = "circular requirement: " + " -> ".join(cycle or [pid])
            return False
        self._loading.add(pid)
        try:
            return self._load_after_deps(pid, inst, installed)
        finally:
            self._loading.discard(pid)

    def _load_after_deps(self, pid: str, inst: Installed, installed: dict[str, Installed]) -> bool:
        m = inst.manifest
        assert m is not None
        for dep in m.requires:
            dep_inst = installed.get(dep)
            if dep_inst is None:
                self.errors[pid] = f"needs the '{dep}' plugin, which is not installed"
                return False
            if dep not in self.loaded and not self.load(dep, installed):
                self.errors[pid] = f"needs the '{dep}' plugin, which is not running"
                return False
        req_file = (inst.folder / m.requirements) if m.requirements else None
        if not requirements.deps_ok(pid, req_file):
            self.needs_deps.add(pid)
            self.errors[pid] = "some of its Python packages are missing - press Repair"
            return False
        return self._activate(inst)

    def _activate(self, inst: Installed) -> bool:
        m = inst.manifest
        assert m is not None
        self._ensure_namespace()
        importlib.invalidate_caches()               # the folder may have been copied in a moment ago
        before = {id(r) for r in self.app.router.routes}
        ctx = PluginContext(self, inst)
        try:
            if ctx.static_dir.is_dir():
                self.app.mount(ctx.static_url, RevalidatingStaticFiles(directory=str(ctx.static_dir)),
                               name=f"plugin_static_{m.id}")
            mod = importlib.import_module(f"{NAMESPACE}.{m.id}.{m.entry}")
            setup = getattr(mod, "setup", None)
            if not callable(setup):
                raise PluginError(f"{m.entry}.py has no setup(ctx) function")
            result = setup(ctx)
            if inspect.isawaitable(result):
                if hasattr(result, "close"):
                    result.close()
                raise PluginError("setup(ctx) must be a plain function, not async")
        except (Exception, SystemExit) as exc:
            self._undo_failed_setup(ctx)
            self._remove_routes_since(before)
            self._purge_modules(m.id)
            self.errors[m.id] = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            self.needs_deps.discard(m.id)
            log.exception("plugin '%s' failed to start", m.id)
            return False
        added = [r for r in self.app.router.routes if id(r) not in before]
        self.loaded[m.id] = Loaded(mod, ctx, added, time.time())
        self.errors.pop(m.id, None)
        self.needs_deps.discard(m.id)
        self.revision += 1
        return True

    def _undo_failed_setup(self, ctx: "PluginContext") -> None:
        """setup(ctx) raised half-way: run the ctx.on_shutdown() hooks it had already registered
        (newest first), so a thread it started or a registry it joined does not leak on every retry.
        The plugin's own teardown() is not called - setup never finished."""
        for hook in reversed(ctx._shutdown):
            try:
                res = hook()
                if inspect.isawaitable(res):
                    try:
                        asyncio.get_running_loop().create_task(_await_quietly(res, ctx.id))
                    except RuntimeError:                  # no loop yet (start-up): run it to the end here
                        asyncio.run(asyncio.wait_for(_await_quietly(res, ctx.id), TEARDOWN_SECONDS))
            except Exception as exc:
                log.warning("plugin '%s' cleanup after a failed start failed: %s: %s", ctx.id, type(exc).__name__, exc)
        ctx._shutdown.clear()

    # ---- stopping --------------------------------------------------------------------

    def _remove_routes_since(self, before: set[int]) -> None:
        routes = self.app.router.routes
        routes[:] = [r for r in routes if id(r) in before]
        self._routes_changed()

    def _remove_routes(self, gone: list) -> None:
        ids = {id(r) for r in gone}
        routes = self.app.router.routes
        routes[:] = [r for r in routes if id(r) not in ids]
        self._routes_changed()

    def _routes_changed(self) -> None:
        mark = getattr(self.app.router, "_mark_routes_changed", None)   # newer FastAPI caches per router
        if callable(mark):
            mark()
        self.app.openapi_schema = None

    async def _run_teardown(self, rec: Loaded, pid: str) -> bool:
        """Call the plugin's cleanup hooks. False if one of them failed or ran too long."""
        clean = True
        hooks: list[Callable[[], Any]] = []
        td = getattr(rec.module, "teardown", None)
        if callable(td):
            hooks.append(lambda: td(rec.ctx))
        hooks.extend(reversed(rec.ctx._shutdown))
        for hook in hooks:
            try:
                res = hook()
                if inspect.isawaitable(res):
                    await asyncio.wait_for(res, TEARDOWN_SECONDS)
            except Exception as exc:
                clean = False
                log.warning("plugin '%s' cleanup failed: %s: %s", pid, type(exc).__name__, exc)
        return clean

    async def _close_sockets(self, rec: Loaded) -> None:
        """Close the open websockets that one of the plugin's routes is serving."""
        from starlette.routing import Match
        for path, send in list(self._sockets):
            scope = {"type": "websocket", "path": path, "root_path": "", "headers": [], "query_string": b"",
                     "scheme": "ws", "subprotocols": []}
            try:
                mine = any(r.matches(scope)[0] == Match.FULL for r in rec.routes)
            except Exception:
                mine = False
            if mine:
                try:
                    await send({"type": "websocket.close", "code": 1012})
                except Exception:
                    pass
                self._sockets.discard((path, send))

    async def unload(self, pid: str) -> tuple[list[str], bool]:
        """Stop a plugin and everything that depends on it (dependents first). Returns
        (the ids stopped, whether every cleanup hook ran cleanly)."""
        stopped: list[str] = []
        clean = True
        chain = [d for d in self.dependents(pid) if d in self.loaded]
        for target in chain + [pid]:
            rec = self.loaded.get(target)
            if rec is None:
                continue
            clean = await self._run_teardown(rec, target) and clean
            await self._close_sockets(rec)
            self._remove_routes(rec.routes)
            if self.loaded.get(target) is rec:        # not replaced by a newer start while we awaited
                self.loaded.pop(target, None)
                self._purge_modules(target)
            stopped.append(target)
        if stopped:
            self.revision += 1
        return stopped, clean

    async def shutdown(self) -> None:
        """Hexcast is stopping: give every running plugin the chance to close its sockets."""
        for pid in reversed(list(self.loaded)):
            rec = self.loaded.get(pid)
            if rec is not None:
                await self._run_teardown(rec, pid)
        self.loaded.clear()

    # ---- what the rest of Hexcast asks -------------------------------------------------

    def module(self, pid: str) -> types.ModuleType | None:
        rec = self.loaded.get(pid)
        return rec.module if rec else None

    def state(self, pid: str, inst: Installed | None = None) -> str:
        """running | disabled | error | needs_deps | stopped"""
        if inst is not None and inst.manifest is None:
            return "error"                          # plugin.json is broken, whatever may still be serving from before
        if pid in self.loaded:
            return "running"
        if self.settings.is_disabled(pid):
            return "disabled"
        if pid in self.needs_deps:
            return "needs_deps"
        if pid in self.errors or (inst is not None and inst.manifest is None):
            return "error"
        return "stopped"

    def update_infos(self, installed: dict[str, Installed] | None = None) -> dict:
        """pid -> UpdateInfo for every installed plugin (see versions.update_info). Also makes
        sure the upstream check is running or cached - in the background, so this stays instant."""
        installed = installed if installed is not None else self.scan()
        catalog = self.catalog.entries()
        self.upstream.kick([p for p, i in installed.items() if i.manifest is not None and i.meta.get("source") in ("bundled", "upstream")])
        return {pid: update_info(self.upstream, inst, catalog.get(pid)) for pid, inst in installed.items()}

    def version_info(self, pid: str) -> dict | None:
        """Version fields of one installed plugin (for pages that describe it), or None."""
        inst = self.installed(pid)
        if inst is None:
            return None
        return self.update_infos({pid: inst})[pid].view()

    def nav_items(self) -> list[dict]:
        """The top-bar tabs: installed, top-level plugins that have a `nav` entry."""
        items = []
        scanned = self.scan()
        infos = self.update_infos(scanned)
        for pid, inst in scanned.items():
            m = inst.manifest
            if m is None or m.parent or not m.nav:
                continue
            state = self.state(pid, inst)
            if state == "disabled":
                continue
            nav = m.nav
            item = {"id": pid, "key": nav.get("key") or pid, "label": nav["label"], "href": nav["href"],
                    "order": nav["order"], "color": nav.get("color") or m.color, "state": state,
                    "error": self.errors.get(pid), "version": m.version, "version_label": fmt_version(m.version),
                    **infos[pid].view()}
            if nav.get("status_url"):
                item["status_url"] = nav["status_url"]
            if nav.get("status_js"):
                item["status_js"] = f"/plugins/{pid}/static/{nav['status_js']}?v={m.version}"
            items.append(item)
        items.sort(key=lambda i: (i["order"], i["label"].lower()))
        return items

    def help_fragments(self) -> list[tuple[Manifest, str]]:
        """(manifest, html) for each running plugin that documents itself, parents first."""
        out = []
        for pid in self.order(self.scan()):
            rec = self.loaded.get(pid)
            inst = self.installed(pid)
            if rec is None or inst is None or inst.manifest is None or not inst.manifest.help:
                continue
            try:
                out.append((inst.manifest, (inst.folder / inst.manifest.help["file"]).read_text(encoding="utf-8")))
            except OSError:
                continue
        return out
