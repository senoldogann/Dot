"""Settings › Rules and memory › Self-repair (item 921) in a real browser: off with no repository, it says how to turn
it on and offers no Run now; on, it names when it runs and how it merges, the account of ``claude`` signed in, each
problem of the book with its status and the link of its pull request, and each session; on a phone's width every row
stays inside the screen."""

import asyncio
import dataclasses
import json
import re
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
from dotagent.repair.claude import RepairStep
from dotagent.repair.night import (
    FAST_KEY,
    LIVE_KEY,
    SESSIONS_KEY,
    WORKER_KEY,
    FastSeen,
    LiveSession,
    RepairSession,
    SessionLog,
)
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
async def _serving(runtime: Runtime, scheduler: object) -> AsyncIterator[tuple[str, int]]:
    manager: RunManager = RunManager(runtime)
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=scheduler,  # type: ignore[arg-type]  # a stand-in of the scheduler's two calls the page makes
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
    async with _serving(runtime, None) as address:
        page: Page = await _open(browser, address, 1280)
        try:
            await expect(page.get_by_text("Set [repair] repo to turn it on")).to_be_visible()
            await expect(page.get_by_role("button", name="Run now")).to_have_count(0)
            await expect(page.get_by_text("owner@example.invalid · Org")).to_be_visible()
            await expect(page.get_by_text("Nothing in line")).to_be_visible()
        finally:
            await page.close()


