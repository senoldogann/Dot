"""The face as the state, end to end (item 49): the face at the head of a run tells idle, waiting, working, done and
failed through a run's life, the tab's icon is the same face in the state of the whole of Dot, and the name given in
Settings › General heads the new chat and the run, in a headless Chrome at a wide and at a phone width, in light and
in dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

# The animation of the face's core by state (item 73): a slow breath at rest, a pulse at work, and none of either
# under reduced motion, where the transforms go and the fades stay.
_CORE_ANIMATION: str = "getComputedStyle(document.querySelector('.home-who .avatar .core')).animationName"
_DRAWER_TRANSITION: str = "getComputedStyle(document.querySelector('.sidebar')).transitionDuration"

_PROVIDER: str = "ollama"
TOKEN: str = "face-test-token"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 90.0
_WORK_S: int = 6
_NAME: str = "Nova"


class _Recorder:
    def __init__(self) -> None:
        self.script: ScriptedProvider | None = None

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        assert self.script is not None, "the test did not set a script"
        return await self.script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await ScriptedProvider(_PROVIDER, ()).model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await ScriptedProvider(_PROVIDER, ()).list_models()

    async def aclose(self) -> None:
        return None


MODEL: _Recorder = _Recorder()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nquestions = "wait"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, MODEL)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime, manager: RunManager) -> AsyncIterator[httpx.AsyncClient]:
    """A client of a real uvicorn server on a free port, so the browser can open the UI."""
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving


def _turn(tool: str, arguments: dict[str, str | bool]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


async def _pending_question(runtime: Runtime, run_id: str) -> ApprovalRecord:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        for record in await runtime.store.list_approvals("pending", 10):
            if record.run_id == run_id:
                return record
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} asked nothing in {_STEP_WAIT_S} s")


async def _favicon_state(page: Page) -> str:
    href: str = await page.evaluate("document.querySelector('link[rel=\"icon\"]').href")
    assert href.startswith("data:image/svg+xml,"), href
    for state in ("idle", "working", "waiting", "done", "failed"):
        if f"data-state%3D%22{state}%22" in href:
            return state
    raise AssertionError(f"the favicon names no state: {href[:80]}")


async def _favicon_becomes(page: Page, state: str) -> None:
    deadline: float = time.monotonic() + _STEP_WAIT_S
    seen: str = await _favicon_state(page)
    while seen != state and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        seen = await _favicon_state(page)
    assert seen == state, f"the favicon shows {seen}, not {state}"


async def test_the_face_tells_the_state_through_a_runs_life_and_wears_the_name(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{host}:{port}"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [
            _turn(ASK_USER_TOOL, {"question": "Which colour?"}),
            _turn("shell", {"command": f"sleep {_WORK_S}"}),
            _turn(FINISH_TOOL, {"answer": "Done.", "success": True}),
        ],
    )
    run: RunRecord = await manager.start(
        "Pick a colour", "task", SCRIPTED_MODEL, None, "test", None, None, "ask", False, ()
    )
    question: ApprovalRecord = await _pending_question(runtime, run.id)
    schemes: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))
    try:
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in schemes:
                    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"{base}/#/runs/{run.id}")
                    face = page.locator(".chat-head .avatar")
                    await expect(face).to_have_attribute("data-state", "waiting")
                    await page.screenshot(path=str(tmp_path / f"waiting-{width}-{scheme}.png"))
                    await page.close()

                page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{base}/#/settings/general")
                name = page.get_by_label("Name", exact=True)
                await name.fill(_NAME)
                await name.press("Enter")
                await page.goto(f"{base}/#/runs/{run.id}")
                face = page.locator(".chat-head .avatar")
                await expect(page.locator(".chat-who-name")).to_have_text(_NAME)
                await expect(face).to_have_attribute("data-state", "waiting")
                assert await _favicon_state(page) == "waiting"
                await manager.decide(question.id, True, "test", "blue")
                await expect(face).to_have_attribute("data-state", "working")
                await _favicon_becomes(page, "working")
                await page.screenshot(path=str(tmp_path / "working-1280-light.png"))
                await expect(face).to_have_attribute("data-state", "done", timeout=(_WORK_S + 15) * 1000)
                await page.screenshot(path=str(tmp_path / "done-1280-light.png"))
                await _favicon_becomes(page, "idle")
                await page.goto(f"{base}/#/")
                await expect(page.locator(".home-who .avatar")).to_have_attribute("data-state", "idle")
                await expect(page.locator(".home-name")).to_have_text(_NAME)
                await page.goto(f"{base}/#/runs/{run.id}")
                await expect(page.locator(".chat-head .avatar")).to_have_attribute("data-state", "idle")

                MODEL.script = ScriptedProvider(_PROVIDER, [])
                failing: RunRecord = await manager.start(
                    "Fail at once", "task", SCRIPTED_MODEL, None, "test", None, None, "ask", False, ()
                )
                async with asyncio.timeout(_RUN_WAIT_S):
                    failed: RunRecord = await manager.wait(failing.id)
                assert failed.status == "failed", failed
                await page.goto(f"{base}/#/runs/{failing.id}")
                await expect(page.locator(".chat-head .avatar")).to_have_attribute("data-state", "failed")
                await page.screenshot(path=str(tmp_path / "failed-1280-light.png"))
                await page.close()
            finally:
                await browser.close()
    finally:
        if (await runtime.store.get_approval(question.id)).status == "pending":
            await manager.decide(question.id, True, "test", "blue")
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended


async def test_the_face_breathes_at_rest_pulses_at_work_and_holds_still_under_reduced_motion(
    runtime: Runtime, manager: RunManager, client: httpx.AsyncClient
) -> None:
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{host}:{port}"
    MODEL.script = ScriptedProvider(
        _PROVIDER,
        [_turn("shell", {"command": f"sleep {_WORK_S}"}), _turn(FINISH_TOOL, {"answer": "Rested.", "success": True})],
    )
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page: Page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{base}/#/runs")
            face = page.locator(".home-who .avatar")
            await expect(face).to_have_attribute("data-state", "idle")
            assert await page.evaluate(_CORE_ANIMATION) == "breathe"
            run: RunRecord = await manager.start(
                "Rest a while", "task", SCRIPTED_MODEL, None, "test", None, None, "yolo", False, ()
            )
            await expect(face).to_have_attribute("data-state", "working")
            assert await page.evaluate(_CORE_ANIMATION) == "pulse-core"
            # Reduced motion: the core holds still in both states, while the fades stay.
            await page.emulate_media(reduced_motion="reduce")
            assert await page.evaluate(_CORE_ANIMATION) == "none"
            assert await page.evaluate("getComputedStyle(document.querySelector('.card')).animationName") == "fade-in"
            async with asyncio.timeout(_RUN_WAIT_S):
                await manager.wait(run.id)
            await expect(face).to_have_attribute("data-state", "idle", timeout=_STEP_WAIT_S * 1000)
            assert await page.evaluate(_CORE_ANIMATION) == "none"
            # On a phone the sidebar is a drawer: its slide is a transition of the base length, none under reduced motion.
            await page.set_viewport_size({"width": 390, "height": 800})
            await page.wait_for_function("document.querySelector('.shell')?.dataset.nav === 'drawer'")
            assert set((await page.evaluate(_DRAWER_TRANSITION)).split(", ")) == {"0s"}
            await page.emulate_media(reduced_motion="no-preference")
            assert await page.evaluate(_CORE_ANIMATION) == "breathe"
            assert "0.2s" in (await page.evaluate(_DRAWER_TRANSITION)).split(", ")
            await page.close()
        finally:
            await browser.close()
