"""Under a message, its foot follows it closely (item 336): the time, the copy button and how long the run took stand
at most a few pixels under your bubble and under the answer's last line, wide and on a phone, whose controls are a
finger's size. A chat of the scripted model, served by a real server, in a real headless Chrome as a Mac's window and
in WebKit as an iPhone.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, BrowserContext, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "message-foot-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_ANSWER: str = "The weekly report is in the reports folder, with the late projects first."
_RUN_WAIT_S: float = 60.0
# How far the foot's first visible line may stand under the bubble and under the answer's last line, in pixels, as a
# Mac's window and as a phone. Before item 336 they stood 8 and 26 px under them in the window, 13 and 23 on the phone.
_MOST_GAP_PX: dict[str, dict[str, float]] = {
    "wide": {"user": 8.0, "answer": 12.0},
    "phone": {"user": 8.0, "answer": 8.0},
}
# What the page measures: from the bubble's foot to the top of the time under it, and from the answer's last line to
# the top of the first thing of its foot that shows (the copy button's mark, the time).
_MEASURE: str = """() => {
  const user = document.querySelector(".user-message");
  const bubble = user.querySelector(".bubble").getBoundingClientRect();
  const sent = user.querySelector(".message-foot .message-time").getBoundingClientRect();
  const answer = document.querySelector(".answer");
  const prose = answer.querySelector(".prose").getBoundingClientRect();
  const mark = answer.querySelector(".answer-foot .btn svg").getBoundingClientRect();
  const facts = answer.querySelector(".answer-foot .answer-facts").getBoundingClientRect();
  return { user: sent.top - bubble.bottom, answer: Math.min(mark.top, facts.top) - prose.bottom };
}"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A throw-away home where every model role is the scripted one."""
    home: Path = tmp_path_factory.mktemp("home")
    roles: str = "".join(f'{role} = "{SCRIPTED_MODEL}"\n' for role in ("main", "fast", "review", "vision"))
    (home / "config.toml").write_text(
        f"[browser]\nheadless = true\n\n[models]\n{roles}\n[memory]\nembed_enabled = false\n\n"
        '[skills]\nelsewhere = false\n\n[providers.ollama]\nbase_url = "http://127.0.0.1:9"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _gaps(page: Page, address: str, run: RunRecord) -> dict[str, float]:
    await page.goto(f"{address}/#/runs/{run.id}")
    await expect(page.locator(".answer .prose")).to_contain_text(_ANSWER)
    found: object = await page.evaluate(_MEASURE)
    assert isinstance(found, dict)
    return {key: float(value) for key, value in found.items()}


async def test_the_foot_of_a_message_follows_it_closely(runtime: Runtime, tmp_path: Path) -> None:
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [ScriptedTurn(text=_ANSWER, calls=())] * 4))
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "Where is the weekly report?",
            "chat",
            SCRIPTED_MODEL,
            None,
            "ui",
            None,
            None,
            "standard",
            None,
            "yolo",
            None,
            (),
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
        assert ended.status == "done", ended.result
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
        address: str = f"http://127.0.0.1:{port}"
        try:
            async with async_playwright() as playwright:
                chrome: Browser = await playwright.chromium.launch(
                    channel=runtime.config.browser.channel, headless=True
                )
                webkit: Browser = await playwright.webkit.launch(headless=True)
                try:
                    wide: Page = await chrome.new_page(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
                    await wide.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await wide.goto(ui_url("127.0.0.1", port, TOKEN))
                    phone_context: BrowserContext = await webkit.new_context(
                        **playwright.devices["iPhone 17 Pro Max"], reduced_motion="reduce"
                    )
                    await phone_context.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    phone: Page = await phone_context.new_page()
                    await phone.goto(ui_url("127.0.0.1", port, TOKEN))
                    measured: dict[str, dict[str, float]] = {
                        "wide": await _gaps(wide, address, ended),
                        "phone": await _gaps(phone, address, ended),
                    }
                    for look, gaps in measured.items():
                        for where, gap in gaps.items():
                            assert 0.0 <= gap <= _MOST_GAP_PX[look][where], measured
                    await wide.screenshot(path=str(tmp_path / "foot-wide.png"))
                    await phone.screenshot(path=str(tmp_path / "foot-phone.png"))
                finally:
                    await chrome.close()
                    await webkit.close()
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
