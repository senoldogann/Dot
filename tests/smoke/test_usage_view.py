"""What the tasks used, made visible (item 1403), on a scripted model, a real server and a real headless Chrome. The
owner sets no ceiling on a task, so what is spent is what there is to read: ``GET /api/usage`` sums the runs of the
last days, a row for each day with the tokens in and out, the steps and the minutes, and the last window of the Claude
plan each kind of window was seen in; Settings › Models draws it as a Usage section, readable on a phone 390 px wide
and wide; and the Cost card of a task that is still at work says first what it has used so far, with the plan's window
its newest step saw. Dot keeps no price for a model, so no money is shown. The caps of what the runs spend (item
1731): ``GET``/``PUT /api/budget``, the costliest tasks and the caps in Settings, the task against its cap on the
Cost card, and a task past a cap that waits on a card, Continue or Stop, in yolo, until Stop ends it."""

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect
from test_side_panel import TOKEN, _open_panel, _tool_calls

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import UPDATE_PLAN_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RESET_IN_S: float = 4800.0
# Long enough for every look below; the task is stopped at the end of the test that starts it.
_WAIT_COMMAND: str = "sleep 300"


@pytest_asyncio.fixture(loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, whose one task starts a command that waits."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    turns: list[ScriptedTurn] = [
        ScriptedTurn(
            text="",
            calls=(
                ScriptedCall(tool=UPDATE_PLAN_TOOL, arguments={"steps": [{"text": "Read", "status": "in_progress"}]}),
            ),
        ),
        ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)),
    ]
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    """The run manager and the address of a real server over it."""
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _plan_step(runtime: Runtime, run: RunRecord, kind: str, utilization: float) -> None:
    """A step of ``run`` that saw a window of the plan: ``utilization`` of it used, resetting in 80 minutes."""
    window: dict[str, object] = {
        "kind": kind,
        "status": "allowed",
        "utilization": utilization,
        "resets_at": time.time() + _RESET_IN_S,
    }
    await runtime.store.add_event(
        run.id, "assistant", {"step": 1, "text": "", "input_tokens": 1, "output_tokens": 1, "plan_window": window}
    )


async def _finished(runtime: Runtime, goal: str, steps: int, tokens_in: int, tokens_out: int) -> RunRecord:
    thread = await runtime.store.create_thread(goal, "ui")
    run: RunRecord = await runtime.store.create_run(
        thread.id, goal, "task", "claude_code:opus", None, "ui", None, "standard", None, "ask", False, None, None, None
    )
    await runtime.store.add_run_tokens(run.id, tokens_in, tokens_out)
    await runtime.store.set_run_steps(run.id, steps)
    await runtime.store.finish_run(run.id, "done", "ok")
    return run


async def test_the_api_sums_the_runs_by_day_and_gives_the_last_window_of_each_kind_and_refuses_a_bad_window_of_days(
    runtime: Runtime, served: tuple[RunManager, str]
) -> None:
    _, address = served
    first: RunRecord = await _finished(runtime, "Write the weekly report", 12, 1_000, 200)
    second: RunRecord = await _finished(runtime, "Check the build", 3, 500, 50)
    await _plan_step(runtime, first, "five_hour", 0.40)
    await _plan_step(runtime, second, "five_hour", 0.62)
    await _plan_step(runtime, second, "seven_day", 0.18)
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        answer: httpx.Response = await client.get("/api/usage")
        assert answer.status_code == 200, answer.text
        week: dict[str, list[dict[str, object]]] = answer.json()
        one: httpx.Response = await client.get("/api/usage", params={"days": 1})
        none: httpx.Response = await client.get("/api/usage", params={"days": 0})
        long: httpx.Response = await client.get("/api/usage", params={"days": 93})
    assert len(week["days"]) == 7
    today: dict[str, object] = week["days"][-1]
    assert (today["runs"], today["steps"], today["input_tokens"], today["output_tokens"]) == (2, 15, 1_500, 250)
    assert [day["runs"] for day in week["days"][:-1]] == [0] * 6
    # The newest window of each kind, the newest step's first.
    assert [(window["kind"], window["utilization"]) for window in week["windows"]] == [
        ("seven_day", 0.18),
        ("five_hour", 0.62),
    ]
    assert all(window["status"] == "allowed" and window["resets_at"] for window in week["windows"])
    assert one.status_code == 200 and len(one.json()["days"]) == 1
    assert none.status_code == 422 and long.status_code == 422
    # The costliest tasks of the period, the most first (item 1731).
    top: list[dict[str, object]] = week["top_runs"]
    assert [(entry["run_id"], entry["tokens"], entry["goal"]) for entry in top[:2]] == [
        (first.id, 1_200, "Write the weekly report"),
        (second.id, 550, "Check the build"),
    ]


