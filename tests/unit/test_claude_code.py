"""Item 1013's pure parts: the command a run on the owner's Claude plan starts ``claude`` with (every flag that keeps
the model to Dot's tools, the secret never among the arguments), its environment (no API key, none of Dot's or git's
variables, the updater and telemetry off), the 0600 file of the door's MCP config, and the reading of Claude Code's
stream-json output (an init line that lists anything but the door's tools is refused; the plan's window and a limit
error say when it resets; a turn's result)."""

import json
import stat
from pathlib import Path

from dotagent.agent.limits import LimitReached, RunLimits
from dotagent.agent.picks import owner_server_groups
from dotagent.agent.prompt import AgentBrief, team_sections
from dotagent.claude_code.models import (
    PlanModel,
    PlanModels,
    find_model,
    keep_models,
    model_labels,
    models_of_line,
)
from dotagent.claude_code.run import (
    LOADED_TOOLS,
    answered_all,
    claude_argv,
    claude_env,
    door_specs,
    door_tool,
    dot_finish,
    dot_tool,
    ended_clean,
    fast_off_words,
    goal_asks_notification,
    mcp_config,
    missing_tools,
    opened_bounds,
    run_effort,
    run_speed,
    system_prompt,
    tools_needed,
    tools_not_given,
    tools_not_listed,
    write_mcp_config,
)
from dotagent.claude_code.stream import (
    SEARCH_TOOL,
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
    listed_tools,
    read_line,
    user_line,
)
from dotagent.core.errors import ClaudeCodeBroke, ClaudeCodeError, failure_kind
from dotagent.llm.claude_code import model_info_of
from dotagent.llm.router import ModelRef, offers_fast_mode
from dotagent.llm.types import Effort, ImageData, ToolSpec
from dotagent.opencode.bridge import Door, new_door
from dotagent.repair.claude import window_refused
from dotagent.store.models import RunPicks
from dotagent.tools.base import ToolResult, ok
from dotagent.tools.shell import build_shell_tool

DOOR: str = "dot_1a2b3c4d"
CONFIG: Path = Path("/private/dot/claude_code/runs/run-x/mcp.json")


async def _run(call: object) -> ToolResult:
    return ok("")


def _door() -> Door:
    spec: ToolSpec = ToolSpec(name="notify", description="Tell the owner.", parameters={"type": "object"})
    return new_door("thread-1", (spec,), frozenset(), _run)


def _value(argv: tuple[str, ...], flag: str) -> str:
    assert argv.count(flag) == 1, flag
    return argv[argv.index(flag) + 1]


def test_the_command_keeps_the_model_to_the_doors_tools_and_holds_no_secret() -> None:
    door: Door = _door()
    argv: tuple[str, ...] = claude_argv(
        "/usr/local/bin/claude", "opus", "medium", "standard", "You are Dot.", CONFIG, door.name, None
    )
    assert argv[:2] == ("/usr/local/bin/claude", "-p")
    assert _value(argv, "--input-format") == "stream-json"
    assert _value(argv, "--output-format") == "stream-json"
    assert _value(argv, "--model") == "opus"
    assert _value(argv, "--append-system-prompt") == "You are Dot."
    # No built-in tool at all, no settings file read, the door as the only MCP server and the only allowed tools.
    # Claude Code's search of the door's tools is its one tool of its own (item 1053).
    assert _value(argv, "--tools") == "ToolSearch"
    assert _value(argv, "--setting-sources") == ""
    assert _value(argv, "--mcp-config") == str(CONFIG)
    assert _value(argv, "--permission-mode") == "dontAsk"
    assert _value(argv, "--permission-prompts") == "none"
    assert _value(argv, "--allowedTools") == f"mcp__{door.name},ToolSearch"
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
    argv: tuple[str, ...] = claude_argv("claude", "sonnet", "medium", "standard", "p", CONFIG, DOOR, "ses-1")
    assert _value(argv, "--resume") == "ses-1"
    # The allowed tools stay the last words, so that nothing after them is read as one.
    assert argv[-2:] == ("--allowedTools", f"mcp__{DOOR},ToolSearch")


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
    # A call of Dot's tool may wait a day for the owner (item 1076): Claude Code's own limit does not cut it short.
    assert env["MCP_TOOL_TIMEOUT"] == str(24 * 3600 * 1000)
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
    prompt: str = system_prompt(
        "Dot", DOOR, ["notify", "shell"], Path("/w"), "Friday", "# Team\n- Ada", ["(you said) My name is Ada."]
    )
    assert prompt.startswith("You are Dot,") and f"MCP server {DOOR}: notify, shell" in prompt
    assert "Dot's workspace, /w" in prompt and prompt.endswith("- (you said) My name is Ada.")
    assert "\n\n# Team\n- Ada\n\nWhat you know about the owner:" in prompt


