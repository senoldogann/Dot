"""Learned skills: how a run of the agent's own did work that went well, kept as a skill for the runs after it
(ROADMAP item 92; the module keeps its first name, playbooks).

A run is distilled when ``[agent] playbooks`` is on, it is a task run of its own (a delegated run's work is its
parent's; an eval run is not asked), it ended done (its answer was checked, or its goal check found the outcome
achieved) and it took at least ``[agent] skill_min_steps`` acting steps (every tool call but the ones that plan, ask,
remember, consult, read a skill, find help or finish: :func:`acting_steps`). Once the run has ended, the review model
reads a digest of those steps (:func:`acting_digest`) and writes the skill
(:func:`dotagent.agent.review.playbook_writer_for`): the kind of work in a few words, the steps that worked, the
parameters a later run fills in, and a one-sentence description. The kind names the folder, ``learned-<kind>/SKILL.md``
under ``<home>/playbooks`` (:func:`render_playbook`), with the evidence (the runs, the goal, the date, the acting steps)
and a ``playbook.json`` that keeps the kind and counts the use. The writer is shown the kinds of the skills the run's
owner keeps (:func:`kinds_shown`) and names the run's work by one of them, word for word, when it is that work; a kind
written the same as one of them, or close to one by meaning, updates that skill instead of writing another, its runs
appended to the evidence. A run updates its owner's skills alone (:func:`owned`): another owner's skill of the same name
keeps its folder, and the new one takes the next free name (:func:`free_name`). The skills catalog reads the folder like
any skills directory, so later runs see the description under Skills, told it is advice from Dot's own work, and read
the rest with the ``skill`` tool.

A run that read content third parties can author (as the lessons' reflection reads it, from the store) is distilled
from its own steps alone (:func:`own_steps_digest`, the answer left out), and its skill's record says so (``tainted``,
review Y7). A run is offered such a skill, or one whose record is older than the mark, with every line the review model
wrote that names a web address, an e-mail address or an account, runs a command or reaches a path in the home folder
withheld, and a warning first (:func:`presented`): a page cannot turn its text into a step of a later run. The file
keeps the steps: a later run of the kind that read no such content rewrites them and clears the mark, and so does the
owner (``dotx playbooks trust``, :func:`trust_playbook`).

Every run that ended done or failed records its use of learned skills (the ``skill`` calls that named one): a skill
read by :data:`MAX_FAILURES` runs in a row that then failed (none ended done between them) is retired, moved under
``.retired`` where the catalog does not look, and no longer offered. Every function here is pure except
:func:`learn_playbook`, which reads the store, the embedder and the review model and writes the folder, and the small
file helpers it calls.

The bar (item 585): a skill is written only from a task run whose steps the review model judges a procedure likely to
recur (``recurs``, one more field of its answer), and never from a chat-mode run or from a run that changed Dot itself
(:func:`touched_dot`: a step the policy flagged ``self_change``, ``secret_access`` or ``protected_read``, or one that
named a place Dot guards or loads, Dot's own program or the daemon's address, in its words or, for an MCP call, in its
arguments, in a step of the run or of a run it delegated; the policy's own classification is read, not changed). A
rewrite, a restore or a merge keeps the file it replaces whole as a version under ``.versions``
(:func:`archive_version`; the record's ``versions`` say how each came to be), so that a version can be put back
(:mod:`dotagent.agent.skill_history`). A skill no run read for ``[agent] skill_rest_days`` rests
(:func:`skill_resting`): it is not offered, and the owner can wake it or pin it. A read is dated where it happens
(:func:`date_reads`: the ``skill`` tool, a skill picked in the composer), whatever the run's mode, parent or end; the
counts of uses (:func:`record_use`) judge how runs ended.

The files are written whole, a new skill's folder under a hidden name before it takes its own, and every change of the
playbooks holds their lock (``.lock`` in the folder), so runs that end at once, the API and another process (``dotx
run`` beside the daemon) lose none of each other's changes, and a listing never meets a record or a folder half
written.
"""

import asyncio
import fcntl
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import JsonValue, ValidationError

from dotagent.agent.learning_health import LEARNED, SKIPPED, LearningOutcome, failed_with
from dotagent.agent.review import (
    PlaybookDraft,
    PlaybookReply,
    PlaybookRequest,
    PlaybookWriter,
    playbook_writer_for,
    shorten_reason,
)
from dotagent.config import AppConfig
from dotagent.core.errors import DotError, InvalidValueError, NotFoundError, SkillError
from dotagent.core.files import write_whole
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.learned_text import (
    OWNER_MARK,
    finds_email,
    finds_handle,
    finds_home_path,
    finds_link,
    hidden_character,
    wears,
)
from dotagent.core.logs import get_logger
from dotagent.core.paths import playbooks_dir
from dotagent.core.text import redact_known, redact_secrets, truncate_tail
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.llm.opencode import OPENCODE_PROVIDER
from dotagent.llm.router import parse_model_ref
from dotagent.memory.ranking import Similar, Vector, most_similar
from dotagent.policy.types import OUTSIDE_CHANNELS, TAINTING_KINDS
from dotagent.secrets import known_secrets
from dotagent.skills.catalog import (
    PLAYBOOK_PREFIX,
    PLAYBOOK_PREFIXES,
    PLAYBOOK_SOURCE,
    TAUGHT_PREFIX,
    playbook_root,
    read_skill,
)
from dotagent.skills.parse import parse_skill, split_frontmatter
from dotagent.skills.types import SKILL_FILE, SKILL_TOOL, Skill, SkillCatalog
from dotagent.store.models import EventRecord, ResultStep, RunRecord, RunStatus

if TYPE_CHECKING:
    # Only named: the runtime's tools import this module (find_help asks it for the skills a run is offered).
    from dotagent.runtime import Runtime

log = get_logger(__name__)

