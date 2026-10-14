"""The feet of a conversation's messages (item 205), after T3 Code's: under each of the owner's messages, once its turn
has its answer, when it was sent, a copy of it as it was written and a button that sends it again with its files; under
each answer, a copy of it and when it came. A turn at work keeps the feet of its messages back until its answer is in;
sending a message again leaves what is typed in the composer. A real server on a scripted model, wide and on a phone,
light and dark."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.attachments import StoredAttachment, store_attachment
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
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "message-feet-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FIRST: str = "Plan the garden beds."
_FIRST_ANSWER: str = "The beds **face south**, with paths between them."
_SECOND: str = "Which seeds go in first?"
_STEER: str = "Add the beans too."
_AGAIN_ANSWER: str = "Again: the beds face south."
_DRAFT: str = "A draft for later"
# The second message works in a short command, while it is steered, then in a long one, until it is stopped.
_SHORT_WAIT: str = "sleep 5"
_LONG_WAIT: str = "sleep 300"
_STEP_WAIT_S: float = 20.0
_RUN_WAIT_S: float = 30.0
_EDGE_PX: float = 12.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "dark"), (390, "light"))
_TODAY_TIME: re.Pattern[str] = re.compile(r"^\d{2}:\d{2}$")


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


def _shell(command: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": command}),))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home, on a scripted model: the first message is answered at once, the second works
    until it is stopped, and the first sent again is answered at once."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    answers: list[ScriptedTurn] = [
        _finish(_FIRST_ANSWER),
        _shell(_SHORT_WAIT),
        _shell(_LONG_WAIT),
        _finish(_AGAIN_ANSWER),
    ]
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


async def _events(runtime: Runtime, run_id: str, kind: str, count: int) -> list[EventRecord]:
    """The first ``count`` events of ``kind`` of the run ``run_id``, once they are stored."""
    deadline: float = time.monotonic() + _STEP_WAIT_S
    while time.monotonic() < deadline:
        found: list[EventRecord] = await runtime.store.events_of_kind(run_id, kind, count)
        if len(found) >= count:
            return found
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} had no {count} {kind} events in {_STEP_WAIT_S} s")


async def _ended(runtime: Runtime, run_id: str) -> RunRecord:
    deadline: float = time.monotonic() + _RUN_WAIT_S
    while time.monotonic() < deadline:
        run: RunRecord = await runtime.store.get_run(run_id)
        if run.status in ("done", "failed", "cancelled"):
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"the run {run_id} did not end in {_RUN_WAIT_S} s")


async def _page(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]
) -> tuple[Page, list[str]]:
    """The page of the run ``run_id``, signed in, in a browser of its own (one that lets a test read the clipboard where
    the browser allows it), with the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    readable: bool = browser.browser_type.name == "chromium"
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 800},
        color_scheme=scheme,
        reduced_motion="reduce",
        permissions=["clipboard-read", "clipboard-write"] if readable else [],
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page, errors


async def _clipboard(page: Page) -> str | None:
    """What the clipboard holds, or None where the browser does not let a test read it."""
    if page.context.browser is None or page.context.browser.browser_type.name != "chromium":
        return None
    return str(await page.evaluate("() => navigator.clipboard.readText()"))


def _file_ids(event: EventRecord) -> list[str]:
    """The ids of the files an ``attachments`` event lists."""
    files = event.data["files"]
    assert isinstance(files, list), files
    return [str(entry["id"]) for entry in files if isinstance(entry, dict)]


async def _copies(page: Page, button: Locator, label: str, text: str) -> None:
    """``button`` puts ``text`` on the clipboard and says so with a check for a moment, then reads ``label`` again.
    ``button`` is found by its place, as its name changes."""
    await expect(button).to_have_attribute("aria-label", label)
    await button.click()
    await expect(button).to_have_attribute("aria-label", "Copied")
    copied: str | None = await _clipboard(page)
    assert copied is None or copied == text, copied
    await expect(button).to_have_attribute("aria-label", label, timeout=5000)


async def _edges(foot: Locator, bubble: Locator) -> tuple[float, float]:
    """The right edges of a message's bubble and of the last icon of its foot (the icon, not its button's padding)."""
    bubble_box = await bubble.bounding_box()
    icon_box = await foot.locator(".btn").last.locator("svg").bounding_box()
    assert bubble_box is not None and icon_box is not None
    return bubble_box["x"] + bubble_box["width"], icon_box["x"] + icon_box["width"]


