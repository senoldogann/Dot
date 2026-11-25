"""A run on one of Zen's free models (item 283). The run goes whole to OpenCode's own agent (:mod:`dotagent.opencode.
server`), in the OpenCode session of its chat, which the agent keeps from run to run; what was said in the chat on other
models since the session last saw it comes with the run's first message. Dot shows what the agent does as its own runs
show theirs: the model's text and thinking as they come, each tool call and its result, each step with its tokens. Each
permission the agent asks for is decided as a tool of Dot's own is (:meth:`dotagent.agent.gate.RunGate.authorize`): by
the owner's rules and preset, the review model or the owner, as the run's approval mode says; a refusal tells the agent
why, and it goes on. The owner's messages go to the agent while it works, and a stop of the run stops it. The run ends
done with the agent's last answer once the agent has nothing left to do, and failed when its session fails, when its
last reply was cut short (the model's output limit) and when it goes on past the run's limits (``run_max_minutes``,
``run_max_tokens``), which it is told of once.

The agent has Dot's own tools besides its own (item 284): every one a run on Claude Code gets but those of what
OpenCode's own tools do (:data:`OPENCODE_OWN`, item 1720), served by Dot's bridge (:mod:`dotagent.opencode.bridge`) at
the chat's door while the run works. A call of one runs as a call of Dot's own loop
(:func:`dotagent.agent.executor.execute_calls`: the run's gate, the tool's time limit, the run's timeline under the
tool's own name), and the session keeps to its own chat's door.

Since item 1695 the agent is told Dot's own system prompt, with each prompt's ``system`` (after OpenCode's own; Zen's
free tier answered with it, measured on 2026-11-24), then which of OpenCode's tools stand for Dot's; its first message
carries how the run was started, the notes recalled for it and the skills that fit its goal; it keeps Dot's plan and
ends with Dot's finish, which the run ends as. A run here has none of the rest of what Dot's own runs bring (the goal
check of a finish, the lessons drawn from a record of the run, and playbooks)."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import JsonValue

from dotagent.agent.attachments import (
    ATTACHMENTS_EVENT,
    StoredAttachment,
    UserInput,
    attachment_summary,
    message_with_attachments,
    steer_event,
)
from dotagent.agent.authorization import Authorization, StepDecision
from dotagent.agent.continuity import new_ledger
from dotagent.agent.gate import masked_action
from dotagent.agent.handover import (
    ChatSession,
    agent_tools,
    bridged_call,
    door_tool,
    handed_notes,
    let_go,
    prompt_facts,
    read_chat,
    tool_context,
    withheld_out,
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
from dotagent.agent.loop import (
    AgentRun,
    PromptFacts,
    RunEnd,
    RunOutcome,
    free_mode_line,
    run_prompt_inputs,
    turn_skills,
)
from dotagent.agent.prompt import PromptInputs, build_system_prompt
from dotagent.agent.streammask import StreamMask, delta_event, said_in_harness
from dotagent.browser.safety import UrlVerdict, browser_host, check_url, host_matches, url_flags
from dotagent.core.errors import (
    ApprovalRejected,
    HandedOff,
    JsonParseError,
    OpenCodeError,
    PolicyDenied,
    RunCancelled,
    ToolInputError,
)
from dotagent.core.jsonutil import parse_json_object
from dotagent.core.logs import get_logger
from dotagent.core.paths import opencode_dir
from dotagent.core.text import redact_json, redact_known, truncate_middle
from dotagent.core.types import JsonObject
from dotagent.llm.opencode import OpenCodeProvider
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import ChatProvider, Message, ModelInfo, StreamDelta, ToolCall, assistant_message, user_message
from dotagent.llm.wire import get_obj, get_str
from dotagent.opencode.bridge import CallRunner, Door, ToolBridge, door_name, new_door, server_config, session_rules
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
from dotagent.policy.types import ActionInfo, Flag, Risk
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.endings import PREFIX_CUT_SHORT
from dotagent.store.models import RunRecord
from dotagent.tools.base import ToolResult
from dotagent.tools.channels import Channel
from dotagent.tools.files import read_action, write_action
from dotagent.tools.groups import BROWSER_GROUP, group_of
from dotagent.tools.meta import FINISH_TOOL, FinishArgs
from dotagent.tools.registry import ToolRegistry, tools_for_mode
from dotagent.web.addresses import is_public_address

log = get_logger(__name__)

# The names of OpenCode's tools in Dot's timeline: ``opencode_bash``, ``opencode_edit``, ...
TOOL_PREFIX: str = "opencode_"
_EVENT_TEXT_CHARS: int = 4000
_RISK_RANK: dict[Risk, int] = {"safe": 0, "low": 1, "medium": 2, "high": 3}
_THINKING_CHARS: int = 12_000
_PREVIEW_CHARS: int = 200
# What of the chat on other models comes with a run's first message: its newest messages, each cut to a length.
_MEANWHILE_MESSAGES: int = 20
_MEANWHILE_CHARS: int = 2000
_TITLE_CHARS: int = 80
# How a step of the agent ended, in OpenCode's words (the ``reason`` of its ``step-finish``): the agent goes on after a
# step that ended with a call of a tool, and stops after any other. A reply that stopped at the model's output limit
# sets no error in OpenCode, which goes idle with the half of an answer; Dot's own loop counts it as cut short (item
# 552), as it does a reply that ended for a reason of the service's own.
_GOES_ON: frozenset[str] = frozenset({"tool-calls", "unknown"})
_CUT_SHORT: frozenset[str] = frozenset({"length", "other"})
# The steps that end after the agent was told that the run's limit is reached before one that goes on ends the run:
# the step that was under way when the note arrived has not read it.
_LIMIT_GRACE_STEPS: int = 2
# What the agent is told when the run's limit is reached: in a mode with finish (bridged since item 1695) to call it, as
# Dot's own loop's note asks, else to write its answer.
_LIMIT_NOTE: str = (
    "[system note] The limit of this run is reached: it has used {spent}. Stop now, in this reply: {ending} with what "
    "was reached (the numbers), what is missing and how to go on. Call no other tool: the run ends after this reply."
)
_LIMIT_FINISH: str = "call finish with success false and your final answer"
_LIMIT_REPLY: str = "write your final answer"
# What the agent is told when it asks for a subagent: the subagent's session would not be the run's, which Dot follows.
NO_SUBAGENT_NOTE: str = "Subagents are not available here: do this part of the work yourself, with your own tools."
# What the agent is told when it makes the same call a third time in a row (OpenCode asks ``doom_loop`` then): Dot's own
# runs get a nudge of the kind for a streak of one action.
DOOM_LOOP_NOTE: str = (
    "You made the same call with the same input three times in a row; it will not give anything new. Try another way,"
    " or say what stops you."
)
# What the agent is told when its webfetch names this Mac or its network, which Dot's own web tools do not reach either.
LOCAL_TARGET_NOTE: str = (
    "That address is on this Mac or its local network, which the web tools do not open ([web] allow_private_hosts)."
)
_WEB_TOOLS: frozenset[str] = frozenset({"webfetch", "websearch", "codesearch"})
# Dot's tools the agent does not get although a run on Claude Code does (:func:`dotagent.agent.handover.door_tool`):
# those of what the agent's own tools do (read, write, edit, list, glob and grep for the files, bash for a command,
# webfetch and websearch for the web; item 284). Every other tool of Dot's is the agent's, as a run on Claude Code has
# it (item 1720): Dot's look, a video, a draft, a wait, a skill saved, the team, the mail and calendars, the office and
# the rest, and since item 1695 finish and the plan's, which Dot's prompt tells it to use.
OPENCODE_OWN: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "find_files",
        "search_files",
        "shell",
        "web_fetch",
        "web_search",
    }
)
# What the agent is told after Dot's own system prompt (item 1695): which of OpenCode's tools stand for Dot's that the
# prompt names, where Dot's other tools are, and that its finish ends the run.
_HARNESS: str = (
    "# This run's harness\n"
    "This run works as OpenCode's agent on one of Zen's free models. OpenCode's own tools do what the instructions "
    "above name Dot's shell, file and web tools for: bash for shell, read, edit and write for read_file, edit_file and "
    "write_file, glob, grep and list for finding and searching files, webfetch and websearch for web_fetch and "
    "web_search. Dot's other tools are those of the MCP server {door} ({door}_<tool>): {tools}. Each call goes through "
    "Dot's rules and, where they say so, the owner's approval; a refused call says why, and you go on another way. "
    "Subagents are not available. {ending} Write every message in the language the owner writes in. Files you make go "
    "in Dot's workspace, {workspace}, unless the owner names another place."
)
_ENDS_WITH_FINISH: str = (
    "When the work is done, or cannot be done, call finish with your final answer, success true only if the goal was "
    "achieved and verified, and needs_from_user for what only the owner can give or decide."
)
_ENDS_WITH_REPLY: str = "When the work is done, write your answer as your last reply: it is what the owner reads."


def harness_prompt(dot_prompt: str, door: str, tools: Sequence[str], workspace: Path) -> str:
    """What OpenCode's agent is told after OpenCode's own prompt (item 1695): ``dot_prompt``, the system prompt Dot's
    own loop builds for the run with the door's ``tools``, then :data:`_HARNESS`."""
    ending: str = _ENDS_WITH_FINISH if FINISH_TOOL in tools else _ENDS_WITH_REPLY
    harness: str = _HARNESS.format(door=door, tools=", ".join(tools), workspace=workspace, ending=ending)
    return f"{dot_prompt}\n\n{harness}"


