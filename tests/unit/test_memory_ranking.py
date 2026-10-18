"""The note ranking helpers, tag cleaning and the embedding answer parser on fixed data."""

import math

import pytest

from dotagent.config import MemoryConfig, load_default_config
from dotagent.core.errors import ProviderProtocolError, StoreError
from dotagent.memory.embedder import parse_embeddings, parse_openai_embeddings
from dotagent.memory.ranking import (
    Similar,
    balance_lift,
    fused_scores,
    most_similar,
    ranked_by_use,
    reciprocal_rank_fusion,
    unit_vector,
    use_lift,
)
from dotagent.memory.render import render_note
from dotagent.memory.service import (
    expired,
    fit_notes,
    memory_budget,
    normalize_tags,
    normalize_text,
    note_tokens,
    resting,
)
from dotagent.store.models import MemoryRecord, MemoryScore

DAY_S: float = 86400.0
NOW: float = 1_000_000_000.0


def _note(
    identifier: str,
    source_run: str | None,
    created_at: float,
    used: int,
    last_used_at: float | None,
    tags: tuple[str, ...],
) -> MemoryRecord:
    return MemoryRecord(
        id=identifier,
        content=f"Note {identifier} says something of twenty characters.",
        tags=tags,
        created_at=created_at,
        updated_at=created_at,
        source_run=source_run,
        agent_id=None,
        used_count=used,
        last_used_at=last_used_at,
        expires_at=None,
        replaced_by=None,
        tainted=False,
    )


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


def test_fused_scores_add_up_over_the_rankings_in_order_of_first_appearance() -> None:
    assert fused_scores([["a", "b"], ["c", "b"]]) == {"a": 1 / 61, "b": 1 / 62 + 1 / 62, "c": 1 / 61}
    assert fused_scores([]) == {}


def test_the_lift_of_a_use_is_one_now_halves_every_half_life_and_is_nothing_for_a_note_never_used() -> None:
    assert use_lift(None, NOW, 30 * DAY_S) == 0.0
    assert use_lift(NOW, NOW, 30 * DAY_S) == 1.0
    assert use_lift(NOW + DAY_S, NOW, 30 * DAY_S) == 1.0
    assert use_lift(NOW - 30 * DAY_S, NOW, 30 * DAY_S) == pytest.approx(0.5)
    assert use_lift(NOW - 60 * DAY_S, NOW, 30 * DAY_S) == pytest.approx(0.25)


def test_a_used_agent_note_overtakes_an_unused_one_of_the_same_score_but_not_two_rankers_agreeing() -> None:
    scores: dict[str, float] = fused_scores([["unused", "used", "agreed"], ["agreed", "yours"]])
    unused: MemoryRecord = _note("unused", "run_1", NOW - DAY_S, 0, None, ())
    used: MemoryRecord = _note("used", "run_1", NOW - DAY_S, 3, NOW - 3600.0, ())
    agreed: MemoryRecord = _note("agreed", "run_1", NOW - DAY_S, 0, None, ())
    yours: MemoryRecord = _note("yours", None, NOW - DAY_S, 5, NOW, ())
    ranked: list[str] = [
        note.id for note in ranked_by_use(scores, [unused, used, agreed, yours], NOW, 30 * DAY_S, {}, 3, 10)
    ]
    # agreed: third in one list and first in the other; used: second in one list plus a lift worth a second place.
    assert ranked == ["agreed", "used", "unused", "yours"]
    # The user's note stands on its fused score alone: used today, it is not lifted over an agent note it tied with.
    tied: dict[str, float] = fused_scores([["yours", "used"], ["used", "yours"]])
    assert [note.id for note in ranked_by_use(tied, [yours, used], NOW, 30 * DAY_S, {}, 3, 10)] == ["used", "yours"]
    assert [note.id for note in ranked_by_use(scores, [unused, used, agreed, yours], NOW, 30 * DAY_S, {}, 3, 2)] == [
        "agreed",
        "used",
    ]


def test_an_agent_note_no_run_was_handed_for_rest_days_rests_and_the_users_and_core_notes_never_do() -> None:
    assert resting(_note("old", "run_1", NOW - 91 * DAY_S, 0, None, ()), NOW, 90, None, 3)
    assert not resting(_note("young", "run_1", NOW - 89 * DAY_S, 0, None, ()), NOW, 90, None, 3)
    # Item 306: a note handed long ago goes stale like one never handed; one handed lately does not.
    assert resting(_note("used long ago", "run_1", NOW - 400 * DAY_S, 1, NOW - 200 * DAY_S, ()), NOW, 90, None, 3)
    assert not resting(_note("used", "run_1", NOW - 400 * DAY_S, 1, NOW - 10 * DAY_S, ()), NOW, 90, None, 3)
    assert resting(_note("retired", "run_1", NOW - DAY_S, 7, NOW, ("lesson", "rest")), NOW, 90, None, 3)
    assert not resting(_note("yours", None, NOW - 400 * DAY_S, 0, None, ()), NOW, 90, None, 3)
    assert not resting(_note("standing", "run_1", NOW - 400 * DAY_S, 0, None, ("core",)), NOW, 90, None, 3)


