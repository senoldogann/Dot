"""The plugin gallery (item 542): its entries, and installing one from an archive made here, never from the network."""

import asyncio
import io
import shutil
import tarfile
import threading
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

import httpx
import pytest

from dotagent.config import McpServerConfig, PluginsConfig
from dotagent.core.errors import GalleryError
from dotagent.help.elsewhere import CopyLimit
from dotagent.plugins import gallery
from dotagent.plugins.catalog import PluginShelf, load_plugins
from dotagent.plugins.gallery import (
    GALLERY_MARKER,
    GalleryEntry,
    files_hash,
    homepage,
    install,
    installed_marker,
    unpack,
)
from dotagent.plugins.gallery_list import GALLERY
from dotagent.plugins.types import Plugin, PluginCatalog

_TOP: str = "acme-skills-" + "a" * 40
_LOGOS: Path = Path(__file__).resolve().parents[2] / "src" / "dotagent" / "server" / "static" / "icons" / "plugins"
_SKILL: bytes = b"---\nname: alpha\ndescription: Does alpha things.\n---\n\nThe instructions.\n"

# A member of an archive: its bytes, a folder (None), or a link to a path (a tuple).
type Member = bytes | tuple[str] | None


def _archive(folder: Path, members: Mapping[str, Member], top: str = _TOP) -> Path:
    """A tar.gz as GitHub makes one: every member under one top folder."""
    folder.mkdir(parents=True, exist_ok=True)
    path: Path = folder / "made.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        root = tarfile.TarInfo(top)
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for name, content in members.items():
            info = tarfile.TarInfo(f"{top}/{name}" if not name.startswith("/") else name)
            if content is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif isinstance(content, tuple):
                info.type = tarfile.SYMTYPE
                info.linkname = content[0]
                tar.addfile(info)
            else:
                info.size = len(content)
                info.mode = 0o755 if name.endswith(".sh") else 0o644
                tar.addfile(info, io.BytesIO(content))
    return path


_MEMBERS: dict[str, Member] = {
    "README.md": b"not kept",
    "skills/alpha/SKILL.md": _SKILL,
    "skills/alpha/scripts/run.sh": b"#!/bin/sh\necho alpha\n",
    "skills/alpha/.hidden": b"not kept either",
    "packs/beta/SKILL.md": b"---\nname: beta\ndescription: Does beta things.\n---\n",
    "other/gamma/SKILL.md": b"---\nname: gamma\ndescription: Not asked for.\n---\n",
}


def _entry(**changes: object) -> GalleryEntry:
    values: dict[str, object] = {
        "id": "acme",
        "name": "Acme",
        "publisher": "Acme",
        "official": True,
        "category": "development",
        "does": "Acme's skills.",
        "repository": "acme/skills",
        "commit": "a" * 40,
        "skills": ("skills/alpha", "packs/beta"),
        "files": "0" * 64,
        "licence": "MIT",
        "stars": 1,
        "added": date(2026, 10, 22),
    }
    values.update(changes)
    return GalleryEntry.model_validate(values)


def _pinned(tmp_path: Path, members: Mapping[str, Member] = _MEMBERS) -> tuple[GalleryEntry, Path]:
    """The entry with the hash of what Dot keeps of ``members``, as pinning computes it, and the archive."""
    archive: Path = _archive(tmp_path, members)
    probe: Path = tmp_path / "probe"
    unpack(_entry(), archive, probe)
    return _entry(files=files_hash(probe / "skills")), archive


def _fetch(archive: Path, calls: list[str] | None = None) -> Callable[[GalleryEntry, Path], asyncio.Future[Path]]:
    async def fetch(entry: GalleryEntry, folder: Path) -> Path:
        if calls is not None:
            calls.append(entry.id)
        copy: Path = folder / "archive.tar.gz"
        await asyncio.to_thread(shutil.copyfile, archive, copy)
        return copy

    return fetch  # type: ignore[return-value]


