"""Changes to learned skills that wait for the owner's yes (item 585).

Two kinds. A **merge**: each night the review model is shown the pairs of skills whose kinds are close in meaning
(``[agent] skill_merge_similarity``) and says which are one procedure (:func:`propose_merges`); for each yes a second
call writes the merged steps, and the proposal waits as a card on Customize › Skills. A **rewrite from the owner's
words** (:func:`propose_rewrite`, which item 586's reactions call): the same second call changes one skill as they say.
Nothing is written to a skill until the owner says yes (:func:`accept_proposal`): it makes a new version of the skill
(the file it replaces is kept, so a restore undoes it), marked if any skill it was made from was, and a merge puts the
other skill to rest, ``merged_into`` naming the first. What is written is exactly the draft the owner was shown, and
only while each skill is at the version the proposal was made from.

A proposal is a file ``.proposals/<id>.json`` in the playbooks' folder, kept under its lock. The ones the owner
dismissed are remembered with the versions they were made from (``.proposals/dismissed.json``), so that the same pair is
not proposed again until one of its skills changes. Skills the owner taught, pinned or put to rest are never merged, nor
is a skill of an agent with another's.
"""

import asyncio
import json
import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from dotagent.agent.playbooks import (
    Playbook,
    PlaybookEntry,
    PlaybookMeta,
    SkillVersion,
    archive_version,
    changing,
    flat,
    list_playbooks,
    noted,
    playbook_folder,
    redacted_draft,
    render_learned,
    render_playbook,
    replace_skill,
    save_meta,
    skill_resting,
)
from dotagent.agent.review import (
    MOST_OVERLAP_PAIRS,
    OverlapJudge,
    OverlapJudgement,
    OverlapRequest,
    OverlapVerdict,
    PlaybookDraft,
    ReviseRequest,
    SkillBrief,
    SkillReviser,
    overlap_judge_for,
    skill_reviser_for,
)
from dotagent.agent.skill_history import SkillText, StepSign, parts_of, step_changes
from dotagent.core.errors import InvalidValueError, NotFoundError, RunConflict
from dotagent.core.files import write_whole
from dotagent.core.ids import new_id
from dotagent.core.logs import get_logger
from dotagent.core.paths import playbooks_dir
from dotagent.core.text import redact_known
from dotagent.core.types import FrozenModel
from dotagent.memory.ranking import Vector
from dotagent.secrets import known_secrets
from dotagent.skills.catalog import PLAYBOOK_SOURCE
from dotagent.skills.parse import parse_skill
from dotagent.skills.types import SKILL_FILE

if TYPE_CHECKING:
    from dotagent.runtime import Runtime

log = get_logger(__name__)

PROPOSALS_DIR: str = ".proposals"
_DISMISSED_FILE: str = "dismissed.json"
_ID: re.Pattern[str] = re.compile(r"skp_[0-9a-z]+_[0-9a-f]{6}")
_GOAL_LINE: re.Pattern[str] = re.compile(r"^- Goal of the last run: (.*)$", re.MULTILINE)
# The most proposals that wait for the owner at once: a night proposes no more past it.
MOST_WAITING: int = 10
# The longest the owner's words for a rewrite may be.
MOST_WORDS: int = 1000

type ProposalKind = Literal["merge", "rewrite"]


class SkillProposal(FrozenModel):
    """A change to a learned skill that waits for the owner's yes: ``names`` are the skills it is made from (a merge's,
    the one that stays first; a rewrite's one), ``draft`` the steps they are to have, as the card shows them,
    ``reason`` why (the judge's words, or the owner's) and ``words`` the owner's, for a rewrite. ``bases`` is the
    version each skill was at when the proposal was made, which it applies to alone, and ``tainted`` whether the
    version it would make carries the mark of content third parties can author (what is written is computed again
    from the skills)."""

    id: str
    kind: ProposalKind
    names: tuple[str, ...]
    draft: PlaybookDraft
    reason: str
    words: str | None
    bases: dict[str, int]
    tainted: bool
    created_at: float