async def test_the_caps_are_read_and_changed_through_the_api_and_kept_in_the_config_file(
    runtime: Runtime, served: tuple[RunManager, str]
) -> None:
    _, address = served
    before = runtime.budget.config
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        read: httpx.Response = await client.get("/api/budget")
        assert read.status_code == 200, read.text
        view: dict[str, object] = read.json()
        assert view["caps"] == before.model_dump()
        assert isinstance(view["today"], int) and isinstance(view["by_source"], list)
        try:
            changed: httpx.Response = await client.put(
                "/api/budget", json={**before.model_dump(), "run_tokens": 7_000_000}
            )
            assert changed.status_code == 200, changed.text
            assert changed.json()["caps"]["run_tokens"] == 7_000_000
            assert runtime.budget.config.run_tokens == 7_000_000
            written: str = (Path(os.environ["DOT_HOME"]) / "config.toml").read_text(encoding="utf-8")
            assert "[budget]\nrun_tokens = 7000000" in written
            refused: httpx.Response = await client.put("/api/budget", json={**before.model_dump(), "day_tokens": -1})
            assert refused.status_code == 422
        finally:
            restored: httpx.Response = await client.put("/api/budget", json=before.model_dump())
            assert restored.status_code == 200, restored.text


async def test_settings_models_draws_the_usage_wide_and_on_a_phone_with_the_plans_window(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    _, address = served
    first: RunRecord = await _finished(runtime, "Write the weekly report", 12, 1_000, 200)
    await _plan_step(runtime, first, "five_hour", 0.62)
    await _finished(runtime, "Check the build", 3, 500, 50)
    port: int = int(address.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/settings/models")
                usage: Locator = page.locator("section[data-section='usage']")
                await expect(usage.locator(".set-section-title")).to_have_text("Usage")
                # Seven days, the newest first, each with its bar; today's two tasks added up.
                days: Locator = usage.locator("[data-usage-day]")
                await expect(days).to_have_count(7)
                await expect(days.first.locator(".set-row-title")).to_have_text("Today")
                await expect(days.first.locator(".set-row-lead")).to_contain_text(
                    re.compile(r"^2 tasks · 1\.5k in · 250 out · ")
                )
                await expect(days.nth(1).locator(".set-row-title")).to_have_text("Yesterday")
                await expect(days.nth(1).locator(".set-row-lead")).to_have_text("No tasks")
                await expect(days.first.locator(".usage-bar-fill")).to_have_count(1)
                await expect(usage.locator("[data-usage-total]")).to_contain_text("Last 7 days")
                # The plan's window as the last step saw it.
                window: Locator = usage.locator("[data-usage-window='five_hour']")
                await expect(window.locator(".set-row-title")).to_have_text("Claude plan · 5-hour window")
                await expect(window.locator(".set-row-lead")).to_contain_text(
                    re.compile(r"^62% used · resets in 1h (?:19|20)m · seen ")
                )
                # The section stands before the long list of every model, and never says a price.
                titles: list[str] = await page.locator(".set-section-title").all_inner_texts()
                assert titles.index("Usage") < titles.index("Every model, by service"), titles
                assert "$" not in await usage.inner_text()
                # The costliest tasks, each opening its task, and the caps in millions (item 1731).
                costliest: Locator = page.locator("section[data-section='usage-top'] [data-usage-top]")
                await expect(costliest.first).to_contain_text("Write the weekly report")
                await expect(costliest.first.locator("a")).to_have_attribute("href", f"#/runs/{first.id}")
                caps: Locator = page.locator("section[data-section='usage-caps']")
                await expect(caps.locator("[data-usage-cap]")).to_have_count(7)
                await expect(caps.locator("[data-usage-cap='run_tokens'] input")).to_have_value("500")
                await expect(caps.locator("[data-usage-today]")).to_contain_text("Today ")
                await caps.scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / f"caps-{width}.png"), animations="disabled")
                client_width: int = await page.evaluate("document.documentElement.clientWidth")
                assert await page.evaluate("document.documentElement.scrollWidth") <= client_width
                box = await usage.bounding_box()
                assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= client_width + 1, box
                await usage.scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / f"usage-{width}.png"), animations="disabled")
                await page.close()
        finally:
            await browser.close()


