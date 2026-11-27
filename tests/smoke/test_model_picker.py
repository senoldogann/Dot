"""The composer's model menu (item 208): a rail of the favourites and of each provider's mark shows one view at a time,
a search finds the models of the view shown and keeps its words when another is shown (item 395), a star keeps a model
among the favourites (in this browser) without picking it, and the keys move, pick, star and leave; on a touch screen
the search leaves the focus where it is. Change, on the default's row, makes the daemon's default from the menu (item
209). A real server, whose providers list the test's models and answer nothing; nothing is sent. Wide and on a phone,
light and dark."""

import asyncio
import json
import time
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from model_menu import expect_chosen
from playwright.async_api import Browser, BrowserContext, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
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
# A phone's width has the chat settings' sheet instead of this menu (item 1202), read below on a touch screen.
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"))
_FAVOURITES_KEY: str = "dot.models.favourites"
# The rail of the menu: Favourites, then one mark for each provider that serves models.
_RAIL_ITEMS: str = "[role=toolbar][aria-label='AI services'] button"


def _model(name: str, tools: bool, free: bool) -> ModelInfo:
    return ModelInfo(
        id=name,
        vision=False,
        tools=tools,
        thinking=False,
        context_length=None,
        own_context_length=None,
        window_source=None,
        efforts=(),
        free=free,
    )


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
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
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
            # On every width the pill opens the chat's settings (items 1202 and 2015), the phone's sheet: a section per
            # provider, the chosen one's open; a star keeps a model among the favourites, which come first.
            for width, scheme in _SHOTS:
                page, errors = await _page(browser, served, width, scheme)
                pill: Locator = page.locator(".composer .composer-pill[data-pill='model']")
                await expect(pill).to_contain_text("scripted")
                await pill.click()
                sheet: Locator = page.get_by_role("dialog", name="Chat settings")
                await expect(sheet).to_be_visible()
                await expect(sheet.locator("details.sheet-section[open]")).to_have_count(1)
                await sheet.locator("summary", has_text="OpenAI").click()
                openai: Locator = sheet.locator("details.sheet-section", has_text="OpenAI")
                star: Locator = openai.locator(".model-star").first
                await star.click()
                await expect(star).to_have_attribute("aria-pressed", "true")
                assert await _favourites(page) != []
                await star.click()
                await expect(star).to_have_attribute("aria-pressed", "false")
                await page.screenshot(path=str(tmp_path / f"settings-{width}-{scheme}.png"), animations="disabled")
                await sheet.get_by_role("button", name="Done").click()
                await expect(sheet).to_be_hidden()
                assert errors == []
                await page.context.close()

            # On a phone the pill opens the chat's settings (item 1202): a sheet with the models in a section per
            # provider, the chosen one's open; its search does not take the focus, so no keyboard springs up over the
            # list; a pick keeps the sheet open, and Done puts it away.
            host, port = "127.0.0.1", int(served.rpartition(":")[2])
            touch: BrowserContext = await browser.new_context(
                viewport={"width": 390, "height": 860}, is_mobile=True, has_touch=True, reduced_motion="reduce"
            )
            phone: Page = await touch.new_page()
            await phone.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await phone.goto(ui_url(host, port, TOKEN))
            await phone.locator(".composer textarea").last.tap()
            phone_pill: Locator = phone.locator(".composer .composer-pill[data-pill='model']")
            await phone_pill.tap()
            sheet: Locator = phone.get_by_role("dialog", name="Chat settings")
            await expect(sheet).to_be_visible()
            await expect(sheet.locator("input.sheet-search-field")).not_to_be_focused()
            await expect(sheet.locator("details.sheet-section[open]")).to_have_count(1)
            await sheet.locator("summary", has_text="OpenAI").tap()
            openai: Locator = sheet.locator("details.sheet-section", has_text="OpenAI")
            names: Locator = openai.locator("[role=option] .sheet-row-text > span:first-child")
            assert await names.all_inner_texts() == ["gpt-5.5", "gpt-5.5-mini"]
            await openai.get_by_role("option", name="gpt-5.5-mini").tap()
            await expect(phone_pill).to_contain_text("gpt-5.5-mini")
            await expect(sheet).to_be_visible()
            await expect(openai.get_by_role("option", name="gpt-5.5-mini")).to_have_attribute("aria-selected", "true")
            # The search makes one list of what it finds, across the providers, and a repaint of the sheet (a choice
            # the daemon changed) leaves the field its focus.
            field: Locator = sheet.locator("input.sheet-search-field")
            await field.fill("mini")
            await expect(sheet.locator(".model-sections [role=option]")).to_have_count(1)
            await sheet.locator("summary", has_text="Approvals").tap()
            await field.focus()
            # A pick made without moving the focus, as a change the daemon tells the page repaints the sheet.
            await sheet.locator(".pop-option[data-mode='ask']").evaluate("option => option.click()")
            await expect(sheet.locator(".pop-option[data-mode='ask']")).to_have_attribute("aria-selected", "true")
            await expect(field).to_be_focused()
            await sheet.get_by_role("button", name="Done").tap()
            await expect(sheet).to_be_hidden()
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
            early_sheet: Locator = late.get_by_role("dialog", name="Chat settings")
            await expect(early_sheet).to_be_visible()
            release.set()
            await expect(early_sheet.locator("details.sheet-section", has_text="OpenAI")).to_have_count(1)
            await expect(early_sheet.get_by_role("option", name="qwen3-coder")).to_have_count(1)
            await early.close()
        finally:
            await browser.close()


async def _main_model(address: str) -> str:
    """The daemon's default model, as its status says."""
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        status: httpx.Response = await http.get("/api/status")
    status.raise_for_status()
    return str(status.json()["models"]["main"])


