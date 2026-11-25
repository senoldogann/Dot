"""A run on the owner's Claude plan (item 1013). The run goes whole to Claude Code, the ``claude`` program the owner
installed and signed in to, started without a terminal (``claude -p``) in Dot's workspace, in the Claude Code session
of its chat, which the program keeps from run to run (``--resume``); what was said in the chat on other models since
the session last saw it comes with the run's first message, as a run on OpenCode's agent has it
(:mod:`dotagent.opencode.run`).

The model sees Dot's tools alone: the program runs with no built-in tool but its search of the door's tools (``--tools
ToolSearch``, item 1053: the tools' definitions stay out of the prompt until the model looks one up), no settings file
of the owner's, the project's or the workspace's (``--setting-sources ""``: no hook, no CLAUDE.md), no MCP server but
the door of Dot's bridge (``--strict-mcp-config``, :mod:`dotagent.opencode.bridge`), no slash command, and a permission
mode that refuses whatever the door and the search do not name (``dontAsk`` with ``--allowedTools
mcp__<door>,ToolSearch``). Its first line (``system``/``init``) says what the model has; a run whose model has anything
else ends failed at once
(:func:`dotagent.claude_code.stream.init_problem`). So does a run short of a tool it cannot do its job without
(:func:`tools_needed`: ``finish`` where its mode has it, ``notify`` for a goal that asks for a notification), whether
Dot's own list for it leaves the tool out (checked before the program starts) or the program's first line does not list
it (item 1400); the run's result says which tool and why. Each call of a tool of Dot's runs as a call of Dot's own loop
(:func:`dotagent.agent.handover.bridged_outcome`: the run's gate, the tool's time limit, the run's timeline under the
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
which it is told of once. A run here is told Dot's own system prompt (item 1691) and handed the notes recalled at the
start and the skills that fit its goal under its message, as Dot's own loop hands them (item 1055), keeps Dot's plan
(item 1693), and ends as Dot's own runs end (item 1694: the nudge to finish, the plan's reminders, the goal check, the
lessons, from Dot's record of its calls); not the playbooks offered or Dot's masking and compaction (the research of
2026-11-18, item 1217)."""

import asyncio
import base64
import contextlib
import os
import re
import shutil
import signal
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
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
from dotagent.agent.continuity import RunLedger, new_ledger, with_actions
from dotagent.agent.executor import ToolOutcome
from dotagent.agent.goal_chain import goal_checker_with_standby
from dotagent.agent.handover import (
    ChatSession,
    agent_tools,
    bridged_outcome,
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
    EMPTY_NOTE,
    NO_TOOL_NOTE,
    AgentRun,
    PromptFacts,
    RunEnd,
    RunOutcome,
    SendBack,
    check_goal,
    close_delegated_steps,
    current_plan,
    free_mode_line,
    opening_text,
    plan_note,
    run_end,
    run_prompt_inputs,
    run_skills,
    turn_skills,
)
from dotagent.agent.picks import PICKS_EVENT, owner_server_groups, picks_event_data
from dotagent.agent.plan import PLAN_EVENT, PlanPayload, open_steps, plan_event_data, plan_lines
from dotagent.agent.progress import stop_result
from dotagent.agent.prompt import PromptInputs, build_system_prompt
from dotagent.agent.reaction_words import HOLD_ENDED_EVENT, HOLD_EVENT, hold_lapsed_text
from dotagent.agent.resume import RESUMED_EVENT, resumed_event_data
from dotagent.agent.review import GoalChecker
from dotagent.agent.scaffold import PROFILE_RUNS, Scaffold, scaffold_for
from dotagent.agent.streammask import StreamMask, delta_event, said_in_harness
from dotagent.agent.verify import RunFacts, new_facts, with_outcomes
from dotagent.claude_code.cut import (
    CONTINUED_EVENT,
    ProgramExit,
    continuation_note,
    continue_after_s,
    continued_event_data,
    ended_by_dot,
    exit_seen,
    mark_ended,
    note_cut,
    outward,
    resume_refused,
    run_refusal,
    why_not_continued,
)
from dotagent.claude_code.env import claude_env
from dotagent.claude_code.stream import (
    SEARCH_TOOL,
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
    listed_tools,
    read_line,
    refusal_said,
    tool_prefix,
    user_line,
)
from dotagent.config import AgentConfig
from dotagent.core.errors import (
    ClaudeCodeBroke,
    ClaudeCodeCut,
    ClaudeCodeError,
    ClaudeCodeRefused,
    ClaudePlanLimitError,
    FailSafeTriggered,
    RunCancelled,
    RunHoldLapsed,
)
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.logs import get_logger
from dotagent.core.paths import claude_code_dir
from dotagent.core.text import redact_known, truncate_middle
from dotagent.core.types import JsonObject
from dotagent.llm.claude_code import FAST_SETTINGS, ClaudeCodeProvider
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
    tool_message,
    user_message,
)
from dotagent.opencode.bridge import Door, authorization, new_door
from dotagent.opencode.run import meanwhile
from dotagent.process_groups import forget_group, record_group
from dotagent.repair.claude import LimitWindow, window_refused
from dotagent.runtime import Runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.secrets import known_secrets
from dotagent.skills.catalog import playbook_root
from dotagent.skills.types import Skill
from dotagent.store.endings import PREFIX_CUT_SHORT, RESULT_EMPTY_REPLIES
from dotagent.store.models import NO_PICKS, EventRecord, RunMode, RunRecord
from dotagent.tools.base import PreparedCall, RegisteredTool, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.groups import CORE_GROUP, group_of
from dotagent.tools.meta import (
    FINISH_TOOL,
    NOTIFY_TOOL,
    FinishArgs,
)
from dotagent.tools.registry import ToolRegistry, tools_for_mode
from dotagent.tools.shell import kill_group
from dotagent.tools.terminal import chatless_specs, tells_no_chat

log = get_logger(__name__)

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
# How the model ends its turn's work: with finish where its mode has it (item 1076), else with its last reply (a chat).
_ENDS_WITH_FINISH: str = (
    "When the work is done, or cannot be done, call finish with your final answer (what the owner reads in the chat: "
    "what you did and what you found, with the evidence), success true only if the goal was achieved and verified, "
    "and needs_from_user for what only the owner can give or decide; then end your turn without writing the answer "
    "again."
)
_ENDS_WITH_REPLY: str = "When the work is done, write your answer as your last reply: it is what the owner reads."
_PRIVATE: int = 0o700
_SECRET_FILE: int = 0o600
# What the model is told after Dot's own system prompt (item 1691: the one Dot's own loop builds, whole): what this
# harness changes of it. The tools are Dot's through the door, the definitions of most are looked up (item 1053), Claude
# Code's own are off, and what Dot knows of the owner is in its memory and the workspace too (item 1055).
_HARNESS: str = (
    "# This run's harness\n"
    "This run works in Claude Code on the owner's Claude plan. Your tools are Dot's tools above, those of the MCP "
    "server {door} (mcp__{door}__<tool>): {tools}. "
    # Item 1201: with only the names in the prompt (item 1053), runs called browser_find and search_files with a
    # `query` they had guessed, which neither takes.
    "The definitions of the core ones are in your prompt; look any other up with "
    + SEARCH_TOOL
    + " (select:mcp__{door}__<tool>, several at once with commas) before your first call of it, and call it with "
    "the parameters its definition names; never guess them. Claude Code's own tools, slash commands and skills are "
    "not available here; do not offer them (Dot's skills are, with the skill tool). Each call goes through Dot's "
    "rules and, where they say so, the owner's approval; a refused call says why, and you go on another way. {ending} "
    "Write every message, the short lines between tool calls too, in the language the owner writes in. Files you make "
    "go in Dot's workspace, {workspace}, unless the owner names another place.\n\n"
    # Item 1055: a run on the plan said it did not know what Dot's memory and its agents' files held.
    "What Dot knows of the owner and of their work is yours too: the lines about the owner above (one marked "
    "(you said) is the owner's own and outranks the others), the notes under their message, Dot's memory "
    "(memory_search, recall) and the files Dot and its agents wrote in the workspace (list_dir, read_file, "
    "search_files). Before you say you do not know something about the owner or their work, look it up there."
)


def chat_path(thread_id: str) -> Path:
    """Where the Claude Code session of the chat ``thread_id`` is kept."""
    return claude_code_dir() / "chats" / f"{thread_id}.json"


def forget_chat(thread_id: str) -> None:
    """Let the chat's Claude Code session go: a rewind took out of the chat what the session saw, so the chat's next
    run on Claude Code starts a new session, which hears the chat's history with its first message. Blocking."""
    chat_path(thread_id).unlink(missing_ok=True)


def door_tools(registry: ToolRegistry, run: RunRecord, bounded: frozenset[str] | None) -> ToolRegistry:
    """The tools of Dot's ``run`` gives the model: the :func:`door_tool` ones of its mode (finish among them since item
    1076, where its mode has it), of those only the ones
    ``bounded`` names when the run is an agent's whose tools are bounded (item 86)."""
    delegated: bool = run.parent_run_id is not None
    return ToolRegistry(
        tools={
            tool.spec.name: tool
            for tool in tools_for_mode(registry, run.mode)
            if door_tool(tool.spec.name, delegated) and (bounded is None or tool.spec.name in bounded)
        }
    )


