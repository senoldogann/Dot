"""A run on the owner's Claude plan (item 1013), end to end with a stand-in ``claude`` (``tests/fixtures/
claude_standin.py``) on a real runtime, manager, bridge and gate: the program is started with the flags that keep the
model to Dot's tools, an environment without Dot's variables and a 0600 MCP config file in a private folder, removed
once the run ends; it reaches the chat's door over HTTP with that file, and each call it makes goes through the run's
gate as a call of Dot's own loop (a write into the workspace's ``.claude`` folder refused, one into the workspace
done); the run ends done with the program's answer and keeps its session for the chat. A program whose model has a tool
that is not Dot's, and a plan whose window is used up, end the run failed with why. No model is called."""

import asyncio
import json
import os
import stat
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.claude_code.run import chat_path
from dotagent.config import load_config
from dotagent.core.paths import claude_code_dir
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord

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
    (home / "config.toml").write_text("[browser]\nheadless = true\n\n[agent]\ngoal_mode = false", encoding="utf-8")
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
        ("--tools", ""),
        ("--setting-sources", ""),
        ("--permission-mode", "dontAsk"),
        ("--permission-prompts", "none"),
    ):
        assert args[args.index(flag) + 1] == value
    for flag in ("--include-partial-messages", "--verbose", "--strict-mcp-config", "--disable-slash-commands"):
        assert flag in args
    door: str = args[args.index("--allowedTools") + 1]
    assert door.startswith("mcp__dot_") and "--resume" not in args
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
