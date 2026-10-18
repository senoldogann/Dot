"""Themes of one's own (item 128): a theme is made from a background, an accent and an action colour with the page
drawn in it while it is made, kept by the daemon as a theme file in its home's ``themes`` folder, picked, on the page
before the first paint after a reload, held to Dot's contrast rule in both schemes, exported as a file, copied from a
built-in theme and deleted; a VS Code colour theme and a theme file's short form are added from files, a file Dot cannot
read is refused with the reason, and a file put in the folder by hand shows in the gallery or says why it does not."""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect
from test_themes import _base, _painted, _short

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import themes_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "own-themes-test-token"
_BUILT_IN: int = 8
# A VS Code colour theme as an extension ships one: a slug for a name, workbench colours, token colours.
_VSCODE: dict[str, object] = {
    "name": "harbour-night",
    "type": "dark",
    "colors": {
        "editor.background": "#0f1419",
        "editor.foreground": "#e6e1cf",
        "focusBorder": "#e6b450",
        "button.background": "#e6b450",
        "button.foreground": "#0f1419",
        "sideBar.background": "#0b0e14",
        "list.activeSelectionBackground": "#273747",
    },
    "tokenColors": [],
}
# The short form of a theme file: a name, a scheme and the two colours the rest is derived from.
_SHORT: dict[str, object] = {"name": "Moss", "appearance": "light", "canvas": "#f4f6f0", "accent": "#4f7a3a"}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a home of its own, so that its ``themes`` folder starts out missing."""
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


async def _colour(page: Page, label: str, value: str) -> None:
    """Type a colour into a field of the editor, as hex, and leave the field so that it is taken."""
    field = page.get_by_label(f"{label}, as hex", exact=True)
    await field.fill(value)
    await field.press("Tab")


async def _inline(page: Page, token: str) -> str:
    value: str = await page.evaluate(f"document.documentElement.style.getPropertyValue('{token}')")
    return value


async def _holds(page: Page, token: str, value: str) -> None:
    """Wait until the page element's own style gives `token` the colour `value`."""
    await page.wait_for_function(
        "([token, value]) => document.documentElement.style.getPropertyValue(token) === value", arg=[token, value]
    )


async def _themes(client: httpx.AsyncClient) -> dict[str, dict[str, object]]:
    listed = (await client.get("/api/themes")).json()
    return {entry["id"]: entry["file"] for entry in listed["themes"]}


async def _add(page: Page, path: Path) -> None:
    async with page.expect_file_chooser() as chosen:
        await page.locator("[data-card=themes]").get_by_role("button", name="Add theme").click()
    await (await chosen.value).set_files(str(path))


