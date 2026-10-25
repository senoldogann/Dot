"""Pages a run shows in the conversation (item 193), on the pages of a daemon in Chrome. A run's page sits in its card,
in the colours of the theme on screen and as tall as it is, and takes the other scheme when the system's changes. Its
scripts reach neither the API nor the page around it nor the UI's storage, framed or opened full size; a link clicked in
it opens outside, and a script cannot open one by itself; a page that loads another in its frame is closed and shown
again on request. Wide and on a phone, light and dark.

No network and no model service is used: the scripted model plays the runs of a daemon served by a real server, and
the address the page links to is answered by the browser itself.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import (
    APIResponse,
    Browser,
    BrowserContext,
    ElementHandle,
    Frame,
    FrameLocator,
    Locator,
    Page,
    Route,
    async_playwright,
    expect,
)

from dotagent.agent.manager import RunManager
from dotagent.agent.pages import PAGE_EVENT
from dotagent.config import load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.pages import SHOW_PAGE_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "shown-pages-test-token"
WAIT_S: float = 30.0
TITLE: str = "Sales by month"
LINK: str = "https://example.com/report"
UNASKED: str = "https://example.com/unasked"
ELSEWHERE: str = "https://example.com/elsewhere"
OUT_OF_REACH: str = "out of reach"
# A chart of three bars in the conversation's colours, which says what its scripts could reach: the API, the page
# around it (the page that opened it, full size) and the UI's storage. Framed, it asks on load to open a link no
# click asked for; a button of it loads another page in its place.
PAGE: str = f"""<!doctype html><html><head><title>{TITLE}</title><style>
.bars {{ display: flex; gap: 8px; align-items: end; height: 240px; }}
.bar {{ flex: 1; background: var(--chart-2); border-radius: var(--radius) var(--radius) 0 0; }}
p {{ margin: 8px 0; }}
</style></head><body>
<p id="ink" style="color: var(--foreground)">{TITLE}</p>
<div class="bars"><div class="bar" style="height: 40%"></div><div class="bar" style="height: 70%"></div>
<div class="bar" style="height: 100%"></div></div>
<p>API: <span id="api">…</span>. Page around: <span id="around">…</span>. Storage: <span id="storage">…</span>.
Unasked link: <span id="unasked">…</span>.</p>
<p><a id="link" href="{LINK}">The report</a> <button id="away" type="button">Somewhere else</button></p>
<script>
const say = (id, text) => {{ document.getElementById(id).textContent = text; }};
fetch("/api/status").then(() => say("api", "reached"), () => say("api", "{OUT_OF_REACH}"));
const around = window.parent === window ? window.opener : window.parent;
try {{ say("around", around !== null && around.document ? "reached" : "{OUT_OF_REACH}"); }}
catch {{ say("around", "{OUT_OF_REACH}"); }}
try {{ localStorage.getItem("dot.scheme"); say("storage", "reached"); }} catch {{ say("storage", "{OUT_OF_REACH}"); }}
window.addEventListener("message", (event) => {{
  if (event.data?.id === "unasked") say("unasked", event.data.error ? "refused" : "opened");
}});
document.getElementById("away").addEventListener("click", () => {{ location.href = "{ELSEWHERE}"; }});
if (window.parent !== window) {{
  window.parent.postMessage(
    {{ jsonrpc: "2.0", id: "unasked", method: "ui/open-link", params: {{ url: "{UNASKED}" }} }}, "*");
}} else {{
  say("unasked", "refused");
}}
</script></body></html>"""
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_OTHER: dict[Literal["light", "dark"], Literal["light", "dark"]] = {"light": "dark", "dark": "light"}
_SHOW: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=SHOW_PAGE_TOOL, arguments={"title": TITLE, "html": PAGE}),)
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "The chart is above.", "success": True}),)
)
# A page longer than the 2,000 pixels a frame stops at, and one whose height follows its frame's.
LONG: str = "A long page"
GROWING: str = "A page that grows"
_TWO: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(
        ScriptedCall(tool=SHOW_PAGE_TOOL, arguments={"title": LONG, "html": '<div style="height: 3000px">long</div>'}),
        ScriptedCall(
            tool=SHOW_PAGE_TOOL,
            arguments={"title": GROWING, "html": '<div style="height: 100vh">tall</div><p>more</p>'},
        ),
    ),
)
_TURNS: list[ScriptedTurn] = [_SHOW, _FINISH] * len(_SHOTS) + [_TWO, _FINISH]
# The text colour of the UI as the scheme on screen draws it.
_TEXT_COLOUR: str = """() => {
  const probe = document.createElement("span");
  probe.style.color = "var(--text)";
  document.body.append(probe);
  const colour = getComputedStyle(probe).color;
  probe.remove();
  return colour;
}"""
_INK: str = "() => getComputedStyle(document.getElementById('ink')).color"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _daemon(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """The daemon in the home of the environment, whose model plays ``_TURNS``, served at the address it yields."""
    runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
    try:
        await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _TURNS))
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
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


async def _shown(runtime: Runtime, manager: RunManager, goal: str) -> tuple[RunRecord, str]:
    """A run started for ``goal`` that shows the page, once it has ended, and the id of the page it showed."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
    shown: list[EventRecord] = await runtime.store.events_of_kind(run.id, PAGE_EVENT, 5)
    assert [event.data["title"] for event in shown] == [TITLE]
    return run, str(shown[0].data["page_id"])


