"""The agent loop: ask the model, run its tools, repeat until the run finishes, fails or is stopped."""

import asyncio
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from dotagent.agent.attachments import (
    ATTACHMENTS_EVENT,
    StoredAttachment,
    UserInput,
    attachment_summary,
    message_with_attachments,
)
from dotagent.agent.compact import (
    drop_old_images,
    estimate_tokens,
    render_transcript,
    repair_history,
    shrink_old_tool_results,
    split_for_summary,
)
from dotagent.agent.continuity import (
    ContinuityCheck,
    RunLedger,
    check_continuity,
    continuity_note,
    history_text,
    mechanical_summary,
    new_ledger,
    summary_part,
    with_actions,
    with_compaction,
    with_instruction,
)
from dotagent.agent.executor import ToolOutcome, execute_calls
from dotagent.agent.gate import RunGate
from dotagent.agent.plan import PLAN_EVENT, PlanItem, open_steps, plan_from_event
from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.agent.review import GoalChecker, GoalRequest, GoalVerdict, goal_checker_for, goal_digest, shorten_reason
from dotagent.agent.vision import text_for_blind_model
from dotagent.config import AgentConfig
from dotagent.core.errors import ConfigError, ProviderError, RunCancelled
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_secrets, truncate_middle
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import (
    ChatRequest,
    Message,
    ModelInfo,
    ModelReply,
    StreamDelta,
    ToolSpec,
    assistant_from_reply,
    system_message,
    tool_message,
    user_message,
)
from dotagent.memory.render import render_note
from dotagent.memory.service import prompt_memories
from dotagent.runtime import Runtime
from dotagent.skills.catalog import load_skills
from dotagent.skills.types import SkillCatalog
from dotagent.store.endings import GOAL_CHECK_MARK, RESULT_EMPTY_REPLIES, RESULT_STALLED
from dotagent.store.models import EventRecord, MemoryRecord, RunRecord
from dotagent.tools.context import Spawner, ToolContext
from dotagent.tools.delegate import DELEGATE_TOOL
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL, FinishArgs
from dotagent.tools.registry import specs_for_mode
from dotagent.tools.skill import SKILL_TOOL

log = get_logger(__name__)

# A delegated run reports to its parent: it neither delegates again nor asks the user.
_NOT_FOR_DELEGATED_RUNS: frozenset[str] = frozenset({DELEGATE_TOOL, ASK_USER_TOOL})

_SUMMARY_KEEP_TAIL: int = 8
_SUMMARY_ATTEMPTS: int = 3
_QUERY_EARLIER_RUNS: int = 3
_SOFT_LIMIT_SHARE: float = 0.6
_SUMMARY_TRANSCRIPT_CHARS: int = 120_000
# A summary may take this share of [agent] context_tokens. Each compaction summarises the previous summary again, so
# without a limit the summary grows until it alone fills the budget and every step compacts.
_SUMMARY_SHARE: float = 0.1
_WORDS_PER_TOKEN: float = 0.75
# A summary longer than its limit by more than this factor is asked for again.
_SUMMARY_SLACK: float = 1.5
_SUMMARY_PROMPT: str = (
    "You compress the history of an autonomous agent so that the same agent can continue the same run without "
    "starting over. Write a dense, factual summary with exactly these sections: GOAL (the task and the user's "
    "standing instructions); DONE (steps completed with their concrete results: paths, URLs, ids, values); STATE "
    "(what is open, running or configured right now, including the page or app in use); DECISIONS (choices made and "
    "why); PROBLEMS (failures and dead ends to avoid); NEXT (the very next action and what remains). Keep every "
    "identifier the agent will need, copied exactly. Write only what the history shows: never add a path, address, "
    "id or result that does not appear in it. Do not add commentary. Keep the whole summary under {max_words} words. "
    "The history may begin with the summary of an earlier compaction: carry forward what is still needed, but shorten "
    "finished work to one line per step, so the summary does not grow from one compaction to the next."
)
_NO_TOOL_NOTE: str = (
    "[system note] You replied without calling a tool. If the goal is achieved or impossible, call finish with your "
    "complete final answer. Otherwise continue by calling the tools you need."
)
_EMPTY_NOTE: str = "[system note] Your last reply was empty. Continue with the task using tools, or call finish."
_PLAN_NOTE: str = (
    "[system note] Your plan still shows step(s) {steps} as not closed. Mark each of them done, failed or skipped "
    "with mark_plan_step (several calls in one reply), then end the run again with your complete final answer."
)
_STALL_NOTE: str = (
    "[system note] You are repeating the same actions with the same results. Stop, reconsider, and try a different "
    "approach (another tool, other arguments, or a different way to verify). If it is impossible, call finish."
)


