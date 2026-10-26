"""Proposed for you on Customize › Plugins (item 712), in a real headless Chrome over a real server, wide and light
and on a phone and dark: a proposal's card with its risk, its fit, the review's words in the owner's language and its
details; Install puts it in Dot's plugins folder and the gallery lists it; Skip takes the other away; what the review
refused is folded under them; Look now starts a night. The archive is made here: nothing is downloaded, no model is
called, and nothing is wider than the screen.
"""

import asyncio
import io
import shutil
import tarfile
import time
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import failsafe_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.plugins import scout_service
from dotagent.plugins.gallery import GalleryEntry, files_hash, unpack
from dotagent.plugins.scout_types import Candidate, Finding, Review, ScoutState
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_gallery
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "plugin-scout-page-token"
_COMMIT: str = "e" * 40
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
SHOTS_DIR: Path | None = None


def _archive(path: Path) -> None:
    with tarfile.open(path, "w:gz") as tar:
        data: bytes = b"---\nname: weekly-report\ndescription: Write the weekly sales report.\n---\n\nThe steps.\n"
        info: tarfile.TarInfo = tarfile.TarInfo(f"reports-{_COMMIT}/skills/weekly-report/SKILL.md")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def _proposal(plugin_id: str, name: str, files: str, **more: object) -> Candidate:
    return Candidate(
        id=plugin_id,
        kind="new",
        name=name,
        publisher="Maker Inc",
        tier="a",
        source="Anthropic's official marketplace",
        category="business",
        repository="maker/reports",
        commit=_COMMIT,
        skills=("skills/weekly-report",),
        files=files,
        licence="MIT",
        stars=1200,
        created=date(2025, 1, 1),
        pushed=date(2026, 10, 20),
        findings=(
            Finding(
                rule="script",
                severity="medium",
                path="weekly-report/pull.py",
                detail="1 script a run may be told to run: pull.py",
            ),
        ),
        review=Review(
            risk="low",
            refuse=False,
            does="CRM verilerinden haftalık satış raporunu yazar ve özetler.",
            why="Yalnızca talimat ve küçük bir okuma betiği; dışarıya veri göndermiyor.",
            model="ollama:reviewer",
        ),
        fit=0.82,
        found_at=time.time(),
        **more,  # type: ignore[arg-type]
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[str, Path, Runtime]]:
    home: Path = tmp_path_factory.mktemp("home")
    work: Path = tmp_path_factory.mktemp("work")
    archive: Path = work / "reports.tar.gz"
    _archive(archive)
    probe: Path = work / "probe"
    unpack(
        GalleryEntry(
            id="reports", name="Reports", publisher="Maker", official=True, category="business", does="x",
            repository="maker/reports", commit=_COMMIT, skills=("skills/weekly-report",), files="0" * 64,
            licence="MIT", stars=1, added=date(2026, 10, 1),
        ),
        archive,
        probe,
    )  # fmt: skip
    files: str = files_hash(probe / "skills")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )

    async def fetch(entry: GalleryEntry, folder: Path) -> Path:
        copy: Path = folder / "archive.tar.gz"
        await asyncio.to_thread(shutil.copyfile, archive, copy)
        return copy

    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    patch.setattr(routes_gallery, "GALLERY", ())
    patch.setattr(routes_gallery, "download_archive", fetch)

    async def night(given: Runtime, now: float) -> ScoutState:
        # Never a real look from a test: no GitHub, no review model.
        return await scout_service.load(given.store)

    patch.setattr(scout_service, "night", night)
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    refused: Candidate = _proposal("sync-everything", "Sync Everything", "0" * 64).model_copy(
        update={
            "state": "refused",
            "repository": "someone/sync",
            "review": Review(
                risk="high",
                refuse=True,
                does="Dosyaları dışarı gönderir.",
                why="Klasörü bir toplama servisine yolluyor.",
                model="r",
            ),
        }
    )
    await scout_service.save(
        runtime.store,
        ScoutState(
            candidates=(
                _proposal("reports", "Sales Reports", files),
                _proposal("brand-voice", "Brand Voice Writer", "0" * 64).model_copy(
                    update={"tier": "b", "source": "GitHub topic agent-skills", "fit": 0.4, "findings": ()}
                ),
                refused,
            ),
            last_run=time.time() - 3600,
            last_summary="2 proposed, 1 refused by the review, 3 refused by the rules, 0 unusable, 0 failed",
        ),
    )
    manager: RunManager = RunManager(runtime)
    try:
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime, manager=manager, scheduler=None, token=TOKEN, host="127.0.0.1", port=port,
            started_at=time.time(), stopping=stopping,
        )  # fmt: skip
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield f"http://127.0.0.1:{port}", home / "plugins", runtime
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def _fits(page: Page) -> None:
    wide: int = await page.evaluate("document.scrollingElement.scrollWidth - window.innerWidth")
    assert wide <= 0, wide


