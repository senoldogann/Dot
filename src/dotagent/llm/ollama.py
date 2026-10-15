"""Ollama provider: native ``/api/chat`` with NDJSON streaming, tools, thinking and vision.

Works against a local server (``http://localhost:11434``; cloud models such as ``gpt-oss:120b-cloud``
are proxied through it once the user is signed in) and directly against ``https://ollama.com``
with an API key.
"""

import asyncio
import ipaddress
import json

import httpx
from pydantic import JsonValue

from dotagent.config import OllamaConfig
from dotagent.core.errors import ProviderProtocolError, ProviderTransportError
from dotagent.core.ids import new_id
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.connector import get_json, post_json, retry_with_reset
from dotagent.llm.http import (
    build_client,
    iter_ndjson,
    raise_for_provider_status,
    request_context,
    transport_error_types,
    wrap_transport_error,
)
from dotagent.llm.provider_errors import WireError, classify_ollama, error_from_stream, wire_error_from_object
from dotagent.llm.traits import effort_ladder, is_known_label, label_rank
from dotagent.llm.types import (
    ChatRequest,
    DeltaSink,
    Effort,
    FinishReason,
    ImageData,
    Message,
    ModelInfo,
    ModelReply,
    StreamDelta,
    ToolCall,
    Usage,
    empty_usage,
)
from dotagent.llm.wire import function_tools, hoist_tool_images, parse_tool_arguments


class StreamState(FrozenModel):
    """Everything accumulated from the NDJSON stream so far."""

    model: str
    text: str
    thinking: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    done_reason: str | None
    done: bool


def initial_state(model: str) -> StreamState:
    return StreamState(
        model=model, text="", thinking="", tool_calls=(), usage=empty_usage(), done_reason=None, done=False
    )


def _as_int(value: JsonValue) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _wire_tool_call(call: ToolCall) -> JsonObject:
    return {"id": call.id, "function": {"name": call.name, "arguments": call.arguments}}


def _wire_images(images: tuple[ImageData, ...]) -> list[JsonValue]:
    return [image.data_b64 for image in images]


def to_wire_messages(messages: tuple[Message, ...]) -> list[JsonObject]:
    """Convert neutral messages to Ollama's schema.

    Screenshots returned by tools are re-attached as a user message after the tool results that produced them,
    because chat templates only render images on user turns.
    """
    wire: list[JsonObject] = []
    for message in hoist_tool_images(messages):
        if message.role == "system":
            wire.append({"role": "system", "content": message.content})
        elif message.role == "user":
            entry: JsonObject = {"role": "user", "content": message.content}
            if message.images:
                entry["images"] = _wire_images(message.images)
            wire.append(entry)
        elif message.role == "assistant":
            assistant: JsonObject = {"role": "assistant", "content": message.content}
            if message.thinking:
                assistant["thinking"] = message.thinking
            if message.tool_calls:
                assistant["tool_calls"] = [_wire_tool_call(call) for call in message.tool_calls]
            wire.append(assistant)
        else:
            wire.append(
                {
                    "role": "tool",
                    "tool_name": message.tool_name or "",
                    "tool_call_id": message.tool_call_id or "",
                    "content": message.content,
                }
            )
    return wire


class OllamaModelDetails(FrozenModel):
    """What ``/api/show`` tells us about a model, including which ``think`` values it accepts."""

    info: ModelInfo
    think_values: tuple[str | bool, ...]


def _advertised_levels(values: tuple[str | bool, ...]) -> list[str]:
    """The named levels a model accepts for ``think``; names that have no place on the scale are left out."""
    return [value for value in values if isinstance(value, str) and is_known_label(value)]


def think_ladder(thinking: bool, values: tuple[str | bool, ...]) -> tuple[Effort, ...]:
    """The levels a model offers: its named levels plus ``off``/``on`` for the booleans it accepts.

    A model that thinks but advertises no values (an older server) has a plain switch.
    """
    if not thinking:
        return ()
    if not values:
        return effort_ladder(("off", "on"))
    labels: list[str] = _advertised_levels(values)
    if any(value is False for value in values):
        labels.append("off")
    if any(value is True for value in values):
        labels.append("on")
    return effort_ladder(labels)


def choose_think(details: OllamaModelDetails, effort: Effort) -> str | bool | None:
    """Map the neutral effort onto a ``think`` value the model advertises (``None`` = do not send)."""
    if not details.info.thinking:
        return None
    values: tuple[str | bool, ...] = details.think_values
    if not values:
        return effort != "off"
    levels: list[str] = _advertised_levels(values)
    if effort == "off":
        if any(value is False for value in values):
            return False
        if levels:
            return min(levels, key=label_rank)
        return True
    if levels:
        target: int = label_rank(effort)
        return min(levels, key=lambda level: (abs(label_rank(level) - target), label_rank(level)))
    return any(value is True for value in values)


