"""A chat's loop (item 281): "/loop 30m <message>" makes a schedule of the chat that starts its runs as the chat's
follow-ups do, the message goes at once, the line over the composer shows the loop, and its Stop deletes it. In a new
chat's box (item 400) the message starts the chat with its loop.

The scripted model plays the runs; the daemon is served on this Mac in a throw-away home.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
TOKEN: str = "chat-loop-test-token"
_RUN_WAIT_S: float = 60.0
# Room for two schedules switched on, so that a test reaches the limit in a few steps; and ten minutes between two
# firings at least, so that a five-minute loop, a rhythm a loop keeps, is one the scheduler refuses.
_MAX_SCHEDULES: int = 2
_MIN_INTERVAL_S: int = 600
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


@asynccontextmanager
async def _daemon(turns: Sequence[ScriptedTurn]) -> AsyncIterator[tuple[RunManager, str]]:
    """A daemon in a throw-away home whose scripted model plays ``turns``: its manager, and the address it serves on."""
    defaults: AppConfig = eval_config(load_default_config())
    config: AppConfig = defaults.model_copy(
        update={
            "scheduler": defaults.scheduler.model_copy(
                update={"max_schedules": _MAX_SCHEDULES, "min_interval_s": _MIN_INTERVAL_S}
            )
        }
    )
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, list(turns)))
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


async def _first_run(manager: RunManager) -> RunRecord:
    run: RunRecord = await manager.start(
        "Say hello", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        return await manager.wait(run.id)


async def test_a_loop_is_a_schedule_of_the_chat_that_starts_as_its_follow_ups_do() -> None:
    async with _daemon((_finish("Hello."),)) as (manager, address):
        run: RunRecord = await _first_run(manager)
        async with httpx.AsyncClient(
            base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
        ) as client:
            made = await client.post(f"/runs/{run.id}/loop", json={"every": "30m", "text": "check the build"})
            assert made.status_code == 201, made.text
            record: dict[str, object] = made.json()
            assert (record["thread_id"], record["cron"], record["mode"], record["enabled"]) == (
                run.thread_id,
                "*/30 * * * *",
                "task",
                True,
            )
            assert record["name"] == "Loop: check the build"
            assert str(record["goal"]).startswith("check the build\n\n(This message is a loop of this chat")
            options = record["options"]
            assert isinstance(options, dict)
            assert (options["model"], options["approvals"]) == (run.model, run.approvals)
            # Asked for again, the loop is the one there is.
            again = await client.post(f"/runs/{run.id}/loop", json={"every": "30m", "text": "check the build"})
            assert again.status_code == 201 and again.json()["id"] == record["id"], again.text
            schedules = await client.get("/schedules")
            assert [view["record"]["id"] for view in schedules.json()] == [record["id"]]
            refused = await client.post(f"/runs/{run.id}/loop", json={"every": "7m", "text": "check the build"})
            assert refused.status_code == 422 and "a loop asks again every" in refused.text


async def test_a_new_chat_sent_with_a_loop_starts_with_it_or_not_at_all() -> None:
    async with (
        _daemon((_finish("Hello."), _finish("Again."))) as (manager, address),
        httpx.AsyncClient(
            base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
        ) as client,
    ):
        message: dict[str, object] = {"goal": "check the build", "model": SCRIPTED_MODEL, "thread_id": None}
        # A rhythm a loop cannot keep, one the scheduler refuses, or a loop with Keep going, starts nothing.
        odd = await client.post("/runs", json={**message, "loop": "7m"})
        assert odd.status_code == 422 and "a loop asks again every" in odd.text, odd.text
        often = await client.post("/runs", json={**message, "loop": "5m"})
        assert often.status_code == 422 and "fires too often" in often.text, often.text
        standing = await client.post("/runs", json={**message, "standing": True, "loop": "30m"})
        assert standing.status_code == 422 and "a campaign (standing) goes on by itself" in standing.text
        assert (await client.get("/runs")).json() == []
        started = await client.post("/runs", json={**message, "loop": "30m", "key": "new-chat-loop-0001"})
        assert started.status_code == 201, started.text
        run: dict[str, object] = started.json()
        # Sent again with its key, the message is the run and the loop it made.
        again = await client.post("/runs", json={**message, "loop": "30m", "key": "new-chat-loop-0001"})
        assert again.status_code == 201 and again.json()["id"] == run["id"], again.text
        schedules = (await client.get("/schedules")).json()
        assert [
            (view["record"]["thread_id"], view["record"]["cron"], view["record"]["name"]) for view in schedules
        ] == [(run["thread_id"], "*/30 * * * *", "Loop: check the build")]
        assert str(schedules[0]["record"]["goal"]).startswith("check the build\n\n(This message is a loop of this chat")
        other = await client.post("/runs", json={**message, "goal": "tidy the notes", "loop": "1h"})
        assert other.status_code == 201, other.text
        # With no room for one more schedule switched on, a new chat with a loop starts nothing.
        full = await client.post("/runs", json={**message, "goal": "water the plants", "loop": "2h"})
        assert full.status_code == 422 and "too many schedules are switched on" in full.text, full.text
        runs = (await client.get("/runs")).json()
        assert sorted(record["id"] for record in runs) == sorted([run["id"], other.json()["id"]])
        async with asyncio.timeout(_RUN_WAIT_S):
            for record in runs:
                await manager.wait(str(record["id"]))


async def test_the_loop_of_a_new_chat_with_an_agent_runs_as_that_agent() -> None:
    """Item 463: the schedule of a loop sent in a new chat with one of the owner's agents names that agent."""
    async with (
        _daemon((_finish("Hello."),)) as (manager, address),
        httpx.AsyncClient(
            base_url=f"{address}/api", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30.0
        ) as client,
    ):
        agent = await client.post(
            "/agents",
            json={
                "name": "Scout",
                "role": "Reads files",
                "instructions": "",
                "model": "",
                "effort": None,
                "approvals": None,
                "tools": ["read_file"],
            },
        )
        assert agent.status_code == 201, agent.text
        message: dict[str, object] = {"goal": "check the build", "model": SCRIPTED_MODEL, "thread_id": None}
        started = await client.post("/runs", json={**message, "agent_id": agent.json()["id"], "loop": "30m"})
        assert started.status_code == 201, started.text
        schedules = (await client.get("/schedules")).json()
        assert [(view["record"]["options"]["agent_id"], view["agent"]["name"]) for view in schedules] == [
            (agent.json()["id"], "Scout")
        ]
        async with asyncio.timeout(_RUN_WAIT_S):
            await manager.wait(str(started.json()["id"]))


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page