class TestEntries:
    def test_ids_are_unique_and_each_skill_folder_name_once_in_its_plugin(self) -> None:
        ids: list[str] = [entry.id for entry in GALLERY]
        assert len(ids) == len(set(ids))
        for entry in GALLERY:
            names: list[str] = [gallery.skill_folder_name(path) for path in entry.skills]
            assert len(names) == len(set(names)), entry.id

    def test_logos_exist_and_some_plugins_are_featured(self) -> None:
        for entry in GALLERY:
            if entry.logo is not None:
                assert (_LOGOS / entry.logo).is_file(), entry.logo
        assert sum(entry.featured for entry in GALLERY) >= 3

    def test_the_owner_asked_for_these_plugins(self) -> None:
        ids: set[str] = {entry.id for entry in GALLERY}
        assert {"superpowers", "ui-ux-pro-max", "frontend-design", "product-design", "engineering"} <= ids

    def test_only_open_licences(self) -> None:
        assert {entry.licence for entry in GALLERY} <= {"MIT", "Apache-2.0"}

    def test_homepage_is_the_pinned_commit_at_the_shared_folder(self) -> None:
        entry: GalleryEntry = _entry(skills=("skills/alpha", "skills/beta"))
        assert homepage(entry) == f"https://github.com/acme/skills/tree/{'a' * 40}/skills"
        assert homepage(_entry(skills=("a/b",))) == f"https://github.com/acme/skills/tree/{'a' * 40}/a/b"


class TestUnpack:
    def test_keeps_only_the_named_skills_their_scripts_running_and_writes_the_manifest(self, tmp_path: Path) -> None:
        plugin: Path = tmp_path / "plugin"
        unpack(_entry(), _archive(tmp_path, _MEMBERS), plugin)

        kept: set[str] = {path.relative_to(plugin).as_posix() for path in plugin.rglob("*") if path.is_file()}
        assert kept == {
            ".claude-plugin/plugin.json",
            f".claude-plugin/{GALLERY_MARKER}",
            "skills/alpha/SKILL.md",
            "skills/alpha/scripts/run.sh",
            "skills/beta/SKILL.md",
        }
        assert (plugin / "skills/alpha/scripts/run.sh").stat().st_mode & 0o100
        assert not (plugin / "skills/alpha/SKILL.md").stat().st_mode & 0o100
        assert '"name": "acme"' in (plugin / ".claude-plugin/plugin.json").read_text()
        marker = installed_marker(plugin)
        assert marker is not None and (marker.id, marker.commit) == ("acme", "a" * 40)

    @pytest.mark.parametrize(
        ("members", "words"),
        [
            ({**_MEMBERS, "skills/alpha/out": ("../../../../etc/passwd",)}, "link"),
            ({**_MEMBERS, "skills/alpha/../../../evil": b"x"}, "leads out"),
            ({"skills/alpha/SKILL.md": _SKILL}, "no skill at packs/beta"),
        ],
    )
    def test_refuses_links_paths_out_and_missing_skills(
        self, tmp_path: Path, members: Mapping[str, Member], words: str
    ) -> None:
        with pytest.raises(GalleryError, match=words):
            unpack(_entry(), _archive(tmp_path, members), tmp_path / "plugin")

    def test_refuses_an_absolute_path(self, tmp_path: Path) -> None:
        archive: Path = _archive(tmp_path, {**_MEMBERS, "/etc/evil": b"x"})
        with pytest.raises(GalleryError, match="leads out"):
            unpack(_entry(), archive, tmp_path / "plugin")

    def test_refuses_a_plugin_over_the_limit(self, tmp_path: Path) -> None:
        with pytest.raises(GalleryError, match="too big"):
            unpack(_entry(), _archive(tmp_path, _MEMBERS), tmp_path / "plugin", CopyLimit(files=2, bytes=10_000))

    def test_refuses_what_is_not_an_archive(self, tmp_path: Path) -> None:
        archive: Path = tmp_path / "broken.tar.gz"
        archive.write_bytes(b"not a gzip at all")
        with pytest.raises(GalleryError, match="cannot be read"):
            unpack(_entry(), archive, tmp_path / "plugin")

    def test_the_hash_follows_content_names_and_modes(self, tmp_path: Path) -> None:
        first, _ = _pinned(tmp_path / "one")
        changed, _ = _pinned(tmp_path / "two", {**_MEMBERS, "skills/alpha/SKILL.md": _SKILL + b"More.\n"})
        renamed, _ = _pinned(
            tmp_path / "three",
            {
                **{k: v for k, v in _MEMBERS.items() if k != "skills/alpha/scripts/run.sh"},
                "skills/alpha/run.sh": b"#!/bin/sh\necho alpha\n",
            },
        )
        assert len({first.files, changed.files, renamed.files}) == 3


