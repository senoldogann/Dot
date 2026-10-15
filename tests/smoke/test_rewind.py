"""Edit from here (item 282), after T3 Code's: a chat of three turns, whose runs change files with the file tools on a
scripted model, is rewound from its page to before its second message. The second and third turns leave the chat, the
files are as the first turn left them (the file the third made is gone, with its folder), the chat's OpenCode session is
let go, and the second message with its file is back in the composer of the first turn's page; sent from there, the next
run's history holds no word of the turns taken out. A rewind that keeps the changes leaves the files, and one to a turn
taken out already is refused. In the store, a rewind across a compaction reads the history kept aside, a question a run
taken out left waits no more, and a helper's run, a run from before the places were kept and a chat at work are refused. The dialog wide and light, and on a phone
in the dark. A real server and a real headless Chrome; no model service is called."""

import asyncio
import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, FloatRect, Locator, Page, async_playwright, expect

from dotagent.agent.attachments import StoredAttachment, store_attachment
from dotagent.agent.manager import RunManager
from dotagent.config import load_default_config
from dotagent.core.errors import InvalidValueError, RunConflict
from dotagent.core.paths import db_path, opencode_dir
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import Message, assistant_message, user_message
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, RewindCut, RunRecord, ThreadRecord, ThreadSummary
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "rewind-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FIRST: str = "Start the notes."
_SECOND: str = "Add a line, as beds.txt says."
_THIRD: str = "Add another line, and a list."
_TIME_LIMIT_S: float = 60.0
_DIALOG_LINE: str = "Rewind chat to before this message. Your prompt and attachments return to the composer."
_EDGE_PX: float = 1.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _write(path: Path, content: str) -> ScriptedTurn:
    return ScriptedTurn(
        text="", calls=(ScriptedCall(tool="write_file", arguments={"path": str(path), "content": content}),)
    )


def _finish(answer: str) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}),))


def _script(workspace: Path) -> list[ScriptedTurn]:
    """The turns of the chat's four runs: three that change the notes (the third makes a list in a new folder too), and
    the one sent again after the rewind."""
    notes: Path = workspace / "notes.txt"
    return [
        _write(notes, "one\n"),
        _finish("The notes are started."),
        _write(notes, "one\ntwo\n"),
        _finish("A line is added."),
        _write(notes, "one\ntwo\nthree\n"),
        _write(workspace / "made" / "list.txt", "a list\n"),
        _finish("Another line, and a list."),
        _write(notes, "one\ntwo again\n"),
        _finish("The line is added again."),
    ]


