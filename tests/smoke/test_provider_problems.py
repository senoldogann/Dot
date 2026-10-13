"""The problems of the providers in Settings › Models (item 185): a provider that refused access (HTTP 403) says so,
with its words, and has no link to the keys, which would not help; one that refused its key (HTTP 401) says that and
links to API keys; one without a key says it has none. Served by a real server, wide and on a phone, light and dark;
the screenshots are the record of the look. The providers are stand-ins: no network and no model service is used."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.errors import DotError, MissingSecretError, ProviderAccessError, ProviderAuthError
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import KNOWN_PROVIDERS, ModelInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "provider-problems-token"
ACCESS: str = "provider answered HTTP 403: this key may not use the models of this project"
KEY: str = "provider answered HTTP 401: the key is not valid"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


class _Refusing(ScriptedProvider):
    """A provider whose list of models fails with ``error``."""

    def __init__(self, name: str, error: DotError) -> None:
        super().__init__(name, [])
        self._error: DotError = error

    async def list_models(self) -> tuple[ModelInfo, ...]:
        raise self._error


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[Runtime, str, int]]:
    """A runtime whose providers are stand-ins, served by a real server on a free port: its host and port."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    scripted: str = parse_model_ref(SCRIPTED_MODEL).provider
    refusals: dict[str, DotError] = {
        "openai": ProviderAccessError(ACCESS, 403, {}),
        "anthropic": ProviderAuthError(KEY, 401, {}),
        "opencode_go": MissingSecretError("no key is set for opencode_go", {}),
    }
    for name in KNOWN_PROVIDERS:
        error: DotError | None = refusals.get(name)
        await runtime.router.register(
            name, ScriptedProvider(name, []) if name == scripted or error is None else _Refusing(name, error)
        )
    manager: RunManager = RunManager(runtime)
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
        yield runtime, "127.0.0.1", port
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


def _provider(page: Page, name: str) -> Locator:
    """The fold of the provider ``name`` in Settings › Models."""
    return page.locator(".fold-group", has=page.locator(f"#fold-system-models-{name}"))


async def test_a_provider_that_refused_access_says_so_apart_from_one_that_refused_its_key(
    served: tuple[Runtime, str, int], tmp_path: Path
) -> None:
    runtime, host, port = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"http://{host}:{port}/#/settings/models")
                # The opening plays once per tab (item 114); the shots show the page once it has ended.
                await expect(page.locator("html")).not_to_have_attribute("data-opening", re.compile(r".+"))
                access: Locator = _provider(page, "openai")
                await expect(access.locator(".badge")).to_have_text("access refused")
                await expect(access).to_contain_text(ACCESS)
                await expect(access.get_by_role("link", name="Check the key")).to_have_count(0)
                key: Locator = _provider(page, "anthropic")
                await expect(key.locator(".badge")).to_have_text("key refused")
                await expect(key.get_by_role("link", name="Check the key")).to_have_count(1)
                await expect(_provider(page, "opencode_go").locator(".badge")).to_have_text("no key")
                await page.screenshot(path=str(tmp_path / f"providers-{width}-{scheme}.png"), full_page=True)
                await page.close()
        finally:
            await browser.close()
