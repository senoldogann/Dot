"""What the plugin scout keeps (item 710): the candidates it found for the gallery, each checked and reviewed and
waiting for the owner's word, and what it already looked at, so that a repository at a commit is never looked at twice.

Nothing here installs anything. A candidate becomes a gallery entry (:func:`candidate_entry`) only when the owner
installs it, and the install checks that the files are the ones the scout scanned and the review read: the commit is
pinned, and the hash of the kept files with it.

The state is one JSON document in the store's key-value table (:data:`SCOUT_KEY`): the candidates and a few hundred
repositories looked at, bounded by :data:`MAX_CANDIDATES` (those waiting), :data:`MAX_SETTLED` (those skipped, refused
or failed) and :data:`MAX_LOOKED`. A plugin the owner installed from a proposal keeps its candidate, whatever the
bounds: the gallery lists it, its updates are looked for and it is not proposed again by it. A document that cannot be
read is logged and taken as empty: the next night looks again, and nothing it held was installed by it.
"""

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.plugins.gallery import GalleryCategory, GalleryEntry, GalleryMarker, installed_marker

log = get_logger(__name__)

SCOUT_KEY: str = "plugins.scout"
MAX_CANDIDATES: int = 60
MAX_SETTLED: int = 200
MAX_INSTALLED: int = 1000
MAX_LOOKED: int = 600
MAX_SEEN: int = 2000
MAX_DECLINED: int = 5000
MAX_FINDINGS: int = 40

# Where a candidate comes from, by how far Dot trusts the publisher before reading a line: Anthropic's own marketplaces
# and the makers of what the skills are about ("a"), the rest that passed the rules ("b").
type Tier = Literal["a", "b"]
type Risk = Literal["low", "medium", "high"]
type CandidateKind = Literal["new", "update"]
# ``installing``: the owner's Install is under way (item 712), which neither a Skip nor a second Install takes; one left
# so longer than :data:`INSTALL_GRACE_S` (a daemon that died during it) waits again.
type CandidateState = Literal["proposed", "installing", "installed", "skipped", "refused", "failed"]
INSTALL_GRACE_S: float = 600.0


def waiting(candidate: "Candidate", now: float) -> bool:
    """Whether ``candidate`` waits for the owner: proposed, or an install that was left off long ago."""
    if candidate.state == "proposed":
        return True
    return candidate.state == "installing" and (candidate.decided_at or 0.0) < now - INSTALL_GRACE_S


type Severity = Literal["note", "medium", "high"]


class Finding(FrozenModel):
    """One thing the static scan found in a file the plugin would bring (:mod:`dotagent.plugins.scout_scan`)."""

    rule: str
    severity: Severity
    path: str
    detail: str = Field(max_length=200)


class Review(FrozenModel):
    """What the review model made of a candidate: how risky, whether to refuse it outright, what it does and why the
    risk is what it is, the last two in the owner's language."""

    risk: Risk
    refuse: bool
    does: str = Field(max_length=200)
    why: str = Field(max_length=300)
    model: str


