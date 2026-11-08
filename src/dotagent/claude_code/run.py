"""A run on the owner's Claude plan (item 1013). The run goes whole to Claude Code, the ``claude`` program the owner
installed and signed in to, started without a terminal (``claude -p``) in Dot's workspace, in the Claude Code session
of its chat, which the program keeps from run to run (``--resume``); what was said in the chat on other models since
the session last saw it comes with the run's first message, as a run on OpenCode's agent has it
(:mod:`dotagent.opencode.run`).

The model sees Dot's tools alone: the program runs with no built-in tool (``--tools ""``), no settings file of the
owner's, the project's or the workspace's (``--setting-sources ""``: no hook, no CLAUDE.md), no MCP server but the
chat's door of Dot's bridge (``--strict-mcp-config``, :mod:`dotagent.opencode.bridge`), no slash command, and a
permission mode that refuses whatever the door does not name (``dontAsk`` with ``--allowedTools mcp__<door>``). Its
first line (``system``/``init``) says what the model has; a run whose model has anything else ends failed at once
(:func:`dotagent.claude_code.stream.init_problem`). Each call of a tool of Dot's runs as a call of Dot's own loop
(:func:`dotagent.agent.handover.bridged_call`: the run's gate, the tool's time limit, the run's timeline under the
tool's own name). The door's secret is in a 0600 file of a private folder of the run, removed as it ends, never in the
program's arguments, which ``ps`` shows every process of the owner.

The program's environment is Self-repair's worker's (:func:`dotagent.repair.claude.worker_env`: no API key, so the plan
is billed, no secret-named variable) without Dot's or git's own variables, with its updater, telemetry and error
reports off. Dot never reads Claude Code's credentials.

Dot shows what the model does as its own runs show theirs: its text and thinking as they come, each step with its tools,
its tokens and the plan's window. The owner's messages go to the program on its input while it works, and a stop of the
run ends it. The run ends done with the model's answer at a result once the program has taken in each message sent and
answered it (a message taken after the turn's last step began waits for a turn of its own: :func:`answered_all`), and
failed when the session fails, when the plan's window is used up (with when it resets: nothing moves the run to another
model), when the program says nothing for ``[providers.claude_code] silence_s`` while none of Dot's tools works for it,
when its last reply was cut short, and when it goes on past the run's limits (``run_max_minutes``, ``run_max_tokens``),
which it is told of once. A run here has none of the rest of what Dot's own runs bring (the memory recalled at the
start, the checks of a finish, the lessons and playbooks)."""

import asyncio
import base64
import contextlib
import os
import shutil
import signal
import tempfile
import time
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path

from dotagent.agent.attachments import (
    ATTACHMENTS_EVENT,
    StoredAttachment,
    UserInput,
    attachment_summary,
    message_with_attachments,
    steer_event,
)
from dotagent.agent.continuity import new_ledger
from dotagent.agent.handover import (
    ChatSession,
    agent_tools,
    bridged_call,
    let_go,
    read_chat,
    tool_context,
    write_chat,
)
from dotagent.agent.limits import (
    LIMIT_EVENT,
    LimitReached,
    RunLimits,
    limit_event_data,
    limit_reached,
    limit_result,
    limit_spent,
    limits_of,
)
from dotagent.agent.loop import AgentRun, RunEnd, RunOutcome, opening_text
from dotagent.agent.picks import PICKS_EVENT, owner_server_groups, picks_event_data
from dotagent.agent.playbooks import offered_to_run
from dotagent.agent.prompt import THOROUGH_AND_BOLD
from dotagent.agent.scaffold import PROFILE_RUNS, Scaffold, scaffold_for
from dotagent.agent.streammask import StreamMask
from dotagent.assistant import AssistantView, called_lines, owner_name_lines
from dotagent.claude_code.env import claude_env
from dotagent.claude_code.stream import (
    BlockBegan,
    BlockKind,
    ClaudeEvent,
    Delta,
    Init,
    LimitSeen,
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
    tool_prefix,
    user_line,
)
from dotagent.core.errors import ClaudeCodeError, RunCancelled
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.logs import get_logger
from dotagent.core.paths import claude_code_dir
from dotagent.core.text import redact_known, truncate_middle
from dotagent.core.types import JsonObject
from dotagent.llm.claude_code import EFFORTS, FAST_SETTINGS, ClaudeCodeProvider
from dotagent.llm.router import ModelRef, offers_fast_mode, parse_model_ref
from dotagent.llm.traits import effective_effort
from dotagent.llm.types import (
    ChatProvider,
    Effort,
    ImageData,
    Message,
    ModelInfo,
    Speed,
    StreamDelta,
    ToolCall,
    ToolSpec,
    assistant_message,
)
from dotagent.opencode.bridge import CallRunner, Door, authorization, new_door
from dotagent.opencode.run import bridged, meanwhile
from dotagent.repair.claude import LimitWindow, window_refused
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import Skill, SkillCatalog
from dotagent.store.endings import PREFIX_CUT_SHORT
from dotagent.store.models import NO_PICKS, AgentRecord, RunMode
from dotagent.tools.base import ToolResult
from dotagent.tools.groups import BROWSER_GROUP, group_of
from dotagent.tools.registry import ToolRegistry, tools_for_mode
from dotagent.tools.shell import kill_group

