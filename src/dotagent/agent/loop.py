"""The agent loop: ask the model, run its tools, repeat until the run finishes, fails or is stopped."""

import asyncio
import time
from collections.abc import Awaitable, Mapping, Sequence, Set
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, ValidationError

from dotagent.agent.attachments import (
    ATTACHMENTS_EVENT,
    RunInbox,
    StoredAttachment,
    UserInput,
    attachment_summary,
    message_with_attachments,
    steer_event,
)
from dotagent.agent.campaign import session_number
from dotagent.agent.compact import (
    drop_old_images,
    estimate_tokens,
    kept_as,
    last_settled,
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
    missing_sections,
    new_ledger,
    reminder_note,
    summary_part,
    with_actions,
    with_compaction,
    with_empty_sections,
    with_instruction,
)
from dotagent.agent.executor import ToolOutcome, batch_steps, execute_calls, reached_sites
from dotagent.agent.gate import RunGate
from dotagent.agent.helpers import (
    MODEL_SWITCHED_EVENT,
    Roster,
    Unavailable,
    current_roster,
    paused_model,
    unavailable_text,
)
from dotagent.agent.judge import ClaimJudgment, TypeSafeService, claim_refused, claim_state, describe_claim
from dotagent.agent.keep_going import keep_going_ask
from dotagent.agent.lessons import LessonCase, may_learn
from dotagent.agent.limits import (
    LIMIT_EVENT,
    LIMIT_TOOLS,
    LimitReached,
    RunLimits,
    limit_event_data,
    limit_note,
    limit_reached,
    limit_result,
    limits_of,
)
from dotagent.agent.peers import peers_note
from dotagent.agent.picks import PICKS_EVENT, owner_server_groups, picked_groups, picks_event_data, with_picks
from dotagent.agent.plan import (
    EMPTY_PLAN,
    PLAN_EVENT,
    PlanItem,
    PlanPayload,
    open_steps,
    plan_event_data,
    plan_from_event,
    plan_lines,
    render_plan,
    with_step_status,
)
from dotagent.agent.playbooks import offered_to_run
from dotagent.agent.progress import (
    SINGLE_ACTION_STEPS,
    ActCall,
    Alert,
    CallSeen,
    Limits,
    Rung,
    SingleAction,
    WatchState,
    advance,
    ask_note,
    can_ask_user,
    clear_window,
    consult_note,
    image_hash,
    ladder_for,
    limits_for,
    new_watch,
    nudge_note,
    question_mode,
    replan_note,
    restriction_for,
    single_action,
    single_action_steer,
    single_action_streak,
    stop_result,
)
from dotagent.agent.prompt import (
    AgentBrief,
    build_system_prompt,
    collect_prompt_inputs,
    forgotten_unnamed,
    handed_note_ids,
    notes_missing,
    recalled_again,
    turn_note,
)
from dotagent.agent.reaction_words import HOLD_ENDED_EVENT, HOLD_EVENT, hold_lapsed_text
from dotagent.agent.resume import RESUMED_EVENT, Resumption, resumed_event_data
from dotagent.agent.review import (
    GoalChecker,
    GoalRequest,
    GoalVerdict,
    ProgressAdvice,
    ProgressConsultant,
    ProgressRequest,
    goal_checker_for,
    goal_digest,
    progress_consultant_for,
    shorten_reason,
)
from dotagent.agent.scaffold import (
    PROFILE_RUNS,
    Scaffold,
    reminder_due,
    require_room,
    scaffold_event,
    scaffold_for,
    summarise_point,
    surface_for,
    surface_for_tools,
)
from dotagent.agent.sites import SiteKnowledge, new_sites, site_knowledge, site_note, site_tag
from dotagent.agent.stepmodel import (
    STEP_ROUTE_FAILED_EVENT,
    STEP_ROUTED_EVENT,
    StepChoice,
    StepModel,
    choose,
    route_question,
    route_state,
    routed_event,
    step_state,
)
from dotagent.agent.streammask import StreamMask
from dotagent.agent.templates import AgentTeam, team_of
from dotagent.agent.verify import (
    RunFacts,
    answer_paths,
    checked_facts,
    missing_paths,
    new_facts,
    paths_note,
    with_outcomes,
)
from dotagent.agent.vision import text_for_blind_model
from dotagent.assistant import AssistantView, called_lines, owner_name_lines
from dotagent.browser.devtools import devtools_address
from dotagent.browser.session import BrowserSession
from dotagent.config import AgentConfig
from dotagent.core.errors import (
    ConfigError,
    DotError,
    InvalidValueError,
    MissingSecretError,
    ProviderError,
    ProviderFilterError,
    ProviderTransportError,
    RunCancelled,
    RunHoldLapsed,
    StoreError,
    ToolInputError,
)
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.logs import get_logger
from dotagent.core.text import CHARS_PER_TOKEN, redact_known, truncate_middle
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.harness.service import adopted_text
from dotagent.help.listed import SkillListing, listed_skills, skill_text, tools_in_order
from dotagent.help.meaning import HelpVectorsCold
from dotagent.llm.calls import CallOrigin
from dotagent.llm.http import is_retryable_provider_error
from dotagent.llm.router import ModelRef, offers_fast_mode, parse_model_ref, unavailable_reason
from dotagent.llm.traits import effective_effort
from dotagent.llm.types import (
    ChatRequest,
    DeltaSink,
    Effort,
    FinishReason,
    Message,
    ModelInfo,
    ModelReply,
    OneMessage,
    StreamDelta,
    ToolCall,
    ToolSpec,
    assistant_from_reply,
    splits_work,
    system_message,
    tool_message,
    user_message,
)
from dotagent.llm.typesafe import ChoiceAnswer
from dotagent.memory.lifecycle import Gone, notes_gone
from dotagent.memory.render import render_note
from dotagent.memory.service import (
    LiveNotes,
    Remembered,
    live_memories,
    memory_budget,
    owner_prompt_lines,
    owner_section,
    prompt_memories,
    words_only,
)
from dotagent.oauth.tokens import Account
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.skills.catalog import load_skills, playbook_root
from dotagent.skills.types import SKILL_TOOL, Skill, SkillCatalog
from dotagent.store.endings import GOAL_CHECK_MARK, PREFIX_CUT_SHORT, RESULT_EMPTY_REPLIES
from dotagent.store.models import (
    NO_PICKS,
    AgentRecord,
    CampaignRecord,
    EventRecord,
    MemoryRecord,
    RunMode,
    RunPicks,
    RunRecord,
    ThreadRecord,
)
from dotagent.store.stats import ACT_TOOLS
from dotagent.terminal.session import TerminalSession, ended_notices
from dotagent.tools.browser import VAULT_TOOLS
from dotagent.tools.campaign import CAMPAIGN_TOOLS
from dotagent.tools.consult import CONSULT_TOOL
from dotagent.tools.context import AskMode, Spawner, ToolContext
from dotagent.tools.delegate import DELEGATE_TOOL, closing_step, delegated_evidence
from dotagent.tools.groups import (
    ACCOUNTS_GROUP,
    BROWSER_GROUP,
    CORE_GROUP,
    group_of,
    groups_event,
    groups_of,
    hidden_tools,
    offered_specs,
    tools_in,
    used_groups,
)
from dotagent.tools.mail_calendar import account_lines
from dotagent.tools.meta import (
    ASK_USER_TOOL,
    FINISH_TOOL,
    MARK_PLAN_STEP_TOOL,
    UPDATE_PLAN_TOOL,
    USE_TOOLS_TOOL,
    AskUserArgs,
    FinishArgs,
    UseToolsArgs,
)
from dotagent.tools.posts import POST_TOOLS
from dotagent.tools.registry import ToolRegistry, specs_for_mode
from dotagent.tools.skill import skill_instructions
from dotagent.tools.team import CREATE_AGENT_TOOL, MESSAGE_AGENT_TOOL
from dotagent.vault.onepassword import kept_token

log = get_logger(__name__)
# The event of a run's start that found its notes or skills by their words alone (item 256).
EMBEDDING_FAILED: str = "embedding_failed"

# A delegated run reports to its parent: it neither delegates again nor asks the user, and its parent asks the helpers.
# The tools every agent keeps whatever its list says (item 86): the ones that end a run, ask, and turn groups on.
AGENT_CORE_TOOLS: tuple[str, ...] = (FINISH_TOOL, ASK_USER_TOOL, USE_TOOLS_TOOL)
_NOT_FOR_DELEGATED_RUNS: frozenset[str] = frozenset(
    {DELEGATE_TOOL, ASK_USER_TOOL, CONSULT_TOOL, MESSAGE_AGENT_TOOL, CREATE_AGENT_TOOL}
)

_SUMMARY_ATTEMPTS: int = 3
# The most skills named under a message, besides those the instructions list (item 404).
_TURN_SKILLS_MOST: int = 6
_QUERY_EARLIER_RUNS: int = 3
_SUMMARY_TRANSCRIPT_CHARS: int = 120_000
# A run whose model could not be reached waits and asks the same step again (item 158): the events that say so, and the
# delays, the first doubled each time up to the longest, and never more than a quarter of [agent] model_wait_s.
MODEL_WAITING_EVENT: str = "model_waiting"
MODEL_BACK_EVENT: str = "model_back"
_RETRY_FIRST_S: float = 10.0
_RETRY_LONGEST_S: float = 60.0
_RETRY_LEAST: int = 4
# A summary is never asked for in more words than this, whatever the budget: a longer one costs the writer's seconds
# at every compaction and fills the note that every later step carries (item 55).
_SUMMARY_MAX_WORDS: int = 1500
# The thinking of a step that its `assistant` event keeps for the page to show (item 246), its secrets masked: of a
# longer one, its start and its end. Steps think 3,500 characters on average, the longest seen near 100,000.
_THINKING_CHARS: int = 12_000
# What a step asked again first streams (item 246): the page drops what the failed try streamed, its text and its
# thinking, as after a retry of the connector (llm/connector.py).
_RESET: StreamDelta = StreamDelta(kind="reset", text="")
# A summary may take this share of the run's context budget. Each compaction summarises the previous summary again, so
# without a limit the summary grows until it alone fills the budget and every step compacts.
_SUMMARY_SHARE: float = 0.1
# A compaction leaves this share of the compaction point free under it: right after it the prompt holds the fixed
# part, the continuity note (its summary and its pinned facts may take a _SUMMARY_SHARE of the budget each) and the
# newest messages in what is left, and the room is for the steps that follow, so that a run with a small budget does
# not compact again at its next step.
_COMPACTION_ROOM: float = 0.15
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
_SUMMARY_SECTIONS_PROMPT: str = (
    "Your summary lacked the sections {sections}. Write it again, complete, with every section, starting from the "
    "draft that follows the history. "
)
_SUMMARY_SHORTER_PROMPT: str = (
    "Your summary had {words} words; the limit is {max_words}. Write it again under {max_words} words: shorten "
    "finished work to one line per step and keep every identifier. "
)
_SUMMARY_DRAFT_HEADING: str = "\n\n## Your earlier summary, which lacked the sections {sections}\n"
_NO_TOOL_NOTE: str = (
    "[system note] You replied without calling a tool. If the goal is achieved or impossible, call finish with your "
    "complete final answer. Otherwise continue by calling the tools you need."
)
_EMPTY_NOTE: str = "[system note] Your last reply was empty. Continue with the task using tools, or call finish."
# How a reply ends that the model service stopped before it was complete: at its output limit, or for an abnormal
# reason it gave (a refusal, a reason the codecs do not know).
_CUT_SHORT: frozenset[FinishReason] = frozenset({"length", "other"})
# The ends, in the service's own words, of a reply its content filter stopped (OpenCode Go's DeepSeek gave
# 'content_filter' with no output on a 340k-token chat, 2026-10-20): asked again with the same conversation, the model
# is stopped again, so the run moves to a standby model at once (:func:`_cannot_answer`) instead of being nudged.
_FILTERED: frozenset[str] = frozenset({"content_filter"})
_FILTERED_REASON: str = "its content filter stopped the reply"
_CUT_SHORT_NOTE: str = (
    "[system note] Your last reply was cut short before its end (it ended with '{reason}'). Continue with the tools "
    "you need, or give your complete answer again in fewer words."
)
_PLAN_NOTE: str = (
    "[system note] Your plan still shows step(s) {steps} as not closed. Mark each of them done, failed or skipped "
    "with mark_plan_step (several calls in one reply), then end the run again with your complete final answer."
)
# The tools whose failure says nothing of the work a finish reports: the plan's bookkeeping, a tool group turned on.
_BOOKKEEPING_TOOLS: frozenset[str] = frozenset({UPDATE_PLAN_TOOL, MARK_PLAN_STEP_TOOL, USE_TOOLS_TOOL})
_FAILED_FINISH_NOTE: str = (
    "[system note] You called finish in the same reply as {calls}, which did not work, so your answer was written "
    "before you saw what happened. Read the result above, then end the run again with your complete final answer: say "
    "what did not happen, or do it."
)
# A run that called finish in the same reply as a call that did not work was sent back once (item 552).
FINISH_AFTER_FAILURE_EVENT: str = "finish_after_failure"
_NO_PLAN: str = "The agent has not written a plan."
_CAMPAIGN_TARGETS_NOTE: str = (
    "[system note] The campaign has no targets yet. Record them with campaign_targets (the outcome, measurable, and "
    "how a session verifies it), then end the session again with your complete final answer."
)
# A session of a campaign that wanted to end while the campaign had no targets was sent back once.
CAMPAIGN_TARGETS_EVENT: str = "campaign_targets_reminder"
# The note that steers a run toward batches after single-action steps in a row (item 23).
SINGLE_ACTION_EVENT: str = "single_action_steer"
_SWITCH_NOTE: str = (
    "[system note] From this step on you run on {to}: {old} cannot answer ({reason}). Go on with the task from where "
    "it stands."
)


