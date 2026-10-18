"""The marks of a conversation's messages (item 201): a column of short marks at the left of the conversation, one for
each message of the owner. Resting the pointer on it shows the message nearest to it and the start of its answer; a
click, or Enter after the arrows, goes to that message; the marks of the messages in sight stand out. On a phone, or in
a conversation of one message, there is no column. Beside the side panel, the conversation leaves the column its room
(item 228), and neither covers the other. A real server on a scripted model, wide and on a phone, light and dark."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

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
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "outline-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# Each message of the chat and the first words of its answer; every answer is long, so the chat is taller than the
# window.
_TURNS: tuple[tuple[str, str], ...] = (
    ("Plan the garden beds.", "The beds face south"),
    ("Which seeds go in first?", "Peas and spinach go in first"),
    ("How deep is the soil?", "Thirty centimetres is enough"),
    ("When do the tomatoes go out?", "After the last frost"),
    ("Draw the watering rota.", "Water every other morning"),
)
_FILLER: str = "\n\n".join(
    f"Paragraph {n} of the plan, long enough to fill a line of the conversation." for n in range(8)
)
# A one-message chat, to show that it has no marks.
_ALONE: str = "Name the cat."
_RUN_WAIT_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, on a scripted model that answers each message of the chat at its first step."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    answers: list[ScriptedTurn] = [_finish(f"{start}.\n\n{_FILLER}") for _, start in _TURNS] + [_finish("Miso.")]
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, answers))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
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


async def _answered(runtime: Runtime, manager: RunManager, goal: str, thread_id: str | None) -> RunRecord:
    """Send ``goal`` to the chat ``thread_id`` (a new one for ``None``) and wait until the run has answered it."""
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, thread_id, "test", None, None, "standard", None, "yolo", False, ()
    )
    deadline: float = time.monotonic() + _RUN_WAIT_S
    while time.monotonic() < deadline:
        ended: RunRecord = await runtime.store.get_run(run.id)
        if ended.status == "done":
            return ended
        assert ended.status in ("queued", "running"), ended.status
        await asyncio.sleep(0.1)
    raise AssertionError(f"the run {run.id} did not end in {_RUN_WAIT_S} s")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chats(runtime: Runtime, served: tuple[RunManager, str]) -> tuple[RunRecord, RunRecord]:
    """The last run of a chat of five messages, and the run of a chat of one."""
    manager, _ = served
    last: RunRecord = await _answered(runtime, manager, _TURNS[0][0], None)
    for goal, _ in _TURNS[1:]:
        last = await _answered(runtime, manager, goal, last.thread_id)
    alone: RunRecord = await _answered(runtime, manager, _ALONE, None)
    return last, alone


async def _run_page(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]
) -> tuple[Page, list[str]]:
    """The page of the run ``run_id``, signed in, in a browser of its own, with the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    await expect(page.locator(".answer").last).to_contain_text(_TURNS[-1][1])
    return page, errors


def _message(page: Page, index: int) -> Locator:
    return page.locator(".run-thread .bubble.user").nth(index)


async def _clear_of_the_conversation(page: Page) -> None:
    """Wait until the column ends where the conversation begins, or before: it never covers its text."""
    await page.wait_for_function(
        "() => document.querySelector('.outline-rail').getBoundingClientRect().right"
        " <= document.querySelector('.run-main').getBoundingClientRect().left"
    )


async def _clear_of_the_panel(page: Page) -> None:
    """The conversation ends where the handle of the side panel begins, or before: giving the column its room, it does
    not run under the panel."""
    await page.wait_for_function(
        "() => document.querySelector('.run-main').getBoundingClientRect().right"
        " <= document.querySelector('.stage-handle').getBoundingClientRect().left"
    )


async def _rest_on(page: Page, index: int) -> None:
    """Rest the pointer on the column at the height of the mark ``index``."""
    box = await page.locator(".outline-rail").bounding_box()
    assert box is not None
    along: float = index / (len(_TURNS) - 1)
    await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + along * box["height"])


async def _counted_again(page: Page) -> None:
    """Change the conversation as a run that writes does, and wait for the column to count its marks again."""
    await page.locator(".outline-mark").evaluate_all(
        "(marks) => marks.forEach((mark) => { mark.dataset.before = ''; })"
    )
    await page.locator(".run-thread").evaluate("(thread) => thread.append(document.createElement('div'))")
    await expect(page.locator(".outline-mark[data-before]")).to_have_count(0)