async def test_proposals_are_shown_installed_and_skipped_wide_and_on_a_phone(
    world: tuple[str, Path, Runtime], mac_at_rest: None
) -> None:
    address, plugins, runtime = world
    port: int = int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/customize/plugins")
                section: Locator = page.locator(".scout-section")
                await expect(section.locator(".gallery-section-title")).to_have_text("Proposed for you")
                cards: Locator = section.locator(".scout-card")
                await expect(cards).to_have_count(2 if width > 400 else 1)
                if width > 400:
                    reports: Locator = cards.first
                    await expect(reports.locator(".gallery-card-name")).to_have_text("Sales Reports")
                    await expect(reports.locator(".scout-badges .badge").first).to_have_text("Low risk")
                    await expect(reports.locator(".scout-badges")).to_contain_text("Fits your work 82%")
                    await expect(reports.locator(".gallery-card-does")).to_have_text(
                        "CRM verilerinden haftalık satış raporunu yazar ve özetler."
                    )
                    await expect(reports.locator(".gallery-card-by")).to_contain_text("Anthropic or its maker")
                    await reports.locator(".scout-details > summary").click()
                    await expect(reports.locator(".scout-findings")).to_contain_text("weekly-report/pull.py")
                    await expect(section.locator(".scout-line")).to_contain_text("Dot looks every night at 03:30")
                    await expect(section.locator(".scout-refused > summary")).to_have_text(
                        "1 plugin refused by Dot's review"
                    )
                    await _fits(page)
                    if SHOTS_DIR is not None:
                        await section.screenshot(path=str(SHOTS_DIR / f"scout-{width}.png"))
                    await reports.get_by_role("button", name="Install Sales Reports").click()
                    await expect(cards).to_have_count(1)
                    await expect(page.locator(".gallery-grid .gallery-card[data-plugin='reports']")).to_have_count(1)
                    assert (plugins / "reports" / "skills" / "weekly-report" / "SKILL.md").is_file()
                else:
                    await expect(cards.first.locator(".gallery-card-name")).to_have_text("Brand Voice Writer")
                    await expect(cards.first.locator(".gallery-card-by")).to_contain_text("Community")
                    await _fits(page)
                    if SHOTS_DIR is not None:
                        await section.screenshot(path=str(SHOTS_DIR / f"scout-{width}.png"))
                    await cards.first.get_by_role("button", name="Skip Brand Voice Writer").click()
                    await expect(cards).to_have_count(0)
                    await expect(section.locator(".empty-state")).to_contain_text("Nothing waiting")
                await page.close()
        finally:
            await browser.close()
    state: ScoutState = await scout_service.load(runtime.store)
    assert {one.id: one.state for one in state.candidates} == {
        "reports": "installed",
        "brand-voice": "skipped",
        "sync-everything": "refused",
    }


async def test_look_now_refused_by_the_emergency_stop_gives_its_button_back(
    world: tuple[str, Path, Runtime], mac_at_rest: None
) -> None:
    """The review of the thirteenth push: refused (409, the emergency stop), Look now stayed disabled until the page
    was loaded again."""
    address, _plugins, _runtime = world
    port: int = int(address.rpartition(":")[2])
    stop: Path = failsafe_path()
    await asyncio.to_thread(stop.touch)
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/customize/plugins")
                look: Locator = page.locator(".scout-look")
                await expect(look).to_be_enabled()
                async with page.expect_response(
                    lambda response: response.url.endswith("/api/plugin-scout/look")
                ) as sent:
                    await look.click()
                assert (await sent.value).status == 409
                await expect(look).to_be_enabled()
                await expect(look).to_have_text("Look now")
            finally:
                await browser.close()
    finally:
        await asyncio.to_thread(stop.unlink)
