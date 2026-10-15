"""How a provider's HTTP answers are told apart (item 254): a 402 is a model the plan does not include, terminal like a
spent quota, on every provider; Ollama's 429 that says the plan's included usage is used up is terminal as well, while
another 429 stays a rate limit to retry; and the pause of a model names which it was. Ollama's answer that its model
runner broke off is retried as a broken connection is (item 256)."""

from dotagent.core.errors import ProviderQuotaError, ProviderRateLimitError, ProviderRunnerError
from dotagent.llm.http import is_retryable_provider_error
from dotagent.llm.provider_errors import WireError, classify_generic, classify_ollama
from dotagent.llm.router import unavailable_reason

SPENT: WireError = WireError(kind="", code="", message="You used all included Pro usage", limit_name="")
BURST: WireError = WireError(kind="", code="", message="too many requests", limit_name="")
EXCLUDED: WireError = WireError(
    kind="", code="", message="this model is not included in your free usage, add usage credits", limit_name=""
)


def test_a_402_is_a_model_the_plan_does_not_include_which_pauses_the_model() -> None:
    error = classify_generic(402, EXCLUDED, None, {"model": "m"})
    assert isinstance(error, ProviderQuotaError) and error.status == 402
    assert unavailable_reason(error) == "not in the plan"
    assert unavailable_reason(ProviderQuotaError("spent", 429, None, {})) == "quota exhausted"


def test_ollamas_429_of_a_spent_plan_is_terminal_and_another_429_a_rate_limit() -> None:
    spent = classify_ollama(429, SPENT, None, {})
    assert isinstance(spent, ProviderQuotaError)
    burst = classify_ollama(429, BURST, 2.0, {})
    assert isinstance(burst, ProviderRateLimitError) and not isinstance(burst, ProviderQuotaError)
    assert burst.retry_after_s == 2.0
    assert isinstance(classify_ollama(402, EXCLUDED, None, {}), ProviderQuotaError)


RUNNER: WireError = WireError(
    kind="",
    code="",
    message='Post "http://127.0.0.1:60949/tokenize": read tcp 127.0.0.1:49762->127.0.0.1:60949: read: connection reset '
    "by peer",
    limit_name="",
)
INVALID: WireError = WireError(kind="", code="", message="invalid input type", limit_name="")


def test_ollamas_answer_that_its_model_runner_broke_off_is_retried_and_another_400_is_not() -> None:
    lost = classify_ollama(400, RUNNER, None, {})
    assert isinstance(lost, ProviderRunnerError) and lost.status == 400
    assert is_retryable_provider_error(lost)
    assert lost.message.startswith('provider answered HTTP 400: Post "http://127.0.0.1:60949/tokenize"')
    plain = classify_ollama(400, INVALID, None, {})
    # The runner's words count only on a 400 or a 5xx.
    assert not isinstance(classify_ollama(404, RUNNER, None, {}), ProviderRunnerError)
    assert not isinstance(plain, ProviderRunnerError) and not is_retryable_provider_error(plain)
    # Only Ollama reads the runner's words: another provider's 400 stays a plain failure.
    assert not is_retryable_provider_error(classify_generic(400, RUNNER, None, {}))
