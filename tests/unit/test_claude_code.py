"""Item 1013's pure parts: the command a run on the owner's Claude plan starts ``claude`` with (every flag that keeps
the model to Dot's tools, the secret never among the arguments), its environment (no API key, none of Dot's or git's
variables, the updater and telemetry off), the 0600 file of the door's MCP config, and the reading of Claude Code's
stream-json output (an init line that lists anything but the door's tools is refused; the plan's window and a limit
error say when it resets; a turn's result)."""

import json
import stat
from pathlib import Path

from dotagent.agent.limits import LimitReached, RunLimits
from dotagent.claude_code.run import (
    answered_all,
    claude_argv,
    claude_env,
    ended_clean,
    mcp_config,
    system_prompt,
    write_mcp_config,
)
from dotagent.claude_code.stream import (
    BlockBegan,
    Delta,
    Init,
    LimitSeen,
    McpServer,
    Result,
    StepBegan,
    StepClosed,
    StepFinished,
    Taken,
    init_problem,
    limit_message,
    limit_resets,
    limit_said,
    read_line,
    user_line,
)
from dotagent.llm.types import ImageData, ToolSpec
from dotagent.opencode.bridge import Door, new_door
from dotagent.repair.claude import window_refused
from dotagent.tools.base import ToolResult, ok

DOOR: str = "dot_1a2b3c4d"
CONFIG: Path = Path("/private/dot/claude_code/runs/run-x/mcp.json")


async def _run(call: object) -> ToolResult:
    return ok("")


def _door() -> Door:
    spec: ToolSpec = ToolSpec(name="notify", description="Tell the owner.", parameters={"type": "object"})
    return new_door("thread-1", (spec,), _run)


def _value(argv: tuple[str, ...], flag: str) -> str:
    assert argv.count(flag) == 1, flag
    return argv[argv.index(flag) + 1]


def test_the_command_keeps_the_model_to_the_doors_tools_and_holds_no_secret() -> None:
    door: Door = _door()
    argv: tuple[str, ...] = claude_argv("/usr/local/bin/claude", "opus", "You are Dot.", CONFIG, door.name, None)
    assert argv[:2] == ("/usr/local/bin/claude", "-p")
    assert _value(argv, "--input-format") == "stream-json"
    assert _value(argv, "--output-format") == "stream-json"
    assert _value(argv, "--model") == "opus"
    assert _value(argv, "--append-system-prompt") == "You are Dot."
    # No built-in tool at all, no settings file read, the door as the only MCP server and the only allowed tools.
    assert _value(argv, "--tools") == ""
    assert _value(argv, "--setting-sources") == ""
    assert _value(argv, "--mcp-config") == str(CONFIG)
    assert _value(argv, "--permission-mode") == "dontAsk"
    assert _value(argv, "--permission-prompts") == "none"
    assert _value(argv, "--allowedTools") == f"mcp__{door.name}"
    for flag in (
        "--include-partial-messages",
        "--replay-user-messages",
        "--verbose",
        "--strict-mcp-config",
        "--disable-slash-commands",
    ):
        assert argv.count(flag) == 1, flag
    for never in ("--dangerously-skip-permissions", "--plugin-dir", "--plugin-url", "--safe-mode", "--resume"):
        assert never not in argv
    assert not any(door.secret in arg for arg in argv)


def test_a_chat_with_a_session_resumes_it() -> None:
    argv: tuple[str, ...] = claude_argv("claude", "sonnet", "p", CONFIG, DOOR, "ses-1")
    assert _value(argv, "--resume") == "ses-1"
    # The allowed tools stay the last words, so that nothing after them is read as one.
    assert argv[-2:] == ("--allowedTools", f"mcp__{DOOR}")


def test_the_environment_has_no_api_key_and_none_of_dots_or_gits_variables(tmp_path: Path) -> None:
    env: dict[str, str] = claude_env(
        {
            "HOME": "/Users/owner",
            "PATH": "/usr/bin:/bin",
            "ANTHROPIC_API_KEY": "sk-ant-1",
            "ANTHROPIC_BASE_URL": "https://proxy.example",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "GITHUB_TOKEN": "ghp_1",
            "DOT_HOME": "/Users/owner/.dot",
            "DOT_API_TOKEN": "t",
            "GIT_DIR": "/x",
            "CLAUDE_CONFIG_DIR": "/Users/owner/.claude-work",
        },
        tmp_path,
    )
    assert env["HOME"] == "/Users/owner" and env["PATH"] == "/usr/bin:/bin"
    assert env["CLAUDE_CONFIG_DIR"] == "/Users/owner/.claude-work"
    assert env["DISABLE_AUTOUPDATER"] == "1" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    for gone in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "GITHUB_TOKEN"):
        assert gone not in env
    assert not [name for name in env if name.startswith(("DOT_", "GIT_"))]