def _written() -> dict[str, object]:
    """The config file's settings, as the daemon would read them."""
    return tomllib.loads(config_path().read_text(encoding="utf-8"))


async def test_the_model_menu_makes_the_default_that_new_chats_start_on(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            # No service worker, so that the test can hold a request of the page in every browser (WebKit sends the
            # page's requests past `page.route` once one is installed).
            host, port = "127.0.0.1", int(served.rpartition(":")[2])
            context: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 860}, reduced_motion="reduce", service_workers="block"
            )
            page: Page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url(host, port, TOKEN))
            pill: Locator = page.locator(".composer .composer-pill[data-pill='model']")
            # The chat's settings on every width (items 1202 and 2015): the default's row has Change.
            menu: Locator = page.get_by_role("dialog", name="Chat settings")
            choosing: Locator = menu.locator(".model-choosing")
            change: Locator = menu.get_by_role("button", name="Change")
            await expect(pill).to_contain_text("scripted")

            # Change turns the list to choosing the default: a bar says so.
            await pill.click()
            await expect(menu).to_be_visible()
            await expect(choosing).to_be_hidden()
            await page.screenshot(path=str(tmp_path / "change-1280-light.png"), animations="disabled")
            await change.click()
            await expect(choosing).to_be_visible()
            await page.screenshot(path=str(tmp_path / "choosing-1280-light.png"), animations="disabled")

            # A model picked while choosing is the default: told, the default's row and the home's pill (which follows
            # the default) name it, and the daemon and its config file have it.
            await menu.locator("summary", has_text="OpenAI").click()
            await menu.get_by_role("option", name="gpt-5.5-mini", exact=True).click()
            await expect(page.locator(".toast.ok")).to_contain_text("New chats start on gpt-5.5-mini.")
            await expect(choosing).to_be_hidden()
            await expect(menu).to_be_visible()
            await expect(menu.get_by_role("option", name="Default (gpt-5.5-mini)")).to_be_visible()
            await expect(pill).to_contain_text("gpt-5.5-mini")
            assert await _main_model(served) == "openai:gpt-5.5-mini"
            models: object = _written()["models"]
            assert isinstance(models, dict) and models["main"] == "openai:gpt-5.5-mini", models
            assert _written()["browser"] == {"headless": True}

            # A change the daemon refuses says why and leaves the default as it was.
            async def refused(route: Route) -> None:
                await route.fulfill(
                    status=422,
                    json={
                        "message": "a task needs a model that can use actions",
                        "context": {"model": "openai:gpt-5.5"},
                    },
                )

            await page.route("**/api/models/main", refused)
            await change.click()
            await expect(choosing).to_be_visible()
            await menu.get_by_role("option", name="gpt-5.5", exact=True).click()
            await expect(page.locator(".toast.error")).to_contain_text("A task needs a model that can use actions")
            await page.unroute("**/api/models/main")
            assert await _main_model(served) == "openai:gpt-5.5-mini"
            await menu.get_by_role("button", name="Done").click()
            await expect(menu).to_be_hidden()

            # The Settings show the default the menu made.
            await page.goto(f"{served}/#/settings/models")
            await expect_chosen(page, "Default model", "openai:gpt-5.5-mini")
            assert errors == []
            await page.context.close()

            # Dark, and on a phone, whose chat settings (item 1202) have Change on the default's row: the bar fits.
            for width, scheme in ((1280, "dark"), (390, "light")):
                shown, shown_errors = await _page(browser, served, width, scheme)
                await shown.locator(".composer textarea").last.click()
                await shown.locator(".composer .composer-pill[data-pill='model']").click()
                held: Locator = shown.get_by_role("dialog", name="Chat settings")
                await held.get_by_role("button", name="Change").click()
                await expect(held.locator(".model-choosing")).to_be_visible()
                await shown.screenshot(path=str(tmp_path / f"choosing-{width}-{scheme}.png"), animations="disabled")
                assert shown_errors == []
                await shown.context.close()

            # On a touch screen: a tap on Change, then on a model, makes it the default.
            touch: BrowserContext = await browser.new_context(
                viewport={"width": 390, "height": 860}, is_mobile=True, has_touch=True, reduced_motion="reduce"
            )
            phone: Page = await touch.new_page()
            await phone.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await phone.goto(ui_url(host, port, TOKEN))
            await phone.locator(".composer textarea").last.tap()
            await phone.locator(".composer .composer-pill[data-pill='model']").tap()
            menu = phone.get_by_role("dialog", name="Chat settings")
            # Escape right after Change leaves the choosing, and the sheet stays open.
            await menu.get_by_role("button", name="Change").tap()
            await expect(menu.locator(".model-choosing")).to_be_visible()
            await phone.keyboard.press("Escape")
            await expect(menu.locator(".model-choosing")).to_have_count(0)
            await expect(menu).to_be_visible()
            await menu.get_by_role("button", name="Change").tap()
            # The default's provider is not this one now: its section opens first.
            await menu.locator("summary", has_text="Ollama").tap()
            await menu.locator("[role=option]").filter(has_text="qwen3-coder").tap()
            await expect(phone.locator(".toast.ok")).to_contain_text("New chats start on qwen3-coder.")
            # The word stands over the sheet, which a pick keeps open: the toast's middle is the toast's own.
            assert await phone.locator(".toast.ok").evaluate(
                "toast => { const r = toast.getBoundingClientRect();"
                " return toast.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)); }"
            )
            await expect(menu.get_by_role("option", name="Default (qwen3-coder)")).to_be_visible()
            assert await _main_model(served) == f"{_PROVIDER}:qwen3-coder"
            await touch.close()
        finally:
            await browser.close()