# What a run says when Zen's free tier turns a request down. Its own words say that the free tier serves OpenCode alone,
# but it says them to OpenCode's own command line as well, for a few minutes after many requests in a short time
# (measured 2026-10-15: refused from 17:48 to about 17:56, answered at 17:57, the same program and setup throughout).
FREE_TIER_REFUSED: str = (
    "Zen's free tier turned the request down for now (FreeTierError). It does so for a few minutes after many requests"
    " in a short time; try again in a few minutes, or pick another model"
)


def _chat_path(thread_id: str) -> Path:
    return opencode_dir() / "chats" / f"{thread_id}.json"


def forget_chat(thread_id: str) -> None:
    """Let the chat's OpenCode session go (item 282): a rewind took out of the chat what the session saw, so the chat's
    next run on OpenCode starts a new session, which reads the chat's history. Blocking."""
    _chat_path(thread_id).unlink(missing_ok=True)


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


def bridged(name: str, delegated: bool) -> bool:
    """Whether Dot's tool ``name`` is one the agent gets besides its own: one a run on Claude Code gets, for a run
    another run handed its work to (``delegated``) too, but for those of :data:`OPENCODE_OWN`."""
    return door_tool(name, delegated) and name not in OPENCODE_OWN


def bridged_tools(registry: ToolRegistry, run: RunRecord, agent_tools: frozenset[str] | None) -> ToolRegistry:
    """The tools of Dot's ``run`` gives the agent: the :func:`bridged` ones of its mode, and of those only the ones
    ``agent_tools`` names when the run is an agent's whose tools are bounded (item 86)."""
    delegated: bool = run.parent_run_id is not None
    return ToolRegistry(
        tools={
            tool.spec.name: tool
            for tool in tools_for_mode(registry, run.mode)
            if bridged(tool.spec.name, delegated) and (agent_tools is None or tool.spec.name in agent_tools)
        }
    )


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


