"""Custom providers (``[providers.custom.<name>]``, docs/models.md): a server the owner added that speaks one of the
wires Dot already speaks, OpenAI's Chat Completions, OpenAI's Responses or Anthropic's Messages, through the same codecs
and the same streaming plumbing as Dot's own connectors. Nothing of a particular service is known here: what a model can
do is what the configuration says of it (``CustomModel``), and a model the provider lists that the configuration does
not name is assumed to call tools, read no images and hold :data:`ASSUMED_CONTEXT` tokens.

The key goes to the provider's ``base_url`` alone: the client follows no redirect, and the configuration takes no
address but https or this Mac's own http (``config._custom_base_url``)."""

import asyncio

import httpx

from dotagent import __version__
from dotagent.config import CustomModel, CustomProtocol, CustomProviderConfig
from dotagent.core.errors import ConfigError, DotError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, chat_codec
from dotagent.llm.codec_messages import MessagesOptions, build_messages_body, messages_codec
from dotagent.llm.codec_responses import ResponsesOptions, build_responses_body, responses_codec
from dotagent.llm.connector import StreamCodec, get_json, retry_with_reset, stream_model_reply
from dotagent.llm.http import build_client, request_context
from dotagent.llm.provider_errors import StatusClassifier, classify_anthropic, classify_openai
from dotagent.llm.traits import ModelTraits, info_from_traits
from dotagent.llm.types import (
    ChatRequest,
    DeltaSink,
    ModelInfo,
    ModelReply,
    StreamDelta,
    system_message,
    user_message,
)
from dotagent.llm.wire import get_objects, get_str

_ANTHROPIC_VERSION: str = "2023-06-01"
# The window and output limit of a model the provider lists and the configuration does not describe.
ASSUMED_CONTEXT: int = 128_000
_ASSUMED_OUTPUT: int = 8_192
# The plain Chat Completions most OpenAI-compatible servers take: usage asked for in the stream, nothing of a gateway's.
_CHAT_OPTIONS: ChatOptions = ChatOptions(
    max_tokens_field="max_tokens",
    request_usage=True,
    replay_reasoning=False,
    cache_key_in_body=False,
    reasoning_object=False,
    replay_details=False,
)
# How long a check of a provider may take, its retries included, and how much of its answer is told.
CHECK_TIMEOUT_S: float = 60.0
_SAID_CHARS: int = 200
_RESPONSES_OPTIONS: ResponsesOptions = ResponsesOptions(image_detail="auto", tool_output_images=False)


def messages_options(max_output: int) -> MessagesOptions:
    """The Messages format as the protocol defines it: the automatic cache mark on a conversation, a thinking switch
    rather than Anthropic's adaptive thinking, and the model's own output limit when a request names none."""
    return MessagesOptions(
        adaptive_thinking=False,
        prompt_cache=True,
        settled_mark=False,
        default_max_tokens=max_output,
        long_ttl=False,
    )


def model_traits(model: CustomModel) -> ModelTraits:
    """What the configuration says ``model`` can do, as the codecs read it; a model that thinks without labels on the
    Messages wire takes the plain switch."""
    return ModelTraits(
        vision=model.vision,
        context_length=model.context,
        window_source="published",
        max_output_tokens=model.max_output,
        efforts=model.efforts,
        reasoning=model.thinking,
        thinking_toggle=model.thinking and not model.efforts,
        temperature=True,
    )


def assumed_model(model: str) -> CustomModel:
    """A model the provider lists that the configuration does not describe: tools, no images, no thinking."""
    return CustomModel(
        id=model,
        tools=True,
        vision=False,
        thinking=False,
        efforts=(),
        context=ASSUMED_CONTEXT,
        max_output=_ASSUMED_OUTPUT,
    )


def model_info(model: CustomModel, assumed: bool) -> ModelInfo:
    """The model as the pickers and the runs read it; ``assumed`` for one the configuration does not describe."""
    traits: ModelTraits = model_traits(model)
    if assumed:
        traits = traits.model_copy(update={"window_source": "assumed"})
    return info_from_traits(model.id, traits, False).model_copy(update={"tools": model.tools})


def protocol_path(protocol: CustomProtocol) -> str:
    match protocol:
        case "chat":
            return "/chat/completions"
        case "responses":
            return "/responses"
        case "messages":
            return "/messages"


def protocol_headers(config: CustomProviderConfig, api_key: str | None) -> dict[str, str]:
    """The headers of every call: the configured ones, then Dot's own, then the key the protocol's way (``x-api-key``
    on Messages, a Bearer token on the others), when the provider takes one."""
    headers: dict[str, str] = {
        **config.headers,
        "Content-Type": "application/json",
        "User-Agent": f"dot-agent/{__version__}",
    }
    if config.protocol == "messages":
        headers["anthropic-version"] = _ANTHROPIC_VERSION
    if api_key is None:
        return headers
    if config.protocol == "messages":
        return {**headers, "x-api-key": api_key}
    return {**headers, "Authorization": f"Bearer {api_key}"}


def classifier(protocol: CustomProtocol) -> StatusClassifier:
    """How an error answer is read: Anthropic's way on Messages, OpenAI's on the others."""
    return classify_anthropic if protocol == "messages" else classify_openai