class Candidate(FrozenModel):
    """A plugin the scout proposes: a new one for the gallery, or an update of one Dot has installed. ``files`` is the
    hash of the skills' files at ``commit`` as the scout unpacked, scanned and had reviewed them: the install refuses
    any other. ``previous`` is the installed commit an update replaces. ``path`` is the folder of ``repository`` the
    plugin is ("" for the whole repository): with the repository, what the plugin is, whichever source named it."""

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    kind: CandidateKind
    name: str = Field(min_length=1, max_length=40)
    publisher: str = Field(min_length=1, max_length=40)
    tier: Tier
    source: str = Field(max_length=120)
    category: GalleryCategory
    repository: str = Field(pattern=r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
    path: str = Field(default="", max_length=300)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    skills: tuple[str, ...] = Field(min_length=1)
    files: str = Field(pattern=r"^[0-9a-f]{64}$")
    licence: str
    stars: int = Field(ge=0)
    created: date
    pushed: date
    findings: tuple[Finding, ...] = ()
    review: Review | None = None
    fit: float = Field(default=0.0, ge=0.0, le=1.0)
    previous: str | None = None
    changed: tuple[str, ...] = ()
    state: CandidateState = "proposed"
    found_at: float
    decided_at: float | None = None
    failure: str | None = None


class ScoutState(FrozenModel):
    """The candidates, newest first, what was looked at (``repository@commit``), each plugin a night took and when
    (``seen``, ``repository:path``, the latest last: the next nights take first those taken longest ago), and when the
    scout last ran."""

    candidates: tuple[Candidate, ...] = ()
    looked: tuple[str, ...] = ()
    seen: tuple[tuple[str, float], ...] = ()
    declined: tuple[str, ...] = ()
    last_run: float | None = None
    last_summary: str = ""


def declined_key(repository: str, path: str) -> str:
    """A folder of a repository, whichever source names it: a plugin's, or one of its skills'."""
    return f"{repository.lower()}:{path}"


def looked_key(repository: str, commit: str) -> str:
    return f"{repository.lower()}@{commit}"


def read_state(raw: str | None) -> ScoutState:
    """The state the store keeps; an empty one when it holds none, or one that cannot be read (logged)."""
    if raw is None:
        return ScoutState()
    try:
        return ScoutState.model_validate_json(raw)
    except ValidationError as error:
        log.warning("scout_state_unreadable", error=str(error)[:300])
        return ScoutState()


def bounded(state: ScoutState) -> ScoutState:
    """``state`` within its limits, each kind of candidate bounded on its own, newest first, in the order they stand:
    the proposals waiting (:data:`MAX_CANDIDATES`), the skipped, refused and failed (:data:`MAX_SETTLED`), and the
    installed, which a night's finds never push out (:data:`MAX_INSTALLED`, each one an install the owner made); and the
    latest repositories looked at."""
    counts: dict[str, int] = {}
    limits: dict[str, int] = {"proposed": MAX_CANDIDATES, "installed": MAX_INSTALLED}
    kept: list[Candidate] = []
    # A new plugin skipped or refused that the bounds push out stays declined (the review of the ninth push): it is
    # not proposed again, as the docs say, while those kept are known by their candidate. It is kept by its folder and
    # by each of its skills, so that a plugin of a whole repository, known by its skills alone, stays declined too (the
    # review of the eleventh push).
    declined: list[str] = list(state.declined)
    for candidate in state.candidates:
        kind: str = candidate.state if candidate.state in limits else "settled"
        counts[kind] = counts.get(kind, 0) + 1
        if counts[kind] <= limits.get(kind, MAX_SETTLED):
            kept.append(candidate)
        elif candidate.kind == "new" and candidate.state in ("skipped", "refused"):
            keys: list[str] = [declined_key(candidate.repository, skill) for skill in candidate.skills]
            if candidate.path:
                keys.append(declined_key(candidate.repository, candidate.path))
            declined.extend(key for key in keys if key not in declined)
    return state.model_copy(
        update={
            "candidates": tuple(kept),
            "looked": state.looked[-MAX_LOOKED:],
            "seen": state.seen[-MAX_SEEN:],
            "declined": tuple(declined[-MAX_DECLINED:]),
        }
    )


def _same(candidate: Candidate) -> tuple[str, str, str, float]:
    return candidate.id, candidate.kind, candidate.commit, candidate.found_at


def merged(before: ScoutState, after: ScoutState, now: ScoutState) -> ScoutState:
    """The state a night leaves, laid over the state as it is ``now``: ``after`` is what the night made of ``before``,
    the state it began with, and the owner may have installed, updated, skipped or reverted meanwhile. What the night
    found is added to what is there now, standing in for a proposal of the same plugin still undecided; what the owner
    decided stays as they decided it."""
    began: set[tuple[str, str, str, float]] = {_same(candidate) for candidate in before.candidates}
    found: tuple[Candidate, ...] = tuple(candidate for candidate in after.candidates if _same(candidate) not in began)
    replaced: set[tuple[str, str]] = {(candidate.id, candidate.kind) for candidate in found}
    kept: tuple[Candidate, ...] = tuple(
        candidate
        for candidate in now.candidates
        if not (candidate.state == "proposed" and (candidate.id, candidate.kind) in replaced)
    )
    known: set[str] = set(now.looked)
    refused: set[str] = set(now.declined)
    return bounded(
        ScoutState(
            candidates=(*found, *kept),
            looked=(*now.looked, *(key for key in after.looked if key not in known)),
            seen=after.seen,
            declined=(*now.declined, *(key for key in after.declined if key not in refused)),
            last_run=after.last_run,
            last_summary=after.last_summary,
        )
    )


def applies(candidate: Candidate, plugins: Path) -> bool:
    """Whether ``candidate`` can still be taken: a new plugin can; an update only while its plugin is installed at the
    version it was found against, which a Revert, a Remove or an install again changes (the review of the twelfth
    push: one that no longer could stayed proposed, its Update answering 409, until the repository moved on)."""
    if candidate.kind != "update":
        return True
    marker: GalleryMarker | None = installed_marker(plugins / candidate.id)
    return marker is not None and marker.commit == candidate.previous


def retired(state: ScoutState, plugins: Path) -> ScoutState:
    """``state`` with each update proposed that no longer :func:`applies` failed, so that the next night may propose
    one from the version installed now."""
    return state.model_copy(
        update={
            "candidates": tuple(
                candidate.model_copy(
                    update={"state": "failed", "failure": "its plugin is no longer at the version it was found against"}
                )
                if candidate.state == "proposed" and not applies(candidate, plugins)
                else candidate
                for candidate in state.candidates
            )
        }
    )


def candidate_entry(candidate: Candidate, does: str, added: date) -> GalleryEntry:
    """The gallery entry the owner installs ``candidate`` as: the commit and the hash the scout checked."""
    return GalleryEntry(
        id=candidate.id,
        name=candidate.name,
        publisher=candidate.publisher,
        official=candidate.tier == "a",
        category=candidate.category,
        does=does[:160] or candidate.name,
        repository=candidate.repository,
        commit=candidate.commit,
        skills=candidate.skills,
        files=candidate.files,
        licence=candidate.licence,
        stars=candidate.stars,
        added=added,
    )