def _written_paths(asked: PermissionAsked, workspace: Path) -> tuple[Path, ...]:
    """Every file an edit OpenCode asks for writes: each of its patterns, one per file (a patch of several files names
    them all there, and its ``filepath`` is their names joined by commas, item 677), else the file it names."""
    raw: tuple[str, ...] = asked.patterns or (get_str(asked.metadata, "filepath") or get_str(asked.metadata, "path"),)
    return tuple(expand_path(pattern or ".", workspace) for pattern in raw)


def _worst(actions: Sequence[ActionInfo]) -> ActionInfo:
    """The action of the file the policy weighs most, with the flags of all of them: a patch is as much as its worst
    file asks for."""
    ranked: list[ActionInfo] = sorted(actions, key=lambda action: (_RISK_RANK[action.risk], len(action.flags)))
    worst: ActionInfo = ranked[-1]
    flags: tuple[Flag, ...] = tuple(dict.fromkeys(flag for action in actions for flag in action.flags))
    if len(actions) == 1:
        return worst
    shown: str = truncate_middle(", ".join(action.target for action in actions), _PREVIEW_CHARS)
    # Every file in the target, one a line, so that a rule of the owner's and the review model read each of them, not
    # the worst alone (the code review of item 677).
    targets: str = "\n".join(action.target for action in actions)
    summary: str = f"Write {len(actions)} files: {shown}"
    return worst.model_copy(update={"flags": flags, "summary": summary, "target": targets})


