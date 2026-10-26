"""The plugin scout's night (item 710): it finds plugins worth proposing for the gallery, and updates of those Dot
installed, checks and reviews each, and keeps them for the owner's word. It installs nothing.

A night, within its time and its number of plugins:

1. the sources give their leads (:mod:`dotagent.plugins.scout_sources`): Anthropic's marketplaces at their newest
   commit, the makers' repositories, GitHub's topics; a source that fails is logged and the others go on;
2. what the gallery holds, what is installed, what was looked at under the commit its catalogue pins and what is
   already proposed are left out, before any request; the rest is taken by trust (tier ``a`` first), then those a
   night took longest ago (never first), then by how near its description is to the owner's work, so that the leads a
   night has no room for come first the next nights;
3. each lead is checked by the rules on what GitHub says of its repository; one that passes is downloaded at its
   commit, its skills found and unpacked as an install unpacks them, their files hashed and scanned
   (:mod:`dotagent.plugins.scout_scan`), then reviewed by the review model and given its fit
   (:mod:`dotagent.plugins.scout_review`);
4. each installed gallery plugin whose repository moved on is downloaded at the new commit, and when the skills' files
   changed, the change is scanned and reviewed and proposed as an update.

A lead the rules refuse, one without skills and one reviewed is not looked at again under that commit. One that failed
for a reason of the night (GitHub or the model did not answer) is tried again the next night.
"""

import asyncio
import hashlib
import os
import re
import shutil
import tarfile
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

from dotagent.core.errors import DotError, GalleryError
from dotagent.core.logs import get_logger
from dotagent.llm.router import ModelRouter
from dotagent.memory.embedder import Embedder
from dotagent.plugins.gallery import (
    ArchiveFetch,
    GalleryEntry,
    GalleryMarker,
    files_hash,
    installed_marker,
    skill_folder_name,
    unpack,
)
from dotagent.plugins.scout_review import (
    FILE_CHARS,
    Asked,
    FileText,
    SkillText,
    fit,
    question,
    review,
    unread_finding,
    word_fit,
)
from dotagent.plugins.scout_scan import runnable, scan, text_of
from dotagent.plugins.scout_sources import (
    MAKERS,
    MARKETPLACES,
    SEARCH_TOPICS,
    GitHub,
    Lead,
    OutOfRequests,
    RepoFacts,
    maker_lead,
    marketplace_leads,
    plugin_id,
    refusal,
    search_leads,
    unique,
)
from dotagent.plugins.scout_types import (
    Candidate,
    Finding,
    Review,
    ScoutState,
    Tier,
    bounded,
    declined_key,
    looked_key,
)
from dotagent.plugins.types import SKILLS_DIR

log = get_logger(__name__)

MAX_SKILLS: int = 40
MAX_DEPTH: int = 6
_PLACEHOLDER_HASH: str = "0" * 64
_DESCRIPTION: re.Pattern[str] = re.compile(r"^description:\s*(.+)$", re.MULTILINE)
_LEFT_OUT: frozenset[str] = frozenset({"node_modules", "__pycache__"})


@dataclass
class ScoutDeps:
    """What a night needs, given so that a test gives its own: GitHub, the archive's fetch, the review model, the
    embedder, the owner's recent goals, Dot's plugins folder and gallery, the day and the limits."""

    github: GitHub
    fetch: ArchiveFetch
    router: ModelRouter
    model: str
    review_timeout_s: float
    embedder: Embedder | None
    goals: tuple[str, ...]
    plugins: Path
    gallery: tuple[GalleryEntry, ...]
    today: date
    now: float
    max_leads: int
    max_updates: int
    deadline: float
    clock: Callable[[], float] = time.monotonic
    # Whether the owner's emergency stop is engaged: the night ends at the next plugin, and stores nothing.
    stopped: Callable[[], bool] = lambda: False
    archives: dict[str, Path] = field(default_factory=dict)


class ScoutStopped(GalleryError):
    """The owner's emergency stop was engaged during the night: it ends where it is, and what it found is not kept,
    so that the next night looks at the same plugins again."""