async def _serve(runtime: Runtime, manager: RunManager) -> tuple[DotServer, asyncio.Task[None], str]:
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
    server = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    return server, serving, f"http://127.0.0.1:{port}"


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(login_env: dict[str, str]) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """A runtime in a throw-away home on the scripted model, its run manager and the address of a real server."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _script(runtime.workspace)))
            manager: RunManager = RunManager(runtime)
            try:
                server, serving, address = await _serve(runtime, manager)
                try:
                    yield runtime, manager, address
                finally:
                    server.should_exit = True
                    await serving
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _done(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(_TIME_LIMIT_S):
        record: RunRecord = await manager.wait(run_id)
    assert record.status == "done", record
    return record


async def _turn(
    manager: RunManager, goal: str, thread_id: str | None, files: tuple[StoredAttachment, ...]
) -> RunRecord:
    started: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, thread_id, "test", None, None, "standard", None, "ask", False, files
    )
    return await _done(manager, started.id)


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> tuple[Page, list[str]]:
    context = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    return page, errors


async def _text(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


def _user_texts(messages: list[Message]) -> list[str]:
    return [message.content for message in messages if message.role == "user"]


async def _box(locator: Locator) -> FloatRect:
    box: FloatRect | None = await locator.bounding_box()
    assert box is not None
    return box


async def test_a_chat_rewound_to_its_second_message_takes_its_files_back_and_gives_the_message_back(
    served: tuple[Runtime, RunManager, str], tmp_path: Path
) -> None:
    runtime, manager, address = served
    workspace: Path = runtime.workspace
    notes: Path = workspace / "notes.txt"
    beds: StoredAttachment = await asyncio.to_thread(
        store_attachment, workspace, "beds.txt", "text/plain", b"North bed, south bed.\n"
    )
    first: RunRecord = await _turn(manager, _FIRST, None, ())
    second: RunRecord = await _turn(manager, _SECOND, first.thread_id, (beds,))
    third: RunRecord = await _turn(manager, _THIRD, first.thread_id, ())
    assert (first.rewindable, second.rewindable, third.rewindable) == (True, True, True)
    assert await _text(notes) == "one\ntwo\nthree\n"
    assert (workspace / "made" / "list.txt").is_file()
    # The chat's OpenCode session, as a run on OpenCode's agent leaves it.
    session: Path = opencode_dir() / "chats" / f"{first.thread_id}.json"
    session.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(session.write_text, '{"session_id": "ses_rewind", "seen": 6}\n', encoding="utf-8")
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            page, errors = await _page(browser, address, 1280, "light")
            await page.goto(f"{address}/#/runs/{third.id}")
            earlier: Locator = page.locator(".history .user-message")
            await expect(earlier).to_have_count(2)
            await expect(
                page.locator(".run-thread > .user-message").get_by_role("button", name="Edit from here")
            ).to_be_visible()

            # The dialog says what T3 Code's says, and Cancel leaves all as it was.
            await earlier.nth(1).get_by_role("button", name="Edit from here").click()
            dialog: Locator = page.locator("dialog.modal")
            await expect(dialog.get_by_role("heading", name="Edit from here?")).to_be_visible()
            await expect(dialog).to_contain_text(_DIALOG_LINE)
            labels: list[str] = await dialog.locator(".modal-actions button").all_inner_texts()
            assert labels == ["Cancel", "Revert files too", "Revert and keep changes"], labels
            # Wide, they stand side by side, inside the dialog.
            row: list[FloatRect] = [
                await _box(button) for button in await dialog.locator(".modal-actions button").all()
            ]
            assert len({round(box["y"]) for box in row}) == 1, row
            frame: FloatRect = await _box(dialog)
            assert row[-1]["x"] + row[-1]["width"] <= frame["x"] + frame["width"], (row, frame)
            await page.screenshot(path=str(tmp_path / "rewind-1280-light.png"), animations="disabled")
            await dialog.get_by_role("button", name="Cancel").click()
            await expect(dialog).to_have_count(0)
            assert [run.id for run in await runtime.store.list_runs(10, first.thread_id)] == [
                third.id,
                second.id,
                first.id,
            ]
            assert await _text(notes) == "one\ntwo\nthree\n"

            # Revert files too: the page of the first turn, alone in the chat, with the second message and its file in
            # the box; the files as the first turn left them, and the chat's OpenCode session let go.
            await earlier.nth(1).get_by_role("button", name="Edit from here").click()
            await dialog.get_by_role("button", name="Revert files too").click()
            await expect(page).to_have_url(f"{address}/#/runs/{first.id}")
            await expect(page.locator(".toast.ok").last).to_contain_text(
                "The chat went back to before your message, and 2 files are as they were."
            )
            await expect(page.locator(".dock textarea")).to_have_value(_SECOND)
            await expect(page.locator(".dock .attach-chip")).to_contain_text("beds.txt")
            await expect(page.locator(".run-thread .user-message")).to_have_count(1)
            assert await _text(notes) == "one\n"
            assert not (workspace / "made").exists()
            assert not await asyncio.to_thread(session.exists)
            assert [run.id for run in await runtime.store.list_runs(10, first.thread_id)] == [first.id]
            summaries: list[ThreadSummary] = await runtime.store.list_thread_summaries(50)
            chat: ThreadSummary = next(summary for summary in summaries if summary.id == first.thread_id)
            assert (chat.run_count, chat.latest_run_id) == (1, first.id)

            # Sent from there, the next run reads the first turn and the message, and nothing of the turns taken out.
            await page.locator(".dock button.send").click()
            await expect(page).not_to_have_url(f"{address}/#/runs/{first.id}")
            again: RunRecord = await _done(manager, page.url.rpartition("/")[2])
            assert (again.goal, again.thread_id) == (_SECOND, first.thread_id)
            said: list[str] = _user_texts(await runtime.store.load_messages(first.thread_id, 0))
            openings: list[str] = [text for text in said if text.startswith((_FIRST, _SECOND, _THIRD))]
            assert len(openings) == 2, said
            assert openings[0].startswith(_FIRST) and openings[1].startswith(_SECOND), said
            assert await _text(notes) == "one\ntwo again\n"
            assert errors == []
            await page.context.close()

            # Revert and keep changes, through the API: the files stay as the run left them.
            async with httpx.AsyncClient(base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
                kept: httpx.Response = await client.post(f"/api/runs/{again.id}/rewind", json={"files": "keep"})
                assert kept.status_code == 200, kept.text
                view = kept.json()
                assert (view["back_to"], view["text"], view["reverted"], view["left"]) == (first.id, _SECOND, [], [])
                assert [file["name"] for file in view["files"]] == ["beds.txt"]
                assert await _text(notes) == "one\ntwo again\n"
                for taken_out in (again, third):
                    refused: httpx.Response = await client.post(
                        f"/api/runs/{taken_out.id}/rewind", json={"files": "revert"}
                    )
                    assert refused.status_code == 409, refused.text
                    assert "rewound to before this message already" in refused.text

            # On a phone in the dark, the three ways out stand one above the other, the dialog's width, Cancel at the
            # foot.
            phone, phone_errors = await _page(browser, address, 390, "dark")
            await phone.goto(f"{address}/#/runs/{first.id}")
            await phone.locator(".run-thread > .user-message").get_by_role("button", name="Edit from here").click()
            actions: Locator = phone.locator("dialog.modal .modal-actions")
            await expect(actions).to_be_visible()
            keep_box: FloatRect = await _box(actions.get_by_role("button", name="Revert and keep changes"))
            revert_box: FloatRect = await _box(actions.get_by_role("button", name="Revert files too"))
            cancel_box: FloatRect = await _box(actions.get_by_role("button", name="Cancel"))
            assert keep_box["y"] < revert_box["y"] < cancel_box["y"], (keep_box, revert_box, cancel_box)
            for box in (revert_box, cancel_box):
                assert abs(box["x"] - keep_box["x"]) <= _EDGE_PX and abs(box["width"] - keep_box["width"]) <= _EDGE_PX
            dialog_box: FloatRect = await _box(phone.locator("dialog.modal"))
            assert keep_box["x"] + keep_box["width"] <= dialog_box["x"] + dialog_box["width"]
            await phone.screenshot(path=str(tmp_path / "rewind-390-dark.png"), animations="disabled")
            await actions.get_by_role("button", name="Cancel").click()
            await expect(phone.locator("dialog.modal")).to_have_count(0)
            assert phone_errors == []
            await phone.context.close()
        finally:
            await browser.close()


async def _store_turn(store: Store, thread_id: str, said: tuple[str, str]) -> RunRecord:
    """A run of the chat that said ``said`` (its message and its answer) and ended."""
    run: RunRecord = await store.create_run(
        thread_id,
        said[0],
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
    await store.append_messages(thread_id, [user_message(said[0], ()), assistant_message(said[1], None, ())])
    await store.set_run_status(run.id, "done")
    return run


async def _history(store: Store, thread_id: str) -> list[str]:
    return [message.content for message in await store.load_messages(thread_id, 0)]


async def test_a_rewind_across_a_compaction_reads_the_history_kept_aside_and_is_refused_where_it_cannot_be(
    served: tuple[Runtime, RunManager, str],
) -> None:
    store: Store = served[0].store
    thread: ThreadRecord = await store.create_thread("Compacted", "test")
    one: RunRecord = await _store_turn(store, thread.id, ("one", "a1"))
    two: RunRecord = await _store_turn(store, thread.id, ("two", "a2"))
    three: RunRecord = await _store_turn(store, thread.id, ("three", "a3"))
    left: ApprovalRecord = await store.create_left_approval(three.id, "ask_user", "Which bed first?", "low", {})

    # Within one version of the history, a rewind cuts it back, and the question the third run left waits no more.
    cut: RewindCut = await store.rewind_chat(three.id)
    assert ([run.id for run in cut.rewound], cut.back_to) == ([three.id], two.id)
    assert await _history(store, thread.id) == ["one", "a1", "two", "a2"]
    assert (await store.get_approval(left.id)).status == "expired"

    # A run compacts the history, and the next run works on the new version; rewound to before the second run, the
    # history is what was kept aside of the old version, up to the second run's message.
    four: RunRecord = await store.create_run(
        thread.id,
        "four",
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
    await store.append_messages(thread.id, [user_message("four", ()), assistant_message("a4", None, ())])
    await store.replace_messages(thread.id, [user_message("one to four, in short", ())])
    await store.append_messages(thread.id, [assistant_message("a4 after", None, ())])
    await store.set_run_status(four.id, "done")
    five: RunRecord = await _store_turn(store, thread.id, ("five", "a5"))
    assert await _history(store, thread.id) == ["one to four, in short", "a4 after", "five", "a5"]
    cut = await store.rewind_chat(two.id)
    assert ([run.id for run in cut.rewound], cut.back_to) == ([two.id, four.id, five.id], one.id)
    assert await _history(store, thread.id) == ["one", "a1"]
    assert [run.id for run in await store.list_runs(10, thread.id)] == [one.id]

    # The next run follows on from there, and the chat can be rewound to its first message, which leaves it empty and
    # out of the list of chats.
    six: RunRecord = await _store_turn(store, thread.id, ("six", "a6"))
    assert await _history(store, thread.id) == ["one", "a1", "six", "a6"]
    with pytest.raises(RunConflict, match="rewound to before this message already"):
        await store.rewind_chat(five.id)

    # A helper's run has no message of the owner's; a run from before the places were kept has none kept; a chat at
    # work is not rewound.
    helper: RunRecord = await store.create_run(
        thread.id,
        "help",
        "task",
        SCRIPTED_MODEL,
        one.id,
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
    assert not helper.rewindable
    await store.set_run_status(helper.id, "done")
    with pytest.raises(InvalidValueError, match="no message of the owner's"):
        await store.rewind_chat(helper.id)
    connection: sqlite3.Connection = sqlite3.connect(db_path(), timeout=10)
    try:
        with connection:
            connection.execute("UPDATE runs SET history_version = NULL, history_seq = NULL WHERE id = ?", (six.id,))
    finally:
        connection.close()
    with pytest.raises(InvalidValueError, match="sent before Dot kept the place"):
        await store.rewind_chat(six.id)
    working: RunRecord = await store.create_run(
        thread.id,
        "seven",
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
    with pytest.raises(RunConflict, match="cannot be rewound while a run works on it"):
        await store.rewind_chat(one.id)
    await store.set_run_status(working.id, "done")
    cut = await store.rewind_chat(one.id)
    assert ([run.id for run in cut.rewound], cut.back_to) == ([one.id, six.id, working.id], None)
    assert await _history(store, thread.id) == []
    assert thread.id not in [summary.id for summary in await store.list_thread_summaries(50)]