def local_target(url: str) -> bool:
    """Whether ``url`` names this Mac or its network by itself: ``localhost`` or a name under it, or an address that is
    not public in any spelling a browser reads (the code review's Y1). A name that resolves to such an address is not
    seen here, as OpenCode looks names up itself."""
    host: str | None = browser_host(urlsplit(url).hostname or "")
    if host is None:
        return True
    bare: str = host.removesuffix(".")
    if host_matches(bare, ("localhost",)):
        return True
    try:
        return not is_public_address(bare)
    except ValueError:
        return False


def permission_action(
    asked: PermissionAsked,
    workspace: Path,
    protected: tuple[Path, ...],
    dot_paths: tuple[Path, ...],
    workdir: str | None = None,
) -> ActionInfo:
    """A permission OpenCode's agent asks for, as Dot's policy weighs the same action of one of its own tools: a command
    as the shell tool's, in the folder it runs in (``workdir``, the call's own, item 677: the workspace when it names
    none), an edit as a write of each file it writes, a read, a search or a folder outside the run's as a read, a fetch
    or a search of the web as reading the web. A permission of a kind Dot does not know is high risk."""
    tool: str = f"{TOOL_PREFIX}{asked.permission}"
    match asked.permission:
        case "bash":
            command: str = get_str(asked.metadata, "command") or _patterns(asked)
            folder: str | None = workdir or get_str(asked.metadata, "workdir") or None
            cwd: Path = workspace if folder is None else expand_path(folder, workspace)
            assessment: ShellAssessment = assess_shell(command, cwd, workspace, protected, dot_paths)
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
            edit: ActionInfo = _worst(
                [
                    write_action(tool, path, workspace, protected, dot_paths, "Write")
                    for path in _written_paths(asked, workspace)
                ]
            )
            # OpenCode's agent writes outside Dot's file tools and their undo journal: a change of Dot's own files,
            # which those tools make once the user agrees (item 398), stays refused to it outside yolo (item 433).
            return edit.model_copy(update={"flags": ("secret_access",)}) if "self_change" in edit.flags else edit
        case "read" | "list" | "glob" | "grep" | "external_directory":
            return read_action(tool, _target_path(asked, workspace), workspace, protected, dot_paths, "Read")
        case "webfetch":
            url: str = get_str(asked.metadata, "url") or _patterns(asked)
            return ActionInfo(
                tool=tool, kind="network", risk="low", summary=f"Fetch {url}", target=url, flags=url_flags(url)
            )
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
    """A tool call of the agent as the run follows it: its policy action once it asked, the time it waited there, and
    what decided it when the gate let it through (item 474)."""

    announced: bool = False
    # What the agent called the tool with, once OpenCode showed the call (the folder a command runs in, item 677).
    arguments: JsonObject = field(default_factory=dict)
    action: ActionInfo | None = None
    waited_s: float = 0.0
    decision: StepDecision | None = None

    def asked(self, action: ActionInfo) -> None:
        """The agent asks to act for this call. OpenCode may ask twice for one call (a folder outside the workspace,
        then the edit itself): the call's action and decision are those of its last ask, the decision only once the
        gate lets it through."""
        self.action = action
        self.decision = None


