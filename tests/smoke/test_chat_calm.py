"""A chat that reads calm (item 352), on a run's page in Chrome and on a WebKit iPhone, light and dark.

- How the run was set up is not in the conversation (item 365): the notes recalled by their words alone, the notes
  handed over, the skills listed by their words alone are lines of the side panel's Run tab, under Set up, whose head
  names them in short; the warning keeps its tone. Before item 352 each was a line of its own between the goal and the
  answer, and before item 365 they were one quiet line there.
- On a phone a chat is one screen: the top bar and the composer stay where they are and only the conversation scrolls
  between them. Before, the window scrolled, and the composer stuck to its foot.
- **On a phone the conversation scrolls up and down only (item 360)**, whatever it holds: a message's foot, a yolo line
  with a long command, a call of a long MCP tool's name; a table or a block of code wider than the phone scrolls in its
  own box. Before, each of these but the last two made the whole conversation scroll sideways.

No network and no model service is used: the scripted model plays the runs of a daemon served by a real server, and
the embedding service is an address of this Mac where nothing listens, as Ollama's is when it is not running.
"""

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.gate import REVIEWER_DECIDER
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

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "chat-calm-test-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_OWNERS_PHONE: str = "iPhone 17 Pro Max"
_NOTE: str = "The owner keeps the invoices in the shared Invoices folder."
_GOAL: str = "Find the invoices and list them"
# An answer longer than a phone's screen, so that the conversation has somewhere to scroll.
_LONG_ANSWER: str = "\n\n".join(f"Invoice {number}: paid in full, filed under March." for number in range(1, 61))
_SCHEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
# What is wider than a phone (item 360): a command and a tool's name with nowhere to break, a table and a block of code.
_LONG_COMMAND: str = (
    "Run: ls /Users/owner/Library/Mobile_Documents/com~apple~CloudDocs/Invoices/2026/March/Paid_in_full"
)
_LONG_REASON: str = "the cautious preset asks before any command"
_LONG_TOOL: str = "mcp__microsoft-learn__microsoft_docs_search"
_WIDE_ANSWER: str = "\n".join(
    (
        "Three invoices:",
        "",
        "| Invoice | Supplier | Filed under | Paid | Note |",
        "|---|---|---|---|---|",
        "| 2026-0311 | Northwind Traders International | Shared › Invoices › March › Paid in full | 18 March | Twice |",
        "| 2026-0412 | Contoso Pharmaceuticals | Shared › Invoices › April › Paid in full | 16 April | Paid by card |",
        "",
        "```",
        "ls -la '/Users/owner/Library/Mobile Documents/com~apple~CloudDocs/Invoices/2026/March/Paid in full/'",
        "```",
    )
)


