"""A chat reached from the side menu opens at its foot, as one opened by its address does (item 446): a chat that is
over, one with earlier turns, one whose run is still at work, and one opened before and drawn again from what was kept;
wide in Chrome and on a phone in WebKit. A real server on a scripted model."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright

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

TOKEN: str = "chat-switch-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_LONG: str = "\n\n".join(
    f"Paragraph {index}: the sea keeps its own time, and the shore answers a little later every century."
    for index in range(40)
)
_GOALS: tuple[str, str, str] = (
    "First chat: write about the sea.",
    "Second chat: write about the shore.",
    "Second chat, once more: and about the tide.",
)
_RUN_WAIT_S: float = 30.0
# What the page may still move once it stands: a pixel or two of rounding.
_FOOT_SLACK_PX: int = 2
_LOOKS: tuple[tuple[str, int], ...] = (("chromium", 1280), ("webkit", 390))


def _finish() -> ScriptedTurn:
    return ScriptedTurn(
        text="",
        calls=(
            ScriptedCall(tool="shell", arguments={"command": "echo the tide is in"}),
            ScriptedCall(tool=FINISH_TOOL, arguments={"answer": _LONG, "success": True}),
        ),
    )


def _wait(flag: Path) -> ScriptedTurn:
    """Words as long as an answer, then a shell command that waits until ``flag`` is there."""
    command: str = f"while [ ! -e {flag} ]; do sleep 0.2; done"
    return ScriptedTurn(text=_LONG, calls=(ScriptedCall(tool="shell", arguments={"command": command}),))


def _script(flags: tuple[Path, ...]) -> list[ScriptedTurn]:
    """Three runs that end with a long answer; then, one look after the other, a run that writes as much, waits for the
    look's flag and ends with the long answer."""
    return [_finish(), _finish(), _finish(), *(turn for flag in flags for turn in (_wait(flag), _finish()))]


@pytest.fixture(scope="module")
def flags(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, ...]:
    folder: Path = tmp_path_factory.mktemp("flags")
    return tuple(folder / f"flag-{index}" for index in range(len(_LOOKS)))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, flags: tuple[Path, ...]) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[agent]\nplaybooks = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script(flags)))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[tuple[RunManager, str]]:
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


_AT_THE_FOOT: str = """() => {
  const thread = document.querySelector('.run-thread');
  const box = thread !== null && thread.scrollHeight > thread.clientHeight + 4 ? thread : document.scrollingElement;
  return { top: Math.round(box.scrollTop), max: Math.round(box.scrollHeight - box.clientHeight) };
}"""


async def _open_from_the_side(page: Page, title: str, goal: str, phone: bool) -> None:
    """Open the chat named ``title`` from the side menu, the drawer on a phone, and wait for its run ``goal``."""
    if phone:
        await page.get_by_role("button", name="Show sidebar").click()
    await page.locator("a.side-run", has_text=title).first.click()
    await page.wait_for_function(
        "goal => document.querySelector('.run-main h1')?.textContent === goal", arg=goal, timeout=15000
    )


async def _at_the_foot(page: Page, what: str) -> None:
    """The conversation stands at its foot once it has settled, and stays there past the moment it is held there."""
    samples: list[dict[str, int]] = []
    for pause_ms in (500, 1000, 1500):
        await page.wait_for_timeout(pause_ms)
        samples.append(await page.evaluate(_AT_THE_FOOT))
    assert all(sample["max"] > 0 for sample in samples), (what, samples)
    assert all(sample["max"] - sample["top"] <= _FOOT_SLACK_PX for sample in samples), (what, samples)


async def _start(manager: RunManager, goal: str, thread_id: str | None) -> RunRecord:
    return await manager.start(
        goal, "task", SCRIPTED_MODEL, thread_id, "test", None, None, "standard", None, "yolo", False, ()
    )


async def _walk(
    browser: Browser, address: str, first: RunRecord, width: int, manager: RunManager, flag: Path
) -> list[str]:
    """Start a chat at work, open the first chat by its address, then each chat from the side menu; the run at work
    ends, once ``flag`` is there, while another chat is open. Return the page's errors."""
    at_work: RunRecord = await _start(manager, f"Chat at work at {width} px: keep working on the harbour.", None)
    phone: bool = width < 500
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 800}, is_mobile=phone, has_touch=phone
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/runs/{first.id}")
    await page.wait_for_selector(".run-main", timeout=15000)
    # The chat with earlier turns, the one at work, the first again and the one at work again, both drawn from what the
    # page kept of them, and the second again.
    stops: tuple[tuple[str, str], ...] = (
        (_GOALS[1], _GOALS[2]),
        (at_work.goal, at_work.goal),
        (_GOALS[0], _GOALS[0]),
        (at_work.goal, at_work.goal),
        (_GOALS[1], _GOALS[2]),
    )
    for title, goal in stops:
        await _open_from_the_side(page, title, goal, phone)
        await _at_the_foot(page, f"{width} {goal}")
        if goal == at_work.goal:
            # The dock grows with a draft of several lines, and shrinks back: the reader at the end stays there.
            box: Locator = page.locator(".dock textarea")
            await box.fill("\n".join(f"Line {number} of a long draft" for number in range(1, 7)))
            await _at_the_foot(page, f"{width} {goal}, a draft of six lines")
            await box.fill("")
            await _at_the_foot(page, f"{width} {goal}, the draft gone")
    # The run at work ends while another chat is open: coming back, its chat opens at its foot, not at the top of the
    # answer it ended with.
    await asyncio.to_thread(flag.touch)
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(at_work.id)).status == "done"
    await _open_from_the_side(page, at_work.goal, at_work.goal, phone)
    await _at_the_foot(page, f"{width} {at_work.goal}, ended while away")
    await context.close()
    return errors


async def test_a_chat_opened_from_the_side_menu_stands_at_its_foot(
    served: tuple[RunManager, str], flags: tuple[Path, ...]
) -> None:
    manager, address = served

    async def finished(goal: str, thread_id: str | None) -> RunRecord:
        run: RunRecord = await _start(manager, goal, thread_id)
        async with asyncio.timeout(_RUN_WAIT_S):
            assert (await manager.wait(run.id)).status == "done"
        return run

    first: RunRecord = await finished(_GOALS[0], None)
    second: RunRecord = await finished(_GOALS[1], None)
    await finished(_GOALS[2], second.thread_id)
    async with async_playwright() as playwright:
        for (name, width), flag in zip(_LOOKS, flags, strict=True):
            browser: Browser = await (playwright.chromium if name == "chromium" else playwright.webkit).launch()
            try:
                assert await _walk(browser, address, first, width, manager, flag) == []
            finally:
                await browser.close()
