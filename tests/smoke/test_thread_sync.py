"""The phone's sync contract (item 1686) on a real server in a throw-away home: a chat's view holds its queue, its
helper in another chat with what it does now and the helper's ask, at a revision that every change moves, whichever
process writes (a second connection to the database plays a ``dotx`` command); the list's stream tells each move as
``thread_changed``, a deleted chat among them, whose view is then 404. A chat's stream carries a run that starts later
in the chat and the helper it starts, an event told late with an id lower than one sent, and after a break goes on
from the client's cursors with nothing lost. No model is called."""

import asyncio
import json
import re
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent import events as events_module
from dotagent.agent.helper_reports import DetachedHelper, keep_detached
from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import db_path, ensure_layout
from dotagent.llm.types import Message
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.thread_sync import follow_thread, watch_revisions
from dotagent.store.db import Store
from dotagent.store.models import NO_PICKS, EventRecord, RunRecord, ThreadRecord

pytestmark = pytest.mark.asyncio

TOKEN: str = "thread-sync-token"
_MODEL: str = "ollama:none"
_WAIT_S: float = 20.0


@dataclass(frozen=True)
class Served:
    runtime: Runtime
    http: httpx.AsyncClient


@dataclass(frozen=True)
class Frame:
    kind: str
    event: EventRecord


@pytest_asyncio.fixture
async def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Served]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (tmp_path / "home" / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    runtime: Runtime = await build_runtime(load_config(tmp_path / "home" / "config.toml"))
    manager: RunManager = RunManager(runtime)
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
    watching: asyncio.Task[None] = asyncio.create_task(watch_revisions(runtime.store, runtime.bus))
    try:
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
        ) as http:
            yield Served(runtime=runtime, http=http)
    finally:
        watching.cancel()
        stopping.set()
        server.should_exit = True
        await serving
        await manager.shutdown()
        await close_runtime(runtime)


async def _frames(response: httpx.Response) -> AsyncIterator[Frame]:
    kind: str = ""
    data: str = ""
    async for line in response.aiter_lines():
        if line.startswith("event: "):
            kind = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data = line.removeprefix("data: ")
        elif line == "" and kind:
            yield Frame(kind=kind, event=EventRecord.model_validate_json(data))
            kind, data = "", ""


async def _run(store: Store, thread_id: str, parent: str | None, goal: str = "goal") -> RunRecord:
    return await store.create_run(
        thread_id, goal, "task", _MODEL, parent, "test", "medium", "standard", None, "ask", False, None, None, None
    )


async def _changed(frames: AsyncIterator[Frame], thread_id: str) -> dict[str, object]:
    """The data of the next ``thread_changed`` of the chat."""
    async with asyncio.timeout(_WAIT_S):
        async for frame in frames:
            if frame.kind == "thread_changed" and frame.event.data["thread_id"] == thread_id:
                return frame.event.data
    raise AssertionError("the stream ended")


def _rename_elsewhere(database: Path, thread_id: str) -> None:
    """A write of another process (a dotx command): a connection of its own, no store, no bus."""
    connection: sqlite3.Connection = sqlite3.connect(database, timeout=10)
    try:
        connection.execute("UPDATE threads SET title = 'Named by a command' WHERE id = ?", (thread_id,))
        connection.commit()
    finally:
        connection.close()


