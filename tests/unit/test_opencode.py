"""Item 283's data: OpenCode's event stream as a run reads it (recorded from OpenCode 1.19.0 on one of Zen's free
models, for a command whose permission the agent asked for, with an event of another session put in), Zen's free
models as ``opencode models opencode --verbose`` prints them, a permission as Dot's policy weighs it, and the
environment of the server Dot starts. Item 284's: the tools of Dot's the agent gets, each chat's door and the rules
that keep a session to its own, and a result of Dot's tool as an MCP result."""

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import JsonValue

from dotagent.agent.authorization import StepDecision
from dotagent.core.types import JsonObject
from dotagent.llm.types import ImageData, assistant_message, user_message
from dotagent.opencode.bridge import door_name, mcp_result, session_rules
from dotagent.opencode.events import (
    MessageSeen,
    PartSeen,
    PermissionAsked,
    Piece,
    SessionEvent,
    SessionIdle,
    StepEnded,
    ToolSeen,
    read_event,
)
from dotagent.opencode.run import (
    FREE_TIER_REFUSED,
    _Call,
    agent_failed,
    bridged_tools,
    local_target,
    meanwhile,
    permission_action,
    step_result,
)
from dotagent.opencode.server import PERMISSIONS, FreeModel, free_models, server_environment
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ToolResult
from dotagent.tools.browser import build_browser_tools
from dotagent.tools.delegate import build_delegate_tools
from dotagent.tools.files import build_file_tools
from dotagent.tools.memory import build_memory_tools
from dotagent.tools.meta import build_meta_tools
from dotagent.tools.registry import ToolRegistry, build_registry
from dotagent.tools.schedule import build_schedule_tools
from dotagent.tools.shell import build_shell_tool
from dotagent.tools.terminal import build_terminal_tools
from dotagent.tools.web import build_web_tools

_STREAM: Path = Path(__file__).parents[1] / "fixtures" / "opencode_events.jsonl"
_SESSION: str = "ses_ec014f256ffeJqrkLkfivVnEKB"
_CALL: str = "call_5228678cf56f47098290bba6"


def _events() -> list[SessionEvent]:
    lines: list[str] = _STREAM.read_text(encoding="utf-8").splitlines()
    read: list[SessionEvent | None] = [read_event(json.loads(line), _SESSION) for line in lines]
    return [event for event in read if event is not None]


def _asked(permission: str, patterns: tuple[str, ...], metadata: dict[str, str]) -> PermissionAsked:
    return PermissionAsked(
        request_id="per_1", permission=permission, patterns=patterns, metadata=dict(metadata), call_id="call_1"
    )


def test_a_recorded_stream_reads_as_two_steps_around_a_permission() -> None:
    events: list[SessionEvent] = _events()
    assert [event for event in events if isinstance(event, PermissionAsked)] == [
        PermissionAsked(
            request_id="per_13feb2492001ROaL5qGWlgEwuQ",
            permission="bash",
            patterns=("ls -1",),
            metadata={"command": "ls -1"},
            call_id=_CALL,
        )
    ]
    calls: list[ToolSeen] = [event for event in events if isinstance(event, ToolSeen)]
    assert {(call.call_id, call.tool) for call in calls} == {(_CALL, "bash")}
    assert (calls[-1].status, calls[-1].output) == ("completed", "alpha.txt\nbeta.txt\ngamma.md\n")
    steps: list[StepEnded] = [event for event in events if isinstance(event, StepEnded)]
    # What a step read counts what came from the cache, and what it wrote counts its reasoning.
    assert [(step.input_tokens, step.output_tokens, step.cached_tokens, step.reason) for step in steps] == [
        (8084, 49, 6144, "tool-calls"),
        (8149, 21, 8064, "stop"),
    ]
    roles: set[tuple[str, str]] = {(event.message_id, event.role) for event in events if isinstance(event, MessageSeen)}
    assert {role for _, role in roles} == {"user", "assistant"}
    answer: list[PartSeen] = [
        event
        for event in events
        if isinstance(event, PartSeen) and event.kind == "text" and event.message_id == steps[-1].message_id
    ]
    assert answer[-1].text.startswith("It printed 3 entries")
    assert "not this run's" not in "".join(event.text for event in events if isinstance(event, Piece))
    assert isinstance(events[-1], SessionIdle)


