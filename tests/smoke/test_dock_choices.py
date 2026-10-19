"""The composer's choices on a chat's page (item 133): the dock wears the mode, the model with its reasoning and the
Options, set at first to the open run's; a follow-up starts its run with what was chosen there, and the new run's page
starts from that, while the home's box keeps its own. While a run works, a message steers it, and the pills say that
they wait for the next follow-up."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

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

TOKEN: str = "dock-choices-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
THINKER: str = f"{_PROVIDER}:thinker"
HELD: str = f"{_PROVIDER}:held"
# Five runs, each finished at its first step: a run and its follow-up, one that works while the page is read, and a
# run on the model with levels and its follow-up on one without.
_RUNS: int = 5
_RUN_WAIT_S: float = 30.0
_CONTEXT_LENGTH: int = 32768
# Where the home keeps its choices in the browser (runmodes.js, efforts.js).
_HOME_KEYS: tuple[str, ...] = ("dot.approvals", "dot.goalMode")
_LEVEL_KEY_PREFIX: str = "dot.effort."


def _finish() -> ScriptedTurn:
    return ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )


class ThreeModels:
    """The scripted model and two more under its provider's name: one that offers three reasoning levels, and one whose
    replies wait until the test lets them go, so that a run works while its page is read."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.release: asyncio.Event = asyncio.Event()

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if request.model == "held":
            await self.release.wait()
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        if model == "thinker":
            return ModelInfo(
                id=model,
                vision=False,
                tools=True,
                thinking=True,
                context_length=_CONTEXT_LENGTH,
                own_context_length=None,
                window_source="assumed",
                efforts=("low", "medium", "high"),
                free=False,
            )
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return tuple([await self.model_info(model) for model in ("scripted", "thinker", "held")])

    async def aclose(self) -> None:
        await self._script.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def models() -> ThreeModels:
    return ThreeModels(ScriptedProvider(_PROVIDER, [_finish() for _ in range(_RUNS)]))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, models: ThreeModels) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the three models, so that the chats are only the ones made here."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, models)
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


