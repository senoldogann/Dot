"""Long-term memory: notes saved by the agent (or the user) and found again by words and by meaning.

A search runs two rankers over the same notes - BM25 over the words and cosine similarity over the embeddings - and
merges their rankings with reciprocal rank fusion, so a name or an identifier is found as reliably as a paraphrase.
Embeddings come from Ollama or from the OpenAI API (``[memory] embed_provider``); with ``embed_enabled = false`` only
the words are searched. Nothing here falls back silently: when the embedding service fails, the call fails with its
error.

Each stored vector belongs to the embedding model that made it. The model name is kept in the ``kv`` table, and
:func:`sync_embedding_model` drops all vectors when the configured model is another one; :func:`backfill` embeds
the notes that have no vector, so a search always covers every note.
"""

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from dotagent.config import AppConfig, MemoryConfig
from dotagent.core.errors import InvalidValueError, StoreError
from dotagent.core.learned_text import OWNER_MARK, gate
from dotagent.core.text import estimate_text_tokens
from dotagent.core.types import FrozenModel
from dotagent.memory.embedder import Embedder, build_embedder
from dotagent.memory.ranking import Similar, Vector, fused_scores, most_similar, ranked_by_use
from dotagent.memory.words import lowercase, search_terms
from dotagent.secrets import mask_filled
from dotagent.store.db import Store
from dotagent.store.models import AwakeNotes, MemoryRecord, MemoryScore

CORE_TAG: str = "core"
# The tags the nightly reflection (:mod:`dotagent.agent.reflection`) writes: the day's journal note, and a lesson
# that rests, which a search no longer finds while the Memory page still lists it.
JOURNAL_TAG: str = "journal"
REST_TAG: str = "rest"
# The source of a note the reflection wrote, in place of a run's id.
REFLECTION_SOURCE: str = "reflection"
# The tag of a note the owner took out of a merge (item 584): the nightly merge leaves it apart from the others. The
# owner can take it off.
SEPARATE_TAG: str = "keep-separate"
# The kv key of the owner section (item 93): what Dot knows about the owner, kept by the nightly reflection.
OWNER_KEY: str = "owner.section"
# The mark the prompt puts before a line that is the owner's own, and says outranks the others (item 93), is
# :data:`dotagent.core.learned_text.OWNER_MARK`: the gate refuses it in every text Dot learns (item 587).
# The most lines Dot's part of the owner section holds.
OWNER_LINES: int = 12
_MODEL_KEY: str = "memory.embed_model"
_BACKFILL_BATCH: int = 32
_QUERY_CHARS: int = 1500
_SIMILAR_SHOWN: int = 3
_MIN_NOTE_CHARS: int = 3
_MAX_TAGS: int = 6
_TAG_CHARS: int = 32
_TAG_NOISE: re.Pattern[str] = re.compile(r"\W+")
_MIN_NOTES_FOR_SHARE: int = 4
_MAX_NOTE_SHARE: float = 0.5


@dataclass(frozen=True)
class MemoryIndex:
    """The note store plus the embedder that makes it searchable by meaning (``None`` when embeddings are off)."""

    store: Store
    embedder: Embedder | None
    config: MemoryConfig


def words_only(index: MemoryIndex) -> MemoryIndex:
    """``index`` searched by the words of its notes alone, as with embeddings off: what a run starts with when the
    embedding service failed (item 256), which the run says."""
    return MemoryIndex(store=index.store, embedder=None, config=index.config)


class MemoryHit(FrozenModel):
    """A note found by a search; ``similarity`` is its cosine to the query, ``None`` when only the words matched."""

    record: MemoryRecord
    similarity: float | None


def stands(record: MemoryRecord) -> bool:
    """Whether ``record`` stands for every run (item 613): tagged ``core`` and the owner's own, a note they wrote or one
    they pinned. A run may tag a note ``core`` to ask that it stand, and it stands once the owner pins it: until then it
    is handed when it fits, rests and may be proposed to be forgotten as any note of a run's. Every run of a week was
    handed the core notes runs had written of a thesis and a job search, whatever its goal, and no rule let them go."""
    return CORE_TAG in record.tags and (record.source_run is None or record.pinned_at is not None)


def visible_to(record: MemoryRecord, agent_id: str | None) -> bool:
    """Whether a run of the agent ``agent_id`` (``None`` for Dot) may be handed ``record`` (item 94): Dot's notes go
    to every run, an agent's lessons to its own runs alone."""
    return record.agent_id is None or record.agent_id == agent_id


