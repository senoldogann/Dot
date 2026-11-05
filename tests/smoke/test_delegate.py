"""Delegation end to end: a run hands part of its work to a run of its own, against the real store.

The runs use the real local Ollama server and a cloud model; the tests that need it are skipped when it is not there.
The manager is built with a single run slot, so a delegated run can only finish if it does not need a slot of its own.
"""

import asyncio
import secrets
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
import pytest_asyncio
from ollama_live import live_with

from dotagent.agent.loop import RunOpening
from dotagent.agent.manager import BACKGROUND_SOURCE, RunHandle, RunManager
from dotagent.config import load_config
from dotagent.core.errors import InvalidValueError, RunConflict
from dotagent.core.paths import workspace_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import NO_PICKS, EventRecord, RunMode, RunRecord, ThreadRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolResult
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.office import read_thread

pytestmark = pytest.mark.asyncio(loop_scope="module")

OLLAMA_ROOT: str = "http://localhost:11434"
MODEL: str = "gpt-oss:20b-cloud"
MODEL_REF: str = f"ollama:{MODEL}"
_SCRIPTED_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
RUN_TIMEOUT_S: float = 240.0
CANCEL_TIMEOUT_S: float = 60.0
GONE_TIMEOUT_S: float = 15.0
DELEGATE_SOURCE: str = "delegate"
FENCE_OPENING: str = "<delegate_content"


def model_is_listed() -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(model["name"] == MODEL for model in answer.json()["models"])


# Live (item 632): the cloud model answers from off this Mac.
requires_model = live_with(model_is_listed(), f"needs the local Ollama server with {MODEL}")


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("home")
    (path / "config.toml").write_text(
        f'[models]\nmain = "{MODEL_REF}"\n\n[agent]\ngoal_mode = false\nmax_concurrent_runs = 1', encoding="utf-8"
    )
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


async def _stored_run(runtime: Runtime, mode: RunMode, parent_run_id: str | None, source: str) -> RunRecord:
    """A run that only exists in the store: enough for the checks that happen before any model is involved."""
    thread: ThreadRecord = await runtime.store.create_thread("delegation test", source)
    return await runtime.store.create_run(
        thread.id,
        "goal",
        mode,
        MODEL_REF,
        parent_run_id,
        source,
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        None,
        None,
    )


def _secret_file() -> tuple[Path, str]:
    token: str = f"MARKER-{secrets.token_hex(6)}"
    path: Path = workspace_dir() / f"delegate-{secrets.token_hex(3)}.txt"
    path.write_text(token, encoding="utf-8")
    return path, token


def _delegating_goal(path: Path) -> str:
    return (
        f"Call the delegate tool exactly once. Its goal must tell the sub-agent to read the file {path} "
        "and report the exact text in it. When it has answered, call finish with the text it reported."
    )


async def _children_of(runtime: Runtime, parent_id: str) -> list[RunRecord]:
    return [run for run in await runtime.store.list_runs(100, None) if run.parent_run_id == parent_id]


async def _delegate_result(runtime: Runtime, run_id: str) -> str:
    """The text the delegating run was shown for its delegate call."""
    events: list[EventRecord] = await runtime.store.list_events(run_id, 0, 500)
    texts: list[str] = [
        str(event.data["text"]) for event in events if event.kind == "tool_result" and event.data["tool"] == "delegate"
    ]
    assert len(texts) == 1, events
    return texts[0]


async def _count_processes(marker: str) -> int:
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        "pgrep", "-f", marker, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    out, _ = await process.communicate()
    return len(out.split())


async def _assert_gone(marker: str) -> None:
    deadline: float = time.monotonic() + GONE_TIMEOUT_S
    while await _count_processes(marker) > 0:
        assert time.monotonic() < deadline, f"a process with {marker} is still running"
        await asyncio.sleep(0.25)


async def _until_running(marker: str) -> None:
    deadline: float = time.monotonic() + CANCEL_TIMEOUT_S
    while await _count_processes(marker) == 0:
        assert time.monotonic() < deadline, f"no process with {marker} ever started"
        await asyncio.sleep(0.25)


async def _until_a_run_is_active(manager: RunManager) -> str:
    deadline: float = time.monotonic() + CANCEL_TIMEOUT_S
    while not (active := manager.active_run_ids()):
        assert time.monotonic() < deadline, "the delegated run never started"
        await asyncio.sleep(0.1)
    return active[0]


# ------------------------------------------------------------------------------------------ the rules


