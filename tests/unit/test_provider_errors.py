"""How a provider's HTTP answers are told apart (item 254): a 402 is a model the plan does not include, terminal like a
spent quota, on every provider; Ollama's 429 that says the plan's included usage is used up is terminal as well, while
another 429 stays a rate limit to retry; and the pause of a model names which it was. Ollama's answer that its model
runner broke off is retried as a broken connection is (item 256). OpenRouter's 404 for a model the account's privacy
settings leave no provider of is a refused access that says why and where to change it (item 361), and its 402 for
credits that do not pay for the longest answer a request allows says so and how long an answer they do pay for."""

from dotagent.core.errors import (
    ProviderAccessError,
    ProviderCreditError,
    ProviderHttpError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderRunnerError,
    ProviderWindowError,
)
from dotagent.llm.http import is_retryable_provider_error
from dotagent.llm.provider_errors import (
    WireError,
    classify_anthropic,
    classify_generic,
    classify_ollama,
    classify_opencode,
    classify_openrouter,
    parse_wire_error,
)
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


# OpenRouter's answer of 2026-10-18 to a call of a model whose one provider keeps and trains on what it is sent.
_POLICY_BODY: str = (
    '{"error": {"message": "0 endpoints out of 1 requested are available matching your guardrail restrictions and data '
    "policy. We removed them for the following reasons (an endpoint may have matched multiple reasons):\\nZDR "
    "violation (account settings): 1 endpoint excluded; configurable at https://openrouter.ai/settings/privacy\\nPaid "
    "model training violation (account settings): 1 endpoint excluded; configurable at "
    'https://openrouter.ai/settings/privacy", "code": 404}}'
)


def test_openrouters_404_for_its_privacy_settings_is_a_refused_access_that_says_why_and_where() -> None:
    error = classify_openrouter(404, parse_wire_error(_POLICY_BODY), None, {"model": "m"})
    assert isinstance(error, ProviderAccessError) and error.status == 404
    assert error.message == (
        "OpenRouter's privacy settings for this account leave no provider of the model (ZDR violation; Paid model "
        "training violation): choose another model in Settings › Models, or change them at "
        "https://openrouter.ai/settings/privacy"
    )
    assert unavailable_reason(error) == "access refused"
    # Another 404, such as a model OpenRouter does not have, stays a plain failure.
    unknown = classify_openrouter(
        404, WireError(kind="", code="404", message="Model not found", limit_name=""), None, {}
    )
    assert type(unknown) is ProviderHttpError and unknown.message == "provider answered HTTP 404: Model not found"


# OpenRouter's answer of 2026-10-19 to a summary of a run on a model that may answer with 131072 tokens.
_CREDITS_BODY: str = (
    '{"error": {"message": "This request requires more credits, or fewer max_tokens. You requested up to 131072 '
    "tokens, but can only afford 29597. To increase, visit https://openrouter.ai/settings/credits and upgrade to a "
    'paid account", "code": 402}}'
)


def test_openrouters_402_for_credits_is_out_of_credits_and_names_the_answer_they_pay_for() -> None:
    error = classify_openrouter(402, parse_wire_error(_CREDITS_BODY), None, {"model": "m"})
    assert isinstance(error, ProviderCreditError) and isinstance(error, ProviderQuotaError)
    assert (error.status, error.affordable_tokens) == (402, 29597)
    assert unavailable_reason(error) == "out of credits"
    # Credits without a figure: the same reason, no answer length.
    plain = classify_openrouter(
        402, WireError(kind="", code="402", message="Insufficient credits", limit_name=""), None, {}
    )
    assert isinstance(plain, ProviderCreditError) and plain.affordable_tokens is None
    # A 402 that speaks of no credits stays a model the plan does not include.
    unplanned = classify_openrouter(402, EXCLUDED.model_copy(update={"message": "model not included"}), None, {})
    assert not isinstance(unplanned, ProviderCreditError) and unavailable_reason(unplanned) == "not in the plan"


def test_spent_credits_are_told_apart_from_a_spent_usage_window() -> None:
    """Item 713: a run waits for a quota or a rate that comes back by itself, a subscription's window, and never for
    credits, which the owner buys: Anthropic's spent balance and OpenCode's CreditsError are out of credits, OpenCode
    Go's usage limit a quota."""
    no_balance: WireError = WireError(
        kind="invalid_request_error",
        code="",
        message="Your credit balance is too low to access the API.",
        limit_name="",
    )
    credits: WireError = WireError(kind="CreditsError", code="", message="No credits left", limit_name="")
    window: WireError = WireError(kind="GoUsageLimitError", code="", message="Go usage limit exceeded", limit_name="5h")
    assert unavailable_reason(classify_anthropic(400, no_balance, None, {})) == "out of credits"
    assert unavailable_reason(classify_opencode(429, credits, None, {})) == "out of credits"
    assert isinstance(classify_opencode(429, window, None, {}), ProviderWindowError)
    assert unavailable_reason(classify_opencode(429, window, None, {})) == "quota exhausted"