class LineChange(FrozenModel):
    """One line of a field against the skill it replaces: ``sign`` is ``" "`` for a line the skill keeps, ``"-"`` for
    one the yes replaces or drops and ``"+"`` for one it writes."""

    sign: StepSign
    text: str


class FieldChange(FrozenModel):
    """One field of the skill that a yes writes (item 585), as the card and ``dotx skills proposals`` show it: which
    field, what it is called, whether it is a list of lines (the steps, the parameters) or one text, its lines against
    what the first skill says now and whether the yes changes it."""

    field: Literal["name", "description", "kind", "when", "steps", "parameters"]
    label: str
    many: bool
    lines: tuple[LineChange, ...]
    changed: bool


class ProposalView(FrozenModel):
    """A proposal as the page shows it: the proposal, why it cannot be applied now (``stale``, ``None`` while it can),
    what each of its skills says now, in the order of its ``names`` (a skill that is gone is left out), and every field
    the yes would write to the first (:func:`written_fields`), each against what that skill says now."""

    proposal: SkillProposal
    stale: str | None
    current: tuple[SkillText, ...]
    fields: tuple[FieldChange, ...]


def _folder(root: Path) -> Path:
    return root / PROPOSALS_DIR


def _file(root: Path, proposal_id: str) -> Path:
    """The file of the proposal ``proposal_id``; anything but the shape of an id (a request's, or a run's) is not found,
    so that no name reaches a file outside the proposals."""
    if _ID.fullmatch(proposal_id) is None:
        raise NotFoundError("there is no such proposal", {"proposal_id": proposal_id})
    return _folder(root) / f"{proposal_id}.json"


def list_proposals(root: Path) -> tuple[SkillProposal, ...]:
    """The proposals that wait, the oldest first; a file that cannot be read is left out and logged."""
    folder: Path = _folder(root)
    found: list[SkillProposal] = []
    for file in sorted(folder.glob("skp_*.json")) if folder.is_dir() else ():
        try:
            found.append(SkillProposal.model_validate_json(file.read_text(encoding="utf-8")))
        except (OSError, ValueError) as error:
            log.warning("skill_proposal_unreadable", path=str(file), error=str(error))
    return tuple(sorted(found, key=lambda proposal: proposal.created_at))


