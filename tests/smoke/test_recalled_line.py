"""A chat says it recalled notes only when it recalls one it had not before (item 335), through the real loop: three
chat runs of one thread on the scripted model, with notes found by their words. The first run is handed two notes and
its line says so; the second is handed the same two and draws no line; the third is handed those and a note saved
since, and its line says that one of the three is new. The events count the fresh notes and the pages draw them, in a
real headless Chrome. No model is called and no embedding service is reached.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import assistant_message, user_message
from dotagent.memory.service import save_memory
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "recalled-line-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_GOAL: str = "Write the weekly report"
_NOTES: tuple[str, ...] = (
    "The weekly report goes into the reports folder.",
    "The weekly report is written in British English.",
)
_LATER: str = "The weekly report lists the late projects first."
_RUN_WAIT_S: float = 60.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A throw-away home where every model role is the scripted one and notes are found by their words."""
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


async def _chat(manager: RunManager, thread_id: str | None) -> RunRecord:
    """A message of the chat ``thread_id`` (a new chat for ``None``), once its run is over."""
    run: RunRecord = await manager.start(
        _GOAL, "chat", SCRIPTED_MODEL, thread_id, "ui", None, None, "standard", None, "yolo", None, ()
    )
    async with asyncio.timeout(_RUN_WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended.result
    return ended


async def _fresh(runtime: Runtime, run: RunRecord) -> tuple[int, int]:
    """How many notes the run was handed at its start, and how many of them its chat had not recalled before."""
    events: list[EventRecord] = await runtime.store.events_of_kind(run.id, "memories", 5)
    assert len(events) == 1, events
    ids: object = events[0].data["ids"]
    fresh: object = events[0].data["fresh"]
    assert isinstance(ids, list) and isinstance(fresh, int)
    return len(ids), fresh


async def test_a_chat_says_it_recalled_notes_only_when_one_is_new(runtime: Runtime, tmp_path: Path) -> None:
    for note in _NOTES:
        await save_memory(runtime.memory, note, (), None, None, None)
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [ScriptedTurn(text="Done.", calls=())] * 12))
    manager: RunManager = RunManager(runtime)
    try:
        first: RunRecord = await _chat(manager, None)
        second: RunRecord = await _chat(manager, first.thread_id)
        await save_memory(runtime.memory, _LATER, (), None, None, None)
        third: RunRecord = await _chat(manager, first.thread_id)
        assert [await _fresh(runtime, run) for run in (first, second, third)] == [(2, 2), (2, 0), (3, 1)]
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
            async with async_playwright() as playwright:
                browser: Browser = await playwright.chromium.launch(
                    channel=runtime.config.browser.channel, headless=True
                )
                try:
                    page: Page = await browser.new_page(viewport={"width": 1280, "height": 860})
                    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
                    await page.goto(ui_url("127.0.0.1", port, TOKEN))
                    recalled: Locator = page.locator(".note", has_text="Recalled")
                    await page.goto(f"http://127.0.0.1:{port}/#/runs/{first.id}")
                    await expect(page.locator(".answer").last).to_contain_text("Done.")
                    await expect(recalled).to_have_count(1)
                    await expect(recalled).to_contain_text("Recalled 2 memories, ")
                    await expect(recalled).not_to_contain_text("new")
                    await page.goto(f"http://127.0.0.1:{port}/#/runs/{second.id}")
                    await expect(page.locator(".answer").last).to_contain_text("Done.")
                    await expect(recalled).to_have_count(0)
                    await page.goto(f"http://127.0.0.1:{port}/#/runs/{third.id}")
                    await expect(page.locator(".answer").last).to_contain_text("Done.")
                    await expect(recalled).to_have_count(1)
                    await expect(recalled).to_contain_text("Recalled 3 memories (1 new), ")
                    await page.screenshot(path=str(tmp_path / "recalled-third.png"), full_page=True)
                finally:
                    await browser.close()
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def test_a_run_a_rewind_took_out_of_the_chat_no_longer_counts_its_notes(runtime: Runtime) -> None:
    """Edit from here (item 282) takes a run out of its chat: the notes it was handed are new again to the run that
    takes its place (review B6)."""
    store: Store = runtime.store
    thread: str = (await store.create_thread("Rewound", "test")).id
    note: str = (await save_memory(runtime.memory, "The rewound chat's note.", (), None, None, None)).record.id
    first: RunRecord = await _stored_run(store, thread, "First message")
    await store.mark_memories_used(first.id, [note], time.time(), "start")
    await store.rewind_chat(first.id, [run.id for run in await store.runs_from(first.id)])
    again: RunRecord = await _stored_run(store, thread, "The message written again")
    assert await store.handed_in_thread(thread, again.id) == set()


async def _stored_run(store: Store, thread_id: str, goal: str) -> RunRecord:
    """A run of the chat ``thread_id`` that said ``goal`` and ended, as the store keeps one."""
    run: RunRecord = await store.create_run(
        thread_id,
        goal,
        "chat",
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
    await store.append_messages(thread_id, [user_message(goal, ()), assistant_message("Done.", None, ())])
    await store.set_run_status(run.id, "done")
    return run
