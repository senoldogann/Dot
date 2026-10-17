"""Continuity across compaction: what a run must not lose when its earlier conversation is folded into a summary.

The loop keeps a :class:`RunLedger` of facts it observed itself (the goal word for word, the user's instructions, the
tool actions that ran and how they ended, the files and addresses they touched). The ledger never comes from a model,
so it cannot drift: every summary message carries it pinned above the model's own summary, and every new compaction
pins it again from the loop's state instead of summarising the previous pin.

:func:`check_continuity` validates a model's summary against the history it replaced: the sections it must have, and
the identifiers (paths, addresses, ids) it names that appear nowhere in that history or the ledger, which are flagged as
unverified instead of being trusted. When no model summary can be had, :func:`mechanical_summary` condenses the history
without a model. Every function here is pure.
"""

import re
from collections.abc import Collection, Sequence

from dotagent.agent.plan import PlanPayload, plan_lines
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.llm.types import Message, ToolCall

CONTINUITY_MARK: str = "[Continuity note"
SUMMARY_HEADING: str = "## Summary of the earlier conversation"
SUMMARY_SECTIONS: tuple[str, ...] = ("GOAL", "DONE", "STATE", "PROBLEMS", "NEXT")

# Tools whose calls say nothing about the work itself.
_BOOKKEEPING_TOOLS: frozenset[str] = frozenset({"update_plan", "mark_plan_step", "finish", "notify"})
# Argument names whose values are files or addresses a run touched.
_TOUCH_KEYS: frozenset[str] = frozenset({"path", "paths", "url", "cwd", "file", "destination"})
_LEDGER_ACTIONS: int = 40
_LEDGER_TOUCHED: int = 60
_LEDGER_INSTRUCTIONS: int = 20
_ACTION_ARGS_CHARS: int = 160
_ACTION_RESULT_CHARS: int = 160
_PINNED_TEXT_CHARS: int = 4000
_REMINDER_ACTIONS: int = 5
_IDENTIFIERS_SHOWN: int = 12

_IDENTIFIER: re.Pattern[str] = re.compile(
    r"https?://[^\s)\]>\"'`]+"  # addresses
    r"|(?:~|/Users|/tmp|/private|/var|/opt|/Applications|/Library)(?:/[\w.@+\-]+)+"  # absolute paths
    r"|\b(?:run|thr|mem|sch|apv|rule)_[a-z0-9]{6,}_[a-z0-9]{4,}\b"  # Dot's own ids
)
# The ellipsis is the mark Dot's own renderings leave on a value they cut (the pinned actions, a page's text), which
# a summary copies as it is.
_TRAILING_PUNCTUATION: str = ".,;:!?…"
# A known identifier this long, lengthened by a fragment, a query or a closing slash the history did not have, is that
# address (``_lengthens``); a shorter known identifier says little about what follows it.
_LONG_IDENTIFIER_CHARS: int = 24
# Where a Mac's paths have a second spelling: ``/tmp`` and ``/var`` are ``/private/tmp`` and ``/private/var``.
_PRIVATE_PREFIXES: tuple[tuple[str, str], ...] = (("/tmp/", "/private/tmp/"), ("/var/", "/private/var/"))


class LedgerAction(FrozenModel):
    """One tool call that ran: what it was and how it ended."""

    tool: str
    args: str
    ok: bool
    result: str


class RunLedger(FrozenModel):
    """What the loop observed during a run, independent of any model. ``actions`` keeps the newest ones;
    ``action_count`` counts all of them."""

    goal: str
    instructions: tuple[str, ...]
    actions: tuple[LedgerAction, ...]
    action_count: int
    touched: tuple[str, ...]
    compactions: int


class ContinuityCheck(FrozenModel):
    """The outcome of validating a summary. ``unverified`` lists identifiers the summary names that the history it
    replaced never mentioned; ``missing_sections`` the required headings it lacks."""

    ok: bool
    missing_sections: tuple[str, ...]
    unverified: tuple[str, ...]
    summary_chars: int


