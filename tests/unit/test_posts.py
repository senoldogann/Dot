"""The pure parts of the post tools of a campaign: the weights read from the ledger, the batches and questions of a
ranking, the composite score and its rendering, the choice over drafts, and the checks of a draft."""

import pytest
from pydantic import ValidationError

from dotagent.core.errors import ProviderProtocolError
from dotagent.core.types import JsonObject
from dotagent.llm.typesafe import ChoiceAnswer, ChoiceQuestion, NoulQuestion, _choices
from dotagent.store.models import CampaignEntry
from dotagent.tools.posts import (
    CheckDraftArgs,
    Draft,
    PickDraftArgs,
    Post,
    RankPostsArgs,
    Weights,
    check_questions,
    composite,
    draft_checks,
    draft_choice,
    equal_weights,
    post_batches,
    post_questions,
    posts_state,
    ranked_posts,
    ranking_event,
    render_checks,
    render_pick,
    render_ranking,
    weights_of,
)

TOPIC: str = "Python packaging"
POSTS: tuple[Post, ...] = (
    Post(id="a", author="@mara", text="Why does uv lock differ from pip freeze? Lost an afternoon to this."),
    Post(id="b", author="@dealsbot", text="GIVEAWAY: retweet to win a laptop!"),
    Post(id="c", author="@tomas", text="Shipped our first wheel with cibuildwheel today, feels good."),
)


def _entry(numbers: dict[str, float], ts: float) -> CampaignEntry:
    return CampaignEntry(
        id=f"e{ts:.0f}", campaign_id="cmp", run_id="run", ts=ts, kind="note", text="weights", numbers=numbers
    )


def test_the_weights_come_from_the_newest_entry_that_holds_all_three_numbers() -> None:
    assert weights_of([]) is None
    assert weights_of([_entry({"w_topic": 1, "w_person": 1}, 1.0)]) is None
    entries: list[CampaignEntry] = [
        _entry({"w_topic": 1.0, "w_person": 1.0, "w_reply": 1.0}, 1.0),
        _entry({"w_topic": 2.0, "w_person": 0.5, "w_reply": 1.0}, 2.0),
        _entry({"leads": 12.0}, 3.0),
    ]
    assert weights_of(entries) == Weights(topic=2.0, person=0.5, reply=1.0)
    assert equal_weights() == Weights(topic=1.0, person=1.0, reply=1.0)


def test_a_ranking_asks_three_questions_per_post_and_fits_them_in_the_request_size() -> None:
    assert post_batches(POSTS, 6) == ((POSTS[0], POSTS[1]), (POSTS[2],))
    assert post_batches(POSTS, 2) == ((POSTS[0],), (POSTS[1],), (POSTS[2],))
    assert posts_state(TOPIC, POSTS[:1]) == {
        "topic": TOPIC,
        "posts": {"a": {"author": "@mara", "text": POSTS[0].text}},
    }
    questions: tuple[NoulQuestion, ...] = post_questions(POSTS[:2])
    assert [question.id for question in questions] == [
        "a__w_topic",
        "a__w_person",
        "a__w_reply",
        "b__w_topic",
        "b__w_person",
        "b__w_reply",
    ]
    assert questions[3].instructions.startswith("Is the post at `posts.b` about the topic in `topic`")


def test_the_composite_is_the_weighted_mean_and_the_ranking_puts_the_highest_first() -> None:
    answers: dict[str, float] = {"w_topic": 1.0, "w_person": 0.5, "w_reply": 0.0}
    assert composite(answers, Weights(topic=1.0, person=1.0, reply=1.0)) == pytest.approx(0.5)
    assert composite(answers, Weights(topic=2.0, person=0.0, reply=2.0)) == pytest.approx(0.5)
    assert composite(answers, Weights(topic=0.0, person=0.0, reply=0.0)) == pytest.approx(0.5)
    batch: dict[str, float] = {
        "a__w_topic": 0.9,
        "a__w_person": 0.9,
        "a__w_reply": 0.9,
        "b__w_topic": 0.1,
        "b__w_person": 0.05,
        "b__w_reply": 0.1,
        "c__w_topic": 0.9,
        "c__w_person": 0.9,
        "c__w_reply": 0.3,
    }
    ranked = ranked_posts(batch, POSTS, equal_weights())
    assert [(item.id, round(item.score, 2)) for item in ranked] == [("a", 0.9), ("c", 0.7), ("b", 0.08)]
    text: str = render_ranking(ranked, POSTS, equal_weights(), 1)
    assert text.startswith(
        "3 posts ranked in 1 request by the weighted probability of yes to three questions (on the topic ×1, by a "
        "person ×1, worth a reply ×1; the weights are in the campaign's ledger), the highest first:\n"
        "1. a 0.90 (topic 0.90, person 0.90, reply 0.90) @mara: Why does uv lock differ from pip freeze? Lost an "
        "afternoon …\n"
        "2. c 0.70 (topic 0.90, person 0.90, reply 0.30) @tomas: Shipped our first wheel"
    )
    event: JsonObject = ranking_event(TOPIC, equal_weights(), ranked)
    assert event["weights"] == {"w_topic": 1.0, "w_person": 1.0, "w_reply": 1.0}
    assert event["posts"] == [
        {"id": "a", "score": 0.9, "w_topic": 0.9, "w_person": 0.9, "w_reply": 0.9},
        {"id": "c", "score": 0.7, "w_topic": 0.9, "w_person": 0.9, "w_reply": 0.3},
        {"id": "b", "score": 0.0833, "w_topic": 0.1, "w_person": 0.05, "w_reply": 0.1},
    ]


