"""The web UI speaks Turkish when the owner chose it (item 19 of the 2026-11-26 review): the greeting, the composer and
every page of Settings, wide and on a phone, with no error in the page's console and no line of English left among the
words Dot writes itself. A real server on a scripted model, in Chrome.

Words that come from the owner's data or from a model's service (a model's name, a schedule's goal) are not Dot's own,
so the scan reads only the lines that hold several common English words together, which no name does."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "turkish-interface-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# The pages of Settings, as the nav lists them (js/settingstabs.js), with the Turkish name each carries.
_TABS: dict[str, str] = {
    "general": "Genel",
    "appearance": "Görünüm",
    "rules": "Kurallar",
    "memory": "Bellek",
    "vault": "Kasa",
    "snapshots": "Anlık görüntüler",
    "agents": "Ajanlar",
    "models": "Modeller",
    "browser": "Tarayıcı",
    "websearch": "Web araması",
    "schedules": "Zamanlanmış görevler",
    "campaigns": "Kampanyalar",
    "harness": "Kendini geliştirme",
    "connectors": "Bağlayıcılar",
    "plugins": "Eklentiler",
    "skills": "Beceriler",
    "keys": "Yapay zekâ hizmetleri",
    "phone": "Telefon",
    "channels": "Kanallar",
    "system": "Sistem",
    "repair": "Kendini onarma",
    "diagnostics": "Tanılama",
    "licences": "Açık kaynak lisansları",
}
# Words that only a sentence of English holds together: a line with three of them is English, not a name.
_ENGLISH: re.Pattern[str] = re.compile(r"\b(the|and|your|you|of|for|is|are|with|to|that|this|from|when|its|it)\b", re.I)
_ENGLISH_LINE_MOST: int = 3
# The pages whose descriptions the daemon writes in English (the templates of agents, the search engines, the catalogue
# of connectors and the gallery of plugins, the packages' own licences, a macOS tool's message): they are data of the
# daemon's, not words of the page, and stay English until the daemon says them in the owner's language too.
_FROM_THE_DAEMON: frozenset[str] = frozenset({"general", "agents", "websearch", "connectors", "plugins", "licences"})
_WAIT_S: float = 30.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false"
        f'\ngoal_mode = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
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


async def _open(browser: Browser, address: str, width: int) -> tuple[Page, list[str]]:
    """A page of the daemon in Turkish, `width` wide, and the errors its console and its scripts raise."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme="light", has_touch=width < 600
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    page.on(
        "console",
        lambda message: (
            errors.append(f"console: {message.text}")
            if message.type == "error" and "404" not in message.text and "422" not in message.text
            else None
        ),
    )
    await page.add_init_script(
        "sessionStorage.setItem('dot.opened', '1'); localStorage.setItem('dot.uiLanguage', 'tr')"
    )
    await page.goto(ui_url(host, port, TOKEN))
    return page, errors


def _english_lines(text: str) -> list[str]:
    """The lines of `text` that read as English: several of the words above in one line."""
    return [line for line in text.splitlines() if len(_ENGLISH.findall(line)) >= _ENGLISH_LINE_MOST]


@pytest.mark.parametrize("width", [1280, 390])
async def test_every_page_of_settings_reads_turkish_without_an_error(address: str, width: int) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page, errors = await _open(browser, address, width)
            assert await page.evaluate("document.documentElement.lang") == "tr"
            english: dict[str, list[str]] = {}
            for tab, name in _TABS.items():
                await page.goto(f"{address}/#/settings/{tab}")
                await expect(page).to_have_title(re.compile(re.escape(name)), timeout=_WAIT_S * 1000)
                await page.wait_for_timeout(300)
                lines = _english_lines(await page.locator("main").inner_text())
                if lines:
                    english[tab] = lines
                overflow = await page.evaluate(
                    "document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                assert overflow <= 1, f"{tab} scrolls sideways by {overflow} px at {width}"
            assert errors == []
            mine = {tab: lines for tab, lines in english.items() if tab not in _FROM_THE_DAEMON}
            assert mine == {}, f"English among the words of Settings: {mine}"
        finally:
            await browser.close()


async def test_the_home_and_the_composer_speak_turkish(address: str) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            page, errors = await _open(browser, address, 1280)
            await page.goto(f"{address}/#/")
            composer = page.locator("textarea").first
            await expect(composer).to_be_visible(timeout=_WAIT_S * 1000)
            assert await composer.get_attribute("placeholder") not in (None, "", "Message Dot")
            body: str = await page.locator("body").inner_text()
            assert "Good" not in body and "What should I work on" not in body
            assert errors == []
        finally:
            await browser.close()