def test_the_free_models_are_zens_own_that_cost_nothing() -> None:
    free: dict[str, object] = {"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}
    models: dict[str, dict[str, object]] = {
        "space-bunny-free": {
            "cost": free,
            "status": "active",
            "capabilities": {"toolcall": True, "reasoning": True, "input": {"image": True}},
            "limit": {"context": 1048576},
            "variants": {"low": {}, "high": {}, "max": {}, "turbo": {}},
        },
        "big-pickle": {
            "name": "Big Pickle",
            "cost": free,
            "status": "beta",
            "capabilities": {"toolcall": True, "reasoning": True, "input": {"image": False}},
            "limit": {"context": 200000},
            "variants": {},
        },
        "priced": {"cost": {"input": 1.25, "output": 10}},
        "half-priced": {"cost": {"input": 0, "output": 0.5}},
        "unpriced": {},
        "gone-free": {"cost": free, "status": "deprecated"},
    }
    listing: str = "".join(f"opencode/{name}\n{json.dumps(model, indent=2)}\n" for name, model in models.items())
    assert free_models(listing) == (
        FreeModel(
            id="big-pickle",
            name="Big Pickle",
            vision=False,
            tools=True,
            thinking=True,
            context_length=200000,
            efforts=(),
        ),
        FreeModel(
            id="space-bunny-free",
            name="space-bunny-free",
            vision=True,
            tools=True,
            thinking=True,
            context_length=1048576,
            efforts=("low", "high", "max"),
        ),
    )


def test_a_permission_is_weighed_as_the_same_action_of_dots_own_tools(tmp_path: Path) -> None:
    workspace: Path = tmp_path / "workspace"
    secrets: Path = tmp_path / "secrets"
    edit = permission_action(_asked("edit", ("notes.md",), {"filepath": "notes.md"}), workspace, (secrets,), ())
    assert (edit.tool, edit.kind, edit.risk, edit.flags, edit.target) == (
        "opencode_edit",
        "write",
        "low",
        (),
        str(workspace / "notes.md"),
    )
    elsewhere = permission_action(_asked("edit", (), {"filepath": str(secrets / "key")}), workspace, (secrets,), ())
    assert (elsewhere.risk, elsewhere.flags) == ("high", ("secret_access",))
    # A change of Dot's own files, which Dot's file tools make once the user agrees (item 398), stays refused to
    # OpenCode's agent outside yolo (item 433): it writes past their undo journal.
    own: Path = tmp_path / "Dot" / "src"
    into_dot = permission_action(_asked("edit", (), {"filepath": str(own / "app.css")}), workspace, (), (own,))
    assert (into_dot.risk, into_dot.flags) == ("high", ("secret_access",))
    read = permission_action(_asked("read", (str(secrets / "key"),), {}), workspace, (secrets,), ())
    assert (read.kind, read.risk, read.flags) == ("read", "safe", ("secret_access",))
    command = permission_action(_asked("bash", ("ls -1",), {"command": "ls -1"}), workspace, (), ())
    assert (command.tool, command.kind, command.target) == ("opencode_bash", "shell", "ls -1")
    fetch = permission_action(
        _asked("webfetch", ("https://example.com",), {"url": "https://example.com"}), workspace, (), ()
    )
    assert (fetch.kind, fetch.target, fetch.flags) == ("network", "https://example.com", ())
    # A fetch whose address could carry data out is flagged as Dot's own web_fetch is (the code review's Y1).
    long_url: str = "https://example.com/?q=" + "a" * 2100
    carrying = permission_action(_asked("webfetch", (long_url,), {"url": long_url}), workspace, (), ())
    assert carrying.flags == ("exfiltration",)
    unknown = permission_action(_asked("teleport", ("mars",), {}), workspace, (), ())
    assert (unknown.tool, unknown.risk) == ("opencode_teleport", "high")


def test_a_new_session_hears_what_the_chat_said_meanwhile() -> None:
    assert meanwhile("Go on.", []) == "Go on."
    said: str = meanwhile("Go on.", [user_message("Plan a trip.", ()), assistant_message("To where?", None, ())])
    assert said == (
        "Earlier in this conversation, with another assistant:\n\nuser: Plan a trip.\n\nassistant: To where?\n\n"
        "Now:\n\nGo on."
    )


def test_the_server_gets_no_key_of_the_login_environment(tmp_path: Path) -> None:
    login: dict[str, str] = {
        "PATH": "/opt/tools/bin:/usr/bin",
        "HOME": "/Users/owner",
        "LANG": "en_US.UTF-8",
        "OPENAI_API_KEY": "sk-not-for-opencode",
        "GITHUB_TOKEN": "not-for-opencode",
    }
    env: dict[str, str] = server_environment(login, tmp_path)
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert (env["HOME"], env["LANG"]) == ("/Users/owner", "en_US.UTF-8")
    assert env["PATH"].startswith("/opt/tools/bin:/usr/bin")
    assert all(
        env[name].startswith(str(tmp_path)) for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "OPENCODE_TEST_HOME")
    )
    assert json.loads(env["OPENCODE_PERMISSION"]) == PERMISSIONS
    # Every tool stays in what the agent sends the model; the ones that act ask first.
    assert "deny" not in PERMISSIONS.values() and "*" not in PERMISSIONS
    assert (PERMISSIONS["bash"], PERMISSIONS["edit"], PERMISSIONS["task"]) == ("ask", "ask", "ask")


