"""An answer that ends a run under the reader's eyes is written in (item 366): its words come a few at a time, formatted
as they stay, and its foot shows once they are all in; what the run changed is no line under it (item 841). Under
reduced motion, and on a run opened after it ended, the answer shows at once. A real server on a scripted model, in
Chrome."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

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

TOKEN: str = "answer-streams-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Look at the shelves, then say what is on them."
_RUNS: int = 3
# Long enough to take many frames: paragraphs, a list and a closing line.
_ANSWER: str = "\n\n".join(
    [
        "The shelves hold the winter things, sorted by the room they belong to.",
        *[
            f"- Shelf {number}: the blankets, the boots and the lamps of room {number}, labelled."
            for number in range(1, 13)
        ],
        "Everything is dry, and nothing is missing from the list you gave me.",
    ]
)
_RUN_WAIT_S: float = 60.0
# A window shorter than the run's page, so that landing on its answer is a scroll.
_SHORT_WINDOW: int = 420
# Every frame's length of the answer's words, whether its foot shows, where the answer's top stands in the window and
# whether the line of what the run changed shows once it is drawn, from before the answer comes.
_SAMPLER: str = """() => {
  window.__seen = [];
  const sample = () => {
    const words = document.querySelector('.answer > .prose');
    const foot = document.querySelector('.answer > .answer-foot');
    const answer = document.querySelector('.answer');
    const changed = document.querySelector('.answer > .run-changes:not([hidden])');
    window.__seen.push([
      words === null ? -1 : words.textContent.length,
      foot === null ? null : getComputedStyle(foot).visibility,
      answer === null ? null : Math.round(answer.getBoundingClientRect().top),
      changed === null ? null : getComputedStyle(changed).visibility,
    ]);
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
}"""


def _script() -> list[ScriptedTurn]:
    # Each run writes a file, so that a line of what it changed would come, and it is not under the answer (item 841).
    write = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="write_file", arguments={"path": "shelves.md", "content": "Dry.\n"}),)
    )
    look = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 2"}),))
    finish = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": _ANSWER, "success": True}),)
    )
    return [turn for _ in range(_RUNS) for turn in (write, look, finish)]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
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
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script()))
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


async def _start(manager: RunManager) -> RunRecord:
    return await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )


async def _watch(
    browser: Browser, address: str, run: RunRecord, reduced: bool, height: int
) -> tuple[list[list[int | str | None]], int]:
    """What the page showed of the answer of `run` frame by frame, from its opening, in a window `height` tall, and the
    length of its words."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context = await browser.new_context(
        viewport={"width": 1280, "height": height},
        color_scheme="light",
        reduced_motion="reduce" if reduced else "no-preference",
    )
    errors: list[str] = []
    try:
        page: Page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
        await page.goto(ui_url(host, port, TOKEN))
        await page.goto(f"{address}/#/runs/{run.id}")
        await page.evaluate(_SAMPLER)
        foot = page.locator(".answer > .answer-foot")
        await expect(foot).to_be_visible(timeout=_RUN_WAIT_S * 1000)
        await expect(foot).to_have_css("visibility", "visible")
        await page.wait_for_timeout(200)
        seen: list[list[int | str | None]] = await page.evaluate("window.__seen")
        whole: int = await page.locator(".answer > .prose").evaluate("(words) => words.textContent.length")
    finally:
        await context.close()
    assert errors == []
    return seen, whole


async def test_an_answer_that_ends_a_run_under_the_readers_eyes_is_written_in(served: tuple[RunManager, str]) -> None:
    manager, address = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            run: RunRecord = await _start(manager)
            # A window shorter than the conversation: the page must scroll to land on the answer.
            seen, whole = await _watch(browser, address, run, reduced=False, height=_SHORT_WINDOW)
            lengths: list[int] = [int(length) for length, _, _, _ in seen if isinstance(length, int) and length >= 0]
            partial = [frame for frame in seen if isinstance(frame[0], int) and 0 < frame[0] < whole]
            assert len(partial) >= 5, f"the words came at once: {sorted(set(lengths))[:12]} of {whole}"
            assert lengths == sorted(lengths), "the words only ever grew"
            assert lengths[-1] == whole
            # The foot waits until the words are all in.
            assert all(shown == "hidden" for _, shown, _, _ in partial)
            # The answer holds no line of what the run changed, which heads the side panel's Changes tab (item 841).
            assert all(changed is None for _, _, _, changed in seen)
            # The words are written in sight, where the answer stands: its top in the upper half of the window, still
            # once the page has landed on it.
            tops = [int(top) for _, _, top, _ in partial[2:] if isinstance(top, int)]
            assert tops and max(tops) < _SHORT_WINDOW / 2, tops
            assert max(tops) - min(tops) <= 2, tops
        finally:
            await browser.close()


async def test_under_reduced_motion_and_on_a_run_read_again_the_answer_shows_at_once(
    served: tuple[RunManager, str],
) -> None:
    manager, address = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            live: RunRecord = await _start(manager)
            seen, whole = await _watch(browser, address, live, reduced=True, height=900)
            assert next(length for length, _, _, _ in seen if isinstance(length, int) and length >= 0) == whole
            ended: RunRecord = await _start(manager)
            async with asyncio.timeout(_RUN_WAIT_S):
                assert (await manager.wait(ended.id)).status == "done"
            seen, whole = await _watch(browser, address, ended, reduced=False, height=900)
            assert next(length for length, _, _, _ in seen if isinstance(length, int) and length >= 0) == whole
        finally:
            await browser.close()