# What makes a goal a request for a notification to the owner (item 1400), in the owner's three languages: "notify
# me", "send me a notification", "show me a notification", "use notify"; Turkish "bildirim gönder/at/yap/ver", "bana
# bildir"; Finnish "lähetä ilmoitus", "ilmoita minulle". A goal that only mentions one ("review the notification
# settings", "lue tämä ilmoitus"), names a thing that is none ("a notification email", "a notification badge") or
# refuses one ("do not send a notification", "älä lähetä ilmoitusta"; a Turkish refusal is the verb with a suffix,
# which the word's end leaves out) is none. "Remind me" is not here: a reminder may as well be the run's own answer,
# which the schedule's notice carries. This is a list of phrases, not a reading of the goal: one it does not know asks
# for nothing (the run is as it was), and one it reads wrongly (a notification sent to another) fails with a result that
# names the tool and what to change.
_NOT_REFUSED: str = r"(?<!not )(?<!n't )(?<!never )"
_NOT_REFUSED_FI: str = r"(?<!älä )(?<!ei )"
_OWNER: str = r"(?:me|us|the\s+(?:owner|user))"
# Finnish: who is told ("minulle", "meille", the owner, the user), and the notification's own forms (ilmoitus,
# ilmoitusta, ilmoituksen, ilmoituksia).
_TOLD_FI: str = r"(?:minulle|meille|omistajalle|käyttäjälle)"
_FI_NOTIFICATION: str = r"ilmoitu(?:s|sta|ksen|ksia)"
# What follows "a notification" when it names another thing ("notification email"), not the notification itself.
_NOT_A_THING: str = (
    r"(?!\s+(?:e-?mails?|badges?|settings?|screens?|banners?|cent(?:er|re)s?|templates?|texts?|preferences?"
    r"|permissions?)\b)"
)
_NOTIFICATION_REQUESTS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        _NOT_REFUSED + r"\bnotify\s+" + _OWNER + r"\b",
        _NOT_REFUSED + r"\b(?:use|call)\s+(?:the\s+)?notify\b",
        _NOT_REFUSED
        + r"\b(?:send|give|push)\s+(?:(?:me|us|him|her|them|the\s+(?:owner|user))\s+)?"
        + r"(?:an?\s+(?:\w+\s+)?notifications?|notifications)\b"
        + _NOT_A_THING,
        _NOT_REFUSED
        + r"\b(?:show|display)\s+(?:"
        + _OWNER
        + r"\s+an?\s+(?:\w+\s+)?notifications?\b"
        + r"|an?\s+(?:\w+\s+)?notifications?\s+(?:to\s+"
        + _OWNER
        + r"\b|at\s+\d|with\s+(?:the\s+)?title\b))",
        r"\bbildirim\w*\s+(?:gönder(?:in|iniz|mek)?|at|atın|yap(?:ın|mak)?|ver(?:in|mek)?|göster(?:in|mek)?"
        r"|yolla(?:yın|mak)?)\b",
        r"\bbana\s+bildir(?:in|iniz)?\b",
        # "ilmoitus" is also an ad ("hakemus tähän ilmoitukseen": an application to this ad): only the
        # notification's own forms (not the ad's "ilmoitukseen", "ilmoituksesta"), and only right after the verb or
        # after the one who is told, name a notification.
        _NOT_REFUSED_FI + r"\blähetä\s+(?:" + _TOLD_FI + r"\s+)?" + _FI_NOTIFICATION + r"\b",
        _NOT_REFUSED_FI + r"\b(?:näytä|anna)\s+" + _TOLD_FI + r"\s+" + _FI_NOTIFICATION + r"\b",
        _NOT_REFUSED_FI + r"\bilmoita\s+(?:minulle|meille)\b",
    )
)


def goal_asks_notification(goal: str) -> bool:
    """Whether ``goal`` asks for a notification to the owner (:data:`_NOTIFICATION_REQUESTS`)."""
    return any(request.search(goal) is not None for request in _NOTIFICATION_REQUESTS)


def tools_needed(mode_tools: Iterable[str], goal: str, source: str) -> tuple[str, ...]:
    """The tools of Dot's a run cannot do its job without (item 1400): ``finish``, which the model ends the run with,
    where its mode has it (``mode_tools``: a chat has none and ends with its last reply), and ``notify`` when the run
    is a schedule's firing (``source``) whose ``goal`` asks for a notification: nobody is there to read an answer, so
    a notification is how the owner is told, and a run that could not send one has not done its job. A run the owner
    watches is not held to it. No other rule: a run that calls no tool is a run that answered."""
    offered: frozenset[str] = frozenset(mode_tools)
    needed: list[str] = [FINISH_TOOL] if FINISH_TOOL in offered else []
    if source == SCHEDULE_SOURCE and goal_asks_notification(goal):
        needed.append(NOTIFY_TOOL)
    return tuple(needed)


def missing_tools(needed: Iterable[str], present: Iterable[str]) -> tuple[str, ...]:
    """The ``needed`` tools that are not among those ``present``, in the order of ``needed``."""
    held: frozenset[str] = frozenset(present)
    return tuple(name for name in needed if name not in held)


def _tools_named(names: Sequence[str]) -> str:
    return f"Dot's {' and '.join(names)} tool{'s' if len(names) > 1 else ''}"


def tools_not_given(missing: Sequence[str], mode: RunMode, agent: str | None, context: JsonObject) -> ClaudeCodeError:
    """What ends a run at its start when Dot's own list for it (its mode, its agent's tools, Settings) leaves out a tool
    it cannot work without (item 1400). The owner reads it as the run's result, on the schedule's History and in the
    notice of its end: what is missing, why, and what to change. A change is needed, so no try again follows
    (:func:`dotagent.core.errors.failure_kind`: a plain :class:`ClaudeCodeError` lasts). ``agent`` is the name of the
    agent whose tools are a list of their own and name none of ``missing``, else ``None``."""
    these: str = "them" if len(missing) > 1 else "it"
    cause: str = (
        f"The tools of the agent {agent} are a list of their own that leaves {these} out: add {these} to the agent's "
        "tools, then run the task again."
        if agent is not None
        else f"The tools Dot gives a {mode} run (its mode and Settings decide them) do not include {these}."
    )
    return ClaudeCodeError(
        f"Dot ended this task before it began: it needs {_tools_named(missing)}, and this run was not given {these}, "
        f"so it could not do its job. {cause} Nothing was done.",
        {**context, "needed_missing": list(missing), "why": "not_given"},
    )


def tools_not_listed(missing: Sequence[str], listed: Iterable[str], context: JsonObject) -> ClaudeCodeBroke:
    """What ends a run at its start when Claude Code's first line lists none of a tool Dot's bridge offered it and the
    run cannot work without (item 1400). The program's own session came up short, which may not happen again, so a
    schedule tries its task once more (:func:`dotagent.core.errors.failure_kind`: a :class:`ClaudeCodeBroke` passes)."""
    these: str = "them" if len(missing) > 1 else "it"
    names: str = ", ".join(sorted(listed)) or "none"
    return ClaudeCodeBroke(
        f"Dot ended this task before it began: it needs {_tools_named(missing)}, and Claude Code did not list {these} "
        f"among the tools Dot's bridge gave it (it listed: {names}), so the model could not have used {these}. "
        "Nothing was done.",
        {**context, "needed_missing": list(missing), "why": "not_listed"},
    )


# The name of the door of every run in Claude Code (item 1052): each run is a process of its own that is given its door
# alone (``--strict-mcp-config``), and its secret is what lets its requests in, so the name need not tell chats apart.
# One name keeps the tools' names, and so the head of every chat's prompt, the same, and Anthropic's cache of that head
# is read by each new chat instead of written again (with 480 tools before item 1053: 151,000 tokens read instead of
# 158,000 written, the first word in 3.9 s instead of 11.7; measured 2026-11-08).
CLAUDE_DOOR: str = "dot"


# What turns on Claude Code's own search of the door's tools (item 1053, :data:`SEARCH_TOOL`), the one tool of its own a
# run's model has, whatever the size of the door (Claude Code's own threshold would leave it off for a small one): the
# door's tools are not put in the prompt, only their names; the model looks a tool up when it needs it and calls it as
# before, through the door and the run's gate. It reaches nothing but the door's list. With 296 tools, as many as the
# owner's servers give, a new chat wrote 10,800 tokens to the cache instead of 99,000 and its first word came in 1.2 s
# (measured 2026-11-08; on the owner's Dot a two-line answer had read 169,000 tokens and come 4 to 26 s after the
# message).
SEARCH_ENV: dict[str, str] = {"ENABLE_TOOL_SEARCH": "true"}

# The door's tools whose definitions are in the prompt from the start, not looked up (item 1341): the ones the shell's
# own description sends the model to, which it called by their names alone, without a look-up. On the owner's Mac, on
# claude_code:sonnet, three of seven calls of terminal_open came with no argument, and wait_until's with arguments
# guessed (item 1340); each was refused and sent again. A probe with Claude Code 2.1.330 showed it: a tool not looked
# up was called with a name for its argument the model made up.
LOADED_TOOLS: frozenset[str] = frozenset({"terminal_open", "wait_until"})