def test_zens_free_tier_refusal_is_said_as_what_it_is() -> None:
    refused: dict[str, object] = {
        "name": "APIError",
        "data": {
            "message": "OpenCode's free tier can only be used from within OpenCode.",
            "statusCode": 403,
            "isRetryable": False,
            "responseBody": '{"type":"error","error":{"type":"FreeTierError","message":"OpenCode\'s free tier can only'
            ' be used from within OpenCode."}}',
        },
    }
    assert agent_failed("OpenCode's free tier can only be used from within OpenCode.", refused).message == (
        FREE_TIER_REFUSED
    )
    other: dict[str, object] = {"name": "APIError", "data": {"message": "Overloaded", "responseBody": "Overloaded"}}
    assert agent_failed("Overloaded", other).message == "OpenCode's agent failed: Overloaded"


def _registry() -> ToolRegistry:
    return build_registry(
        (
            build_file_tools(),
            (build_shell_tool(),),
            build_web_tools(),
            build_browser_tools(),
            build_memory_tools(),
            build_schedule_tools(),
            build_delegate_tools(),
            build_meta_tools(),
            build_terminal_tools(),
        )
    )


def test_the_agent_gets_dots_tools_for_what_opencode_has_none_of() -> None:
    names: set[str] = set(bridged_tools(_registry(), "task", None).tools)
    assert {"browser_open", "browser_act", "memory_save", "memory_search", "memory_forget", "schedule_create"} <= names
    assert {"ask_user", "notify"} <= names
    # OpenCode's agent has its own for files, commands and the web; the plan, finish, tool groups and helpers are Dot's
    # loop's.
    left_out: set[str] = {"read_file", "write_file", "shell", "web_fetch", "web_search", "terminal_send", "finish"}
    assert not names & (left_out | {"update_plan", "use_tools", "delegate"})


def test_an_agent_bounded_to_some_tools_gives_only_those() -> None:
    bounded: frozenset[str] = frozenset({"browser_open", "finish", "ask_user", "read_file"})
    assert set(bridged_tools(_registry(), "task", bounded).tools) == {"browser_open", "ask_user"}


