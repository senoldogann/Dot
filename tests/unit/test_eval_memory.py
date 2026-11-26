"""The memory benchmark (item 1728) offline: its scripted embeddings have the cosines its cases give, and the numbers it
reports for a search over notes that were replaced are the ones the docs and the roadmap record."""

import math
from pathlib import Path

import pytest

from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import InvalidValueError
from dotagent.evals.memory_bench import (
    SHOWN,
    CaseResult,
    Tally,
    case_vectors,
    cholesky_vectors,
    play_scripted,
    render_results,
    tallies,
)
from dotagent.evals.memory_cases import CASES, MemoryCase
from dotagent.memory.service import normalize_text


def _cosine(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    return math.sumprod(first, second)


def test_the_scripted_vectors_have_the_cosines_the_cases_give() -> None:
    for case in CASES:
        vectors = case_vectors(case)
        texts: list[str] = [normalize_text(fact.text) for fact in case.facts]
        pairs = [(i, j) for i in range(len(texts)) for j in range(i + 1, len(texts))]
        for (i, j), cosine in zip(pairs, case.pair_cosines, strict=True):
            assert _cosine(vectors[texts[i]], vectors[texts[j]]) == pytest.approx(cosine, abs=1e-9), case.id
        question: tuple[float, ...] = vectors[normalize_text(case.question)]
        for text, fact in zip(texts, case.facts, strict=True):
            assert _cosine(vectors[text], question) == pytest.approx(fact.question_cosine, abs=1e-9), case.id


def test_the_cases_are_forty_six_and_name_their_answers() -> None:
    assert len(CASES) == 46
    assert len({case.id for case in CASES}) == 46
    for case in CASES:
        assert 0 <= case.answer < len(case.facts), case.id
        assert all(place < case.answer for place in case.outdated), case.id
        assert (case.kind == "update") == bool(case.outdated), case.id
        assert len({fact.text for fact in case.facts}) == len(case.facts), case.id
        assert all(place in case.outdated for place in case.echoed), case.id


def test_cosines_no_vectors_have_are_refused_with_the_case_named() -> None:
    impossible: list[list[float]] = [[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]]
    with pytest.raises(InvalidValueError, match="no vectors have these cosines") as raised:
        cholesky_vectors(impossible, "X1")
    assert raised.value.context["case"] == "X1"


def test_a_case_with_a_cosine_missing_for_a_pair_is_refused() -> None:
    case: MemoryCase = CASES[0].model_copy(update={"pair_cosines": ()})
    with pytest.raises(InvalidValueError, match="one cosine for each pair"):
        case_vectors(case)


async def test_a_search_over_replaced_notes_shows_the_newest_first_and_the_outdated_only_where_no_subject_said_so(
    tmp_path: Path,
) -> None:
    """Item 1729: before it, the newest fact was first in 2 of 34 cases and the outdated one was shown too in all 34
    (item 1728's roadmap record). Now a subject replaces the older note in every case that gives one, and a swapped
    number or a word of change replaces it with none; the 10 cases of a note reworded, of a name swapped or of a
    version after a word that names a thing wait for a subject or the night's model."""
    config: AppConfig = load_default_config()
    results: list[CaseResult] = await play_scripted(config.memory, tmp_path)
    groups: dict[str, Tally] = {group.name.strip(): group for group in tallies(results)}
    changed: Tally = groups["facts that changed"]
    assert (changed.cases, changed.first, changed.found, changed.stale) == (34, 25, 34, 10)
    keyed: Tally = groups["saved with a subject"]
    assert (keyed.cases, keyed.first, keyed.found, keyed.stale) == (18, 18, 18, 0)
    unkeyed: Tally = groups["saved without one"]
    assert (unkeyed.cases, unkeyed.first, unkeyed.found, unkeyed.stale) == (16, 7, 16, 10)
    used: Tally = groups["old note handed to a run"]
    assert (used.cases, used.first, used.stale) == (4, 3, 1)
    kept: Tally = groups["facts that did not change"]
    assert (kept.cases, kept.first, kept.found) == (8, 8, 8)
    # Item 1866: before it, a run handed the old note that saved its words again after the newer note woke the old one
    # and, with a subject, put the newer aside: newest first 0 of 4, among the three 2 of 4, the old shown in 4.
    echoed: Tally = groups["old note saved again by a run"]
    assert (echoed.cases, echoed.first, echoed.found, echoed.stale) == (4, 4, 4, 0)
    lines: list[str] = render_results("scripted", results)
    assert f"the {SHOWN} best notes" in lines[0]
    assert "facts that changed" in lines[2]
