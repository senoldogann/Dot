"""The default model is chosen in Settings, and a chat keeps its choices (item 199). The Models card makes a model the
default: the runs that name none start on it at once, the home's composer shows it and the config file keeps it, while a
model that cannot call tools is refused and nothing changes. A chat keeps what its composer chose (the mode, the model
with its reasoning, the approvals): a reload and another browser show them, and the follow-up starts with them. A run
moved to a standby model leaves the chat's model as it was. A real server on scripted models, wide and on a phone, light
and dark."""

import asyncio
import time
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.core.types import JsonObject
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
from dotagent.store.models import ChatChoices, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "chat-choices-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
THINKER: str = f"{_PROVIDER}:thinker"
OTHER: str = f"{_PROVIDER}:other"
TALKER: str = f"{_PROVIDER}:talker"
STANDBY: str = f"{_PROVIDER}:standby"
# Each run finishes at its first step: one on the new default, a chat and its follow-up, and the run of the standby.
_RUNS: int = 4
_RUN_WAIT_S: float = 30.0
_CONTEXT_LENGTH: int = 32768
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _finish() -> ScriptedTurn:
    return ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )


class Models:
    """The scripted model and three more under its provider's name: one that offers three reasoning levels, one without
    levels, and one that cannot call tools."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        if model in ("thinker", "talker"):
            return ModelInfo(
                id=model,
                vision=False,
                tools=model == "thinker",
                thinking=model == "thinker",
                context_length=_CONTEXT_LENGTH,
                own_context_length=None,
                window_source="assumed",
                efforts=("low", "medium", "high") if model == "thinker" else (),
                free=False,
            )
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return tuple([await self.model_info(model) for model in ("scripted", "thinker", "other", "talker")])

    async def aclose(self) -> None:
        await self._script.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted models, whose config file the Settings change."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, Models(ScriptedProvider(_PROVIDER, [_finish() for _ in range(_RUNS)])))
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


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> tuple[Page, list[str]]:
    """A page signed in, in a browser of its own, with the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
    )
    await context.add_init_script(
        "localStorage.setItem('dot.composer.choices', 'shown')"
    )  # the choices this test reads (item 833)
    page: Page = await context.new_page()
    await page.add_init_script(
        "localStorage.setItem('dot.composer.choices', 'shown')"
    )  # the choices this test reads (item 833)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    return page, errors


def _pill(page: Page, name: str) -> Locator:
    return page.locator(f".dock .composer-pill[data-pill='{name}']")


def _menu(page: Page, label: str) -> Locator:
    return page.locator(f".dock .composer-popover[aria-label='{label}']")


async def _shows(page: Page, mode: str, model: str, level: str, approvals: str) -> None:
    """The dock's pills show these choices; on Auto (item 495) the mode has no pill."""
    if mode == "Auto":
        await expect(_pill(page, "mode")).to_be_hidden()
    else:
        await expect(_pill(page, "mode")).to_contain_text(mode)
    await expect(_pill(page, "model")).to_contain_text(model)
    await expect(_pill(page, "reasoning")).to_contain_text(level)
    await expect(_pill(page, "options")).to_contain_text(approvals)


def _written_models() -> JsonObject:
    models: object = tomllib.loads(config_path().read_text(encoding="utf-8")).get("models")
    assert isinstance(models, dict), models
    return models


async def _kept(runtime: Runtime, thread_id: str, wanted: ChatChoices) -> None:
    """Wait until the chat keeps ``wanted``: the page sends each choice once the one before it was answered."""
    deadline: float = time.monotonic() + _RUN_WAIT_S
    kept: ChatChoices | None = None
    while time.monotonic() < deadline:
        kept = await runtime.store.chat_choices(thread_id)
        if kept == wanted:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"the chat kept {kept}, not {wanted}")


async def _follow_up_of(runtime: Runtime, run: RunRecord) -> RunRecord:
    """The run that followed ``run`` on its thread, once the daemon has it."""
    deadline: float = time.monotonic() + _RUN_WAIT_S
    while time.monotonic() < deadline:
        newest: RunRecord = (await runtime.store.list_runs(1, run.thread_id))[0]
        if newest.id != run.id:
            return newest
        await asyncio.sleep(0.1)
    raise AssertionError(f"no run followed {run.id} in {_RUN_WAIT_S} s")


