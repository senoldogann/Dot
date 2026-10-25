"""Tool execution: validate, authorise, run under a timeout, and turn every failure into a readable result."""

import asyncio
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from dotagent.agent.authorization import Authorization, StepDecision
from dotagent.agent.gate import RunGate, hold_note, masked_action
from dotagent.agent.playbooks import arguments_name_dot, daemon_addresses, dot_names
from dotagent.core.errors import (
    ApprovalParked,
    ApprovalRejected,
    DotError,
    FailSafeTriggered,
    HandedOff,
    McpError,
    PolicyDenied,
    RunCancelled,
    ToolInputError,
    WebError,
    WebTransportError,
)
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_json, redact_known, strip_blobs_keep_text, truncate_middle
from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolCall, invalid_arguments_text
from dotagent.policy.types import TAINTING_KINDS, ActionInfo
from dotagent.secrets import known_secrets
from dotagent.tools.apple import APPLE_SCRIPT_TOOL, script_apps
from dotagent.tools.base import PreparedCall, RegisteredTool, ToolResult, fail, ok
from dotagent.tools.channels import channel_of
from dotagent.tools.context import Gate, ToolContext
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.registry import ToolRegistry

log = get_logger(__name__)

_EVENT_TEXT_CHARS: int = 4000
_BROKEN_ARGUMENTS_CHARS: int = 600
# The kinds of action whose target does not show what the call acts on: an MCP call's is the name of its server.
_BY_ARGUMENTS: frozenset[str] = frozenset({"mcp", "mcp_read"})


@dataclass(frozen=True)
class ToolOutcome:
    call: ToolCall
    result: ToolResult
    seconds: float
    action: ActionInfo | None


def _safe_json(value: JsonObject, known: Sequence[str]) -> JsonObject:
    # A command or a script longer than a blob's size stays readable in the call's row; only a picture's base64 goes.
    # Secrets are masked first: a cut in the middle of a key would leave halves no pattern knows (item 431), and the
    # secrets Dot holds (``known``) word for word, whatever their form (item 525).
    sanitized = strip_blobs_keep_text(redact_json(value, known), _EVENT_TEXT_CHARS)
    return sanitized if isinstance(sanitized, dict) else {}


def reached_sites(call: ToolCall, action: ActionInfo | None) -> tuple[str, ...]:
    """The sites a call reached: the host of the page for a browser action, the application for a desktop one and the
    applications a script names (item 308), and none for every other tool (a file path or a command is not a site)."""
    if action is None:
        return ()
    if action.kind.startswith("browser_"):
        host: str = urlsplit(action.target).hostname or ""
        return (host,) if host else ()
    if call.name == APPLE_SCRIPT_TOOL:
        return script_apps(action.target)
    return (action.target,) if call.name.startswith("desktop_") and action.target.strip() else ()


def _site(call: ToolCall, action: ActionInfo | None) -> str:
    """Where a call acted, for the statistics and the site of a learned skill: the first site it reached."""
    return next(iter(reached_sites(call, action)), "")


def _names_dot(call: ToolCall, action: ActionInfo | None, ctx: ToolContext) -> str:
    """What the arguments of an MCP call name that Dot guards or loads, or ``""`` (item 585). The policy knows nothing
    of what an MCP server does with its arguments, and the step's target is the server's name, so the skill bar
    (:func:`dotagent.agent.playbooks.touched_dot`) could not tell a write of Dot's configuration through a server of
    files from a write of a note. It is decided here, where the call has been classified and its arguments and the
    places Dot guards are at hand, and stored with the step."""
    if action is None or action.kind not in _BY_ARGUMENTS:
        return ""
    runtime = ctx.runtime
    names: tuple[str, ...] = dot_names(
        (*runtime.protected, *runtime.dot_paths), daemon_addresses(runtime.config.server.port), Path.home()
    )
    return arguments_name_dot(call.arguments, names) or ""


def batch_steps(arguments: JsonObject) -> int:
    """How many steps a batched call (``browser_act``, ``desktop_act``) carried; 0 for a call without a step list."""
    steps: object = arguments.get("steps")
    return len(steps) if isinstance(steps, list) else 0


