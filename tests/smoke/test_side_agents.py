"""The side menu shows every agent of a run (item 198): a chat at work shows every run it delegated, those
at work and those that ended, as one count (items 507 and 1327), and its name for a screen reader says how
many there are. The side menu counts the chats at work (item 202). Scripted runs, served by a real server, wide and on a
phone, light and dark."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

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
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "side-agents-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHELVES: tuple[str, ...] = ("Read the first shelf", "Read the second shelf", "Read the third shelf")
# Long enough for every look below; the run is stopped long before.
_WAIT_COMMAND: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _script() -> list[ScriptedTurn]:
    """The model's turns in the order the runs ask for them: the run delegates the three shelves in one turn; two of
    the delegated runs finish at once, whichever asks first, and the third waits in a shell command."""
    delegate: ScriptedTurn = ScriptedTurn(
        text="", calls=tuple(ScriptedCall(tool="delegate", arguments={"goal": shelf}) for shelf in _SHELVES)
    )
    done: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="finish", arguments={"answer": "The shelf is read.", "success": True}),)
    )
    wait: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="shell", arguments={"command": _WAIT_COMMAND}),)
    )
    return [delegate, done, done, wait]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
    """The run manager and the address of a real server over it."""
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
            yield manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _two_done_one_working(runtime: Runtime, parent_id: str) -> list[RunRecord]:
    """The runs ``parent_id`` delegated, once two of them have ended done and the third has made its first tool call."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        children: list[RunRecord] = [
            run for run in await runtime.store.list_runs(100, None) if run.parent_run_id == parent_id
        ]
        working: list[RunRecord] = [run for run in children if run.status == "running"]
        if (
            len(children) == len(_SHELVES)
            and [run.status for run in children].count("done") == 2
            and len(working) == 1
            and await runtime.store.events_of_kind(working[0].id, "tool_call", 1)
        ):
            return children
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {parent_id} did not get two delegated runs done and one at work in {_STEP_WAIT_S} s")


