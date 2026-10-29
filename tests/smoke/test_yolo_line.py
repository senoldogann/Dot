"""What a run's page draws of the actions nobody was asked about, on a run's page in Chrome, wide and on a phone, light
and dark. A yolo run asks nothing (item 443) and its steps draw no line at all (item 500): the records and the events
keep what it did and why it would have asked, and the chat does not warn about the mode. The review model's line reads
the action and the reason once each, for a record kept now and for one kept before item 253, whose summary ended with
its note. Such lines are out of the chat until Settings › General brings them in, and it takes them out again (items 346
and 362).

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

from dotagent.agent.gate import REVIEWER_DECIDER, YOLO_DECIDER
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
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "yolo-line-test-token"
WAIT_S: float = 30.0
COMMAND: str = "touch shelf.txt"
OLD_SUMMARY: str = "Run: touch old-shelf.txt"
OLD_REASON: str = "the cautious preset asks before any command"
REVIEW_SUMMARY: str = "Write the shelf"
REVIEW_REASON: str = "the cautious preset asks before a write"
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


async def _waived(runtime: Runtime, manager: RunManager, goal: str) -> RunRecord:
    """A yolo run started for ``goal`` and ended; it did a command it was never asked about, and the review model
    answered two questions it kept, one of them in the shape of item 253's records."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    # Records kept before item 253 ended their summary with their note; they go in before the run's own.
    await runtime.approvals.record_automatic(
        run.id, "shell", f"{OLD_SUMMARY} — {OLD_REASON}", "medium", {}, "approved", REVIEWER_DECIDER, OLD_REASON
    )
    await runtime.approvals.record_automatic(
        run.id, "write_file", REVIEW_SUMMARY, "medium", {}, "approved", REVIEWER_DECIDER, REVIEW_REASON
    )
    deadline: float = time.monotonic() + WAIT_S
    while (await runtime.store.get_run(run.id)).status not in ("done", "failed"):
        assert time.monotonic() < deadline, f"the run {run.id} did not end in {WAIT_S} s"
        await asyncio.sleep(0.05)
    assert (await runtime.store.get_run(run.id)).status == "done"
    return run


async def _lines(page: Page, count: int) -> list[str]:
    """The texts of the decision lines of the page once ``count`` of them are there, their folded group opened, so that
    the lines are in sight."""
    lines: Locator = page.locator(".auto-decision")
    deadline: float = time.monotonic() + WAIT_S
    while await lines.count() < count:
        assert time.monotonic() < deadline, (
            f"{await lines.count()} lines, not {count}: {await lines.all_text_contents()}"
        )
        await asyncio.sleep(0.1)
    for head in await page.locator("details.steps:not([open]) > summary.steps-head").all():
        await head.click()
    await lines.last.wait_for(state="visible")
    await lines.first.scroll_into_view_if_needed()
    return [text.strip() for text in await lines.all_text_contents()]


@pytest.mark.browser
async def test_a_yolo_step_draws_no_line_and_a_reviewed_one_says_the_reason_once(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Item 500: the command a yolo run did without asking draws no line, and no line of the page says the mode; the
    review model's lines show, as the switch under test is turned on, and each says the reason once."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.add_init_script(
                        "localStorage.setItem('dot.work', 'steps')"
                    )  # the steps this test reads (item 830)
                    await page.goto(ui_url(host, port, TOKEN))
                    # The lines show only once they are turned on (item 362).
                    await page.evaluate("localStorage.setItem('dot.autoLines', 'shown')")
                    run: RunRecord = await _waived(runtime, manager, f"Touch the shelf ({width}, {scheme})")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    texts: list[str] = await _lines(page, 2)
                    assert texts == [
                        f"The reviewer approved: {OLD_SUMMARY} — {OLD_REASON}",
                        f"The reviewer approved: {REVIEW_SUMMARY} — {REVIEW_REASON}",
                    ]
                    # The step the run did without asking has its record, and no line of the chat says so (item 500).
                    waivers = [
                        record
                        for record in await runtime.store.list_approvals(None, 50)
                        if record.run_id == run.id and record.decided_by == YOLO_DECIDER
                    ]
                    assert [record.status for record in waivers] == ["approved"], waivers
                    await expect(page.locator(".auto-decision.yolo")).to_have_count(0)
                    assert "YOLO" not in await page.locator(".run-thread").inner_text()
                    await page.screenshot(path=tmp_path / f"yolo-line-{width}-{scheme}.png")
                    await page.close()
            finally:
                await browser.close()


@pytest.mark.browser
async def test_settings_keep_the_lines_of_the_review_models_decisions_out_of_the_chat(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """Items 346 and 362: the review model's lines are out of a chat until the switch of Settings › General brings them
    in, and it takes them out again; a yolo run's step draws none either way (item 500)."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in ((1280, "light"), (390, "dark")):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.add_init_script(
                        "localStorage.setItem('dot.work', 'steps')"
                    )  # the steps this test reads (item 830)
                    await page.goto(ui_url(host, port, TOKEN))
                    run: RunRecord = await _waived(runtime, manager, f"Touch the shelf again ({width}, {scheme})")
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
                    await expect(page.locator(".auto-decision.yolo")).to_have_count(0)
                    await page.goto(f"{address}/#/settings/general")
                    await shown.click()
                    await expect(shown).to_have_attribute("aria-checked", "false")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(page.locator(".answer").last).to_be_visible()
                    await expect(page.locator(".auto-decision")).to_have_count(0)
                    await page.close()
            finally:
                await browser.close()
