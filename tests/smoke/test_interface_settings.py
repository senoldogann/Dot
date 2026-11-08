"""The interface, the motion and the type (item 127): every setting of Settings › Appearance beside the theme is set
through its control, lands on the page as the stylesheets read it, and is on the page before the first paint after a
reload (main.js held back, so that only boot.js runs); set back to its default, nothing of it is left on the page or
kept in the browser."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

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

TOKEN: str = "interface-test-token"
_KEYS: tuple[str, ...] = (
    "dot.contrast",
    "dot.chatWidth",
    "dot.panelMotion",
    "dot.fontInterface",
    "dot.sizeInterface",
    "dot.weightInterface",
    "dot.fontAnswers",
    "dot.sizeAnswers",
    "dot.weightAnswers",
    "dot.fontComposer",
    "dot.sizeComposer",
    "dot.weightComposer",
    "dot.fontCode",
    "dot.sizeCode",
    "dot.weightCode",
    "dot.smoothing",
    "dot.wrapCode",
)
# What the settings put on the page element: its attributes and the tokens on its style.
_ON_THE_PAGE: str = """() => {
  const root = document.documentElement;
  const flags = ["data-contrast", "data-chat-width", "data-smoothing", "data-wrap-code"];
  const tokens = ["--contrast-base", "--contrast-boost", "--contrast-border-boost", "--motion-panel", "--font",
    "--type-scale", "--weight-text", "--voice", "--answer-size", "--answer-weight", "--composer-font",
    "--composer-size", "--composer-weight", "--mono", "--code-size", "--code-weight"];
  return {
    ...Object.fromEntries(flags.map((name) => [name, root.getAttribute(name)])),
    ...Object.fromEntries(tokens.map((token) => [token, root.style.getPropertyValue(token)])),
  };
}"""
# The sRGB of the colour a token paints, through a probe and a canvas, whatever form the browser computes it in.
_INK: str = """(token) => {
  const probe = document.createElement("span");
  document.body.append(probe);
  probe.style.color = `var(${token})`;
  const painted = getComputedStyle(probe).color;
  probe.remove();
  const context = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  context.fillStyle = "#010203";
  context.fillStyle = painted;
  if (context.fillStyle === "#010203") {
    throw new Error(`the canvas does not take ${painted}`);
  }
  context.fillRect(0, 0, 1, 1);
  return Array.from(context.getImageData(0, 0, 1, 1).data.slice(0, 3));
}"""
# The weights a label, a title and a label drawn with the shorthand `font` are drawn in.
_WEIGHTS: str = """() => {
  const probe = (style) => {
    const node = document.createElement("span");
    node.style.cssText = style;
    document.body.append(node);
    const weight = getComputedStyle(node).fontWeight;
    node.remove();
    return weight;
  };
  return [
    probe("font-weight: var(--weight-label)"),
    probe("font-weight: var(--weight-title)"),
    probe("font: var(--weight-label) var(--text-xs) / var(--leading-xs) var(--font)"),
  ];
}"""
# An answer's heading, inline code and code in bold, as the page draws them.
_ANSWER: str = """() => {
  const answer = document.createElement("div");
  answer.className = "prose";
  answer.innerHTML = "<h1>Title</h1><h3>Part</h3><p><code>x</code> and <strong><code>y</code></strong></p>";
  document.body.append(answer);
  const style = (selector) => getComputedStyle(answer.querySelector(selector));
  const drawn = {
    h1: style("h1").fontSize,
    h1_weight: style("h1").fontWeight,
    h3_weight: style("h3").fontWeight,
    code: style("p > code").fontSize,
    code_weight: style("p > code").fontWeight,
    bold_code: style("strong code").fontWeight,
  };
  answer.remove();
  return drawn;
}"""
_STYLE: str = "([selector, property]) => getComputedStyle(document.querySelector(selector)).getPropertyValue(property)"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
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


def _luminance(rgb: list[int]) -> float:
    def channel(value: int) -> float:
        c: float = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(value) for value in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


async def _contrast(page: Page, token: str) -> float:
    """The contrast of `token` on the page's background, as the page paints them."""
    ink: list[int] = await page.evaluate(_INK, token)
    ground: list[int] = await page.evaluate(_INK, "--bg")
    lighter, darker = sorted((_luminance(ink), _luminance(ground)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


async def _style(page: Page, selector: str, prop: str) -> str:
    value: str = await page.evaluate(_STYLE, [selector, prop])
    return value


async def _on_the_page(page: Page) -> dict[str, str | None]:
    state: dict[str, str | None] = await page.evaluate(_ON_THE_PAGE)
    return state


async def _kept(page: Page) -> dict[str, str | None]:
    kept: dict[str, str | None] = await page.evaluate(
        "(keys) => Object.fromEntries(keys.map((key) => [key, localStorage.getItem(key)]))", list(_KEYS)
    )
    return kept


async def test_each_setting_lands_on_the_page_and_is_there_before_the_first_paint(
    browser: Browser, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    port: int | None = client.base_url.port
    assert port is not None
    base: str = f"http://{client.base_url.host}:{port}"
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
    # The opening has played in this tab, so that nothing covers the page.
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(client.base_url.host, port, TOKEN))
    await page.goto(f"{base}/#/settings/appearance")
    await expect(page.get_by_role("slider", name="Contrast")).to_have_value("100")
    untouched: dict[str, str | None] = await _on_the_page(page)
    assert all(value in (None, "") for value in untouched.values()), untouched
    text_at_100: float = await _contrast(page, "--text")
    muted_at_100: float = await _contrast(page, "--muted")

    # The contrast: over 100 the text moves toward black, under it toward the page.
    contrast = page.get_by_role("slider", name="Contrast")
    await contrast.fill("150")
    state: dict[str, str | None] = await _on_the_page(page)
    assert (state["data-contrast"], state["--contrast-base"], state["--contrast-boost"]) == ("", "100%", "50%")
    assert state["--contrast-border-boost"] == "12.5%"
    assert await _contrast(page, "--text") > text_at_100
    assert await _contrast(page, "--muted") > muted_at_100
    await contrast.fill("60")
    assert (await _on_the_page(page))["--contrast-base"] == "60%"
    assert await _contrast(page, "--muted") < muted_at_100
    await contrast.fill("150")

    # The chat's width and the slide of the panels.
    await page.get_by_role("group", name="Chat width").get_by_role("button", name="Wide").click()
    await expect(page.locator("html")).to_have_attribute("data-chat-width", "wide")
    panels = page.get_by_role("slider", name="Panel animation")
    await panels.fill("0")
    assert await _style(page, ".shell", "transition-duration") == "0s"
    await panels.fill("400")
    assert await _style(page, ".shell", "transition-duration") == "0.4s"

    # The interface's family: the system's by default (item 1057), Inter, then one typed; what a family cannot hold is
    # refused and not kept.
    interface_font = page.get_by_role("combobox", name="Interface font")
    assert (await _style(page, "body", "font-family")).startswith("system-ui")
    await interface_font.select_option("inter")
    assert (await _style(page, "body", "font-family")).startswith("Inter")
    await interface_font.select_option("another")
    typed = page.get_by_role("textbox", name="Interface font: another family")
    await typed.fill("IBM Plex Sans")
    await typed.press("Tab")
    assert (await _style(page, "body", "font-family")).startswith('"IBM Plex Sans", system-ui')
    await typed.fill("Plex; } body {")
    await typed.press("Tab")
    await expect(typed).to_have_attribute("aria-invalid", "true")
    assert (await _kept(page))["dot.fontInterface"] == "IBM Plex Sans"
    await page.get_by_role("slider", name="Interface size").fill("16")
    assert await _style(page, "body", "font-size") == "16px"
    await page.get_by_role("combobox", name="Interface weight").select_option("300")
    assert await _style(page, "body", "font-weight") == "300"
    # A label and a title move with it, a step and two over the text, the shorthand `font` of a label too.
    assert await page.evaluate(_WEIGHTS) == ["400", "500", "400"]

    # Every face has the same three (item 1062): its family, its weight and its size.
    answers = ".type-preview[data-preview='answers']"
    # Until set, the answers' size follows the interface's: a message's 15 px scaled by 16 over 14.
    await expect(page.get_by_role("slider", name="Answers size")).to_have_value("17")
    await page.get_by_role("combobox", name="Answers font").select_option("serif")
    assert (await _style(page, answers, "font-family")).startswith("ui-serif")
    await page.get_by_role("combobox", name="Answers weight").select_option("500")
    await page.get_by_role("slider", name="Answers size").fill("18")
    assert await _style(page, answers, "font-weight") == "500"
    assert await _style(page, answers, "font-size") == "18px"
    # An answer's first heading a step over its text and its inline code a step under, as 17 and 13 are to 15; code has
    # the code's weight, but in bold it keeps the bold's; its headings are a step and two over its weight (500).
    assert await page.evaluate(_ANSWER) == {
        "h1": "20.4px",
        "h1_weight": "700",
        "h3_weight": "600",
        "code": "15.6px",
        "code_weight": "400",
        "bold_code": "700",
    }
    # A size kept stays when the interface's brings the default to it (17 px: 15 × 17 / 14 is 18), and after it.
    await page.get_by_role("slider", name="Interface size").fill("17")
    await page.reload()
    await expect(page.get_by_role("slider", name="Answers size")).to_have_value("18")
    assert await _style(page, answers, "font-size") == "18px"
    await page.get_by_role("slider", name="Interface size").fill("13")
    assert await _style(page, answers, "font-size") == "18px"
    await page.get_by_role("slider", name="Interface size").fill("16")
    await page.get_by_role("combobox", name="Code font").select_option("menlo")
    await page.get_by_role("combobox", name="Code weight").select_option("500")
    await page.get_by_role("slider", name="Code size").fill("15")
    code = ".type-preview[data-preview='code']"
    assert (await _style(page, code, "font-family")).startswith("Menlo")
    assert await _style(page, code, "font-size") == "15px"
    assert await _style(page, code, "font-weight") == "500"
    await page.get_by_role("combobox", name="Message box font").select_option("geist-mono")
    await page.get_by_role("combobox", name="Message box weight").select_option("600")
    await page.get_by_role("slider", name="Message box size").fill("18")
    composer = ".type-preview[data-preview='composer']"
    assert (await _style(page, composer, "font-family")).startswith('"Geist Mono"')
    assert await _style(page, composer, "font-size") == "18px"
    assert await _style(page, composer, "font-weight") == "600"
    await page.get_by_role("switch", name="Font smoothing").click()
    assert await _style(page, "body", "-webkit-font-smoothing") == "auto"
    await page.get_by_role("switch", name="Wrap long lines of code").click()
    assert await _style(page, code, "white-space") == "pre-wrap"
    chosen: dict[str, str | None] = await _on_the_page(page)
    assert chosen == {
        "data-contrast": "",
        "data-chat-width": "wide",
        "data-smoothing": "off",
        "data-wrap-code": "",
        "--contrast-base": "100%",
        "--contrast-boost": "50%",
        "--contrast-border-boost": "12.5%",
        "--motion-panel": "400ms",
        "--font": '"IBM Plex Sans", var(--family-system)',
        "--type-scale": str(16 / 14),
        "--weight-text": "300",
        "--voice": "var(--family-serif)",
        "--answer-size": "18px",
        "--answer-weight": "500",
        "--composer-font": "var(--family-geist-mono)",
        "--composer-size": "18px",
        "--composer-weight": "600",
        "--mono": "var(--family-menlo)",
        "--code-size": "15px",
        "--code-weight": "500",
    }
    await page.screenshot(path=str(tmp_path / "appearance-set.png"), full_page=True)

    # Without main.js only boot.js runs: what it puts on the page from this browser's keys is the first paint.
    await page.route("**/js/main.js", lambda route: route.abort())
    await page.reload()
    assert await _on_the_page(page) == chosen
    assert await _style(page, "body", "font-size") == "16px"
    await page.unroute("**/js/main.js")
    await page.reload()
    await expect(page.get_by_role("slider", name="Contrast")).to_have_value("150")
    assert await _on_the_page(page) == chosen
    await expect(page.get_by_role("combobox", name="Interface font")).to_have_value("another")
    await expect(page.get_by_role("textbox", name="Interface font: another family")).to_have_value("IBM Plex Sans")
    await expect(page.get_by_role("switch", name="Font smoothing")).to_have_attribute("aria-checked", "false")

    # The home reads at the chat's width, the composer in its own family and size.
    await page.goto(f"{base}/#/runs")
    await expect(page.locator(".home .composer .textarea")).to_be_visible()
    assert await _style(page, ".home", "max-width") == "1152px"
    assert await _style(page, ".home .composer .textarea", "font-size") == "18px"
    assert (await _style(page, ".home .composer .textarea", "font-family")).startswith('"Geist Mono"')
    assert await _style(page, ".home .composer .textarea", "font-weight") == "600"
    await expect(page.locator("main.page")).to_have_css("opacity", "1")
    await page.screenshot(path=str(tmp_path / "home-set.png"))

    # Back to the defaults: nothing on the page element, nothing kept.
    await page.goto(f"{base}/#/settings/appearance")
    await page.get_by_role("slider", name="Contrast").fill("100")
    await page.get_by_role("group", name="Chat width").get_by_role("button", name="Comfortable").click()
    await page.get_by_role("slider", name="Panel animation").fill("200")
    await page.get_by_role("combobox", name="Interface font").select_option("")
    await page.get_by_role("slider", name="Interface size").fill("14")
    await page.get_by_role("combobox", name="Interface weight").select_option("400")
    await page.get_by_role("combobox", name="Answers font").select_option("")
    await page.get_by_role("combobox", name="Answers weight").select_option("400")
    await page.get_by_role("slider", name="Answers size").fill("15")
    await page.get_by_role("combobox", name="Message box font").select_option("")
    await page.get_by_role("combobox", name="Message box weight").select_option("400")
    await page.get_by_role("slider", name="Message box size").fill("15")
    await page.get_by_role("combobox", name="Code font").select_option("")
    await page.get_by_role("combobox", name="Code weight").select_option("400")
    await page.get_by_role("slider", name="Code size").fill("13")
    await page.get_by_role("switch", name="Font smoothing").click()
    await page.get_by_role("switch", name="Wrap long lines of code").click()
    assert await _on_the_page(page) == untouched
    assert all(value is None for value in (await _kept(page)).values())
    assert await _contrast(page, "--text") == pytest.approx(text_at_100)

    # What a browser kept before every face offered the same families reads as what it is now: the answers' "interface"
    # the interface's, the message box's "mono" the code's, before the first paint and on the page.
    await page.evaluate(
        "localStorage.setItem('dot.fontAnswers', 'interface'); localStorage.setItem('dot.fontComposer', 'mono')"
    )
    await page.route("**/js/main.js", lambda route: route.abort())
    await page.reload()
    kept = await _on_the_page(page)
    assert (kept["--voice"], kept["--composer-font"]) == ("var(--family-interface)", "var(--family-mono)")
    await page.unroute("**/js/main.js")
    await page.reload()
    await expect(page.get_by_role("combobox", name="Answers font")).to_have_value("interface")
    await expect(page.get_by_role("combobox", name="Message box font")).to_have_value("mono")
    assert (await _on_the_page(page))["--composer-font"] == "var(--family-mono)"
    await page.close()


async def test_the_controls_page_draws_the_range(browser: Browser, client: httpx.AsyncClient) -> None:
    port: int | None = client.base_url.port
    assert port is not None
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme="light")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(client.base_url.host, port, TOKEN))
    await page.goto(f"http://{client.base_url.host}:{port}/#/controls")
    sample = page.locator("[data-control='range']")
    await expect(sample.get_by_role("slider", name="Range")).to_have_value("40")
    await sample.get_by_role("slider", name="Range").fill("75")
    await expect(sample.locator(".range-value")).to_have_text("75%")
    await page.close()
