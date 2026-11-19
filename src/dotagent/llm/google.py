"""Google provider: the Gemini models of a Google AI Studio key, over the Gemini API's OpenAI-compatible route.

``https://generativelanguage.googleapis.com/v1beta/openai`` speaks Chat Completions (text, images, tools, streaming,
``reasoning_effort``), so this connector is the chat wire with Gemini's own traits: a key from aistudio.google.com
serves its project's free tier, and a model is free only while that project stays inside the tier's limits
(docs/models.md). Ids are listed with a ``models/`` prefix, which is taken off: a reference reads
``google:gemini-3.1-flash-lite``.
"""

import asyncio

import httpx

from dotagent import __version__
from dotagent.config import GoogleConfig
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, chat_codec
from dotagent.llm.connector import get_json, retry_with_reset, stream_model_reply
from dotagent.llm.http import build_client, request_context
from dotagent.llm.provider_errors import classify_openai
from dotagent.llm.traits import ModelTraits, info_from_traits
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.llm.wire import get_objects, get_str

# What the endpoint lists beside the chat models: embeddings, pictures, speech, live audio, video, music, and the
# research and computer-use agents, which answer on routes of their own.
_NOT_CHAT: tuple[str, ...] = (
    "embedding",
    "image",
    "tts",
    "audio",
    "live",
    "transcribe",
    "computer-use",
    "robotics",
    "lyria",
    "nano-banana",
    "omni",
    "deep-research",
    "antigravity",
    "aqa",
    "decision",
)
# The route lists ids as "models/<id>"; the wire takes either, and a reference reads better without it.
_PREFIX: str = "models/"

# Gemini 3 and later label their thinking minimal, low, medium and high; 2.5 also offers "none" to switch it off, and
# an id from neither family is assumed to take the labels of the newest family.
_GEMINI_EFFORTS: tuple[str, ...] = ("minimal", "low", "medium", "high")
_GEMINI_25_EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high")


class TraitRule(FrozenModel):
    """Traits shared by every model whose id starts with ``prefix`` (the first matching rule wins)."""

    prefix: str
    traits: ModelTraits


def _gemini(context: int, max_output: int, efforts: tuple[str, ...]) -> ModelTraits:
    return ModelTraits(
        vision=True,
        context_length=context,
        window_source="published",
        max_output_tokens=max_output,
        efforts=efforts,
        reasoning=True,
        thinking_toggle=False,
        temperature=True,
    )


def _gemma(context: int, max_output: int) -> ModelTraits:
    """A Gemma model: text and tools, no thinking level, and images only on the models that take them (not sent)."""
    return ModelTraits(
        vision=False,
        context_length=context,
        window_source="published",
        max_output_tokens=max_output,
        efforts=(),
        reasoning=False,
        thinking_toggle=False,
        temperature=True,
    )


# Gemini 3 and later hold a million tokens of prompt and answer up to 65536, and so do 2.5's flash and pro; Gemma 4
# holds 262144.
RULES: tuple[TraitRule, ...] = (
    TraitRule(prefix="gemini-4", traits=_gemini(1_048_576, 65_536, _GEMINI_EFFORTS)),
    TraitRule(prefix="gemini-3", traits=_gemini(1_048_576, 65_536, _GEMINI_EFFORTS)),
    TraitRule(prefix="gemini-2.5-pro", traits=_gemini(1_048_576, 65_536, _GEMINI_25_EFFORTS)),
    TraitRule(prefix="gemini-2.5", traits=_gemini(1_048_576, 65_536, _GEMINI_25_EFFORTS)),
    TraitRule(prefix="gemini-", traits=_gemini(1_048_576, 65_536, _GEMINI_EFFORTS)),
    TraitRule(prefix="gemma-4", traits=_gemma(262_144, 32_768)),
    TraitRule(prefix="gemma-", traits=_gemma(131_072, 16_384)),
)
_UNKNOWN: ModelTraits = _gemini(128_000, 8_192, _GEMINI_EFFORTS).model_copy(update={"window_source": "assumed"})

_CHAT_OPTIONS: ChatOptions = ChatOptions(
    max_tokens_field="max_tokens",
    request_usage=True,
    replay_reasoning=False,
    cache_key_in_body=False,
    reasoning_object=False,
    replay_details=False,
)


def google_traits(model: str) -> ModelTraits:
    for rule in RULES:
        if model.startswith(rule.prefix):
            return rule.traits
    return _UNKNOWN


def is_chat_model(model: str) -> bool:
    """Whether an id the route lists is something the agent can chat with."""
    if not model.startswith(("gemini-", "gemma-")):
        return False
    return not any(marker in model.lower() for marker in _NOT_CHAT)


def bare_id(listed: str) -> str:
    """The id without the route's ``models/`` prefix."""
    return listed[len(_PREFIX) :] if listed.startswith(_PREFIX) else listed


class GoogleProvider:
    """Connector to the Gemini API's OpenAI-compatible route, on a Google AI Studio key."""

    def __init__(self, config: GoogleConfig, api_key: str) -> None:
        self._config: GoogleConfig = config
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": f"dot-agent/{__version__}",
        }
        self._slots: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent)
        self._policy: RetryPolicy = RetryPolicy(attempts=config.attempts, base_delay_s=1.0, max_delay_s=30.0)

    @property
    def name(self) -> str:
        return "google"

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    async def model_info(self, model: str) -> ModelInfo:
        return info_from_traits(model, google_traits(model), False)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        listed: JsonObject = await get_json(
            self._client, self._url("/models"), self._headers, classify_openai, self._policy, "google.models"
        )
        identifiers: set[str] = {bare_id(get_str(entry, "id")) for entry in get_objects(listed, "data")}
        chat_models: list[str] = sorted(model for model in identifiers if model and is_chat_model(model))
        return tuple(info_from_traits(model, google_traits(model), False) for model in chat_models)

    async def _stream(self, body: JsonObject, model: str, sink: DeltaSink) -> ModelReply:
        url: str = self._url("/chat/completions")
        payload: str = dumps_compact(body)
        context: JsonObject = request_context(url, body)

        async def attempt() -> ModelReply:
            return await stream_model_reply(
                self._client,
                url,
                payload,
                self._headers,
                context,
                classify_openai,
                chat_codec(classify_openai, _CHAT_OPTIONS),
                model,
                sink,
            )

        async with self._slots:
            return await retry_with_reset(attempt, sink, self._policy, "google.chat")

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        traits: ModelTraits = google_traits(request.model)
        origin: str = f"google:{request.model}"
        body: JsonObject = build_chat_body(request, traits, _CHAT_OPTIONS, origin)
        reply: ModelReply = await self._stream(body, request.model, sink)
        if not any(call.extra for call in reply.tool_calls):
            return reply
        # The thought signature goes back only to the model that wrote it: the calls remember where they came from.
        return reply.model_copy(
            update={"tool_calls": tuple(call.model_copy(update={"extra_origin": origin}) for call in reply.tool_calls)}
        )

    async def aclose(self) -> None:
        await self._client.aclose()
