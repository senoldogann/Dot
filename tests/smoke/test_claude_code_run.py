"""A run on the owner's Claude plan (item 1013), end to end with a stand-in ``claude`` (``tests/fixtures/
claude_standin.py``) on a real runtime, manager, bridge and gate: the program is started with the flags that keep the
model to Dot's tools, an environment without Dot's variables and a 0600 MCP config file in a private folder, removed
once the run ends; it reaches the chat's door over HTTP with that file, and each call it makes goes through the run's
gate as a call of Dot's own loop (a write into the workspace's ``.claude`` folder refused, one into the workspace
done); the run ends done with the program's answer and keeps its session for the chat. A program whose model has a tool
that is not Dot's, and a plan whose window is used up, end the run failed with why. No model is called."""

import asyncio
import dataclasses
import json
import os
import stat
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.claude_code.run import chat_path
from dotagent.config import load_config
from dotagent.core.errors import FailSafeTriggered, InvalidValueError
from dotagent.core.paths import claude_code_dir
from dotagent.core.types import JsonObject
from dotagent.memory.service import SavedMemory, save_memory, update_memory_note, words_only
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler.notices import said_nothing_new
from dotagent.store.endings import PREFIX_EMERGENCY_STOP
from dotagent.store.models import EventRecord, RunPicks, RunRecord
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
    """How many replies of the model with words the run has stored."""
    steps: list[EventRecord] = await room.runtime.store.events_of_kind(run_id, "assistant", 50)
    return sum(1 for step in steps if str(step.data.get("text", "")).strip())


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