def read_proposal(root: Path, proposal_id: str) -> SkillProposal:
    """The proposal ``proposal_id``; one that is not there, or whose file cannot be read, is not found."""
    file: Path = _file(root, proposal_id)
    try:
        return SkillProposal.model_validate_json(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise NotFoundError("there is no such proposal", {"proposal_id": proposal_id, "error": str(error)}) from error


def save_proposal(root: Path, proposal: SkillProposal) -> None:
    """Keep ``proposal`` in its file, written whole, under the lock of the playbooks."""
    with changing(root):
        _folder(root).mkdir(exist_ok=True)
        write_whole(_file(root, proposal.id), proposal.model_dump_json(indent=2) + "\n")


def _drop(root: Path, proposal_id: str) -> None:
    _file(root, proposal_id).unlink(missing_ok=True)


def _active(entries: Iterable[PlaybookEntry]) -> dict[str, PlaybookEntry]:
    """The active skills by name. A retired skill is left out, not put in beside them: a retired one may carry the name
    of an active one (a skill that retired under a name and another written under it later; the listing puts the retired
    ones after the active ones), and a map by name that held it would have the retired skill stand for the active one:
    a proposal for it would read as made from a skill that is gone, be dropped and proposed again every night, and
    never be applied."""
    return {entry.meta.name: entry for entry in entries if not entry.retired}


def _stale(entries: dict[str, PlaybookEntry], proposal: SkillProposal) -> str | None:
    """Why ``proposal`` cannot be applied to the skills as they are, or ``None`` (item 585): a skill is gone, is another
    version than the one it was made from, or (a merge) cannot be merged now: one the owner taught, pinned or put to
    rest, one a merge put aside already, or two of different agents."""
    metas: list[PlaybookMeta] = []
    for name in proposal.names:
        entry: PlaybookEntry | None = entries.get(name)
        if entry is None or entry.retired:
            return f"the skill {name} is gone"
        if entry.meta.version != proposal.bases.get(name):
            return f"the skill {name} changed since this was proposed"
        metas.append(entry.meta)
    if proposal.kind == "merge" and (
        len(metas) < 2
        or any(
            meta.taught or meta.pinned or meta.rested_at is not None or meta.merged_into is not None for meta in metas
        )
        or len({meta.agent_id for meta in metas}) != 1
    ):
        return (
            "these skills cannot be merged now: one is taught, pinned, resting or merged already, "
            "or they differ in whose they are"
        )
    return None


_TEXT_FIELDS: tuple[tuple[Literal["description", "kind", "when"], str], ...] = (
    ("description", "Description"),
    ("kind", "Kind of work"),
    ("when", "When"),
)
_LIST_FIELDS: tuple[tuple[Literal["steps", "parameters"], str], ...] = (
    ("steps", "Steps"),
    ("parameters", "Parameters"),
)


def _changed(lines: tuple[LineChange, ...]) -> bool:
    return any(line.sign != " " for line in lines)


def _text_field(field: Literal["description", "kind", "when"], label: str, was: str, now: str) -> FieldChange:
    """A field of one text: kept as it is, written where the skill has none, or the old text replaced by the new."""
    if was == now:
        lines: tuple[LineChange, ...] = (LineChange(sign=" ", text=now),)
    elif was == "":
        lines = (LineChange(sign="+", text=now),)
    else:
        lines = (LineChange(sign="-", text=was), LineChange(sign="+", text=now))
    return FieldChange(field=field, label=label, many=False, lines=lines, changed=_changed(lines))


def written_fields(draft: PlaybookDraft, before: SkillText | None, name: str) -> tuple[FieldChange, ...]:
    """Every field a yes writes to the skill ``name`` (item 585): its name, which stays; the description, the kind of
    work, when it fits, the steps and the parameters of ``draft``, each as the file keeps it (:func:`flat`) and against
    what the skill says now (``before``, ``None`` for one that is gone). The review model wrote the draft, from skills
    that third parties may have written, and the description is a line of every run's instructions: all of it is shown,
    and nothing but what is shown is written (a draft field that is not in this list fails a test)."""
    now: dict[str, str] = {"description": flat(draft.description), "kind": flat(draft.kind), "when": flat(draft.when)}
    was: dict[str, str] = (
        {} if before is None else {"description": before.description, "kind": before.kind, "when": before.when}
    )
    fields: list[FieldChange] = [
        FieldChange(field="name", label="Name", many=False, lines=(LineChange(sign=" ", text=name),), changed=False)
    ]
    for field, label in _TEXT_FIELDS:
        fields.append(_text_field(field, label, was.get(field, ""), now[field]))
    written: dict[str, tuple[str, ...]] = {
        "steps": tuple(flat(step) for step in draft.steps),
        "parameters": tuple(flat(parameter) for parameter in draft.parameters),
    }
    held: dict[str, tuple[str, ...]] = (
        {} if before is None else {"steps": before.steps, "parameters": before.parameters}
    )
    for listed, heading in _LIST_FIELDS:
        lines = tuple(
            LineChange(sign=sign, text=text) for sign, text in step_changes(held.get(listed, ()), written[listed])
        )
        fields.append(FieldChange(field=listed, label=heading, many=True, lines=lines, changed=_changed(lines)))
    return tuple(fields)


def waiting_proposals(root: Path) -> tuple[ProposalView, ...]:
    """The proposals that wait, each with why it cannot be applied now, if it cannot, what its skills say now and every
    field the yes would write against what the first says."""
    entries: dict[str, PlaybookEntry] = _active(list_playbooks(root))
    views: list[ProposalView] = []
    for proposal in list_proposals(root):
        first: PlaybookEntry | None = entries.get(proposal.names[0])
        views.append(
            ProposalView(
                proposal=proposal,
                stale=_stale(entries, proposal),
                current=tuple(
                    parts_of(entries[name].skill.description, entries[name].skill.body)
                    for name in proposal.names
                    if name in entries
                ),
                fields=written_fields(
                    proposal.draft,
                    None if first is None else parts_of(first.skill.description, first.skill.body),
                    proposal.names[0],
                ),
            )
        )
    return tuple(views)


def _date(at: float) -> str:
    return datetime.fromtimestamp(at, tz=UTC).strftime("%Y-%m-%d")


def _last_goal(body: str) -> str | None:
    """The goal of the last run a skill's evidence names, or ``None`` for a skill whose file has none."""
    found: re.Match[str] | None = _GOAL_LINE.search(body)
    return None if found is None else found.group(1)


def _made(
    kept: PlaybookEntry, others: list[PlaybookEntry], proposal: SkillProposal, now: float
) -> tuple[str, PlaybookMeta]:
    """The SKILL.md and the record ``proposal`` makes of the skill ``kept`` (and the ``others`` a merge takes in). The
    mark of content third parties can author is computed from the skills here: a merged skill carries it when any skill
    it is made from does (a record older than the mark counts as marked), and a skill rewritten keeps its own; what the
    proposal's own field says does not decide it."""
    meta: PlaybookMeta = kept.meta
    draft: PlaybookDraft = proposal.draft
    number: int = meta.version + 1
    goal: str = _last_goal(kept.skill.body) or draft.description
    if proposal.kind == "rewrite":
        note: SkillVersion = SkillVersion(
            n=number, at=now, run_id=None, change="revised", tainted=meta.tainted, words=proposal.words
        )
        if meta.taught:
            text: str = render_playbook(
                Playbook(
                    name=meta.name,
                    kind=draft.kind,
                    host=meta.host,
                    draft=draft,
                    run_id="",
                    agent_id=meta.agent_id,
                    goal=draft.description,
                    written_at=meta.written_at,
                    acting_steps=0,
                    tainted=False,
                    taught=True,
                ),
                None,
            )
        else:
            since: str = f"; revised on {_date(now)} at the owner's word"
            text = render_learned(meta.name, draft.kind, meta.host, draft, goal, meta.runs, meta.acting_steps, since)
        return text, meta.model_copy(
            update={
                "kind": draft.kind,
                "updated_at": now,
                "failing": 0,
                "version": number,
                "versions": (*noted(meta), note),
            }
        )
    metas: list[PlaybookMeta] = [meta, *(other.meta for other in others)]
    counts: dict[str, int] = {}
    for held in metas:
        for run, count in zip(held.runs, held.acting_steps, strict=True):
            counts.setdefault(run, count)
    marked: bool = any(held.tainted is not False for held in metas)
    since = f"; the steps of {len(metas)} skills merged on {_date(now)}"
    text = render_learned(meta.name, draft.kind, meta.host, draft, goal, tuple(counts), tuple(counts.values()), since)
    read: list[float] = [held.last_read_at for held in metas if held.last_read_at is not None]
    return text, meta.model_copy(
        update={
            "kind": draft.kind,
            "runs": tuple(counts),
            "acting_steps": tuple(counts.values()),
            "updated_at": now,
            "uses": sum(held.uses for held in metas),
            "failures": sum(held.failures for held in metas),
            "failing": 0,
            "shared": all(held.shared for held in metas),
            "tainted": marked,
            "tainted_runs": tuple(dict.fromkeys(run for held in metas for run in held.tainted_runs)),
            "last_read_at": max(read) if read else None,
            "version": number,
            "versions": (
                *noted(meta),
                SkillVersion(
                    n=number,
                    at=now,
                    run_id=None,
                    change="merged",
                    tainted=marked,
                    merged=tuple(other.meta.name for other in others),
                ),
            ),
        }
    )


def accept_proposal(root: Path, proposal_id: str, now: float) -> PlaybookMeta:
    """The owner's yes to a proposal (item 585): the draft they were shown is written as a new version of the skill it
    names first, the file it replaces kept as a version; a merge puts the other skill to rest (``merged_into``) and
    takes its runs and its use counts in. A proposal that is not there is :class:`NotFoundError`; one whose skills
    changed since, or cannot be merged now, is a :class:`RunConflict` that changes nothing. Returns the record of the
    skill after it."""
    with changing(root):
        proposal: SkillProposal = read_proposal(root, proposal_id)
        entries: dict[str, PlaybookEntry] = _active(list_playbooks(root))
        problem: str | None = _stale(entries, proposal)
        if problem is not None:
            raise RunConflict(f"this proposal cannot be applied: {problem}", {"proposal_id": proposal_id})
        kept: PlaybookEntry = entries[proposal.names[0]]
        others: list[PlaybookEntry] = [entries[name] for name in proposal.names[1:]]
        text, meta = _made(kept, others, proposal, now)
        folder: Path = playbook_folder(root, kept.meta.name)
        file: Path = folder / SKILL_FILE
        parse_skill(file, text, PLAYBOOK_SOURCE)
        archive_version(folder, kept.meta.version)
        replace_skill(folder, text, meta)
        for other in others:
            put_aside: PlaybookMeta = other.meta.model_copy(
                update={"rested_at": now, "merged_into": kept.meta.name, "pinned": False}
            )
            save_meta(playbook_folder(root, other.meta.name), put_aside)
        _drop(root, proposal_id)
    log.info(
        "skill_proposal_accepted",
        proposal_id=proposal_id,
        kind=proposal.kind,
        names=list(proposal.names),
        version=meta.version,
        tainted=meta.tainted,
    )
    return meta


def _key(names: tuple[str, ...]) -> str:
    return "|".join(sorted(names))


def _dismissed(root: Path) -> dict[str, dict[str, int]]:
    """The pairs of skills the owner dismissed a merge of, each with the versions it was made from."""
    file: Path = _folder(root) / _DISMISSED_FILE
    try:
        raw: object = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        log.warning("skill_dismissed_unreadable", path=str(file), error=str(error))
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): {str(name): int(version) for name, version in value.items()}
        for key, value in raw.items()
        if isinstance(value, dict) and all(isinstance(version, int) for version in value.values())
    }