async def test_a_delegated_run_cannot_delegate_again(runtime: Runtime, manager: RunManager) -> None:
    parent: RunRecord = await _stored_run(runtime, "task", None, "test")
    child: RunRecord = await _stored_run(runtime, "task", parent.id, DELEGATE_SOURCE)
    with pytest.raises(RunConflict) as refused:
        await manager.delegate(child.id, "anything", "task", None, None)
    assert "cannot delegate further" in refused.value.message
    assert manager.active_run_ids() == []


async def test_a_run_that_has_ended_delegates_nothing(runtime: Runtime, manager: RunManager) -> None:
    """Item 941 (the review of 2026-11-02): a helper takes its parent's taint and guards from the parent's gate; one
    whose parent ended before it was registered (an offer of help raced with the end) would take none, so none
    starts."""
    parent: RunRecord = await _stored_run(runtime, "task", None, "test")
    await runtime.store.finish_run(parent.id, "done", "Done.")
    with pytest.raises(RunConflict) as refused:
        await manager.delegate(parent.id, "anything", "task", None, None)
    assert "has ended" in refused.value.message
    assert manager.active_run_ids() == []
    assert await _children_of(runtime, parent.id) == []


async def test_an_agents_work_goes_on_in_its_one_chat_unless_that_chat_is_at_work(
    runtime: Runtime, manager: RunManager
) -> None:
    """Item 952: the work sent to an agent goes to its own chat, named after it, which the next work continues; while
    a run works there, the next work takes a new chat, so that two pieces sent at once still run side by side."""
    agent = await runtime.store.create_agent(
        "Desk", "Answers questions.", "", "", None, None, ("read_file",), None, None
    )
    sender: RunRecord = await _stored_run(runtime, "task", None, "ui")
    first: str = await manager._agent_chat(agent)
    assert (await runtime.store.get_thread(first)).title == "Desk"
    run: RunRecord = await runtime.store.create_run(
        first,
        "goal",
        "task",
        MODEL_REF,
        sender.id,
        "team",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        agent.id,
        None,
    )
    # At work there: the next piece goes to a chat of its own.
    second: str = await manager._agent_chat(agent)
    assert second != first and (await runtime.store.get_thread(second)).title == "Desk"
    # Once it is done, the work continues in the chat the agent last worked in.
    await runtime.store.finish_run(run.id, "done", "Done.")
    assert await manager._agent_chat(agent) == first
    # A chat made while the first was at work is taken again once it is free, not a third one made.
    newer: RunRecord = await runtime.store.create_run(
        second,
        "goal",
        "task",
        MODEL_REF,
        sender.id,
        "team",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        agent.id,
        None,
    )
    await runtime.store.finish_run(newer.id, "done", "Done.")
    busy: RunRecord = await runtime.store.create_run(
        second,
        "goal",
        "task",
        MODEL_REF,
        sender.id,
        "team",
        "medium",
        "standard",
        None,
        "ask",
        False,
        None,
        agent.id,
        None,
    )
    assert await manager._agent_chat(agent) == first
    await runtime.store.finish_run(busy.id, "done", "Done.")


async def test_work_that_goes_on_in_a_chat_starts_as_tainted_as_its_last_run_ended(
    runtime: Runtime, manager: RunManager
) -> None:
    """Item 952 (the pre-push review): an agent's chat holds the words its earlier work read; the next piece, sent by a
    run that read nothing from outside, still starts tainted when the run it goes on from ended so."""
    earlier: RunRecord = await _stored_run(runtime, "task", None, "team")
    await runtime.store.add_event(earlier.id, "tool_result", {"tool": "web_fetch", "ok": True, "tainted": True})
    await runtime.store.finish_run(earlier.id, "done", "Done.")
    clean = SimpleNamespace(gate=SimpleNamespace(tainted=False))
    goes_on = RunOpening(text="Go on.", files=(), resumed=None, once=None, carried=(earlier.id,), picks=NO_PICKS)
    fresh = RunOpening(text="Start.", files=(), resumed=None, once=None, carried=(), picks=NO_PICKS)
    # A stand-in for the sender's handle, of which only its gate's mark is read: a sender that read nothing.
    sender = cast(RunHandle, clean)
    assert await manager._starts_tainted(sender, earlier.thread_id, "team", goes_on)
    assert not await manager._starts_tainted(sender, earlier.thread_id, "team", fresh)


