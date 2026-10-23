"""``dotx playbooks`` and the learned skills' part of ``dotx skills``: the playbooks Dot distilled from its runs, what
each holds and where it comes from, dropping one and trusting one learned from third-party content; and (item 585) the
versions of a skill and putting one back, a skill's rest (pin, rest, wake) and the merges that wait for the owner's
yes."""

import asyncio
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dotagent.agent.playbooks import (
    PlaybookEntry,
    PlaybookMeta,
    SkillVersion,
    drop_playbook,
    list_playbooks,
    skill_resting,
    trust_playbook,
)
from dotagent.agent.skill_history import (
    VersionDetail,
    pin_playbook,
    rest_playbook,
    restore_playbook,
    step_changes,
    versions_of,
    wake_playbook,
)
from dotagent.agent.skill_proposals import (
    FieldChange,
    ProposalView,
    accept_proposal,
    dismiss_proposal,
    waiting_proposals,
)
from dotagent.cli_commands import (
    Command,
    PlaybooksDropCommand,
    PlaybooksListCommand,
    PlaybooksShowCommand,
    PlaybooksTrustCommand,
    SkillsDecideCommand,
    SkillsKeepCommand,
    SkillsProposalsCommand,
    SkillsRestoreCommand,
    SkillsVersionsCommand,
)
from dotagent.config import load_user_config
from dotagent.core.errors import DotError, InvalidValueError, NotFoundError
from dotagent.core.paths import playbooks_dir

_DESCRIPTION_CHARS: int = 90


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def origin(meta: PlaybookMeta) -> str | None:
    """What the listing says of where a playbook's steps come from, or ``None`` for steps runs are offered as
    written."""
    if meta.tainted is None:
        return "origin unknown"
    return "learned from third-party content" if meta.tainted else None


def rest_of(meta: PlaybookMeta, now: float, rest_days: int) -> str | None:
    """What the listing says of a skill's rest (item 585): ``pinned``, why it rests, or ``None`` for one that is
    awake."""
    if meta.pinned:
        return "pinned"
    if not skill_resting(meta, now, rest_days):
        return None
    if meta.merged_into is not None:
        return f"resting, merged into {meta.merged_into}"
    if meta.rested_at is not None:
        return "resting, put to rest by hand"
    return f"resting, no run read it for {rest_days} days"


def render_entry(entry: PlaybookEntry, now: float | None = None, rest_days: int = 60) -> str:
    """A skill's line: its name, how it fared, whether it was rewritten (its version), rests or is pinned (said only
    when the time ``now`` is given, with the days a skill is unread before it rests) or is marked, and its
    description."""
    meta: PlaybookMeta = entry.meta
    state: str = "retired" if entry.retired else f"used {meta.uses}, failed {meta.failures}"
    notes: list[str] = [state]
    if meta.version > 1:
        notes.append(f"version {meta.version}")
    rest: str | None = None if entry.retired or now is None else rest_of(meta, now, rest_days)
    if rest is not None:
        notes.append(rest)
    marked: str | None = origin(meta)
    if marked is not None:
        notes.append(marked)
    return f"  {entry.skill.name}  [{', '.join(notes)}]  {_clip(entry.skill.description, _DESCRIPTION_CHARS)}"


def render_list(
    entries: tuple[PlaybookEntry, ...], root: Path, now: float | None = None, rest_days: int = 60
) -> list[str]:
    if not entries:
        return [f"No learned skills yet (a task run that ends done after enough acting steps writes one under {root})."]
    lines: list[str] = [
        f"Learned skills ({len(entries)}):",
        *(render_entry(entry, now, rest_days) for entry in entries),
    ]
    if any(origin(entry.meta) is not None for entry in entries):
        lines.append(
            "Runs are offered a playbook marked as learned from third-party content, or of unknown origin, with the "
            "steps that name an address, run a command or reach the home folder or the Mac's own folders withheld; "
            "'dotx playbooks trust "
            "<name>' offers one with all its steps."
        )
    if now is not None and any(rest_of(entry.meta, now, rest_days) not in (None, "pinned") for entry in entries):
        lines.append(
            "A skill that rests is not offered to runs and nothing of it is deleted: 'dotx skills wake <name>' wakes "
            "it, 'dotx skills pin <name>' keeps it from resting."
        )
    return lines


async def cmd_playbooks_list(command: PlaybooksListCommand) -> int:
    root: Path = playbooks_dir()
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, root)
    days: int = load_user_config().agent.skill_rest_days
    sys.stdout.write("\n".join(render_list(entries, root, time.time(), days)) + "\n")
    return 0