def new_ledger(goal: str) -> RunLedger:
    return RunLedger(goal=goal, instructions=(), actions=(), action_count=0, touched=(), compactions=0)


def with_instruction(ledger: RunLedger, text: str) -> RunLedger:
    """The ledger after the user told the running agent ``text``."""
    return ledger.model_copy(update={"instructions": (*ledger.instructions, text)[-_LEDGER_INSTRUCTIONS:]})


def with_compaction(ledger: RunLedger) -> RunLedger:
    return ledger.model_copy(update={"compactions": ledger.compactions + 1})


def _touched_values(call: ToolCall) -> list[str]:
    values: list[str] = []
    for key, value in call.arguments.items():
        if key not in _TOUCH_KEYS:
            continue
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, list):
            values.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
    return values


def _first_line(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def with_actions(ledger: RunLedger, done: Sequence[tuple[ToolCall, bool, str]]) -> RunLedger:
    """The ledger after the tool calls in ``done`` ran: ``(call, succeeded, result text)`` each."""
    work: list[tuple[ToolCall, bool, str]] = [item for item in done if item[0].name not in _BOOKKEEPING_TOOLS]
    if not work:
        return ledger
    actions: list[LedgerAction] = [
        LedgerAction(
            tool=call.name,
            args=_first_line(dumps_compact(call.arguments), _ACTION_ARGS_CHARS),
            ok=succeeded,
            result=_first_line(text, _ACTION_RESULT_CHARS),
        )
        for call, succeeded, text in work
    ]
    touched: dict[str, None] = dict.fromkeys(ledger.touched)
    for call, succeeded, _ in work:
        if succeeded:
            touched.update(dict.fromkeys(_touched_values(call)))
    return ledger.model_copy(
        update={
            "actions": (*ledger.actions, *actions)[-_LEDGER_ACTIONS:],
            "action_count": ledger.action_count + len(actions),
            "touched": tuple(touched)[-_LEDGER_TOUCHED:],
        }
    )


def identifiers(text: str) -> set[str]:
    """Paths, addresses and Dot ids named in ``text``, without trailing punctuation."""
    return {match.rstrip(_TRAILING_PUNCTUATION) for match in _IDENTIFIER.findall(text)}


def history_text(messages: Sequence[Message]) -> str:
    """Everything the messages say, untruncated: the reference a summary is checked against."""
    parts: list[str] = []
    for message in messages:
        parts.append(message.content)
        parts.extend(dumps_compact(call.arguments) for call in message.tool_calls)
    return "\n".join(parts)


def summary_part(text: str) -> str:
    """The model-written part of a continuity note; other text is returned unchanged.

    A new compaction summarises only this part of an earlier note: the pinned part is written again from the ledger.
    """
    if not text.startswith(CONTINUITY_MARK) or SUMMARY_HEADING not in text:
        return text
    after: str = text.split(SUMMARY_HEADING, 1)[1]
    body: str = after.split("\n", 1)[1] if "\n" in after else ""
    return body.split("\n## Consistency check", 1)[0].strip()


def _heading(section: str) -> re.Pattern[str]:
    """Where ``section`` opens: its name at the start of a line, after marks of Markdown or a number, before a colon,
    a dash, a bracket or the line's end (``GOAL:``, ``## Done``, ``**STATE** -``, ``1. NEXT``), or its name in capitals
    and a colon anywhere (``GOAL: x. DONE: y``)."""
    return re.compile(rf"^\W*(?:\d+[.)]\s*)?\W*(?i:{section})\W*?(?:[:\-–—(]|$)|\b{section}\s*:", re.MULTILINE)


_HEADINGS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((section, _heading(section)) for section in SUMMARY_SECTIONS)


def missing_sections(summary: str) -> tuple[str, ...]:
    """The required headings ``summary`` lacks: a section counts only where a line opens it, not where its name is
    a word of the text ("the state of the report")."""
    return tuple(section for section, heading in _HEADINGS if heading.search(summary) is None)


def with_empty_sections(summary: str, missing: Sequence[str]) -> str:
    """``summary`` with an empty heading for each section of ``missing`` (review O9): a summary that still lacks one
    after it was asked for again keeps every heading the steps after it read, empty where it said nothing."""
    return "\n".join([summary.rstrip(), *(f"{section}:" for section in missing)])


def spellings(name: str, home: str) -> tuple[str, ...]:
    """``name`` and the other ways a Mac writes the same path: ``~`` for the home folder ``home`` and ``home`` for
    ``~``, and ``/tmp`` or ``/var`` with or without ``/private`` before them. An address or an id is only itself."""
    forms: list[str] = [name]
    if name.startswith("~/"):
        forms.append(home + name[1:])
    elif name.startswith(f"{home}/"):
        forms.append("~" + name[len(home) :])
    for short, full in _PRIVATE_PREFIXES:
        for form in tuple(forms):
            if form.startswith(short):
                forms.append(full + form[len(short) :])
            elif form.startswith(full):
                forms.append(short + form[len(full) :])
    return tuple(forms)


def _lengthens(form: str, other: str) -> bool:
    """Whether ``form`` is the known identifier ``other`` with what an address adds without being another one: a
    fragment, a query or a closing slash. A path or a page under it is another one."""
    return form.startswith((f"{other}#", f"{other}?")) or form == f"{other}/"


def _is_known(name: str, known: str, known_names: Collection[str], home: str) -> bool:
    """Whether the history holds ``name``, as it is written or as the Mac also writes it (:func:`spellings`): whole,
    cut short (a cut identifier is a part of its whole), or as a long identifier it lengthens (:func:`_lengthens`). A
    path or a page under a known one is not known by that."""
    return any(
        form in known or any(len(other) >= _LONG_IDENTIFIER_CHARS and _lengthens(form, other) for other in known_names)
        for form in spellings(name, home)
    )


def check_continuity(summary: str, reference: str, ledger: RunLedger, home: str) -> ContinuityCheck:
    """Validate a model's summary against ``reference`` (the full text of the history it replaces) and the ledger.

    An identifier counts as known when the history holds it, whole or cut short (a trailing ellipsis, the mark of a
    value Dot cut, is not part of it), or when it is a known identifier of at least 24 characters with a fragment, a
    query or a closing slash the history did not have; a path or a page under a known one does not count by that. A
    path counts in any of its spellings: ``~`` for ``home``, the home folder, and ``/tmp`` or ``/var`` with or without
    ``/private``.
    """
    missing: tuple[str, ...] = missing_sections(summary)
    known: str = "\n".join([reference, ledger.goal, *ledger.instructions, *ledger.touched])
    known_names: set[str] = identifiers(known)
    unverified: tuple[str, ...] = tuple(
        sorted(name for name in identifiers(summary) if not _is_known(name, known, known_names, home))
    )
    return ContinuityCheck(
        ok=bool(summary.strip()) and not missing and not unverified,
        missing_sections=missing,
        unverified=unverified,
        summary_chars=len(summary),
    )


def mechanical_summary(transcript: str, reason: str, max_chars: int) -> str:
    """A summary written without a model: the newest ``max_chars`` characters of the transcript, said to be one."""
    tail: str = transcript[max(0, len(transcript) - max_chars) :]
    return (
        f"No model summary could be written ({reason}). The newest part of the earlier conversation follows as it was; "
        f"rely on the pinned facts above for what came before it.\n{tail}"
    )


def _action_line(action: LedgerAction) -> str:
    outcome: str = "ok" if action.ok else "FAILED"
    return f"- {action.tool} {action.args} → {outcome}: {action.result}"


def _task_sections(ledger: RunLedger, plan: PlanPayload) -> list[str]:
    """The task word for word, what the user said during the run and the plan with its targets."""
    sections: list[str] = [
        "## Task (the user's words, verbatim)",
        truncate_middle(ledger.goal, _PINNED_TEXT_CHARS),
    ]
    if ledger.instructions:
        sections.append("## What the user told you during this run (oldest first; these still apply)")
        sections.extend(f"- {truncate_middle(text, 600)}" for text in ledger.instructions)
    if plan.steps:
        sections.append("## Your plan as last recorded")
        sections.append(plan_lines(plan))
    return sections


def _newest_fitting(lines: Sequence[str], max_chars: int) -> list[str]:
    """The newest of ``lines`` (given oldest first) that fit together in ``max_chars`` characters, oldest first."""
    kept: list[str] = []
    used: int = 0
    for line in reversed(lines):
        used += len(line) + 1
        if used > max_chars:
            break
        kept.append(line)
    return kept[::-1]


def _unlisted(count: int) -> str:
    return f" ({count} earlier ones not listed)" if count > 0 else ""


def render_pinned(ledger: RunLedger, plan: PlanPayload, max_chars: int) -> str:
    """The facts of the ledger as the model reads them in a continuity note, in about ``max_chars`` characters (the
    headings of the lists come on top).

    The task, the user's instructions and the plan are always there in full. Of the files and addresses touched and of
    the tool actions, the newest that fit are listed (the files and addresses take up to a third of the room left), and
    the headings say how many earlier ones are not.
    """
    sections: list[str] = _task_sections(ledger, plan)
    room: int = max_chars - len("\n".join(sections))
    touched: list[str] = _newest_fitting([f"- {item}" for item in ledger.touched], room // 3)
    actions: list[str] = _newest_fitting(
        [_action_line(action) for action in ledger.actions], room - len("\n".join(touched))
    )
    if ledger.actions:
        unlisted: str = _unlisted(ledger.action_count - len(actions))
        sections.append(f"## Tool actions that already ran, recorded by the system (oldest first{unlisted})")
        sections.extend(actions)
    if ledger.touched:
        sections.append(f"## Files and addresses touched so far{_unlisted(len(ledger.touched) - len(touched))}")
        sections.extend(touched)
    return "\n".join(sections)


def reminder_note(ledger: RunLedger, plan: PlanPayload) -> str:
    """The note that brings the goal back to the end of a long run's context: the task word for word, what the user
    said since, the plan and the newest actions. It asks for nothing new."""
    sections: list[str] = [
        "[system note] Reminder, nothing new is asked: keep working on the same task.",
        *_task_sections(ledger, plan),
    ]
    if ledger.actions:
        sections.append("## Your newest tool actions (oldest first)")
        sections.extend(_action_line(action) for action in ledger.actions[-_REMINDER_ACTIONS:])
    sections.append(
        "Continue with the next step towards the task. Call finish with your complete final answer once it is reached."
    )
    return "\n".join(sections)


def _check_lines(check: ContinuityCheck) -> list[str]:
    lines: list[str] = []
    if check.unverified:
        shown: str = ", ".join(check.unverified[:_IDENTIFIERS_SHOWN])
        lines.append(
            f"- The summary names identifiers that appear nowhere in the earlier conversation: {shown}. Treat them "
            "as unverified and check before relying on them."
        )
    if check.missing_sections:
        lines.append(
            f"- The summary lacks the sections {', '.join(check.missing_sections)}; rely on the pinned facts above."
        )
    return lines


def continuity_note(
    ledger: RunLedger,
    plan: PlanPayload,
    pinned_chars: int,
    summary: str,
    author: str,
    check: ContinuityCheck | None,
) -> str:
    """The message that replaces the earlier conversation: pinned facts in about ``pinned_chars`` characters, the
    summary and what its check found.

    ``check`` is ``None`` for a summary written without a model, which has nothing to validate.
    """
    header: str = (
        f"{CONTINUITY_MARK} - compaction {ledger.compactions} of this run. This is the SAME run going on, not a new "
        "session. The earlier conversation was compacted into the facts below. Do not restart the task and do not "
        "repeat actions listed as already run; continue from where the summary's NEXT points.]"
    )
    parts: list[str] = [
        header,
        render_pinned(ledger, plan, pinned_chars),
        f"{SUMMARY_HEADING} ({author})",
        summary.strip(),
    ]
    lines: list[str] = [] if check is None else _check_lines(check)
    if lines:
        parts.append("## Consistency check")
        parts.extend(lines)
    return "\n\n".join(parts)