def loaded_tools(names: Iterable[str]) -> frozenset[str]:
    """The tools of ``names`` whose definitions are in the prompt from the start: :data:`LOADED_TOOLS` and every tool of
    Dot's core group (item 1692), as Dot's own loop gives a run whose tools come in groups the core ones whole and the
    others by use_tools. The audit of 2026-11-24 measured "Invalid arguments" in 24 of 124 runs on the plan (19 %)
    against 10 of 162 in Dot's own loop (6 %), most of them core tools called before they were looked up (delegate's
    step as a string, notify without its title); the 29 core tools add about 27,000 characters to the prompt."""
    return frozenset(name for name in names if name in LOADED_TOOLS or group_of(name) == CORE_GROUP)


def dot_tool(tool: str, door: str) -> str | None:
    """The name of Dot's tool a block of a step calls through ``door``, ``None`` for a look-up of one (item 1053), which
    is Claude Code's own and not a call of Dot's."""
    prefix: str = tool_prefix(door)
    return tool.removeprefix(prefix) if tool.startswith(prefix) else None


def _offered_specs(run: RunRecord, tools: ToolRegistry) -> tuple[ToolSpec, ...]:
    """The specs of ``tools`` as ``run`` is offered them: without a command in the background where no chat is told of
    its end (item 1760)."""
    specs: tuple[ToolSpec, ...] = tuple(tool.spec for tool in tools.tools.values())
    return chatless_specs(specs) if tells_no_chat(run) else specs


def door_specs(specs: Iterable[ToolSpec]) -> tuple[ToolSpec, ...]:
    """``specs`` in the order of their names (item 1052): the tools head the prompt, and the same tools in the same
    order are the same head, which Anthropic's cache reads, whatever order the servers connected in."""
    return tuple(sorted(specs, key=lambda spec: spec.name))


def mcp_config(door: Door, url: str) -> JsonObject:
    """The MCP configuration the program is given: the door alone, over HTTP, its secret in its header."""
    return {"mcpServers": {door.name: {"type": "http", "url": url, "headers": {"Authorization": authorization(door)}}}}


def write_mcp_config(folder: Path, config: JsonObject) -> Path:
    """Write ``config`` to a new file only the owner reads (0600) in ``folder``. Blocking."""
    return _write_private(folder / "mcp.json", dumps_compact(config))


def write_prompt(folder: Path, prompt: str) -> Path:
    """Write the run's system prompt to a new file only the owner reads (0600) in ``folder``: since item 1691 it is
    Dot's whole prompt (what Dot knows of the owner, the team, the skills), which the program's arguments, shown by
    ``ps`` to every process of the owner, must not carry (the pre-push review). Blocking."""
    return _write_private(folder / "prompt.md", prompt)


def _write_private(path: Path, text: str) -> Path:
    descriptor: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _SECRET_FILE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as written:
        written.write(text)
    return path


def claude_argv(
    program: str,
    model: str,
    effort: Effort | None,
    speed: Speed,
    prompt_file: Path,
    config_file: Path,
    door: str,
    resume: str | None,
) -> tuple[str, ...]:
    """The command of a run: ``model`` (Claude Code's alias) at ``effort`` (``--effort``, a level of
    :func:`run_effort`; none for a model without levels) and ``speed`` (fast mode through ``--settings``, the
    one settings source a run reads, item 1014; :func:`run_speed`), with what ``prompt_file`` holds after Claude Code's
    own system prompt (at every start: the program records it with the session, which a resume sends as recorded until
    a compaction renders it again), the door ``door`` of ``config_file`` as its only MCP server and its tools as the
    only ones, in the session ``resume`` names or a new one; its input and output are lines of JSON."""
    again: tuple[str, ...] = () if resume is None else ("--resume", resume)
    fast: tuple[str, ...] = ("--settings", FAST_SETTINGS) if speed == "fast" else ()
    level: tuple[str, ...] = () if effort is None else ("--effort", effort)
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
        *level,
        *fast,
        "--append-system-prompt-file",
        str(prompt_file),
        "--tools",
        SEARCH_TOOL,
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
        f"mcp__{door},{SEARCH_TOOL}",
    )


def system_prompt(dot_prompt: str, door: str, tools: Sequence[str], workspace: Path) -> str:
    """What the model is told besides Claude Code's own prompt (item 1691): ``dot_prompt``, the system prompt Dot's own
    loop gives a run with the door's ``tools`` (its character, how to work, the owner, the skills, the team, what Dot
    learned), then what this harness changes of it (:data:`_HARNESS`)."""
    ending: str = _ENDS_WITH_FINISH if FINISH_TOOL in tools else _ENDS_WITH_REPLY
    harness: str = _HARNESS.format(door=door, tools=", ".join(tools), workspace=workspace, ending=ending)
    return f"{dot_prompt}\n\n{harness}"


class OwnerUnread:
    """The owner's own messages sent to the program that it has not taken in yet (item 1685): the program takes a
    message in at the end of the tool call at work, which it says (``Taken``), so a wait of Dot's (``wait_until``) that
    gives way to one has the model read it at once (measured on Claude Code 2.1.331). The run counts them; a wait gives
    way to a message once."""

    def __init__(self) -> None:
        self._sent: int = 0
        self._taken: int = 0
        self._heeded: int = 0
        self._changed: asyncio.Event = asyncio.Event()

    def sent(self) -> None:
        """An owner's own message went to the program."""
        self._sent += 1
        self._changed.set()

    def taken(self) -> None:
        """The program took one of them in."""
        self._taken += 1

    def owner_unread(self) -> bool:
        return self._sent > max(self._taken, self._heeded)

    def heed_owner(self) -> None:
        self._heeded = self._sent

    async def owner_arrival(self) -> None:
        while not self.owner_unread():
            self._changed.clear()
            await self._changed.wait()


@dataclass(frozen=True)
class _Pending:
    """A message sent to the program that it has not taken in: ``keep`` says it is sent again to the program that goes
    on when this one is cut before it takes it in (item 1581), ``own`` that it is the owner's own (item 1685)."""

    text: str
    images: tuple[ImageData, ...]
    keep: bool
    own: bool


@dataclass
class _Quiet:
    """When the program last wrote a line or a call of Dot's tool for it ended, and how many of those are at work: the
    watchdog does not count a call's wait (an approval the owner takes a while over) as silence. ``queue`` is what the
    run reads, which a call's emergency stop reaches (item 1076)."""

    heard: float
    queue: "asyncio.Queue[_Item]"
    working: int = 0
    # When the program last wrote a line (``heard`` also moves when a call of Dot's tool ends): a cut says how long it
    # had been silent (item 1580).
    wrote: float = field(default_factory=time.monotonic)
    # The tools whose call, since the last step began, was an action the policy keeps for the owner even in yolo (a
    # message or mail sent for them, a payment, a public post): a program cut now may have done it without the model
    # reading its result, and the run is not continued (item 1581). A step that begins says the model read the results.
    outward: list[str] = field(default_factory=list)
    # What the model's finish said (item 1076): how the run ends, its answer and what it needs from the owner.
    finished: FinishArgs | None = None
    # Since when the owner holds the run with a ✋ (item 1076), None while they do not. ``resting_since`` is when its
    # turn ended while held: the program waits for the owner then, its silence is no fault, and the hold's time counts
    # from it, so a turn at work (a tool, an approval) is never cut by it, unlike Dot's own loop, which counts from the
    # ✋ but cannot be held mid-step.
    held_since: float | None = None
    resting_since: float | None = None
    # The owner's own messages the program has not taken in, which end a wait of Dot's (item 1685).
    owner: OwnerUnread = field(default_factory=OwnerUnread)


type _OutcomeRunner = Callable[[ToolCall], Awaitable[ToolOutcome]]


@dataclass
class _Record:
    """What Dot's own loop keeps of a run that a run on the plan keeps too (item 1694): the conversation as its goal
    check and its lessons read it (the goal, the model's words, each call of Dot's tool with its result, the owner's
    messages and Dot's notes), the facts the calls showed (:class:`RunFacts`), and how the end of its work went: the
    replies it was told to finish after (``nudges``), the plan reminders, the times the goal check sent it back and the
    end the check settled, ``None`` while none did."""

    goal: Message
    history: list[Message]
    facts: RunFacts
    # What the run did, as the loop's ledger keeps it: what a failure the owner reads says it did last and touched.
    ledger: RunLedger
    used_tools: bool = False
    nudges: int = 0
    plan_reminders: int = 0
    plan_handed: bool = False
    sent_back: int = 0
    settled: RunOutcome | None = None

    def called(self, outcome: ToolOutcome, result: ToolResult) -> None:
        """A call of Dot's tool ended with ``result`` (its ``outcome``'s, with what Dot added to it)."""
        self.used_tools = True
        # A step that acts starts the count of replies told to finish again, as in Dot's own loop.
        self.nudges = 0
        self.history.extend((assistant_message("", None, (outcome.call,)), tool_message(outcome.call, result.text, ())))
        self.facts = with_outcomes(self.facts, (outcome,))
        self.ledger = with_actions(self.ledger, [(outcome.call, not result.is_error, result.text)])