# The prefixes of the names of the skills in the playbook folder (:data:`dotagent.skills.catalog.PLAYBOOK_PREFIXES`):
# ``taught-`` is a skill the owner taught by showing the task once (item 423), Dot's to offer and read and the owner's
# to keep; ``playbook-`` the folders written before item 92, which a run may still read.
_FOLDER_NAME: re.Pattern[str] = re.compile(
    rf"(?:{'|'.join(re.escape(prefix) for prefix in PLAYBOOK_PREFIXES)})[a-z0-9-]*"
)
META_FILE: str = "playbook.json"
RETIRED_DIR: str = ".retired"
# The folder inside a skill's own where the versions it replaced are kept, one file each (item 585). Hidden, so that the
# files the ``skill`` tool lists to a run leave it out.
VERSIONS_DIR: str = ".versions"
_LOCK_FILE: str = ".lock"
# The hidden folder a new skill is written in before it takes its name (the skills catalog leaves hidden folders alone).
_STAGING_PREFIX: str = ".writing-"
_PRIVATE_FILE_MODE: int = 0o600
PLAYBOOK_WRITTEN: str = "playbook_written"
PLAYBOOK_FAILED: str = "playbook_failed"
PLAYBOOK_RETIRED: str = "playbook_retired"
# A playbook that this many runs read and then failed is retired.
MAX_FAILURES: int = 2
# How many of the newest tool calls and results of a run its skill work reads (item 557): the steps that ended a long
# run, not the first part of it.
_EVENTS_READ: int = 2000
_DIGEST_STEPS: int = 80
_ARGS_CHARS: int = 200
_RESULT_CHARS: int = 160
# The kind of work names the folder: its letters and digits with single hyphens between.
_UNSAFE: re.Pattern[str] = re.compile(r"[^a-z0-9]+")
_SLUG_CHARS: int = 50
# A frontmatter value stays on one line: the loader reads a folded one too, but a line is what the file shows.
_FRONTMATTER_WIDTH: int = 1 << 16
# The tool calls that are not acting: they plan, ask, remember, consult, read a skill, find help or end the run.
_NOT_ACTING: frozenset[str] = frozenset(
    {
        "finish",
        "ask_user",
        "use_tools",
        "skill",
        "find_help",
        "consult",
        "delegate",
        "message_agent",
        "update_plan",
        "mark_plan_step",
        "write_notes",
        "memory_search",
        "memory_save",
        "memory_update",
        "memory_forget",
        "judge_items",
    }
)
# Two kinds this close in meaning are one kind: the new run updates the skill instead of writing another.
_SAME_KIND: float = 0.85
# The kinds of the skills kept that the writer is shown, so that it names a run's work by one of them when it is that
# work: the skills of the run's site first, then the newest.
_KINDS_SHOWN: int = 30
# What the writer reads for the answer of a run that read content third parties can author.
_ANSWER_LEFT_OUT: str = "(Left out: the run read content other people wrote.)"
# What withholds a line of a skill that may come from content third parties can author, when a run is offered it: what
# the one gate for learned texts refuses (a web address, an e-mail address, an account, a path in somebody's home
# folder, a character that does not print: :func:`withholds`, item 587), and what only a skill's line is asked, since
# it is a step a run follows: a command (code, a tool that runs one, the syntax of a shell, an option, a command with
# its argument), a path in the home folder and one in the Mac's own folders (item 351: /tmp and the like), and, as a
# withheld line costs little, any dotted word followed by a slash, whatever its domain.
_WITHHELD: tuple[re.Pattern[str], ...] = (
    # A dotted word and a slash: read from the start of the word, once, so that a long run of labels costs a pass.
    re.compile(r"(?<![a-z0-9-])(?<![a-z0-9-]\.)(?:[a-z0-9-]++\.)++[a-z]{2,}+/", re.IGNORECASE),
    re.compile(r"`|\b(?:shell|terminal|apple_?script|osascript|browser_eval|command[ -]line)", re.IGNORECASE),
    re.compile(r"\$\(|\$\{|\$[A-Z_]{2,}\b|\|\s*\S|&&|(?:^|\s)--?[A-Za-z]"),
    re.compile(
        r"\b(?:sudo|curl|wget|bash|zsh|sh|rm|rmdir|mv|cp|chmod|chown|ssh|scp|rsync|nc|dd|eval|exec|launchctl|pkill"
        r"|killall|xattr|crontab|cat|ls|echo|grep|python3?|pip3?|npm|npx|uvx?|brew|git|docker)\s+\S"
    ),
    re.compile(r"(?<![\w/.~])~(?:[A-Za-z_][\w.-]*)?(?=/|\s|$|[)\"'.,;:])|/Users/"),
    re.compile(r"(?<![\w.])/(?:tmp|private|var|etc|opt|usr|Library|System|Volumes|Applications)/"),
)
# The longest line of a skill that is read for what it withholds: a step is a line of a hundred characters or so, and
# a longer one is withheld unread, so that no line can hold the loop that reads it (item 587).
_MAX_LINE: int = 2000
# The sections of a learned skill's instructions that the review model wrote (the others are Dot's own words).
_HEADING: re.Pattern[str] = re.compile(r"##\s+(.*?)\s*")
_STEPS: str = "Steps"
_EVIDENCE: str = "Evidence"
_LIST_ITEM: re.Pattern[str] = re.compile(r"\s*(?:\d+\.|[-*])\s+")
_FENCE: str = "```"
# Why a line is withheld, by what the skill's record says of its runs (``tainted``), and the warning the skill starts
# with.
_THIRD_PARTY: str = "learned from third-party content"
_UNKNOWN_ORIGIN: str = "its origin is unknown"
_HIDDEN_CHARACTER: str = "it holds a character that does not print"
_WITHHELD_LINES: str = (
    "Its lines that name a web address, an e-mail address or an account, run a command or reach a path in the home "
    "folder or in the Mac's own folders are withheld; work those steps out from the task in front of you."
)
_THIRD_PARTY_WARNING: str = f"Warning: learned from a run that read third-party content. {_WITHHELD_LINES}"
_HIDDEN_WARNING: str = (
    "Warning: a line of this skill holds a character that does not print, which nobody could read. It is withheld; "
    "work that step out from the task in front of you."
)
_UNKNOWN_WARNING: str = (
    "Warning: learned before Dot recorded whether a run read third-party content, so it is offered as if one did. "
    f"{_WITHHELD_LINES}"
)
# The name a skill is written under when the run acted on no web site or application.
NO_SITE: str = "this Mac"
# The kinds of action that only observe: a step of one of them changes nothing, so what it names is not looked at when
# the run is asked whether it changed Dot itself (:func:`touched_dot`); a step of any other kind is.
_OBSERVING: frozenset[str] = frozenset(
    {"read", "browser_read", "desktop_observe", "memory_read", "mcp_read", "meta", "delegate"}
)
# Dot's own program, as a command line names it: ``dotx``, or the package ``dotagent`` (``python -m dotagent``), in any
# letter case: a Mac's volume ignores it.
_DOT_PROGRAM: re.Pattern[str] = re.compile(r"(?<![\w.-])(?:dotx|dotagent)(?![\w-])", re.IGNORECASE)
# What the policy stores on a step that reached a place it guards, besides ``self_change``: a secret or a protected
# location (``secret_access``, which is also how it flags a command that changes Dot's own files), and a read it could
# not tell from one of a protected location (``protected_read``). Read by :func:`touched_dot` whatever the step's words.
_GUARD_FLAGS: tuple[str, ...] = ("secret_access", "protected_read")
_STEP_CHARS: int = 160


class ActingStep(FrozenModel):
    """One acting tool call of a run, as the digest shows it."""

    tool: str
    arguments: str
    ok: bool
    result: str


class Playbook(FrozenModel):
    """A learned skill as one run writes it: its skill name, the kind of work, the host or application it acted on
    most (:data:`NO_SITE` for none), what the review model wrote and the evidence of the run it comes from, which
    read content third parties can author when ``tainted``."""

    name: str
    kind: str
    host: str
    draft: PlaybookDraft
    run_id: str
    agent_id: str | None
    goal: str
    written_at: float
    acting_steps: int
    tainted: bool
    # A skill the owner taught by showing the task once (item 423) rather than one Dot learned from its own work: its
    # own header, no run behind it, and never retired for failures.
    taught: bool = False


type VersionChange = Literal["learned", "relearned", "taught", "restored", "merged", "revised"]


class SkillVersion(FrozenModel):
    """One version of a learned skill (item 585): its number, when it was made, the run that made it (``None`` for the
    owner's own work), how it came to be and whether its steps carry the mark of content third parties can author, as
    the record had it while the version was the skill's (``None``: of unknown origin, offered as marked). ``learned`` is
    the first version of a run's, ``relearned`` a later run of the kind rewriting it, ``taught`` the owner's recording,
    ``restored`` an earlier version (``from_version``) put back, ``merged`` the steps of two skills (``merged`` names
    the ones taken in) and ``revised`` the steps changed at the owner's ``words``."""

    n: int
    at: float
    run_id: str | None
    change: VersionChange
    tainted: bool | None
    acting_steps: int | None = None
    from_version: int | None = None
    merged: tuple[str, ...] = ()
    words: str | None = None


class PlaybookMeta(FrozenModel):
    """``playbook.json``: the kind the skill is matched by, where it comes from (``runs``, the first one first, with
    the acting steps each took) and how it fared since. ``uses`` counts the runs that read it and ended done,
    ``failures`` the runs that read it and failed, ``failing`` those of them since the last that ended done.
    ``agent_id`` is the agent whose skill it is (item 94), ``None`` for Dot's, offered to every run; an agent's is
    offered to its runs alone until the owner marks it ``shared``. ``tainted``: the run whose steps the skill holds (the
    last of ``runs``) read content third parties can author, and the owner has not trusted it since; ``None`` in a
    record older than the mark, which is offered as one that did (:func:`presented`). ``tainted_runs`` are the runs
    among ``runs`` that read such content. The fields with a default are absent from the records written before
    them. Item 585 added: ``last_read_at`` (when a run that ended read the skill last; ``None`` for a record that counts
    uses and had no date yet), ``pinned`` (the owner's: it never rests), ``rested_at`` (when the owner, or a merge,
    put it to rest by hand), ``woken_at`` (when the owner woke it), ``merged_into`` (the skill a merge put this one
    aside for), ``version`` and ``versions`` (the notes of the versions it had, the current one last; empty in a record
    written before them, whose one version is :func:`current_note`)."""

    name: str
    kind: str
    host: str
    runs: tuple[str, ...]
    acting_steps: tuple[int, ...]
    written_at: float
    updated_at: float
    uses: int
    failures: int
    agent_id: str | None = None
    shared: bool = False
    failing: int = 0
    tainted: bool | None = None
    tainted_runs: tuple[str, ...] = ()
    # A skill the owner taught (item 423): trusted as they showed it, and never retired for the runs that failed with
    # it, which is the owner's to decide.
    taught: bool = False
    last_read_at: float | None = None
    pinned: bool = False
    rested_at: float | None = None
    woken_at: float | None = None
    merged_into: str | None = None
    version: int = 1
    versions: tuple[SkillVersion, ...] = ()


class PlaybookEntry(FrozenModel):
    """A playbook on disk, for the listing: its skill and its record, and whether it is retired."""

    skill: Skill
    meta: PlaybookMeta
    retired: bool


def slug(text: str) -> str:
    """The folder part of a skill's name for ``text`` (the kind of work): lower-case letters and digits with single
    hyphens between, at most :data:`_SLUG_CHARS` long, empty when the text has none."""
    return _UNSAFE.sub("-", text.lower()).strip("-")[:_SLUG_CHARS].strip("-")


def taught_name(kind: str) -> str:
    """The folder of a skill taught from a recording of ``kind`` (item 423)."""
    return f"{TAUGHT_PREFIX}{slug(kind)}"


def playbook_name(kind: str) -> str:
    return f"{PLAYBOOK_PREFIX}{slug(kind)}"