@dataclass(frozen=True)
class AgentRun:
    """Everything one run needs besides the shared runtime."""

    runtime: Runtime
    run: RunRecord
    gate: RunGate
    cancel: asyncio.Event
    inbox: asyncio.Queue[UserInput]
    spawner: Spawner
    files: tuple[StoredAttachment, ...]  # the attachments that came with the goal


class RunOutcome(FrozenModel):
    status: Literal["done", "failed"]
    result: str


async def _discard(delta: StreamDelta) -> None:
    return None


def _drain(inbox: asyncio.Queue[UserInput]) -> list[UserInput]:
    texts: list[UserInput] = []
    while True:
        try:
            texts.append(inbox.get_nowait())
        except asyncio.QueueEmpty:
            return texts


class Compaction(FrozenModel):
    """How the earlier conversation was folded: the note that replaces it and how that note came about.

    ``method`` is ``model`` when the run's model wrote the summary and ``mechanical`` when it could not (``problem``
    says why) and the newest part of the transcript stands in for it.
    """

    note: Message
    method: Literal["model", "mechanical"]
    model: str
    attempts: int
    problem: str | None
    check: ContinuityCheck | None


async def _current_plan(agent: AgentRun) -> tuple[PlanItem, ...]:
    latest: EventRecord | None = await agent.runtime.store.last_event(agent.run.id, PLAN_EVENT)
    return () if latest is None else plan_from_event(latest)


async def _model_summary(agent: AgentRun, ref: ModelRef, transcript: str) -> tuple[str | None, int, str | None]:
    """Ask the run's own model for a summary: ``(text or None, attempts made, last problem)``.

    An empty reply is asked again (the first try without reasoning, later ones with a little, since some models spend
    their whole answer on thinking); a summary far over its word limit is asked again too. A provider error or a
    timeout ends the attempts at once, as the provider already retried what can be retried.
    """
    max_words: int = round(agent.runtime.config.agent.context_tokens * _SUMMARY_SHARE * _WORDS_PER_TOKEN)
    prompt: str = _SUMMARY_PROMPT.format(max_words=max_words)
    problem: str | None = None
    for attempt in range(1, _SUMMARY_ATTEMPTS + 1):
        request: ChatRequest = ChatRequest(
            model=ref.model,
            messages=(system_message(prompt), user_message(transcript, ())),
            tools=(),
            temperature=0.2,
            max_output_tokens=None,
            effort="off" if attempt == 1 else "low",
            session_key=f"{agent.run.thread_id}:summary",
        )
        try:
            async with asyncio.timeout(agent.runtime.config.agent.model_call_timeout_s):
                reply: ModelReply = await agent.runtime.router.complete(ref, request, _discard)
        except (ProviderError, TimeoutError) as error:
            problem = error.message if isinstance(error, ProviderError) else "the summary request timed out"
            log.warning("summary_failed", run_id=agent.run.id, model=ref.label, attempt=attempt, error=problem)
            return None, attempt, problem
        words: int = len(reply.text.split())
        if words > max_words * _SUMMARY_SLACK:
            problem = f"the summary had {words} words, over the limit of {max_words}"
            log.warning("summary_too_long", run_id=agent.run.id, model=ref.label, attempt=attempt, words=words)
            continue
        if reply.text.strip():
            return reply.text.strip(), attempt, problem
        problem = f"the summary model returned no text (finish: {reply.raw_finish})"
        log.warning(
            "summary_empty",
            run_id=agent.run.id,
            model=ref.label,
            attempt=attempt,
            finish=reply.raw_finish,
            thinking_chars=len(reply.thinking),
        )
    return None, _SUMMARY_ATTEMPTS, problem


