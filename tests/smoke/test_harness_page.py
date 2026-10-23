"""Settings › Self-improvement (the harness) in a real browser (item 393, its words item 503, its look item 566): with
no version the page says Dot's built-in instructions alone are in use; a version written on the page and put in use
shows in use and in the list; setting it aside leaves the built-in instructions again; the version in use reads as two
lists, each version opens its texts and notes, the nightly learning says when it runs and lists its nights; on a
phone the settings list has the row."""

import asyncio
import dataclasses
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
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

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "harness-page-token"
HABIT: str = "Read a file before you change it."
# Whether each grid of the page keeps its columns inside it, and itself inside the screen (item 527): a column with a
# fixed least width reaches past a narrow phone's edge, inside a pane that scrolls on its own.
FITS: str = """() => [...document.querySelectorAll(".stat-card, .follow-columns, .harness-line")].every(
  (grid) => grid.scrollWidth <= grid.clientWidth && grid.getBoundingClientRect().right <= window.innerWidth
)"""


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


@asynccontextmanager
async def _serving(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
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
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    async with _serving(runtime) as http:
        yield http


@pytest_asyncio.fixture(loop_scope="module")
async def nightly_client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    """A server over the same home whose config has the nightly learning at 04:00."""
    harness = runtime.config.harness.model_copy(update={"step_time": "04:00"})
    config = runtime.config.model_copy(update={"harness": harness})
    async with _serving(dataclasses.replace(runtime, config=config)) as http:
        yield http


@pytest_asyncio.fixture(loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


def _address(client: httpx.AsyncClient) -> tuple[str, int]:
    return "127.0.0.1", int(str(client.base_url).rpartition(":")[2].rstrip("/"))


async def _open(browser: Browser, client: httpx.AsyncClient, width: int) -> Page:
    page: Page = await browser.new_page(viewport={"width": width, "height": 900})
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    host, port = _address(client)
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/settings/harness")
    return page


def _section(page: Page, name: str) -> Locator:
    """The section of the page whose heading is `name`."""
    return page.locator("section.set-section").filter(has=page.get_by_role("heading", name=name))


async def test_a_version_written_on_the_page_is_in_use_until_it_is_set_aside(
    browser: Browser, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    page: Page = await _open(browser, client, 1280)
    try:
        # The page says what it is in plain words (item 503).
        await expect(
            page.get_by_text("Dot learns from its own runs how to work better on this Mac.", exact=False)
        ).to_be_visible()
        await expect(page.get_by_text("Dot's built-in instructions alone: no version is in use.")).to_be_visible()
        await expect(page.get_by_text("No version yet")).to_be_visible()
        await expect(page.get_by_text("Nothing learned yet")).to_be_visible()
        # The overview leads the page (item 527): the instructions in use, the versions, the nights and the lessons.
        overview = page.get_by_role("group", name="Overview")
        await expect(overview.locator(".stat-tile")).to_have_count(4)
        await expect(overview).to_contain_text("Built-in")
        await expect(overview).to_contain_text("The nightly learning is off")
        # The form says what goes in it (item 412): that it is optional, what each field takes, with examples; it
        # stays shut until the owner asks for it, with the button at the head of Versions (item 566).
        write = page.locator(".harness-form")
        await expect(write).to_be_hidden()
        await expect(page.get_by_role("textbox", name="Habits")).to_have_count(0)
        await page.get_by_role("button", name="Write your own").click()
        await expect(write.locator(".harness-intro")).to_contain_text("Optional: you need not write anything here.")
        await expect(write.locator(".harness-intro")).to_contain_text(
            "Once a time of day is set for the nightly learning"
        )
        assert "For example:" in (await page.get_by_role("textbox", name="Habits").get_attribute("placeholder") or "")
        await write.get_by_role("button", name="Start from an example").click()
        await expect(page.get_by_role("textbox", name="Habits")).to_have_value(re.compile(r"^- Read a file whole"))
        await expect(page.get_by_role("textbox", name="Summary")).not_to_have_value("")
        await page.get_by_role("textbox", name="Habits").fill(f"- {HABIT}")
        await page.get_by_role("textbox", name="Summary").fill("Read first")
        await page.get_by_role("button", name="Save and use").click()
        in_use = _section(page, "What Dot follows now")
        await expect(in_use.get_by_text(HABIT)).to_be_visible()
        await expect(in_use.locator(".badge", has_text="In use")).to_be_visible()
        row = page.locator("[data-version]")
        await expect(row).to_have_count(1)
        await expect(row.locator(".badge")).to_have_text("In use")
        await expect(row.get_by_text("Read first")).to_be_visible()
        await expect(overview).to_contain_text("A version")
        await expect(overview).to_contain_text("1 in use")
        # The API says the same.
        view: dict[str, object] = (await client.get("/api/harness")).json()
        assert view["adopted"] is not None
        # Setting the version in use aside, from What Dot follows now, leaves the built-in instructions, after the
        # question is answered.
        await in_use.get_by_role("button", name="Set aside").click()
        await page.get_by_role("dialog").get_by_role("button", name="Set aside").click()
        await expect(page.get_by_text("Dot's built-in instructions alone: no version is in use.")).to_be_visible()
        await expect(row.locator(".badge")).to_have_text("Set aside")
        await row.get_by_role("button", name="Use", exact=True).click()
        await expect(in_use.get_by_text(HABIT)).to_be_visible()
        await expect(page.get_by_text("every night at", exact=False)).to_have_count(0)
        await expect(page.get_by_text("Set a time of day in the config file", exact=False)).to_be_visible()
        # The button that opened the form shuts it again.
        await page.get_by_role("button", name="Close").click()
        await expect(write).to_be_hidden()
        await page.screenshot(path=tmp_path / "harness-1280.png", full_page=True)
    finally:
        await page.close()


async def test_the_phone_lists_self_improvement_among_how_dot_works(
    browser: Browser, client: httpx.AsyncClient, tmp_path: Path
) -> None:
    # A version in use, so that its two texts stand on the page when its width is measured.
    written: httpx.Response = await client.post(
        "/api/harness",
        json={
            "habits": f"- {HABIT}",
            "tool_guidance": "- Search code with rg.",
            "summary": "On a phone",
            "adopt": True,
        },
    )
    assert written.status_code == 200, written.text
    page: Page = await browser.new_page(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2
    )
    try:
        await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
        host, port = _address(client)
        await page.goto(ui_url(host, port, TOKEN))
        await page.goto(f"http://{host}:{port}/#/settings")
        row = page.get_by_role("link", name="Self-improvement", exact=False)
        await expect(row).to_be_visible()
        await row.click()
        await expect(page.get_by_role("heading", name="Self-improvement", exact=True)).to_be_visible()
        await expect(_section(page, "What Dot follows now")).to_be_visible()
        # The overview's tiles go two to a row on a phone, and nothing reaches past the screen's edge: not on this
        # phone, nor on an iPhone mini (375 points wide) or the first iPhone SE (320).
        tiles = page.get_by_role("group", name="Overview").locator(".stat-tile")
        await expect(tiles).to_have_count(4)
        await expect(page.locator(".follow-columns")).to_be_visible()
        for width in (390, 375, 320):
            await page.set_viewport_size({"width": width, "height": 844})
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            assert await page.evaluate(FITS), width
            await page.screenshot(path=tmp_path / f"harness-{width}.png", full_page=True)
    finally:
        await page.close()


async def test_the_version_in_use_reads_as_two_lists_and_the_nights_as_a_card(
    browser: Browser, nightly_client: httpx.AsyncClient, runtime: Runtime, tmp_path: Path
) -> None:
    habits: str = "- Pipe long output through `head` or `tail`.\n- Read a file whole before you change it."
    written: httpx.Response = await nightly_client.post(
        "/api/harness",
        json={
            "habits": habits,
            "tool_guidance": "- Search code with `rg`.",
            "summary": "Pipes and reads",
            "adopt": True,
        },
    )
    assert written.status_code == 200, written.text
    now: float = time.time()
    for days, state in ((2, "keep"), (1, "done")):
        step = await runtime.store.create_harness_step(now - days * 86400, None, None)
        await runtime.store.finish_harness_step(step.id, now - days * 86400 + 60, state, "{}", None)
    page: Page = await _open(browser, nightly_client, 1280)
    try:
        # What Dot follows now: the two texts as lists, a line a point, the words between backticks in code type.
        columns: Locator = _section(page, "What Dot follows now").locator(".follow-column")
        await expect(columns).to_have_count(2)
        await expect(columns.nth(0).locator(".follow-label")).to_have_text("Habits · how to work")
        await expect(columns.nth(1).locator(".follow-label")).to_have_text("Tool guidance · what this Mac wants")
        await expect(columns.nth(0).locator("li")).to_have_count(2)
        await expect(columns.nth(0).locator("li code")).to_have_text(["head", "tail"])
        await expect(columns.nth(1).locator("li code")).to_have_text(["rg"])
        # The overview counts the nights and names the last one, beside the strip of them.
        overview: Locator = page.get_by_role("group", name="Overview")
        await expect(overview).to_contain_text("Learning nights")
        await expect(overview).to_contain_text("· done")
        await expect(overview.locator(".night-strip .night-cell")).to_have_count(2)
        # The nightly learning says when it runs and what comes next, and lists its nights under that.
        nightly: Locator = _section(page, "Nightly learning")
        await expect(nightly).to_contain_text("Every night at 04:00")
        await expect(nightly.locator(".night-next")).to_have_text(
            re.compile(r"^Next: (tonight|today|tomorrow), 04:00$")
        )
        await expect(nightly.locator("[data-step]")).to_have_count(2)
        await expect(nightly.locator(".night-legend")).to_contain_text("a change")
        # A version's texts and notes open under its row, and shut again.
        # The earlier tests of this home left versions of their own: this one is the one in use, and the first.
        row: Locator = page.locator("[data-version]").filter(has_text="Pipes and reads")
        await expect(row).to_have_count(1)
        await expect(page.locator("[data-version]").first).to_contain_text("Pipes and reads")
        details: Locator = row.locator(".version-details")
        await expect(details).to_be_hidden()
        await row.get_by_role("button", name="Texts and notes").click()
        await expect(details).to_contain_text("Pipe long output through `head` or `tail`.")
        await row.get_by_role("button", name="Texts and notes").click()
        await expect(details).to_be_hidden()
        # How it learns is folded until it is asked for.
        how: Locator = page.locator("section.fold", has_text="How it learns")
        await expect(how.get_by_text("Test tasks")).to_be_hidden()
        await how.get_by_role("button", name="How it learns").click()
        await expect(how.get_by_text("Test tasks")).to_be_visible()
        await page.screenshot(path=tmp_path / "harness-nightly-1280.png", full_page=True)
    finally:
        await page.close()