async def test_the_messages_have_their_time_a_copy_and_a_send_again_once_answered(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    file: StoredAttachment = await asyncio.to_thread(
        store_attachment, runtime.workspace, "beds.txt", "text/plain", b"North bed, south bed.\n"
    )
    first: RunRecord = await manager.start(
        _FIRST, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", "yolo", False, (file,)
    )
    assert (await _ended(runtime, first.id)).status == "done"
    second: RunRecord = await manager.start(
        _SECOND, "task", SCRIPTED_MODEL, first.thread_id, "test", None, None, "standard", "yolo", False, ()
    )
    await _events(runtime, second.id, "tool_call", 1)
    manager.steer(second.id, _STEER, ())
    await _events(runtime, second.id, "steer", 1)
    await _events(runtime, second.id, "tool_call", 2)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, address, second.id, 1280, "light")
            earlier: Locator = page.locator(".history .user-message")
            goal: Locator = page.locator(".run-thread > .user-message")
            steered: Locator = page.locator(".timeline .user-message")
            await expect(steered).to_contain_text(_STEER)
            # The earlier turn has its answer: its message shows when it was sent, a copy and a send again.
            await expect(earlier.locator(".message-foot")).to_be_visible()
            sent: Locator = earlier.locator(".message-time")
            await expect(sent).to_have_text(_TODAY_TIME)
            assert re.search(r"\d{4}", await sent.get_attribute("title") or ""), "no full date where the pointer rests"
            await expect(earlier.get_by_role("button", name="Copy the message")).to_be_visible()
            await expect(earlier.get_by_role("button", name="Send again")).to_be_visible()
            # Its answer shows a copy and when it came; how long the run took waits for the pointer.
            answer: Locator = page.locator(".history article.answer")
            await expect(answer.get_by_role("button", name="Copy the answer")).to_be_visible()
            await expect(answer.locator(".message-time")).to_have_text(_TODAY_TIME)
            assert await answer.locator(".answer-took").evaluate("node => getComputedStyle(node).opacity") == "0"
            # The run at work keeps the feet of its goal and of the message that steered it back.
            await expect(goal.locator(".bubble.user")).to_have_text(_SECOND)
            await expect(goal.locator(".message-foot")).to_be_hidden()
            await expect(steered.locator(".message-foot")).to_be_hidden()
            # Stopped, the run has its answer: the feet show, without a reload.
            manager.cancel(second.id)
            assert (await _ended(runtime, second.id)).status == "cancelled"
            await expect(goal.locator(".message-foot")).to_be_visible()
            await expect(steered.locator(".message-foot")).to_be_visible()
            bubble_edge, icon_edge = await _edges(goal.locator(".message-foot"), goal.locator(".bubble.user"))
            assert abs(bubble_edge - icon_edge) <= _EDGE_PX, (bubble_edge, icon_edge)
            # A copy takes the message, and the answer, as they were written.
            await _copies(page, goal.locator(".message-foot button").first, "Copy the message", _SECOND)
            await _copies(page, steered.locator(".message-foot button").first, "Copy the message", _STEER)
            await _copies(page, answer.locator(".answer-foot button").first, "Copy the answer", _FIRST_ANSWER)
            # Sent again, the first message starts the chat's next run with its file; what is typed in the composer stays.
            composer: Locator = page.locator(".dock textarea")
            await composer.fill(_DRAFT)
            await earlier.get_by_role("button", name="Send again").click()
            await expect(page).not_to_have_url(f"{address}/#/runs/{second.id}")
            again_id: str = page.url.rpartition("/")[2]
            again: RunRecord = await _ended(runtime, again_id)
            assert (again.goal, again.thread_id, again.status) == (_FIRST, first.thread_id, "done")
            files: list[EventRecord] = await _events(runtime, again.id, "attachments", 1)
            assert _file_ids(files[0]) == [file.id]
            # The file stands under the message it came with, over its foot.
            sent_again: Locator = page.locator(".run-thread > .user-message")
            await expect(sent_again.locator(".bubble.user")).to_have_text(_FIRST)
            await expect(sent_again.locator(".attach-list.sent")).to_contain_text("beds.txt")
            await expect(sent_again.locator(".message-foot")).to_be_visible()
            parts: list[str] = await sent_again.evaluate("node => [...node.children].map((child) => child.className)")
            assert parts == ["bubble user", "attach-list sent", "message-foot"], parts
            await page.screenshot(path=str(tmp_path / "feet-1280-light.png"), animations="disabled")
            await page.goto(f"{address}/#/runs/{second.id}")
            await expect(page.locator(".dock textarea")).to_have_value(_DRAFT)
            # The time of a message is as short as its day allows.
            shown: list[str] = await page.evaluate(
                """async () => {
                    const { messageTime } = await import("/js/dom.js");
                    const now = new Date();
                    const day = (back, hours, minutes) =>
                        new Date(now.getFullYear(), now.getMonth(), now.getDate() - back, hours, minutes).getTime() / 1000;
                    return [messageTime(day(0, 0, 5)), messageTime(day(1, 14, 3)), messageTime(new Date(2020, 2, 5, 14, 3).getTime() / 1000)];
                }"""
            )
            assert shown == ["00:05", "Yesterday, 14:03", "5 Mar 2020, 14:03"], shown
            assert errors == []
            await page.context.close()
            # The look, wide in the dark and on a phone: the chat that was sent again, its feet under its messages.
            for width, scheme in _SHOTS:
                look, look_errors = await _page(browser, address, again.id, width, scheme)
                messages: Locator = look.locator(".run-thread .user-message")
                await expect(messages).to_have_count(3)
                for index in range(3):
                    message: Locator = messages.nth(index)
                    await expect(message.locator(".message-foot")).to_be_visible()
                    bubble_edge, icon_edge = await _edges(
                        message.locator(".message-foot"), message.locator(".bubble.user")
                    )
                    assert abs(bubble_edge - icon_edge) <= _EDGE_PX, (width, index, bubble_edge, icon_edge)
                await expect(look.locator("article.answer .message-time")).to_have_count(3)
                await look.screenshot(path=str(tmp_path / f"feet-{width}-{scheme}.png"), animations="disabled")
                assert look_errors == []
                await look.context.close()
        finally:
            await browser.close()
