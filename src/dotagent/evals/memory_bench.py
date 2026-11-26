"""The memory benchmark (item 1728): does a search find the newest fact when a fact changed?

Each case of :mod:`dotagent.evals.memory_cases` is played in a store of its own: a run saves its facts one after the
other (the way ``memory_save`` saves a note), some of them are handed to a run in between, and then the question is
searched the way ``memory_search`` searches (:func:`dotagent.memory.service.recall_for`), asking for the three best
notes. Three things are counted for a fact that changed: the newest fact came first, it was among the three, and an
outdated one was among the three too (a run that reads both acts on the right one about half the time, MERIT 2026).
For a fact that did not change, that it is still found.

With ``scripted`` embeddings (:class:`ScriptedEmbedder`) it runs offline in a test, the words ranking by BM25 as they
do; with the configured embedding model it measures what a search finds on this Mac. No model is asked to write or to
judge: only the notes' ranking is measured, not what a run does with them.
"""

import math
import shutil
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from dotagent.config import AppConfig, MemoryConfig
from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.evals.memory_cases import CASES, Fact, MemoryCase
from dotagent.memory.embedder import Embedder, build_embedder
from dotagent.memory.ranking import Vector
from dotagent.memory.service import MemoryHit, MemoryIndex, normalize_text, recall_for
from dotagent.memory.supersede import handed_echo, save_replacing
from dotagent.store.db import Store
from dotagent.store.models import MemoryRecord

# The run that saves every note of the benchmark: a note a run saved, which a run may replace.
RUN_ID: str = "run_memory_bench"
# The second run of an E case (item 1866): handed the old fact as it was saved, it saves it again at the end.
ECHO_RUN_ID: str = "run_memory_bench_echo"
# How many notes a search is asked for: the few a run reads.
SHOWN: int = 3
# Below this a pivot of the factorisation is not a number the vectors can be made from.
_PIVOT_FLOOR: float = 1e-9


class ScriptedEmbedder:
    """Embeddings that are looked up: the vector of each text a case saves or asks, made so that their cosines are the
    numbers the case gives (:func:`case_vectors`). A text it was not given is an error, not a guess."""

    def __init__(self, vectors: Mapping[str, Vector]) -> None:
        self._vectors: Mapping[str, Vector] = vectors

    @property
    def model(self) -> str:
        return "scripted-memory-bench"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        missing: list[str] = [text for text in texts if text not in self._vectors]
        if missing:
            raise InvalidValueError("the scripted embeddings were not given this text", {"texts": " | ".join(missing)})
        return [self._vectors[text] for text in texts]

    async def aclose(self) -> None:
        return None


def cholesky_vectors(gram: Sequence[Sequence[float]], context: str) -> list[Vector]:
    """Unit vectors whose dot products are the entries of ``gram`` (a symmetric matrix with ones on its diagonal): its
    Cholesky factor's rows. A matrix no vectors have (a cosine too far from the others) is an error that names
    ``context``."""
    size: int = len(gram)
    rows: list[list[float]] = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            known: float = math.sumprod(rows[i][:j], rows[j][:j])
            if i == j:
                pivot: float = gram[i][i] - known
                if pivot < _PIVOT_FLOOR:
                    raise InvalidValueError("no vectors have these cosines", {"case": context, "row": i})
                rows[i][j] = math.sqrt(pivot)
            else:
                rows[i][j] = (gram[i][j] - known) / rows[j][j]
    return [tuple(row) for row in rows]


def case_vectors(case: MemoryCase) -> dict[str, Vector]:
    """The scripted vector of each fact's text and of the question of ``case``: the facts are ``pair_cosines`` close to
    one another and each fact is its ``question_cosine`` close to the question."""
    count: int = len(case.facts)
    gram: list[list[float]] = [[1.0 if i == j else 0.0 for j in range(count + 1)] for i in range(count + 1)]
    pairs: list[tuple[int, int]] = [(i, j) for i in range(count) for j in range(i + 1, count)]
    if len(pairs) != len(case.pair_cosines):
        raise InvalidValueError(
            "a case needs one cosine for each pair of its facts",
            {"case": case.id, "facts": count, "pair_cosines": len(case.pair_cosines)},
        )
    for (i, j), cosine in zip(pairs, case.pair_cosines, strict=True):
        gram[i][j] = gram[j][i] = cosine
    for i, fact in enumerate(case.facts):
        gram[i][count] = gram[count][i] = fact.question_cosine
    vectors: list[Vector] = cholesky_vectors(gram, case.id)
    texts: list[str] = [normalize_text(fact.text) for fact in case.facts]
    return {**dict(zip(texts, vectors[:count], strict=True)), normalize_text(case.question): vectors[count]}


class CaseResult(FrozenModel):
    """What one case found: ``shown`` are the facts (their places in the case) the search returned, best first."""

    case_id: str
    kind: str
    keyed: bool
    used: bool
    echoed: bool
    shown: tuple[int, ...]
    answer: int
    outdated: tuple[int, ...]

    @property
    def first(self) -> bool:
        return bool(self.shown) and self.shown[0] == self.answer

    @property
    def found(self) -> bool:
        return self.answer in self.shown

    @property
    def stale(self) -> bool:
        return any(place in self.shown for place in self.outdated)