async def _settles(read: Callable[[], Awaitable[object]], expected: object) -> None:
    """Wait until ``read()`` gives ``expected``."""
    deadline: float = time.monotonic() + WAIT_S
    while (seen := await read()) != expected:
        assert time.monotonic() < deadline, f"{seen!r} is still not {expected!r}"
        await asyncio.sleep(0.05)


async def _address_of(page: Page) -> str:
    return page.url


async def _answer_locally(route: Route) -> None:
    await route.fulfill(status=200, content_type="text/plain", body="The report.")


async def _check_reach(page: FrameLocator | Page) -> None:
    for name in ("api", "around", "storage"):
        await expect(page.locator(f"#{name}")).to_have_text(OUT_OF_REACH)
    await expect(page.locator("#unasked")).to_have_text("refused")


async def _fits(framing: Locator, frame: Frame) -> bool:
    """Whether the frame is as tall as the page in it, a page taller than the frame's own first height."""
    shown: float = await framing.evaluate("(node) => node.clientHeight")
    tall: float = await frame.evaluate("() => document.documentElement.getBoundingClientRect().height")
    return shown > 240 and abs(shown - tall) <= 1


async def _one_size(
    browser: Browser,
    runtime: Runtime,
    manager: RunManager,
    address: str,
    size: tuple[int, Literal["light", "dark"]],
    shots: Path,
) -> None:
    """A run shows the page, and the run's page in Chrome at ``size`` (a width and a scheme) shows it."""
    width, scheme = size
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    context: BrowserContext = page.context
    await context.route("https://example.com/**", _answer_locally)
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    run, page_id = await _shown(runtime, manager, f"Chart the sales ({width}, {scheme})")
    await page.goto(f"{address}/#/runs/{run.id}")

    # Part of the answer (item 651): no title over it, which names it for a screen reader alone, and no box round it.
    card: Locator = page.locator(".shown-page")
    await expect(card).to_have_attribute("aria-label", TITLE)
    await expect(card.locator("figcaption")).to_have_count(0)
    await expect(card.locator("iframe.shown-page-frame")).to_have_css("border-top-width", "0px")
    framing: Locator = card.locator("iframe.shown-page-frame")
    await expect(framing).to_have_attribute("sandbox", "allow-scripts")
    await card.scroll_into_view_if_needed()
    inside: FrameLocator = card.frame_locator("iframe.shown-page-frame")
    await _check_reach(inside)
    handle: ElementHandle | None = await framing.element_handle()
    assert handle is not None
    frame: Frame | None = await handle.content_frame()
    assert frame is not None

    # The frame is as tall as the page, and the page is in the colours of the scheme on screen; this browser keeps the
    # height for the next time the page is shown at this width.
    await _settles(partial(_fits, framing, frame), True)
    fitted: str = await framing.evaluate("(node) => node.style.height")
    # Kept once the page has stood still a moment, not at every step of its growth.
    kept_of: str = (
        "(id) => Object.values(JSON.parse(localStorage.getItem('dot.pageHeights') ?? '{}')[id] ?? {})"
        ".map((h) => `${h}px`)"
    )
    await _settles(partial(page.evaluate, f"({kept_of})('{page_id}').includes('{fitted}')"), True)
    await _settles(partial(frame.evaluate, _INK), await page.evaluate(_TEXT_COLOUR))
    await card.scroll_into_view_if_needed()
    await page.screenshot(path=str(shots / f"page-{width}-{scheme}.png"))

    # The system's scheme changes: the page takes the other colours.
    await page.emulate_media(color_scheme=_OTHER[scheme])
    other: str = await page.evaluate(_TEXT_COLOUR)
    await _settles(partial(frame.evaluate, _INK), other)

    # A link clicked in the page opens outside it; the one the page asked for by itself did not.
    async with context.expect_page() as opened:
        await inside.locator("#link").click()
    report: Page = await opened.value
    assert report.url == LINK
    await report.close()
    assert len(context.pages) == 1

    # Full size, the page is in the theme on screen and still reaches nothing of Dot's; the theme is taken out of
    # the address once the page has it.
    async with context.expect_page() as opened:
        await card.locator(".shown-page-open").click()
    full: Page = await opened.value
    await full.wait_for_load_state()
    assert full.url.startswith(f"{address}/pages/{page_id}")
    await _check_reach(full)
    await _settles(partial(full.evaluate, _INK), other)
    await _settles(partial(_address_of, full), f"{address}/pages/{page_id}")

    # Full screen, in a dialog of the UI: the page in the theme on screen, reaching nothing; Escape closes it.
    await card.hover()
    await card.get_by_role("button", name="Full screen").click()
    dialog: Locator = page.get_by_role("dialog", name=TITLE)
    await expect(dialog).to_be_visible()
    await expect(dialog.frame_locator("iframe.shown-page-frame").locator("#ink")).to_have_text(TITLE)
    await _check_reach(dialog.frame_locator("iframe.shown-page-frame"))
    await page.screenshot(path=str(shots / f"full-{width}-{_OTHER[scheme]}.png"))
    await page.keyboard.press("Escape")
    await expect(dialog).to_have_count(0)

    # A page that loads another in its frame is closed, and the run's page can be shown again.
    await inside.locator("#away").click()
    left: Locator = card.locator(".shown-page-left")
    await expect(left).to_contain_text("loaded another page in its place")
    await expect(card.locator("iframe")).to_have_count(0)
    await page.screenshot(path=str(shots / f"left-{width}-{_OTHER[scheme]}.png"))
    await left.get_by_role("button", name="Show it again").click()
    await expect(card.frame_locator("iframe.shown-page-frame").locator("#ink")).to_have_text(TITLE)
    await context.close()


