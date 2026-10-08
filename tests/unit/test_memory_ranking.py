"""The note ranking helpers, tag cleaning and the embedding answer parser on fixed data."""

import math

import pytest

from dotagent.core.errors import ProviderProtocolError, StoreError
from dotagent.memory.embedder import parse_embeddings, parse_openai_embeddings
from dotagent.memory.ranking import Similar, most_similar, reciprocal_rank_fusion, unit_vector
from dotagent.memory.service import normalize_tags, normalize_text


def test_unit_vector_has_length_one() -> None:
    assert unit_vector([3.0, 4.0], {}) == pytest.approx((0.6, 0.8))
    assert math.sumprod(unit_vector([1.0, 2.0, 2.0], {}), unit_vector([1.0, 2.0, 2.0], {})) == pytest.approx(1.0)


@pytest.mark.parametrize("values", [[0.0, 0.0], [1.0, float("nan")], [float("inf"), 1.0]])
def test_unit_vector_refuses_zero_length_and_non_finite_numbers(values: list[float]) -> None:
    with pytest.raises(ProviderProtocolError) as caught:
        unit_vector(values, {"model": "m"})
    assert caught.value.context == {"model": "m", "dimension": 2}


def test_most_similar_orders_by_cosine_and_applies_the_floor_and_the_limit() -> None:
    stored: list[tuple[str, tuple[float, ...]]] = [
        ("far", (0.0, 1.0)),
        ("same", (1.0, 0.0)),
        ("near", (0.8, 0.6)),
        ("middle", (0.6, 0.8)),
    ]
    query: tuple[float, ...] = (1.0, 0.0)
    assert [item.id for item in most_similar(query, stored, 0.5, 10)] == ["same", "near", "middle"]
    assert [item.id for item in most_similar(query, stored, 0.5, 2)] == ["same", "near"]
    assert most_similar(query, stored, 0.99, 10) == [Similar(id="same", similarity=1.0)]
    assert most_similar(query, [], 0.0, 10) == []


def test_most_similar_refuses_a_vector_of_another_model() -> None:
    with pytest.raises(StoreError) as caught:
        most_similar((1.0, 0.0), [("mem_old", (1.0, 0.0, 0.0))], 0.0, 5)
    assert caught.value.context == {"memory_id": "mem_old", "stored_dimension": 3, "query_dimension": 2}


def test_fusion_rewards_agreement_between_rankings() -> None:
    assert reciprocal_rank_fusion([["a", "b"], ["c", "b"]], 10) == ["b", "a", "c"]
    assert reciprocal_rank_fusion([["a", "b"], ["c", "b"]], 2) == ["b", "a"]
    assert reciprocal_rank_fusion([["a", "b", "c"]], 10) == ["a", "b", "c"]
    assert reciprocal_rank_fusion([], 10) == []
    assert reciprocal_rank_fusion([[], []], 10) == []


def test_text_is_one_line() -> None:
    assert normalize_text("  The user\n\n likes\ttea.  ") == "The user likes tea."


def test_tags_are_short_lowercase_words_without_duplicates() -> None:
    cleaned: tuple[str, ...] = normalize_tags([" Core ", "kişisel tercih", "core", "a,b", "", "---", "İstanbul"])
    assert cleaned == ("core", "kişisel-tercih", "a-b", "istanbul")
    assert all("," not in tag for tag in cleaned)


def test_tags_are_limited_in_number_and_length() -> None:
    many: tuple[str, ...] = normalize_tags([f"tag{number}" for number in range(10)])
    assert many == tuple(f"tag{number}" for number in range(6))
    (long_tag,) = normalize_tags(["x" * 100])
    assert len(long_tag) == 32


def test_embedding_answer_becomes_unit_vectors_one_per_text() -> None:
    vectors = parse_embeddings({"embeddings": [[3, 4], [0, 2.0]]}, 2, {"url": "http://ollama.test"})
    assert vectors == [pytest.approx((0.6, 0.8)), pytest.approx((0.0, 1.0))]


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"embeddings": []},
        {"embeddings": [[1.0, 0.0]]},
        {"embeddings": "no"},
        {"embeddings": [["a", "b"], [1.0, 0.0]]},
        {"embeddings": [[True, 1.0], [1.0, 0.0]]},
        {"embeddings": [[1.0, 0.0], [1.0, 0.0, 0.0]]},
        {"embeddings": [[0.0, 0.0], [1.0, 0.0]]},
    ],
)
def test_malformed_embedding_answers_are_refused_with_their_context(answer: dict[str, object]) -> None:
    with pytest.raises(ProviderProtocolError) as caught:
        parse_embeddings(answer, 2, {"url": "http://ollama.test"})  # type: ignore[arg-type]
    assert caught.value.context["url"] == "http://ollama.test"


def test_openai_embedding_answer_is_put_in_the_order_of_the_texts() -> None:
    answer: dict[str, object] = {
        "object": "list",
        "data": [{"object": "embedding", "index": 1, "embedding": [0, 2.0]}, {"index": 0, "embedding": [3, 4]}],
        "model": "text-embedding-3-small",
    }
    vectors = parse_openai_embeddings(answer, 2, {"url": "https://openai.test"})  # type: ignore[arg-type]
    assert vectors == [pytest.approx((0.6, 0.8)), pytest.approx((0.0, 1.0))]


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"data": []},
        {"data": "no"},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 0, "embedding": [0.0, 1.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 2, "embedding": [0.0, 1.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}]},
        {"data": [{"index": True, "embedding": [1.0, 0.0]}, {"index": 1, "embedding": [0.0, 1.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}, "no"]},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 1}]},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 1, "embedding": [1.0, 0.0, 0.0]}]},
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 1, "embedding": [0.0, 0.0]}]},
    ],
)
def test_malformed_openai_embedding_answers_are_refused_with_their_context(answer: dict[str, object]) -> None:
    with pytest.raises(ProviderProtocolError) as caught:
        parse_openai_embeddings(answer, 2, {"url": "https://openai.test"})  # type: ignore[arg-type]
    assert caught.value.context["url"] == "https://openai.test"
