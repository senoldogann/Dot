"""Themes (item 126): Settings › Appearance holds the colour scheme as three miniatures and the themes as a gallery of
cards with a light and a dark orb; every theme, Dot's two looks and six palettes, holds Dot's contrast rule in both
schemes as the browser paints it; a theme for light and another for dark ride on the page element and are there before
the first paint after a reload; ⌥⌘A opens the tab and ⇧⌥⌘A steps through the schemes."""

import asyncio
import re
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
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "themes-test-token"
Scheme = Literal["light", "dark"]
_THEMES: tuple[str, ...] = (
    "neutral",
    "paper",
    "clean",
    "rose",
    "grove",
    "ocean",
    "ember",
    "iris",
    "midnight",
    "nord",
    "solarized",
    "sepia",
    "forest",
    "contrast",
)
_SCHEMES: tuple[Scheme, ...] = ("light", "dark")
# Dot's contrast rule, as tests/unit/test_css_tokens.py holds the looks of themes.css to it.
_SURFACES: tuple[str, ...] = ("--bg", "--sidebar", "--surface", "--surface-2", "--surface-3")
_LEAST: tuple[tuple[str, float], ...] = (("--text", 4.5), ("--text-2", 4.5), ("--muted", 3.0), ("--faint", 3.0))
_ON: tuple[tuple[str, str], ...] = (
    ("--on-accent", "--accent"),
    ("--on-bubble", "--bubble"),
    ("--ok", "--ok-weak"),
    ("--warn", "--warn-weak"),
    ("--bad", "--bad-weak"),
    ("--on-bad", "--bad"),
)
_TOKENS: tuple[str, ...] = tuple(
    dict.fromkeys((*_SURFACES, *(tier for tier, _ in _LEAST), *(token for pair in _ON for token in pair)))
)
# The colour each token paints with on the page now, read through a probe so that light-dark() resolves as it draws.
_PAINTED: str = """(tokens) => {
  const probe = document.createElement("span");
  document.body.append(probe);
  const colours = {};
  for (const token of tokens) {
    probe.style.color = `var(${token})`;
    colours[token] = getComputedStyle(probe).color;
  }
  probe.remove();
  return colours;
}"""
_RGB: re.Pattern[str] = re.compile(r"^rgb\((\d+), (\d+), (\d+)\)$")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    """A client of a real uvicorn server on a free port, so the browser can open the UI."""
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
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


def _base(client: httpx.AsyncClient) -> tuple[str, int]:
    port: int | None = client.base_url.port
    assert port is not None
    return client.base_url.host, port


async def _appearance(browser: Browser, client: httpx.AsyncClient) -> Page:
    """A page signed in on Settings › Appearance, on a light system that asks for less motion (no opening)."""
    host, port = _base(client)
    page: Page = await browser.new_page(
        viewport={"width": 1280, "height": 900}, color_scheme="light", reduced_motion="reduce"
    )
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/settings/appearance")
    await expect(page.locator(".theme-card")).to_have_count(len(_THEMES))
    return page


async def _painted(page: Page) -> dict[str, str]:
    colours: dict[str, str] = await page.evaluate(_PAINTED, list(_TOKENS))
    return colours