async def test_the_default_model_chosen_in_settings_starts_the_runs_that_name_none(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            # The Models card offers the models that can call tools, the default one chosen, wide and on a phone.
            for width, scheme in _SHOTS:
                shown, shown_errors = await _page(browser, address, width, scheme)
                await shown.goto(f"{address}/#/settings/models")
                offered: Locator = shown.locator(".models-default select")
                await expect(offered).to_have_value(SCRIPTED_MODEL)
                values: list[str] = await offered.locator("option").evaluate_all(
                    "(options) => options.map((option) => option.value)"
                )
                assert values == [SCRIPTED_MODEL, THINKER, OTHER], values
                await shown.screenshot(
                    path=str(tmp_path / f"default-model-{width}-{scheme}.png"), animations="disabled"
                )
                assert shown_errors == []
                await shown.context.close()

            page, errors = await _page(browser, address, 1280, "light")
            await page.goto(f"{address}/#/settings/models")
            select: Locator = page.locator(".models-default select")
            # It shows once the models are listed.
            await expect(select).to_have_value(SCRIPTED_MODEL)
            await select.focus()
            await select.select_option(OTHER)
            await expect(page.locator(".toast.ok")).to_contain_text("New chats start on other")
            await expect(page.locator("tr", has=page.locator(".badge", has_text="default"))).to_contain_text(OTHER)
            await expect(select).to_have_value(OTHER)
            await expect(select).to_be_focused()
            assert manager.main_model() == OTHER
            assert _written_models()["main"] == OTHER
            assert tomllib.loads(config_path().read_text(encoding="utf-8"))["browser"] == {"headless": True}

            # The home's composer shows it, and a run that names no model starts on it.
            await page.goto(f"{address}/#/runs")
            await expect(page.locator(".composer-pill[data-pill='model']")).to_contain_text("other")
            started: RunRecord = await manager.start(
                "Sort the photos.", "task", None, None, "test", None, None, "standard", None, "ask", False, ()
            )
            assert started.model == OTHER
            async with asyncio.timeout(_RUN_WAIT_S):
                await manager.wait(started.id)

            # A model that cannot call tools is refused, and so is one of no provider: nothing changes.
            async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
                refused: httpx.Response = await http.post("/api/models/main", json={"model": TALKER})
                assert refused.status_code == 422, refused.text
                assert "can use actions" in refused.text
                unknown: httpx.Response = await http.post("/api/models/main", json={"model": "nowhere:x"})
                assert unknown.status_code == 422, unknown.text
                status: httpx.Response = await http.get("/api/status")
                assert status.json()["models"]["main"] == OTHER
            assert manager.main_model() == OTHER
            assert _written_models()["main"] == OTHER
            assert errors == []
        finally:
            await browser.close()


async def test_a_chat_keeps_what_its_composer_chose_after_a_reload_and_in_another_browser(
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
            page, errors = await _page(browser, address, 1280, "light")
            await page.goto(f"{address}/#/runs/{first.id}")
            await expect(_pill(page, "model")).to_contain_text("scripted")

            # Research, named with its command (the dock shows no mode pill on Auto, item 495), the model with
            # levels at High, and Auto approvals.
            await page.locator(".dock textarea").type("/research")
            await page.locator(".dock textarea").press("Tab")
            await _pill(page, "model").click()
            await _menu(page, "Model").get_by_role("option").filter(has_text="thinker").click()
            await _pill(page, "reasoning").click()
            await _menu(page, "Reasoning").get_by_role("option", name="High").click()
            await _pill(page, "options").click()
            await _menu(page, "Options").locator(".pop-option[data-mode='auto']").click()
            await page.keyboard.press("Escape")
            await _shows(page, "Research", "thinker", "High", "Auto")
            await _kept(
                runtime,
                first.thread_id,
                ChatChoices(
                    mode="research", model=THINKER, effort="high", speed="standard", approvals="auto", goal_mode=False
                ),
            )

            # A reload shows them, and so does another browser.
            await page.reload()
            # The curtain of a page opened on a chat (item 328) lifts once the chat shows.
            await expect(page.locator(".waiting")).to_have_count(0)
            await _shows(page, "Research", "thinker", "High", "Auto")
            await page.screenshot(path=str(tmp_path / "chat-choices-1280-light.png"), animations="disabled")
            other, other_errors = await _page(browser, address, 390, "dark")
            await other.goto(f"{address}/#/runs/{first.id}")
            await _shows(other, "Research", "thinker", "High", "Auto")
            await other.screenshot(path=str(tmp_path / "chat-choices-390-dark.png"), animations="disabled")

            # The follow-up starts with them.
            box: Locator = page.locator(".dock textarea")
            await box.fill("Name the folders.")
            await box.press("Enter")
            follow_up: RunRecord = await _follow_up_of(runtime, first)
            async with asyncio.timeout(_RUN_WAIT_S):
                second: RunRecord = await manager.wait(follow_up.id)
            assert (second.mode, second.model, second.effort, second.approvals) == (
                "research",
                THINKER,
                "high",
                "auto",
            ), second
            assert errors == []
            assert other_errors == []
        finally:
            await browser.close()


async def test_a_run_moved_to_a_standby_model_leaves_the_chats_model(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    started: RunRecord = await manager.start(
        "Count the files.", "task", THINKER, None, "test", None, "low", "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        first: RunRecord = await manager.wait(started.id)
    # A chat keeps what its first run started with ...
    assert await runtime.store.chat_choices(first.thread_id) == ChatChoices(
        mode="task", model=THINKER, effort="low", speed="standard", approvals="ask", goal_mode=False
    )
    # ... when the daemon moves the run to a standby model, as agent.loop._to_standby does.
    await runtime.store.set_run_model(first.id, STANDBY, None)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in (_SHOTS[1], _SHOTS[2]):
                page, errors = await _page(browser, address, width, scheme)
                await page.goto(f"{address}/#/runs/{first.id}")
                await _shows(page, "Auto", "thinker", "Low", "Edits")
                await page.screenshot(path=str(tmp_path / f"standby-{width}-{scheme}.png"), animations="disabled")
                assert errors == []
                await page.context.close()
        finally:
            await browser.close()