def step_result(
    event: ToolSeen, name: str, call: _Call, text: str, seconds: float, tainted: bool, known: Sequence[str]
) -> JsonObject:
    """The ``tool_result`` event of one of OpenCode's calls, as the executor writes one of Dot's own: a call that asked
    the gate keeps its action, its flags and what decided it (item 474: ``None`` for one the gate did not let through);
    one that never asked keeps OpenCode's title as its summary, and no action. The secrets Dot holds (``known``) are
    masked in what it says of the call, as they are in the executor's (item 551)."""
    action: ActionInfo | None = None if call.action is None else masked_action(call.action, known)
    return {
        "call_id": event.call_id,
        "tool": name,
        "ok": event.status == "completed",
        "text": truncate_middle(redact_known(text, known), _EVENT_TEXT_CHARS),
        "images": [],
        "seconds": round(seconds, 2),
        "waited_s": round(call.waited_s, 2),
        "summary": action.summary if action is not None else redact_known(event.title, known),
        "kind": action.kind if action is not None else "",
        "risk": action.risk if action is not None else "",
        "target": action.target if action is not None else "",
        "site": "",
        "steps": [],
        "channel": _channel(event.tool),
        "tainted": tainted,
        "decision": None if call.decision is None else call.decision.model_dump(mode="json"),
        "flags": [] if action is None else list(action.flags),
    }


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
    # What the names of Dot's own tools start with in the session (``dot_1a2b3c4d_``): their calls show as Dot's.
    door: str
    # When the run began (monotonic seconds) and its limits (``[agent] run_max_minutes`` and ``run_max_tokens``, which
    # the loop of Dot's own runs reads between its steps too, item 552), the limit the agent was told of and how many
    # steps ended since, and the limit once the agent went on regardless: the run ends failed then.
    started: float = field(default_factory=time.monotonic)
    limits: RunLimits | None = None
    told: LimitReached | None = None
    since_told: int = 0
    over: LimitReached | None = None
    # Dot's system prompt for the run (item 1695), sent with every prompt: OpenCode keeps a prompt's system to its turn;
    # and whether the agent has Dot's finish (a mode that ends with one).
    system: str = ""
    finishes: bool = False
    # The agent's finishes since its last other call (item 1695): the run ends as the newest says.
    finished_calls: list[FinishArgs] = field(default_factory=list)
    # The step (its message) that called Dot's finish last: a call of OpenCode's own in that same step ran beside it,
    # not after it, and drops nothing.
    finish_step: str | None = None
    # What the agent's finish said (item 1695), ``None`` until it calls it, and again after a later call of Dot's tools.
    finished: FinishArgs | None = None
    # How the newest step ended, in OpenCode's words.
    finish: str = ""
    steps: int = 0
    prompts: int = 0
    users: set[str] = field(default_factory=set)
    assistants: dict[str, _Step] = field(default_factory=dict)
    calls: dict[str, _Call] = field(default_factory=dict)
    answer: str = ""
    added: int = 0
    asks: set[asyncio.Task[None]] = field(default_factory=set)
    idle: bool = False
    # What the agent writes and thinks, as it comes, masked as the step's stored message is (item 551).
    stream: StreamMask = field(init=False)

    def __post_init__(self) -> None:
        self.stream = StreamMask(self.show)

    @property
    def runtime(self) -> Runtime:
        return self.agent.runtime

    async def show(self, delta: StreamDelta) -> None:
        await self.emit("delta", delta_event(delta, self.steps + 1, said_in_harness(self.agent.run.mode)), False)

    async def prompt(self, text: str, files: tuple[PromptFile, ...]) -> None:
        self.prompts += 1
        await self.server.prompt(self.session_id, self.directory, self.model, self.variant, text, files, self.system)

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
                self.stream.begin(await asyncio.to_thread(known_secrets, filled=True))
                self.assistants.setdefault(event.message_id, _Step(started=time.monotonic())).started = time.monotonic()
            case PartSeen():
                await self.part(event)
            case Piece():
                await self.piece(event)
            case ToolSeen():
                await self.tool(event)
            case StepEnded():
                await self.step_ended(event)
                return self.over is not None
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
        await self.stream(StreamDelta(kind=kind, text=event.text))

    async def tool(self, event: ToolSeen) -> None:
        step: _Step | None = self.step_of(event.message_id)
        if step is None:
            return
        call: _Call = self.calls.setdefault(event.call_id, _Call())
        if event.input:
            call.arguments = event.input
        if event.status == "pending":
            return
        if event.tool.startswith(self.door):
            # A tool of Dot's: the call runs as Dot's loop runs one, which shows it in the timeline under its own name.
            if not call.announced:
                call.announced = True
                step.tools.append(event.tool.removeprefix(self.door))
                if event.tool.removeprefix(self.door) == FINISH_TOOL:
                    self.finish_step = event.message_id
            return
        name: str = f"{TOOL_PREFIX}{event.tool}"
        # The secrets Dot holds are masked word for word, as the executor masks those of its own calls (item 525).
        known: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        if not call.announced:
            # A call of OpenCode's own tool after a finish is work, which drops what the finish said, as any call does
            # on the Claude plan (item 1695's pre-push review); one of the finish's own step ran beside it and is not.
            if event.message_id != self.finish_step:
                self.finished_calls.clear()
            call.announced = True
            step.tools.append(name)
            await self.emit(
                "tool_call", {"call_id": event.call_id, "tool": name, "args": redact_json(event.input, known)}, True
            )
        if event.status == "running":
            return
        ok: bool = event.status == "completed"
        if ok and call.action is not None:
            self.agent.gate.observe(call.action)
            self.runtime.peers.saw(self.agent.run.id, name, call.action)
        seconds: float = (
            (event.ended_ms - event.started_ms) / 1000
            if event.ended_ms is not None and event.started_ms is not None
            else 0.0
        )
        result: JsonObject = step_result(
            event, name, call, event.output if ok else event.error, seconds, self.agent.gate.tainted, known
        )
        await self.emit("tool_result", result, True)

    async def step_ended(self, event: StepEnded) -> None:
        step: _Step | None = self.step_of(event.message_id)
        if step is None:
            return
        # What the stream still holds of the step (the word being written, item 551) goes out under the step's own
        # number: let go after the step was counted, it came under the next one, and the page drew it as a message of
        # its own and left the step's message streaming, the answer under both (item 660).
        await self.stream.flush()
        self.steps += 1
        self.finish = event.reason
        run_id: str = self.agent.run.id
        await self.runtime.store.add_run_tokens(run_id, event.input_tokens, event.output_tokens)
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
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cached_tokens": event.cached_tokens,
                "seconds": round(time.monotonic() - step.started, 3),
                "finish": event.reason,
            },
            True,
        )
        if event.reason in _GOES_ON:
            await self.keep_to_limits()

    async def keep_to_limits(self) -> None:
        """Read the run's limits after a step that the agent goes on from, as the loop reads them between its steps
        (item 552): the first step that ends past one tells the agent to stop and answer, and a run whose agent still
        goes on after :data:`_LIMIT_GRACE_STEPS` more steps is over (``over``): it ends failed and the agent is
        stopped."""
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
        log.info("opencode_limit_reached", run_id=self.agent.run.id, spent=limit_spent(reached))
        await self.emit(LIMIT_EVENT, limit_event_data(reached), True)
        ending: str = _LIMIT_FINISH if self.finishes else _LIMIT_REPLY
        await self.prompt(_LIMIT_NOTE.format(spent=limit_spent(reached), ending=ending), ())

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
        workdir: JsonValue = call.arguments.get("workdir")
        try:
            action: ActionInfo = permission_action(
                event,
                self.directory,
                runtime.protected,
                runtime.dot_paths,
                workdir if isinstance(workdir, str) and workdir else None,
            )
        except ToolInputError as refused:
            # A step that cannot be told (a path or a line with a NUL character, item 727) is refused, the run goes on.
            await self.server.reply(event.request_id, self.directory, "reject", refused.message)
            return
        call.asked(action)
        # An edit that would step on another run at work is refused, as a call of Dot's own tools is (item 301).
        held: str | None = runtime.peers.conflict(self.agent.run.id, action.tool, action)
        if held is not None:
            await self.server.reply(event.request_id, self.directory, "reject", held)
            return
        if action.kind == "network" and event.permission == "webfetch":
            verdict: UrlVerdict = check_url(
                action.target, runtime.config.browser.allowed_hosts, runtime.config.browser.blocked_hosts
            )
            if not verdict.allowed:
                await self.server.reply(event.request_id, self.directory, "reject", verdict.reason)
                return
            if not runtime.config.web.allow_private_hosts and local_target(action.target):
                await self.server.reply(event.request_id, self.directory, "reject", LOCAL_TARGET_NOTE)
                return
        asked_at: float = time.monotonic()
        try:
            # What OpenCode asks for, whole: an action's target may show it cut (item 525).
            asked: JsonObject = {"patterns": list(event.patterns), "metadata": event.metadata}
            authorized: Authorization = await self.agent.gate.authorize_recorded(action, asked)
            call.decision = authorized.decision
            note: str = authorized.note
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
        # As Dot's own loop takes it (item 902): one place for what a message does to the run's trust.
        self.agent.gate.take(sent)
        await runtime.store.append_messages(self.agent.run.thread_id, [note])
        self.added += 1
        await self.emit("steer", steer_event(sent, self.agent.gate.tainted), True)
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