async def test_the_cost_card_of_a_task_at_work_says_what_it_has_used_so_far_with_the_plans_window(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        "Summarise the brief", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    await _tool_calls(runtime, run.id, 2)
    await _plan_step(runtime, run, "five_hour", 0.62)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width in (1280, 390):
                page: Page = await _open_panel(browser, address, run.id, width, "light")
                shown: Locator = page.locator(".inspector").get_by_role("tabpanel")
                more: Locator = shown.get_by_role("button", name="More")
                if await more.get_attribute("aria-expanded") == "false":
                    await more.click()
                bill: Locator = shown.get_by_label("Cost of this task")
                sofar: Locator = bill.locator(".cost-so-far")
                await expect(sofar).to_be_visible()
                await expect(sofar.locator(".cost-so-far-label")).to_have_text("Used so far")
                await expect(sofar.locator(".cost-so-far-line")).to_contain_text(
                    re.compile(r" · [\d.]+[kM]? in · [\d.]+[kM]? out · \d+ steps?$")
                )
                await expect(sofar.locator(".cost-so-far-window")).to_contain_text(
                    re.compile(r"^5-hour window: 62% used · resets in 1h (?:19|20)m$")
                )
                # The task against its cap, from its first step (item 1731).
                await expect(bill.locator(".cost-budget")).to_contain_text(
                    re.compile(r"^With helpers .+ of its 500\.0M cap · ")
                )
                # The details stay under it, as before.
                await expect(bill).to_contain_text("Usage in")
                await bill.scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / f"cost-{width}.png"), animations="disabled")
                await page.close()
        finally:
            manager.cancel(run.id)
            async with asyncio.timeout(30):
                assert (await manager.wait(run.id)).status == "cancelled"
            await browser.close()
    # Once the task has ended there is no "so far": the card says what it cost, as it did.
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page = await _open_panel(browser, address, run.id, 1280, "light")
            shown = page.locator(".inspector").get_by_role("tabpanel")
            more = shown.get_by_role("button", name="More")
            if await more.get_attribute("aria-expanded") == "false":
                await more.click()
            bill = shown.get_by_label("Cost of this task")
            await expect(bill).to_contain_text("Usage in")
            await expect(bill.locator(".cost-so-far")).to_have_count(0)
        finally:
            await browser.close()


async def test_a_task_past_the_days_cap_waits_on_a_card_in_yolo_and_stop_ends_it(
    runtime: Runtime, served: tuple[RunManager, str]
) -> None:
    manager, address = served
    before = runtime.budget.config
    await _finished(runtime, "Earlier today", 1, 2_000, 0)
    # A day's cap the tasks of today are past already: the task's first step waits for the owner.
    runtime.budget.apply(before.model_copy(update={"day_tokens": 1_000}))
    run: RunRecord | None = None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            run = await manager.start(
                "Summarise the brief",
                "task",
                SCRIPTED_MODEL,
                None,
                "test",
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            page: Page = await browser.new_page(viewport={"width": 390, "height": 900}, reduced_motion="reduce")
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/runs/{run.id}")
            card: Locator = page.locator(".approval").filter(has_text="A task reached its budget")
            await expect(card).to_be_visible(timeout=20_000)
            await expect(card).to_contain_text("past the day's cap of 1k ([budget] day_tokens)")
            await expect(card.get_by_role("button", name="Continue (+1k)")).to_be_visible()
            await expect(card.get_by_text("Don't ask again in this chat")).to_have_count(0)
            await card.get_by_role("button", name="Stop").click()
            async with asyncio.timeout(30):
                ended: RunRecord = await manager.wait(run.id)
            assert ended.status == "cancelled"
            assert [(record.tool, record.status) for record in await runtime.store.run_approvals(run.id)] == [
                ("budget", "denied")
            ]
            await page.close()
        finally:
            runtime.budget.apply(before)
            if run is not None and run.id in manager.active_run_ids():
                manager.cancel(run.id)
            await browser.close()
