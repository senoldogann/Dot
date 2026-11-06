"""What Claude Code says on its output while a run works in it (item 1013), read line by line: ``claude -p
--output-format stream-json --include-partial-messages --verbose`` writes one JSON object a line. Everything here is
pure: :func:`read_line` makes a line one of the events below (``None`` for a line the run has no use for), and the
checks the run makes of them are functions of their own.

- ``system``/``init`` (:class:`Init`): the session, and what the model has: its tools, its MCP servers, its slash
  commands, skills and plugins. A run's must list the door's tools alone (:func:`init_problem`).
- ``stream_event``: the model's reply as it comes, Anthropic's own stream events: ``message_start``
  (:class:`StepBegan`, with the tokens read), ``content_block_start`` (:class:`BlockBegan`: text, thinking or a tool's
  call), ``content_block_delta`` (:class:`Delta`, text or thinking), ``message_delta`` (:class:`StepFinished`: why it
  stopped and the tokens written) and ``message_stop`` (:class:`StepClosed`).
- ``rate_limit_event`` (:class:`LimitSeen`): the plan's window, how much of it is used and when it resets.
- ``result`` (:class:`Result`): the end of a turn, its answer or its error."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from dotagent.core.jsonutil import dumps_compact
from dotagent.core.types import JsonObject
from dotagent.llm.types import ImageData
from dotagent.repair.claude import LimitWindow, limit_of

type BlockKind = Literal["text", "thinking", "tool"]

# The start of the name Claude Code gives each tool of an MCP server: ``mcp__<server>__<tool>``.
MCP_TOOL_PREFIX: str = "mcp__"
# The words of a result that says the plan's limit stopped the turn ("Claude AI usage limit reached|<epoch>", "5-hour
# limit reached ∙ resets 3pm", "You've hit your limit").
_LIMIT_WORDS: re.Pattern[str] = re.compile(r"usage limit|limit reached|hit your limit|rate limit", re.IGNORECASE)
# The reset an older limit error carries after a bar, in epoch seconds.
_LIMIT_EPOCH: re.Pattern[str] = re.compile(r"\|(\d{9,11})\b")


@dataclass(frozen=True)
class McpServer:
    name: str
    status: str


@dataclass(frozen=True)
class Init:
    """The ``system``/``init`` line: the session, and what the model was given."""

    session_id: str
    tools: tuple[str, ...]
    mcp_servers: tuple[McpServer, ...]
    slash_commands: tuple[str, ...]
    skills: tuple[str, ...]
    plugins: tuple[str, ...]


@dataclass(frozen=True)
class StepBegan:
    """A reply of the model began: the tokens it read (``cached_tokens`` of them from the cache)."""

    input_tokens: int
    cached_tokens: int


@dataclass(frozen=True)
class BlockBegan:
    """A block of the reply began: text, thinking or a call of the tool ``tool``."""

    index: int
    kind: BlockKind
    tool: str


@dataclass(frozen=True)
class Delta:
    """Words of a block of text or thinking, as they come."""

    index: int
    kind: Literal["text", "thinking"]
    text: str


@dataclass(frozen=True)
class StepFinished:
    """Why the reply stopped (``end_turn``, ``tool_use``, ``max_tokens``...), and the tokens it wrote."""

    stop_reason: str
    output_tokens: int


@dataclass(frozen=True)
class StepClosed:
    """The reply ended."""


@dataclass(frozen=True)
class LimitSeen:
    window: LimitWindow


@dataclass(frozen=True)
class Result:
    """The end of a turn: its answer, or, ``is_error``, why it failed (``subtype`` says the kind)."""

    session_id: str
    is_error: bool
    subtype: str
    text: str


@dataclass(frozen=True)
class Taken:
    """A message of Dot's the program took in (``--replay-user-messages``): into a turn of its own, or folded into the
    turn at work (measured on 2.1.318: a message sent while a tool worked was taken into that turn, and one result
    answered both)."""


type ClaudeEvent = Init | StepBegan | BlockBegan | Delta | StepFinished | StepClosed | LimitSeen | Result | Taken


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# The plugins the program comes with, which every install has and no flag turns off (`--safe-mode` lists them too):
# measured on 2.1.318, with `--tools ""` and `--setting-sources ""` they gave the model no tool, slash command or
# skill, and no CLAUDE.md or AGENTS.md reached it (item 1013's first real run). Only these pass, by name: one a later
# version brings (it could carry hooks or skills) ends the run until it is measured and added here.
_BUILT_IN: str = "builtin"
MEASURED_BUILT_INS: frozenset[str] = frozenset(
    {"cc-plugin-sec-default", "cc-plugin-agents-md", "cc-plugin-privacy", "cc-plugin-plugin-authoring"}
)


def _added_plugins(value: object) -> tuple[str, ...]:
    """The plugins of the init line but the measured ones the program comes with (:data:`MEASURED_BUILT_INS`): one the
    owner or a project installed, or a built-in of a later version, could bring hooks, agents or skills of its own and
    so ends the run (:func:`init_problem`)."""
    if not isinstance(value, list):
        return ()
    added: list[object] = [
        item
        for item in value
        if not (
            isinstance(item, dict)
            and item.get("path") == _BUILT_IN
            and _str(item.get("name")) in MEASURED_BUILT_INS
            and _str(item.get("source")) == f"{_str(item.get('name'))}@{_BUILT_IN}"
        )
    ]
    return _names(added)


def _names(value: object) -> tuple[str, ...]:
    """The names of a list of the init line: each a name, or an object with one."""
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            names.append(_str(item.get("name")) or json.dumps(item, sort_keys=True))
        else:
            names.append(repr(item))
    return tuple(names)


def _servers(value: object) -> tuple[McpServer, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        McpServer(name=_str(item.get("name")), status=_str(item.get("status")))
        if isinstance(item, dict)
        else McpServer(name=repr(item), status="")
        for item in value
    )


def _stream_event(event: dict[str, object]) -> ClaudeEvent | None:
    kind: object = event.get("type")
    if kind == "message_start":
        message: object = event.get("message")
        usage: object = message.get("usage") if isinstance(message, dict) else None
        tokens: dict[str, object] = usage if isinstance(usage, dict) else {}
        cached: int = _int(tokens.get("cache_read_input_tokens"))
        read: int = _int(tokens.get("input_tokens")) + _int(tokens.get("cache_creation_input_tokens")) + cached
        return StepBegan(input_tokens=read, cached_tokens=cached)
    if kind == "content_block_start":
        block: object = event.get("content_block")
        if not isinstance(block, dict):
            return None
        index: int = _int(event.get("index"))
        match block.get("type"):
            case "text":
                return BlockBegan(index=index, kind="text", tool="")
            case "thinking" | "redacted_thinking":
                return BlockBegan(index=index, kind="thinking", tool="")
            case "tool_use" | "server_tool_use":
                return BlockBegan(index=index, kind="tool", tool=_str(block.get("name")))
        return None
    if kind == "content_block_delta":
        delta: object = event.get("delta")
        if not isinstance(delta, dict):
            return None
        index = _int(event.get("index"))
        match delta.get("type"):
            case "text_delta":
                return Delta(index=index, kind="text", text=_str(delta.get("text")))
            case "thinking_delta":
                return Delta(index=index, kind="thinking", text=_str(delta.get("thinking")))
        return None
    if kind == "message_delta":
        said: object = event.get("delta")
        usage = event.get("usage")
        stop: str = _str(said.get("stop_reason")) if isinstance(said, dict) else ""
        return StepFinished(
            stop_reason=stop, output_tokens=_int(usage.get("output_tokens")) if isinstance(usage, dict) else 0
        )
    if kind == "message_stop":
        return StepClosed()
    return None


def read_line(line: str) -> ClaudeEvent | None:
    """The event a line of the stream is, or ``None`` for one the run has no use for (a whole ``assistant`` message,
    which the stream events said already, a tool's result, which the bridge recorded, or a line that is no JSON
    object: the program may write a warning among them)."""
    try:
        item: object = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(item, dict):
        return None
    match item.get("type"):
        case "system" if item.get("subtype") == "init":
            return Init(
                session_id=_str(item.get("session_id")),
                tools=_names(item.get("tools")),
                mcp_servers=_servers(item.get("mcp_servers")),
                slash_commands=_names(item.get("slash_commands")),
                skills=_names(item.get("skills")),
                plugins=_added_plugins(item.get("plugins")),
            )
        case "stream_event":
            event: object = item.get("event")
            # A subagent's events carry the call that started it; a run has no tool that starts one.
            if not isinstance(event, dict) or item.get("parent_tool_use_id") is not None:
                return None
            return _stream_event(event)
        case "user" if item.get("isReplay") is True:
            return Taken()
        case "rate_limit_event":
            window: LimitWindow | None = limit_of(item.get("rate_limit_info"))
            return None if window is None else LimitSeen(window=window)
        case "result":
            return Result(
                session_id=_str(item.get("session_id")),
                is_error=item.get("is_error") is not False,
                subtype=_str(item.get("subtype")),
                text=_str(item.get("result")) or ", ".join(_names(item.get("errors"))),
            )
    return None


def tool_prefix(door: str) -> str:
    """What the names of the door ``door``'s tools start with in Claude Code: ``mcp__dot_1a2b3c4d__``."""
    return f"{MCP_TOOL_PREFIX}{door}__"