async def _session(server: OpenCodeServer, thread_id: str, directory: Path, title: str) -> ChatSession:
    """The OpenCode session of the chat: the one kept for it while OpenCode still has it, else a new one."""
    kept: ChatSession | None = await asyncio.to_thread(read_chat, _chat_path(thread_id))
    if kept is not None and await server.has_session(kept.session_id, directory):
        return kept
    return ChatSession(session_id=await server.create_session(directory, title), seen=0)


async def _drop_door(server: OpenCodeServer, directory: Path, name: str) -> None:
    """Take Dot's tools of a run that ends away from OpenCode; a failure is logged, the run's own ending is what counts
    (a server started later has none of them in any case)."""
    try:
        await server.drop_mcp(directory, name)
    except OpenCodeError as error:
        log.warning("opencode_drop_tools_failed", door=name, error=error.message, context=error.context)


@asynccontextmanager
async def _dot_tools(bridge: ToolBridge, server: OpenCodeServer, directory: Path, door: Door) -> AsyncIterator[None]:
    """Dot's tools at ``door`` for the agents of ``directory`` while the block runs; nothing when it has none."""
    if not door.specs:
        yield
        return
    async with bridge.opened(door) as address:
        await server.add_mcp(directory, door.name, server_config(address, door))
        try:
            yield
        finally:
            await asyncio.shield(_drop_door(server, directory, door.name))


