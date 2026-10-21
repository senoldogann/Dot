"""The page the browser is sent back to after a sign-in (item 437), in a real Chrome on a real server: drawn with
the interface's own sheets, which it carries, under the policy of its own (no console error, which a refused style or
script would be), asking the server for nothing but its faces (item 448), in the system's light or dark, wide and on a
phone, with the account's mark. The outcomes are the real ones of the accounts: a no with a provider's words, a return
that waited too long, and a state nobody waits for. No provider is reached."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import Browser, ConsoleMessage, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.oauth.accounts import WAIT_S
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "sign-in-page-token"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _sum(colour: str) -> int:
    """The sum of the channels of an ``rgb(r, g, b)`` colour: how light it is."""
    return sum(int(part) for part in colour.removeprefix("rgb(").removesuffix(")").split(",")[:3])


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, with a client of Google saved in its environment."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[models]\nmain = "ollama:scripted"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_GOOGLE_CLIENT_ID", "client-1")
    patch.setenv("DOT_GOOGLE_CLIENT_SECRET", "secret-1")
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
    """A real server over ``runtime``, whose address it answers on until the fixture ends."""
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


async def _address(runtime: Runtime, served: str, outcome: str) -> str:
    """The address a browser comes back to for ``outcome``: a state of a Google sign-in just started (a state is good
    once, so each window gets its own), one that was started too long ago, or one nobody waits for."""
    port: int = int(served.rpartition(":")[2])
    started: float = time.time() - (WAIT_S + 60 if outcome == "expired" else 0)
    link: str = await runtime.accounts.start("google", port, started)
    state: str = "nobody" if outcome == "unknown" else parse_qs(urlsplit(link).query)["state"][0]
    answer: dict[str, str] = (
        {"error": "server_error", "error_description": "Try later."} if outcome == "declined" else {"code": "c"}
    )
    return f"{served}/oauth/callback?{urlencode({'state': state, **answer})}"


async def _checked(page: Page, address: str, title: str, tmp_path: Path, name: str) -> None:
    """Open ``address`` and check the page is drawn as Dot is: the sheets applied, nothing refused, nothing wider than
    the window, and nothing asked of the server but the page and its faces."""
    problems: list[str] = []
    asked: list[str] = []

    def noted(message: ConsoleMessage) -> None:
        # The page's own 400 (a return that waited too long, a state nobody waits for) is logged by Chrome as an error
        # of its address; a refused style or script is one of its own.
        if message.type == "error" and message.location["url"] != address:
            problems.append(message.text)

    page.on("console", noted)
    page.on("requestfailed", lambda request: problems.append(f"{request.url} failed"))
    page.on("request", lambda request: asked.append(request.url))
    await page.goto(address)
    await expect(page.locator("h1")).to_have_text(title)
    await expect(page).to_have_title(f"{title} - Dot")
    await page.wait_for_load_state("networkidle")
    assert problems == [], problems
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    font: str = await page.evaluate("getComputedStyle(document.querySelector('h1')).fontFamily")
    assert "Inter" in font, f"the page is drawn without the interface's sheets: {font}"
    files: list[str] = [urlsplit(url).path for url in asked if url != address and not url.startswith("data:")]
    assert all(path.startswith("/fonts/") for path in files) and files, asked
    await page.screenshot(path=str(tmp_path / f"{name}.png"), full_page=True)


async def test_the_pages_are_drawn_as_dot_is_in_light_and_dark_wide_and_on_a_phone(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    titles: dict[str, str] = {
        "declined": "Not signed in to Google",
        "expired": "This sign-in ran out of time",
        "unknown": "This sign-in has ended",
    }
    grounds: dict[str, int] = {}
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for outcome, title in titles.items():
                for width, scheme in _SHOTS:
                    name: str = f"{outcome}-{width}-{scheme}"
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 844}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    try:
                        await _checked(page, await _address(runtime, served, outcome), title, tmp_path, name)
                        if outcome == "declined":
                            await expect(page.locator(".callback-reason")).to_have_text("server_error: Try later.")
                            marked: bool = await page.evaluate(
                                "document.querySelector('.callback-mark img').naturalWidth > 0"
                            )
                            assert marked, "the mark of Google did not load under the content security policy"
                        grounds[name] = _sum(await page.evaluate("getComputedStyle(document.body).backgroundColor"))
                    finally:
                        await page.close()
        finally:
            await browser.close()
    # The ground follows the system's scheme: lighter in the light than in the dark, wide and on a phone.
    for outcome in titles:
        for width in (1280, 390):
            assert grounds[f"{outcome}-{width}-light"] > grounds[f"{outcome}-{width}-dark"], grounds