def init_problem(init: Init, door: str) -> str | None:
    """What is wrong with what the model was given, ``None`` when it has the tools of the door ``door`` and nothing
    else: no tool of Claude Code's own nor of another MCP server, no MCP server but the door, connected, no slash
    command, no skill and no plugin."""
    prefix: str = tool_prefix(door)
    problems: list[str] = []
    others: list[str] = [tool for tool in init.tools if not tool.startswith(prefix)]
    if others:
        problems.append(f"tools that are not Dot's: {', '.join(others)}")
    servers: list[str] = [server.name for server in init.mcp_servers if server.name != door]
    if servers:
        problems.append(f"MCP servers that are not Dot's: {', '.join(servers)}")
    own: list[McpServer] = [server for server in init.mcp_servers if server.name == door]
    if not own or own[0].status != "connected":
        status: str = own[0].status if own else "missing"
        problems.append(f"Dot's own tools did not reach it (their server is {status})")
    if init.slash_commands:
        problems.append(f"slash commands: {', '.join(init.slash_commands)}")
    if init.skills:
        problems.append(f"skills: {', '.join(init.skills)}")
    if init.plugins:
        problems.append(f"plugins: {', '.join(init.plugins)}")
    return "; ".join(problems) if problems else None


def limit_said(text: str) -> bool:
    """Whether a turn's error says the plan's limit stopped it."""
    return _LIMIT_WORDS.search(text) is not None