async def _server_of(runtime: Runtime, ref: ModelRef) -> OpenCodeProvider:
    provider: ChatProvider = await runtime.router.provider(ref.provider)
    if not isinstance(provider, OpenCodeProvider):
        raise OpenCodeError("the model's provider is not OpenCode's server", {"model": ref.label})
    return provider


def _outcome(follow: _Follow, answer: str, goal: str) -> RunOutcome:
    """How the run ends. Failed when its limit was reached and the agent went on regardless, and when the agent's last
    reply was cut short, at the model's output limit or for a reason of the service's own, as Dot's own loop ends a run
    whose replies it cannot get whole (item 552); else done with the agent's last answer, or with a line that says it
    gave none."""
    if follow.over is not None:
        result: str = limit_result(follow.over, new_ledger(goal), "", answer)
        return RunOutcome(status="failed", result=result, needs="")
    if follow.finish in _CUT_SHORT:
        cut: str = f"{PREFIX_CUT_SHORT} and the run did not finish (the last one ended with '{follow.finish}')."
        return RunOutcome(status="failed", result=cut, needs="")
    if follow.finished is not None:
        # As the agent's finish says (item 1695): done only when it succeeded, with what it needs from the owner.
        return RunOutcome(
            status="done" if follow.finished.success else "failed",
            result=follow.finished.answer,
            needs=follow.finished.needs_from_user or "",
        )
    return RunOutcome(status="done", result=answer or "OpenCode's agent finished without a closing message.", needs="")