async def _watched_call(
    quiet: _Quiet, record: "_Record", agent: AgentRun, run: _OutcomeRunner, call: ToolCall
) -> ToolResult:
    """Run a call of Dot's tool for the program. It runs in the bridge's task, not the run's (item 1076): the owner's
    emergency stop that it meets (the pointer in a corner of the screen) ends the run as one, as in Dot's own loop, not
    as an error the model reads and goes past. (A Stop cuts it already: the program ended, its request is cancelled.)"""
    quiet.working += 1
    # A finish ends Dot's own runs on the spot; here the program's turn can go on after it, and what it does then (a
    # check that fails, a fix) is newer than what the finish said: a later call drops it, as a message does.
    if call.name != FINISH_TOOL:
        quiet.finished = None
    try:
        outcome: ToolOutcome = await run(call)
        result: ToolResult = await _after_call(agent, outcome)
        record.called(outcome, result)
        # The model's finish, as Dot's own loop reads its own (item 1076): the run ends as it says, not "done" whatever
        # it said; a finish the gate refused or that did not parse says nothing.
        if call.name == FINISH_TOOL and not result.is_error:
            quiet.finished = FinishArgs.model_validate(call.arguments)
        return result
    except FailSafeTriggered as error:
        quiet.queue.put_nowait(_Broke(error))
        raise
    finally:
        quiet.working -= 1
        quiet.heard = time.monotonic()


async def _after_call(agent: AgentRun, outcome: ToolOutcome) -> ToolResult:
    """What the model reads of a call of Dot's tool, once Dot's own loop has done what it does after one (item 1693): a
    delegate call that named a step of the plan closes it (:func:`close_delegated_steps`), and when it could not, the
    result says so, as the loop's note does."""
    note: str | None = await close_delegated_steps(agent, (outcome,))
    if note is None:
        return outcome.result
    return outcome.result.model_copy(update={"text": f"{outcome.result.text}\n\n{note}"})


