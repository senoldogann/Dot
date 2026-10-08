"""Terminal presentation of runs: progress lines, streamed text and interactive approval prompts."""

import asyncio
import os
import select
import sys
import threading
from typing import Literal, TextIO

from pydantic import BaseModel, ConfigDict, JsonValue

from dotagent.agent.gate import YOLO_DECIDER
from dotagent.agent.plan import StepStatus
from dotagent.agent.progress import Rung
from dotagent.agent.resume import CallEnd, CutOff
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.text import truncate_tail
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.runtime import Runtime
from dotagent.store.models import EventRecord

_STDIN_POLL_S: float = 0.2
_ARG_PREVIEW_CHARS: int = 140
_RESULT_PREVIEW_CHARS: int = 170
_PREFERRED_ARGS: tuple[str, ...] = ("command", "url", "path", "query", "goal", "text", "title", "name")
_CHILD_INDENT: str = "    │ "


class Payload(BaseModel):
    """Event payloads as the terminal reads them; fields it does not render are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class RunStartedPayload(Payload):
    model: str
    parent_run_id: str | None = None


class AssistantPayload(Payload):
    step: int
    text: str
    tool_calls: list[str]
    input_tokens: int
    output_tokens: int


class DeltaPayload(Payload):
    kind: Literal["text", "thinking", "reset"]
    text: str
    step: int


class ToolCallPayload(Payload):
    call_id: str
    tool: str
    args: JsonObject


class ToolResultPayload(Payload):
    call_id: str
    tool: str
    ok: bool
    text: str
    images: list[str]
    seconds: float
    summary: str


class PolicyPayload(Payload):
    summary: str
    verdict: str
    reason: str


class PlanStepPayload(Payload):
    text: str
    status: StepStatus


class PlanPayload(Payload):
    steps: list[PlanStepPayload]


class ApprovalPayload(Payload):
    approval_id: str
    tool: str
    summary: str
    severity: str
    args: JsonObject


class AutoApprovalPayload(Payload):
    """An approval that nobody was asked about: waived in yolo mode, or answered by the review model."""

    tool: str
    summary: str
    status: Literal["approved", "denied"]
    decided_by: str
    note: str


class GoalCheckPayload(Payload):
    decision: Literal["achieved", "continue", "blocked", "error"]
    reason: str
    check: int
    patience: int
    final: bool


class AnswerPathsPayload(Payload):
    """The paths a final answer named that do not exist: the run was sent back once to correct them."""

    missing: list[str]


class StallPayload(Payload):
    """A progress alert: what the watchdog saw (``detail``) and the rung it answered with (``action``), with the advice
    or the error of the review model for the consult rung. Events of the older stall guard carry only ``warnings``."""

    warnings: int
    action: Rung | None = None
    detail: str | None = None
    diagnosis: str | None = None
    next_steps: list[str] | None = None
    error: str | None = None


class NotePayload(Payload):
    text: str


class ResumedCallPayload(Payload):
    tool: str
    end: CallEnd
    repeatable: bool


class ResumedPayload(Payload):
    """A run that goes on from where the daemon cut the run before it off: that run, how it was cut off, whether the
    daemon resumed it on its own, and how each call of the step it was in ended."""

    from_run_id: str
    cut_off: CutOff
    automatic: bool
    calls: list[ResumedCallPayload]


class MemoriesPayload(Payload):
    ids: list[str]


class UiOptions(FrozenModel):
    interactive: bool
    stream_text: bool
    show_thinking: bool
    verbose: bool


def _read_line_blocking(prompt: str, stop: threading.Event) -> str | None:
    """Read one line from the terminal without ever blocking past ``stop`` (polls the raw descriptor)."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    fd: int = sys.stdin.fileno()
    collected: bytearray = bytearray()
    while not stop.is_set():
        ready, _, _ = select.select([fd], [], [], _STDIN_POLL_S)
        if not ready:
            continue
        chunk: bytes = os.read(fd, 4096)
        if chunk == b"":
            return None
        newline: int = chunk.find(b"\n")
        if newline >= 0:
            collected.extend(chunk[:newline])
            return collected.decode("utf-8", errors="replace").rstrip("\r")
        collected.extend(chunk)
    return None