class RunOpening(FrozenModel):
    """How a run starts: ``text`` is its first message to the model (its goal, or the note of a resumed run), ``files``
    the attachments that came with it, ``resumed`` what a resumed run takes over from the run it resumes, ``once`` the
    choice for one message it was asked with (item 251), which the run keeps (``RunRecord.once``), ``carried`` the
    earlier runs whose words its opening or its chat brings it without the owner's message (a schedule's firing, item
    257), whose taint it starts with, and ``picks`` what the owner picked in the composer for the message (item 318),
    which the manager checked (``RunManager._check_picks``). A run that goes on from another (resumed, or started by an
    answer that came after its run ended) opens without picks: the first run's message, with the picks' lines and
    skills, stays in the chat's history, and with grouped tools ``use_tools`` turns a picked server's group on.
    ``tainted`` starts the run untrusted whatever it goes on from: the run a watcher starts (item 571) is set off by
    what a third party wrote."""

    text: str
    files: tuple[StoredAttachment, ...]
    resumed: Resumption | None
    once: OneMessage | None
    carried: tuple[str, ...]
    picks: RunPicks
    tainted: bool = False


@dataclass(frozen=True)
class AgentRun:
    """Everything one run needs besides the shared runtime."""

    runtime: Runtime
    run: RunRecord
    gate: RunGate
    cancel: asyncio.Event
    inbox: RunInbox
    spawner: Spawner
    opening: RunOpening


class RunOutcome(FrozenModel):
    """How a run ended: its status, its answer and what the finish call that ended it said the run needs from the user
    (empty when nothing, and for a run that did not end by finishing)."""

    status: Literal["done", "failed"]
    result: str
    needs: str


class RunEnd(FrozenModel):
    """How a run ended, and what its lessons are drawn from once it has ended, when it may hold some
    (:mod:`dotagent.agent.lessons`)."""

    outcome: RunOutcome
    lessons: LessonCase | None


async def _discard(delta: StreamDelta) -> None:
    return None


def _acts(calls: Sequence[ToolCall], questions: AskMode) -> bool:
    """Whether a step does something: a question left for the user does not (every question where the run leaves them,
    one asked without ``wait`` where the run chooses, item 508), since it changes nothing until someone answers it, so a
    run that only leaves questions between text replies still ends."""
    return any(call.name != ASK_USER_TOOL or _waits(call, questions) for call in calls)


def _waits(call: ToolCall, questions: AskMode) -> bool:
    """Whether the question ``call`` asks waits for its answer: every one where the run waits, and one asked with
    ``wait``, as the tool reads its arguments, where it chooses. A call whose arguments the tool refuses asks nothing,
    so it waits for nothing."""
    if questions != "choose":
        return questions == "wait"
    try:
        return AskUserArgs.model_validate(call.arguments).wait
    except ValidationError:
        return False


class Compaction(FrozenModel):
    """How the earlier conversation was folded: the note that replaces it and how that note came about.

    ``method`` is ``model`` when a model wrote the summary (``model`` names it: the fast model of ``[models] fast``, or
    the run's own model when the fast one gave no usable summary or was paused, which ``fast_problem`` explains) and
    ``mechanical`` when neither could (``problem`` says why) and the newest part of the transcript stands in for it.
    """

    note: Message
    method: Literal["model", "mechanical"]
    model: str
    attempts: int
    problem: str | None
    fast_problem: str | None
    check: ContinuityCheck | None


async def _with_plan(agent: AgentRun, system: Message) -> Message:
    """The system message with the run's plan after it while the plan has open steps (item 87); the message itself
    when there is no plan or every step is closed."""
    plan: PlanPayload = await _current_plan(agent)
    if not plan.steps or not open_steps(plan.steps):
        return system
    return system.model_copy(update={"content": f"{system.content}\n\n# Your plan now\n{plan_lines(plan)}"})


async def _current_plan(agent: AgentRun) -> PlanPayload:
    latest: EventRecord | None = await agent.runtime.store.last_event(agent.run.id, PLAN_EVENT)
    return EMPTY_PLAN if latest is None else plan_from_event(latest)


async def _model_summary(
    agent: AgentRun, ref: ModelRef, transcript: str, context_tokens: int, window: int | None
) -> tuple[str | None, int, str | None]:
    """Ask the run's own model for a summary that fits a run with a budget of ``context_tokens``: ``(text or None,
    attempts made, last problem)``.

    An empty reply is asked again (the first try without reasoning, later ones with a little, since some models spend
    their whole answer on thinking). A summary far over its word limit is asked again with the limit and the overrun
    in front of the prompt; one that lacks a section is asked again once, with the missing sections named and the
    first reply carried along as a draft. The attempts stay within ``_SUMMARY_ATTEMPTS``; a second reply that still
    lacks a section is returned as it is, for the check to say so. A provider error or a timeout ends the attempts at
    once, as the provider already retried what can be retried.
    """
    max_words: int = min(_SUMMARY_MAX_WORDS, round(context_tokens * _SUMMARY_SHARE * _WORDS_PER_TOKEN))
    base: str = _SUMMARY_PROMPT.format(max_words=max_words)
    prompt: str = base
    history: str = transcript
    problem: str | None = None
    asked_shorter: bool = False
    asked_sections: bool = False
    for attempt in range(1, _SUMMARY_ATTEMPTS + 1):
        request: ChatRequest = ChatRequest(
            model=ref.model,
            messages=(system_message(prompt), user_message(history, ())),
            tools=(),
            temperature=0.2,
            max_output_tokens=None,
            effort="off" if attempt == 1 else "low",
            speed="standard",
            session_key=f"{agent.run.thread_id}:summary",
            cache_mark=None,
            window=window,
        )
        try:
            async with asyncio.timeout(agent.runtime.config.agent.model_call_timeout_s):
                reply: ModelReply = await agent.runtime.router.complete(
                    ref, request, _discard, CallOrigin(run_id=agent.run.id, purpose="compaction")
                )
        except (ProviderError, MissingSecretError, TimeoutError) as error:
            problem = "the summary request timed out" if isinstance(error, TimeoutError) else error.message
            log.warning("summary_failed", run_id=agent.run.id, model=ref.label, attempt=attempt, error=problem)
            return None, attempt, problem
        text: str = reply.text.strip()
        words: int = len(text.split())
        if words > max_words * _SUMMARY_SLACK:
            problem = f"the summary had {words} words, over the limit of {max_words}"
            log.warning("summary_too_long", run_id=agent.run.id, model=ref.label, attempt=attempt, words=words)
            if not asked_shorter:
                asked_shorter = True
                prompt = _SUMMARY_SHORTER_PROMPT.format(words=words, max_words=max_words) + base
            continue
        if not text:
            problem = f"the summary model returned no text (finish: {reply.raw_finish})"
            log.warning(
                "summary_empty",
                run_id=agent.run.id,
                model=ref.label,
                attempt=attempt,
                finish=reply.raw_finish,
                thinking_chars=len(reply.thinking),
            )
            continue
        missing: tuple[str, ...] = missing_sections(text)
        if missing and not asked_sections:
            asked_sections = True
            sections: str = ", ".join(missing)
            problem = f"the summary lacked the sections {sections}"
            log.warning(
                "summary_sections_missing", run_id=agent.run.id, model=ref.label, attempt=attempt, sections=missing
            )
            prompt = _SUMMARY_SECTIONS_PROMPT.format(sections=sections) + base
            history = transcript + _SUMMARY_DRAFT_HEADING.format(sections=sections) + text
            continue
        return text, attempt, problem
    return None, _SUMMARY_ATTEMPTS, problem


async def _compact(
    agent: AgentRun,
    head: Sequence[Message],
    ledger: RunLedger,
    context_tokens: int,
    part_chars: int,
    window: int | None,
) -> Compaction:
    """Fold ``head`` into a continuity note written by the fast model (``[models] fast``), the side work of a run that
    its own, slower model need not do; the run's own model writes it when the fast one gives no usable summary (a
    provider error, a timeout, no text, a summary far too long, a window that cannot be read) and, without asking the
    fast one, while the router has paused it (item 240). The compaction event names the fast model's problem, so that a
    fast model that never answers is seen, not hidden.

    The ledger and the plan are pinned into the note in about ``part_chars`` characters; the model's summary is checked
    against ``head``, and a section it still lacks after it was asked for again goes into the note as an empty heading,
    which the check names (review O9). When neither model writes one, the note carries the newest ``part_chars``
    characters of the transcript instead and says so.
    """
    own: ModelRef = parse_model_ref(agent.run.model)
    fast: ModelRef = parse_model_ref(agent.runtime.roles.fast())
    readable: list[Message] = [
        message.model_copy(update={"content": summary_part(message.content)}) for message in head
    ]
    transcript: str = truncate_middle(render_transcript(readable), _SUMMARY_TRANSCRIPT_CHARS)
    plan: PlanPayload = await _current_plan(agent)
    now: float = time.time()
    paused: Unavailable | None = (
        None if fast.label == own.label else paused_model(agent.runtime.router, fast.label, now)
    )
    writer: ModelRef = fast if paused is None else own
    fast_problem: str | None = None if paused is None else f"{unavailable_text(paused, now)} cannot answer now"
    if paused is not None:
        log.info("summary_fast_paused", run_id=agent.run.id, fast=fast.label, own=own.label, until=paused.until)
    # The run's own model is asked with the window the run planned with (item 520), the fast one with its own. A fast
    # model whose window cannot be read (an Ollama model that is not reachable) is one that cannot answer: the run's
    # own model writes the summary, as it does for any other problem of the fast one (item 552).
    asked_window: int | None = window
    if writer.label != own.label:
        try:
            asked_window = await agent.runtime.router.window_of(writer)
        except (ProviderError, MissingSecretError) as error:
            fast_problem = error.message
            log.warning(
                "summary_fast_failed", run_id=agent.run.id, fast=fast.label, own=own.label, problem=fast_problem
            )
            writer = own
    text, attempts, problem = await _model_summary(agent, writer, transcript, context_tokens, asked_window)
    if text is None and writer.label != own.label:
        fast_problem = problem if problem is not None else "no reply"
        log.warning("summary_fast_failed", run_id=agent.run.id, fast=fast.label, own=own.label, problem=fast_problem)
        writer = own
        text, own_attempts, problem = await _model_summary(agent, own, transcript, context_tokens, window)
        attempts += own_attempts
    # The summary stays in the run's context and its record: the secrets Dot holds, and any of a known form, are
    # masked in it (review Y3).
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    if text is None:
        reason: str = problem if problem is not None else "no reply"
        mechanical: str = redact_known(mechanical_summary(transcript, reason, part_chars), secrets)
        note: str = continuity_note(ledger, plan, part_chars, mechanical, "mechanical, no model", None)
        return Compaction(
            note=user_message(note, ()),
            method="mechanical",
            model=writer.label,
            attempts=attempts,
            problem=reason,
            fast_problem=fast_problem,
            check=None,
        )
    summary: str = redact_known(text, secrets)
    check: ContinuityCheck = check_continuity(summary, history_text(head), ledger, str(Path.home()))
    if check.missing_sections:
        log.warning(
            "summary_sections_left_empty",
            run_id=agent.run.id,
            model=writer.label,
            sections=list(check.missing_sections),
        )
        summary = with_empty_sections(summary, check.missing_sections)
    author: str = (
        f"written by you, {writer.label}" if writer.label == own.label else f"written by the fast model {writer.label}"
    )
    return Compaction(
        note=user_message(continuity_note(ledger, plan, part_chars, summary, author, check), ()),
        method="model",
        model=writer.label,
        attempts=attempts,
        problem=problem,
        fast_problem=fast_problem,
        check=check,
    )


