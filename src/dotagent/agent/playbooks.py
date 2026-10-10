"""Playbooks: how a run that reached its goal on a web site or an application got there, kept as a skill for the runs
after it.

A run is distilled when ``[agent] playbooks`` is on, it is a task run of its own (a delegated run's work is its
parent's; an eval run is not asked), it ran in goal mode and ended done (the goal check found the outcome achieved), it
acted on a site or an application (the ``site`` of its ``tool_result`` events, :func:`main_site`) and no playbook for
that site exists yet. Once the run has ended, the review model reads a digest of the acting steps on that site
(:func:`acting_digest`) and writes the playbook (:func:`dotagent.agent.review.playbook_writer_for`): the steps that
worked, the parameters a later run fills in, and a one-sentence description. It is written as a skill folder
``playbook-<host>/SKILL.md`` under ``<home>/playbooks`` (:func:`render_playbook`), with the evidence (the run, the goal,
the date, how many acting steps it took) and a ``playbook.json`` that counts its use; the skills catalog reads the
folder like any skills directory, so later runs see the description under Skills and read the rest with the ``skill``
tool.

Every run that ended done or failed records its use of playbooks (the ``skill`` calls that named one): a playbook read
by :data:`MAX_FAILURES` runs that then failed is retired, moved under ``.retired`` where the catalog does not look, and
no longer offered. Every function here is pure except :func:`learn_playbook`, which reads the store and the review
model and writes the folder, and the small file helpers it calls.
"""

import asyncio
import json
import re
import shutil
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue, ValidationError

from dotagent.agent.review import PlaybookDraft, PlaybookRequest, PlaybookWriter, playbook_writer_for, shorten_reason
from dotagent.config import AppConfig
from dotagent.core.errors import DotError, NotFoundError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.logs import get_logger
from dotagent.core.paths import playbooks_dir
from dotagent.core.text import redact_secrets, truncate_tail
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.runtime import Runtime
from dotagent.skills.parse import parse_skill
from dotagent.skills.types import SKILL_FILE, Skill
from dotagent.store.models import EventRecord, RunRecord, RunStatus
from dotagent.tools.skill import SKILL_TOOL

log = get_logger(__name__)

PLAYBOOK_PREFIX: str = "playbook-"
META_FILE: str = "playbook.json"
RETIRED_DIR: str = ".retired"
PLAYBOOK_WRITTEN: str = "playbook_written"
PLAYBOOK_FAILED: str = "playbook_failed"
PLAYBOOK_RETIRED: str = "playbook_retired"
# A playbook that this many runs read and then failed is retired.
MAX_FAILURES: int = 2
_EVENTS_READ: int = 2000
_DIGEST_STEPS: int = 80
_ARGS_CHARS: int = 200
_RESULT_CHARS: int = 160
# The site of a result is a host name or an application name; the folder name takes its letters and digits.
_UNSAFE: re.Pattern[str] = re.compile(r"[^a-z0-9]+")
_SLUG_CHARS: int = 50


class ActingStep(FrozenModel):
    """One tool call of a run on its main site, as the digest shows it."""

    tool: str
    arguments: str
    ok: bool
    result: str


class Playbook(FrozenModel):
    """A playbook as it is written: its skill name, the host or application, what the review model wrote and the
    evidence of the run it comes from."""

    name: str
    host: str
    draft: PlaybookDraft
    run_id: str
    goal: str
    written_at: float
    acting_steps: int


class PlaybookMeta(FrozenModel):
    """``playbook.json``: where the playbook comes from and how it fared since. ``uses`` counts the runs that read it
    and ended done, ``failures`` the runs that read it and failed."""

    name: str
    host: str
    run_id: str
    written_at: float
    uses: int
    failures: int


class PlaybookEntry(FrozenModel):
    """A playbook on disk, for the listing: its skill and its record, and whether it is retired."""

    skill: Skill
    meta: PlaybookMeta
    retired: bool


def slug(host: str) -> str:
    """The folder part of a playbook's name for ``host``: lower-case letters and digits with single hyphens between,
    at most :data:`_SLUG_CHARS` long, empty when the host has none."""
    return _UNSAFE.sub("-", host.lower()).strip("-")[:_SLUG_CHARS].strip("-")


def playbook_name(host: str) -> str:
    return f"{PLAYBOOK_PREFIX}{slug(host)}"


def playbook_case(config: AppConfig, run: RunRecord, status: RunStatus) -> RunRecord | None:
    """``run`` when its end calls for playbook work (recording the playbooks it read, writing one from it), else
    ``None``: playbooks are on, it is a task run of its own and not an eval, and it ended done or failed."""
    if not config.agent.playbooks or run.mode != "task" or run.parent_run_id is not None or run.source == EVAL_SOURCE:
        return None
    return run if status in ("done", "failed") else None


