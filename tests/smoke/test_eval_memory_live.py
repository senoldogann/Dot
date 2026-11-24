"""The memory benchmark (item 1728) with the real ``bge-m3`` of the local Ollama server: the cosines its scripted
embeddings were made from are still what the model measures on those texts, and a search over the facts that did not
change still finds them. Marked ``live`` and skipped where the server or the model is not there."""

import math
from pathlib import Path

import httpx
import pytest

from dotagent.config import AppConfig, load_default_config
from dotagent.evals.memory_bench import CaseResult, Tally, play_configured, tallies
from dotagent.evals.memory_cases import CASES
from dotagent.memory.embedder import Embedder, build_embedder
from dotagent.memory.ranking import Vector

pytestmark = pytest.mark.live

# How far a cosine the model measures today may lie from the one the case was made from (rounded to 3 places then).
_DRIFT: float = 0.02


def _model_is_ready() -> bool:
    config: AppConfig = load_default_config()
    try:
        answer: httpx.Response = httpx.get(f"{config.providers.ollama.base_url}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200 and any(
        model["name"].startswith(config.memory.embed_model) for model in answer.json()["models"]
    )


requires_model = pytest.mark.skipif(not _model_is_ready(), reason="needs the local Ollama server with bge-m3")


@requires_model
async def test_the_scripted_cosines_are_what_bge_m3_measures_on_the_same_texts() -> None:
    config: AppConfig = load_default_config()
    embedder: Embedder = await build_embedder(config)
    try:
        for case in CASES:
            vectors: list[Vector] = await embedder.embed([fact.text for fact in case.facts] + [case.question])
            pairs: list[tuple[int, int]] = [
                (i, j) for i in range(len(case.facts)) for j in range(i + 1, len(case.facts))
            ]
            for (i, j), cosine in zip(pairs, case.pair_cosines, strict=True):
                assert math.sumprod(vectors[i], vectors[j]) == pytest.approx(cosine, abs=_DRIFT), case.id
            for i, fact in enumerate(case.facts):
                assert math.sumprod(vectors[i], vectors[-1]) == pytest.approx(fact.question_cosine, abs=_DRIFT), case.id
    finally:
        await embedder.aclose()


@requires_model
async def test_a_search_with_bge_m3_over_the_benchmark_replaces_what_a_subject_or_a_swapped_value_says(
    tmp_path: Path,
) -> None:
    """Item 1729: with the real model the numbers are those of the scripted run: every case that gives a subject finds
    the newest fact first with no outdated note beside it, nothing that is still true is put aside, and no more than
    the 10 cases a note worded apart or a name swapped leave open show an outdated note."""
    config: AppConfig = load_default_config()
    results: list[CaseResult] = await play_configured(config, tmp_path)
    groups: dict[str, Tally] = {group.name.strip(): group for group in tallies(results)}
    keyed: Tally = groups["saved with a subject"]
    assert (keyed.cases, keyed.first, keyed.stale) == (18, 18, 0)
    changed: Tally = groups["facts that changed"]
    assert (changed.found, changed.stale) == (34, 10)
    kept: Tally = groups["facts that did not change"]
    assert (kept.cases, kept.first, kept.found) == (8, 8, 8)