def dismiss_proposal(root: Path, proposal_id: str) -> None:
    """The owner's no to a proposal (item 585): it is dropped, and a merge is remembered with the versions it was made
    from, so that the same pair is proposed again only after one of its skills changes. One that is not there is
    :class:`NotFoundError`."""
    with changing(root):
        proposal: SkillProposal = read_proposal(root, proposal_id)
        if proposal.kind == "merge":
            remembered: dict[str, dict[str, int]] = _dismissed(root)
            remembered[_key(proposal.names)] = dict(proposal.bases)
            write_whole(_folder(root) / _DISMISSED_FILE, json.dumps(remembered, indent=2) + "\n")
        _drop(root, proposal_id)
    log.info("skill_proposal_dismissed", proposal_id=proposal_id, kind=proposal.kind, names=list(proposal.names))


def _mergeable(entry: PlaybookEntry, now: float, rest_days: int) -> bool:
    """Whether a skill may be proposed for a merge: a learned one (not taught), awake and not pinned, none a merge put
    aside, and one whose file has steps to merge."""
    meta: PlaybookMeta = entry.meta
    return (
        not entry.retired
        and not meta.taught
        and meta.merged_into is None
        and not meta.pinned
        and meta.rested_at is None
        and not skill_resting(meta, now, rest_days)
        and bool(parts_of(entry.skill.description, entry.skill.body).steps)
    )