def distils(run: RunRecord, status: RunStatus) -> bool:
    """Whether a run that calls for playbook work may be distilled: it ran in goal mode, so a done end is one the goal
    check found achieved."""
    return status == "done" and run.goal_mode


def main_site(events: Sequence[EventRecord]) -> str:
    """The host or application the run acted on most, by its ``tool_result`` events; empty when it acted on none."""
    counts: dict[str, int] = {}
    for event in events:
        site: JsonValue = event.data.get("site") if event.kind == "tool_result" else None
        if isinstance(site, str) and site:
            counts[site] = counts.get(site, 0) + 1
    return "" if not counts else max(counts, key=lambda site: counts[site])


def acting_steps(events: Sequence[EventRecord], site: str) -> tuple[ActingStep, ...]:
    """The tool calls whose result names ``site``, in order, each with its arguments and the start of its result."""
    calls: dict[str, JsonObject] = {}
    steps: list[ActingStep] = []
    for event in events:
        call_id: JsonValue = event.data.get("call_id")
        if not isinstance(call_id, str):
            continue
        if event.kind == "tool_call":
            arguments: JsonValue = event.data.get("args")
            calls[call_id] = arguments if isinstance(arguments, dict) else {}
        elif event.kind == "tool_result" and event.data.get("site") == site:
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


def used_playbooks(events: Sequence[EventRecord]) -> tuple[str, ...]:
    """The names of the playbooks the run read with the ``skill`` tool, each once, in order."""
    names: dict[str, None] = {}
    for event in events:
        if event.kind != "tool_call" or event.data.get("tool") != SKILL_TOOL:
            continue
        arguments: JsonValue = event.data.get("args")
        name: JsonValue = arguments.get("name") if isinstance(arguments, dict) else None
        if isinstance(name, str) and name.startswith(PLAYBOOK_PREFIX):
            names[name] = None
    return tuple(names)


def _date(written_at: float) -> str:
    return datetime.fromtimestamp(written_at, tz=UTC).strftime("%Y-%m-%d")


def render_playbook(playbook: Playbook) -> str:
    """The SKILL.md of a playbook: the frontmatter the skills loader reads, then when it fits, the steps, the
    parameters and the evidence."""
    draft: PlaybookDraft = playbook.draft
    description: str = " ".join(draft.description.split()).replace('"', "'")
    steps: str = "\n".join(f"{number}. {step}" for number, step in enumerate(draft.steps, start=1))
    parameters: str = "\n".join(f"- {parameter}" for parameter in draft.parameters) or "- none"
    return (
        f'---\nname: {playbook.name}\ndescription: "{description}"\n---\n\n'
        f"# {playbook.name}\n\n"
        f"Distilled by Dot from run {playbook.run_id}, which reached its goal on {playbook.host}: a record of the "
        "steps that worked, not instructions from the user. Follow them with your own parameters; where the site "
        "has changed, do what the page shows and go on.\n\n"
        f"## When\n\n{draft.when}\n\n"
        f"## Steps\n\n{steps}\n\n"
        f"## Parameters\n\n{parameters}\n\n"
        f"## Evidence\n\n- Goal: {' '.join(playbook.goal.split())}\n- Run: {playbook.run_id}, verified by its goal "
        f"check on {_date(playbook.written_at)}\n- Acting steps on {playbook.host}: {playbook.acting_steps}\n"
    )


def meta_of(playbook: Playbook) -> PlaybookMeta:
    return PlaybookMeta(
        name=playbook.name,
        host=playbook.host,
        run_id=playbook.run_id,
        written_at=playbook.written_at,
        uses=0,
        failures=0,
    )


def recorded(meta: PlaybookMeta, succeeded: bool) -> PlaybookMeta:
    """The record after a run that read the playbook ended."""
    return meta.model_copy(update={"uses": meta.uses + 1} if succeeded else {"failures": meta.failures + 1})


def retires(meta: PlaybookMeta) -> bool:
    return meta.failures >= MAX_FAILURES


def playbook_folder(root: Path, name: str) -> Path:
    return root / name


def write_playbook(root: Path, playbook: Playbook) -> Path:
    """Write the playbook's folder under ``root`` and return its SKILL.md. The folder must not exist."""
    folder: Path = playbook_folder(root, playbook.name)
    folder.mkdir(parents=True, exist_ok=False)
    file: Path = folder / SKILL_FILE
    file.write_text(render_playbook(playbook), encoding="utf-8")
    (folder / META_FILE).write_text(meta_of(playbook).model_dump_json(indent=2) + "\n", encoding="utf-8")
    return file