async def test_a_theme_of_ones_own_is_made_kept_picked_exported_copied_and_deleted(
    browser: Browser, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    host, port = _base(client)
    page: Page = await browser.new_page(
        viewport={"width": 1280, "height": 900}, color_scheme="light", reduced_motion="reduce"
    )
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/settings/appearance")
    html = page.locator("html")
    cards = page.locator(".theme-card")
    await expect(cards).to_have_count(_BUILT_IN)
    await expect(page.locator("[data-theme-folder]")).to_contain_text(str(themes_dir()))

    # Create: the editor takes the card, the other cards step aside, and the page is drawn in the palette as it moves.
    await page.get_by_role("button", name="Create theme").click()
    await expect(page.locator("[data-card=themes] .card-head")).to_contain_text("New theme")
    await expect(page.get_by_role("group", name="Colour scheme")).to_be_hidden()
    await page.get_by_label("Theme name").fill("Harbour")
    await _colour(page, "Background", "#f3f1ea")
    await _colour(page, "Accent", "#1f6feb")
    await expect(html).to_have_attribute("data-theme", "draft")
    await _holds(page, "--bg", "#f3f1ea")
    await page.get_by_role("group", name="Palette").get_by_role("button", name="Dark").click()
    await expect(html).to_have_attribute("data-scheme", "dark")
    await _colour(page, "Background", "#101418")
    await _colour(page, "Accent", "#58a6ff")
    await _colour(page, "Action", "#e3b341")
    await _holds(page, "--accent", "#e3b341")
    await page.get_by_role("button", name="Colour families").click()
    await _colour(page, "Sidebar", "#0b0e11")
    await _holds(page, "--sidebar", "#0b0e11")
    await page.screenshot(path=str(tmp_path / "editor-1280-dark.png"), full_page=True)
    await page.get_by_role("button", name="Save theme").click()

    # Saved: a ninth card, picked for both schemes, and the scheme held again as it was (the system's).
    harbour = page.locator("[data-theme-card='harbour']")
    await expect(cards).to_have_count(_BUILT_IN + 1)
    await expect(harbour).to_have_attribute("data-own", "")
    await expect(harbour.locator(".theme-card-pick")).to_have_attribute("aria-pressed", "true")
    await expect(html).to_have_attribute("data-theme", "harbour")
    await expect(html).not_to_have_attribute("data-scheme", re.compile(".+"))
    await expect(page.get_by_role("group", name="Colour scheme")).to_be_visible()
    kept = (await _themes(client))["harbour"]
    assert (kept["name"], kept["appearance"], kept["version"]) == ("Harbour", "light", 1)
    colours = kept["colors"]
    assert isinstance(colours, dict) and len(colours) == 61 and colours["canvas"] == "#f3f1ea"
    assert kept["variants"] == {"dark": kept["variants"]["dark"]}  # type: ignore[index]
    dark = kept["variants"]["dark"]  # type: ignore[index]
    assert (dark["canvas"], dark["messageAction"], dark["sidebar"]) == ("#101418", "#e3b341", "#0b0e11")
    assert "managed" not in kept, "a palette changed by hand is not the generator's alone"
    on_disk: str = await asyncio.to_thread((themes_dir() / "harbour.json").read_text, encoding="utf-8")
    assert on_disk.startswith('{\n  "version": 1,\n  "id": "harbour",\n  "name": "Harbour",')

    # Dot's rule holds for it in both schemes, as the browser paints it.
    short: list[str] = []
    for scheme in ("light", "dark"):
        await page.get_by_role("group", name="Colour scheme").get_by_role("button", name=scheme.title()).click()
        await expect(html).to_have_attribute("data-scheme", scheme)
        short.extend(_short("harbour", scheme, await _painted(page)))  # type: ignore[arg-type]
    assert short == [], short
    await page.get_by_role("group", name="Colour scheme").get_by_role("button", name="System").click()

    # Before the first paint: boot.js alone puts it on the page; then the page reads it from the daemon again.
    await page.route("**/js/main.js", lambda route: route.abort())
    await page.reload()
    await expect(html).to_have_attribute("data-theme", "harbour")
    assert (await _inline(page, "--bg")).startswith("light-dark(#f3f1ea, #101418")
    await page.unroute("**/js/main.js")
    await page.reload()
    await expect(harbour.locator(".theme-card-pick")).to_have_attribute("aria-pressed", "true")

    # Export: the theme file, version 1, as the daemon keeps it.
    await harbour.hover()
    async with page.expect_download() as downloading:
        await harbour.get_by_role("button", name="Export Harbour").click()
    download = await downloading.value
    assert download.suggested_filename == "harbour.json"
    exported = json.loads(await asyncio.to_thread(Path(await download.path()).read_text, encoding="utf-8"))
    assert (exported["version"], exported["id"], exported["name"]) == (1, "harbour", "Harbour")
    assert exported["colors"] == colours and exported["variants"] == kept["variants"]

    # Add: a VS Code colour theme, dark only, with one orb, taken for dark alone.
    vscode: Path = tmp_path / "harbour-night-color-theme.json"
    await asyncio.to_thread(vscode.write_text, json.dumps(_VSCODE), encoding="utf-8")
    await _add(page, vscode)
    night = page.locator("[data-theme-card='harbour-night']")
    await expect(night).to_contain_text("Harbour Night")
    await expect(night.locator("[data-orb]")).to_have_count(1)
    await night.locator(".theme-card-pick").click(position={"x": 12, "y": 12})
    assert await page.evaluate("localStorage.getItem('dot.themeDark')") == "harbour-night"
    assert await page.evaluate("localStorage.getItem('dot.theme')") == "harbour"
    # The short form is kept written out whole, as a theme file.
    short_form: Path = tmp_path / "moss.json"
    await asyncio.to_thread(short_form.write_text, json.dumps(_SHORT), encoding="utf-8")
    await _add(page, short_form)
    await expect(page.locator("[data-theme-card='moss']")).to_be_visible()
    moss = (await _themes(client))["moss"]
    assert "canvas" not in moss and len(moss["colors"]) == 61 and moss["version"] == 1  # type: ignore[arg-type]
    # A file Dot cannot read is refused with the reason, and nothing is kept.
    wrong: Path = tmp_path / "wrong.json"
    wrong_file: str = json.dumps({"version": 2, "name": "Wrong", "appearance": "light", "colors": {}})
    await asyncio.to_thread(wrong.write_text, wrong_file, encoding="utf-8")
    await _add(page, wrong)
    await expect(page.locator(".toast.error")).to_contain_text("version Dot does not read")
    await expect(cards).to_have_count(_BUILT_IN + 3)
    short = []
    await page.get_by_role("group", name="Colour scheme").get_by_role("button", name="Dark").click()
    short.extend(_short("harbour-night", "dark", await _painted(page)))
    await page.locator("[data-theme-card='moss'] .theme-card-pick").click(position={"x": 12, "y": 12})
    await page.get_by_role("group", name="Colour scheme").get_by_role("button", name="Light").click()
    short.extend(_short("moss", "light", await _painted(page)))
    assert short == [], short
    await page.get_by_role("group", name="Colour scheme").get_by_role("button", name="System").click()

    # Duplicate: a built-in theme copied into the editor, kept as one's own for both schemes.
    grove = page.locator("[data-theme-card='grove']")
    await grove.hover()
    await grove.get_by_role("button", name="Duplicate Grove").click()
    await expect(page.get_by_label("Theme name")).to_have_value("Grove copy")
    await page.get_by_role("button", name="Save theme").click()
    await expect(page.locator("[data-theme-card='grove-copy'] [data-orb]")).to_have_count(2)
    await expect(html).to_have_attribute("data-theme", "grove-copy")
    await page.screenshot(path=str(tmp_path / "appearance-own-1280.png"), full_page=True)

    # Delete: asked first; the scheme it was picked for goes back to Neutral and its file goes.
    copy = page.locator("[data-theme-card='grove-copy']")
    await copy.hover()
    await copy.get_by_role("button", name="Delete Grove copy").click()
    await page.get_by_role("dialog").get_by_role("button", name="Delete").click()
    await expect(copy).to_have_count(0)
    await expect(html).to_have_attribute("data-theme", "neutral")
    assert await page.evaluate("localStorage.getItem('dot.theme')") == "neutral"
    assert not await asyncio.to_thread((themes_dir() / "grove-copy.json").exists)

    # A file put in the folder by hand shows after a reload; one Dot cannot read says why.
    dune: str = json.dumps({"name": "Dune", "appearance": "light", "canvas": "#f5efe3", "accent": "#b4632d"})
    await asyncio.to_thread((themes_dir() / "dune.json").write_text, dune, encoding="utf-8")
    await asyncio.to_thread((themes_dir() / "broken.json").write_text, "{ not json", encoding="utf-8")
    await page.reload()
    await expect(page.locator("[data-theme-card='dune'] [data-orb]")).to_have_count(1)
    await expect(page.locator(".theme-skipped")).to_contain_text("broken.json: Invalid JSON")
    # A new theme does not take the name of a file left out, so the file put there by hand is not written over.
    assert await page.evaluate("import('/js/appearance/own.js').then((own) => own.freeId('broken'))") == "broken-2"
    # On a phone nothing pushes the page wider than the window, the folder's path included.
    # A function, not an expression: Playwright evals an expression again on every frame it polls, which the page's
    # Content-Security-Policy refuses once the first look comes out false.
    narrow: str = "() => document.documentElement.scrollWidth <= window.innerWidth"
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.wait_for_function(narrow)
    await page.screenshot(path=str(tmp_path / "appearance-own-390.png"), full_page=True)
    await page.get_by_role("button", name="Create theme").click()
    await page.wait_for_function(narrow)
    await page.screenshot(path=str(tmp_path / "editor-390.png"), full_page=True)
    await page.get_by_role("button", name="Cancel").click()
    await expect(cards).to_have_count(_BUILT_IN + 4)
    assert errors == [], errors
    await page.close()