log = get_logger(__name__)

# Dot's core tools the model gets besides those an OpenCode agent gets (:func:`dotagent.opencode.run.bridged`): Claude
# Code's own tools are off, so the files, the commands and the web are Dot's tools here. Those of Dot's own loop (the
# plan, finish, the tool groups, helpers, teammates and a campaign's) stay out: the program runs its own loop.
_OWN_CORE: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_files",
        "find_files",
        "shell",
        "web_fetch",
        "web_search",
        "web_extract",
        "web_crawl",
    }
)
# A line of the stream may be long (a step that read a big page): the reader takes lines up to this size.
_LINE_LIMIT: int = 32 * 1024 * 1024
_STDERR_KEPT: int = 4000
# How long the program has to end after its input closed, and after a SIGTERM, before its group is killed.
_EXIT_WAIT_S: float = 10.0
_TERM_GRACE_S: float = 5.0
# How often the watchdog looks at how long the program has been silent.
_WATCH_EVERY_S: float = 1.0
_THINKING_CHARS: int = 12_000
# How a reply that the run counts as cut short stopped (Dot's own loop counts one so, item 552).
_CUT_SHORT: frozenset[str] = frozenset({"max_tokens"})
# The steps that end after the model was told that the run's limit is reached before one that goes on ends the run.
_LIMIT_GRACE_STEPS: int = 2
_LIMIT_NOTE: str = (
    "[system note] The limit of this run is reached: it has used {spent}. Stop now, in this reply: write your final "
    "answer with what was reached (the numbers), what is missing and how to go on. Call no more tools: the run ends "
    "after this reply."
)
_PRIVATE: int = 0o700
_SECRET_FILE: int = 0o600
# What the model is told, before what Dot knows of the owner: who it is, its tools, how a run of Dot's ends.
_PROMPT: str = (
    "You are {name}, the owner's autonomous assistant on their Mac, at work on a task they gave you in one of Dot's "
    "chats. You act through Dot's tools alone, the tools of the MCP server {door}: {tools}. Claude Code's own tools, "
    "slash commands and skills are not available here; do not offer them. Each call goes through Dot's rules and, "
    "where they say so, the owner's approval; a refused call says why, and you go on another way. Work on the task "
    "until it is done, acting without asking what you can find out or decide yourself; ask_user is for what only the "
    "owner can answer. When the work is done, or cannot be done, write your final answer as your reply: it is what the "
    "owner reads in the chat, so say what you did and what you found, with the evidence. Write every message, the "
    "short lines between tool calls too, in the language the owner writes in. Files you make go in Dot's "
    "workspace, {workspace}, unless the owner names another place. Today is {today}.\n\n" + THOROUGH_AND_BOLD
)


def chat_path(thread_id: str) -> Path:
    """Where the Claude Code session of the chat ``thread_id`` is kept."""
    return claude_code_dir() / "chats" / f"{thread_id}.json"


def forget_chat(thread_id: str) -> None:
    """Let the chat's Claude Code session go: a rewind took out of the chat what the session saw, so the chat's next
    run on Claude Code starts a new session, which hears the chat's history with its first message. Blocking."""
    chat_path(thread_id).unlink(missing_ok=True)


def door_tool(name: str) -> bool:
    """Whether Dot's tool ``name`` is one the model gets: an OpenCode agent's (:func:`dotagent.opencode.run.bridged`)
    or one of :data:`_OWN_CORE`."""
    return bridged(name) or name in _OWN_CORE


def door_tools(
    registry: ToolRegistry, mode: RunMode, bounded: frozenset[str] | None, left_out: frozenset[str]
) -> ToolRegistry:
    """The tools of Dot's a run in ``mode`` gives the model: the :func:`door_tool` ones of the mode, of those only the
    ones ``bounded`` names when the run is an agent's whose tools are bounded (item 86), and none of the groups
    ``left_out`` (the browser's, when Settings › Browser keeps every run off it, item 354)."""
    return ToolRegistry(
        tools={
            tool.spec.name: tool
            for tool in tools_for_mode(registry, mode)
            if door_tool(tool.spec.name)
            and (bounded is None or tool.spec.name in bounded)
            and group_of(tool.spec.name) not in left_out
        }
    )


# The name of the door of every run in Claude Code (item 1052): each run is a process of its own that is given its door
# alone (``--strict-mcp-config``), and its secret is what lets its requests in, so the name need not tell chats apart.
# One name keeps the tools' names, and so the head of every chat's prompt, the same: Anthropic's cache of that head,
# about 160,000 tokens with the owner's servers' tools, is read by each new chat instead of written again, which took a
# new chat's first word from 3 to 25 seconds after its message (measured 2026-11-08).
CLAUDE_DOOR: str = "dot"