async def _save_fact(index: MemoryIndex, fact: Fact, run_id: str) -> None:
    """The fact saved by the run ``run_id`` as ``memory_save`` saves a note, with the subject the run gave it: not when
    it is the words of a note recall handed the run (item 1866)."""
    if await handed_echo(index, run_id, fact.text) is None:
        await save_replacing(index, fact.text, [], run_id, None, False, fact.subject)


async def play_case(case: MemoryCase, embedder: Embedder, config: MemoryConfig, folder: Path) -> CaseResult:
    """``case`` played in a store of its own under ``folder``, embedded by ``embedder``: the scripted one of the case,
    or the configured model."""
    store: Store = await Store.open(folder / f"{case.id}.db", folder / f"{case.id}-images")
    try:
        index: MemoryIndex = MemoryIndex(store=store, embedder=embedder, config=config)
        for place, fact in enumerate(case.facts):
            await _save_fact(index, fact, RUN_ID)
            note: MemoryRecord | None = await store.find_memory_by_content(normalize_text(fact.text), None)
            if note is None:
                raise InvalidValueError("a fact the case saved is not in its store", {"case": case.id, "fact": place})
            if place in case.used:
                await store.mark_memories_used(RUN_ID, [note.id], time.time(), "start")
            if place in case.echoed:
                await store.mark_memories_used(ECHO_RUN_ID, [note.id], time.time(), "start")
        for place in case.echoed:
            await _save_fact(index, case.facts[place], ECHO_RUN_ID)
        places: dict[str, int] = {normalize_text(fact.text): place for place, fact in enumerate(case.facts)}
        hits: list[MemoryHit] = await recall_for(index, case.question, SHOWN, None)
    finally:
        await store.close()
    return CaseResult(
        case_id=case.id,
        kind=case.kind,
        keyed=case.facts[case.answer].subject is not None,
        used=bool(case.used),
        echoed=bool(case.echoed),
        shown=tuple(places[hit.record.content] for hit in hits),
        answer=case.answer,
        outdated=case.outdated,
    )


async def play_scripted(config: MemoryConfig, folder: Path) -> list[CaseResult]:
    """Every case, each with the scripted embeddings of its own."""
    return [await play_case(case, ScriptedEmbedder(case_vectors(case)), config, folder) for case in CASES]


async def play_configured(app: AppConfig, folder: Path) -> list[CaseResult]:
    """Every case, embedded by the model ``[memory]`` names (a server of this Mac or the provider's, as configured)."""
    embedder: Embedder = await build_embedder(app)
    try:
        return [await play_case(case, embedder, app.memory, folder) for case in CASES]
    finally:
        await embedder.aclose()


@contextmanager
def scratch_folder() -> Iterator[Path]:
    """A folder for the benchmark's stores, removed afterwards, so that nothing of the owner's notes is touched."""
    folder: Path = Path(tempfile.mkdtemp(prefix="dot-memory-bench-"))
    try:
        yield folder
    finally:
        shutil.rmtree(folder)


async def play_scripted_here(config: MemoryConfig) -> list[CaseResult]:
    """The scripted benchmark in a scratch folder."""
    with scratch_folder() as folder:
        return await play_scripted(config, folder)


async def play_configured_here(app: AppConfig) -> list[CaseResult]:
    """The benchmark with the configured embedding model, in a scratch folder."""
    with scratch_folder() as folder:
        return await play_configured(app, folder)


class Tally(FrozenModel):
    """A group of cases and how many of them the search got right."""

    name: str
    cases: int
    first: int
    found: int
    stale: int


def tally(name: str, results: Sequence[CaseResult]) -> Tally:
    return Tally(
        name=name,
        cases=len(results),
        first=sum(1 for result in results if result.first),
        found=sum(1 for result in results if result.found),
        stale=sum(1 for result in results if result.stale),
    )


def tallies(results: Sequence[CaseResult]) -> list[Tally]:
    """The groups the report shows: every fact that changed, those saved with a subject and those without, those whose
    old note a run was handed (a search lifts it), and the facts that did not change; and apart, the E cases, whose old
    note a run handed it saved again (item 1866)."""
    updates: list[CaseResult] = [result for result in results if result.kind == "update" and not result.echoed]
    return [
        tally("facts that changed", updates),
        tally("  saved with a subject", [result for result in updates if result.keyed]),
        tally("  saved without one", [result for result in updates if not result.keyed]),
        tally("  old note handed to a run", [result for result in updates if result.used]),
        tally("facts that did not change", [result for result in results if result.kind == "addition"]),
        tally("old note saved again by a run", [result for result in results if result.echoed]),
    ]


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole}" if whole else "-"


def render_results(label: str, results: Sequence[CaseResult]) -> list[str]:
    """The report: per group the cases, how often the newest fact (the right one, for a fact that did not change) came
    first, how often it was among the three notes, and how often an outdated note was among them too."""
    lines: list[str] = [
        f"Memory benchmark, {len(results)} cases, {label} embeddings, the {SHOWN} best notes of each search:",
        f"  {'':30}{'cases':>6}{'first':>8}{'in top ' + str(SHOWN):>10}{'outdated shown':>16}",
    ]
    for group in tallies(results):
        shown_stale: str = _share(group.stale, group.cases) if group.name != "facts that did not change" else "-"
        lines.append(
            f"  {group.name:30}{group.cases:>6}{_share(group.first, group.cases):>8}"
            f"{_share(group.found, group.cases):>10}{shown_stale:>16}"
        )
    missed: list[str] = [result.case_id for result in results if not result.first]
    lines.append(f"  not first: {', '.join(missed) if missed else 'none'}")
    return lines
