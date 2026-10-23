"""The versions of a learned skill and its rest (item 585).

A rewrite by a later run, a restore and a merge keep the SKILL.md they replace whole, as ``.versions/<n>.md`` in the
skill's own folder (:func:`dotagent.agent.playbooks.archive_version`), and the skill's record says how each version
came to be and whether its steps carry the mark of content third parties can author. This module lists the versions
(:func:`versions_of`) and puts one back (:func:`restore_playbook`): a restore is itself a new version, so it too can be
taken back, and it brings back the mark the version had, never the one the skill has now. It also keeps what the owner
decides of a skill's rest: pin it (it never rests), put it to rest, wake it (:func:`pin_playbook`,
:func:`rest_playbook`, :func:`wake_playbook`), and gives a record that counts uses and has no date of its last read one
(:func:`settle_read_dates`), so that it rests after the days pass and not at the first start. Nothing is deleted.
"""

import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

import yaml

from dotagent.agent.playbooks import (
    VERSIONS_DIR,
    PlaybookMeta,
    SkillVersion,
    archive_version,
    changing,
    folder_of,
    list_playbooks,
    noted,
    read_meta,
    replace_skill,
    save_meta,
)
from dotagent.core.errors import InvalidValueError, NotFoundError, SkillError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.skills.catalog import PLAYBOOK_SOURCE
from dotagent.skills.parse import parse_skill, split_frontmatter
from dotagent.skills.types import SKILL_FILE

log = get_logger(__name__)

_SECTION: re.Pattern[str] = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_NUMBERED: re.Pattern[str] = re.compile(r"^\s*\d+\.\s+(.*\S)\s*$")
_BULLET: re.Pattern[str] = re.compile(r"^\s*-\s+(.*\S)\s*$")
_ARCHIVED: re.Pattern[str] = re.compile(r"^(\d+)\.md$")
# What a skill with no parameters lists under Parameters.
_NO_PARAMETERS: str = "none"


class SkillText(FrozenModel):
    """What a SKILL.md says of the work: the parts a page shows and compares."""

    description: str
    kind: str
    when: str
    steps: tuple[str, ...]
    parameters: tuple[str, ...]


type StepSign = Literal[" ", "+", "-"]


def step_changes(before: Sequence[str], after: Sequence[str]) -> tuple[tuple[StepSign, str], ...]:
    """The steps of ``after`` against those of ``before``, as the owner reads a diff (item 585): each step once, in
    order, ``" "`` for one both have, ``"-"`` for one only ``before`` has and ``"+"`` for one only ``after`` has; a
    step that changed is the old one dropped and the new one added."""
    changes: list[tuple[StepSign, str]] = []
    for tag, start, end, other_start, other_end in SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes():
        if tag == "equal":
            changes.extend((" ", step) for step in before[start:end])
            continue
        changes.extend(("-", step) for step in before[start:end])
        changes.extend(("+", step) for step in after[other_start:other_end])
    return tuple(changes)


class VersionDetail(FrozenModel):
    """A version of a skill as the page shows it: its note (:class:`SkillVersion`), whether it is the skill's own now,
    and what its file says."""

    note: SkillVersion
    current: bool
    text: SkillText


def skill_text(text: str, where: Path) -> SkillText:
    """What the SKILL.md ``text`` (read from ``where``) says of the work (:func:`parts_of`). A text that is no skill is
    a :class:`SkillError`."""
    yaml_text, body = split_frontmatter(where, text)
    front: object = yaml.safe_load(yaml_text)
    return parts_of(str(front.get("description", "")) if isinstance(front, dict) else "", body)


def parts_of(description: str, body: str) -> SkillText:
    """What a skill says of the work: its ``description`` and the sections Dot writes in its instructions ``body``
    (``## Kind of work``, ``## When``, ``## Steps``, ``## Parameters``). A skill written by hand with other sections has
    the parts it lacks empty."""
    description = " ".join(description.split())
    marks: list[re.Match[str]] = list(_SECTION.finditer(body))
    sections: dict[str, str] = {
        mark.group(1): body[mark.end() : marks[index + 1].start() if index + 1 < len(marks) else len(body)].strip()
        for index, mark in enumerate(marks)
    }
    steps: tuple[str, ...] = tuple(
        found.group(1) for line in sections.get("Steps", "").splitlines() if (found := _NUMBERED.match(line))
    )
    parameters: tuple[str, ...] = tuple(
        found.group(1)
        for line in sections.get("Parameters", "").splitlines()
        if (found := _BULLET.match(line)) and found.group(1) != _NO_PARAMETERS
    )
    return SkillText(
        description=description,
        kind=" ".join(sections.get("Kind of work", "").split()),
        when=" ".join(sections.get("When", "").split()),
        steps=steps,
        parameters=parameters,
    )


def versions_of(root: Path, name: str) -> tuple[VersionDetail, ...]:
    """The versions of the skill ``name`` (active or retired), the oldest first and the skill's own now last: each with
    its note and what its file says. A version whose file is gone is left out, and so is a copy kept under another name
    (``3-2.md``, which a crash may leave): neither can be put back. A skill not found is :class:`NotFoundError`."""
    with changing(root):
        folder: Path = folder_of(root, name)
        meta: PlaybookMeta = read_meta(folder)
        notes: dict[int, SkillVersion] = {note.n: note for note in noted(meta)}
        details: list[VersionDetail] = []
        kept: Path = folder / VERSIONS_DIR
        for file in sorted(kept.iterdir(), key=lambda entry: entry.name) if kept.is_dir() else ():
            found: re.Match[str] | None = _ARCHIVED.match(file.name)
            # The version the skill has now is listed once, from its SKILL.md: a copy of it under the folder is what
            # an update that stopped before it replaced the file left.
            if found is None or int(found.group(1)) not in notes or int(found.group(1)) == meta.version:
                continue
            number: int = int(found.group(1))
            details.append(
                VersionDetail(
                    note=notes[number],
                    current=False,
                    text=skill_text(file.read_text(encoding="utf-8"), file),
                )
            )
        current_file: Path = folder / SKILL_FILE
        details.sort(key=lambda detail: detail.note.n)
        details.append(
            VersionDetail(
                note=notes[meta.version],
                current=True,
                text=skill_text(current_file.read_text(encoding="utf-8"), current_file),
            )
        )
    return tuple(details)