def door_specs(specs: Iterable[ToolSpec]) -> tuple[ToolSpec, ...]:
    """``specs`` in the order of their names (item 1052): the tools head the prompt, and the same tools in the same
    order are the same head, which Anthropic's cache reads, whatever order the servers connected in."""
    return tuple(sorted(specs, key=lambda spec: spec.name))


def mcp_config(door: Door, url: str) -> JsonObject:
    """The MCP configuration the program is given: the door alone, over HTTP, its secret in its header."""
    return {"mcpServers": {door.name: {"type": "http", "url": url, "headers": {"Authorization": authorization(door)}}}}


def write_mcp_config(folder: Path, config: JsonObject) -> Path:
    """Write ``config`` to a new file only the owner reads (0600) in ``folder``. Blocking."""
    path: Path = folder / "mcp.json"
    descriptor: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _SECRET_FILE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as written:
        written.write(dumps_compact(config))
    return path


def claude_argv(
    program: str,
    model: str,
    effort: Effort,
    speed: Speed,
    prompt: str,
    config_file: Path,
    door: str,
    resume: str | None,
) -> tuple[str, ...]:
    """The command of a run: ``model`` (Claude Code's alias) at ``effort`` (``--effort``, a level of
    :data:`~dotagent.llm.claude_code.EFFORTS`, :func:`run_effort`) and ``speed`` (fast mode through ``--settings``, the
    one settings source a run reads, item 1014; :func:`run_speed`), with ``prompt`` after Claude Code's own system
    prompt, the door ``door`` of ``config_file`` as its only MCP server and its tools as the only ones, in the session
    ``resume`` names or a new one; its input and output are lines of JSON."""
    again: tuple[str, ...] = () if resume is None else ("--resume", resume)
    fast: tuple[str, ...] = ("--settings", FAST_SETTINGS) if speed == "fast" else ()
    return (
        program,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--replay-user-messages",
        "--verbose",
        "--model",
        model,
        "--effort",
        effort,
        *fast,
        "--append-system-prompt",
        prompt,
        "--tools",
        "",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        str(config_file),
        "--disable-slash-commands",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        *again,
        "--allowedTools",
        f"mcp__{door}",
    )


def system_prompt(name: str, door: str, tools: Sequence[str], workspace: Path, today: str, known: Sequence[str]) -> str:
    """What the model is told besides Claude Code's own prompt: who it is (``name``), the door's ``tools``, how a run of
    Dot's works and ends, and the lines ``known`` of the owner."""
    lead: str = _PROMPT.format(name=name, door=door, tools=", ".join(tools), workspace=workspace, today=today)
    if not known:
        return lead
    lines: str = "\n".join(f"- {line}" for line in known)
    return f"{lead}\n\nWhat you know about the owner:\n{lines}"


@dataclass
class _Quiet:
    """When the program last wrote a line or a call of Dot's tool for it ended, and how many of those are at work: the
    watchdog does not count a call's wait (an approval the owner takes a while over) as silence."""

    heard: float
    working: int = 0


async def _watched_call(quiet: _Quiet, run: CallRunner, call: ToolCall) -> ToolResult:
    quiet.working += 1
    try:
        return await run(call)
    finally:
        quiet.working -= 1
        quiet.heard = time.monotonic()


@dataclass
class _Step:
    """A reply of the model: its blocks' text and thinking, the tools it called, its tokens and why it stopped."""

    started: float
    input_tokens: int
    cached_tokens: int
    output_tokens: int = 0
    stop_reason: str = ""
    kinds: dict[int, BlockKind] = field(default_factory=dict)
    texts: dict[int, str] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)

    def text(self, kind: BlockKind) -> str:
        return "".join(text for index, text in sorted(self.texts.items()) if self.kinds.get(index) == kind)


@dataclass(frozen=True)
class _Ended:
    """The program's output ended."""


@dataclass(frozen=True)
class _Stopped:
    """The run was stopped."""


@dataclass(frozen=True)
class _Silent:
    seconds: float


@dataclass(frozen=True)
class _Broke:
    error: BaseException


type _Item = str | UserInput | _Ended | _Stopped | _Silent | _Broke