async def test_an_agents_chat_takes_one_run_at_a_time_and_is_not_rewound_past_its_work(runtime: Runtime) -> None:
    """Item 952 (the pre-push review): an agent's work goes on in its one chat, so the store keeps that chat to one run
    at work whichever process starts it, and refuses to rewind it to before work an agent did there: that work's runs
    keep no place in its history, and a rewind would cut their words and leave them counted."""
    store = runtime.store
    chat: ThreadRecord = await store.create_thread("Desk", "team")
    parent: RunRecord = await _stored_run(runtime, "task", None, "ui")
    asked: RunRecord = await store.create_run(
        chat.id, "Tell Desk", "task", MODEL_REF, None, "ui", "medium", "standard", None, "ask", False, None, None, "a"
    )
    await store.finish_run(asked.id, "done", "Done.")
    work: RunRecord = await store.create_run(
        chat.id, "Work", "task", MODEL_REF, parent.id, "team", "medium", "standard", None, "ask", False, None, None, "a"
    )
    # Another process's piece of work for the same agent is refused while that one works.
    with pytest.raises(RunConflict) as refused:
        await store.create_run(
            chat.id, "More", "task", MODEL_REF, parent.id, "team", "medium", "standard", None, "ask", False, None,
            None, "b",
        )  # fmt: skip
    assert refused.value.context["run_ids"] == [work.id]
    await store.finish_run(work.id, "done", "Done.")
    with pytest.raises(InvalidValueError, match="an agent worked in this chat after this message"):
        await store.rewind_chat(asked.id, [asked.id])


async def test_an_agents_chat_keeps_the_mark_of_work_deleted_with_the_chat_that_sent_it(
    runtime: Runtime, manager: RunManager
) -> None:
    """Item 952 (the pre-push review of item 956): the work an agent did for a chat stays in the agent's one chat when
    that chat is deleted, though its runs go; when such a run read a page, the agent's chat keeps the mark, so that its
    next work starts marked."""
    store = runtime.store
    sender: RunRecord = await _stored_run(runtime, "task", None, "ui")
    chat: ThreadRecord = await store.create_thread("Desk", "team")
    work: RunRecord = await store.create_run(
        chat.id, "Work", "task", MODEL_REF, sender.id, "team", "medium", "standard", None, "ask", False, None, None, "a"
    )
    await store.add_event(work.id, "tool_result", {"tool": "web_fetch", "ok": True, "tainted": True})
    await store.finish_run(work.id, "done", "Done.")
    other: RunRecord = await _stored_run(runtime, "task", None, "ui")
    kept: RunRecord = await store.create_run(
        chat.id, "More", "task", MODEL_REF, other.id, "team", "medium", "standard", None, "ask", False, None, None, "a"
    )
    await store.finish_run(kept.id, "done", "Done.")
    assert not await store.thread_tainted(chat.id)
    await store.finish_run(sender.id, "done", "Done.")
    await store.delete_threads([sender.thread_id])
    assert await store.thread_tainted(chat.id)
    clean = cast(RunHandle, SimpleNamespace(gate=SimpleNamespace(tainted=False)))
    goes_on = RunOpening(text="Go on.", files=(), resumed=None, once=None, carried=(kept.id,), picks=NO_PICKS)
    assert await manager._starts_tainted(clean, chat.id, "team", goes_on)
    finish = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Read.", "success": True}),)
    )
    await runtime.router.register(_SCRIPTED_PROVIDER, ScriptedProvider(_SCRIPTED_PROVIDER, [finish, finish]))
    # A run that reads the chat's earlier runs, as a kept step's answer and a reaction's follow-up do, though the
    # owner's chat source starts it, is no message of the owner's own: it starts marked and clears nothing (the
    # pre-push review of item 955).
    follows = RunOpening(text="Done it.", files=(), resumed=None, once=None, carried=(kept.id,), picks=NO_PICKS)
    assert await manager._starts_tainted(None, chat.id, UI_SOURCE, follows)
    # So does the notice of a command in the background that ended there, though the chat's last run ended clean.
    notice = RunOpening(text="It ended.", files=(), resumed=None, once=None, carried=(), picks=NO_PICKS)
    assert await manager._starts_tainted(None, chat.id, BACKGROUND_SOURCE, notice)
    followed, _ = await manager._launch(
        "Done it.", "task", SCRIPTED_MODEL, chat.id, UI_SOURCE, None, None, "standard", "yolo", False, follows, None,
        None,
    )  # fmt: skip
    await asyncio.wait_for(manager.wait(followed.id), RUN_TIMEOUT_S)
    assert await store.thread_tainted(chat.id)
    # The owner writes in the agent's chat: what is marked there is theirs to have read now, and the next work starts
    # clean.
    owners: RunRecord = await manager.start(
        "I read it.", "task", SCRIPTED_MODEL, chat.id, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )
    assert not await store.thread_tainted(chat.id)
    # What the deleted run read is still in the chat's history: a lesson is not saved from it (the pre-push review of
    # item 955, :mod:`dotagent.tools.skill_save`).
    assert await store.chat_ever_tainted(chat.id)
    await asyncio.wait_for(manager.wait(owners.id), RUN_TIMEOUT_S)
    next_work = RunOpening(text="Next.", files=(), resumed=None, once=None, carried=(owners.id,), picks=NO_PICKS)
    assert not await manager._starts_tainted(clean, chat.id, "team", next_work)
    # A reader of the chat still reads the deleted run's words: it is marked whatever the owner wrote there since.
    read: ToolResult = await read_thread(store, await store.get_run(owners.id), "Desk", 40)
    assert read.untrusted