def _compaction_event(
    compaction: Compaction, summarised: int, ledger: RunLedger, tokens_before: int, tokens_after: int
) -> JsonObject:
    """The data of a ``compacted`` event; ``tokens_before`` and ``tokens_after`` are the estimated size of the prompt
    before and after the compaction, which the conversation shows (item 312)."""
    check: ContinuityCheck | None = compaction.check
    return {
        "summarised_messages": summarised,
        "compaction": ledger.compactions,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "method": compaction.method,
        "model": compaction.model,
        "attempts": compaction.attempts,
        "problem": compaction.problem,
        "fast_problem": compaction.fast_problem,
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


def _cache_mark(history: Sequence[Message], config: AgentConfig) -> int | None:
    """The index in a step's messages, its system message first, of the last one that trimming leaves as it is in the
    steps to come (:func:`dotagent.agent.compact.last_settled`)."""
    settled: int | None = last_settled(history, config.keep_recent_tool_results, config.keep_recent_screenshots)
    return None if settled is None else settled + 1


async def _fit_context(
    agent: AgentRun,
    history: list[Message],
    system: Message,
    specs: tuple[ToolSpec, ...],
    ratio: float,
    ledger: RunLedger,
    scaffold: Scaffold,
) -> tuple[list[Message], int, RunLedger]:
    """Keep the prompt inside the run's budget (``scaffold``): drop stale screenshots, shorten old output, then
    compact. The messages a compaction keeps and the parts of its note are sized to the budget, so that a compaction
    leaves room for the steps after it."""
    config: AgentConfig = agent.runtime.config.agent
    context_tokens: int = scaffold.context_tokens
    current: list[Message] = drop_old_images(history, config.keep_recent_screenshots)
    estimated: int = round(estimate_tokens([system, *current], specs) * ratio)
    if estimated > context_tokens * config.context_elide_share:
        current = shrink_old_tool_results(current, config.keep_recent_tool_results)
        estimated = round(estimate_tokens([system, *current], specs) * ratio)
    summarise_at: float = summarise_point(config, scaffold.window, scaffold.chosen, context_tokens)
    if estimated > summarise_at:
        # The budget in the units of estimate_tokens, before its calibration against the provider's counts.
        plain: float = context_tokens / ratio
        tail_tokens: int = round(
            summarise_at / ratio * (1 - _COMPACTION_ROOM) - 2 * _SUMMARY_SHARE * plain
        ) - estimate_tokens([system], specs)
        head, tail = split_for_summary(current, tail_tokens)
        if head:
            ledger = with_compaction(ledger)
            part_chars: int = round(plain * _SUMMARY_SHARE * CHARS_PER_TOKEN)
            compaction: Compaction = await _compact(agent, head, ledger, context_tokens, part_chars, scaffold.window)
            before: int = estimated
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
                agent.run.id, "compacted", _compaction_event(compaction, len(head), ledger, before, estimated), True
            )
    if current != history:
        await agent.runtime.store.replace_messages(agent.run.thread_id, current)
    return current, estimated, ledger


async def _embedding_failed(runtime: Runtime, run: RunRecord, purpose: str, error: DotError) -> None:
    """Tell the log and the timeline that the start of ``run`` found its ``purpose`` (\"notes\" or \"skills\") by
    their words alone, since the embedding service failed with ``error`` after its retries (item 256)."""
    log.warning(
        "embedding_failed",
        run_id=run.id,
        purpose=purpose,
        error_type=type(error).__name__,
        status=error.context.get("status"),
        error=error.message,
    )
    await runtime.bus.emit(run.id, EMBEDDING_FAILED, {"purpose": purpose, "error": error.message}, True)


async def _start_notes(runtime: Runtime, run: RunRecord, query: str, budget: int) -> Remembered:
    """The notes ``run`` starts with: by meaning and words, or by words alone when the embedding service fails."""
    try:
        return await prompt_memories(runtime.memory, query, budget, run.agent_id)
    except ProviderError as error:
        await _embedding_failed(runtime, run, "notes", error)
        return await prompt_memories(words_only(runtime.memory), query, budget, run.agent_id)


async def _close_skills(runtime: Runtime, run: RunRecord, needs: Sequence[str], texts: list[str]) -> list[list[int]]:
    """For each of ``needs``, the places of the skills ``texts`` close to it in meaning, the closest first; none when
    embeddings are off, or when the embedding service fails (the skills are then listed by their words, and the run
    says so, once)."""
    if runtime.help_vectors is None:
        return [[] for _ in needs]
    found: list[list[int]] = []
    try:
        for need in needs:
            found.append(await runtime.help_vectors.closest(need, texts))
    except HelpVectorsCold as cold:
        # The skills' embeddings are being made in the background (the daemon's first run, or new skills; item 404):
        # by their words this once, without a word of failure.
        log.info("skill_embeddings_warming", run_id=run.id, missing=cold.missing)
    except (ProviderError, StoreError) as error:
        # The service failed, or answered in another dimension than the texts kept: another model.
        await _embedding_failed(runtime, run, "skills", error)
    return [*found, *([] for _ in range(len(needs) - len(found)))]


async def warm_help_vectors(runtime: Runtime) -> None:
    """Embed the texts of every skill a run may be offered, as the daemon starts (item 404), so that the first run
    finds them ready: before, the first run after each start waited for them, half a minute and more. One warming at
    a time: a search that finds its texts cold meanwhile joins this one instead of starting its own. The daemon runs
    this as a task of its own and cancels it as it stops; a service that fails is logged by the warming, and the first
    search that finds its texts cold starts it again."""
    if runtime.help_vectors is None:
        return
    catalog: SkillCatalog = await asyncio.to_thread(
        load_skills, runtime.config.skills, runtime.plugins.plugins, playbook_root(runtime.config)
    )
    offered: tuple[Skill, ...] = await offered_to_run(runtime.config, catalog, None)
    await runtime.help_vectors.warm([skill_text(skill) for skill in offered])


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
                    runtime.roles.vision(),
                    agent.run.thread_id,
                    agent.run.id,
                    outcome.call,
                    outcome.result,
                )
                for outcome in outcomes
            )
        )
    )
    return [tool_message(outcome.call, text, ()) for outcome, text in zip(outcomes, texts, strict=True)]


async def _keep_ended(agent: AgentRun, calls: Sequence[ToolCall], ended: Sequence[ToolOutcome], can_see: bool) -> None:
    """Store the results of the calls of a cut step that had ended, in the order of ``calls``. A model that cannot see
    gets their text without the images: a description would ask the vision model while the run is being stopped."""
    order: dict[str, int] = {call.id: index for index, call in enumerate(calls)}
    kept: list[ToolOutcome] = sorted(ended, key=lambda outcome: order[outcome.call.id])
    if kept:
        await agent.runtime.store.append_messages(
            agent.run.thread_id,
            [
                tool_message(outcome.call, outcome.result.text, outcome.result.images if can_see else ())
                for outcome in kept
            ],
        )


def _act_calls(outcomes: Sequence[ToolOutcome]) -> tuple[ActCall, ...]:
    """The act calls of a step as the single-action counter sees them: the batched browser and desktop calls, with
    the steps they carried and the target of their policy action (the page's address, the application)."""
    return tuple(
        ActCall(
            tool=outcome.call.name,
            steps=batch_steps(outcome.call.arguments),
            target=outcome.action.target if outcome.action is not None else "",
        )
        for outcome in outcomes
        if outcome.call.name in ACT_TOOLS
    )


def _calls_seen(outcomes: Sequence[ToolOutcome]) -> tuple[CallSeen, ...]:
    """The calls of a step as the progress watchdog sees them."""
    return tuple(
        CallSeen(
            name=outcome.call.name,
            arguments=dumps_compact(outcome.call.arguments),
            result=outcome.result.text,
            failed=outcome.result.is_error,
            images=tuple(image_hash(image.data_b64) for image in outcome.result.images),
        )
        for outcome in outcomes
    )


def _only(registry: ToolRegistry, allowed: frozenset[str]) -> ToolRegistry:
    """The tools of ``registry`` that ``allowed`` names; a call of any other one is answered as a call of an unknown
    tool, so a step that is limited stays limited even for a model that calls a tool it was not offered."""
    return ToolRegistry(tools={name: tool for name, tool in registry.tools.items() if name in allowed})


def _run_tools(registry: ToolRegistry, mode: RunMode, specs: Sequence[ToolSpec]) -> ToolRegistry:
    """The tools a run can call: the ones it offers, and the ones of other modes, whose calls are answered as not
    available in the run's mode. A tool of the run's mode that the run leaves out (use_tools when its tools are not
    grouped, delegate, ask_user and consult in a delegated run, skill when no skill is installed, consult without a
    helper) is answered as unknown."""
    offered: frozenset[str] = frozenset(spec.name for spec in specs)
    return ToolRegistry(
        tools={name: tool for name, tool in registry.tools.items() if name in offered or mode not in tool.modes}
    )


def _turned_on(outcomes: Sequence[ToolOutcome], hidden: Mapping[str, str], on: frozenset[str]) -> tuple[str, ...]:
    """The tool groups that the calls of a step turn on, in the order of the calls: the group of a call of a tool whose
    group was off, and the group of a use_tools call that succeeded."""
    named: tuple[str, ...] = tuple(
        hidden[outcome.call.name]
        if outcome.call.name in hidden
        else UseToolsArgs.model_validate(outcome.call.arguments).group
        for outcome in outcomes
        if outcome.call.name in hidden or (outcome.call.name == USE_TOOLS_TOOL and not outcome.result.is_error)
    )
    return tuple(dict.fromkeys(group for group in named if group not in on))


async def _opening_text(opening: RunOpening, offered: Sequence[Skill], room: int) -> str:
    """The run's first message to the model: its opening's text, then what the owner picked for it (item 318), each
    picked skill's instructions within ``room`` characters as the ``skill`` tool gives them. A picked skill no longer
    ``offered`` to the run (taken away since the message was sent) fails the run, which says why."""
    if opening.picks == NO_PICKS:
        return opening.text
    by_name: dict[str, Skill] = {skill.name: skill for skill in offered}
    gone: list[str] = [name for name in opening.picks.skills if name not in by_name]
    if gone:
        raise InvalidValueError(
            f"the skills {', '.join(gone)} picked for this message are no longer offered to it", {"skills": list(gone)}
        )
    skills: list[str] = [await skill_instructions(by_name[name], room) for name in opening.picks.skills]
    return with_picks(opening.text, opening.picks, skills)


async def _goal_query(runtime: Runtime, run: RunRecord) -> str:
    """What a run's notes, and the help its instructions list (item 166), are chosen by: its goal, then the goals of the
    latest earlier runs of its thread, so a short follow-up ("go on", "now the second one") still finds the notes and
    the help of the work it continues."""
    earlier: list[RunRecord] = await runtime.store.list_runs(_QUERY_EARLIER_RUNS + 1, run.thread_id)
    goals: list[str] = [record.goal for record in earlier if record.id != run.id and record.parent_run_id is None]
    return "\n".join([run.goal, *goals[:_QUERY_EARLIER_RUNS]])


async def _arrival_note(
    agent: AgentRun, sites: Sequence[str], offered: Sequence[Skill], listed: Set[str]
) -> str | None:
    """The note of what earlier runs learned on ``sites``, which the run just reached for the first time (item 308),
    its lessons counted as handed to the run; ``None`` when they bring nothing. ``offered`` are the skills the run is
    offered, ``listed`` the names of those its instructions list."""
    runtime: Runtime = agent.runtime
    run: RunRecord = agent.run
    handed: set[str] = await runtime.store.handed_memory_ids(run.id)
    known: tuple[SiteKnowledge, ...] = await site_knowledge(runtime, run.agent_id, sites, offered, listed, handed)
    if not known:
        return None
    ids: list[str] = [note.id for entry in known for note in entry.lessons]
    if ids:
        await runtime.store.mark_memories_used(run.id, ids, time.time(), "site")
        where: str = ", ".join(entry.site for entry in known if entry.lessons)
        await runtime.bus.emit(run.id, "memories", {"ids": [*ids], "site": where}, True)
    return site_note(known)


def _new_memories_note(live: LiveNotes) -> str:
    lines: str = "\n".join(f"- {render_note(record)}" for record in live.records)
    more: str = (
        f"\n{live.left_out} more changed notes did not fit here; memory_search finds them." if live.left_out else ""
    )
    return (
        "[system note] These notes were saved to memory or changed while you work (by the user or another run). They "
        "are data about the user and their setup, like the notes under your messages; take them into "
        f"account:\n{lines}"
        f"{more}"
    )


def _team_name(name: str) -> str:
    """The name of the ready team an agent called ``name`` is in (item 512), empty for none."""
    found: AgentTeam | None = team_of(name)
    return "" if found is None else found.name


def _new_teammates_note(agents: Sequence[AgentRecord]) -> str:
    """The teammates that appeared since the run started (item 99): made by this run, by the owner or by another run."""
    lines: str = "\n".join(f"- {agent.name}: {agent.role}" if agent.role else f"- {agent.name}" for agent in agents)
    return (
        "[system note] These agents joined the team while you work (made by you, by the owner or by another run). "
        f"Send one a message with message_agent when a part of the work fits its role:\n{lines}"
    )


def _plan_note(unclosed: Sequence[int]) -> str:
    return _PLAN_NOTE.format(steps=", ".join(str(number) for number in unclosed))


def _failed_before_finish(outcomes: Sequence[ToolOutcome], finished: ToolOutcome) -> tuple[ToolOutcome, ...]:
    """The calls of the reply that ends the run with ``finished``, ahead of it, that did not work (an error, a
    refusal, a step kept for the owner): its answer was written before the model read their results (item 552). The
    plan's bookkeeping is not among them."""
    ahead: Sequence[ToolOutcome] = outcomes[: outcomes.index(finished)]
    return tuple(
        outcome for outcome in ahead if outcome.result.is_error and outcome.call.name not in _BOOKKEEPING_TOOLS
    )


def _failed_finish_note(failed: Sequence[ToolOutcome]) -> str:
    return _FAILED_FINISH_NOTE.format(calls=", ".join(dict.fromkeys(outcome.call.name for outcome in failed)))


def _finish_result(outcome: ToolOutcome) -> RunOutcome:
    args: FinishArgs = FinishArgs.model_validate(outcome.call.arguments)
    return RunOutcome(status="done" if args.success else "failed", result=args.answer, needs=args.needs_from_user or "")


