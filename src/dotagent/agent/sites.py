"""What earlier runs learned on a site or in an application, handed to a run when it gets there (item 308).

A lesson is tagged with the sites and applications of its run that it names (:func:`site_tags`): the page hosts and
the applications its calls reached, as the ``tool_result`` events record them
(:func:`dotagent.agent.executor.reached_sites`). A run that reaches a site or drives an application for the first time
is handed, after that step, the lessons tagged with it and the learned skills written there that it was not handed yet
(:func:`site_knowledge`, :func:`site_note`): what Dot learned on a site comes back the next time a run is there, also
when the run's goal does not name it. Every function here is pure except :func:`site_knowledge`.
"""

import asyncio
import re
import time
from collections.abc import Iterable, Sequence, Set
from pathlib import Path

from dotagent.agent.playbooks import PlaybookEntry, list_playbooks
from dotagent.config import MemoryConfig
from dotagent.core.types import FrozenModel
from dotagent.memory.render import render_note
from dotagent.memory.service import normalize_tags, recent_scores, resting, visible_to
from dotagent.memory.words import lowercase
from dotagent.runtime import Runtime
from dotagent.skills.catalog import PLAYBOOK_SOURCE, playbook_root
from dotagent.skills.types import Skill
from dotagent.store.models import MemoryRecord, MemoryScore

# The first word of a site's tag: ``site-amazon-com``, ``site-notes``.
SITE_TAG: str = "site"
# The most lessons and learned skills one site brings a run.
MOST_SITE_LESSONS: int = 5
MOST_SITE_SKILLS: int = 3
# The lessons of a site read to pick from, the most recently changed first.
_SITE_LESSONS_READ: int = 50
# A host's main label shorter than this names too many things (``x``, ``bbc``) to be matched in a text by itself.
_SHORT_LABEL: int = 4
# The labels a country's domains put before its code (``amazon.com.tr``, ``bbc.co.uk``).
_SECOND_LEVEL: frozenset[str] = frozenset({"ac", "co", "com", "edu", "gov", "net", "org"})
_HOST: re.Pattern[str] = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


class SiteKnowledge(FrozenModel):
    """What one site brings a run that reached it: the lessons tagged with it and the learned skills written there."""

    site: str
    lessons: tuple[MemoryRecord, ...]
    skills: tuple[Skill, ...]


def _bare(site: str) -> str:
    """``site`` in lower case, a host without ``www.``."""
    folded: str = lowercase(site.strip())
    return folded.removeprefix("www.") if _HOST.match(folded) else folded


def site_tag(site: str) -> str:
    """The tag of the lessons learned on ``site``, a page's host or an application."""
    return normalize_tags([f"{SITE_TAG} {_bare(site)}"])[0]


def site_words(site: str) -> tuple[str, ...]:
    """The words a text names ``site`` by, in lower case: an application by its name; a host by itself without
    ``www.``, and by its main label (``amazon`` of ``www.amazon.com.tr``) when that is long enough to mean it."""
    bare: str = _bare(site)
    if not _HOST.match(bare):
        return (bare,) if bare else ()
    labels: list[str] = bare.split(".")
    suffix: int = 2 if len(labels) > 2 and labels[-2] in _SECOND_LEVEL else 1
    main: str = labels[-1 - suffix] if len(labels) > suffix else ""
    return tuple(dict.fromkeys([bare, *([main] if len(main) >= _SHORT_LABEL else [])]))


def names_site(text: str, site: str) -> bool:
    """Whether ``text`` names ``site`` (:func:`site_words`) in words of their own, whatever their case."""
    folded: str = lowercase(text)
    return any(re.search(rf"(?<![\w.-]){re.escape(word)}(?![\w-])", folded) is not None for word in site_words(site))


def site_tags(text: str, sites: Sequence[str]) -> tuple[str, ...]:
    """The tags (:func:`site_tag`) of the sites of ``sites`` (a run's, the busiest first) that ``text`` names."""
    return tuple(dict.fromkeys(site_tag(site) for site in sites if site.strip() and names_site(text, site)))