def _luminance(colour: str) -> float:
    match: re.Match[str] | None = _RGB.match(colour)
    assert match is not None, f"not an opaque rgb() colour: {colour}"

    def channel(value: int) -> float:
        c: float = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(part) for part in match.groups())
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(one: str, other: str) -> float:
    lighter, darker = sorted((_luminance(one), _luminance(other)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _short(theme: str, scheme: Scheme, colours: dict[str, str]) -> list[str]:
    """Where `colours` break Dot's rule: a tier or an ink under its least contrast, or a surface out of its step."""
    short: list[str] = []
    for tier, least in _LEAST:
        for surface in _SURFACES:
            ratio: float = _contrast(colours[tier], colours[surface])
            if ratio < least:
                short.append(f"{theme} {scheme}: {tier} on {surface} {ratio:.2f} < {least}")
    for ink, ground in _ON:
        ratio = _contrast(colours[ink], colours[ground])
        if ratio < 4.5:
            short.append(f"{theme} {scheme}: {ink} on {ground} {ratio:.2f} < 4.5")
    steps: list[float] = [_luminance(colours[name]) for name in ("--surface", "--surface-2", "--surface-3")]
    if steps != sorted(steps, reverse=scheme == "light"):
        short.append(f"{theme} {scheme}: the surfaces do not step {'down' if scheme == 'light' else 'up'}: {steps}")
    if scheme == "dark" and _luminance(colours["--bg"]) >= steps[0]:
        short.append(f"{theme} dark: the page is not darker than the card")
    return short


async def _hold(page: Page, scheme: Literal["system", "light", "dark"]) -> None:
    await page.get_by_role("group", name="Colour scheme").get_by_role("button", name=scheme.title()).click()
    if scheme == "system":
        await expect(page.locator("html")).not_to_have_attribute("data-scheme", re.compile(".+"))
    else:
        await expect(page.locator("html")).to_have_attribute("data-scheme", scheme)


async def _pick(page: Page, theme: str, scheme: Scheme | None) -> None:
    """Click a theme's card (both schemes) or one of its orbs, by what a screen reader hears."""
    card = page.locator(f"[data-theme-card='{theme}']")
    if scheme is None:
        # The corner of the card, clear of its orbs.
        await card.locator(".theme-card-pick").click(position={"x": 12, "y": 12})
    else:
        await card.locator(f"[data-orb='{scheme}']").click()


async def test_every_theme_reads_in_both_schemes_as_the_browser_paints_it(
    browser: Browser, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    page: Page = await _appearance(browser, client)
    host, port = _base(client)
    await page.screenshot(path=str(tmp_path / "appearance-1280-light.png"), full_page=True)
    short: list[str] = []
    for scheme in _SCHEMES:
        await _hold(page, scheme)
        for theme in _THEMES:
            await _pick(page, theme, None)
            await expect(page.locator("html")).to_have_attribute("data-theme", theme)
            pick = page.locator(f"[data-theme-card='{theme}'] .theme-card-pick")
            await expect(pick).to_have_attribute("aria-pressed", "true")
            short.extend(_short(theme, scheme, await _painted(page)))
            await page.goto(f"http://{host}:{port}/#/controls")
            await expect(page.locator(".control-section").first).to_be_visible()
            await page.screenshot(path=str(tmp_path / f"controls-{theme}-{scheme}.png"), full_page=True)
            await page.goto(f"http://{host}:{port}/#/settings/appearance")
            await expect(page.locator(".theme-card")).to_have_count(len(_THEMES))
        await page.screenshot(path=str(tmp_path / f"appearance-1280-{scheme}-iris.png"), full_page=True)
    assert short == [], short

    # General no longer holds the look: the scheme and the layout are Appearance's.
    await page.goto(f"http://{host}:{port}/#/settings/general")
    await expect(page.get_by_role("switch", name="Notifications", exact=True)).to_be_visible()
    await expect(page.get_by_role("group", name="Colour scheme")).to_have_count(0)
    await expect(page.get_by_role("group", name="Density")).to_have_count(0)
    await page.close()


async def test_a_theme_for_light_and_another_for_dark_are_on_the_page_before_its_first_paint(
    browser: Browser, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    page: Page = await _appearance(browser, client)
    html = page.locator("html")
    await _hold(page, "light")
    await _pick(page, "paper", "light")
    await _pick(page, "ember", "dark")
    assert await page.evaluate("[localStorage.getItem('dot.theme'), localStorage.getItem('dot.themeDark')]") == [
        "paper",
        "ember",
    ]
    for theme, scheme, pressed in (
        ("paper", "light", "true"),
        ("paper", "dark", "false"),
        ("ember", "light", "false"),
        ("ember", "dark", "true"),
        ("neutral", "light", "false"),
        ("neutral", "dark", "false"),
    ):
        await expect(page.locator(f"[data-theme-card='{theme}'] [data-orb='{scheme}']")).to_have_attribute(
            "aria-pressed", pressed
        )
    await expect(page.locator(".theme-card[data-in-use]")).to_have_count(2)
    # The pair rides on the page element; Paper's voice and its layout are the light scheme's.
    assert (await page.evaluate("document.documentElement.style.getPropertyValue('--bg')")).startswith("light-dark(")
    await expect(html).to_have_attribute("data-theme", "paper")
    await expect(page.get_by_role("switch", name="Fold tool steps")).to_have_attribute("aria-checked", "false")
    await expect(page.get_by_text("These two follow Paper, the theme picked for light")).to_be_visible()
    light: dict[str, str] = await _painted(page)
    assert _short("paper/ember", "light", light) == []
    await _hold(page, "dark")
    await expect(html).to_have_attribute("data-theme", "ember")
    dark: dict[str, str] = await _painted(page)
    assert _short("paper/ember", "dark", dark) == []
    assert dark["--bg"] != light["--bg"]
    await page.screenshot(path=str(tmp_path / "mix-dark.png"), full_page=True)

    # Without main.js only boot.js runs: what it puts on the page from this browser's keys is the first paint.
    await page.route("**/js/main.js", lambda route: route.abort())
    await page.reload()
    await expect(html).to_have_attribute("data-theme", "ember")
    assert await _painted(page) == dark
    await page.evaluate("localStorage.setItem('dot.scheme', 'light')")
    await page.reload()
    await expect(html).to_have_attribute("data-theme", "paper")
    assert await _painted(page) == light
    await page.unroute("**/js/main.js")
    await page.reload()
    await expect(page.locator(".theme-card")).to_have_count(len(_THEMES))
    assert await _painted(page) == light

    # One look for both is its own block of themes.css: nothing on the page element, nothing kept.
    await _pick(page, "neutral", None)
    await expect(html).to_have_attribute("data-theme", "neutral")
    assert await page.evaluate("document.documentElement.style.getPropertyValue('--bg')") == ""
    assert await page.evaluate("[localStorage.getItem('dot.themeDark'), localStorage.getItem('dot.themeTokens')]") == [
        None,
        None,
    ]
    await expect(page.get_by_role("switch", name="Fold tool steps")).to_have_attribute("aria-checked", "true")
    await page.close()


async def test_the_keys_open_appearance_and_step_through_the_schemes(
    browser: Browser, client: httpx.AsyncClient
) -> None:
    page: Page = await _appearance(browser, client)
    host, port = _base(client)
    html = page.locator("html")
    await _hold(page, "system")
    await page.goto(f"http://{host}:{port}/#/runs")
    await page.keyboard.press("Control+Alt+KeyA")
    await expect(page).to_have_url(re.compile(r"#/settings/appearance$"))
    await expect(page.locator(".theme-card")).to_have_count(len(_THEMES))
    for scheme in ("light", "dark"):
        await page.keyboard.press("Control+Alt+Shift+KeyA")
        await expect(html).to_have_attribute("data-scheme", scheme)
        await expect(page.get_by_text(f"Colour scheme: {scheme.title()}")).to_be_visible()
        card = page.get_by_role("group", name="Colour scheme").get_by_role("button", name=scheme.title())
        await expect(card).to_have_attribute("aria-pressed", "true")
    await page.keyboard.press("Control+Alt+Shift+KeyA")
    await expect(html).not_to_have_attribute("data-scheme", re.compile(".+"))
    await page.close()
