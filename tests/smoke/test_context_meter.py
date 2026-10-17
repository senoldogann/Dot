"""The context meter of a run's composer (item 222), after T3 Code's: a ring of how much of the run's context budget its
last step filled, warning near the budget and past it, and on a click the figures: the tokens the step sent the model,
the budget past which Dot summarizes the older part of the run, and the model's window; for a model given a context
size of its own (item 250), that size and the point past which the run summarizes, which the timeline notes too. Runs
seeded with their scaffold and assistant events, served by a real server, wide and on a phone, light and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "context-meter-token"
_BUDGET: int = 96_000
_WINDOW: int = 1_000_000
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))
_METER: str = ".dock .context-meter"
# The scaffold event of a run from before item 250, which names no chosen size and no point of summary.
_UNSIZED: JsonObject = {
    "model": SCRIPTED_MODEL,
    "window": _WINDOW,
    "context_tokens": _BUDGET,
    "tool_output_chars": 12_000,
    "cut_to_window": False,
    "tier": "standard",
    "reason": "",
    "reminder_every": None,
}


def _sized(chosen: int, window: int) -> JsonObject:
    """The scaffold event of a run whose model has a context size of ``chosen`` tokens of its own (item 250), which three
    quarters of its ``window`` cut when they are less."""
    budget: int = min(chosen, round(window * 0.75))
    return {
        **_UNSIZED,
        "window": window,
        "chosen": chosen,
        "context_tokens": budget,
        "summarise_at": min(round(chosen * 0.85), budget),
        "cut_to_window": budget < chosen,
    }


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
    """The address of a real server over the runtime."""
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
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _seeded_run(runtime: Runtime, prompts: tuple[int, ...], scaffold: JsonObject) -> RunRecord:
    """A chat whose one run is over, with its ``scaffold`` event and a step for each prompt size, in tokens."""
    thread: ThreadRecord = await runtime.store.create_thread("Summarise the brief", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Summarise the brief",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    await runtime.store.add_event(run.id, "scaffold", scaffold)
    for step, tokens in enumerate(prompts, 1):
        await runtime.store.add_event(
            run.id,
            "assistant",
            {
                "step": step,
                "text": f"Step {step} of the summary.",
                "thinking_chars": 0,
                "tool_calls": [],
                "input_tokens": tokens,
                "output_tokens": 120,
                "cached_tokens": 0,
                "seconds": 1.5,
                "finish": "stop",
            },
        )
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})
    return run


async def _ring_filled(meter: Locator) -> float:
    """How much of the meter's ring is drawn filled, of 100, as the page draws it (its stroke's first dash)."""
    filled: float = await meter.locator(".context-ring-fill").evaluate(
        "node => parseFloat(getComputedStyle(node).strokeDasharray)"
    )
    return filled


async def _run_page(browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


async def test_the_context_meter_shows_how_much_of_the_budget_the_last_step_filled(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    near: RunRecord = await _seeded_run(runtime, (40_000, 81_000), _UNSIZED)
    past: RunRecord = await _seeded_run(runtime, (50_000, 101_000), _UNSIZED)
    roomy: RunRecord = await _seeded_run(runtime, (12_000,), _UNSIZED)
    unstarted: RunRecord = await _seeded_run(runtime, (), _UNSIZED)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                # The last step counts, not the run's sum: 81,000 of 96,000 is near the budget.
                page: Page = await _run_page(browser, served, near.id, width, scheme)
                meter: Locator = page.locator(_METER)
                await expect(meter).to_be_visible()
                await expect(meter).to_have_attribute("data-level", "near")
                await expect(meter).to_have_attribute(
                    "aria-label", "Context: 84% of the run's budget, 81k of 96k tokens"
                )
                # The ring is drawn filled as far as the share: no rule of the page's overrides it.
                assert await _ring_filled(meter) == 84
                await meter.click()
                panel: Locator = page.locator(".composer-popover[aria-label='Context']")
                await expect(panel.locator(".context-meter-title")).to_have_text("Context window")
                await expect(panel.locator(".context-meter-figure")).to_have_text("84% · 81k / 96k")
                # Why the budget is a tenth of the window, and where it is set (item 239).
                await expect(panel.locator(".context-meter-text")).to_have_text(
                    [
                        "The run's last step sent the model 81,000 tokens. Past 96,000, the run's budget, Dot "
                        "summarizes its older part.",
                        "The model holds 1,000,000 tokens.",
                        "Each step sends the model the whole run again, so a run kept within its budget is answered "
                        "sooner and costs less. Dot's default size sets the budget, and Settings › Models one model's "
                        "own, up to three quarters of the model's window. In the config file: [agent] context_tokens.",
                    ]
                )
                await page.screenshot(path=str(tmp_path / f"meter-{width}-{scheme}.png"), animations="disabled")
                await page.keyboard.press("Escape")
                await expect(panel).to_be_hidden()
                await page.close()
            # Past the budget the ring is full and says so; with room it says that.
            for run, level, label, filled in (
                (past, "full", "Context: 105% of the run's budget, 101k of 96k tokens", 100),
                (roomy, "room", "Context: 13% of the run's budget, 12k of 96k tokens", 13),
            ):
                page = await _run_page(browser, served, run.id, 1280, "light")
                await expect(page.locator(_METER)).to_have_attribute("data-level", level)
                await expect(page.locator(_METER)).to_have_attribute("aria-label", label)
                assert await _ring_filled(page.locator(_METER)) == filled
                await page.close()
            # A run whose model has not answered yet shows no meter.
            page = await _run_page(browser, served, unstarted.id, 1280, "light")
            await expect(page.locator(".dock .composer")).to_be_visible()
            await expect(page.locator(_METER)).to_be_hidden()
            await page.close()
        finally:
            await browser.close()


async def test_a_model_with_a_context_size_of_its_own_has_the_meter_and_the_timeline_say_so(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    sized: RunRecord = await _seeded_run(runtime, (90_000, 150_000), _sized(192_000, _WINDOW))
    # A window of 204,800 tokens cuts the same size to 153,600.
    cut: RunRecord = await _seeded_run(runtime, (90_000,), _sized(192_000, 204_800))
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await _run_page(browser, served, sized.id, width, scheme)
                # The timeline says where the budget comes from.
                await expect(
                    page.get_by_text(
                        "Context budget 192k tokens, the size chosen for the model in Settings, tool output up to 12k "
                        "characters"
                    )
                ).to_be_visible()
                meter: Locator = page.locator(_METER)
                await expect(meter).to_have_attribute(
                    "aria-label", "Context: 78% of the run's budget, 150k of 192k tokens"
                )
                await meter.click()
                panel: Locator = page.locator(".composer-popover[aria-label='Context']")
                await expect(panel.locator(".context-meter-text")).to_have_text(
                    [
                        "The run's last step sent the model 150,000 tokens. Past 163,200, Dot summarizes its older "
                        "part, so the run stays within its budget of 192,000.",
                        "The model holds 1,000,000 tokens.",
                        "Each step sends the model the whole run again, so a run kept within its budget is answered "
                        "sooner and costs less. The context size chosen for this model in Settings › Models, 192,000 "
                        "tokens, sets the budget, up to three quarters of its window.",
                    ]
                )
                await page.screenshot(path=str(tmp_path / f"sized-{width}-{scheme}.png"), animations="disabled")
                await page.close()
            # A size the window cut: the note names both.
            page = await _run_page(browser, served, cut.id, 1280, "light")
            await expect(
                page.get_by_text(
                    "Context budget 154k tokens, the 192k chosen for the model in Settings cut to fit its window of "
                    "205k, tool output up to 12k characters"
                )
            ).to_be_visible()
            await page.close()
        finally:
            await browser.close()
