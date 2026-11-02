"""Settings › Rules and memory › Self-repair (item 921) in a real browser: off with no repository, it says how to turn
it on and offers no Run now; on, it names when it runs and how it merges, the account of ``claude`` signed in, each
problem of the book with its status and the link of its pull request, and each session; on a phone's width every row
stays inside the screen."""

import asyncio
import dataclasses
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.repair.night import SESSIONS_KEY, RepairSession, SessionLog
from dotagent.repair.problems import BOOK_KEY, EMPTY_BOOK, Sighting, with_problem, with_sightings
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "repair-page-token"
PR: str = "https://github.com/senoldogann/Dot/pull/950"
# Whether every row of the page keeps inside the screen.
FITS: str = """() => [...document.querySelectorAll(".set-row")].every(
  (row) => row.getBoundingClientRect().right <= window.innerWidth + 1
)"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    claude: Path = tmp_path_factory.mktemp("claude")
    (claude / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": "owner@example.invalid", "organizationName": "Org"}}),
        encoding="utf-8",
    )
    built.shell_env["CLAUDE_CONFIG_DIR"] = str(claude)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@asynccontextmanager
async def _serving(runtime: Runtime) -> AsyncIterator[tuple[str, int]]:
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
        yield "127.0.0.1", port
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


async def _open(browser: Browser, address: tuple[str, int], width: int) -> Page:
    page: Page = await browser.new_page(viewport={"width": width, "height": 900})
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    host, port = address
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/settings/repair")
    return page


async def test_off_it_says_how_to_turn_it_on(browser: Browser, runtime: Runtime) -> None:
    async with _serving(runtime) as address:
        page: Page = await _open(browser, address, 1280)
        try:
            await expect(
                page.get_by_text("Set [repair] repo to the checkout of Dot's code to turn it on.")
            ).to_be_visible()
            await expect(page.get_by_role("button", name="Run now")).to_have_count(0)
            await expect(page.get_by_text("owner@example.invalid · Org")).to_be_visible()
            await expect(page.get_by_text("No problems")).to_be_visible()
        finally:
            await page.close()


@pytest.mark.parametrize("width", [1280, 390])
async def test_on_it_shows_the_problems_their_fixes_and_the_sessions(
    browser: Browser, runtime: Runtime, width: int, tmp_path: Path
) -> None:
    seen: list[Sighting] = [
        Sighting(kind="daemon", words="schedule_start_failed: no such effort", evidence="a", at=time.time() - 60),
        Sighting(kind="daemon", words="schedule_start_failed: no such effort", evidence="b", at=time.time() - 30),
        Sighting(
            kind="tool_error", words="browser_open: taking the page snapshot failed", evidence="c", at=time.time()
        ),
    ]
    book = with_sightings(EMPTY_BOOK, seen, time.time())
    fixed = next(problem for problem in book.problems if problem.kind == "daemon")
    book = with_problem(book, fixed.model_copy(update={"status": "fixed", "pr_url": PR}))
    session = RepairSession(
        problem_id=fixed.id,
        account="owner@example.invalid",
        session_id="s-1",
        started_at=time.time() - 3600,
        ended_at=time.time() - 3000,
        rounds=2,
        input_tokens=120_000,
        output_tokens=9_000,
        cost_usd=1.2,
        end="merged",
        pr_url=PR,
        note="Merged; Dot restarts once idle.",
        window=None,
    )
    await runtime.store.kv_set(BOOK_KEY, book.model_dump_json())
    await runtime.store.kv_set(SESSIONS_KEY, SessionLog(sessions=(session,)).model_dump_json())
    repair = runtime.config.repair.model_copy(update={"repo": "/Users/owner/Desktop/Dot", "merge": "on_pass"})
    on: Runtime = dataclasses.replace(runtime, config=runtime.config.model_copy(update={"repair": repair}))
    async with _serving(on) as address:
        page: Page = await _open(browser, address, width)
        try:
            await expect(page.get_by_text("On: 05:30 each night, merged once every check passes")).to_be_visible()
            await expect(page.get_by_role("button", name="Run now")).to_be_enabled()
            problems = page.locator("section.set-section").filter(has=page.get_by_role("heading", name="Problems"))
            await expect(problems.locator(".set-row")).to_have_count(2)
            await expect(problems.get_by_text("Fixed", exact=True)).to_be_visible()
            await expect(problems.get_by_text("Open", exact=True)).to_be_visible()
            await expect(problems.get_by_role("link", name="Pull request")).to_have_attribute("href", PR)
            sessions = page.locator("section.set-section").filter(has=page.get_by_role("heading", name="Sessions"))
            await expect(sessions.get_by_text("Merged", exact=True)).to_be_visible()
            await expect(sessions).to_contain_text("2 rounds · 129k tokens · owner@example.invalid")
            await expect(sessions).to_contain_text("schedule_start_failed: no such effort")
            assert await page.evaluate(FITS)
            await page.screenshot(path=tmp_path / f"repair-{width}.png", full_page=True)
        finally:
            await page.close()