async def test_a_chats_view_holds_its_state_at_a_revision_that_every_writer_moves(served: Served) -> None:
    store: Store = served.runtime.store
    http: httpx.AsyncClient = served.http
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    root: RunRecord = await _run(store, chat.id, None)
    await served.runtime.bus.emit(root.id, "assistant", {"step": 1, "text": "Started a helper."}, True)
    await store.finish_run(root.id, "done", "ok")
    elsewhere: ThreadRecord = await store.create_thread("Helper's chat", "delegate")
    helper: RunRecord = await _run(
        store, elsewhere.id, root.id, "Run the tests of the store for run_mwupcdl7_a9d338. Then report what failed."
    )
    await store.set_run_status(helper.id, "running")
    await served.runtime.bus.emit(helper.id, "tool_call", {"tool": "shell", "args": {"command": "pytest -q"}}, True)
    await store.hold_queue(chat.id)
    await store.add_queued(
        chat.id, "And then the docs.", (), "task", None, None, "standard", None, NO_PICKS, None, None, None, "ui"
    )
    ask = await store.create_approval(helper.id, "shell", "Push the branch", "handoff", {})

    answer: httpx.Response = await http.get(f"/api/threads/{chat.id}/view")
    assert answer.status_code == 200, answer.text
    view: dict[str, object] = answer.json()
    first: int = int(view["revision"])  # type: ignore[call-overload]  # JSON
    assert first > 0
    assert [run["id"] for run in view["runs"]] == [root.id]  # type: ignore[attr-defined, index]
    assert view["newest"]["run"]["id"] == root.id  # type: ignore[index, call-overload]
    assert [event["kind"] for event in view["newest_events"]] == ["assistant"]  # type: ignore[attr-defined, index]
    assert view["queue"]["held"] is True  # type: ignore[index, call-overload]
    assert view["queue"]["messages"][0]["text"] == "And then the docs."  # type: ignore[index, call-overload]
    (shown,) = view["helpers"]  # type: ignore[misc]
    assert (shown["run_id"], shown["thread_id"], shown["status"]) == (helper.id, elsewhere.id, "running")
    assert (shown["provider"], shown["model"], shown["effort"]) == ("ollama", "none", "medium")
    assert (shown["step"]["tool"], shown["step"]["target"]) == ("shell", "pytest -q")
    assert shown["step"]["line"] == "Running a command" and shown["stop"] == f"/api/runs/{helper.id}/cancel"
    assert shown["title"] == "Run the tests of the store for." and shown["blocking"] is True
    assert view["background"] == []
    assert [one["id"] for one in view["asks"]] == [ask.id]  # type: ignore[attr-defined, index]
    listed: dict[str, object] = (await http.get("/api/threads/view")).json()
    (row,) = [one for one in listed["threads"] if one["summary"]["id"] == chat.id]  # type: ignore[attr-defined]
    assert (row["revision"], row["asks"]) == (first, 1)

    gone: ThreadRecord = await store.create_thread("To delete", "ui")
    async with http.stream("GET", "/api/threads/stream") as stream:
        assert stream.status_code == 200
        frames: AsyncIterator[Frame] = _frames(stream)
        async with aclosing(frames):
            assert (await http.patch(f"/api/threads/{chat.id}", json={"title": "Renamed"})).status_code == 204
            renamed: dict[str, object] = await _changed(frames, chat.id)
            assert int(renamed["revision"]) > first and renamed["deleted"] is False  # type: ignore[call-overload]
            assert (await http.patch(f"/api/threads/{chat.id}", json={"pinned": True})).status_code == 204
            pinned: dict[str, object] = await _changed(frames, chat.id)
            assert int(pinned["revision"]) > int(renamed["revision"])  # type: ignore[call-overload]
            # Another process writes: no bus of this daemon hears it, the revision tells it.
            await asyncio.to_thread(_rename_elsewhere, db_path(), chat.id)
            named: dict[str, object] = await _changed(frames, chat.id)
            assert int(named["revision"]) > int(pinned["revision"])  # type: ignore[call-overload]
            # The helper let go to the background (its parent no longer waits for it) moves the chat, as the view says.
            await keep_detached(
                store,
                DetachedHelper(
                    helper_run_id=helper.id, parent_run_id=root.id, thread_id=chat.id, step=None, since=time.time()
                ),
            )
            released: dict[str, object] = await _changed(frames, chat.id)
            view = (await http.get(f"/api/threads/{chat.id}/view")).json()
            assert view["revision"] == released["revision"]
            assert view["helpers"][0]["blocking"] is False  # type: ignore[index, call-overload]
            assert [(one["kind"], one["id"]) for one in view["background"]] == [("helper", helper.id)]  # type: ignore[attr-defined]
            # The helper's end, in its own chat, moves the parent's chat too.
            await store.finish_run(helper.id, "done", "tests pass")
            ended: dict[str, object] = await _changed(frames, chat.id)
            view = (await http.get(f"/api/threads/{chat.id}/view")).json()
            assert view["revision"] == ended["revision"]
            assert view["thread"]["title"] == "Named by a command"  # type: ignore[index, call-overload]
            assert view["helpers"][0]["step"] is None  # type: ignore[index, call-overload]
            assert view["helpers"][0]["blocking"] is False  # type: ignore[index, call-overload]
            deleted: httpx.Response = await http.request("DELETE", "/api/threads", json={"ids": [gone.id]})
            assert deleted.status_code == 200, deleted.text
            told: dict[str, object] = await _changed(frames, gone.id)
            assert told["deleted"] is True
    assert (await http.get(f"/api/threads/{gone.id}/view")).status_code == 404