@pytest.fixture
def folders(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "work").mkdir()
    return tmp_path / "work", tmp_path / "plugins"


class TestInstall:
    async def test_installs_the_pinned_plugin_into_dots_plugins_folder(self, folders: tuple[Path, Path]) -> None:
        work, plugins = folders
        entry, archive = _pinned(work)

        where: Path = await install(entry, plugins, _fetch(archive))

        assert where == plugins / "acme"
        assert (where / "skills/beta/SKILL.md").is_file()
        assert files_hash(where / "skills") == entry.files
        # Nothing is left beside it: the work happened in a hidden folder that went.
        assert [path.name for path in plugins.iterdir()] == ["acme"]
        catalog: PluginCatalog = load_plugins(PluginsConfig(dirs=[str(plugins)]), [])
        assert [plugin.name for plugin in catalog.plugins] == ["acme"]

    async def test_a_plugin_whose_files_differ_from_the_pinned_ones_is_not_installed(
        self, folders: tuple[Path, Path]
    ) -> None:
        work, plugins = folders
        entry, archive = _pinned(work)
        other: GalleryEntry = entry.model_copy(update={"files": "f" * 64})

        with pytest.raises(GalleryError, match="not the ones pinned"):
            await install(other, plugins, _fetch(archive))

        assert list(plugins.iterdir()) == []

    async def test_a_plugin_dot_has_already_is_not_downloaded(self, folders: tuple[Path, Path]) -> None:
        work, plugins = folders
        entry, archive = _pinned(work)
        (plugins / "acme").mkdir(parents=True)
        calls: list[str] = []

        with pytest.raises(GalleryError, match="already holds acme"):
            await install(entry, plugins, _fetch(archive, calls))

        assert calls == []

    async def test_a_failed_download_leaves_nothing(self, folders: tuple[Path, Path]) -> None:
        _, plugins = folders

        async def refuse(entry: GalleryEntry, folder: Path) -> Path:
            raise GalleryError("GitHub answered 404 for the plugin's archive", {})

        with pytest.raises(GalleryError, match="404"):
            await install(_entry(), plugins, refuse)

        assert list(plugins.iterdir()) == []


class TestDownload:
    def _client(self, handler: Callable[[httpx.Request], httpx.Response]) -> type[httpx.AsyncClient]:
        transport: httpx.MockTransport = httpx.MockTransport(handler)

        class Client(httpx.AsyncClient):
            def __init__(self, **options: object) -> None:
                super().__init__(transport=transport, follow_redirects=True)

        return Client

    async def test_downloads_the_archive_of_the_pinned_commit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(str(request.url))
            return httpx.Response(200, content=b"archive bytes")

        monkeypatch.setattr(httpx, "AsyncClient", self._client(handler))

        path: Path = await gallery.download_archive(_entry(), tmp_path)

        assert asked == [f"https://codeload.github.com/acme/skills/tar.gz/{'a' * 40}"]
        assert await asyncio.to_thread(path.read_bytes) == b"archive bytes"

    @pytest.mark.parametrize(("status", "size", "words"), [(404, 10, "answered 404"), (200, 300, "larger than")])
    async def test_refuses_a_missing_or_too_large_archive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, size: int, words: str
    ) -> None:
        monkeypatch.setattr(httpx, "AsyncClient", self._client(lambda _: httpx.Response(status, content=b"x" * size)))
        monkeypatch.setattr(gallery, "ARCHIVE_LIMIT_BYTES", 100)

        with pytest.raises(GalleryError, match=words):
            await gallery.download_archive(_entry(), tmp_path)