class SavedMemory(FrozenModel):
    """The outcome of saving: the note, whether it is new, and the existing notes that say nearly the same."""

    record: MemoryRecord
    created: bool
    similar: tuple[MemoryHit, ...]


def normalize_text(text: str) -> str:
    """A note as one line: runs of whitespace (newlines included) become a single space."""
    return " ".join(text.split())


def normalize_tags(tags: Sequence[str]) -> tuple[str, ...]:
    """Tags as short lowercase words, without duplicates. A tag never holds a comma: the store joins tags with it."""
    cleaned: dict[str, None] = {}
    for tag in tags:
        lowered: str = lowercase(tag.strip())
        slug: str = _TAG_NOISE.sub("-", lowered).strip("-")[:_TAG_CHARS].strip("-")
        if slug:
            cleaned[slug] = None
    return tuple(cleaned)[:_MAX_TAGS]


def _checked_text(content: str, max_chars: int) -> str:
    # Every write of a note comes here, a model's save, a lesson, a pin and the owner's own: a value filled into a page
    # is never kept in one, nor sent to the embedding model (item 621). Nothing refuses a note for holding one.
    text: str = normalize_text(mask_filled(content))
    if len(text) < _MIN_NOTE_CHARS:
        raise InvalidValueError("the note is empty", {"content": content})
    if len(text) > max_chars:
        raise InvalidValueError(
            f"the note is {len(text)} characters long; the limit is {max_chars}. Save one shorter, single fact.",
            {"chars": len(text), "max_chars": max_chars},
        )
    return text


async def open_memory_index(store: Store, config: AppConfig) -> MemoryIndex:
    """The memory of ``store``; its stored vectors are checked against the configured embedding model."""
    embedder: Embedder | None = await build_embedder(config) if config.memory.embed_enabled else None
    index: MemoryIndex = MemoryIndex(store=store, embedder=embedder, config=config.memory)
    await sync_embedding_model(index)
    return index


async def close_memory_index(index: MemoryIndex) -> None:
    if index.embedder is not None:
        await index.embedder.aclose()


async def sync_embedding_model(index: MemoryIndex) -> None:
    """Drop every stored vector when another embedding model made them; they are made again on demand."""
    embedder: Embedder | None = index.embedder
    if embedder is None:
        return
    if await index.store.kv_get(_MODEL_KEY) == embedder.model:
        return
    await index.store.clear_memory_vectors()
    await index.store.kv_set(_MODEL_KEY, embedder.model)


async def backfill(index: MemoryIndex) -> int:
    """Embed the notes that have no vector yet; returns how many were embedded."""
    embedder: Embedder | None = index.embedder
    if embedder is None:
        return 0
    embedded: int = 0
    while True:
        pending: list[MemoryRecord] = await index.store.memories_without_vectors(_BACKFILL_BATCH)
        if not pending:
            return embedded
        vectors: list[Vector] = await embedder.embed([record.content for record in pending])
        for record, vector in zip(pending, vectors, strict=True):
            await index.store.set_memory_vector(record.id, vector)
        embedded += len(pending)


async def reindex(index: MemoryIndex) -> int:
    """Embed every note again; returns how many notes there are."""
    await index.store.clear_memory_vectors()
    return await backfill(index)


async def _similar_notes(index: MemoryIndex, vector: Vector) -> tuple[MemoryHit, ...]:
    close: list[Similar] = most_similar(
        vector, await index.store.all_memory_vectors(), index.config.similar_threshold, _SIMILAR_SHOWN
    )
    scores: dict[str, float] = {item.id: item.similarity for item in close}
    records: list[MemoryRecord] = await index.store.get_memories([item.id for item in close])
    return tuple(MemoryHit(record=record, similarity=scores[record.id]) for record in records)


