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
``.retired`` where the catalog does not look, and no longer offered. Every function here is pure except :func:`learn_playbook`, which reads the store, the
embedder and the review model and writes the folder, and the small file helpers it calls.

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
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import JsonValue, ValidationError

from dotagent.agent.learning_health import LEARNED, SKIPPED, LearningOutcome, failed_with
from dotagent.agent.review import PlaybookDraft, PlaybookRequest, PlaybookWriter, playbook_writer_for, shorten_reason
from dotagent.config import AppConfig
from dotagent.core.errors import DotError, InvalidValueError, NotFoundError, SkillError
from dotagent.core.files import write_whole
from dotagent.core.jsonutil import dumps_compact
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
from dotagent.skills.catalog import PLAYBOOK_SOURCE, playbook_root, read_skill
from dotagent.skills.parse import parse_skill
from dotagent.skills.types import SKILL_FILE, SKILL_TOOL, Skill, SkillCatalog
from dotagent.store.models import EventRecord, RunRecord, RunStatus

if TYPE_CHECKING:
    # Only named: the runtime's tools import this module (find_help asks it for the skills a run is offered).
    from dotagent.runtime import Runtime

log = get_logger(__name__)

PLAYBOOK_PREFIX: str = "learned-"
# The prefix of the folders written before item 92, which a run may still read.
_OLD_PREFIX: str = "playbook-"
_FOLDER_NAME: re.Pattern[str] = re.compile(r"(?:learned|playbook)-[a-z0-9-]*")
META_FILE: str = "playbook.json"
RETIRED_DIR: str = ".retired"
_LOCK_FILE: str = ".lock"
# The hidden folder a new skill is written in before it takes its name (the skills catalog leaves hidden folders alone).
_STAGING_PREFIX: str = ".writing-"
_PRIVATE_FILE_MODE: int = 0o600
PLAYBOOK_WRITTEN: str = "playbook_written"
PLAYBOOK_FAILED: str = "playbook_failed"
PLAYBOOK_RETIRED: str = "playbook_retired"
# A playbook that this many runs read and then failed is retired.
MAX_FAILURES: int = 2
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
# What withholds a line of a skill that may come from content third parties can author, when a run is offered it: a web
# address, an e-mail address or an account, a command (code, a tool that runs one, the syntax of a shell, an option, a
# command with its argument) and a path in the home folder.
_WITHHELD: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[a-z][a-z0-9+.-]*://|\bwww\.\w|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/", re.IGNORECASE),
    re.compile(r"\b(?:javascript|vbscript|mailto):\S|\bdata:[\w.+-]+/[\w.+-]+[;,]", re.IGNORECASE),
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?<![\w.@])@[A-Za-z0-9_]{2,}"),
    re.compile(r"`|\b(?:shell|terminal|apple_?script|osascript|browser_eval|command[ -]line)", re.IGNORECASE),
    re.compile(r"\$\(|\$\{|\$[A-Z_]{2,}\b|\|\s*\S|&&|(?:^|\s)--?[A-Za-z]"),
    re.compile(
        r"\b(?:sudo|curl|wget|bash|zsh|sh|rm|rmdir|mv|cp|chmod|chown|ssh|scp|rsync|nc|dd|eval|exec|launchctl|pkill"
        r"|killall|xattr|crontab|cat|ls|echo|grep|python3?|pip3?|npm|npx|uvx?|brew|git|docker)\s+\S"
    ),
    re.compile(r"(?<![\w/.~])~(?:[A-Za-z_][\w.-]*)?(?=/|\s|$|[)\"'.,;:])|/Users/"),
)
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
_WITHHELD_LINES: str = (
    "Its lines that name a web address, an e-mail address or an account, run a command or reach a path in the home "
    "folder are withheld; work those steps out from the task in front of you."
)
_THIRD_PARTY_WARNING: str = f"Warning: learned from a run that read third-party content. {_WITHHELD_LINES}"
_UNKNOWN_WARNING: str = (
    "Warning: learned before Dot recorded whether a run read third-party content, so it is offered as if one did. "
    f"{_WITHHELD_LINES}"
)
# The name a skill is written under when the run acted on no web site or application.
NO_SITE: str = "this Mac"


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