async def test_a_chats_view_holds_the_drafts_that_still_take_an_answer(served: Served) -> None:
    """A draft of the chat's run that waits, and one whose send failed, are in the view; a discarded one is not (the
    view once asked for a status no draft has, and held none)."""
    store: Store = served.runtime.store
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    root: RunRecord = await _run(store, chat.id, None)
    waiting = await store.add_draft(root.id, "mail", ["a@example.com"], [], "Waits", "Hello")
    failed = await store.add_draft(root.id, "mail", ["b@example.com"], [], "Failed", "Hello")
    await store.claim_draft(failed.id, ["b@example.com"], [], "Failed", "Hello")
    await store.settle_draft(failed.id, "Mail is not set up")
    dropped = await store.add_draft(root.id, "message", ["+358401234567"], [], "", "Hi")
    await store.discard_draft(dropped.id)

    view: dict[str, object] = (await served.http.get(f"/api/threads/{chat.id}/view")).json()
    shown: list[tuple[str, str]] = [(one["id"], one["status"]) for one in view["drafts"]]  # type: ignore[attr-defined]
    assert shown == [(waiting.id, "waiting"), (failed.id, "failed")]


async def _until_synchronized(frames: AsyncIterator[Frame], got: list[EventRecord]) -> dict[str, object]:
    async with asyncio.timeout(_WAIT_S):
        async for frame in frames:
            if frame.kind == "synchronized":
                return frame.event.data
            if frame.event.id > 0:
                got.append(frame.event)
    raise AssertionError("the stream ended before it was synchronized")


async def _until(frames: AsyncIterator[Frame], got: list[EventRecord], wanted: set[int]) -> None:
    async with asyncio.timeout(_WAIT_S):
        async for frame in frames:
            if frame.event.id > 0:
                got.append(frame.event)
            if wanted <= {event.id for event in got}:
                return
    raise AssertionError("the stream ended")