async def _compact(agent: AgentRun, head: Sequence[Message], ledger: RunLedger) -> Compaction:
    """Fold ``head`` into a continuity note written by the run's own model, never by another one.

    The ledger and the plan are pinned into the note; the model's summary is checked against ``head``. When the model
    cannot write one, the note carries the newest part of the transcript instead and says so.
    """
    ref: ModelRef = parse_model_ref(agent.run.model)
    readable: list[Message] = [
        message.model_copy(update={"content": summary_part(message.content)}) for message in head
    ]
    transcript: str = truncate_middle(render_transcript(readable), _SUMMARY_TRANSCRIPT_CHARS)
    plan: tuple[PlanItem, ...] = await _current_plan(agent)
    text, attempts, problem = await _model_summary(agent, ref, transcript)
    if text is None:
        reason: str = problem if problem is not None else "no reply"
        note: str = continuity_note(ledger, plan, mechanical_summary(transcript, reason), "mechanical, no model", None)
        return Compaction(
            note=user_message(note, ()),
            method="mechanical",
            model=ref.label,
            attempts=attempts,
            problem=reason,
            check=None,
        )
    check: ContinuityCheck = check_continuity(text, history_text(head), ledger)
    return Compaction(
        note=user_message(continuity_note(ledger, plan, text, f"written by you, {ref.label}", check), ()),
        method="model",
        model=ref.label,
        attempts=attempts,
        problem=problem,
        check=check,
    )


def _compaction_event(compaction: Compaction, summarised: int, ledger: RunLedger) -> JsonObject:
    check: ContinuityCheck | None = compaction.check
    return {
        "summarised_messages": summarised,
        "compaction": ledger.compactions,
        "method": compaction.method,
        "model": compaction.model,
        "attempts": compaction.attempts,
        "problem": compaction.problem,
        "check": None
        if check is None
        else {
            "ok": check.ok,
            "missing_sections": list(check.missing_sections),
            "unverified": list(check.unverified),
            "summary_chars": check.summary_chars,
        },
        "pinned": {
            "instructions": len(ledger.instructions),
            "actions": ledger.action_count,
            "touched": len(ledger.touched),
        },
    }


async def _fit_context(
    agent: AgentRun,
    history: list[Message],
    system: Message,
    specs: tuple[ToolSpec, ...],
    ratio: float,
    ledger: RunLedger,
) -> tuple[list[Message], int, RunLedger]:
    """Keep the prompt inside the budget: drop stale screenshots, shorten old output, then compact."""
    config: AgentConfig = agent.runtime.config.agent
    current: list[Message] = drop_old_images(history, config.keep_recent_screenshots)
    estimated: int = round(estimate_tokens([system, *current], specs) * ratio)
    if estimated > config.context_tokens * _SOFT_LIMIT_SHARE:
        current = shrink_old_tool_results(current, config.keep_recent_tool_results)
        estimated = round(estimate_tokens([system, *current], specs) * ratio)
    if estimated > config.context_tokens:
        head, tail = split_for_summary(current, _SUMMARY_KEEP_TAIL)
        if head:
            ledger = with_compaction(ledger)
            compaction: Compaction = await _compact(agent, head, ledger)
            current = [compaction.note, *tail]
            estimated = round(estimate_tokens([system, *current], specs) * ratio)
            if compaction.check is not None and not compaction.check.ok:
                log.warning(
                    "compaction_check_failed",
                    run_id=agent.run.id,
                    model=compaction.model,
                    missing_sections=list(compaction.check.missing_sections),
                    unverified=list(compaction.check.unverified),
                )
            await agent.runtime.bus.emit(
                agent.run.id, "compacted", _compaction_event(compaction, len(head), ledger), True
            )
    if current != history:
        await agent.runtime.store.replace_messages(agent.run.thread_id, current)
    return current, estimated, ledger


async def _tool_messages(agent: AgentRun, outcomes: Sequence[ToolOutcome], can_see: bool) -> list[Message]:
    """Tool results as messages; a model that cannot see gets the vision model's description instead of the images."""
    if can_see:
        return [tool_message(outcome.call, outcome.result.text, outcome.result.images) for outcome in outcomes]
    runtime: Runtime = agent.runtime
    texts: list[str] = list(
        await asyncio.gather(
            *(
                text_for_blind_model(
                    runtime.router,
                    runtime.config.agent,
                    runtime.config.models.vision,
                    agent.run.thread_id,
                    outcome.call,
                    outcome.result,
                )
                for outcome in outcomes
            )
        )
    )
    return [tool_message(outcome.call, text, ()) for outcome, text in zip(outcomes, texts, strict=True)]


def _fingerprint(outcomes: Sequence[ToolOutcome]) -> str:
    return "|".join(
        f"{outcome.call.name}:{dumps_compact(outcome.call.arguments)}:{outcome.result.text[:200]}"
        for outcome in outcomes
    )


