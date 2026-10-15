"""The models of Dot's own work (item 232): Settings › Models chooses the fast, review and vision models under the default
one, offering those that can do that work; the config file keeps each choice with the rest of it as it was, the status
says it, and the next call of that work goes to it, with no restart. A model that cannot do the work is refused, and
nothing changes. A real server whose providers list the test's models and answer nothing; wide and on a phone, light
and dark."""

import asyncio
import os
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

TOKEN: str = "role-models-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_QUICK: str = f"{_PROVIDER}:qwen3-coder"
_SEEING: str = f"{_PROVIDER}:llava"
_HAIKU: str = "anthropic:claude-haiku-5-5"
_OPUS: str = "anthropic:claude-opus-5-5"
_MINI: str = "openai:gpt-5.5-mini"
# The owner's own words in the file, which a choice must leave as they are.
_MODELS_TABLE: str = (
    f'[models]\nmain = "{SCRIPTED_MODEL}"\n# mine: keep this\nfast = "{_QUICK}"\nreview = "{_QUICK}"\n'
    f'vision = "{_SEEING}"\n'
)


def _model(name: str, tools: bool, vision: bool) -> ModelInfo:
    return ModelInfo(
        id=name,
        vision=vision,
        tools=tools,
        thinking=False,
        context_length=None,
        own_context_length=None,
        window_source=None,
        efforts=(),
        free=False,
    )


class Listed:
    """A provider that lists the test's models, keeps the model of each call it is sent and answers none of them."""

    def __init__(self, name: str, models: tuple[ModelInfo, ...]) -> None:
        self._name: str = name
        self._models: tuple[ModelInfo, ...] = models
        self.asked: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.asked.append(request.model)
        raise ProviderProtocolError("the stand-in answers nothing", {"provider": self._name, "model": request.model})

    async def model_info(self, model: str) -> ModelInfo:
        return next(info for info in self._models if info.id == model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return self._models

    async def aclose(self) -> None:
        return None


# A model that cannot call tools (llava) and one that cannot see (gpt-5.5-mini), which some work cannot use.
_LISTS: dict[str, tuple[ModelInfo, ...]] = {
    _PROVIDER: (
        _model(parse_model_ref(SCRIPTED_MODEL).model, True, False),
        _model("qwen3-coder", True, False),
        _model("llava", False, True),
    ),
    "anthropic": (_model("claude-opus-5-5", True, True), _model("claude-haiku-5-5", True, True)),
    "openai": (_model("gpt-5.5-mini", True, False),),
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


async def _roles(http: httpx.AsyncClient) -> tuple[object, object, object]:
    """The fast, review and vision models the daemon's status says."""
    response: httpx.Response = await http.get("/api/status")
    assert response.status_code == 200, response.text
    models: dict[str, object] = response.json()["models"]
    return models["fast"], models["review"], models["vision"]


async def _offered(select: Locator) -> list[str]:
    return await select.locator("option").evaluate_all("(options) => options.map((option) => option.value)")


async def test_settings_choose_the_models_of_dots_own_work_and_the_next_call_goes_to_them(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    # The throw-away home's config file, never the owner's.
    path: Path = config_path()
    assert path == Path(os.environ["DOT_HOME"]) / "config.toml"
    host, port = "127.0.0.1", int(served.rpartition(":")[2])
    async with (
        httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as http,
        async_playwright() as playwright,
    ):
        # A model that cannot do the work is refused, and nothing changes.
        for role, model in (("vision", _MINI), ("review", _SEEING), ("fast", _SEEING)):
            refused: httpx.Response = await http.post("/api/models/roles", json={"role": role, "model": model})
            assert refused.status_code == 422, refused.text
        assert await _roles(http) == (_QUICK, _QUICK, _SEEING)
        assert (await _file(path)).endswith(_MODELS_TABLE)

        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{served}/#/settings/models")
                fast: Locator = page.get_by_role("combobox", name="Fast model")
                review: Locator = page.get_by_role("combobox", name="Review model")
                vision: Locator = page.get_by_role("combobox", name="Vision model")
                # Each offers the models that can do its work: tools for the fast and review models, sight for vision.
                await expect(review.locator("option")).to_have_count(5)
                assert sorted(await _offered(review)) == sorted([SCRIPTED_MODEL, _QUICK, _OPUS, _HAIKU, _MINI])
                assert sorted(await _offered(fast)) == sorted([SCRIPTED_MODEL, _QUICK, _OPUS, _HAIKU, _MINI])
                assert sorted(await _offered(vision)) == sorted([_SEEING, _OPUS, _HAIKU])
                if index == 0:
                    await expect(review).to_have_value(_QUICK)
                    # The review model chosen, the next call of its work goes to it, with no restart.
                    await review.select_option(_HAIKU)
                    await expect(page.get_by_text(f"The review model is {_HAIKU.split(':')[1]}")).to_be_visible()
                    await expect(review).to_have_value(_HAIKU)
                    assert runtime.roles.review() == _HAIKU
                    drafted: httpx.Response = await http.post(
                        "/api/rules/draft", json={"sentence": "Ask me before any file is deleted."}
                    )
                    assert drafted.status_code >= 400, drafted.text
                    anthropic = await runtime.router.provider("anthropic")
                    assert isinstance(anthropic, Listed)
                    assert anthropic.asked and set(anthropic.asked) == {"claude-haiku-5-5"}, anthropic.asked
                    await fast.select_option(_OPUS)
                    await expect(page.get_by_text(f"The fast model is {_OPUS.split(':')[1]}")).to_be_visible()
                    await expect(fast).to_have_value(_OPUS)
                    await vision.select_option(_HAIKU)
                    await expect(page.get_by_text(f"The vision model is {_HAIKU.split(':')[1]}")).to_be_visible()
                    await expect(vision).to_have_value(_HAIKU)
                    assert await _roles(http) == (_OPUS, _HAIKU, _HAIKU)
                    assert (runtime.roles.fast(), runtime.roles.vision()) == (_OPUS, _HAIKU)
                    # The config file keeps each choice, and the rest of it as it was.
                    assert (await _file(path)).endswith(
                        f'[models]\nmain = "{SCRIPTED_MODEL}"\n# mine: keep this\nfast = "{_OPUS}"\n'
                        f'review = "{_HAIKU}"\nvision = "{_HAIKU}"\n'
                    )
                    written = load_config(path).models
                    assert (written.fast, written.review, written.vision) == (_OPUS, _HAIKU, _HAIKU)
                else:
                    await expect(fast).to_have_value(_OPUS)
                    await expect(review).to_have_value(_HAIKU)
                    await expect(vision).to_have_value(_HAIKU)
                card: Locator = page.locator(".setting.models-role").first
                assert not (
                    await page.locator(".models-role").evaluate_all(
                        "(rows) => rows.some((row) => row.scrollWidth > row.clientWidth)"
                    )
                )
                await card.scroll_into_view_if_needed()
                await page.mouse.move(0, 0)
                await page.screenshot(path=str(tmp_path / f"roles-{width}-{scheme}.png"), animations="disabled")
                await page.close()
        finally:
            await browser.close()