class Unusable(GalleryError):
    """A plugin whose own files cannot be taken (its archive holds a link, is too big, lacks a skill it lists): looked
    at, and not again under that commit, unlike a failure of the night (GitHub or the model did not answer)."""


@dataclass
class _Night:
    """What a night found, as it goes."""

    found: list[Candidate] = field(default_factory=list)
    looked: list[str] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)
    refused: int = 0
    unusable: int = 0
    failed: int = 0
    out_of_time: bool = False
    out_of_requests: bool = False


def skill_folders(archive: Path, path: str) -> tuple[str, ...]:
    """The folders under ``path`` of the repository in ``archive`` that hold a ``SKILL.md``, at most
    :data:`MAX_DEPTH` deep and :data:`MAX_SKILLS` of them, no hidden folder and none of what tools leave behind
    (:data:`_LEFT_OUT`). Raises GalleryError when the archive cannot be read."""
    prefix: tuple[str, ...] = PurePosixPath(path).parts if path else ()
    folders: set[str] = set()
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                parts: tuple[str, ...] = PurePosixPath(member.name).parts[1:]
                if not member.isreg() or not parts or parts[-1] != "SKILL.md":
                    continue
                if parts[: len(prefix)] != prefix or len(parts) - len(prefix) > MAX_DEPTH:
                    continue
                within: tuple[str, ...] = parts[:-1]
                if not within or any(part.startswith(".") or part in _LEFT_OUT for part in within):
                    continue
                folders.add("/".join(within))
    except (tarfile.TarError, EOFError, OSError) as error:
        raise GalleryError(f"the archive cannot be read ({type(error).__name__})", {"archive": archive.name}) from error
    # A skill's own folder may hold another SKILL.md among its resources: that one goes with the skill around it.
    outer: list[str] = [
        folder for folder in folders if not any(folder.startswith(f"{other}/") for other in folders if other != folder)
    ]
    kept: dict[str, str] = {}
    for folder in sorted(outer):
        # Two skills of one name would land in one folder of the plugin: the first is kept.
        kept.setdefault(skill_folder_name(folder), folder)
    return tuple(sorted(kept.values()))[:MAX_SKILLS]