def test_the_doors_config_is_a_file_only_the_owner_reads(tmp_path: Path) -> None:
    door: Door = _door()
    path: Path = write_mcp_config(tmp_path, mcp_config(door, "http://127.0.0.1:5000/mcp"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "mcpServers": {
            door.name: {
                "type": "http",
                "url": "http://127.0.0.1:5000/mcp",
                "headers": {"Authorization": f"Bearer {door.secret}"},
            }
        }
    }


def test_the_prompt_says_who_the_model_is_its_tools_and_what_it_knows_of_the_owner() -> None:
    prompt: str = system_prompt("Dot", DOOR, ["notify", "shell"], Path("/w"), "Friday", ["(you said) My name is Ada."])
    assert prompt.startswith("You are Dot,") and f"MCP server {DOOR}: notify, shell" in prompt
    assert "Dot's workspace, /w" in prompt and prompt.endswith("- (you said) My name is Ada.")


def _init(tools: list[str], servers: list[dict[str, str]], extra: dict[str, list[str] | list[dict[str, str]]]) -> Init:
    line: dict[str, object] = {
        "type": "system",
        "subtype": "init",
        "session_id": "ses-1",
        "tools": tools,
        "mcp_servers": servers,
        "slash_commands": [],
        "skills": [],
        **extra,
    }
    event: object = read_line(json.dumps(line))
    assert isinstance(event, Init)
    return event


def test_an_init_with_the_doors_tools_alone_passes_and_anything_more_is_refused() -> None:
    own: list[str] = [f"mcp__{DOOR}__notify", f"mcp__{DOOR}__shell"]
    connected: list[dict[str, str]] = [{"name": DOOR, "status": "connected"}]
    good: Init = _init(own, connected, {})
    assert good.session_id == "ses-1" and good.mcp_servers == (McpServer(name=DOOR, status="connected"),)
    assert init_problem(good, DOOR) is None
    built_in: str | None = init_problem(_init([*own, "Bash", "Read"], connected, {}), DOOR)
    assert built_in is not None and "tools that are not Dot's: Bash, Read" in built_in
    other_door: str | None = init_problem(_init([*own, "mcp__dot_99999999__shell"], connected, {}), DOOR)
    assert other_door is not None and "mcp__dot_99999999__shell" in other_door
    server: str | None = init_problem(_init(own, [*connected, {"name": "gmail", "status": "connected"}], {}), DOOR)
    assert server is not None and "MCP servers that are not Dot's: gmail" in server
    failed: str | None = init_problem(_init([], [{"name": DOOR, "status": "failed"}], {}), DOOR)
    assert failed is not None and "their server is failed" in failed
    for kind in ("slash_commands", "skills", "plugins"):
        extra: str | None = init_problem(_init(own, connected, {kind: ["review"]}), DOOR)
        assert extra is not None and "review" in extra, kind
    # The plugins the program comes with pass (item 1013's first real run); one installed beside them does not.
    shipped: dict[str, str] = {"name": "cc-plugin-privacy", "path": "builtin", "source": "cc-plugin-privacy@builtin"}
    assert init_problem(_init(own, connected, {"plugins": [shipped]}), DOOR) is None
    added: dict[str, str] = {"name": "review", "path": "/Users/x/.claude/plugins/review", "source": "review@market"}
    installed: str | None = init_problem(_init(own, connected, {"plugins": [shipped, added]}), DOOR)
    assert installed is not None and "plugins: review" in installed


def test_the_stream_events_of_a_reply_are_read_in_order() -> None:
    def event(body: dict[str, object]) -> object:
        return read_line(json.dumps({"type": "stream_event", "event": body, "parent_tool_use_id": None}))

    usage: dict[str, int] = {"input_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 85}
    assert event({"type": "message_start", "message": {"usage": usage}}) == StepBegan(
        input_tokens=100, cached_tokens=85
    )
    assert event({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}) == BlockBegan(
        index=0, kind="thinking", tool=""
    )
    assert event(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hm"}}
    ) == Delta(index=0, kind="thinking", text="hm")
    assert event(
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "name": "mcp__d__shell"}}
    ) == BlockBegan(index=1, kind="tool", tool="mcp__d__shell")
    assert event({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta"}}) is None
    assert event(
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}}
    ) == StepFinished(stop_reason="tool_use", output_tokens=9)
    assert event({"type": "message_stop"}) == StepClosed()
    # A subagent's events, a whole assistant message and a line that is no JSON are not the run's to read.
    subagent: dict[str, object] = {"type": "stream_event", "event": {"type": "message_stop"}, "parent_tool_use_id": "t"}
    assert read_line(json.dumps(subagent)) is None
    assert read_line(json.dumps({"type": "assistant", "message": {"content": []}})) is None
    assert read_line("warning: something") is None