async def save_memory(
    index: MemoryIndex,
    content: str,
    tags: Sequence[str],
    run_id: str | None,
    agent_id: str | None,
    expires_at: float | None,
    tainted: bool,
) -> SavedMemory:
    """Save a note, Dot's or, with ``agent_id``, an agent's own (:func:`visible_to`), holding until ``expires_at``
    (``None``: until it is changed). The same text twice stays one note (the tags are merged, the owner kept, the time
    it holds is the latest save's), saved one after the other or at once, as long as the saver is handed the note that
    has it (another agent's note is not merged into); a resting note saved again is awake. A run that had read content
    third parties can author saves a tainted note (``tainted``): a note keeps where it came from. A note that had the
    text already keeps the mark it had, since the same words bring nothing new in. A new note comes back with the
    existing notes the saver is handed that are nearly the same, so the caller can retire the ones that are outdated."""
    text: str = _checked_text(content, index.config.max_chars)
    clean_tags: tuple[str, ...] = normalize_tags(tags)
    existing: MemoryRecord | None = await index.store.find_memory_by_content(text, agent_id)
    if existing is not None:
        return await _saved_again(index.store, existing, clean_tags, expires_at)
    vector: Vector | None = None
    similar: tuple[MemoryHit, ...] = ()
    if index.embedder is not None:
        await backfill(index)
        vector = (await index.embedder.embed([text]))[0]
        similar = tuple(hit for hit in await _similar_notes(index, vector) if visible_to(hit.record, agent_id))
    record, created = await index.store.add_memory_once(text, clean_tags, run_id, agent_id, vector, expires_at, tainted)
    if not created:
        # Another save of the text, by another run or process, added it meanwhile.
        return await _saved_again(index.store, record, clean_tags, expires_at)
    return SavedMemory(record=record, created=True, similar=similar)


def _merged_tags(own: Sequence[str], saved: Sequence[str]) -> tuple[str, ...]:
    """The tags of a note saved again: its own, then the ones saved now, at most :data:`_MAX_TAGS`, or as many as it
    had: a note made by a merge holds the tags of every source (item 584), and saving its words again keeps them."""
    both: tuple[str, ...] = tuple(dict.fromkeys([*own, *normalize_tags(saved)]))
    return both[: max(_MAX_TAGS, len(own))]


async def _saved_again(
    store: Store,
    existing: MemoryRecord,
    tags: tuple[str, ...],
    expires_at: float | None,
) -> SavedMemory:
    """The note ``existing``, whose text was saved again with ``tags``, holding until ``expires_at``: the tags are
    merged into its own, and a note the reflection rested (tagged ``rest``), or whose time ran out or changes, is
    awake, written anew now, since a run found it worth saving again. A note a merge put aside (item 584) is saved
    again as the note it was merged into: that says it, unless it was forgotten since."""
    if existing.merged_into is not None:
        standing: list[MemoryRecord] = await store.get_memories([existing.merged_into])
        existing = standing[0] if standing else existing
    merged: tuple[str, ...] = _merged_tags(existing.tags, tags)
    if REST_TAG in existing.tags or existing.expires_at != expires_at:
        awake: tuple[str, ...] = tuple(tag for tag in merged if tag != REST_TAG)
        woken: MemoryRecord = await store.wake_memory(existing.id, awake, time.time(), expires_at)
        return SavedMemory(record=woken, created=False, similar=())
    record: MemoryRecord = existing if merged == existing.tags else await store.set_memory_tags(existing.id, merged)
    return SavedMemory(record=record, created=False, similar=())


async def _distinctive_terms(store: Store, terms: tuple[str, ...], total: int) -> tuple[str, ...]:
    """The ``terms`` that tell notes apart. A word found in more than half of the notes (``user``, when every note
    starts with "The user") matches everything and says nothing, once there are enough notes to tell."""
    if total < _MIN_NOTES_FOR_SHARE or not terms:
        return terms
    counts: dict[str, int] = await store.memory_term_counts(terms)
    return tuple(term for term in terms if counts[term] <= total * _MAX_NOTE_SHARE)


def expired(record: MemoryRecord, now: float) -> bool:
    """Whether the time a note was saved for has run out (item 306)."""
    return record.expires_at is not None and record.expires_at <= now


def resting(record: MemoryRecord, now: float, rest_days: int, score: MemoryScore | None, min_uses: int) -> bool:
    """Whether a note rests: its time ran out (:func:`expired`), or it is the agent's (the user's notes and the ones the
    owner pinned, the standing ones among them, never rest otherwise; a run's ``core`` note rests as any of a run's
    until the owner pins it, item 613) and either the reflection retired it (tagged ``rest``: unused, of no help, or
    replaced by a newer note), or no run was handed it in ``rest_days`` days since it was last
    handed or written (item 306: a note handed once long ago goes stale like one never handed), or its ``score`` (how
    the runs handed it lately ended) is negative over at least ``min_uses`` runs. A resting note is not handed to a run
    on its own; a search still finds it unless it was retired or its time ran out."""
    if expired(record, now):
        return True
    if record.pinned_at is not None or record.source_run is None:
        return False
    if REST_TAG in record.tags:
        return True
    if score is not None and score.handed >= min_uses and score.done < score.failed:
        return True
    return max(record.created_at, record.updated_at, record.last_used_at or 0.0) < now - rest_days * 86400.0


