"""Upgrading from a Hexcast without plugins, and start-up repair."""

import asyncio
import time

from fastapi.testclient import TestClient

from conftest import write_plugin
from hexcast_core import PluginService, migrate


def legacy(world, **extra_plugins):
    write_plugin(world.catalog, "twitchy", extra={"legacy_config": ["twitchy.json", "twitchy_secrets.json"]})
    write_plugin(world.catalog, "musicy", requires=["libby"], extra={"legacy_config": ["musicy.json"]})
    write_plugin(world.catalog, "libby", nav=False, extra={"hidden": True})
    write_plugin(world.catalog, "quiet", extra={"legacy_config": ["quiet.json"]})


def test_fresh_install_starts_with_nothing(world):
    legacy(world)
    assert migrate.run(world.host, world.installer, lambda m: None) == []
    assert world.host.scan() == {}
    assert world.host.settings.data["migrated"] is True
    assert (world.config / "plugins.json").exists()


def test_existing_setups_keep_the_plugins_they_used(world):
    legacy(world)
    (world.config / "twitchy_secrets.json").write_text("{}")           # any one of the files is enough
    (world.config / "musicy.json").write_text("{}")
    said = []
    done = migrate.run(world.host, world.installer, said.append)
    assert set(done) == {"twitchy", "musicy", "libby"} and done.index("libby") < done.index("musicy")
    assert set(world.host.scan()) == {"twitchy", "musicy", "libby"}       # "quiet" had no settings: not installed
    assert said and "twitchy" in said[0]
    world.host.load_all()
    assert set(world.host.loaded) == {"twitchy", "musicy", "libby"}


def test_migration_runs_once_and_removed_plugins_stay_removed(world):
    legacy(world)
    (world.config / "twitchy.json").write_text("{}")
    migrate.run(world.host, world.installer, lambda m: None)
    world.installer.uninstall("twitchy")                                   # settings stay behind ...
    assert (world.config / "twitchy.json").exists()
    assert migrate.run(world.host, world.installer, lambda m: None) == []  # ... but it must not come back
    assert "twitchy" not in world.host.scan()


def test_one_failing_plugin_does_not_stop_the_rest(world):
    write_plugin(world.catalog, "broken", requires=["ghost"], extra={"legacy_config": ["broken.json"]})
    write_plugin(world.catalog, "fine", extra={"legacy_config": ["fine.json"]})
    (world.config / "broken.json").write_text("{}")
    (world.config / "fine.json").write_text("{}")
    said = []
    assert migrate.run(world.host, world.installer, said.append) == ["fine"]
    assert any("broken" in m for m in said)


def test_plugins_with_missing_packages_are_repaired_at_startup(world):
    write_plugin(world.catalog, "alpha", requirements="fastapi>=0.1\n")
    world.installer.copy_in(world.host.catalog.get("alpha"))
    # pretend a brand-new virtualenv: the packages check failed at boot
    world.host.needs_deps.add("alpha")
    world.host.errors["alpha"] = "some of its Python packages are missing - press Repair"
    static = world.plugins.parent / "static"
    static.mkdir()
    service = PluginService(world.host, world.installer, static)

    async def run():
        await service.repair_pending()
        job = service.running_job()
        assert job is not None and "Restoring" in job.title
        for _ in range(200):
            if job.state != "running":
                break
            await asyncio.sleep(0.05)
        return job

    job = asyncio.run(run())
    assert job.state == "done", job.lines
    assert "alpha" in world.host.loaded and not world.host.needs_deps
