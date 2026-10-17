"""OpenAI provider: the Responses API (default) or Chat Completions, streamed over SSE.

The same connector also drives any server that speaks OpenAI's wire format (set ``base_url``), for example the
OpenAI-compatible routes of a local Ollama.
"""

import asyncio

import httpx

from dotagent import __version__
from dotagent.config import OpenAIConfig
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, chat_codec
from dotagent.llm.codec_responses import ResponsesOptions, build_responses_body, responses_codec
from dotagent.llm.connector import StreamCodec, get_json, retry_with_reset, stream_model_reply
from dotagent.llm.http import build_client, request_context
from dotagent.llm.provider_errors import classify_openai
from dotagent.llm.traits import ModelTraits, info_from_traits
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.llm.wire import get_objects, get_str

_SOL_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
_LUNA_EFFORTS: tuple[str, ...] = ("none", "low", "medium", "high", "xhigh", "max")
_GPT5_EFFORTS: tuple[str, ...] = ("none", "low", "medium", "high", "xhigh")
_CLASSIC_EFFORTS: tuple[str, ...] = ("low", "medium", "high")

# Hosted ids that are not chat models (OpenAI's /models lists everything it serves).
_NOT_CHAT: tuple[str, ...] = (
    "audio",
    "realtime",
    "transcribe",
    "tts",
    "image",
    "embedding",
    "instruct",
    "moderation",
    "whisper",
    "dall-e",
    "sora",
)

_RESPONSES_OPTIONS: ResponsesOptions = ResponsesOptions(image_detail="original", tool_output_images=True)
_CHAT_OPTIONS: ChatOptions = ChatOptions(
    max_tokens_field="max_completion_tokens",
    request_usage=True,
    replay_reasoning=False,
    cache_key_in_body=True,
    reasoning_object=False,
    replay_details=False,
)


class TraitRule(FrozenModel):
    """Traits shared by every model whose id starts with ``prefix`` (the first matching rule wins)."""

    prefix: str
    traits: ModelTraits


def _reasoning(context: int, max_output: int, efforts: tuple[str, ...], vision: bool) -> ModelTraits:
    return ModelTraits(
        vision=vision,
        context_length=context,
        window_source="published",
        max_output_tokens=max_output,
        efforts=efforts,
        reasoning=True,
        thinking_toggle=False,
        temperature=False,
    )


def _plain(context: int, max_output: int, vision: bool) -> ModelTraits:
    return ModelTraits(
        vision=vision,
        context_length=context,
        window_source="published",
        max_output_tokens=max_output,
        efforts=(),
        reasoning=False,
        thinking_toggle=False,
        temperature=True,
    )


# Facts come from OpenAI's model pages (the windows of GPT-5.1 and later read again 2026-10-14, item 221) and are kept
# conservative for older families: a context or output limit that is too small only makes the agent compact earlier,
# while an effort label a model rejects fails the call.
RULES: tuple[TraitRule, ...] = (
    TraitRule(prefix="gpt-6-luna", traits=_reasoning(1_050_000, 128_000, _LUNA_EFFORTS, True)),
    TraitRule(prefix="gpt-5.6-luna", traits=_reasoning(1_050_000, 128_000, _LUNA_EFFORTS, True)),
    TraitRule(prefix="gpt-6", traits=_reasoning(1_050_000, 128_000, _SOL_EFFORTS, True)),
    TraitRule(prefix="gpt-5.6", traits=_reasoning(1_050_000, 128_000, _SOL_EFFORTS, True)),
    # GPT-5.4 and 5.5 hold 1.05 M tokens, the mini and the nano of 5.4 400 k; the chat models of 5.1 to 5.3 hold 128 k,
    # as gpt-5-chat does.
    TraitRule(prefix="gpt-5.4-mini", traits=_reasoning(400_000, 128_000, _GPT5_EFFORTS, True)),
    TraitRule(prefix="gpt-5.4-nano", traits=_reasoning(400_000, 128_000, _GPT5_EFFORTS, True)),
    TraitRule(prefix="gpt-5.4", traits=_reasoning(1_050_000, 128_000, _GPT5_EFFORTS, True)),
    TraitRule(prefix="gpt-5.5", traits=_reasoning(1_050_000, 128_000, _GPT5_EFFORTS, True)),
    TraitRule(prefix="gpt-5.1-chat", traits=_reasoning(128_000, 16_384, _GPT5_EFFORTS, True)),
    TraitRule(prefix="gpt-5.2-chat", traits=_reasoning(128_000, 16_384, _GPT5_EFFORTS, True)),
    TraitRule(prefix="gpt-5.3-chat", traits=_reasoning(128_000, 16_384, _GPT5_EFFORTS, True)),
    TraitRule(prefix="gpt-5-chat", traits=_plain(128_000, 16_384, True)),
    TraitRule(prefix="gpt-5.", traits=_reasoning(400_000, 128_000, _GPT5_EFFORTS, True)),
    TraitRule(prefix="gpt-5", traits=_reasoning(400_000, 128_000, _CLASSIC_EFFORTS, True)),
    TraitRule(prefix="gpt-oss", traits=_reasoning(131_072, 32_768, _CLASSIC_EFFORTS, False)),
    TraitRule(prefix="gpt-4.1", traits=_plain(1_047_576, 32_768, True)),
    TraitRule(prefix="gpt-4o", traits=_plain(128_000, 16_384, True)),
    TraitRule(prefix="gpt-4-turbo-preview", traits=_plain(128_000, 4_096, False)),
    TraitRule(prefix="gpt-4-turbo", traits=_plain(128_000, 4_096, True)),
    TraitRule(prefix="gpt-4-1106", traits=_plain(128_000, 4_096, False)),
    TraitRule(prefix="gpt-4-0125", traits=_plain(128_000, 4_096, False)),
    TraitRule(prefix="gpt-4", traits=_plain(8_192, 4_096, False)),
    TraitRule(prefix="gpt-3.5", traits=_plain(16_385, 4_096, False)),
    TraitRule(prefix="o1", traits=_reasoning(200_000, 100_000, _CLASSIC_EFFORTS, True)),
    TraitRule(prefix="o3", traits=_reasoning(200_000, 100_000, _CLASSIC_EFFORTS, True)),
    TraitRule(prefix="o4", traits=_reasoning(200_000, 100_000, _CLASSIC_EFFORTS, True)),
)
# An unknown "gpt-" id is assumed to be a newer reasoning model; any other unknown id a plain text model.
_UNKNOWN_GPT: ModelTraits = _reasoning(128_000, 16_384, _CLASSIC_EFFORTS, True).model_copy(
    update={"window_source": "assumed"}
)
_UNKNOWN_PLAIN: ModelTraits = _plain(128_000, 8_192, False).model_copy(update={"window_source": "assumed"})


