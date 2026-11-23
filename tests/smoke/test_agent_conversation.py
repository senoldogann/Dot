"""An agent is one conversation (item 1233, the owner's word of 2026-11-19 after Grok Bot): its face in the sidebar
opens its chat, its page's box goes on in that chat rather than starting a new one each time, New chat in its page's
"…" starts the next message afresh, and its name in the chat's bar opens its page (item 1234). Dot is one
conversation in its way (item 1245): its entry opens its home chat, and the home's box starts a chat of its own for each
message (item 1350). The "+" makes a new agent, whose conversation opens
next, and a phone lists the agents as rows of the list (item 1259). A real server on a scripted model, in Chrome."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "agent-conversation-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_RUN_WAIT_S: float = 60.0
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)
_AGENT: dict[str, object] = {
    "name": "Scout",
    "role": "Finds and checks sources",
    "instructions": "Prefer primary sources.",
    "model": "",
    "effort": None,
    "approvals": "yolo",
    "tools": None,
}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[RunManager, httpx.AsyncClient]]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\ngoal_mode = false\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"'
        "\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH] * 16))
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
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_RUN_WAIT_S
        ) as client:
            yield manager, client
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def _opened(browser: Browser, client: httpx.AsyncClient, route: str) -> Page:
    port: int | None = client.base_url.port
    assert port is not None
    page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"http://127.0.0.1:{port}/#{route}")
    return page


async def _settled(manager: RunManager, run_id: str) -> None:
    async with asyncio.timeout(_RUN_WAIT_S):
        await manager.wait(run_id)


async def _sent_from_page(manager: RunManager, page: Page, words: str) -> str:
    """Send `words` from the agent page's box; the id of the run it started, once it ended."""
    box: Locator = page.get_by_role("textbox", name="Message")
    await box.fill(words)
    await box.press("Enter")
    await page.wait_for_url("**/#/runs/*")
    run_id: str = page.url.rsplit("/", 1)[1]
    await _settled(manager, run_id)
    return run_id


async def _entry(page: Page, name: str) -> Locator:
    """An agent's entry in the sidebar's agents menu (item 1328: the faces became a menu), which this opens; whatever
    acts on the entry closes the menu."""
    await page.get_by_role("button", name="Your agents", exact=True).click()
    return page.get_by_role("menu", name="Your agents").get_by_role("menuitem", name=re.compile(f"^{name}"))


async def _opens(page: Page, name: str, route: str) -> None:
    """Open the agent's entry and see where it leads."""
    await (await _entry(page, name)).click()
    await expect(page).to_have_url(re.compile(f"#{route}$"))


