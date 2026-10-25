"""The context under the recap of a run's Run tab (item 539), in a real browser: an ended run shows the files its chat
wrote under Artifacts, the page it fetched under Web reading (and not the one it did not get) and the note it saved
under Memory updated, with the recap first and the time it was written; a row of Artifacts opens the Changes tab where
the diff is. Each row wears its mark on a tile, and a file its language's own logo (item 547). A run that used no skill
and no MCP server shows no Skills & MCP section (item 546). At 1280 beside the conversation and at 390 in the phone's
sheet, the panel no wider than the screen."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, site_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.site import serve_site
from dotagent.evals.types import ScriptedCall, ScriptedTurn, SitePage
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "context-panel-token"
RECAP: str = "Wrote the four notes and saved the owner's preference.\nNothing waits for you."
ANSWER: str = "The four notes are written and the note saved."
# The pages the first turn of a chat reads (item 650): more than the eight rows Web reading shows before it scrolls.
PAGES_READ: int = 10
KEPT: str = "The user prefers short answers."
WAIT_S: float = 60.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SIZES: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


def _turns(workspace: Path, tags: list[str], site: str) -> list[ScriptedTurn]:
    """For each size: a run of four writes, one in each of four languages, a note saved, a page fetched and its answer,
    then the recap. Each size writes its own files and saves its own note, so that neither run finds the other's
    already there."""
    turns: list[ScriptedTurn] = []
    for tag in tags:
        turns.extend(
            ScriptedTurn(
                text="",
                calls=(
                    ScriptedCall(
                        tool="write_file",
                        arguments={
                            "path": str(workspace / f"note-{tag}-{ext}"),
                            "content": f"Note {tag} {ext.rpartition('.')[2]}\n",
                        },
                    ),
                ),
            )
            for ext in ("0.py", "1.ts", "2.md", "3.rs")
        )
        turns.append(
            ScriptedTurn(
                text="",
                calls=(ScriptedCall(tool="memory_save", arguments={"content": f"{KEPT} ({tag})", "tags": []}),),
            )
        )
        turns.append(
            ScriptedTurn(text="", calls=(ScriptedCall(tool="web_fetch", arguments={"url": f"{site}/about/"}),))
        )
        # A page the run asked for and did not get: not one it read.
        turns.append(
            ScriptedTurn(text="", calls=(ScriptedCall(tool="web_fetch", arguments={"url": f"{site}/missing/"}),))
        )
        turns.append(
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": ANSWER, "success": True}),)
            )
        )
        turns.append(ScriptedTurn(text=RECAP, calls=()))
    return turns


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    pages = (
        SitePage(
            path="/about/",
            status=200,
            body="<!doctype html><title>About</title><p>About the notes.</p>",
            location=None,
        ),
        *(
            SitePage(
                path=f"/page-{n}/", status=200, body=f"<!doctype html><title>Page {n}</title><p>{n}</p>", location=None
            )
            for n in range(PAGES_READ)
        ),
    )
    with serve_site(pages) as address:
        yield address


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config() -> AppConfig:
    """The scripted model for the runs and their recaps, Ollama's address a closed port, the loopback page the run's
    own site."""
    config: AppConfig = site_config(eval_config(load_default_config()))
    return config.model_copy(
        update={
            "models": config.models.model_copy(update={"main": SCRIPTED_MODEL, "fast": SCRIPTED_MODEL}),
            "providers": config.providers.model_copy(
                update={"ollama": config.providers.ollama.model_copy(update={"base_url": "http://127.0.0.1:9"})}
            ),
        }
    )


@asynccontextmanager
async def _daemon(
    login_env: dict[str, str], site: str, turns: Callable[[Path], list[ScriptedTurn]] | None = None
) -> AsyncIterator[tuple[RunManager, str]]:
    """The daemon in a throw-away home, served at the address it yields; the scripted model takes ``turns`` of the
    workspace, else the four notes of each size."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            await runtime.router.register(
                _PROVIDER,
                ScriptedProvider(
                    _PROVIDER,
                    turns(runtime.workspace)
                    if turns is not None
                    else _turns(runtime.workspace, [str(width) for width, _ in _SIZES], site),
                ),
            )
            manager: RunManager = RunManager(runtime)
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
                yield manager, f"http://127.0.0.1:{port}"
            finally:
                server.should_exit = True
                stopping.set()
                await serving
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _one_size(
    browser: Browser, manager: RunManager, address: str, site: str, width: int, scheme: str, shots: Path
) -> None:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900},
        color_scheme="dark" if scheme == "dark" else "light",
        reduced_motion="reduce",
    )
    try:
        await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
        run: RunRecord = await manager.start(
            f"Write four notes and remember one ({width})",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "ask",
            False,
            (),
        )
        async with asyncio.timeout(WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        await page.goto(f"{address}/#/runs/{run.id}")
        layout: Locator = page.locator(".run-layout")
        await expect(page.locator(".answer")).to_contain_text(ANSWER)
        if width < 960 or await layout.get_attribute("data-inspector") != "on":
            await page.get_by_role("button", name="Task details", exact=True).click()
        details: Locator = page.locator(".inspector")
        await details.get_by_role("tab", name="Task").click()
        # The recap still heads the tab, and says when it was written.
        recap: Locator = details.locator("#panel-run-recap")
        await expect(recap.locator(".recap-text p")).to_have_text(RECAP.split("\n"))
        await expect(recap.locator(".recap-meta")).to_contain_text("Updated")
        # The files the chat wrote, each once, its language's own logo on a light tile, a row opening the Changes tab.
        # A run that used no skill and no MCP server has no Skills & MCP section.
        await expect(details.locator("#panel-run-skills")).to_be_hidden()
        artifacts: Locator = details.locator("#panel-run-artifacts")
        await expect(artifacts).to_be_visible()
        await expect(artifacts.locator(".context-row")).to_have_count(4)
        await expect(artifacts.locator(".context-tile-logo")).to_have_count(4)
        await expect(artifacts.locator(".context-kind")).to_have_count(0)
        logos: list[str] = await artifacts.locator(".context-logo").evaluate_all(
            "(logos) => logos.map((logo) => logo.getAttribute('src'))"
        )
        assert sorted(logos) == [
            "/icons/languages/markdown.svg",
            "/icons/languages/python.svg",
            "/icons/languages/rust.svg",
            "/icons/languages/typescript.svg",
        ], logos
        await expect(
            details.locator(".panel-section", has=page.locator("#panel-run-artifacts")).locator(".panel-summary")
        ).to_have_text("4 files")
        # The page it fetched, under the host and path it read; the one it did not get is no reading.
        reading: Locator = details.locator("#panel-run-reading")
        await expect(reading).to_be_visible()
        await expect(reading.locator(".context-row")).to_have_count(1)
        await expect(reading.locator(".context-tile .icon[data-icon='globe']")).to_have_count(1)
        await expect(reading.locator(".context-name")).to_have_text(f"{site.removeprefix('http://')}/about/")
        await expect(reading.locator("a.context-row")).to_have_attribute("href", f"{site}/about/")
        await expect(
            details.locator(".panel-section", has=page.locator("#panel-run-reading")).locator(".panel-summary")
        ).to_have_text("1 page")
        # The note it saved.
        memory: Locator = details.locator("#panel-run-memory")
        await expect(memory).to_be_visible()
        await expect(memory.locator(".context-name")).to_have_text("Saved to memory")
        await expect(memory.locator(".context-note")).to_contain_text(KEPT)
        await expect(
            details.locator(".panel-section", has=page.locator("#panel-run-memory")).locator(".panel-summary")
        ).to_have_text("1 note")
        # The recap is first; Artifacts, Web reading and Memory updated come after it.
        titles: list[str] = await details.locator(".panel-section-title").all_inner_texts()
        assert titles[0] == "Recap", titles
        assert titles.index("Files made") < titles.index("Web reading") < titles.index("Memory updated"), titles
        await page.screenshot(path=str(shots / f"context-{width}-{scheme}.png"))
        # A row of Artifacts opens the Changes tab, where the diff is.
        await artifacts.locator(".context-row").first.click()
        changes_tab = details.get_by_role("tab", name="Changes")
        await expect(changes_tab).to_have_attribute("aria-selected", "true")
        await expect(details.locator("#panel-run-changes details.change")).to_have_count(4)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        await page.close()


async def test_the_run_tab_shows_what_the_run_wrote_read_and_remembered_under_its_recap(
    login_env: dict[str, str], site: str, tmp_path: Path
) -> None:
    async with _daemon(login_env, site) as (manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            for width, scheme in _SIZES:
                await _one_size(browser, manager, address, site, width, scheme, tmp_path)
        finally:
            await browser.close()


def _two_turns(site: str) -> Callable[[Path], list[ScriptedTurn]]:
    """A chat of two turns (item 650): the first saves a note, reads ten pages and gets its recap; the second only
    answers, too short a turn for a recap of its own."""

    def turns(_workspace: Path) -> list[ScriptedTurn]:
        reads: list[ScriptedTurn] = [
            ScriptedTurn(text="", calls=(ScriptedCall(tool="web_fetch", arguments={"url": f"{site}/page-{n}/"}),))
            for n in range(PAGES_READ)
        ]
        # The note is saved before the pages are read: a note saved after reading outside content asks the owner.
        return [
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool="memory_save", arguments={"content": f"{KEPT} (chat)", "tags": []}),)
            ),
            *reads,
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": ANSWER, "success": True}),)
            ),
            ScriptedTurn(text=RECAP, calls=()),
            ScriptedTurn(
                text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done again.", "success": True}),)
            ),
        ]

    return turns


