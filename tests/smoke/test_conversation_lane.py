"""One lane for the conversation (item 656). Every row of a run's conversation starts its words where a message's do and
centres its mark on the column of a face: what the run said, a group of steps and a step in it, a change of the
memory, the answer, and a quiet line wherever the conversation holds one (the timeline, an earlier turn and its
steps, the head of the history), each measured with a probe line where the page has none. Before, a step's words
started 2 px to the left of a message's, a change of the memory's 9 px, and their marks stood 1 to 3 px off the
face's centre: the conversation read down three edges. Measured in Chrome on a real run of a scripted model, wide and
on a phone's width."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, Page, async_playwright

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
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "conversation-lane-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_ANSWER: str = "**Found:** the notes were old.\n\n- one item in a list\n- `code` in a second item\n\nThe last line."
_TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(
        text="I will look at the folder first.", calls=(ScriptedCall(tool="shell", arguments={"command": "ls"}),)
    ),
    ScriptedTurn(
        text="The note was wrong, so I fix it.",
        calls=(
            ScriptedCall(tool="memory_save", arguments={"content": "The skills live in Dot's home.", "tags": ["fact"]}),
        ),
    ),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": _ANSWER, "success": True}),)),
)
# Where each row starts its words and centres its mark, in pixels from the left of the window.
_LANE: str = """() => {
  const box = (node) => node.getBoundingClientRect();
  const face = box(document.querySelector('.answer .turn-face'));
  const words = [
    ...[...document.querySelectorAll('.bubble.say')].map((n) => ['said', box(n.querySelector('.prose') ?? n).left]),
    // The first words of each head of a group or a step on the screen, after its mark (a step in a folded group shows
    // nothing).
    ...[...document.querySelectorAll('.steps-head, .tool-head')]
      .filter((head) => head.getBoundingClientRect().width > 1)
      .map((head) => {
      const first = [...head.children].slice(1).find((n) => n.getBoundingClientRect().width > 1);
      return [head.className, box(first).left];
    }),
    ...[...document.querySelectorAll('.memory-line .memory-change')].map((n) => ['memory', box(n).left]),
    ['answer', box(document.querySelector('.answer .prose')).left],
  ];
  // Every quiet line of the conversation's column outside a card, and a probe line put straight into the column and
  // into a list of its own there, as the messages waiting to be read stand.
  const thread = document.querySelector('.run-thread');
  const list = thread.appendChild(Object.assign(document.createElement('div'), { className: 'stack tight' }));
  for (const home of [thread, list]) {
    const probe = document.createElement('div');
    probe.className = 'note';
    probe.innerHTML = '<svg class="icon" viewBox="0 0 24 24"></svg><span>probe</span>';
    home.appendChild(probe);
  }
  const seen = (n) => n.getBoundingClientRect().width > 0;
  const lines = [...thread.querySelectorAll('.note:not(.card .note, .connection-slot .note)')].filter(seen);
  words.push(...lines.map((n) => ['quiet ' + n.className, box([...n.children].find((c) => !c.matches('.icon'))).left]));
  const quiet = '.run-thread .note:not(.card .note, .connection-slot .note) > .icon:first-child';
  const marks = [...document.querySelectorAll(`.steps-state, .tool-state, ${quiet}`)]
    .filter((n) => n.getBoundingClientRect().width > 0)
    .map((n) => [n.className.baseVal ?? n.className, box(n).left + box(n).width / 2]);
  return { face: face.left + face.width / 2, words, marks };
}"""


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str, int]]:
    """A daemon whose scripted model says a message, runs a command, keeps a note and answers: its run manager, its
    address and its port."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, list(_TURNS)))
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
            yield manager, f"http://127.0.0.1:{port}", port
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def test_every_row_of_a_run_starts_its_words_and_centres_its_mark_on_one_lane(
    own_home: Path, tmp_path: Path
) -> None:
    async with _served(own_home) as (manager, address, port):
        run: RunRecord = await manager.start(
            "Check the skills folder",
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for width in (1440, 390):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 1000}, reduced_motion="reduce"
                    )
                    await page.add_init_script(
                        "localStorage.setItem('dot.work', 'steps')"
                    )  # the steps this test reads (item 830)
                    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    await page.goto(f"{address}/#/runs/{run.id}")
                    await page.wait_for_selector(".answer .prose")
                    lane: dict[str, Any] = await page.evaluate(_LANE)
                    await page.screenshot(path=str(tmp_path / f"lane-{width}.png"))
                    answer: float = dict(lane["words"])["answer"]
                    kinds: set[str] = {kind for kind, _ in lane["words"]}
                    assert {"said", "memory", "answer"} <= kinds and len(lane["marks"]) >= 3, lane
                    for kind, left in lane["words"]:
                        assert abs(left - answer) <= 1, (width, kind, left, answer)
                    for kind, centre in lane["marks"]:
                        assert abs(centre - lane["face"]) <= 1, (width, kind, centre, lane["face"])
                    await page.close()
            finally:
                await browser.close()
