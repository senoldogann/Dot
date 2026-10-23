"""Customize › Plugins, the gallery (item 542), in a real headless Chrome over a real server, wide and light and on a
phone and dark. The head with its search; New & noteworthy with the featured plugin and All plugins with every one,
each card with its mark (its logo, else a skill's puzzle piece on a tile of its kind's colour), the check of a plugin
from its maker, who publishes it, its kind and its skills; the order and the search. Install puts the plugin in Dot's
plugins folder and its card says Installed; Installed lists it with the plugin put there by hand; Remove asks first and
moves it to the Trash. A plugin's own page lists its skills and where it comes from, and a service's page at the address
the tab had before item 542 opens under Connectors. The gallery is three plugins of an archive made here: nothing is
downloaded, and nothing is wider than the screen.
"""

import asyncio
import io
import json
import re
import shutil
import tarfile
import time
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.plugins.gallery import GalleryCategory, GalleryEntry, files_hash, unpack
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_gallery, routes_mcp
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "plugin-gallery-page-token"
_COMMIT: str = "d" * 40
_SKILLS: tuple[str, ...] = ("acme-alpha", "acme-beta")
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


def _skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: Does the {name} work.\n---\n\nThe instructions.\n"


def _archive(path: Path) -> None:
    """GitHub's archive of a repository with two skill folders, as a tar.gz under one top folder."""
    with tarfile.open(path, "w:gz") as tar:
        members: list[tuple[str, str]] = [("README.md", "the repository")]
        members += [(f"skills/{name}/SKILL.md", _skill(name)) for name in _SKILLS]
        for name, content in members:
            data: bytes = content.encode()
            info: tarfile.TarInfo = tarfile.TarInfo(f"acme-skills-{_COMMIT}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _home(home: Path) -> None:
    """Dot's config in ``home``, and a plugin put in its plugins folder by hand."""
    by_hand: Path = home / "plugins" / "by-hand" / ".claude-plugin"
    by_hand.mkdir(parents=True)
    (by_hand / "plugin.json").write_text(
        json.dumps({"name": "by-hand", "description": "Put here by hand."}), encoding="utf-8"
    )
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )


def _entry(
    plugin_id: str,
    name: str,
    files: str,
    *,
    category: GalleryCategory,
    official: bool,
    featured: bool,
    stars: int,
    added: date,
    logo: str | None,
) -> GalleryEntry:
    return GalleryEntry(
        id=plugin_id,
        name=name,
        publisher="Acme",
        official=official,
        category=category,
        does=f"The {name} of Acme.",
        repository="acme/skills",
        commit=_COMMIT,
        skills=tuple(f"skills/{skill}" for skill in _SKILLS),
        files=files,
        licence="MIT",
        featured=featured,
        stars=stars,
        added=added,
        logo=logo,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[str, Path, Path]]:
    """A real server over a runtime in a throw-away home with a plugin put there by hand; the gallery is three plugins
    of the archive made here, the most starred one featured, one wearing a logo. Yields the server's address, Dot's
    plugins folder and the Trash."""
    home: Path = tmp_path_factory.mktemp("home")
    trash: Path = tmp_path_factory.mktemp("trash")
    work: Path = tmp_path_factory.mktemp("work")
    archive: Path = work / "acme.tar.gz"
    _archive(archive)
    probe: Path = work / "probe"
    pinned: str = "0" * 64
    unpack(
        _entry(
            "acme",
            "Acme",
            pinned,
            category="development",
            official=True,
            featured=True,
            stars=1,
            added=date(2026, 10, 1),
            logo=None,
        ),
        archive,
        probe,
    )
    pinned = files_hash(probe / "skills")
    gallery: tuple[GalleryEntry, ...] = (
        _entry(
            "acme",
            "Acme Skills",
            pinned,
            category="development",
            official=True,
            featured=True,
            stars=500,
            added=date(2026, 10, 1),
            logo=None,
        ),
        _entry(
            "brand-kit",
            "Brand Kit",
            pinned,
            category="design",
            official=False,
            featured=False,
            stars=50,
            added=date(2026, 10, 20),
            logo=None,
        ),
        _entry(
            "base-tools",
            "Base Tools",
            pinned,
            category="data",
            official=True,
            featured=False,
            stars=5,
            added=date(2026, 10, 10),
            logo="supabase-icon.svg",
        ),
    )
    _home(home)

    async def fetch(entry: GalleryEntry, folder: Path) -> Path:
        copy: Path = folder / "archive.tar.gz"
        await asyncio.to_thread(shutil.copyfile, archive, copy)
        return copy

    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    patch.setattr(routes_gallery, "GALLERY", gallery)
    patch.setattr(routes_gallery, "download_archive", fetch)
    patch.setattr(routes_mcp, "trash_folder", lambda: trash)
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    try:
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}", home / "plugins", trash
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


def _card(page: Page, plugin_id: str) -> Locator:
    """The card of ``plugin_id`` in All plugins."""
    return page.locator(f".gallery-grid .gallery-card[data-plugin='{plugin_id}']")