async def recent_scores(index: MemoryIndex, memory_ids: Sequence[str], now: float) -> dict[str, MemoryScore]:
    """The scores of ``memory_ids`` over the last ``[memory] score_days``, from every run."""
    return await index.store.memory_scores(memory_ids, now - index.config.score_days * 86400.0)


async def recent_scores_for(
    index: MemoryIndex, memory_ids: Sequence[str], now: float, agent_id: str | None
) -> dict[str, MemoryScore]:
    """The scores of ``memory_ids`` over the last ``[memory] score_days`` as a run of ``agent_id`` ranks by them:
    from every run for Dot (``None``), from the agent's own runs for an agent (item 94)."""
    since: float = now - index.config.score_days * 86400.0
    if agent_id is None:
        return await index.store.memory_scores(memory_ids, since)
    return await index.store.agent_memory_scores(memory_ids, since, agent_id)


async def recall(index: MemoryIndex, query: str, limit: int) -> list[MemoryHit]:
    """The ``limit`` notes that best fit ``query``, best first, among every note (the owner's view: the Memory page,
    ``dotx memory``, the lesson writer's check for a note that says it already): the fusion of the two rankers, with
    the agent's notes lifted by how lately a run was handed them (:func:`dotagent.memory.ranking.ranked_by_use`,
    half-life ``[memory] use_half_life_days``) and by their score (:func:`recent_scores`), either way. A note the
    reflection retired (tagged ``rest``) is never one, nor one whose time ran out (:func:`expired`)."""
    return await _recall(index, query, limit, None, False)


async def recall_for(index: MemoryIndex, query: str, limit: int, agent_id: str | None) -> list[MemoryHit]:
    """Like :func:`recall`, as a run of the agent ``agent_id`` searches (``None`` for Dot's run): among the notes it
    may be handed (:func:`visible_to`), by the scores of its own runs (:func:`recent_scores_for`)."""
    return await _recall(index, query, limit, agent_id, True)


async def _recall(index: MemoryIndex, query: str, limit: int, agent_id: str | None, scoped: bool) -> list[MemoryHit]:
    text: str = normalize_text(query)[:_QUERY_CHARS]
    total: int = await index.store.count_memories()
    if not text or limit <= 0 or total == 0:
        return []
    config: MemoryConfig = index.config
    terms: tuple[str, ...] = await _distinctive_terms(index.store, search_terms(text), total)
    now: float = time.time()
    # The cut to the best ``candidate_pool`` notes is made among the notes that may be found (item 557): resting and
    # expired notes, and an agent's lessons in a search that is not its own, do not use up the places of the others.
    among: AwakeNotes = AwakeNotes(now=now, rest_tag=REST_TAG, scoped=scoped, agent_id=agent_id)
    rankings: list[list[str]] = [await index.store.search_memories_terms(terms, config.candidate_pool, among)]
    similarities: dict[str, float] = {}
    if index.embedder is not None:
        await backfill(index)
        query_vector: Vector = (await index.embedder.embed([text]))[0]
        close: list[Similar] = most_similar(
            query_vector, await index.store.all_memory_vectors(among), config.min_similarity, config.candidate_pool
        )
        rankings.append([item.id for item in close])
        similarities = {item.id: item.similarity for item in close}
    scores: dict[str, float] = fused_scores(rankings)
    candidates: list[MemoryRecord] = [
        record
        for record in await index.store.get_memories(list(scores))
        if REST_TAG not in record.tags and not expired(record, now) and (not scoped or visible_to(record, agent_id))
    ]
    balances: dict[str, MemoryScore] = await recent_scores_for(
        index, [record.id for record in candidates], now, agent_id
    )
    ranked: list[MemoryRecord] = ranked_by_use(
        scores, candidates, now, config.use_half_life_days * 86400.0, balances, config.score_min_uses, limit
    )
    return [MemoryHit(record=record, similarity=similarities.get(record.id)) for record in ranked]


