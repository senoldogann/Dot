"""The links of a message as plain words (item 1346).

A link in an answer or in the owner's message has no border, no fill and no icon, is no longer the text's own
colour, is underlined only under the pointer or with the focus, and keeps the focus ring; no link in the panel or the
messages is drawn as a chip. Served by a real server over runs seeded in a real store, in Chrome.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord, RunStatus, ScheduleOptions, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "message-links-token"
_ANSWER: str = (
    "The pull request is [#1001 on GitHub](https://github.com/example/dot/pull/1001) and the run is at "
    "https://github.com/example/dot/actions/runs/123, with the guide at [the thesis guide](https://www.oulu.fi/guide)."
)
_ASKED: str = "Please check https://example.com/notes and [the board](https://example.com/board) again."
_PLAIN: ScheduleOptions = ScheduleOptions(
    model=None,
    effort=None,
    speed=None,
    approvals=None,
    goal_mode=None,
    timezone=None,
    history_runs=None,
    same_chat=True,
    stop_after_failures=None,
    notify=None,
    agent_id=None,
)
_LOOKS: tuple[tuple[Literal["chromium", "webkit"], int, int, Literal["light", "dark"]], ...] = (
    ("chromium", 1440, 900, "light"),
    ("chromium", 1440, 900, "dark"),
    ("webkit", 390, 844, "light"),
    ("webkit", 390, 844, "dark"),
)
_NONE: str = "rgba(0, 0, 0, 0)"


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


async def _run(
    runtime: Runtime, thread_id: str, goal: str, parent: str | None, status: RunStatus, result: str
) -> RunRecord:
    run: RunRecord = await runtime.store.create_run(
        thread_id,
        goal,
        "task",
        SCRIPTED_MODEL,
        parent,
        "test" if parent is None else "delegate",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    await runtime.store.end_run(run.id, status, result, {"status": status, "result": result, "needs": ""})
    await asyncio.sleep(0.01)
    return run


async def _seed(runtime: Runtime) -> RunRecord:
    """A chat whose answer holds links, with two subagents that ended and a schedule of its own."""
    chat: ThreadRecord = await runtime.store.create_thread("Ship the dictation work", "test")
    run: RunRecord = await _run(runtime, chat.id, _ASKED, None, "done", _ANSWER)
    for goal, status in (("Find the invoices of March", "done"), ("Check the totals against the bank", "failed")):
        thread: ThreadRecord = await runtime.store.create_thread(goal, "delegate")
        await _run(runtime, thread.id, goal, run.id, status, "Done.")
    at: float = time.time() + 6 * 3600
    await runtime.store.add_schedule(
        "Loop: check the build", "0 9 * * *", None, "check the build", "task", at, _PLAIN, chat.id, True
    )
    return run


async def _page(
    browser: Browser, address: str, run_id: str, width: int, height: int, scheme: Literal["light", "dark"], touch: bool
) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": height},
        color_scheme=scheme,
        reduced_motion="reduce",
        has_touch=touch,
        is_mobile=touch,
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".prose a.msg-link").first).to_be_visible()
    return page


async def _style(node: Locator, *names: str) -> dict[str, str]:
    return await node.evaluate(
        "(el, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(el).getPropertyValue(name)]))",
        list(names),
    )


async def test_a_link_in_a_message_has_no_chip_reads_a_shade_brighter_and_is_underlined_only_under_the_pointer(
    runtime: Runtime, address: str
) -> None:
    run: RunRecord = await _seed(runtime)
    async with async_playwright() as playwright:
        for engine, width, height, scheme in _LOOKS[:2]:
            browser: Browser = await getattr(playwright, engine).launch(headless=True)
            try:
                page: Page = await _page(browser, address, run.id, width, height, scheme, False)
                links: Locator = page.locator(".prose a.msg-link")
                # The owner's two and the answer's three, markdown links and autolinks.
                await expect(links).to_have_count(5)
                assert await page.locator(".link-pill, .link-pill-icon, .link-pill-fallback").count() == 0
                assert await page.locator(".prose a img, .prose a svg").count() == 0
                body: str = (await _style(page.locator(".bubble.say > .prose, .answer > .prose").first, "color"))[
                    "color"
                ]
                for index in range(5):
                    style: dict[str, str] = await _style(
                        links.nth(index),
                        "border-top-width",
                        "border-bottom-width",
                        "border-left-width",
                        "border-right-width",
                        "background-color",
                        "background-image",
                        "box-shadow",
                        "padding-left",
                        "display",
                        "text-decoration-line",
                        "color",
                    )
                    widths: list[str] = [style[f"border-{side}-width"] for side in ("top", "bottom", "left", "right")]
                    assert widths == ["0px"] * 4, style
                    assert style["background-color"] == _NONE and style["background-image"] == "none", style
                    assert style["box-shadow"] == "none" and style["padding-left"] == "0px", style
                    assert style["display"] == "inline", style
                    assert style["text-decoration-line"] == "none", style
                    assert style["color"] != body, (style["color"], body)
                # The pointer underlines it, and leaving it takes the line back.
                await links.nth(0).hover()
                assert (await _style(links.nth(0), "text-decoration-line"))["text-decoration-line"] == "underline"
                await page.mouse.move(2, 2)
                assert (await _style(links.nth(0), "text-decoration-line"))["text-decoration-line"] == "none"
                # The keyboard's focus is drawn: the page's ring, and the line.
                await links.nth(0).focus()
                await page.keyboard.press("Shift+Tab")
                await page.keyboard.press("Tab")
                ring: dict[str, str] = await _style(
                    links.nth(0), "outline-style", "outline-width", "text-decoration-line"
                )
                assert ring["outline-style"] == "solid" and ring["outline-width"] == "2px", ring
                assert ring["text-decoration-line"] == "underline", ring
                # It opens in a new window and says where; no icon of a site is fetched for it.
                assert await links.nth(0).get_attribute("target") == "_blank"
                assert "noopener" in (await links.nth(0).get_attribute("rel") or "")
                assert await links.nth(0).get_attribute("title") == "https://example.com/notes"
                # The owner's two come first (an autolink, which names its site, and a markdown link), then Dot's.
                await expect(links).to_have_text(
                    ["example.com", "the board", "#1001 on GitHub", "github.com", "the thesis guide"]
                )
                # What else is a link in the messages and in the panel wears no frame and no fill at rest.
                await page.get_by_role("button", name="Task details").click()
                await expect(page.locator(".inspector")).to_be_visible()
                chips: list[str] = await page.evaluate(
                    """() => [...document.querySelectorAll('.prose a, .inspector a, .timeline a')]
                        .filter((node) => !node.matches('.btn') && node.offsetParent !== null)
                        .map((node) => [node, getComputedStyle(node)])
                        .filter(([, s]) => parseFloat(s.borderTopWidth) > 0 || s.backgroundColor !== 'rgba(0, 0, 0, 0)')
                        .map(([node]) => node.className || node.outerHTML.slice(0, 60))"""
                )
                assert chips == [], chips
                await page.close()
            finally:
                await browser.close()
