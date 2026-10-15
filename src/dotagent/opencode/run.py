"""A run on one of Zen's free models (item 283). The run goes whole to OpenCode's own agent (:mod:`dotagent.opencode.
server`), in the OpenCode session of its chat, which the agent keeps from run to run; what was said in the chat on other
models since the session last saw it comes with the run's first message. Dot shows what the agent does as its own runs
show theirs: the model's text and thinking as they come, each tool call and its result, each step with its tokens. Each
permission the agent asks for is decided as a tool of Dot's own is (:meth:`dotagent.agent.gate.RunGate.authorize`): by
the owner's rules and preset, the review model or the owner, as the run's approval mode says; a refusal tells the agent
why, and it goes on. The owner's messages go to the agent while it works, and a stop of the run stops it. The run ends
done with the agent's last answer once the agent has nothing left to do, and failed when its session fails.

A run here has none of what Dot's own runs bring besides (Dot's tools, memory and skills, the checks of a finish, the
lessons and playbooks): item 284 brings Dot's tools to the agent over MCP."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from dotagent.agent.attachments import (
    ATTACHMENTS_EVENT,
    StoredAttachment,
    UserInput,
    attachment_summary,
    message_with_attachments,
)
from dotagent.agent.loop import AgentRun, RunEnd, RunOutcome
from dotagent.browser.safety import UrlVerdict, check_url
from dotagent.core.errors import (
    ApprovalRejected,
    HandedOff,
    JsonParseError,
    OpenCodeError,
    PolicyDenied,
    RunCancelled,
)
from dotagent.core.jsonutil import parse_json_object
from dotagent.core.logs import get_logger
from dotagent.core.paths import opencode_dir
from dotagent.core.text import redact_json, redact_secrets, truncate_middle
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.opencode import OpenCodeProvider
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import ChatProvider, Message, ModelInfo, assistant_message
from dotagent.llm.wire import get_obj, get_str
from dotagent.opencode.events import (
    MessageSeen,
    PartSeen,
    PermissionAsked,
    Piece,
    PieceKind,
    SessionEvent,
    SessionFailed,
    SessionIdle,
    StepEnded,
    StepStarted,
    ToolSeen,
    read_event,
)
from dotagent.opencode.server import OpenCodeServer, PromptFile
from dotagent.policy.paths import expand_path
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime
from dotagent.tools.channels import Channel
from dotagent.tools.files import read_action, write_action

log = get_logger(__name__)

# The names of OpenCode's tools in Dot's timeline: ``opencode_bash``, ``opencode_edit``, ...
TOOL_PREFIX: str = "opencode_"
_EVENT_TEXT_CHARS: int = 4000
_THINKING_CHARS: int = 12_000
_PREVIEW_CHARS: int = 200
# What of the chat on other models comes with a run's first message: its newest messages, each cut to a length.
_MEANWHILE_MESSAGES: int = 20
_MEANWHILE_CHARS: int = 2000
_TITLE_CHARS: int = 80
# How long a run that ends lets the permissions it still decides finish (:func:`_let_go`).
_ASK_GRACE_S: float = 10.0
# What the agent is told when it asks for a subagent: the subagent's session would not be the run's, which Dot follows.
NO_SUBAGENT_NOTE: str = "Subagents are not available here: do this part of the work yourself, with your own tools."
# What the agent is told when it makes the same call a third time in a row (OpenCode asks ``doom_loop`` then): Dot's own
# runs get a nudge of the kind for a streak of one action.
DOOM_LOOP_NOTE: str = (
    "You made the same call with the same input three times in a row; it will not give anything new. Try another way,"
    " or say what stops you."
)
_WEB_TOOLS: frozenset[str] = frozenset({"webfetch", "websearch", "codesearch"})
# What a run says when Zen's free tier turns a request down. Its own words say that the free tier serves OpenCode alone,
# but it says them to OpenCode's own command line as well, for a few minutes after many requests in a short time
# (measured 2026-10-15: refused from 17:48 to about 17:56, answered at 17:57, the same program and setup throughout).
FREE_TIER_REFUSED: str = (
    "Zen's free tier turned the request down for now (FreeTierError). It does so for a few minutes after many requests"
    " in a short time; try again in a few minutes, or pick another model"
)


class ChatSession(FrozenModel):
    """The OpenCode session of a chat, and how many of the chat's messages it has seen."""

    session_id: str
    seen: int


