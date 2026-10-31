"""The reactions on Dot's messages (item 586) in a real browser, wide in Chrome and on a phone in WebKit, against a real
server and a scripted model: the button under an answer and under what Dot said in the middle of a run, the palette it
opens (by pointer and by keyboard), the chips of what stands, which a second press takes back and which are there again
after a reload, on another screen at once, and under the turns before the run; a hold from the palette that holds the
run until the owner writes; a "not right" that reaches a run at work, and one that starts a follow-up run which the
page goes on at; and Settings › General › Reactions, which changes what each reaction does after a run.

No network and no model service is used: the scripted model plays the runs."""

import asyncio
import re
import time
import tomllib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright, async_playwright, expect
from reaction_stand_ins import ANSWER, BURST_S, GOAL, REST_S, SAID, THUMB_DOWN, Gated, Stage, finish, says, until

from dotagent.agent.manager import RunManager
from dotagent.agent.reactions import Reactions
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.types import ScriptedTurn
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ReactionRecord, RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "reactions-page-token"
WAIT_S: float = 30.0
MESSAGE: str = "textarea[aria-label^='Message for']"
# The palette, as a screen reader names its reactions.
PALETTE: list[str] = ["Good", "Love it", "Not right", "Do it again", "Remember this", "Explain", "Hold on"]
type Look = Literal["wide", "phone"]
LOOKS: list[Look] = ["wide", "phone"]
# How far past the screen's edge the page may reach: none, but the rounding of a fraction.
_OVER_PX: int = 1
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


@dataclass(frozen=True)
class Served:
    """A daemon on a throw-away home: its runtime, its runs, the service of the reactions and where it is."""

    runtime: Runtime
    manager: RunManager
    reactions: Reactions
    address: str
    port: int
    home: Path


@dataclass(frozen=True)
class Browsers:
    """Chrome as a Mac's window and WebKit as an iPhone."""

    playwright: Playwright
    chrome: Browser
    webkit: Browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def stage() -> Stage:
    return Stage()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(tmp_path_factory: pytest.TempPathFactory, stage: Stage) -> AsyncIterator[Served]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nlessons = "off"\n'
        "playbooks = false\n\n[memory]\nembed_enabled = false\n\n[skills]\nelsewhere = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(stage.name, stage)
    manager: RunManager = RunManager(runtime)
    reactions: Reactions = Reactions(runtime, manager, burst_s=BURST_S)
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
        reactions=reactions,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        yield Served(runtime, manager, reactions, f"http://127.0.0.1:{port}", port, home)
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await reactions.close()
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browsers(served: Served) -> AsyncIterator[Browsers]:
    async with async_playwright() as playwright:
        chrome: Browser = await playwright.chromium.launch(channel=served.runtime.config.browser.channel, headless=True)
        webkit: Browser = await playwright.webkit.launch(headless=True)
        try:
            yield Browsers(playwright, chrome, webkit)
        finally:
            await chrome.close()
            await webkit.close()


async def _start(served: Served, thread_id: str | None = None) -> RunRecord:
    return await served.manager.start(
        GOAL, "task", SCRIPTED_MODEL, thread_id, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )


async def _answered(served: Served, stage: Stage, turns: list[ScriptedTurn], thread_id: str | None = None) -> RunRecord:
    """A run of the scripted ``turns`` that has ended."""
    stage.load(turns)
    run: RunRecord = await _start(served, thread_id)
    async with asyncio.timeout(WAIT_S):
        return await served.manager.wait(run.id)