class PlaybookMeta(FrozenModel):
    """``playbook.json``: the kind the skill is matched by, where it comes from (``runs``, the first one first, with
    the acting steps each took) and how it fared since. ``uses`` counts the runs that read it and ended done,
    ``failures`` the runs that read it and failed, ``failing`` those of them since the last that ended done.
    ``agent_id`` is the agent whose skill it is (item 94), ``None`` for Dot's, offered to every run; an agent's is
    offered to its runs alone until the owner marks it ``shared``. ``tainted``: the run whose steps the skill holds (the
    last of ``runs``) read content third parties can author, and the owner has not trusted it since; ``None`` in a
    record older than the mark, which is offered as one that did (:func:`presented`). ``tainted_runs`` are the runs
    among ``runs`` that read such content. The fields with a default are absent from the records written before
    them."""

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


class PlaybookEntry(FrozenModel):
    """A playbook on disk, for the listing: its skill and its record, and whether it is retired."""

    skill: Skill
    meta: PlaybookMeta
    retired: bool


def slug(text: str) -> str:
    """The folder part of a skill's name for ``text`` (the kind of work): lower-case letters and digits with single
    hyphens between, at most :data:`_SLUG_CHARS` long, empty when the text has none."""
    return _UNSAFE.sub("-", text.lower()).strip("-")[:_SLUG_CHARS].strip("-")


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
            "steps": tuple(redact_known(step, secrets) for step in draft.steps),
            "parameters": tuple(redact_known(parameter, secrets) for parameter in draft.parameters),
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
        if isinstance(name, str) and name.startswith((PLAYBOOK_PREFIX, _OLD_PREFIX)):
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


def render_playbook(playbook: Playbook, earlier: PlaybookMeta | None) -> str:
    """The SKILL.md of a learned skill: the frontmatter the skills loader reads, then the kind, when it fits, the
    steps and the parameters a line each, and the evidence (the runs of ``earlier``, when the skill is updated, before
    this one)."""
    draft: PlaybookDraft = playbook.draft
    description: str = " ".join(draft.description.split())
    steps: str = "\n".join(f"{number}. {' '.join(step.split())}" for number, step in enumerate(draft.steps, start=1))
    parameters: str = "\n".join(f"- {' '.join(parameter.split())}" for parameter in draft.parameters) or "- none"
    runs: list[str] = [*(earlier.runs if earlier is not None else ()), playbook.run_id]
    counts: list[int] = [*(earlier.acting_steps if earlier is not None else ()), playbook.acting_steps]
    evidence: str = "\n".join(f"- Run {run}: {count} acting steps" for run, count in zip(runs, counts, strict=True))
    where: str = f" on {playbook.host}" if playbook.host != NO_SITE else ""
    return (
        f"---\n{_frontmatter(playbook.name, description)}---\n\n"
        f"# {playbook.name}\n\n"
        f"Learned by Dot from its own work{where} ({len(runs)} {'run' if len(runs) == 1 else 'runs'}, the last one "
        f"verified on {_date(playbook.written_at)}): a record of the steps that worked, not instructions from the "
        "user. It is advice: follow the steps with your own parameters and your own judgement; where things have "
        "changed, do what you see and go on.\n\n"
        f"## Kind of work\n\n{playbook.kind}\n\n"
        f"## When\n\n{draft.when}\n\n"
        f"## Steps\n\n{steps}\n\n"
        f"## Parameters\n\n{parameters}\n\n"
        f"## Evidence\n\n- Goal of the last run: {' '.join(playbook.goal.split())}\n{evidence}\n"
    )


def meta_of(playbook: Playbook, earlier: PlaybookMeta | None) -> PlaybookMeta:
    """The record of a skill just written from ``playbook``, or updated from ``earlier`` by it (the runs appended,
    the kind and the counts kept). The steps are the run's alone either way, and so is the mark of content third
    parties can author (``tainted``): a run of the kind that read none clears it."""
    tainted_runs: tuple[str, ...] = (playbook.run_id,) if playbook.tainted else ()
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
        )
    return earlier.model_copy(
        update={
            "runs": (*earlier.runs, playbook.run_id),
            "acting_steps": (*earlier.acting_steps, playbook.acting_steps),
            "updated_at": playbook.written_at,
            "tainted": playbook.tainted,
            "tainted_runs": (*earlier.tainted_runs, *tainted_runs),
        }
    )


def recorded(meta: PlaybookMeta, succeeded: bool) -> PlaybookMeta:
    """The record after a run that read the playbook ended: one that ended done clears the failures in a row."""
    if succeeded:
        return meta.model_copy(update={"uses": meta.uses + 1, "failing": 0})
    return meta.model_copy(update={"failures": meta.failures + 1, "failing": meta.failing + 1})