def _decided(tool: str, rules: list[JsonValue]) -> str:
    """The action OpenCode takes for ``tool``: that of the last rule whose permission matches it."""
    for rule in reversed(rules):
        assert isinstance(rule, dict)
        permission: JsonValue = rule["permission"]
        action: JsonValue = rule["action"]
        assert isinstance(permission, str) and isinstance(action, str)
        if fnmatchcase(tool, permission):
            return action
    return "unmatched"


def test_each_chat_has_its_own_door_and_its_session_keeps_to_it() -> None:
    first: str = door_name("thread-a")
    other: str = door_name("thread-b")
    assert first == door_name("thread-a") != other
    assert re.fullmatch(r"dot_[0-9a-f]{8}", first)
    rules: list[JsonValue] = session_rules(first)
    assert _decided(f"{first}_browser_open", rules) == "allow"
    assert _decided(f"{other}_browser_open", rules) == "deny"
    # OpenCode's own tools are left to the agent's rules.
    assert _decided("bash", rules) == "unmatched"


def test_a_result_of_dots_tool_goes_back_with_its_pictures_and_its_failure() -> None:
    picture: ImageData = ImageData(media_type="image/png", data_b64="iVBORw0KGgo=")
    result: CallToolResult = mcp_result(ToolResult(text="no such page", images=(picture,), is_error=True))
    assert result.is_error
    text, image = result.content
    assert isinstance(text, TextContent) and text.text == "no such page"
    assert isinstance(image, ImageContent) and image.data == picture.data_b64 and image.mime_type == "image/png"


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:8765/", True),
        ("http://127\u30020\u30020\u30021:8765/", True),
        ("http://2852039166/latest/meta-data/", True),
        ("http://10.0.0.1/admin", True),
        ("http://[::ffff:192.168.1.1]/", True),
        ("https://example.com/", False),
        ("https://93.184.215.14/", False),
    ],
)
def test_a_fetch_of_this_mac_or_its_network_is_seen_in_any_spelling(url: str, local: bool) -> None:
    """The code review's Y1: OpenCode's webfetch reached addresses Dot's own web tools refuse."""
    assert local_target(url) is local


def test_a_call_keeps_what_decided_it_as_a_call_of_dots_own_does() -> None:
    """Item 474: the result of a call the gate let through keeps what decided it and its action's flags; one the gate
    stopped keeps no decision, its flags still; one that never asked keeps OpenCode's title and no action."""
    action: ActionInfo = ActionInfo(
        tool="bash", kind="shell", risk="medium", summary="Run: git push", target="git push", flags=("external_send",)
    )
    decided: StepDecision = StepDecision(
        verdict="ask",
        source="preset",
        reason="the preset asks",
        rule_id=None,
        answered_by="user",
        preset="balanced",
        tainted=True,
    )
    seen: ToolSeen = ToolSeen(
        call_id="call-1",
        message_id="message-1",
        tool="bash",
        status="completed",
        input={},
        output="pushed",
        error="",
        title="git push",
        started_ms=1_000,
        ended_ms=2_500,
    )
    let_through: JsonObject = step_result(
        seen,
        "opencode_bash",
        _Call(announced=True, action=action, waited_s=1.25, decision=decided),
        "pushed",
        1.5,
        True,
    )
    assert (let_through["decision"], let_through["flags"], let_through["summary"], let_through["kind"]) == (
        decided.model_dump(mode="json"),
        ["external_send"],
        "Run: git push",
        "shell",
    )
    assert (let_through["ok"], let_through["waited_s"], let_through["tainted"]) == (True, 1.25, True)
    refused: ToolSeen = seen.model_copy(update={"status": "error", "error": "refused"})
    stopped: JsonObject = step_result(
        refused, "opencode_bash", _Call(announced=True, action=action), "refused", 0.0, False
    )
    assert (stopped["ok"], stopped["decision"], stopped["flags"]) == (False, None, ["external_send"])
    unasked: JsonObject = step_result(seen, "opencode_read", _Call(announced=True), "text", 0.2, False)
    assert (unasked["summary"], unasked["kind"], unasked["decision"], unasked["flags"]) == ("git push", "", None, [])
