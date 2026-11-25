"""The tell of a model whose account ran out of money (item 1207): once a day for each provider."""

from dotagent.llm.router import ModelRef, Pause
from dotagent.notify.money import MONEY_TITLE, RETELL_S, due, money_text
from dotagent.notify.owner import told

REF: ModelRef = ModelRef(provider="opencode_go", model="deepseek-v4.1-flash")


def test_a_provider_is_told_again_only_after_a_day() -> None:
    assert due({}, "opencode_go", 1000.0)
    assert not due({"opencode_go": 1000.0}, "opencode_go", 1000.0 + RETELL_S - 1)
    assert due({"opencode_go": 1000.0}, "opencode_go", 1000.0 + RETELL_S)
    assert due({"openrouter": 1000.0}, "opencode_go", 1001.0)


def test_the_tell_names_the_model_the_reason_and_the_providers_words_within_a_tells_limits() -> None:
    words: str = "provider answered HTTP 402: Insufficient balance. Top up at https://opencode.ai/console/billing"
    tell = told(
        MONEY_TITLE,
        money_text(REF, Pause(reason="not in the plan", until=0.0), words),
        "#/settings/models",
        "system",
        "",
    )
    assert tell.title == "A model cannot answer: payment needed"
    assert tell.text == f"opencode_go:deepseek-v4.1-flash (not in the plan): {words}"
