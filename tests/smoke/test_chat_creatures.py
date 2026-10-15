"""Every chat wears a creature (item 277): in the sidebar, wide and on a phone, light and dark, each chat shows a face, a
chat with Dot the look its id gives and a chat with an agent the agent's; its name over its last line (the answer in
plain words, or the owner's message after "You:" while the run works); its age at its right, over the accent dot of a
chat that is unread; the creature of a chat at work wears the working state, that of a failed run the failed one.

The chats are written to the store, but for the one at work: its run sleeps in a shell command while the page looks.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

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
from dotagent.store.models import AgentLook, AgentRecord, RunRecord, RunStatus, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "chat-creatures-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_START_WAIT_S: float = 30.0
# The run at work sleeps in a shell command while the page looks at its chat; the manager stops it at the end.
_AT_WORK: ScriptedTurn = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 120"}),))
_SCOUT_LOOK: AgentLook = AgentLook(shape="cloud", expression="neutral", colour="blue")
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
_PHONE_PX: int = 860
TRIP: str = "Trip to Izmir"
INVOICE: str = "Invoice in the inbox"
FORM: str = "Contact form"
FLIGHTS: str = "Flights to Berlin"
TIDY: str = "Tidy the Downloads folder"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home on the scripted model, so that the chats are only the ones made here."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_AT_WORK]))
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


async def _ended_chat(
    runtime: Runtime, title: str, goal: str, answer: str, status: RunStatus, agent_id: str | None, seen: bool
) -> ThreadRecord:
    """A chat a person started, whose one run ended with ``status`` and ``answer``; seen by the owner or not."""
    thread: ThreadRecord = await runtime.store.create_thread(title, "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        goal,
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        agent_id,
        None,
    )
    await runtime.store.finish_run(run.id, status, answer)
    if seen:
        await runtime.store.see_thread(thread.id, True)
    return thread


async def _until_running(runtime: Runtime, run_id: str) -> None:
    """Wait until the run works: its shell command keeps it at work while the page looks."""
    deadline: float = time.monotonic() + _START_WAIT_S
    while time.monotonic() < deadline:
        if (await runtime.store.get_run(run_id)).status == "running":
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"the run {run_id} did not start within {_START_WAIT_S} s")


def _row(page: Page, title: str) -> Locator:
    return page.locator("#sidebar .side-chat").filter(has_text=title)


async def _box(locator: Locator) -> FloatRect:
    box: FloatRect | None = await locator.bounding_box()
    assert box is not None, locator
    return box


async def test_every_chat_wears_a_creature_over_its_last_line_and_its_unread_dot(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    port: int = int(address.rpartition(":")[2])
    trip: ThreadRecord = await _ended_chat(
        runtime,
        TRIP,
        "Plan the trip to Izmir",
        "## Plan\n\nI booked **two nights** at [the hotel](https://example.test/hotel) by the sea.",
        "done",
        None,
        True,
    )
    invoice: ThreadRecord = await _ended_chat(
        runtime, INVOICE, "Check the inbox for the invoice", "Found the March invoice.", "done", None, False
    )
    await _ended_chat(runtime, FORM, "Fix the contact form", "The host refused the deploy key.", "failed", None, True)
    scout: AgentRecord = await runtime.store.create_agent(
        "Scout", "Finds flights", "Find cheap flights.", SCRIPTED_MODEL, None, None, (), None, _SCOUT_LOOK
    )
    await _ended_chat(
        runtime, FLIGHTS, "Find a flight to Berlin", "Pegasus on Friday, 96 euros.", "done", scout.id, True
    )
    tidy: RunRecord = await manager.start(
        TIDY, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    await _until_running(runtime, tidy.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                if width < _PHONE_PX:
                    await page.get_by_role("button", name="Show sidebar").first.click()
                # One creature for every chat, the one at work among them.
                await expect(page.locator("#sidebar .side-chat")).to_have_count(5)
                await expect(page.locator("#sidebar .side-chat .avatar")).to_have_count(5)
                # A chat with Dot wears the look its id gives; a chat with an agent wears the agent's.
                for thread in (trip, invoice):
                    look: dict[str, str] = await page.evaluate(
                        "async (id) => (await import('/js/hues.js')).chatLook(id)", thread.id
                    )
                    face: Locator = _row(page, thread.title).locator(".avatar")
                    await expect(face).to_have_attribute("data-shape", look["shape"])
                    await expect(face).to_have_attribute("data-colour", look["colour"])
                    await expect(face).to_have_attribute("data-state", "idle")
                flights_face: Locator = _row(page, FLIGHTS).locator(".avatar")
                await expect(flights_face).to_have_attribute("data-shape", _SCOUT_LOOK.shape)
                await expect(flights_face).to_have_attribute("data-colour", _SCOUT_LOOK.colour)
                # The creature works while its chat's run works, and looks as a run that failed ended.
                await expect(_row(page, TIDY).locator(".avatar")).to_have_attribute("data-state", "working")
                await expect(_row(page, FORM).locator(".avatar")).to_have_attribute("data-state", "failed")
                # The name over the last line: the answer in plain words, the owner's own message after "You:".
                await expect(_row(page, TRIP).locator(".side-run-goal")).to_have_text(TRIP)
                await expect(_row(page, TRIP).locator(".side-chat-last")).to_have_text(
                    "Plan I booked two nights at the hotel by the sea."
                )
                await expect(_row(page, FLIGHTS).locator(".side-chat-last")).to_have_text(
                    "Pegasus on Friday, 96 euros."
                )
                await expect(_row(page, TIDY).locator(".side-chat-last")).to_have_text(f"You: {TIDY}")
                title_box: FloatRect = await _box(_row(page, TRIP).locator(".side-run-goal"))
                last_box: FloatRect = await _box(_row(page, TRIP).locator(".side-chat-last"))
                assert last_box["y"] >= title_box["y"] + title_box["height"] - 1, (title_box, last_box)
                # The unread chat's accent dot at its right, under its age; a chat that was seen has none.
                dot: Locator = _row(page, INVOICE).locator(".side-unread")
                await expect(dot).to_have_count(1)
                await expect(dot).not_to_have_css("background-color", "rgba(0, 0, 0, 0)")
                await expect(_row(page, TRIP).locator(".side-unread")).to_have_count(0)
                dot_box: FloatRect = await _box(dot)
                age_box: FloatRect = await _box(_row(page, INVOICE).locator(".side-age"))
                invoice_title: FloatRect = await _box(_row(page, INVOICE).locator(".side-run-goal"))
                assert dot_box["x"] >= invoice_title["x"] + invoice_title["width"], (invoice_title, dot_box)
                assert dot_box["y"] >= age_box["y"] + age_box["height"] - 1, (age_box, dot_box)
                await page.locator("#sidebar").screenshot(path=str(tmp_path / f"creatures-{width}-{scheme}.png"))
                await page.close()
        finally:
            await browser.close()