async def test_a_research_run_only_delegates_research(runtime: Runtime, manager: RunManager) -> None:
    parent: RunRecord = await _stored_run(runtime, "research", None, "test")
    with pytest.raises(RunConflict) as refused:
        await manager.delegate(parent.id, "anything", "task", None, None)
    assert "only delegate research" in refused.value.message
    assert manager.active_run_ids() == []
    assert await _children_of(runtime, parent.id) == []


async def test_the_delegate_tool_is_known_to_every_mode_and_classified_as_safe(runtime: Runtime) -> None:
    tool: RegisteredTool = runtime.registry.tools["delegate"]
    assert tool.modes == ALL_MODES
    assert tool.parallel_safe


# ------------------------------------------------------------------------------------------ with a model


@requires_model
@pytest.mark.live
async def test_a_run_reads_a_file_through_a_delegated_run(runtime: Runtime, manager: RunManager) -> None:
    path, token = _secret_file()
    parent: RunRecord = await manager.start(
        _delegating_goal(path), "task", None, None, "test", None, None, "standard", None, None, False, ()
    )
    finished: RunRecord = await asyncio.wait_for(manager.wait(parent.id), RUN_TIMEOUT_S)
    children: list[RunRecord] = await _children_of(runtime, parent.id)
    assert finished.status == "done", finished
    assert token in finished.result
    assert len(children) == 1, children
    child: RunRecord = children[0]
    assert (child.status, child.source, child.mode, child.model) == ("done", DELEGATE_SOURCE, "task", MODEL_REF)
    assert child.thread_id != parent.thread_id
    assert token in child.result
    shown: str = await _delegate_result(runtime, parent.id)
    assert token in shown
    assert FENCE_OPENING not in shown
    assert manager.active_run_ids() == []


@requires_model
@pytest.mark.live
@pytest.mark.internet
async def test_a_delegated_run_starts_as_untrusted_as_its_parent(runtime: Runtime, manager: RunManager) -> None:
    path, token = _secret_file()
    goal: str = (
        "Do these in order. 1. Fetch https://www.iana.org/help/example-domains with web_fetch. 2. "
        f"{_delegating_goal(path)}"
    )
    parent: RunRecord = await manager.start(
        goal, "task", None, None, "test", None, None, "standard", None, None, False, ()
    )
    finished: RunRecord = await asyncio.wait_for(manager.wait(parent.id), RUN_TIMEOUT_S)
    assert finished.status == "done", finished
    assert token in finished.result
    shown: str = await _delegate_result(runtime, parent.id)
    assert FENCE_OPENING in shown
    assert token in shown


@requires_model
@pytest.mark.live
async def test_stopping_the_waiting_caller_stops_the_delegated_run(runtime: Runtime, manager: RunManager) -> None:
    marker: str = f"sleep 47.{secrets.randbelow(900) + 100}"
    parent: RunRecord = await _stored_run(runtime, "task", None, "test")
    goal: str = f"Run the shell command `{marker}`, then call finish."
    waiting: asyncio.Task[object] = asyncio.create_task(manager.delegate(parent.id, goal, "task", None, None))
    child_id: str = await _until_a_run_is_active(manager)
    await _until_running(marker)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    stopped: RunRecord = await asyncio.wait_for(manager.wait(child_id), CANCEL_TIMEOUT_S)
    assert stopped.status == "cancelled", stopped
    assert stopped.parent_run_id == parent.id
    assert manager.active_run_ids() == []
    await _assert_gone(marker)