class SendBack(FrozenModel):
    """The goal check does not accept the end of the run yet: the run goes on after ``note``."""

    note: str


def _delegated_closings(outcomes: Sequence[ToolOutcome]) -> list[tuple[int, ToolOutcome]]:
    """The delegate calls of one step that named a plan step the plan had and ran, with that step."""
    closings: list[tuple[int, ToolOutcome]] = []
    for outcome in outcomes:
        if outcome.call.name != DELEGATE_TOOL or outcome.action is None:
            continue
        step: int | None = closing_step(outcome.call.arguments.get("step"), outcome.result)
        if step is not None:
            closings.append((step, outcome))
    return closings


async def _close_delegated_steps(agent: AgentRun, outcomes: Sequence[ToolOutcome]) -> str | None:
    """Close the plan steps the delegate calls of this step named: done with the report as evidence when the delegated
    run ended done, failed otherwise. The loop does this, not the tool, because delegate calls run side by side and the
    plan is rewritten whole. Returns the note for the model when a step of a run that ended done could not be closed
    (the plan was rewritten under it), else ``None``; a call that was refused closes nothing and its result already
    says why."""
    closings: list[tuple[int, ToolOutcome]] = _delegated_closings(outcomes)
    if not closings:
        return None
    plan: PlanPayload = await _current_plan(agent)
    steps: tuple[PlanItem, ...] = plan.steps
    problems: list[str] = []
    for number, outcome in closings:
        failed: bool = outcome.result.is_error
        try:
            steps = with_step_status(steps, number, "failed" if failed else "done", delegated_evidence(outcome.result))
        except ToolInputError as error:
            if not failed:
                problems.append(f"step {number}: {error.message}")
    if steps != plan.steps:
        await agent.runtime.bus.emit(
            agent.run.id, PLAN_EVENT, plan_event_data(plan.model_copy(update={"steps": steps})), True
        )
    if not problems:
        return None
    return (
        "[system note] The delegated runs ended, but their plan steps could not be closed, so mark them yourself: "
        + ("; ".join(problems))
    )


def _ended_with(reply: ModelReply) -> str:
    """How the model service said a reply ended, in its own words where it gave them."""
    return reply.raw_finish or reply.finish


def _cut_short_end(reply: ModelReply) -> RunOutcome:
    """The end of a run whose replies the model service kept cutting short: failed, with the last one's reason."""
    return RunOutcome(
        status="failed",
        result=f"{PREFIX_CUT_SHORT} and the run did not finish (the last one ended with '{_ended_with(reply)}').",
        needs="",
    )


async def _stored_note(agent: AgentRun, text: str) -> Message:
    """A note from the system to the model, stored with the conversation. The caller adds it to the history."""
    note: Message = user_message(text, ())
    await agent.runtime.store.append_messages(agent.run.thread_id, [note])
    return note


def _since_goal(history: Sequence[Message], goal: Message) -> Sequence[Message]:
    """The part of the conversation that belongs to this run: from its goal on, or all of it once summarising has
    folded the start of the run into a summary. The goal is found also once its images were dropped into a note
    (item 351: the whole conversation of the chat's earlier runs went to the goal check then)."""
    for index in range(len(history) - 1, -1, -1):
        if kept_as(history[index], goal):
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
        return RunOutcome(status="done", result=ended.result, needs=ended.needs)
    if verdict.decision == "blocked":
        return RunOutcome(
            status="failed",
            result=_with_check_note(ended.result, f"the goal cannot be reached from here. {reason}"),
            needs=ended.needs,
        )
    if sent_back >= patience:
        return RunOutcome(
            status="failed",
            result=_with_check_note(
                ended.result, f"the goal is still not reached after {patience} rounds of further work. {reason}"
            ),
            needs=ended.needs,
        )
    return SendBack(note=_send_back_note(reason))


def _after_limit_verdict(verdict: GoalVerdict, ended: RunOutcome) -> RunOutcome:
    """What the checker's verdict makes of the finish of a run past its limit: the run cannot be sent back to work, so
    a goal that is not reached ends it as failed."""
    reason: str = shorten_reason(verdict.reason)
    if verdict.decision == "achieved":
        return RunOutcome(status="done", result=ended.result, needs=ended.needs)
    if verdict.decision == "blocked":
        return RunOutcome(
            status="failed",
            result=_with_check_note(ended.result, f"the goal cannot be reached from here. {reason}"),
            needs=ended.needs,
        )
    return RunOutcome(
        status="failed",
        result=_with_check_note(ended.result, f"the goal is not reached, and the run's limit is reached. {reason}"),
        needs=ended.needs,
    )


async def browser_of(runtime: Runtime, run: RunRecord) -> BrowserSession:
    """The browser session the run works in: the one its campaign asked for (a delegated run follows its parent's
    campaign), else the daemon's."""
    campaign_id: str | None = run.campaign_id
    if campaign_id is None and run.parent_run_id is not None:
        campaign_id = (await runtime.store.get_run(run.parent_run_id)).campaign_id
    if campaign_id is None:
        return runtime.browser
    campaign: CampaignRecord = await runtime.store.get_campaign(campaign_id)
    return runtime.browsers.session_for(campaign.browser)


async def _session_number(agent: AgentRun, campaign_id: str) -> int:
    return session_number(await agent.runtime.store.campaign_sessions(campaign_id), agent.run.id)


async def _missing_in_answer(agent: AgentRun, answer: str, facts: RunFacts) -> tuple[str, ...]:
    """The paths ``answer`` names that do not exist and that no successful call of the run named."""
    return await asyncio.to_thread(missing_paths, answer_paths(answer), facts.named, agent.runtime.workspace)


