"""The goal check goes on to the next model when its own cannot answer (item 1354)."""

import asyncio

import pytest

from dotagent.agent.goal_chain import check_with_standby
from dotagent.agent.review import GoalChecker, GoalRequest, GoalVerdict
from dotagent.core.errors import ProviderAuthError, ProviderProtocolError, ProviderRateLimitError

_REQUEST = GoalRequest(
    goal="send the mail",
    plan="",
    targets="",
    digest="",
    facts="",
    answer="sent",
    claimed_success=True,
    keep_going=False,
)
_ACHIEVED = GoalVerdict(decision="achieved", reason="the mail is in Sent", unproven_steps=())


def _spent(model: str) -> ProviderRateLimitError:
    return ProviderRateLimitError(
        "provider answered HTTP 429: Individual quota reached. Resets in 116h58m5s.", 429, None, {"model": model}
    )


def _answers(asked: list[str], model: str) -> GoalChecker:
    async def check(request: GoalRequest) -> GoalVerdict:
        asked.append(model)
        return _ACHIEVED

    return check


def _fails(asked: list[str], model: str, error: Exception) -> GoalChecker:
    async def check(request: GoalRequest) -> GoalVerdict:
        asked.append(model)
        raise error

    return check


def test_a_check_the_first_model_cannot_give_is_asked_of_the_next_and_the_run_is_judged() -> None:
    asked: list[str] = []
    chain = check_with_standby(
        [("review", _fails(asked, "review", _spent("review"))), ("standby", _answers(asked, "standby"))], "run_1"
    )
    assert asyncio.run(chain(_REQUEST)) == _ACHIEVED
    assert asked == ["review", "standby"]


def test_the_first_model_that_answers_is_the_only_one_asked() -> None:
    asked: list[str] = []
    chain = check_with_standby([("review", _answers(asked, "review")), ("standby", _answers(asked, "standby"))], None)
    assert asyncio.run(chain(_REQUEST)) == _ACHIEVED
    assert asked == ["review"]


def test_a_key_that_is_refused_goes_on_to_the_next_model_too() -> None:
    asked: list[str] = []
    refused = ProviderAuthError("provider answered HTTP 401", 401, {})
    chain = check_with_standby(
        [("review", _fails(asked, "review", refused)), ("standby", _answers(asked, "standby"))], None
    )
    assert asyncio.run(chain(_REQUEST)) == _ACHIEVED


def test_an_error_that_says_nothing_of_the_model_being_spent_is_raised_at_once() -> None:
    asked: list[str] = []
    broken = ProviderProtocolError("the verdict holds no decision", {})
    chain = check_with_standby(
        [("review", _fails(asked, "review", broken)), ("standby", _answers(asked, "standby"))], None
    )
    with pytest.raises(ProviderProtocolError):
        asyncio.run(chain(_REQUEST))
    assert asked == ["review"]


def test_when_no_model_can_answer_the_first_error_is_raised_naming_every_model_asked() -> None:
    asked: list[str] = []
    chain = check_with_standby(
        [
            ("review", _fails(asked, "review", _spent("review"))),
            ("standby", _fails(asked, "standby", _spent("standby"))),
        ],
        "run_1",
    )
    with pytest.raises(ProviderRateLimitError) as raised:
        asyncio.run(chain(_REQUEST))
    assert raised.value.context["model"] == "review"
    assert "no model could take it: review (rate limited), standby (rate limited)" in raised.value.message
    assert asked == ["review", "standby"]


def test_a_single_model_that_cannot_answer_keeps_its_own_error_unchanged() -> None:
    asked: list[str] = []
    error = _spent("review")
    chain = check_with_standby([("review", _fails(asked, "review", error))], None)
    with pytest.raises(ProviderRateLimitError) as raised:
        asyncio.run(chain(_REQUEST))
    assert raised.value is error
    assert "no model could take it" not in raised.value.message


def test_a_check_with_no_model_to_ask_is_refused_where_it_is_built() -> None:
    with pytest.raises(ValueError):
        check_with_standby([], None)