async def read_line(prompt: str) -> str | None:
    """Prompt on stderr and read a line from stdin. ``None`` on end of input or when the task is cancelled."""
    stop: threading.Event = threading.Event()
    try:
        return await asyncio.to_thread(_read_line_blocking, prompt, stop)
    finally:
        stop.set()


def preview_args(args: JsonObject) -> str:
    """One line describing a tool call: the most telling argument first, then the others."""
    ordered: list[str] = [name for name in _PREFERRED_ARGS if name in args]
    ordered.extend(name for name in args if name not in ordered)
    parts: list[str] = []
    for name in ordered:
        value: JsonValue = args[name]
        rendered: str = value if isinstance(value, str) else dumps_compact(value)
        flat: str = " ".join(rendered.split())
        parts.append(flat if name == ordered[0] else f"{name}={flat}")
    return truncate_tail(" ".join(parts), _ARG_PREVIEW_CHARS)


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return truncate_tail(line.strip(), _RESULT_PREVIEW_CHARS)
    return ""


class TerminalUi:
    """Writes run events to stderr and answers approval requests on the terminal."""

    def __init__(self, runtime: Runtime, options: UiOptions) -> None:
        self._runtime: Runtime = runtime
        self._options: UiOptions = options
        self._out: TextIO = sys.stderr
        self._color: bool = self._out.isatty()
        self._open_line: bool = False
        self._streamed_steps: set[int] = set()
        self._streamed_text: str = ""
        self._delegated: set[str] = set()
        self._indent: str = ""

    def _paint(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self._color else text

    def _line(self, text: str) -> None:
        if self._open_line:
            self._out.write("\n")
            self._open_line = False
        for part in text.split("\n"):
            self._out.write(self._indent + part + "\n")
        self._out.flush()

    def _inline(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()
        self._open_line = not text.endswith("\n")

    def streamed(self, text: str) -> bool:
        """True when ``text`` is what was just streamed to the terminal (so the caller need not print it again)."""
        return self._streamed_text.strip() == text.strip() and text.strip() != ""

    async def handle(self, event: EventRecord) -> None:
        data: JsonObject = event.data
        delegated: bool = event.run_id in self._delegated
        self._indent = _CHILD_INDENT if delegated else ""
        match event.kind:
            case "run_started":
                payload = RunStartedPayload.model_validate(data)
                if payload.parent_run_id is None:
                    self._line(self._paint(f"▶ {event.run_id} · {payload.model}", "36"))
                else:
                    self._delegated.add(event.run_id)
                    self._indent = _CHILD_INDENT
                    self._line(self._paint(f"↳ {event.run_id} · {payload.model}", "36"))
            case "delta":
                if not delegated:
                    self._on_delta(DeltaPayload.model_validate(data))
            case "assistant":
                self._on_assistant(AssistantPayload.model_validate(data), event.run_id)
            case "tool_call":
                call = ToolCallPayload.model_validate(data)
                self._line(f"  {self._paint('›', '36')} {self._paint(call.tool, '1')} {preview_args(call.args)}")
            case "tool_result":
                self._on_tool_result(ToolResultPayload.model_validate(data))
            case "policy":
                policy = PolicyPayload.model_validate(data)
                self._line(self._paint(f"  ⚠ {policy.verdict}: {policy.summary} ({policy.reason})", "33"))
            case "plan":
                self._on_plan(PlanPayload.model_validate(data))
            case "approval_requested":
                await self._on_approval(ApprovalPayload.model_validate(data))
            case "approval_auto":
                self._on_auto_approval(AutoApprovalPayload.model_validate(data))
            case "goal_check":
                self._on_goal_check(GoalCheckPayload.model_validate(data))
            case "answer_paths":
                missing: str = ", ".join(AnswerPathsPayload.model_validate(data).missing)
                self._line(self._paint(f"  ◎ the answer names paths that do not exist, back to work: {missing}", "33"))
            case "memories":
                recalled = MemoriesPayload.model_validate(data)
                noun: str = "note" if len(recalled.ids) == 1 else "notes"
                self._line(self._paint(f"  ◆ {len(recalled.ids)} saved {noun} in context", "2"))
            case "stall":
                self._on_stall(StallPayload.model_validate(data))
            case "compacted":
                self._line(self._paint("  ⇲ older context summarised", "2"))
            case "steer":
                self._line(self._paint(f"  ✎ {NotePayload.model_validate(data).text}", "35"))
            case "resumed":
                self._on_resumed(ResumedPayload.model_validate(data))
            case _:
                return

    def _on_delta(self, delta: DeltaPayload) -> None:
        if delta.kind == "reset":
            if self._options.stream_text:
                self._line(self._paint("  … retrying the model call", "2"))
            self._streamed_text = ""
            return
        if delta.kind == "thinking":
            if self._options.show_thinking:
                self._inline(self._paint(delta.text, "2"))
            return
        if self._options.stream_text:
            if delta.step not in self._streamed_steps:
                self._streamed_steps.add(delta.step)
                self._streamed_text = ""
            self._streamed_text += delta.text
            self._inline(delta.text)

    def _on_assistant(self, assistant: AssistantPayload, run_id: str) -> None:
        if self._open_line:
            self._out.write("\n")
            self._open_line = False
        streamed: bool = run_id not in self._delegated and assistant.step in self._streamed_steps
        if assistant.text.strip() and not streamed:
            self._line(assistant.text.strip())
        if self._options.verbose:
            self._line(
                self._paint(
                    f"  · step {assistant.step}: {assistant.input_tokens} in / {assistant.output_tokens} out tokens",
                    "2",
                )
            )

    def _on_tool_result(self, result: ToolResultPayload) -> None:
        mark: str = self._paint("✓", "32") if result.ok else self._paint("✗", "31")
        shown: str = first_line(result.text)
        images: str = f" [{len(result.images)} image]" if result.images else ""
        detail: str = f"  {shown}" if shown else ""
        self._line(f"  {mark} {self._paint(f'{result.seconds:.1f}s', '2')}{images}{detail}")
        if self._options.verbose:
            for extra in result.text.splitlines()[1:12]:
                self._line(self._paint(f"      {truncate_tail(extra, _RESULT_PREVIEW_CHARS)}", "2"))

    def _on_plan(self, plan: PlanPayload) -> None:
        marks: dict[StepStatus, str] = {"pending": "○", "in_progress": "◐", "done": "●", "failed": "✕", "skipped": "–"}
        self._line(self._paint("  plan", "1"))
        for step in plan.steps:
            self._line(f"    {marks[step.status]} {step.text}")

    def _on_resumed(self, resumed: ResumedPayload) -> None:
        who: str = "on its own when the daemon started" if resumed.automatic else "by hand"
        how: str = "was shut down" if resumed.cut_off == "daemon_shutdown" else "stopped"
        self._line(self._paint(f"  ↺ resumed {who}: the daemon {how} while run {resumed.from_run_id} worked", "35"))
        ends: dict[CallEnd, str] = {
            "succeeded": "finished",
            "failed": "failed",
            "cut_off": "cut off",
            "not_started": "not started",
        }
        for call in resumed.calls:
            risk: str = " (it may have changed something)" if call.end == "cut_off" and not call.repeatable else ""
            self._line(self._paint(f"    {call.tool}: {ends[call.end]}{risk}", "2"))

    def _on_auto_approval(self, decided: AutoApprovalPayload) -> None:
        if decided.decided_by == YOLO_DECIDER:
            self._line(self._paint(f"  ⚡ yolo: {decided.summary}", "33"))
            return
        verdict: str = "approved" if decided.status == "approved" else "denied"
        code: str = "32" if decided.status == "approved" else "31"
        reason: str = f" — {decided.note}" if decided.note else ""
        self._line(self._paint(f"  ⚖ {decided.decided_by} {verdict}: {decided.summary}{reason}", code))

    def _on_stall(self, stall: StallPayload) -> None:
        if stall.action is None or stall.detail is None:
            self._line(self._paint("  ↻ repeating itself; nudged to change approach", "33"))
            return
        answers: dict[Rung, str] = {
            "nudge": "nudged to change approach",
            "replan": "next step may only plan or finish",
            "consult": "second opinion from the review model",
            "ask": "next step may only ask you or finish",
            "stop": "stopped",
        }
        ends: bool = stall.action == "stop" or stall.error is not None
        self._line(
            self._paint(f"  ↻ not getting anywhere: {stall.detail} → {answers[stall.action]}", "31" if ends else "33")
        )
        if stall.error is not None:
            self._line(self._paint(f"    the review model could not answer, so the run stops: {stall.error}", "31"))
        if stall.diagnosis is not None:
            self._line(self._paint(f"    {stall.diagnosis}", "2"))
        for step in stall.next_steps or []:
            self._line(self._paint(f"    → {step}", "2"))

    def _on_goal_check(self, check: GoalCheckPayload) -> None:
        reason: str = f" — {check.reason}" if check.reason else ""
        match check.decision:
            case "achieved":
                self._line(self._paint(f"  ◎ goal check: reached{reason}", "32"))
            case "continue":
                note: str = "back to work" if not check.final else "gave up"
                self._line(self._paint(f"  ◎ goal check {check.check}/{check.patience}: not yet, {note}{reason}", "33"))
            case "blocked":
                self._line(self._paint(f"  ◎ goal check: blocked{reason}", "31"))
            case "error":
                self._line(self._paint(f"  ◎ goal check could not run{reason}", "31"))

    async def _on_approval(self, request: ApprovalPayload) -> None:
        if request.severity == "question":
            await self._ask_question(request)
            return
        await self._ask_approval(request)

    async def _ask_question(self, request: ApprovalPayload) -> None:
        options: JsonValue = request.args.get("options")
        self._line(self._paint(f"  ? {request.summary}", "1;35"))
        if isinstance(options, list) and options:
            self._line(self._paint("    options: " + " | ".join(str(option) for option in options), "2"))
        answer: str | None = await read_line("  answer> ") if self._options.interactive else None
        if answer is None or not answer.strip():
            await self._resolve(request, approve=False, note="no answer was given")
            return
        await self._resolve(request, approve=True, note=answer.strip())

    async def _ask_approval(self, request: ApprovalPayload) -> None:
        handoff: bool = request.severity == "handoff"
        label: str = "hand-off" if handoff else f"approval ({request.severity})"
        self._line(self._paint(f"  ⚠ {label}: {request.summary}", "1;33"))
        if not self._options.interactive:
            self._line(self._paint("    no interactive terminal: denied", "2"))
            await self._resolve(request, approve=False, note="no interactive terminal was available")
            return
        if handoff:
            reply: str | None = await read_line("    do this step yourself, then press Enter (or type 'skip')> ")
            skipped: bool = reply is None or reply.strip().lower() == "skip"
            await self._resolve(request, approve=not skipped, note="the user completed the step themselves")
            return
        reply = await read_line("    allow? [y/N]> ")
        approved: bool = reply is not None and reply.strip().lower() in ("y", "yes", "e", "evet")
        await self._resolve(request, approve=approved, note="" if approved else "denied in the terminal")

    async def _resolve(self, request: ApprovalPayload, approve: bool, note: str) -> None:
        await self._runtime.approvals.resolve(request.approval_id, approve, "cli", note)