@pytest.mark.parametrize("width", [1280, 390])
async def test_on_it_shows_the_problems_their_fixes_and_the_sessions(
    browser: Browser, runtime: Runtime, width: int, tmp_path: Path
) -> None:
    """Item 932: an overview leads; what waits for the owner, the nights by date and the problems next in line follow,
    a few of them and the rest a click away, the closed ones folded, a problem's placeholders read as words."""
    now: float = time.time()
    seen: list[Sighting] = [
        Sighting(kind="daemon", words="schedule_start_failed: no such effort", evidence="a", at=now - 60),
        Sighting(kind="daemon", words="schedule_start_failed: no such effort", evidence="b", at=now - 30),
        Sighting(kind="tool_error", words="browser_open: waited 15 s for 'Allow'", evidence="w", at=now - 20),
        *(
            Sighting(kind="tool_error", words=f"{name}: broke", evidence=name, at=now - number)
            for number, name in enumerate(("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"), 1)
        ),
    ]
    book = with_sightings(EMPTY_BOOK, seen, now)
    fixed = next(problem for problem in book.problems if problem.kind == "daemon")
    waited = next(problem for problem in book.problems if problem.title.startswith("browser_open"))
    book = with_problem(book, fixed.model_copy(update={"status": "fixed", "pr_url": PR}))
    book = with_problem(
        book, waited.model_copy(update={"status": "waiting", "pr_url": PR, "note": "It changes REVIEW.md"})
    )
    session = RepairSession(
        problem_id=fixed.id,
        account="owner@example.invalid",
        session_id="s-1",
        started_at=now - 3600,
        ended_at=now - 3000,
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
    async with _serving(on, None) as address:
        page: Page = await _open(browser, address, width)
        try:
            overview = page.get_by_role("group", name="Overview")
            await expect(overview).to_contain_text("On, 05:30")
            await expect(overview).to_contain_text("Merged once every check passes")
            await expect(overview.locator(".stat-tile")).to_have_count(4)
            await expect(page.get_by_role("button", name="Run now")).to_be_enabled()
            waiting = page.locator("section.set-section").filter(has=page.get_by_role("heading", name="Waits for you"))
            await expect(waiting.locator(".set-row")).to_have_count(1)
            # The book's placeholders read as words.
            await expect(waiting).to_contain_text("browser_open: waited … s for “…”")
            await expect(waiting.get_by_role("link", name="Pull request")).to_have_attribute("href", PR)
            nights = page.locator("section.set-section").filter(has=page.get_by_role("heading", name="Nights"))
            await expect(nights.get_by_text("Merged", exact=True)).to_be_visible()
            await expect(nights).to_contain_text("2 rounds · 129k tokens · owner@example.invalid")
            await expect(nights).to_contain_text("1 session")
            line = page.locator("section.set-section").filter(has=page.get_by_role("heading", name="Next in line"))
            await expect(line.locator(".set-row")).to_have_count(5)
            await line.get_by_role("button", name="Show all 7").click()
            await expect(line.locator(".set-row")).to_have_count(7)
            closed = page.locator("section.set-section").filter(has=page.get_by_role("heading", name="Closed"))
            await expect(closed.get_by_text("1 fixed or not Dot's")).to_be_visible()
            assert await page.evaluate(FITS)
            await page.screenshot(path=tmp_path / f"repair-{width}.png", full_page=True)
        finally:
            await page.close()


async def test_the_claude_code_card_keeps_the_model_effort_and_fast_mode_chosen(
    browser: Browser, runtime: Runtime, tmp_path: Path
) -> None:
    """Item 933: each control saves at once, the page reads it back, and the fast mode line says what the last session
    that asked for it got."""
    seen = FastSeen(state="off", why="extra_usage_disabled", at=time.time() - 60)
    await runtime.store.kv_set(FAST_KEY, seen.model_dump_json())
    async with _serving(runtime, None) as address:
        page: Page = await _open(browser, address, 390)
        try:
            card = page.locator("section.fold").filter(has=page.locator(".fold-toggle", has_text="Claude Code"))
            # Folded at the page's foot to the model and effort (item 939): opened to change them.
            await expect(card.locator(".fold-toggle")).to_contain_text("Claude Code's default · default effort")
            await card.locator(".fold-toggle").click()
            await expect(card.get_by_label("Model")).to_have_value("default")
            await card.get_by_label("Model").select_option("opus")
            await expect(page.get_by_text("Saved: the next night works so.")).to_be_visible()
            await card.get_by_label("Effort").select_option("xhigh")
            await card.get_by_role("switch", name="Fast mode").click()
            await expect(card.get_by_role("switch", name="Fast mode")).to_have_attribute("aria-checked", "true")
            await expect(card).to_contain_text("the account has no extra usage, which fast mode is billed to")
            kept = await runtime.store.kv_get(WORKER_KEY)
            assert kept is not None and json.loads(kept) == {"model": "opus", "effort": "xhigh", "fast": True}
            await page.reload()
            await expect(card.get_by_label("Effort")).to_have_value("xhigh")
            assert await page.evaluate(FITS)
            await card.screenshot(path=tmp_path / "claude-code-card-390.png")
        finally:
            await page.close()
            await runtime.store.kv_delete(WORKER_KEY)
            await runtime.store.kv_delete(FAST_KEY)


async def test_a_choice_that_cannot_be_read_is_said_and_chosen_again(browser: Browser, runtime: Runtime) -> None:
    """The fourth pre-push review of item 933: the nights stop on it, but the page opens and takes a new one."""
    await runtime.store.kv_set(WORKER_KEY, '{"model": "retired"}')
    async with _serving(runtime, None) as address:
        page: Page = await _open(browser, address, 1280)
        try:
            # Never folded away while the nights wait on it (the pre-push review of item 939).
            card = page.locator("section.set-section").filter(has=page.get_by_role("heading", name="Claude Code"))
            await expect(card.get_by_text("Choose again")).to_be_visible()
            await card.get_by_label("Model").select_option("sonnet")
            await expect(page.get_by_text("Choose again")).to_have_count(0)
            kept = await runtime.store.kv_get(WORKER_KEY)
            assert kept is not None and json.loads(kept)["model"] == "sonnet"
        finally:
            await page.close()
            await runtime.store.kv_delete(WORKER_KEY)


class _NightAtWork:
    """The scheduler as the app's pages ask it (item 939): a night is at work until Stop is pressed."""

    def __init__(self) -> None:
        self.at_work: bool = True

    def repairing(self) -> bool:
        return self.at_work

    def running(self) -> bool:
        return True

    def night_work(self) -> tuple[str, ...]:
        return ("repair",) if self.at_work else ()

    async def stop_repair(self) -> bool:
        stopped: bool = self.at_work
        self.at_work = False
        return stopped


async def test_a_night_at_work_shows_its_problem_steps_and_time_and_stops(
    browser: Browser, runtime: Runtime, tmp_path: Path
) -> None:
    """Item 939: while a night works, a card above the overview says what on, for how long and with what, its newest
    steps (all a click away), and reads the night again by itself; Stop ends it after a question."""
    now: float = time.time()
    steps = tuple(
        RepairStep(kind=kind, words=words, at=now - 60 + number)
        for number, (kind, words) in enumerate(
            [
                ("read", "src/a.py"),
                ("search", "settle"),
                ("edit", "src/a.py"),
                ("read", "tests/b.py"),
                ("run", "Run the tests"),
            ]
        )
    )
    live = LiveSession(
        problem_id="prb_1", title="browser_open: waited <n> s for <quoted>", kind="session", started_at=now - 125,
        round=2, account="owner@example.invalid", model="opus", effort="high", steps=steps,
    )  # fmt: skip
    await runtime.store.kv_set(LIVE_KEY, live.model_dump_json())
    night = _NightAtWork()
    async with _serving(runtime, night) as address:
        page: Page = await _open(browser, address, 390)
        try:
            card = page.get_by_role("region", name="Working now")
            await expect(card).to_contain_text("Working")
            await expect(card).to_contain_text("round 2")
            await expect(card).to_contain_text("browser_open: waited … s for “…”")
            await expect(card).to_contain_text("Opus · high · owner@example.invalid")
            await expect(card.locator(".repair-step")).to_have_count(4)
            await expect(card.locator(".repair-step.current")).to_contain_text("Ran Run the tests")
            await expect(card.locator(".repair-elapsed")).to_have_text(re.compile(r"^2m \d\ds$"))
            await card.get_by_role("button", name="All 5 steps").click()
            await expect(card.locator(".repair-step")).to_have_count(5)
            # A step seen meanwhile is read in by the page itself.
            more = live.model_copy(update={"steps": (*steps, RepairStep(kind="write", words="roadmap/9.md", at=now))})
            await runtime.store.kv_set(LIVE_KEY, more.model_dump_json())
            await expect(card.locator(".repair-step.current")).to_contain_text("Wrote roadmap/9.md", timeout=10_000)
            assert await page.evaluate(FITS)
            await page.screenshot(path=tmp_path / "repair-at-work-390.png", full_page=True)
            await card.get_by_role("button", name="Stop").click()
            await page.get_by_role("dialog").get_by_role("button", name="Stop").click()
            await expect(page.get_by_text("The night is stopped.")).to_be_visible()
            await expect(page.get_by_role("region", name="Working now")).to_have_count(0)
            assert not night.at_work
        finally:
            await page.close()
            await runtime.store.kv_delete(LIVE_KEY)