def playbook_case(config: AppConfig, run: RunRecord, status: RunStatus) -> RunRecord | None:
    """``run`` when its end calls for playbook work (recording the playbooks it read, writing one from it), else
    ``None``: playbooks are on, it is a task run of its own and not an eval nor one of OpenCode's agent (item 283),
    whose tools a playbook of Dot's cannot name, and it ended done or failed."""
    if not config.agent.playbooks or run.mode != "task" or run.parent_run_id is not None or run.source == EVAL_SOURCE:
        return None
    if parse_model_ref(run.model).provider == OPENCODE_PROVIDER:
        return None
    return run if status in ("done", "failed") else None


def distils(status: RunStatus, steps: int, min_steps: int) -> bool:
    """Whether a run that calls for playbook work may be distilled: it ended done (its answer checked, or its goal
    found achieved) after at least ``min_steps`` acting steps; a run that did less has nothing worth a skill."""
    return status == "done" and steps >= min_steps


def main_site(events: Sequence[EventRecord]) -> str:
    """The host or application the run acted on most, by its ``tool_result`` events; empty when it acted on none."""
    counts: dict[str, int] = {}
    for event in events:
        site: JsonValue = event.data.get("site") if event.kind == "tool_result" else None
        if isinstance(site, str) and site:
            counts[site] = counts.get(site, 0) + 1
    return "" if not counts else max(counts, key=lambda site: counts[site])


def acting_steps(events: Sequence[EventRecord]) -> tuple[ActingStep, ...]:
    """The acting tool calls of the run (every call but :data:`_NOT_ACTING`), in order, each with its arguments and
    the start of its result."""
    calls: dict[str, JsonObject] = {}
    steps: list[ActingStep] = []
    for event in events:
        call_id: JsonValue = event.data.get("call_id")
        if not isinstance(call_id, str):
            continue
        if event.kind == "tool_call":
            arguments: JsonValue = event.data.get("args")
            calls[call_id] = arguments if isinstance(arguments, dict) else {}
        elif event.kind == "tool_result" and str(event.data.get("tool", "")) not in _NOT_ACTING:
            steps.append(
                ActingStep(
                    tool=str(event.data.get("tool", "")),
                    arguments=truncate_tail(dumps_compact(calls.get(call_id, {})), _ARGS_CHARS),
                    ok=event.data.get("ok") is True,
                    result=truncate_tail(" ".join(str(event.data.get("text", "")).split()), _RESULT_CHARS),
                )
            )
    return tuple(steps)


def acting_digest(steps: Sequence[ActingStep]) -> str:
    """The acting steps as the writer reads them, secrets masked, the newest when there are many."""
    kept: Sequence[ActingStep] = steps[-_DIGEST_STEPS:]
    omitted: int = len(steps) - len(kept)
    lines: list[str] = [
        f"{number}. {step.tool} {step.arguments} -> {'ok' if step.ok else 'FAILED'}: {step.result}"
        for number, step in enumerate(kept, start=omitted + 1)
    ]
    header: str = f"({omitted} earlier steps are left out.)\n" if omitted else ""
    return redact_secrets(header + "\n".join(lines))


def own_steps_digest(steps: Sequence[ActingStep]) -> str:
    """The acting steps without what they returned, as the writer reads them for a run that read content third parties
    can author: what the agent did, never what a page or a message said, secrets masked, the newest when there are
    many."""
    kept: Sequence[ActingStep] = steps[-_DIGEST_STEPS:]
    omitted: int = len(steps) - len(kept)
    lines: list[str] = [
        f"{number}. {step.tool} {step.arguments} -> {'ok' if step.ok else 'FAILED'}"
        for number, step in enumerate(kept, start=omitted + 1)
    ]
    header: str = "(What the calls returned is left out: the run read content other people wrote.)\n" + (
        f"({omitted} earlier steps are left out.)\n" if omitted else ""
    )
    return redact_secrets(header + "\n".join(lines))


def redacted_draft(draft: PlaybookDraft, secrets: Sequence[str]) -> PlaybookDraft:
    """``draft`` with every secret masked before it is written (review Y3): the values Dot holds (``secrets``), the
    value of a key that names a secret, and keys and tokens of known forms (:func:`dotagent.core.text.redact_known`)."""
    return draft.model_copy(
        update={
            "kind": redact_known(draft.kind, secrets),
            "description": redact_known(draft.description, secrets),
            "when": redact_known(draft.when, secrets),
            "steps": [redact_known(step, secrets) for step in draft.steps],
            "parameters": [redact_known(parameter, secrets) for parameter in draft.parameters],
        }
    )


def used_playbooks(events: Sequence[EventRecord]) -> tuple[str, ...]:
    """The names of the playbooks the run read with the ``skill`` tool, each once, in order."""
    names: dict[str, None] = {}
    for event in events:
        if event.kind != "tool_call" or event.data.get("tool") != SKILL_TOOL:
            continue
        arguments: JsonValue = event.data.get("args")
        name: JsonValue = arguments.get("name") if isinstance(arguments, dict) else None
        if isinstance(name, str) and name.startswith(PLAYBOOK_PREFIXES):
            names[name] = None
    return tuple(names)


def _date(written_at: float) -> str:
    return datetime.fromtimestamp(written_at, tz=UTC).strftime("%Y-%m-%d")


def _frontmatter(name: str, description: str) -> str:
    """The YAML the skills loader reads, written by the YAML library: a description with a backslash, a quote or a
    colon in it (a regular expression, a shell line) reads back as it is, where one quoted by hand did not (a ``\\d``
    made the SKILL.md unreadable, and with it every learned skill of the daemon)."""
    return yaml.safe_dump(
        {"name": name, "description": description}, sort_keys=False, allow_unicode=True, width=_FRONTMATTER_WIDTH
    )


def flat(text: str) -> str:
    """``text`` as the file of a skill keeps it (item 585): one line, its runs of spaces and its line breaks made one
    space. What the owner is shown of a change to a skill is the words in this form, which are the ones written."""
    return " ".join(text.split())


def render_playbook(playbook: Playbook, earlier: PlaybookMeta | None) -> str:
    """The SKILL.md of a learned skill: the frontmatter the skills loader reads, then the kind, when it fits, the
    steps and the parameters a line each, and the evidence (the runs of ``earlier``, when the skill is updated, before
    this one)."""
    draft: PlaybookDraft = playbook.draft
    # Every part the review model wrote is one line, so that none of it reads as a heading of its own (item 351: a
    # "## Evidence" in the When section took the lines after it out of what a tainted skill withholds).
    description: str = flat(draft.description)
    kind: str = flat(playbook.kind)
    when: str = flat(draft.when)
    steps: str = "\n".join(f"{number}. {flat(step)}" for number, step in enumerate(draft.steps, start=1))
    parameters: str = "\n".join(f"- {flat(parameter)}" for parameter in draft.parameters) or "- none"
    where: str = f" on {playbook.host}" if playbook.host != NO_SITE else ""
    if playbook.taught:
        # A skill the owner taught (item 423): its own header, no run behind it, and the same sections a run reads.
        return (
            f"---\n{_frontmatter(playbook.name, description)}---\n\n"
            f"# {playbook.name}\n\n"
            f"Taught by the owner on {_date(playbook.written_at)}, showing the task once in Dot's browser{where}: the "
            "steps they took, written down so that a later run can do it again. It is the owner's own instruction to "
            "Dot, not advice learned from a run.\n\n"
            f"## Kind of work\n\n{kind}\n\n"
            f"## When\n\n{when}\n\n"
            f"## Steps\n\n{steps}\n\n"
            f"## Parameters\n\n{parameters}\n"
        )
    runs: list[str] = [*(earlier.runs if earlier is not None else ()), playbook.run_id]
    counts: list[int] = [*(earlier.acting_steps if earlier is not None else ()), playbook.acting_steps]
    return render_learned(
        playbook.name,
        playbook.kind,
        playbook.host,
        draft,
        playbook.goal,
        runs,
        counts,
        f", the last one verified on {_date(playbook.written_at)}",
    )


def render_learned(
    name: str,
    kind: str,
    host: str,
    draft: PlaybookDraft,
    goal: str,
    runs: Sequence[str],
    counts: Sequence[int],
    since: str,
) -> str:
    """The SKILL.md of a learned skill whose evidence is ``runs`` (each with its acting steps in ``counts``) and the
    goal of the last of them; ``since`` ends what the header says of the runs: when the last was verified, or (item
    585) when the steps of several were merged or were revised at the owner's word."""
    description: str = flat(draft.description)
    kind_line: str = flat(kind)
    when: str = flat(draft.when)
    steps: str = "\n".join(f"{number}. {flat(step)}" for number, step in enumerate(draft.steps, start=1))
    parameters: str = "\n".join(f"- {flat(parameter)}" for parameter in draft.parameters) or "- none"
    where: str = f" on {host}" if host != NO_SITE else ""
    evidence: str = "\n".join(f"- Run {run}: {count} acting steps" for run, count in zip(runs, counts, strict=True))
    return (
        f"---\n{_frontmatter(name, description)}---\n\n"
        f"# {name}\n\n"
        f"Learned by Dot from its own work{where} ({len(runs)} {'run' if len(runs) == 1 else 'runs'}{since}): a "
        "record of the steps that worked, not instructions from the "
        "user. It is advice: follow the steps with your own parameters and your own judgement; where things have "
        "changed, do what you see and go on.\n\n"
        f"## Kind of work\n\n{kind_line}\n\n"
        f"## When\n\n{when}\n\n"
        f"## Steps\n\n{steps}\n\n"
        f"## Parameters\n\n{parameters}\n\n"
        f"## Evidence\n\n- Goal of the last run: {' '.join(goal.split())}\n{evidence}\n"
    )