def openai_traits(model: str) -> ModelTraits:
    for rule in RULES:
        if model.startswith(rule.prefix):
            return rule.traits
    return _UNKNOWN_GPT if model.startswith("gpt-") else _UNKNOWN_PLAIN


def is_chat_model(model: str) -> bool:
    """Whether a hosted id is something the agent can chat with (a known family, minus audio/image/embedding ids)."""
    if any(marker in model for marker in _NOT_CHAT):
        return False
    return model.startswith("gpt-") or any(model.startswith(rule.prefix) for rule in RULES)


def request_headers(api_key: str, organization: str | None, project: str | None) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"dot-agent/{__version__}",
    }
    if organization:
        headers["OpenAI-Organization"] = organization
    if project:
        headers["OpenAI-Project"] = project
    return headers


class OpenAIProvider:
    """Connector to the OpenAI API (or a server that mimics it)."""

    def __init__(self, config: OpenAIConfig, api_key: str, organization: str | None, project: str | None) -> None:
        self._config: OpenAIConfig = config
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._headers: dict[str, str] = request_headers(api_key, organization, project)
        self._slots: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent)
        self._policy: RetryPolicy = RetryPolicy(attempts=config.attempts, base_delay_s=1.0, max_delay_s=30.0)

    @property
    def name(self) -> str:
        return "openai"

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    async def model_info(self, model: str) -> ModelInfo:
        return info_from_traits(model, openai_traits(model), False)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        listed: JsonObject = await get_json(
            self._client, self._url("/models"), self._headers, classify_openai, self._policy, "openai.models"
        )
        identifiers: set[str] = {get_str(entry, "id") for entry in get_objects(listed, "data")}
        chat_models: list[str] = sorted(model for model in identifiers if model and is_chat_model(model))
        return tuple(info_from_traits(model, openai_traits(model), False) for model in chat_models)

    async def _stream[S](
        self, path: str, body: JsonObject, codec: StreamCodec[S], model: str, sink: DeltaSink, label: str
    ) -> ModelReply:
        url: str = self._url(path)
        payload: str = dumps_compact(body)
        context: JsonObject = request_context(url, body)

        async def attempt() -> ModelReply:
            return await stream_model_reply(
                self._client, url, payload, self._headers, context, classify_openai, codec, model, sink
            )

        async with self._slots:
            return await retry_with_reset(attempt, sink, self._policy, label)

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        traits: ModelTraits = openai_traits(request.model)
        if self._config.wire == "responses":
            origin: str = f"openai:{request.model}"
            body: JsonObject = build_responses_body(request, traits, origin, _RESPONSES_OPTIONS)
            return await self._stream(
                "/responses", body, responses_codec(classify_openai), request.model, sink, "openai.responses"
            )
        chat_body: JsonObject = build_chat_body(request, traits, _CHAT_OPTIONS, f"openai:{request.model}")
        return await self._stream(
            "/chat/completions",
            chat_body,
            chat_codec(classify_openai, _CHAT_OPTIONS),
            request.model,
            sink,
            "openai.chat",
        )

    async def aclose(self) -> None:
        await self._client.aclose()