@pytest.mark.browser
async def test_slash_loop_shows_the_loop_over_the_composer_and_its_stop_ends_it(tmp_path: Path) -> None:
    turns: tuple[ScriptedTurn, ...] = tuple(_finish(f"Answer {number}.") for number in range(1, 6))
    async with _daemon(turns) as (manager, address), async_playwright() as playwright:
        run: RunRecord = await _first_run(manager)
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await _page(browser, address, width, scheme)
                await page.goto(f"{address}/#/runs/{run.id}")
                box: Locator = page.locator(".dock textarea")
                await box.fill("/loop 30m check the build")
                await box.press("Enter")
                loops: Locator = page.get_by_role("region", name="Loops of this chat")
                await expect(loops).to_be_visible()
                await expect(loops).to_contain_text("check the build")
                await expect(loops).to_contain_text("every 30 minutes")
                # The message itself went at once, as the chat's next run.
                await expect(page.locator(".run-thread")).to_contain_text("check the build")
                # The page of that run shows the chat's loop too.
                await expect(loops).to_contain_text("every 30 minutes")
                await page.screenshot(path=tmp_path / f"loop-{width}-{scheme}.png")
                await page.get_by_role("button", name="Stop the loop check the build").click()
                await page.get_by_role("dialog").get_by_role("button", name="Stop").click()
                await expect(loops).to_be_hidden()
                # A loop with no rhythm is said how to write it, and nothing is sent.
                await box.fill("/loop check the build")
                await box.press("Enter")
                await expect(page.get_by_text("Write /loop, how often and the message")).to_be_visible()
                await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_slash_loop_in_a_new_chat_starts_it_with_its_loop(tmp_path: Path) -> None:
    turns: tuple[ScriptedTurn, ...] = tuple(_finish(f"Answer {number}.") for number in range(1, len(_SHOTS) + 1))
    async with _daemon(turns) as (_manager, address), async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await _page(browser, address, width, scheme)
                box: Locator = page.locator("textarea[aria-label='Goal']")
                # A loop with no rhythm is said how to write it, and no chat starts.
                await box.fill("/loop check the build")
                await box.press("Enter")
                await expect(page.get_by_text("Write /loop, how often and the message")).to_be_visible()
                await expect(page).to_have_url(f"{address}/#/runs")
                # The slash menu offers /loop beside the modes; picked, it is written for the rhythm and the message.
                await box.fill("")
                await box.type("/lo")
                menu: Locator = page.locator(".composer .slash-menu")
                await expect(menu.locator("[role=option]")).to_have_count(1)
                await expect(menu.locator("[role=option]")).to_contain_text("/loop")
                await box.press("Tab")
                await expect(box).to_have_value("/loop ")
                await box.type("30m check the build")
                await box.press("Enter")
                # The message starts the chat, whose loop shows over its composer.
                await expect(page).to_have_url(re.compile(r"#/runs/run_"))
                await expect(page.locator(".run-thread")).to_contain_text("check the build")
                loops: Locator = page.get_by_role("region", name="Loops of this chat")
                await expect(loops).to_contain_text("check the build")
                await expect(loops).to_contain_text("every 30 minutes")
                await page.screenshot(path=tmp_path / f"new-chat-loop-{width}-{scheme}.png")
                # Stopped, the loop leaves room for the next width's.
                await page.get_by_role("button", name="Stop the loop check the build").click()
                await page.get_by_role("dialog").get_by_role("button", name="Stop").click()
                await expect(loops).to_be_hidden()
                await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_a_looping_chat_wears_the_loop_mark_in_the_sidebar_until_its_loop_stops(tmp_path: Path) -> None:
    """Item 580: a chat with a loop wears its mark at the foot of its creature in the sidebar, turning slowly in a glow
    that breathes (still under reduced motion), and its row says the loop to a screen reader; the loop stopped, the mark
    goes without the page being read again. A chat without a loop wears none."""
    turns: tuple[ScriptedTurn, ...] = tuple(_finish(f"Answer {number}.") for number in range(1, 6))
    async with _daemon(turns) as (manager, address), async_playwright() as playwright:
        run: RunRecord = await _first_run(manager)
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            for scheme, motion in (("light", "no-preference"), ("dark", "reduce")):
                page: Page = await browser.new_page(
                    viewport={"width": 1280, "height": 900}, color_scheme=scheme, reduced_motion=motion
                )
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                sidebar: Locator = page.get_by_role("complementary", name="Sidebar")
                # The chat's row, which leads to its latest run: the first run's, or the one the last loop sent.
                row: Locator = sidebar.locator(".side-run", has_text="Say hello")
                await expect(row).to_be_visible()
                await expect(row.locator(".side-loop")).to_have_count(0)
                box: Locator = page.locator(".dock textarea")
                await box.fill("/loop 30m check the build")
                await box.press("Enter")
                await expect(page.get_by_role("region", name="Loops of this chat")).to_be_visible()
                # The loop's message went at once as the chat's next run, which is the row's now.
                looping: Locator = sidebar.locator(".side-run", has=page.locator(".side-loop"))
                await expect(looping).to_have_count(1)
                await expect(looping).to_have_attribute(
                    "aria-label", re.compile(r"loops every 30 minutes, next (\w{3} )?\d")
                )
                mark: Locator = looping.locator(".side-loop")
                await expect(mark).to_have_attribute("title", re.compile(r"^Loops every 30 minutes, next (\w{3} )?\d"))
                face = await looping.locator(".side-face .avatar").bounding_box()
                badge = await mark.bounding_box()
                assert face is not None and badge is not None
                # At the creature's foot, on its right.
                assert badge["x"] + badge["width"] > face["x"] + face["width"] - 1
                assert badge["y"] + badge["height"] > face["y"] + face["height"] - 1
                animation: str = await mark.evaluate("node => getComputedStyle(node).animationName")
                assert animation == ("none" if motion == "reduce" else "loop-glow"), animation
                await sidebar.screenshot(path=tmp_path / f"loop-mark-{scheme}.png")
                await page.get_by_role("button", name="Stop the loop check the build").click()
                await page.get_by_role("dialog").get_by_role("button", name="Stop").click()
                await expect(sidebar.locator(".side-loop")).to_have_count(0)
                await page.close()
        finally:
            await browser.close()
