"""The work told in one line (item 663). With Settings › Interface › Work in a chat on "What it is doing", a run's steps
and thoughts are out of the conversation, and one line under it says what the run does now, in the words of its
latest event ("Running a command…" while a command runs); pressed, it shows the steps again, and once the run is over it
offers them. With the default, every step shows and the line does not. Measured in Chrome on a real run of a scripted
model whose command takes a few seconds."""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

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

TOKEN: str = "work-status-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="Listing it now.", calls=(ScriptedCall(tool="shell", arguments={"command": "sleep 6"}),)),
    ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)),
)
# The words the line has for the command, with the command it runs (item 1091).
# What the step said it does next, which the line says while its command runs (item 1131).
_TOLD_WORDS: str = r"^Listing it now…$"


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[RunManager, str, int]]:
    """A daemon whose scripted model runs a command of a few seconds and answers: its run manager, address and port."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
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


async def _start(manager: RunManager) -> RunRecord:
    return await manager.start(
        "List the folder slowly",
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


async def _open(browser: Browser, port: int, address: str, run_id: str, work: str | None, width: int) -> Page:
    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    if work is not None:
        await page.add_init_script(f"localStorage.setItem('dot.work', '{work}')")
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page


async def test_the_line_says_what_the_run_does_and_hides_the_steps_until_asked(own_home: Path, tmp_path: Path) -> None:
    async with _served(own_home) as (manager, address, port):
        run: RunRecord = await _start(manager)
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page: Page = await _open(browser, port, address, run.id, "status", 1280)
                line = page.locator(".work-status")
                words = line.locator(".work-status-words")
                await expect(line).to_have_attribute("data-state", "working")
                await expect(words).to_have_text(re.compile(_TOLD_WORDS))
                # The message it said on the way folds with the steps (item 1131).
                await expect(page.locator(".bubble.say.say-progress")).to_be_hidden()
                # Not the model typing while the command runs: the face, not the three dots (item 1091).
                await expect(line.locator(".typing-dots")).to_be_hidden()
                # The agent typing (item 843): its face first, in the answer's face's place, the words where the words
                # of the messages start, 28 px of face and a gap of 8 on.
                await expect(line.locator(".turn-face .avatar")).to_have_count(1)
                face_x, words_x = await line.evaluate(
                    "(line) => { const box = (part) => line.querySelector(part).getBoundingClientRect();"
                    " return [box('.turn-face').x, box('.work-status-words').x]; }"
                )
                assert words_x - face_x == 36, (face_x, words_x)
                # Its words alone while the run works (item 971, the owner's ask): no dots, and no "Show the steps"
                # beside them; the line itself is the way to the steps, as its tooltip says, and its name to a screen
                # reader too (item 977).
                await expect(line.locator(".work-status-dots")).to_have_count(0)
                await expect(line).to_have_attribute("title", "Show the steps")
                await expect(line).to_have_accessible_name(re.compile(r"Show the steps$"))
                more_box = await line.locator(".work-status-more").bounding_box()
                assert more_box is not None and more_box["width"] <= 1 and more_box["height"] <= 1, more_box
                face = await line.locator(".turn-face").bounding_box()
                assert face is not None
                await page.mouse.click(face["x"] + face["width"] / 2, face["y"] + face["height"] / 2)
                await expect(line).to_have_attribute("aria-pressed", "true")
                await line.click()
                await expect(line).to_have_attribute("aria-pressed", "false")
                await expect(page.locator("details.steps")).to_be_hidden()
                await page.screenshot(path=str(tmp_path / "working.png"))
                # Pressed while the run works, the line shows the steps; pressed again, it folds them back; pressed a
                # third time, the steps stay shown when the answer comes.
                await line.click()
                await expect(page.locator("details.steps").first).to_be_visible()
                await expect(line).to_have_attribute("aria-pressed", "true")
                await line.click()
                await expect(page.locator("details.steps")).to_be_hidden()
                await line.click()
                async with asyncio.timeout(30):
                    assert (await manager.wait(run.id)).status == "done"
                await expect(line).to_have_attribute("data-state", "over")
                await expect(page.locator(".answer .prose")).to_have_text("Done.")
                # The foot's button starts from that: the steps are shown, and it folds them (the review of item 842).
                await expect(page.locator(".answer-steps")).to_have_text("Hide the steps")
                await expect(page.locator(".answer-steps")).to_have_attribute("aria-pressed", "true")
                await page.locator(".answer").hover()
                await page.locator(".answer-steps").click()
                await expect(page.locator("details.steps")).to_be_hidden()
                # Over, with an answer, the way to the steps is in the answer's foot and the line goes (item 842).
                await expect(line).to_be_hidden()
                steps: Locator = page.locator(".answer .answer-foot").get_by_role("button", name="Show the steps")
                await page.locator(".answer").hover()
                await steps.click()
                await expect(page.locator("details.steps").first).to_be_visible()
                await expect(page.locator(".answer-steps")).to_have_text("Hide the steps")
                await expect(page.locator(".answer-steps")).to_have_attribute("aria-pressed", "true")
                await page.locator(".answer-steps").click()
                await expect(page.locator("details.steps")).to_be_hidden()
                await page.close()
                # On a phone's width the button is in the foot, which a touch screen always shows, and the steps fold
                # out of the conversation as wide.
                phone: Page = await _open(browser, port, address, run.id, "status", 390)
                await expect(phone.locator(".answer-steps")).to_have_text("Show the steps")
                # On a phone the answer sits in a bubble; on a Mac's width it is plain text (item 1091; item 854).
                # What the run said on the way is no bubble: it folds with the steps, a note among them (item 1131).
                fills: list[str] = await phone.locator(".run-thread :is(.answer, .bubble.say) > .prose").evaluate_all(
                    "nodes => nodes.map((node) => getComputedStyle(node).backgroundColor)"
                )
                assert len(fills) == 2 and fills[0] == "rgba(0, 0, 0, 0)" and fills[1] != "rgba(0, 0, 0, 0)", fills
                await expect(phone.locator(".bubble.say.say-progress")).to_be_hidden()
                # A step whose model has sent only spaces holds no empty bubble: timeline.js marks it blank, and the
                # rule that hides it is one the browser keeps (a `:has()` inside a `:has()` was dropped whole).
                blank: str = await phone.locator(".run-thread").first.evaluate(
                    "thread => { const node = document.createElement('div');"
                    " node.className = 'bubble say live'; node.toggleAttribute('data-blank', true);"
                    " node.append(Object.assign(document.createElement('div'), { className: 'prose stream' }));"
                    " thread.append(node); const shown = getComputedStyle(node).display; node.remove(); return shown; }"
                )
                assert blank == "none", blank
                await expect(phone.locator(".work-status")).to_be_hidden()
                await expect(phone.locator("details.steps")).to_be_hidden()
                await phone.screenshot(path=str(tmp_path / "phone.png"))
                await phone.close()
            finally:
                await browser.close()


async def test_by_default_the_steps_fold_away_and_the_line_offers_them(own_home: Path) -> None:
    """Item 830: until the owner chooses, a chat shows the conversation as people chat, the steps a press away."""
    async with _served(own_home) as (manager, address, port):
        run: RunRecord = await _start(manager)
        async with asyncio.timeout(30):
            assert (await manager.wait(run.id)).status == "done"
        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page: Page = await _open(browser, port, address, run.id, None, 1280)
                await expect(page.locator(".answer .prose")).to_have_text("Done.")
                await expect(page.locator("details.steps").first).to_be_hidden()
                await expect(page.locator(".answer-steps")).to_have_count(1)
                await expect(page.locator(".work-status")).to_be_hidden()
                await page.close()
            finally:
                await browser.close()
