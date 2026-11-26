"""The screens of a schedule's check, a check watcher and the estimate in dollars (items 1855 and 1856), on a real
server and a real headless Chrome, wide and 390 px wide: the schedule's editor shows its check under When, the watcher
form offers the Check kind with its file name, Settings › Models › Usage says each day's dollars, and a task's Cost card
says its own and, beside its cap, with its helpers. Every model call goes to the scripted provider: a run's page asks
the fast model for a recap, which on Ollama's cloud would leave this Mac."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import checks_dir, prices_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.price_list import write_book
from dotagent.llm.pricing import PriceBook, read_catalogue
from dotagent.llm.router import parse_model_ref
from dotagent.proactive.watchers import WatcherRequest, add_watcher
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ScheduleRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "check-and-cost-token"
_CATALOGUE: str = (Path(__file__).parent.parent / "fixtures" / "prices" / "catalogue.json").read_text(encoding="utf-8")
_CHECK: str = "price-drop.sh"


def _write_check() -> None:
    """An executable check in the checks folder that finds nothing to wake for."""
    check: Path = checks_dir() / _CHECK
    check.write_text("#!/bin/sh\necho '{\"wake\": false}'\n", encoding="utf-8")
    check.chmod(0o700)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose models all answer from the scripted provider, with a price list kept."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\nfast = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\nvision = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    write_book(
        prices_path(), PriceBook(fetched_at=time.time(), source="local", etag=None, prices=read_catalogue(_CATALOGUE))
    )
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    provider: str = parse_model_ref(SCRIPTED_MODEL).provider
    await built.router.register(provider, ScriptedProvider(provider, []))
    await asyncio.to_thread(_write_check)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
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
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def _priced_run(runtime: Runtime) -> RunRecord:
    """A finished run of two calls: 120k in (40k cached) and 3k out on Sonnet, 60k in and 1k out on GPT: $0.38."""
    store = runtime.store
    thread = await store.create_thread("Write the weekly report", "ui")
    run: RunRecord = await store.create_run(
        thread.id, "Write the weekly report", "task", "anthropic:claude-sonnet-5-5", None, "ui", None, "standard",
        None, "ask", False, None, None, None,
    )  # fmt: skip
    for used_in, used_out, cached, model in (
        (120_000, 3_000, 40_000, "anthropic:claude-sonnet-5-5"),
        (60_000, 1_000, 0, "openai:gpt-6"),
    ):
        await store.add_run_tokens(run.id, used_in, used_out)
        await store.add_event(
            run.id,
            "model_call",
            {
                "purpose": "step",
                "model": model,
                "seconds": 2.0,
                "first_token_seconds": 0.5,
                "input_tokens": used_in,
                "output_tokens": used_out,
                "cached_tokens": cached,
                "cache_write_tokens": 0,
                "error": None,
            },
        )
    await store.set_run_steps(run.id, 2)
    await store.finish_run(run.id, "done", "ok")
    return run


async def _page(browser: Browser, address: str, width: int) -> Page:
    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page


async def _fits(page: Page) -> None:
    width: int = await page.evaluate("document.documentElement.clientWidth")
    assert await page.evaluate("document.documentElement.scrollWidth") <= width


async def test_the_check_and_the_dollars_are_shown_wide_and_on_a_phone(
    runtime: Runtime, address: str, tmp_path: Path
) -> None:
    schedule: ScheduleRecord = await create_schedule(
        runtime.store,
        runtime.config.scheduler,
        runtime.schedule_zone,
        ScheduleRequest(
            name="Watch the price",
            goal="Tell me the price.",
            cron="*/30 * * * *",
            at=None,
            mode="research",
            options=UNSET_OPTIONS.model_copy(update={"precheck": _CHECK}),
        ),
        time.time(),
        None,
    )
    await add_watcher(
        runtime.store, WatcherRequest(name="Price drop", kind="check", minutes=15, check=_CHECK), time.time()
    )
    run: RunRecord = await _priced_run(runtime)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await _page(browser, address, width)
                # The schedule's editor: its check under When.
                await page.goto(f"{address}/#/settings/schedules/{schedule.id}")
                check: Locator = page.get_by_label("Check", exact=True)
                await expect(check).to_have_value(_CHECK)
                await check.scroll_into_view_if_needed()
                await _fits(page)
                await page.screenshot(path=str(tmp_path / f"schedule-{width}.png"), animations="disabled")
                # The list: the watcher of kind Check with its file name.
                await page.goto(f"{address}/#/settings/schedules")
                await expect(page.locator('[title="The check it runs, in Dot\'s checks folder"]')).to_have_text(_CHECK)
                await _fits(page)
                # The usage: today's dollars.
                await page.goto(f"{address}/#/settings/models")
                today: Locator = page.locator("section[data-section='usage'] [data-usage-day]").first
                await expect(today.locator(".set-row-lead")).to_contain_text(re.compile(r" · ~\$0\.38 · "))
                await today.scroll_into_view_if_needed()
                await _fits(page)
                await page.screenshot(path=str(tmp_path / f"usage-{width}.png"), animations="disabled")
                await page.close()
            # The task's Cost card, its own and with its helpers beside the cap.
            page = await _page(browser, address, 1280)
            await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
            await page.goto(f"{address}/#/runs/{run.id}")
            inspector: Locator = page.locator(".inspector")
            await expect(inspector).to_be_visible()
            await inspector.get_by_text("More", exact=True).click()
            cost: Locator = inspector.get_by_label("Cost of this task")
            await expect(cost).to_contain_text("~$0.38")
            await expect(cost).to_contain_text(re.compile(r"With helpers \S+ \(~\$0\.38\)"))
            await page.screenshot(path=str(tmp_path / "cost-1280.png"), animations="disabled")
            await page.close()
        finally:
            await browser.close()