def _closest_first(notes: Sequence[MemoryRecord], similarities: dict[str, float], limit: int) -> list[MemoryRecord]:
    """``limit`` of ``notes``: the ones closest to the query first, then the most recently changed."""
    ranked: list[MemoryRecord] = sorted(
        notes, key=lambda record: (-similarities.get(record.id, -1.0), -record.updated_at)
    )
    return ranked[:limit]


class OwnerSection(FrozenModel):
    """What Dot knows about the owner: ``dot_lines``, which the nightly reflection rewrites from the owner's notes and
    the day's runs, and ``owner_lines``, the owner's own corrections, kept as they are and never rewritten;
    ``updated_at`` is when either last changed (``0.0`` before anything was written)."""

    dot_lines: tuple[str, ...]
    owner_lines: tuple[str, ...]
    updated_at: float


EMPTY_OWNER: OwnerSection = OwnerSection(dot_lines=(), owner_lines=(), updated_at=0.0)


async def owner_section(store: Store) -> OwnerSection:
    """The owner section the store holds, empty before the first reflection wrote one."""
    raw: str | None = await store.kv_get(OWNER_KEY)
    if raw is None:
        return EMPTY_OWNER
    try:
        return OwnerSection.model_validate_json(raw)
    except ValidationError as exc:
        raise StoreError("the owner section cannot be read", {"key": OWNER_KEY, "error": str(exc)}) from exc


async def set_owner_section(store: Store, section: OwnerSection) -> None:
    await store.kv_set(OWNER_KEY, section.model_dump_json())


def owner_lines_within(lines: Sequence[str], owner_chars: int) -> tuple[str, ...]:
    """``lines`` as Dot's part of the section: one line each, without blanks, at most :data:`OWNER_LINES`, and no
    more than ``owner_chars`` characters in all (the lines past the budget are left out)."""
    kept: list[str] = []
    spent: int = 0
    for line in lines:
        flat: str = " ".join(line.split())
        if not flat or len(kept) >= OWNER_LINES:
            continue
        if spent + len(flat) > owner_chars:
            break
        kept.append(flat)
        spent += len(flat)
    return tuple(kept)


def dot_line_problem(line: str, secrets: Sequence[str]) -> str | None:
    """Why ``line`` may not stand among Dot's lines of the owner section, or ``None`` (item 587): it holds what the one
    gate for learned texts refuses (:func:`dotagent.core.learned_text.gate`), the owner's mark among it
    (:data:`OWNER_MARK`, which the prompt says outranks the other lines). ``secrets`` are the values Dot holds."""
    return gate(line, secrets)


def vetted_dot_lines(
    lines: Sequence[str], secrets: Sequence[str]
) -> tuple[tuple[str, ...], tuple[tuple[int, str], ...]]:
    """The lines of ``lines`` that may stand among Dot's lines of the owner section, and each that may not with its
    number (from 1) and why (:func:`dot_line_problem`). The section stands unfenced at the end of every prompt, and what
    the reflection's model writes of the owner comes from their notes and the day's goals, which a page may have
    reached: a line that is refused is dropped, and the rest are kept."""
    kept: list[str] = []
    refused: list[tuple[int, str]] = []
    for number, line in enumerate(lines, start=1):
        problem: str | None = dot_line_problem(line, secrets)
        if problem is None:
            kept.append(line)
        else:
            refused.append((number, problem))
    return tuple(kept), tuple(refused)


def owner_prompt_lines(section: OwnerSection, secrets: Sequence[str] = ()) -> tuple[str, ...]:
    """The section as the prompt lists it: the owner's own lines first, marked as theirs, then Dot's. A line of Dot's
    that the gate refuses (:func:`dot_line_problem`, with ``secrets``, the values Dot holds) is left out here too, also
    one written before the gate was: the prompt says a line with the owner's mark outranks the others. A value filled
    into a page is not among ``secrets`` (the gate must not tell by it, item 621); no line shows one, it is masked."""
    return (
        *(mask_filled(f"{OWNER_MARK} {line}") for line in section.owner_lines),
        *(mask_filled(line) for line in section.dot_lines if dot_line_problem(line, secrets) is None),
    )


class Remembered(FrozenModel):
    """The notes a run starts with, the tokens they are estimated to take, and how many of the notes that matched it
    were left out as saved while reading outside content, whether or not they would have fit (``tainted_left_out``,
    item 384)."""

    records: tuple[MemoryRecord, ...]
    tokens: int
    tainted_left_out: int


