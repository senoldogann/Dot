"""Each step's thinking is a block that folds (item 246), on the pages of a daemon in Chrome. While the model thinks,
the block says "Thinking…" with the start of the thought beside it, folded; the reader may open it and read the thought
as it comes. Once the step is over it says "Thought" and is the first row of the step's group, before the step's call;
the thought of a step that writes a message stands before the message, out of the group before it, and so does the
thought of the step that ends the run, before the answer. The `assistant` events keep the thinking, of a long one its
start and its end, so the page loaded again shows the same blocks in the same places, folded: each opens to its thought
and folds again, and a long one scrolls within its box. Wide and on a phone, light and dark. Before, a line said
"Thinking…" with a count of characters and went away when the step ended, and nothing of the thought was kept.

No network and no model service is used: a scripted model streams the thoughts.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.text import truncate_middle
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply, StreamDelta
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "thinking-blocks-test-token"
WAIT_S: float = 30.0
FIRST: str = (
    "The shelves are numbered from the door.\n"
    "Shelf 1 holds the atlases, so I start there and count its rows.\n"
    "Then I write the count down."
)
FIRST_LINE: str = (
    "The shelves are numbered from the door. Shelf 1 holds the atlases, so I start there and count its rows. Then I "
    "write the count down."
)
# A thought of more than 20,000 characters: the events keep 12,000 of it, its start and its end.
SECOND: str = "\n".join(
    f"Row {row} of the shelf holds forty books, the same as the catalogue says." for row in range(300)
)
THIRD: str = "Both shelves are counted, so I can answer."
# What the second step writes before its call.
MESSAGE: str = "Forty books a row on the first shelf; now the second one."
# What the run's conversation shows, in order, by kind (see `_order`): the first step's group, its thought its first
# row; the second step's thought and message, then its group; the last step's thought, then the answer.
_ORDER: list[str] = ["group", "thought", "message", "group", "thought", "answer"]
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "echo shelf-1"}),)),
    ScriptedTurn(text=MESSAGE, calls=(ScriptedCall(tool="shell", arguments={"command": "echo shelf-2"}),)),
    ScriptedTurn(
        text="",
        calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The first shelf holds 40.", "success": True}),),
    ),
)


class ThinkingProvider(ScriptedProvider):
    """The scripted model, which thinks before each reply: it streams the turn's thought in twenty pieces or so, then
    the turn's text, and returns the thought with the reply. The first step starts to think when the test says that the
    page follows the run (what a model streams goes live only, it is not stored), and holds after it until the test lets
    it go."""

    def __init__(self, thoughts: Sequence[str], start: asyncio.Event, hold: asyncio.Event) -> None:
        super().__init__(_PROVIDER, _TURNS)
        self._thoughts: tuple[str, ...] = tuple(thoughts)
        self._start: asyncio.Event = start
        self._hold: asyncio.Event = hold
        self._asked: int = 0

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        thought: str = self._thoughts[self._asked]
        self._asked += 1
        if self._asked == 1:
            await self._start.wait()
        piece: int = max(40, len(thought) // 20)
        for at in range(0, len(thought), piece):
            await sink(StreamDelta(kind="thinking", text=thought[at : at + piece]))
        if self._asked == 1:
            await self._hold.wait()
        reply: ModelReply = await super().complete(request, sink)
        if reply.text != "":
            await sink(StreamDelta(kind="text", text=reply.text))
        return reply.model_copy(update={"thinking": thought})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(
    login_env: dict[str, str], start: asyncio.Event, hold: asyncio.Event
) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, whose model thinks ``FIRST``, ``SECOND`` and ``THIRD``, served at
    the address it yields."""
    config: AppConfig = eval_config(load_default_config())
    runtime: Runtime = await open_runtime(config, login_env)
    try:
        await runtime.router.register(_PROVIDER, ThinkingProvider((FIRST, SECOND, THIRD), start, hold))
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