def _chat_path(thread_id: str) -> Path:
    return opencode_dir() / "chats" / f"{thread_id}.json"


def _read_chat(thread_id: str) -> ChatSession | None:
    path: Path = _chat_path(thread_id)
    return ChatSession.model_validate_json(path.read_bytes()) if path.exists() else None


def _write_chat(thread_id: str, chat: ChatSession) -> None:
    path: Path = _chat_path(thread_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged: Path = path.with_suffix(".tmp")
    staged.write_text(chat.model_dump_json() + "\n", encoding="utf-8")
    staged.replace(path)


def meanwhile(text: str, messages: list[Message]) -> str:
    """``text`` as the agent gets it when the chat said more on other models than its session saw: those messages'
    words first, the newest of them, so that the agent knows what the chat is at."""
    said: list[str] = [
        f"{message.role}: {truncate_middle(message.content.strip(), _MEANWHILE_CHARS)}"
        for message in messages
        if message.role in ("user", "assistant") and message.content.strip()
    ][-_MEANWHILE_MESSAGES:]
    if not said:
        return text
    joined: str = "\n\n".join(said)
    return f"Earlier in this conversation, with another assistant:\n\n{joined}\n\nNow:\n\n{text}"


def free_tier_refused(error: JsonObject) -> bool:
    """Whether an error of OpenCode's is Zen's free tier turning the request down: the answer it carries is a
    ``FreeTierError``."""
    body: str = get_str(get_obj(error, "data"), "responseBody")
    if not body.startswith("{"):
        return False
    try:
        answer: JsonObject = parse_json_object(body, "zen error")
    except JsonParseError:
        return False
    return get_str(get_obj(answer, "error"), "type") == "FreeTierError"


def agent_failed(text: str, error: JsonObject) -> OpenCodeError:
    """The error that ends a run whose agent failed: Zen's free tier's refusal said as what it is, anything else in
    OpenCode's words."""
    if free_tier_refused(error):
        return OpenCodeError(FREE_TIER_REFUSED, {"error": error})
    return OpenCodeError(f"OpenCode's agent failed: {text}", {"error": error})


def _patterns(asked: PermissionAsked) -> str:
    return " ".join(asked.patterns)


def _target_path(asked: PermissionAsked, workspace: Path) -> Path:
    """The path a permission of OpenCode's is about: the file or folder it names, else its first pattern."""
    raw: str = (
        get_str(asked.metadata, "filepath")
        or get_str(asked.metadata, "path")
        or (asked.patterns[0] if asked.patterns else "")
    )
    return expand_path(raw.removesuffix("/*").removesuffix("*") or ".", workspace)


def permission_action(
    asked: PermissionAsked, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> ActionInfo:
    """A permission OpenCode's agent asks for, as Dot's policy weighs the same action of one of its own tools: a command
    as the shell tool's, an edit as a write of the file, a read, a search or a folder outside the run's as a read, a
    fetch or a search of the web as reading the web. A permission of a kind Dot does not know is high risk."""
    tool: str = f"{TOOL_PREFIX}{asked.permission}"
    match asked.permission:
        case "bash":
            command: str = get_str(asked.metadata, "command") or _patterns(asked)
            assessment: ShellAssessment = assess_shell(command, workspace, workspace, protected, dot_paths)
            preview: str = truncate_middle(" ".join(command.split()), _PREVIEW_CHARS)
            return ActionInfo(
                tool=tool,
                kind="shell",
                risk=assessment.risk,
                summary=f"Run: {preview} ({'; '.join(assessment.reasons)})",
                target=command,
                flags=assessment.flags,
            )
        case "edit":
            return write_action(tool, _target_path(asked, workspace), workspace, protected, dot_paths, "Write")
        case "read" | "list" | "glob" | "grep" | "external_directory":
            return read_action(tool, _target_path(asked, workspace), workspace, protected, dot_paths, "Read")
        case "webfetch":
            url: str = get_str(asked.metadata, "url") or _patterns(asked)
            return ActionInfo(tool=tool, kind="network", risk="low", summary=f"Fetch {url}", target=url, flags=())
        case "websearch" | "codesearch":
            query: str = get_str(asked.metadata, "query") or _patterns(asked)
            return ActionInfo(
                tool=tool, kind="network", risk="low", summary=f"Search the web for {query}", target=query, flags=()
            )
        case _:
            patterns: str = truncate_middle(_patterns(asked), _PREVIEW_CHARS)
            return ActionInfo(
                tool=tool,
                kind="shell",
                risk="high",
                summary=f"OpenCode's agent asks for {asked.permission}: {patterns}",
                target=patterns,
                flags=(),
            )


def _channel(tool: str) -> Channel:
    if tool == "bash":
        return "command"
    return "web" if tool in _WEB_TOOLS else "agent"


@dataclass
class _Call:
    """A tool call of the agent as the run follows it: its policy action once it asked, the time it waited there."""

    announced: bool = False
    action: ActionInfo | None = None
    waited_s: float = 0.0


@dataclass
class _Step:
    """A step of the agent (one assistant message of OpenCode's): its parts' text and thinking, and its tool calls."""

    started: float
    parts: dict[str, PieceKind] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)

    def text(self, kind: PieceKind) -> str:
        return "".join(text for part, text in self.texts.items() if self.parts.get(part) == kind)


@dataclass(frozen=True)
class _Ended:
    """OpenCode's event stream ended: its server stopped."""


@dataclass(frozen=True)
class _Stopped:
    """The run was stopped."""


@dataclass(frozen=True)
class _Broke:
    error: BaseException


@dataclass(frozen=True)
class _Answered:
    """A permission the agent asked for was answered."""


type _Item = JsonObject | UserInput | _Ended | _Stopped | _Broke | _Answered


@dataclass
class _Follow:
    """One run's following of OpenCode's session: what it has seen so far, and what it answers."""

    agent: AgentRun
    server: OpenCodeServer
    session_id: str
    directory: Path
    model: str
    variant: str | None
    vision: bool
    queue: asyncio.Queue[_Item]
    steps: int = 0
    prompts: int = 0
    users: set[str] = field(default_factory=set)
    assistants: dict[str, _Step] = field(default_factory=dict)
    calls: dict[str, _Call] = field(default_factory=dict)
    answer: str = ""
    added: int = 0
    asks: set[asyncio.Task[None]] = field(default_factory=set)
    idle: bool = False

    @property
    def runtime(self) -> Runtime:
        return self.agent.runtime

    async def prompt(self, text: str, files: tuple[PromptFile, ...]) -> None:
        self.prompts += 1
        await self.server.prompt(self.session_id, self.directory, self.model, self.variant, text, files)

    async def emit(self, kind: str, data: JsonObject, persisted: bool) -> None:
        await self.runtime.bus.emit(self.agent.run.id, kind, data, persisted)

    def step_of(self, message_id: str) -> _Step | None:
        return self.assistants.get(message_id)

    def settled(self) -> bool:
        """Whether the session took every prompt the run sent and no permission it asked for is still being answered:
        an idle session then has nothing left to do, while a prompt it has not taken yet starts it again."""
        return self.idle and len(self.users) >= self.prompts and not self.asks

    async def follow(self) -> str:
        """Read the session's events until the agent has nothing left to do; the agent's last answer."""
        while True:
            item: _Item = await self.queue.get()
            match item:
                case _Stopped():
                    raise RunCancelled("the run was stopped", {"run_id": self.agent.run.id})
                case _Ended():
                    raise OpenCodeError("OpenCode's server stopped during the run", {"session": self.session_id})
                case _Broke(error=error):
                    raise error
                case UserInput():
                    await self.steer(item)
                case _Answered():
                    if self.settled():
                        return self.answer
                case _:
                    event: SessionEvent | None = read_event(item, self.session_id)
                    if event is not None and await self.seen(event):
                        return self.answer

    async def seen(self, event: SessionEvent) -> bool:
        """Show ``event``; whether the agent has nothing left to do after it."""
        match event:
            case MessageSeen(role="user"):
                self.users.add(event.message_id)
            case MessageSeen(role="assistant"):
                step: _Step = self.assistants.setdefault(event.message_id, _Step(started=time.monotonic()))
                if event.error is not None:
                    raise agent_failed(event.error, event.error_detail)
                answer: str = step.text("text").strip()
                if answer:
                    self.answer = answer
            case StepStarted():
                self.idle = False
                self.assistants.setdefault(event.message_id, _Step(started=time.monotonic())).started = time.monotonic()
            case PartSeen():
                await self.part(event)
            case Piece():
                await self.piece(event)
            case ToolSeen():
                await self.tool(event)
            case StepEnded():
                await self.step_ended(event)
            case PermissionAsked():
                self.ask(event)
            case SessionFailed():
                raise agent_failed(event.message, event.detail)
            case SessionIdle():
                self.idle = True
                return self.settled()
        return False

    async def part(self, event: PartSeen) -> None:
        step: _Step | None = self.step_of(event.message_id)
        if step is None:
            return
        step.parts[event.part_id] = event.kind
        if event.text:
            step.texts[event.part_id] = event.text

    async def piece(self, event: Piece) -> None:
        step: _Step | None = self.step_of(event.message_id)
        kind: PieceKind | None = None if step is None else step.parts.get(event.part_id)
        if step is None or kind is None:
            return
        step.texts[event.part_id] = step.texts.get(event.part_id, "") + event.text
        await self.emit("delta", {"kind": kind, "text": event.text, "step": self.steps + 1}, False)

    async def tool(self, event: ToolSeen) -> None:
        step: _Step | None = self.step_of(event.message_id)
        if step is None or event.status == "pending":
            return
        call: _Call = self.calls.setdefault(event.call_id, _Call())
        name: str = f"{TOOL_PREFIX}{event.tool}"
        if not call.announced:
            call.announced = True
            step.tools.append(name)
            await self.emit(
                "tool_call", {"call_id": event.call_id, "tool": name, "args": redact_json(event.input)}, True
            )
        if event.status == "running":
            return
        ok: bool = event.status == "completed"
        if ok and call.action is not None:
            self.agent.gate.observe(call.action)
        text: str = event.output if ok else event.error
        seconds: float = (
            (event.ended_ms - event.started_ms) / 1000
            if event.ended_ms is not None and event.started_ms is not None
            else 0.0
        )
        action: ActionInfo | None = call.action
        await self.emit(
            "tool_result",
            {
                "call_id": event.call_id,
                "tool": name,
                "ok": ok,
                "text": redact_json(truncate_middle(text, _EVENT_TEXT_CHARS)),
                "images": [],
                "seconds": round(seconds, 2),
                "waited_s": round(call.waited_s, 2),
                "summary": action.summary if action is not None else event.title,
                "kind": action.kind if action is not None else "",
                "risk": action.risk if action is not None else "",
                "target": action.target if action is not None else "",
                "site": "",
                "steps": [],
                "channel": _channel(event.tool),
                "tainted": self.agent.gate.tainted,
            },
            True,
        )

    async def step_ended(self, event: StepEnded) -> None:
        step: _Step | None = self.step_of(event.message_id)
        if step is None:
            return
        self.steps += 1
        run_id: str = self.agent.run.id
        await self.runtime.store.add_run_tokens(run_id, event.input_tokens, event.output_tokens)
        await self.runtime.store.set_run_steps(run_id, self.steps)
        thinking: str = step.text("thinking")
        text: str = step.text("text")
        if text.strip():
            self.answer = text.strip()
        await self.emit(
            "assistant",
            {
                "step": self.steps,
                "text": redact_secrets(text),
                "thinking": truncate_middle(redact_secrets(thinking), _THINKING_CHARS),
                "thinking_chars": len(thinking),
                "tool_calls": list(step.tools),
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cached_tokens": event.cached_tokens,
                "seconds": round(time.monotonic() - step.started, 3),
                "finish": event.reason,
            },
            True,
        )

    def ask(self, event: PermissionAsked) -> None:
        """Decide the permission in a task of its own: the owner may take a while, and the run reads on meanwhile."""
        task: asyncio.Task[None] = asyncio.create_task(
            self.decide(event), name=f"opencode-permission-{event.request_id}"
        )
        self.asks.add(task)
        task.add_done_callback(self.decided)

    def decided(self, task: asyncio.Task[None]) -> None:
        """A permission was answered; one whose answer failed fails the run, where it reads its events."""
        self.asks.discard(task)
        error: BaseException | None = None if task.cancelled() else task.exception()
        if error is not None:
            self.queue.put_nowait(_Broke(error=error))
        elif not task.cancelled():
            # The session may have gone idle while the answer was on its way: the run looks again then.
            self.queue.put_nowait(_Answered())

    async def decide(self, event: PermissionAsked) -> None:
        runtime: Runtime = self.runtime
        call: _Call = self.calls.setdefault(event.call_id or event.request_id, _Call())
        if event.permission == "doom_loop":
            await self.server.reply(event.request_id, self.directory, "reject", DOOM_LOOP_NOTE)
            return
        if event.permission == "task":
            await self.server.reply(event.request_id, self.directory, "reject", NO_SUBAGENT_NOTE)
            return
        action: ActionInfo = permission_action(event, self.directory, runtime.protected, runtime.dot_paths)
        call.action = action
        if action.kind == "network" and event.permission == "webfetch":
            verdict: UrlVerdict = check_url(
                action.target, runtime.config.browser.allowed_hosts, runtime.config.browser.blocked_hosts
            )
            if not verdict.allowed:
                await self.server.reply(event.request_id, self.directory, "reject", verdict.reason)
                return
        asked_at: float = time.monotonic()
        try:
            note: str = await self.agent.gate.authorize(action)
            await self.agent.gate.wait_free()
        except (ApprovalRejected, PolicyDenied) as refused:
            await self.server.reply(event.request_id, self.directory, "reject", refused.message)
            return
        except HandedOff as handed:
            await self.server.reply(
                event.request_id, self.directory, "reject", f"The owner did this step themselves: {handed.message}"
            )
            return
        finally:
            call.waited_s += time.monotonic() - asked_at
        await self.server.reply(event.request_id, self.directory, "once", None)
        if note:
            await self.prompt(f"[The owner approved {action.summary} and wrote: “{note}”]", ())

    async def steer(self, sent: UserInput) -> None:
        """The owner's message while the agent works: kept in the chat and handed to the agent, which reads it once
        its step ends."""
        runtime: Runtime = self.runtime
        run_id: str = self.agent.run.id
        note: Message = await message_with_attachments(
            runtime, self.agent.run.thread_id, run_id, sent.text, sent.files, self.vision
        )
        self.agent.gate.clear_taint()
        self.agent.gate.add_instruction(sent.text)
        await runtime.store.append_messages(self.agent.run.thread_id, [note])
        self.added += 1
        await self.emit("steer", {"text": sent.text, "files": attachment_summary(sent.files)}, True)
        await self.prompt(note.content, _prompt_files(sent.files, self.vision))


def _prompt_files(files: tuple[StoredAttachment, ...], vision: bool) -> tuple[PromptFile, ...]:
    """The pictures that go to the agent as files, for a model that can see; their text, and the description of the
    pictures for a model that cannot, is in the message's text already."""
    if not vision:
        return ()
    return tuple(
        PromptFile(path=Path(file.path), media_type=file.media_type, name=file.name)
        for file in files
        if file.kind == "image"
    )


async def _pump_events(stream: AsyncIterator[JsonObject], queue: asyncio.Queue[_Item]) -> None:
    try:
        async for event in stream:
            queue.put_nowait(event)
    except OpenCodeError as error:
        queue.put_nowait(_Broke(error=error))
        return
    queue.put_nowait(_Ended())


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


async def _stop_agent(server: OpenCodeServer, session_id: str, directory: Path) -> None:
    """Stop the agent of a run that ends before it did; the run's own error is what is raised."""
    try:
        await server.abort(session_id, directory)
    except OpenCodeError as error:
        log.warning("opencode_abort_failed", session=session_id, error=error.message, context=error.context)


async def _let_go(runtime: Runtime, run_id: str, asks: tuple[asyncio.Task[None], ...]) -> None:
    """End the permissions a run that ends still decides. The questions it waits for are let go, and each task stores
    its question as decided before it ends, so no page keeps offering one; a task cancelled in the middle of that would
    leave its question open. One still under way after ``_ASK_GRACE_S`` is cancelled, and one that failed is logged:
    the run's own ending is what it reports."""
    runtime.approvals.release_run(run_id)
    if not asks:
        return
    _, late = await asyncio.wait(asks, timeout=_ASK_GRACE_S)
    for task in late:
        task.cancel()
    for ended in await asyncio.gather(*asks, return_exceptions=True):
        if isinstance(ended, Exception):
            log.warning("opencode_permission_failed", run_id=run_id, error=str(ended), error_type=type(ended).__name__)


async def _session(server: OpenCodeServer, thread_id: str, directory: Path, title: str) -> ChatSession:
    """The OpenCode session of the chat: the one kept for it while OpenCode still has it, else a new one."""
    kept: ChatSession | None = await asyncio.to_thread(_read_chat, thread_id)
    if kept is not None and await server.has_session(kept.session_id, directory):
        return kept
    return ChatSession(session_id=await server.create_session(directory, title), seen=0)


async def _server_of(runtime: Runtime, ref: ModelRef) -> OpenCodeProvider:
    provider: ChatProvider = await runtime.router.provider(ref.provider)
    if not isinstance(provider, OpenCodeProvider):
        raise OpenCodeError("the model's provider is not OpenCode's server", {"model": ref.label})
    return provider


async def run_in_opencode(agent: AgentRun) -> RunEnd:
    """Run ``agent``'s run as OpenCode's own agent (module docstring)."""
    runtime: Runtime = agent.runtime
    run = agent.run
    ref: ModelRef = parse_model_ref(run.model)
    provider: OpenCodeProvider = await _server_of(runtime, ref)
    server: OpenCodeServer = provider.server
    info: ModelInfo = await provider.model_info(ref.model)
    variant: str | None = run.effort if run.effort is not None and run.effort in info.efforts else None
    directory: Path = runtime.workspace
    earlier: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
    async with server.hold():
        chat: ChatSession = await _session(
            server, run.thread_id, directory, truncate_middle(" ".join(agent.opening.text.split()), _TITLE_CHARS)
        )
        goal: Message = await message_with_attachments(
            runtime, run.thread_id, run.id, agent.opening.text, agent.opening.files, info.vision
        )
        if agent.opening.files:
            await runtime.bus.emit(run.id, ATTACHMENTS_EVENT, {"files": attachment_summary(agent.opening.files)}, True)
        await runtime.store.append_messages(run.thread_id, [goal])
        queue: asyncio.Queue[_Item] = asyncio.Queue()
        follow: _Follow = _Follow(
            agent=agent,
            server=server,
            session_id=chat.session_id,
            directory=directory,
            model=ref.model,
            variant=variant,
            vision=info.vision,
            queue=queue,
        )
        async with server.events(directory) as stream:
            pumps: tuple[asyncio.Task[None], ...] = (
                asyncio.create_task(_pump_events(stream, queue), name="opencode-events"),
                asyncio.create_task(_pump_inbox(agent.inbox, queue), name="opencode-inbox"),
                asyncio.create_task(_pump_stop(agent.cancel, queue), name="opencode-stop"),
            )
            ended: bool = False
            try:
                await follow.prompt(
                    meanwhile(goal.content, earlier[chat.seen :]), _prompt_files(agent.opening.files, info.vision)
                )
                answer: str = await follow.follow()
                ended = True
            finally:
                for task in pumps:
                    task.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                if not ended:
                    await asyncio.shield(_stop_agent(server, chat.session_id, directory))
                await _let_go(runtime, run.id, tuple(follow.asks))
        result: str = answer or "OpenCode's agent finished without a closing message."
        await runtime.store.append_messages(run.thread_id, [assistant_message(answer, None, ())])
        await asyncio.to_thread(
            _write_chat, run.thread_id, ChatSession(session_id=chat.session_id, seen=len(earlier) + 2 + follow.added)
        )
    log.info("opencode_run_ended", run_id=run.id, session=chat.session_id, steps=follow.steps)
    return RunEnd(outcome=RunOutcome(status="done", result=result, needs=""), lessons=None)