async def test_a_chats_stream_carries_new_runs_and_helpers_and_goes_on_from_the_cursors_after_a_break(
    served: Served,
) -> None:
    store: Store = served.runtime.store
    bus = served.runtime.bus
    http: httpx.AsyncClient = served.http
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    elsewhere: ThreadRecord = await store.create_thread("Helper's chat", "delegate")
    first: RunRecord = await _run(store, chat.id, None)
    told: list[int] = [(await bus.emit(first.id, "assistant", {"step": n}, True)).id for n in range(3)]
    await store.finish_run(first.id, "done", "ok")
    held: list[EventRecord] = []

    async with http.stream("GET", f"/api/threads/{chat.id}/stream", params={"cursors": f"{first.id}:{told[1]}"}) as s:
        assert s.status_code == 200
        frames: AsyncIterator[Frame] = _frames(s)
        async with aclosing(frames):
            synced: dict[str, object] = await _until_synchronized(frames, held)
            assert told[2] in {event.id for event in held}
            assert synced["runs"] == {first.id: told[2]}
            # A run that starts later in the chat and the helper it starts come on the same stream.
            second: RunRecord = await _run(store, chat.id, None)
            await bus.emit(second.id, "run_queued", {"goal": "goal", "parent_run_id": None}, True)
            step: EventRecord = await bus.emit(second.id, "tool_call", {"tool": "delegate"}, True)
            helper: RunRecord = await _run(store, elsewhere.id, second.id)
            await bus.emit(helper.id, "run_queued", {"goal": "goal", "parent_run_id": second.id}, True)
            # Told late: stored first, with the lower id, and published after an event of another run.
            late: EventRecord = await store.add_event(second.id, "assistant", {"step": 2, "text": "late"})
            sooner: EventRecord = await bus.emit(helper.id, "assistant", {"step": 1}, True)
            assert late.id < sooner.id
            bus.publish(late)
            await _until(frames, held, {step.id, sooner.id, late.id})
            renamed: httpx.Response = await http.patch(f"/api/threads/{chat.id}", json={"title": "On the stream"})
            assert renamed.status_code == 204
            await _changed(frames, chat.id)
    ids: list[int] = [event.id for event in held]
    assert len(ids) == len(set(ids)), "one stream sends an event once"

    # A break: what is stored meanwhile, an event never told on any bus among it, comes when the client is back.
    newest: dict[str, int] = {}
    for event in held:
        newest[event.run_id] = max(event.id, newest.get(event.run_id, 0))
    missed: list[int] = [
        (await bus.emit(second.id, "assistant", {"step": 3}, True)).id,
        (await store.add_event(helper.id, "assistant", {"step": 2})).id,
        (await bus.emit(helper.id, "tool_call", {"tool": "shell"}, True)).id,
    ]
    again: list[EventRecord] = []
    cursors: str = ",".join(f"{run_id}:{at}" for run_id, at in newest.items())
    async with http.stream("GET", f"/api/threads/{chat.id}/stream", params={"cursors": cursors}) as s:
        frames = _frames(s)
        async with aclosing(frames):
            await _until_synchronized(frames, again)
    again_ids: list[int] = [event.id for event in again]
    assert len(again_ids) == len(set(again_ids))
    assert set(missed) <= set(again_ids), "nothing stored during the break is lost"
    # What came again is what the client held already (the stream reads a little behind each cursor): kept once by id.
    assert set(again_ids) - set(missed) <= set(ids)
    stored: set[int] = {
        event.id
        for run_id in (first.id, second.id, helper.id)
        for event in await store.list_events(run_id, 0, 500)
        if event.kind != "loop_state"
    }
    assert stored == set(ids) | set(again_ids), "every stored event of the chat's runs and its helper came"