async def _signed_in(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    """A page of the daemon at ``address``, signed in."""
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    return page


async def _folded(block: Locator) -> bool:
    """Whether the details ``block`` is as tall as its summary: what it holds takes no room. Its box is read, as
    WebKit's Playwright counts the content of a closed details as visible."""
    return await block.evaluate(
        "(node) => node.getBoundingClientRect().height <= "
        "node.querySelector('summary').getBoundingClientRect().height + 1"
    )


async def _order(page: Page) -> list[str]:
    """What the conversation of the run on ``page`` shows, in order, by kind: a group of steps, a thought out of the
    groups, a message of Dot's, the answer; anything else is left out."""
    return await page.locator(".timeline").first.evaluate(
        "(timeline) => [...timeline.children].flatMap((node) =>"
        " node.matches('details.steps') ? ['group']"
        " : node.matches('details.thought') ? ['thought']"
        " : node.matches(\".bubble[aria-label='Dot']\") ? ['message']"
        " : node.matches('.answer') ? ['answer'] : [])"
    )


async def _open_run(browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await _signed_in(browser, address, width, scheme)
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


@pytest.mark.browser
async def test_each_steps_thinking_is_a_block_that_folds_live_and_loaded_again(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    start: asyncio.Event = asyncio.Event()
    hold: asyncio.Event = asyncio.Event()
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env, start, hold) as (runtime, manager, address), async_playwright() as playwright:
            run: RunRecord = await manager.start(
                "Count the books on the first shelf",
                "task",
                SCRIPTED_MODEL,
                None,
                "test",
                None,
                None,
                "standard",
                None,
                "ask",
                False,
                (),
            )
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                # The page follows the run once its stream answers (the server listens to the run as it answers): then
                # the model starts to think.
                page: Page = await _signed_in(browser, address, 1280, "light")
                async with page.expect_response(lambda response: f"/runs/{run.id}/stream" in response.url):
                    await page.goto(f"{address}/#/runs/{run.id}")
                start.set()
                # While the model thinks: "Thinking…" with the brain, folded, the start of the thought beside it.
                thought: Locator = page.locator(".thought").first
                await expect(thought.locator(".thought-label")).to_have_text("Thinking…")
                await expect(thought).to_have_attribute("data-live", "true")
                await expect(thought.locator("svg[data-icon='brain']")).to_have_count(1)
                await expect(thought).to_have_js_property("open", False)
                await expect(thought.locator(".thought-preview")).to_have_text(FIRST_LINE)
                await page.screenshot(path=str(tmp_path / "thinking-live.png"))
                # The reader opens it while the step is under way and reads the thought so far.
                await thought.locator("summary").click()
                await expect(thought.locator(".thought-text")).to_have_text(FIRST)
                hold.set()
                async with asyncio.timeout(WAIT_S):
                    assert (await manager.wait(run.id)).status == "done"
                # Over, it says "Thought", open as the reader left it, the first row of its step's group.
                await expect(thought.locator(".thought-label")).to_have_text("Thought")
                await expect(thought).to_have_attribute("data-live", "false")
                await expect(thought).to_have_js_property("open", True)
                assert await thought.evaluate(
                    "(node) => node.parentElement.classList.contains('steps-body')"
                    " && node.parentElement.firstElementChild === node"
                ), "the thought is not the first row of its step's group"
                # The reader read it: the group it went into opened with it.
                assert await thought.evaluate("(node) => node.closest('details.steps').open")
                # The thought of the step that wrote stands before its message, that of the last step before the answer.
                await expect(page.locator(".thought")).to_have_count(3)
                assert await _order(page) == _ORDER
                await page.close()

                # The events keep the thinking: whole, and of a long one its start and its end.
                steps: list[EventRecord] = await runtime.store.events_of_kind(run.id, "assistant", 10)
                assert [event.data["thinking"] for event in steps] == [FIRST, truncate_middle(SECOND, 12_000), THIRD]

                for width, scheme in _SHOTS:
                    page = await _open_run(browser, address, run.id, width, scheme)
                    blocks: Locator = page.locator(".thought")
                    await expect(blocks).to_have_count(3)
                    await expect(blocks.locator(".thought-label")).to_have_text(["Thought", "Thought", "Thought"])
                    assert await _order(page) == _ORDER
                    # The first thought is a row of its step's group, folded at first as a group of steps is.
                    group: Locator = page.locator("details.steps").filter(has=blocks.first)
                    await group.locator("summary.steps-head").click()
                    await expect(group).to_have_js_property("open", True)
                    first: Locator = blocks.first
                    await expect(first).to_have_js_property("open", False)
                    assert await _folded(first)
                    await expect(first.locator(".thought-preview")).to_have_text(FIRST_LINE)
                    await page.screenshot(path=str(tmp_path / f"thoughts-folded-{width}-{scheme}.png"))
                    await first.locator("summary").click()
                    await expect(first.locator(".thought-text")).to_have_text(FIRST)
                    assert not await _folded(first)
                    long: Locator = blocks.nth(1)
                    assert await _folded(long)
                    await long.locator("summary").click()
                    await expect(long.locator(".thought-text")).to_contain_text("chars omitted")
                    # A long thought scrolls within its box.
                    assert await long.locator(".thought-text").evaluate("(box) => box.scrollHeight > box.clientHeight")
                    await page.screenshot(path=str(tmp_path / f"thoughts-open-{width}-{scheme}.png"))
                    await first.locator("summary").click()
                    await expect(first).to_have_js_property("open", False)
                    assert await _folded(first)
                    await page.close()
            finally:
                start.set()
                hold.set()
                await browser.close()
