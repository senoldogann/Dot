"""The line of an action a yolo run did without asking (item 253), on a run's page in Chrome: the action and the
policy's reason read once each, for a record the gate keeps now and for one kept before, whose summary ended with its
note. Wide and on a phone, light and dark. Before, the line said the reason twice. Such lines are out of the chat until
Settings › General brings them in, and it takes them out again (items 346 and 362).

No network and no model service is used: the scripted model plays the runs of a daemon served by a real server.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.gate import YOLO_DECIDER
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "yolo-line-test-token"
WAIT_S: float = 30.0
COMMAND: str = "touch shelf.txt"
OLD_SUMMARY: str = "Run: touch old-shelf.txt"
OLD_REASON: str = "the cautious preset asks before any command"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_TURNS: list[ScriptedTurn] = [
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": COMMAND}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Touched.", "success": True}),)),
] * len(_SHOTS)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, under the cautious preset (it asks before any command, which a yolo
    run waives), whose model plays ``_TURNS``, served at the address it yields."""
    config: AppConfig = eval_config(load_default_config())
    cautious: AppConfig = config.model_copy(update={"policy": config.policy.model_copy(update={"preset": "cautious"})})
    runtime: Runtime = await open_runtime(cautious, login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _TURNS))
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
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _waived(runtime: Runtime, manager: RunManager, goal: str) -> tuple[RunRecord, ApprovalRecord]:
    """A yolo run started for ``goal``, ended, and the record of the command it did without asking."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    # A record kept before item 253 ended its summary with its note; it goes in before the run's own.
    await runtime.approvals.record_automatic(
        run.id, "shell", f"{OLD_SUMMARY} — {OLD_REASON}", "medium", {}, "approved", YOLO_DECIDER, OLD_REASON
    )
    deadline: float = time.monotonic() + WAIT_S
    while (await runtime.store.get_run(run.id)).status not in ("done", "failed"):
        assert time.monotonic() < deadline, f"the run {run.id} did not end in {WAIT_S} s"
        await asyncio.sleep(0.05)
    assert (await runtime.store.get_run(run.id)).status == "done"
    (record,) = [
        record
        for record in await runtime.store.list_approvals(None, 50)
        if record.run_id == run.id and record.decided_by == YOLO_DECIDER and record.note != OLD_REASON
    ]
    return run, record


async def _lines(page: Page, count: int) -> list[str]:
    """The texts of the yolo lines of the page once ``count`` of them are there, their folded group opened, so that
    the lines are in sight."""
    lines: Locator = page.locator(".auto-decision.yolo")
    deadline: float = time.monotonic() + WAIT_S
    while await lines.count() < count:
        assert time.monotonic() < deadline, (
            f"{await lines.count()} yolo lines, not {count}: {await lines.all_text_contents()}"
        )
        await asyncio.sleep(0.1)
    for head in await page.locator("details.steps:not([open]) > summary.steps-head").all():
        await head.click()
    await lines.last.wait_for(state="visible")
    await lines.first.scroll_into_view_if_needed()
    return [text.strip() for text in await lines.all_text_contents()]


@pytest.mark.browser
async def test_the_line_of_an_action_done_without_asking_says_the_reason_once(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    # The lines show only once they are turned on (item 362).
                    await page.evaluate("localStorage.setItem('dot.autoLines', 'shown')")
                    run, record = await _waived(runtime, manager, f"Touch the shelf ({width}, {scheme})")
                    # The record keeps the action and the reason apart, each once.
                    assert record.summary.startswith(f"Run: {COMMAND}") and record.note != ""
                    assert record.note not in record.summary
                    await page.goto(f"{address}/#/runs/{run.id}")
                    texts: list[str] = await _lines(page, 2)
                    assert texts == [
                        f"YOLO, not asked: {OLD_SUMMARY} — {OLD_REASON}",
                        f"YOLO, not asked: {record.summary} — {record.note}",
                    ]
                    await page.screenshot(path=tmp_path / f"yolo-line-{width}-{scheme}.png")
                    await page.close()
            finally:
                await browser.close()


@pytest.mark.browser
async def test_settings_keep_the_lines_of_what_ran_without_asking_out_of_the_chat(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Items 346 and 362: the lines are out of a chat until the switch of Settings › General brings them in, and it
    takes them out again. Before item 346 they always showed, and before item 362 until the switch was turned off."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in ((1280, "light"), (390, "dark")):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    run, _ = await _waived(runtime, manager, f"Touch the shelf again ({width}, {scheme})")
                    # Off as it starts: the chat leaves the lines out.
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(page.locator(".answer").last).to_be_visible()
                    await expect(page.locator(".auto-decision")).to_have_count(0)
                    shown: Locator = page.get_by_role("switch", name="Show what ran without asking")
                    await page.goto(f"{address}/#/settings/general")
                    await shown.scroll_into_view_if_needed()
                    await expect(shown).to_have_attribute("aria-checked", "false")
                    await page.screenshot(path=tmp_path / f"chat-card-{width}-{scheme}.png")
                    await shown.click()
                    await expect(shown).to_have_attribute("aria-checked", "true")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    assert len(await _lines(page, 2)) == 2
                    await page.goto(f"{address}/#/settings/general")
                    await shown.click()
                    await expect(shown).to_have_attribute("aria-checked", "false")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(page.locator(".answer").last).to_be_visible()
                    await expect(page.locator(".auto-decision")).to_have_count(0)
                    await page.close()
            finally:
                await browser.close()
