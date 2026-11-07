"""A single question of Dot's on the owner's Claude plan (item 1017), end to end with a stand-in ``claude``
(``tests/fixtures/claude_ask_standin.py``) behind Dot's real router: the harness's proposer on ``claude_code:opus``
gets its proposal from the program's structured output, the program started with no tool of its own, no settings file,
MCP server or kept session, the question on its input and the system prompt in a 0600 file of a private folder that is
removed once the call ends, in an environment without an API key or Dot's variables. A model given a tool of the
program's own, and a plan whose window is used up, fail the call with why. No model is called."""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.review import HarnessBounds, HarnessProposal, ProposalRequest, harness_proposer_for
from dotagent.config import load_config
from dotagent.core.errors import ProviderProtocolError, ProviderWindowError
from dotagent.core.paths import claude_code_dir
from dotagent.llm.calls import CallOrigin
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, ModelReply, StreamDelta, system_message, user_message
from dotagent.runtime import Runtime, build_runtime, close_runtime

pytestmark = pytest.mark.asyncio(loop_scope="module")

STAND_IN: Path = Path(__file__).resolve().parents[1] / "fixtures" / "claude_ask_standin.py"
MODEL: str = "claude_code:opus"
REQUEST: ProposalRequest = ProposalRequest(
    goal="Find the open issues",
    digest="step 2 read the page before it settled",
    signals=(),
    answer="There are none.",
    reached_goal=False,
    habits="",
    tool_guidance="",
    experience=(),
    scope="invariant",
    bounds=HarnessBounds(habits_chars=1200, tool_guidance_chars=1500, component_lines=16),
)


@dataclass(frozen=True)
class Room:
    runtime: Runtime
    record: Path


def _lay_out(root: Path) -> tuple[Path, Path, Path, Path]:
    home: Path = root / "home"
    home.mkdir()
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def room(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Room]:
    root: Path = Path(await asyncio.to_thread(os.path.realpath, tmp_path_factory.mktemp("claude-ask")))
    home, record, program, state = await asyncio.to_thread(_lay_out, root)
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_LOG_DIR", str(root / "logs"))
    patch.setenv("DOT_CHROME_DIR", str(root / "chrome"))
    patch.setenv("DOT_CLAUDE", str(program))
    patch.setenv("CLAUDE_CONFIG_DIR", str(state))
    patch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-for-the-program")
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield Room(runtime=built, record=record)
    finally:
        await close_runtime(built)
        patch.undo()


def _started(room: Room) -> dict[str, object]:
    loaded: object = json.loads((room.record / "started.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


async def _propose(room: Room) -> HarnessProposal:
    return await harness_proposer_for(room.runtime.router, MODEL, 30.0, "harness:test", None)(REQUEST)


async def test_the_harness_proposer_answers_on_the_plan_through_the_programs_structured_output(room: Room) -> None:
    await asyncio.to_thread((room.record / "mode").write_text, "propose", encoding="utf-8")
    proposal: HarnessProposal = await _propose(room)
    assert proposal.tool_guidance == "- Wait for a page to settle before you read it."
    assert proposal.habits is None
    started: dict[str, object] = _started(room)
    argv = started["argv"]
    assert isinstance(argv, list)
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == "opus"
    # The schema lets each field be null, as a provider's API does; Dot's own validation holds the answer.
    schema: object = json.loads(str(argv[argv.index("--json-schema") + 1]))
    assert isinstance(schema, dict)
    assert schema["properties"]["habits"]["type"] == ["string", "null"]
    # The question is on the program's input and the system prompt in a 0600 file: neither is in its arguments.
    assert "step 2 read the page before it settled" in str(started["stdin"])
    assert not any("step 2 read the page" in str(word) for word in argv)
    assert started["system_mode"] == 0o600
    assert "propose_harness" in str(started["system"])
    env = started["env"]
    assert isinstance(env, list)
    assert "ANTHROPIC_API_KEY" not in env
    assert not [name for name in env if str(name).startswith("DOT_")]
    # The call's private folder is gone once it answered.
    assert str(started["cwd"]).startswith(str(claude_code_dir() / "asks"))
    assert not await asyncio.to_thread(Path(str(started["cwd"])).exists)


async def _discard(delta: StreamDelta) -> None:
    return None


async def test_a_question_without_a_tool_is_answered_in_text(room: Room) -> None:
    await asyncio.to_thread((room.record / "mode").write_text, "text", encoding="utf-8")
    request: ChatRequest = ChatRequest(
        model="opus",
        messages=(system_message("Answer briefly."), user_message("Say something.", ())),
        tools=(),
        temperature=None,
        max_output_tokens=None,
        effort="minimal",
        speed="standard",
        session_key="test",
        cache_mark=None,
        prompt_reuse="single",
        window=None,
    )
    origin: CallOrigin = CallOrigin(run_id=None, purpose="review")
    reply: ModelReply = await room.runtime.router.complete(parse_model_ref(MODEL), request, _discard, origin)
    assert (reply.text, reply.finish, reply.usage.input_tokens, reply.usage.cached_tokens) == (
        "A plain answer.",
        "stop",
        1502,
        300,
    )
    argv = _started(room)["argv"]
    assert isinstance(argv, list)
    assert "--json-schema" not in argv
    assert argv[argv.index("--effort") + 1] == "low"


async def test_a_tool_of_the_programs_own_or_a_used_up_window_fails_the_call(room: Room) -> None:
    await asyncio.to_thread((room.record / "mode").write_text, "tool", encoding="utf-8")
    with pytest.raises(ProviderProtocolError, match=r"more than Dot's question allows \(tools: Bash\)"):
        await _propose(room)
    await asyncio.to_thread((room.record / "mode").write_text, "limit", encoding="utf-8")
    with pytest.raises(ProviderWindowError, match="usage limit is reached"):
        await _propose(room)