async def _task_tab(page: Page) -> Locator:
    layout: Locator = page.locator(".run-layout")
    await expect(page.locator(".answer").last).to_be_visible()
    if await layout.get_attribute("data-inspector") != "on":
        await page.get_by_role("button", name="Task details", exact=True).click()
    details: Locator = page.locator(".inspector")
    await details.get_by_role("tab", name="Task").click()
    return details


async def test_a_new_message_keeps_the_chats_context_and_web_reading_scrolls_past_eight_rows(
    login_env: dict[str, str], site: str, tmp_path: Path
) -> None:
    """Item 650: the panel of a chat's second turn shows what its first read and saved, under this turn's own, and
    the first turn's recap until this one has its own; ten pages read scroll inside Web reading."""
    async with _daemon(login_env, site, _two_turns(site)) as (manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        try:
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            first: RunRecord = await manager.start(
                "Read the ten pages",
                "task",
                SCRIPTED_MODEL,
                None,
                "test",
                None,
                None,
                "standard",
                None,
                "ask",
                False,
                (),
            )
            async with asyncio.timeout(WAIT_S):
                assert (await manager.wait(first.id)).status == "done"
            # The first turn's page writes its recap.
            await page.goto(f"{address}/#/runs/{first.id}")
            details: Locator = await _task_tab(page)
            await expect(details.locator("#panel-run-recap .recap-text p")).to_have_text(RECAP.split("\n"))
            second: RunRecord = await manager.start(
                "Once more",
                "task",
                SCRIPTED_MODEL,
                first.thread_id,
                "test",
                None,
                None,
                "standard",
                None,
                "ask",
                False,
                (),
            )
            async with asyncio.timeout(WAIT_S):
                assert (await manager.wait(second.id)).status == "done"
            await page.goto(f"{address}/#/runs/{second.id}")
            details = await _task_tab(page)
            recap: Locator = details.locator("#panel-run-recap")
            await expect(recap.locator(".recap-text p")).to_have_text(RECAP.split("\n"))
            await expect(recap.locator(".recap-meta")).to_contain_text("From the task before")
            reading: Locator = details.locator("#panel-run-reading")
            await expect(reading.locator(".context-row")).to_have_count(PAGES_READ)
            await expect(
                details.locator(".panel-section", has=page.locator("#panel-run-reading")).locator(".panel-summary")
            ).to_have_text(f"{PAGES_READ} pages")
            # The newest the first turn read stands first.
            await expect(reading.locator(".context-name").first).to_have_text(
                f"{site.removeprefix('http://')}/page-{PAGES_READ - 1}/"
            )
            box: dict[str, int] = await reading.locator(".context-scroll").evaluate(
                "(list) => ({ shown: list.clientHeight, whole: list.scrollHeight })"
            )
            assert box["shown"] < box["whole"], box
            memory: Locator = details.locator("#panel-run-memory")
            await expect(memory.locator(".context-note")).to_contain_text(f"{KEPT} (chat)")
            await page.screenshot(path=str(tmp_path / "chat-context.png"))
        finally:
            await page.close()
            await browser.close()