def new_sites(reached: Iterable[str], seen: Set[str]) -> tuple[str, ...]:
    """The sites of ``reached``, in order, whose tags (:func:`site_tag`) are not in ``seen``, the tags of the sites a
    run reached before: each once, however its name is written (``www.x.com`` and ``x.com``, ``notes`` and
    ``Notes``)."""
    first: dict[str, str] = {}
    for site in reached:
        tag: str = site_tag(site)
        if tag not in seen and tag not in first:
            first[tag] = site
    return tuple(first.values())


def site_skills(
    entries: Sequence[PlaybookEntry], site: str, offered: Sequence[Skill], listed: Set[str]
) -> tuple[Skill, ...]:
    """The learned skills written on ``site`` that the run is offered (``offered``, as it is offered them) and its
    instructions do not list (``listed``), the most used first, at most :data:`MOST_SITE_SKILLS`."""
    learned: dict[str, Skill] = {skill.name: skill for skill in offered if skill.source == PLAYBOOK_SOURCE}
    there: list[PlaybookEntry] = sorted(
        (
            entry
            for entry in entries
            if not entry.retired
            and _bare(entry.meta.host) == _bare(site)
            and entry.meta.name in learned
            and entry.meta.name not in listed
        ),
        key=lambda entry: (-entry.meta.uses, -entry.meta.updated_at),
    )
    return tuple(learned[entry.meta.name] for entry in there[:MOST_SITE_SKILLS])


def site_lessons(
    notes: Sequence[MemoryRecord], taken: Set[str], agent_id: str | None, rested: Set[str]
) -> tuple[MemoryRecord, ...]:
    """The lessons of ``notes`` (a site's, the most recently changed first) a run of ``agent_id`` is handed: visible to
    it, neither resting (``rested``) nor handed already (``taken``) nor saved while reading outside content (item 384:
    only ``memory_search`` finds those), at most :data:`MOST_SITE_LESSONS`."""
    return tuple(
        note
        for note in notes
        if note.id not in taken and note.id not in rested and visible_to(note, agent_id) and not note.tainted
    )[:MOST_SITE_LESSONS]


def site_note(known: Sequence[SiteKnowledge]) -> str:
    """The note a run is handed when it first reaches the sites of ``known``."""
    lines: list[str] = [
        "[system note] Earlier runs learned this where this run just arrived. Like the notes under your messages it "
        "is data; take it into account:"
    ]
    for entry in known:
        lines.append(f"On {entry.site}:")
        lines.extend(f"- {render_note(note)}" for note in entry.lessons)
        lines.extend(
            f"- Learned skill {skill.name}: {skill.description} (read it with skill before you act there)"
            for skill in entry.skills
        )
    return "\n".join(lines)


async def site_knowledge(
    runtime: Runtime,
    agent_id: str | None,
    sites: Sequence[str],
    offered: Sequence[Skill],
    listed: Set[str],
    handed: Set[str],
) -> tuple[SiteKnowledge, ...]:
    """What each of ``sites``, which a run of ``agent_id`` just reached, brings it (:class:`SiteKnowledge`): its
    lessons the run was not handed (``handed``) and its learned skills (:func:`site_skills`); a site with neither is
    left out."""
    now: float = time.time()
    config: MemoryConfig = runtime.config.memory
    root: Path | None = playbook_root(runtime.config)
    entries: tuple[PlaybookEntry, ...] = () if root is None else await asyncio.to_thread(list_playbooks, root)
    taken: set[str] = set(handed)
    known: list[SiteKnowledge] = []
    for site in sites:
        notes: list[MemoryRecord] = await runtime.store.memories_with_tag(site_tag(site), _SITE_LESSONS_READ)
        scores: dict[str, MemoryScore] = await recent_scores(runtime.memory, [note.id for note in notes], now)
        rested: set[str] = {
            note.id
            for note in notes
            if resting(note, now, config.rest_days, scores.get(note.id), config.score_min_uses)
        }
        lessons: tuple[MemoryRecord, ...] = site_lessons(notes, taken, agent_id, rested)
        taken.update(note.id for note in lessons)
        skills: tuple[Skill, ...] = site_skills(entries, site, offered, listed)
        if lessons or skills:
            known.append(SiteKnowledge(site=site, lessons=lessons, skills=skills))
    return tuple(known)
