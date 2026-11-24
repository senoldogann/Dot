"""A run on the owner's Claude plan (item 1013), end to end with a stand-in ``claude`` (``tests/fixtures/
claude_standin.py``) on a real runtime, manager, bridge and gate: the program is started with the flags that keep the
model to Dot's tools, an environment without Dot's variables and a 0600 MCP config file in a private folder, removed
once the run ends; it reaches the chat's door over HTTP with that file, and each call it makes goes through the run's
gate as a call of Dot's own loop (a write into the workspace's ``.claude`` folder refused, one into the workspace
done); the run ends done with the program's answer and keeps its session for the chat. A program whose model has a tool
that is not Dot's, and a plan whose window is used up, end the run failed with why. No model is called."""

import asyncio
import contextlib
import dataclasses
import json
import os
import re
import stat
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.claude_code import cut as cut_module
from dotagent.claude_code import run as run_module
from dotagent.claude_code.cut import EndedTogether
from dotagent.claude_code.run import chat_path
from dotagent.config import load_config
from dotagent.core.errors import FailSafeTriggered, InvalidValueError
from dotagent.core.paths import claude_code_dir
from dotagent.core.types import JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.catalog import all_tasks
from dotagent.evals.results import Cut
from dotagent.evals.runner import _supervise, ended_by_plan_limit
from dotagent.evals.types import EvalTask
from dotagent.memory.service import SavedMemory, save_memory, update_memory_note, words_only
from dotagent.opencode.bridge import Door, ToolBridge
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.notices import said_nothing_new
from dotagent.scheduler.plans import UNSET_OPTIONS, ScheduleRequest, create_schedule
from dotagent.scheduler.runner import Scheduler
from dotagent.store.endings import PREFIX_EMERGENCY_STOP
from dotagent.store.models import AgentRecord, EventRecord, RunPicks, RunRecord, ScheduleRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, RegisteredTool, ToolResult
from dotagent.tools.context import ToolContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

STAND_IN: Path = Path(__file__).resolve().parents[1] / "fixtures" / "claude_standin.py"
MODEL: str = "claude_code:sonnet"


@dataclass(frozen=True)
class Room:
    runtime: Runtime
    manager: RunManager
    record: Path


def _lay_out(root: Path) -> tuple[Path, Path, Path, Path]:
    """Dot's home with its config, the folder the stand-in writes to, the ``claude`` Dot starts (the stand-in, told
    where to write what it was started with) and Claude Code's state folder, whose state file says which account is
    signed in (no secret in it)."""
    home: Path = root / "home"
    home.mkdir()
    # No embedding service, as on CI: no test reaches one, and a run's notes are recalled by their words.
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\ngoal_mode = false\n\n[memory]\nembed_enabled = false", encoding="utf-8"
    )
    record: Path = root / "record"
    record.mkdir()
    program: Path = root / "bin" / "claude"
    program.parent.mkdir()
    program.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STAND_IN}" "{record}" "$@"\n', encoding="utf-8")
    program.chmod(0o755)
    state: Path = root / "claude-state"
    state.mkdir()
    (state / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": "owner@example.org", "organizationName": "Owner"}}),
        encoding="utf-8",
    )
    return home, record, program, state


def _json(path: Path) -> dict[str, object]:
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def room(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Room]:
    root: Path = Path(await asyncio.to_thread(os.path.realpath, tmp_path_factory.mktemp("claude-code")))
    home, record, program, state = await asyncio.to_thread(_lay_out, root)
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(root / "logs"))
    patch.setenv("DOT_CHROME_DIR", str(root / "chrome"))
    patch.setenv("DOT_CLAUDE", str(program))
    patch.setenv("CLAUDE_CONFIG_DIR", str(state))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(built)
    try:
        yield Room(runtime=built, manager=manager, record=record)
    finally:
        await manager.shutdown()
        await close_runtime(built)
        patch.undo()


async def _run(room: Room, goal: str) -> RunRecord:
    started: RunRecord = await room.manager.start(
        goal, "task", MODEL, None, "test", None, None, "standard", None, "auto", False, ()
    )
    return await room.manager.wait(started.id)


