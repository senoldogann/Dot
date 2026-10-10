"""Tool execution: validate, authorise, run under a timeout, and turn every failure into a readable result."""

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from dotagent.agent.gate import RunGate
from dotagent.core.errors import (
    ApprovalRejected,
    DotError,
    FailSafeTriggered,
    HandedOff,
    PolicyDenied,
    RunCancelled,
    ToolInputError,
)
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_json, strip_blobs, truncate_middle
from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolCall, invalid_arguments_text
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import PreparedCall, RegisteredTool, ToolResult, fail, ok
from dotagent.tools.channels import channel_of
from dotagent.tools.context import Gate, ToolContext
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.registry import ToolRegistry

log = get_logger(__name__)

_EVENT_TEXT_CHARS: int = 4000
_BROKEN_ARGUMENTS_CHARS: int = 600


@dataclass(frozen=True)
class ToolOutcome:
    call: ToolCall
    result: ToolResult
    seconds: float
    action: ActionInfo | None


def _safe_json(value: JsonObject) -> JsonObject:
    sanitized = redact_json(strip_blobs(value))
    return sanitized if isinstance(sanitized, dict) else {}


def _site(call: ToolCall, action: ActionInfo | None) -> str:
    """Where a call acted, for the statistics: the host of the page for a browser action, the application for a
    desktop one, and nothing for every other tool (a file path or a command is not a site)."""
    if action is None:
        return ""
    if action.kind.startswith("browser_"):
        return urlsplit(action.target).hostname or ""
    return action.target if call.name.startswith("desktop_") else ""


def batch_steps(arguments: JsonObject) -> int:
    """How many steps a batched call (``browser_act``, ``desktop_act``) carried; 0 for a call without a step list."""
    steps: object = arguments.get("steps")
    return len(steps) if isinstance(steps, list) else 0


def _describe_failure(exc: Exception, tool: RegisteredTool) -> str | None:
    """The text the model sees for an expected failure, or ``None`` when the exception is not an expected one."""
    if isinstance(exc, ToolInputError):
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
    return None


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
    which its seconds include."""

    result: ToolResult
    action: ActionInfo | None
    waited_s: float


async def _run_checked(call: ToolCall, tool: RegisteredTool, ctx: ToolContext, gate: RunGate) -> _Checked:
    action: ActionInfo | None = None
    at_gate: _GateTime = _GateTime()
    try:
        prepared: PreparedCall = await tool.prepare(call.arguments, ctx)
        action = prepared.action
        asked_at: float = time.monotonic()
        try:
            note: str = await gate.authorize(action)
        finally:
            at_gate.seconds += time.monotonic() - asked_at
        async with asyncio.timeout(tool.timeout_s) as scope:
            result: ToolResult = await prepared.run(replace(ctx, gate=_PausedClock(ctx.gate, scope, at_gate)))
    except (RunCancelled, FailSafeTriggered):
        raise
    except (DotError, TimeoutError, OSError) as exc:
        text: str | None = _describe_failure(exc, tool)
        if text is None:
            raise
        handed_off: bool = isinstance(exc, HandedOff)
        return _Checked(result=ok(text) if handed_off else fail(text), action=action, waited_s=at_gate.seconds)
    if action is not None and not result.is_error:
        gate.observe(action)
    if result.untrusted:
        gate.taint()
    return _Checked(result=_with_approval_note(result, note), action=action, waited_s=at_gate.seconds)


def _turn_on_action(tool: str, group: str) -> ActionInfo:
    return ActionInfo(
        tool=tool, kind="meta", risk="safe", summary=f"Turn on the tool group {group}", target="", flags=()
    )


async def _execute_one(
    call: ToolCall, registry: ToolRegistry, hidden: Mapping[str, str], ctx: ToolContext, gate: RunGate
) -> ToolOutcome:
    started: float = time.monotonic()
    await ctx.runtime.bus.emit(
        ctx.run_id, "tool_call", {"call_id": call.id, "tool": call.name, "args": _safe_json(call.arguments)}, True
    )
    tool: RegisteredTool | None = registry.tools.get(call.name)
    action: ActionInfo | None = None
    waited_s: float = 0.0
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
        result, action, waited_s = checked.result, checked.action, checked.waited_s
    clipped: ToolResult = result.model_copy(update={"text": truncate_middle(result.text, ctx.output_chars)})
    seconds: float = time.monotonic() - started
    files: list[str] = await ctx.runtime.store.save_images(clipped.images) if clipped.images else []
    await ctx.runtime.bus.emit(
        ctx.run_id,
        "tool_result",
        {
            "call_id": call.id,
            "tool": call.name,
            "ok": not clipped.is_error,
            "text": redact_json(truncate_middle(clipped.text, _EVENT_TEXT_CHARS)),
            "images": list(files),
            "seconds": round(seconds, 2),
            "waited_s": round(waited_s, 2),
            "summary": action.summary if action is not None else "",
            "risk": action.risk if action is not None else "",
            "site": _site(call, action),
            "steps": batch_steps(call.arguments),
            "channel": channel_of(call.name),
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
) -> list[ToolOutcome]:
    """Run the calls of one model turn in order, overlapping read-only ones. Results keep the call order; the text of
    each is clipped to the run's ``ctx.output_chars``. ``hidden`` maps the tools of the tool groups that are off to
    their group: a call of one of them does not run, and its result says to call it again now that its group is on."""
    finish_index: int | None = next((index for index, call in enumerate(calls) if call.name == FINISH_TOOL), None)
    runnable: Sequence[ToolCall] = calls if finish_index is None else calls[: finish_index + 1]
    skipped: list[ToolOutcome] = [] if finish_index is None else [_skipped(call) for call in calls[finish_index + 1 :]]
    slots: asyncio.Semaphore = asyncio.Semaphore(parallelism)

    async def bounded(call: ToolCall) -> ToolOutcome:
        async with slots:
            return await _execute_one(call, registry, hidden, ctx, gate)

    outcomes: list[ToolOutcome] = []
    for group in _group_calls(runnable, registry):
        if len(group) == 1:
            outcomes.append(await _execute_one(group[0], registry, hidden, ctx, gate))
        else:
            outcomes.extend(await asyncio.gather(*(bounded(call) for call in group)))
    return [*outcomes, *skipped]