@dataclass
class _Follow:
    """One run's following of the program: what it has seen so far, and what it sends."""

    agent: AgentRun
    stdin: asyncio.StreamWriter
    # The chat's door (``dot_1a2b3c4d``): its tools are named ``mcp__dot_1a2b3c4d__<tool>`` in the program.
    door: str
    vision: bool
    queue: asyncio.Queue[_Item]
    started: float
    limits: RunLimits | None
    told: LimitReached | None = None
    since_told: int = 0
    over: LimitReached | None = None
    steps: int = 0
    sent: int = 0
    # The messages the program said it took in, and whether one came after the turn's last step began (taken then, it
    # waits for a turn of its own; one taken before a step is that step's to answer).
    taken: int = 0
    taken_since_step: bool = False
    # Why fast mode was off though the run asked for it, said with its answer (item 1014); "" when it was not.
    fast_off: str = ""
    answer: str = ""
    session_id: str = ""
    inited: bool = False
    step: _Step | None = None
    finish: str = ""
    window: LimitWindow | None = None
    added: int = 0
    stream: StreamMask = field(init=False)

    def __post_init__(self) -> None:
        self.stream = StreamMask(self.show)

    @property
    def runtime(self) -> Runtime:
        return self.agent.runtime

    async def show(self, delta: StreamDelta) -> None:
        await self.emit("delta", {"kind": delta.kind, "text": delta.text, "step": self.steps + 1}, False)

    async def emit(self, kind: str, data: JsonObject, persisted: bool) -> None:
        await self.runtime.bus.emit(self.agent.run.id, kind, data, persisted)

    async def send(self, text: str, images: tuple[ImageData, ...]) -> None:
        """A message to the model, on the program's input."""
        self.sent += 1
        try:
            self.stdin.write(user_line(text, images).encode("utf-8"))
            await self.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise ClaudeCodeError(
                "Claude Code stopped reading its input before the run ended",
                {"run_id": self.agent.run.id, "error": str(error)},
            ) from error

    async def follow(self) -> str:
        """Read the program's output until a result comes after the program took in each message sent; the model's
        last answer."""
        while True:
            item: _Item = await self.queue.get()
            match item:
                case _Stopped():
                    raise RunCancelled("the run was stopped", {"run_id": self.agent.run.id})
                case _Ended():
                    raise ClaudeCodeError(
                        "Claude Code ended before it answered", {"run_id": self.agent.run.id, "inited": self.inited}
                    )
                case _Silent(seconds=seconds):
                    raise ClaudeCodeError(
                        f"Claude Code said nothing for {round(seconds)} s while none of Dot's tools worked for it, so "
                        "Dot ended it",
                        {"run_id": self.agent.run.id, "silence_s": round(seconds, 1)},
                    )
                case _Broke(error=error):
                    raise error
                case UserInput():
                    await self.steer(item)
                case str():
                    event: ClaudeEvent | None = read_line(item)
                    if event is not None and await self.seen(event):
                        return self.answer

    async def seen(self, event: ClaudeEvent) -> bool:
        """Show ``event``; whether the run is over after it."""
        match event:
            case Init():
                problem: str | None = init_problem(event, self.door)
                if problem is not None:
                    raise ClaudeCodeError(
                        f"Claude Code gave the model more than Dot's tools, so Dot ended the run: {problem}",
                        {"run_id": self.agent.run.id, "tools": list(event.tools)},
                    )
                self.inited = True
                self.session_id = event.session_id or self.session_id
            case LimitSeen(window=window):
                self.window = window
                if window_refused(window):
                    raise ClaudeCodeError(
                        limit_message(window, limit_resets(window, "")),
                        {"run_id": self.agent.run.id, "window": window.model_dump(mode="json")},
                    )
            case Taken():
                self.taken += 1
                self.taken_since_step = True
                return False
            case Result():
                return await self.result(event)
            case _:
                if not self.inited:
                    raise ClaudeCodeError(
                        "Claude Code began to answer before it said what the model has, so Dot ended the run",
                        {"run_id": self.agent.run.id},
                    )
                await self.reply(event)
        return self.over is not None

    async def reply(self, event: StepBegan | BlockBegan | Delta | StepFinished | StepClosed) -> None:
        match event:
            case StepBegan():
                self.taken_since_step = False
                self.stream.begin(await asyncio.to_thread(known_secrets, filled=True))
                self.step = _Step(
                    started=time.monotonic(), input_tokens=event.input_tokens, cached_tokens=event.cached_tokens
                )
            case BlockBegan() if self.step is not None:
                self.step.kinds[event.index] = event.kind
                if event.kind == "tool":
                    self.step.tools.append(event.tool.removeprefix(tool_prefix(self.door)))
            case Delta() if self.step is not None:
                self.step.texts[event.index] = self.step.texts.get(event.index, "") + event.text
                await self.stream(StreamDelta(kind=event.kind, text=event.text))
            case StepFinished() if self.step is not None:
                self.step.stop_reason = event.stop_reason or self.step.stop_reason
                self.step.output_tokens = event.output_tokens
            case StepClosed() if self.step is not None:
                await self.step_ended(self.step)
                self.step = None

    async def step_ended(self, step: _Step) -> None:
        await self.stream.flush()
        self.steps += 1
        self.finish = step.stop_reason
        run_id: str = self.agent.run.id
        await self.runtime.store.add_run_tokens(run_id, step.input_tokens, step.output_tokens)
        await self.runtime.store.set_run_steps(run_id, self.steps)
        thinking: str = step.text("thinking")
        text: str = step.text("text")
        if text.strip():
            self.answer = text.strip()
        known: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        await self.emit(
            "assistant",
            {
                "step": self.steps,
                "text": redact_known(text, known),
                "thinking": truncate_middle(redact_known(thinking, known), _THINKING_CHARS),
                "thinking_chars": len(thinking),
                "tool_calls": list(step.tools),
                "input_tokens": step.input_tokens,
                "output_tokens": step.output_tokens,
                "cached_tokens": step.cached_tokens,
                "seconds": round(time.monotonic() - step.started, 3),
                "finish": dot_finish(step.stop_reason),
                "plan_window": None if self.window is None else self.window.model_dump(mode="json"),
            },
            True,
        )
        if step.stop_reason == "tool_use":
            await self.keep_to_limits()

    async def result(self, event: Result) -> bool:
        """A turn ended: the run is over once the program has taken in each message sent, whether into a turn of its own
        or folded into the one at work (one result then answers several)."""
        if event.is_error:
            context: JsonObject = {"run_id": self.agent.run.id, "subtype": event.subtype, "said": event.text[-2000:]}
            if limit_said(event.text) or (self.window is not None and window_refused(self.window)):
                raise ClaudeCodeError(limit_message(self.window, limit_resets(self.window, event.text)), context)
            raise ClaudeCodeError(f"Claude Code's session failed: {event.text or event.subtype}", context)
        if not self.inited:
            raise ClaudeCodeError(
                "Claude Code answered before it said what the model has, so Dot ended the run",
                {"run_id": self.agent.run.id},
            )
        if self.agent.run.speed == "fast" and event.fast_off:
            self.fast_off = event.fast_off
            log.warning("claude_code_fast_mode_off", run_id=self.agent.run.id, why=event.fast_off)
        self.session_id = event.session_id or self.session_id
        if event.text.strip():
            self.answer = event.text.strip()
        return answered_all(self.taken, self.sent, self.taken_since_step)

    async def keep_to_limits(self) -> None:
        """Read the run's limits after a step that the model goes on from, as the loop reads them between its steps
        (item 552): the first step that ends past one tells the model to stop and answer, and a run whose model still
        goes on after :data:`_LIMIT_GRACE_STEPS` more steps is over (``over``): it ends failed and the program is
        ended."""
        if self.limits is None:
            return
        if self.told is not None:
            self.since_told += 1
            if self.since_told >= _LIMIT_GRACE_STEPS:
                self.over = self.told
            return
        tokens: int = await self.runtime.store.family_tokens(self.agent.run.id)
        reached: LimitReached | None = limit_reached(self.limits, time.monotonic() - self.started, tokens)
        if reached is None:
            return
        self.told = reached
        log.info("claude_code_limit_reached", run_id=self.agent.run.id, spent=limit_spent(reached))
        await self.emit(LIMIT_EVENT, limit_event_data(reached), True)
        await self.send(_LIMIT_NOTE.format(spent=limit_spent(reached)), ())

    async def steer(self, sent: UserInput) -> None:
        """The owner's message while the model works: kept in the chat and sent to the program, which reads it once
        its turn allows."""
        runtime: Runtime = self.runtime
        run_id: str = self.agent.run.id
        note: Message = await message_with_attachments(
            runtime, self.agent.run.thread_id, run_id, sent.text, sent.files, self.vision
        )
        # As Dot's own loop takes it (item 902): one place for what a message does to the run's trust.
        self.agent.gate.take(sent)
        await runtime.store.append_messages(self.agent.run.thread_id, [note])
        self.added += 1
        await self.emit("steer", steer_event(sent, self.agent.gate.tainted), True)
        await self.send(note.content, await asyncio.to_thread(_images, sent.files, self.vision))