async def _open(browsers: Browsers, served: Served, look: Look, path: str) -> Page:
    """A page signed in at ``path`` (a hash route), in a browser of its own: a Mac's window, or an iPhone."""
    context: BrowserContext
    if look == "wide":
        context = await browsers.chrome.new_context(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
    else:
        context = await browsers.webkit.new_context(
            **browsers.playwright.devices["iPhone 17 Pro Max"], reduced_motion="reduce"
        )
    await context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    page: Page = await context.new_page()
    await page.goto(ui_url("127.0.0.1", served.port, TOKEN))
    await page.goto(f"{served.address}/#{path}")
    return page


async def _fits(page: Page, label: str) -> None:
    over: object = await page.evaluate(_OVERFLOW)
    assert isinstance(over, int | float)
    assert over <= _OVER_PX, f"{label}: {over} px past the screen's edge"


def _palette(page: Page) -> Locator:
    return page.get_by_role("menu", name="React to this message")


def _choice(page: Page, name: str) -> Locator:
    return _palette(page).get_by_role("menuitemcheckbox", name=name)


async def _standing(served: Served, run: RunRecord, message: str = "answer") -> list[str]:
    return [record.reaction for record in await served.runtime.store.message_reactions(run.id, message)]


async def _react(page: Page, foot: Locator, name: str) -> None:
    """Open the palette of the message whose foot is ``foot``, pick ``name`` and wait for its chip."""
    await foot.get_by_role("button", name="React to this message").click()
    await expect(_palette(page)).to_be_visible()
    await _choice(page, name).click()
    await expect(_palette(page)).to_be_hidden()
    await expect(foot.get_by_role("button", name=name, pressed=True)).to_be_visible()


# ----------------------------------------------------------------------------------------- the palette and chips


@pytest.mark.parametrize("look", LOOKS)
async def test_the_palette_puts_a_reaction_under_an_answer_and_a_second_press_of_its_chip_takes_it_back(
    browsers: Browsers, served: Served, stage: Stage, look: Look, tmp_path: Path
) -> None:
    run: RunRecord = await _answered(served, stage, [finish(ANSWER)])
    page: Page = await _open(browsers, served, look, f"/runs/{run.id}")
    answer: Locator = page.get_by_role("article", name="Answer")
    await expect(answer).to_contain_text(ANSWER)
    button: Locator = answer.get_by_role("button", name="React to this message")
    await expect(button).to_have_attribute("aria-expanded", "false")
    # Item 652: with a pointer the button is seen only on the message it is on, and while its palette is open; a
    # phone, with no pointer to rest, shows it always.
    if look == "wide":
        await page.mouse.move(0, 0)
        await expect(button).to_have_css("opacity", "0")
        await answer.hover()
    await expect(button).to_have_css("opacity", "1")
    await button.click()
    await expect(button).to_have_attribute("aria-expanded", "true")
    if look == "wide":
        await page.mouse.move(0, 0)
    await expect(button).to_have_css("opacity", "1")
    palette: Locator = _palette(page)
    await expect(palette).to_be_visible()
    labels: object = await palette.get_by_role("menuitemcheckbox").evaluate_all(
        "nodes => nodes.map((node) => node.getAttribute('aria-label'))"
    )
    assert labels == PALETTE
    # The palette is in the window whole, over the page, and each choice is a thumb's on a phone.
    box = await palette.bounding_box()
    assert box is not None and page.viewport_size is not None
    assert box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
    first = await _choice(page, "Good").bounding_box()
    assert first is not None and first["width"] >= (44 if look == "phone" else 32), first
    # Once the palette has faded in.
    await page.wait_for_timeout(300)
    await page.screenshot(path=str(tmp_path / f"palette-{look}.png"))
    await _choice(page, "Good").click()
    await expect(palette).to_be_hidden()
    await expect(button).to_have_attribute("aria-expanded", "false")
    chip: Locator = answer.get_by_role("group", name="Your reactions").get_by_role("button", name="Good")
    await expect(chip).to_have_attribute("aria-pressed", "true")
    assert await _standing(served, run) == ["good"]
    # Opened again, the reaction that stands is ticked, and a pick of it takes it back.
    await button.click()
    await expect(_choice(page, "Good")).to_have_attribute("aria-checked", "true")
    await expect(_choice(page, "Love it")).to_have_attribute("aria-checked", "false")
    await _choice(page, "Good").click()
    await expect(chip).to_have_count(0)
    assert await _standing(served, run) == []
    # A second press of the chip is the other way to take one back.
    await _react(page, answer, "Love it")
    love: Locator = answer.get_by_role("button", name="Love it", pressed=True)
    await expect(love).to_be_visible()
    await _fits(page, f"{look} with a chip")
    await page.screenshot(path=str(tmp_path / f"chip-{look}.png"))
    await love.click()
    await expect(love).to_have_count(0)
    assert await _standing(served, run) == []
    await page.context.close()


@pytest.mark.parametrize("look", LOOKS)
async def test_the_chips_are_there_after_a_reload_in_the_order_of_the_palette_and_on_another_screen_at_once(
    browsers: Browsers, served: Served, stage: Stage, look: Look
) -> None:
    run: RunRecord = await _answered(served, stage, [finish(ANSWER)])
    page: Page = await _open(browsers, served, look, f"/runs/{run.id}")
    other: Page = await _open(browsers, served, "wide", f"/runs/{run.id}")
    answer: Locator = page.get_by_role("article", name="Answer")
    elsewhere: Locator = other.get_by_role("article", name="Answer")
    await expect(answer).to_contain_text(ANSWER)
    await expect(elsewhere).to_contain_text(ANSWER)
    for name in ("Hold on", "Love it", "Good"):
        await _react(page, answer, name)
    chips: Locator = answer.get_by_role("group", name="Your reactions").get_by_role("button")
    await expect(chips).to_have_count(3)
    assert await chips.evaluate_all("nodes => nodes.map((node) => node.getAttribute('aria-label'))") == [
        "Good",
        "Love it",
        "Hold on",
    ]
    # Another screen of the same chat has them without being asked: the daemon told it.
    others: Locator = elsewhere.get_by_role("group", name="Your reactions").get_by_role("button")
    await expect(others).to_have_count(3)
    await _fits(page, f"{look} with three chips")
    await page.reload()
    await expect(chips).to_have_count(3)
    assert await chips.evaluate_all("nodes => nodes.map((node) => node.getAttribute('aria-label'))") == [
        "Good",
        "Love it",
        "Hold on",
    ]
    # Taken back on one screen, it goes from the other.
    await chips.nth(1).click()
    await expect(others).to_have_count(2)
    assert await _standing(served, run) == ["hold", "good"] or sorted(await _standing(served, run)) == ["good", "hold"]
    await page.context.close()
    await other.context.close()


async def test_the_turns_before_the_run_have_their_reactions_too_and_keep_them(
    browsers: Browsers, served: Served, stage: Stage
) -> None:
    first: RunRecord = await _answered(served, stage, [says(SAID), finish(ANSWER)])
    second: RunRecord = await _answered(served, stage, [finish("The second answer.")], first.thread_id)
    page: Page = await _open(browsers, served, "wide", f"/runs/{second.id}")
    earlier: Locator = page.locator(".history")
    await expect(earlier.get_by_role("article", name="Answer")).to_contain_text(ANSWER)
    said: Locator = earlier.locator(".turn-say").filter(has_text=SAID)
    # A line of an earlier turn shows no button of its own (item 863: not on every line); its answer has one.
    await expect(said.get_by_role("button", name="React to this message")).to_be_hidden()
    await _react(page, earlier.get_by_role("article", name="Answer"), "Good")
    assert await _standing(served, first) == ["good"]
    assert await _standing(served, second) == []
    # A reaction that stands on such a line still shows there, as its chip.
    await served.reactions.add(first.id, "step-1", "love")
    await page.reload()
    await expect(earlier.get_by_role("article", name="Answer").get_by_role("button", name="Good")).to_be_visible()
    await expect(said.get_by_role("button", name="Love it")).to_be_visible()
    # The answer of the run itself has none of them.
    own: Locator = page.get_by_role("article", name="Answer").last
    await expect(own).to_contain_text("The second answer.")
    await expect(own.get_by_role("group", name="Your reactions").get_by_role("button")).to_have_count(0)
    await page.context.close()


async def test_the_palette_is_reached_and_worked_by_the_keyboard(
    browsers: Browsers, served: Served, stage: Stage
) -> None:
    run: RunRecord = await _answered(served, stage, [finish(ANSWER)])
    page: Page = await _open(browsers, served, "wide", f"/runs/{run.id}")
    answer: Locator = page.get_by_role("article", name="Answer")
    await expect(answer).to_contain_text(ANSWER)
    button: Locator = answer.get_by_role("button", name="React to this message")
    # Tab reaches the button from the answer's copy button, and Enter opens the palette on its first choice.
    await answer.get_by_role("button", name="Copy the answer").focus()
    for _ in range(3):
        await page.keyboard.press("Tab")
        if await button.evaluate("node => node === document.activeElement"):
            break
    await expect(button).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(_palette(page)).to_be_visible()
    await expect(_choice(page, "Good")).to_be_focused()
    # The arrow keys, Home and End move between the choices and wrap round.
    await page.keyboard.press("ArrowRight")
    await expect(_choice(page, "Love it")).to_be_focused()
    await page.keyboard.press("End")
    await expect(_choice(page, "Hold on")).to_be_focused()
    await page.keyboard.press("ArrowRight")
    await expect(_choice(page, "Good")).to_be_focused()
    await page.keyboard.press("ArrowLeft")
    await expect(_choice(page, "Hold on")).to_be_focused()
    await page.keyboard.press("Home")
    await expect(_choice(page, "Good")).to_be_focused()
    # Escape closes it and gives the focus back to the button.
    await page.keyboard.press("Escape")
    await expect(_palette(page)).to_be_hidden()
    await expect(button).to_be_focused()
    # Space opens it too; Enter on a choice puts the reaction on, closes the palette and gives the focus back.
    await page.keyboard.press("Space")
    await expect(_palette(page)).to_be_visible()
    await page.keyboard.press("ArrowRight")
    await page.keyboard.press("Enter")
    await expect(_palette(page)).to_be_hidden()
    await expect(button).to_be_focused()
    chip: Locator = answer.get_by_role("button", name="Love it", pressed=True)
    await expect(chip).to_be_visible()
    assert await _standing(served, run) == ["love"]
    # The chip is a button of the tab order, and Enter on it takes the reaction back.
    await chip.focus()
    await page.keyboard.press("Enter")
    await expect(chip).to_have_count(0)
    # Tab leaves a palette and closes it.
    await button.focus()
    await page.keyboard.press("Enter")
    await expect(_palette(page)).to_be_visible()
    await page.keyboard.press("Tab")
    await expect(_palette(page)).to_be_hidden()
    assert await _standing(served, run) == []
    await page.context.close()


async def test_the_palette_closes_by_a_click_outside_and_one_palette_is_open_at_a_time(
    browsers: Browsers, served: Served, stage: Stage
) -> None:
    first: RunRecord = await _answered(served, stage, [finish(ANSWER)])
    second: RunRecord = await _answered(served, stage, [finish("The second answer.")], first.thread_id)
    page: Page = await _open(browsers, served, "wide", f"/runs/{second.id}")
    answers: Locator = page.get_by_role("article", name="Answer")
    await expect(answers).to_have_count(2)
    await answers.first.get_by_role("button", name="React to this message").click()
    await expect(_palette(page)).to_have_count(1)
    await answers.last.get_by_role("button", name="React to this message").click()
    await expect(_palette(page)).to_have_count(1)
    await expect(answers.first.get_by_role("button", name="React to this message")).to_have_attribute(
        "aria-expanded", "false"
    )
    await expect(answers.last.get_by_role("button", name="React to this message")).to_have_attribute(
        "aria-expanded", "true"
    )
    await page.locator("h1").first.click(force=True)
    await expect(_palette(page)).to_be_hidden()
    await answers.last.get_by_role("button", name="React to this message").click()
    await expect(_palette(page)).to_be_visible()
    await answers.last.get_by_role("button", name="React to this message").click()
    await expect(_palette(page)).to_be_hidden()
    assert await _standing(served, first) == [] and await _standing(served, second) == []
    await page.context.close()


# ------------------------------------------------------------------------------------------------- at work


async def _working(served: Served, stage: Stage, turns: list[ScriptedTurn], held: set[int]) -> tuple[RunRecord, Gated]:
    """A run of the scripted ``turns`` that has said its first words and is at its second ask, which waits."""
    model: Gated = stage.load(turns, held)
    run: RunRecord = await _start(served)
    await model.started(2)
    return run, model


@pytest.mark.parametrize("look", LOOKS)
async def test_what_a_run_said_has_a_foot_with_the_reactions_while_it_works_and_a_not_right_reaches_it(
    browsers: Browsers, served: Served, stage: Stage, look: Look
) -> None:
    run, model = await _working(served, stage, [says(SAID), finish("Forty shelves.")], {2})
    page: Page = await _open(browsers, served, look, f"/runs/{run.id}")
    said: Locator = page.locator(".bubble.say").filter(has_text=SAID)
    foot: Locator = said.locator(".say-foot")
    await expect(foot).to_be_visible()
    await _react(page, foot, "Not right")
    await expect(foot.get_by_role("button", name="Not right", pressed=True)).to_be_visible()
    assert await _standing(served, run, "step-1") == ["wrong"]
    # The step the model was at is given up, and asked again with the note.
    await model.started(3)
    assert f"The owner reacted 👎 to your message «{SAID}»" in model.told(3)
    await expect(page.get_by_role("article", name="Answer")).to_contain_text("Forty shelves.")
    # The note is no message of the owner's, and no line of the conversation.
    await expect(page.get_by_text("[system note]")).to_have_count(0)
    # What the run said keeps its foot and its chip once the run ended.
    await expect(foot.get_by_role("button", name="Not right", pressed=True)).to_be_visible()
    await _fits(page, f"{look} at the end of the run")
    await page.context.close()


async def test_a_hold_from_the_palette_holds_the_run_until_the_owner_writes_and_the_message_is_not_queued(
    browsers: Browsers, served: Served, stage: Stage, tmp_path: Path
) -> None:
    run, model = await _working(served, stage, [says(SAID), finish("Forty shelves.")], {2})
    page: Page = await _open(browsers, served, "wide", f"/runs/{run.id}")
    foot: Locator = page.locator(".bubble.say").filter(has_text=SAID).locator(".say-foot")
    await _react(page, foot, "Hold on")
    await expect(
        page.locator(".note", has_text="You held the task (✋): it waits for your next message.")
    ).to_be_visible()
    box: Locator = page.locator(MESSAGE)
    await expect(box).to_have_attribute("placeholder", re.compile("on hold"))
    await page.screenshot(path=str(tmp_path / "hold-wide.png"))
    await asyncio.sleep(REST_S)
    assert len(model.asks) == 2
    assert served.manager.holding(run.id)
    # What the owner writes goes to the run at once, not into a queue behind a run that waits for it.
    await box.fill("Use the other folder.")
    await box.press("Enter")
    await expect(page.locator(".note", has_text="You wrote to the task, and it goes on.")).to_be_visible()
    await expect(page.get_by_role("article", name="Answer")).to_contain_text("Forty shelves.")
    assert "Use the other folder." in model.told(3)
    assert await served.runtime.store.list_queued(run.thread_id) == []
    await expect(foot.get_by_role("button", name="Hold on", pressed=True)).to_be_visible()
    await page.context.close()


async def test_a_hold_taken_back_by_its_chip_lets_the_run_go_on(
    browsers: Browsers, served: Served, stage: Stage
) -> None:
    run, model = await _working(served, stage, [says(SAID), finish("Forty shelves.")], {2})
    page: Page = await _open(browsers, served, "wide", f"/runs/{run.id}")
    foot: Locator = page.locator(".bubble.say").filter(has_text=SAID).locator(".say-foot")
    await _react(page, foot, "Hold on")
    await expect(page.locator(".note", has_text="You held the task")).to_be_visible()
    await foot.get_by_role("button", name="Hold on", pressed=True).click()
    await expect(page.locator(".note", has_text="You took the ✋ back, and the task goes on.")).to_be_visible()
    await expect(page.get_by_role("article", name="Answer")).to_contain_text("Forty shelves.")
    assert "took back their ✋" in model.told(3)
    await expect(page.locator(MESSAGE)).not_to_have_attribute("placeholder", re.compile("on hold"))
    await page.context.close()


async def test_a_note_told_to_a_run_is_no_line_in_the_turns_before_a_later_run_either(
    browsers: Browsers, served: Served, stage: Stage
) -> None:
    run, model = await _working(served, stage, [says(SAID), finish("Forty shelves.")], {2})
    await served.reactions.add(run.id, "step-1", "wrong")
    await model.started(3)
    async with asyncio.timeout(WAIT_S):
        await served.manager.wait(run.id)
    told = await served.runtime.store.events_of_kind(run.id, "steer", 10)
    assert [(event.data.get("vouches"), event.data.get("reaction")) for event in told] == [(False, "note")]
    second: RunRecord = await _answered(served, stage, [finish("The second answer.")], run.thread_id)
    page: Page = await _open(browsers, served, "wide", f"/runs/{second.id}")
    earlier: Locator = page.locator(".history")
    await expect(earlier.get_by_role("article", name="Answer")).to_contain_text("Forty shelves.")
    # What the run said is drawn with the chip of the reaction on it; the note the run was told is no line of the turn,
    # as it is none on the run's own page.
    said: Locator = earlier.locator(".turn-say").filter(has_text=SAID)
    await expect(said.get_by_role("button", name="Not right", pressed=True)).to_be_visible()
    await expect(earlier.get_by_text("[system note]")).to_have_count(0)
    await expect(earlier.locator(".note")).to_have_count(0)
    await page.context.close()


# ---------------------------------------------------------------------------------------------- after the run


@pytest.mark.parametrize("look", LOOKS)
async def test_a_not_right_on_an_answer_starts_a_follow_up_run_the_page_goes_on_at(
    browsers: Browsers, served: Served, stage: Stage, look: Look
) -> None:
    run: RunRecord = await _answered(served, stage, [finish(ANSWER), finish("Reconsidered: the beds face east.")])
    page: Page = await _open(browsers, served, look, f"/runs/{run.id}")
    answer: Locator = page.get_by_role("article", name="Answer")
    await expect(answer).to_contain_text(ANSWER)
    await _react(page, answer, "Not right")
    await expect(page.locator(".toast")).to_contain_text("Started a follow-up run.")
    # The chat goes on at the run the reaction started, as it does for a message typed in the box.
    await expect(page).to_have_url(re.compile(r"#/runs/(?!" + re.escape(run.id) + r")run_"))
    await expect(page.get_by_role("article", name="Answer").last).to_contain_text("Reconsidered: the beds face east.")
    # The follow-up draws no message of yours, as a messaging app sends none for a reaction: the chip on the answer
    # reacted to says it (item 844). The message you wrote first stays.
    await expect(page.locator(".user-message .bubble", has_text=THUMB_DOWN)).to_have_count(0)
    await expect(page.locator(".user-message .bubble", has_text=GOAL)).to_have_count(1)
    reacted: Locator = page.get_by_role("article", name="Answer").first
    await expect(reacted.get_by_role("button", name="Not right", pressed=True)).to_be_visible()
    runs: list[RunRecord] = await served.runtime.store.list_runs(10, run.thread_id)
    assert [one.goal for one in runs] == [THUMB_DOWN, GOAL]
    standing: list[ReactionRecord] = await served.runtime.store.message_reactions(run.id, "answer")
    assert [(record.reaction, record.follow_up_run_id) for record in standing] == [("wrong", runs[0].id)]
    await _fits(page, f"{look} after the follow-up")
    await page.context.close()


async def test_a_queued_follow_up_sent_on_with_steer_is_drawn_as_the_owners_message_and_stays_so_in_the_turns_before(
    browsers: Browsers, served: Served, stage: Stage
) -> None:
    first: RunRecord = await _answered(served, stage, [finish(ANSWER)])
    model: Gated = stage.load([says(SAID), finish("Done.")], {2})
    working: RunRecord = await _start(served, first.thread_id)
    await model.started(2)
    page: Page = await _open(browsers, served, "wide", f"/runs/{working.id}")
    answer: Locator = page.locator(".history").get_by_role("article", name="Answer")
    await expect(answer).to_contain_text(ANSWER)
    # A run works in the chat, so the follow-up of the reaction waits in the queue.
    await _react(page, answer, "Not right")
    await expect(page.locator(".toast")).to_contain_text("Queued")
    row: Locator = page.locator(".queue-row", has_text="My reaction")
    await expect(row).to_be_visible()
    await row.get_by_role("button", name="Steer").click()
    # The press is theirs: its words stand in the conversation of the run at work as their message, and the note the run
    # read around them is no line.
    sent: Locator = page.locator(".user-message .bubble", has_text=THUMB_DOWN)
    await expect(sent).to_have_count(1)
    await expect(page.get_by_role("article", name="Answer").last).to_contain_text("Done.")
    await expect(page.get_by_text("[system note]")).to_have_count(0)
    # It was read, so nothing waits to be sent and nothing says the run ended before it read it.
    await expect(page.get_by_text("Sending to the run")).to_have_count(0)
    await expect(page.get_by_text("The run ended before")).to_have_count(0)
    assert await served.runtime.store.list_queued(first.thread_id) == []
    steered = await served.runtime.store.events_of_kind(working.id, "steer", 10)
    assert [
        (event.data.get("vouches"), event.data.get("reaction"), event.data.get("owner_text")) for event in steered
    ] == [(False, None, THUMB_DOWN)]
    # In the turns before a later run it stands as theirs too.
    third: RunRecord = await _answered(served, stage, [finish("The third answer.")], first.thread_id)
    later: Page = await _open(browsers, served, "wide", f"/runs/{third.id}")
    await expect(later.locator(".history .user-message .bubble", has_text=THUMB_DOWN)).to_have_count(1)
    await expect(later.get_by_text("[system note]")).to_have_count(0)
    await page.context.close()
    await later.context.close()


async def test_a_pin_keeps_the_message_as_a_note_and_taking_it_back_forgets_the_note(
    browsers: Browsers, served: Served, stage: Stage
) -> None:
    run: RunRecord = await _answered(served, stage, [finish("Use pnpm, not npm, in this repo.")])
    page: Page = await _open(browsers, served, "wide", f"/runs/{run.id}")
    answer: Locator = page.get_by_role("article", name="Answer")
    await _react(page, answer, "Remember this")
    await expect(page.locator(".toast")).to_contain_text("Kept as a note.")
    pinned: Locator = answer.get_by_role("button", name="Remember this", pressed=True)
    await expect(pinned).to_be_visible()
    notes = await served.runtime.memory.store.list_memories(50, 0)
    assert [note.content for note in notes if note.tags == ("from-chat",)] == ["Use pnpm, not npm, in this repo."]
    await pinned.click()
    await expect(pinned).to_have_count(0)
    assert [
        note for note in await served.runtime.memory.store.list_memories(50, 0) if note.tags == ("from-chat",)
    ] == []
    await page.context.close()


# ------------------------------------------------------------------------------------------------- settings


def _settings_card(page: Page) -> Locator:
    return page.locator("section.card").filter(has=page.locator(".card-head", has_text="Reactions"))


def _saved(served: Served) -> dict[str, object]:
    return tomllib.loads((served.home / "config.toml").read_text(encoding="utf-8")).get("reactions", {})


@pytest.mark.parametrize("look", LOOKS)
async def test_settings_change_what_each_reaction_does_after_a_run_and_whether_it_starts_one_at_all(
    browsers: Browsers, served: Served, look: Look
) -> None:
    page: Page = await _open(browsers, served, look, "/settings/general")
    card: Locator = _settings_card(page)
    await expect(card).to_be_visible()
    # The card folds, shut until it is opened, its head saying what a reaction on a task that ended does.
    fold: Locator = card.get_by_role("button", name=re.compile(r"^Reactions"))
    await expect(fold).to_have_attribute("aria-expanded", "false")
    await expect(fold).to_contain_text("Follow-up tasks on")
    await expect(card.get_by_role("switch", name="Start a task for a reaction")).to_be_hidden()
    await fold.click()
    await expect(fold).to_have_attribute("aria-expanded", "true")
    switch: Locator = card.get_by_role("switch", name="Start a task for a reaction")
    await expect(switch).to_have_attribute("aria-checked", "true")
    names: object = await card.get_by_role("combobox").evaluate_all(
        "nodes => nodes.map((node) => node.getAttribute('aria-label'))"
    )
    assert names == [
        f"{name}, after the task has ended"
        for name in ("Good", "Love it", "Not right", "Do it again", "Explain", "Hold on")
    ]
    wrong: Locator = card.get_by_role("combobox", name="Not right, after the task has ended")
    await expect(wrong).to_have_value("follow_up")
    await expect(card.get_by_role("combobox", name="Good, after the task has ended")).to_have_value("feedback")
    await wrong.select_option("feedback")
    await until(lambda: _saved(served).get("wrong") == "feedback")
    assert served.reactions.config.wrong == "feedback"
    await _fits(page, f"{look} settings")
    # The page asks the daemon again when it is opened: what was chosen is what it shows, in the card as it was left.
    await page.reload()
    await expect(fold).to_have_attribute("aria-expanded", "true")
    await expect(wrong).to_have_value("feedback")
    # The switch turns every follow-up off, and its choices wait, as they were, for it to be turned on again.
    await switch.click()
    await expect(switch).to_have_attribute("aria-checked", "false")
    await expect(wrong).to_be_disabled()
    await expect(fold).to_contain_text("Only feedback")
    await until(lambda: _saved(served).get("follow_ups") is False)
    assert served.reactions.config.follow_ups is False
    await switch.click()
    await expect(switch).to_have_attribute("aria-checked", "true")
    await expect(wrong).to_be_enabled()
    await wrong.select_option("follow_up")
    await until(lambda: _saved(served).get("wrong") == "follow_up")
    assert _saved(served) == {"wrong": "follow_up", "follow_ups": True}
    assert served.reactions.config.wrong == "follow_up"
    await page.context.close()