def is_cloud_model(model: str) -> bool:
    """Whether ``model`` runs on Ollama's servers: its tag is ``cloud`` (``kimi-k3:cloud``) or ends in ``-cloud``
    (``gpt-oss:120b-cloud``), as Ollama names them (item 221)."""
    name, colon, tag = model.rpartition(":")
    return colon == ":" and name != "" and (tag == "cloud" or tag.endswith("-cloud"))


def num_ctx_for(model: str, config: OllamaConfig) -> int:
    """The window asked of ``model`` with every request: ``num_ctx_cloud`` for a cloud model (:func:`is_cloud_model`),
    ``num_ctx`` for one that runs on this Mac."""
    return config.num_ctx_cloud if is_cloud_model(model) else config.num_ctx


def build_chat_payload(request: ChatRequest, details: OllamaModelDetails, config: OllamaConfig) -> JsonObject:
    options: JsonObject = {"num_ctx": num_ctx_for(request.model, config)}
    if request.temperature is not None:
        options["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        options["num_predict"] = request.max_output_tokens
    payload: JsonObject = {
        "model": request.model,
        "messages": list(to_wire_messages(request.messages)),
        "stream": True,
        "keep_alive": config.keep_alive,
        "options": options,
    }
    if request.tools:
        payload["tools"] = list(function_tools(request.tools))
    think: str | bool | None = choose_think(details, request.effort)
    if think is not None:
        payload["think"] = think
    return payload


def _parse_arguments(raw: JsonValue, context: JsonObject) -> JsonObject:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return parse_tool_arguments(raw)
    if raw is None:
        return {}
    raise ProviderProtocolError(
        "tool call arguments are neither an object nor a JSON string",
        {**context, "arguments": str(raw)[:500]},
    )


def _parse_tool_calls(raw: JsonValue, context: JsonObject) -> tuple[ToolCall, ...]:
    if not isinstance(raw, list):
        return ()
    calls: list[ToolCall] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ProviderProtocolError("tool call entry is not an object", {**context, "entry": str(entry)[:500]})
        function: JsonValue = entry.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ProviderProtocolError("tool call has no function name", {**context, "entry": str(entry)[:500]})
        identifier: JsonValue = entry.get("id")
        calls.append(
            ToolCall(
                id=identifier if isinstance(identifier, str) and identifier else new_id("call"),
                name=str(function["name"]),
                arguments=_parse_arguments(function.get("arguments"), context),
            )
        )
    return tuple(calls)


def apply_chunk(
    state: StreamState, chunk: JsonObject, context: JsonObject
) -> tuple[StreamState, tuple[StreamDelta, ...]]:
    """Fold one NDJSON object into the state and report the incremental text it carried. An error Ollama reports
    inside the stream is raised with its words, and with the status the table of stream errors gives it
    (:func:`error_from_stream`)."""
    error: JsonValue = chunk.get("error")
    if error is not None:
        wire: WireError | None = wire_error_from_object(chunk)
        raise error_from_stream(
            wire if wire is not None else WireError(kind="", code="", message=str(error), limit_name=""),
            context,
            classify_ollama,
        )
    deltas: list[StreamDelta] = []
    text: str = state.text
    thinking: str = state.thinking
    calls: tuple[ToolCall, ...] = state.tool_calls
    message: JsonValue = chunk.get("message")
    if isinstance(message, dict):
        piece: JsonValue = message.get("content")
        if isinstance(piece, str) and piece:
            text += piece
            deltas.append(StreamDelta(kind="text", text=piece))
        reasoning: JsonValue = message.get("thinking")
        if isinstance(reasoning, str) and reasoning:
            thinking += reasoning
            deltas.append(StreamDelta(kind="thinking", text=reasoning))
        calls = calls + _parse_tool_calls(message.get("tool_calls"), context)
    done: bool = chunk.get("done") is True
    usage: Usage = state.usage
    done_reason: str | None = state.done_reason
    if done:
        reason: JsonValue = chunk.get("done_reason")
        done_reason = reason if isinstance(reason, str) else "stop"
        usage = Usage(
            input_tokens=_as_int(chunk.get("prompt_eval_count")),
            output_tokens=_as_int(chunk.get("eval_count")),
            cached_tokens=_as_int(chunk.get("prompt_eval_cached_count")),
        )
    new_state: StreamState = StreamState(
        model=state.model,
        text=text,
        thinking=thinking,
        tool_calls=calls,
        usage=usage,
        done_reason=done_reason,
        done=done or state.done,
    )
    return new_state, tuple(deltas)


def finish_reason(state: StreamState) -> FinishReason:
    if state.tool_calls:
        return "tool_calls"
    if state.done_reason in ("", "stop"):
        return "stop"
    if state.done_reason == "length":
        return "length"
    return "other"


def to_reply(state: StreamState) -> ModelReply:
    return ModelReply(
        model=state.model,
        text=state.text,
        thinking=state.thinking,
        tool_calls=state.tool_calls,
        usage=state.usage,
        finish=finish_reason(state),
        raw_finish=state.done_reason or "",
        raw=(),
    )


def _parse_think_values(raw: JsonValue) -> tuple[str | bool, ...]:
    if not isinstance(raw, dict):
        return ()
    values: JsonValue = raw.get("values")
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, (str, bool)))