class LiveNotes(FrozenModel):
    """The notes changed while a run works that it is handed, and how many more it could be handed that did not fit."""

    records: tuple[MemoryRecord, ...]
    left_out: int


# The tokens a note's rendered line adds to its text and tags: who wrote it and when.
_NOTE_LINE_TOKENS: int = 12
# How many standing and user notes are read before the budget picks among them.
_FETCH_NOTES: int = 200


def note_tokens(record: MemoryRecord) -> int:
    """The tokens a note takes in the prompt, estimated: its text, its tags and the line around them."""
    return estimate_text_tokens(record.content + "".join(f" #{tag}" for tag in record.tags)) + _NOTE_LINE_TOKENS


def memory_budget(config: MemoryConfig, context_length: int | None) -> int:
    """The tokens a run's starting notes may take: ``inject_share`` of the model's window, at most
    ``inject_tokens``; ``inject_tokens`` alone when the provider does not say the window."""
    if context_length is None:
        return config.inject_tokens
    return min(config.inject_tokens, round(context_length * config.inject_share))


def fit_notes(notes: Sequence[MemoryRecord], budget: int, spent: int) -> tuple[list[MemoryRecord], int]:
    """``notes`` in order, each taken when it still fits ``budget`` after ``spent`` tokens; a note that does not fit
    is passed over, a shorter one after it may still fit. Returns the taken notes and the tokens spent in all."""
    taken: list[MemoryRecord] = []
    for note in notes:
        cost: int = note_tokens(note)
        if spent + cost <= budget:
            taken.append(note)
            spent += cost
    return taken, spent


def fit_groups(groups: Sequence[Sequence[MemoryRecord]], budget: int) -> tuple[list[MemoryRecord], int]:
    """The notes of ``groups`` that fit ``budget`` tokens (:func:`fit_notes`), group by group in their order: first
    each group within its own share (an equal part of the budget), then, in the same order, the notes left over in what
    is left, so that a long group cannot crowd out the ones after it (item 154). Returns the taken notes and the tokens
    spent in all."""
    share: int = budget // max(len(groups), 1)
    picked: set[str] = set()
    spent: int = 0
    for group in groups:
        part, cost = fit_notes(group, share, 0)
        picked.update(note.id for note in part)
        spent += cost
    for group in groups:
        part, spent = fit_notes([note for note in group if note.id not in picked], budget, spent)
        picked.update(note.id for note in part)
    return [note for group in groups for note in group if note.id in picked], spent


async def prompt_memories(index: MemoryIndex, query: str, budget: int, agent_id: str | None) -> Remembered:
    """The notes a run of the agent ``agent_id`` (``None`` for Dot) starts with, without repeats, as many as fit
    ``budget`` tokens, each group within its own share first (:func:`fit_groups`), in this order: the standing ones
    (:func:`stands`: tagged ``core`` and the owner's, newest first), the ones the user wrote (closest to ``query``
    first), the agent's own lessons and then Dot's notes related to ``query`` (the goal and the earlier goals of its
    thread; by fit and the agent's own scores) that do not rest (:func:`resting`). Only the notes the run may be
    handed are among them (:func:`visible_to`); a note that does not fit is left to ``memory_search``. A note saved
    while reading outside
    content (``tainted``) is never among them (item 384): the start notes are handed as trusted data, with no reader in
    between, so it is left to ``memory_search``, whose answer counts as outside content, and the notes left out so are
    counted (``tainted_left_out``).

    A goal is full of everyday words that match unrelated notes, so when embeddings are on only the notes whose
    meaning clearly fits the query are taken; ``memory_search`` still finds the others, and the resting ones."""
    config: MemoryConfig = index.config
    now: float = time.time()
    standing: list[MemoryRecord] = [
        record
        for record in await index.store.memories_with_tag(CORE_TAG, _FETCH_NOTES)
        if stands(record) and visible_to(record, agent_id) and not expired(record, now)
    ]
    candidates: list[MemoryHit] = await recall_for(index, query, config.candidate_pool, agent_id)
    similarities: dict[str, float] = {hit.record.id: hit.similarity for hit in candidates if hit.similarity is not None}
    written: list[MemoryRecord] = await index.store.user_memories(_FETCH_NOTES)
    known: set[str] = {record.id for record in standing}
    users: list[MemoryRecord] = _closest_first(
        [
            record
            for record in written
            if record.id not in known and visible_to(record, agent_id) and not expired(record, now)
        ],
        similarities,
        _FETCH_NOTES,
    )
    known.update(record.id for record in users)
    related: list[MemoryHit] = (
        candidates
        if index.embedder is None
        else [
            hit for hit in candidates if hit.similarity is not None and hit.similarity >= config.inject_min_similarity
        ]
    )
    balances: dict[str, MemoryScore] = await recent_scores_for(index, [hit.record.id for hit in related], now, agent_id)
    fresh: list[MemoryRecord] = [
        hit.record
        for hit in related
        if hit.record.id not in known
        and not resting(hit.record, now, config.rest_days, balances.get(hit.record.id), config.score_min_uses)
    ]
    own: list[MemoryRecord] = [record for record in fresh if record.agent_id is not None]
    shared: list[MemoryRecord] = [record for record in fresh if record.agent_id is None]
    groups: tuple[list[MemoryRecord], ...] = (standing, users, own, shared)
    tainted: int = sum(1 for group in groups for record in group if record.tainted)
    taken, spent = fit_groups(tuple([record for record in group if not record.tainted] for group in groups), budget)
    return Remembered(records=tuple(taken), tokens=spent, tainted_left_out=tainted)


