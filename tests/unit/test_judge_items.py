"""The pure parts of judge_items: the batches a list goes in, the state and the questions of one request, the ranking
and its rendering, the arguments it refuses, and how TypeSafe's score answers and its failures are read."""

import pytest
from pydantic import ValidationError

from dotagent.agent.judge import request_error
from dotagent.core.errors import ProviderAuthError, ProviderHttpError, ProviderProtocolError
from dotagent.core.types import JsonObject
from dotagent.llm.typesafe import ScoreAnswer, ScoreQuestion, _scores
from dotagent.tools.judge_items import (
    Item,
    Judged,
    JudgeItemsArgs,
    batches,
    judged_scores,
    noul_questions,
    ranked,
    render,
    score_questions,
    state_of,
)

QUESTION: str = "Is this post about Python packaging?"
LEVELS: tuple[str, ...] = ("unrelated", "mentions packaging in passing", "about Python packaging")
ITEMS: tuple[Item, ...] = (
    Item(id="p1", text="uv 0.5 ships a lockfile format that replaces requirements.txt"),
    Item(id="p2", text="My cat learned to open the fridge"),
    Item(id="p-3", text="Why wheels beat sdists: a packaging story"),
)


def test_a_list_goes_in_batches_of_the_request_size_in_its_order() -> None:
    assert batches(ITEMS, 2) == ((ITEMS[0], ITEMS[1]), (ITEMS[2],))
    assert batches(ITEMS, 3) == (ITEMS,)
    assert batches((), 3) == ()


def test_one_request_holds_the_items_by_id_and_one_question_per_item_naming_its_path() -> None:
    assert state_of(ITEMS) == {
        "candidates": {
            "p1": "uv 0.5 ships a lockfile format that replaces requirements.txt",
            "p2": "My cat learned to open the fridge",
            "p-3": "Why wheels beat sdists: a packaging story",
        }
    }
    nouls = noul_questions(QUESTION, ITEMS)
    assert [question.id for question in nouls] == ["p1", "p2", "p-3"]
    assert nouls[2].instructions == f"About the candidate at `candidates.p-3`: {QUESTION}"
    scores = score_questions(QUESTION, ITEMS[:1], LEVELS)
    assert scores == (ScoreQuestion(id="p1", instructions=nouls[0].instructions, levels=LEVELS),)


def test_the_ranking_puts_the_highest_value_first_and_keeps_the_order_of_equals() -> None:
    judged: tuple[Judged, ...] = (
        Judged(id="a", value=0.2, level=None),
        Judged(id="b", value=0.9, level=None),
        Judged(id="c", value=0.2, level=None),
    )
    assert [item.id for item in ranked(judged)] == ["b", "a", "c"]


def test_score_answers_become_the_expected_level_and_the_likeliest_level() -> None:
    answers: dict[str, ScoreAnswer] = {
        "p1": ScoreAnswer(score=1.7, probabilities=(0.1, 0.1, 0.8), confidence=0.8),
        "p2": ScoreAnswer(score=0.3, probabilities=(0.7, 0.3, 0.0), confidence=0.7),
    }
    assert judged_scores(answers, LEVELS) == (
        Judged(id="p1", value=1.7, level="about Python packaging"),
        Judged(id="p2", value=0.3, level="unrelated"),
    )


def test_the_rendering_names_each_item_with_its_value_and_the_start_of_its_text() -> None:
    texts: dict[str, str] = {item.id: item.text for item in ITEMS}
    nouls: str = render(
        QUESTION,
        (Judged(id="p1", value=0.93, level=None), Judged(id="p2", value=0.04, level=None)),
        texts,
        (),
        1,
    )
    assert nouls == (
        f"2 items judged on “{QUESTION}” in 1 request; the probability of yes, the likeliest yes first:\n"
        "1. p1 0.93 — uv 0.5 ships a lockfile format that replaces requirements.t…\n"
        "2. p2 0.04 — My cat learned to open the fridge"
    )
    scores: str = render(QUESTION, (Judged(id="p-3", value=1.84, level="about Python packaging"),), texts, LEVELS, 2)
    assert scores == (
        f"1 items rated on “{QUESTION}” in 2 requests; the expected level (where 0 = unrelated, 1 = mentions "
        "packaging in passing, 2 = about Python packaging) and the likeliest level, the highest first:\n"
        "1. p-3 1.84 (about Python packaging) — Why wheels beat sdists: a packaging story"
    )


def test_the_arguments_refuse_one_level_repeated_ids_and_ids_with_other_characters() -> None:
    items: list[JsonObject] = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]
    JudgeItemsArgs.model_validate({"question": QUESTION, "items": items})
    JudgeItemsArgs.model_validate({"question": QUESTION, "items": items, "levels": ["low", "high"]})
    with pytest.raises(ValidationError, match="at least 2"):
        JudgeItemsArgs.model_validate({"question": QUESTION, "items": items, "levels": ["only"]})
    with pytest.raises(ValidationError, match="ids repeat: a"):
        JudgeItemsArgs.model_validate({"question": QUESTION, "items": [*items, {"id": "a", "text": "z"}]})
    with pytest.raises(ValidationError, match="pattern"):
        JudgeItemsArgs.model_validate({"question": QUESTION, "items": [{"id": "a.b", "text": "x"}]})


def test_a_score_answer_is_read_as_the_reference_documents_it_and_anything_else_is_a_protocol_error() -> None:
    question: ScoreQuestion = ScoreQuestion(id="q", instructions="How formal?", levels=("casual", "formal"))
    reply: JsonObject = {
        "answers": {
            "q": {
                "type": "score",
                "score": 0.6,
                "confidence": 0.2,
                "legend": {"0": "casual", "1": "formal"},
                "probabilities": {"0": 0.4, "1": 0.6},
            }
        }
    }
    assert _scores(reply, (question,), "u") == {"q": ScoreAnswer(score=0.6, probabilities=(0.4, 0.6), confidence=0.2)}
    short: JsonObject = {
        "answers": {"q": {"type": "score", "score": 0.6, "confidence": 0.2, "probabilities": {"0": 1}}}
    }
    with pytest.raises(ProviderProtocolError, match="no score"):
        _scores(short, (question,), "u")
    with pytest.raises(ProviderProtocolError, match="no score"):
        _scores({"answers": {"q": {"type": "score", "score": 1.5}}}, (question,), "u")


def test_a_rejected_request_and_a_malformed_answer_are_request_errors_and_an_outage_is_not() -> None:
    assert request_error(ProviderHttpError("provider answered HTTP 422", 422, {}))
    assert request_error(ProviderProtocolError("no score", {}))
    assert not request_error(ProviderHttpError("provider answered HTTP 500", 500, {}))
    assert not request_error(ProviderAuthError("provider answered HTTP 401", 401, {}))
    assert not request_error(TimeoutError())