def test_the_plans_window_and_a_limit_error_say_when_it_resets() -> None:
    refused: object = read_line(
        json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": 1_900_000_000},
            }
        )
    )
    assert isinstance(refused, LimitSeen) and window_refused(refused.window)
    assert limit_resets(refused.window, "") == 1_900_000_000.0
    said: str = limit_message(refused.window, 1_900_000_000.0)
    assert "usage limit is reached (five hour window)" in said and "resets at 2030-03-1" in said
    assert "does not move the task to another model" in said
    allowed: object = read_line(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}))
    assert isinstance(allowed, LimitSeen) and not window_refused(allowed.window)
    # An older error carries the reset after a bar.
    assert limit_said("Claude AI usage limit reached|1900000000")
    assert limit_resets(None, "Claude AI usage limit reached|1900000000") == 1_900_000_000.0
    assert limit_said("You've hit your limit · resets 3pm") and not limit_said("the file was not found")
    assert "when it resets is not known" in limit_message(None, None)


def test_a_result_is_read_with_its_answer_or_its_error() -> None:
    done: object = read_line(
        json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Done.", "session_id": "s"})
    )
    assert done == Result(session_id="s", is_error=False, subtype="success", text="Done.")
    failed: object = read_line(
        json.dumps({"type": "result", "subtype": "error_during_execution", "errors": ["boom"], "session_id": "s"})
    )
    assert failed == Result(session_id="s", is_error=True, subtype="error_during_execution", text="boom")


def test_a_message_goes_to_the_program_as_a_line_with_its_pictures() -> None:
    line: str = user_line("Look", (ImageData(media_type="image/png", data_b64="AAAA"),))
    assert line.endswith("\n") and "\n" not in line[:-1]
    assert json.loads(line) == {
        "type": "user",
        "session_id": "",
        "parent_tool_use_id": None,
        "message": {
            "role": "user",
            "content": [
                {"type": "text", "text": "Look"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
            ],
        },
    }


def test_a_run_ends_clean_only_neither_past_its_limits_nor_cut_at_the_models_output_limit() -> None:
    """The pre-push review of item 1013: a reply cut at the model's output limit ends the run failed, so its session is
    let go as one past the run's limits is."""
    over: LimitReached = LimitReached(seconds=99.0, tokens=1, limits=RunLimits(max_minutes=1.0, max_tokens=10))
    assert ended_clean(None, "end_turn")
    assert not ended_clean(None, "max_tokens")
    assert not ended_clean(over, "end_turn")


def test_a_message_the_program_took_in_is_read_and_a_tool_result_is_not() -> None:
    """Item 1013's first real run: a message sent while a tool worked was folded into that turn and one result answered
    both, so the run counts the messages the program says it took in (--replay-user-messages), not the results."""
    replay: dict[str, object] = {"type": "user", "isReplay": True, "message": {"role": "user", "content": []}}
    assert read_line(json.dumps(replay)) == Taken()
    tool_result: dict[str, object] = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result"}]}}
    assert read_line(json.dumps(tool_result)) is None


def test_a_result_answers_the_messages_taken_before_its_last_step_and_not_one_taken_after() -> None:
    """The pre-push review of item 1013: a message taken while a tool worked is answered by the next step of the turn,
    one result for both (measured); one taken while the last step streamed waits for a turn of its own."""
    assert answered_all(taken=1, sent=1, taken_since_step=False)
    assert answered_all(taken=2, sent=2, taken_since_step=False)
    assert not answered_all(taken=2, sent=2, taken_since_step=True)
    assert not answered_all(taken=1, sent=2, taken_since_step=False)


def test_only_the_measured_built_in_plugins_pass() -> None:
    later: dict[str, str] = {"name": "cc-plugin-new", "path": "builtin", "source": "cc-plugin-new@builtin"}
    own: list[str] = [f"mcp__{DOOR}__notify"]
    connected: list[dict[str, str]] = [{"name": DOOR, "status": "connected"}]
    refused: str | None = init_problem(_init(own, connected, {"plugins": [later]}), DOOR)
    assert refused is not None and "cc-plugin-new" in refused