def skill_texts(skills: Path) -> tuple[SkillText, ...]:
    """What the reviewer reads of each skill under ``skills``: its ``SKILL.md``, the names of its other files and the
    text of those a model reads as text, the scripts first (the review of the seventh push: the review read only the
    ``SKILL.md``, and a script it tells a run to run went unread)."""
    texts: list[SkillText] = []
    for folder in sorted(child for child in skills.iterdir() if child.is_dir()):
        paths: list[Path] = sorted(
            (
                path
                for path in folder.rglob("*")
                if path.is_file() and path.relative_to(folder).as_posix() != "SKILL.md"
            ),
            key=lambda path: (not runnable(path), path.relative_to(folder).as_posix()),
        )
        # Every file that is text, whatever its name says; one that is not (an image, a font) cannot instruct a run,
        # and one compiled or of no known kind is a high finding of the scan.
        read: list[tuple[Path, str | None]] = [(path, text_of(path, FILE_CHARS * 4)) for path in paths]
        contents: tuple[FileText, ...] = tuple(
            FileText(name=path.relative_to(folder).as_posix(), text=text, size=path.stat().st_size)
            for path, text in read
            if text is not None
        )
        text: str = (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        texts.append(
            SkillText(
                folder=folder.name,
                text=text,
                files=tuple(sorted(path.relative_to(folder).as_posix() for path in paths)),
                contents=contents,
            )
        )
    return tuple(texts)


def described(lead: Lead, texts: Sequence[SkillText]) -> str:
    """The plugin in a few lines, for its fit: its name, its description and its skills' descriptions."""
    lines: list[str] = [lead.name, lead.description]
    for text in texts:
        found: re.Match[str] | None = _DESCRIPTION.search(text.text)
        lines.append(f"{text.folder}: {found.group(1).strip() if found else ''}")
    return "\n".join(line for line in lines if line.strip())[:4000]


def _file_digests(skills: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for root, _folders, names in os.walk(skills):
        for name in names:
            path: Path = Path(root) / name
            digests[path.relative_to(skills).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def changed_files(before: Path, after: Path) -> tuple[str, ...]:
    """The files under ``after`` that are new or differ from those under ``before``, and those it no longer has."""
    old: dict[str, str] = _file_digests(before) if before.is_dir() else {}
    new: dict[str, str] = _file_digests(after)
    changed: set[str] = {path for path, digest in new.items() if old.get(path) != digest}
    gone: set[str] = {f"{path} (removed)" for path in old if path not in new}
    return tuple(sorted(changed | gone))


def _free_id(wanted: str, taken: set[str]) -> str:
    candidate: str = wanted
    number: int = 2
    while candidate in taken:
        candidate = f"{wanted[:36]}-{number}"
        number += 1
    return candidate


def installed_plugins(plugins: Path) -> list[tuple[str, GalleryMarker]]:
    """The plugins of Dot's folder that came from the gallery, by their folder's name, with what their marker says."""
    if not plugins.is_dir():
        return []
    found: list[tuple[str, GalleryMarker]] = []
    for child in sorted(plugins.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            marker: GalleryMarker | None = installed_marker(child)
            if marker is not None:
                found.append((child.name, marker))
    return found


class _Scout:
    """One night of the scout (module docstring)."""

    def __init__(self, deps: ScoutDeps, state: ScoutState) -> None:
        self._deps: ScoutDeps = deps
        self._state: ScoutState = state
        self._night: _Night = _Night()
        self._staging: Path = Path(tempfile.mkdtemp(prefix="dot-scout-"))
        self._taken: set[str] = {entry.id for entry in deps.gallery} | {candidate.id for candidate in state.candidates}
        self._installed: list[tuple[str, GalleryMarker]] = installed_plugins(deps.plugins)
        self._declined: frozenset[str] = frozenset(state.declined)
        self._taken |= {name for name, _ in self._installed}

    def _out_of_time(self) -> bool:
        if self._deps.stopped():
            raise ScoutStopped("the emergency stop is engaged: the look ended where it was", {})
        if self._deps.clock() >= self._deps.deadline:
            self._night.out_of_time = True
        if self._deps.github.exhausted:
            self._night.out_of_requests = True
        return self._night.out_of_time or self._night.out_of_requests

    async def _leads(self) -> list[Lead]:
        github: GitHub = self._deps.github
        leads: list[Lead] = []
        for repository, tier, label in MARKETPLACES:
            try:
                commit: str = await github.commit_of(repository, "HEAD")
                leads.extend(
                    marketplace_leads(await github.marketplace(repository, commit), repository, commit, tier, label)
                )
            except (GalleryError, ValueError) as error:
                log.warning("scout_source_failed", source=repository, error=_said(error))
        leads.extend(maker_lead(repository) for repository in MAKERS)
        for topic in SEARCH_TOPICS:
            try:
                leads.extend(search_leads(await github.search(topic), topic))
            except (GalleryError, ValueError) as error:
                log.warning("scout_source_failed", source=f"topic {topic}", error=_said(error))
        return unique(leads)

    def _known(self, lead: Lead) -> bool:
        """Whether ``lead`` is a plugin the gallery holds, one installed, or one already proposed, skipped or refused:
        by its repository and its folder there, never by the source that named it (the review of the fifth push: a
        skipped plugin a GitHub topic named came back under another topic's name)."""
        repository: str = lead.repository.lower()
        if any(
            entry.repository.lower() == repository and _holds(entry.skills, lead.path) for entry in self._deps.gallery
        ):
            return True
        for name, marker in self._installed:
            if marker.repository.lower() == repository and _holds(self._skills_of(name) or (), lead.path):
                return True
        if not lead.path:
            # A whole repository (a maker's) is never known whole: the skills of it held already are left out one by
            # one, and those it gained since are proposed.
            return False
        if declined_key(lead.repository, lead.path) in self._declined:
            return True
        return any(
            candidate.repository.lower() == repository and candidate.path == lead.path and candidate.kind == "new"
            for candidate in self._state.candidates
        )

    def _held(self, repository: str) -> set[str]:
        """The skill folders of ``repository`` the gallery, the plugins installed, the new candidates and the plugins
        declined hold."""
        wanted: str = repository.lower()
        held: set[str] = {
            skill for entry in self._deps.gallery if entry.repository.lower() == wanted for skill in entry.skills
        }
        for name, marker in self._installed:
            if marker.repository.lower() == wanted:
                held.update(self._skills_of(name) or ())
        held.update(
            skill
            for candidate in self._state.candidates
            if candidate.kind == "new" and candidate.repository.lower() == wanted
            for skill in candidate.skills
        )
        # And the skills of the plugins declined that the bounds pushed out (scout_types.bounded).
        held.update(key.removeprefix(f"{wanted}:") for key in self._declined if key.startswith(f"{wanted}:"))
        return held

    def _pinned_and_looked(self, lead: Lead) -> bool:
        """Whether ``lead`` was looked at under the commit its catalogue pins: known without a request to GitHub."""
        return lead.commit is not None and self._look_key(lead.repository, lead.commit, lead.path) in self._state.looked

    def _chosen(self, leads: Sequence[Lead]) -> list[Lead]:
        """The leads of the night: those not known (the review of the fourth push: a catalogue's plugins without skills
        were taken again each night before any other, and filled the night with requests that proposed nothing), in
        the order the module says, at most ``max_leads``."""
        last: dict[str, float] = dict(self._state.seen)
        fresh: list[Lead] = [lead for lead in leads if not self._known(lead) and not self._pinned_and_looked(lead)]
        fresh.sort(
            key=lambda lead: (
                lead.tier,
                last.get(_seen_key(lead), 0.0),
                -word_fit(f"{lead.name} {lead.description}", self._deps.goals),
            )
        )
        return fresh[: self._deps.max_leads]

    async def _archive(self, entry: GalleryEntry) -> Path:
        key: str = f"{entry.repository.lower()}@{entry.commit}"
        found: Path | None = self._deps.archives.get(key)
        if found is None:
            folder: Path = self._staging / f"archive-{len(self._deps.archives)}"
            await asyncio.to_thread(folder.mkdir)
            found = await self._deps.fetch(entry, folder)
            self._deps.archives[key] = found
        return found

    def _look_key(self, repository: str, commit: str, path: str) -> str:
        return f"{looked_key(repository, commit)}:{path}"

    async def _examine(self, lead: Lead, facts: RepoFacts, commit: str) -> Candidate | None:
        """Download, unpack, hash, scan and review ``lead`` at ``commit``; None for one with no skills."""
        provisional: GalleryEntry = GalleryEntry(
            id=_free_id(plugin_id(lead.name), self._taken),
            name=lead.name[:40] or "plugin",
            publisher=lead.publisher[:40] or facts.owner[:40] or "unknown",
            official=lead.tier == "a",
            category=lead.category,
            does=(lead.description or lead.name)[:160],
            repository=lead.repository,
            commit=commit,
            skills=("SKILL.md",),
            files=_PLACEHOLDER_HASH,
            licence=facts.licence or "",
            stars=facts.stars,
            added=self._deps.today,
        )
        archive: Path = await self._archive(provisional)
        try:
            found: tuple[str, ...] = await asyncio.to_thread(skill_folders, archive, lead.path)
        except GalleryError as error:
            raise Unusable(error.message, error.context) from error
        listed: tuple[str, ...] = tuple(
            folder if not lead.path or folder.startswith(f"{lead.path}/") else f"{lead.path}/{folder}"
            for folder in lead.skills
        )
        # The skills a catalogue lists are taken where they hold a SKILL.md; one that lists none is read whole.
        folders: tuple[str, ...] = tuple(folder for folder in listed if folder in found) if listed else found
        # The skills of the repository a plugin of the gallery, one installed or one proposed already holds are left
        # out (the review of the tenth push: a maker's repository is the gallery's too, and was left out whole, so a
        # skill the maker added was never proposed).
        held: set[str] = self._held(lead.repository)
        folders = tuple(folder for folder in folders if folder not in held)
        if not folders:
            return None
        entry: GalleryEntry = provisional.model_copy(update={"skills": folders})
        made: Path = self._staging / f"made-{entry.id}"
        try:
            await asyncio.to_thread(unpack, entry, archive, made)
        except GalleryError as error:
            raise Unusable(error.message, error.context) from error
        skills: Path = made / SKILLS_DIR
        files: str = await asyncio.to_thread(files_hash, skills)
        findings = await asyncio.to_thread(scan, skills)
        texts: tuple[SkillText, ...] = await asyncio.to_thread(skill_texts, skills)
        asked: Asked = question(
            entry.name, entry.publisher, lead.source, texts, findings, "\n".join(self._deps.goals[:5])
        )
        findings = _with_unread(findings, asked)
        verdict: Review = await review(
            self._deps.router, self._deps.model, self._deps.review_timeout_s, asked.text, findings
        )
        closeness: float = await fit(self._deps.embedder, described(lead, texts), self._deps.goals)
        self._taken.add(entry.id)
        return Candidate(
            id=entry.id,
            kind="new",
            name=entry.name,
            publisher=entry.publisher,
            tier=lead.tier,
            source=lead.source[:120],
            category=lead.category,
            repository=lead.repository,
            path=lead.path,
            commit=commit,
            skills=folders,
            files=files,
            licence=facts.licence or "",
            stars=facts.stars,
            created=facts.created,
            pushed=facts.pushed,
            findings=findings,
            review=verdict,
            fit=closeness,
            state="refused" if verdict.refuse else "proposed",
            found_at=self._deps.now,
        )

    async def _lead(self, lead: Lead) -> None:
        self._night.seen.append(_seen_key(lead))
        github: GitHub = self._deps.github
        facts: RepoFacts = await github.repo(lead.repository)
        reason: str | None = refusal(lead, facts, self._deps.today)
        commit: str = lead.commit or await github.commit_of(lead.repository, facts.default_branch)
        key: str = self._look_key(lead.repository, commit, lead.path)
        if key in self._state.looked or key in self._night.looked:
            return
        if reason is not None:
            log.info("scout_lead_refused", repository=lead.repository, path=lead.path, reason=reason)
            self._night.refused += 1
            self._night.looked.append(key)
            return
        try:
            candidate: Candidate | None = await self._examine(lead, facts, commit)
        except Unusable as error:
            log.info("scout_lead_unusable", repository=lead.repository, path=lead.path, reason=error.message)
            self._night.unusable += 1
            self._night.looked.append(key)
            return
        self._night.looked.append(key)
        if candidate is None:
            log.info("scout_lead_without_skills", repository=lead.repository, path=lead.path)
            return
        self._night.found.append(candidate)
        log.info(
            "scout_candidate",
            plugin=candidate.id,
            repository=candidate.repository,
            risk=candidate.review.risk if candidate.review else None,
            fit=round(candidate.fit, 2),
            state=candidate.state,
        )

    def _updates_chosen(self) -> list[tuple[str, GalleryMarker]]:
        """The installed plugins whose updates a night looks for: those of the gallery or of a proposal (the others have
        no skills to compare), those looked at longest ago first, at most ``max_updates`` (the review of the sixth push:
        they were the first by name, each night the same, and those after them never had an update looked for)."""
        last: dict[str, float] = dict(self._state.seen)
        known: list[tuple[str, GalleryMarker]] = [one for one in self._installed if self._skills_of(one[0]) is not None]
        known.sort(key=lambda one: last.get(_update_key(one[0]), 0.0))
        return known[: self._deps.max_updates]

    def _tier_of(self, name: str) -> Tier:
        """The tier of the installed plugin ``name``: the gallery's official ones and those of a tier ``a`` proposal
        are ``a``, the rest ``b``."""
        for entry in self._deps.gallery:
            if entry.id == name:
                return "a" if entry.official else "b"
        for candidate in self._state.candidates:
            if candidate.id == name and candidate.kind == "new" and candidate.state == "installed":
                return candidate.tier
        return "b"

    def _skills_of(self, name: str) -> tuple[str, ...] | None:
        for entry in self._deps.gallery:
            if entry.id == name:
                return entry.skills
        for candidate in self._state.candidates:
            if candidate.id == name and candidate.state == "installed":
                return candidate.skills
        return None

    async def _update(self, name: str, marker: GalleryMarker) -> None:
        """Look for an update of the installed plugin ``name``: its repository's newest commit, and when its skills'
        files changed there, the change scanned and reviewed."""
        skills_paths: tuple[str, ...] | None = self._skills_of(name)
        if skills_paths is None:
            return
        self._night.seen.append(_update_key(name))
        github: GitHub = self._deps.github
        facts: RepoFacts = await github.repo(marker.repository)
        commit: str = await github.commit_of(marker.repository, facts.default_branch)
        # The version installed is part of what was looked at: after a Revert, the update from the version put back is
        # one not looked at yet (the review of the ninth push).
        key: str = self._look_key(marker.repository, commit, f"update:{name}@{marker.commit}")
        if commit == marker.commit or key in self._state.looked:
            return
        # An update passes the rules a new plugin passes, of the tier the plugin was installed from: a repository whose
        # licence changed, that was archived, or a community one that fell under its stars, has no update proposed (the
        # installed version stays as it is).
        tier: Tier = self._tier_of(name)
        reason: str | None = refusal(
            maker_lead(marker.repository).model_copy(update={"tier": tier}), facts, self._deps.today
        )
        if reason is not None:
            log.info("scout_update_refused", plugin=name, repository=marker.repository, reason=reason)
            self._night.refused += 1
            self._night.looked.append(key)
            return
        entry: GalleryEntry = GalleryEntry(
            id=name,
            name=name,
            publisher=facts.owner[:40] or "unknown",
            official=False,
            category="development",
            does=name,
            repository=marker.repository,
            commit=commit,
            skills=skills_paths,
            files=_PLACEHOLDER_HASH,
            licence=facts.licence or "",
            stars=facts.stars,
            added=self._deps.today,
        )
        archive: Path = await self._archive(entry)
        made: Path = self._staging / f"update-{name}"
        try:
            await asyncio.to_thread(unpack, entry, archive, made)
        except GalleryError as error:
            # The skills Dot has are not at that commit as they were (one went, or holds a link now): no update to
            # propose of that commit, and none looked for again (the review of the eighth push).
            log.info("scout_update_unusable", plugin=name, commit=commit, reason=error.message)
            self._night.unusable += 1
            self._night.looked.append(key)
            return
        skills: Path = made / SKILLS_DIR
        files: str = await asyncio.to_thread(files_hash, skills)
        if files == marker.files:
            # The skills did not change at that commit: nothing to propose, nor to look at again.
            self._night.looked.append(key)
            return
        changed: tuple[str, ...] = await asyncio.to_thread(
            changed_files, self._deps.plugins / name / SKILLS_DIR, skills
        )
        findings = await asyncio.to_thread(scan, skills)
        texts: tuple[SkillText, ...] = await asyncio.to_thread(skill_texts, skills)
        asked: Asked = question(
            name,
            entry.publisher,
            f"an update of {marker.repository} from {marker.commit[:12]} to {commit[:12]}",
            texts,
            findings,
            "\n".join(self._deps.goals[:5]),
            changed,
        )
        findings = _with_unread(findings, asked)
        verdict: Review = await review(
            self._deps.router, self._deps.model, self._deps.review_timeout_s, asked.text, findings
        )
        # Looked at once reviewed: a review that did not come leaves the update for the next night.
        self._night.looked.append(key)
        gallery_entry: GalleryEntry | None = next((one for one in self._deps.gallery if one.id == name), None)
        self._night.found.append(
            Candidate(
                id=name,
                kind="update",
                name=(gallery_entry.name if gallery_entry else name)[:40],
                publisher=(gallery_entry.publisher if gallery_entry else entry.publisher)[:40],
                tier=tier,
                source=f"an update of {marker.repository}"[:120],
                category=gallery_entry.category if gallery_entry else "development",
                repository=marker.repository,
                commit=commit,
                skills=skills_paths,
                files=files,
                licence=facts.licence or "",
                stars=facts.stars,
                created=facts.created,
                pushed=facts.pushed,
                findings=findings,
                review=verdict,
                fit=1.0,
                previous=marker.commit,
                changed=changed,
                state="refused" if verdict.refuse else "proposed",
                found_at=self._deps.now,
            )
        )
        log.info("scout_update", plugin=name, commit=commit, changed=len(changed), risk=verdict.risk)

    async def night(self) -> ScoutState:
        try:
            for name, marker in self._updates_chosen():
                if self._out_of_time():
                    break
                try:
                    await self._update(name, marker)
                except ScoutStopped:
                    raise
                except OutOfRequests:
                    self._night.out_of_requests = True
                    break
                except (DotError, OSError, ValueError) as error:
                    self._night.failed += 1
                    log.warning("scout_update_failed", plugin=name, error=_said(error))
            if not self._out_of_time():
                for lead in self._chosen(await self._leads()):
                    if self._out_of_time():
                        break
                    try:
                        await self._lead(lead)
                    except ScoutStopped:
                        raise
                    except OutOfRequests:
                        self._night.out_of_requests = True
                        break
                    # ValueError: a model of what a source said that does not hold (pydantic's ValidationError), which
                    # ends that plugin, never the night.
                    except (DotError, OSError, ValueError) as error:
                        self._night.failed += 1
                        log.warning("scout_lead_failed", repository=lead.repository, path=lead.path, error=_said(error))
        finally:
            await asyncio.to_thread(shutil.rmtree, self._staging, True)
        return self._settled()

    def _settled(self) -> ScoutState:
        night: _Night = self._night
        replaced: set[tuple[str, str]] = {(candidate.id, candidate.kind) for candidate in night.found}
        kept: tuple[Candidate, ...] = tuple(
            candidate
            for candidate in self._state.candidates
            # A newer proposal of the same update stands in for one not decided yet.
            if not (candidate.state == "proposed" and (candidate.id, candidate.kind) in replaced)
        )
        proposed: int = sum(1 for candidate in night.found if candidate.state == "proposed")
        summary: str = (
            f"{proposed} proposed, {len(night.found) - proposed} refused by the review, {night.refused} refused by the "
            f"rules, {night.unusable} unusable, {night.failed} failed{', out of time' if night.out_of_time else ''}"
            f"{", out of GitHub's requests" if night.out_of_requests else ''}"
        )
        log.info("scout_night", summary=summary, github_requests=self._deps.github.requests)
        taken: set[str] = set(night.seen)
        return bounded(
            ScoutState(
                candidates=(*night.found, *kept),
                looked=(*self._state.looked, *night.looked),
                declined=self._state.declined,
                seen=(
                    *(one for one in self._state.seen if one[0] not in taken),
                    *((key, self._deps.now) for key in night.seen),
                ),
                last_run=self._deps.now,
                last_summary=summary,
            )
        )


def _with_unread(findings: tuple[Finding, ...], asked: Asked) -> tuple[Finding, ...]:
    """``findings`` with the one that the review did not read some files in full, when it did not."""
    unread: Finding | None = unread_finding(asked.unread)
    return findings if unread is None else (*findings, unread)


def _holds(skills: Sequence[str], path: str) -> bool:
    """Whether a plugin of ``skills`` is, or is within, the folder ``path`` of its repository. A lead of the whole
    repository ("") is never held by one plugin: its skills are left out one by one (:meth:`_Scout._held`)."""
    return bool(path) and any(skill == path or skill.startswith(f"{path}/") for skill in skills)


def _update_key(name: str) -> str:
    """The key in ``seen`` of the look for an update of the installed plugin ``name`` (a lead's key holds a ``/``)."""
    return f"update:{name}"


def _seen_key(lead: Lead) -> str:
    return f"{lead.repository.lower()}:{lead.path}"


def _said(error: Exception) -> str:
    return error.message if isinstance(error, DotError) else f"{type(error).__name__}: {error}"


async def run_night(deps: ScoutDeps, state: ScoutState) -> ScoutState:
    """One night of the scout from ``state``: the state it leaves, which the caller stores."""
    return await _Scout(deps, state).night()