def test_the_pick_is_one_choice_over_the_drafts_and_its_answer_is_read_as_documented() -> None:
    args: PickDraftArgs = PickDraftArgs(
        topic=TOPIC,
        post=POSTS[0],
        drafts=[
            Draft(id="d1", text="Same here."),
            Draft(id="d2", text="uv resolves per platform; pip freeze is one env."),
        ],
    )
    question: ChoiceQuestion = draft_choice(args)
    assert question.options == {"d1": "Same here.", "d2": "uv resolves per platform; pip freeze is one env."}
    assert "`post`" in question.instructions and "`drafts`" in question.instructions
    reply: JsonObject = {
        "answers": {
            "best": {"type": "choice", "choice": "d2", "confidence": 0.6, "probabilities": {"d1": 0.2, "d2": 0.8}}
        }
    }
    answers: dict[str, ChoiceAnswer] = _choices(reply, (question,), "u")
    assert answers["best"] == ChoiceAnswer(choice="d2", probabilities={"d1": 0.2, "d2": 0.8}, confidence=0.6)
    assert render_pick(answers["best"], args.drafts) == (
        "Best draft: d2 (probability 0.80, confidence 0.60). All drafts, the likeliest first:\n"
        "d2 0.80 — uv resolves per platform; pip freeze is one env.\n"
        "d1 0.20 — Same here."
    )
    with pytest.raises(ProviderProtocolError, match="no choice"):
        _choices(
            {"answers": {"best": {"type": "choice", "choice": "d9", "confidence": 1, "probabilities": {}}}},
            (question,),
            "u",
        )


def test_a_draft_is_checked_for_topic_repeats_and_each_rule_and_refused_at_the_threshold() -> None:
    args: CheckDraftArgs = CheckDraftArgs(
        topic=TOPIC,
        post=POSTS[0],
        draft="Check out our course at example.com/uv",
        earlier_posts=["uv resolves per platform."],
        rules=["no links", "never sell"],
    )
    questions: tuple[NoulQuestion, ...] = check_questions(args)
    assert [question.id for question in questions] == ["off_topic", "repeats", "rule_0", "rule_1"]
    assert questions[3].instructions.endswith("break this rule of the campaign: `rules[1]`?")
    without_earlier: CheckDraftArgs = args.model_copy(update={"earlier_posts": []})
    assert [question.id for question in check_questions(without_earlier)] == ["off_topic", "rule_0", "rule_1"]
    answers: dict[str, float] = {"off_topic": 0.2, "repeats": 0.1, "rule_0": 0.95, "rule_1": 0.6}
    checks = draft_checks(answers, questions, args.rules, 0.5)
    assert [(check.name, check.fired) for check in checks] == [
        ("off_topic", False),
        ("repeats", False),
        ("rule_0", True),
        ("rule_1", True),
    ]
    assert render_checks(checks, 0.5) == (
        "Do not post the draft as it is: 2 of 4 checks reached 0.50. Change it and check again, or answer another post.\n"
        "- off the topic or beside the point of the post: 0.20\n"
        "- repeats an earlier post of the account: 0.10\n"
        "- breaks the rule: no links: 0.95 (fired)\n"
        "- breaks the rule: never sell: 0.60 (fired)"
    )
    passed = draft_checks({"off_topic": 0.1, "repeats": 0.1, "rule_0": 0.1, "rule_1": 0.1}, questions, args.rules, 0.5)
    assert render_checks(passed, 0.5).startswith("The draft passes its 4 checks (none reached 0.50)")


def test_the_arguments_refuse_repeated_ids_and_fewer_than_two_drafts() -> None:
    with pytest.raises(ValidationError, match="post ids repeat: a"):
        RankPostsArgs.model_validate(
            {"topic": TOPIC, "posts": [{"id": "a", "author": "", "text": "x"}, {"id": "a", "author": "", "text": "y"}]}
        )
    with pytest.raises(ValidationError, match="at least 2"):
        PickDraftArgs.model_validate(
            {"topic": TOPIC, "post": {"id": "a", "author": "", "text": "x"}, "drafts": [{"id": "d", "text": "y"}]}
        )
