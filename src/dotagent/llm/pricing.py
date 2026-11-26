"""What a model's tokens cost in dollars, estimated from a public price list (item 1856).

The prices come from the catalogue of models.dev (:data:`CATALOGUE_URL`, ``DOT_PRICES_URL`` overrides it and an empty
value turns the fetching off): dollars per million tokens of input, output, cache reads and cache writes, for each model
of each provider, some with a dearer tier above a context size. Dot keeps the part it can use (:data:`PRICED_PROVIDERS`)
in ``prices.json`` in its home, fetches it again once a day with the catalogue's ETag, and estimates a run's cost from
the tokens its ``model_call`` events recorded, call by call on the model of each.

What it never does is guess. A model is priced only when its provider is billed by the token at the catalogue's price
(Anthropic, OpenAI and Google with your own key, OpenRouter, OpenCode Zen); a subscription (the Claude plan, OpenCode
Go, Antigravity, Ollama's cloud) is a ``plan`` model, with no dollars per token; a model run on this Mac (Ollama's local
models) costs nothing; and a model the catalogue has no price for, or tokens whose model was not recorded, are
``unknown``, which makes the whole estimate unknown rather than short.
"""

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from pydantic import Field

from dotagent.core.types import FrozenModel

# Where the catalogue is read from; ``DOT_PRICES_URL`` overrides it (the tests set it empty: no fetching).
CATALOGUE_URL: str = "https://models.dev/api.json"
# Dot's provider by the catalogue's name of it, for the providers billed by the token at the catalogue's price.
PRICED_PROVIDERS: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "openrouter": "openrouter",
    "opencode": "opencode",
}
# Dot's providers that are a subscription: what their tokens cost is the plan's, not a price per token.
PLAN_PROVIDERS: frozenset[str] = frozenset({"claude_code", "opencode_go", "antigravity"})
_PER_TOKENS: float = 1_000_000.0

type PriceKind = Literal["priced", "plan", "local", "unknown"]


class Tier(FrozenModel):
    """A dearer price of a model for a call whose input is above ``above`` tokens."""

    above: int
    input: float
    output: float
    cache_read: float | None
    cache_write: float | None


class Price(FrozenModel):
    """Dollars per million tokens of a model. ``cache_read`` and ``cache_write`` are ``None`` when the catalogue gives
    none: those tokens are billed as input."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float | None = Field(ge=0)
    cache_write: float | None = Field(ge=0)
    tiers: tuple[Tier, ...]


class PriceBook(FrozenModel):
    """The prices Dot keeps: by the catalogue's provider, then model id; when they were fetched (or last found
    unchanged), from where, and the catalogue's ETag for the next conditional request."""

    fetched_at: float
    source: str
    etag: str | None
    prices: dict[str, dict[str, Price]]


class TokenUse(FrozenModel):
    """Tokens of one model (``None``: not recorded) in calls whose input was above the ``bucket``-th of the tier sizes
    asked for (0: above none). ``input`` holds the cached and cache-written tokens too, as the calls record them."""

    model: str | None
    bucket: int
    input: int
    output: int
    cached: int
    cache_write: int


class CostEstimate(FrozenModel):
    """What tokens cost in dollars. ``usd`` is ``None`` when part of them is of a model with no known price
    (``unknown_models``; a model not recorded is ``"?"``); ``plan_models`` ran on a subscription and add no dollars.
    ``prices_at`` is when the prices were fetched, ``None`` when Dot has none yet (then all is unknown)."""

    usd: float | None
    priced_usd: float
    unknown_models: tuple[str, ...]
    plan_models: tuple[str, ...]
    prices_at: float | None


def catalogue_url() -> str:
    """Where the catalogue is fetched from: ``DOT_PRICES_URL`` when it is set (empty: not fetched), else models.dev."""
    override: str | None = os.environ.get("DOT_PRICES_URL")
    return CATALOGUE_URL if override is None else override


def _number(value: object) -> float | None:
    """``value`` as a price when it is a number that is not below 0, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


def _tier(entry: object) -> Tier | None:
    """A tier of the catalogue (``{"input", "output", ..., "tier": {"type": "context", "size"}}``), or ``None`` for one
    this module does not read (another type, a price missing)."""
    if not isinstance(entry, dict):
        return None
    limit: object = entry.get("tier")
    if not isinstance(limit, dict) or limit.get("type") != "context":
        return None
    size: float | None = _number(limit.get("size"))
    input_price: float | None = _number(entry.get("input"))
    output_price: float | None = _number(entry.get("output"))
    if size is None or input_price is None or output_price is None:
        return None
    return Tier(
        above=int(size),
        input=input_price,
        output=output_price,
        cache_read=_number(entry.get("cache_read")),
        cache_write=_number(entry.get("cache_write")),
    )


def _price(model: object) -> Price | None:
    """A model's price from its entry in the catalogue, ``None`` when it has no input and output price."""
    if not isinstance(model, dict):
        return None
    cost: object = model.get("cost")
    if not isinstance(cost, dict):
        return None
    input_price: float | None = _number(cost.get("input"))
    output_price: float | None = _number(cost.get("output"))
    if input_price is None or output_price is None:
        return None
    listed: object = cost.get("tiers")
    tiers: list[Tier] = []
    if isinstance(listed, list):
        tiers = [tier for tier in (_tier(entry) for entry in listed) if tier is not None]
    return Price(
        input=input_price,
        output=output_price,
        cache_read=_number(cost.get("cache_read")),
        cache_write=_number(cost.get("cache_write")),
        tiers=tuple(sorted(tiers, key=lambda tier: tier.above)),
    )