async def test_a_run_goes_whole_to_claude_code_and_its_calls_go_through_the_gate(room: Room) -> None:
    ended: RunRecord = await _run(room, "Write a note in the workspace.")
    assert ended.status == "done", ended.result
    workspace: Path = room.runtime.workspace
    # The call into the workspace's .claude folder was refused by the gate; the one into the workspace went through.
    assert ".claude: refused: Not allowed: auto-review refused this action (secret_access)" in ended.result
    assert "notes: written" in ended.result
    assert not await asyncio.to_thread((workspace / ".claude" / "settings.json").exists)
    assert await asyncio.to_thread((workspace / "notes.txt").read_text, encoding="utf-8") == "from claude"
    results: list[EventRecord] = await room.runtime.store.events_of_kind(ended.id, "tool_result", 10)
    assert [(event.data["tool"], event.data["ok"]) for event in results] == [
        ("write_file", False),
        ("write_file", True),
    ]
    assert all(event.data["decision"] is not None or not event.data["ok"] for event in results)
    steps: list[EventRecord] = await room.runtime.store.events_of_kind(ended.id, "assistant", 10)
    assert [event.data["tool_calls"] for event in steps] == [["write_file"], []]
    assert steps[0].data["input_tokens"] == 120 and steps[0].data["cached_tokens"] == 20

    # The plan's models were asked for first (item 1367): with the telemetry the list comes over, nothing else of the
    # program's own traffic.
    listed: dict[str, object] = await asyncio.to_thread(_json, room.record / "listed.json")
    assert listed["env"] == {"DISABLE_AUTOUPDATER": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_BUG_COMMAND": "1"}
    assert "--input-format" in listed["args"] and "--mcp-config" not in listed["args"]  # type: ignore[operator]  # JSON
    started: dict[str, object] = await asyncio.to_thread(_json, room.record / "started.json")
    args: list[str] = started["args"]  # type: ignore[assignment]  # what the stand-in wrote
    for flag, value in (
        ("--input-format", "stream-json"),
        ("--output-format", "stream-json"),
        ("--model", "sonnet"),
        ("--tools", "ToolSearch"),
        ("--setting-sources", ""),
        ("--permission-mode", "dontAsk"),
        ("--permission-prompts", "none"),
    ):
        assert args[args.index(flag) + 1] == value
    for flag in (
        "--include-partial-messages",
        "--replay-user-messages",
        "--verbose",
        "--strict-mcp-config",
        "--disable-slash-commands",
    ):
        assert flag in args
    door: str = args[args.index("--allowedTools") + 1]
    # One door's name for every chat (item 1052), so that the tools head every prompt the same and its cache is read.
    assert door == "mcp__dot,ToolSearch" and "--resume" not in args
    assert "--dangerously-skip-permissions" not in args
    env: list[str] = started["env"]  # type: ignore[assignment]
    assert not [name for name in env if name.startswith(("DOT_", "GIT_")) or name == "ANTHROPIC_API_KEY"]
    assert started["env_values"] == {
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "HOME": os.environ["HOME"],
    }
    assert started["cwd"] == str(workspace)
    assert started["config_mode"] == stat.S_IRUSR | stat.S_IWUSR and started["folder_mode"] == stat.S_IRWXU
    # The config file and its folder are gone with the run, and the secret was never an argument.
    config_file: Path = Path(str(started["config_file"]))
    assert config_file.is_relative_to(claude_code_dir()) and not await asyncio.to_thread(config_file.parent.exists)
    assert not any("Bearer" in arg for arg in args)
    # The chat keeps its session, which its next run resumes.
    kept: dict[str, object] = await asyncio.to_thread(_json, chat_path(ended.thread_id))
    assert kept["session_id"] == "ses-standin-1"


async def test_a_finish_that_did_not_succeed_ends_the_run_failed_with_what_it_needs(room: Room) -> None:
    """Item 1076: a run on the plan ended done whatever it said, with nothing under what it needs, so the page never
    said "Waiting on you" and a schedule's failure was never told. Its finish now ends it as Dot's own loop's does."""
    started: RunRecord = await room.manager.start(
        "Pay the bill; it needs a code.", "task", MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(30):
        ended: RunRecord = await room.manager.wait(started.id)
    assert ended.status == "failed" and ended.result == "The bank asks for a code.", ended.result
    (finished,) = await room.runtime.store.events_of_kind(ended.id, "run_finished", 5)
    assert finished.data["needs"] == "The code the bank sent to your phone."
    # The chat keeps the finish's answer, not the empty line the program wrote after it, for the chat's next run.
    replies: list[str] = [
        m.content for m in await room.runtime.store.load_messages(ended.thread_id, 0) if m.role == "assistant"
    ]
    assert replies[-1] == "The bank asks for a code.", replies


async def test_a_hold_keeps_the_run_waiting_for_the_owners_next_message(room: Room) -> None:
    """Item 1076: a ✋ was sent to the program as words, and the run ended with the turn. Now it holds the run, as in
    Dot's own loop: the run stays at work after the turn, waiting, until the owner writes, and the pages are told."""
    started: RunRecord = await room.manager.start(
        "Please talk with me.", "task", MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    await asyncio.sleep(0.3)
    room.manager.tell(started.id, "The owner reacted ✋: hold on.", "hold")
    await asyncio.sleep(3.0)
    assert started.id in room.manager.active_run_ids() and room.manager.holding(started.id)
    held = await room.runtime.store.events_of_kind(started.id, "reaction_hold", 5)
    assert len(held) == 1
    room.manager.steer(started.id, "Go on now.", ())
    async with asyncio.timeout(30):
        ended: RunRecord = await room.manager.wait(started.id)
    assert ended.status == "done", ended.result
    (let_go,) = await room.runtime.store.events_of_kind(started.id, "reaction_hold_ended", 5)
    assert let_go.data["reason"] == "message"


async def test_the_emergency_stop_a_tool_meets_ends_the_run_as_one(room: Room) -> None:
    """Item 1076: a call of Dot's tool runs in the bridge's task, so the emergency stop it met (the pointer in a corner
    of the screen) went to Claude Code as a failed call and the run went on. Now it ends the run as the emergency
    stop."""
    registry = room.runtime.registry
    shell: RegisteredTool = registry.tools["shell"]

    async def stopped(arguments: JsonObject, ctx: ToolContext) -> PreparedCall:
        prepared: PreparedCall = await shell.prepare(arguments, ctx)

        async def corner(_: ToolContext) -> ToolResult:
            raise FailSafeTriggered("the pointer is in a corner of the screen", {})

        return dataclasses.replace(prepared, run=corner)

    registry.tools["shell"] = dataclasses.replace(shell, prepare=stopped)
    try:
        started: RunRecord = await room.manager.start(
            "Run the slow command.", "task", MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(30):
            ended: RunRecord = await room.manager.wait(started.id)
    finally:
        registry.tools["shell"] = shell
    assert ended.status == "cancelled" and ended.result.startswith(PREFIX_EMERGENCY_STOP), ended.result


async def _replies(room: Room, run_id: str) -> int:
    """How many steps of the model the run has stored: a reply after a finish shows no words (item 1125)."""
    steps: list[EventRecord] = await room.runtime.store.events_of_kind(run_id, "assistant", 50)
    return len(steps)


async def test_a_finish_then_a_message_ends_as_the_reply_after_it(room: Room) -> None:
    """Item 1076's pre-push review: a finish before the owner's next message does not decide the run's end once the
    model went on after it. The model finishes needing a code, the owner holds the run and writes the code, and the
    model answers plainly: the run ends done with that answer, not failed with the old one."""
    started: RunRecord = await room.manager.start(
        "Pay it: finish then talk.", "task", MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    await asyncio.sleep(0.3)
    room.manager.tell(started.id, "The owner reacted ✋: hold on.", "hold")
    # Held once its turn, the finish among it, has ended.
    deadline: float = time.monotonic() + 30
    while not (room.manager.holding(started.id) and await _replies(room, started.id) >= 2):
        assert time.monotonic() < deadline, "the run never came to rest held"
        await asyncio.sleep(0.1)
    room.manager.steer(started.id, "Here is the code: 8087.", ())
    async with asyncio.timeout(30):
        ended: RunRecord = await room.manager.wait(started.id)
    assert ended.status == "done" and ended.result == "Heard you again.", ended.result


async def test_a_call_after_a_finish_drops_what_the_finish_said(room: Room) -> None:
    """Item 1076's pre-push review: Dot's own runs end at a finish, but the program's turn can go on after it. The
    model finishes saying it paid, then checks and writes that the payment was declined: the run ends with that, not
    with the finish's answer."""
    started: RunRecord = await room.manager.start(
        "Pay it: finish then check.", "task", MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(30):
        ended: RunRecord = await room.manager.wait(started.id)
    assert ended.result == "The payment was declined after all.", ended.result
    # Nor does the finish's "nothing new" keep the end from the phone.
    assert not await said_nothing_new(room.runtime.store, ended.id)


async def test_a_reply_after_a_finish_is_not_shown_as_a_second_answer(room: Room) -> None:
    """Item 1125: the program's turn ends, after the finish it called, with a reply that says the answer again: the
    run's answer is the finish's, and the step of that reply shows no text of its own (the chat showed both)."""
    started: RunRecord = await room.manager.start(
        "How are you: finish then restate.",
        "task",
        MODEL,
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
        ended: RunRecord = await room.manager.wait(started.id)
    assert ended.result == "I am well, thanks.", ended.result
    steps = await room.runtime.store.events_of_kind(ended.id, "assistant", 10)
    assert [step.data["text"] for step in steps] == ["", ""], [step.data for step in steps]


async def test_a_stop_ends_the_call_of_dots_tool_at_work_for_the_program(room: Room) -> None:
    """Item 1076: a call of Dot's tool runs in the bridge's task, not the run's; a Stop that ends the program cuts it
    all the same (its request is cancelled as the program goes), which this keeps: stopped while its command sleeps,
    the command never writes."""
    started: RunRecord = await room.manager.start(
        "Run the slow command.", "task", MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
    )
    deadline: float = time.monotonic() + 30
    while not await room.runtime.store.events_of_kind(started.id, "tool_call", 5):
        assert time.monotonic() < deadline, "the program never called its command"
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)
    room.manager.cancel(started.id)
    async with asyncio.timeout(30):
        ended: RunRecord = await room.manager.wait(started.id)
    assert ended.status == "cancelled", ended.result
    # The command slept 2 s before it would write: well past that, nothing was written.
    await asyncio.sleep(3)
    assert not await asyncio.to_thread((room.runtime.workspace / "late.txt").exists)


async def test_a_model_given_a_tool_that_is_not_dots_ends_the_run_failed_at_once(room: Room) -> None:
    ended: RunRecord = await _run(room, "Use the extra tools.")
    assert ended.status == "failed"
    assert "more than Dot's tools" in ended.result and "Bash" in ended.result
    assert not await asyncio.to_thread(chat_path(ended.thread_id).exists)


async def test_a_used_up_plan_ends_the_run_failed_with_when_it_resets(room: Room) -> None:
    ended: RunRecord = await _run(room, "Hit the limit.")
    assert ended.status == "failed"
    assert "usage limit is reached (five hour window)" in ended.result and "resets at 2030-" in ended.result


async def test_a_chat_whose_run_did_not_end_clean_starts_a_new_session(room: Room) -> None:
    """The pre-push review of item 1013: a run that took the chat's messages into its session and then failed (here the
    plan's window, as an owner's stop or the run's limits would) forgets the session, so that the next run does not
    resume one whose count of what it saw is behind and hear the failed goal again as a new ask."""
    first: RunRecord = await _run(room, "Write a note in the workspace.")
    assert first.status == "done", first.result
    assert await asyncio.to_thread(chat_path(first.thread_id).exists)
    started: RunRecord = await room.manager.start(
        "Hit the limit.", "task", MODEL, first.thread_id, "test", None, None, "standard", None, "auto", False, ()
    )
    ended: RunRecord = await room.manager.wait(started.id)
    assert ended.status == "failed"
    assert not await asyncio.to_thread(chat_path(first.thread_id).exists)


_SKILL: str = "weekly-report"
_SKILL_STEPS: str = "MARKER-weekly-report-steps: one line a project, the late ones first."


def _write_skill(home: Path) -> None:
    folder: Path = home / "skills" / _SKILL
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f'---\nname: {_SKILL}\ndescription: "Write the weekly report as the owner likes it."\n---\n'
        f"# The weekly report\n\n{_SKILL_STEPS}\n",
        encoding="utf-8",
    )


async def test_a_picked_skill_reaches_claude_code_in_the_first_message(room: Room) -> None:
    """Item 1022: a message sent with skills picked on a model of the owner's Claude plan was refused before it ran;
    the skill's instructions now come in the program's first message, as Dot's own loop gives them."""
    await asyncio.to_thread(_write_skill, Path(os.environ["DOT_HOME"]))
    picks: RunPicks = RunPicks(plugins=(), skills=(_SKILL,), plan_first=False)
    started: RunRecord = await room.manager.start_picked(
        "Write a note in the workspace.",
        "task",
        MODEL,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        "auto",
        False,
        (),
        picks,
    )
    ended: RunRecord = await room.manager.wait(started.id)
    assert ended.status == "done", ended.result
    assert ended.goal == "Write a note in the workspace."
    first: str = await asyncio.to_thread((room.record / "first.json").read_text, encoding="utf-8")
    assert _SKILL_STEPS in first and "The owner picked the skills below for this message." in first
    chips: list[EventRecord] = await room.runtime.store.events_of_kind(ended.id, "picks", 10)
    assert [event.data["skills"] for event in chips] == [[_SKILL]]


async def test_plan_first_on_claude_code_is_refused_before_the_run(room: Room) -> None:
    """Plan first is Dot's own loop's plan and ask, which Claude Code does not run: refused, with what to do instead."""
    with pytest.raises(InvalidValueError, match="but not Plan first"):
        await room.manager.start_picked(
            "Write a note in the workspace.",
            "task",
            MODEL,
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "auto",
            False,
            (),
            RunPicks(plugins=(), skills=(), plan_first=True),
        )


async def test_a_run_on_the_plan_is_handed_what_dot_remembers_of_its_goal(room: Room) -> None:
    """Item 1055: a run on the plan said it did not know what Dot's memory held. Its first message carries the notes
    recalled for its goal, as Dot's own loop hands them, and they count as used by the run."""
    # Saved by its words, as with embeddings off: no test reaches an embedding service, and the run's recall falls back
    # to the words when the service cannot be reached, as it does in the owner's Dot.
    saved: SavedMemory = await save_memory(
        words_only(room.runtime.memory),
        "The owner's Finnish level is B1, from the YKI test of 2026.",
        ["language"],
        None,
        None,
        None,
        False,
    )
    ended: RunRecord = await _run(room, "What is my Finnish level?")
    assert ended.status == "done", ended.result
    first: str = await asyncio.to_thread((room.record / "first.json").read_text, encoding="utf-8")
    assert "Finnish level is B1" in first and saved.record.id in first
    handed: list[EventRecord] = await room.runtime.store.events_of_kind(ended.id, "memories", 10)
    assert [event.data["ids"] for event in handed] == [[saved.record.id]]
    # The chat's next message goes on in the session, which heard the note: it is not handed again.
    started: RunRecord = await room.manager.start(
        "And my level in Swedish?",
        "task",
        MODEL,
        ended.thread_id,
        "test",
        None,
        None,
        "standard",
        None,
        "auto",
        False,
        (),
    )
    again: RunRecord = await room.manager.wait(started.id)
    assert again.status == "done", again.result
    # It counts as used, as in Dot's own loop, though its text is not sent again.
    used: list[EventRecord] = await room.runtime.store.events_of_kind(again.id, "memories", 10)
    assert [(event.data["ids"], event.data["fresh"]) for event in used] == [([saved.record.id], 0)]
    second: str = await asyncio.to_thread((room.record / "first.json").read_text, encoding="utf-8")
    assert saved.record.id not in second
    # A note changed since the session heard it comes again with its new text (the pre-push review of item 1055).
    await update_memory_note(
        words_only(room.runtime.memory),
        saved.record.id,
        "The owner's Finnish level is B2, from the YKI test of 2026.",
        ["language"],
    )
    started = await room.manager.start(
        "And my Finnish level now?",
        "task",
        MODEL,
        ended.thread_id,
        "test",
        None,
        None,
        "standard",
        None,
        "auto",
        False,
        (),
    )
    third: RunRecord = await room.manager.wait(started.id)
    assert third.status == "done", third.result
    changed: str = await asyncio.to_thread((room.record / "first.json").read_text, encoding="utf-8")
    assert "Finnish level is B2" in changed


async def test_an_eval_cuts_a_run_on_the_plan_at_its_token_budget(room: Room) -> None:
    """Item 1220: a run on the plan records its tokens with each step's assistant event and no model call; the eval's
    watch read the spending only at a model call, so a plan run was never cut at its budget."""
    task: EvalTask = all_tasks()[0].model_copy(update={"token_budget": 50, "time_limit_s": 60.0})
    async with room.runtime.bus.subscribe(None) as subscription:
        started: RunRecord = await room.manager.start(
            "Write a note in the workspace.", "task", MODEL, None, EVAL_SOURCE, None, None, "standard", None, "auto",
            False, (),
        )  # fmt: skip
        cut: Cut = await _supervise(room.runtime, room.manager, subscription, started.id, task)
    ended: RunRecord = await room.manager.wait(started.id)
    assert cut == "token_budget"
    assert ended.status == "cancelled", ended.result


async def test_an_eval_tells_a_run_the_plans_window_refused_from_its_end(room: Room) -> None:
    """Item 1220: a refused window ends the run before any step tells it; the eval reads its end and does not score it
    as the model's failure."""
    ended: RunRecord = await _run(room, "Hit the limit.")
    events: list[EventRecord] = await room.runtime.store.list_events(ended.id, 0, 500)
    assert ended_by_plan_limit(events), ended.result


def _short_bridge(monkeypatch: pytest.MonkeyPatch, left_out: str) -> None:
    """Fake the bridge's list: the door Dot's bridge answers a program with has all its tools but ``left_out``. (The
    bridge's server holds its ``_list`` from its first start, so the door each request finds is what is replaced.)"""
    whole = ToolBridge._door_of

    def short(self: ToolBridge, request: object) -> Door | None:
        door: Door | None = whole(self, request)
        if door is None:
            return None
        return dataclasses.replace(door, specs=tuple(spec for spec in door.specs if spec.name != left_out))

    monkeypatch.setattr(ToolBridge, "_door_of", short)


_NOTIFY_GOAL: str = "Send me a notification that the course starts at 16:00."


async def _run_as(room: Room, goal: str, source: str, thread_id: str | None = None) -> RunRecord:
    """``goal`` as a run of ``source`` (``schedule`` for a firing, ``test`` for one the owner asked), waited for."""
    started: RunRecord = await room.manager.start(
        goal, "task", MODEL, thread_id, source, None, None, "standard", None, "auto", False, ()
    )
    async with asyncio.timeout(30):
        return await room.manager.wait(started.id)


async def test_a_firing_whose_bridge_lists_no_notify_ends_failed_at_once_when_its_goal_asks_for_a_notification(
    room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1400: a reminder ended done with "I couldn't send the notification: the Dot tools (including notify) are no
    longer available to me". The program is told what its bridge lists (here a list that lacks notify) and the run ends
    failed at once, before its model says a word, with the tool and the cause in its result; nothing was sent. The
    session is not kept, and the failure may pass: a schedule tries its task once more."""
    _short_bridge(monkeypatch, "notify")
    await asyncio.to_thread((room.record / "started.json").unlink, missing_ok=True)
    ended: RunRecord = await _run_as(room, _NOTIFY_GOAL, "schedule")
    assert ended.status == "failed", ended.result
    assert ended.result.startswith("Dot ended this task before it began: it needs Dot's notify tool, and Claude Code")
    assert "did not list it among the tools Dot's bridge gave it" in ended.result and "notify" not in ended.result[150:]
    # The program started and listed what the bridge gave it; no step of its model was taken.
    assert await asyncio.to_thread((room.record / "started.json").exists)
    assert await room.runtime.store.events_of_kind(ended.id, "assistant", 5) == []
    assert not await asyncio.to_thread(chat_path(ended.thread_id).exists)
    (finished,) = await room.runtime.store.events_of_kind(ended.id, "run_finished", 5)
    error: object = finished.data["error"]
    assert isinstance(error, dict) and error["kind"] == "passing" and error["context"]["why"] == "not_listed"


async def test_the_same_goal_runs_on_the_whole_list_and_the_check_is_no_rule_about_tool_calls(
    room: Room, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1400: the bridge's whole list lets a firing that asks for a notification run; on a list without notify a
    firing that only mentions one, a goal that is an answer and a run the owner watches (who reads its answer) run
    all the same, as a run that calls no tool at all would."""
    whole: RunRecord = await _run_as(room, f"{_NOTIFY_GOAL} Write a note in the workspace.", "schedule")
    assert whole.status == "done", whole.result
    _short_bridge(monkeypatch, "notify")
    for goal, source in (
        ("Review the push notification settings screen. Write a note in the workspace.", "schedule"),
        ("Write a note in the workspace.", "schedule"),
        (f"{_NOTIFY_GOAL} Write a note in the workspace.", "test"),
    ):
        ended: RunRecord = await _run_as(room, goal, source)
        assert ended.status == "done", (goal, source, ended.result)


async def test_a_schedule_whose_run_lacks_notify_says_failed_in_its_row_and_why_in_its_history(room: Room) -> None:
    """Item 1400: before, the row of the schedule said ``done``. Dot's own list for the run leaves notify out (here the
    registry has none), the run ends failed before the program starts, the row says failed and counts it, and the
    History's line for the firing is the reason, which no try again follows: it needs a change."""
    registry = room.runtime.registry
    notify: RegisteredTool = registry.tools.pop("notify")
    scheduler: Scheduler = Scheduler(room.runtime, room.manager)
    await asyncio.to_thread((room.record / "started.json").unlink, missing_ok=True)
    try:
        options = UNSET_OPTIONS.model_copy(update={"model": MODEL})
        request: ScheduleRequest = ScheduleRequest(
            name="Course reminder", goal=_NOTIFY_GOAL, cron="30 15 * * *", at=None, mode="task", options=options
        )
        record: ScheduleRecord = await create_schedule(
            room.runtime.store, room.runtime.config.scheduler, room.runtime.schedule_zone, request, time.time(), None
        )
        run: RunRecord = await scheduler.fire_by_hand(record.id, time.time())
        async with asyncio.timeout(30):
            ended: RunRecord = await room.manager.wait(run.id)
            await scheduler.drain()
    finally:
        registry.tools["notify"] = notify
        await scheduler.stop()
    assert ended.status == "failed", ended.result
    assert ended.result.startswith(
        "Dot ended this task before it began: it needs Dot's notify tool, and this run was not"
    )
    assert "The tools Dot gives a task run (its mode and Settings decide them) do not include it." in ended.result
    row: ScheduleRecord = await room.runtime.store.get_schedule(record.id)
    assert row.last_status == "failed" and row.runs_failed_in_row == 1
    # The program never started, and nothing says the run will be tried again.
    assert not await asyncio.to_thread((room.record / "started.json").exists)
    assert await room.runtime.store.events_of_kind(ended.id, "schedule_retry", 5) == []
    (finished,) = await room.runtime.store.events_of_kind(ended.id, "run_finished", 5)
    error: object = finished.data["error"]
    assert isinstance(error, dict) and error["kind"] == "lasting"


async def test_an_agent_whose_tools_leave_out_notify_is_told_so_before_its_program_starts(room: Room) -> None:
    """Item 1400: an agent's own list of tools keeps the core ones (finish, ask_user, ...) and what it names, and notify
    is not core, so a reminder in its chat had no way to notify. The result names the agent and what to change, the
    chat keeps its session (the program never touched it), and the agent's other work goes on: a firing that only
    mentions notifications, and a run the owner asked, which they read."""
    store = room.runtime.store
    tools: list[str] = ["read_file", "write_file"]
    ada: AgentRecord = await store.create_agent("Ada", "Reminders", "", MODEL, None, None, tools, None, None)
    chat: ThreadRecord = await store.create_thread("Ada's chat", "ui", agent_id=ada.id)
    first: RunRecord = await _run_as(room, "Write a note in the workspace.", "test", chat.id)
    assert first.status == "done", first.result
    assert await asyncio.to_thread(chat_path(chat.id).exists)
    await asyncio.to_thread((room.record / "started.json").unlink, missing_ok=True)
    ended: RunRecord = await _run_as(room, _NOTIFY_GOAL, "schedule", chat.id)
    assert ended.status == "failed", ended.result
    assert (
        "The tools of the agent Ada are a list of their own that leaves it out: add it to the agent's tools"
        in ended.result
    )
    assert not await asyncio.to_thread((room.record / "started.json").exists)
    assert await asyncio.to_thread(chat_path(chat.id).exists)
    for goal, source in (
        ("Review the push notification settings screen.", "schedule"),
        (_NOTIFY_GOAL, "test"),
    ):
        again: RunRecord = await _run_as(room, goal, source, chat.id)
        assert again.status == "done", (goal, source, again.result)


@pytest.fixture
def ends(monkeypatch: pytest.MonkeyPatch) -> Iterator[EndedTogether]:
    """The count of programs cut together (item 1580) as a test's own, with a window no test outlasts, so that the cuts
    of one test never join those of another in the line of the daemon's own."""
    together: EndedTogether = EndedTogether(window_s=60.0)
    monkeypatch.setattr(cut_module, "ENDED_TOGETHER", together)
    yield together
    together.flush()


async def _failed_context(room: Room, ended: RunRecord) -> dict[str, object]:
    """The context of the error that ended the run, as its ``run_finished`` event keeps it."""
    (finished,) = await room.runtime.store.events_of_kind(ended.id, "run_finished", 5)
    error: object = finished.data["error"]
    assert isinstance(error, dict) and error["kind"] == "passing", error
    context: object = error["context"]
    assert isinstance(context, dict)
    return context


@pytest.mark.parametrize(
    ("goal", "returncode", "named", "stderr_says"),
    [
        ("Please die by sigkill.", -9, "SIGKILL", ""),
        ("Please die by sigterm.", -15, "SIGTERM", ""),
        ("Please die with exit 1.", 1, None, "stand-in crashed: out of memory"),
    ],
)
async def test_a_program_that_ends_before_its_result_says_how_it_ended(
    room: Room,
    ends: EndedTogether,
    monkeypatch: pytest.MonkeyPatch,
    goal: str,
    returncode: int,
    named: str | None,
    stderr_says: str,
) -> None:
    """Item 1580: a program killed from outside ended its run with "Claude Code ended before it answered", its stderr
    empty and nothing else. The error's context and a log line now carry its exit code and signal, the end of its error
    stream, that it had said what the model has, the steps it took and the seconds since its last line. (Continuing the
    run is off here, item 1581's own tests have it on: the run ends with the cut, as it did.)"""
    await _continuing(room, monkeypatch, 0)
    with capture_logs() as logs:
        ended: RunRecord = await _run(room, goal)
    assert ended.status == "failed" and ended.result == "Claude Code ended before it answered", ended.result
    context: dict[str, object] = await _failed_context(room, ended)
    assert context["returncode"] == returncode and context["signal"] == named and context["exited"] is True
    assert context["inited"] is True and context["steps"] == 1
    assert re.fullmatch(r"ses-cut-[0-9a-f]{8}", str(context["claude_session"])), context["claude_session"]
    assert context["continued"] == 0 and "continue_cut = 0" in str(context["not_continued"])
    assert isinstance(context["stderr"], str) and stderr_says in context["stderr"]
    # The key the program printed in its crash is masked, as everything Dot stores or logs is.
    assert "sk-ant" not in context["stderr"] and ("***" in context["stderr"]) is (returncode == 1)
    assert isinstance(context["silent_s"], float) and 0 <= context["silent_s"] < 30
    assert isinstance(context["cut_at"], str) and len(context["cut_at"]) == len("10:44:04")
    cuts: list[dict[str, object]] = [line for line in logs if line["event"] == "claude_code_program_cut"]
    assert len(cuts) == 1 and cuts[0]["run_id"] == ended.id and cuts[0]["returncode"] == returncode
    assert cuts[0]["signal"] == named and cuts[0]["steps"] == 1 and "sk-ant" not in str(cuts[0]["stderr"])
    # The run's own line says the same: its context is in the log (item 1580), where the cause was missing before.
    failed: list[dict[str, object]] = [line for line in logs if line["event"] == "run failed"]
    assert failed and failed[0]["context"]["returncode"] == returncode  # type: ignore[index]  # a dict by the lines above


async def test_programs_cut_together_are_said_in_one_line(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1580: five programs ended within 20 ms of each other and nothing said so. Two runs whose programs are cut
    in the same window are one line, ``claude_code_programs_ended_together``, with their run ids and signals."""
    await _continuing(room, monkeypatch, 0)
    with capture_logs() as logs:
        first, second = await asyncio.gather(_run(room, "Please die by sigkill."), _run(room, "Please die by sigterm."))
        assert not [line for line in logs if line["event"] == "claude_code_programs_ended_together"]
        ends.flush()
    (line,) = [line for line in logs if line["event"] == "claude_code_programs_ended_together"]
    assert sorted(line["run_ids"]) == sorted([first.id, second.id])  # type: ignore[type-var]  # a list of ids
    assert sorted(line["signals"], key=str) == ["SIGKILL", "SIGTERM"]  # type: ignore[type-var]  # a list of names
    assert line["programs"] == 2


async def test_a_stop_that_ends_the_program_is_no_cut(room: Room, ends: EndedTogether) -> None:
    """Item 1580: Dot's own ends are marked and never counted: the program a Stop ends is no cut, and is no part of a
    mass cut however many runs are stopped together."""
    with capture_logs() as logs:
        started: RunRecord = await room.manager.start(
            "Run the slow command.", "task", MODEL, None, "test", None, None, "standard", None, "yolo", False, ()
        )
        deadline: float = time.monotonic() + 30
        while not await room.runtime.store.events_of_kind(started.id, "tool_call", 5):
            assert time.monotonic() < deadline, "the program never called its command"
            await asyncio.sleep(0.1)
        room.manager.cancel(started.id)
        async with asyncio.timeout(30):
            ended: RunRecord = await room.manager.wait(started.id)
        ends.flush()
    assert ended.status == "cancelled", ended.result
    assert not [
        line for line in logs if line["event"] in ("claude_code_program_cut", "claude_code_programs_ended_together")
    ]


async def _continuing(room: Room, monkeypatch: pytest.MonkeyPatch, count: int, wait_s: float = 0.05) -> None:
    """How often a cut run is continued (``[providers.claude_code] continue_cut``) for this test, and how long Dot waits
    before it starts the program again (2 s and 10 s in the owner's Dot)."""
    provider = await room.runtime.router.provider("claude_code")
    monkeypatch.setattr(provider, "_config", provider.config.model_copy(update={"continue_cut": count}))
    monkeypatch.setattr(cut_module, "CONTINUE_AFTER_S", (wait_s, wait_s))


def _starts(room: Room) -> list[dict[str, object]]:
    """The programs the stand-in started, in order, each with the session it resumed (``None`` for a new one) and the
    one it is in."""
    path: Path = room.record / "starts.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _forget_starts(room: Room) -> None:
    for name in ("starts.log", "continued.json", "fresh-died"):
        (room.record / name).unlink(missing_ok=True)


async def _start(room: Room, goal: str, approvals: str = "yolo") -> RunRecord:
    return await room.manager.start(
        goal,
        "task",
        MODEL,
        None,
        "test",
        None,
        None,
        "standard",
        None,
        approvals,
        False,
        (),  # type: ignore[arg-type]
    )


async def _finished(room: Room, started: RunRecord, seconds: float = 60) -> RunRecord:
    async with asyncio.timeout(seconds):
        return await room.manager.wait(started.id)


async def _continued_events(room: Room, run_id: str) -> list[EventRecord]:
    return await room.runtime.store.events_of_kind(run_id, "claude_code_continued", 10)


@pytest.mark.parametrize(
    ("how", "returncode", "named"),
    [("sigkill", -9, "SIGKILL"), ("sigterm", -15, "SIGTERM"), ("exit 1", 1, None)],
)
async def test_a_cut_run_is_continued_in_place_and_ends_done(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch, how: str, returncode: int, named: str | None
) -> None:
    """Items 1581 and 1582: the program of a run is killed (or crashes) after a step, with nothing of Dot's doing. The
    run is not lost: Dot starts the program again with ``--resume`` of its session in the SAME run (one run record, its
    id, its events go on), tells it it was cut off and to look at the real state, and the run ends done."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    verb: str = "die with exit 1" if how == "exit 1" else f"die by {how}"
    with capture_logs() as logs:
        started: RunRecord = await _start(room, f"Please {verb} once.")
        ended: RunRecord = await _finished(room, started)
    assert ended.id == started.id and ended.status == "done" and ended.result == "Continued.", ended.result
    # The steps of both programs are the run's, and the chat has the one run.
    assert ended.steps == 2
    assert [run.id for run in await room.runtime.store.list_runs(10, ended.thread_id)] == [ended.id]
    starts: list[dict[str, object]] = _starts(room)
    assert [one["resume"] is not None for one in starts] == [False, True], starts
    assert starts[1]["resume"] == starts[0]["session"] == starts[1]["session"]
    (event,) = await _continued_events(room, ended.id)
    data: JsonObject = event.data
    assert (data["attempt"], data["of"], data["fresh_session"]) == (1, 2, False)
    assert data["returncode"] == returncode and data["signal"] == named and data["steps"] == 1
    assert data["inited"] is True and data["claude_session"] == starts[0]["session"]
    assert isinstance(data["cut_at"], str) and re.fullmatch(r"\d\d:\d\d:\d\d", data["cut_at"])
    steps: list[EventRecord] = await room.runtime.store.events_of_kind(ended.id, "assistant", 10)
    assert [step.data["step"] for step in steps] == [1, 2]
    for kind in ("run_started", "run_finished"):
        assert len(await room.runtime.store.events_of_kind(ended.id, kind, 10)) == 1, kind
    # The program that went on was told what happened, in the words of the brief.
    went_on: dict[str, object] = await asyncio.to_thread(_json, room.record / "continued.json")
    note: str = str(went_on["messages"][-1])  # type: ignore[index]  # a list of what it read
    assert f"was cut off at {data['cut_at']} by something outside this run" in note
    assert "may be lost" in note and "look at the real state" in note and "go on from where your plan says" in note
    assert "--resume" in went_on["args"]  # type: ignore[operator]  # the arguments it was started with
    # The run's log says it too, and the chat keeps the session at the clean end.
    assert [line["attempt"] for line in logs if line["event"] == "claude_code_run_continued"] == [1]
    kept: dict[str, object] = await asyncio.to_thread(_json, chat_path(ended.thread_id))
    assert kept["session_id"] == starts[0]["session"]


async def test_a_second_cut_ends_the_run_when_one_continuation_is_allowed(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Items 1581 and 1582: ``continue_cut = 1``: the program that went on is cut too, and the run fails, with both
    exits in its context and what was done about the first."""
    await _continuing(room, monkeypatch, 1)
    _forget_starts(room)
    ended: RunRecord = await _finished(room, await _start(room, "Please die by sigkill."))
    assert ended.status == "failed" and ended.result == "Claude Code ended before it answered", ended.result
    assert len(_starts(room)) == 2
    context: dict[str, object] = await _failed_context(room, ended)
    assert context["continued"] == 1 and "as often as allowed" in str(context["not_continued"])
    cuts: object = context["cuts"]
    assert isinstance(cuts, list) and [cut["returncode"] for cut in cuts] == [-9, -9]  # type: ignore[index]
    assert len(await _continued_events(room, ended.id)) == 1
    assert not await asyncio.to_thread(chat_path(ended.thread_id).exists)


async def test_a_run_is_continued_twice_and_the_third_cut_ends_it(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1581: ``continue_cut`` is 2 by default, after 2 s and after 10 s: the third cut ends the run, with the exits
    of all three in its context."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    ended: RunRecord = await _finished(room, await _start(room, "Please die by sigterm."))
    assert ended.status == "failed", ended.result
    assert len(_starts(room)) == 3
    context: dict[str, object] = await _failed_context(room, ended)
    assert context["continued"] == 2 and isinstance(context["cuts"], list) and len(context["cuts"]) == 3
    assert [event.data["attempt"] for event in await _continued_events(room, ended.id)] == [1, 2]


async def test_a_stop_while_dot_waits_to_start_the_program_again_ends_the_run_stopped(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Items 1581 and 1582: the owner's Stop is never continued, and it cuts the wait short: the run ends cancelled at
    once and the program is not started again."""
    await _continuing(room, monkeypatch, 2, wait_s=30.0)
    _forget_starts(room)
    with capture_logs() as logs:
        started: RunRecord = await _start(room, "Please die by sigkill once.")
        deadline: float = time.monotonic() + 30
        while not any(line["event"] == "claude_code_program_cut" for line in logs):
            assert time.monotonic() < deadline, "the program was never cut"
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        room.manager.cancel(started.id)
        ended: RunRecord = await _finished(room, started, 10)
    assert ended.status == "cancelled", ended.result
    assert len(_starts(room)) == 1 and not await _continued_events(room, ended.id)
    assert not await asyncio.to_thread(chat_path(ended.thread_id).exists)


async def test_a_stop_that_comes_with_the_death_wins_and_nothing_is_continued(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Items 1581 and 1582: a Stop racing the death. The run's stop is set as the program's exit is read (what the Stop
    sets that the task's own cancellation does not reach: the event the gate and the pumps read): the run ends stopped,
    not failed and not continued."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    real = run_module.exit_seen

    async def stopped_with_the_death(process: asyncio.subprocess.Process, wait_s: float = 2.0) -> int | None:
        for handle in room.manager._handles.values():
            handle.cancel.set()
        return await real(process, wait_s)

    monkeypatch.setattr(run_module, "exit_seen", stopped_with_the_death)
    ended: RunRecord = await _finished(room, await _start(room, "Please die by sigkill once."))
    assert ended.status == "cancelled", ended.result
    assert len(_starts(room)) == 1 and not await _continued_events(room, ended.id)


async def test_a_plan_limit_is_not_continued(room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch) -> None:
    """Items 1581 and 1582: the plan's window used up is an error of its own that ends the run saying when it resets: no
    second program is started, the same window would refuse it."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    ended: RunRecord = await _finished(room, await _start(room, "Hit the limit."))
    assert ended.status == "failed" and "usage limit is reached" in ended.result
    assert len(_starts(room)) == 1 and not await _continued_events(room, ended.id)


@contextlib.asynccontextmanager
async def _outward_shell(room: Room, free: bool, slow: bool) -> AsyncIterator[None]:
    """Dot's ``shell`` as an action the policy keeps for the owner (a message sent for them), slow or quick; with the
    owner's free mode on or off, which decides whether the gate asks about it."""
    registry = room.runtime.registry
    shell: RegisteredTool = registry.tools["shell"]

    async def sending(arguments: JsonObject, ctx: ToolContext) -> PreparedCall:
        prepared: PreparedCall = await shell.prepare(arguments, ctx)

        async def sent(_: ToolContext) -> ToolResult:
            await asyncio.sleep(30 if slow else 0)
            return ToolResult(text="sent", images=(), is_error=False)

        mail = prepared.action.model_copy(update={"kind": "message", "flags": ("message_send", "external_send")})
        return dataclasses.replace(prepared, action=mail, run=sent)

    registry.tools["shell"] = dataclasses.replace(shell, prepare=sending)
    room.runtime.approvals.switch_free(free)
    try:
        yield
    finally:
        room.runtime.approvals.switch_free(False)
        registry.tools["shell"] = shell


@pytest.mark.parametrize("free", [False, True])
async def test_a_call_that_may_have_sent_something_for_the_owner_is_not_continued(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch, free: bool
) -> None:
    """Items 1581 and 1582: the program is cut while a call of an action the policy keeps for the owner (a message or
    mail sent for them, a payment, a public post) is in flight: nobody knows whether it was sent, and a program told to
    go on could send it twice. The run ends failed with the full context, the call named; the owner's free mode, which
    asks nothing, changes nothing here."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    async with _outward_shell(room, free=free, slow=True):
        ended: RunRecord = await _finished(room, await _start(room, "Please die while sending."))
    assert ended.status == "failed" and ended.result == "Claude Code ended before it answered", ended.result
    assert len(_starts(room)) == 1 and not await _continued_events(room, ended.id)
    context: dict[str, object] = await _failed_context(room, ended)
    assert context["continued"] == 0 and context["signal"] == "SIGKILL"
    assert "outward action was in flight (shell)" in str(context["not_continued"])


async def test_a_call_whose_result_the_model_read_does_not_stop_the_continuation(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1581: only what began since the model's last step counts as in flight. A message was sent, the model took
    the result in (a step began after it) and the program was cut later: the run goes on, which stays the model's to do
    with the real state in front of it."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    async with _outward_shell(room, free=True, slow=False):
        ended: RunRecord = await _finished(room, await _start(room, "Please die after sending."))
    assert ended.status == "done" and ended.result == "Continued.", ended.result
    assert len(_starts(room)) == 2 and len(await _continued_events(room, ended.id)) == 1


async def test_the_words_of_the_step_the_cut_interrupted_are_dropped_from_the_page(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1581: the program was cut in the middle of a step, after half a sentence the page already showed. The step
    never closed and the program that goes on begins its own as step 1 again, so Dot sends the page a ``reset`` for the
    interrupted one, as it does for a step it sends back: the half sentence is not glued to the new words."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    async with room.runtime.bus.subscribe_kinds(frozenset({"delta"})) as deltas:
        ended: RunRecord = await _finished(room, await _start(room, "Please die in a step."))
    shown: list[tuple[object, object]] = []
    while not deltas.queue.empty():
        event: EventRecord = deltas.queue.get_nowait()
        if event.run_id == ended.id:
            shown.append((event.data["kind"], event.data["step"]))
    assert ended.status == "done" and ended.steps == 1, (ended.status, ended.result)
    assert ("text", 1) in shown and ("reset", 1) in shown, shown
    assert shown.index(("reset", 1)) > shown.index(("text", 1))
    assert len(_starts(room)) == 2


async def test_a_program_cut_after_the_models_finish_ends_the_run_as_the_finish_said(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1581: the model called ``finish`` and the program was cut before it said more: the work is done and its
    answer is Dot's, so the run ends as the finish said (done, with that answer), not failed with "ended before it
    answered", and it is not continued. The chat does not keep a session that never ended clean."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    ended: RunRecord = await _finished(room, await _start(room, "Please die after finishing."))
    assert ended.status == "done" and ended.result == "Everything is checked.", ended.result
    assert len(_starts(room)) == 1 and not await _continued_events(room, ended.id)
    assert not await asyncio.to_thread(chat_path(ended.thread_id).exists)


async def test_a_helper_that_is_cut_gives_its_parent_the_final_result_of_the_continued_run(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Items 1581 and 1582: the run is continued in place, so who waits for it is not told it failed: a delegated
    helper whose program is cut once gives its parent the end of the continued run, the same run record."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    parent: RunRecord = await _start(room, "Please talk with me.")
    try:
        async with asyncio.timeout(60):
            outcome = await room.manager.delegate(parent.id, "Please die by sigkill once.", "task", None, None)
        helper: RunRecord = outcome.run
        assert helper.status == "done" and helper.result == "Continued." and helper.parent_run_id == parent.id
        assert helper.steps == 2 and len(await _continued_events(room, helper.id)) == 1
        assert [one["resume"] is not None for one in _starts(room)][1:] == [False, True]
    finally:
        room.manager.cancel(parent.id)
        await _finished(room, parent, 30)


async def test_a_resume_the_api_refuses_is_met_with_a_new_session_that_is_told_what_was_done(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1581: if the resume of the cut session fails with an API error (the kind that says the session cannot be
    taken up again), the next program is a NEW session, with the run's goal, what the chat said and a note of what the
    cut one had done, from Dot's record of the run. (Claude Code 2.1.331 did not refuse a resume of a session cut in a
    call: measured. This is for another version, or a session file that is gone.)"""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    ended: RunRecord = await _finished(room, await _start(room, "Please die by sigkill and refuse the resume."))
    assert ended.status == "done" and ended.result == "Continued in a new session.", ended.result
    starts: list[dict[str, object]] = _starts(room)
    assert [one["resume"] is not None for one in starts] == [False, True, False]
    events: list[EventRecord] = await _continued_events(room, ended.id)
    assert [(event.data["attempt"], event.data["fresh_session"]) for event in events] == [(1, False), (2, True)]
    # What the refused program printed before it failed is masked, in the event as everywhere (the pre-push review).
    refused: object = events[1].data["stderr"]
    assert isinstance(refused, str) and "resume failed" in refused and "sk-ant" not in refused
    # The refused program was running when it said so and Dot ended it: no exit of its own, and what it said instead.
    assert events[1].data["returncode"] is None and events[1].data["exited"] is False
    assert "API Error: 400" in str(events[1].data["said"]) and "sk-ant" not in str(events[1].data["said"])
    fresh: dict[str, object] = await asyncio.to_thread(_json, room.record / "continued.json")
    first: str = str(fresh["messages"][0])  # type: ignore[index]  # what the new session read
    assert "refuse the resume" in first and "What the cut session had done" in first
    assert "Its first words" in first and "Looking into it." in first and "could not be taken up again" in first
    assert "--resume" not in fresh["args"]  # type: ignore[operator]  # a new session
    # The new session ended clean, so the chat keeps it.
    kept: dict[str, object] = await asyncio.to_thread(_json, chat_path(ended.thread_id))
    assert kept["session_id"] == starts[2]["session"] != starts[0]["session"]


async def test_a_new_session_cut_before_it_took_in_its_brief_is_given_the_brief_again(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1581 (the pre-push review): the brief of a new session is sent once; a program that began (it said what the
    model has) and was cut before it took the brief in has heard nothing of the run, so the next program is a new
    session again, with the brief, not a resume of a session that has no goal."""
    await _continuing(room, monkeypatch, 3)
    _forget_starts(room)
    goal: str = "Please die by sigkill and refuse the resume and the new session dies first."
    ended: RunRecord = await _finished(room, await _start(room, goal))
    assert ended.status == "done" and ended.result == "Continued in a new session.", ended.result
    assert [one["resume"] is not None for one in _starts(room)] == [False, True, False, False]
    events: list[EventRecord] = await _continued_events(room, ended.id)
    assert [(event.data["attempt"], event.data["fresh_session"]) for event in events] == [
        (1, False),
        (2, True),
        (3, True),
    ]
    last: dict[str, object] = await asyncio.to_thread(_json, room.record / "continued.json")
    brief: str = str(last["messages"][0])  # type: ignore[index]  # what the last program read
    assert "the new session dies first" in brief and "What the cut session had done" in brief
    assert "--resume" not in last["args"]  # type: ignore[operator]  # a new session


async def test_a_message_that_comes_while_dot_waits_is_not_lost_when_the_next_program_is_cut_too(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1581 (the pre-push review): the owner writes while Dot waits to start the program again. The new program
    takes in the note about the cut, not that message, and is cut again before it reads it: the one after it must be
    sent the message, which a note counted as taken in place of it once lost."""
    await _continuing(room, monkeypatch, 2, wait_s=1.0)
    _forget_starts(room)
    with capture_logs() as logs:
        started: RunRecord = await _start(room, "Please die by sigkill twice.")
        deadline: float = time.monotonic() + 30
        while not any(line["event"] == "claude_code_program_cut" for line in logs):
            assert time.monotonic() < deadline, "the program was never cut"
            await asyncio.sleep(0.05)
        room.manager.steer(started.id, "Also look in the second folder.", ())
        ended: RunRecord = await _finished(room, started)
    assert ended.status == "done" and ended.result == "Continued.", ended.result
    assert len(_starts(room)) == 3 and len(await _continued_events(room, ended.id)) == 2
    last: dict[str, object] = await asyncio.to_thread(_json, room.record / "continued.json")
    messages: list[str] = [str(one) for one in last["messages"]]  # type: ignore[attr-defined]  # a list of text
    assert len(messages) == 2 and "Also look in the second folder." in messages[0], messages
    assert "was cut off at" in messages[1]


async def test_what_the_owner_sent_that_the_cut_program_never_took_in_is_sent_to_the_next(
    room: Room, ends: EndedTogether, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1581: a message the owner sent while the program worked, which it had not taken in when it was cut, would be
    lost with it. The program that goes on is sent it first, as sent, and then the note about the cut."""
    await _continuing(room, monkeypatch, 2)
    _forget_starts(room)
    started: RunRecord = await _start(room, "Please die with a message waiting.")
    deadline: float = time.monotonic() + 30
    while not await room.runtime.store.events_of_kind(started.id, "tool_call", 5):
        assert time.monotonic() < deadline, "the program never called its command"
        await asyncio.sleep(0.05)
    room.manager.steer(started.id, "Also look in the second folder.", ())
    ended: RunRecord = await _finished(room, started)
    assert ended.status == "done" and ended.result == "Continued.", ended.result
    went_on: dict[str, object] = await asyncio.to_thread(_json, room.record / "continued.json")
    messages: list[str] = [str(one) for one in went_on["messages"]]  # type: ignore[attr-defined]  # a list of text
    assert len(messages) == 2 and "Also look in the second folder." in messages[0]
    assert "was cut off at" in messages[1]
