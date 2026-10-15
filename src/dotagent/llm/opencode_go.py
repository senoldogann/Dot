"""OpenCode Go provider: one gateway that serves each model over Chat Completions, Responses or Messages.

Every model is valid on exactly one route, so the connector picks the route (and the matching codec, auth header
and reasoning labels) from a built-in catalog, then from the id's family prefix, and finally from the user's
``route_overrides``. Gateway behaviour the codecs depend on: it forces usage reporting on chat streams, appends one
cost/ping event after the provider's own end marker, and reads the last literal ``"stream"`` in the body.
"""

import asyncio
from collections.abc import Mapping

import httpx

from dotagent import __version__
from dotagent.config import GoRoute, OpenCodeGoConfig
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.codec_chat import ChatOptions, build_chat_body, chat_codec
from dotagent.llm.codec_messages import MessagesOptions, build_messages_body, messages_codec
from dotagent.llm.codec_responses import ResponsesOptions, build_responses_body, responses_codec
from dotagent.llm.connector import StreamCodec, get_json, retry_with_reset, stream_model_reply
from dotagent.llm.http import build_client, request_context
from dotagent.llm.provider_errors import classify_opencode
from dotagent.llm.traits import ModelTraits, info_from_traits
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.llm.wire import get_float, get_obj, get_objects, get_scalar_text, get_str

_ANTHROPIC_VERSION: str = "2023-06-01"
_USAGE_WINDOWS: tuple[str, ...] = ("rolling", "weekly", "monthly")

_CHAT_OPTIONS: ChatOptions = ChatOptions(
    max_tokens_field="max_tokens", request_usage=False, replay_reasoning=True, cache_key_in_body=False
)
# OpenAI models behind the gateway take full-size screenshots inline; the other Responses models get them hoisted.
_OPENAI_RESPONSES: ResponsesOptions = ResponsesOptions(image_detail="original", tool_output_images=True)
_OTHER_RESPONSES: ResponsesOptions = ResponsesOptions(image_detail="auto", tool_output_images=False)
# The gateway's messages route takes a plain thinking switch and the automatic cache mark (measured 2026-10-12: Claude
# Haiku 5.5 reads a repeated prompt from the cache only when it is marked, Qwen caches by itself and with the mark,
# MiniMax takes it); the format insists on an output limit, and this one is used when the request names none. A mark
# inside the conversation (item 237) was not tried on the gateway, so it gets none.
_MESSAGES_OPTIONS: MessagesOptions = MessagesOptions(
    adaptive_thinking=False, prompt_cache=True, settled_mark=False, default_max_tokens=32768
)
# OpenCode names the models it serves at no cost with this ending (Go's list on 2026-10-12: `longcat-2.5-preview-free`,
# `step-5-preview-free`).
_FREE_SUFFIX: str = "-free"


class GoModel(FrozenModel):
    route: GoRoute
    traits: ModelTraits


def _model(
    route: GoRoute,
    vision: bool,
    context: int,
    max_output: int,
    efforts: tuple[str, ...],
    thinking_toggle: bool,
    temperature: bool,
) -> GoModel:
    """A catalog entry. Every model on the gateway reasons; the arguments are the facts that differ between them:

    route, vision, context length, max output tokens, reasoning labels, thinking switch, temperature accepted.
    """
    return GoModel(
        route=route,
        traits=ModelTraits(
            vision=vision,
            context_length=context,
            window_source="published",
            max_output_tokens=max_output,
            efforts=efforts,
            reasoning=True,
            thinking_toggle=thinking_toggle,
            temperature=temperature,
        ),
    )


_NO_EFFORT: tuple[str, ...] = ()
_LMH: tuple[str, ...] = ("low", "medium", "high")
_GPT_LUNA: tuple[str, ...] = ("none", "low", "medium", "high", "xhigh", "max")

