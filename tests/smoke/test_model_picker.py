"""The composer's model menu (item 208), after T3 Code's: a rail of the favourites and of each provider's mark shows one
view at a time, a search finds models across every provider, a star keeps a model among the favourites (in this
browser) without picking it, and the keys move, pick, star and leave; on a touch screen the search leaves the focus
where it is. A real server, whose providers list the test's models and answer nothing; nothing is sent. Wide and on
a phone, light and dark."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "model-picker-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))
_FAVOURITES_KEY: str = "dot.models.favourites"
_MENU: str = ".composer .composer-popover[aria-label='Model']"


def _model(name: str, tools: bool, free: bool) -> ModelInfo:
    return ModelInfo(id=name, vision=False, tools=tools, thinking=False, context_length=None, efforts=(), free=free)


class Listed:
    """A provider that lists the test's models and answers nothing of its own: no run here calls a model."""

    def __init__(self, name: str, models: tuple[ModelInfo, ...]) -> None:
        self._script: ScriptedProvider = ScriptedProvider(name, [])
        self._models: tuple[ModelInfo, ...] = models

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return next(info for info in self._models if info.id == model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return self._models

    async def aclose(self) -> None:
        await self._script.aclose()


# The models the providers list: one that cannot call tools, which runs cannot use; OpenCode Go has no key here, so it
# lists none.
_LISTS: dict[str, tuple[ModelInfo, ...]] = {
    _PROVIDER: (
        _model(parse_model_ref(SCRIPTED_MODEL).model, True, False),
        _model("qwen3-coder", True, True),
        _model("llava", False, False),
    ),
    "openai": (_model("gpt-5.5", True, False), _model("gpt-5.5-mini", True, False)),
    "anthropic": (_model("claude-opus-5-5", True, False), _model("claude-haiku-5-5", True, False)),
}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, which no run here calls."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
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


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> tuple[Page, list[str]]:
    """The home page, signed in, in a browser of its own, and the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 860}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    return page, errors


async def _rail(menu: Locator) -> list[tuple[str, str]]:
    """The rail's items, `(label, pressed)`, in order."""
    items: Locator = menu.locator("[role=toolbar][aria-label='Providers'] button")
    return [
        (str(await item.get_attribute("aria-label")), str(await item.get_attribute("aria-pressed")))
        for item in await items.all()
    ]


async def _rows(menu: Locator) -> list[str]:
    """The names of the rows the list shows, in order."""
    names: Locator = menu.locator("[role=listbox] [role=option] .pop-option-text > span:first-child")
    return [str(text) for text in await names.all_inner_texts()]


async def _favourites(page: Page) -> list[str]:
    stored: object = json.loads(await page.evaluate(f"localStorage.getItem('{_FAVOURITES_KEY}') ?? '[]'"))
    assert isinstance(stored, list)
    return [str(ref) for ref in stored]


async def test_the_model_menu_shows_providers_finds_models_and_keeps_favourites(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page, errors = await _page(browser, served, width, scheme)
                pill: Locator = page.locator(".composer .composer-pill[data-pill='model']")
                menu: Locator = page.locator(_MENU)
                search: Locator = menu.locator("input[aria-label='Search models']")
                await expect(pill).to_contain_text("scripted")

                # Opened with no favourites: on the provider of the model a run would use, the search taking the keys.
                await pill.click()
                await expect(menu.locator(".model-picker")).to_have_count(1)
                assert await _rail(menu) == [
                    ("Favourites", "false"),
                    ("Ollama", "true"),
                    ("OpenAI", "false"),
                    ("Anthropic", "false"),
                ]
                marks: list[str | None] = [
                    await mark.get_attribute("data-provider")
                    for mark in await menu.locator("[role=toolbar] .provider-mark").all()
                ]
                assert marks == [_PROVIDER, "openai", "anthropic"]
                assert await _rows(menu) == ["Default (scripted)", "scripted", "qwen3-coder"]
                await expect(menu.locator("[role=option][aria-selected=true]")).to_contain_text("Default (scripted)")
                await expect(search).to_be_focused()
                await expect(menu.locator("[role=option] kbd")).to_have_text(["⌘1", "⌘2"])
                await page.screenshot(path=str(tmp_path / f"menu-{width}-{scheme}.png"), animations="disabled")

                # The rail shows one provider's models, and gives the keys back to the search.
                await menu.get_by_role("button", name="OpenAI").click()
                assert (await _rail(menu))[2] == ("OpenAI", "true")
                assert await _rows(menu) == ["Default (scripted)", "gpt-5.5", "gpt-5.5-mini"]
                await expect(search).to_be_focused()

                # A search looks across every provider, the rail pressing none; a search that finds nothing says so.
                await search.fill("haiku")
                assert await _rows(menu) == ["claude-haiku-5-5"]
                assert all(pressed == "false" for _, pressed in await _rail(menu))
                await expect(menu.locator("[role=option] .pop-option-hint")).to_have_text(["Anthropic"])
                await page.screenshot(path=str(tmp_path / f"search-{width}-{scheme}.png"), animations="disabled")
                await search.fill("free coder")
                assert await _rows(menu) == ["qwen3-coder"]
                await search.fill("default")
                assert await _rows(menu) == ["Default (scripted)"]
                await expect(menu.locator(".model-note")).to_be_hidden()
                await search.fill("zzz")
                assert await _rows(menu) == []
                await expect(menu.locator(".model-note")).to_have_text("No model matches “zzz”.")
                await search.fill("")
                assert await _rows(menu) == ["Default (scripted)", "gpt-5.5", "gpt-5.5-mini"]
                await expect(menu.locator(".model-note")).to_be_hidden()

                # A star keeps a model among the favourites, and picks nothing.
                star: Locator = menu.locator(".model-row").filter(has_text="gpt-5.5").first.locator(".model-star")
                await star.click()
                await expect(star).to_have_attribute("aria-pressed", "true")
                assert await _favourites(page) == ["openai:gpt-5.5"]
                await expect(menu).to_be_visible()
                await expect(pill).to_contain_text("scripted")

                # The keys: down to the second model, Enter picks it and the menu closes.
                await expect(search).to_be_focused()
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("ArrowDown")
                await expect(menu.locator("[role=option].active")).to_contain_text("gpt-5.5-mini")
                await page.keyboard.press("Enter")
                await expect(menu).to_be_hidden()
                await expect(pill).to_contain_text("gpt-5.5-mini")

                # Opened again: on the favourites, each with its provider; ⌘1 picks the first.
                await pill.click()
                assert (await _rail(menu))[0] == ("Favourites", "true")
                assert await _rows(menu) == ["Default (scripted)", "gpt-5.5"]
                await expect(menu.locator("[role=option] .pop-option-hint")).to_have_text(["OpenAI"])
                await page.screenshot(path=str(tmp_path / f"favourites-{width}-{scheme}.png"), animations="disabled")
                await page.keyboard.press("ControlOrMeta+1")
                await expect(menu).to_be_hidden()
                await expect(pill).to_contain_text("gpt-5.5")

                # ⌘D stars or unstars the row the keys are on, which stays in its place.
                await pill.click()
                await expect(menu.locator("[role=option].active")).to_contain_text("gpt-5.5")
                await page.keyboard.press("ControlOrMeta+d")
                assert await _favourites(page) == []
                assert await _rows(menu) == ["Default (scripted)", "gpt-5.5"]
                await expect(menu.locator(".model-star")).to_have_attribute("aria-pressed", "false")

                # The rail by the keys: left from an empty search, down to the next view, Enter shows it, right back.
                await page.keyboard.press("ArrowLeft")
                await expect(menu.get_by_role("button", name="Favourites")).to_be_focused()
                await page.keyboard.press("ArrowDown")
                await expect(menu.get_by_role("button", name="Ollama")).to_be_focused()
                await page.keyboard.press("Enter")
                assert (await _rail(menu))[1] == ("Ollama", "true")
                await expect(search).to_be_focused()
                await page.keyboard.press("ArrowLeft")
                await page.keyboard.press("ArrowRight")
                await expect(search).to_be_focused()

                # Escape closes the menu and gives the keys back to the pill; the default's row picks the default.
                await page.keyboard.press("Escape")
                await expect(menu).to_be_hidden()
                await expect(pill).to_be_focused()
                await pill.click()
                await menu.get_by_role("option", name="Default (scripted)").click()
                await expect(pill).to_contain_text("scripted")
                assert errors == []
                await page.context.close()

            # On a touch screen the search does not take the focus, so no keyboard springs up over the list, and the
            # rows wear no keys.
            host, port = "127.0.0.1", int(served.rpartition(":")[2])
            touch: BrowserContext = await browser.new_context(
                viewport={"width": 390, "height": 860}, is_mobile=True, has_touch=True, reduced_motion="reduce"
            )
            phone: Page = await touch.new_page()
            await phone.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await phone.goto(ui_url(host, port, TOKEN))
            await phone.locator(".composer .composer-pill[data-pill='model']").tap()
            menu = phone.locator(_MENU)
            await expect(menu.locator(".model-picker")).to_have_count(1)
            await expect(menu.locator("input[aria-label='Search models']")).not_to_be_focused()
            await expect(menu.locator("[role=option] kbd")).to_have_count(0)
            await menu.get_by_role("button", name="OpenAI").tap()
            assert await _rows(menu) == ["Default (scripted)", "gpt-5.5", "gpt-5.5-mini"]
            await expect(menu.locator("input[aria-label='Search models']")).not_to_be_focused()
            await touch.close()

            # Opened before the model list has come, the menu moves to its opening view once the list comes. (The
            # list is held back where the browser lets a test hold a request.)
            release: asyncio.Event = asyncio.Event()

            async def held(route: Route) -> None:
                await release.wait()
                await route.continue_()

            early: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 860}, reduced_motion="reduce"
            )
            late: Page = await early.new_page()
            await late.route("**/api/models", held)
            await late.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await late.goto(ui_url(host, port, TOKEN))
            await late.locator(".composer .composer-pill[data-pill='model']").click()
            menu = late.locator(_MENU)
            await expect(menu.locator(".model-picker")).to_have_count(1)
            release.set()
            await expect(menu.locator("[role=toolbar] button")).to_have_count(4)
            assert (await _rail(menu))[1] == ("Ollama", "true")
            assert await _rows(menu) == ["Default (scripted)", "scripted", "qwen3-coder"]
            await early.close()
        finally:
            await browser.close()
