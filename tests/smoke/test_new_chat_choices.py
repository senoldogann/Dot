"""A new chat keeps its choices with its draft (item 397): the mode, the model with its reasoning level, a choice for
one message, the agent and Keep going picked in the home's box, with the words typed there, are in the box again when
the owner comes back to a new chat from another page, and a new chat after a message went starts afresh. A real server
whose provider is scripted; the one run that goes finishes at its first step."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "new-chat-choices-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
DEEP: str = f"{_PROVIDER}:deep"
# Where the home keeps the new chat's own choices (views/runs.js).
_CHOICES_KEY: str = "dot.home.choices"
_RUN_WAIT_S: float = 30.0
GOAL: str = "textarea[aria-label='Goal']"
WORDS: str = "Compare the two offers"


class TwoModels:
    """The scripted model, and one under its provider's name whose reasoning levels go up to max, so that it offers the
    choices for one message too."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        if model == "deep":
            return ModelInfo(
                id=model,
                vision=False,
                tools=True,
                thinking=True,
                context_length=32_768,
                own_context_length=None,
                window_source="assumed",
                efforts=("low", "medium", "high", "max"),
                free=False,
            )
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return (await self.model_info("scripted"), await self.model_info("deep"))

    async def aclose(self) -> None:
        await self._script.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the two models."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    finish: ScriptedCall = ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True})
    await built.router.register(
        _PROVIDER, TwoModels(ScriptedProvider(_PROVIDER, [ScriptedTurn(text="", calls=(finish,))]))
    )
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


def _pill(page: Page, name: str) -> Locator:
    return page.locator(f".composer-pill[data-pill='{name}']")


def _menu(page: Page, label: str) -> Locator:
    return page.locator(f".composer .composer-popover[aria-label='{label}']")


async def _pick(page: Page, pill: str, menu: str, option: str) -> None:
    await _pill(page, pill).click()
    await _menu(page, menu).get_by_role("option", name=option).first.click()
    await expect(_menu(page, menu)).to_be_hidden()


async def _keep_going(page: Page) -> Locator:
    """Keep going's switch, in the Options menu opened (its Execution group)."""
    await _pill(page, "options").click()
    return _menu(page, "Options").get_by_role("switch", name="Keep going until done")


async def _away_and_back(page: Page, address: str) -> None:
    """To another page, and back to a new chat by the sidebar's New chat."""
    await page.goto(f"{address}/#/agents")
    await expect(page.locator(GOAL)).to_have_count(0)
    await page.locator(".side-btn", has_text="New chat").click()
    await expect(page.locator(GOAL)).to_be_visible()


async def _run_of(runtime: Runtime, run_id: str) -> RunRecord:
    """The run once it has ended."""
    deadline: float = time.monotonic() + _RUN_WAIT_S
    while time.monotonic() < deadline:
        run: RunRecord = await runtime.store.get_run(run_id)
        if run.finished_at is not None:
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"the run {run_id} did not end in {_RUN_WAIT_S} s")


async def test_a_new_chat_keeps_its_choices_until_its_message_goes(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    async with httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        made: httpx.Response = await http.post(
            "/api/agents",
            json={
                "name": "Scout",
                "role": "Finds and checks sources",
                "instructions": "Prefer primary sources.",
                "model": "",
                "effort": None,
                "approvals": "ask",
                "tools": None,
            },
        )
        assert made.status_code == 201, made.text
    scout: str = made.json()["id"]
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            context: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 860}, reduced_motion="reduce"
            )
            page: Page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
            await expect(_pill(page, "agent")).to_be_visible()

            # A task that keeps going, on the model with levels at High: all there again after a walk elsewhere.
            await _pick(page, "model", "Model", "deep")
            await _pick(page, "reasoning", "Reasoning", "High")
            keep_going: Locator = await _keep_going(page)
            await keep_going.click()
            await expect(keep_going).to_have_attribute("aria-checked", "true")
            await page.keyboard.press("Escape")
            await _away_and_back(page, served)
            await expect(_pill(page, "mode")).to_contain_text("Task")
            await expect(_pill(page, "model")).to_contain_text("deep")
            await expect(_pill(page, "reasoning")).to_contain_text("High")
            await expect(await _keep_going(page)).to_have_attribute("aria-checked", "true")
            await expect(page.locator(".composer-stack > .composer-note")).to_be_visible()
            await page.keyboard.press("Escape")

            # Research for an agent, Ultrathink for this message, and words typed: there again too.
            await _pick(page, "mode", "Mode", "Research")
            await _pick(page, "agent", "Agent", "Scout")
            await _pick(page, "reasoning", "Reasoning", "Ultrathink")
            await page.locator(GOAL).fill(WORDS)
            await _away_and_back(page, served)
            await expect(page.locator(GOAL)).to_have_value(WORDS)
            await expect(_pill(page, "mode")).to_contain_text("Research")
            await expect(_pill(page, "agent")).to_contain_text("Scout")
            await expect(_pill(page, "model")).to_contain_text("deep")
            await expect(_pill(page, "reasoning")).to_contain_text("Ultrathink")
            await _pill(page, "reasoning").click()
            chosen: Locator = _menu(page, "Reasoning").locator("[role=option][aria-selected=true]")
            await expect(chosen).to_have_count(2)
            await expect(chosen.first).to_contain_text("High")
            await expect(chosen.last).to_contain_text("Ultrathink")
            await page.keyboard.press("Escape")

            # When the agents cannot be listed, a choice made then keeps the agent the new chat had.
            await page.goto(f"{served}/#/agents")
            await expect(page.locator(GOAL)).to_have_count(0)
            await page.route("**/api/agents", lambda route: route.fulfill(status=503, body="not now"))
            await page.locator(".side-btn", has_text="New chat").click()
            await expect(page.locator(GOAL)).to_have_value(WORDS)
            await _pick(page, "mode", "Mode", "Research")
            await page.unroute("**/api/agents")
            await _away_and_back(page, served)
            await expect(_pill(page, "agent")).to_contain_text("Scout")
            await page.screenshot(path=str(tmp_path / "kept.png"), animations="disabled")

            # The message goes with them, and the next new chat starts afresh.
            await page.locator(GOAL).press("Enter")
            await page.wait_for_url(re.compile(r"#/runs/[^/]+$"))
            run_id: str = page.url.rpartition("/")[2]
            await page.locator(".side-btn", has_text="New chat").click()
            await expect(page.locator(GOAL)).to_have_value("")
            await expect(_pill(page, "mode")).to_contain_text("Task")
            await expect(_pill(page, "model")).to_contain_text("scripted")
            await expect(_pill(page, "agent")).to_contain_text("Dot")
            await expect(_pill(page, "reasoning")).to_be_hidden()
            await expect(await _keep_going(page)).to_have_attribute("aria-checked", "false")
            stored: str | None = await page.evaluate(f"() => localStorage.getItem('{_CHOICES_KEY}')")
            assert errors == []
        finally:
            await browser.close()
    run: RunRecord = await _run_of(runtime, run_id)
    assert (run.goal, run.mode, run.model, run.agent_id, run.once) == (WORDS, "research", DEEP, scout, "ultrathink")
    assert stored is None
