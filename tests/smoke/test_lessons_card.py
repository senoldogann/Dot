"""The lessons of a run on its page (item 302): with ``auto``, the lesson the second look kept says it was saved on its
own and has Undo, the one it left out says why and has Save anyway; Undo forgets the note and Save anyway keeps the
lesson. A real server on a scripted model, wide and dark, and on a phone, light."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import APIResponse, Browser, BrowserContext, Locator, Page, Route, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.agent.review import LESSONS_TOOL, VET_TOOL
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

TOKEN: str = "lessons-card-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Keep the line hello in notes.txt and read it back."
_KEPT: str = "read_file fails on a file that does not exist yet; create notes.txt with write_file first."
_LEFT: str = "Reading a file back right after writing it confirms that the write worked."
_WHY: str = "says nothing a later run needs"
# A lesson of almost the most a lesson may hold: more than four lines in a phone's column, fewer in a wide one.
_LONG: str = (
    "A file a run writes and reads back in the same step should be written with write_file and read with read_file, "
    "never echoed through the shell, because the shell guard asks about a redirect and the run then waits on the "
    "owner for a write it could have made without a question."
)
_RUN_WAIT_S: float = 30.0
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "dark"), (390, "light"))


def _turn(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _script() -> list[ScriptedTurn]:
    """A run that reads notes.txt before it exists, writes and reads it, and finishes; then the lesson writer's three
    lessons and the second look, which keeps the first and leaves the other two out."""
    return [
        _turn("read_file", {"path": "notes.txt"}),
        _turn("write_file", {"path": "notes.txt", "content": "hello\n"}),
        _turn("read_file", {"path": "notes.txt"}),
        _turn(FINISH_TOOL, {"answer": "The file holds hello.", "success": True}),
        _turn(LESSONS_TOOL, {"lessons": [_KEPT, _LEFT, _LONG]}),
        _turn(
            VET_TOOL,
            {
                "verdicts": [
                    {"lesson": 1, "keep": True, "reason": "know-how about read_file"},
                    {"lesson": 2, "keep": False, "reason": _WHY},
                    {"lesson": 3, "keep": False, "reason": _WHY},
                ]
            },
        ),
    ]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose main and review models are the scripted one, lessons at their default."""
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


async def _page(
    browser: Browser, address: str, run_id: str, width: int, scheme: Literal["light", "dark"]
) -> tuple[Page, list[str]]:
    """The page of the run ``run_id``, signed in, in a browser of its own, with the errors it throws."""
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page, errors