def test_a_note_whose_time_ran_out_rests_whoever_wrote_it_and_says_until_when_it_holds() -> None:
    lasting: MemoryRecord = _note("trip", "run_1", NOW - DAY_S, 0, None, ())
    for_a_while: MemoryRecord = lasting.model_copy(update={"expires_at": NOW + 2 * DAY_S})
    ran_out: MemoryRecord = lasting.model_copy(update={"expires_at": NOW - 1.0})
    assert not expired(lasting, NOW) and not expired(for_a_while, NOW) and expired(ran_out, NOW)
    assert not resting(for_a_while, NOW, 90, None, 3)
    assert resting(ran_out, NOW, 90, None, 3)
    # The user's note and a standing one rest too once their time ran out.
    assert resting(ran_out.model_copy(update={"source_run": None}), NOW, 90, None, 3)
    assert resting(ran_out.model_copy(update={"tags": ("core",)}), NOW, 90, None, 3)
    assert ", holds until " in render_note(for_a_while) and ", holds until " not in render_note(lasting)


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


def test_the_balance_lift_counts_from_min_uses_and_is_capped_either_way() -> None:
    assert balance_lift(None, 3) == 0.0
    assert balance_lift(MemoryScore(handed=2, done=2, failed=0), 3) == 0.0
    assert balance_lift(MemoryScore(handed=3, done=3, failed=0), 3) == 1.0
    assert balance_lift(MemoryScore(handed=4, done=1, failed=3), 3) == pytest.approx(-2 / 3)
    assert balance_lift(MemoryScore(handed=9, done=0, failed=9), 3) == -1.0
    helped: MemoryRecord = _note("helped", "run_1", NOW - 40 * DAY_S, 3, NOW - 10 * DAY_S, ())
    hurt: MemoryRecord = _note("hurt", "run_1", NOW - 40 * DAY_S, 3, NOW - DAY_S, ())
    balances: dict[str, MemoryScore] = {
        "helped": MemoryScore(handed=3, done=3, failed=0),
        "hurt": MemoryScore(handed=3, done=0, failed=3),
    }
    # The fresher use would put hurt first; its score puts helped first.
    assert [note.id for note in ranked_by_use({}, [hurt, helped], NOW, 30 * DAY_S, balances, 3, 10)] == [
        "helped",
        "hurt",
    ]


def test_a_note_rests_once_its_score_is_negative_over_enough_runs() -> None:
    fresh: MemoryRecord = _note("fresh", "run_1", NOW - DAY_S, 4, NOW, ())
    assert resting(fresh, NOW, 90, MemoryScore(handed=3, done=1, failed=2), 3)
    assert not resting(fresh, NOW, 90, MemoryScore(handed=2, done=0, failed=2), 3)
    assert not resting(fresh, NOW, 90, MemoryScore(handed=3, done=2, failed=1), 3)
    assert not resting(
        _note("yours", None, NOW - DAY_S, 4, NOW, ()), NOW, 90, MemoryScore(handed=3, done=0, failed=3), 3
    )


def test_the_budget_is_a_share_of_the_window_capped_and_the_cap_alone_without_a_window() -> None:
    config: MemoryConfig = load_default_config().memory
    assert config.inject_tokens == 1500 and config.inject_share == 0.02
    assert memory_budget(config, None) == 1500
    assert memory_budget(config, 32_000) == 640
    assert memory_budget(config, 200_000) == 1500


def test_notes_are_taken_in_order_while_they_fit_and_a_shorter_one_after_a_long_one_still_fits() -> None:
    short: MemoryRecord = _note("short", "run_1", NOW, 0, None, ())
    long_note: MemoryRecord = _note("long", "run_1", NOW, 0, None, ()).model_copy(update={"content": "x" * 640})
    second: MemoryRecord = _note("second", "run_1", NOW, 0, None, ("lesson",))
    assert note_tokens(short) == round(len(short.content) / 3.2) + 12
    assert note_tokens(second) == round((len(second.content) + len(" #lesson")) / 3.2) + 12
    budget: int = note_tokens(short) + note_tokens(second) + 5
    taken, spent = fit_notes([short, long_note, second], budget, 0)
    assert [note.id for note in taken] == ["short", "second"]
    assert spent == note_tokens(short) + note_tokens(second)
    assert fit_notes([short], budget, budget) == ([], budget)
