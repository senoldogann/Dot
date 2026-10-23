"""Customize › Plugins' gallery (item 542) through the API of a real in-process server. Install downloads the pinned
commit (an archive made here: no network), puts the plugin in Dot's plugins folder and offers its skills to the runs
that start after, without a restart; a second Install, a plugin the gallery does not have and one whose files are not
the ones pinned are refused, and the last leaves nothing behind. Remove plugin moves it to the Trash (one made here) and
its skills leave the runs. A plugin put in Dot's plugins folder by hand is listed apart, as one Remove can take out.
"""

import asyncio
import io
import json
import shutil
import tarfile
import time
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.plugins.gallery import GalleryEntry, files_hash, unpack
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_gallery, routes_mcp
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.skills.catalog import load_skills

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "plugin-gallery-token"
_COMMIT: str = "c" * 40
_SKILL: str = "---\nname: acme-alpha\ndescription: Does the alpha work of Acme.\n---\n\nThe instructions.\n"


def _archive(path: Path) -> None:
    """GitHub's archive of a repository with one skill folder, as a tar.gz under one top folder."""
    with tarfile.open(path, "w:gz") as tar:
        for name, content in (("README.md", "the repository"), ("skills/acme-alpha/SKILL.md", _SKILL)):
            data: bytes = content.encode()
            info: tarfile.TarInfo = tarfile.TarInfo(f"acme-skills-{_COMMIT}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _entry(plugin_id: str, files: str) -> GalleryEntry:
    return GalleryEntry(
        id=plugin_id,
        name=plugin_id.title(),
        publisher="Acme",
        official=True,
        category="development",
        does="Acme's skills.",
        repository="acme/skills",
        commit=_COMMIT,
        skills=("skills/acme-alpha",),
        files=files,
        licence="MIT",
        stars=3,
        added=date(2026, 10, 22),
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def setup(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[tuple[httpx.AsyncClient, Runtime, Path, Path, list[str]]]:
    """A runtime in a throw-away home with a plugin put there by hand; the gallery is two entries of the archive made
    here, one pinned to its files and one to other files. Yields the client, the runtime, Dot's plugins folder, the
    Trash and the plugins downloaded."""
    home: Path = tmp_path_factory.mktemp("home")
    trash: Path = tmp_path_factory.mktemp("trash")
    work: Path = tmp_path_factory.mktemp("work")
    archive: Path = work / "acme.tar.gz"
    _archive(archive)
    probe: Path = work / "probe"
    unpack(_entry("acme", "0" * 64), archive, probe)
    pinned: GalleryEntry = _entry("acme", files_hash(probe / "skills"))
    (home / "plugins" / "by-hand" / ".claude-plugin").mkdir(parents=True)
    (home / "plugins" / "by-hand" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "by-hand", "description": "Put here by hand."}), encoding="utf-8"
    )
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    fetched: list[str] = []

    async def fetch(entry: GalleryEntry, folder: Path) -> Path:
        fetched.append(entry.id)
        copy: Path = folder / "archive.tar.gz"
        await asyncio.to_thread(shutil.copyfile, archive, copy)
        return copy

    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    patch.setattr(routes_gallery, "GALLERY", (pinned, _entry("tampered", "e" * 64)))
    patch.setattr(routes_gallery, "download_archive", fetch)
    patch.setattr(routes_mcp, "trash_folder", lambda: trash)
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    transport: httpx.ASGITransport = httpx.ASGITransport(app=create_app(context))
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as client:
            yield client, runtime, home / "plugins", trash, fetched
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


def _states(view: httpx.Response) -> dict[str, str]:
    body: JsonObject = view.raise_for_status().json()
    plugins = body["plugins"]
    assert isinstance(plugins, list)
    return {str(plugin["id"]): str(plugin["state"]) for plugin in plugins if isinstance(plugin, dict)}


def _offered(runtime: Runtime) -> set[str]:
    return {skill.name for skill in load_skills(runtime.config.skills, runtime.plugins.plugins, None).skills}


async def test_install_offers_the_skills_at_once_and_remove_takes_them_back(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, list[str]],
) -> None:
    client, runtime, plugins, trash, fetched = setup
    listed: httpx.Response = await client.get("/api/plugin-gallery")
    assert _states(listed) == {"acme": "available", "tampered": "available"}
    assert listed.json()["plugins"][0]["skills"] == ["acme-alpha"]
    assert listed.json()["plugins"][0]["homepage"] == f"https://github.com/acme/skills/tree/{_COMMIT}/skills/acme-alpha"
    assert [(one["name"], one["own"]) for one in listed.json()["others"]] == [("by-hand", True)]
    assert "acme-alpha" not in _offered(runtime)

    installed: httpx.Response = await client.post("/api/plugin-gallery/acme")

    assert _states(installed)["acme"] == "installed"
    assert (plugins / "acme" / "skills" / "acme-alpha" / "SKILL.md").is_file()
    assert "acme-alpha" in _offered(runtime)
    again: httpx.Response = await client.post("/api/plugin-gallery/acme")
    assert again.status_code == 409 and "already has a plugin named acme" in again.text, again.text
    assert fetched == ["acme"]

    removed: httpx.Response = await client.delete("/api/installed-plugins/acme")
    assert removed.status_code == 200, removed.text
    assert (trash / "acme" / ".claude-plugin" / "plugin.json").is_file()
    assert _states(await client.get("/api/plugin-gallery"))["acme"] == "available"
    assert "acme-alpha" not in _offered(runtime)