def brief_of(entry: PlaybookEntry) -> SkillBrief:
    """A skill as the review model is shown it for a judgement or a rewrite: its kind and what its file says."""
    parts: SkillText = parts_of(entry.skill.description, entry.skill.body)
    return SkillBrief(
        kind=entry.meta.kind,
        description=parts.description or entry.skill.description,
        when=parts.when,
        steps=parts.steps,
        parameters=parts.parameters,
    )


def _close_pairs(
    candidates: list[PlaybookEntry], vectors: list[Vector], threshold: float, skip: set[str]
) -> list[tuple[PlaybookEntry, PlaybookEntry]]:
    """The pairs of ``candidates`` of one owner whose kinds are at least ``threshold`` close, the closest first, each
    skill in one pair at most, at most :data:`MOST_OVERLAP_PAIRS`; a pair of which a name is in ``skip`` is left out."""
    close: list[tuple[float, int, int]] = []
    for one, other in combinations(range(len(candidates)), 2):
        first, second = candidates[one], candidates[other]
        if first.meta.agent_id != second.meta.agent_id or len(vectors[one]) != len(vectors[other]):
            continue
        if first.meta.name in skip or second.meta.name in skip:
            continue
        cosine: float = math.sumprod(vectors[one], vectors[other])
        if cosine >= threshold:
            close.append((cosine, one, other))
    pairs: list[tuple[PlaybookEntry, PlaybookEntry]] = []
    taken: set[int] = set()
    for _, one, other in sorted(close, key=lambda found: -found[0]):
        if one in taken or other in taken:
            continue
        taken.update((one, other))
        pairs.append((candidates[one], candidates[other]))
        if len(pairs) == MOST_OVERLAP_PAIRS:
            break
    return pairs