def _images(files: tuple[StoredAttachment, ...], vision: bool) -> tuple[ImageData, ...]:
    """The pictures that go to the model with a message, for a model that sees; their description is in the message's
    text already for one that does not. Blocking."""
    if not vision:
        return ()
    return tuple(
        ImageData(media_type=file.media_type, data_b64=base64.b64encode(Path(file.path).read_bytes()).decode("ascii"))
        for file in files
        if file.kind == "image"
    )


async def _pump_lines(stream: asyncio.StreamReader, queue: asyncio.Queue[_Item], quiet: _Quiet) -> None:
    while True:
        try:
            raw: bytes = await stream.readline()
        except (ValueError, asyncio.LimitOverrunError) as error:
            queue.put_nowait(
                _Broke(ClaudeCodeError("Claude Code wrote a line longer than Dot reads", {"error": str(error)}))
            )
            return
        if not raw:
            queue.put_nowait(_Ended())
            return
        quiet.heard = time.monotonic()
        queue.put_nowait(raw.decode("utf-8", errors="replace"))


async def _pump_inbox(inbox: asyncio.Queue[UserInput], queue: asyncio.Queue[_Item]) -> None:
    while True:
        try:
            sent: UserInput = await inbox.get()
        except asyncio.QueueShutDown:
            return
        queue.put_nowait(sent)


async def _pump_stop(cancel: asyncio.Event, queue: asyncio.Queue[_Item]) -> None:
    await cancel.wait()
    queue.put_nowait(_Stopped())