def read_meta(folder: Path) -> PlaybookMeta:
    """The record of the playbook in ``folder``; a missing or malformed one is not found."""
    file: Path = folder / META_FILE
    try:
        return PlaybookMeta.model_validate(json.loads(file.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise NotFoundError("the playbook has no readable record", {"path": str(file), "error": str(exc)}) from exc


def record_use(root: Path, name: str, succeeded: bool) -> PlaybookMeta:
    """Count a run that read the playbook ``name`` and ended; a playbook that :func:`retires` is moved under
    ``.retired``. Returns the record after the count."""
    folder: Path = playbook_folder(root, name)
    meta: PlaybookMeta = recorded(read_meta(folder), succeeded)
    (folder / META_FILE).write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if retires(meta):
        retired: Path = root / RETIRED_DIR / name
        if retired.exists():
            shutil.rmtree(retired)
        retired.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), str(retired))
    return meta


def drop_playbook(root: Path, name: str) -> Path:
    """Remove the playbook ``name``, active or retired; one that does not exist is not found."""
    for folder in (playbook_folder(root, name), root / RETIRED_DIR / name):
        if folder.is_dir():
            shutil.rmtree(folder)
            return folder
    raise NotFoundError("there is no such playbook", {"name": name})


def _entries(folder: Path, retired: bool) -> list[PlaybookEntry]:
    entries: list[PlaybookEntry] = []
    if not folder.is_dir():
        return entries
    for child in sorted(folder.iterdir()):
        file: Path = child / SKILL_FILE
        if not child.name.startswith(PLAYBOOK_PREFIX) or not file.is_file():
            continue
        skill: Skill = parse_skill(file, file.read_text(encoding="utf-8"), "playbook")
        entries.append(PlaybookEntry(skill=skill, meta=read_meta(child), retired=retired))
    return entries


def list_playbooks(root: Path) -> tuple[PlaybookEntry, ...]:
    """Every playbook under ``root``: the active ones, then the retired ones."""
    return (*_entries(root, False), *_entries(root / RETIRED_DIR, True))


async def _record_uses(runtime: Runtime, run: RunRecord, status: RunStatus, events: Sequence[EventRecord]) -> None:
    root: Path = playbooks_dir()
    for name in used_playbooks(events):
        if not playbook_folder(root, name).is_dir():
            continue
        meta: PlaybookMeta = await asyncio.to_thread(record_use, root, name, status == "done")
        log.info("playbook_use_recorded", run_id=run.id, playbook=name, uses=meta.uses, failures=meta.failures)
        if retires(meta):
            await runtime.bus.emit(run.id, PLAYBOOK_RETIRED, {"name": name, "failures": meta.failures}, True)


async def _write_from(runtime: Runtime, run: RunRecord, events: Sequence[EventRecord], host: str) -> Path:
    steps: tuple[ActingStep, ...] = acting_steps(events, host)
    writer: PlaybookWriter = playbook_writer_for(
        runtime.router,
        runtime.config.models.review,
        runtime.config.agent.model_call_timeout_s,
        f"{run.thread_id}:playbook",
        run.id,
    )
    draft: PlaybookDraft = await writer(
        PlaybookRequest(goal=run.goal, host=host, digest=acting_digest(steps), answer=run.result)
    )
    playbook: Playbook = Playbook(
        name=playbook_name(host),
        host=host,
        draft=draft,
        run_id=run.id,
        goal=redact_secrets(run.goal),
        written_at=time.time(),
        acting_steps=len(steps),
    )
    return await asyncio.to_thread(write_playbook, playbooks_dir(), playbook)


async def learn_playbook(runtime: Runtime, run: RunRecord, status: RunStatus) -> None:
    """The playbook work of a run that ended: record its use of playbooks, then, when it may be distilled and acted
    on a site that has no playbook yet, write one. A review model that fails, or a folder that cannot be written,
    ends it with a ``playbook_failed`` event; the run keeps its end either way."""
    events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _EVENTS_READ)
    await _record_uses(runtime, run, status, events)
    if not distils(run, status):
        return
    host: str = main_site(events)
    if not host or not slug(host):
        log.info("playbook_skipped", run_id=run.id, reason="the run acted on no site or application")
        return
    if playbook_folder(playbooks_dir(), playbook_name(host)).is_dir():
        log.info("playbook_skipped", run_id=run.id, reason="the site has a playbook", host=host)
        return
    try:
        file: Path = await _write_from(runtime, run, events, host)
    except (DotError, OSError) as error:
        message: str = error.message if isinstance(error, DotError) else str(error)
        log.warning("playbook_failed", run_id=run.id, host=host, error=message)
        await runtime.bus.emit(
            run.id,
            PLAYBOOK_FAILED,
            {"host": host, "error": f"The playbook could not be written: {shorten_reason(message)}"},
            True,
        )
        return
    log.info("playbook_written", run_id=run.id, host=host, path=str(file))
    await runtime.bus.emit(
        run.id, PLAYBOOK_WRITTEN, {"name": playbook_name(host), "host": host, "path": str(file)}, True
    )