def _closed_port() -> int:
    """A port of this Mac where nothing listens: one the system gives out, freed again."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(
    login_env: dict[str, str], turns: list[ScriptedTurn]
) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, its notes searched by meaning through an embedding service that does
    not answer, its model playing ``turns``, served at the address it yields."""
    config: AppConfig = eval_config(load_default_config())
    embedding: AppConfig = config.model_copy(
        update={
            "memory": config.memory.model_copy(update={"embed_enabled": True, "embed_provider": "ollama"}),
            "providers": config.providers.model_copy(
                update={
                    "ollama": config.providers.ollama.model_copy(
                        update={"base_url": f"http://127.0.0.1:{_closed_port()}"}
                    )
                }
            ),
        }
    )
    runtime: Runtime = await open_runtime(embedding, login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
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


async def _ended(runtime: Runtime, manager: RunManager, goal: str) -> RunRecord:
    """A run started for ``goal``, once it has ended done."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    return await _done(runtime, run)


async def _done(runtime: Runtime, run: RunRecord) -> RunRecord:
    """``run`` once it has ended done."""
    deadline: float = time.monotonic() + WAIT_S
    while (await runtime.store.get_run(run.id)).status not in ("done", "failed"):
        assert time.monotonic() < deadline, f"the run {run.id} did not end in {WAIT_S} s"
        await asyncio.sleep(0.05)
    ended: RunRecord = await runtime.store.get_run(run.id)
    assert ended.status == "done", ended.result
    return ended


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


# The head of Set up for a long run's lines (item 365), from the page's own module.
_LONG_RUN_SUMMARY: str = """import('/js/setup.js').then(({ setupSummary }) => setupSummary([
  { kind: 'budget', short: 'budget 192k' },
  { kind: 'cadence', short: 'goal every 10 steps' },
  { kind: 'reminder', short: 'goal at step 10' },
  { kind: 'reminder', short: 'goal at step 20' },
  { kind: 'route', short: 'step 3 on gpt-5.6' },
  { kind: 'route-back', short: "step 3 back on the run's model" },
  { kind: 'words', short: 'notes' },
]))"""


async def test_how_a_run_was_set_up_is_in_the_side_panel_not_the_conversation(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    turns: list[ScriptedTurn] = [_finish("Three invoices: March, April and May.") for _ in range(4)]
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, turns) as (runtime, manager, address), async_playwright() as playwright:
            await runtime.store.add_memory(_NOTE, (), None, None, None, False)
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            chromium: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            webkit: Browser = await playwright.webkit.launch(headless=True)
            try:
                for scheme in _SCHEMES:
                    for name, browser, phone in (("wide", chromium, False), ("phone", webkit, True)):
                        options = (
                            {**playwright.devices[_OWNERS_PHONE], "color_scheme": scheme, "reduced_motion": "reduce"}
                            if phone
                            else {
                                "viewport": {"width": 1280, "height": 900},
                                "color_scheme": scheme,
                                "reduced_motion": "reduce",
                            }
                        )
                        context = await browser.new_context(**options)
                        await context.add_init_script(
                            "localStorage.setItem('dot.work', 'steps')"
                        )  # the steps this test reads (item 830)
                        page: Page = await context.new_page()
                        await page.add_init_script(
                            "localStorage.setItem('dot.work', 'steps')"
                        )  # the steps this test reads (item 830)
                        await page.goto(ui_url(host, port, TOKEN))
                        # The side panel is on beside a wide conversation; a phone opens it as a drawer.
                        await page.evaluate("localStorage.setItem('dot.inspector', 'on')")
                        run: RunRecord = await _ended(runtime, manager, f"{_GOAL} ({name}, {scheme})")
                        await page.goto(f"{address}/#/runs/{run.id}")
                        await expect(page.locator(".answer").last).to_be_visible()
                        # Nothing of how the run was set up stands in the conversation.
                        thread: Locator = page.locator(".timeline")
                        await expect(thread).not_to_contain_text("Recalled 1 memory")
                        await expect(thread).not_to_contain_text("by their words alone")
                        await expect(page.locator(".timeline > .note")).to_have_count(0)
                        await page.screenshot(path=tmp_path / f"setup-chat-{name}-{scheme}.png")
                        if phone:
                            await page.locator("button[aria-label='Task details']").click()
                        # Set up is under the Run tab's More (item 452), its warning counted beside the title.
                        more: Locator = page.locator(".inspector").get_by_role("button", name="More")
                        await expect(page.locator(".inspector .run-more-warning")).to_have_text("1 warning")
                        if await more.get_attribute("aria-expanded") == "false":
                            await more.click()
                        setup: Locator = page.locator("#panel-run-setup")
                        await expect(setup).to_be_visible()
                        await expect(setup).to_contain_text("Recalled 1 memory")
                        await expect(setup).to_contain_text(
                            "Notes were recalled by their words alone: the search by meaning did not work."
                        )
                        await expect(setup.locator(".note.warn")).to_have_count(1)
                        summary: Locator = page.locator(".setup-summary")
                        await expect(summary).to_contain_text("1 memory")
                        await expect(summary).to_contain_text("notes found by their words alone")
                        # A kind a long run repeats is said once, with its count.
                        said: str = await page.evaluate(_LONG_RUN_SUMMARY)
                        assert said == (
                            "budget 192k · goal every 10 steps · goal repeated 2 times · step 3 on gpt-5.6 · "
                            "step 3 back on the run's model · notes found by their words alone"
                        ), said
                        await page.screenshot(path=tmp_path / f"setup-panel-{name}-{scheme}.png")
                        await context.close()
            finally:
                await chromium.close()
                await webkit.close()


async def test_on_a_phone_only_the_conversation_scrolls(login_env: dict[str, str], tmp_path: Path) -> None:
    turns: list[ScriptedTurn] = [_finish(_LONG_ANSWER) for _ in range(2)]
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, turns) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            webkit: Browser = await playwright.webkit.launch(headless=True)
            try:
                for scheme in _SCHEMES:
                    context = await webkit.new_context(
                        **playwright.devices[_OWNERS_PHONE], color_scheme=scheme, reduced_motion="reduce"
                    )
                    await context.add_init_script(
                        "localStorage.setItem('dot.work', 'steps')"
                    )  # the steps this test reads (item 830)
                    page: Page = await context.new_page()
                    await page.add_init_script(
                        "localStorage.setItem('dot.work', 'steps')"
                    )  # the steps this test reads (item 830)
                    await page.goto(ui_url(host, port, TOKEN))
                    run: RunRecord = await _ended(runtime, manager, f"{_GOAL} on the phone ({scheme})")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(page.locator(".answer").last).to_contain_text("Invoice 60")
                    composer: Locator = page.locator(".dock .composer")
                    bar: Locator = page.locator(".stagebar")
                    seen: float = await page.evaluate("window.innerHeight")
                    composer_before = await composer.bounding_box()
                    assert composer_before is not None
                    # The conversation is taller than what is seen of it and scrolls inside its own box; the window
                    # has nothing to scroll, and a scroll asked of it moves nothing.
                    thread: Locator = page.locator(".run-thread")
                    assert await thread.evaluate("(box) => getComputedStyle(box).overflowY") == "auto"
                    assert await thread.evaluate("(box) => box.scrollHeight > box.clientHeight + 200")
                    assert await page.evaluate("document.scrollingElement.scrollHeight <= window.innerHeight")
                    await page.evaluate("window.scrollTo(0, 600)")
                    assert await page.evaluate("document.scrollingElement.scrollTop") == 0, "the window scrolled"
                    await thread.evaluate("(box) => box.scrollTo({ top: 0 })")
                    await page.wait_for_timeout(100)
                    assert await thread.evaluate("(box) => box.scrollTop") == 0
                    # The top bar and the composer have not moved, and the composer stands at the foot of what is seen.
                    bar_box = await bar.bounding_box()
                    composer_after = await composer.bounding_box()
                    assert bar_box is not None and composer_after is not None
                    assert bar_box["y"] == 0
                    assert abs(composer_after["y"] - composer_before["y"]) < 1
                    assert composer_after["y"] + composer_after["height"] <= seen
                    assert composer_after["y"] + composer_after["height"] > seen - 120
                    await page.screenshot(path=tmp_path / f"phone-shell-{scheme}.png")
                    # Leaving the chat gives the window its scroll back.
                    await page.goto(f"{address}/#/settings/general")
                    await expect(page.locator("html")).not_to_have_class("chat-shell")
                    await context.close()
            finally:
                await webkit.close()


async def test_on_a_phone_the_conversation_never_scrolls_sideways(login_env: dict[str, str], tmp_path: Path) -> None:
    turns: list[ScriptedTurn] = [
        ScriptedTurn(text="", calls=(ScriptedCall(tool=_LONG_TOOL, arguments={"question": "invoices"}),)),
        _finish(_WIDE_ANSWER),
    ] * len(_SCHEMES)
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, turns) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            webkit: Browser = await playwright.webkit.launch(headless=True)
            try:
                for scheme in _SCHEMES:
                    context = await webkit.new_context(
                        **playwright.devices[_OWNERS_PHONE], color_scheme=scheme, reduced_motion="reduce"
                    )
                    await context.add_init_script(
                        "localStorage.setItem('dot.work', 'steps')"
                    )  # the steps this test reads (item 830)
                    page: Page = await context.new_page()
                    await page.add_init_script(
                        "localStorage.setItem('dot.work', 'steps')"
                    )  # the steps this test reads (item 830)
                    await page.goto(ui_url(host, port, TOKEN))
                    # The review model's line shows only once such lines are turned on (item 362); here it is one of
                    # what must not widen the chat.
                    await page.evaluate("localStorage.setItem('dot.autoLines', 'shown')")
                    started: RunRecord = await manager.start(
                        f"{_GOAL} in a table ({scheme})",
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
                    await runtime.approvals.record_automatic(
                        started.id, "shell", _LONG_COMMAND, "medium", {}, "approved", REVIEWER_DECIDER, _LONG_REASON
                    )
                    run: RunRecord = await _done(runtime, started)
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(page.locator(".answer").last).to_contain_text("Three invoices")
                    for head in await page.locator("details.steps:not([open]) > summary.steps-head").all():
                        await head.click()
                    await expect(page.locator(".auto-decision")).to_contain_text("Paid_in_full")
                    await expect(page.locator(f".tool-name[title='{_LONG_TOOL}']")).to_be_visible()
                    await expect(page.locator(".user-message .message-foot")).to_be_visible()
                    # Nothing reaches past the conversation's box, and the box scrolls up and down only.
                    thread: Locator = page.locator(".run-thread")
                    assert await thread.evaluate("(box) => getComputedStyle(box).overflowX") == "hidden"
                    wide, seen = await thread.evaluate("(box) => [box.scrollWidth, box.clientWidth]")
                    assert wide <= seen, f"the conversation is {wide - seen} px wider than its box"
                    assert await thread.evaluate("(box) => { box.scrollLeft = 120; return box.scrollLeft; }") == 0
                    # The table and the block of code are wider than the phone, and each scrolls in its own box.
                    for own in (page.locator(".answer .prose .prose-table-scroll"), page.locator(".answer .prose pre")):
                        assert await own.evaluate("(box) => box.scrollWidth > box.clientWidth + 40")
                    await page.screenshot(path=tmp_path / f"phone-width-{scheme}.png")
                    await context.close()
            finally:
                await webkit.close()