async def _watch(quiet: _Quiet, silence_s: float, queue: asyncio.Queue[_Item]) -> None:
    """Tell the run once the program has been silent for ``silence_s`` with none of Dot's tools at work for it."""
    while True:
        await asyncio.sleep(_WATCH_EVERY_S)
        silent: float = time.monotonic() - quiet.heard
        if quiet.working == 0 and silent > silence_s:
            queue.put_nowait(_Silent(seconds=silent))
            return


async def _read_tail(stream: asyncio.StreamReader) -> str:
    data: bytes = await stream.read()
    return data.decode("utf-8", errors="replace")[-_STDERR_KEPT:]


async def _end_program(process: asyncio.subprocess.Process) -> None:
    """End the program and everything it started: SIGTERM to its group, then, after :data:`_TERM_GRACE_S`, SIGKILL."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(_TERM_GRACE_S):
                await process.wait()
    kill_group(process.pid)
    if process.returncode is None:
        await process.wait()


async def _close_input(stdin: asyncio.StreamWriter) -> None:
    stdin.close()
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        await stdin.wait_closed()


@asynccontextmanager
async def _private_folder() -> AsyncIterator[Path]:
    """A folder of the run's own that only the owner reads, in Dot's home (which no run's command reads, item 1013),
    removed with all it holds as the block ends."""
    parent: Path = claude_code_dir() / "runs"
    await asyncio.to_thread(parent.mkdir, mode=_PRIVATE, parents=True, exist_ok=True)
    folder: Path = Path(await asyncio.to_thread(tempfile.mkdtemp, "", "run-", str(parent)))
    try:
        yield folder
    finally:
        await asyncio.to_thread(shutil.rmtree, folder, True)


async def _provider_of(runtime: Runtime, ref: ModelRef) -> ClaudeCodeProvider:
    provider: ChatProvider = await runtime.router.provider(ref.provider)
    if not isinstance(provider, ClaudeCodeProvider):
        raise ClaudeCodeError("the model's provider is not Claude Code", {"model": ref.label})
    return provider


async def _prompt_of(agent: AgentRun, door: Door) -> str:
    """Dot's system prompt for the run: who the model is, the door's tools, and the names of the owner and of Dot."""
    runtime: Runtime = agent.runtime
    names: AssistantView = runtime.assistant.view()
    record: AgentRecord | None = (
        None if agent.run.agent_id is None else await runtime.store.get_agent(agent.run.agent_id)
    )
    known: list[str] = list(owner_name_lines(names.owner, names.mac_name))
    if record is None:
        known.extend(called_lines(names.name))
        name: str = "Dot"
    else:
        name = f"{record.name}, one of Dot's agents ({record.role})"
        if record.instructions.strip():
            known.append(f"Your instructions as {record.name}: {record.instructions.strip()}")
    return system_prompt(
        name,
        door.name,
        [spec.name for spec in door.specs],
        runtime.workspace,
        datetime.now().astimezone().strftime("%A %Y-%m-%d, time zone %Z (UTC%z)"),
        known,
    )


# What the owner is told of fast mode that ran off, by the program's reason (item 1014): measured on 2.1.318, a plan
# without extra usage runs it off, as fast mode is billed to extra usage.
_FAST_OFF_WORDS: dict[str, str] = {
    "extra_usage_disabled": "your Claude plan has no extra usage turned on, which fast mode is billed to (claude.ai "
    "settings, Usage)",
}


def fast_off_words(why: str) -> str:
    """The line said with a run's answer when fast mode was asked for and the program ran it off, for ``why``."""
    return f"Fast mode was off for this run: {_FAST_OFF_WORDS.get(why, why)}; it went at the standard speed."


# Claude's names of how a reply ended, as Dot's timeline names them (item 1016): a reply that stops to call a tool or
# ends its turn ended as it should, which the timeline reads from "tool_calls" and "stop"; any other (max_tokens,
# refusal) stays as Claude names it, and the timeline says the message ended early.
_DOT_FINISH: dict[str, str] = {
    "tool_use": "tool_calls",
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
}


def dot_finish(stop_reason: str) -> str:
    """How a step of a Claude Code run ended, in Dot's words (:data:`_DOT_FINISH`): the timeline marks each step whose
    reply stopped for a tool call "ended early: tool_use" otherwise."""
    return _DOT_FINISH.get(stop_reason, stop_reason)


def run_effort(chosen: Effort | None, configured: Effort) -> Effort:
    """The level a run of the plan thinks at (item 1014): the run's own, else the configured default, rounded to the
    nearest of Claude Code's (``off`` and ``minimal`` to the lowest, ``max`` the highest), as Dot's own loop rounds a
    level to a model's (:func:`dotagent.llm.traits.effective_effort`); what the composer shows is what goes."""
    level: Effort | None = effective_effort(EFFORTS, configured if chosen is None else chosen)
    return EFFORTS[0] if level is None else level


def run_speed(ref: ModelRef, speed: Speed) -> Speed:
    """The speed a run asks the program for: fast only on a model with fast mode (:func:`offers_fast_mode`), as Dot's
    own loop asks it of a step, so a chat's fast speed carried to another model asks for none."""
    return speed if offers_fast_mode(ref) else "standard"


def answered_all(taken: int, sent: int, taken_since_step: bool) -> bool:
    """Whether a result answers every message sent: the program took each one in (``taken``, by
    ``--replay-user-messages``), and none was taken after the turn's last step began (``taken_since_step``). Measured
    on 2.1.318: a message sent while a tool worked was taken in and the next step of the same turn answered it, one
    result for both; one taken while the last step streamed waits for a turn of its own, which a step begins."""
    return taken >= sent and not taken_since_step


def ended_clean(over: LimitReached | None, finish: str) -> bool:
    """Whether a run ended clean, its session worth keeping: not cut at the run's limits (``over``) and its last reply
    not cut at the model's output limit (``finish``), either of which ends the run failed (:func:`_outcome`)."""
    return over is None and finish not in _CUT_SHORT


def _outcome(follow: _Follow, answer: str, goal: str) -> RunOutcome:
    """How the run ends: failed when its limit was reached and the model went on regardless, and when its last reply
    was cut short at the model's output limit (item 552); else done with the model's last answer."""
    if follow.over is not None:
        result: str = limit_result(follow.over, new_ledger(goal), "", answer)
        return RunOutcome(status="failed", result=result, needs="")
    if follow.finish in _CUT_SHORT:
        cut: str = f"{PREFIX_CUT_SHORT} and the run did not finish (the last one ended with '{follow.finish}')."
        return RunOutcome(status="failed", result=cut, needs="")
    said: str = answer or "Claude Code finished without a closing message."
    if follow.fast_off:
        said = f"{said}\n\n{fast_off_words(follow.fast_off)}"
    return RunOutcome(status="done", result=said, needs="")


async def _bounds_of(agent: AgentRun) -> frozenset[str] | None:
    """The tools a run of an agent whose tools are bounded may have (item 86), ``None`` when it is not bounded: the ones
    it names and the core ones, and those of the connected apps the owner opened to it (item 415, item 1022): picked for
    this message or an earlier one of its chat, as Dot's own loop opens them."""
    runtime: Runtime = agent.runtime
    bounded: frozenset[str] | None = await agent_tools(runtime, agent.run.agent_id)
    if bounded is None:
        return None
    picked_before: frozenset[str] = await runtime.store.picked_servers(agent.run.thread_id, PICKS_EVENT)
    return opened_bounds(bounded, owner_server_groups(agent.opening.picks, picked_before), runtime.registry.tools)


def opened_bounds(bounded: frozenset[str], opened: frozenset[str], names: Iterable[str]) -> frozenset[str]:
    """``bounded``, an agent's tools, with those of ``names`` in the groups ``opened`` to it by the owner's picks."""
    return bounded | frozenset(name for name in names if group_of(name) in opened)


async def _opening_of(agent: AgentRun, ref: ModelRef, info: ModelInfo) -> str:
    """The run's first message: the owner's words, then what they picked for it (item 1022), as Dot's own loop gives
    them: each picked skill's instructions and the connected apps (MCP servers) whose tools to use, which reach the
    program through the door. The manager refuses the picks
    Claude Code cannot take (the accounts, Plan first) before the run starts."""
    runtime: Runtime = agent.runtime
    if agent.opening.picks == NO_PICKS:
        return agent.opening.text
    catalog: SkillCatalog = await asyncio.to_thread(
        load_skills, runtime.config.skills, runtime.plugins.plugins, playbook_root(runtime.config)
    )
    offered: tuple[Skill, ...] = await offered_to_run(runtime.config, catalog, agent.run.agent_id)
    scaffold: Scaffold = scaffold_for(
        runtime.config.agent,
        info,
        await runtime.store.model_profile(ref.label, PROFILE_RUNS),
        runtime.context_sizes.of(ref.label),
    )
    return await opening_text(agent.opening, offered, scaffold.tool_output_chars, playbook_root(runtime.config), {})


async def run_in_claude_code(agent: AgentRun) -> RunEnd:
    """Run ``agent``'s run in Claude Code on the owner's plan (module docstring)."""
    started: float = time.monotonic()
    runtime: Runtime = agent.runtime
    run = agent.run
    if agent.opening.files:
        await runtime.bus.emit(run.id, ATTACHMENTS_EVENT, {"files": attachment_summary(agent.opening.files)}, True)
    ref: ModelRef = parse_model_ref(run.model)
    provider: ClaudeCodeProvider = await _provider_of(runtime, ref)
    program: Path = await provider.program()
    info: ModelInfo = await provider.model_info(ref.model)
    earlier: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
    left_out: frozenset[str] = frozenset() if runtime.browser.config.enabled else frozenset({BROWSER_GROUP})
    # What the message came with stands under it at once, as in Dot's own loop (item 967).
    if agent.opening.picks != NO_PICKS:
        await runtime.bus.emit(run.id, PICKS_EVENT, picks_event_data(agent.opening.picks), True)
    tools: ToolRegistry = door_tools(runtime.registry, run.mode, await _bounds_of(agent), left_out)
    quiet: _Quiet = _Quiet(heard=time.monotonic())
    door: Door = new_door(
        CLAUDE_DOOR,
        door_specs(tool.spec for tool in tools.tools.values()),
        partial(_watched_call, quiet, partial(bridged_call, tools, await tool_context(agent, ref, info), agent.gate)),
    )
    chat: ChatSession | None = await asyncio.to_thread(read_chat, chat_path(run.thread_id))
    goal: Message = await message_with_attachments(
        runtime, run.thread_id, run.id, await _opening_of(agent, ref, info), agent.opening.files, info.vision
    )
    await runtime.store.append_messages(run.thread_id, [goal])
    prompt: str = await _prompt_of(agent, door)
    # The chat's session is kept only when this run ended clean and its count of what the session saw was written:
    # a run that failed, was stopped by the owner or was cut at its limits after the program took its messages leaves
    # that count behind, and resumed, the session would hear them again (a stopped goal read as a new ask). Whatever
    # ended it, the next run then starts a new session, which hears the chat with its first message.
    kept: bool = False
    try:
        async with provider.bridge.opened(door) as url, _private_folder() as folder:
            config_file: Path = await asyncio.to_thread(write_mcp_config, folder, mcp_config(door, url))
            argv: tuple[str, ...] = claude_argv(
                str(program),
                ref.model,
                run_effort(run.effort, runtime.config.models.effort),
                run_speed(ref, run.speed),
                prompt,
                config_file,
                door.name,
                None if chat is None else chat.session_id,
            )
            try:
                process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=str(runtime.workspace),
                    env=claude_env(runtime.shell_env, folder),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    limit=_LINE_LIMIT,
                )
            except OSError as error:
                raise ClaudeCodeError(
                    "Claude Code could not be started", {"program": str(program), "error": str(error)}
                ) from error
            assert process.stdin is not None and process.stdout is not None and process.stderr is not None
            log.info(
                "claude_code_run_started", run_id=run.id, pid=process.pid, model=ref.model, resume=chat is not None
            )
            queue: asyncio.Queue[_Item] = asyncio.Queue()
            follow: _Follow = _Follow(
                agent=agent,
                stdin=process.stdin,
                door=door.name,
                vision=info.vision,
                queue=queue,
                started=started,
                limits=limits_of(runtime.config.agent, run.mode),
            )
            tail: asyncio.Task[str] = asyncio.create_task(_read_tail(process.stderr), name="claude-code-stderr")
            pumps: tuple[asyncio.Task[None], ...] = (
                asyncio.create_task(_pump_lines(process.stdout, queue, quiet), name="claude-code-lines"),
                asyncio.create_task(_pump_inbox(agent.inbox, queue), name="claude-code-inbox"),
                asyncio.create_task(_pump_stop(agent.cancel, queue), name="claude-code-stop"),
                asyncio.create_task(_watch(quiet, provider.config.silence_s, queue), name="claude-code-watch"),
            )
            clean: bool = False
            try:
                opening: str = meanwhile(goal.content, earlier[0 if chat is None else chat.seen :])
                await follow.send(opening, await asyncio.to_thread(_images, agent.opening.files, info.vision))
                try:
                    answer: str = await follow.follow()
                except ClaudeCodeError as error:
                    error.context["stderr"] = await _stderr_now(tail, process)
                    raise
                clean = ended_clean(follow.over, follow.finish)
                await _close_input(process.stdin)
                if clean:
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(_EXIT_WAIT_S):
                            await process.wait()
            finally:
                for task in pumps:
                    task.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                await asyncio.shield(_end_program(process))
                tail.cancel()
                await asyncio.gather(tail, return_exceptions=True)
                await let_go(runtime, run.id, ())
        await runtime.store.append_messages(run.thread_id, [assistant_message(answer, None, ())])
        if clean and follow.session_id:
            await asyncio.to_thread(
                write_chat,
                chat_path(run.thread_id),
                ChatSession(session_id=follow.session_id, seen=len(earlier) + 2 + follow.added),
            )
            kept = True
    finally:
        if chat is not None and not kept:
            await asyncio.to_thread(forget_chat, run.thread_id)
    log.info("claude_code_run_ended", run_id=run.id, session=follow.session_id, steps=follow.steps, clean=clean)
    return RunEnd(outcome=_outcome(follow, answer, run.goal), lessons=None)


async def _stderr_now(tail: asyncio.Task[str], process: asyncio.subprocess.Process) -> str:
    """What the program wrote to its error stream, once it ended (it is ended first, so that the stream closes)."""
    await asyncio.shield(_end_program(process))
    try:
        async with asyncio.timeout(_EXIT_WAIT_S):
            return await asyncio.shield(tail)
    except TimeoutError:
        return ""