def listed_ids(listed: JsonObject) -> list[str]:
    """The model ids of a ``GET /models`` answer (``{"data": [{"id": ...}]}``, OpenAI's and Anthropic's shape)."""
    return sorted({get_str(entry, "id") for entry in get_objects(listed, "data")} - {""})


def test_request(model: str) -> ChatRequest:
    """The one small call the owner's Test makes: a word asked, a few tokens allowed."""
    return ChatRequest(
        model=model,
        messages=(system_message("Answer with the single word OK."), user_message("Say OK.", ())),
        tools=(),
        temperature=None,
        max_output_tokens=16,
        effort="off",
        speed="standard",
        session_key="",
        cache_mark=None,
        prompt_reuse="single",
        window=None,
    )


class CallCheck(FrozenModel):
    """What one small real call to a provider said: whether it answered, and how, in words."""

    ok: bool
    detail: str


async def _discard(delta: StreamDelta) -> None:
    return None


async def check_call(name: str, config: CustomProviderConfig, key: str | None, model: str) -> CallCheck:
    """One small real call to the provider ``name`` as ``config`` holds it, with ``key``: its list of models for
    ``model`` ``""``, else a word asked of ``model``. A failure is said, not raised: it is the answer asked for."""
    provider: CustomProvider = CustomProvider(name, config, key)
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            if model == "":
                listed: list[str] = await provider.listed()
                return CallCheck(ok=True, detail=f"it lists {len(listed)} models")
            reply: ModelReply = await provider.complete(test_request(model), _discard)
    except TimeoutError:
        return CallCheck(ok=False, detail=f"no answer within {CHECK_TIMEOUT_S:g}s")
    except DotError as error:
        return CallCheck(ok=False, detail=error.message)
    finally:
        await provider.aclose()
    said: str = " ".join(reply.text.split())[:_SAID_CHARS]
    return CallCheck(
        ok=True,
        detail=f'{model} answered "{said}" ({reply.usage.input_tokens} tokens in, {reply.usage.output_tokens} out)',
    )


class CustomProvider:
    """Connector to one custom provider, named ``name`` in model references."""

    def __init__(self, name: str, config: CustomProviderConfig, api_key: str | None) -> None:
        self._name: str = name
        self._config: CustomProviderConfig = config
        self._headers: dict[str, str] = protocol_headers(config, api_key)
        self._classify: StatusClassifier = classifier(config.protocol)
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._slots: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent)
        self._policy: RetryPolicy = RetryPolicy(attempts=config.attempts, base_delay_s=1.0, max_delay_s=30.0)
        self._models: dict[str, CustomModel] = {model.id: model for model in config.models}

    @property
    def name(self) -> str:
        return self._name

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    def _model(self, model: str) -> CustomModel:
        known: CustomModel | None = self._models.get(model)
        if known is not None:
            return known
        if self._config.list_models:
            return assumed_model(model)
        raise ConfigError(
            f"the custom provider {self._name} lists no model {model}",
            {"provider": self._name, "model": model, "models": list(self._models)},
        )

    async def model_info(self, model: str) -> ModelInfo:
        return model_info(self._model(model), model not in self._models)

    async def listed(self) -> list[str]:
        """The ids the provider's ``GET /models`` lists: one call."""
        answer: JsonObject = await get_json(
            self._client, self._url("/models"), self._headers, self._classify, self._policy, f"{self._name}.models"
        )
        return listed_ids(answer)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        """The configured models, and with ``list_models`` those the provider lists beside them."""
        configured: list[ModelInfo] = [model_info(model, False) for model in self._config.models]
        if not self._config.list_models:
            return tuple(configured)
        more: list[ModelInfo] = [
            model_info(assumed_model(model), True) for model in await self.listed() if model not in self._models
        ]
        return (*configured, *more)

    async def _stream[S](self, body: JsonObject, codec: StreamCodec[S], model: str, sink: DeltaSink) -> ModelReply:
        url: str = self._url(protocol_path(self._config.protocol))
        payload: str = dumps_compact(body)
        context: JsonObject = request_context(url, body)

        async def attempt() -> ModelReply:
            return await stream_model_reply(
                self._client, url, payload, self._headers, context, self._classify, codec, model, sink
            )

        async with self._slots:
            return await retry_with_reset(attempt, sink, self._policy, f"{self._name}.{self._config.protocol}")

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        model: CustomModel = self._model(request.model)
        traits: ModelTraits = model_traits(model)
        origin: str = f"{self._name}:{request.model}"
        match self._config.protocol:
            case "chat":
                chat_body: JsonObject = build_chat_body(request, traits, _CHAT_OPTIONS, origin)
                return await self._stream(chat_body, chat_codec(self._classify, _CHAT_OPTIONS), request.model, sink)
            case "responses":
                responses_body: JsonObject = build_responses_body(request, traits, origin, _RESPONSES_OPTIONS)
                return await self._stream(responses_body, responses_codec(self._classify), request.model, sink)
            case "messages":
                options: MessagesOptions = messages_options(model.max_output)
                messages_body: JsonObject = build_messages_body(request, traits, origin, options)
                return await self._stream(messages_body, messages_codec(self._classify), request.model, sink)

    async def aclose(self) -> None:
        await self._client.aclose()
