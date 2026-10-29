"""Dot's reaction on the owner's message in the conversation (item 664): a run whose model reacts 👍 and answers "Done."
shows the 👍 as a round badge on the corner of the message's bubble, on the run's own page and, once the chat went on,
on the earlier turn; a run that reacts with nothing shows no badge. Measured in Chrome on real runs of a scripted
model, wide and on a phone's width."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, Page, async_playwright, expect

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
from dotagent.tools.meta import FINISH_TOOL, REACT_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "dot-reaction-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TURNS: tuple[ScriptedTurn, ...] = (
    # The first run reacts and answers in a word.
    ScriptedTurn(text="", calls=(ScriptedCall(tool=REACT_TOOL, arguments={"reaction": "👍"}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
    # The follow-up only answers.
    ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Here it is.", "success": True}),)
    ),
)
# Where the badge stands against its bubble: its centre on the bubble's lower left corner, give or take its radius.
_PLACE: str = """(badge) => {
  const own = badge.getBoundingClientRect();
  const bubble = badge.closest('.bubble.user').getBoundingClientRect();
  return { dx: own.left + own.width / 2 - bubble.left, dy: own.top + own.height / 2 - bubble.bottom, w: own.width };
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
    """A daemon whose scripted model reacts and answers, then answers a follow-up: its run manager, address and port."""
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


async def _run(manager: RunManager, goal: str, thread_id: str | None) -> RunRecord:
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, thread_id, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(30):
        assert (await manager.wait(run.id)).status == "done"
    return run


async def test_dots_reaction_stands_on_the_corner_of_the_message(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as (manager, address, port):
        first: RunRecord = await _run(manager, "Send the weekly report", None)
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                for width in (1280, 390):
                    page: Page = await browser.new_page(
                        viewport={"width": width, "height": 900}, reduced_motion="reduce"
                    )
                    await page.add_init_script(
                        "localStorage.setItem('dot.work', 'steps')"
                    )  # the steps this test reads (item 830)
                    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    await page.goto(f"{address}/#/runs/{first.id}")
                    await expect(page.locator(".answer .prose")).to_have_text("Done.")
                    badge = page.locator(".user-message .bubble.user .dot-reaction")
                    await expect(badge).to_have_text("👍")
                    await expect(badge).to_have_attribute("aria-label", "Reacted 👍: understood")
                    # The reaction is said by its badge: its call is no step of the conversation.
                    await expect(page.locator("details.steps")).to_have_count(0)
                    place: dict[str, float] = await badge.evaluate(_PLACE)
                    assert abs(place["dx"]) <= place["w"] / 2 and abs(place["dy"]) <= place["w"] / 2, place
                    await page.screenshot(path=str(tmp_path / f"reaction-{width}.png"))
                    await page.close()
                # Once the chat goes on, the earlier turn keeps its badge, and the new turn, which reacted with
                # nothing, shows none.
                second: RunRecord = await _run(manager, "And the monthly one", first.thread_id)
                page = await browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                await page.add_init_script(
                    "localStorage.setItem('dot.work', 'steps')"
                )  # the steps this test reads (item 830)
                await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                await page.goto(ui_url("127.0.0.1", port, TOKEN))
                await page.goto(f"{address}/#/runs/{second.id}")
                await expect(page.locator(".answer .prose").last).to_have_text("Here it is.")
                await expect(page.locator(".dot-reaction")).to_have_count(1)
                earlier = page.locator(".user-message", has_text="Send the weekly report")
                await expect(earlier.locator(".dot-reaction")).to_have_text("👍")
                await page.close()
            finally:
                await browser.close()