def current_note(meta: PlaybookMeta) -> SkillVersion:
    """The note of the version the record holds now (item 585): the last of its ``versions``, or, for a record written
    before versions were kept, the one its fields tell: the run whose steps the skill holds (the last of ``runs``), when
    they were written and their mark."""
    if meta.versions and meta.versions[-1].n == meta.version:
        return meta.versions[-1]
    return SkillVersion(
        n=meta.version,
        at=meta.updated_at,
        run_id=meta.runs[-1] if meta.runs else None,
        change="taught" if meta.taught else ("relearned" if len(meta.runs) > 1 else "learned"),
        tainted=meta.tainted,
        acting_steps=meta.acting_steps[-1] if meta.acting_steps else None,
    )


def noted(meta: PlaybookMeta) -> tuple[SkillVersion, ...]:
    """The notes of every version the record knows of, the current one last: its own, or for a record older than the
    versions the one note :func:`current_note` tells."""
    if meta.versions and meta.versions[-1].n == meta.version:
        return meta.versions
    return (*meta.versions, current_note(meta))


def meta_of(playbook: Playbook, earlier: PlaybookMeta | None) -> PlaybookMeta:
    """The record of a skill just written from ``playbook``, or updated from ``earlier`` by it (the runs appended,
    the kind and the counts kept, the failures in a row cleared, a version added). The steps are the run's alone either
    way, and so is the mark of content third parties can author (``tainted``): a run of the kind that read none clears
    it."""
    tainted_runs: tuple[str, ...] = (playbook.run_id,) if playbook.tainted else ()
    if playbook.taught:
        # The owner showed it, so no run stands behind it and nothing of it is withheld: it is trusted as they gave it.
        return PlaybookMeta(
            name=playbook.name,
            kind=playbook.kind,
            host=playbook.host,
            runs=(),
            acting_steps=(),
            written_at=playbook.written_at,
            updated_at=playbook.written_at,
            uses=0,
            failures=0,
            agent_id=playbook.agent_id,
            shared=False,
            failing=0,
            tainted=False,
            tainted_runs=(),
            taught=True,
            version=1,
            versions=(SkillVersion(n=1, at=playbook.written_at, run_id=None, change="taught", tainted=False),),
        )
    if earlier is None:
        return PlaybookMeta(
            name=playbook.name,
            kind=playbook.kind,
            host=playbook.host,
            runs=(playbook.run_id,),
            acting_steps=(playbook.acting_steps,),
            written_at=playbook.written_at,
            updated_at=playbook.written_at,
            uses=0,
            failures=0,
            agent_id=playbook.agent_id,
            shared=False,
            failing=0,
            tainted=playbook.tainted,
            tainted_runs=tainted_runs,
            version=1,
            versions=(
                SkillVersion(
                    n=1,
                    at=playbook.written_at,
                    run_id=playbook.run_id,
                    change="learned",
                    tainted=playbook.tainted,
                    acting_steps=playbook.acting_steps,
                ),
            ),
        )
    number: int = earlier.version + 1
    return earlier.model_copy(
        update={
            "runs": (*earlier.runs, playbook.run_id),
            "acting_steps": (*earlier.acting_steps, playbook.acting_steps),
            "updated_at": playbook.written_at,
            # The steps are those of a run that ended done now: the failures in a row were of the steps before them
            # (item 557), and one failure of a run that reads these would otherwise retire them.
            "failing": 0,
            "tainted": playbook.tainted,
            "tainted_runs": (*earlier.tainted_runs, *tainted_runs),
            "version": number,
            "versions": (
                *noted(earlier),
                SkillVersion(
                    n=number,
                    at=playbook.written_at,
                    run_id=playbook.run_id,
                    change="relearned",
                    tainted=playbook.tainted,
                    acting_steps=playbook.acting_steps,
                ),
            ),
        }
    )


def recorded(meta: PlaybookMeta, succeeded: bool, read_at: float | None = None) -> PlaybookMeta:
    """The record after a run that read the playbook ended: one that ended done clears the failures in a row, and the
    time ``read_at`` (when given) is the last time the skill was read if it is later than the date the record has (a
    read dates the skill where it happens, :func:`date_read`: this never takes that date back; item 585)."""
    dated: dict[str, float | None] = {} if read_at is None else {"last_read_at": max(meta.last_read_at or 0.0, read_at)}
    if succeeded:
        return meta.model_copy(update={"uses": meta.uses + 1, "failing": 0, **dated})
    return meta.model_copy(update={"failures": meta.failures + 1, "failing": meta.failing + 1, **dated})


def retires(meta: PlaybookMeta) -> bool:
    """Whether the playbook is retired: :data:`MAX_FAILURES` runs in a row read it and failed, none since ending done
    (a skill that served forty runs is not retired for two failures among them). A skill the owner taught is never
    retired: whether it still fits is theirs to say (item 423)."""
    return False if meta.taught else meta.failing >= MAX_FAILURES


def skill_resting(meta: PlaybookMeta, now: float, rest_days: int) -> bool:
    """Whether a learned or taught skill rests (item 585): the owner or a merge put it to rest by hand
    (``rested_at``), or no run read it, nothing rewrote it and the owner did not wake it for ``rest_days`` days. A
    skill the owner pinned never rests. A record that counts uses and has no ``last_read_at`` yet (a run read it before
    the date was kept) does not rest for want of one: :func:`dotagent.agent.skill_history.settle_read_dates` gives it
    one, so that it rests after the days pass and not at the first start. A resting skill is not offered to a run, is
    shown as resting and is never retired for it."""
    if meta.pinned:
        return False
    if meta.rested_at is not None:
        return True
    if meta.last_read_at is None and meta.uses + meta.failures > 0:
        return False
    touched: float = max(meta.last_read_at or 0.0, meta.updated_at, meta.woken_at or 0.0, meta.written_at)
    return touched < now - rest_days * 86400.0


def offered_to(meta: PlaybookMeta, agent_id: str | None) -> bool:
    """Whether a run of the agent ``agent_id`` (``None`` for Dot) is offered the skill (item 94): Dot's and the shared
    ones go to every run, an agent's own to its runs alone."""
    return meta.shared or meta.agent_id is None or meta.agent_id == agent_id


def withholds(text: str, *, naming: bool = True) -> bool:
    """Whether a line of a skill that may come from content third parties can author is withheld when a run is offered
    it: it holds what the gate for learned texts refuses (:mod:`dotagent.core.learned_text`: a character that does not
    print, a web address, an e-mail address, an account, a path in somebody's home folder), or it runs a command or
    reaches the owner's own home folder or the Mac's own folders (:data:`_WITHHELD`). A step that names a host bare
    sends the run there; ``naming=False`` (the description of a skill) lets a site be named. A line longer than
    :data:`_MAX_LINE` characters is withheld without being read, and the owner's mark is withheld in any spelling."""
    return (
        len(text) > _MAX_LINE
        or hidden_character(text) is not None
        or finds_link(text, naming=naming)
        or finds_email(text)
        or finds_handle(text)
        or finds_home_path(text)
        or wears(text, OWNER_MARK)
        or any(pattern.search(text) is not None for pattern in _WITHHELD)
    )


def withheld_body(body: str, why: str, warning: str) -> str:
    """``body``, the instructions of a learned skill (:func:`render_playbook`), with ``warning`` after its title and,
    in place of every line the review model wrote (its sections but the evidence) that :func:`withholds` or that a code
    block holds, ``[step withheld: <why>]`` (``[withheld: <why>]`` outside the steps); a numbered or bulleted line keeps
    its number or bullet. The evidence is the last section Dot writes: a heading of it anywhere before the last one is
    a line of the review model's (item 351; a skill written before its parts were made one line each may hold one)."""
    given: list[str] = body.splitlines()
    evidence: int = max((index for index, line in enumerate(given) if line.strip() == f"## {_EVIDENCE}"), default=-1)
    lines: list[str] = []
    section: str | None = None
    fenced: bool = False
    for index, line in enumerate(given):
        heading: re.Match[str] | None = None if fenced else _HEADING.fullmatch(line)
        fence: bool = line.lstrip().startswith(_FENCE)
        if heading is not None and (heading.group(1) != _EVIDENCE or index == evidence):
            section = heading.group(1)
            lines.append(line)
        elif section is not None and section != _EVIDENCE and (fenced or fence or withholds(line)):
            item: re.Match[str] | None = _LIST_ITEM.match(line)
            what: str = "step withheld" if section == _STEPS else "withheld"
            lines.append(f"{item.group(0) if item is not None else ''}[{what}: {why}]")
        else:
            lines.append(line)
        fenced = fenced != fence
    return _with_warning(lines, warning)