def limit_resets(window: LimitWindow | None, text: str) -> float | None:
    """When the plan's window resets (epoch seconds): as its last ``rate_limit_event`` said, else as the error's words
    carry it, else unknown."""
    if window is not None and window.resets_at is not None:
        return window.resets_at
    found: re.Match[str] | None = _LIMIT_EPOCH.search(text)
    return None if found is None else float(found.group(1))


def limit_message(window: LimitWindow | None, resets_at: float | None) -> str:
    """What a run the plan's limit stopped says: which window, when it resets, and that nothing switched model."""
    kind: str = "" if window is None or not window.kind else f" ({window.kind.replace('_', ' ')} window)"
    when: str = (
        "when it resets is not known"
        if resets_at is None
        else f"it resets at {datetime.fromtimestamp(resets_at).astimezone().strftime('%Y-%m-%d %H:%M %Z')}"
    )
    return (
        f"Your Claude plan's usage limit is reached{kind}; {when}. Dot does not move the task to another model on its "
        "own: send it again after the reset, or on another model"
    )


def user_line(text: str, images: tuple[ImageData, ...]) -> str:
    """A message of the owner's as a line of Claude Code's input (``--input-format stream-json``): its words, then its
    pictures."""
    content: list[JsonObject] = [{"type": "text", "text": text}]
    content.extend(
        {"type": "image", "source": {"type": "base64", "media_type": image.media_type, "data": image.data_b64}}
        for image in images
    )
    line: JsonObject = {
        "type": "user",
        "session_id": "",
        "parent_tool_use_id": None,
        "message": {"role": "user", "content": list(content)},
    }
    return dumps_compact(line) + "\n"
