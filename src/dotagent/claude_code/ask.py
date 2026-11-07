"""A single call of Dot's on the owner's Claude plan (item 1017): a question the review model's roles ask (the harness's
proposer and composer among them), answered by ``claude -p`` in one go, with no tool of the program's own, of Dot's or
of an MCP server. A question that gives the model a tool to answer by has its parameters as the program's
``--json-schema``: the program's ``StructuredOutput`` is then the one tool the model has, and what it gives is the
reply's call of the question's tool. A question with no tool is answered in text.

The program runs as a run's does (:mod:`dotagent.claude_code.run`): its environment without an API key (the plan is
billed) nor any secret-named variable or Dot's own (:func:`dotagent.claude_code.env.claude_env`), no settings file
(``--setting-sources ""``: no hook, no CLAUDE.md), no MCP server, no slash command, a permission mode that refuses all,
in a private folder of its own that is removed as the call ends, with nothing kept of the session. The system prompt is
in a 0600 file there and the question on the program's input, never in its arguments, which ``ps`` shows every process
of the owner. Its ``system``/``init`` line must list nothing but ``StructuredOutput`` (or nothing), else the call fails.
A conversation, a picture or more than one tool is not a single call of this kind and is refused.

Everything here is pure but :func:`ask_claude`, the adapter that runs the program."""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import tempfile
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from dotagent.claude_code.env import claude_env
from dotagent.claude_code.stream import MEASURED_BUILT_INS, limit_message, limit_resets, limit_said
from dotagent.core.errors import (
    ClaudeCodeError,
    ProviderProtocolError,
    ProviderRefusedError,
    ProviderTransportError,
    ProviderWindowError,
)
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.paths import claude_code_dir
from dotagent.core.types import JsonObject
from dotagent.llm.types import ChatRequest, Effort, FinishReason, ModelReply, ToolCall, ToolSpec, Usage
from dotagent.repair.claude import LimitWindow, limit_of, window_refused

# The one tool the program gives the model when it is to answer in the shape of a JSON schema.
STRUCTURED_TOOL: str = "StructuredOutput"
_BUILT_IN: str = "builtin"
_PRIVATE: int = 0o700
_STDERR_KEPT: int = 2000
# The status a refusal of the plan's window is given, as a provider's HTTP 429 would be.
_WINDOW_STATUS: int = 429


@dataclass(frozen=True)
class Question:
    """What a single call asks: the system prompt, the question, and the tool to answer by (``None``: text)."""

    system: str
    text: str
    tool: ToolSpec | None


@dataclass(frozen=True)
class Answer:
    """What the program said of a call: its tools (from its init line), the plan's window, and its result line."""

    tools: tuple[str, ...]
    servers: tuple[str, ...]
    commands: tuple[str, ...]
    skills: tuple[str, ...]
    plugins: tuple[str, ...]
    window: LimitWindow | None
    result: JsonObject | None


def question_of(request: ChatRequest) -> Question:
    """The single call ``request`` is: its system messages, one question of the owner's side, and one tool at most.
    Raises :class:`ProviderRefusedError` for anything else (a conversation, a picture, a tool's result, several tools),
    which only a run on the plan, whole, can have."""
    systems: list[str] = [message.content for message in request.messages if message.role == "system"]
    others = [message for message in request.messages if message.role != "system"]
    single: bool = (
        len(others) == 1
        and others[0].role == "user"
        and not others[0].images
        and not others[0].tool_calls
        and len(request.tools) <= 1
    )
    if not single:
        raise ProviderRefusedError(
            "A model of your Claude plan answers Dot's single questions (a review, the harness's roles) and runs whole "
            "as Claude Code; a conversation, a picture or several tools in one call it cannot take: pick another model "
            "for this work",
            {"purpose": request.session_key, "messages": len(request.messages), "tools": len(request.tools)},
        )
    tool: ToolSpec | None = request.tools[0] if request.tools else None
    asked: str = "\n\n".join(systems)
    if tool is not None:
        asked = (
            f"{asked}\n\nGive your answer as the structured output: it is your call of the tool {tool.name} "
            f"({tool.description.strip()}), with its arguments."
        )
    return Question(system=asked.strip(), text=others[0].content, tool=tool)


