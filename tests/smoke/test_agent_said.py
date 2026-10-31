"""An agent's words in the conversation that handed it a part (item 668): the part on its way shows no line of its own
in the conversation (item 892, the owner's word: only the main agent's work is told there), the agent's row standing
for it once its run starts, and the agent's answer stands as its
message under "Message from Scout", with its face and name, wide and on a phone's width, and in sight when the chat
shows only what Dot is doing (item 663); folded to that head until the reader opens it, then as wide as Dot's own
answer (item 733). The agent's run carries its name in its queued event. Measured in Chrome on
real runs of a scripted model. A helper an agent's own run delegated to carries the agent's id (item 553) but is no
message to it: it is named "Helper 1", and its report is no message of the agent's."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import NO_PICKS, AgentRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "agent-said-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_REPLY: str = "Three sources agree: the launch moved to **May**."
_TURNS: tuple[ScriptedTurn, ...] = (
    # Dot hands the part to Scout; Scout's run answers; Dot answers with what it got.
    ScriptedTurn(
        text="", calls=(ScriptedCall(tool="message_agent", arguments={"agent": "Scout", "message": "Check the date"}),)
    ),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": _REPLY, "success": True}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "It is May.", "success": True}),)),
)


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(
    home: Path, turns: tuple[ScriptedTurn, ...] = _TURNS
) -> AsyncIterator[tuple[RunManager, Runtime, str, int]]:
    """A daemon whose scripted model hands a part to Scout, which answers, and then answers itself: its run manager,
    address and port."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, list(turns)))
    await runtime.store.create_agent(
        "Scout", "Finds and checks sources", "Check every date twice.", SCRIPTED_MODEL, None, "yolo", (), None, None
    )
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
            yield manager, runtime, f"http://127.0.0.1:{port}", port
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def test_the_agents_answer_is_its_message_in_the_conversation(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as (manager, runtime, address, port):
        run: RunRecord = await manager.start(
            "When is the launch?", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        scout: AgentRecord = next(agent for agent in await runtime.store.list_agents() if agent.name == "Scout")
        queued = [
            event
            for child in await runtime.store.list_child_runs(run.id)
            for event in await runtime.store.events_of_kind(child.id, "run_queued", 1)
        ]
        assert [(event.data["agent_id"], event.data["agent_name"]) for event in queued] == [(scout.id, "Scout")]
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for width, work in ((1280, "steps"), (390, "status")):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, reduced_motion="reduce"
                    )
                    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await page.add_init_script(f"localStorage.setItem('dot.work', '{work}')")
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(page.locator(".answer .prose").last).to_have_text("It is May.")
                    # The agent's row is named by its name, before what it was asked (item 669).
                    await expect(page.locator(".child-row .child-name")).to_have_text("Scout")
                    await expect(page.locator(".child-row .child-goal")).to_have_text("Check the date")
                    said = page.locator(".agent-said")
                    await expect(said.locator(".agent-said-names")).to_have_text("Message from Scout")
                    message = said.locator(".agent-message")
                    # Folded until the reader opens it, whatever the chat shows of the work (items 733 and 859): Dot's
                    # own answer keeps the conversation, and a return to the chat leaves it folded.
                    await expect(said).not_to_have_attribute("open", "")
                    await expect(message).to_be_hidden()
                    await page.goto(f"{address}/#/runs")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(said).not_to_have_attribute("open", "")
                    if work == "steps":
                        await page.screenshot(path=str(tmp_path / f"said-{width}-folded.png"))
                    await said.locator(".agent-said-head").click()
                    # Opened, it stays open when the chat is drawn again.
                    await expect(said).to_have_attribute("open", "")
                    await page.goto(f"{address}/#/runs")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await expect(said).to_have_attribute("open", "")
                    await expect(message).to_be_visible()
                    await expect(message.locator(".agent-message-name")).to_have_text("Scout")
                    # Opened, the message is as wide as Dot's own answer, on a phone too.
                    answer_box = await page.locator(".answer").last.bounding_box()
                    message_box = await message.bounding_box()
                    assert answer_box is not None and message_box is not None
                    assert message_box["width"] >= answer_box["width"] - 2, (message_box, answer_box)
                    assert abs(message_box["x"] - answer_box["x"]) <= 2, (message_box, answer_box)
                    await expect(message.locator(".prose")).to_have_text(
                        "Three sources agree: the launch moved to May."
                    )
                    await expect(message.locator(".avatar")).to_be_visible()
                    # Nothing is on its way once the part arrived.
                    await expect(page.locator(".agent-handoff")).to_have_count(0)
                    await page.screenshot(path=str(tmp_path / f"said-{width}.png"))
                    await page.close()
            finally:
                await browser.close()


# Scout's own run delegates a part; the helper reports; Scout answers.
_SCOUT_DELEGATES: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="delegate", arguments={"goal": "Count the sources"}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Seven.", "success": True}),)),
    ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Seven sources.", "success": True}),)
    ),
)


async def test_a_helper_of_an_agents_own_run_is_a_helper_and_says_nothing_as_the_agent(own_home: Path) -> None:
    async with _served(own_home, _SCOUT_DELEGATES) as (manager, runtime, address, port):
        scout: AgentRecord = next(agent for agent in await runtime.store.list_agents() if agent.name == "Scout")
        run: RunRecord = await manager.start_for_agent(
            scout, "How many sources?", "task", None, None, "test", None, "standard", None, "yolo", False, (), NO_PICKS
        )
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        queued = [
            event
            for child in await runtime.store.list_child_runs(run.id)
            for event in await runtime.store.events_of_kind(child.id, "run_queued", 1)
        ]
        assert [(event.data["agent_id"], event.data["agent_name"]) for event in queued] == [(scout.id, None)]
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page: Page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/runs/{run.id}")
                await expect(page.locator(".answer .prose").last).to_have_text("Seven sources.")
                await expect(page.locator(".child-row .child-name")).to_have_text("Helper 1")
                await expect(page.locator(".agent-said")).to_have_count(0)
                await page.close()
            finally:
                await browser.close()