def retires(meta: PlaybookMeta) -> bool:
    """Whether the playbook is retired: :data:`MAX_FAILURES` runs in a row read it and failed, none since ending done
    (a skill that served forty runs is not retired for two failures among them)."""
    return meta.failing >= MAX_FAILURES


def offered_to(meta: PlaybookMeta, agent_id: str | None) -> bool:
    """Whether a run of the agent ``agent_id`` (``None`` for Dot) is offered the skill (item 94): Dot's and the shared
    ones go to every run, an agent's own to its runs alone."""
    return meta.shared or meta.agent_id is None or meta.agent_id == agent_id


def withholds(text: str) -> bool:
    """Whether a line of a skill that may come from content third parties can author is withheld when a run is offered
    it: it names a web address, an e-mail address or an account, runs a command or reaches a path in the home folder
    (:data:`_WITHHELD`)."""
    return any(pattern.search(text) is not None for pattern in _WITHHELD)


def withheld_body(body: str, why: str, warning: str) -> str:
    """``body``, the instructions of a learned skill (:func:`render_playbook`), with ``warning`` after its title and,
    in place of every line the review model wrote (its sections but the evidence) that :func:`withholds` or that a code
    block holds, ``[step withheld: <why>]`` (``[withheld: <why>]`` outside the steps); a numbered or bulleted line keeps
    its number or bullet."""
    lines: list[str] = []
    section: str | None = None
    fenced: bool = False
    for line in body.splitlines():
        heading: re.Match[str] | None = None if fenced else _HEADING.fullmatch(line)
        fence: bool = line.lstrip().startswith(_FENCE)
        if heading is not None:
            section = heading.group(1)
            lines.append(line)
        elif section is not None and section != _EVIDENCE and (fenced or fence or withholds(line)):
            item: re.Match[str] | None = _LIST_ITEM.match(line)
            what: str = "step withheld" if section == _STEPS else "withheld"
            lines.append(f"{item.group(0) if item is not None else ''}[{what}: {why}]")
        else:
            lines.append(line)
        fenced = fenced != fence
    title: int = next((index for index, line in enumerate(lines) if line.startswith("# ")), -1)
    return "\n".join((*lines[: title + 1], *([""] if title >= 0 else []), warning, *lines[title + 1 :]))


def presented(skill: Skill, meta: PlaybookMeta) -> Skill:
    """The learned skill ``skill`` as a run is offered it, by its record ``meta``: as written when the run whose steps
    it holds read no content third parties can author, or the owner trusts it; else, its record saying so or older
    than the mark (``tainted`` ``None``), with the lines that could carry that content's instructions withheld
    (:func:`withheld_body`) and its description marked, withheld too when it names what a line is withheld for."""
    if meta.tainted is False:
        return skill
    why: str = _THIRD_PARTY if meta.tainted else _UNKNOWN_ORIGIN
    warning: str = _THIRD_PARTY_WARNING if meta.tainted else _UNKNOWN_WARNING
    description: str = f"[withheld: {why}]" if withholds(skill.description) else skill.description
    return skill.model_copy(
        update={"description": f"({why}) {description}", "body": withheld_body(skill.body, why, warning)}
    )


def offered_skills(
    skills: Sequence[Skill], entries: Sequence[PlaybookEntry], agent_id: str | None
) -> tuple[Skill, ...]:
    """``skills`` without the learned ones a run of ``agent_id`` is not offered (:func:`offered_to`), each learned one
    as its record has it offered (:func:`presented`); a learned skill without a readable record among the active
    ``entries`` is not offered."""
    offered: dict[str, PlaybookMeta] = {
        entry.meta.name: entry.meta for entry in entries if not entry.retired and offered_to(entry.meta, agent_id)
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
def _changing(root: Path) -> Iterator[None]:
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
    with _changing(root):
        if file.is_file():
            raise FileExistsError(f"the learned skill {playbook.name} is there already")
        staging: Path = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=root))
        try:
            _save_meta(staging, meta_of(playbook, None))
            write_whole(staging / SKILL_FILE, text)
            if folder.is_dir():
                shutil.rmtree(folder)
            staging.rename(folder)
        finally:
            if staging.is_dir():
                shutil.rmtree(staging)
    return file