def test_the_shelf_keeps_the_servers_a_plugin_had_at_the_start(tmp_path: Path) -> None:
    server: McpServerConfig = McpServerConfig(name="acme-tools-fixture", command="tool")
    started: Plugin = Plugin(
        name="acme-tools", root=tmp_path, description=None, version=None, skill_dirs=(), servers=(server,)
    )
    shelf: PluginShelf = PluginShelf(PluginCatalog(plugins=(started,), problems=()))
    later: Plugin = Plugin(
        name="new-one",
        root=tmp_path,
        description=None,
        version=None,
        skill_dirs=(),
        servers=(server.model_copy(update={"name": "new-one-x"}),),
    )

    shelf.take(PluginCatalog(plugins=(started.model_copy(update={"servers": ()}), later), problems=()))

    assert [(plugin.name, [one.name for one in plugin.servers]) for plugin in shelf.plugins] == [
        ("acme-tools", ["acme-tools-fixture"]),
        ("new-one", []),
    ]


def test_the_shelf_keeps_a_plugin_whose_folder_went_while_its_servers_run_until_remove_plugin_forgets_it(
    tmp_path: Path,
) -> None:
    """A plugin whose folder went without Dot stays on the shelf while the servers it started run, so that Remove plugin
    can still stop them (item 497); forgotten, it leaves at the next reading, and one of its name read later starts
    none of them. One that started no server simply leaves."""
    server: McpServerConfig = McpServerConfig(name="acme-tools-fixture", command="tool")
    started: Plugin = Plugin(
        name="acme-tools", root=tmp_path / "gone", description=None, version=None, skill_dirs=(), servers=(server,)
    )
    quiet: Plugin = Plugin(
        name="quiet", root=tmp_path / "quiet", description=None, version=None, skill_dirs=(), servers=()
    )
    shelf: PluginShelf = PluginShelf(PluginCatalog(plugins=(started, quiet), problems=()))

    shelf.take(PluginCatalog(plugins=(), problems=()))
    assert [(plugin.name, [one.name for one in plugin.servers]) for plugin in shelf.plugins] == [
        ("acme-tools", ["acme-tools-fixture"])
    ]

    shelf.forget("acme-tools")
    shelf.take(PluginCatalog(plugins=(), problems=()))
    assert not shelf.plugins
    shelf.take(PluginCatalog(plugins=(started.model_copy(update={"servers": ()}),), problems=()))
    assert [(plugin.name, len(plugin.servers)) for plugin in shelf.plugins] == [("acme-tools", 0)]


async def test_the_shelf_keeps_the_reading_that_began_last(tmp_path: Path) -> None:
    """A reading that began before a plugin went to the Trash and ends after the one that began once it was there (an
    install's and a Remove plugin's) must not bring the plugin back: the shelf takes one reading at a time."""
    removed: Plugin = Plugin(
        name="removed", root=tmp_path / "removed", description=None, version=None, skill_dirs=(), servers=()
    )
    shelf: PluginShelf = PluginShelf(PluginCatalog(plugins=(removed,), problems=()))
    began: threading.Event = threading.Event()
    go_on: threading.Event = threading.Event()

    def before_the_trash() -> PluginCatalog:
        began.set()
        go_on.wait(5)
        return PluginCatalog(plugins=(removed,), problems=())

    first: asyncio.Task[None] = asyncio.create_task(shelf.refill(before_the_trash))
    await asyncio.to_thread(began.wait, 5)
    second: asyncio.Task[None] = asyncio.create_task(shelf.refill(lambda: PluginCatalog(plugins=(), problems=())))
    await asyncio.sleep(0.05)
    go_on.set()
    await asyncio.gather(first, second)

    assert not shelf.plugins