async def propose_merges(runtime: "Runtime", now: float) -> int:
    """The night's proposals to merge skills (item 585): the pairs of learned skills whose kinds are at least
    ``[agent] skill_merge_similarity`` close (by embeddings) that the review model says are one procedure, each with the
    merged steps a second call writes (secrets masked), saved as a proposal for the owner's yes: at most ``[agent]
    skill_proposals_per_night`` a night and at most :data:`MOST_WAITING` waiting. A pair the owner dismissed is not
    proposed again until a skill of it changes, and one that already has a proposal waiting is not either; a proposal
    that cannot be applied any more is dropped first. Nothing is asked without embeddings. Returns how many were
    made."""
    config = runtime.config.agent
    embedder = runtime.memory.embedder
    if not config.playbooks or config.skill_proposals_per_night == 0 or embedder is None:
        return 0
    root: Path = playbooks_dir()
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, root)
    by_name: dict[str, PlaybookEntry] = _active(entries)
    waiting: list[SkillProposal] = []
    for earlier in await asyncio.to_thread(list_proposals, root):
        reason: str | None = _stale(by_name, earlier)
        if reason is None:
            waiting.append(earlier)
        else:
            await asyncio.to_thread(_drop, root, earlier.id)
            log.info("skill_proposal_dropped", proposal_id=earlier.id, reason=reason)
    room: int = min(config.skill_proposals_per_night, MOST_WAITING - len(waiting))
    if room <= 0:
        return 0
    candidates: list[PlaybookEntry] = [entry for entry in entries if _mergeable(entry, now, config.skill_rest_days)]
    if len(candidates) < 2:
        return 0
    remembered: dict[str, dict[str, int]] = await asyncio.to_thread(_dismissed, root)
    skip: set[str] = {name for earlier in waiting for name in earlier.names}
    vectors: list[Vector] = await embedder.embed([entry.meta.kind for entry in candidates])
    pairs: list[tuple[PlaybookEntry, PlaybookEntry]] = [
        pair
        for pair in _close_pairs(candidates, vectors, config.skill_merge_similarity, skip)
        if remembered.get(_key((pair[0].meta.name, pair[1].meta.name)))
        != {pair[0].meta.name: pair[0].meta.version, pair[1].meta.name: pair[1].meta.version}
    ]
    if not pairs:
        return 0
    judge: OverlapJudge = overlap_judge_for(
        runtime.router,
        runtime.roles.review(),
        runtime.roles.after_review(),
        config.model_call_timeout_s,
        f"reflection:{int(now)}:skills",
    )
    judgement: OverlapJudgement = await judge(
        OverlapRequest(pairs=tuple((brief_of(first), brief_of(second)) for first, second in pairs))
    )
    reviser: SkillReviser = skill_reviser_for(
        runtime.router,
        runtime.roles.review(),
        runtime.roles.after_review(),
        config.model_call_timeout_s,
        f"reflection:{int(now)}:skills",
    )
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    made: int = 0
    by_pair: dict[int, OverlapVerdict] = {verdict.pair: verdict for verdict in judgement.verdicts}
    for number, (first, second) in enumerate(pairs, start=1):
        verdict: OverlapVerdict | None = by_pair.get(number)
        if verdict is None or not verdict.one or made >= room:
            continue
        kept, other = (first, second) if verdict.keep == 1 else (second, first)
        draft: PlaybookDraft = redacted_draft(
            await reviser(ReviseRequest(sources=(brief_of(kept), brief_of(other)), words="")), secrets
        )
        proposal: SkillProposal = SkillProposal(
            id=new_id("skp"),
            kind="merge",
            names=(kept.meta.name, other.meta.name),
            draft=draft,
            reason=verdict.reason,
            words=None,
            bases={kept.meta.name: kept.meta.version, other.meta.name: other.meta.version},
            tainted=any(entry.meta.tainted is not False for entry in (kept, other)),
            created_at=now,
        )
        await asyncio.to_thread(save_proposal, root, proposal)
        log.info("skill_merge_proposed", proposal_id=proposal.id, names=list(proposal.names), reason=verdict.reason)
        made += 1
    return made