async def live_memories(
    index: MemoryIndex, changed: Sequence[MemoryRecord], budget: int, agent_id: str | None
) -> LiveNotes:
    """Of the notes ``changed`` while a run of the agent ``agent_id`` works, the ones it would be handed at its start
    (item 157): those it may be handed (:func:`visible_to`) that do not rest (:func:`resting`) and were not saved while
    reading outside content (item 384), in their order, as many as fit ``budget`` tokens (:func:`fit_notes`)."""
    config: MemoryConfig = index.config
    now: float = time.time()
    visible: list[MemoryRecord] = [record for record in changed if visible_to(record, agent_id) and not record.tainted]
    balances: dict[str, MemoryScore] = await recent_scores_for(index, [record.id for record in visible], now, agent_id)
    fresh: list[MemoryRecord] = [
        record
        for record in visible
        if not resting(record, now, config.rest_days, balances.get(record.id), config.score_min_uses)
    ]
    taken, _ = fit_notes(fresh, budget, 0)
    return LiveNotes(records=tuple(taken), left_out=len(fresh) - len(taken))


async def update_memory_note(index: MemoryIndex, memory_id: str, content: str, tags: Sequence[str]) -> MemoryRecord:
    """The user's rewrite of a note in place (text and tags): it keeps its id, its author and its use count and is
    embedded again. The user vouches for the text they saved, so a note saved while reading outside content no longer
    counts as such, and goes back among the notes a run starts with (item 384)."""
    existing: MemoryRecord = await index.store.get_memory(memory_id)
    text, vector = await _embedded(index, content)
    return await index.store.update_memory_vouched(memory_id, text, normalize_tags(tags), vector, existing.source_run)


async def rewrite_memory_note(
    index: MemoryIndex, memory_id: str, content: str, tags: Sequence[str], run_id: str, tainted: bool
) -> MemoryRecord:
    """A run's rewrite of a note in place, as :func:`update_memory_note`, except that the run becomes its author: the
    text is the model's now, so a note the user wrote no longer reads as theirs (item 157). A run that had read content
    third parties can author leaves the note tainted, and a clean one leaves the mark a note had."""
    await index.store.get_memory(memory_id)
    text, vector = await _embedded(index, content)
    return await index.store.update_memory(memory_id, text, normalize_tags(tags), vector, run_id, tainted)


async def _embedded(index: MemoryIndex, content: str) -> tuple[str, Vector | None]:
    """A note's text, checked, and its embedding, or ``None`` when embeddings are off."""
    text: str = _checked_text(content, index.config.max_chars)
    if index.embedder is None:
        return text, None
    return text, (await index.embedder.embed([text]))[0]


async def forget_memory(index: MemoryIndex, memory_id: str) -> MemoryRecord:
    """Forget a note, whoever asks (the owner on the Memory page or with ``dotx memory forget``, a run with
    ``memory_forget``): it goes to the trash, whole, for ``[memory] trash_days`` (item 584), from which the owner can
    bring it back (:func:`dotagent.memory.lifecycle.restore_note`). Returns the note. An unknown id is an error."""
    return await index.store.trash_memory(memory_id, time.time())
