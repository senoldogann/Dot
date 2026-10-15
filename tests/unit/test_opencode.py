"""Item 283's data: OpenCode's event stream as a run reads it (recorded from OpenCode 1.19.0 on one of Zen's free models,
for a command whose permission the agent asked for, with an event of another session put in), Zen's free models in
OpenCode's list of providers, a permission as Dot's policy weighs it, and the environment of the server Dot starts."""

import json
from pathlib import Path

from dotagent.llm.types import assistant_message, user_message
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
from dotagent.opencode.run import FREE_TIER_REFUSED, agent_failed, meanwhile, permission_action
from dotagent.opencode.server import PERMISSIONS, FreeModel, free_models, server_environment

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
    free: dict[str, object] = {"input": 0, "output": 0}
    listing: dict[str, object] = {
        "all": [
            {"id": "elsewhere", "models": {"also-free": {"cost": free}}},
            {
                "id": "opencode",
                "models": {
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
                },
            },
        ]
    }
    assert free_models(json.loads(json.dumps(listing))) == (
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
    read = permission_action(_asked("read", (str(secrets / "key"),), {}), workspace, (secrets,), ())
    assert (read.kind, read.risk, read.flags) == ("read", "safe", ("secret_access",))
    command = permission_action(_asked("bash", ("ls -1",), {"command": "ls -1"}), workspace, (), ())
    assert (command.tool, command.kind, command.target) == ("opencode_bash", "shell", "ls -1")
    fetch = permission_action(
        _asked("webfetch", ("https://example.com",), {"url": "https://example.com"}), workspace, (), ()
    )
    assert (fetch.kind, fetch.target) == ("network", "https://example.com")
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
