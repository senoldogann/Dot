"""Anthropic provider: the Claude models over the Messages API (``POST /messages``), streamed over SSE.

The body and the stream are the Messages codec that OpenCode Go's messages route uses as well; what only Anthropic
takes (adaptive thinking with the reasoning level as ``output_config.effort``, and the prompt cache) is switched on
through :class:`MessagesOptions`. What a model can do is read from the Models API, so a new Claude model needs no change
here.
"""

import asyncio
from urllib.parse import quote, urlencode

import httpx

from dotagent import __version__
from dotagent.config import AnthropicConfig
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import JsonObject
from dotagent.llm.codec_messages import MessagesOptions, MessagesState, build_messages_body, messages_codec
from dotagent.llm.connector import StreamCodec, get_json, retry_with_reset, stream_model_reply
from dotagent.llm.http import build_client, request_context
from dotagent.llm.provider_errors import classify_anthropic
from dotagent.llm.traits import ModelTraits, info_from_traits
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.llm.wire import get_int, get_obj, get_objects, get_str

ANTHROPIC_VERSION: str = "2023-06-01"
# Lets a request have a replayed thinking block whose earlier context changed dropped instead of refused: a run edits
# its history when it drops an old screenshot, and the next run of a chat starts with a new system prompt.
_THINKING_BINDING_BETA: str = "thinking-binding-controls-2026-08-01"
# The largest page of the model listing.
_PAGE_SIZE: int = 1000
_OPTIONS: MessagesOptions = MessagesOptions(adaptive_thinking=True, prompt_cache=True, default_max_tokens=None)
# The levels of the effort parameter, lowest first.
_EFFORT_LABELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
# The limits of a model whose entry names none: the smallest window of the current models, and the output limit
# OpenCode Go's messages route uses.
_DEFAULT_CONTEXT: int = 200_000
_DEFAULT_OUTPUT: int = 32_768


def _request_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": f"dot-agent/{__version__}",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }


def _supported(capabilities: JsonObject, key: str) -> bool:
    return get_obj(capabilities, key).get("supported") is True


def claude_traits(entry: JsonObject) -> ModelTraits:
    """A model's traits from its entry in the Models API (``GET /models``).

    A model that takes adaptive thinking reasons; its levels are the efforts it supports, above ``off`` when it lets
    thinking be turned off. Only a model without adaptive thinking gets a temperature: the newer ones refuse any but
    the default, and none takes one while it thinks. An entry without capabilities gets traits that send neither.
    """
    capabilities: JsonObject = get_obj(entry, "capabilities")
    types: JsonObject = get_obj(get_obj(capabilities, "thinking"), "types")
    adaptive: bool = _supported(types, "adaptive")
    off: tuple[str, ...] = ("off",) if _supported(types, "disabled") else ()
    effort: JsonObject = get_obj(capabilities, "effort")
    levels: tuple[str, ...] = (
        tuple(label for label in _EFFORT_LABELS if _supported(effort, label))
        if _supported(capabilities, "effort")
        else ()
    )
    return ModelTraits(
        vision=_supported(capabilities, "image_input"),
        context_length=get_int(entry, "max_input_tokens") or _DEFAULT_CONTEXT,
        max_output_tokens=get_int(entry, "max_tokens") or _DEFAULT_OUTPUT,
        efforts=(*off, *levels) if adaptive else (),
        reasoning=adaptive,
        thinking_toggle=False,
        temperature=bool(capabilities) and not adaptive,
    )


class AnthropicProvider:
    """Connector to the Anthropic API. A model's traits are read from the Models API once and kept."""

    def __init__(self, config: AnthropicConfig, api_key: str) -> None:
        self._config: AnthropicConfig = config
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._headers: dict[str, str] = _request_headers(api_key)
        self._message_headers: dict[str, str] = {**self._headers, "anthropic-beta": _THINKING_BINDING_BETA}
        self._traits_cache: dict[str, ModelTraits] = {}
        self._slots: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent)
        self._policy: RetryPolicy = RetryPolicy(attempts=config.attempts, base_delay_s=1.0, max_delay_s=30.0)

    @property
    def name(self) -> str:
        return "anthropic"

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    async def _get_json(self, path: str, label: str) -> JsonObject:
        return await get_json(self._client, self._url(path), self._headers, classify_anthropic, self._policy, label)

    async def _traits(self, model: str) -> ModelTraits:
        cached: ModelTraits | None = self._traits_cache.get(model)
        if cached is not None:
            return cached
        entry: JsonObject = await self._get_json(f"/models/{quote(model, safe='')}", "anthropic.model")
        traits: ModelTraits = claude_traits(entry)
        self._traits_cache[model] = traits
        return traits

    async def model_info(self, model: str) -> ModelInfo:
        return info_from_traits(model, await self._traits(model), False)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        """Every model the key can use, read page by page; their traits are kept for the requests that follow."""
        entries: dict[str, JsonObject] = {}
        after: str = ""
        while True:
            query: dict[str, str] = {"limit": str(_PAGE_SIZE), **({"after_id": after} if after else {})}
            page: JsonObject = await self._get_json(f"/models?{urlencode(query)}", "anthropic.models")
            entries |= {get_str(entry, "id"): entry for entry in get_objects(page, "data") if get_str(entry, "id")}
            if page.get("has_more") is not True:
                break
            last: str = get_str(page, "last_id")
            if not last or last == after:
                raise ProviderProtocolError(
                    "the model listing has more pages but does not say where the next one starts",
                    {"after_id": after, "last_id": last},
                )
            after = last
        self._traits_cache |= {model: claude_traits(entry) for model, entry in entries.items()}
        return tuple(info_from_traits(model, self._traits_cache[model], False) for model in sorted(entries))

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        traits: ModelTraits = await self._traits(request.model)
        body: JsonObject = build_messages_body(request, traits, f"anthropic:{request.model}", _OPTIONS)
        url: str = self._url("/messages")
        payload: str = dumps_compact(body)
        context: JsonObject = request_context(url, body)
        codec: StreamCodec[MessagesState] = messages_codec(classify_anthropic)

        async def attempt() -> ModelReply:
            return await stream_model_reply(
                self._client,
                url,
                payload,
                self._message_headers,
                context,
                classify_anthropic,
                codec,
                request.model,
                sink,
            )

        async with self._slots:
            return await retry_with_reset(attempt, sink, self._policy, "anthropic.messages")

    async def aclose(self) -> None:
        await self._client.aclose()
