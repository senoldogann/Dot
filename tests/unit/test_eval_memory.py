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


def test_the_cases_are_forty_two_and_name_their_answers() -> None:
    assert len(CASES) == 42
    assert len({case.id for case in CASES}) == 42
    for case in CASES:
        assert 0 <= case.answer < len(case.facts), case.id
        assert all(place < case.answer for place in case.outdated), case.id
        assert (case.kind == "update") == bool(case.outdated), case.id
        assert len({fact.text for fact in case.facts}) == len(case.facts), case.id


def test_cosines_no_vectors_have_are_refused_with_the_case_named() -> None:
    impossible: list[list[float]] = [[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]]
    with pytest.raises(InvalidValueError, match="no vectors have these cosines") as raised:
        cholesky_vectors(impossible, "X1")
    assert raised.value.context["case"] == "X1"


def test_a_case_with_a_cosine_missing_for_a_pair_is_refused() -> None:
    case: MemoryCase = CASES[0].model_copy(update={"pair_cosines": ()})
    with pytest.raises(InvalidValueError, match="one cosine for each pair"):
        case_vectors(case)


async def test_a_search_over_replaced_notes_today_shows_the_outdated_one_and_often_first(tmp_path: Path) -> None:
    config: AppConfig = load_default_config()
    results: list[CaseResult] = await play_scripted(config.memory, tmp_path)
    groups: dict[str, Tally] = {group.name.strip(): group for group in tallies(results)}
    changed: Tally = groups["facts that changed"]
    assert (changed.cases, changed.first, changed.found, changed.stale) == (34, 2, 34, 34)
    kept: Tally = groups["facts that did not change"]
    assert (kept.cases, kept.first, kept.found) == (8, 8, 8)
    lines: list[str] = render_results("scripted", results)
    assert f"the {SHOWN} best notes" in lines[0]
    assert "facts that changed" in lines[2]
