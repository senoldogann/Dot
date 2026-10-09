"""Which helper and standby models can answer: the keys of their providers and the pauses after an error that says a
provider cannot answer for a while, and how that is told to the model."""

import re

from dotagent.agent.helpers import Roster, Unavailable, roster_for, roster_text, unavailable_text, until_text
from dotagent.core.errors import (
    MissingSecretError,
    ProviderAuthError,
    ProviderHttpError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransportError,
)
from dotagent.llm.router import Pause, pause_for

NOW: float = 1_800_000_000.0
COOLDOWN_S: float = 600.0


def test_a_model_is_available_when_its_provider_has_its_key_and_is_not_paused() -> None:
    roster: Roster = roster_for(
        ["anthropic:claude-opus-5-5", "openai:gpt-5.5", "ollama:qwen3:8b", "anthropic:claude-sonnet-5-5"],
        frozenset({"anthropic", "ollama"}),
        {"anthropic": Pause(reason="quota exhausted", until=NOW + COOLDOWN_S)},
    )
    assert roster.available == ("ollama:qwen3:8b",)
    assert roster.unavailable == (
        Unavailable(model="anthropic:claude-opus-5-5", reason="quota exhausted", until=NOW + COOLDOWN_S),
        Unavailable(model="openai:gpt-5.5", reason="no key", until=None),
        Unavailable(model="anthropic:claude-sonnet-5-5", reason="quota exhausted", until=NOW + COOLDOWN_S),
    )


def test_the_roster_names_each_model_once_in_its_order_as_a_label() -> None:
    roster: Roster = roster_for([" ollama:qwen3:8b ", "ollama:qwen3:8b", "ollama:gemma4"], frozenset({"ollama"}), {})
    assert roster == Roster(available=("ollama:qwen3:8b", "ollama:gemma4"), unavailable=())


def test_a_provider_is_paused_for_the_time_it_named_else_for_the_cooldown() -> None:
    assert pause_for(ProviderQuotaError("credits", 429, 3600.0, {}), COOLDOWN_S, NOW) == Pause(
        reason="quota exhausted", until=NOW + 3600.0
    )
    assert pause_for(ProviderQuotaError("credits", 402, None, {}), COOLDOWN_S, NOW) == Pause(
        reason="quota exhausted", until=NOW + COOLDOWN_S
    )
    assert pause_for(ProviderRateLimitError("slow down", 429, 300.0, {}), COOLDOWN_S, NOW) == Pause(
        reason="rate limited", until=NOW + 300.0
    )
    assert pause_for(ProviderRateLimitError("slow down", 429, 0.0, {}), COOLDOWN_S, NOW) == Pause(
        reason="rate limited", until=NOW + COOLDOWN_S
    )
    assert pause_for(ProviderAuthError("bad key", 401, {}), COOLDOWN_S, NOW) == Pause(
        reason="key refused", until=NOW + COOLDOWN_S
    )
    assert pause_for(MissingSecretError("missing API key", {}), COOLDOWN_S, NOW) == Pause(
        reason="no key", until=NOW + COOLDOWN_S
    )


def test_an_error_that_says_nothing_about_the_provider_pauses_nothing() -> None:
    assert pause_for(ProviderTransportError("connection reset", {}), COOLDOWN_S, NOW) is None
    assert pause_for(ProviderHttpError("server error", 503, {}), COOLDOWN_S, NOW) is None
    assert pause_for(KeyError("choices"), COOLDOWN_S, NOW) is None


def test_the_model_reads_which_helpers_can_answer_and_until_when_the_others_cannot() -> None:
    paused: Unavailable = Unavailable(model="anthropic:claude-opus-5-5", reason="quota exhausted", until=NOW)
    keyless: Unavailable = Unavailable(model="openai:gpt-5.5", reason="no key", until=None)
    assert unavailable_text(keyless, NOW) == "openai:gpt-5.5 (no key)"
    assert unavailable_text(paused, NOW) == f"anthropic:claude-opus-5-5 (quota exhausted until {until_text(NOW, NOW)})"
    assert roster_text(Roster(available=("ollama:qwen3:8b",), unavailable=(keyless,)), NOW) == (
        "available: ollama:qwen3:8b; cannot answer now: openai:gpt-5.5 (no key)"
    )
    assert roster_text(Roster(available=(), unavailable=()), NOW) == "available: none"


def test_the_end_of_a_pause_is_a_time_of_day_today_and_a_date_later() -> None:
    assert re.fullmatch(r"\d{2}:\d{2}", until_text(NOW, NOW))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", until_text(NOW + 3 * 86_400, NOW))