async def _open_sidebar(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    await page.goto(ui_url(host, port, TOKEN))
    if width < 860:
        await page.get_by_role("button", name="Show sidebar").click()
    return page


async def test_the_owners_agents_are_a_menu_beside_the_search_each_to_its_page_and_all_to_every_agent(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    """Items 835 and 1327: the owner's agents stand in a menu behind the button beside the search (the faces of item 835
    and the rows of a phone's home, item 1259, are one quiet list): each its badge, its name and the count of its
    chats, to its page while it has no chat, the open one marked; under them All bots, New bot and Campaigns. Wide
    and on a phone, light and dark."""
    _, address = served
    agents: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        for name, role in (("Scout", "Looks things up"), ("Competitor Watch", "Watches the competitors' prices")):
            made: httpx.Response = await client.post(
                "/api/agents",
                json={"name": name, "role": role, "instructions": "", "model": "", "effort": None, "approvals": None},
            )
            assert made.status_code == 201, made.text
            agents[name] = made.json()["id"]
        try:
            async with async_playwright() as playwright:
                browser: Browser = await playwright.chromium.launch(
                    channel=runtime.config.browser.channel, headless=True
                )
                try:
                    for width, scheme in _SHOTS:
                        page: Page = await _open_sidebar(browser, address, width, scheme)
                        button: Locator = page.get_by_role("button", name="Your bots", exact=True)
                        # No face stands at the top any more, and no phone's rows: one button.
                        await expect(page.locator(".side-agent-faces, .side-bots, nav.side-links")).to_have_count(0)
                        await button.click()
                        # A narrow window with a mouse has the menu; a touch screen's sheet is
                        # test_agent_conversation.py's.
                        menu: Locator = page.get_by_role("menu", name="Your bots")
                        items: Locator = menu.get_by_role("menuitem")
                        # Dot first (item 1245), then the newest, as neither has worked yet; every entry its count.
                        await expect(items).to_have_text(
                            [
                                re.compile(r"Dot\s*0$"),
                                re.compile(r"Competitor Watch\s*0$"),
                                re.compile(r"Scout( Two)?\s*0$"),
                                "All bots",
                                "New bot",
                                "Campaigns",
                            ]
                        )
                        scout: Locator = items.filter(has_text=re.compile(r"^Scout"))
                        await expect(scout).to_have_attribute("title", "Looks things up")
                        codes: list[str] = await menu.locator(".side-badge").evaluate_all(
                            "(badges) => badges.map((badge) => badge.dataset.code)"
                        )
                        # Scout is renamed Scout Two further on, and a later look has its new letters.
                        assert codes in (["DO", "CW", "SC"], ["DO", "CW", "ST"]), codes
                        await page.screenshot(
                            path=str(tmp_path / f"agents-{width}-{scheme}.png"), animations="disabled"
                        )
                        if width < 860:
                            await page.close()
                            continue
                        # The open page's agent is marked, and the button on the page of every agent.
                        watch: Locator = items.filter(has_text="Competitor Watch")
                        await watch.click()
                        await expect(page).to_have_url(f"{address}/#/agents/{agents['Competitor Watch']}")
                        await button.click()
                        await expect(watch).to_have_attribute("aria-current", "true")
                        await expect(scout).not_to_have_attribute("aria-current", "true")
                        await items.filter(has_text="All bots").click()
                        # The page of every bot is Settings › Bots since item 1336: the sidebar is the settings' nav
                        # while it is open, with Bots its marked row, and Back to chats gives the chats' sidebar back.
                        await expect(page).to_have_url(f"{address}/#/settings/agents")
                        settings: Locator = page.get_by_role("navigation", name="Settings")
                        await expect(settings.locator("[aria-current='page'] .snav-label")).to_have_text("Bots")
                        await settings.get_by_role("link", name="Back to chats").click()
                        # A bot renamed from a page shows its new name at once, in the menu and on its chats' rows.
                        await page.evaluate(
                            "async (id) => { const { api } = await import('/js/api.js');"
                            " await api.put(`/agents/${id}`, { name: 'Scout Two', role: 'Looks things up',"
                            " instructions: '', model: '', effort: null, approvals: null }); }",
                            agents["Scout"],
                        )
                        async with page.expect_response(re.compile(r"/api/agents$")):
                            await page.evaluate("() => window.dispatchEvent(new CustomEvent('dot:agents-changed'))")
                        await button.click()
                        await expect(items.filter(has_text="Scout Two")).to_be_visible()
                        await page.keyboard.press("Escape")
                        await page.close()
                    # A bot deleted from its page leaves the menu at once; Dot stays (item 1245), All bots too.
                    page = await _open_sidebar(browser, address, 1280, "light")
                    for agent_id in agents.values():
                        await page.evaluate(
                            "async (id) => { const { api } = await import('/js/api.js');"
                            " await api.del(`/agents/${id}`); }",
                            agent_id,
                        )
                    agents.clear()
                    async with page.expect_response(re.compile(r"/api/agents$")):
                        await page.evaluate("() => window.dispatchEvent(new CustomEvent('dot:agents-changed'))")
                    await page.get_by_role("button", name="Your bots", exact=True).click()
                    await expect(page.get_by_role("menu", name="Your bots").get_by_role("menuitem")).to_have_text(
                        [re.compile(r"Dot\s*0$"), "All bots", "New bot", "Campaigns"]
                    )
                    await page.close()
                finally:
                    await browser.close()
        finally:
            for agent_id in agents.values():
                await client.delete(f"/api/agents/{agent_id}")


async def test_the_side_menu_shows_every_agent_of_a_run_on_the_chats_row(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        "Read the shelves", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    delegated: list[RunRecord] = await _two_done_one_working(runtime, run.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await _open_sidebar(browser, address, width, scheme)
                section: Locator = page.locator("#sidebar [data-section=working]")
                # Working holds the run's chat (item 202); while its run waits on the one helper still at work the
                # row says so in place of the count of every run it delegated (item 2010), and nothing hangs under it.
                await expect(section.locator(".side-chat")).to_have_count(1)
                row: Locator = section.locator(".side-chat > .side-run")
                await expect(row.locator(".side-chat-live")).to_have_text("Waiting on 1 helper…")
                await expect(row.locator(".side-helpers")).to_have_count(0)
                await expect(section.locator(".side-children")).to_have_count(0)
                assert "Waiting on 1 helper…" in (await row.get_attribute("aria-label") or "")
                await page.screenshot(path=str(tmp_path / f"agents-{width}-{scheme}.png"))
                await page.close()
        finally:
            await browser.close()
    manager.cancel(run.id)
    async with asyncio.timeout(_RUN_WAIT_S):
        for record in (run, *delegated):
            ended: RunRecord = await manager.wait(record.id)
            assert ended.status in ("done", "cancelled"), ended