async def cmd_playbooks_show(command: PlaybooksShowCommand) -> int:
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
    found: PlaybookEntry | None = next((entry for entry in entries if entry.skill.name == command.playbook), None)
    if found is None:
        sys.stderr.write(f"there is no playbook called {command.playbook}\n")
        return 1
    days: int = load_user_config().agent.skill_rest_days
    sys.stdout.write(f"{render_entry(found, time.time(), days).strip()}\n{found.skill.root}\n\n{found.skill.body}\n")
    return 0


async def cmd_playbooks_drop(command: PlaybooksDropCommand) -> int:
    try:
        folder: Path = await asyncio.to_thread(drop_playbook, playbooks_dir(), command.playbook)
    except NotFoundError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 1
    sys.stdout.write(f"dropped {command.playbook} ({folder})\n")
    return 0


async def cmd_playbooks_trust(command: PlaybooksTrustCommand) -> int:
    try:
        meta: PlaybookMeta = await asyncio.to_thread(trust_playbook, playbooks_dir(), command.playbook)
    except NotFoundError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 1
    runs: str = ", ".join(meta.tainted_runs) or "none recorded"
    sys.stdout.write(
        f"trusted {command.playbook}: runs are offered all its steps until a run that reads third-party content "
        f"rewrites them (runs that read such content: {runs})\n"
    )
    return 0


def _when(at: float) -> str:
    return datetime.fromtimestamp(at, tz=UTC).strftime("%Y-%m-%d %H:%M")


def describe_version(note: SkillVersion) -> str:
    """How a version came to be, in the words of the listing: its change, the run or the owner who made it and its
    mark."""
    made: str = {
        "learned": "learned",
        "relearned": "rewritten by a later run",
        "taught": "taught by you",
        "restored": f"restored from version {note.from_version}",
        "merged": "merged with " + (", ".join(note.merged) or "another skill"),
        "revised": "revised at your word" + (f": {note.words}" if note.words else ""),
    }[note.change]
    by: str = "" if note.run_id is None else f", run {note.run_id}"
    mark: str = (
        ", origin unknown" if note.tainted is None else (", learned from third-party content" if note.tainted else "")
    )
    return f"{made}{by}{mark}"


def render_versions(name: str, details: tuple[VersionDetail, ...]) -> list[str]:
    """The versions of a skill, the oldest first, each with when and how it was made and, but for the first, the steps
    it changed from the version before it (``-`` dropped, ``+`` added)."""
    lines: list[str] = [f"{name}: {len(details)} {'version' if len(details) == 1 else 'versions'}"]
    earlier: VersionDetail | None = None
    for detail in details:
        note: SkillVersion = detail.note
        now_mark: str = "  (current)" if detail.current else ""
        lines.append(f"  version {note.n}  {_when(note.at)}  {describe_version(note)}{now_mark}")
        if earlier is not None:
            lines.extend(
                f"      {sign} {step}"
                for sign, step in step_changes(earlier.text.steps, detail.text.steps)
                if sign != " "
            )
        earlier = detail
    if len(details) > 1:
        lines.append("'dotx skills restore <name> <version>' puts a version back as a new version of its own.")
    return lines


def _failed(error: DotError) -> int:
    sys.stderr.write(f"{error.message}\n")
    return 1


async def cmd_skills_versions(command: SkillsVersionsCommand) -> int:
    try:
        details: tuple[VersionDetail, ...] = await asyncio.to_thread(versions_of, playbooks_dir(), command.skill)
    except DotError as error:
        return _failed(error)
    sys.stdout.write("\n".join(render_versions(command.skill, details)) + "\n")
    return 0


async def cmd_skills_restore(command: SkillsRestoreCommand) -> int:
    try:
        meta: PlaybookMeta = await asyncio.to_thread(
            restore_playbook, playbooks_dir(), command.skill, command.version, time.time()
        )
    except DotError as error:
        return _failed(error)
    marked: str = (
        "" if meta.tainted is False else " (offered with its addresses and commands withheld: it carries the mark)"
    )
    sys.stdout.write(
        f"restored version {command.version} of {command.skill} as version {meta.version}{marked}; "
        f"'dotx skills restore {command.skill} <version>' takes it back\n"
    )
    return 0


