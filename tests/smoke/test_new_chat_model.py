"""A message from the home's box starts a chat of its own on the model picked there (item 1350): an old chat left over
from another day is not continued, the run records the model that was picked, and a message for a model that cannot
answer (its provider has no key) is refused in words that name it, keeps its words in the box and goes on the default
model with one press. The same on a phone, and on the page of a chat the owner opened. A real server whose provider is
scripted; no model is called."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from chat_settings import pick_model
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.toolcalls_play import no_keychain
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

TOKEN: str = "new-chat-model-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
DEEP: str = f"{_PROVIDER}:deep"
_RUN_WAIT_S: float = 30.0
# The runs the scripted model answers: each test starts a few.
_RUNS: int = 400
OLD_GOAL: str = "Old project: fix the invoice template"
# Where the home keeps the new chat's own choices (views/runs.js).
_CHOICES_KEY: str = "dot.home.choices"
KEYLESS: str = "openai:gpt-5.5"
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
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    # No OpenAI key anywhere: the model of a stale choice cannot answer.
    patch.delenv("OPENAI_API_KEY", raising=False)
    keychain = no_keychain()
    keychain.__enter__()
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    finish: ScriptedCall = ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True})
    await built.router.register(
        _PROVIDER, TwoModels(ScriptedProvider(_PROVIDER, [ScriptedTurn(text="", calls=(finish,))] * _RUNS))
    )
    try:
        yield built
    finally:
        await close_runtime(built)
        keychain.__exit__(None, None, None)
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


async def _away_and_back(page: Page, address: str) -> None:
    """To another page, and back to the home."""
    await page.goto(f"{address}/#/agents")
    await expect(page.locator(GOAL)).to_have_count(0)
    await page.goto(f"{address}/#/runs")
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


async def _old_chat(runtime: Runtime, served: str) -> RunRecord:
    """A chat left over from another day, on the scripted model, whose run has ended."""
    async with httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        made: httpx.Response = await http.post(
            "/api/runs",
            json={"goal": OLD_GOAL, "mode": "task", "model": SCRIPTED_MODEL, "goal_mode": False, "source": "ui"},
        )
    assert made.status_code == 201, made.text
    return await _run_of(runtime, made.json()["id"])


async def _choose(page: Page, name: str) -> None:
    """Pick the model `name` in the box: its pill opens the chat's settings on every width (item 2015)."""
    await page.locator(GOAL).click()
    await pick_model(page, page, name)
    await expect(_pill(page, "model")).to_contain_text(name)


async def _runs(runtime: Runtime) -> list[RunRecord]:
    return await runtime.store.list_runs(50, None)


async def _open(playwright, runtime: Runtime, served: str, phone: bool) -> tuple[Browser, Page]:
    browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
    context: BrowserContext = await browser.new_context(
        viewport={"width": 390, "height": 844} if phone else {"width": 1280, "height": 860},
        has_touch=phone,
        is_mobile=phone,
        reduced_motion="reduce",
    )
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", int(served.rpartition(":")[2]), TOKEN))
    await expect(page.locator(GOAL)).to_be_visible()
    return browser, page


@pytest.mark.parametrize("phone", [False, True], ids=["desktop", "phone"])
async def test_a_message_from_the_home_starts_a_new_chat_on_the_model_picked_and_leaves_the_old_chat_alone(
    runtime: Runtime, served: str, phone: bool
) -> None:
    old: RunRecord = await _old_chat(runtime, served)
    before: int = len(await _runs(runtime))
    async with async_playwright() as playwright:
        browser, page = await _open(playwright, runtime, served, phone)
        try:
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await _choose(page, "deep")
            await page.locator(GOAL).fill(WORDS)
            await page.get_by_role("button", name="Start").click()
            await page.wait_for_url(re.compile(r"#/runs/[^/]+$"))
            run_id: str = page.url.rpartition("/")[2]
            # The first send opens the one chat page (item 2025): the message in it and the chat's own composer, as
            # any chat shows them, not a page of its own for a chat that starts.
            await expect(page.get_by_text(WORDS).first).to_be_visible()
            await expect(page.locator(".dock .composer")).to_be_visible()
            assert errors == []
        finally:
            await browser.close()
    run: RunRecord = await _run_of(runtime, run_id)
    assert run.id != old.id
    assert run.thread_id != old.thread_id, "the message went on in the old chat"
    assert (run.goal, run.model, run.status) == (WORDS, DEEP, "done")
    assert len(await _runs(runtime)) == before + 1
    assert [one.id for one in await runtime.store.list_runs(10, old.thread_id)] == [old.id]


async def test_a_chat_the_owner_opened_is_gone_on_in_with_the_model_picked_there(runtime: Runtime, served: str) -> None:
    old: RunRecord = await _old_chat(runtime, served)
    async with async_playwright() as playwright:
        browser, page = await _open(playwright, runtime, served, False)
        try:
            await page.goto(f"{served}/#/runs/{old.id}")
            box: Locator = page.get_by_role("textbox", name="Message for Dot")
            await expect(box).to_be_visible()
            await pick_model(page, page, "deep")
            await box.fill(WORDS)
            await page.get_by_role("button", name="Follow up").click()
            await page.wait_for_url(re.compile(r"#/runs/(?!" + old.id + r")[^/]+$"))
            run_id: str = page.url.rpartition("/")[2]
        finally:
            await browser.close()
    run: RunRecord = await _run_of(runtime, run_id)
    assert run.thread_id == old.thread_id
    assert (run.goal, run.model) == (WORDS, DEEP)


@pytest.mark.parametrize("phone", [False, True], ids=["desktop", "phone"])
async def test_a_message_for_a_model_that_cannot_answer_is_refused_by_name_and_goes_on_the_default_with_one_press(
    runtime: Runtime, served: str, phone: bool
) -> None:
    before: int = len(await _runs(runtime))
    async with async_playwright() as playwright:
        browser, page = await _open(playwright, runtime, served, phone)
        try:
            # A model kept from another day, whose provider has lost its key.
            kept: str = json.dumps({"mode": "auto", "model": KEYLESS, "agent": "", "standing": False, "once": None})
            await page.evaluate("([key, value]) => localStorage.setItem(key, value)", [_CHOICES_KEY, kept])
            await page.reload()
            await expect(page.locator(GOAL)).to_be_visible()
            await page.locator(GOAL).fill(WORDS)
            await page.get_by_role("button", name="Start").click()
            toast: Locator = page.locator(".toast.error")
            await expect(toast).to_contain_text(f"{KEYLESS} cannot answer this message")
            await expect(toast).to_contain_text("no openai key is set")
            await expect(toast.get_by_role("button", name="Send on scripted")).to_be_visible()
            # Nothing started and the words are still in the box.
            await expect(page.locator(GOAL)).to_have_value(WORDS)
            assert len(await _runs(runtime)) == before
            await toast.get_by_role("button", name="Send on scripted").click()
            await page.wait_for_url(re.compile(r"#/runs/[^/]+$"))
            run_id: str = page.url.rpartition("/")[2]
        finally:
            await browser.close()
    run: RunRecord = await _run_of(runtime, run_id)
    assert (run.goal, run.model, run.status) == (WORDS, SCRIPTED_MODEL, "done")
    assert len(await _runs(runtime)) == before + 1