async def _ask_checker(
    agent: AgentRun,
    checker: GoalChecker,
    history: Sequence[Message],
    goal: Message,
    ended: RunOutcome,
    sent_back: int,
    facts: RunFacts,
) -> GoalVerdict | RunOutcome:
    """Goal mode: the checker's verdict on whether the end a run reached is the goal reached, from a digest of the run
    and the ``facts`` the harness measured.

    A checker that cannot answer ends the run as failed, with the answer kept and the problem stated: an ending that
    was not checked is not accepted.
    """
    run: RunRecord = agent.run
    patience: int = agent.runtime.config.agent.goal_patience
    plan: PlanPayload = await _current_plan(agent)
    request: GoalRequest = GoalRequest(
        goal=run.goal,
        plan=render_plan(plan.steps),
        targets=plan.targets,
        digest=goal_digest(render_transcript(_since_goal(history, goal))),
        facts=await asyncio.to_thread(checked_facts, facts, ended.result, agent.runtime.workspace),
        answer=ended.result,
        claimed_success=ended.status == "done",
        keep_going=keep_going_ask(run.goal) is not None,
    )
    # The typed judge, when one is on, refuses a claim it finds clearly false before the review model is called.
    if request.claimed_success:
        judged: ClaimJudgment = await agent.runtime.judge.judge_claim(
            claim_state(request.goal, request.plan, request.targets, request.digest, request.facts, request.answer),
            run.id,
        )
        threshold: float = agent.runtime.config.judge.target_threshold
        if claim_refused(judged, threshold):
            log.info(
                "goal_claim_judged",
                run_id=run.id,
                probability=judged.probability,
                second=judged.second,
                check=sent_back + 1,
            )
            return GoalVerdict(
                decision="continue",
                reason=(
                    f"The typed judge gives {describe_claim(judged)} for the goal being reached as the answer claims "
                    f"(it takes {threshold:.2f}). Verify the result with your own reads and finish with what they "
                    "show."
                ),
                unproven_steps=(),
            )
    try:
        return await checker(request)
    except (ProviderError, MissingSecretError) as error:
        log.error("goal_check_failed", run_id=run.id, error=error.message, context=error.context)
        await agent.runtime.bus.emit(
            run.id,
            "goal_check",
            {
                "decision": "error",
                "reason": shorten_reason(error.message),
                # No step was judged; every goal check says how many it could not bear out, which the evals count.
                "unproven_steps": [],
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
            needs=ended.needs,
        )


async def _check_goal(
    agent: AgentRun,
    checker: GoalChecker,
    history: Sequence[Message],
    goal: Message,
    ended: RunOutcome,
    sent_back: int,
    facts: RunFacts,
) -> RunOutcome | SendBack:
    """What the checker makes of the end a run reached: it stands, or the run is sent back to work."""
    asked: GoalVerdict | RunOutcome = await _ask_checker(agent, checker, history, goal, ended, sent_back, facts)
    if isinstance(asked, RunOutcome):
        return asked
    patience: int = agent.runtime.config.agent.goal_patience
    settled: RunOutcome | SendBack = _after_verdict(asked, ended, sent_back, patience)
    await agent.runtime.bus.emit(
        agent.run.id,
        "goal_check",
        {
            "decision": asked.decision,
            "reason": shorten_reason(asked.reason),
            "unproven_steps": list(asked.unproven_steps),
            "check": sent_back + 1,
            "patience": patience,
            "final": isinstance(settled, RunOutcome),
        },
        True,
    )
    return settled


async def _check_goal_at_limit(
    agent: AgentRun,
    checker: GoalChecker,
    history: Sequence[Message],
    goal: Message,
    ended: RunOutcome,
    sent_back: int,
    facts: RunFacts,
) -> RunOutcome:
    """What the checker makes of the finish of a run past its limit: its verdict is final, as the run cannot be sent
    back to work."""
    asked: GoalVerdict | RunOutcome = await _ask_checker(agent, checker, history, goal, ended, sent_back, facts)
    if isinstance(asked, RunOutcome):
        return asked
    await agent.runtime.bus.emit(
        agent.run.id,
        "goal_check",
        {
            "decision": asked.decision,
            "reason": shorten_reason(asked.reason),
            "unproven_steps": list(asked.unproven_steps),
            "check": sent_back + 1,
            "patience": agent.runtime.config.agent.goal_patience,
            "final": True,
            "limit": True,
        },
        True,
    )
    return _after_limit_verdict(asked, ended)


class StallAnswer(FrozenModel):
    """How a run goes on after a progress alert: ``note`` is given to the model, and ``allowed`` names the tools of the
    next step (``None``: all of them)."""

    note: str
    allowed: frozenset[str] | None


def _stall_data(alert: Alert, extra: JsonObject) -> JsonObject:
    """The ``stall`` event of an alert. ``warnings`` is the rung reached, under the name that older readers know."""
    return {
        "warnings": alert.level,
        "level": alert.level,
        "action": alert.rung,
        "reason": alert.pattern,
        "detail": alert.detail,
        **extra,
    }


async def _consult(
    agent: AgentRun,
    consultant: ProgressConsultant,
    alert: Alert,
    history: Sequence[Message],
    goal: Message,
    ledger: RunLedger,
    plan: str,
) -> StallAnswer | RunOutcome:
    """The consult rung: the review model looks at the run and its advice is given to the model as a note.

    A review model that cannot answer ends the run as stalled, with the problem stated: the run is not told that a
    second opinion was taken when none was.
    """
    run: RunRecord = agent.run
    request: ProgressRequest = ProgressRequest(
        goal=run.goal,
        plan=plan if plan else _NO_PLAN,
        digest=goal_digest(render_transcript(_since_goal(history, goal))),
        pattern=alert.detail,
    )
    try:
        advice: ProgressAdvice = await consultant(request)
    except ProviderError as error:
        log.error("progress_consult_failed", run_id=run.id, error=error.message, context=error.context)
        await agent.runtime.bus.emit(
            run.id, "stall", _stall_data(alert, {"error": shorten_reason(error.message)}), True
        )
        reason: str = f"{alert.detail} The review model could not give a second opinion: {error.message}"
        return RunOutcome(status="failed", result=stop_result(reason, ledger, plan), needs="")
    diagnosis: str = shorten_reason(advice.diagnosis)
    next_steps: list[str] = [shorten_reason(step) for step in advice.next_steps]
    await agent.runtime.bus.emit(
        run.id, "stall", _stall_data(alert, {"diagnosis": diagnosis, "next_steps": list(next_steps)}), True
    )
    return StallAnswer(note=consult_note(alert, diagnosis, next_steps), allowed=None)


async def _answer_alert(
    agent: AgentRun,
    consultant: ProgressConsultant,
    alert: Alert,
    can_ask: bool,
    history: Sequence[Message],
    goal: Message,
    ledger: RunLedger,
) -> StallAnswer | RunOutcome:
    """What the rung an alert reached asks for: a note (with the tools of the next step limited for a re-plan or a
    question), the advice of the review model, or the end of the run with what was tried."""
    plan: str = plan_lines(await _current_plan(agent))
    match alert.rung:
        case "consult":
            return await _consult(agent, consultant, alert, history, goal, ledger, plan)
        case "stop":
            await agent.runtime.bus.emit(agent.run.id, "stall", _stall_data(alert, {}), True)
            return RunOutcome(status="failed", result=stop_result(alert.detail, ledger, plan), needs="")
        case "nudge":
            note: str = nudge_note(alert)
        case "replan":
            note = replan_note(alert, can_ask)
        case "ask":
            note = ask_note(alert)
    await agent.runtime.bus.emit(agent.run.id, "stall", _stall_data(alert, {}), True)
    return StallAnswer(note=note, allowed=restriction_for(alert.rung, can_ask))


def _run_end(
    agent: AgentRun, history: Sequence[Message], goal: Message, outcome: RunOutcome, facts: RunFacts
) -> RunEnd:
    """The end of a run, with what its lessons are drawn from when it may hold some."""
    run: RunRecord = agent.run
    if not may_learn(agent.runtime.config.agent.lessons, run.mode, run.parent_run_id, outcome.status):
        return RunEnd(outcome=outcome, lessons=None)
    case: LessonCase = LessonCase(
        run_id=run.id,
        thread_id=run.thread_id,
        model=run.model,
        goal=run.goal,
        digest=goal_digest(render_transcript(_since_goal(history, goal))),
        failures=facts.failures,
        resolved=facts.resolved,
        answer=outcome.result,
        tainted=agent.gate.ever_tainted,
        reached_goal=outcome.status == "done",
    )
    return RunEnd(outcome=outcome, lessons=case)


async def _limit_seen(agent: AgentRun, limits: RunLimits, started: float) -> LimitReached | None:
    """The limit the run has reached by now, if any, from the seconds since ``started`` and the tokens of the run and
    of the runs it delegated to as the store holds them (a call's tokens are added before the call returns)."""
    tokens: int = await agent.runtime.store.family_tokens(agent.run.id)
    return limit_reached(limits, time.monotonic() - started, tokens)


async def _limit_end(agent: AgentRun, reached: LimitReached, ledger: RunLedger, reply: str) -> RunOutcome:
    """The end of a run that did not finish in the step after it was told that its limit is reached; ``reply`` is the
    text that step's reply had."""
    plan: str = plan_lines(await _current_plan(agent))
    return RunOutcome(status="failed", result=limit_result(reached, ledger, plan, reply), needs="")


@dataclass(frozen=True)
class _Standby:
    """A run that moved to a standby model: the run as it goes on, its model and what depends on the model, and why
    the model before could not answer."""

    agent: AgentRun
    ref: ModelRef
    info: ModelInfo
    scaffold: Scaffold
    reason: str


async def _fitted(
    agent: AgentRun, target: ModelRef, offers_tools: bool, fixed_tokens: int
) -> tuple[ModelInfo, Scaffold] | str:
    """The information and the scaffold of ``target`` for this run, or why it cannot take the run over: its information
    cannot be read, it cannot call tools while the run offers them, or its window cannot hold the system prompt and
    the tools (``fixed_tokens``)."""
    runtime: Runtime = agent.runtime
    try:
        info: ModelInfo = await runtime.router.model_info(target)
    except (ProviderError, MissingSecretError) as error:
        return f"its model information could not be read: {error.message}"
    if offers_tools and not info.tools:
        return "it cannot call tools"
    scaffold: Scaffold = scaffold_for(
        runtime.config.agent,
        info,
        await runtime.store.model_profile(target.label, PROFILE_RUNS),
        runtime.context_sizes.of(target.label),
    )
    try:
        require_room(scaffold, fixed_tokens)
    except ConfigError as error:
        return error.message
    return info, scaffold


async def _complete_in_time(
    runtime: Runtime, ref: ModelRef, request: ChatRequest, sink: DeltaSink, origin: CallOrigin, timeout_s: float
) -> ModelReply:
    """One step's model call, given ``timeout_s`` to answer. A model that does not answer in time fails like one whose
    connection broke (``ProviderTransportError``), so a routed step goes back to the run's own model, the run waits for
    its own model as for one out of reach (:func:`_connection_broke`), and a run that ends says what happened rather
    than an internal error (item 116)."""
    try:
        async with asyncio.timeout(timeout_s):
            return await runtime.router.complete(ref, request, sink, origin)
    except TimeoutError as exc:
        raise ProviderTransportError(
            "the model did not answer in time", {"model": ref.label, "timeout_s": timeout_s}
        ) from exc


async def _complete_unless_steered(inbox: RunInbox, call: Awaitable[ModelReply]) -> ModelReply | None:
    """Await the model's reply to a step, unless a message to the run (a steer, the answer to a question) arrives
    first: the call is then cancelled and ``None`` returned, and the loop reads the message, which stays in the inbox,
    before it asks again (item 509). An error of the call is raised as it was."""
    reply_task: asyncio.Future[ModelReply] = asyncio.ensure_future(call)
    heard: asyncio.Task[None] = asyncio.ensure_future(inbox.arrival())
    racing: set[asyncio.Future[ModelReply] | asyncio.Future[None]] = {reply_task, heard}
    try:
        await asyncio.wait(racing, return_when=asyncio.FIRST_COMPLETED)
    finally:
        heard.cancel()
        if not reply_task.done():
            reply_task.cancel()
        await asyncio.wait(racing)
    return None if reply_task.cancelled() else reply_task.result()


def _connection_broke(error: Exception) -> bool:
    """Whether ``error`` says that the model could not be reached, rather than that it cannot answer for a while or
    refused the request: the connection broke or timed out, or its server failed with a status that retrying can get
    past (the connector has retried it already). A spent quota or rate and a refused or missing key are not (item
    158)."""
    return is_retryable_provider_error(error) and unavailable_reason(error) is None


def _unreachable_reason(error: DotError) -> str:
    """Why the model could not be reached, in a few words for the run's page: what the broken connection raised (its
    type and text, as :func:`~dotagent.llm.http.wrap_transport_error` keeps them), else the error's own message (the
    status its server failed with and its words, or the loop's own time limit)."""
    kind: JsonValue = error.context.get("error_type")
    text: JsonValue = error.context.get("error")
    if isinstance(kind, str) and isinstance(text, str):
        return kind if text == "" else f"{kind}: {text}"
    return error.message


def _retry_delay(asked_again: int, wait_s: float, left_s: float) -> float:
    """The seconds before a step whose model could not be reached is asked again after ``asked_again`` times:
    :data:`_RETRY_FIRST_S`, doubled each time up to :data:`_RETRY_LONGEST_S`, at most a quarter of the whole wait
    (``wait_s``), so that a short wait still asks again several times, and never past its end (``left_s``)."""
    return min(_RETRY_FIRST_S * 2.0**asked_again, _RETRY_LONGEST_S, wait_s / _RETRY_LEAST, left_s)


async def _wait_unless_stopped(cancel: asyncio.Event, seconds: float) -> None:
    """Wait ``seconds``, or less when the run is stopped meanwhile: the loop then ends it."""
    try:
        await asyncio.wait_for(cancel.wait(), seconds)
    except TimeoutError:
        return


async def _held_by_the_owner(agent: AgentRun, held_since: float) -> None:
    """The owner holds the run with a ✋ (item 586): wait for what lets it go on, a message of theirs or their ✋
    taken back, and leave it to the top of the loop to read it, or to end the run if it was stopped meanwhile. The wait
    is bounded by ``[reactions] hold_minutes`` since the hold began: a run held and forgotten keeps one of the places
    the daemon has for runs, so past that time it stops (:class:`RunHoldLapsed`), and its chat is where the owner goes
    on."""
    minutes: float = agent.runtime.config.reactions.hold_minutes
    left_s: float = minutes * 60.0 - (time.monotonic() - held_since)
    heard: asyncio.Task[None] = asyncio.ensure_future(agent.inbox.arrival())
    stopped: asyncio.Task[bool] = asyncio.ensure_future(agent.cancel.wait())
    racing: set[asyncio.Task[None] | asyncio.Task[bool]] = {heard, stopped}
    try:
        done, _ = await asyncio.wait(racing, timeout=max(left_s, 0.0), return_when=asyncio.FIRST_COMPLETED)
    finally:
        heard.cancel()
        stopped.cancel()
        await asyncio.wait(racing)
    if done:
        return
    held_s: float = time.monotonic() - held_since
    await agent.runtime.bus.emit(agent.run.id, HOLD_ENDED_EVENT, {"reason": "lapsed", "held_s": round(held_s, 1)}, True)
    raise RunHoldLapsed(hold_lapsed_text(minutes), {"run_id": agent.run.id, "held_s": round(held_s, 1)})


def _cannot_answer(error: Exception) -> str | None:
    """Why the run's model cannot answer this run: what pauses it for every run (:func:`unavailable_reason`), or its
    service's content filter, which stops this conversation alone. ``None`` for any other error."""
    if isinstance(error, ProviderFilterError):
        return _FILTERED_REASON
    return unavailable_reason(error)


def filtered_stop(reply: ModelReply, model: str) -> ProviderFilterError | None:
    """The error of a reply that the model service's content filter stopped (:data:`_FILTERED`), else ``None``."""
    if reply.raw_finish not in _FILTERED:
        return None
    return ProviderFilterError(
        f"the model's service stopped its reply with its content filter ('{reply.raw_finish}'), and asked again with "
        "this conversation it stops it again: choose another model, or name a standby model "
        "([models] standby) that the run moves to",
        {"model": model, "input_tokens": reply.usage.input_tokens, "output_tokens": reply.usage.output_tokens},
    )


async def _to_standby(
    agent: AgentRun,
    ref: ModelRef,
    error: Exception,
    left: frozenset[str],
    offers_tools: bool,
    fixed_tokens: int,
    step: int,
) -> _Standby | None:
    """Move the run to the first standby model that can take it over, now that ``ref`` could not answer (``error``,
    which paused it in the router, or its content filter's stop, which paused nothing: :func:`_cannot_answer`). The
    models the run has worked on (``left``) are not taken again, so a run goes through its standby models once at most.
    The run's reasoning level follows the move: the level of the new model that is nearest to it.

    ``None`` when ``error`` says nothing about the model being unable to answer, or when no standby model is left that
    is available (each one the run has not worked on lacks its key or is paused): the run then ends with ``error``. A
    standby model that cannot take the run over (:func:`_fitted`) is passed over; when every available one is, the
    run fails with a :class:`ConfigError` whose message, the run's result, says why for each."""
    runtime: Runtime = agent.runtime
    reason: str | None = _cannot_answer(error)
    if reason is None:
        return None
    standby: Roster = await current_roster(runtime.router, runtime.config.providers, runtime.roles.standby())
    candidates: list[str] = [model for model in standby.available if model not in left]
    if not candidates:
        log.warning("no_standby_model", run_id=agent.run.id, model=ref.label, reason=reason)
        return None
    passed_over: JsonObject = {}
    for target in candidates:
        moved: ModelRef = parse_model_ref(target)
        fitted: tuple[ModelInfo, Scaffold] | str = await _fitted(agent, moved, offers_tools, fixed_tokens)
        if isinstance(fitted, str):
            log.warning("standby_passed_over", run_id=agent.run.id, model=target, problem=fitted)
            passed_over[target] = fitted
            continue
        info, scaffold = fitted
        effort: Effort | None = None if agent.run.effort is None else effective_effort(info.efforts, agent.run.effort)
        await runtime.store.set_run_model(agent.run.id, target, effort)
        await runtime.bus.emit(
            agent.run.id, MODEL_SWITCHED_EVENT, {"from": ref.label, "to": target, "reason": reason, "step": step}, True
        )
        log.warning("model_switched", run_id=agent.run.id, from_model=ref.label, to_model=target, reason=reason)
        run: RunRecord = agent.run.model_copy(update={"model": target, "effort": effort})
        return _Standby(agent=replace(agent, run=run), ref=moved, info=info, scaffold=scaffold, reason=reason)
    reasons: str = "; ".join(f"{model} ({problem})" for model, problem in passed_over.items())
    raise ConfigError(
        f"the run's model cannot answer ({reason}) and no standby model can take the run over: {reasons}",
        {"model": ref.label, "reason": reason, "passed_over": passed_over},
    ) from error


@dataclass(frozen=True)
class _Candidate:
    """A model a step may be routed to, with what the run needs to know about it."""

    option: StepModel
    ref: ModelRef
    info: ModelInfo
    scaffold: Scaffold


def still_routable(candidates: tuple[_Candidate, ...], worked_on: frozenset[str]) -> tuple[_Candidate, ...]:
    """The candidates a step may still be routed to: none of the models ``worked_on``, which the run has worked on."""
    return tuple(candidate for candidate in candidates if candidate.ref.label not in worked_on)


async def _routing(agent: AgentRun, ref: ModelRef, offers_tools: bool, fixed_tokens: int) -> tuple[_Candidate, ...]:
    """The models a step of this run may be routed to (``[agent] step_router``): the fast model and the first
    available helper, each when it is not the run's own model and can take the run over; empty when the router is off,
    TypeSafe is not set up or no other model fits."""
    runtime: Runtime = agent.runtime
    if not runtime.config.agent.step_router or runtime.typesafe is None:
        return ()
    helpers: Roster = await current_roster(runtime.router, runtime.config.providers, runtime.config.models.helpers)
    named: list[tuple[StepModel, str]] = [
        ("fast", runtime.roles.fast()),
        *(("helper", m) for m in helpers.available[:1]),
    ]
    candidates: list[_Candidate] = []
    for option, label in named:
        target: ModelRef = parse_model_ref(label)
        if target.label == ref.label:
            continue
        # A paused fast model is not offered (item 255), as the fast model's work leaves it out (item 240).
        paused: Unavailable | None = paused_model(runtime.router, target.label, time.time())
        if paused is not None:
            log.warning("step_route_candidate_skipped", run_id=agent.run.id, model=target.label, problem=paused.reason)
            continue
        fitted: tuple[ModelInfo, Scaffold] | str = await _fitted(agent, target, offers_tools, fixed_tokens)
        if isinstance(fitted, str):
            log.warning("step_route_candidate_skipped", run_id=agent.run.id, model=target.label, problem=fitted)
            continue
        candidates.append(_Candidate(option=option, ref=target, info=fitted[0], scaffold=fitted[1]))
    return tuple(candidates)


async def _route_step(
    agent: AgentRun,
    candidates: tuple[_Candidate, ...],
    history: Sequence[Message],
    step: int,
    failures: int,
    failures_in_a_row: int,
    estimated: int,
) -> _Candidate | None:
    """The candidate that takes ``step``, by the typed judgment over the step's state and the policy's threshold;
    ``None`` when the run's own model takes it, when TypeSafe is paused or rejects the request, or when the chosen
    candidate cannot hold the step (its window is too small for the history, or it cannot see images the history
    carries)."""
    runtime: Runtime = agent.runtime
    service: TypeSafeService | None = runtime.typesafe
    if service is None:
        return None
    # TypeSafe is a service outside the Mac: what it is sent has the secrets Dot holds masked (item 551).
    known: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    state: JsonObject = route_state(step_state(agent.run.goal, step, history, failures, failures_in_a_row, known))
    try:
        answers: dict[str, ChoiceAnswer] | None = await service.choices(
            state, (route_question([*(candidate.option for candidate in candidates), "main"]),), agent.run.id
        )
    except ProviderError:
        return None
    if answers is None:
        return None
    choice: StepChoice = choose(answers, runtime.config.agent.step_router_threshold)
    chosen: _Candidate | None = next((c for c in candidates if c.option == choice.model), None)
    if chosen is None:
        return None
    if estimated > chosen.scaffold.context_tokens or (
        not chosen.info.vision and any(message.images for message in history)
    ):
        log.info("step_route_unfit", run_id=agent.run.id, step=step, model=chosen.ref.label)
        return None
    await runtime.bus.emit(agent.run.id, STEP_ROUTED_EVENT, routed_event(step, chosen.ref.label, choice), True)
    return chosen


async def run_agent(agent: AgentRun) -> RunEnd:
    started: float = time.monotonic()
    runtime: Runtime = agent.runtime
    run: RunRecord = agent.run
    config: AgentConfig = runtime.config.agent
    opening: RunOpening = agent.opening
    # Before anything that can fail, so that a resumed run that ends at once still tells which run it goes on from.
    if opening.resumed is not None:
        await runtime.bus.emit(run.id, RESUMED_EVENT, resumed_event_data(opening.resumed), True)
        # The plan of the run it resumes becomes this run's plan: the plan tools and the plan reminder go on with it.
        if opening.resumed.plan.steps:
            await runtime.bus.emit(run.id, PLAN_EVENT, plan_event_data(opening.resumed.plan), True)
    ref: ModelRef = parse_model_ref(run.model)
    info: ModelInfo = await runtime.router.model_info(ref)
    if not info.tools and run.mode != "chat":
        raise ConfigError("the selected model cannot call tools", {"model": ref.label, "mode": run.mode})
    scaffold: Scaffold = scaffold_for(
        config, info, await runtime.store.model_profile(ref.label, PROFILE_RUNS), runtime.context_sizes.of(ref.label)
    )
    surface, surface_reason = surface_for(config, scaffold)
    specs: tuple[ToolSpec, ...] = specs_for_mode(runtime.registry, run.mode) if info.tools else ()
    if run.parent_run_id is not None:
        specs = tuple(spec for spec in specs if spec.name not in _NOT_FOR_DELEGATED_RUNS)
    if run.campaign_id is None:
        specs = tuple(spec for spec in specs if spec.name not in CAMPAIGN_TOOLS | POST_TOOLS)
    # The agent the run belongs to (item 86) bounds its tools: the ones it names, the core tools in any case, and the
    # MCP servers the owner opened to it (item 415): the ones picked for its message or an earlier one of its chat.
    agent_record: AgentRecord | None = None if run.agent_id is None else await runtime.store.get_agent(run.agent_id)
    # The team (item 87): the other agents, whom the run may message.
    team: tuple[AgentRecord, ...] = tuple(
        member for member in await runtime.store.list_agents() if member.id != run.agent_id
    )
    if agent_record is not None and agent_record.tools:
        agent_tools: frozenset[str] = frozenset(agent_record.tools) | frozenset(AGENT_CORE_TOOLS)
        picked_before: frozenset[str] = await runtime.store.picked_servers(run.thread_id, PICKS_EVENT)
        owner_servers: frozenset[str] = owner_server_groups(opening.picks, picked_before)
        specs = tuple(spec for spec in specs if spec.name in agent_tools or group_of(spec.name) in owner_servers)
    # Settings › Browser may keep every run off the browser (item 354).
    if not runtime.browser.config.enabled:
        specs = tuple(spec for spec in specs if group_of(spec.name) != BROWSER_GROUP)
    # The mail and calendar tools (item 424) are a run's only when the owner signed an account in.
    signed_in: list[Account] = await runtime.accounts.accounts()
    if not signed_in:
        specs = tuple(spec for spec in specs if group_of(spec.name) != ACCOUNTS_GROUP)
    # The logins of 1Password (item 493) are a run's only while the owner has connected it.
    if await asyncio.to_thread(kept_token) is None:
        specs = tuple(spec for spec in specs if spec.name not in VAULT_TOOLS)
    # Without a teammate the message tool goes, unless the run may make one (item 99): then it stays, so a teammate
    # made mid-run can be messaged at once.
    if not team and all(spec.name != CREATE_AGENT_TOOL for spec in specs):
        specs = tuple(spec for spec in specs if spec.name != MESSAGE_AGENT_TOOL)
    # The agents the run knows of (item 99): the ones at its start; a newcomer is noted at the next step.
    known_agents: set[str] = {member.id for member in team} | ({run.agent_id} if run.agent_id is not None else set())
    may_message: bool = any(spec.name == MESSAGE_AGENT_TOOL for spec in specs)
    skills: SkillCatalog = await asyncio.to_thread(
        load_skills, runtime.config.skills, runtime.plugins.plugins, playbook_root(runtime.config)
    )
    for problem in skills.problems:
        log.warning("skill_skipped", run_id=run.id, path=str(problem.path), reason=problem.reason)
    skills = skills.model_copy(update={"skills": await offered_to_run(runtime.config, skills, run.agent_id)})
    if not skills.skills:
        specs = tuple(spec for spec in specs if spec.name != SKILL_TOOL)
    if config.consult_max_per_run == 0:
        specs = tuple(spec for spec in specs if spec.name != CONSULT_TOOL)
    # The helpers that can answer as the run starts, besides its own model; only for a run with a tool that uses them.
    helpers: tuple[str, ...] = (
        tuple(
            model
            for model in (
                await current_roster(runtime.router, runtime.config.providers, runtime.config.models.helpers)
            ).available
            if model != ref.label
        )
        if any(spec.name in (CONSULT_TOOL, DELEGATE_TOOL) for spec in specs)
        else ()
    )
    if not helpers:
        specs = tuple(spec for spec in specs if spec.name != CONSULT_TOOL)
    surface, surface_reason = surface_for_tools(surface, surface_reason, len(specs))
    # Every tool group of the run, core first. A run with no group besides core has nothing to turn on.
    groups: tuple[str, ...] = (CORE_GROUP, *groups_of([spec.name for spec in specs]))
    grouped: bool = surface == "grouped" and len(groups) > 1
    if not grouped:
        specs = tuple(spec for spec in specs if spec.name != USE_TOOLS_TOOL)
    callable_tools: ToolRegistry = _run_tools(runtime.registry, run.mode, specs)
    memory_seen_at: float = time.time()
    # The tags of the sites the run reached so far (item 308): the first time it reaches one, it is handed what was
    # learned there.
    sites_seen: set[str] = set()
    # The last note about the other runs at work (item 301); empty while there were none.
    peers_said: str = ""
    notes_budget: int = memory_budget(runtime.config.memory, info.context_length)
    query: str = await _goal_query(runtime, run)
    recalled: Remembered = await _start_notes(runtime, run, query, notes_budget)
    # The adopted version of the harness (item 389): its texts go into the prompt, and the run records which it took.
    harness_text, harness_version = await adopted_text(runtime.store)
    if harness_version is not None:
        await runtime.store.set_run_harness(run.id, harness_version)
    remembered: tuple[MemoryRecord, ...] = recalled.records
    if remembered:
        handed: list[str] = [record.id for record in remembered]
        # How many of them no earlier run of the chat was handed: the chat's page says nothing of notes it recalled
        # before (item 335).
        before: set[str] = await runtime.store.handed_in_thread(run.thread_id, run.id)
        await runtime.store.mark_memories_used(run.id, handed, memory_seen_at, "start")
        await runtime.bus.emit(
            run.id,
            "memories",
            {"ids": [*handed], "tokens": recalled.tokens, "fresh": sum(1 for one in handed if one not in before)},
            True,
        )
    can_ask: bool = can_ask_user(run.source, run.parent_run_id, run.approvals)
    asks: AskMode = question_mode(config.questions, can_ask, agent.spawner.answers_reach_runs)
    # A message sent with Plan first acts only once the owner agrees (item 318): where the run chooses, its questions
    # wait for the answer whatever it says (item 508).
    questions: AskMode = "wait" if asks == "choose" and agent.opening.picks.plan_first else asks
    # The instructions list the skills that fit the chat's first words, its title, the same at every message of the
    # chat, and the notes the run recalled stay out of them: what fits this message goes under it (turn_note), so that
    # the provider's cache of the instructions and the history holds from message to message (item 404). More skills
    # than the instructions list are picked by meaning too (item 169), or by their words when the embeddings fail
    # (item 256).
    thread: ThreadRecord = await runtime.store.get_thread(run.thread_id)
    close_to_title, close = (
        await _close_skills(runtime, run, [thread.title, query], [skill_text(skill) for skill in skills.skills])
        if len(skills.skills) > runtime.config.skills.listed
        else ([], [])
    )
    listing: SkillListing = listed_skills(skills.skills, thread.title, close_to_title, runtime.config.skills.listed)
    fitting: SkillListing = listed_skills(skills.skills, query, close, runtime.config.skills.listed)
    browser: BrowserSession = await browser_of(runtime, run)
    names: AssistantView = runtime.assistant.view()
    system: Message = system_message(
        build_system_prompt(
            collect_prompt_inputs(
                run.mode,
                run.source,
                runtime.config.policy.preset,
                run.approvals,
                questions,
                run.goal_mode,
                splits_work(run.once),
                runtime.workspace,
                (),
                0,
                tools_in_order(specs, thread.title),
                listing.skills,
                listing.left,
                None if run.campaign_id is None else await _session_number(agent, run.campaign_id),
                helpers,
                None
                if agent_record is None
                else AgentBrief(
                    name=agent_record.name,
                    role=agent_record.role,
                    instructions=agent_record.instructions,
                    made_by_dot=agent_record.made_by is not None,
                    team=_team_name(agent_record.name),
                ),
                [
                    AgentBrief(
                        name=member.name,
                        role=member.role,
                        instructions="",
                        made_by_dot=member.made_by is not None,
                        team=_team_name(member.name),
                    )
                    for member in team
                ],
                (
                    *owner_name_lines(names.owner, names.mac_name),
                    *(called_lines(names.name) if agent_record is None else ()),
                    # The accounts are named only to a run that has one of the tools that take them (an agent's may
                    # have none, or only some).
                    *(account_lines(signed_in) if tools_in([spec.name for spec in specs], ACCOUNTS_GROUP) else ()),
                    *owner_prompt_lines(await owner_section(runtime.store)),
                ),
                devtools_address(browser.config),
                config.prompt,
                harness_text,
            )
        )
    )
    await runtime.bus.emit(run.id, "scaffold", scaffold_event(scaffold, config), True)
    # Every tool of the run counts, also the ones of groups that are off: a group the run turns on later must fit too.
    require_room(scaffold, estimate_tokens([system], specs))
    candidates: tuple[_Candidate, ...] = await _routing(agent, ref, bool(specs), estimate_tokens([system], specs))
    history: list[Message] = repair_history(
        await runtime.store.load_messages(run.thread_id, config.keep_recent_screenshots)
    )
    # The tool groups that are on; they only grow. A grouped run starts with core, the groups its thread used and the
    # servers the owner picked for its message (item 318).
    on: frozenset[str] = (
        frozenset({CORE_GROUP, *used_groups(history), *picked_groups(opening.picks)}) & frozenset(groups)
        if grouped
        else frozenset(groups)
    )
    if grouped:
        await runtime.bus.emit(run.id, "tool_groups", groups_event(0, groups, on, (), surface_reason), True)
    goal: Message = await message_with_attachments(
        runtime,
        run.thread_id,
        run.id,
        await _opening_text(opening, skills.skills, scaffold.tool_output_chars),
        opening.files,
        info.vision,
    )
    if opening.files:
        await runtime.bus.emit(run.id, ATTACHMENTS_EVENT, {"files": attachment_summary(opening.files)}, True)
    if opening.picks != NO_PICKS:
        await runtime.bus.emit(run.id, PICKS_EVENT, picks_event_data(opening.picks), True)
    history.append(goal)
    await runtime.store.append_messages(run.thread_id, [goal])
    # Under the message (item 404): the notes recalled for it that no earlier message of the chat carries with the
    # same text (a note changed since, on the Memory page or by a run, comes again, and one folded into a summary is
    # handed again), the notes forgotten since a message was handed them (their old text stays in the chat), and the
    # fitting skills the instructions leave out.
    carried: str = "\n".join(message.content for message in history if message.role == "user")
    handed_before: set[str] = handed_note_ids(carried) - {record.id for record in remembered}
    # A note the chat quotes that was forgotten, or that a merge put aside (item 584), is named as forgotten; the note
    # a merge made of it, which the chat does not carry, is handed in its place.
    away: Gone = await notes_gone(runtime.memory, handed_before, run.agent_id, time.time())
    forgotten: list[str] = forgotten_unnamed(list(away.gone), carried)
    stood_in: list[MemoryRecord] = [
        record for record in away.stand_in if record.id not in {note.id for note in remembered}
    ]
    fresh_notes: list[str] = notes_missing([render_note(record) for record in (*remembered, *stood_in)], carried)
    if stood_in_fresh := [record.id for record in stood_in if render_note(record) in fresh_notes]:
        await runtime.store.mark_memories_used(run.id, stood_in_fresh, memory_seen_at, "start")
    listed_names: set[str] = {skill.name for skill in listing.skills}
    extra_skills: list[Skill] = [skill for skill in fitting.skills if skill.name not in listed_names][
        :_TURN_SKILLS_MOST
    ]
    recalled_ids: list[str] = [record.id for record in (*remembered, *stood_in)]

    async def recalled_now(present: str) -> str:
        """The notes recalled for this message that the context ``present`` (its user messages) no longer carries, as
        they stand now, and the ones forgotten since and not yet named: for the note handed again after a compaction,
        whichever messages it folded away."""
        kept: list[MemoryRecord] = await runtime.store.get_memories(recalled_ids)
        gone: list[str] = sorted(set(recalled_ids) - {record.id for record in kept})
        return recalled_again(
            [render_note(record) for record in kept], recalled.tainted_left_out, extra_skills, gone, present
        )

    history.append(
        await _stored_note(
            agent,
            turn_note(datetime.now().astimezone(), fresh_notes, recalled.tainted_left_out, extra_skills, forgotten),
        )
    )
    facts: RunFacts = new_facts()
    context: ToolContext = ToolContext(
        runtime=runtime,
        browser=browser,
        run_id=run.id,
        agent_id=run.agent_id,
        thread_id=run.thread_id,
        mode=run.mode,
        approvals=run.approvals,
        questions=questions,
        workspace=runtime.workspace,
        gate=agent.gate,
        cancel=agent.cancel,
        output_chars=scaffold.tool_output_chars,
        # The facts as they stand when a tool asks: each step replaces them.
        facts=lambda: facts,
        spawner=agent.spawner,
        # The taint as it stands when a tool asks: a step result of this run may have set it.
        tainted=lambda: agent.gate.tainted,
        run_tools=frozenset(spec.name for spec in specs),
    )
    checker: GoalChecker | None = (
        goal_checker_for(
            runtime.router, runtime.roles.review(), config.model_call_timeout_s, f"{run.thread_id}:goal", run.id
        )
        if run.goal_mode
        else None
    )
    steps: int = 0
    step_origin: CallOrigin = CallOrigin(run_id=run.id, purpose="step")
    ratio: float = 1.0
    nudges: int = 0
    sent_back: int = 0
    used_tools: bool = False
    plan_reminded: bool = False
    failure_reminded: bool = False
    targets_reminded: bool = False
    paths_reminded: bool = False
    # The step of the newest reminder of the goal (weak tier); 0 before the first.
    reminded_at: int = 0
    # Acting steps in a row whose acts carried one step each on one page or application, and what the last one was.
    single_streak: int = 0
    single_last: SingleAction | None = None
    ledger: RunLedger = new_ledger(run.goal)
    limits: Limits = limits_for(config)
    ladder: tuple[Rung, ...] = ladder_for(config.stall_escalation, can_ask)
    consultant: ProgressConsultant = progress_consultant_for(
        runtime.router,
        runtime.roles.review(),
        config.model_call_timeout_s,
        f"{run.thread_id}:progress",
        run.id,
    )
    watch: WatchState = new_watch()
    # Since when (monotonic seconds) the owner holds the run with a ✋ (item 586), None while they do not.
    held_since: float | None = None
    # The tools of the next step when a progress alert or the run's limit restricts them; None offers the tools of the
    # groups that are on. A step asked again on a standby model keeps them.
    allowed: frozenset[str] | None = None
    # The models the run has worked on: a move to a standby model never goes back to one of them.
    left: frozenset[str] = frozenset({ref.label})
    run_limits: RunLimits | None = limits_of(config, run.mode)
    # What the run had spent when it was seen past its limit; from then on every step may only finish, and ends the run.
    past_limit: LimitReached | None = None
    # The tool calls that failed so far, the steps in a row that ended with a failure, and whether the next step is
    # asked on the run's own model whatever the judge says (after a routed model could not answer it).
    failures: int = 0
    failures_in_a_row: int = 0
    route_skip: bool = False
    # Since when (monotonic seconds) the run's model could not be reached, and how often the step was asked again since.
    unreachable_since: float | None = None
    asked_again: int = 0

    async def show(delta: StreamDelta) -> None:
        await runtime.bus.emit(run.id, "delta", {"kind": delta.kind, "text": delta.text, "step": steps}, False)

    # A step's stream is masked as its stored message is: a secret split between two deltas is not shown (item 551).
    sink: StreamMask = StreamMask(show)

    while True:
        if agent.cancel.is_set():
            raise RunCancelled("the run was stopped", {"run_id": run.id})
        while not agent.inbox.empty():
            sent: UserInput = agent.inbox.first()
            text: str = sent.text
            note: Message = await message_with_attachments(
                runtime, run.thread_id, run.id, text, sent.files, info.vision
            )
            history.append(note)
            agent.gate.heard_from_owner()
            if sent.vouches:
                agent.gate.clear_taint()
            if sent.said != "":
                agent.gate.add_instruction(sent.said)
                ledger = with_instruction(ledger, sent.said)
            await runtime.store.append_messages(run.thread_id, [note])
            # Taken once it is in the thread, with nothing awaited between: a stop before leaves it to the run's end.
            agent.inbox.get_nowait()
            await runtime.bus.emit(run.id, "steer", steer_event(sent, agent.gate.tainted), True)
            # A ✋ of the owner holds the run (item 586); what they write next, or their ✋ taken back, lets it go on. A
            # note about any other reaction changes nothing of that.
            if sent.reaction == "hold" and held_since is None:
                held_since = time.monotonic()
                agent.inbox.holding = True
                await runtime.bus.emit(run.id, HOLD_EVENT, {"step": steps}, True)
            elif sent.reaction in (None, "release") and held_since is not None:
                held_s: float = time.monotonic() - held_since
                held_since = None
                agent.inbox.holding = False
                reason: str = "message" if sent.reaction is None else "released"
                await runtime.bus.emit(run.id, HOLD_ENDED_EVENT, {"reason": reason, "held_s": round(held_s, 1)}, True)
        if held_since is not None:
            await _held_by_the_owner(agent, held_since)
            continue
        changed: list[MemoryRecord] = await runtime.store.memories_changed_since(memory_seen_at, run.id)
        if changed:
            memory_seen_at = max(record.updated_at for record in changed)
            live: LiveNotes = await live_memories(runtime.memory, changed, notes_budget, run.agent_id)
            if live.records:
                history.append(await _stored_note(agent, _new_memories_note(live)))
                await runtime.store.mark_memories_used(
                    run.id, [record.id for record in live.records], time.time(), "live"
                )
                await runtime.bus.emit(
                    run.id, "memories", {"ids": [record.id for record in live.records], "live": True}, True
                )
        if may_message:
            newcomers: list[AgentRecord] = [
                member for member in await runtime.store.list_agents() if member.id not in known_agents
            ]
            if newcomers:
                known_agents.update(member.id for member in newcomers)
                history.append(await _stored_note(agent, _new_teammates_note(newcomers)))
        # The commands in the background of this chat that ended since the last step, told once (item 437). A system
        # note, not a message of the owner: it clears no taint and is no instruction.
        commands_ended: list[TerminalSession] = runtime.terminals.take_ended(run.thread_id)
        if commands_ended:
            history.append(await _stored_note(agent, f"[system note] {ended_notices(commands_ended)}"))
        # The other runs at work and what they hold, said again whenever that changes (item 301).
        others: str = peers_note(runtime.peers.others(run.id), runtime.workspace)
        if others and others != peers_said:
            history.append(await _stored_note(agent, others))
        peers_said = others
        if run_limits is not None and past_limit is None:
            past_limit = await _limit_seen(agent, run_limits, started)
            if past_limit is not None:
                history.append(await _stored_note(agent, limit_note(past_limit)))
                await runtime.bus.emit(run.id, LIMIT_EVENT, limit_event_data(past_limit), True)
        if past_limit is not None:
            # The limit wins over what a progress alert of the step before allowed.
            allowed = LIMIT_TOOLS
        # A step that a progress alert or the limit restricts offers what they allow, and turns no group on.
        offered: tuple[ToolSpec, ...] = (
            offered_specs(specs, on) if allowed is None else tuple(spec for spec in specs if spec.name in allowed)
        )
        registry: ToolRegistry = callable_tools if allowed is None else _only(callable_tools, allowed)
        hidden: dict[str, str] = hidden_tools(specs, on) if allowed is None else {}
        # The open plan at the end of the system prompt of every step (item 87), rebuilt from the newest plan event so
        # that nothing accumulates in the history; the prompt's own text stays the same for the provider's cache.
        step_system: Message = (
            await _with_plan(agent, system) if config.plan_each_turn and run.mode != "chat" else system
        )
        compactions_before: int = ledger.compactions
        history, estimated, ledger = await _fit_context(agent, history, step_system, offered, ratio, ledger, scaffold)
        # A compaction folded messages away, and with them the notes recalled for this message that they carried, this
        # message's own or an earlier one's: those the context no longer carries are handed again after the summary
        # (item 404), as they stand now, stored, so that the next message's prefix holds them too.
        if ledger.compactions > compactions_before:
            present: str = "\n".join(message.content for message in history if message.role == "user")
            recalled_text: str = await recalled_now(present)
            if recalled_text != "":
                history.append(await _stored_note(agent, recalled_text))
        routed: _Candidate | None = (
            None
            if route_skip or not candidates
            else await _route_step(agent, candidates, history, steps + 1, failures, failures_in_a_row, estimated)
        )
        route_skip = False
        step_ref: ModelRef = ref if routed is None else routed.ref
        effort: Effort = run.effort if run.effort is not None else runtime.config.models.effort
        # The routed model's level nearest to the run's; the run's own where the model offers no choice.
        routed_effort: Effort | None = None if routed is None else effective_effort(routed.info.efforts, effort)
        request: ChatRequest = ChatRequest(
            model=step_ref.model,
            messages=(step_system, *history),
            tools=offered,
            temperature=None,
            max_output_tokens=None,
            effort=effort if routed_effort is None else routed_effort,
            # The run's speed where the step's model has fast mode: a routed or standby model may not (item 223).
            speed=run.speed if offers_fast_mode(step_ref) else "standard",
            session_key=run.thread_id,
            # The end of what the next step sends unchanged, after the system message (item 237).
            cache_mark=_cache_mark(history, config),
            # The window the run planned with as it started (item 520): a context size chosen since does not change it.
            window=scaffold.window if routed is None else routed.scaffold.window,
        )
        steps += 1
        asked_at: float = time.monotonic()
        # The secrets Dot holds, masked in what the step streams and in the message it stores (item 551).
        held_secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
        sink.begin(held_secrets)
        try:
            call: Awaitable[ModelReply] = _complete_in_time(
                runtime, step_ref, request, sink, step_origin, config.model_call_timeout_s
            )
            # A step past the run's limit is its last: a message does not hold back the finish it ends with.
            heard: ModelReply | None = (
                await call if past_limit is not None else await _complete_unless_steered(agent.inbox, call)
            )
            if heard is None:
                # A message arrived while the model was at the step: the step is given up and asked again with it.
                log.info("step_steered", run_id=run.id, step=steps, model=step_ref.label)
                await sink(_RESET)
                steps -= 1
                continue
            reply: ModelReply = heard
            await sink.flush()
            # A reply the service's content filter stopped is handled as a model that cannot answer this run: a
            # routed step goes back to the run's own model, the run's own model hands over to a standby one.
            filtered: ProviderFilterError | None = filtered_stop(reply, step_ref.label)
            if filtered is not None:
                log.warning("reply_filtered", run_id=run.id, step=steps, model=step_ref.label, finish=reply.raw_finish)
                raise filtered
        except (ProviderError, MissingSecretError) as error:
            if routed is not None:
                # The routed model could not answer: the step is asked again on the run's own model.
                log.warning("step_route_failed", run_id=run.id, step=steps, model=routed.ref.label, error=str(error))
                await runtime.bus.emit(
                    run.id,
                    STEP_ROUTE_FAILED_EVENT,
                    {"step": steps, "model": routed.ref.label, "error": str(error)[:300]},
                    True,
                )
                route_skip = True
                if isinstance(error, ProviderFilterError):
                    # Its filter stops the same conversation again: no later step is routed to it, and the run does
                    # not move to it as a standby model either.
                    left = left | {routed.ref.label}
                    candidates = still_routable(candidates, left)
                await sink(_RESET)
                steps -= 1
                continue
            standby: _Standby | None = await _to_standby(
                agent, ref, error, left, bool(specs), estimate_tokens([system], specs), steps
            )
            if standby is None:
                if config.model_wait_s <= 0 or not _connection_broke(error):
                    raise
                now: float = time.monotonic()
                if unreachable_since is None:
                    unreachable_since, asked_again = now, 0
                left_s: float = config.model_wait_s - (now - unreachable_since)
                if left_s <= 0:
                    raise ProviderTransportError(
                        f"the model could not be reached for {config.model_wait_s:g} s: {_unreachable_reason(error)}",
                        {**error.context, "model": ref.label, "waited_s": round(now - unreachable_since, 1)},
                    ) from error
                retry_in: float = _retry_delay(asked_again, config.model_wait_s, left_s)
                asked_again += 1
                log.warning(
                    "model_unreachable",
                    run_id=run.id,
                    model=ref.label,
                    step=steps,
                    retry_in_s=round(retry_in, 1),
                    error=str(error),
                )
                await runtime.bus.emit(
                    run.id,
                    MODEL_WAITING_EVENT,
                    {
                        "model": ref.label,
                        "error": _unreachable_reason(error)[:300],
                        "step": steps,
                        "retry_in_s": round(retry_in, 1),
                        "until": time.time() + left_s,
                    },
                    True,
                )
                await sink(_RESET)
                await _wait_unless_stopped(agent.cancel, retry_in)
                # The same step is asked again once the wait is over; a stop meanwhile ends the run at the loop's top.
                steps -= 1
                continue
            unreachable_since = None
            history.append(
                await _stored_note(
                    standby.agent, _SWITCH_NOTE.format(to=standby.ref.label, old=ref.label, reason=standby.reason)
                )
            )
            agent, run, ref, info, scaffold = (
                standby.agent,
                standby.agent.run,
                standby.ref,
                standby.info,
                standby.scaffold,
            )
            left = left | {ref.label}
            # A step is routed to none of the models the run has worked on: one that stopped answering, or the one it
            # is on now (the outside audit of 2026-10-17, R3).
            candidates = still_routable(candidates, left)
            context = replace(context, output_chars=scaffold.tool_output_chars)
            if not info.vision:
                history = drop_old_images(history, 0)
            # The step is asked again on the new model, whose tokens the estimate has not been fitted to.
            await sink(_RESET)
            steps -= 1
            ratio = 1.0
            continue
        # The run's own model is back once it answers; a step routed to another model meanwhile says nothing of it.
        if unreachable_since is not None and routed is None:
            waited_s: float = round(time.monotonic() - unreachable_since, 1)
            log.info("model_reached_again", run_id=run.id, model=step_ref.label, step=steps, waited_s=waited_s)
            await runtime.bus.emit(
                run.id, MODEL_BACK_EVENT, {"model": step_ref.label, "waited_s": waited_s, "step": steps}, True
            )
            unreachable_since = None
        allowed = None
        step_seconds: float = time.monotonic() - asked_at
        if reply.usage.input_tokens > 0 and estimated > 0:
            ratio = min(3.0, max(0.4, reply.usage.input_tokens / (estimated / ratio)))
        # The tokens join the run's totals as the router records the call; the side calls' tokens join them too.
        await runtime.store.set_run_steps(run.id, steps)
        assistant: Message = assistant_from_reply(reply, step_ref.label)
        history.append(assistant)
        # Stored before the tools run: a stop or crash while they run then leaves the calls in the thread, and the next
        # run reads them as interrupted (``repair_history``) instead of as never made.
        await runtime.store.append_messages(run.thread_id, [assistant])
        await runtime.bus.emit(
            run.id,
            "assistant",
            {
                "step": steps,
                "text": redact_known(reply.text, held_secrets),
                "thinking": truncate_middle(redact_known(reply.thinking, held_secrets), _THINKING_CHARS),
                "thinking_chars": len(reply.thinking),
                "tool_calls": [call.name for call in reply.tool_calls],
                "input_tokens": reply.usage.input_tokens,
                "output_tokens": reply.usage.output_tokens,
                "cached_tokens": reply.usage.cached_tokens,
                "seconds": round(step_seconds, 3),
                "finish": reply.finish,
            },
            True,
        )
        ended: RunOutcome | None = None
        # The calls of the reply that ended the run that did not work, whose results its answer did not wait for.
        unread: tuple[ToolOutcome, ...] = ()
        if reply.tool_calls:
            used_tools = True
            # Only text replies in a row end a task run; a step that acts starts the count again.
            if _acts(reply.tool_calls, questions):
                nudges = 0
            calls_ended: list[ToolOutcome] = []
            try:
                outcomes: list[ToolOutcome] = await execute_calls(
                    reply.tool_calls, registry, hidden, context, agent.gate, config.parallel_tools, calls_ended.append
                )
            except BaseException:
                # A stop of the run or of the daemon, the desktop's fail-safe, or an error outside a tool (the store
                # could not keep a screenshot) cut the step: the results of the calls that had ended stay in the thread,
                # so that what goes on there reads what they did instead of making them again (items 159, 552). The
                # error that cut the step is the one that goes on, whatever keeping them comes to.
                try:
                    await _keep_ended(agent, reply.tool_calls, calls_ended, info.vision)
                except Exception as keeping:  # noqa: BLE001 - logged here; the error that cut the step is raised again
                    log.error("ended_calls_not_kept", run_id=run.id, step=steps, error=str(keeping))
                raise
            turned_on: tuple[str, ...] = _turned_on(outcomes, hidden, on)
            if turned_on:
                on = on | frozenset(turned_on)
                await runtime.bus.emit(run.id, "tool_groups", groups_event(steps, groups, on, turned_on, ""), True)
            results: list[Message] = await _tool_messages(agent, outcomes, info.vision)
            ledger = with_actions(
                ledger, [(outcome.call, not outcome.result.is_error, outcome.result.text) for outcome in outcomes]
            )
            facts = with_outcomes(facts, outcomes)
            failed_now: int = sum(1 for outcome in outcomes if outcome.result.is_error)
            failures += failed_now
            failures_in_a_row = failures_in_a_row + 1 if failed_now else 0
            single_now: SingleAction | None = single_action(_act_calls(outcomes))
            single_streak = single_action_streak(single_streak, single_last, single_now)
            single_last = single_now
            history.extend(results)
            await runtime.store.append_messages(run.thread_id, results)
            delegated_note: str | None = await _close_delegated_steps(agent, outcomes)
            if delegated_note is not None:
                history.append(await _stored_note(agent, delegated_note))
            finished: ToolOutcome | None = next(
                (outcome for outcome in outcomes if outcome.call.name == FINISH_TOOL and not outcome.result.is_error),
                None,
            )
            arrived: tuple[str, ...] = new_sites(
                (
                    site
                    for outcome in outcomes
                    if not outcome.result.is_error
                    for site in reached_sites(outcome.call, outcome.action)
                ),
                sites_seen,
            )
            sites_seen.update(site_tag(site) for site in arrived)
            if arrived and finished is None:
                arrival: str | None = await _arrival_note(
                    agent, arrived, skills.skills, frozenset(skill.name for skill in listing.skills)
                )
                if arrival is not None:
                    history.append(await _stored_note(agent, arrival))
            if finished is not None:
                ended = _finish_result(finished)
                unread = _failed_before_finish(outcomes, finished)
            elif past_limit is not None:
                return _run_end(agent, history, goal, await _limit_end(agent, past_limit, ledger, reply.text), facts)
            else:
                alert: Alert | None
                watch, alert = advance(watch, _calls_seen(outcomes), limits, ladder)
                if alert is not None:
                    answer: StallAnswer | RunOutcome = await _answer_alert(
                        agent, consultant, alert, can_ask, history, goal, ledger
                    )
                    if isinstance(answer, RunOutcome):
                        return _run_end(agent, history, goal, answer, facts)
                    history.append(await _stored_note(agent, answer.note))
                    allowed = answer.allowed
                elif reminder_due(scaffold, run.mode, steps, reminded_at):
                    reminded_at = steps
                    plan: PlanPayload = await _current_plan(agent)
                    history.append(await _stored_note(agent, reminder_note(ledger, plan)))
                    await runtime.bus.emit(
                        run.id, "reminder", {"step": steps, "open_steps": list(open_steps(plan.steps))}, True
                    )
                elif config.steer_single_actions and single_now is not None and single_streak >= SINGLE_ACTION_STEPS:
                    # Said once per streak: the count starts again, so a run that keeps clicking one at a time is told
                    # again only after as many steps more.
                    single_streak = 0
                    history.append(await _stored_note(agent, single_action_steer(single_now)))
                    await runtime.bus.emit(
                        run.id,
                        SINGLE_ACTION_EVENT,
                        {
                            "step": steps,
                            "tool": single_now.tool,
                            "target": redact_known(single_now.target, held_secrets),
                        },
                        True,
                    )
                continue
        else:
            answered: bool = bool(reply.text.strip())
            if past_limit is not None:
                return _run_end(agent, history, goal, await _limit_end(agent, past_limit, ledger, reply.text), facts)
            elif reply.finish in _CUT_SHORT and nudges >= config.finish_nudges:
                return _run_end(agent, history, goal, _cut_short_end(reply), facts)
            elif reply.finish in _CUT_SHORT:
                nudges += 1
                history.append(await _stored_note(agent, _CUT_SHORT_NOTE.format(reason=_ended_with(reply))))
                continue
            elif answered and (run.mode == "chat" or not used_tools or nudges >= config.finish_nudges):
                ended = RunOutcome(status="done", result=reply.text, needs="")
            elif nudges >= config.finish_nudges:
                return _run_end(
                    agent, history, goal, RunOutcome(status="failed", result=RESULT_EMPTY_REPLIES, needs=""), facts
                )
            else:
                nudges += 1
                history.append(await _stored_note(agent, _NO_TOOL_NOTE if answered else _EMPTY_NOTE))
                continue
        # The run wants to end here. Past its limit it ends with this finish: nothing sends it back, goal mode's verdict
        # on it is final, and what the user sent during the step comes too late for it (the manager keeps an answer in
        # the thread for the next run).
        if past_limit is not None:
            at_limit: RunOutcome = (
                ended
                if checker is None
                else await _check_goal_at_limit(agent, checker, history, goal, ended, sent_back, facts)
            )
            return _run_end(agent, history, goal, at_limit, facts)
        # Otherwise a finish that came in the reply of a call that did not work is sent back once, to read what
        # happened (item 552); so is a plan with open steps, so the plan the user sees matches the work, a session of a
        # campaign that has no targets yet, and, with verify_answer_paths, an answer that names paths that do not
        # exist; then goal mode lets the checker decide whether the goal is really reached.
        if unread and not failure_reminded:
            failure_reminded = True
            nudges = 0
            history.append(await _stored_note(agent, _failed_finish_note(unread)))
            await runtime.bus.emit(
                run.id, FINISH_AFTER_FAILURE_EVENT, {"calls": [outcome.call.name for outcome in unread]}, True
            )
            continue
        if not plan_reminded and run.mode != "chat":
            unclosed: tuple[int, ...] = open_steps((await _current_plan(agent)).steps)
            if unclosed:
                plan_reminded = True
                nudges = 0
                history.append(await _stored_note(agent, _plan_note(unclosed)))
                await runtime.bus.emit(run.id, "plan_reminder", {"open_steps": list(unclosed)}, True)
                continue
        if (
            run.campaign_id is not None
            and not targets_reminded
            and not (await runtime.store.get_campaign(run.campaign_id)).targets
        ):
            targets_reminded = True
            nudges = 0
            history.append(await _stored_note(agent, _CAMPAIGN_TARGETS_NOTE))
            await runtime.bus.emit(run.id, CAMPAIGN_TARGETS_EVENT, {}, True)
            continue
        if config.verify_answer_paths and not paths_reminded and run.mode != "chat" and ended.status == "done":
            missing: tuple[str, ...] = await _missing_in_answer(agent, ended.result, facts)
            if missing:
                paths_reminded = True
                nudges = 0
                watch = clear_window(watch)
                history.append(await _stored_note(agent, paths_note(missing)))
                await runtime.bus.emit(run.id, "answer_paths", {"missing": list(missing)}, True)
                continue
        settled: RunOutcome | SendBack = (
            ended if checker is None else await _check_goal(agent, checker, history, goal, ended, sent_back, facts)
        )
        if isinstance(settled, RunOutcome):
            if not agent.inbox.asks():
                return _run_end(agent, history, goal, settled, facts)
            # The user sent something during this step, such as the answer to a question the run left: the run reads it
            # before it ends. Nothing awaits between this check and the moment the manager shuts the inbox.
            continue
        sent_back += 1
        nudges = 0
        watch = clear_window(watch)
        history.append(await _stored_note(agent, settled.note))