def test_the_prompt_tells_the_model_to_look_a_tool_up_before_it_calls_it() -> None:
    """Item 1201: only the door's tools' names are in the prompt (item 1053), and runs on the plan called
    ``browser_find`` and ``search_files`` with a ``query`` they had guessed, which neither takes, at 15 times the rate
    of Dot's own loop. The prompt says that a tool's parameters come from its look-up, and how to look it up."""
    prompt: str = system_prompt("Dot", DOOR, ["browser_find"], Path("/w"), "Friday", "", [])
    assert f"{SEARCH_TOOL} (select:mcp__{DOOR}__<tool>, several at once" in prompt
    assert "before your first call of it" in prompt and "never guess" in prompt


def test_the_tools_in_the_prompt_from_the_start_are_door_tools_the_shell_sends_the_model_to() -> None:
    """Item 1341: the shell's description sends the model to ``terminal_open`` and ``wait_until``, and it called them by
    their names alone, with no arguments or guessed ones; their definitions are in the prompt from the start."""
    shell: str = build_shell_tool().spec.description
    assert {"terminal_open", "wait_until"} == LOADED_TOOLS
    assert all(name in shell and door_tool(name, False) and door_tool(name, True) for name in LOADED_TOOLS)


def test_the_prompt_names_finish_only_to_a_run_that_has_it() -> None:
    """Item 1076's pre-push review: a chat-mode run has no finish, and a prompt that told it to call one lost its
    answer; it ends with its last reply."""
    task: str = system_prompt("Dot", DOOR, ["finish", "shell"], Path("/w"), "Friday", "", [])
    chat: str = system_prompt("Dot", DOOR, ["shell"], Path("/w"), "Friday", "", [])
    assert "call finish" in task and "your last reply" not in task
    assert "finish" not in chat and "write your answer as your last reply" in chat


def test_the_model_gets_every_tool_of_dots_own_loop_but_the_loops_own() -> None:
    """Item 1063, the owner's screenshot of 2026-11-09: a run on the plan said it had no tool to talk to the team. It
    gets what a run of Dot's own loop gets: the team's, the mail's, the drafts'; not the loop's own (finish, the plan,
    use_tools, react); a run handed a part asks the owner nothing and hands nothing on; a campaign's stay Dot's
    own loop's."""
    for name in (
        "message_agent",
        "assign_work",
        "review_work",
        "work_orders",
        "delegate",
        "create_agent",
        "mail_search",
    ):
        assert door_tool(name, False), name
    for name in ("update_plan", "mark_plan_step", "use_tools", "react"):
        assert not door_tool(name, False), name
    for name in ("delegate", "message_agent", "ask_user", "ask_secret"):
        assert not door_tool(name, True), name
    assert door_tool("read_file", True)
    assert not door_tool("campaign_note", False) and not door_tool("campaign_achieved", False)


def test_the_prompt_tells_the_team_as_dots_own_does() -> None:
    """Item 1063: the agents the run may message, by name and role, and how to give them work orders."""
    ada: AgentBrief = AgentBrief(name="Ada", role="Writes copy", instructions="", made_by_dot=False, team="")
    said: str = team_sections(None, None, [ada], ["message_agent", "assign_work", "delegate"])
    assert "# Team" in said and "  - Ada: Writes copy" in said and "assign_work" in said
    assert team_sections(None, None, [ada], ["read_file"]) == ""


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
    # Claude Code's search of the door's tools is the one tool of its own a run's model may have (item 1053).
    assert init_problem(_init([*own, "ToolSearch"], connected, {}), DOOR) is None
    built_in: str | None = init_problem(_init([*own, "ToolSearch", "Bash", "Read"], connected, {}), DOOR)
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
    assert done == Result(session_id="s", is_error=False, subtype="success", text="Done.", fast_off="")
    failed: object = read_line(
        json.dumps({"type": "result", "subtype": "error_during_execution", "errors": ["boom"], "session_id": "s"})
    )
    assert failed == Result(session_id="s", is_error=True, subtype="error_during_execution", text="boom", fast_off="")


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