def update_playbook(root: Path, name: str, playbook: Playbook) -> Path:
    """Rewrite the active skill ``name`` from ``playbook`` (a later run of the same kind): the steps are the new
    run's, the evidence keeps every run. Returns its SKILL.md."""
    folder: Path = playbook_folder(root, name)
    renamed: Playbook = playbook.model_copy(update={"name": name})
    file: Path = folder / SKILL_FILE
    with _changing(root):
        earlier: PlaybookMeta = read_meta(folder)
        write_whole(file, rendered_skill(file, renamed, earlier))
        _save_meta(folder, meta_of(renamed, earlier))
    return file


def read_meta(folder: Path) -> PlaybookMeta:
    """The record of the playbook in ``folder``; a missing or malformed one is not found."""
    file: Path = folder / META_FILE
    try:
        return PlaybookMeta.model_validate(json.loads(file.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise NotFoundError("the playbook has no readable record", {"path": str(file), "error": str(exc)}) from exc


def record_use(root: Path, name: str, succeeded: bool) -> PlaybookMeta | None:
    """Count a run that read the playbook ``name`` and ended; a playbook that :func:`retires` is moved under
    ``.retired``, where an earlier one of its name keeps its folder. Returns the record after the count, or None for a playbook that is no longer active (another run
    retired it or the owner deleted it since this run read it), which is not counted."""
    folder: Path = playbook_folder(root, name)
    with _changing(root):
        if not folder.is_dir():
            return None
        meta: PlaybookMeta = recorded(read_meta(folder), succeeded)
        _save_meta(folder, meta)
        if retires(meta):
            retired: Path = root / RETIRED_DIR / name
            retired.parent.mkdir(parents=True, exist_ok=True)
            if retired.exists():
                # A skill of the name retired before keeps its folder, under the next free name (item 306).
                shutil.move(str(retired), str(_unused(root / RETIRED_DIR, name)))
            shutil.move(str(folder), str(retired))
    return meta


def _unused(parent: Path, name: str) -> Path:
    """The first of ``name-2``, ``name-3``, ... that ``parent`` holds nothing of."""
    return next(candidate for number in count(2) if not (candidate := parent / f"{name}-{number}").exists())


def _folder_of(root: Path, name: str) -> Path:
    """The folder of the playbook ``name``, active or retired; one that does not exist is not found."""
    for folder in (playbook_folder(root, name), root / RETIRED_DIR / name):
        if folder.is_dir():
            return folder
    raise NotFoundError("there is no such learned skill", {"name": name})


def _save_meta(folder: Path, meta: PlaybookMeta) -> None:
    write_whole(folder / META_FILE, meta.model_dump_json(indent=2) + "\n")


def drop_playbook(root: Path, name: str) -> Path:
    """Remove the playbook ``name``, active or retired; one that does not exist is not found."""
    with _changing(root):
        folder: Path = _folder_of(root, name)
        shutil.rmtree(folder)
    return folder


def share_playbook(root: Path, name: str, shared: bool) -> PlaybookMeta:
    """Mark the playbook ``name`` (active or retired) as offered to every run, or to its agent's alone again; Dot's
    own is offered to every run either way. Returns the record after it."""
    with _changing(root):
        folder: Path = _folder_of(root, name)
        meta: PlaybookMeta = read_meta(folder).model_copy(update={"shared": shared})
        _save_meta(folder, meta)
    return meta


def trust_playbook(root: Path, name: str) -> PlaybookMeta:
    """The owner's word that the playbook ``name`` (active or retired) may be offered as written although a run it
    comes from read content third parties can author, or its record is older than the mark: ``tainted`` is ``False``
    until a run that read such content rewrites its steps. Returns the record after it."""
    with _changing(root):
        folder: Path = _folder_of(root, name)
        meta: PlaybookMeta = read_meta(folder).model_copy(update={"tainted": False})
        _save_meta(folder, meta)
    log.info("playbook_trusted", name=name, tainted_runs=list(meta.tainted_runs))
    return meta


def release_playbooks(root: Path, agent_id: str) -> int:
    """Make the playbooks of the agent ``agent_id`` Dot's (an agent that was deleted); how many there were."""
    released: int = 0
    with _changing(root):
        for entry in list_playbooks(root):
            if entry.meta.agent_id != agent_id:
                continue
            _save_meta(_folder_of(root, entry.meta.name), entry.meta.model_copy(update={"agent_id": None}))
            released += 1
    return released


def _entries(folder: Path, retired: bool) -> list[PlaybookEntry]:
    entries: list[PlaybookEntry] = []
    if not folder.is_dir():
        return entries
    for child in sorted(folder.iterdir()):
        file: Path = child / SKILL_FILE
        if not child.name.startswith((PLAYBOOK_PREFIX, _OLD_PREFIX)) or not file.is_file():
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


async def offered_to_run(config: AppConfig, skills: SkillCatalog, agent_id: str | None) -> tuple[Skill, ...]:
    """The skills of ``skills`` that a run of the agent ``agent_id`` is offered: every one but the learned skills of
    other agents (item 94, :func:`offered_skills`); a learned skill whose files cannot be read is not offered
    (:func:`list_playbooks`)."""
    root: Path | None = playbook_root(config)
    if root is None:
        return skills.skills
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, root)
    return offered_skills(skills.skills, entries, agent_id)


async def _record_uses(runtime: "Runtime", run: RunRecord, status: RunStatus, events: Sequence[EventRecord]) -> None:
    root: Path = playbooks_dir()
    for name in used_playbooks(events):
        meta: PlaybookMeta | None = await asyncio.to_thread(record_use, root, name, status == "done")
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
    runs). A skill of another owner, shared or not, is never rewritten by a run that is not its owner's."""
    return [entry for entry in entries if not entry.retired and entry.meta.agent_id == agent_id]


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


