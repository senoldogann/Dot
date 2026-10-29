"""A run's rows stand close on a phone (the owner's word of 2026-10-20): a call is one line with its icon, a group of
calls a short list, and the breath between two turns does not stand between the rows of one. Before, a row kept the
height of a button and the gaps between them a paragraph's, so a run of ten calls ran off the screen.

The rows are measured where the owner sees them: a real server on a scripted model, a phone's window (390, a touch
screen). Every rule of the change sits under the phone's media query, so a wide window keeps the rows it had.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

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

TOKEN: str = "phone-rows-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_READ: ScriptedCall = ScriptedCall(tool="read_file", arguments={"path": "notes.txt"})
_RUN_WAIT_S: float = 60.0
# What a row may be on a phone: a line's height and a thumb's, and never a button's 40 with a paragraph's gaps.
_ROW_MIN_PX: int = 28
_ROW_MAX_PX: int = 36
_PITCH_MAX_PX: int = 34


def _script() -> list[ScriptedTurn]:
    return [
        ScriptedTurn(text="", calls=(_READ, _READ, _READ)),
        ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
    ]


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
            stopping.set()
            server.should_exit = True
            await serving
    finally:
        await manager.shutdown()


_ROWS: str = """() => {
  // The calls of the run's flow as the owner sees them: their own heights, and the space between one and the next.
  const rows = [...document.querySelectorAll('.run-main .tool-head')].map((row) => row.getBoundingClientRect());
  return {
    heights: rows.map((box) => Math.round(box.height)),
    pitches: rows.slice(1).map((box, index) => Math.round(box.top - rows[index].top)),
  };
}"""


async def _measure(served: tuple[RunManager, str], run_id: str) -> dict[str, object]:
    _manager, address = served
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            context: BrowserContext = await browser.new_context(
                viewport={"width": 390, "height": 844},
                color_scheme="dark",
                reduced_motion="reduce",
                has_touch=True,
                is_mobile=True,
                device_scale_factor=3,
            )
            await context.add_init_script(
                "localStorage.setItem('dot.work', 'steps')"
            )  # the steps this test reads (item 830)
            page: Page = await context.new_page()
            await page.add_init_script(
                "localStorage.setItem('dot.work', 'steps')"
            )  # the steps this test reads (item 830)
            await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
            await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
            await page.goto(f"{address}/#/runs/{run_id}")
            await page.wait_for_selector(".run-main", timeout=20000)
            # A run's steps stand folded: their rows are in the page but not painted until the group is open, which the
            # owner's own page does with a tap.
            await page.wait_for_selector(".run-main .tool-head", state="attached", timeout=20000)
            await page.evaluate(
                "() => document.querySelectorAll('.run-main details.steps').forEach((node) => { node.open = true; })"
            )
            await page.wait_for_selector(".run-main .steps-body .tool-head", state="visible", timeout=20000)
            found: dict[str, object] = await page.evaluate(_ROWS)
            await context.close()
            return found
        finally:
            await browser.close()


async def test_a_runs_rows_stand_close_on_a_phone(served: tuple[RunManager, str]) -> None:
    manager, _address = served
    run: RunRecord = await manager.start(
        "Read the notes.", "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done"

    phone: dict[str, object] = await _measure(served, run.id)
    heights: list[int] = phone["heights"]  # type: ignore[assignment]
    pitches: list[int] = phone["pitches"]  # type: ignore[assignment]
    assert len(heights) >= 3, phone
    assert all(_ROW_MIN_PX <= height <= _ROW_MAX_PX for height in heights), heights
    assert all(pitch <= _PITCH_MAX_PX for pitch in pitches), pitches

    # The rows are a phone's: run.css puts every rule of this under `html.chat-shell` and the phone's media query,
    # so a wide window keeps what it had (its rows were 29 px in a pitch of 39 before this change, and are still).