async def test_an_agent_is_one_conversation(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    made = await client.post("/api/agents", json=_AGENT)
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            # With no chat yet, its entry opens its page, whose box starts the first chat.
            page: Page = await _opened(browser, client, f"/agents/{agent_id}")
            await _opens(page, "Scout", f"/agents/{agent_id}")
            first: str = await _sent_from_page(manager, page, "Find two sources on the matter")
            thread: str = (await client.get(f"/api/runs/{first}")).json()["run"]["thread_id"]

            # The chat's bar names the agent, a link to its page.
            name: Locator = page.locator(".stage-title a.stage-name-link")
            await expect(name).to_have_text("Scout")
            await expect(name).to_have_attribute("href", f"#/agents/{agent_id}")

            # Its entry now opens that chat, and is marked while it is open.
            await _opens(page, "Scout", f"/runs/{first}")
            await expect(await _entry(page, "Scout")).to_have_attribute("aria-current", "true")
            await page.keyboard.press("Escape")

            # A message from its page goes on in the same chat, rather than starting a new one.
            await page.goto(page.url.split("#", 1)[0] + f"#/agents/{agent_id}")
            await expect(page.get_by_role("link", name="Open your chat with Scout")).to_have_attribute(
                "href", f"#/runs/{first}"
            )
            second: str = await _sent_from_page(manager, page, "And a third one")
            assert second != first
            assert (await client.get(f"/api/runs/{second}")).json()["run"]["thread_id"] == thread

            # New chat in the page's "…" starts the next message afresh, and the entry follows the newest chat.
            await page.goto(page.url.split("#", 1)[0] + f"#/agents/{agent_id}")
            await page.get_by_role("button", name="More for Scout").click()
            await page.get_by_role("menuitem", name="New chat").click()
            await expect(page.get_by_role("link", name="Open your chat with Scout")).to_be_hidden()
            third: str = await _sent_from_page(manager, page, "Something else entirely")
            fresh: str = (await client.get(f"/api/runs/{third}")).json()["run"]["thread_id"]
            assert fresh != thread
            await _opens(page, "Scout", f"/runs/{third}")

            # The conversation deleted from the sidebar, the page and the entry go on in the chat left, not a gone one.
            await page.goto(page.url.split("#", 1)[0] + f"#/agents/{agent_id}")
            await expect(page.get_by_role("link", name="Open your chat with Scout")).to_have_attribute(
                "href", f"#/runs/{third}"
            )
            row: Locator = page.locator(".side-chat").filter(
                has=page.get_by_text("Something else entirely", exact=True)
            )
            await row.hover()
            await row.get_by_role("button", name="More for Something else entirely").click()
            await page.get_by_role("menuitem", name="Delete").click()
            # The agents are read again as the chat goes, so that the entry leads to the chat left.
            async with page.expect_response(re.compile(r"/api/agents$")):
                await page.get_by_role("dialog").get_by_role("button", name="Delete").click()
            await expect(page.get_by_role("link", name="Open your chat with Scout")).to_have_attribute(
                "href", f"#/runs/{second}"
            )
            await _opens(page, "Scout", f"/runs/{second}")
            await page.goto(page.url.split("#", 1)[0] + f"#/agents/{agent_id}")

            # Its conversation's run that ends while you are elsewhere puts the dot of an unread chat on its row
            # (item 1235); opening the chat from the entry takes it off.
            more = await client.post("/api/runs", json={"goal": "One more", "agent_id": agent_id, "thread_id": thread})
            assert more.status_code == 201, more.text
            await _settled(manager, more.json()["id"])
            unread: Locator = page.locator("#sidebar a.side-run .side-unread")
            await expect(unread).to_have_count(1)
            await _opens(page, "Scout", f"/runs/{more.json()['id']}")
            await expect(unread).to_have_count(0)
            await page.close()
        finally:
            await browser.close()


async def _sent_from_home(manager: RunManager, page: Page, words: str) -> str:
    """Send `words` from the home's box; the id of the run it started, once it ended."""
    box: Locator = page.get_by_role("textbox", name="Goal")
    await box.fill(words)
    await box.press("Enter")
    await page.wait_for_url("**/#/runs/*")
    run_id: str = page.url.rsplit("/", 1)[1]
    await _settled(manager, run_id)
    return run_id


async def test_dot_is_one_conversation(served: tuple[RunManager, httpx.AsyncClient]) -> None:
    manager, client = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page: Page = await _opened(browser, client, "/runs")
            home: str = page.url.split("#", 1)[0] + "#/runs"
            # No button starts a new chat: the one beside the search writes to Dot, in its one conversation. Dot's entry
            # leads the agents' menu, to the home before there is a chat with Dot.
            await expect(page.locator("#sidebar").get_by_role("link", name="New chat")).to_have_count(0)
            await expect(page.locator("#sidebar").get_by_role("link", name="Write to Dot")).to_have_attribute(
                "href", "#/runs"
            )
            await _opens(page, "Dot", "/runs")
            await expect(page.get_by_role("link", name="Open your chat with Dot")).to_be_hidden()
            first: str = await _sent_from_home(manager, page, "Plan my week")
            thread: str = (await client.get(f"/api/runs/{first}")).json()["run"]["thread_id"]
            await _opens(page, "Dot", f"/runs/{first}")
            await expect(await _entry(page, "Dot")).to_have_attribute("aria-current", "true")
            await page.keyboard.press("Escape")

            # The home's next message starts a chat of its own (item 1350), not the chat of the latest activity, which
            # may be any old one; that chat is left as it was, and the way back into it is under the box.
            await page.goto(home)
            await expect(page.get_by_role("link", name="Open your chat with Dot")).to_have_attribute(
                "href", f"#/runs/{first}"
            )
            second: str = await _sent_from_home(manager, page, "And add the gym on Friday")
            assert second != first
            assert (await client.get(f"/api/runs/{second}")).json()["run"]["thread_id"] != thread

            # No New chat under the box (item 1279): the way back into the chat is all the home offers, and Dot's entry
            # leads to the newest run of the chat of the latest activity.
            await page.goto(home)
            await expect(page.get_by_role("link", name="Open your chat with Dot")).to_be_visible()
            await expect(page.get_by_role("button", name="New chat")).to_have_count(0)
            await _opens(page, "Dot", f"/runs/{second}")
            await page.close()
        finally:
            await browser.close()


async def test_the_plus_makes_an_agent_and_a_phone_lists_its_chat_as_the_mac_does(
    served: tuple[RunManager, httpx.AsyncClient],
) -> None:
    """Items 1259 and 1328: the "+" makes an agent from a face and a name and opens its page, whose box talks to it; on
    a phone its conversation is a chat of the list as on a Mac, with the agent's badge and name, and the agents' menu
    lists the agent."""
    manager, client = served
    port: int | None = client.base_url.port
    assert port is not None
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, reduced_motion="reduce"
            )
            page: Page = await context.new_page()
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.get_by_role("button", name="Show sidebar").click()
            await page.get_by_role("button", name="New agent").click()
            dialog: Locator = page.get_by_role("dialog", name="New agent")
            create: Locator = dialog.get_by_role("button", name="Create")
            await expect(create).to_be_disabled()
            await dialog.get_by_role("textbox", name="Name").fill("Quill")
            await create.click()
            await page.wait_for_url(re.compile(r".*#/agents/agt_[a-z0-9_]+$"))
            agent_id: str = page.url.rsplit("/", 1)[1]
            started = await client.post("/api/runs", json={"goal": "Draft a short note", "agent_id": agent_id})
            assert started.status_code == 201, started.text
            await _settled(manager, started.json()["id"])

            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            await page.get_by_role("button", name="Show sidebar").click()
            # Its conversation is a chat among the days as every chat is (item 1328: a phone's list is the Mac's): its
            # agent's badge and name over its title and last words.
            await expect(page.locator(".side-runs .spinner")).to_have_count(0)
            row: Locator = page.locator(".side-runs .side-chat").filter(has_text="Draft a short note")
            await expect(row.locator(".side-agent")).to_have_text("Quill")
            await expect(row.locator(".side-run-goal")).to_have_text("Draft a short note")
            await expect(row.locator(".side-chat-words")).to_have_text("Done.")
            home = (await client.get(f"/api/agents/{agent_id}")).json()["home"]
            link: Locator = row.locator("a.side-run")
            await expect(link).to_have_attribute("href", f"#/runs/{home['latest_run_id']}")
            # The agents' menu, a sheet on a phone, lists it with its count of chats and opens its conversation.
            await page.get_by_role("button", name="Your agents", exact=True).click()
            entry: Locator = page.get_by_role("dialog", name="Your agents").get_by_role(
                "menuitem", name=re.compile("^Quill")
            )
            await expect(entry).to_contain_text("1")
            # Unread until its chat is open; the row's dot goes then (item 1235).
            await expect(row.locator(".side-unread")).to_be_visible()
            await entry.click()
            await page.wait_for_url(f"**/#/runs/{home['latest_run_id']}")
            await page.get_by_role("button", name="Show sidebar").click()
            await expect(row.locator(".side-unread")).to_have_count(0)
            await expect(link).to_have_attribute("aria-current", "page")
            await context.close()
        finally:
            await browser.close()