async def test_a_lesson_saved_on_its_own_has_undo_and_one_left_out_says_why_with_save_anyway(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    manager, address = served
    run: RunRecord = await manager.start(
        _GOAL, "task", SCRIPTED_MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        assert (await manager.wait(run.id)).status == "done"
        await manager.learned(run.id)
    assert await runtime.store.count_memories() == 1
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for width, scheme in _SHOTS:
                page, errors = await _page(browser, address, run.id, width, scheme)
                card: Locator = page.get_by_role("region", name="Lessons from this task")
                await expect(card).to_be_visible()
                assert not await card.locator(".lesson").first.is_visible(), "the lessons arrive folded"
                assert await card.get_attribute("open") is None
                await card.scroll_into_view_if_needed()
                await card.screenshot(path=str(tmp_path / f"card-folded-{width}-{scheme}.png"))
                await card.locator(".lessons-head").click()
                await expect(card.locator(".lesson").first).to_be_visible()
                assert await card.get_attribute("open") is not None
                kept: Locator = card.locator(".lesson").nth(0)
                left: Locator = card.locator(".lesson").nth(1)
                await expect(kept.locator(".lesson-text")).to_have_text(_KEPT)
                await expect(kept.locator(".lesson-state")).to_have_text("Saved on its own")
                await expect(kept.get_by_role("button", name="Undo")).to_be_visible()
                await expect(left.locator(".lesson-why")).to_have_text(f"Left out: {_WHY}")
                await expect(left.get_by_role("button", name="Save anyway")).to_be_visible()
                # More shows where the four lines cut a lesson: the long one on a phone, none on a wide window.
                await expect(kept.locator(".lesson-more")).to_be_hidden()
                long: Locator = card.locator(".lesson").nth(2)
                more: Locator = long.get_by_role("button", name="More")
                if width < 600:
                    await expect(more).to_be_visible()
                    await more.click()
                    await expect(long.get_by_role("button", name="Less")).to_be_visible()
                    await expect(long.locator(".lesson-text")).to_have_class("lesson-text open")
                else:
                    await expect(more).to_be_hidden()
                await card.scroll_into_view_if_needed()
                await card.screenshot(path=str(tmp_path / f"card-{width}-{scheme}.png"))
                assert errors == []
                if width == _SHOTS[-1][0]:
                    # On the phone: Undo forgets the note, and Save anyway keeps the lesson that was left out.
                    await kept.get_by_role("button", name="Undo").click()
                    await expect(kept.locator(".lesson-state")).to_have_text("Dismissed")
                    await expect(page.get_by_text("Undone: the lesson is no longer in memory.")).to_be_visible()
                    await left.get_by_role("button", name="Save anyway").click()
                    await expect(left.locator(".lesson-state")).to_have_text("Saved to memory")
                    await expect(left.get_by_role("button", name="Undo")).to_be_visible()
                    await card.screenshot(path=str(tmp_path / f"card-{width}-{scheme}-decided.png"))
                    assert errors == []
                await page.context.close()
            # Folded, the head says what waits inside: lessons that wait for a decision, or why none could be drawn.
            for lessons, said in (
                ({"state": "proposed", "by": None, "reason": None}, "2 to decide"),
                (None, "could not be drawn"),
            ):
                page, errors = await _page_with_lessons(browser, address, run.id, lessons)
                count: Locator = page.get_by_role("region", name="Lessons from this task").locator(".lessons-count")
                await expect(count).to_have_text(said)
                await expect(count).to_have_attribute("data-tone", "warn" if lessons is not None else "bad")
                assert errors == []
                # A read of the run still under way when the page goes is let go, not rewritten.
                await page.unroute_all(behavior="ignoreErrors")
                await page.context.close()
        finally:
            await browser.close()
    notes = await runtime.store.list_memories(10, 0)
    assert [note.content for note in notes] == [_LEFT]


async def _page_with_lessons(
    browser: Browser, address: str, run_id: str, lesson: dict[str, str | None] | None
) -> tuple[Page, list[str]]:
    """The page of the run with what the daemon says of its lessons rewritten: two lessons whose fields are ``lesson``,
    or, for ``None``, none and the error of lessons that could not be drawn."""

    async def rewrite(route: Route) -> None:
        answer: APIResponse = await route.fetch()
        detail = await answer.json()
        drawn = detail["lessons"]
        if lesson is None:
            detail["lessons"] = {**drawn, "lessons": [], "error": "the review model did not answer"}
        else:
            detail["lessons"] = {
                **drawn,
                "lessons": [{**each, **lesson} for each in drawn["lessons"][:2]],
                "error": None,
            }
        await route.fulfill(response=answer, json=detail)

    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    context: BrowserContext = await browser.new_context(
        viewport={"width": 1280, "height": 900}, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.route(f"**/api/runs/{run_id}", rewrite)
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/runs/{run_id}")
    return page, errors


async def test_the_follow_up_of_a_dislike_names_the_notes_the_disliked_run_was_handed_and_forgets_one(
    runtime: Runtime, served: tuple[RunManager, str], tmp_path: Path
) -> None:
    """Item 654: the page of a run that followed up the owner's 👎 names, in its lessons card, the notes the disliked
    run was handed, each with Forget, which puts it in the trash; the card shows even with no lesson drawn."""
    _, address = served
    store = runtime.store
    chat = await store.create_thread("Weekly report", "test")
    made: list[RunRecord] = [
        await store.create_run(
            chat.id,
            goal,
            "task",
            SCRIPTED_MODEL,
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        for goal in ("Write the weekly report", "My reaction 👎 to your answer above: it is not right.")
    ]
    disliked, follow_up = made
    for run in made:
        await store.finish_run(run.id, "done", "Done.")
    note = await store.add_memory("Write the weekly report as one long paragraph.", ["lesson"], None, None, None, False)
    now: float = time.time()
    await store.mark_memories_used(disliked.id, [note.id], now, "start")
    await store.put_reaction(disliked.id, "answer", "wrong", chat.id, now)
    await store.link_follow_up(disliked.id, "answer", "wrong", follow_up.id)
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for width, scheme in _SHOTS:
                page, errors = await _page(browser, address, follow_up.id, width, scheme)
                card: Locator = page.get_by_role("region", name="Lessons from this task")
                await expect(card).to_be_visible()
                await expect(card.locator(".lessons-count")).to_have_text("1 to look at")
                await card.locator("summary").click()
                await expect(card).to_contain_text("The run you reacted 👎 to was handed these notes")
                await expect(card.locator(".lesson.doubted .lesson-text")).to_have_text(note.content)
                await page.screenshot(path=str(tmp_path / f"doubted-{width}-{scheme}.png"))
                assert errors == []
                await page.context.close()
            page, errors = await _page(browser, address, follow_up.id, 1280, "light")
            card = page.get_by_role("region", name="Lessons from this task")
            await card.locator("summary").click()
            await card.get_by_role("button", name="Forget").click()
            await expect(card).to_contain_text("Forgotten: in the trash for 30 days")
            assert await store.get_memories([note.id]) == []
            assert errors == []
            await page.context.close()
        finally:
            await browser.close()
