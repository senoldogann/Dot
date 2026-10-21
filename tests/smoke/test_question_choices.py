"""Questions with options (item 192), on the pages of a daemon in Chrome. The card of a question shows a row for each
option, with what it leads to and the one Dot recommends marked; a row sends its option, and the run reads which one was
chosen. A question that takes several is answered with the options ticked and what was written, as one text, and Send
answer sends nothing while nothing is ticked or written. Wide and on a phone, light and dark.

No network and no model service is used: the scripted model plays the runs of a daemon served by a real server.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "question-choices-test-token"
WAIT_S: float = 30.0
COLOUR: str = "Which colour should the buttons be?"
COLOURS: list[JsonObject] = [
    {"label": "Blue", "description": "The colour of the rest of the site.", "recommended": True},
    {"label": "Green", "description": "Stands out more, and matches the logo."},
]
PAGES: str = "Which pages should get the new buttons?"
PAGE_NAMES: list[JsonObject] = [
    {"label": "Home"},
    {"label": "Settings", "recommended": True},
    {"label": "Runs", "description": "Its buttons sit close together."},
]
WRITTEN: str = "and leave the settings alone"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)


def _asking(question: str, options: list[JsonObject], multiple: bool) -> ScriptedTurn:
    call: ScriptedCall = ScriptedCall(
        tool=ASK_USER_TOOL, arguments={"question": question, "options": options, "multiple": multiple, "wait": True}
    )
    return ScriptedTurn(text="", calls=(call,))


_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)
# Each screenshot's two runs: a question that takes one option, then one that takes several.
_TURNS: list[ScriptedTurn] = [
    _asking(COLOUR, COLOURS, False),
    _FINISH,
    _asking(PAGES, PAGE_NAMES, True),
    _FINISH,
] * len(_SHOTS)


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


async def _asked(runtime: Runtime, manager: RunManager, goal: str) -> tuple[RunRecord, ApprovalRecord]:
    """A run started for ``goal`` that asks its question, and that question once it waits for the user."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "ask", False, ()
    )
    deadline: float = time.monotonic() + WAIT_S
    while not (
        asked := [record for record in await runtime.store.list_approvals("pending", 20) if record.run_id == run.id]
    ):
        assert time.monotonic() < deadline, f"the run {run.id} asked nothing in {WAIT_S} s"
        await asyncio.sleep(0.05)
    return run, asked[0]


async def _answered(runtime: Runtime, manager: RunManager, run: RunRecord, question: ApprovalRecord) -> tuple[str, str]:
    """Once the run has ended: the answer its question was given, and what the run read of it."""
    async with asyncio.timeout(WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
    results: list[EventRecord] = await runtime.store.events_of_kind(run.id, "tool_result", 5)
    read: list[str] = [str(result.data["text"]) for result in results if result.data["tool"] == ASK_USER_TOOL]
    assert len(read) == 1, read
    return (await runtime.store.get_approval(question.id)).note, read[0]


@pytest.mark.browser
async def test_a_question_offers_its_options_and_the_run_reads_what_was_chosen(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    """A row sends its option, the one Dot recommends among them; several ticked are sent with what was written, after
    a Send answer with nothing ticked or written that sent nothing. Before, the options were words on small buttons,
    without what each leads to or a recommendation, and only one could be chosen."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, scheme in _SHOTS:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))

                    run, question = await _asked(runtime, manager, f"Colour the buttons ({width}, {scheme})")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    card: Locator = page.locator(".approval.question")
                    rows: Locator = card.locator(".choice")
                    await expect(rows).to_have_count(2)
                    await expect(rows.nth(0).locator(".choice-label")).to_contain_text("Blue")
                    await expect(rows.nth(0).locator(".badge")).to_have_text("Recommended")
                    await expect(rows.nth(0).locator(".choice-description")).to_have_text(
                        str(COLOURS[0]["description"])
                    )
                    await expect(rows.nth(1).locator(".badge")).to_have_count(0)
                    await card.scroll_into_view_if_needed()
                    await page.screenshot(path=str(tmp_path / f"choice-{width}-{scheme}.png"))
                    await rows.nth(0).click()
                    assert await _answered(runtime, manager, run, question) == (
                        "Blue",
                        "The user chose “Blue”, the one you recommended.",
                    )

                    run, question = await _asked(runtime, manager, f"Pick the pages ({width}, {scheme})")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    card = page.locator(".approval.question")
                    ticks: Locator = card.locator(".choice input[type=checkbox]")
                    await expect(ticks).to_have_count(3)
                    await expect(card).to_contain_text("Choose one or more, then send them.")
                    answer: Locator = card.get_by_label("Your answer")
                    await card.get_by_role("button", name="Send answer").click()
                    await expect(answer).to_be_focused()
                    assert (await runtime.store.get_approval(question.id)).status == "pending"
                    await ticks.nth(0).check()
                    await ticks.nth(2).check()
                    await answer.fill(WRITTEN)
                    await card.scroll_into_view_if_needed()
                    await page.screenshot(path=str(tmp_path / f"choices-{width}-{scheme}.png"))
                    await card.get_by_role("button", name="Send answer").click()
                    assert await _answered(runtime, manager, run, question) == (
                        f"Home; Runs\n{WRITTEN}",
                        f"The user answered: Home; Runs\n{WRITTEN}",
                    )
                    settled: Locator = page.locator(".approval.decided .approval-note")
                    await expect(settled).to_contain_text("Home; Runs")
                    assert (await settled.inner_text()).endswith(f"Home; Runs\n{WRITTEN}")
                    await page.close()
            finally:
                await browser.close()