async def test_the_marks_show_each_message_and_go_to_it(
    runtime: Runtime, served: tuple[RunManager, str], chats: tuple[RunRecord, RunRecord], tmp_path: Path
) -> None:
    _, address = served
    last, _ = chats
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for width, scheme in _SHOTS:
                page, errors = await _run_page(browser, address, last.id, width, scheme)
                outline: Locator = page.locator(".outline")
                if width < 720:
                    # A phone has no room at the left of the conversation.
                    await expect(page.locator(".run-thread .bubble.user")).to_have_count(len(_TURNS))
                    await expect(_message(page, len(_TURNS) - 1)).to_be_in_viewport()
                    await expect(outline).to_be_hidden()
                    await page.screenshot(path=str(tmp_path / f"outline-{width}-{scheme}.png"), animations="disabled")
                    assert errors == []
                    await page.context.close()
                    continue

                # One mark for each message, the column clear of the conversation; the last message is in sight.
                await expect(outline).to_be_visible()
                marks: Locator = page.locator(".outline-mark")
                await expect(marks).to_have_count(len(_TURNS))
                await _clear_of_the_conversation(page)
                await expect(marks.last).to_have_attribute("data-seen", "true")
                await expect(marks.first).to_have_attribute("data-seen", "false")

                # Resting on the second mark shows that message and the start of its answer.
                await _rest_on(page, 1)
                preview: Locator = page.locator(".outline-preview")
                await expect(preview).to_be_visible()
                await expect(page.locator(".outline-asked")).to_have_text(_TURNS[1][0])
                await expect(page.locator(".outline-reply")).to_contain_text(f"{_TURNS[1][1]}. Paragraph 0 of the plan")
                await expect(marks.nth(1)).to_have_attribute("data-near", "0")
                await expect(marks.nth(2)).to_have_attribute("data-near", "1")
                await page.screenshot(path=str(tmp_path / f"outline-{width}-{scheme}.png"), animations="disabled")

                # The conversation changes while the pointer rests, as when a run writes: the marks are counted again,
                # and leaving the column still puts the preview away.
                await _counted_again(page)
                await page.mouse.move(4, 400)
                await expect(preview).to_be_hidden()
                await _rest_on(page, 1)
                await expect(preview).to_be_visible()

                # A click goes to it; the mark of that message stands out then.
                await expect(_message(page, 1)).not_to_be_in_viewport()
                await page.mouse.down()
                await page.mouse.up()
                await expect(_message(page, 1)).to_be_in_viewport()
                await expect(marks.nth(1)).to_have_attribute("data-seen", "true")
                await page.mouse.move(4, 400)
                await expect(preview).to_be_hidden()

                # From the keyboard: the column takes the focus, the arrows walk the marks, and Enter goes.
                await page.locator(".outline-rail").focus()
                await page.keyboard.press("End")
                await expect(page.locator(".outline-asked")).to_have_text(_TURNS[-1][0])
                await page.keyboard.press("ArrowUp")
                await expect(page.locator(".outline-asked")).to_have_text(_TURNS[-2][0])
                await expect(page.locator(".outline-rail")).to_have_attribute(
                    "aria-label", f"Jump to message: {_TURNS[-2][0]}"
                )
                await page.keyboard.press("Enter")
                await expect(_message(page, len(_TURNS) - 2)).to_be_in_viewport()
                assert errors == []
                await page.context.close()
        finally:
            await browser.close()


async def test_the_conversation_leaves_the_column_its_room_beside_the_side_panel_and_one_message_has_none(
    runtime: Runtime, served: tuple[RunManager, str], chats: tuple[RunRecord, RunRecord], tmp_path: Path
) -> None:
    _, address = served
    last, alone = chats
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _run_page(browser, address, last.id, 1280, "light")
            await expect(page.locator(".outline")).to_be_visible()

            # Beside the side panel the conversation gives the column its room at the left: the column stays, clear of
            # the conversation, which stays clear of the panel's handle.
            details: Locator = page.get_by_role("button", name="Run details")
            await details.click()
            await expect(page.locator(".run-layout")).to_have_attribute("data-inspector", "on")
            await expect(page.locator(".outline")).to_be_visible()
            await _clear_of_the_conversation(page)
            await _clear_of_the_panel(page)
            await _rest_on(page, 1)
            await expect(page.locator(".outline-preview")).to_be_visible()
            await page.screenshot(path=str(tmp_path / "outline-1280-light-panel.png"), animations="disabled")
            await details.click()
            await expect(page.locator(".run-layout")).to_have_attribute("data-inspector", "off")
            await expect(page.locator(".outline")).to_be_visible()

            # So does a wider window, where the conversation had the room already.
            wide, wide_errors = await _run_page(browser, address, last.id, 1512, "dark")
            await wide.get_by_role("button", name="Run details").click()
            await expect(wide.locator(".run-layout")).to_have_attribute("data-inspector", "on")
            await expect(wide.locator(".outline")).to_be_visible()
            await _clear_of_the_conversation(wide)
            await _clear_of_the_panel(wide)
            await _rest_on(wide, 2)
            await expect(wide.locator(".outline-preview")).to_be_visible()
            await wide.screenshot(path=str(tmp_path / "outline-1512-dark-panel.png"), animations="disabled")
            assert wide_errors == []

            # A chat of one message has nothing to walk.
            await page.goto(f"{address}/#/runs/{alone.id}")
            await expect(page.locator(".answer").last).to_contain_text("Miso.")
            await expect(page.locator(".outline")).to_be_hidden()
            assert errors == []
        finally:
            await browser.close()
