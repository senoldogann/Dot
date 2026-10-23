"""The standby models chosen in Settings › Models (item 478): the models a run moves to, in their order, when its own
model cannot answer. The card adds one of the models that call tools, moves one up or down and takes one off; the config
file keeps the list with the rest of it as it was, the status says it, and the runtime moves the next run by it with no
restart. A model named twice, one that cannot call tools and one of Zen's free models are refused, and nothing changes.
A real server whose providers list the test's models and answer nothing; wide and light, and on a phone and dark."""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.paths import config_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "standby-models-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
_SEEING: str = f"{_PROVIDER}:llava"
_HAIKU: str = "anthropic:claude-haiku-5-5"
_OPUS: str = "anthropic:claude-opus-5-5"
# One of Zen's free models, which answer only as OpenCode's own agent.
_ZEN: str = "opencode:big-pickle"
# The owner's own words in the file, which a choice must leave as they are.
_MODELS_TABLE: str = f'[models]\nmain = "{SCRIPTED_MODEL}"\n# mine: keep this\nstandby = []\n'


def _model(name: str, tools: bool) -> ModelInfo:
    return ModelInfo(
        id=name,
        vision=False,
        tools=tools,
        thinking=False,
        context_length=None,
        own_context_length=None,
        window_source=None,
        efforts=(),
        free=False,
    )


class Listed:
    """A provider that lists the test's models and answers none of the calls it is sent."""

    def __init__(self, name: str, models: tuple[ModelInfo, ...]) -> None:
        self._name: str = name
        self._models: tuple[ModelInfo, ...] = models

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        raise ProviderProtocolError("the stand-in answers nothing", {"provider": self._name, "model": request.model})

    async def model_info(self, model: str) -> ModelInfo:
        return next(info for info in self._models if info.id == model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return self._models

    async def aclose(self) -> None:
        return None


# A model that cannot call tools (llava), which no run could move to.
_LISTS: dict[str, tuple[ModelInfo, ...]] = {
    _PROVIDER: (_model(parse_model_ref(SCRIPTED_MODEL).model, True), _model("llava", False)),
    "anthropic": (_model("claude-opus-5-5", True), _model("claude-haiku-5-5", True)),
}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose providers are the stand-ins, which answer no call."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f"[browser]\nheadless = true\n\n{_MODELS_TABLE}", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    for provider, models in _LISTS.items():
        await built.router.register(provider, Listed(provider, models))
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


async def _file(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def _standby(http: httpx.AsyncClient) -> object:
    """The standby models the daemon's status says."""
    response: httpx.Response = await http.get("/api/status")
    assert response.status_code == 200, response.text
    models: dict[str, object] = response.json()["models"]
    return models["standby"]


async def _hold_first(page: Page, pattern: str, until: asyncio.Event) -> None:
    """Hold the first request of ``page`` to ``pattern`` until ``until`` is set, then let it go on."""

    async def hold(route: Route) -> None:
        await until.wait()
        await route.continue_()

    await page.route(pattern, hold, times=1)


async def _listed(page: Page) -> list[str]:
    """The standby models the card lists, in their order."""
    rows: Locator = page.locator(".standby-list .standby-row")
    return await rows.evaluate_all("(rows) => rows.map((row) => row.dataset.model)")


async def test_settings_choose_the_standby_models_in_their_order(runtime: Runtime, served: str, tmp_path: Path) -> None:
    # The throw-away home's config file, never the owner's.
    path: Path = config_path()
    assert path == Path(os.environ["DOT_HOME"]) / "config.toml"
    host, port = "127.0.0.1", int(served.rpartition(":")[2])
    async with (
        httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as http,
        async_playwright() as playwright,
    ):
        # A model named twice, one that cannot call tools and one of Zen's free models are refused; nothing changes.
        for models in ([_HAIKU, _HAIKU], [_SEEING], [_ZEN]):
            refused: httpx.Response = await http.put("/api/models/standby", json={"models": models})
            assert refused.status_code == 422, refused.text
        assert await _standby(http) == []
        assert runtime.roles.standby() == ()
        assert (await _file(path)).endswith(_MODELS_TABLE)

        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{served}/#/settings/models")
                add: Locator = page.get_by_role("combobox", name="Add a fallback model")
                card: Locator = page.locator(".models-standby")
                if index == 0:
                    await expect(card.locator(".standby-none")).to_be_visible()
                    # The menu offers the models that call tools: not llava.
                    offered: list[str] = await add.locator("option").evaluate_all(
                        "(options) => options.map((option) => option.value).filter((value) => value !== '')"
                    )
                    assert sorted(offered) == sorted([SCRIPTED_MODEL, _HAIKU, _OPUS]), offered
                    # Two adds in a row, the second made while the first is not answered yet (its answer held until
                    # then): both hold, in their order.
                    second_made: asyncio.Event = asyncio.Event()
                    await _hold_first(page, "**/api/models/standby", second_made)
                    await add.select_option(_HAIKU)
                    await add.select_option(_OPUS)
                    second_made.set()
                    await expect(card.locator(".standby-row")).to_have_count(2)
                    assert await _listed(page) == [_HAIKU, _OPUS]
                    # Opus up: a run tries it first; then Haiku off the list.
                    await page.get_by_role("button", name="Move claude-opus-5-5 up").click()
                    await expect(card.locator(".standby-row").first).to_have_attribute("data-model", _OPUS)
                    await page.get_by_role("button", name="Remove claude-haiku-5-5").click()
                    await expect(card.locator(".standby-row")).to_have_count(1)
                    # The status, the runtime and the config file say it, the rest of the file as it was.
                    assert await _standby(http) == [_OPUS]
                    assert runtime.roles.standby() == (_OPUS,)
                    assert (await _file(path)).endswith(
                        f'[models]\nmain = "{SCRIPTED_MODEL}"\n# mine: keep this\nstandby = ["{_OPUS}"]\n'
                    )
                    assert load_config(path).models.standby == [_OPUS]
                else:
                    await expect(card.locator(".standby-row")).to_have_count(1)
                    assert await _listed(page) == [_OPUS]
                    await expect(card.locator(".standby-none")).to_be_hidden()
                    # One model alone: both its arrows are off.
                    await expect(page.get_by_role("button", name="Move claude-opus-5-5 up")).to_be_disabled()
                    await expect(page.get_by_role("button", name="Move claude-opus-5-5 down")).to_be_disabled()
                assert not await card.evaluate("(node) => node.scrollWidth > node.clientWidth")
                assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                await card.scroll_into_view_if_needed()
                await page.mouse.move(0, 0)
                await page.screenshot(path=str(tmp_path / f"standby-{width}-{scheme}.png"), animations="disabled")
                await page.close()
        finally:
            await browser.close()
