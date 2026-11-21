"""The retry budget (item 1306): a server's Retry-After is held within the policy's delays with jitter, the retries of a
model call begin no pause or try after a deadline below the run's call timeout (the pauses are budgeted, not the
duration of a try), and a rate limit that asks for a long pause is raised to the run loop instead of slept through. A
fake clock and a fake sleep play the time; a mock transport plays the server."""

import httpx
import pytest

from dotagent.core.errors import ProviderHttpError, ProviderRateLimitError
from dotagent.core.retry import RetryPolicy, hinted_delay, retry_async
from dotagent.llm.connector import RETRY_DEADLINE_S, get_json, post_json
from dotagent.llm.http import is_retryable_provider_error, retry_after_hint
from dotagent.llm.provider_errors import classify_generic

POLICY: RetryPolicy = RetryPolicy(attempts=6, base_delay_s=1.0, max_delay_s=30.0)


class _Time:
    """A clock that only :meth:`sleep` and the operations move."""

    def __init__(self) -> None:
        self.now: float = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _limited(retry_after_s: float | None) -> ProviderRateLimitError:
    error: ProviderHttpError = classify_generic(429, None, retry_after_s, {})
    assert isinstance(error, ProviderRateLimitError)
    return error


@pytest.mark.parametrize(("hint", "low", "high"), [(0.0, 1.0, 1.2), (5.0, 5.0, 6.0), (120.0, 30.0, 30.0)])
def test_a_hint_is_held_within_the_policys_delays_with_jitter(hint: float, low: float, high: float) -> None:
    for _ in range(50):
        assert low <= hinted_delay(hint, POLICY) <= high


@pytest.mark.asyncio
async def test_a_hint_of_zero_is_not_an_instant_retry_and_a_long_one_is_cut_to_the_longest_delay() -> None:
    fake: _Time = _Time()
    hints: list[float] = [0.0, 25.0, 500.0]

    async def operation() -> str:
        if hints:
            raise _limited(hints.pop(0))
        return "answered"

    answer: str = await retry_async(
        operation, lambda error: True, retry_after_hint, POLICY, "t", clock=fake.clock, sleep=fake.sleep
    )
    assert answer == "answered"
    assert 1.0 <= fake.slept[0] <= 1.2 and 25.0 <= fake.slept[1] <= 30.0 and fake.slept[2] == 30.0


@pytest.mark.asyncio
async def test_the_retries_stop_at_the_deadline_and_the_error_that_came_is_raised() -> None:
    fake: _Time = _Time()
    policy: RetryPolicy = POLICY.model_copy(update={"deadline_s": 50.0})
    calls: list[int] = []

    async def operation() -> str:
        calls.append(1)
        raise _limited(28.0)

    with pytest.raises(ProviderRateLimitError):
        await retry_async(
            operation, lambda error: True, retry_after_hint, policy, "t", clock=fake.clock, sleep=fake.sleep
        )
    # One pause of about 28 s fits in 50 s; a second would end past it, so the third try is never made.
    assert len(calls) == 2 and len(fake.slept) == 1 and fake.now < 50.0


@pytest.mark.asyncio
async def test_a_try_that_began_in_time_is_not_cut_and_only_a_pause_past_the_deadline_stops_the_retries() -> None:
    """The deadline is of the pauses (item 1306): a retry whose pause ends in time is made even when the try before it
    took most of the budget, and the try it makes runs to its end; the next pause, past the deadline, is not taken."""
    fake: _Time = _Time()
    policy: RetryPolicy = POLICY.model_copy(update={"deadline_s": 300.0})
    tries: list[float] = []

    async def operation() -> str:
        tries.append(fake.now)
        fake.now += 280.0 if len(tries) == 1 else 90.0
        raise _limited(5.0)

    with pytest.raises(ProviderRateLimitError):
        await retry_async(
            operation, lambda error: True, retry_after_hint, policy, "t", clock=fake.clock, sleep=fake.sleep
        )
    assert len(tries) == 2 and 285.0 <= tries[1] <= 287.0, tries
    assert fake.now > 300.0, "the second try was not cut at the deadline"
    assert len(fake.slept) == 1


@pytest.mark.asyncio
async def test_without_a_deadline_the_retries_go_on_as_before_and_the_normal_case_answers() -> None:
    fake: _Time = _Time()
    failures: list[int] = [1, 1]

    async def operation() -> str:
        if failures:
            failures.pop()
            raise _limited(3.0)
        return "ok"

    assert (
        await retry_async(
            operation, lambda error: True, retry_after_hint, POLICY, "t", clock=fake.clock, sleep=fake.sleep
        )
        == "ok"
    )
    assert len(fake.slept) == 2


def test_a_rate_limit_asking_for_more_than_thirty_seconds_is_for_the_run_loop_and_a_short_one_is_retried() -> None:
    assert is_retryable_provider_error(_limited(None))
    assert is_retryable_provider_error(_limited(0.0))
    assert is_retryable_provider_error(_limited(30.0))
    assert not is_retryable_provider_error(_limited(31.0))
    assert not is_retryable_provider_error(_limited(120.0))


def test_the_retry_deadline_is_below_the_run_loops_call_timeout() -> None:
    from dotagent.config import load_defaults

    agent: dict[str, object] = load_defaults()["agent"]  # type: ignore[assignment]
    assert float(agent["model_call_timeout_s"]) > RETRY_DEADLINE_S  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_connector_hands_a_long_rate_limit_to_the_caller_after_one_request() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(429, headers={"retry-after": "90"}, json={"error": {"message": "slow down"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderRateLimitError) as caught:
            await post_json(client, "https://p.example/v1", {}, {}, classify_generic, POLICY, "t")
    assert caught.value.retry_after_s == 90.0
    assert requests == ["/v1"]


@pytest.mark.asyncio
async def test_a_connector_retries_a_short_rate_limit_and_the_normal_answer_comes() -> None:
    answers: list[httpx.Response] = [
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(503),
        httpx.Response(200, json={"ok": True}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        policy: RetryPolicy = RetryPolicy(attempts=6, base_delay_s=0.0, max_delay_s=0.0)
        assert await get_json(client, "https://p.example/v1", {}, classify_generic, policy, "t") == {"ok": True}
    assert answers == []