async def _write_from(
    runtime: "Runtime", run: RunRecord, steps: Sequence[ActingStep], host: str, tainted: bool
) -> tuple[Path, str, bool]:
    """Ask the review model for the skill of ``run`` and write it, its secrets masked (:func:`redacted_draft`): a new
    folder, or the folder of the run's owner's skill of the same kind. Returns the SKILL.md, the skill's name and
    whether it was updated rather than written.
    ``tainted``: the run read content third parties can author, so the writer sees what the agent did and not what the
    calls returned, and the skill's record says so (:func:`presented`)."""
    root: Path = playbooks_dir()
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, root)
    own: list[PlaybookEntry] = owned(entries, run.agent_id)
    writer: PlaybookWriter = playbook_writer_for(
        runtime.router,
        runtime.roles.review(),
        (run.model,),
        runtime.config.agent.model_call_timeout_s,
        f"{run.thread_id}:playbook",
        run.id,
    )
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    written: PlaybookDraft = await writer(
        PlaybookRequest(
            goal=run.goal,
            host=host,
            digest=own_steps_digest(steps) if tainted else acting_digest(steps),
            answer=_ANSWER_LEFT_OUT if tainted else run.result,
            kinds=kinds_shown(own, host),
        )
    )
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
    events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _EVENTS_READ)
    await _record_uses(runtime, run, status, events)
    steps: tuple[ActingStep, ...] = acting_steps(events)
    if not distils(status, len(steps), runtime.config.agent.skill_min_steps):
        if status == "done":
            log.info(
                "playbook_skipped", run_id=run.id, reason="fewer acting steps than skill_min_steps", steps=len(steps)
            )
        return SKIPPED
    host: str = main_site(events) or NO_SITE
    tainted: bool = await runtime.store.read_outside(run.id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS))
    try:
        file, name, updated = await _write_from(runtime, run, steps, host, tainted)
    except (DotError, OSError) as error:
        message: str = error.message if isinstance(error, DotError) else str(error)
        log.warning("playbook_failed", run_id=run.id, host=host, error=message)
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
    events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _EVENTS_READ)
    steps: tuple[ActingStep, ...] = acting_steps(events)
    why: str | None = unsavable(run, len(steps))
    if why is not None:
        raise InvalidValueError(f"this run cannot be saved as a skill: {why}", {"run_id": run.id})
    host: str = main_site(events) or NO_SITE
    tainted: bool = await runtime.store.read_outside(run.id, tuple(TAINTING_KINDS), tuple(OUTSIDE_CHANNELS))
    file, name, updated = await _write_from(runtime, run, steps, host, tainted)
    log.info("playbook_saved", run_id=run.id, name=name, host=host, path=str(file), updated=updated, tainted=tainted)
    await runtime.bus.emit(
        run.id, PLAYBOOK_WRITTEN, {"name": name, "host": host, "path": str(file), "updated": updated}, True
    )
    return name, updated