def _noting_outward(quiet: _Quiet, tools: ToolRegistry) -> ToolRegistry:
    """``tools`` as they are, but a call whose action is one the policy keeps for the owner even in yolo (a message or
    mail sent for them, a payment, a public post) is noted in ``quiet`` as the tool classifies it, which is how the gate
    sees it too (item 1581): a call made inside another's (``code_run``'s) is noted all the same, as it goes through the
    same registry. Nothing waits, asks or refuses here."""

    def noting(tool: RegisteredTool) -> RegisteredTool:
        async def prepare(arguments: JsonObject, ctx: ToolContext) -> PreparedCall:
            prepared: PreparedCall = await tool.prepare(arguments, ctx)
            if outward(prepared.action):
                quiet.outward.append(tool.spec.name)
            return prepared

        return replace(tool, prepare=prepare)

    return ToolRegistry(tools={name: noting(tool) for name, tool in tools.tools.items()})


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
    quiet: _Quiet
    told: LimitReached | None = None
    since_told: int = 0
    over: LimitReached | None = None
    steps: int = 0
    sent: int = 0
    # The messages the program said it took in, and whether one came after the turn's last step began (taken then, it
    # waits for a turn of its own; one taken before a step is that step's to answer).
    taken: int = 0
    taken_since_step: bool = False
    # One entry for each message sent to the program that it has not taken in yet, in the order sent (a note of Dot's
    # about the cut too, which is not sent again but which the program takes in all the same), so that each ``Taken``
    # takes out its own message. And how many steps the run had when this program began.
    pending: list[_Pending] = field(default_factory=list)
    steps_before: int = 0
    # Why fast mode was off though the run asked for it, said with its answer (item 1014); "" when it was not.
    fast_off: str = ""
    answer: str = ""
    session_id: str = ""
    inited: bool = False
    step: _Step | None = None
    finish: str = ""
    window: LimitWindow | None = None
    added: int = 0
    # The tools of Dot's the run cannot work without (:func:`tools_needed`), which the init line must list.
    needed: tuple[str, ...] = ()
    # What the goal check and the lessons read (item 1694), the goal mode's checker (``None`` outside goal mode), and
    # the text of the model's last step.
    record: _Record | None = None
    checker: GoalChecker | None = None
    last_text: str = ""
    stream: StreamMask = field(init=False)

    def __post_init__(self) -> None:
        self.stream = StreamMask(self.show)

    @property
    def runtime(self) -> Runtime:
        return self.agent.runtime

    async def show(self, delta: StreamDelta) -> None:
        await self.emit("delta", delta_event(delta, self.steps + 1, said_in_harness(self.agent.run.mode)), False)

    async def emit(self, kind: str, data: JsonObject, persisted: bool) -> None:
        await self.runtime.bus.emit(self.agent.run.id, kind, data, persisted)

    async def send(self, text: str, images: tuple[ImageData, ...], keep: bool, own: bool) -> None:
        """A message to the model, on the program's input. ``keep`` says it is sent again to the program that goes on
        when this one is cut before it takes the message in (item 1581): not a note of Dot's about the cut; ``own``
        that it is the owner's own words (item 1685)."""
        self.sent += 1
        self.pending.append(_Pending(text=text, images=images, keep=keep, own=own))
        try:
            self.stdin.write(user_line(text, images).encode("utf-8"))
            await self.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise ClaudeCodeCut(
                "Claude Code stopped reading its input before the run ended",
                {"run_id": self.agent.run.id, "error": str(error)},
            ) from error

    async def follow(self) -> str:
        """Read the program's output until a result comes after the program took in each message sent; the model's
        last answer."""
        while True:
            item: _Item = await self._next()
            match item:
                case _Stopped():
                    raise RunCancelled("the run was stopped", {"run_id": self.agent.run.id})
                case _Ended():
                    raise ClaudeCodeCut(
                        "Claude Code ended before it answered", {"run_id": self.agent.run.id, "inited": self.inited}
                    )
                case _Silent(seconds=seconds):
                    raise ClaudeCodeBroke(
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
                    # A run the owner holds with a ✋ (item 586) does not end with the turn: it waits for what they
                    # write next, or their ✋ taken back, as Dot's own loop does (item 1076).
                    # A run past its limit ends all the same.
                    if event is not None and await self.seen(event):
                        if self.over is not None or (self.quiet.held_since is None and await self.settle()):
                            return self.answer
                        if self.quiet.held_since is None:
                            # Sent back to work (item 1694): the note starts a turn of its own.
                            continue
                        # Held, the turn ended: the hold's time counts from now.
                        self.quiet.resting_since = time.monotonic()

    async def note(self, text: str) -> None:
        """A note of Dot's to the model that sends it back to work, as Dot's own loop stores one in its history."""
        if self.record is not None:
            self.record.history.append(user_message(text, ()))
        # Sent again to a program that goes on after a cut (item 1581), as the limit's note is; never the owner's words.
        await self.send(text, (), True, False)

    async def settle(self) -> bool:
        """Whether the run ends now that the program's turn ended, decided as Dot's own loop decides at a reply that
        ends its work (item 1694): a run whose mode ends with ``finish`` and that did not call it after working is told
        to (``[agent] finish_nudges`` times), a finish with steps of the plan still open is reminded of them (``[agent]
        stall_limit`` times), and in goal mode the end goes to the goal check, which settles it or sends the run back
        to work with its reason, ``[agent] goal_patience`` times; a run whose plan stays open with no check ends failed.
        A run past its limit, a chat (no finish) and a run whose record is not kept end as they are."""
        record: _Record | None = self.record
        if record is None or FINISH_TOOL not in self.needed:
            return True
        config: AgentConfig = self.runtime.config.agent
        finished: FinishArgs | None = self.quiet.finished
        if finished is None and record.used_tools and record.nudges < config.finish_nudges:
            record.nudges += 1
            await self.note(NO_TOOL_NOTE if self.last_text else EMPTY_NOTE)
            return False
        if finished is None and record.used_tools and not self.last_text:
            # Empty again and again: the run ends failed, as Dot's own loop ends it (the pre-push review).
            record.settled = RunOutcome(status="failed", result=RESULT_EMPTY_REPLIES, needs="")
            return True
        ended: RunOutcome = (
            # The reply that ended the turn is the answer, not an earlier step's words (the pre-push review).
            RunOutcome(status="done", result=self.last_text or self.answer, needs="")
            if finished is None
            else RunOutcome(
                status="done" if finished.success else "failed",
                result=finished.answer,
                needs=finished.needs_from_user or "",
            )
        )
        plan: PlanPayload = await current_plan(self.agent)
        unclosed: tuple[int, ...] = open_steps(plan.steps)
        if unclosed and record.plan_reminders < config.stall_limit:
            record.plan_reminders += 1
            record.nudges = 0
            await self.emit("plan_reminder", {"open_steps": list(unclosed)}, True)
            await self.note(plan_note(unclosed))
            return False
        if unclosed and (self.checker is None or record.plan_handed):
            steps: str = ", ".join(str(number) for number in unclosed)
            stopped: str = stop_result(
                f"finish was called while plan step(s) {steps} were still open",
                record.ledger,
                plan_lines(plan),
            )
            record.settled = RunOutcome(status="failed", result=stopped, needs="")
            return True
        if self.checker is None:
            record.settled = ended
            return True
        record.plan_handed = record.plan_handed or bool(unclosed)
        settled: RunOutcome | SendBack = await check_goal(
            self.agent, self.checker, record.history, record.goal, ended, record.sent_back, record.facts
        )
        if isinstance(settled, RunOutcome):
            record.settled = settled
            return True
        record.sent_back += 1
        record.nudges = 0
        # What the finish said is what the check did not accept: the run's end is the next one's.
        self.quiet.finished = None
        await self.note(settled.note)
        return False

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
                # Before the model's first word (item 1400): a bridge that came up short, the run is over before it can
                # answer that its tools are gone.
                listed: frozenset[str] = listed_tools(event, self.door)
                absent: tuple[str, ...] = missing_tools(self.needed, listed)
                if absent:
                    raise tools_not_listed(absent, listed, {"run_id": self.agent.run.id, "tools": list(event.tools)})
                self.inited = True
                self.session_id = event.session_id or self.session_id
            case LimitSeen(window=window):
                self.window = window
                if window_refused(window):
                    resets: float | None = limit_resets(window, "")
                    raise ClaudePlanLimitError(
                        limit_message(window, resets),
                        resets,
                        {"run_id": self.agent.run.id, "window": window.model_dump(mode="json")},
                    )
            case Taken():
                self.taken += 1
                self.taken_since_step = True
                if self.pending and self.pending.pop(0).own:
                    self.quiet.owner.taken()
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
                # The model read the results of the calls before this step: none of them is in flight now (item 1581).
                self.quiet.outward.clear()
                self.stream.begin(await asyncio.to_thread(known_secrets, filled=True))
                self.step = _Step(
                    started=time.monotonic(), input_tokens=event.input_tokens, cached_tokens=event.cached_tokens
                )
            case BlockBegan() if self.step is not None:
                self.step.kinds[event.index] = event.kind
                called: str | None = dot_tool(event.tool, self.door) if event.kind == "tool" else None
                if called is not None:
                    self.step.tools.append(called)
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
        # The program's turn may end, after the finish it called, with a reply of its own that says the answer again in
        # other words: the finish's answer is the run's, and the chat showed both (the owner's screenshot of
        # 2026-11-11). A step that calls a tool after it is work, which clears the finish (``_watched_call``).
        shown: str = "" if self.quiet.finished is not None and not step.tools else text
        self.last_text = text.strip()
        if text.strip():
            self.answer = text.strip()
            if self.record is not None:
                self.record.history.append(assistant_message(text.strip(), None, ()))
        known: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
        await self.emit(
            "assistant",
            {
                "step": self.steps,
                "text": redact_known(shown, known),
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
                resets_at: float | None = limit_resets(self.window, event.text)
                raise ClaudePlanLimitError(limit_message(self.window, resets_at), resets_at, context)
            if refusal_said(event.text):
                # Declined for what it says, the same message is declined again: a try of it would be too (item 1761).
                raise ClaudeCodeRefused(
                    f"The model declined the run's message, and asked again it declines it again: change the run's "
                    f"model or reword the task. Claude Code said: {event.text}",
                    {**context, "model": self.agent.run.model},
                )
            raise ClaudeCodeBroke(f"Claude Code's session failed: {event.text or event.subtype}", context)
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
            # The turn's own answer, which its last step may have left out of its words.
            self.last_text = event.text.strip()
        return answered_all(self.taken, self.sent, self.taken_since_step)

    @property
    def began_a_step(self) -> bool:
        """Whether the program at work has begun a step of its own: the model answered something in it."""
        return self.step is not None or self.steps > self.steps_before

    async def restart(self, stdin: asyncio.StreamWriter) -> list[_Pending]:
        """Go on with a new program in the same run (item 1581): what the cut one had of its own (its input, the
        messages it took in, the step it was in, what it said of the model's tools) is gone; what the run has is not
        (its steps, its answer so far, its session, its limits). The page drops the words of the step that was
        streaming, as it does for a step it sends back. Returns the messages the cut program had not taken in."""
        if self.step is not None:
            await self.stream(StreamDelta(kind="reset", text=""))
        lost: list[_Pending] = [one for one in self.pending if one.keep]
        self.pending = []
        self.stdin = stdin
        self.sent = self.taken = 0
        self.taken_since_step = False
        self.inited = False
        self.step = None
        self.steps_before = self.steps
        self.quiet.outward.clear()
        self.quiet.heard = self.quiet.wrote = time.monotonic()
        return lost

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
        await self.send(_LIMIT_NOTE.format(spent=limit_spent(reached)), (), True, False)

    async def _next(self) -> _Item:
        """What comes next for the run; while the owner holds it, within ``[reactions] hold_minutes`` of the hold, past
        which it ends (:class:`RunHoldLapsed`), as a held run of Dot's own loop does."""
        resting_since: float | None = self.quiet.resting_since
        held_since: float | None = self.quiet.held_since
        if resting_since is None or held_since is None:
            return await self.queue.get()
        minutes: float = self.runtime.config.reactions.hold_minutes
        try:
            async with asyncio.timeout(max(minutes * 60.0 - (time.monotonic() - resting_since), 0.0)):
                return await self.queue.get()
        except TimeoutError:
            held_s: float = round(time.monotonic() - held_since, 1)
            await self.emit(HOLD_ENDED_EVENT, {"reason": "lapsed", "held_s": held_s}, True)
            raise RunHoldLapsed(hold_lapsed_text(minutes), {"run_id": self.agent.run.id, "held_s": held_s}) from None

    async def _mind_hold(self, sent: UserInput) -> None:
        """A ✋ of the owner holds the run (items 586 and 1076); what they write next, or their ✋ taken back, lets it
        go on, told to the pages as Dot's own loop tells them. A note about any other reaction changes nothing of
        that."""
        if sent.reaction == "hold" and self.quiet.held_since is None:
            self.quiet.held_since = time.monotonic()
            self.agent.inbox.holding = True
            await self.emit(HOLD_EVENT, {"step": self.steps}, True)
        elif sent.reaction in (None, "release") and self.quiet.held_since is not None:
            held_s: float = round(time.monotonic() - self.quiet.held_since, 1)
            self.quiet.held_since = None
            self.quiet.resting_since = None
            self.quiet.heard = time.monotonic()
            self.agent.inbox.holding = False
            reason: str = "message" if sent.reaction is None else "released"
            await self.emit(HOLD_ENDED_EVENT, {"reason": reason, "held_s": held_s}, True)

    async def steer(self, sent: UserInput) -> None:
        """The owner's message while the model works: kept in the chat and sent to the program, which reads it once
        its turn allows."""
        await self._mind_hold(sent)
        # What comes after a finish (the owner's message, a note) goes on with the work: the run ends as the model's
        # next finish says, or with its last reply, not as the finish before it did (the pre-push review). A ✋ changes
        # nothing of the work.
        if sent.reaction != "hold":
            self.quiet.finished = None
            if self.record is not None:
                self.record.nudges = 0
        # Whatever is sent (a message, a note, a second ✋) starts a turn of the program's: it is at work, not resting,
        # so the hold's time stops and the silence counts from now (the pre-push review).
        self.quiet.resting_since = None
        self.quiet.heard = time.monotonic()
        runtime: Runtime = self.runtime
        run_id: str = self.agent.run.id
        note: Message = await message_with_attachments(
            runtime, self.agent.run.thread_id, run_id, sent.text, sent.files, self.vision
        )
        # As Dot's own loop takes it (item 902): one place for what a message does to the run's trust.
        self.agent.gate.take(sent)
        await runtime.store.append_messages(self.agent.run.thread_id, [note])
        if self.record is not None:
            self.record.history.append(note)
        self.added += 1
        await self.emit("steer", steer_event(sent, self.agent.gate.tainted), True)
        own: bool = sent.owners_own()
        if own:
            self.quiet.owner.sent()
        await self.send(note.content, await asyncio.to_thread(_images, sent.files, self.vision), True, own)


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
        quiet.heard = quiet.wrote = time.monotonic()
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
        if quiet.working == 0 and quiet.resting_since is None and silent > silence_s:
            queue.put_nowait(_Silent(seconds=silent))
            return


async def _read_tail(stream: asyncio.StreamReader) -> str:
    data: bytes = await stream.read()
    return data.decode("utf-8", errors="replace")[-_STDERR_KEPT:]


async def _end_program(process: asyncio.subprocess.Process) -> None:
    """End the program and everything it started: SIGTERM to its group, then, after :data:`_TERM_GRACE_S`, SIGKILL. A
    program still running is marked as ended by Dot (item 1580): its end is no cut and counts for no mass cut."""
    if process.returncode is None:
        mark_ended(process)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGTERM)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(_TERM_GRACE_S):
                await process.wait()
    kill_group(process.pid)
    forget_group(process.pid)
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


def _checker_of(agent: AgentRun) -> GoalChecker | None:
    """The goal check of a goal-mode run (item 1694), as Dot's own loop makes it: the review model, then the standby
    one; ``None`` outside goal mode."""
    runtime: Runtime = agent.runtime
    run: RunRecord = agent.run
    if not run.goal_mode:
        return None
    return goal_checker_with_standby(
        runtime.router,
        runtime.roles.review(),
        runtime.roles.standby(),
        runtime.config.agent.model_call_timeout_s,
        f"{run.thread_id}:goal",
        run.id,
    )


async def _provider_of(runtime: Runtime, ref: ModelRef) -> ClaudeCodeProvider:
    provider: ChatProvider = await runtime.router.provider(ref.provider)
    if not isinstance(provider, ClaudeCodeProvider):
        raise ClaudeCodeError("the model's provider is not Claude Code", {"model": ref.label})
    return provider


async def _prompt_of(agent: AgentRun, door: Door, inputs: PromptInputs) -> str:
    """Dot's system prompt for the run (item 1691): the one Dot's own loop builds from ``inputs``, the owner's free
    mode as it stands, and what the harness changes of it."""
    runtime: Runtime = agent.runtime
    dot_prompt: str = build_system_prompt(inputs) + free_mode_line(runtime)
    return system_prompt(dot_prompt, door.name, [spec.name for spec in door.specs], runtime.workspace)


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


def run_effort(levels: tuple[Effort, ...], chosen: Effort | None, configured: Effort) -> Effort | None:
    """The level a run of the plan thinks at (item 1014): the run's own, else the configured default, rounded to the
    nearest of the levels Claude Code lists for the model (``levels``, item 1367; ``off`` and ``minimal`` to the
    lowest, ``max`` the highest), as Dot's own loop rounds a level to a model's
    (:func:`dotagent.llm.traits.effective_effort`); what the composer shows is what goes. ``None`` (no ``--effort``)
    for a model without levels."""
    return effective_effort(levels, configured if chosen is None else chosen)


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


def _outcome(follow: _Follow, answer: str, goal: str, finished: FinishArgs | None) -> RunOutcome:
    """How the run ends: failed when its limit was reached and the model went on regardless, and when its last reply
    was cut short at the model's output limit (item 552); else as the model's ``finished`` says (item 1076: done only
    when it succeeded, with what it needs from the owner), or, when it never called finish, done with its last
    answer."""
    if follow.over is not None:
        result: str = limit_result(follow.over, new_ledger(goal), "", answer)
        return RunOutcome(status="failed", result=result, needs="")
    if follow.finish in _CUT_SHORT:
        cut: str = f"{PREFIX_CUT_SHORT} and the run did not finish (the last one ended with '{follow.finish}')."
        return RunOutcome(status="failed", result=cut, needs="")
    settled: RunOutcome | None = None if follow.record is None else follow.record.settled
    closing: str = finished.answer if finished is not None else answer
    said: str = (
        settled.result if settled is not None else closing
    ) or "Claude Code finished without a closing message."
    if follow.fast_off:
        said = f"{said}\n\n{fast_off_words(follow.fast_off)}"
    if settled is not None:
        # As the goal check, the plan's reminders or the nudges to finish settled it (item 1694).
        return RunOutcome(status=settled.status, result=said, needs=settled.needs)
    if finished is not None:
        return RunOutcome(
            status="done" if finished.success else "failed", result=said, needs=finished.needs_from_user or ""
        )
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


async def _agent_leaving_out(agent: AgentRun, bounds: frozenset[str] | None, missing: Sequence[str]) -> str | None:
    """The name of the agent whose own list of tools (``bounds``) leaves out one of the ``missing`` tools, ``None`` when
    the run's agent has no list or names them all: what leaves them out is then Dot's mode or Settings."""
    agent_id: str | None = agent.run.agent_id
    if bounds is None or agent_id is None or all(name in bounds for name in missing):
        return None
    return (await agent.runtime.store.get_agent(agent_id)).name


def opened_bounds(bounded: frozenset[str], opened: frozenset[str], names: Iterable[str]) -> frozenset[str]:
    """``bounded``, an agent's tools, with those of ``names`` in the groups ``opened`` to it by the owner's picks."""
    return bounded | frozenset(name for name in names if group_of(name) in opened)


# What a resumed run on the plan is told after the note of the run it goes on from (item 1076): that note says the
# conversation holds every step of that run, which Dot's own loop keeps; a session of the program does not.
_RESUMED_HERE: str = (
    "[system note] On this model the steps of the run you go on from are not in your session: what it found is in the "
    "files and pages it left and in the chat above. Look at them again before you act on them."
)


async def _opening_of(agent: AgentRun, ref: ModelRef, info: ModelInfo) -> str:
    """The run's first message (:func:`_picked_opening`), and for a resumed run what it cannot see of the run it goes
    on from (:data:`_RESUMED_HERE`)."""
    text: str = await _picked_opening(agent, ref, info)
    return text if agent.opening.resumed is None else f"{text}\n\n{_RESUMED_HERE}"


async def _picked_opening(agent: AgentRun, ref: ModelRef, info: ModelInfo) -> str:
    """The run's first message: the owner's words, then what they picked for it (item 1022), as Dot's own loop gives
    them: each picked skill's instructions and the connected apps (MCP servers) whose tools to use, which reach the
    program through the door. The manager refuses what Claude Code cannot take (Plan first) before the run starts."""
    runtime: Runtime = agent.runtime
    if agent.opening.picks == NO_PICKS:
        return agent.opening.text
    offered: tuple[Skill, ...] = tuple((await run_skills(runtime, agent.run)).skills)
    scaffold: Scaffold = scaffold_for(
        runtime.config.agent,
        info,
        await runtime.store.model_profile(ref.label, PROFILE_RUNS),
        runtime.context_sizes.of(ref.label),
    )
    return await opening_text(agent.opening, offered, scaffold.tool_output_chars, playbook_root(runtime.config), {})


@dataclass
class _Program:
    """One start of the ``claude`` program in a run: its process and input, the end of its error stream and the pumps of
    what it says and of its silence. ``continuing`` is whether it was started to go on after a cut (item 1581)."""

    process: asyncio.subprocess.Process
    stdin: asyncio.StreamWriter
    tail: asyncio.Task[str]
    pumps: tuple[asyncio.Task[None], ...]
    continuing: bool = False
    # Whether it is a new session that goes on in place of a cut one and is sent the run's brief (item 1581).
    fresh: bool = False


@dataclass(frozen=True)
class _Launch:
    """What starts the program of a run, and starts it again after a cut (item 1581)."""

    agent: AgentRun
    program: Path
    ref: ModelRef
    info: ModelInfo
    prompt_file: Path
    config_file: Path
    door: str
    folder: Path
    queue: asyncio.Queue[_Item]
    quiet: _Quiet
    silence_s: float

    async def start(self, resume: str | None, continuing: bool, fresh: bool = False) -> _Program:
        """Start the program in the session ``resume`` names, or in a new one when it is ``None``; ``fresh`` says it is
        the new session of a cut run, which is sent the run's brief."""
        run: RunRecord = self.agent.run
        runtime: Runtime = self.agent.runtime
        argv: tuple[str, ...] = claude_argv(
            str(self.program),
            self.ref.model,
            run_effort(self.info.efforts, run.effort, runtime.config.models.effort),
            run_speed(self.ref, run.speed),
            self.prompt_file,
            self.config_file,
            self.door,
            resume,
        )
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(runtime.workspace),
                env={**claude_env(runtime.shell_env, self.folder), **SEARCH_ENV},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=_LINE_LIMIT,
            )
        except OSError as error:
            raise ClaudeCodeError(
                "Claude Code could not be started", {"program": str(self.program), "error": str(error)}
            ) from error
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        # Until :func:`_end_program`: a daemon that dies with the program at work leaves it to the next start.
        record_group(process.pid, run.id, "claude_code", f"Claude Code ({self.ref.model})")
        log.info(
            "claude_code_run_started",
            run_id=run.id,
            pid=process.pid,
            model=self.ref.model,
            resume=resume is not None,
            continuing=continuing,
        )
        return _Program(
            process=process,
            stdin=process.stdin,
            tail=asyncio.create_task(_read_tail(process.stderr), name="claude-code-stderr"),
            pumps=(
                asyncio.create_task(_pump_lines(process.stdout, self.queue, self.quiet), name="claude-code-lines"),
                asyncio.create_task(_watch(self.quiet, self.silence_s, self.queue), name="claude-code-watch"),
            ),
            continuing=continuing,
            fresh=fresh,
        )


async def _retire(program: _Program) -> None:
    """Let go of a program that was cut (item 1581): its pumps, its input, whose pipe is broken, and what is left of its
    group. Its error stream was read already (:func:`_cut_of`)."""
    for task in program.pumps:
        task.cancel()
    await asyncio.gather(*program.pumps, return_exceptions=True)
    await _close_input(program.stdin)
    await asyncio.shield(_end_program(program.process))
    program.tail.cancel()
    await asyncio.gather(program.tail, return_exceptions=True)


async def _pause(cancel: asyncio.Event, seconds: float, run_id: str) -> None:
    """Wait ``seconds`` before the program is started again (item 1581), which a Stop that comes meanwhile ends at once:
    the run then ends as stopped, never continued."""
    try:
        async with asyncio.timeout(seconds):
            await cancel.wait()
    except TimeoutError:
        return
    raise RunCancelled("the run was stopped", {"run_id": run_id})


def _drop_stale(queue: asyncio.Queue[_Item]) -> None:
    """Take out of the run's queue what a program that is gone left in it, its end and its silence, which would end the
    run of the one that goes on; the owner's messages, a stop and the error of a call wait where they are."""
    kept: list[_Item] = []
    while not queue.empty():
        item: _Item = queue.get_nowait()
        if not isinstance(item, _Ended | _Silent):
            kept.append(item)
    for item in kept:
        queue.put_nowait(item)


# What a new session that goes on in place of a cut one is told of it (item 1581): how many steps and calls of the run's
# record it is given, and how much of each.
_BRIEF_STEPS: int = 400
_BRIEF_CALLS: int = 40
_BRIEF_WORDS: int = 700
_BRIEF_LINE: int = 200


async def _what_was_done(runtime: Runtime, run_id: str, steers: Sequence[str]) -> str:
    """What the program that was cut had done, from Dot's record of the run, for the new session that goes on in its
    place when the cut one could not be resumed (item 1581): its first words, which are its plan, the last calls it made
    and how each ended, its last words, and what the owner wrote while it worked."""
    steps: list[EventRecord] = await runtime.store.events_of_kind(run_id, "assistant", _BRIEF_STEPS)
    if len(steps) == _BRIEF_STEPS:
        # Past the number read, the oldest are not among them, and the first words are the plan (the review).
        steps = await runtime.store.events_of_kind_after(run_id, "assistant", 0)
    calls: list[EventRecord] = await runtime.store.events_of_kind(run_id, "tool_result", _BRIEF_CALLS)
    spoken: list[str] = [text for text in (str(event.data.get("text") or "").strip() for event in steps) if text]
    parts: list[str] = [
        "What the cut session had done, from Dot's record of this run (its steps are not in your session):"
    ]
    if spoken:
        parts.append(f'Its first words: "{truncate_middle(spoken[0], _BRIEF_WORDS)}"')
    if calls:
        lines: list[str] = []
        for call in calls:
            said: str = " ".join(str(call.data.get("summary") or call.data.get("text") or "").split())
            ended: str = "worked" if call.data.get("ok") is True else "failed"
            lines.append(f"- {call.data.get('tool')}: {truncate_middle(said, _BRIEF_LINE)} ({ended})")
        parts.append(f"Its last {len(calls)} calls, oldest first:\n" + "\n".join(lines))
    if len(spoken) > 1:
        parts.append(f'Its last words: "{truncate_middle(spoken[-1], _BRIEF_WORDS)}"')
    if steers:
        written: str = "\n".join(f"- {truncate_middle(steer.strip(), _BRIEF_WORDS)}" for steer in steers)
        parts.append(f"What the owner wrote while it worked:\n{written}")
    return "\n\n".join(parts)


@dataclass
class _Cuts:
    """How a run meets the cuts of its program (item 1581): the exits so far, how often it went on, and what it does
    about the next one."""

    agent: AgentRun
    follow: _Follow
    launch: _Launch
    allowed: int
    earlier: list[Message]
    opening: str
    pictures: tuple[ImageData, ...]
    exits: list[ProgramExit] = field(default_factory=list)
    continued: int = 0
    # Whether the program was cut after the model's finish: the run ends as the finish says, with nothing continued.
    at_finish: bool = False

    async def after(self, error: ClaudeCodeError, current: _Program) -> tuple[_Program, list[_Pending]] | None:
        """What the run does when ``error`` ended the program ``current`` before its result: the program that goes on,
        with what it is sent first, when the run is continued; ``None`` when the program was cut after the model's
        finish, which ends the run as the finish says; otherwise it raises: ``error`` with what the cuts were
        (``continued``, ``not_continued``, ``cuts``), or :class:`RunCancelled` when the run was stopped meanwhile, which
        wins over a cut. A program that was itself a continuation and fails before any step of its own with what says
        its session could not be taken up again is met with a new session."""
        follow: _Follow = self.follow
        run_id: str = self.agent.run.id
        fresh: bool
        refusal: str | None
        if isinstance(error, ClaudeCodeCut):
            program: ProgramExit = await _cut_of(follow, current)
            error.context.update(program.context())
            self.exits.append(program)
            if follow.quiet.finished is not None:
                self.at_finish = True
                return None
            # A continuation that had not said what the model has could not take its session up. Nor has a new session
            # that never took in its brief (the run's goal, the chat, what was done) heard anything of the run: a resume
            # of it would go on from a plan it does not have, so the next one is a new session again.
            fresh = current.continuing and (not program.inited or (current.fresh and follow.taken == 0))
            refusal = why_not_continued(
                program=program,
                resumed=current.continuing,
                cancelled=self.agent.cancel.is_set(),
                outward_calls=tuple(follow.quiet.outward),
                continued=self.continued,
                allowed=self.allowed,
            )
        else:
            error.context["stderr"] = await _masked_stderr(current)
            fresh = (
                isinstance(error, ClaudeCodeBroke)
                and current.continuing
                and not follow.began_a_step
                and resume_refused(error.message)
            )
            if not fresh:
                raise error
            program = ProgramExit(
                run_id=run_id,
                returncode=None,
                stderr=str(error.context["stderr"]),
                inited=follow.inited,
                steps=follow.steps,
                silent_s=round(time.monotonic() - follow.quiet.wrote, 1),
                session_id=follow.session_id,
                at=time.time(),
                said=await _masked(error.message),
            )
            self.exits.append(program)
            refusal = run_refusal(cancelled=self.agent.cancel.is_set(), continued=self.continued, allowed=self.allowed)
        if refusal is not None:
            if self.agent.cancel.is_set():
                raise RunCancelled("the run was stopped", {"run_id": run_id}) from error
            error.context["continued"] = self.continued
            error.context["not_continued"] = refusal
            if len(self.exits) > 1:
                error.context["cuts"] = [one.context() for one in self.exits]
            raise error
        return await self._go_on(program, current, fresh)

    async def _go_on(self, program: ProgramExit, current: _Program, fresh: bool) -> tuple[_Program, list[_Pending]]:
        """Start the program again after ``program`` ended: in the session it had (told it was cut and to look at the
        real state), or in a new one when ``fresh``; the run's page gets its line."""
        follow: _Follow = self.follow
        agent: AgentRun = self.agent
        waited: float = continue_after_s(self.continued)
        await _retire(current)
        _drop_stale(self.launch.queue)
        await _pause(agent.cancel, waited, agent.run.id)
        self.continued += 1
        started: _Program = await self.launch.start(None if fresh else follow.session_id, continuing=True, fresh=fresh)
        try:
            return started, await self._first_messages(program, started, fresh, waited)
        except BaseException:
            # A stop that comes before the run holds the new program (it is handed over when this returns): nobody else
            # would end it.
            await asyncio.shield(_retire(started))
            raise

    async def _first_messages(
        self, program: ProgramExit, started: _Program, fresh: bool, waited: float
    ) -> list[_Pending]:
        """The run takes the new program up (what it had of the cut one's is gone, the page is told) and what is sent
        it first: what the owner sent that the cut program never took in, as sent, then the note about the cut; or, for
        a new session, the run's brief."""
        agent: AgentRun = self.agent
        lost: list[_Pending] = await self.follow.restart(started.stdin)
        data: JsonObject = continued_event_data(program, self.continued, self.allowed, fresh, waited)
        log.warning("claude_code_run_continued", run_id=agent.run.id, **data)
        await self.follow.emit(CONTINUED_EVENT, data, True)
        note: str = continuation_note(program, fresh)
        if not fresh:
            return [*lost, _Pending(text=note, images=(), keep=False, own=False)]
        # A new session is told what the owner sent in its brief, a message of Dot's: none of theirs waits any more.
        for one in lost:
            if one.own:
                self.follow.quiet.owner.taken()
        runtime: Runtime = agent.runtime
        sent: list[Message] = await runtime.store.load_messages(agent.run.thread_id, 0)
        steers: list[str] = [message.content for message in sent[len(self.earlier) + 2 :] if message.role == "user"]
        briefing: str = await _what_was_done(runtime, agent.run.id, steers)
        text: str = f"{meanwhile(self.opening, self.earlier)}\n\n{briefing}\n\n{note}"
        return [_Pending(text=text, images=self.pictures, keep=False, own=False)]


async def run_in_claude_code(agent: AgentRun) -> RunEnd:
    """Run ``agent``'s run in Claude Code on the owner's plan (module docstring)."""
    started: float = time.monotonic()
    runtime: Runtime = agent.runtime
    run = agent.run
    # A resumed run says which it goes on from, as Dot's own loop's does (item 1076): its page links that run.
    if agent.opening.resumed is not None:
        await runtime.bus.emit(run.id, RESUMED_EVENT, resumed_event_data(agent.opening.resumed), True)
        # The plan of the run it resumes becomes this run's plan, as in Dot's own loop (item 1693).
        if agent.opening.resumed.plan.steps:
            await runtime.bus.emit(run.id, PLAN_EVENT, plan_event_data(agent.opening.resumed.plan), True)
    if agent.opening.files:
        await runtime.bus.emit(run.id, ATTACHMENTS_EVENT, {"files": attachment_summary(agent.opening.files)}, True)
    ref: ModelRef = parse_model_ref(run.model)
    provider: ClaudeCodeProvider = await _provider_of(runtime, ref)
    program: Path = await provider.program()
    info: ModelInfo = await provider.model_info(ref.model)
    earlier: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
    # What the message came with stands under it at once, as in Dot's own loop (item 967).
    if agent.opening.picks != NO_PICKS:
        await runtime.bus.emit(run.id, PICKS_EVENT, picks_event_data(agent.opening.picks), True)
    bounds: frozenset[str] | None = await _bounds_of(agent)
    tools: ToolRegistry = await withheld_out(runtime, run, door_tools(runtime.registry, run, bounds), ref.label)
    needed: tuple[str, ...] = tools_needed(
        (tool.spec.name for tool in tools_for_mode(runtime.registry, run.mode)), run.goal, run.source
    )
    queue: asyncio.Queue[_Item] = asyncio.Queue()
    quiet: _Quiet = _Quiet(heard=time.monotonic(), queue=queue)
    chat: ChatSession | None = await asyncio.to_thread(read_chat, chat_path(run.thread_id))
    # The notes the session the run goes on in heard, read from the messages it took in whole, as Dot's own loop reads
    # what its context carries (``handed_note_ids``); what the chat said on other models since comes cut short
    # (``meanwhile``), and a new session has heard none (the pre-push reviews of item 1055).
    heard: list[Message] = [] if chat is None else earlier[: chat.seen]
    carried: str = "\n".join(message.content for message in heard if message.role == "user")
    goal: Message = await message_with_attachments(
        runtime, run.thread_id, run.id, await _opening_of(agent, ref, info), agent.opening.files, info.vision
    )
    # What the goal check and the lessons read of the run, as Dot's own loop keeps it (item 1694).
    record: _Record = _Record(goal=goal, history=[goal], facts=new_facts(), ledger=new_ledger(run.goal))
    door: Door = new_door(
        CLAUDE_DOOR,
        door_specs(_offered_specs(run, tools)),
        loaded_tools(tools.tools),
        partial(
            _watched_call,
            quiet,
            record,
            agent,
            partial(
                bridged_outcome,
                _noting_outward(quiet, tools),
                await tool_context(agent, ref, info, frozenset(tools.tools), quiet.owner),
                agent.gate,
            ),
        ),
    )
    # The notes go after the owner's words as a message of their own, as Dot's own loop stores them, so that no one
    # reads them as the owner's (a group chat's members read the lead's chat); the program gets both in one message.
    facts: PromptFacts = await prompt_facts(agent, ref, frozenset(tools.tools))
    inputs: PromptInputs = await run_prompt_inputs(agent, facts, door.specs)
    noted: Message = user_message(await handed_notes(agent, info, carried, inputs, turn_skills(facts)), ())
    await runtime.store.append_messages(run.thread_id, [goal, noted])
    prompt: str = await _prompt_of(agent, door, inputs)
    # Before the program starts (item 1400): what Dot itself left out of the run's tools is known already. Outside the
    # try below, whose end lets the chat's session go: the program never touched it.
    lacking: tuple[str, ...] = missing_tools(needed, tools.tools)
    if lacking:
        raise tools_not_given(lacking, run.mode, await _agent_leaving_out(agent, bounds, lacking), {"run_id": run.id})
    # The chat's session is kept only when this run ended clean and its count of what the session saw was written:
    # a run that failed, was stopped by the owner or was cut at its limits after the program took its messages leaves
    # that count behind, and resumed, the session would hear them again (a stopped goal read as a new ask). Whatever
    # ended it, the next run then starts a new session, which hears the chat with its first message.
    kept: bool = False
    try:
        async with provider.bridge.opened(door) as url, _private_folder() as folder:
            config_file: Path = await asyncio.to_thread(write_mcp_config, folder, mcp_config(door, url))
            prompt_file: Path = await asyncio.to_thread(write_prompt, folder, prompt)
            launch: _Launch = _Launch(
                agent=agent,
                program=program,
                ref=ref,
                info=info,
                prompt_file=prompt_file,
                config_file=config_file,
                door=door.name,
                folder=folder,
                queue=queue,
                quiet=quiet,
                silence_s=provider.config.silence_s,
            )
            pictures: tuple[ImageData, ...] = await asyncio.to_thread(_images, agent.opening.files, info.vision)
            current: _Program = await launch.start(None if chat is None else chat.session_id, continuing=False)
            follow: _Follow = _Follow(
                agent=agent,
                stdin=current.stdin,
                door=door.name,
                vision=info.vision,
                queue=queue,
                started=started,
                limits=limits_of(runtime.config.agent, run.mode),
                quiet=quiet,
                needed=needed,
                record=record,
                checker=_checker_of(agent),
            )
            pumps: tuple[asyncio.Task[None], ...] = (
                asyncio.create_task(_pump_inbox(agent.inbox, queue), name="claude-code-inbox"),
                asyncio.create_task(_pump_stop(agent.cancel, queue), name="claude-code-stop"),
            )
            opening: str = f"{goal.content}\n\n{noted.content}"
            cuts: _Cuts = _Cuts(
                agent=agent,
                follow=follow,
                launch=launch,
                allowed=provider.config.continue_cut,
                earlier=earlier,
                opening=opening,
                pictures=pictures,
            )
            clean: bool = False
            try:
                outgoing: list[_Pending] = [
                    _Pending(
                        text=meanwhile(opening, earlier[0 if chat is None else chat.seen :]),
                        images=pictures,
                        keep=True,
                        own=False,
                    )
                ]
                # A program that is cut goes on in place (item 1581): the same run, the same session, the same events.
                while True:
                    try:
                        for one in outgoing:
                            await follow.send(one.text, one.images, one.keep, one.own)
                        answer: str = await follow.follow()
                        break
                    except ClaudeCodeError as error:
                        gone: tuple[_Program, list[_Pending]] | None = await cuts.after(error, current)
                        if gone is None:
                            answer = follow.answer
                            break
                        current, outgoing = gone
                clean = ended_clean(follow.over, follow.finish) and not cuts.at_finish
                await _close_input(current.stdin)
                if clean:
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(_EXIT_WAIT_S):
                            await current.process.wait()
            finally:
                for task in (*pumps, *current.pumps):
                    task.cancel()
                await asyncio.gather(*pumps, *current.pumps, return_exceptions=True)
                # What the owner sent that the program never took in (a message in the run's last seconds) goes back
                # to the run's inbox, where Dot keeps it as it keeps one a run of its own loop did not read (item 1076).
                # Ahead of what reached the inbox meanwhile, so that they keep the order they were sent in.
                later: list[UserInput] = [agent.inbox.get_nowait() for _ in range(agent.inbox.qsize())]
                while not queue.empty():
                    unread: _Item = queue.get_nowait()
                    if isinstance(unread, UserInput):
                        agent.inbox.put_nowait(unread)
                for sent_later in later:
                    agent.inbox.put_nowait(sent_later)
                await asyncio.shield(_end_program(current.process))
                current.tail.cancel()
                await asyncio.gather(current.tail, return_exceptions=True)
                await let_go(runtime, run.id, ())
        # The chat keeps the run's answer, its finish's when it called one (the pre-push review: the reply after a
        # finish is often empty, and a later run read nothing of the answer).
        closing: str = answer if quiet.finished is None else quiet.finished.answer
        await runtime.store.append_messages(run.thread_id, [assistant_message(closing, None, ())])
        if clean and follow.session_id:
            await asyncio.to_thread(
                write_chat,
                chat_path(run.thread_id),
                # What the session took in of the chat: what was there, the goal and its notes, the answer, and the
                # messages added while it worked.
                ChatSession(session_id=follow.session_id, seen=len(earlier) + 3 + follow.added),
            )
            kept = True
    finally:
        if chat is not None and not kept:
            await asyncio.to_thread(forget_chat, run.thread_id)
    log.info("claude_code_run_ended", run_id=run.id, session=follow.session_id, steps=follow.steps, clean=clean)
    # Its lessons are drawn as a run of Dot's own loop's are (item 1694), from the record of its calls.
    return run_end(agent, record.history, record.goal, _outcome(follow, answer, run.goal, quiet.finished), record.facts)


async def _stderr_now(tail: asyncio.Task[str], process: asyncio.subprocess.Process) -> str:
    """What the program wrote to its error stream, once it ended (it is ended first, so that the stream closes)."""
    await asyncio.shield(_end_program(process))
    try:
        async with asyncio.timeout(_EXIT_WAIT_S):
            return await asyncio.shield(tail)
    except TimeoutError:
        return ""


async def _masked(text: str) -> str:
    """``text`` of a program that ended, what it printed or said, masked as everything Dot logs or stores is (items 1580
    and 1581): a program may print a key in the words of its failure, and the event of a continuation carries them."""
    known: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
    return redact_known(text, known)


async def _masked_stderr(current: _Program) -> str:
    """What the program wrote to its error stream, once it ended (:func:`_stderr_now`), masked (:func:`_masked`)."""
    return await _masked(await _stderr_now(current.tail, current.process))


async def _cut_of(follow: _Follow, current: _Program) -> ProgramExit:
    """How the program ended that ended before its result (item 1580): its exit code, which it is given a moment to have
    (:func:`exit_seen`; a program still running then is one Dot ends itself, with no exit of its own to report), the
    end of its error stream, whether it had said what the model has, the steps it took and the seconds since its last
    line. One log line says it (``claude_code_program_cut``), and a program that ended by itself is counted for a mass
    cut (``claude_code_programs_ended_together``), unless Dot had ended it already."""
    silent_s: float = round(time.monotonic() - follow.quiet.wrote, 1)
    returncode: int | None = await exit_seen(current.process)
    ours: bool = ended_by_dot(current.process)
    stderr: str = await _masked_stderr(current)
    program: ProgramExit = ProgramExit(
        run_id=follow.agent.run.id,
        returncode=returncode,
        stderr=stderr,
        inited=follow.inited,
        steps=follow.steps,
        silent_s=silent_s,
        session_id=follow.session_id,
        at=time.time(),
    )
    note_cut(program, ours)
    return program