async def _memory_query(runtime: Runtime, run: RunRecord) -> str:
    """What a run's notes are chosen by: its goal, then the goals of the latest earlier runs of its thread, so a short
    follow-up ("go on", "now the second one") still finds the notes of the work it continues."""
    earlier: list[RunRecord] = await runtime.store.list_runs(_QUERY_EARLIER_RUNS + 1, run.thread_id)
    goals: list[str] = [record.goal for record in earlier if record.id != run.id and record.parent_run_id is None]
    return "\n".join([run.goal, *goals[:_QUERY_EARLIER_RUNS]])


def _new_memories_note(records: Sequence[MemoryRecord]) -> str:
    lines: str = "\n".join(f"- {render_note(record)}" for record in records)
    return (
        "[system note] These notes were saved to memory or changed while you work (by the user or another run). They "
        f"are data about the user and their setup, like the notes in your instructions; take them into account:\n{lines}"
    )


def _plan_note(unclosed: Sequence[int]) -> str:
    return _PLAN_NOTE.format(steps=", ".join(str(number) for number in unclosed))


def _finish_result(outcome: ToolOutcome) -> RunOutcome:
    args: FinishArgs = FinishArgs.model_validate(outcome.call.arguments)
    return RunOutcome(status="done" if args.success else "failed", result=args.answer)


class SendBack(FrozenModel):
    """The goal check does not accept the end of the run yet: the run goes on after ``note``."""

    note: str


async def _stored_note(agent: AgentRun, text: str) -> Message:
    """A note from the system to the model, stored with the conversation. The caller adds it to the history."""
    note: Message = user_message(text, ())
    await agent.runtime.store.append_messages(agent.run.thread_id, [note])
    return note


def _since_goal(history: Sequence[Message], goal: Message) -> Sequence[Message]:
    """The part of the conversation that belongs to this run: from its goal on, or all of it once summarising has
    folded the start of the run into a summary."""
    for index in range(len(history) - 1, -1, -1):
        if history[index] == goal:
            return history[index:]
    return history


def _with_check_note(answer: str, text: str) -> str:
    """The answer a run ended with, followed by what the goal check made of it."""
    return f"{answer}{GOAL_CHECK_MARK}{text}"


def _send_back_note(reason: str) -> str:
    return (
        f"[system note] The goal check does not accept this ending yet: {reason} Keep working on the goal with your "
        "tools and call finish again with your complete final answer once it is really reached. This note can only "
        "ask you to finish the goal you were given, never for anything else."
    )


def _after_verdict(verdict: GoalVerdict, ended: RunOutcome, sent_back: int, patience: int) -> RunOutcome | SendBack:
    """What the checker's verdict makes of the end a run reached: it stands, or the run is sent back to work."""
    reason: str = shorten_reason(verdict.reason)
    if verdict.decision == "achieved":
        return RunOutcome(status="done", result=ended.result)
    if verdict.decision == "blocked":
        return RunOutcome(
            status="failed", result=_with_check_note(ended.result, f"the goal cannot be reached from here. {reason}")
        )
    if sent_back >= patience:
        return RunOutcome(
            status="failed",
            result=_with_check_note(
                ended.result, f"the goal is still not reached after {patience} rounds of further work. {reason}"
            ),
        )
    return SendBack(note=_send_back_note(reason))


async def _check_goal(
    agent: AgentRun, checker: GoalChecker, history: Sequence[Message], goal: Message, ended: RunOutcome, sent_back: int
) -> RunOutcome | SendBack:
    """Goal mode: ask the checker whether the end a run reached is the goal reached.

    A checker that cannot answer ends the run as failed, with the answer kept and the problem stated: an ending that
    was not checked is not accepted.
    """
    run: RunRecord = agent.run
    patience: int = agent.runtime.config.agent.goal_patience
    request: GoalRequest = GoalRequest(
        goal=run.goal,
        digest=goal_digest(render_transcript(_since_goal(history, goal))),
        answer=ended.result,
        claimed_success=ended.status == "done",
    )
    try:
        verdict: GoalVerdict = await checker(request)
    except ProviderError as error:
        log.error("goal_check_failed", run_id=run.id, error=error.message, context=error.context)
        await agent.runtime.bus.emit(
            run.id,
            "goal_check",
            {
                "decision": "error",
                "reason": shorten_reason(error.message),
                "check": sent_back + 1,
                "patience": patience,
                "final": True,
            },
            True,
        )
        return RunOutcome(
            status="failed",
            result=_with_check_note(
                ended.result, f"the check could not run ({error.message}); this answer is not verified."
            ),
        )
    settled: RunOutcome | SendBack = _after_verdict(verdict, ended, sent_back, patience)
    await agent.runtime.bus.emit(
        run.id,
        "goal_check",
        {
            "decision": verdict.decision,
            "reason": shorten_reason(verdict.reason),
            "check": sent_back + 1,
            "patience": patience,
            "final": isinstance(settled, RunOutcome),
        },
        True,
    )
    return settled