async def test_a_plugin_whose_files_are_not_the_pinned_ones_installs_nothing(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, list[str]],
) -> None:
    client, _runtime, plugins, _trash, _fetched = setup

    refused: httpx.Response = await client.post("/api/plugin-gallery/tampered")

    assert refused.status_code == 409 and "not the ones pinned" in refused.text, refused.text
    assert not (plugins / "tampered").exists()
    assert not any(path.name.startswith(".installing-") for path in plugins.iterdir())


async def test_a_plugin_the_gallery_does_not_have_is_not_found(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, list[str]],
) -> None:
    client = setup[0]

    missing: httpx.Response = await client.post("/api/plugin-gallery/nothing-like-it")

    assert missing.status_code == 404, missing.text


async def test_a_plugin_installed_from_a_pin_the_gallery_has_moved_since_is_outdated(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 558: after a newer Dot re-pins an entry the card said Installed at the new commit while the runs read the
    skills of the old one, and nothing said so. The state says it now, and tells the commit that is installed."""
    client, _runtime, _plugins, _trash, _fetched = setup
    pinned: GalleryEntry = routes_gallery.GALLERY[0]
    assert (await client.post("/api/plugin-gallery/acme")).status_code == 200

    for moved in (pinned.model_copy(update={"commit": "d" * 40}), pinned.model_copy(update={"files": "a" * 64})):
        monkeypatch.setattr(routes_gallery, "GALLERY", (moved, *routes_gallery.GALLERY[1:]))
        view: JsonObject = (await client.get("/api/plugin-gallery")).json()["plugins"][0]
        assert (view["id"], view["state"]) == ("acme", "outdated")
        assert (view["commit"], view["installed_commit"]) == (moved.commit, _COMMIT)
        refused: httpx.Response = await client.post("/api/plugin-gallery/acme")
        assert refused.status_code == 409, refused.text

    monkeypatch.setattr(routes_gallery, "GALLERY", (pinned, *routes_gallery.GALLERY[1:]))
    current: JsonObject = (await client.get("/api/plugin-gallery")).json()["plugins"][0]
    assert (current["state"], current["installed_commit"]) == ("installed", _COMMIT)
    assert (await client.delete("/api/installed-plugins/acme")).status_code == 200
    gone: JsonObject = (await client.get("/api/plugin-gallery")).json()["plugins"][0]
    assert (gone["state"], gone["installed_commit"]) == ("available", None)
