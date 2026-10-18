"""OpenRouter provider: the models of many labs behind one OpenAI-style Chat Completions API, on one key (item 349).

What a model can do is read from OpenRouter's own list of its models (``GET /models``), kept for ``models_ttl_s``:
whether it reads images and calls tools, its context and output limits, its reasoning levels or its reasoning switch,
and whether it takes a temperature. The level goes as OpenRouter's ``reasoning`` object, and an assistant turn carries
the ``reasoning_details`` it came with back to the same model, unchanged, as OpenRouter asks beside tool calls. A model
whose id ends in ``:free``, or whose prices are both zero, is free. A variant of a listed model (``:nitro``, ``:floor``,
``:online``, which choose how OpenRouter routes it and are not listed themselves) can do what the model can.
``GET /key`` tells what the key has spent and may spend, and costs nothing. An answer of either that is not shaped as
OpenRouter documents it is a :class:`ProviderProtocolError`, not an empty list or nothing spent.
"""

import asyncio
import time
from collections.abc import Collection, Mapping

import httpx
from pydantic import JsonValue

from dotagent import __version__
from dotagent.config import OpenRouterConfig
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, chat_codec
from dotagent.llm.connector import get_json, retry_with_reset, stream_model_reply
from dotagent.llm.http import build_client, request_context
from dotagent.llm.provider_errors import classify_generic, classify_openrouter
from dotagent.llm.traits import ModelTraits, info_from_traits, is_known_label
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.llm.wire import get_float, get_int, get_list, get_obj, get_str

# OpenRouter takes ``max_tokens``, sends the usage with the last chunk unasked, reads the level as its ``reasoning``
# object and wants the reasoning details of a turn back beside its tool calls.
_CHAT_OPTIONS: ChatOptions = ChatOptions(
    max_tokens_field="max_tokens",
    request_usage=False,
    replay_reasoning=False,
    cache_key_in_body=False,
    reasoning_object=True,
    replay_details=True,
)
_FREE_SUFFIX: str = ":free"
_ZERO_PRICE: str = "0"
# The limits of a model OpenRouter lists without them, or does not list at all: assumed, as other providers assume.
_ASSUMED_CONTEXT: int = 128_000
_ASSUMED_OUTPUT: int = 32_768
_UNLISTED: ModelTraits = ModelTraits(
    vision=False,
    context_length=_ASSUMED_CONTEXT,
    window_source="assumed",
    max_output_tokens=_ASSUMED_OUTPUT,
    efforts=(),
    reasoning=False,
    thinking_toggle=False,
    temperature=True,
)


class ListedModel(FrozenModel):
    """What OpenRouter's list says of one model: its traits, whether it calls tools, whether it is free."""

    traits: ModelTraits
    tools: bool
    free: bool


class KeyStatus(FrozenModel):
    """What OpenRouter says of a key: the credits it has spent, its limit (``None`` for none) and whether it is on the
    free tier."""

    usage: float
    limit: float | None
    free_tier: bool


def _strings(entry: JsonObject, key: str) -> frozenset[str]:
    return frozenset(item for item in get_list(entry, key) if isinstance(item, str))


def writes_text(entry: JsonObject) -> bool:
    """Whether a listed model answers in text (an image model does not); a model that names no outputs is taken to."""
    outputs: frozenset[str] = _strings(get_obj(entry, "architecture"), "output_modalities")
    return not outputs or "text" in outputs


def listed_model(entry: JsonObject) -> ListedModel:
    """One entry of OpenRouter's list as Dot reads it: ``supported_parameters`` says whether the model calls tools,
    reasons and takes a temperature, ``architecture`` whether it reads images, ``reasoning`` its levels (the ones Dot
    knows) or, when it has none and its reasoning is not mandatory, a switch, ``top_provider`` its output limit and
    ``pricing`` whether it costs nothing."""
    parameters: frozenset[str] = _strings(entry, "supported_parameters")
    reasoning: JsonObject = get_obj(entry, "reasoning")
    reasons: bool = "reasoning" in parameters
    labels: tuple[str, ...] = tuple(
        label for label in get_list(reasoning, "supported_efforts") if isinstance(label, str) and is_known_label(label)
    )
    mandatory: JsonValue = reasoning.get("mandatory")
    context: int = get_int(entry, "context_length") or get_int(get_obj(entry, "top_provider"), "context_length")
    output: int = get_int(get_obj(entry, "top_provider"), "max_completion_tokens")
    pricing: JsonObject = get_obj(entry, "pricing")
    zero: bool = get_str(pricing, "prompt") == _ZERO_PRICE and get_str(pricing, "completion") == _ZERO_PRICE
    traits: ModelTraits = ModelTraits(
        vision="image" in _strings(get_obj(entry, "architecture"), "input_modalities"),
        context_length=context or _ASSUMED_CONTEXT,
        window_source="published" if context else "assumed",
        max_output_tokens=output or _ASSUMED_OUTPUT,
        efforts=labels if reasons else (),
        reasoning=reasons,
        thinking_toggle=reasons and len(labels) < 2 and mandatory is False,
        temperature="temperature" in parameters,
    )
    return ListedModel(
        traits=traits, tools="tools" in parameters, free=get_str(entry, "id").endswith(_FREE_SUFFIX) or zero
    )