def test_a_runs_effort_and_fast_mode_reach_the_program() -> None:
    """Item 1014: the run's reasoning level goes as --effort when it is one of Claude Code's, and fast mode as the
    --settings the run reads; neither goes when the run has none."""
    argv: tuple[str, ...] = claude_argv("claude", "opus", "xhigh", "fast", "p", CONFIG, DOOR, None)
    assert argv[argv.index("--effort") + 1] == "xhigh"
    assert json.loads(argv[argv.index("--settings") + 1]) == {"fastMode": True}
    plain: tuple[str, ...] = claude_argv("claude", "sonnet", "low", "standard", "p", CONFIG, DOOR, None)
    assert "--settings" not in plain
    # The configured default goes when the run chose none, rounded to the levels Claude Code lists for the model, as
    # the composer shows it (item 1367): Opus 4.6 has no xhigh, Haiku 4.5 no level, and then no --effort goes.
    five: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")
    assert run_effort(five, None, "medium") == "medium"
    assert run_effort(five, "minimal", "medium") == "low" and run_effort(five, "off", "high") == "low"
    assert run_effort(five, "max", "medium") == "max"
    opus_4_6 = find_model(_listed(), "claude-opus-4-6")
    haiku_4_5 = find_model(_listed(), "claude-haiku-4-5-20251001")
    assert opus_4_6 is not None and haiku_4_5 is not None
    assert run_effort(opus_4_6.efforts, None, "xhigh") in ("high", "max")
    assert run_effort(haiku_4_5.efforts, None, "xhigh") is None
    assert "--effort" not in claude_argv("claude", "haiku", None, "standard", "p", CONFIG, DOOR, None)
    # Fast mode goes to a model that has it alone, as Claude Code last listed them (item 1367).
    keep_models(PlanModels(fetched_at=0.0, models=_listed()))
    assert run_speed(ModelRef(provider="claude_code", model="sonnet"), "fast") == "standard"
    assert run_speed(ModelRef(provider="claude_code", model="opus"), "fast") == "fast"
    assert offers_fast_mode(ModelRef(provider="claude_code", model="opus"))
    assert not offers_fast_mode(ModelRef(provider="claude_code", model="sonnet"))
    fable = find_model(_listed(), "fable")
    assert fable is not None and model_info_of(fable).efforts == ("low", "medium", "high", "xhigh", "max")
    # Haiku is one of the plan's models too (item 1018), without fast mode.
    assert find_model(_listed(), "haiku") is not None
    assert not offers_fast_mode(ModelRef(provider="claude_code", model="haiku"))


def test_fast_mode_the_plan_ran_off_is_read_and_said() -> None:
    """Item 1014, measured on 2.1.318: a plan without extra usage runs fast mode off and the result says why."""
    line: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "ok",
        "session_id": "s",
        "fast_mode_state": "off",
        "fast_mode_disabled_reason": "extra_usage_disabled",
    }
    read: object = read_line(json.dumps(line))
    assert isinstance(read, Result) and read.fast_off == "extra_usage_disabled"
    assert "no extra usage" in fast_off_words("extra_usage_disabled")


def test_a_step_that_stops_for_a_tool_ends_as_dots_steps_do() -> None:
    """Item 1016, the owner's screenshot of 2026-11-07: each step that stopped to call a tool was marked "ended early:
    tool_use", as Claude names a stop for a tool; the timeline reads Dot's names, and a cut reply still shows."""
    assert dot_finish("tool_use") == "tool_calls" and dot_finish("end_turn") == "stop"
    assert dot_finish("max_tokens") == "max_tokens"
    said: str = system_prompt("Dot", DOOR, ("read_file",), Path("/w"), "2026-11-07", "", ())
    assert "in the language the owner writes in" in said


def test_an_agents_bounds_take_in_the_tools_of_the_apps_the_owner_picked() -> None:
    """Item 1022: a run on the plan of an agent whose tools are bounded gets the tools of a connected app picked for
    its message (or an earlier one of its chat), as Dot's own loop gives them (item 415), and no other app's."""
    names: list[str] = ["read_file", "mcp__notes__save", "mcp__notes__list", "mcp__mail__send"]
    opened: frozenset[str] = owner_server_groups(RunPicks(plugins=("notes",), skills=(), plan_first=False), frozenset())
    assert opened_bounds(frozenset({"read_file"}), opened, names) == {
        "read_file",
        "mcp__notes__save",
        "mcp__notes__list",
    }