# The reasoning labels follow what OpenCode's own client sends per family (it sends none for GLM, Kimi, MiniMax
# and Qwen on the chat route), so a label is never invented for a model the official client treats as having none.
CATALOG: dict[str, GoModel] = {
    "glm-5.3-flash": _model("chat", True, 1_000_000, 131_072, _NO_EFFORT, False, True),
    "glm-5.3": _model("chat", False, 1_000_000, 131_072, _NO_EFFORT, False, True),
    "glm-5.2": _model("chat", False, 1_000_000, 131_072, ("high", "max"), False, True),
    "kimi-k3": _model("chat", True, 1_048_576, 131_072, _NO_EFFORT, False, False),
    "kimi-k2.7-code": _model("chat", True, 262_144, 262_144, _NO_EFFORT, False, False),
    "kimi-k2.6": _model("chat", True, 262_144, 65_536, _NO_EFFORT, False, False),
    "longcat-2.0": _model("chat", False, 1_000_000, 131_072, _LMH, False, True),
    "longcat-2.5-preview-free": _model("chat", True, 1_000_000, 131_072, _LMH, False, True),
    # DeepSeek's reasoning labels are not honoured on the chat route (measured 2026-10-15, item 258: the reasoning tokens of
    # one prompt were the same at low, high, max and a made-up label; GLM 5.2 at max thought three times longer than at
    # high), so no level is offered for it: a choice that changes nothing would be a lie in the menu.
    "deepseek-v4.1-flash": _model("chat", True, 1_000_000, 384_000, _NO_EFFORT, False, True),
    "deepseek-v4-flash": _model("chat", False, 1_000_000, 384_000, _NO_EFFORT, False, True),
    "deepseek-v4-pro": _model("chat", False, 1_000_000, 384_000, _NO_EFFORT, False, True),
    "deepseek-v4-flash-vision-exp": _model("chat", True, 1_000_000, 384_000, _NO_EFFORT, False, True),
    "mimo-v2.6-flash": _model("chat", True, 1_048_576, 131_072, _LMH, False, True),
    "mimo-v2.6-pro": _model("chat", True, 1_048_576, 131_072, _LMH, False, True),
    "mimo-v2.5": _model("chat", True, 1_000_000, 128_000, _LMH, False, True),
    "mimo-v2.5-pro": _model("chat", False, 1_048_576, 128_000, _LMH, False, True),
    "hy4-preview": _model("chat", False, 1_024_000, 64_000, ("none", "high"), False, True),
    "hy3": _model("chat", False, 256_000, 128_000, ("none", "low", "high"), False, True),
    "space-bunny": _model("chat", True, 1_048_576, 524_288, _LMH, False, True),
    "step-5-preview-free": _model("chat", True, 1_000_000, 65_536, _NO_EFFORT, False, True),
    "grok-4.7": _model("responses", True, 500_000, 500_000, ("low", "medium", "high", "xhigh"), False, True),
    "grok-4.6": _model("responses", True, 500_000, 500_000, ("low", "medium", "high", "xhigh"), False, True),
    "gpt-6-luna": _model("responses", True, 1_050_000, 128_000, _GPT_LUNA, False, False),
    "gpt-5.6-luna": _model("responses", True, 1_050_000, 128_000, _GPT_LUNA, False, False),
    "muse-spark-1.3-contributor": _model(
        "responses", True, 1_048_576, 131_072, ("minimal", "low", "medium", "high", "xhigh"), False, True
    ),
    "muse-spark-1.2-contributor": _model(
        "responses", True, 1_048_576, 131_072, ("minimal", "low", "medium", "high", "xhigh"), False, True
    ),
    "minimax-m3": _model("messages", False, 1_000_000, 131_072, _NO_EFFORT, True, True),
    "minimax-m2.7": _model("messages", False, 204_800, 131_072, _NO_EFFORT, False, True),
    "qwen3.8-max": _model("messages", True, 1_000_000, 131_072, _NO_EFFORT, False, True),
    "qwen3.8-flash": _model("messages", True, 1_000_000, 131_072, _NO_EFFORT, False, True),
    "qwen3.7-plus": _model("messages", True, 1_000_000, 65_536, _NO_EFFORT, False, True),
    "qwen3.7-max": _model("messages", False, 1_000_000, 65_536, _NO_EFFORT, False, True),
    "qwen3.6-plus": _model("messages", True, 1_000_000, 65_536, _NO_EFFORT, False, True),
    # Claude is served on the Messages route only (the chat route answers 400, measured 2026-10-12); its thinking is a
    # switch here, adaptive when on, and it takes no temperature.
    "claude-haiku-5-5": _model("messages", True, 1_000_000, 128_000, _NO_EFFORT, True, False),
}

# A model the catalog does not know: assume nothing optional (no images, no effort labels, no thinking switch).
_UNKNOWN_TRAITS: ModelTraits = ModelTraits(
    vision=False,
    context_length=200_000,
    window_source="assumed",
    max_output_tokens=32_768,
    efforts=(),
    reasoning=True,
    thinking_toggle=False,
    temperature=True,
)
_PREFIX_ROUTES: tuple[tuple[str, GoRoute], ...] = (
    ("grok-", "responses"),
    ("gpt-", "responses"),
    ("muse-", "responses"),
    ("minimax-", "messages"),
    ("qwen", "messages"),
    ("claude-", "messages"),
)


def route_of(model: str, overrides: Mapping[str, GoRoute]) -> GoRoute:
    """The route a model is served on: the user's override, then the catalog, then the id's family prefix."""
    override: GoRoute | None = overrides.get(model)
    if override is not None:
        return override
    known: GoModel | None = CATALOG.get(model)
    if known is not None:
        return known.route
    for prefix, route in _PREFIX_ROUTES:
        if model.startswith(prefix):
            return route
    return "chat"


def go_free(model: str) -> bool:
    """Whether the gateway serves ``model`` at no cost."""
    return model.endswith(_FREE_SUFFIX)