async def cmd_skills_keep(command: SkillsKeepCommand) -> int:
    root: Path = playbooks_dir()
    now: float = time.time()
    try:
        match command.name:
            case "skills-pin":
                await asyncio.to_thread(pin_playbook, root, command.skill, True, now)
                said: str = f"pinned {command.skill}: it never rests"
            case "skills-unpin":
                await asyncio.to_thread(pin_playbook, root, command.skill, False, now)
                said = f"took the pin off {command.skill}"
            case "skills-rest":
                await asyncio.to_thread(rest_playbook, root, command.skill, now)
                said = f"{command.skill} rests: runs are not offered it until you wake it, and nothing of it is deleted"
            case "skills-wake":
                await asyncio.to_thread(wake_playbook, root, command.skill, now)
                said = f"{command.skill} is awake: runs are offered it again"
    except DotError as error:
        return _failed(error)
    sys.stdout.write(f"{said}\n")
    return 0


def render_field(field: FieldChange) -> list[str]:
    """One field of what a yes would write (item 585): a text on its line, with ``(unchanged)`` when the skill says it
    already, else the old text after a ``-`` and the new after a ``+``; the lines of a list each after the sign that
    says whether the skill keeps, drops or gets it. Nothing is shortened: it is what is written."""
    if field.many and not field.lines:
        return [f"      {field.label}: none"]
    if not field.many and not field.changed:
        said: str = "" if field.field == "name" else "  (unchanged)"
        return [f"      {field.label}: {field.lines[0].text}{said}"]
    return [f"      {field.label}:", *(f"        {line.sign} {line.text}" for line in field.lines)]


def render_proposals(views: tuple[ProposalView, ...]) -> list[str]:
    """The proposals that wait: each with its id, what it does, why, every field the yes would write to the first
    skill against what it says now (:func:`render_field`: its name, description, kind of work, when, steps and
    parameters), what becomes of each other skill and, when it cannot be applied now, why not."""
    if not views:
        return ["Nothing waits for your yes: no merge of learned skills is proposed."]
    lines: list[str] = [f"Waiting for your yes ({len(views)}):"]
    for view in views:
        proposal = view.proposal
        first, *others = proposal.names
        what: str = f"merge {' and '.join(proposal.names)}" if proposal.kind == "merge" else f"rewrite {first}"
        marked: str = ", carries the mark of third-party content" if proposal.tainted else ""
        lines.append(f"  {proposal.id}  {what}  ({_clip(proposal.reason, _DESCRIPTION_CHARS)}{marked})")
        lines.append(f"      a yes writes this to {first}, against what it says now ('-' goes, '+' is written):")
        for field in view.fields:
            lines.extend(render_field(field))
        lines.extend(f"      {name} rests, kept whole: 'dotx skills wake {name}' offers it again" for name in others)
        if view.stale is not None:
            lines.append(f"      cannot be applied now: {view.stale}")
    lines.append(
        "'dotx skills accept <id>' writes it as a new version (the old file is kept); "
        "'dotx skills dismiss <id>' drops it."
    )
    return lines


async def cmd_skills_proposals(command: SkillsProposalsCommand) -> int:
    views: tuple[ProposalView, ...] = await asyncio.to_thread(waiting_proposals, playbooks_dir())
    sys.stdout.write("\n".join(render_proposals(views)) + "\n")
    return 0


async def cmd_skills_decide(command: SkillsDecideCommand) -> int:
    root: Path = playbooks_dir()
    try:
        if command.name == "skills-accept":
            meta: PlaybookMeta = await asyncio.to_thread(accept_proposal, root, command.proposal_id, time.time())
            sys.stdout.write(
                f"accepted: {meta.name} is version {meta.version}, its old file kept; "
                f"'dotx skills versions {meta.name}' shows it\n"
            )
        else:
            await asyncio.to_thread(dismiss_proposal, root, command.proposal_id)
            sys.stdout.write(f"dismissed {command.proposal_id}\n")
    except DotError as error:
        return _failed(error)
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case PlaybooksListCommand():
            return await cmd_playbooks_list(command)
        case PlaybooksShowCommand():
            return await cmd_playbooks_show(command)
        case PlaybooksDropCommand():
            return await cmd_playbooks_drop(command)
        case PlaybooksTrustCommand():
            return await cmd_playbooks_trust(command)
        case SkillsVersionsCommand():
            return await cmd_skills_versions(command)
        case SkillsRestoreCommand():
            return await cmd_skills_restore(command)
        case SkillsKeepCommand():
            return await cmd_skills_keep(command)
        case SkillsProposalsCommand():
            return await cmd_skills_proposals(command)
        case SkillsDecideCommand():
            return await cmd_skills_decide(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