async def _fits(page: Page) -> None:
    """Nothing is wider than the screen."""
    wide: int = await page.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
    assert wide <= 0, wide


async def test_the_gallery_installs_lists_and_removes_a_plugin(
    world: tuple[str, Path, Path], tmp_path: Path, mac_at_rest: None
) -> None:
    address, plugins, trash = world
    port: int = int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 860}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/customize")
                await expect(page).to_have_url(f"{address}/#/customize/plugins")
                await expect(page.locator(".customize-tabs a")).to_have_text(["Plugins", "Skills", "Connectors"])
                await expect(page.locator(".customize-tabs a[aria-current='page']")).to_have_text("Plugins")
                await expect(page.locator(".customize-hero-title")).to_have_text("Discover plugins")
                # New & noteworthy holds the featured plugin; All plugins every one, the most starred first.
                await expect(page.locator(".gallery-featured .gallery-card")).to_have_count(1)
                cards: Locator = page.locator(".gallery-grid .gallery-card")
                await expect(cards).to_have_count(3)
                order: str = "cards => cards.map(card => card.dataset.plugin)"
                assert await cards.evaluate_all(order) == ["acme", "brand-kit", "base-tools"]
                # A plugin with no logo wears a skill's puzzle piece on a tile of its kind's colour; a logo is a
                # picture in its own colours on a light tile.
                acme: Locator = _card(page, "acme")
                await expect(acme.locator(".plugin-mark[data-tint] svg[data-icon='puzzle']")).to_have_count(1)
                tile: str = await acme.locator(".plugin-mark").evaluate(
                    "(mark) => getComputedStyle(mark).backgroundImage"
                )
                assert tile.startswith("linear-gradient"), tile
                logo: Locator = _card(page, "base-tools").locator(".plugin-mark img.plugin-logo")
                await expect(logo).to_have_attribute("src", "/icons/plugins/supabase-icon.svg")
                assert await logo.evaluate("(image) => image.decode().then(() => image.naturalWidth > 0)")
                # The check of a plugin from its maker, and who publishes it, its kind and its skills.
                await expect(acme.locator(".gallery-verified")).to_have_count(1)
                await expect(_card(page, "brand-kit").locator(".gallery-verified")).to_have_count(0)
                await expect(acme.locator(".gallery-card-by")).to_have_text("Acme · Development · 2 skills")
                await expect(page.locator(".gallery")).not_to_contain_text("null")
                await expect(page.locator(".gallery")).not_to_contain_text("undefined")
                await _fits(page)
                await page.screenshot(path=str(tmp_path / f"gallery-{width}-{scheme}.png"), full_page=True)
                # Newest puts the plugin the gallery took last first.
                await page.get_by_role("button", name="Newest").click()
                assert await cards.evaluate_all(order) == ["brand-kit", "base-tools", "acme"]
                await page.get_by_role("button", name="Popular").click()
                # The search reads names and kinds; New & noteworthy steps aside while it is used.
                search: Locator = page.get_by_role("searchbox", name="Search plugins")
                await search.press_sequentially("design")
                await expect(search).to_be_focused()
                await expect(cards).to_have_count(1)
                await expect(_card(page, "brand-kit")).to_be_visible()
                await expect(page.locator(".gallery-section[data-section='noteworthy']")).to_be_hidden()
                await search.fill("nothing like it")
                await expect(page.locator(".gallery-grid .empty-state")).to_contain_text("No plugin matches the search")
                await search.fill("")
                await expect(cards).to_have_count(3)
                await page.close()

            page = await browser.new_page(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.goto(f"{address}/#/customize/plugins")
            acme = _card(page, "acme")
            # Install: the plugin's skills land in Dot's plugins folder and its card says Installed.
            await acme.get_by_role("button", name="Install Acme Skills").click()
            await expect(acme.locator(".gallery-installed")).to_have_text("Installed")
            await expect(page.locator(".toast")).to_contain_text("Acme Skills is installed")
            for skill in _SKILLS:
                assert (plugins / "acme" / "skills" / skill / "SKILL.md").is_file()
            await expect(
                page.locator(".gallery-featured .gallery-card[data-plugin='acme'] .gallery-installed")
            ).to_be_visible()
            # Installed lists it, and the plugin put in the folder by hand apart, with its Remove.
            await page.get_by_role("button", name=re.compile(r"^Installed 2$")).click()
            await expect(page.locator(".gallery-section[data-section='noteworthy']")).to_be_hidden()
            await expect(page.locator(".gallery-grid .gallery-card")).to_have_count(2)
            await expect(page.locator(".gallery-others-title")).to_have_text("Not from the gallery")
            by_hand: Locator = page.locator(".gallery-card[data-plugin='by-hand']")
            await expect(by_hand).to_contain_text("Put here by hand.")
            await expect(by_hand.get_by_role("button", name="Remove by-hand")).to_be_visible()
            # Remove asks first, then moves the plugin to the Trash.
            await acme.get_by_role("button", name="Remove Acme Skills").click()
            dialog: Locator = page.get_by_role("dialog")
            await expect(dialog).to_contain_text("Remove Acme Skills from Dot?")
            await dialog.get_by_role("button", name="Remove").click()
            await expect(page.locator(".toast").last).to_contain_text("Acme Skills went to the Trash")
            await expect(acme).to_have_count(0)
            assert not (plugins / "acme").exists()
            assert (trash / "acme" / ".claude-plugin" / "plugin.json").is_file()
            await page.get_by_role("button", name=re.compile(r"^All \d+$")).click()
            await expect(acme.get_by_role("button", name="Install Acme Skills")).to_be_visible()
            # A plugin's own page: its skills and where it comes from.
            await acme.locator(".gallery-card-name").click()
            await expect(page).to_have_url(f"{address}/#/customize/plugins/acme")
            await expect(page.locator(".gallery-page-head .plugin-title")).to_have_text("Acme Skills")
            await expect(page.locator(".gallery-skills li")).to_have_text(list(_SKILLS))
            source: Locator = page.locator(".gallery-facts a")
            await expect(source).to_have_text("github.com/acme/skills")
            # The commit Dot pins, at the folder its skills share.
            await expect(source).to_have_attribute("href", f"https://github.com/acme/skills/tree/{_COMMIT}/skills")
            await expect(page.locator(".gallery-facts")).to_contain_text("dddddd")
            await expect(page.get_by_role("button", name="Install Acme Skills")).to_be_visible()
            await page.screenshot(path=str(tmp_path / "gallery-plugin-1280.png"), full_page=True)
            # A service's page at the address Plugins had before the gallery is under Connectors.
            await page.goto(f"{address}/#/customize/plugins/notion")
            await expect(page).to_have_url(f"{address}/#/customize/connectors/notion")
            await expect(page.locator(".plugin-hero")).to_be_visible()
            await page.close()

            phone: Page = await browser.new_page(
                viewport={"width": 390, "height": 844}, color_scheme="dark", reduced_motion="reduce"
            )
            await phone.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await phone.goto(ui_url("127.0.0.1", port, TOKEN))
            await phone.goto(f"{address}/#/customize/plugins/acme")
            await expect(phone.locator(".gallery-skills li")).to_have_count(len(_SKILLS))
            await _fits(phone)
            await phone.screenshot(path=str(tmp_path / "gallery-plugin-390-dark.png"), full_page=True)
        finally:
            await browser.close()


async def test_a_plugin_whose_pin_the_gallery_moved_since_says_older_version(
    world: tuple[str, Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac_at_rest: None
) -> None:
    """Item 558: after a newer Dot re-pins a plugin the card said Installed while the runs read the skills of the old
    commit. Its badge says Older version, with the two commits in its title and on the plugin's page, and Remove is
    there, as Install is refused until it is used; wide and on a phone."""
    address, _plugins, _trash = world
    port: int = int(address.rpartition(":")[2])
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60.0) as api:
        assert (await api.post("/api/plugin-gallery/acme")).status_code == 200
        try:
            moved: tuple[GalleryEntry, ...] = tuple(
                entry.model_copy(update={"commit": "e" * 40}) if entry.id == "acme" else entry
                for entry in routes_gallery.GALLERY
            )
            monkeypatch.setattr(routes_gallery, "GALLERY", moved)
            async with async_playwright() as playwright:
                browser: Browser = await playwright.chromium.launch(headless=True)
                try:
                    for width in (1280, 390):
                        page: Page = await browser.new_page(
                            viewport={"width": width, "height": 860}, reduced_motion="reduce"
                        )
                        await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                        await page.goto(ui_url("127.0.0.1", port, TOKEN))
                        await page.goto(f"{address}/#/customize/plugins")
                        acme: Locator = _card(page, "acme")
                        older: Locator = acme.locator(".gallery-outdated")
                        await expect(older).to_have_text("Older version")
                        await expect(older).to_have_attribute(
                            "title", re.compile(r"Installed from d{7}; the gallery pins e{7} now")
                        )
                        await expect(acme.get_by_role("button", name="Remove Acme Skills")).to_be_visible()
                        await expect(acme.get_by_role("button", name="Install Acme Skills")).to_have_count(0)
                        # Installed lists it, with the plugin put in the folder by hand.
                        await page.get_by_role("button", name=re.compile(r"^Installed 2$")).click()
                        await expect(acme).to_be_visible()
                        await _fits(page)
                        # The plugin's own page says which commit its files are from, beside the one pinned now.
                        await acme.locator(".gallery-card-name").click()
                        facts: Locator = page.locator(".gallery-facts")
                        await expect(facts).to_contain_text("Installed from")
                        await expect(facts).to_contain_text("ddddddd")
                        await expect(facts).to_contain_text("eeeeeee")
                        await _fits(page)
                        await page.screenshot(path=str(tmp_path / f"gallery-older-version-{width}.png"), full_page=True)
                        await page.close()
                finally:
                    await browser.close()
        finally:
            assert (await api.delete("/api/installed-plugins/acme")).status_code == 200