def _with_warning(lines: list[str], warning: str) -> str:
    """``lines`` joined, with ``warning`` after the title (the first line that starts with ``# ``)."""
    title: int = next((index for index, line in enumerate(lines) if line.startswith("# ")), -1)
    return "\n".join((*lines[: title + 1], *([""] if title >= 0 else []), warning, *lines[title + 1 :]))


def hidden_lines_withheld(body: str, why: str, warning: str) -> str:
    """``body``, the instructions of a skill that a clean run wrote or that the owner trusts or taught, with each line
    that holds a character that does not print withheld in its place (``[step withheld: <why>]`` in the steps,
    ``[withheld: <why>]`` elsewhere; a numbered or bulleted line keeps its number or bullet), and ``warning`` after its
    title. Nothing else is withheld: such a skill stands as it was written but for the line nobody could read (a
    warning sign with its variation selector is one), and the commands around it stand (item 587)."""
    lines: list[str] = []
    section: str | None = None
    for line in body.split("\n"):
        heading: re.Match[str] | None = _HEADING.fullmatch(line)
        if heading is not None:
            section = heading.group(1)
        if hidden_character(line) is None:
            lines.append(line)
            continue
        item: re.Match[str] | None = _LIST_ITEM.match(line)
        what: str = "step withheld" if section == _STEPS else "withheld"
        lines.append(f"{item.group(0) if item is not None else ''}[{what}: {why}]")
    return _with_warning(lines, warning)


def presented(skill: Skill, meta: PlaybookMeta) -> Skill:
    """The learned skill ``skill`` as a run is offered it, by its record ``meta``: as written when the run whose steps
    it holds read no content third parties can author, or the owner trusts or taught it, but for a line that holds a
    character that does not print (:func:`hidden_lines_withheld`), withheld whoever vouches for the skill, and the
    description when it holds one itself (item 587): what nobody could read is not followed, and the rest of the skill
    stands. Else, its record saying content third parties can author was read, or older than the mark (``tainted``
    ``None``), with the lines that could carry that content's instructions withheld (:func:`withheld_body`) and its
    description marked, withheld too when it names what a line is withheld for."""
    if meta.tainted is False:
        if hidden_character(f"{skill.description}\n{skill.body}") is None:
            return skill
        described: str = (
            f"[withheld: {_HIDDEN_CHARACTER}]" if hidden_character(skill.description) is not None else skill.description
        )
        body: str = (
            hidden_lines_withheld(skill.body, _HIDDEN_CHARACTER, _HIDDEN_WARNING)
            if hidden_character(skill.body) is not None
            else skill.body
        )
        return skill.model_copy(update={"description": described, "body": body})
    why: str = _THIRD_PARTY if meta.tainted else _UNKNOWN_ORIGIN
    warning: str = _THIRD_PARTY_WARNING if meta.tainted else _UNKNOWN_WARNING
    description: str = f"[withheld: {why}]" if withholds(skill.description, naming=False) else skill.description
    return skill.model_copy(
        update={"description": f"({why}) {description}", "body": withheld_body(skill.body, why, warning)}
    )


def offered_skills(
    skills: Sequence[Skill], entries: Sequence[PlaybookEntry], agent_id: str | None, resting: Collection[str]
) -> tuple[Skill, ...]:
    """``skills`` without the learned ones a run of ``agent_id`` is not offered (:func:`offered_to`) and the ones that
    rest (``resting``, the names :func:`skill_resting` says; item 585), each learned one as its record has it offered
    (:func:`presented`); a learned skill without a readable record among the active ``entries`` is not offered."""
    offered: dict[str, PlaybookMeta] = {
        entry.meta.name: entry.meta
        for entry in entries
        if not entry.retired and offered_to(entry.meta, agent_id) and entry.meta.name not in resting
    }
    return tuple(
        skill if skill.source != PLAYBOOK_SOURCE else presented(skill, offered[skill.name])
        for skill in skills
        if skill.source != PLAYBOOK_SOURCE or skill.name in offered
    )


def playbook_folder(root: Path, name: str) -> Path:
    """The folder of the playbook ``name`` under ``root``. A name is one folder of the playbooks, a learned skill's
    prefix and a slug; anything else (``..``, ``.retired``, ``learned-x/../..``) is not found, so a name that comes
    from a request or a run never reaches a folder outside them (item 116)."""
    if _FOLDER_NAME.fullmatch(name) is None:
        raise NotFoundError("there is no such learned skill", {"name": name})
    return root / name


@contextmanager
def changing(root: Path) -> Iterator[None]:
    """Hold the lock of the playbooks under ``root`` until the block ends: the threads of this process and other
    processes wait for it in turn."""
    root.mkdir(parents=True, exist_ok=True)
    descriptor: int = os.open(root / _LOCK_FILE, os.O_RDWR | os.O_CREAT, _PRIVATE_FILE_MODE)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def rendered_skill(file: Path, playbook: Playbook, earlier: PlaybookMeta | None) -> str:
    """The SKILL.md of ``playbook`` for ``file``, read back the way the skills loader reads it before it is written:
    one the loader would not read raises :class:`SkillError`, and nothing is written."""
    text: str = render_playbook(playbook, earlier)
    parse_skill(file, text, PLAYBOOK_SOURCE)
    return text


def write_playbook(root: Path, playbook: Playbook) -> Path:
    """Write the skill's folder under ``root`` and return its SKILL.md. The folder is made whole under a hidden name,
    its record and its skill in it, and then takes its own name in one rename, so a write that stops part way leaves no
    folder of the skill behind; one such folder an older Dot left (no skill in it) is replaced. A folder of the name
    with a skill in it is there already: :class:`FileExistsError`."""
    folder: Path = playbook_folder(root, playbook.name)
    file: Path = folder / SKILL_FILE
    text: str = rendered_skill(file, playbook, None)
    with changing(root):
        if file.is_file():
            raise FileExistsError(f"the learned skill {playbook.name} is there already")
        staging: Path = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=root))
        try:
            save_meta(staging, meta_of(playbook, None))
            write_whole(staging / SKILL_FILE, text)
            if folder.is_dir():
                shutil.rmtree(folder)
            staging.rename(folder)
        finally:
            if staging.is_dir():
                shutil.rmtree(staging)
    return file


def archive_version(folder: Path, number: int) -> Path:
    """Keep the SKILL.md of ``folder`` as it is, as version ``number``, under :data:`VERSIONS_DIR` (item 585), before
    it is replaced. A copy of that number kept before with other words stays as it is and this one takes the next free
    name (``3-2.md``): a version kept is never overwritten, whatever a crash left. Returns the file."""
    kept: Path = folder / VERSIONS_DIR
    kept.mkdir(exist_ok=True)
    held: str = (folder / SKILL_FILE).read_text(encoding="utf-8")
    target: Path = kept / f"{number}.md"
    copy: int = 1
    while target.exists():
        if target.read_text(encoding="utf-8") == held:
            return target
        copy += 1
        target = kept / f"{number}-{copy}.md"
    write_whole(target, held)
    return target


def update_playbook(root: Path, name: str, playbook: Playbook) -> Path:
    """Rewrite the active skill ``name`` from ``playbook`` (a later run of the same kind): the steps are the new
    run's, the evidence keeps every run, and the file it replaces is kept as a version (item 585). Returns its
    SKILL.md."""
    folder: Path = playbook_folder(root, name)
    renamed: Playbook = playbook.model_copy(update={"name": name})
    file: Path = folder / SKILL_FILE
    with changing(root):
        earlier: PlaybookMeta = read_meta(folder)
        text: str = rendered_skill(file, renamed, earlier)
        archive_version(folder, earlier.version)
        replace_skill(folder, text, meta_of(renamed, earlier))
    return file


