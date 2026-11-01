"""The Work orders part of the Agents page (items 907, 908) in a real Chrome over a real daemon: the active objectives
at its top, the orders under them with where each stands, read only, on a desktop and on a phone."""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import AgentRecord, ObjectiveNote, WorkOrderRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WAIT_S: float = 30.0
# Whether the page scrolls sideways.
_SIDEWAYS: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
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
async def client(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as http:
            yield http
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


async def _open(browser: Browser, client: httpx.AsyncClient, width: int, phone: bool) -> Page:
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900}, reduced_motion="reduce", is_mobile=phone, has_touch=phone
    )
    page: Page = await context.new_page()
    host: str = client.base_url.host
    port: int | None = client.base_url.port
    assert port is not None
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"http://{host}:{port}/#/agents")
    return page


async def _launch(runtime: Runtime) -> tuple[Any, Browser]:
    playwright = await async_playwright().start()
    return playwright, await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)


async def test_the_part_says_what_will_be_there_while_nothing_is_handed_out(
    runtime: Runtime, client: httpx.AsyncClient
) -> None:
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, 1280, False)
        part = page.locator(".work-part")
        await expect(part.get_by_role("heading", name="Work orders")).to_be_visible(timeout=_WAIT_S * 1000)
        await expect(part).to_contain_text("Nothing handed out yet")
        await expect(part.locator(".work-row")).to_have_count(0)
    finally:
        await browser.close()
        await playwright.stop()


@pytest.mark.parametrize(("width", "phone"), [(1280, False), (390, True)])
async def test_the_part_lists_the_objectives_and_the_orders_read_only(
    runtime: Runtime, client: httpx.AsyncClient, width: int, phone: bool
) -> None:
    store = runtime.store
    if not await store.list_objectives(("active",), 5):
        agent: AgentRecord = await store.create_agent(
            "Bug Finder", "Finds why a thing fails", "", "", None, None, (), None, None, ""
        )
        objective = await store.add_objective(
            "Grow the newsletter to a thousand readers",
            "More readers help the owner's business and bring clients",
            "Subscribers a week, read from the list",
            False,
        )
        await store.update_objective(
            objective.id, None, ObjectiveNote(at=time.time(), text="Sent issue one.", tainted=False)
        )
        working: WorkOrderRecord = await store.add_work_order(
            agent.id, "Write the second issue", "Brief.", "Done.", objective.id, time.time() + 7200, None, None, False
        )
        await store.move_work_order(working.id, "working", ("assigned",), new_run="run_x")
        waiting: WorkOrderRecord = await store.add_work_order(
            agent.id, "Check the signup form on a phone", "Brief.", "Done.", None, time.time() - 1800, None, None, False
        )
        await store.move_work_order(waiting.id, "working", ("assigned",), new_run="run_y")
        await store.move_work_order(waiting.id, "in_review", ("working",), note="attempt 1 ended as done", tainted=True)
    playwright, browser = await _launch(runtime)
    try:
        page: Page = await _open(browser, client, width, phone)
        part = page.locator(".work-part")
        await expect(part.locator(".work-row")).to_have_count(3, timeout=_WAIT_S * 1000)
        # The objective is first, with why it matters and how it is judged; then the orders, each with its state.
        first = part.locator(".work-row").first
        await expect(first).to_contain_text("Grow the newsletter to a thousand readers")
        await expect(first).to_contain_text("Judged by: Subscribers a week")
        await expect(first).to_contain_text("Sent issue one.")
        await expect(part.locator('[data-status="working"]')).to_contain_text("Working")
        await expect(part.locator('[data-status="working"]')).to_contain_text("Bug Finder")
        await expect(part.locator('[data-status="working"]')).to_contain_text("due in")
        review = part.locator('[data-status="in_review"]')
        await expect(review).to_contain_text("Waiting for Dot's check")
        await expect(review).to_contain_text("late")
        await expect(review).to_contain_text("Written after Dot read content from outside")
        # Read only: nothing in the part is a button or a field.
        assert await part.locator("button, input, textarea, select").count() == 0
        assert await page.evaluate(_SIDEWAYS) <= 1
        await page.screenshot(path=f"/tmp/work-orders-{width}.png", full_page=True)
    finally:
        await browser.close()
        await playwright.stop()
