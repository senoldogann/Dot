"""The box of an agent's page (item 426): it starts from the agent's own bounds, its model, its reasoning level and its
approvals, whatever the home's box has kept in this browser; a choice made there goes with that message and leaves
the home's choices as they were. A real server whose provider is scripted; each run that goes finishes at its first
step."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from composer_choices import show_choices
from playwright.async_api import Browser, BrowserContext, FloatRect, Locator, Page, async_playwright, expect

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

TOKEN: str = "agent-page-choices-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
DEEP: str = f"{_PROVIDER}:deep"
_RUN_WAIT_S: float = 30.0
BOX: str = "textarea[aria-label='Message']"


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
        _PROVIDER, TwoModels(ScriptedProvider(_PROVIDER, [ScriptedTurn(text="", calls=(finish,))] * 3))
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
    return page.locator(f".composer-popover[aria-label='{label}']")


async def _run_of(runtime: Runtime, run_id: str) -> RunRecord:
    """The run once it has ended."""
    deadline: float = time.monotonic() + _RUN_WAIT_S
    while time.monotonic() < deadline:
        run: RunRecord = await runtime.store.get_run(run_id)
        if run.finished_at is not None:
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"the run {run_id} did not end in {_RUN_WAIT_S} s")


async def _sent(page: Page, words: str) -> str:
    """Send ``words`` from the agent's box, and the id of the run that begins."""
    await page.locator(BOX).fill(words)
    await page.locator(BOX).press("Enter")
    await page.wait_for_url(re.compile(r"#/runs/[^/]+$"))
    return page.url.rpartition("/")[2]


async def test_the_agents_box_starts_from_the_agents_bounds_and_a_choice_there_stays_there(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    async with httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        made: httpx.Response = await http.post(
            "/api/agents",
            json={
                "name": "Scout",
                "role": "Finds and checks sources",
                "instructions": "Prefer primary sources.",
                "model": DEEP,
                "effort": "low",
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
            await show_choices(context)
            # This browser has chosen the loosest mode and the top reasoning level on the home's box before.
            await context.add_init_script(
                f"localStorage.setItem('dot.approvals', 'yolo'); localStorage.setItem('dot.effort.{DEEP}', 'max')"
            )
            page: Page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
            await page.goto(f"{served}/#/agents/{scout}")
            await expect(page.locator(BOX)).to_be_visible()
            # The pills say the agent's own: the model, the level, and the approvals that bound its runs.
            await expect(_pill(page, "model")).to_contain_text("deep")
            await expect(_pill(page, "reasoning")).to_contain_text("Low")
            await expect(_pill(page, "options")).to_contain_text("Edits")
            await page.screenshot(path=str(tmp_path / "agent-box.png"), animations="disabled")
            first: str = await _sent(page, "Find two sources on the matter")
            # A choice made in its box goes with that message, and the home's choices stay as they were.
            await page.goto(f"{served}/#/agents/{scout}")
            await expect(page.locator(BOX)).to_be_visible()
            await _pill(page, "options").click()
            await _menu(page, "Options").locator(".pop-option[data-mode='supervised']").click()
            await page.keyboard.press("Escape")
            await _pill(page, "reasoning").click()
            await _menu(page, "Reasoning").get_by_role("option", name="High").click()
            await page.keyboard.press("Escape")
            second: str = await _sent(page, "Check them against the register")
            kept: dict[str, str | None] = await page.evaluate(
                "(level) => ({ approvals: localStorage.getItem('dot.approvals'), level: localStorage.getItem(level) })",
                f"dot.effort.{DEEP}",
            )
            assert errors == []
            # On a phone the box fits: the pickers and the send button stay in the window, and the page does not
            # scroll sideways.
            phone: Page = await browser.new_page(
                viewport={"width": 390, "height": 800}, color_scheme="dark", reduced_motion="reduce"
            )
            await show_choices(phone)
            await phone.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
            await phone.goto(f"{served}/#/agents/{scout}")
            await expect(phone.locator(BOX)).to_be_visible()
            for pill in ("model", "reasoning", "options"):
                await expect(_pill(phone, pill)).to_be_visible()
            send: FloatRect | None = await phone.locator(".composer .btn.send").bounding_box()
            assert send is not None and send["x"] >= 0 and send["x"] + send["width"] <= 390, send
            assert await phone.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
            await phone.screenshot(path=str(tmp_path / "agent-box-390.png"), animations="disabled")
            await phone.close()
        finally:
            await browser.close()
    first_run: RunRecord = await _run_of(runtime, first)
    second_run: RunRecord = await _run_of(runtime, second)
    # The first went as the agent bounds it, though this browser had chosen yolo and max for the home.
    assert (first_run.agent_id, first_run.model, first_run.effort, first_run.approvals) == (scout, DEEP, "low", "ask")
    # The second went as chosen in the agent's box.
    assert (second_run.agent_id, second_run.model, second_run.effort, second_run.approvals) == (
        scout,
        DEEP,
        "high",
        "supervised",
    )
    assert kept == {"approvals": "yolo", "level": "max"}