async def run_in_opencode(agent: AgentRun) -> RunEnd:
    """Run ``agent``'s run as OpenCode's own agent (module docstring)."""
    started: float = time.monotonic()
    runtime: Runtime = agent.runtime
    run = agent.run
    # What the message came with stands under it at once (item 967), before OpenCode's server and session start.
    if agent.opening.files:
        await runtime.bus.emit(run.id, ATTACHMENTS_EVENT, {"files": attachment_summary(agent.opening.files)}, True)
    ref: ModelRef = parse_model_ref(run.model)
    provider: OpenCodeProvider = await _server_of(runtime, ref)
    # Outside yolo the agent works in the sandbox of the run's commands, a server of its own (item 677).
    server: OpenCodeServer = provider.server.confined(
        () if run.approvals == "yolo" else runtime.sandbox.current().commands
    )
    info: ModelInfo = await provider.model_info(ref.model)
    variant: str | None = run.effort if run.effort is not None and run.effort in info.efforts else None
    directory: Path = runtime.workspace
    earlier: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
    # Those Dot's own loop withholds from the run as it starts are left out here too (items 1063 and 1068).
    tools: ToolRegistry = await withheld_out(
        runtime, run, bridged_tools(runtime.registry, run, await agent_tools(runtime, run.agent_id)), ref.label
    )
    # Settings › Browser may keep every run off the browser (item 354), OpenCode's agent too.
    if not runtime.browser.config.enabled:
        tools = ToolRegistry(
            tools={name: tool for name, tool in tools.tools.items() if group_of(name) != BROWSER_GROUP}
        )
    finished: list[FinishArgs] = []
    door: Door = new_door(
        door_name(run.thread_id),
        tuple(tool.spec for tool in tools.tools.values()),
        # OpenCode puts every tool of a server in its prompt: none is looked up.
        frozenset(),
        partial(
            _noting_finish,
            finished,
            partial(
                bridged_call, tools, await tool_context(agent, ref, info, frozenset(tools.tools), None), agent.gate
            ),
        ),
    )
    async with server.hold():
        chat: ChatSession = await _session(
            server, run.thread_id, directory, truncate_middle(" ".join(agent.opening.text.split()), _TITLE_CHARS)
        )
        # The session keeps to its own chat's door: the tools of other chats' doors are hidden from its model.
        await server.keep_to(chat.session_id, directory, session_rules(door.name))
        goal: Message = await message_with_attachments(
            runtime, run.thread_id, run.id, agent.opening.text, agent.opening.files, info.vision
        )
        # Dot's own system prompt and the notes under the message (item 1695), as a run on the plan has them.
        facts: PromptFacts = await prompt_facts(agent, ref, frozenset(tools.tools))
        inputs: PromptInputs = await run_prompt_inputs(agent, facts, door.specs)
        heard: list[Message] = earlier[: chat.seen]
        carried: str = "\n".join(message.content for message in heard if message.role == "user")
        noted: Message = user_message(await handed_notes(agent, info, carried, inputs, turn_skills(facts)), ())
        await runtime.store.append_messages(run.thread_id, [goal, noted])
        system: str = harness_prompt(
            build_system_prompt(inputs) + free_mode_line(runtime),
            door.name,
            [spec.name for spec in door.specs],
            directory,
        )
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
            door=f"{door.name}_",
            started=started,
            limits=limits_of(runtime.config.agent, run.mode),
            system=system,
            finishes=FINISH_TOOL in tools.tools,
            finished_calls=finished,
        )
        async with _dot_tools(provider.bridge, server, directory, door), server.events(directory) as stream:
            pumps: tuple[asyncio.Task[None], ...] = (
                asyncio.create_task(_pump_events(stream, queue), name="opencode-events"),
                asyncio.create_task(_pump_inbox(agent.inbox, queue), name="opencode-inbox"),
                asyncio.create_task(_pump_stop(agent.cancel, queue), name="opencode-stop"),
            )
            ended: bool = False
            try:
                await follow.prompt(
                    meanwhile(f"{goal.content}\n\n{noted.content}", earlier[chat.seen :]),
                    _prompt_files(agent.opening.files, info.vision),
                )
                answer: str = await follow.follow()
                # An agent that went on past the run's limit is stopped; the run's own ending is the failure.
                ended = follow.over is None
            finally:
                for task in pumps:
                    task.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
                if not ended:
                    await asyncio.shield(_stop_agent(server, chat.session_id, directory))
                await let_go(runtime, run.id, tuple(follow.asks))
        # The chat keeps the answer of the agent's finish when it called one, as a run on the plan's does: a line it
        # wrote after it is often empty, and a later run would read nothing of the answer (the pre-push review).
        closing: str = finished[-1].answer if finished else answer
        await runtime.store.append_messages(run.thread_id, [assistant_message(closing, None, ())])
        await asyncio.to_thread(
            write_chat,
            _chat_path(run.thread_id),
            ChatSession(session_id=chat.session_id, seen=len(earlier) + 3 + follow.added),
        )
    log.info("opencode_run_ended", run_id=run.id, session=chat.session_id, steps=follow.steps)
    follow.finished = finished[-1] if finished else None
    return RunEnd(outcome=_outcome(follow, answer, run.goal), lessons=None)


async def _noting_finish(finished: list[FinishArgs], run: CallRunner, call: ToolCall) -> ToolResult:
    """A call of Dot's tool for the agent, its finish noted in ``finished`` (item 1695): the run ends as the agent's
    last finish says, unless it called another of Dot's tools after it, which drops it."""
    result: ToolResult = await run(call)
    if call.name == FINISH_TOOL and not result.is_error:
        finished.append(FinishArgs.model_validate(call.arguments))
    elif call.name != FINISH_TOOL:
        finished.clear()
    return result