def parse_model_details(model: str, shown: JsonObject, num_ctx: int) -> OllamaModelDetails:
    """What ``/api/show`` says about a model. Every request is sent with ``num_ctx``, the window Ollama gives the model,
    so the context length is the smaller of that and the model's own window, which is kept when it is the larger."""
    capabilities: JsonValue = shown.get("capabilities")
    caps: set[str] = {str(item) for item in capabilities} if isinstance(capabilities, list) else set()
    thinking: bool = "thinking" in caps
    values: tuple[str | bool, ...] = _parse_think_values(shown.get("thinking"))
    trained: int | None = None
    details: JsonValue = shown.get("model_info")
    if isinstance(details, dict):
        for key, value in details.items():
            if key.endswith(".context_length") and isinstance(value, int) and not isinstance(value, bool):
                trained = value
                break
    info: ModelInfo = ModelInfo(
        id=model,
        vision="vision" in caps,
        tools="tools" in caps,
        thinking=thinking,
        context_length=num_ctx if trained is None else min(trained, num_ctx),
        own_context_length=trained if trained is not None and trained > num_ctx else None,
        window_source="asked" if trained is None or trained > num_ctx else "provider",
        efforts=think_ladder(thinking, values),
        free=False,
    )
    return OllamaModelDetails(info=info, think_values=values)


def _on_this_mac(base_url: str) -> bool:
    """Whether ``base_url`` names a server on this Mac: ``localhost`` or a loopback address."""
    host: str = httpx.URL(base_url).host
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OllamaProvider:
    """Connector to an Ollama server (local, or ollama.com with an API key)."""

    def __init__(self, config: OllamaConfig, api_key: str | None) -> None:
        self._config: OllamaConfig = config
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._details_cache: dict[str, OllamaModelDetails] = {}
        self._slots: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent)
        self._policy: RetryPolicy = RetryPolicy(attempts=config.attempts, base_delay_s=1.0, max_delay_s=20.0)
        self._list_policy: RetryPolicy = (
            RetryPolicy(attempts=1, base_delay_s=0.0, max_delay_s=0.0)
            if _on_this_mac(config.base_url)
            else self._policy
        )

    @property
    def name(self) -> str:
        return "ollama"

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    async def _post_json(self, path: str, payload: JsonObject, label: str) -> JsonObject:
        return await post_json(
            self._client, self._url(path), self._headers, payload, classify_ollama, self._policy, label
        )

    async def _details(self, model: str) -> OllamaModelDetails:
        cached: OllamaModelDetails | None = self._details_cache.get(model)
        if cached is not None:
            return cached
        shown: JsonObject = await self._post_json("/api/show", {"model": model}, "ollama.show")
        details: OllamaModelDetails = parse_model_details(model, shown, num_ctx_for(model, self._config))
        self._details_cache[model] = details
        return details

    async def model_info(self, model: str) -> ModelInfo:
        return (await self._details(model)).info

    async def installed_names(self) -> tuple[str, ...]:
        """The names of the models the server has, embedding models included. A server on this Mac is asked once (item
        212): with no network in between, a refused connection means it is not running, and retries would only keep
        the menu of models and the doctor waiting for it."""
        tags: JsonObject = await get_json(
            self._client, self._url("/api/tags"), self._headers, classify_ollama, self._list_policy, "ollama.tags"
        )
        listed: JsonValue = tags.get("models")
        names: list[str] = []
        if isinstance(listed, list):
            for entry in listed:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    names.append(str(entry["name"]))
        return tuple(names)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        names: tuple[str, ...] = await self.installed_names()
        infos: list[ModelInfo] = await asyncio.gather(*(self.model_info(name) for name in names))
        return tuple(infos)

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        details: OllamaModelDetails = await self._details(request.model)
        payload: JsonObject = build_chat_payload(request, details, self._config)
        url: str = self._url("/api/chat")
        context: JsonObject = request_context(url, payload)

        async def attempt() -> ModelReply:
            return await self._stream_once(url, payload, context, request.model, sink)

        async with self._slots:
            return await retry_with_reset(attempt, sink, self._policy, "ollama.chat")

    async def _stream_once(
        self, url: str, payload: JsonObject, context: JsonObject, model: str, sink: DeltaSink
    ) -> ModelReply:
        state: StreamState = initial_state(model)
        try:
            async with self._client.stream("POST", url, content=json.dumps(payload), headers=self._headers) as response:
                await raise_for_provider_status(response, context, classify_ollama)
                async for chunk in iter_ndjson(response, context):
                    state, deltas = apply_chunk(state, chunk, context)
                    for delta in deltas:
                        await sink(delta)
        except transport_error_types() as exc:
            raise wrap_transport_error(exc, context) from exc
        if not state.done:
            raise ProviderTransportError(
                "stream ended before the final 'done' message", {**context, "text_so_far": state.text[:300]}
            )
        return to_reply(state)

    async def aclose(self) -> None:
        await self._client.aclose()