def read_catalogue(text: str) -> dict[str, dict[str, Price]]:
    """The prices of :data:`PRICED_PROVIDERS` in the catalogue ``text`` (models.dev's ``api.json``); a ``ValueError``
    when it is not a JSON object or holds none of them."""
    whole: object = json.loads(text)
    if not isinstance(whole, dict):
        raise ValueError("the catalogue is not a JSON object")
    prices: dict[str, dict[str, Price]] = {}
    for name in PRICED_PROVIDERS.values():
        provider: object = whole.get(name)
        models: object = provider.get("models") if isinstance(provider, dict) else None
        if not isinstance(models, dict):
            continue
        kept: dict[str, Price] = {}
        for model_id, entry in models.items():
            price: Price | None = _price(entry)
            if isinstance(model_id, str) and price is not None:
                kept[model_id] = price
        prices[name] = kept
    if not prices:
        raise ValueError("the catalogue holds none of the providers Dot prices")
    return prices


def _is_local_ollama(model: str) -> bool:
    """Whether an Ollama model runs on this Mac: Ollama's cloud models end in ``cloud`` (``gpt-oss:20b-cloud``,
    ``deepseek-v4.1-flash:cloud``)."""
    return not (model.endswith(":cloud") or model.endswith("-cloud"))


def price_kind(book: PriceBook | None, model: str | None) -> tuple[PriceKind, Price | None]:
    """How the tokens of ``model`` (a ``provider:model`` reference) are paid for, and its price when it is priced."""
    if model is None:
        return "unknown", None
    provider, _, name = model.partition(":")
    if provider in PLAN_PROVIDERS or (provider == "ollama" and not _is_local_ollama(name)):
        return "plan", None
    if provider == "ollama":
        return "local", None
    catalogue_name: str | None = PRICED_PROVIDERS.get(provider)
    if book is None or catalogue_name is None:
        return "unknown", None
    price: Price | None = book.prices.get(catalogue_name, {}).get(name)
    return ("unknown", None) if price is None else ("priced", price)


def tier_sizes(book: PriceBook | None) -> tuple[int, ...]:
    """Every context size a tier of the book starts above, smallest first: the buckets a store's sums are split by."""
    if book is None:
        return ()
    return tuple(
        sorted({tier.above for models in book.prices.values() for price in models.values() for tier in price.tiers})
    )


def _rates(price: Price, above: int | None) -> tuple[float, float, float, float]:
    """``(input, output, cache read, cache write)`` dollars per million tokens of ``price`` for a call whose input was
    above ``above`` tokens (``None``: above no tier size): the dearest tier it is above, else the base price."""
    chosen: Tier | None = None
    if above is not None:
        for tier in price.tiers:
            if tier.above <= above:
                chosen = tier
    input_rate: float = price.input if chosen is None else chosen.input
    output_rate: float = price.output if chosen is None else chosen.output
    read: float | None = price.cache_read if chosen is None else chosen.cache_read
    write: float | None = price.cache_write if chosen is None else chosen.cache_write
    return input_rate, output_rate, input_rate if read is None else read, input_rate if write is None else write


def use_cost(price: Price, use: TokenUse, sizes: Sequence[int]) -> float:
    """Dollars of ``use`` at ``price``; ``sizes`` are the tier sizes its bucket counts (:func:`tier_sizes`)."""
    above: int | None = sizes[use.bucket - 1] if 0 < use.bucket <= len(sizes) else None
    input_rate, output_rate, read_rate, write_rate = _rates(price, above)
    cached: int = min(use.cached, use.input)
    written: int = min(use.cache_write, use.input - cached)
    plain: int = use.input - cached - written
    return (plain * input_rate + cached * read_rate + written * write_rate + use.output * output_rate) / _PER_TOKENS


def estimate(book: PriceBook | None, uses: Iterable[TokenUse], sizes: Sequence[int]) -> CostEstimate:
    """What ``uses`` cost by ``book`` (``None``: no prices yet), their buckets counted by ``sizes``."""
    priced: float = 0.0
    unknown: set[str] = set()
    plan: set[str] = set()
    for use in uses:
        if use.input == 0 and use.output == 0:
            continue
        kind, price = price_kind(book, use.model)
        if kind == "priced" and price is not None:
            priced += use_cost(price, use, sizes)
        elif kind == "plan" and use.model is not None:
            plan.add(use.model)
        elif kind == "unknown":
            unknown.add("?" if use.model is None else use.model)
    return CostEstimate(
        usd=None if unknown else round(priced, 6),
        priced_usd=round(priced, 6),
        unknown_models=tuple(sorted(unknown)),
        plan_models=tuple(sorted(plan)),
        prices_at=None if book is None else book.fetched_at,
    )


def estimates_by_key(
    book: PriceBook | None, uses: Mapping[str, Sequence[TokenUse]], sizes: Sequence[int]
) -> dict[str, CostEstimate]:
    """:func:`estimate` of each key's uses (a day, a run)."""
    return {key: estimate(book, listed, sizes) for key, listed in uses.items()}
