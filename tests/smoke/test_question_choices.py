"""Questions with options (items 192 and 2001), on the pages of a daemon in Chrome. The asks panel says "User input
needed" and "Fill in the pending answers"; the card of a question shows a full-width card for each option, with what it
leads to and the one Dot recommends marked. A tap picks an option (one, or several when the question takes several; a
second tap lets it go), and the panel's one Submit, disabled while a question has no answer, sends it; the run reads
which one was chosen. Several questions page in the one panel ("1 / 3") and are sent together once each has an answer.
At 1440 x 900 and at a phone's 390 x 844, light and dark.

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
_SHOTS: tuple[tuple[int, int, Literal["light", "dark"]], ...] = (
    (1440, 900, "light"),
    (1440, 900, "dark"),
    (390, 844, "light"),
    (390, 844, "dark"),
)
# Three questions the run leaves on the chat at once, answered together from the one panel.
_THREE: tuple[str, ...] = ("Which day suits you?", "Which pages should change?", "Anything to add?")


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
] * len(_SHOTS) + [
    # Then a run that leaves three questions on its chat in one step and ends.
    ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": _THREE[0], "options": COLOURS, "wait": False}),
            ScriptedCall(
                tool=ASK_USER_TOOL,
                arguments={"question": _THREE[1], "options": PAGE_NAMES, "multiple": True, "wait": False},
            ),
            ScriptedCall(tool=ASK_USER_TOOL, arguments={"question": _THREE[2], "wait": False}),
        ),
    ),
    _FINISH,
]


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
    """A tap picks an option, the one Dot recommends among them, and Submit answer sends it; several picked (one let go
    again) are sent with what was written, after Submit stood disabled while nothing was picked or written. Before item
    2001 a row sent its option at once, and several were ticked in boxes."""
    with scratch_home() as home, eval_environment(home, {}):
        async with _daemon(login_env) as (runtime, manager, address), async_playwright() as playwright:
            host, port = "127.0.0.1", int(address.rpartition(":")[2])
            browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
            try:
                for width, height, scheme in _SHOTS:
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": height}, color_scheme=scheme, reduced_motion="reduce"
                    )
                    await page.goto(ui_url(host, port, TOKEN))

                    run, question = await _asked(runtime, manager, f"Colour the buttons ({width}, {scheme})")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    panel: Locator = page.locator(".asks")
                    await expect(panel.locator(".asks-title")).to_have_text("User input needed")
                    await expect(panel.locator(".asks-name")).to_have_text("Fill in the pending answers")
                    card: Locator = page.locator(".approval.question")
                    rows: Locator = card.locator(".choice")
                    await expect(rows).to_have_count(2)
                    await expect(rows.nth(0).locator(".choice-label")).to_contain_text("Blue")
                    await expect(rows.nth(0).locator(".badge")).to_have_text("Recommended")
                    await expect(rows.nth(0).locator(".choice-description")).to_have_text(
                        str(COLOURS[0]["description"])
                    )
                    await expect(rows.nth(1).locator(".badge")).to_have_count(0)
                    submit: Locator = panel.locator(".asks-submit")
                    await expect(submit).to_have_text("Submit answer")
                    await expect(submit).to_be_disabled()
                    # The card's own Send stays out of the panel: the panel's Submit is the one.
                    await expect(card.locator(".approval-yes")).to_be_hidden()
                    await rows.nth(1).click()
                    await rows.nth(0).click()
                    await expect(rows.nth(0)).to_have_attribute("aria-pressed", "true")
                    await expect(rows.nth(1)).to_have_attribute("aria-pressed", "false")
                    # A full-width card: as wide as the card's words, never narrower than a tap.
                    box = await rows.nth(0).bounding_box()
                    words = await card.locator(".approval-text").bounding_box()
                    assert box is not None and words is not None and abs(box["width"] - words["width"]) <= 1, (
                        box,
                        words,
                    )
                    assert box["height"] >= 44, box
                    await panel.scroll_into_view_if_needed()
                    await page.screenshot(path=str(tmp_path / f"choice-{width}-{scheme}.png"))
                    await submit.click()
                    assert await _answered(runtime, manager, run, question) == (
                        "Blue",
                        "The user chose “Blue”, the one you recommended.",
                    )

                    run, question = await _asked(runtime, manager, f"Pick the pages ({width}, {scheme})")
                    await page.goto(f"{address}/#/runs/{run.id}")
                    card = page.locator(".approval.question")
                    rows = card.locator(".choice")
                    await expect(rows).to_have_count(3)
                    await expect(card).to_contain_text("Choose one or more, then send them.")
                    submit = page.locator(".asks .asks-submit")
                    await expect(submit).to_be_disabled()
                    await rows.nth(0).click()
                    await rows.nth(1).click()
                    await rows.nth(2).click()
                    await rows.nth(1).click()
                    await expect(rows.nth(1)).to_have_attribute("aria-pressed", "false")
                    await card.get_by_label("Your answer").fill(WRITTEN)
                    await page.screenshot(path=str(tmp_path / f"choices-{width}-{scheme}.png"))
                    await submit.click()
                    assert await _answered(runtime, manager, run, question) == (
                        f"Home; Runs\n{WRITTEN}",
                        f"The user answered: Home; Runs\n{WRITTEN}",
                    )
                    settled: Locator = page.locator(".approval.decided .approval-note")
                    await expect(settled).to_contain_text("Home; Runs")
                    assert (await settled.inner_text()).endswith(f"Home; Runs\n{WRITTEN}")
                    await page.close()

                # Three questions page in the one panel and go together (item 2001).
                run = await manager.start(
                    "Three questions",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    "test",
                    None,
                    None,
                    "standard",
                    None,
                    "ask",
                    False,
                    (),
                )
                async with asyncio.timeout(WAIT_S):
                    await manager.wait(run.id)
                for width, height in ((1440, 900), (390, 844)):
                    page = await browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
                    await page.goto(ui_url(host, port, TOKEN))
                    await page.goto(f"{address}/#/runs/{run.id}")
                    panel = page.locator(".asks")
                    count: Locator = panel.locator(".asks-count")
                    await expect(count).to_have_text("1 / 3")
                    count_box = await count.bounding_box()
                    assert count_box is not None and count_box["height"] < 30, f"the count wraps: {count_box}"
                    submit = panel.locator(".asks-submit")
                    await expect(submit).to_have_text("Submit answers")
                    shown: Locator = panel.locator(".approval.question:visible")
                    await expect(shown).to_have_count(1)
                    await shown.locator(".choice").nth(1).click()
                    await expect(submit).to_be_disabled()
                    await panel.get_by_role("button", name="Next ask").click()
                    await expect(count).to_have_text("2 / 3")
                    await shown.locator(".choice").nth(0).click()
                    await panel.get_by_role("button", name="Next ask").click()
                    await expect(count).to_have_text("3 / 3")
                    await expect(submit).to_be_disabled()
                    await shown.get_by_label("Your answer").fill("Friday at noon")
                    await expect(submit).to_be_enabled()
                    await panel.get_by_role("button", name="Previous ask").click()
                    await panel.get_by_role("button", name="Previous ask").click()
                    await expect(count).to_have_text("1 / 3")
                    await expect(shown.locator(".choice").nth(1)).to_have_attribute("aria-pressed", "true")
                    await page.screenshot(path=str(tmp_path / f"three-{width}.png"))
                    if width == 390:
                        await submit.click()
                        deadline: float = time.monotonic() + WAIT_S
                        while (
                            len(
                                notes := [
                                    record.note
                                    for record in await runtime.store.list_approvals("approved", 50)
                                    if record.run_id == run.id
                                ]
                            )
                            < 3
                        ):
                            assert time.monotonic() < deadline, notes
                            await asyncio.sleep(0.1)
                        assert sorted(notes) == sorted(["Green", "Home", "Friday at noon"]), notes
                    await page.close()
            finally:
                await browser.close()