def read_meta(folder: Path) -> PlaybookMeta:
    """The record of the playbook in ``folder``; a missing or malformed one is not found."""
    file: Path = folder / META_FILE
    try:
        return PlaybookMeta.model_validate(json.loads(file.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise NotFoundError("the playbook has no readable record", {"path": str(file), "error": str(exc)}) from exc


def record_use(root: Path, name: str, succeeded: bool, at: float | None = None) -> PlaybookMeta | None:
    """Count a run that read the playbook ``name`` and ended, at ``at`` (now when none is given), as :func:`recorded`
    says: the counts judge how the run ended, and only a task run of its own that ended done or failed is counted. The
    date of the last read, which the rest of a skill is counted from, is written by the read itself (:func:`date_read`),
    so that a chat, a helper's run and a run that was stopped date it too (item 585). A playbook that :func:`retires` is
    moved under ``.retired``, where an earlier one of its name keeps its folder. Returns the record after the count, or
    None for a playbook that is no longer active (another run retired it or the owner deleted it since this run read
    it), which is not counted."""
    folder: Path = playbook_folder(root, name)
    with changing(root):
        if not folder.is_dir():
            return None
        meta: PlaybookMeta = recorded(read_meta(folder), succeeded, time.time() if at is None else at)
        save_meta(folder, meta)
        if retires(meta):
            retired: Path = root / RETIRED_DIR / name
            retired.parent.mkdir(parents=True, exist_ok=True)
            if retired.exists():
                # A skill of the name retired before keeps its folder, under the next free name (item 306).
                aside: Path = _unused(root / RETIRED_DIR, name)
                shutil.move(str(retired), str(aside))
                _renamed(aside, name)
            shutil.move(str(folder), str(retired))
    return meta


# A skill's last read is dated at most once an hour: its rest is counted in days, and a skill that is read again and
# again (the parts of its instructions, a run that loops) is not written each time.
_READ_DATED_EVERY_S: float = 3600.0


def _read_lately(meta: PlaybookMeta, at: float) -> bool:
    """Whether the record has a date of a read within the hour before ``at`` (or after it), which is date enough."""
    return meta.last_read_at is not None and meta.last_read_at > at - _READ_DATED_EVERY_S


def date_read(root: Path, name: str, at: float) -> bool:
    """Date the last read of the playbook ``name`` (a learned or taught skill) as ``at``, where the read happens: the
    ``skill`` tool, a skill picked for a run (item 585). A skill rests when no run read it for the days, whatever the
    run was (a chat, a helper's, one that was stopped), so the read dates it, and the counts of :func:`record_use`,
    which only a task run of its own that ended done or failed makes, are another thing. Cheap: a date within the last
    hour is left as it is, and the lock of the playbooks is not taken for it. Safe: the record is read again under the
    lock before it is written, so that two runs reading at once, and a run counting its use, lose nothing of each
    other's (:func:`changing`), and the date never goes back. Returns whether the date was written; a playbook that is
    not active, or has no readable record, is none to date."""
    try:
        folder: Path = playbook_folder(root, name)
        meta: PlaybookMeta = read_meta(folder)
    except NotFoundError:
        return False
    if _read_lately(meta, at):
        return False
    with changing(root):
        try:
            meta = read_meta(folder)
        except NotFoundError:
            return False
        if _read_lately(meta, at):
            return False
        save_meta(folder, meta.model_copy(update={"last_read_at": at}))
    return True


async def date_reads(root: Path, skills: Sequence[Skill], at: float) -> None:
    """Date the read of each learned or taught skill among ``skills`` (:func:`date_read`), before the run is handed what
    it read. A date that cannot be written (a full disk, a record that cannot be read) is logged and never fails the
    read: the skill is read all the same, and the next read tries again."""
    for skill in skills:
        if skill.source != PLAYBOOK_SOURCE:
            continue
        try:
            await asyncio.to_thread(date_read, root, skill.name, at)
        except (DotError, OSError) as error:
            said: str = error.message if isinstance(error, DotError) else str(error)
            log.warning("playbook_read_not_dated", playbook=skill.name, error=said)


def _renamed(folder: Path, was: str) -> None:
    """The skill in ``folder``, which was ``was`` and now has the name of its folder, under that name in its SKILL.md
    and its record: the loader reads a skill whose name is its folder's, and a listing or a drop finds it by that name
    (item 557). A skill that cannot be read stays as it is, logged: moving it aside never fails the retirement."""
    file: Path = folder / SKILL_FILE
    try:
        yaml_text, body = split_frontmatter(file, file.read_text(encoding="utf-8"))
        front: object = yaml.safe_load(yaml_text)
        if not isinstance(front, dict):
            raise SkillError("the frontmatter of SKILL.md is not a mapping of keys to values", {"path": str(file)})
        front["name"] = folder.name
        write_whole(
            file,
            f"---\n{yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=_FRONTMATTER_WIDTH)}---\n"
            + body.replace(f"# {was}\n", f"# {folder.name}\n", 1),
        )
        save_meta(folder, read_meta(folder).model_copy(update={"name": folder.name}))
    except (DotError, OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        log.warning("playbook_not_renamed", folder=str(folder), was=was, error=str(error))


def _unused(parent: Path, name: str) -> Path:
    """The first of ``name-2``, ``name-3``, ... that ``parent`` holds nothing of."""
    return next(candidate for number in count(2) if not (candidate := parent / f"{name}-{number}").exists())


def folder_of(root: Path, name: str) -> Path:
    """The folder of the playbook ``name``, active or retired; one that does not exist is not found."""
    for folder in (playbook_folder(root, name), root / RETIRED_DIR / name):
        if folder.is_dir():
            return folder
    raise NotFoundError("there is no such learned skill", {"name": name})


def save_meta(folder: Path, meta: PlaybookMeta) -> None:
    write_whole(folder / META_FILE, meta.model_dump_json(indent=2) + "\n")


def replace_skill(folder: Path, text: str, meta: PlaybookMeta) -> None:
    """Make ``text`` the SKILL.md of the skill in ``folder`` and ``meta`` its record, the record first (item 585). A
    write that stops between the two (a full disk) then leaves the record ahead of the file: the older steps under the
    newer mark and version, which only withholds more, and never new steps under a record that says they carry less of
    outside content than they do. The caller keeps the file it replaces as a version first (:func:`archive_version`) and
    holds the lock of the playbooks."""
    save_meta(folder, meta)
    write_whole(folder / SKILL_FILE, text)


def drop_playbook(root: Path, name: str) -> Path:
    """Remove the playbook ``name``, active or retired; one that does not exist is not found."""
    with changing(root):
        folder: Path = folder_of(root, name)
        shutil.rmtree(folder)
    return folder


def share_playbook(root: Path, name: str, shared: bool) -> PlaybookMeta:
    """Mark the playbook ``name`` (active or retired) as offered to every run, or to its agent's alone again; Dot's
    own is offered to every run either way. Returns the record after it."""
    with changing(root):
        folder: Path = folder_of(root, name)
        meta: PlaybookMeta = read_meta(folder).model_copy(update={"shared": shared})
        save_meta(folder, meta)
    return meta


def trust_playbook(root: Path, name: str) -> PlaybookMeta:
    """The owner's word that the playbook ``name`` (active or retired) may be offered as written although a run it
    comes from read content third parties can author, or its record is older than the mark: ``tainted`` is ``False``
    until a run that read such content rewrites its steps. Returns the record after it."""
    with changing(root):
        folder: Path = folder_of(root, name)
        before: PlaybookMeta = read_meta(folder)
        notes: tuple[SkillVersion, ...] = noted(before)
        # The version the skill holds is trusted too, so that putting it back later brings the trust back (item 585).
        meta: PlaybookMeta = before.model_copy(
            update={"tainted": False, "versions": (*notes[:-1], notes[-1].model_copy(update={"tainted": False}))}
        )
        save_meta(folder, meta)
    log.info("playbook_trusted", name=name, tainted_runs=list(meta.tainted_runs))
    return meta


def release_playbooks(root: Path, agent_id: str) -> int:
    """Make the playbooks of the agent ``agent_id`` Dot's (an agent that was deleted); how many there were."""
    released: int = 0
    with changing(root):
        for entry in list_playbooks(root):
            if entry.meta.agent_id != agent_id:
                continue
            # The folder the entry was read from: a retired skill may carry the name of an active one, and the folder a
            # name finds is the active one's.
            save_meta(entry.skill.root, entry.meta.model_copy(update={"agent_id": None}))
            released += 1
    return released


def _entries(folder: Path, retired: bool) -> list[PlaybookEntry]:
    entries: list[PlaybookEntry] = []
    if not folder.is_dir():
        return entries
    for child in sorted(folder.iterdir()):
        file: Path = child / SKILL_FILE
        if not child.name.startswith(PLAYBOOK_PREFIXES) or not file.is_file():
            continue
        try:
            skill: Skill = read_skill(file, PLAYBOOK_SOURCE)
            meta: PlaybookMeta = read_meta(child)
        except (SkillError, NotFoundError) as error:
            # One skill the loader cannot read (edited by hand, written by an older Dot, no UTF-8 text) or whose record
            # cannot be read is left out, as the catalog leaves it out; the others, and the runs that are offered them,
            # go on. A learned skill with no entry is not offered (:func:`offered_skills`).
            log.warning("playbook_unreadable", path=str(file), error=error.message, context=error.context)
            continue
        entries.append(PlaybookEntry(skill=skill, meta=meta, retired=retired))
    return entries


def list_playbooks(root: Path) -> tuple[PlaybookEntry, ...]:
    """Every playbook under ``root``: the active ones, then the retired ones; one whose files cannot be read is left
    out and logged."""
    return (*_entries(root, False), *_entries(root / RETIRED_DIR, True))


class OfferedSkills(FrozenModel):
    """The skills a run is offered, and which of the learned ones among them may carry content third parties can
    author (``third_party``: their record says so, or is older than the mark), which a run that reads one takes as
    having read outside content (item 585)."""

    skills: tuple[Skill, ...]
    third_party: frozenset[str]


async def offered_with_marks(config: AppConfig, skills: SkillCatalog, agent_id: str | None) -> OfferedSkills:
    """The skills of ``skills`` that a run of the agent ``agent_id`` is offered, with the learned ones that carry the
    mark: every skill but the learned ones of other agents (item 94) and those that rest (item 585,
    :func:`offered_skills`); a learned skill whose files cannot be read is not offered (:func:`list_playbooks`)."""
    root: Path | None = playbook_root(config)
    if root is None:
        return OfferedSkills(skills=skills.skills, third_party=frozenset())
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, root)
    now: float = time.time()
    # Only the active skills rest by name: a retired skill of the same name, unread for the days, must not keep the
    # active one from being offered.
    resting: set[str] = {
        entry.meta.name
        for entry in entries
        if not entry.retired and skill_resting(entry.meta, now, config.agent.skill_rest_days)
    }
    offered: tuple[Skill, ...] = offered_skills(skills.skills, entries, agent_id, resting)
    names: set[str] = {skill.name for skill in offered}
    marked: frozenset[str] = frozenset(
        entry.meta.name
        for entry in entries
        if not entry.retired and entry.meta.name in names and entry.meta.tainted is not False
    )
    return OfferedSkills(skills=offered, third_party=marked)


async def offered_to_run(config: AppConfig, skills: SkillCatalog, agent_id: str | None) -> tuple[Skill, ...]:
    """The skills of ``skills`` that a run of the agent ``agent_id`` is offered (:func:`offered_with_marks`)."""
    return (await offered_with_marks(config, skills, agent_id)).skills


def _spellings(place: Path, home: Path) -> tuple[str, ...]:
    """A place as a command line may write it: its full path and, under the home folder, with ``~`` or ``$HOME`` for
    the folder."""
    full: str = str(place)
    try:
        below: Path = place.relative_to(home)
    except ValueError:
        return (full,)
    return (full, f"~/{below}", f"$HOME/{below}", f"${{HOME}}/{below}")


def daemon_addresses(port: int) -> tuple[str, ...]:
    """How a command or a page may write the address of the daemon that listens on ``port``."""
    return (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")


def dot_names(places: Sequence[Path], addresses: Sequence[str], home: Path) -> tuple[str, ...]:
    """Every way a command, a path or an argument may write what Dot guards or loads: its ``places`` (its keys, its
    configuration, its database, its code, skills and playbooks, as a path, with ``~`` or ``$HOME`` for the home folder)
    and the daemon's ``addresses``."""
    return (*(spelling for place in places for spelling in _spellings(place, home)), *addresses)


def named_dot(text: str, names: Sequence[str]) -> str | None:
    """The first of ``names`` (:func:`dot_names`) that ``text`` holds, letter case aside, or ``None``."""
    folded: str = text.casefold()
    return next((name for name in names if name.casefold() in folded), None)


def arguments_name_dot(arguments: JsonObject, names: Sequence[str]) -> str | None:
    """What the arguments of a call name that Dot guards or loads (item 585): the first of ``names`` that a string of
    them holds, or Dot's own program (``dotx``, ``dotagent``); every string is read, the keys of an object too, however
    deep. ``None`` when they name none. It is for the calls whose target does not show them (an MCP call's is its server
    and the arguments only the summary's, cut short), decided where the call is classified, which is where the arguments
    and the places are known."""
    pending: list[JsonValue] = [arguments]
    while pending:
        value: JsonValue = pending.pop()
        if isinstance(value, str):
            found: re.Match[str] | None = _DOT_PROGRAM.search(value)
            named: str | None = named_dot(value, names) or (None if found is None else found.group(0))
            if named is not None:
                return named
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return None


def touched_dot(
    steps: Sequence[ResultStep], places: Sequence[Path], addresses: Sequence[str], home: Path
) -> str | None:
    """Why a run, which took ``steps`` (its own and those of the runs it delegated: a helper that changes Dot's files
    is the run's doing), changed Dot itself, or ``None`` (item 585): a skill is never made from a run that did, since
    it would teach later runs to do it again. Read, as each step was recorded when it ran: a step the
    policy flagged ``self_change``; and, of the steps that are not mere observations (:data:`_OBSERVING`), one the
    policy flagged for reaching a place it guards (``secret_access``, ``protected_read``: :data:`_GUARD_FLAGS`, the
    verdict of the shell guard on a command's words, its working folder and the paths it builds, which a yolo run is
    let do), one whose arguments the executor found naming a place Dot guards or loads (``names_dot``: an MCP call,
    whose target is its server), one whose target names such a place (``places``, as :func:`dot_names` spells them,
    letter case aside), the daemon's address (``addresses``) or, in a command, Dot's own program (``dotx``,
    ``dotagent``). What neither the policy nor the executor could read, a path an MCP server takes relative to a folder
    of its own, is not seen, which the docs say."""
    names: tuple[str, ...] = dot_names(places, addresses, home)
    for step in steps:
        shown: str = truncate_tail(f"{step.tool} {step.target}", _STEP_CHARS)
        if step.helper:
            shown = f"in a helper run: {shown}"
        if "self_change" in step.flags:
            return f"a step changed Dot's own files ({shown})"
        if step.kind in _OBSERVING:
            continue
        flagged: str | None = next((flag for flag in _GUARD_FLAGS if flag in step.flags), None)
        if flagged is not None:
            return f"a step the policy flagged {flagged}, for reaching a place it guards ({shown})"
        named: str | None = step.names_dot or named_dot(step.target, names)
        if named:
            return f"a step named {named}, which Dot guards or loads ({shown})"
        if step.kind == "shell" and _DOT_PROGRAM.search(step.target) is not None:
            return f"a step ran Dot's own program ({shown})"
    return None


async def _refused(runtime: "Runtime", run: RunRecord) -> str | None:
    """Why ``run`` gives no skill whatever the review model says of it (item 585): it is a chat-mode run, which only
    talks, or it changed Dot itself (:func:`touched_dot`, over every step of the run and of the runs it delegated).
    ``None`` when it may."""
    if run.mode == "chat":
        return "a chat-mode run only talks, and gives no skill"
    return touched_dot(
        await runtime.store.result_steps(run.id),
        (*runtime.protected, *runtime.dot_paths),
        daemon_addresses(runtime.config.server.port),
        Path.home(),
    )


async def _acting_events(runtime: "Runtime", run: RunRecord) -> list[EventRecord]:
    """The newest tool calls and results of ``run`` (:data:`_EVENTS_READ` of them), oldest first."""
    return await runtime.store.events_of_kinds(run.id, ("tool_call", "tool_result"), _EVENTS_READ)


async def _record_uses(runtime: "Runtime", run: RunRecord, status: RunStatus) -> None:
    """Count the use of each learned skill ``run`` read with the ``skill`` tool, wherever in the run it did."""
    root: Path = playbooks_dir()
    read_at: float = time.time()
    for name in used_playbooks(await runtime.store.tool_calls_named(run.id, SKILL_TOOL)):
        meta: PlaybookMeta | None = await asyncio.to_thread(record_use, root, name, status == "done", read_at)
        if meta is None:
            continue
        log.info("playbook_use_recorded", run_id=run.id, playbook=name, uses=meta.uses, failures=meta.failures)
        if retires(meta):
            await runtime.bus.emit(run.id, PLAYBOOK_RETIRED, {"name": name, "failures": meta.failures}, True)


def same_kind(kind: str, entries: Sequence[PlaybookEntry], vectors: Sequence[Vector] | None) -> PlaybookEntry | None:
    """The active skill whose kind is ``kind``: by meaning when ``vectors`` holds the embeddings of ``kind`` and then
    of each entry's kind in order (cosine at least :data:`_SAME_KIND`, the closest), else by name."""
    active: list[PlaybookEntry] = [entry for entry in entries if not entry.retired]
    if vectors is None:
        wanted: str = slug(kind)
        return next((entry for entry in active if slug(entry.meta.kind) == wanted), None)
    stored: list[tuple[str, Vector]] = [
        (entry.meta.name, vector) for entry, vector in zip(active, vectors[1:], strict=True)
    ]
    close: list[Similar] = most_similar(vectors[0], stored, _SAME_KIND, 1)
    return next((entry for entry in active if close and entry.meta.name == close[0].id), None)


def owned(entries: Sequence[PlaybookEntry], agent_id: str | None) -> list[PlaybookEntry]:
    """The active skills a run of ``agent_id`` may update: its owner's own (Dot's for Dot's runs, an agent's for its
    runs). A skill of another owner, shared or not, is never rewritten by a run that is not its owner's, and neither is
    a skill the owner taught (item 423): a run of its kind learns one of its own."""
    return [
        entry
        for entry in entries
        if not entry.retired
        and not entry.meta.taught
        and entry.meta.merged_into is None
        and entry.meta.agent_id == agent_id
    ]


def kinds_shown(entries: Sequence[PlaybookEntry], host: str) -> tuple[str, ...]:
    """The kinds the writer is shown (:data:`_KINDS_SHOWN` at most): those of the skills on ``host`` first, then the
    newest, each once."""
    ordered: list[PlaybookEntry] = sorted(entries, key=lambda entry: (entry.meta.host != host, -entry.meta.updated_at))
    return tuple(dict.fromkeys(entry.meta.kind for entry in ordered))[:_KINDS_SHOWN]


def _has_skill(root: Path, name: str) -> bool:
    return (playbook_folder(root, name) / SKILL_FILE).is_file() or (root / RETIRED_DIR / name / SKILL_FILE).is_file()


def free_name(root: Path, name: str) -> str:
    """``name``, or the first of ``name-2``, ``name-3``, ... with no skill under ``root``, active or retired: a skill of
    another owner keeps its folder, and so does one that retired (item 306), which a skill of its name retiring later
    would take the place of."""
    if not _has_skill(root, name):
        return name
    return next(candidate for number in count(2) if not _has_skill(root, candidate := f"{name}-{number}"))


async def _matching(runtime: "Runtime", kind: str, own: Sequence[PlaybookEntry]) -> PlaybookEntry | None:
    """The skill of ``kind`` among ``own`` (:func:`owned`): the one whose kind is written the same (the writer reuses
    a kind it is shown word for word), else the closest by meaning, if any."""
    exact: PlaybookEntry | None = same_kind(kind, own, None)
    if exact is not None or runtime.memory.embedder is None or not own:
        return exact
    vectors: list[Vector] = await runtime.memory.embedder.embed([kind, *(entry.meta.kind for entry in own)])
    return same_kind(kind, own, vectors)


async def _ask_writer(
    runtime: "Runtime", run: RunRecord, steps: Sequence[ActingStep], host: str, tainted: bool
) -> PlaybookReply:
    """What the review model writes of ``run`` as a skill, with its judgement of whether the steps are a procedure that
    will recur (item 585). Nothing is written yet.
    ``tainted``: the run read content third parties can author, so the writer sees what the agent did and not what the
    calls returned (:func:`own_steps_digest`)."""
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
    writer: PlaybookWriter = playbook_writer_for(
        runtime.router,
        runtime.roles.review(),
        (run.model,),
        runtime.config.agent.model_call_timeout_s,
        f"{run.thread_id}:playbook",
        run.id,
    )
    return await writer(
        PlaybookRequest(
            goal=run.goal,
            host=host,
            digest=own_steps_digest(steps) if tainted else acting_digest(steps),
            answer=_ANSWER_LEFT_OUT if tainted else run.result,
            kinds=kinds_shown(owned(entries, run.agent_id), host),
        )
    )


async def _write_reply(
    runtime: "Runtime", run: RunRecord, steps: Sequence[ActingStep], host: str, tainted: bool, written: PlaybookReply
) -> tuple[Path, str, bool]:
    """Write what the review model wrote of ``run`` as a skill, its secrets masked (:func:`redacted_draft`): a new
    folder, or the folder of the run's owner's skill of the same kind, whose file is kept as a version (item 585).
    Returns the SKILL.md, the skill's name and whether it was updated rather than written. The skill's record says
    whether the run read content third parties can author (``tainted``, :func:`presented`)."""
    root: Path = playbooks_dir()
    own: list[PlaybookEntry] = owned(await asyncio.to_thread(list_playbooks, root), run.agent_id)
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    draft: PlaybookDraft = redacted_draft(written, secrets)
    playbook: Playbook = Playbook(
        name=playbook_name(draft.kind),
        kind=draft.kind,
        host=host,
        draft=draft,
        run_id=run.id,
        agent_id=run.agent_id,
        goal=redact_known(run.goal, secrets),
        written_at=time.time(),
        acting_steps=len(steps),
        tainted=tainted,
    )
    match: PlaybookEntry | None = await _matching(runtime, draft.kind, own)
    if match is not None:
        return await asyncio.to_thread(update_playbook, root, match.meta.name, playbook), match.meta.name, True
    if not slug(draft.kind):
        raise InvalidValueError("the kind of work has no letters or digits to name a skill by", {"kind": draft.kind})
    named: Playbook = playbook.model_copy(update={"name": await asyncio.to_thread(free_name, root, playbook.name)})
    return await asyncio.to_thread(write_playbook, root, named), named.name, False


async def learn_playbook(runtime: "Runtime", run: RunRecord, status: RunStatus) -> LearningOutcome:
    """The skill work of a run that ended: record its use of learned skills, then, when it may be distilled, write
    the skill of its kind or update the one that exists (``skipped`` when it may not). A review model that fails, or a
    folder that cannot be written, ends it with a ``playbook_failed`` event and fails it; the run keeps its end either
    way."""
    await _record_uses(runtime, run, status)
    events: list[EventRecord] = await _acting_events(runtime, run)
    steps: tuple[ActingStep, ...] = acting_steps(events)
    if not distils(status, len(steps), runtime.config.agent.skill_min_steps):
        if status == "done":
            log.info(
                "playbook_skipped", run_id=run.id, reason="fewer acting steps than skill_min_steps", steps=len(steps)
            )
        return SKIPPED
    refused: str | None = await _refused(runtime, run)
    if refused is not None:
        log.info("playbook_skipped", run_id=run.id, reason=refused)
        return SKIPPED
    host: str = main_site(events) or NO_SITE
    tainted: bool = await runtime.store.read_outside(run.id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS))
    try:
        reply: PlaybookReply = await _ask_writer(runtime, run, steps, host, tainted)
        if not reply.recurs:
            log.info(
                "playbook_skipped", run_id=run.id, reason="the review model judged the steps no procedure that recurs"
            )
            return SKIPPED
        file, name, updated = await _write_reply(runtime, run, steps, host, tainted, reply)
    except (DotError, OSError) as error:
        message: str = error.message if isinstance(error, DotError) else str(error)
        context: dict[str, JsonValue] = error.context if isinstance(error, DotError) else {}
        log.warning("playbook_failed", run_id=run.id, host=host, error=message, context=context)
        said: str = f"The skill could not be written: {shorten_reason(message)}"
        await runtime.bus.emit(run.id, PLAYBOOK_FAILED, {"host": host, "error": said}, True)
        return failed_with(said)
    log.info("playbook_written", run_id=run.id, name=name, host=host, path=str(file), updated=updated, tainted=tainted)
    await runtime.bus.emit(
        run.id, PLAYBOOK_WRITTEN, {"name": name, "host": host, "path": str(file), "updated": updated}, True
    )
    return LEARNED


def unsavable(run: RunRecord, steps: int) -> str | None:
    """Why ``run``, which took ``steps`` acting steps, gives no skill at the owner's word (item 318), or ``None`` when
    it does: a run of its own (not a delegated run, an eval nor a run of OpenCode's agent, whose tools a skill of Dot's
    cannot name) that ended done after at least one acting step."""
    if run.parent_run_id is not None:
        return "a delegated run's work belongs to the run that delegated it"
    if run.source == EVAL_SOURCE:
        return "an eval's run gives no skill"
    if run.mode == "chat":
        return "a chat-mode run only talks, and gives no skill"
    if parse_model_ref(run.model).provider == OPENCODE_PROVIDER:
        return "OpenCode's agent used tools that a skill of Dot's cannot name"
    if run.status != "done":
        return f"it ended {run.status}, and only a run that ended done gives a skill"
    if steps == 0:
        return "it took no acting step, so it did nothing a skill could repeat"
    return None


async def save_playbook(runtime: "Runtime", run: RunRecord) -> tuple[str, bool]:
    """Write the skill of ``run`` at the owner's word (item 318's Save skill), as :func:`learn_playbook` writes one when
    a run ends, however few its acting steps were. Returns the skill's name and whether the owner's skill of its kind
    was updated rather than a new one written. With learned skills off (``[agent] playbooks``), which no run is then
    offered, and for a run that gives no skill (:func:`unsavable`), it is refused, with why; a review model that fails,
    or a folder that cannot be written, fails it."""
    if not runtime.config.agent.playbooks:
        raise InvalidValueError(
            "learned skills are off ([agent] playbooks), so no run would be offered a skill saved now: turn them on "
            "first",
            {"run_id": run.id},
        )
    events: list[EventRecord] = await _acting_events(runtime, run)
    steps: tuple[ActingStep, ...] = acting_steps(events)
    why: str | None = unsavable(run, len(steps)) or await _refused(runtime, run)
    if why is not None:
        raise InvalidValueError(f"this run cannot be saved as a skill: {why}", {"run_id": run.id})
    host: str = main_site(events) or NO_SITE
    tainted: bool = await runtime.store.read_outside(run.id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS))
    # The owner asked for it: whether the steps will recur is theirs to say, so the model's answer to it is not read.
    written: PlaybookReply = await _ask_writer(runtime, run, steps, host, tainted)
    file, name, updated = await _write_reply(runtime, run, steps, host, tainted, written)
    log.info("playbook_saved", run_id=run.id, name=name, host=host, path=str(file), updated=updated, tainted=tainted)
    await runtime.bus.emit(
        run.id, PLAYBOOK_WRITTEN, {"name": name, "host": host, "path": str(file), "updated": updated}, True
    )
    return name, updated