def test_the_door_offers_its_tools_in_the_order_of_their_names() -> None:
    """Item 1052: the tools head the prompt, so the same tools must come in the same order, whatever order their
    servers connected in, for Anthropic's cache of that head to be read by the next chat."""
    names: tuple[str, ...] = ("web_fetch", "mcp__github__issues", "browser_open", "list_dir")
    specs: list[ToolSpec] = [ToolSpec(name=name, description=name, parameters={"type": "object"}) for name in names]
    assert [spec.name for spec in door_specs(specs)] == sorted(names)


def test_a_look_up_of_a_tool_is_not_a_call_of_dots() -> None:
    """Item 1053: a step's calls of Dot's tools are the blocks that name a tool of the door; Claude Code's search of
    them is its own and is left out, and a tool of another door is no call of this run's."""
    assert dot_tool(f"mcp__{DOOR}__list_dir", DOOR) == "list_dir"
    assert dot_tool("ToolSearch", DOOR) is None
    assert dot_tool("mcp__other__list_dir", DOOR) is None


def _listed() -> tuple[PlanModel, ...]:
    """The models Claude Code 2.1.330 listed on 2026-11-23, its answer recorded in the fixtures."""
    line: str = (Path(__file__).parent.parent / "fixtures" / "claude_models_response.json").read_text(encoding="utf-8")
    listed: tuple[PlanModel, ...] | None = models_of_line(line)
    assert listed is not None
    return listed


def test_the_plans_models_are_those_claude_code_lists() -> None:
    """Item 1367: the plan's models are read from Claude Code's own list, so a new one (Fable 5.2) shows up with its
    name, its levels and its fast mode; its "default" entry is left out, an older model without levels has none, a
    family's alias the list names by id answers by the newest of that family, and another program's line is not it."""
    listed: tuple[PlanModel, ...] = _listed()
    ids: list[str] = [model.id for model in listed]
    assert "default" not in ids and "claude-fable-5-2" in ids and "opus" in ids
    assert {model.id for model in listed if model.fast} == {
        "opus",
        "claude-fable-5-2",
        "claude-opus-5",
        "claude-opus-4-8",
    }
    haiku_4_5 = find_model(listed, "claude-haiku-4-5-20251001")
    assert haiku_4_5 is not None and haiku_4_5.efforts == () and haiku_4_5.label == "Haiku 4.5"
    fable = find_model(listed, "fable")
    assert fable is not None and fable.id == "fable" and fable.label == "Fable 5.2" and fable.fast
    assert find_model(listed, "claude-opus-4-0") is None
    assert models_of_line('{"type": "result"}') is None and models_of_line("not json") is None
    keep_models(PlanModels(fetched_at=0.0, models=listed))
    assert model_labels()["claude-fable-5-2"] == "Fable 5.2" and model_labels()["fable"] == "Fable 5.2"


def test_the_tools_a_run_lists_are_the_names_dot_gave_them_whatever_else_the_line_holds() -> None:
    """Item 1400: what the program was given from the bridge, read from its first line by the door's prefix."""
    init: Init = _init(
        ["ToolSearch", f"mcp__{DOOR}__finish", f"mcp__{DOOR}__notify", "mcp__dot_99999999__shell"],
        [{"name": DOOR, "status": "connected"}],
        {},
    )
    assert listed_tools(init, DOOR) == {"finish", "notify"}
    assert listed_tools(_init([], [{"name": DOOR, "status": "failed"}], {}), DOOR) == frozenset()


def test_a_goal_that_asks_for_a_notification_is_told_in_the_owners_three_languages() -> None:
    for goal in (
        "Send me a notification that the course starts at 16:00.",
        "Send a push notification at 15:30: the course starts at 16:00.",
        "Send the owner a notification when the build is done.",
        "Send me notifications when the build is done.",
        "Send a notification to me at 15:30.",
        "Send me a notification to remind me that the course starts at 16:00.",
        "Send me a notification to my phone at 15:30.",
        "Send me a notification for the dentist appointment at 15:00.",
        "Send the owner a notification for the build.",
        "Show me a notification when it is time.",
        "Show a macOS notification with the title Course.",
        "Display a notification to the user at 15:30.",
        "Notify me when the build is done.",
        "Use notify to tell me the course starts.",
        "Gönder: kurs 16:00'da, bana bildirim gönder.",
        "Saat 15:30'da bildirim at.",
        "Saat 15:30'da bana bildir.",
        "Lähetä ilmoitus puhelimeeni kello 15.30.",
        "Lähetä minulle ilmoitus kun kurssi alkaa.",
        "Ilmoita minulle kello 15.30.",
    ):
        assert goal_asks_notification(goal), goal