async def _seen_height(frame: Locator) -> int:
    """The height of ``frame``, kept in sight: Chrome slows the pages of frames out of sight, and a page that grows says
    so only while it is drawn."""
    await frame.scroll_into_view_if_needed()
    height: int = await frame.evaluate("(node) => node.clientHeight")
    return height


async def _long_and_growing(browser: Browser, runtime: Runtime, manager: RunManager, address: str) -> None:
    """A page of 3,000 pixels and one whose height follows its frame's both stop at 2,000 and scroll inside it."""
    run: RunRecord = await manager.start(
        "Two pages", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    async with asyncio.timeout(WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/runs/{run.id}")
    long: Locator = page.locator(f".shown-page[aria-label='{LONG}'] iframe")
    growing: Locator = page.locator(f".shown-page[aria-label='{GROWING}'] iframe")
    await _settles(partial(_seen_height, long), 2000)
    await _settles(partial(_seen_height, growing), 2000)
    await page.wait_for_timeout(1000)
    assert await _seen_height(growing) == 2000
    await page.context.close()


@pytest.mark.browser
async def test_a_page_a_run_shows_is_drawn_in_the_conversation_and_reaches_nothing_of_dots(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """The page sits in its card in the theme on screen and as tall as it is, and follows the system's scheme. Its
    scripts reach nothing of Dot's, in the card or full size; a link clicked in it opens, and one no click asked for
    does not; once it loads another page in its frame, the card closes it and shows it again on request. An id that
    names no page is not found. Before, a run had no way to show a page."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for size in _SHOTS:
                    await _one_size(browser, runtime, manager, address, size, tmp_path)
                await _long_and_growing(browser, runtime, manager, address)
                gone: APIResponse = await (await browser.new_page()).request.get(f"{address}/pages/page_nothing")
                assert gone.status == 404
                assert gone.headers["content-security-policy"].startswith("sandbox allow-scripts;")
            finally:
                await browser.close()
