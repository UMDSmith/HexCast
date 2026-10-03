"""Shared test helpers: a throw-away Hexcast 'world' (catalog, plugins, config) in a temp folder."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Plugins read the config / media folders from hexcast_core.paths when they are imported, so the
# tests must point them at a throw-away folder BEFORE anything imports it - never at the real
# config/ of whoever runs the tests.
_SESSION = Path(tempfile.mkdtemp(prefix="hexcast-tests-"))
os.environ["HEXCAST_CONFIG_DIR"] = str(_SESSION / "config")
os.environ["SOUNDBOARD_MEDIA_DIR"] = str(_SESSION / "media")
os.environ["HEXCAST_PLUGINS_DIR"] = str(_SESSION / "plugins")
os.environ["HEXCAST_NO_UPSTREAM"] = "1"          # no test ever talks to GitHub (test_versions uses fakes)
SESSION_CONFIG = _SESSION / "config"

from hexcast_core import Installer, PluginHost  # noqa: E402


def write_plugin(folder: Path, pid: str, *, version="1.0.0", requires=(), parent=None, nav=True,
                 setup_body=None, requirements=None, extra=None, files=None):
    """Create a minimal working plugin at folder/pid."""
    d = folder / pid
    d.mkdir(parents=True, exist_ok=True)
    manifest = {"id": pid, "name": pid.title(), "version": version, "description": f"{pid} plugin",
                "requires": list(requires)}
    if parent:
        manifest["parent"] = parent
    if nav:
        manifest["nav"] = {"label": pid.title(), "href": f"/{pid}", "status_url": f"/{pid}/api/status"}
    if requirements:
        (d / "requirements.txt").write_text(requirements, encoding="utf-8")
    manifest.update(extra or {})
    (d / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    body = setup_body or f'''
from fastapi import APIRouter
router = APIRouter()

@router.get("/{pid}/api/status")
async def status():
    return {{"ok": True, "plugin": "{pid}"}}

STOPPED = []

def setup(ctx):
    ctx.include_router(router)

def teardown(ctx):
    STOPPED.append(ctx.id)
'''
    (d / "plugin.py").write_text(body, encoding="utf-8")
    for name, text in (files or {}).items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_path):
    """(host, installer, app, dirs) over empty catalog/plugins/config folders."""
    catalog, plugins, config, media = (tmp_path / n for n in ("catalog", "plugins", "config", "media"))
    for d in (catalog, plugins, config, media):
        d.mkdir()
    app = FastAPI()
    host = PluginHost(app, 4747, root=tmp_path, plugins_dir=plugins, catalog_dir=catalog,
                      config_dir=config, media_dir=media)
    installer = Installer(host)

    class World:
        pass

    w = World()
    w.host, w.installer, w.app = host, installer, app
    w.catalog, w.plugins, w.config, w.media = catalog, plugins, config, media
    yield w
    for name in [n for n in sys.modules if n == "hexcast_plugins" or n.startswith("hexcast_plugins.")]:
        del sys.modules[name]


@pytest.fixture
def real_world(tmp_path):
    """Like `world`, but the catalog is the real catalog/ folder (plugins/ is a fresh temp one)
    and the shared config folder is emptied first - for tests of the plugins that ship."""
    from hexcast_core import paths
    shutil.rmtree(SESSION_CONFIG, ignore_errors=True)
    SESSION_CONFIG.mkdir(parents=True)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    app = FastAPI()
    host = PluginHost(app, 4747, root=ROOT, plugins_dir=plugins, catalog_dir=ROOT / "catalog",
                      config_dir=paths.CONFIG_DIR, media_dir=paths.MEDIA_DIR)
    installer = Installer(host)

    class World:
        pass

    w = World()
    w.host, w.installer, w.app = host, installer, app
    w.plugins, w.config = plugins, paths.CONFIG_DIR
    yield w
    for name in [n for n in sys.modules if n == "hexcast_plugins" or n.startswith("hexcast_plugins.")]:
        del sys.modules[name]