async def propose_rewrite(runtime: "Runtime", name: str, words: str, now: float) -> SkillProposal:
    """A proposal to change the learned skill ``name`` as the owner's ``words`` say (item 585, which item 586's
    reactions call): the review model writes the skill as it should be after the change, secrets masked, and it waits
    for the owner's yes as a card, never applied here. The words are one line of the owner's, at most :data:`MOST_WORDS`
    characters, and hold no secret. A skill that is not there (or is retired) is :class:`NotFoundError`; a skill that
    rests, is pinned or was taught may be rewritten, which is the owner's to say."""
    said: str = " ".join(words.split())
    if not said or len(said) > MOST_WORDS:
        raise InvalidValueError(
            f"say what to change in 1 to {MOST_WORDS} characters", {"name": name, "chars": len(said)}
        )
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    if redact_known(said, secrets) != said:
        raise InvalidValueError("the words hold a secret: say where it lives, not what it is", {"name": name})
    root: Path = playbooks_dir()
    entry: PlaybookEntry | None = next(
        (
            found
            for found in await asyncio.to_thread(list_playbooks, root)
            if found.meta.name == name and not found.retired
        ),
        None,
    )
    if entry is None:
        raise NotFoundError("there is no such learned skill", {"name": name})
    reviser: SkillReviser = skill_reviser_for(
        runtime.router,
        runtime.roles.review(),
        runtime.roles.after_review(),
        runtime.config.agent.model_call_timeout_s,
        f"reflection:{int(now)}:skills",
    )
    draft: PlaybookDraft = redacted_draft(await reviser(ReviseRequest(sources=(brief_of(entry),), words=said)), secrets)
    proposal: SkillProposal = SkillProposal(
        id=new_id("skp"),
        kind="rewrite",
        names=(name,),
        draft=draft,
        reason=said,
        words=said,
        bases={name: entry.meta.version},
        tainted=entry.meta.tainted is not False,
        created_at=now,
    )
    await asyncio.to_thread(save_proposal, root, proposal)
    log.info("skill_rewrite_proposed", proposal_id=proposal.id, name=name)
    return proposal
