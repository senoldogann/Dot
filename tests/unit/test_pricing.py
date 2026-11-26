"""The estimate in dollars (item 1856): what the catalogue gives is read, a call is priced at its tier with its cache
reads and writes, a subscription and a local model add no dollars, and a model with no price makes the whole estimate
unknown, never a guess."""

from pathlib import Path

import pytest

from dotagent.llm.pricing import CostEstimate, PriceBook, TokenUse, estimate, price_kind, read_catalogue, tier_sizes

_CATALOGUE: Path = Path(__file__).parent.parent / "fixtures" / "prices" / "catalogue.json"


def _book() -> PriceBook:
    return PriceBook(
        fetched_at=1_000.0, source="local", etag=None, prices=read_catalogue(_CATALOGUE.read_text(encoding="utf-8"))
    )


def _use(model: str | None, bucket: int, input: int, output: int, cached: int, written: int) -> TokenUse:
    return TokenUse(model=model, bucket=bucket, input=input, output=output, cached=cached, cache_write=written)


def test_only_the_priced_providers_and_models_with_a_price_are_kept() -> None:
    book: PriceBook = _book()
    assert set(book.prices) == {"anthropic", "openai", "openrouter", "opencode"}
    assert set(book.prices["anthropic"]) == {"claude-sonnet-5-5"}
    assert book.prices["anthropic"]["claude-sonnet-5-5"].tiers[0].above == 200_000
    assert tier_sizes(book) == (200_000,)
    with pytest.raises(ValueError):
        read_catalogue('{"nobody": {}}')
    with pytest.raises(ValueError):
        read_catalogue("[]")


@pytest.mark.parametrize(
    ("model", "kind"),
    [
        ("anthropic:claude-sonnet-5-5", "priced"),
        ("openrouter:deepseek/deepseek-v4", "priced"),
        ("opencode:qwen4-max-free", "priced"),
        ("anthropic:claude-unlisted", "unknown"),
        ("claude_code:opus", "plan"),
        ("opencode_go:kimi-k3", "plan"),
        ("antigravity:gemini-3.8-flash", "plan"),
        ("ollama:gpt-oss:20b-cloud", "plan"),
        ("ollama:deepseek-v4.1-flash:cloud", "plan"),
        ("ollama:qwen3:8b", "local"),
        (None, "unknown"),
    ],
)
def test_each_model_is_priced_planned_local_or_unknown(model: str | None, kind: str) -> None:
    assert price_kind(_book(), model)[0] == kind


def test_a_call_is_priced_with_its_cache_and_at_its_tier() -> None:
    book: PriceBook = _book()
    sizes: tuple[int, ...] = tier_sizes(book)
    # 1M input of which 600k read from the cache and 100k written to it, 100k out, below the tier:
    # 300k x 3 + 600k x 0.3 + 100k x 3.75 + 100k x 15 = 0.9 + 0.18 + 0.375 + 1.5 dollars.
    below: CostEstimate = estimate(
        book, [_use("anthropic:claude-sonnet-5-5", 0, 1_000_000, 100_000, 600_000, 100_000)], sizes
    )
    assert below.usd == pytest.approx(2.955)
    # The same above 200k tokens of input is at the tier's prices: 1.8 + 0.36 + 0.75 + 2.25.
    above: CostEstimate = estimate(
        book, [_use("anthropic:claude-sonnet-5-5", 1, 1_000_000, 100_000, 600_000, 100_000)], sizes
    )
    assert above.usd == pytest.approx(5.16)
    # No cache write price: written tokens are billed as input (OpenRouter's 0.5).
    routed: CostEstimate = estimate(
        book, [_use("openrouter:deepseek/deepseek-v4", 0, 2_000_000, 0, 1_000_000, 500_000)], sizes
    )
    assert routed.usd == pytest.approx(0.25 + 0.05 + 0.25)
    assert below.prices_at == 1_000.0


def test_a_plan_or_local_model_adds_no_dollars_and_an_unknown_one_makes_it_unknown() -> None:
    book: PriceBook = _book()
    sizes: tuple[int, ...] = tier_sizes(book)
    mixed: CostEstimate = estimate(
        book,
        [
            _use("openai:gpt-6", 0, 1_000_000, 0, 0, 0),
            _use("claude_code:opus", 0, 5_000_000, 10_000, 0, 0),
            _use("ollama:qwen3:8b", 0, 9_000, 9, 0, 0),
            # No tokens: not counted, even of an unknown model.
            _use("anthropic:claude-unlisted", 0, 0, 0, 0, 0),
        ],
        sizes,
    )
    assert (mixed.usd, mixed.plan_models, mixed.unknown_models) == (pytest.approx(1.25), ("claude_code:opus",), ())
    unknown: CostEstimate = estimate(
        book, [_use("openai:gpt-6", 0, 1_000_000, 0, 0, 0), _use(None, 0, 10, 10, 0, 0)], sizes
    )
    assert (unknown.usd, unknown.priced_usd, unknown.unknown_models) == (None, pytest.approx(1.25), ("?",))
    # Without any prices yet, a priced provider's model is unknown too.
    nothing: CostEstimate = estimate(None, [_use("openai:gpt-6", 0, 1_000_000, 0, 0, 0)], ())
    assert (nothing.usd, nothing.unknown_models, nothing.prices_at) == (None, ("openai:gpt-6",), None)
