"""What an action acts on, whole, on its approval card (item 242), on the pages of a daemon in Chrome: a long command is
shown whole, wrapped within the card, in a box that scrolls past a dozen lines. Wide and on a phone, light and dark.
Before, the card showed its first 300 characters on one line, cut at the card's edge. The card is in its own chat alone:
the home lists the run that waits without it, no line above it says the same (item 871), and once allowed it leaves the
chat (item 434); refused, it stays and says so.

No network and no model service is used: the scripted model plays the runs of a daemon served by a real server.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

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

TOKEN: str = "approval-target-test-token"
WAIT_S: float = 30.0
# A command of 9,605 characters that makes 600 empty files.
COMMAND: str = "touch " + " ".join(f"shelf-{number:05}.txt" for number in range(600))
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_TURNS: list[ScriptedTurn] = [
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": COMMAND}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Left.", "success": True}),)),
] * len(_SHOTS)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, under the cautious preset (it asks before any command), whose model
    plays ``_TURNS``, served at the address it yields."""
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


async def _asking(runtime: Runtime, manager: RunManager, goal: str) -> tuple[RunRecord, ApprovalRecord]:
    """A run started for ``goal`` whose command waits for the user's approval, and that approval."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    deadline: float = time.monotonic() + WAIT_S
    while not (
        asked := [record for record in await runtime.store.list_approvals("pending", 20) if record.run_id == run.id]
    ):
        assert time.monotonic() < deadline, f"the run {run.id} asked nothing in {WAIT_S} s"
        await asyncio.sleep(0.05)
    return run, asked[0]


@pytest.mark.browser
async def test_an_approval_card_shows_a_long_command_whole_wrapped_in_a_box_that_scrolls(
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
                    run, approval = await _asking(runtime, manager, f"Write the shelves ({width}, {scheme})")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    card: Locator = page.locator(".approval").filter(has=page.get_by_role("button", name="Deny"))
                    target: Locator = card.get_by_label("What the action acts on")
                    await target.wait_for()
                    assert await target.text_content() == COMMAND
                    # Wrapped within its box, which scrolls; the card stays within the window.
                    assert await target.evaluate("(box) => box.scrollWidth <= box.clientWidth")
                    assert await target.evaluate("(box) => box.scrollHeight > box.clientHeight * 2")
                    assert await card.evaluate("(card) => card.getBoundingClientRect().right <= window.innerWidth")
                    await card.scroll_into_view_if_needed()
                    await page.screenshot(path=str(tmp_path / f"target-{width}-{scheme}.png"))
                    await card.get_by_role("button", name="Deny").click()
                    async with asyncio.timeout(WAIT_S):
                        assert (await manager.wait(run.id)).status == "done"
                    assert (await runtime.store.get_approval(approval.id)).status == "denied"
                    await page.close()
            finally:
                await browser.close()


async def _decided_shows(page: Page, approved: bool) -> None:
    """What the chat of a run that ended shows of its approval: nothing when it was allowed (item 434), else the refused
    card, alone: the line that asked said what it says (item 871)."""
    chat: Locator = page.locator(".timeline")
    # The run's answer is there: every event before it has been read.
    await expect(chat).to_contain_text("Left.")
    cards: Locator = chat.locator(".approval")
    lines: Locator = chat.locator(".note", has_text="Needs approval")
    await expect(cards).to_have_count(0 if approved else 1)
    await expect(lines).to_have_count(0)
    if not approved:
        await expect(cards).to_contain_text("Denied")


@pytest.mark.browser
async def test_an_approval_card_is_only_in_its_chat_and_leaves_it_once_allowed(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width in (1280, 390):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    for approved in (True, False):
                        run, _ = await _asking(runtime, manager, f"Write the shelves ({width}, {approved})")
                        # The home says something waits, without the card; the sidebar lists the run under Waiting
                        # for you (the sections moved there from the home, item 577).
                        await page.goto(f"{address}/#/")
                        await expect(page.locator(".home")).to_contain_text("waits for you")
                        await expect(page.locator(".home .approval")).to_have_count(0)
                        if width < 860:
                            await page.get_by_role("button", name="Show sidebar").click()
                        # Its chat stands first, its last line saying what waits (item 836), and its name for a
                        # screen reader how.
                        waits: Locator = page.locator("#sidebar [data-section=waiting] .side-chat > a.side-run")
                        await expect(waits).to_have_count(1)
                        await expect(waits.locator(".side-chat-waits")).to_contain_text("Waiting for you: ")
                        await expect(waits).to_have_attribute("aria-label", re.compile("waits for your answer"))
                        if width < 860:
                            await page.keyboard.press("Escape")
                        await page.goto(f"{address}/#/runs/{run.id}")
                        card: Locator = page.locator(".timeline .approval")
                        await expect(card).to_have_count(1)
                        # The card stands alone: the line that asked said what it says (item 871).
                        await expect(page.locator(".timeline .note", has_text="Needs approval")).to_have_count(0)
                        await card.get_by_role("button", name="Approve" if approved else "Deny").click()
                        async with asyncio.timeout(WAIT_S):
                            assert (await manager.wait(run.id)).status == "done"
                        await _decided_shows(page, approved)
                        await page.screenshot(path=str(tmp_path / f"decided-{width}-{approved}.png"))
                        # The chat opened again reads the same.
                        await page.reload()
                        await _decided_shows(page, approved)
                    await page.close()
            finally:
                await browser.close()


@pytest.mark.browser
async def test_the_bell_leads_to_the_chat_that_asks_and_not_to_the_list_of_chats(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """The owner's word of 2026-10-20: the bell of the header leads to the chat whose approval waits, its card there,
    and not to the home's list — wide and on a phone."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width in (1280, 390):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))
                    older, _first = await _asking(runtime, manager, f"Write the shelves (bell, {width})")
                    # A newer approval waits too: the store lists them newest first, and the bell must lead to the one
                    # that has waited longest all the same.
                    newer = await runtime.store.create_approval(
                        "run_older_test_bell", "shell", "A newer thing that waits", "high", {}
                    )
                    await page.goto(f"{address}/#/")
                    bell: Locator = page.locator(".pill.warn").first
                    await expect(bell).to_be_visible()
                    await bell.click()
                    await expect(page).to_have_url(re.compile(rf"#/runs/{older.id}$"))
                    card: Locator = page.locator(".timeline .approval")
                    await expect(card).to_have_count(1)
                    await card.get_by_role("button", name="Deny").click()
                    async with asyncio.timeout(WAIT_S):
                        assert (await manager.wait(older.id)).status == "done"
                    # The newer one waits still, and the bell leads there now.
                    await expect(bell).to_be_visible()
                    await expect(bell).to_have_attribute("href", f"#/runs/{newer.run_id}")
                    await runtime.store.decide_approval(newer.id, "denied", "test", "")
                    # Nothing waits now, and the bell hides with the card — the page reads the approvals again on its
                    # own clock (every fifteen seconds) when nothing told it they changed.
                    await expect(page.locator(".pill.warn").first).to_be_hidden(timeout=20_000)
                    await page.close()
            finally:
                await browser.close()