async def _page(browser: Browser, address: str) -> tuple[Page, list[str]]:
    """A page signed in, with the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": 1280, "height": 800}, color_scheme="light", reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    return page, errors


def _pill(page: Page, name: str) -> Locator:
    return page.locator(f".dock .composer-pill[data-pill='{name}']")


def _menu(page: Page, label: str) -> Locator:
    return page.locator(f".dock .composer-popover[aria-label='{label}']")


async def _follow_up_of(runtime: Runtime, run: RunRecord) -> RunRecord:
    """The run that followed ``run`` on its thread, once the daemon has it."""
    deadline: float = time.monotonic() + _RUN_WAIT_S
    while time.monotonic() < deadline:
        newest: RunRecord = (await runtime.store.list_runs(1, run.thread_id))[0]
        if newest.id != run.id:
            return newest
        await asyncio.sleep(0.1)
    raise AssertionError(f"no run followed {run.id} in {_RUN_WAIT_S} s")


async def test_a_follow_up_starts_its_run_with_the_choices_made_on_the_chats_page(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    started: RunRecord = await manager.start(
        "Sort the photos.", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        first: RunRecord = await manager.wait(started.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, address)
            await page.goto(f"{address}/#/runs/{first.id}")

            # The dock starts from the open run: its mode, its model (no reasoning to choose) and its approvals, and
            # nothing waits, since the run is over.
            await expect(_pill(page, "mode")).to_contain_text("Task")
            await expect(_pill(page, "model")).to_contain_text("scripted")
            await expect(_pill(page, "reasoning")).to_be_hidden()
            await expect(_pill(page, "options")).to_contain_text("Edits")
            await expect(_pill(page, "agent")).to_have_count(0)
            await expect(_pill(page, "model")).not_to_have_attribute("data-waiting", re.compile(".*"))

            # Another model, a level of its reasoning and other approvals, for the follow-up; Keep going is a new
            # chat's.
            await _pill(page, "model").click()
            await _menu(page, "Model").get_by_role("option").filter(has_text="thinker").click()
            await expect(_pill(page, "model")).to_contain_text("thinker")
            await expect(_pill(page, "reasoning")).to_contain_text("Medium")
            await _pill(page, "reasoning").click()
            await _menu(page, "Reasoning").get_by_role("option", name="High").click()
            await expect(_pill(page, "reasoning")).to_contain_text("High")
            # Goal mode is a switch of the "+" menu (item 318), Keep going a new chat's.
            plus: Locator = _menu(page, "Add")
            await page.locator(".dock").get_by_role("button", name="Add", exact=True).click()
            await expect(plus.get_by_role("switch", name="Goal mode")).to_be_visible()
            await expect(plus.get_by_role("switch", name="Plan first")).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(plus).to_be_hidden()
            await _pill(page, "options").click()
            options: Locator = _menu(page, "Options")
            await expect(options.get_by_role("switch", name="Keep going until done")).to_have_count(0)
            await options.locator(".pop-option[data-mode='auto']").click()
            await expect(_pill(page, "options")).to_contain_text("Auto")
            await page.keyboard.press("Escape")
            await expect(options).to_be_hidden()

            # A chat has no goal mode and no Plan first: chosen for one, the "+" holds no switch for them, and Task
            # brings them back.
            await _pill(page, "mode").click()
            await _menu(page, "Mode").get_by_role("option").filter(has_text="Chat").click()
            await expect(_pill(page, "mode")).to_contain_text("Chat")
            await page.locator(".dock").get_by_role("button", name="Add", exact=True).click()
            await expect(plus.get_by_role("switch", name="Goal mode")).to_be_hidden()
            await expect(plus.get_by_role("switch", name="Plan first")).to_be_hidden()
            await page.keyboard.press("Escape")
            await expect(plus).to_be_hidden()
            await _pill(page, "mode").click()
            await _menu(page, "Mode").get_by_role("option").filter(has_text="Task").click()
            await expect(_pill(page, "mode")).to_contain_text("Task")
            await page.screenshot(path=str(tmp_path / "dock-choices-1280-light.png"), animations="disabled")

            # The follow-up's run has them, on the same thread, and its page starts from them.
            box: Locator = page.locator(".dock textarea")
            await box.fill("Name the folders.")
            await box.press("Enter")
            follow_up: RunRecord = await _follow_up_of(runtime, first)
            async with asyncio.timeout(_RUN_WAIT_S):
                second: RunRecord = await manager.wait(follow_up.id)
            assert (second.mode, second.model, second.effort, second.approvals, second.goal_mode) == (
                "task",
                THINKER,
                "high",
                "auto",
                False,
            ), second
            assert second.thread_id == first.thread_id
            await page.wait_for_function(f"() => location.hash === '#/runs/{second.id}'")
            await expect(_pill(page, "model")).to_contain_text("thinker")
            await expect(_pill(page, "reasoning")).to_contain_text("High")
            await expect(_pill(page, "options")).to_contain_text("Auto")

            # They were the page's: this browser keeps none of them, and the home's box shows the daemon's own.
            stored: list[str] = await page.evaluate("() => Object.keys(localStorage)")
            assert [key for key in stored if key in _HOME_KEYS or key.startswith(_LEVEL_KEY_PREFIX)] == [], stored
            await page.goto(f"{address}/#/runs")
            await expect(page.locator(".composer-pill[data-pill='model']")).to_contain_text("scripted")
            await expect(page.locator(".composer-pill[data-pill='reasoning']")).to_be_hidden()
            await expect(page.locator(".composer-pill[data-pill='options']")).to_contain_text("Edits")
            assert errors == []
        finally:
            await browser.close()


async def test_while_a_run_works_the_pills_wait_for_the_next_follow_up(
    runtime: Runtime, served: tuple[RunManager, str], models: ThreeModels, tmp_path: Path
) -> None:
    manager, address = served
    held: RunRecord = await manager.start(
        "Count the files.", "task", HELD, None, "test", None, None, "standard", None, "ask", False, ()
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, address)
            await page.goto(f"{address}/#/runs/{held.id}")
            for name in ("mode", "model", "options"):
                await expect(_pill(page, name)).to_have_attribute("data-waiting", "")
                await expect(_pill(page, name)).to_have_attribute("title", re.compile("For the next follow-up"))
            await page.screenshot(path=str(tmp_path / "dock-waiting-1280-light.png"), animations="disabled")
            models.release.set()
            async with asyncio.timeout(_RUN_WAIT_S):
                ended: RunRecord = await manager.wait(held.id)
            assert ended.status == "done", ended
            await expect(_pill(page, "model")).not_to_have_attribute("data-waiting", re.compile(".*"))
            await expect(_pill(page, "model")).not_to_have_attribute("title", re.compile("For the next follow-up"))
            assert errors == []
        finally:
            await browser.close()


async def test_a_reasoning_level_is_not_carried_to_a_model_without_levels(
    runtime: Runtime, served: tuple[RunManager, str]
) -> None:
    manager, address = served
    started: RunRecord = await manager.start(
        "Tidy the notes.", "task", THINKER, None, "test", None, "high", "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        first: RunRecord = await manager.wait(started.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, address)
            await page.goto(f"{address}/#/runs/{first.id}")
            await expect(_pill(page, "reasoning")).to_contain_text("High")

            # The daemon refuses a level that the model does not offer: on a model with none to choose, the pill goes
            # and the follow-up leaves the level to the daemon.
            await _pill(page, "model").click()
            rows: Locator = _menu(page, "Model").get_by_role("option")
            await rows.filter(has_text="scripted").filter(has_not_text="Default").click()
            await expect(_pill(page, "model")).to_contain_text("scripted")
            await expect(_pill(page, "reasoning")).to_be_hidden()
            box: Locator = page.locator(".dock textarea")
            await box.fill("Sum them up.")
            await box.press("Enter")
            follow_up: RunRecord = await _follow_up_of(runtime, first)
            async with asyncio.timeout(_RUN_WAIT_S):
                second: RunRecord = await manager.wait(follow_up.id)
            assert second.status == "done", second
            assert (second.model, second.effort) == (SCRIPTED_MODEL, runtime.config.models.effort), second
            assert errors == []
        finally:
            await browser.close()