def test_a_goal_that_only_mentions_a_notification_or_refuses_one_asks_for_none() -> None:
    """Item 1400's review: a goal that merely names a notification (a screen to review, a job ad, a sale listing), names
    a thing that is none (an email, a badge), or says not to send one, must not make a run fail for want of ``notify``,
    in any of the owner's three languages."""
    for goal in (
        "Review the push notification settings screen.",
        "Explain how notifications work on macOS.",
        "Write the notification text for the release.",
        "Show the notification settings to the team.",
        "Display a notification badge on the app icon.",
        "Send a notification email to the leads.",
        "I was notified twice already; do not notify again.",
        "Do not send a notification before 08:00.",
        "Don't send me a notification, just write the report.",
        "Never show me a notification for this task.",
        "Bildirim ekranını incele.",
        "Bildirim verilerini ve yapılandırmasını kontrol et.",
        "Bildirim gönderme, sadece raporu yaz.",
        "Bana bildirme, sadece raporu yaz.",
        "Lue tämä ilmoitus ja tee hakemus.",
        "Tarkista myynti-ilmoitus ja vastaa siihen.",
        "Älä lähetä ilmoitusta, kirjoita vain raportti.",
        "Remind me about the course at 16:00.",
        "Register for the course (ilmoittautua).",
        "",
    ):
        assert not goal_asks_notification(goal), goal


def test_a_run_needs_finish_where_its_mode_has_it_and_notify_when_a_schedules_goal_asks_for_one() -> None:
    """Item 1400: two tools and no other rule. A run that calls no tool is not a failed run: a goal that is only an
    answer needs neither, a chat, which ends with its last reply, has no finish to need, and a run the owner watches is
    not held to ``notify``: they read its answer."""
    task_tools: tuple[str, ...] = ("finish", "notify", "read_file")
    chat_tools: tuple[str, ...] = ("notify", "read_file")
    ask: str = "Send me a notification at 15:30."
    assert tools_needed(task_tools, "Write the weekly report.", "schedule") == ("finish",)
    assert tools_needed(task_tools, ask, "schedule") == ("finish", "notify")
    assert tools_needed(task_tools, ask, "ui") == ("finish",)
    assert tools_needed(task_tools, ask, "cli") == ("finish",)
    assert tools_needed(chat_tools, "How are you?", "schedule") == ()
    assert tools_needed(chat_tools, "Notify me at 15:30.", "schedule") == ("notify",)
    assert tools_needed((), "Notify me at 15:30.", "schedule") == ("notify",)


def test_the_tools_a_run_lacks_are_those_needed_that_are_not_there() -> None:
    assert missing_tools(("finish", "notify"), ["finish", "notify", "shell"]) == ()
    assert missing_tools(("finish", "notify"), iter(["finish", "shell"])) == ("notify",)
    assert missing_tools(("finish", "notify"), []) == ("finish", "notify")
    assert missing_tools((), []) == ()


def test_a_run_short_of_a_tool_says_which_and_why_and_a_bridge_that_came_up_short_is_tried_again() -> None:
    """Item 1400: the words end up as the run's result, on the schedule's History and in the notice of its end. Dot's
    own list leaving a tool out is no passing fault (the agent or Settings must change: no try again); the program's
    line not listing a tool Dot offered is (a schedule tries its task once more)."""
    agent: ClaudeCodeError = tools_not_given(("notify",), "task", "Ada", {"run_id": "run_1"})
    assert not isinstance(agent, ClaudeCodeBroke) and failure_kind(agent) == "lasting"
    assert agent.message.startswith(
        "Dot ended this task before it began: it needs Dot's notify tool, and this run was not"
    )
    assert (
        "The tools of the agent Ada are a list of their own that leaves it out: add it to the agent's tools"
        in agent.message
    )
    assert agent.context["needed_missing"] == ["notify"] and agent.context["why"] == "not_given"
    mode: ClaudeCodeError = tools_not_given(("finish", "notify"), "chat", None, {"run_id": "run_1"})
    assert "needs Dot's finish and notify tools" in mode.message and "leaves them out" not in mode.message
    assert "do not include them" in mode.message
    bridge: ClaudeCodeBroke = tools_not_listed(("notify",), ["shell", "finish"], {"run_id": "run_1"})
    assert failure_kind(bridge) == "passing"
    assert (
        "Claude Code did not list it among the tools Dot's bridge gave it (it listed: finish, shell)" in bridge.message
    )
    assert tools_not_listed(("finish",), [], {}).message.count("it listed: none") == 1