async def test_a_phone_opens_an_agent_as_a_chat_and_goes_back_to_the_chats(
    served: tuple[RunManager, httpx.AsyncClient],
) -> None:
    """Item 1272: an agent with no chat yet, opened from its row on a phone, loads as a chat does (Dot's three dots, no
    card), stands its face, name and box in the middle of the room under the bar, and its Back is the list of chats,
    not the agents' page. Opened from the agents' page, Back still returns there."""
    _manager, client = served
    port: int | None = client.base_url.port
    assert port is not None
    made = await client.post("/api/agents", json={**_AGENT, "name": "Ledger"})
    assert made.status_code == 201, made.text
    agent_id: str = made.json()["id"]
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            # No service worker, so that the test can hold the page's read back.
            context = await browser.new_context(
                viewport={"width": 390, "height": 844},
                has_touch=True,
                is_mobile=True,
                reduced_motion="reduce",
                service_workers="block",
            )
            page: Page = await context.new_page()
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", port, TOKEN))
            held: asyncio.Event = asyncio.Event()

            async def hold(route: Route) -> None:
                await held.wait()
                await route.continue_()

            await page.route("**/api/schedules", hold)
            button: Locator = page.locator(".stagebar-left button")
            await button.click()
            await page.get_by_role("button", name="Your agents", exact=True).click()
            await (
                page.get_by_role("dialog", name="Your agents")
                .get_by_role("menuitem", name=re.compile("^Ledger"))
                .click()
            )
            await expect(page).to_have_url(re.compile(f"#/agents/{agent_id}$"))
            await expect(page.locator("main .chat-loading")).to_be_visible()
            await expect(page.locator("main .agent-home .card")).to_have_count(0)
            held.set()
            top: Locator = page.locator("main .agent-top")
            await expect(page.get_by_role("textbox", name="Message")).to_be_visible()
            box = await top.bounding_box()
            bar = await page.locator(".stagebar").bounding_box()
            assert box is not None and bar is not None
            room_middle: float = (bar["y"] + bar["height"] + 844) / 2
            assert abs(box["y"] + box["height"] / 2 - room_middle) < 60, (box, bar)
            await expect(button).to_have_attribute("aria-label", "Show sidebar")
            await button.click()
            await expect(page.locator(".sidebar")).to_be_in_viewport()
            await expect(page).to_have_url(re.compile(f"#/agents/{agent_id}$"))
            await page.unroute("**/api/schedules")
            # From the agents' page, Back returns to it.
            await page.goto(f"http://127.0.0.1:{port}/#/agents")
            await page.locator(f"a.agent-card[data-agent='{agent_id}']").click()
            await expect(page).to_have_url(re.compile(f"#/agents/{agent_id}$"))
            await expect(button).to_have_attribute("aria-label", "Back")
            await button.click()
            await expect(page).to_have_url(re.compile(r"#/settings/agents$"))
            await context.close()
        finally:
            await browser.close()