def catalog_of(listing: JsonObject) -> dict[str, ListedModel]:
    """``GET /models``'s answer as Dot keeps it: the models that answer in text, by id. An answer without a list of
    models in ``data`` is not one."""
    entries: JsonValue = listing.get("data")
    if not isinstance(entries, list) or not entries:
        raise ProviderProtocolError(
            "OpenRouter's list of models came without models",
            {"keys": ", ".join(sorted(listing)), "data": str(entries)[:200]},
        )
    return {
        get_str(entry, "id"): listed_model(entry)
        for entry in entries
        if isinstance(entry, dict) and get_str(entry, "id") and writes_text(entry)
    }


def listed_id(listed: Collection[str], model: str) -> str | None:
    """The id of the list ``listed`` that stands for ``model``: the model's own, or the listed model's a variant id
    (``model:nitro``) routes to; ``None`` when neither is listed."""
    if model in listed:
        return model
    base, colon, _ = model.rpartition(":")
    return base if colon and base in listed else None


def listed_for(catalog: Mapping[str, ListedModel], model: str) -> ListedModel | None:
    """What the list says of ``model``, or of the listed model a variant id routes to (:func:`listed_id`)."""
    found: str | None = listed_id(catalog.keys(), model)
    return None if found is None else catalog[found]


def model_info_of(model: str, listed: ListedModel) -> ModelInfo:
    free: bool = listed.free or model.endswith(_FREE_SUFFIX)
    return info_from_traits(model, listed.traits, free).model_copy(update={"tools": listed.tools})


def _number(value: JsonValue) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def parse_key(payload: JsonObject) -> KeyStatus:
    """``GET /key``'s answer: ``data.usage``, ``data.limit`` (``null`` for no limit) and ``data.is_free_tier``. One
    without the credits spent as a number is not that answer."""
    data: JsonValue = payload.get("data")
    if not isinstance(data, dict) or not _number(data.get("usage")):
        raise ProviderProtocolError(
            "OpenRouter's answer about the key does not say what it has spent",
            {
                "keys": ", ".join(sorted(payload)),
                "data_keys": ", ".join(sorted(data)) if isinstance(data, dict) else "",
            },
        )
    limit: JsonValue = data.get("limit")
    return KeyStatus(
        usage=get_float(data, "usage"),
        limit=float(limit) if isinstance(limit, int | float) and not isinstance(limit, bool) else None,
        free_tier=data.get("is_free_tier") is True,
    )


class OpenRouterProvider:
    """Connector to OpenRouter."""

    def __init__(self, config: OpenRouterConfig, api_key: str) -> None:
        self._config: OpenRouterConfig = config
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"dot-agent/{__version__}",
        }
        self._slots: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent)
        self._policy: RetryPolicy = RetryPolicy(attempts=config.attempts, base_delay_s=1.0, max_delay_s=30.0)
        # OpenRouter's list of its models, by id, and when it was read (``None`` before the first read that worked).
        self._listing: asyncio.Lock = asyncio.Lock()
        self._listed: dict[str, ListedModel] = {}
        self._listed_at: float | None = None

    @property
    def name(self) -> str:
        return "openrouter"

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    async def _catalog(self) -> dict[str, ListedModel]:
        """The models OpenRouter lists that answer in text, by id, read again once ``models_ttl_s`` has passed."""
        async with self._listing:
            now: float = time.monotonic()
            if self._listed_at is None or now - self._listed_at >= self._config.models_ttl_s:
                listed: JsonObject = await get_json(
                    self._client,
                    self._url("/models"),
                    self._headers,
                    classify_generic,
                    self._policy,
                    "openrouter.models",
                )
                self._listed = catalog_of(listed)
                self._listed_at = now
            return self._listed

    async def _traits(self, model: str) -> ModelTraits:
        found: ListedModel | None = listed_for(await self._catalog(), model)
        return _UNLISTED if found is None else found.traits

    async def model_info(self, model: str) -> ModelInfo:
        found: ListedModel | None = listed_for(await self._catalog(), model)
        if found is None:
            return info_from_traits(model, _UNLISTED, model.endswith(_FREE_SUFFIX))
        return model_info_of(model, found)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        catalog: dict[str, ListedModel] = await self._catalog()
        return tuple(model_info_of(model, catalog[model]) for model in sorted(catalog))

    async def key_status(self) -> KeyStatus:
        """What the key has spent and may spend (a free call, which also proves the key)."""
        payload: JsonObject = await get_json(
            self._client, self._url("/key"), self._headers, classify_generic, self._policy, "openrouter.key"
        )
        return parse_key(payload)

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        traits: ModelTraits = await self._traits(request.model)
        body: JsonObject = build_chat_body(request, traits, _CHAT_OPTIONS, f"openrouter:{request.model}")
        url: str = self._url("/chat/completions")
        payload: str = dumps_compact(body)
        context: JsonObject = request_context(url, body)
        codec = chat_codec(classify_openrouter, _CHAT_OPTIONS)

        async def attempt() -> ModelReply:
            return await stream_model_reply(
                self._client, url, payload, self._headers, context, classify_openrouter, codec, request.model, sink
            )

        async with self._slots:
            return await retry_with_reset(attempt, sink, self._policy, "openrouter.chat")

    async def aclose(self) -> None:
        await self._client.aclose()