def _named(text: str, name: str, where: Path) -> str:
    """The SKILL.md ``text`` under the name ``name``, which is its folder's: in its frontmatter and its heading. A skill
    moved aside under another name keeps the name its older versions were written under."""
    yaml_text, body = split_frontmatter(where, text)
    front: object = yaml.safe_load(yaml_text)
    if not isinstance(front, dict):
        raise SkillError("the frontmatter of SKILL.md is not a mapping of keys to values", {"path": str(where)})
    was: object = front.get("name")
    front["name"] = name
    dumped: str = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1 << 16)
    heading: str = f"# {was}\n"
    return f"---\n{dumped}---\n" + (body.replace(heading, f"# {name}\n", 1) if isinstance(was, str) else body)


def restore_playbook(root: Path, name: str, number: int, now: float) -> PlaybookMeta:
    """Put version ``number`` of the skill ``name`` back as the skill's steps (item 585), as a new version of its own
    (``restored``, with the number it came from), so that the put-back can be taken back: the file it replaces is kept
    first. The skill's mark of content third parties can author is the one version ``number`` had (a version the record
    has no note of is of unknown origin, offered as marked), and the failures in a row start again. The version that is
    the skill's own now is :class:`InvalidValueError`, a version with no file :class:`NotFoundError`, and a file that
    is no readable skill :class:`SkillError`, each changing nothing. Returns the record after it."""
    with changing(root):
        folder: Path = folder_of(root, name)
        meta: PlaybookMeta = read_meta(folder)
        if number == meta.version:
            raise InvalidValueError(f"version {number} is the one the skill has now", {"name": name, "version": number})
        source: Path = folder / VERSIONS_DIR / f"{number}.md"
        if not source.is_file():
            raise NotFoundError("the skill has no such version", {"name": name, "version": number})
        file: Path = folder / SKILL_FILE
        text: str = _named(source.read_text(encoding="utf-8"), folder.name, source)
        parse_skill(file, text, PLAYBOOK_SOURCE)
        # The kind the version said is the skill's again, as a merge or a rewrite may have changed it since.
        kind: str = skill_text(text, source).kind or meta.kind
        before: SkillVersion | None = next((note for note in noted(meta) if note.n == number), None)
        marked: bool | None = None if before is None else before.tainted
        archive_version(folder, meta.version)
        made: int = meta.version + 1
        restored: PlaybookMeta = meta.model_copy(
            update={
                "version": made,
                "versions": (
                    *noted(meta),
                    SkillVersion(n=made, at=now, run_id=None, change="restored", tainted=marked, from_version=number),
                ),
                "kind": kind,
                "tainted": marked,
                "updated_at": now,
                "failing": 0,
            }
        )
        replace_skill(folder, text, restored)
    log.info("playbook_restored", name=name, version=number, made=made, tainted=marked)
    return restored


def _changed(root: Path, name: str, update: dict[str, object]) -> PlaybookMeta:
    """The record of the skill ``name`` (active or retired) with ``update`` made to it, written whole."""
    with changing(root):
        folder: Path = folder_of(root, name)
        meta: PlaybookMeta = read_meta(folder).model_copy(update=update)
        save_meta(folder, meta)
    return meta


def pin_playbook(root: Path, name: str, pinned: bool, now: float) -> PlaybookMeta:
    """Pin the skill ``name`` so that it never rests (item 585), or take the pin off. Pinning wakes a skill that rests,
    whether for disuse or by hand or for a merge, and counts its rest from ``now``. Returns the record after it."""
    if pinned:
        return _changed(root, name, {"pinned": True, "rested_at": None, "merged_into": None, "woken_at": now})
    return _changed(root, name, {"pinned": False})


def rest_playbook(root: Path, name: str, now: float) -> PlaybookMeta:
    """Put the skill ``name`` to rest by hand (item 585): it is not offered to a run, is shown as resting and stays so
    until the owner wakes it; a pin comes off. Nothing of it is deleted. Returns the record after it."""
    return _changed(root, name, {"rested_at": now, "pinned": False})


def wake_playbook(root: Path, name: str, now: float) -> PlaybookMeta:
    """Wake the skill ``name`` (item 585): it is offered again, its rest counted from ``now``; what a merge put aside is
    its own skill again. Returns the record after it."""
    return _changed(root, name, {"rested_at": None, "merged_into": None, "woken_at": now})


def settle_read_dates(root: Path, now: float) -> int:
    """Give every active skill whose record counts uses and has no date of its last read one, ``now`` (item 585): a run
    read it before the dates were kept, and a skill is not put to rest for want of a date, so it rests after the days
    pass from here. Returns how many were given one."""
    settled: int = 0
    with changing(root):
        for entry in list_playbooks(root):
            if entry.retired or entry.meta.last_read_at is not None or entry.meta.uses + entry.meta.failures == 0:
                continue
            save_meta(folder_of(root, entry.meta.name), entry.meta.model_copy(update={"last_read_at": now}))
            settled += 1
    return settled