def go_traits(model: str) -> ModelTraits:
    known: GoModel | None = CATALOG.get(model)
    return known.traits if known is not None else _UNKNOWN_TRAITS


def route_path(route: GoRoute) -> str:
    match route:
        case "chat":
            return "/chat/completions"
        case "responses":
            return "/responses"
        case "messages":
            return "/messages"


def route_headers(route: GoRoute, api_key: str, session_key: str) -> dict[str, str]:
    """Request headers: the Messages route authenticates with ``x-api-key``, the others with a Bearer token."""
    headers: dict[str, str] = {"Content-Type": "application/json", "User-Agent": f"dot-agent/{__version__}"}
    if session_key:
        # The gateway keys sticky routing and prompt-cache affinity on this header.
        headers["x-opencode-session"] = session_key
    if route == "messages":
        return {**headers, "x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION}
    return {**headers, "Authorization": f"Bearer {api_key}"}


class GoUsageWindow(FrozenModel):
    """One quota window of the subscription (``rolling``, ``weekly`` or ``monthly``)."""

    name: str
    status: str
    percent: float
    resets_at: str


class GoUsage(FrozenModel):
    windows: tuple[GoUsageWindow, ...]


def parse_usage(payload: JsonObject) -> GoUsage:
    usage: JsonObject = get_obj(payload, "usage")
    windows: list[GoUsageWindow] = []
    for name in _USAGE_WINDOWS:
        entry: JsonObject = get_obj(usage, name)
        if entry:
            windows.append(
                GoUsageWindow(
                    name=name,
                    status=get_str(entry, "status"),
                    percent=get_float(entry, "percent"),
                    resets_at=get_scalar_text(entry, "resetsAt"),
                )
            )
    return GoUsage(windows=tuple(windows))


class OpenCodeGoProvider:
    """Connector to the OpenCode Go gateway."""

    def __init__(self, config: OpenCodeGoConfig, api_key: str) -> None:
        self._config: OpenCodeGoConfig = config
        self._api_key: str = api_key
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._slots: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent)
        self._policy: RetryPolicy = RetryPolicy(attempts=config.attempts, base_delay_s=1.0, max_delay_s=30.0)

    @property
    def name(self) -> str:
        return "opencode_go"

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    def _listing_headers(self) -> dict[str, str]:
        return route_headers("chat", self._api_key, "")

    async def model_info(self, model: str) -> ModelInfo:
        return info_from_traits(model, go_traits(model), go_free(model))

    async def list_models(self) -> tuple[ModelInfo, ...]:
        listed: JsonObject = await get_json(
            self._client,
            self._url("/models"),
            self._listing_headers(),
            classify_opencode,
            self._policy,
            "opencode_go.models",
        )
        identifiers: list[str] = sorted({get_str(entry, "id") for entry in get_objects(listed, "data")} - {""})
        return tuple(info_from_traits(model, go_traits(model), go_free(model)) for model in identifiers)

    async def usage(self) -> GoUsage:
        """The subscription's quota windows (needs an active Go subscription; otherwise a 403 is raised)."""
        payload: JsonObject = await get_json(
            self._client,
            self._url("/usage"),
            self._listing_headers(),
            classify_opencode,
            self._policy,
            "opencode_go.usage",
        )
        return parse_usage(payload)

    async def _stream[S](
        self,
        route: GoRoute,
        body: JsonObject,
        codec: StreamCodec[S],
        request: ChatRequest,
        sink: DeltaSink,
    ) -> ModelReply:
        url: str = self._url(route_path(route))
        payload: str = dumps_compact(body)
        headers: dict[str, str] = route_headers(route, self._api_key, request.session_key)
        context: JsonObject = request_context(url, body)

        async def attempt() -> ModelReply:
            return await stream_model_reply(
                self._client, url, payload, headers, context, classify_opencode, codec, request.model, sink
            )

        async with self._slots:
            return await retry_with_reset(attempt, sink, self._policy, f"opencode_go.{route}")

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        route: GoRoute = route_of(request.model, self._config.route_overrides)
        traits: ModelTraits = go_traits(request.model)
        origin: str = f"opencode_go:{request.model}"
        match route:
            case "chat":
                chat_body: JsonObject = build_chat_body(request, traits, _CHAT_OPTIONS)
                return await self._stream(route, chat_body, chat_codec(classify_opencode), request, sink)
            case "responses":
                options: ResponsesOptions = _OPENAI_RESPONSES if request.model.startswith("gpt-") else _OTHER_RESPONSES
                responses_body: JsonObject = build_responses_body(request, traits, origin, options)
                return await self._stream(route, responses_body, responses_codec(classify_opencode), request, sink)
            case "messages":
                messages_body: JsonObject = build_messages_body(request, traits, origin, _MESSAGES_OPTIONS)
                return await self._stream(route, messages_body, messages_codec(classify_opencode), request, sink)

    async def aclose(self) -> None:
        await self._client.aclose()