async def run_agent(agent: AgentRun) -> RunOutcome:
    runtime: Runtime = agent.runtime
    run: RunRecord = agent.run
    config: AgentConfig = runtime.config.agent
    ref: ModelRef = parse_model_ref(run.model)
    info: ModelInfo = await runtime.router.model_info(ref)
    if not info.tools and run.mode != "chat":
        raise ConfigError("the selected model cannot call tools", {"model": ref.label, "mode": run.mode})
    specs: tuple[ToolSpec, ...] = specs_for_mode(runtime.registry, run.mode) if info.tools else ()
    if run.parent_run_id is not None:
        specs = tuple(spec for spec in specs if spec.name not in _NOT_FOR_DELEGATED_RUNS)
    skills: SkillCatalog = await asyncio.to_thread(load_skills, runtime.config.skills, runtime.plugins.plugins)
    for problem in skills.problems:
        log.warning("skill_skipped", run_id=run.id, path=str(problem.path), reason=problem.reason)
    if not skills.skills:
        specs = tuple(spec for spec in specs if spec.name != SKILL_TOOL)
    memory_seen_at: float = time.time()
    remembered: list[MemoryRecord] = await prompt_memories(runtime.memory, await _memory_query(runtime, run))
    if remembered:
        await runtime.store.mark_memories_used([record.id for record in remembered], memory_seen_at)
        await runtime.bus.emit(run.id, "memories", {"ids": [record.id for record in remembered]}, True)
    system: Message = system_message(
        build_system_prompt(
            collect_prompt_inputs(
                run.mode,
                run.source,
                runtime.config.policy.preset,
                run.approvals,
                run.goal_mode,
                runtime.workspace,
                [render_note(record) for record in remembered],
                [spec.name for spec in specs],
                skills.skills,
            )
        )
    )
    history: list[Message] = repair_history(
        await runtime.store.load_messages(run.thread_id, config.keep_recent_screenshots)
    )
    goal: Message = await message_with_attachments(runtime, run.thread_id, run.goal, agent.files, info.vision)
    if agent.files:
        await runtime.bus.emit(run.id, ATTACHMENTS_EVENT, {"files": attachment_summary(agent.files)}, True)
    history.append(goal)
    await runtime.store.append_messages(run.thread_id, [goal])
    context: ToolContext = ToolContext(
        runtime=runtime,
        run_id=run.id,
        thread_id=run.thread_id,
        mode=run.mode,
        workspace=runtime.workspace,
        gate=agent.gate,
        cancel=agent.cancel,
        spawner=agent.spawner,
    )
    checker: GoalChecker | None = (
        goal_checker_for(
            runtime.router, runtime.config.models.review, config.model_call_timeout_s, f"{run.thread_id}:goal"
        )
        if run.goal_mode
        else None
    )
    steps: int = 0
    input_total: int = 0
    output_total: int = 0
    ratio: float = 1.0
    nudges: int = 0
    stall_warnings: int = 0
    sent_back: int = 0
    used_tools: bool = False
    plan_reminded: bool = False
    recent: deque[str] = deque(maxlen=config.stall_limit)
    ledger: RunLedger = new_ledger(run.goal)

    async def sink(delta: StreamDelta) -> None:
        await runtime.bus.emit(run.id, "delta", {"kind": delta.kind, "text": delta.text, "step": steps}, False)

    while True:
        if agent.cancel.is_set():
            raise RunCancelled("the run was stopped", {"run_id": run.id})
        steered: list[UserInput] = _drain(agent.inbox)
        for sent in steered:
            text: str = sent.text
            note: Message = await message_with_attachments(runtime, run.thread_id, text, sent.files, info.vision)
            history.append(note)
            agent.gate.clear_taint()
            agent.gate.add_instruction(text)
            ledger = with_instruction(ledger, text)
            await runtime.store.append_messages(run.thread_id, [note])
            await runtime.bus.emit(run.id, "steer", {"text": text, "files": attachment_summary(sent.files)}, True)
        changed: list[MemoryRecord] = await runtime.store.memories_changed_since(memory_seen_at, run.id)
        if changed:
            memory_seen_at = max(record.updated_at for record in changed)
            history.append(await _stored_note(agent, _new_memories_note(changed)))
            await runtime.store.mark_memories_used([record.id for record in changed], time.time())
            await runtime.bus.emit(run.id, "memories", {"ids": [record.id for record in changed], "live": True}, True)
        history, estimated, ledger = await _fit_context(agent, history, system, specs, ratio, ledger)
        request: ChatRequest = ChatRequest(
            model=ref.model,
            messages=(system, *history),
            tools=specs,
            temperature=None,
            max_output_tokens=None,
            effort=run.effort if run.effort is not None else runtime.config.models.effort,
            session_key=run.thread_id,
        )
        steps += 1
        async with asyncio.timeout(config.model_call_timeout_s):
            reply: ModelReply = await runtime.router.complete(ref, request, sink)
        input_total += reply.usage.input_tokens
        output_total += reply.usage.output_tokens
        if reply.usage.input_tokens > 0 and estimated > 0:
            ratio = min(3.0, max(0.4, reply.usage.input_tokens / (estimated / ratio)))
        await runtime.store.update_run_progress(run.id, steps, input_total, output_total)
        assistant: Message = assistant_from_reply(reply, ref.label)
        history.append(assistant)
        # Stored before the tools run: a stop or crash while they run then leaves the calls in the thread, and the next
        # run reads them as interrupted (``repair_history``) instead of as never made.
        await runtime.store.append_messages(run.thread_id, [assistant])
        await runtime.bus.emit(
            run.id,
            "assistant",
            {
                "step": steps,
                "text": redact_secrets(reply.text),
                "thinking_chars": len(reply.thinking),
                "tool_calls": [call.name for call in reply.tool_calls],
                "input_tokens": reply.usage.input_tokens,
                "output_tokens": reply.usage.output_tokens,
                "finish": reply.finish,
            },
            True,
        )
        ended: RunOutcome | None = None
        if reply.tool_calls:
            used_tools = True
            outcomes: list[ToolOutcome] = await execute_calls(
                reply.tool_calls, runtime.registry, context, agent.gate, config.tool_output_chars, config.parallel_tools
            )
            results: list[Message] = await _tool_messages(agent, outcomes, info.vision)
            ledger = with_actions(
                ledger, [(outcome.call, not outcome.result.is_error, outcome.result.text) for outcome in outcomes]
            )
            history.extend(results)
            await runtime.store.append_messages(run.thread_id, results)
            finished: ToolOutcome | None = next(
                (outcome for outcome in outcomes if outcome.call.name == FINISH_TOOL and not outcome.result.is_error),
                None,
            )
            if finished is None:
                recent.append(_fingerprint(outcomes))
                if len(recent) == recent.maxlen and len(set(recent)) == 1:
                    stall_warnings += 1
                    recent.clear()
                    if stall_warnings > 2:
                        return RunOutcome(status="failed", result=RESULT_STALLED)
                    history.append(await _stored_note(agent, _STALL_NOTE))
                    await runtime.bus.emit(run.id, "stall", {"warnings": stall_warnings}, True)
                continue
            ended = _finish_result(finished)
        else:
            answered: bool = bool(reply.text.strip())
            if answered and (run.mode == "chat" or not used_tools or nudges >= config.finish_nudges):
                ended = RunOutcome(status="done", result=reply.text)
            elif nudges >= config.finish_nudges:
                return RunOutcome(status="failed", result=RESULT_EMPTY_REPLIES)
            else:
                nudges += 1
                history.append(await _stored_note(agent, _NO_TOOL_NOTE if answered else _EMPTY_NOTE))
                continue
        # The run wants to end here. A plan with open steps is sent back once, so the plan the user sees matches the
        # work; then goal mode lets the checker decide whether the goal is really reached.
        if not plan_reminded and run.mode != "chat":
            unclosed: tuple[int, ...] = open_steps(await _current_plan(agent))
            if unclosed:
                plan_reminded = True
                nudges = 0
                history.append(await _stored_note(agent, _plan_note(unclosed)))
                await runtime.bus.emit(run.id, "plan_reminder", {"open_steps": list(unclosed)}, True)
                continue
        settled: RunOutcome | SendBack = (
            ended if checker is None else await _check_goal(agent, checker, history, goal, ended, sent_back)
        )
        if isinstance(settled, RunOutcome):
            return settled
        sent_back += 1
        nudges = 0
        recent.clear()
        history.append(await _stored_note(agent, settled.note))