def _nullable(node: JsonValue) -> JsonValue:
    if isinstance(node, list):
        return [_nullable(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: JsonObject = {key: _nullable(value) for key, value in node.items()}
    kind: JsonValue = node.get("type")
    if isinstance(kind, str) and kind != "null":
        out["type"] = [kind, "null"]
        listed: JsonValue = node.get("enum")
        if isinstance(listed, list):
            out["enum"] = [*listed, None]
    return out


def answer_schema(parameters: JsonObject) -> JsonObject:
    """The shape a call's answer is held to: the tool's ``parameters`` with null allowed for each field, at any depth.
    Dot's tool schemas drop the null of an optional field (:func:`dotagent.tools.schema.flatten_json_schema`), which a
    provider's API lets a model give all the same, while the program's ``--json-schema`` holds the model to the schema
    strictly: a field to be left out came back as the text ``"null"`` (measured on 2.1.318). What the answer must be is
    Dot's own validation of it, as for any provider."""
    fields: JsonValue = parameters.get("properties")
    if not isinstance(fields, dict):
        return parameters
    return {**parameters, "properties": {name: _nullable(field) for name, field in fields.items()}}


def ask_argv(program: str, model: str, effort: Effort, system_file: Path, schema: JsonObject | None) -> tuple[str, ...]:
    """The command of a call: ``model`` (the program's alias) at ``effort``, the system prompt of ``system_file`` in
    place of the program's own, no tool but the structured output of ``schema`` (none: text), no settings file, MCP
    server, slash command or kept session; the question comes on its input, the answer as one JSON document."""
    shape: tuple[str, ...] = () if schema is None else ("--json-schema", dumps_compact(schema))
    return (
        program,
        "-p",
        "--output-format",
        "json",
        "--verbose",
        "--model",
        model,
        "--effort",
        effort,
        "--system-prompt-file",
        str(system_file),
        "--tools",
        "",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        *shape,
    )


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        item if isinstance(item, str) else str(item.get("name", "")) if isinstance(item, dict) else repr(item)
        for item in value
    )


def _added_plugins(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return _names(
        [
            item
            for item in value
            if not (
                isinstance(item, dict)
                and item.get("path") == _BUILT_IN
                and item.get("name") in MEASURED_BUILT_INS
                and item.get("source") == f"{item.get('name')}@{_BUILT_IN}"
            )
        ]
    )


def read_answer(output: str) -> Answer:
    """What the program's output says: with ``--verbose`` it is a JSON list of the session's lines, the init line, the
    plan's window and the result among them. Raises :class:`ProviderProtocolError` when it is not such a list."""
    try:
        lines: object = json.loads(output)
    except json.JSONDecodeError as error:
        raise ProviderProtocolError(
            "Claude Code's answer is not JSON", {"output": output[:_STDERR_KEPT], "error": str(error)}
        ) from error
    if not isinstance(lines, list):
        raise ProviderProtocolError(
            "Claude Code's answer is not a list of its lines", {"output": output[:_STDERR_KEPT]}
        )
    init: dict[str, object] = {}
    window: LimitWindow | None = None
    result: JsonObject | None = None
    for line in lines:
        if not isinstance(line, dict):
            continue
        match line.get("type"):
            case "system" if line.get("subtype") == "init":
                init = line
            case "rate_limit_event":
                window = limit_of(line.get("rate_limit_info")) or window
            case "result":
                result = line
    servers: object = init.get("mcp_servers")
    return Answer(
        tools=_names(init.get("tools")),
        servers=_names(servers),
        commands=_names(init.get("slash_commands")),
        skills=_names(init.get("skills")),
        plugins=_added_plugins(init.get("plugins")),
        window=window,
        result=result,
    )


def answer_problem(answer: Answer, structured: bool) -> str | None:
    """What is wrong with what the model was given: ``None`` when it had the structured output alone (``structured``)
    or nothing, and no MCP server, slash command, skill or plugin but the measured built-ins."""
    allowed: frozenset[str] = frozenset({STRUCTURED_TOOL}) if structured else frozenset()
    problems: list[str] = []
    others: list[str] = [tool for tool in answer.tools if tool not in allowed]
    if others:
        problems.append(f"tools: {', '.join(others)}")
    for kind, names in (
        ("MCP servers", answer.servers),
        ("slash commands", answer.commands),
        ("skills", answer.skills),
        ("plugins", answer.plugins),
    ):
        if names:
            problems.append(f"{kind}: {', '.join(names)}")
    return "; ".join(problems) if problems else None


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def reply_of(answer: Answer, question: Question, model: str, now: float) -> ModelReply:
    """The reply a call's ``answer`` is: the question's tool called with the structured output, or the text. Raises
    :class:`ProviderWindowError` when the plan's window refused it (with when it resets, from ``now``, so that the
    router pauses the model until then),
    :class:`ProviderTransportError` when the session failed, and :class:`ProviderProtocolError` when the model had more
    than it may or the answer lacks what the question asked for."""
    problem: str | None = answer_problem(answer, question.tool is not None)
    if problem is not None:
        raise ProviderProtocolError(
            f"Claude Code gave the model more than Dot's question allows ({problem})", {"model": model}
        )
    result: JsonObject | None = answer.result
    text: str = "" if result is None else str(result.get("result", ""))
    refused: bool = answer.window is not None and window_refused(answer.window)
    if refused or (result is not None and result.get("is_error") is not False and limit_said(text)):
        resets: float | None = limit_resets(answer.window, text)
        raise ProviderWindowError(
            limit_message(answer.window, resets),
            _WINDOW_STATUS,
            None if resets is None else max(resets - now, 0.0),
            {"model": model, "resets_at": resets},
        )
    if result is None:
        raise ProviderTransportError("Claude Code ended without a result", {"model": model})
    if result.get("is_error") is not False:
        raise ProviderTransportError(
            f"Claude Code's session failed: {text or result.get('subtype', '')}",
            {"model": model, "subtype": str(result.get("subtype", ""))},
        )
    usage: object = result.get("usage")
    tokens: dict[str, object] = usage if isinstance(usage, dict) else {}
    cached: int = _int(tokens.get("cache_read_input_tokens"))
    spent: Usage = Usage(
        input_tokens=_int(tokens.get("input_tokens")) + _int(tokens.get("cache_creation_input_tokens")) + cached,
        output_tokens=_int(tokens.get("output_tokens")),
        cached_tokens=cached,
    )
    stop: str = str(result.get("stop_reason", ""))
    if question.tool is None:
        finish: FinishReason = "length" if stop == "max_tokens" else "stop"
        return ModelReply(
            model=model, text=text, thinking="", tool_calls=(), usage=spent, finish=finish, raw_finish=stop, raw=()
        )
    shaped: object = result.get("structured_output")
    if not isinstance(shaped, dict):
        raise ProviderProtocolError(
            f"Claude Code's answer has no structured output for {question.tool.name}",
            {"model": model, "stop_reason": stop, "text": text[:_STDERR_KEPT]},
        )
    call: ToolCall = ToolCall(id="structured", name=question.tool.name, arguments=shaped)
    return ModelReply(
        model=model,
        text="",
        thinking="",
        tool_calls=(call,),
        usage=spent,
        finish="tool_calls",
        raw_finish=stop,
        raw=(),
    )


@asynccontextmanager
async def _private_folder() -> AsyncIterator[Path]:
    """A folder of the call's own that only the owner reads, in Dot's home, removed with all it holds as it ends."""
    parent: Path = claude_code_dir() / "asks"
    await asyncio.to_thread(parent.mkdir, mode=_PRIVATE, parents=True, exist_ok=True)
    folder: Path = Path(await asyncio.to_thread(tempfile.mkdtemp, "", "ask-", str(parent)))
    try:
        yield folder
    finally:
        await asyncio.to_thread(shutil.rmtree, folder, True)


def _write_private(path: Path, text: str) -> None:
    descriptor: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


async def ask_claude(
    program: Path, request: ChatRequest, login_env: Mapping[str, str], model: str, effort: Effort
) -> ModelReply:
    """Ask the plan's ``model`` the single question ``request`` is, at ``effort``, through ``program``. A
    cancellation (the caller's time limit) kills the program and its group."""
    question: Question = question_of(request)
    async with _private_folder() as folder:
        system_file: Path = folder / "system.txt"
        await asyncio.to_thread(_write_private, system_file, question.system)
        argv: tuple[str, ...] = ask_argv(
            str(program),
            model,
            effort,
            system_file,
            None if question.tool is None else answer_schema(question.tool.parameters),
        )
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(folder),
                env=claude_env(login_env, folder),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            raise ClaudeCodeError(
                "Claude Code could not be started", {"program": str(program), "error": str(error)}
            ) from error
        try:
            output, errors = await process.communicate(question.text.encode("utf-8"))
        except BaseException:
            _kill(process.pid)
            await process.wait()
            raise
        if process.returncode != 0 and not output.strip():
            raise ProviderTransportError(
                "Claude Code ended without an answer",
                {
                    "model": model,
                    "exit_code": process.returncode,
                    "stderr": errors.decode("utf-8", "replace")[-_STDERR_KEPT:],
                },
            )
        return reply_of(read_answer(output.decode("utf-8", "replace")), question, model, time.time())
