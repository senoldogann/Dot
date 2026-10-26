"""The teach panel's Repeat (item 666), in Chrome on the real daemon: once the taught task is saved, the panel offers to
repeat it by itself, every day, on weekdays or on a day of the week at a time; Repeat it makes the schedule in this chat
through the daemon, the panel says when it repeats in words and leads to it, and the Schedules page marks the row as a
taught task. The recording itself needs a window (item 423, tests/smoke/test_teach.py), so the panel's reads of it are
answered here; the skill it repeats is a real one, saved under the playbooks."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, ScheduleRecord
from dotagent.teach import TaughtSkill, save_taught_skill
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "teach-repeat-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
)
SKILL: TaughtSkill = TaughtSkill(
    name="taught-invoices",
    kind="invoices",
    host="example.com",
    description="Pay the invoices of the month.",
    when="At the start of a month.",
    steps=("Open the invoices page.", "Pay each one."),
    parameters=(),
    shown=("Opened https://example.com/invoices",),
)
_SHOWN: list[str] = ["Opened https://example.com/invoices", 'Clicked "Pay"']


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, Runtime, str, int]]:
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nreflection_time = "off"\n',
        encoding="utf-8",
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, list(_TURNS)))
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
            yield manager, runtime, f"http://127.0.0.1:{port}", port
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _answer_the_recording(page: Page) -> None:
    """The panel's reads of the recording, answered as the daemon answers them for a recording of two steps; Repeat
    goes on to the daemon."""
    stopped: dict[str, bool] = {"yes": False}

    async def teach(route: Route) -> None:
        url: str = route.request.url
        method: str = route.request.method
        if url.endswith("/repeat"):
            await route.continue_()
        elif url.endswith("/screencast"):
            await route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body="")
        elif method == "POST" and url.endswith("/api/teach"):
            await route.fulfill(status=201, json={"thread_id": "t", "tab": 0, "address": ""})
        elif method == "POST" and url.endswith("/stop"):
            stopped["yes"] = True
            await route.fulfill(json={"steps": _SHOWN, "address": "https://example.com/invoices"})
        elif method == "POST" and url.endswith("/draft"):
            await route.fulfill(json=json.loads(SKILL.model_dump_json()))
        elif method == "POST" and url.endswith("/save"):
            await route.fulfill(status=201, json={"name": SKILL.name})
        elif method == "GET":
            going: bool = not stopped["yes"]
            await route.fulfill(
                json={
                    "recording": going,
                    "steps": _SHOWN if going else [],
                    "address": "",
                    "stopped": [] if going else _SHOWN,
                }
            )
        else:
            await route.fulfill(json={"discarded": True})

    await page.route(re.compile(r"/api/teach(/|$)"), teach)


async def test_a_saved_task_repeats_from_the_panel_and_its_schedule_says_so(own_home: Path, tmp_path: Path) -> None:
    await asyncio.to_thread(save_taught_skill, SKILL)
    async with _served(own_home) as (manager, runtime, address, port):
        run: RunRecord = await manager.start(
            "Pay the invoices", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for width in (1280, 390):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, reduced_motion="reduce"
                    )
                    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await _answer_the_recording(page)
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(page.locator(".answer .prose")).to_have_text("Done.")
                    await page.locator(".plus-button").click()
                    await page.get_by_role("menuitem", name="Teach a task").click()
                    panel = page.locator("dialog.teach")
                    await expect(panel.locator(".teach-steps li").first).to_have_text(_SHOWN[0])
                    await panel.get_by_role("button", name="Stop").click()
                    await panel.locator(".teach-words").fill("Pay this month's invoices")
                    await panel.get_by_role("button", name="Write the skill").click()
                    await panel.get_by_role("button", name="Save it").click()
                    repeat = panel.locator(".teach-repeat")
                    await expect(repeat).to_be_visible()
                    await expect(repeat.get_by_label("Which day")).to_be_hidden()
                    await repeat.get_by_label("How often").select_option("week")
                    await expect(repeat.get_by_label("Which day")).to_be_visible()
                    await repeat.get_by_label("Which day").select_option("1")
                    await repeat.get_by_label("At what time").fill("08:30")
                    await page.screenshot(path=str(tmp_path / f"repeat-{width}.png"))
                    await repeat.get_by_role("button", name="Repeat it").click()
                    said = repeat.locator(".teach-repeated")
                    await expect(said).to_have_text("It repeats on Mondays at 08:30, in this chat. See it in Schedules")
                    await page.screenshot(path=str(tmp_path / f"repeated-{width}.png"))
                    if width == 1280:
                        await said.get_by_role("link", name="See it in Schedules").click()
                        row = page.locator(".list-row.asked")
                        await expect(row).to_contain_text("Pay the invoices of the month.")
                        await expect(row.locator(".badge", has_text="Taught task")).to_be_visible()
                    await page.close()
                made: list[ScheduleRecord] = await runtime.store.list_schedules()
                assert [(each.cron, each.options.skills, each.thread_id) for each in made] == [
                    ("30 8 * * 1", (SKILL.name,), run.thread_id)
                ] * 2
            finally:
                await browser.close()