def _describe_failure(exc: Exception, tool: RegisteredTool) -> str:
    """The text the model sees for a call that failed: what an expected error says, or the kind and the words of one
    that no tool expects (a fault in Dot, not in what the call asked)."""
    if isinstance(exc, ToolInputError):
        return exc.message
    if isinstance(exc, ApprovalParked):
        # Kept for the owner (item 482): its words say it did not happen, that it waits, and how to go on.
        return exc.message
    if isinstance(exc, PolicyDenied | ApprovalRejected):
        return f"Not allowed: {exc.message}. Do not retry this action or look for a way around it."
    if isinstance(exc, HandedOff):
        return f"The user handled this step themselves ({exc.message}). Continue from there."
    if isinstance(exc, DotError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return f"{tool.spec.name} timed out after {tool.timeout_s:.0f}s."
    if isinstance(exc, OSError):
        return f"Operating system error: {exc}"
    return f"{tool.spec.name} failed with an unexpected error ({type(exc).__name__}): {exc}"


def _may_hold_outside_words(exc: Exception) -> bool:
    """Whether the error of a call that reached outside may hold words that third parties write: what an MCP server
    answered a call with, and what the engines of a search answered with (item 551). A failure in transit holds none,
    nor does a refusal of the gate."""
    return isinstance(exc, McpError | WebError) and not isinstance(exc, WebTransportError)


class _GateTime:
    """The seconds one call spends at the gate: its own question (the gate's checks, an approval, the review model's
    answer) and the questions its tool asks while it runs."""

    def __init__(self) -> None:
        self.seconds: float = 0.0


class _PausedClock:
    """The gate a running tool asks again for a step it did not know about up front. The tool's own timeout stands
    still while the user decides: waiting for an answer is not time the tool spent working. The time these questions
    take is added to ``spent``."""

    def __init__(self, inner: Gate, scope: asyncio.Timeout, spent: _GateTime) -> None:
        self._inner: Gate = inner
        self._scope: asyncio.Timeout = scope
        self._spent: _GateTime = spent

    async def authorize(self, info: ActionInfo) -> str:
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        deadline: float | None = self._scope.when()
        remaining: float | None = None if deadline is None else max(deadline - loop.time(), 0.0)
        self._scope.reschedule(None)
        asked_at: float = time.monotonic()
        try:
            return await self._inner.authorize(info)
        finally:
            self._spent.seconds += time.monotonic() - asked_at
            self._scope.reschedule(None if remaining is None else loop.time() + remaining)

    def add_instruction(self, text: str) -> None:
        self._inner.add_instruction(text)


def _with_approval_note(result: ToolResult, note: str) -> ToolResult:
    """The result followed by what the user wrote when approving the action, so the model reads both together."""
    if note == "":
        return result
    return result.model_copy(update={"text": f"{result.text}\n\n[The user approved this action and wrote: “{note}”]"})


@dataclass(frozen=True)
class _Checked:
    """What a call that reached its tool came to. ``waited_s`` is the time it spent at the gate (:class:`_GateTime`),
    which its seconds include; ``decision`` what the gate decided of it (item 468), ``None`` when it did not let it
    through or the call never reached it."""

    result: ToolResult
    action: ActionInfo | None
    waited_s: float
    decision: StepDecision | None


async def _run_checked(call: ToolCall, tool: RegisteredTool, ctx: ToolContext, gate: RunGate) -> _Checked:
    action: ActionInfo | None = None
    at_gate: _GateTime = _GateTime()
    # What the user wrote when approving the call: the model reads it with the result, a failed one's too.
    note: str = ""
    decision: StepDecision | None = None
    try:
        prepared: PreparedCall = await tool.prepare(call.arguments, ctx)
        action = prepared.action
        # A call that would step on another run at work does not run, and is not asked about (item 301).
        held: str | None = ctx.runtime.peers.conflict(ctx.run_id, call.name, action)
        if held is not None:
            return _Checked(result=fail(held), action=action, waited_s=0.0, decision=None)
        asked_at: float = time.monotonic()
        try:
            # The call's arguments go with it: a run that read outside content sends no secret out in them (item 525).
            authorized: Authorization = await gate.authorize_recorded(action, call.arguments, prepared.weighed)
            note, decision = authorized.note, authorized.decision
            # The user may hold the run (a takeover of the browser): the call waits here, not on its own clock.
            held_s: float = await gate.wait_free()
        finally:
            at_gate.seconds += time.monotonic() - asked_at
        # The wait may have been long, and another run may have taken the app or changed the file since: checked again,
        # and from here the check and the footprint are one step, before the call runs (item 305).
        taken: str | None = ctx.runtime.peers.take(ctx.run_id, call.name, action)
        if taken is not None:
            return _Checked(
                result=_with_approval_note(fail(taken), note),
                action=action,
                waited_s=at_gate.seconds,
                decision=decision,
            )
        async with asyncio.timeout(tool.timeout_s) as scope:
            result: ToolResult = await prepared.run(replace(ctx, gate=_PausedClock(ctx.gate, scope, at_gate)))
    except (RunCancelled, FailSafeTriggered):
        raise
    except Exception as exc:
        # Any error fails the call, not the run: the model reads what went wrong and goes on. One that no tool expects
        # is a fault in Dot, logged with its traceback.
        if not isinstance(exc, DotError | TimeoutError | OSError):
            log.exception("tool_crashed", run_id=ctx.run_id, tool=tool.spec.name)
        text: str = _describe_failure(exc, tool)
        # The model reads the error of a call that reached outside as it reads an answer: the run is untrusted from then
        # on when the error may hold words that third parties write (item 551).
        if action is not None and action.kind in TAINTING_KINDS and _may_hold_outside_words(exc):
            gate.taint()
        handed_off: bool = isinstance(exc, HandedOff)
        failed: ToolResult = _with_approval_note(ok(text) if handed_off else fail(text), note)
        return _Checked(result=failed, action=action, waited_s=at_gate.seconds, decision=decision)
    if action is not None and not result.is_error:
        gate.observe(action)
        ctx.runtime.peers.saw(ctx.run_id, call.name, action)
    if result.untrusted:
        gate.taint()
    noted: ToolResult = _with_approval_note(result, note)
    if held_s > 0.0:
        noted = noted.model_copy(update={"text": f"{noted.text}\n\n{hold_note(held_s)}"})
    return _Checked(result=noted, action=action, waited_s=at_gate.seconds, decision=decision)


def _turn_on_action(tool: str, group: str) -> ActionInfo:
    return ActionInfo(
        tool=tool, kind="meta", risk="safe", summary=f"Turn on the tool group {group}", target="", flags=()
    )


async def _execute_one(
    call: ToolCall, registry: ToolRegistry, hidden: Mapping[str, str], ctx: ToolContext, gate: RunGate
) -> ToolOutcome:
    started: float = time.monotonic()
    # The secrets Dot holds, masked in the step's events as the forms of keys are.
    known: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
    await ctx.runtime.bus.emit(
        ctx.run_id,
        "tool_call",
        {"call_id": call.id, "tool": call.name, "args": _safe_json(call.arguments, known)},
        True,
    )
    tool: RegisteredTool | None = registry.tools.get(call.name)
    action: ActionInfo | None = None
    waited_s: float = 0.0
    decision: StepDecision | None = None
    group: str | None = hidden.get(call.name)
    if group is not None:
        # The model could not know the tool's parameters: it was not in the tool list. The run turns its group on.
        action = _turn_on_action(call.name, group)
        result: ToolResult = fail(
            f"{call.name} did not run: it is in the tool group {group}, which was off. The group is on now: call "
            f"{call.name} again."
        )
    elif tool is None:
        available: str = ", ".join(sorted(name for name, entry in registry.tools.items() if ctx.mode in entry.modes))
        result = fail(f"Unknown tool '{call.name}'. Available tools: {available}.")
    elif ctx.mode not in tool.modes:
        result = fail(f"Tool '{call.name}' is not available in {ctx.mode} mode.")
    elif (broken := invalid_arguments_text(call.arguments)) is not None:
        result = fail(
            f"The arguments of {call.name} were not valid JSON, so nothing ran. "
            f"Send the call again with one well-formed JSON object. What you sent: "
            f"{truncate_middle(broken, _BROKEN_ARGUMENTS_CHARS)}"
        )
    else:
        checked: _Checked = await _run_checked(call, tool, ctx, gate)
        result, action, waited_s, decision = checked.result, checked.action, checked.waited_s, checked.decision
    clipped: ToolResult = result.model_copy(update={"text": truncate_middle(result.text, ctx.output_chars)})
    seconds: float = time.monotonic() - started
    files: list[str] = await ctx.runtime.store.save_images(clipped.images) if clipped.images else []
    # The step as its record shows it: a command or an address carries the secrets Dot holds as the call's arguments do,
    # masked there and so here (item 551). What the loop and the peers read of the call stays as it is.
    shown: ActionInfo | None = None if action is None else masked_action(action, known)
    await ctx.runtime.bus.emit(
        ctx.run_id,
        "tool_result",
        {
            "call_id": call.id,
            "tool": call.name,
            "ok": not clipped.is_error,
            "text": truncate_middle(redact_known(clipped.text, known), _EVENT_TEXT_CHARS),
            "images": list(files),
            "seconds": round(seconds, 2),
            "waited_s": round(waited_s, 2),
            "summary": shown.summary if shown is not None else "",
            "kind": action.kind if action is not None else "",
            "risk": action.risk if action is not None else "",
            "target": shown.target if shown is not None else "",
            "site": redact_known(_site(call, action), known),
            "steps": batch_steps(call.arguments),
            "channel": channel_of(call.name),
            # Whether the run is tainted after this result: a run that goes on from it takes it over (item 243).
            "tainted": gate.tainted,
            # What the policy decided of the step and who answered (item 468), and the flags its action raised; a step
            # inside a batch is not one of these: the batch is, and a step of it that asked has its policy event.
            "decision": None if decision is None else decision.model_dump(mode="json"),
            "flags": [] if action is None else list(action.flags),
            # What an MCP call's arguments name of Dot's own places, address or program, which its target does not show
            # (item 585): a place of Dot's, not a secret, so it is stored as it is.
            "names_dot": _names_dot(call, action, ctx),
        },
        True,
    )
    return ToolOutcome(call=call, result=clipped, seconds=seconds, action=action)


def _skipped(call: ToolCall) -> ToolOutcome:
    return ToolOutcome(call=call, result=fail("Skipped: the run already finished."), seconds=0.0, action=None)


def _group_calls(calls: Sequence[ToolCall], registry: ToolRegistry) -> list[list[ToolCall]]:
    """Consecutive calls to parallel-safe tools form one group; every other call stands alone."""
    groups: list[list[ToolCall]] = []
    previous_parallel: bool = False
    for call in calls:
        tool: RegisteredTool | None = registry.tools.get(call.name)
        parallel: bool = tool is not None and tool.parallel_safe
        if parallel and previous_parallel:
            groups[-1].append(call)
        else:
            groups.append([call])
        previous_parallel = parallel
    return groups


async def execute_calls(
    calls: Sequence[ToolCall],
    registry: ToolRegistry,
    hidden: Mapping[str, str],
    ctx: ToolContext,
    gate: RunGate,
    parallelism: int,
    ended: Callable[[ToolOutcome], None],
) -> list[ToolOutcome]:
    """Run the calls of one model turn in order, overlapping read-only ones. Results keep the call order; the text of
    each is clipped to the run's ``ctx.output_chars``. ``hidden`` maps the tools of the tool groups that are off to
    their group: a call of one of them does not run, and its result says to call it again now that its group is on.
    ``ended`` is told each outcome as its call ends, so that a step cut part way still has the calls that had ended
    (item 159)."""
    finish_index: int | None = next((index for index, call in enumerate(calls) if call.name == FINISH_TOOL), None)
    runnable: Sequence[ToolCall] = calls if finish_index is None else calls[: finish_index + 1]
    skipped: list[ToolOutcome] = [] if finish_index is None else [_skipped(call) for call in calls[finish_index + 1 :]]
    slots: asyncio.Semaphore = asyncio.Semaphore(parallelism)

    async def one(call: ToolCall) -> ToolOutcome:
        outcome: ToolOutcome = await _execute_one(call, registry, hidden, ctx, gate)
        ended(outcome)
        return outcome

    async def bounded(call: ToolCall) -> ToolOutcome:
        async with slots:
            return await one(call)

    outcomes: list[ToolOutcome] = []
    for group in _group_calls(runnable, registry):
        if len(group) == 1:
            outcomes.append(await one(group[0]))
        else:
            outcomes.extend(await _together([bounded(call) for call in group]))
    return [*outcomes, *skipped]


async def _together(calls: Sequence[Coroutine[object, object, ToolOutcome]]) -> list[ToolOutcome]:
    """Run ``calls`` at once and return their outcomes in their order. When one fails outside its tool (a stop of the
    run, an error no result tells) or the step is cancelled, the others are cancelled and awaited before the failure
    goes on, so no call of the group outlives it."""
    tasks: list[asyncio.Task[ToolOutcome]] = [asyncio.create_task(call) for call in calls]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
