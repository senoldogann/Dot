"""The words in the composer (item 259): `ultrathink` or `ultracode` typed in the home's box or a chat's dock, as a
whole word of its own in any case, is the choice for one message of item 251 for that message when the model offers
its level. The Reasoning pill reads it, the frame wears a gradient border (`data-boost`), the run starts with `once`
and the word stays in its goal; deleting the word puts the choice away, a word inside another word is no choice, the
first of the two words wins, and a model without the level reads the word as any other. While a run works, the word
goes with a queued message; the box editing a queued message arms nothing, and a steer, which joins the run under way at
its level, says the choice waits. A real server over a stand-in of Anthropic's provider in Chrome, wide and on a phone,
light and dark. Before, the choice was the menu's alone."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, Effort, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "composer-words-token"
OPUS: str = "anthropic:claude-opus-5-5"
HAIKU: str = "anthropic:claude-haiku-5-5"
# Opus offers both levels, Haiku neither.
_EFFORTS: dict[str, tuple[Effort, ...]] = {
    "claude-opus-5-5": ("low", "medium", "high", "xhigh", "max"),
    "claude-haiku-5-5": ("low", "medium", "high"),
}
_RUN_WAIT_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_HOME_FRAME: str = "section.composer[aria-label='New chat']"
_HOME_GOAL: str = "textarea[aria-label='Goal']"
_ANY: re.Pattern[str] = re.compile(r".+")
_REPLY: ScriptedTurn = ScriptedTurn(text="Hi.", calls=())
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "Done.", "success": True}),)
)


class Claude:
    """Anthropic's provider as far as the box and a run need it: the models of ``_EFFORTS``; every call answers once
    ``go`` is set."""

    def __init__(self, go: asyncio.Event) -> None:
        self._go: asyncio.Event = go

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        await self._go.wait()
        turn: ScriptedTurn = _FINISH if any(tool.name == "finish" for tool in request.tools) else _REPLY
        return await ScriptedProvider(self.name, [turn]).complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return ModelInfo(
            id=model,
            vision=True,
            tools=True,
            thinking=True,
            context_length=1_000_000,
            own_context_length=None,
            window_source="provider",
            efforts=_EFFORTS[model],
            free=False,
        )

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return tuple([await self.model_info(model) for model in _EFFORTS])

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def go() -> asyncio.Event:
    """Set while the stand-in answers; a test that holds a run clears it."""
    event: asyncio.Event = asyncio.Event()
    event.set()
    return event


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, go: asyncio.Event) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose default model is Opus 5.5, served by the stand-in."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f'[browser]\nheadless = true\n\n[models]\nmain = "{OPUS}"\n', encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register("anthropic", Claude(go))
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


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(_RUN_WAIT_S):
        return await manager.wait(run_id)


async def _expect_boost(frame: Locator, pill: Locator, boost: str | None, words: str) -> None:
    """The frame wears `boost` (or none) and the pill reads `words`."""
    if boost is None:
        await expect(frame).not_to_have_attribute("data-boost", _ANY)
    else:
        await expect(frame).to_have_attribute("data-boost", boost)
    await expect(pill).to_contain_text(words)


@pytest.mark.browser
async def test_the_word_in_the_box_is_the_choice_for_that_message(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    # A chat of the model without the levels, for its dock.
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        response: httpx.Response = await http.post(
            "/api/runs", json={"goal": "Say hi.", "mode": "chat", "model": HAIKU}
        )
        assert response.status_code == 201, response.text
        haiku: RunRecord = await _ended(manager, response.json()["id"])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs")
                frame: Locator = page.locator(_HOME_FRAME)
                pill: Locator = frame.locator(".composer-pill[data-pill='reasoning']")
                goal: Locator = page.locator(_HOME_GOAL)
                await _expect_boost(frame, pill, None, "Medium")
                await goal.fill("ultrathink: say hi.")
                await _expect_boost(frame, pill, "ultrathink", "Ultrathink")
                await page.screenshot(path=str(tmp_path / f"word-{width}-{scheme}.png"), animations="disabled")
                if index > 0:
                    await page.close()
                    continue
                # A word inside another word is no choice; the first of the two words wins; deleting it puts it away.
                await goal.fill("ultrathinking about it")
                await _expect_boost(frame, pill, None, "Medium")
                await goal.fill("Say hi, ULTRACODE and ultrathink.")
                await _expect_boost(frame, pill, "ultracode", "Ultracode")
                await goal.fill("Say hi, and ultrathink.")
                await _expect_boost(frame, pill, "ultrathink", "Ultrathink")
                await goal.fill("Say hi.")
                await _expect_boost(frame, pill, None, "Medium")
                # The message goes with the choice, the word stays in its goal, and the box is plain again after.
                await goal.fill("Say hi, and ultrathink.")
                await _expect_boost(frame, pill, "ultrathink", "Ultrathink")
                await goal.press("Enter")
                await expect(page).to_have_url(re.compile(r"#/runs/run_"))
                run_id: str = page.url.rpartition("/")[2]
                thought: RunRecord = await _ended(manager, run_id)
                assert (thought.effort, thought.once, thought.goal) == ("max", "ultrathink", "Say hi, and ultrathink.")
                dock: Locator = page.locator(".dock .composer")
                dock_pill: Locator = dock.locator(".composer-pill[data-pill='reasoning']")
                dock_goal: Locator = dock.locator("textarea")
                await _expect_boost(dock, dock_pill, None, "Medium")
                await dock_goal.fill("ultracode: compare them")
                await _expect_boost(dock, dock_pill, "ultracode", "Ultracode")
                await page.screenshot(path=str(tmp_path / "dock-ultracode.png"), animations="disabled")
                await dock_goal.fill("")
                await _expect_boost(dock, dock_pill, None, "Medium")
                # The model without the levels reads the word as any other.
                await page.goto(f"{address}/#/runs/{haiku.id}")
                dock = page.locator(".dock .composer")
                await dock.locator("textarea").fill("ultrathink: say hi.")
                await expect(dock.locator(".composer-pill[data-pill='reasoning']")).to_contain_text("Medium")
                await expect(dock).not_to_have_attribute("data-boost", _ANY)
                await expect(dock.locator(".composer-pill[data-pill='reasoning']")).not_to_contain_text("Ultrathink")
                await page.screenshot(path=str(tmp_path / "dock-haiku.png"), animations="disabled")
                await page.close()
        finally:
            await browser.close()


async def _next_run(runtime: Runtime, thread_id: str, after: str) -> str:
    """The id of the run the chat ``thread_id`` starts after the run ``after``."""
    async with asyncio.timeout(_RUN_WAIT_S):
        while True:
            newest: list[RunRecord] = await runtime.store.list_runs(1, thread_id)
            if newest and newest[0].id != after:
                return newest[0].id
            await asyncio.sleep(0.1)


@pytest.mark.browser
async def test_while_a_run_works_the_word_goes_with_a_queued_message_and_not_with_an_edit_or_a_steer(
    runtime: Runtime, served: tuple[RunManager, str], go: asyncio.Event
) -> None:
    manager, address = served
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    go.clear()
    try:
        async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
            response: httpx.Response = await http.post(
                "/api/runs", json={"goal": "Say hi.", "mode": "chat", "model": OPUS}
            )
            assert response.status_code == 201, response.text
        held: str = response.json()["id"]
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{address}/#/runs/{held}")
                dock: Locator = page.locator(".dock .composer")
                pill: Locator = dock.locator(".composer-pill[data-pill='reasoning']")
                box: Locator = dock.locator("textarea")
                rows: Locator = page.locator(".queue-row")
                await expect(box).to_have_attribute("placeholder", re.compile(r"^Queue a message"))
                # Queued, the message keeps the word's choice for the run it starts.
                await box.fill("ultrathink: then this.")
                await _expect_boost(dock, pill, "ultrathink", "Ultrathink")
                await box.press("Enter")
                await expect(rows).to_have_count(1)
                await _expect_boost(dock, pill, None, "Medium")
                # Editing the queued message, the box arms nothing; the draft's word is back after.
                await box.fill("ultracode: a draft.")
                await _expect_boost(dock, pill, "ultracode", "Ultracode")
                await rows.nth(0).get_by_role("button", name="Edit queued message").click()
                await expect(box).to_have_value("ultrathink: then this.")
                await _expect_boost(dock, pill, None, "Medium")
                await box.press("Escape")
                await expect(box).to_have_value("ultracode: a draft.")
                await _expect_boost(dock, pill, "ultracode", "Ultracode")
                # A steer joins the run under way and says the choice waits.
                await box.press("ControlOrMeta+Shift+Enter")
                await expect(page.locator(".toast")).to_contain_text("keeps its level")
                await expect(box).to_have_value("")
                await _expect_boost(dock, pill, None, "Medium")
                await page.close()
            finally:
                await browser.close()
        go.set()
        first: RunRecord = await _ended(manager, held)
        assert first.once is None
        queued: RunRecord = await _ended(manager, await _next_run(runtime, first.thread_id, held))
        assert (queued.once, queued.effort, queued.goal) == ("ultrathink", "max", "ultrathink: then this.")
    finally:
        go.set()