async def test_a_steps_thinking_and_words_reach_the_chats_stream_as_they_are_written(served: Served) -> None:
    """The owner's report of 2026-11-25 (the iPhone app's words came late): each live delta of a run, its thinking and
    its words (id 0, never stored), comes on the chat's stream one by one, in order, about as soon as it is said
    (the median under 100 ms)."""
    store: Store = served.runtime.store
    bus = served.runtime.bus
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    run: RunRecord = await _run(store, chat.id, None)
    opened: EventRecord = await bus.emit(run.id, "run_started", {}, True)
    said: list[tuple[str, float]] = []
    came: list[tuple[str, str, float]] = []
    async with served.http.stream(
        "GET", f"/api/threads/{chat.id}/stream", params={"cursors": f"{run.id}:{opened.id}"}
    ) as s:
        frames: AsyncIterator[Frame] = _frames(s)
        async with aclosing(frames):
            await _until_synchronized(frames, [])

            async def say() -> None:
                for n in range(40):
                    kind: str = "thinking" if n < 20 else "text"
                    said.append((f"piece {n}", time.monotonic()))
                    await bus.emit(
                        run.id, "delta", {"kind": kind, "text": f"piece {n}", "step": 1, "says": "either"}, False
                    )
                    await asyncio.sleep(0.02)

            saying: asyncio.Task[None] = asyncio.create_task(say())
            async with asyncio.timeout(_WAIT_S):
                async for frame in frames:
                    if frame.kind == "delta":
                        came.append((str(frame.event.data["kind"]), str(frame.event.data["text"]), time.monotonic()))
                        if len(came) == 40:
                            break
            await saying
    assert [text for _, text, _ in came] == [text for text, _ in said], "every delta came, in order"
    assert [kind for kind, _, _ in came] == ["thinking"] * 20 + ["text"] * 20, "the thinking comes too"
    late: list[float] = [at - when for (_, _, at), (_, when) in zip(came, said, strict=True)]
    # Order and completeness are the contract; the time is checked on the median, with a ceiling a loaded runner keeps.
    assert sorted(late)[len(late) // 2] < 0.1, f"half the deltas came later than 100 ms: {sorted(late)}"
    assert max(late) < 1.0, f"a delta came {max(late):.3f} s after it was said"


async def test_a_view_of_no_chat_and_a_cursor_of_another_chats_run_are_refused(served: Served) -> None:
    store: Store = served.runtime.store
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    other: ThreadRecord = await store.create_thread("Other", "ui")
    stranger: RunRecord = await _run(store, other.id, None)
    assert (await served.http.get("/api/threads/thr_none/view")).status_code == 404
    refused: httpx.Response = await served.http.get(
        f"/api/threads/{chat.id}/stream", params={"cursors": f"{stranger.id}:1"}
    )
    assert refused.status_code == 404, refused.text
    # A helper older than the view's newest 50 is still the chat's: its cursor is taken.
    root: RunRecord = await _run(store, chat.id, None)
    elsewhere: ThreadRecord = await store.create_thread("Helpers' chat", "delegate")
    oldest: RunRecord = await _run(store, elsewhere.id, root.id)
    for _ in range(55):
        await _run(store, elsewhere.id, root.id)
    await store.finish_run(root.id, "done", "ok")
    async with served.http.stream(
        "GET", f"/api/threads/{chat.id}/stream", params={"cursors": f"{oldest.id}:0"}
    ) as taken:
        assert taken.status_code == 200
    malformed: httpx.Response = await served.http.get(f"/api/threads/{chat.id}/stream", params={"cursors": "x"})
    assert malformed.status_code == 422, malformed.text
    assert json.loads(malformed.text)


async def test_a_run_and_a_helper_that_start_while_the_bus_drops_events_still_join_the_open_stream(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second pre-push review of item 1686: after a drop the stream read again only the runs it followed, so a run
    whose start was among the events lost never joined, and its steps never came."""
    store: Store = served.runtime.store
    bus = served.runtime.bus
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    elsewhere: ThreadRecord = await store.create_thread("Helper's chat", "delegate")
    other: ThreadRecord = await store.create_thread("Busy elsewhere", "ui")
    busy: RunRecord = await _run(store, other.id, None)
    monkeypatch.setattr(events_module, "_QUEUE_SIZE", 4)
    manager: RunManager = RunManager(served.runtime)
    working: set[str] = set()
    monkeypatch.setattr(manager, "active_run_ids", lambda: set(working))
    context: ServerContext = ServerContext(
        runtime=served.runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=1,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        async with aclosing(follow_thread(context, chat.id, {})) as stream:
            async with asyncio.timeout(_WAIT_S):
                assert b"event: synchronized" in await anext(stream)
            # Unread meanwhile: a run of the chat starts and ends, the next one starts and starts a helper, and another
            # chat's run fills the queue.
            brief: RunRecord = await _run(store, chat.id, None)
            await bus.emit(brief.id, "run_queued", {"goal": "goal", "parent_run_id": None}, True)
            said: EventRecord = await bus.emit(brief.id, "assistant", {"step": 1, "text": "done"}, True)
            await store.finish_run(brief.id, "done", "ok")
            late: RunRecord = await _run(store, chat.id, None)
            working.add(late.id)
            await bus.emit(late.id, "run_queued", {"goal": "goal", "parent_run_id": None}, True)
            helper: RunRecord = await _run(store, elsewhere.id, late.id)
            await bus.emit(helper.id, "run_queued", {"goal": "goal", "parent_run_id": late.id}, True)
            for number in range(10):
                await bus.emit(busy.id, "assistant", {"step": number}, True)
            wanted: set[int] = {
                said.id,
                (await bus.emit(late.id, "tool_call", {"tool": "shell"}, True)).id,
                (await bus.emit(helper.id, "tool_call", {"tool": "read_file"}, True)).id,
            }
            got: set[int] = set()
            async with asyncio.timeout(_WAIT_S):
                while not wanted <= got:
                    for found in re.findall(rb"^id: (\d+)$", await anext(stream), re.MULTILINE):
                        got.add(int(found))
    finally:
        await manager.shutdown()


async def test_a_drop_does_not_read_again_the_whole_history_of_an_old_runs_finished_helper(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third pre-push review of item 1686: after a drop the stream widened every followed run to its helpers, so
    the finished helper of a run the phone named in its cursors was read again from its first event at every drop."""
    store: Store = served.runtime.store
    bus = served.runtime.bus
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    elsewhere: ThreadRecord = await store.create_thread("Helper's chat", "delegate")
    other: ThreadRecord = await store.create_thread("Busy elsewhere", "ui")
    busy: RunRecord = await _run(store, other.id, None)
    old: RunRecord = await _run(store, chat.id, None)
    said: EventRecord = await bus.emit(old.id, "assistant", {"step": 1, "text": "done"}, True)
    helper: RunRecord = await _run(store, elsewhere.id, old.id)
    helper_ids: set[int] = {(await bus.emit(helper.id, "assistant", {"step": n}, True)).id for n in range(3)}
    await store.finish_run(helper.id, "done", "ok")
    await store.finish_run(old.id, "done", "ok")
    monkeypatch.setattr(events_module, "_QUEUE_SIZE", 4)
    manager: RunManager = RunManager(served.runtime)
    monkeypatch.setattr(manager, "active_run_ids", set)
    context: ServerContext = ServerContext(
        runtime=served.runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=1,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        async with aclosing(follow_thread(context, chat.id, {old.id: said.id})) as stream:
            async with asyncio.timeout(_WAIT_S):
                assert b"event: synchronized" in await anext(stream)
            for number in range(10):
                await bus.emit(busy.id, "assistant", {"step": number}, True)
            after: RunRecord = await _run(store, chat.id, None)
            wanted: int = (await bus.emit(after.id, "assistant", {"step": 1}, True)).id
            got: set[int] = set()
            async with asyncio.timeout(_WAIT_S):
                while wanted not in got:
                    for found in re.findall(rb"^id: (\d+)$", await anext(stream), re.MULTILINE):
                        got.add(int(found))
            assert not got & helper_ids, "the old run's finished helper was read again after the drop"
    finally:
        await manager.shutdown()


async def test_a_helper_that_starts_and_ends_during_a_drop_still_comes(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fourth pre-push review of item 1686: a helper of a run the stream follows through a cursor, made, stored and
    finished while the bus dropped events, must still come."""
    store: Store = served.runtime.store
    bus = served.runtime.bus
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    elsewhere: ThreadRecord = await store.create_thread("Helper's chat", "delegate")
    other: ThreadRecord = await store.create_thread("Busy elsewhere", "ui")
    busy: RunRecord = await _run(store, other.id, None)
    root: RunRecord = await _run(store, chat.id, None)
    said: EventRecord = await bus.emit(root.id, "assistant", {"step": 1, "text": "working"}, True)
    monkeypatch.setattr(events_module, "_QUEUE_SIZE", 4)
    manager: RunManager = RunManager(served.runtime)
    monkeypatch.setattr(manager, "active_run_ids", set)
    context: ServerContext = ServerContext(
        runtime=served.runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=1,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        async with aclosing(follow_thread(context, chat.id, {root.id: said.id})) as stream:
            async with asyncio.timeout(_WAIT_S):
                assert b"event: synchronized" in await anext(stream)
            helper: RunRecord = await _run(store, elsewhere.id, root.id)
            wanted: set[int] = {(await bus.emit(helper.id, "assistant", {"step": n}, True)).id for n in range(2)}
            await store.finish_run(helper.id, "done", "ok")
            for number in range(10):
                await bus.emit(busy.id, "assistant", {"step": number}, True)
            await bus.emit(root.id, "assistant", {"step": 2}, True)
            got: set[int] = set()
            async with asyncio.timeout(_WAIT_S):
                while not wanted <= got:
                    for found in re.findall(rb"^id: (\d+)$", await anext(stream), re.MULTILINE):
                        got.add(int(found))
    finally:
        await manager.shutdown()


async def test_a_page_of_older_runs_holds_a_run_made_at_the_same_instant_as_the_one_it_starts_before(
    served: Served,
) -> None:
    store: Store = served.runtime.store
    chat: ThreadRecord = await store.create_thread("Chat", "ui")
    runs: list[RunRecord] = [await _run(store, chat.id, None) for _ in range(3)]
    await asyncio.to_thread(_same_instant, db_path(), [run.id for run in runs])
    seen: list[str] = []
    before: str | None = None
    for _ in range(3):
        params: dict[str, str] = {"tail": "1"} if before is None else {"tail": "1", "before": before}
        page: dict[str, object] = (await served.http.get(f"/api/threads/{chat.id}/view", params=params)).json()
        (only,) = page["runs"]  # type: ignore[misc]
        seen.append(only["id"])
        before = only["id"]
    assert sorted(seen) == sorted(run.id for run in runs)


def _same_instant(database: Path, run_ids: list[str]) -> None:
    connection: sqlite3.Connection = sqlite3.connect(database, timeout=10)
    try:
        connection.executemany("UPDATE runs SET created_at = 1000.0 WHERE id = ?", [(run_id,) for run_id in run_ids])
        connection.commit()
    finally:
        connection.close()


async def test_the_home_search_finds_what_a_chat_said_and_refuses_an_empty_query(served: Served) -> None:
    """The phone's home search (item 1882): `GET /api/threads/search` finds a chat by a word of its history, folded as
    the index folds it, and says nothing of a chat that does not hold it; an empty query is refused."""
    store: Store = served.runtime.store
    said: ThreadRecord = await store.create_thread("Car", "ui")
    other: ThreadRecord = await store.create_thread("Other", "ui")
    await _run(store, said.id, None)
    await _run(store, other.id, None)
    await store.append_messages(said.id, [_said("user", "Book the Lastikçi garage for Friday")])
    await store.append_messages(other.id, [_said("user", "Water the plants")])
    found: httpx.Response = await served.http.get("/api/threads/search", params={"q": "LASTIKCI"})
    assert found.status_code == 200
    hits: list[dict[str, object]] = found.json()
    assert [hit["thread_id"] for hit in hits] == [said.id]
    assert "[Lastikçi]" in str(hits[0]["snippet"])
    none: httpx.Response = await served.http.get("/api/threads/search", params={"q": "nothing-said-here"})
    assert none.json() == []
    empty: httpx.Response = await served.http.get("/api/threads/search", params={"q": ""})
    assert empty.status_code == 422


def _said(role: str, content: str) -> Message:
    return Message(
        role="user" if role == "user" else "assistant",
        content=content,
        images=(),
        tool_calls=(),
        tool_call_id=None,
        tool_name=None,
        thinking=None,
        raw=(),
        raw_origin=None,
    )
