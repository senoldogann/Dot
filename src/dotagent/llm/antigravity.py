"""Antigravity provider: the models of a Google Antigravity plan, over Cloud Code Assist.

Antigravity's models belong to the account, not to a key: a Google login holding the Cloud Code scopes, which a client
on this Mac keeps on disk (a desktop client's Antigravity driver writes ``acp_token.json``), and the route the IDE
itself speaks:
``POST {base_url}/v1internal:streamGenerateContent?alt=sse``, a Gemini request inside an envelope naming the model, the
project and the client. The same envelope serves the plan's Gemini models and the Claude and GPT-OSS ones Antigravity
offers beside them, so every model of the account is one model id here. Google gates the route on the client's
``User-Agent``, which ``[providers.antigravity] user_agent`` names: a version it retired answers with a text asking for
an update instead of a reply.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue

from dotagent.config import AntigravityConfig
from dotagent.core.errors import ProviderAuthError, ProviderProtocolError, ProviderTransportError
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.connector import StreamCodec, parse_event_object, post_json, retry_with_reset, stream_model_reply
from dotagent.llm.http import SseEvent, build_client, request_context
from dotagent.llm.provider_errors import classify_generic
from dotagent.llm.traits import ModelTraits, choose_effort, info_from_traits
from dotagent.llm.types import (
    ChatRequest,
    DeltaSink,
    ImageData,
    Message,
    ModelInfo,
    ModelReply,
    StreamDelta,
    ToolCall,
    Usage,
    invalid_arguments,
)
from dotagent.llm.wire import get_int, get_obj, get_objects, get_str

# The thinking levels a Gemini model of the plan takes as ``generationConfig.thinkingConfig.thinkingLevel``.
_LEVELS: tuple[str, ...] = ("low", "medium", "high")
# Ids of the listing that are not something the agent can talk to (tab completion, images, embeddings).
_NOT_CHAT: tuple[str, ...] = (
    "tab_",
    "chat_",
    "image",
    "embedding",
    "tts",
    "audio",
    "realtime",
    "transcribe",
    "disabled",
)
# The ids that carry their thinking level themselves, so a level is not set beside them.
_LEVEL_IDS: tuple[str, ...] = ("-low", "-medium", "-high")
# Limits for a model whose entry in the listing names none.
_DEFAULT_CONTEXT: int = 1_048_576
_DEFAULT_OUTPUT: int = 65_536
# A token is used only while this long is left before it expires.
_TOKEN_MARGIN_S: float = 120.0
# A token endpoint that answers 5xx or 429 says nothing about the login: it is asked again, this many times, this far
# apart at first.
_TOKEN_ATTEMPTS: int = 3
_TOKEN_RETRY_S: float = 1.0
_TOKEN_TRANSIENT: frozenset[int] = frozenset({429, 500, 502, 503, 504})
# The endpoints a refresh token may be sent to. The login file is one Dot does not write: it must not be able to point
# the refresh token at a host of its own choosing.
_GOOGLE_TOKEN_HOSTS: frozenset[str] = frozenset({"oauth2.googleapis.com", "accounts.google.com"})


class Credentials(FrozenModel):
    """The Google OAuth login of the Antigravity account, as the client that signed in keeps it."""

    client_id: str
    client_secret: str
    refresh_token: str
    token_uri: str
    project: str


def _written_at(path: Path) -> float:
    """When the file was last written, or 0 when it is gone by now."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def read_credentials(pattern: str) -> Credentials:
    """The login at ``pattern`` (a home directory ``~`` and a ``*`` glob allowed), the most recently written match."""
    expanded: str = str(Path(pattern).expanduser())
    paths: list[Path] = [Path(expanded)]
    if any(char in expanded for char in "*?["):
        matches: list[Path] = sorted((Path(match) for match in glob(expanded)), key=_written_at, reverse=True)
        paths = matches[:1]
    if not paths or not paths[0].is_file():
        raise ProviderAuthError(
            "no Antigravity login was found on this Mac",
            0,
            {
                "path": pattern,
                "hint": "sign in to Antigravity once (the IDE on this Mac, or a client that signs in to it), then set "
                "[providers.antigravity] credentials_file to the file it writes",
            },
        )
    try:
        raw: JsonValue = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderAuthError("the Antigravity login file could not be read", 0, {"path": str(paths[0])}) from exc
    if not isinstance(raw, dict):
        raise ProviderAuthError("the Antigravity login file does not hold a login", 0, {"path": str(paths[0])})
    login: JsonObject = raw
    missing: list[str] = [
        name
        for name in ("client_id", "client_secret", "refresh_token")
        if not isinstance(login.get(name), str) or not login.get(name)
    ]
    if missing:
        raise ProviderAuthError(
            "the Antigravity login is incomplete", 0, {"path": str(paths[0]), "missing": list[JsonValue](missing)}
        )
    return Credentials(
        client_id=get_str(login, "client_id"),
        client_secret=get_str(login, "client_secret"),
        refresh_token=get_str(login, "refresh_token"),
        token_uri=_checked_token_uri(get_str(login, "token_uri")),
        project=get_str(login, "project_id"),
    )


def _checked_token_uri(value: str) -> str:
    """The token endpoint the login names, which only Google's own may be: the file is one Dot does not write, and the
    refresh token goes to whatever it says."""
    uri: str = value or "https://oauth2.googleapis.com/token"
    parts = urlsplit(uri)
    if parts.scheme != "https" or parts.hostname not in _GOOGLE_TOKEN_HOSTS:
        raise ProviderAuthError(
            "the Antigravity login names a token endpoint that is not Google's",
            0,
            {"token_uri": uri, "hint": "the account's own login writes https://oauth2.googleapis.com/token"},
        )
    return uri


class TokenSource:
    """An access token for the login, exchanged from its refresh token and kept until shortly before it expires."""

    def __init__(self, credentials: Credentials, client: httpx.AsyncClient) -> None:
        self._credentials: Credentials = credentials
        self._client: httpx.AsyncClient = client
        self._token: str = ""
        self._expires_at: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def project(self) -> str:
        """The Cloud Code project the login belongs to."""
        return self._credentials.project

    def forget(self) -> None:
        """Drop the kept token, so that the next call exchanges the refresh token again."""
        self._token = ""
        self._expires_at = 0.0

    async def _exchange(self, form: dict[str, str]) -> httpx.Response:
        """The token endpoint's answer, asked again when the endpoint itself is having a bad moment — a 5xx or a 429
        says nothing about the login, and a run that meets one should not be told its login was refused."""
        last: httpx.Response | None = None
        for attempt in range(_TOKEN_ATTEMPTS):
            try:
                last = await self._client.post(self._credentials.token_uri, data=form)
            except httpx.TransportError as exc:
                if attempt + 1 == _TOKEN_ATTEMPTS:
                    raise ProviderTransportError(
                        "the Google login of the Antigravity account could not be renewed",
                        {"url": self._credentials.token_uri, "error": type(exc).__name__},
                    ) from exc
                await asyncio.sleep(_TOKEN_RETRY_S * (2**attempt))
                continue
            if last.status_code in _TOKEN_TRANSIENT:
                if attempt + 1 == _TOKEN_ATTEMPTS:
                    raise ProviderTransportError(
                        "the Google token endpoint could not answer for the Antigravity account",
                        {"url": self._credentials.token_uri, "status": last.status_code},
                    )
                await asyncio.sleep(_TOKEN_RETRY_S * (2**attempt))
                continue
            return last
        raise ProviderTransportError(
            "the Google login of the Antigravity account could not be renewed",
            {"url": self._credentials.token_uri},
        )

    async def token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires_at - _TOKEN_MARGIN_S:
                return self._token
            form: dict[str, str] = {
                "grant_type": "refresh_token",
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
                "refresh_token": self._credentials.refresh_token,
            }
            response: httpx.Response = await self._exchange(form)
            if response.status_code in (400, 401):
                raise ProviderAuthError(
                    "the Google login of the Antigravity account was refused",
                    response.status_code,
                    {
                        "status": response.status_code,
                        "response_body": response.text[:500],
                        "hint": "sign in to Antigravity again on this Mac "
                        "(the login it holds has expired or was revoked)",
                    },
                )
            try:
                payload: JsonValue = response.json()
            except json.JSONDecodeError as exc:
                raise ProviderProtocolError(
                    "the token exchange answered with a body that is not JSON", {"url": self._credentials.token_uri}
                ) from exc
            body: JsonObject = payload if isinstance(payload, dict) else {}
            token: str = get_str(body, "access_token")
            if not token:
                raise ProviderProtocolError(
                    "the token exchange answered without an access token",
                    {
                        "url": self._credentials.token_uri,
                        "status": response.status_code,
                        "response_body": response.text[:500],
                    },
                )
            self._token = token
            self._expires_at = time.monotonic() + max(float(get_int(body, "expires_in")), 60.0)
            return token


def traits_of(model: str, entry: JsonObject) -> ModelTraits:
    """What a model of the account's listing can do (``fetchAvailableModels``).

    A Gemini model reasons at the levels ``thinkingLevel`` names, unless its id already carries the level (Antigravity's
    ``gemini-3.8-flash-high``, ``-medium`` and ``-low`` are three ids of one model). The Claude and GPT-OSS models of
    the plan take their own thinking settings, not this one, and get no level from Dot.
    """
    gemini: bool = get_str(entry, "apiProvider").startswith("API_PROVIDER_GOOGLE")
    reasoning: bool = entry.get("supportsThinking") is True
    window: int = get_int(entry, "maxTokens")
    output: int = get_int(entry, "maxOutputTokens")
    return ModelTraits(
        vision=entry.get("supportsImages") is True,
        context_length=window or _DEFAULT_CONTEXT,
        window_source="provider" if window else "assumed",
        max_output_tokens=output or _DEFAULT_OUTPUT,
        efforts=_LEVELS if reasoning and gemini and not model.endswith(_LEVEL_IDS) else (),
        reasoning=reasoning,
        thinking_toggle=False,
        temperature=True,
    )


def assumed_traits(model: str) -> ModelTraits:
    """Traits of a model the account's listing does not name, from what its id says it is."""
    lowered: str = model.lower()
    window: int = 250_000 if lowered.startswith("claude") else 1_048_576
    output: int = 64_000 if lowered.startswith("claude") else 65_536
    gemini: bool = lowered.startswith("gemini")
    return ModelTraits(
        vision=True,
        context_length=window,
        window_source="assumed",
        max_output_tokens=output,
        efforts=_LEVELS if gemini else (),
        reasoning=True,
        thinking_toggle=False,
        temperature=True,
    )


def is_chat_model(model: str, entry: JsonObject) -> bool:
    """Whether an entry of the listing is something the agent can talk to."""
    if model == "" or not get_str(entry, "displayName"):
        return False
    return not any(marker in model.lower() for marker in _NOT_CHAT)


_DROPPED_SCHEMA_KEYS: frozenset[str] = frozenset(
    {"$schema", "$id", "$comment", "title", "default", "examples", "additionalProperties", "deprecated"}
)

# The maps in a schema whose keys are names, not keywords: their entries are schemas whatever they are called.
_NAME_MAPS: frozenset[str] = frozenset({"properties", "$defs", "definitions"})


def _schema(value: JsonValue) -> JsonValue:
    """A tool's JSON schema as Cloud Code takes it: the keywords Gemini's dialect has no place for are dropped, and a
    value allowed to be null is sent as its other type (Gemini marks optionality by leaving a name out of ``required``).
    """
    if isinstance(value, list):
        return [_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    branches_value: JsonValue = value.get("anyOf")
    if isinstance(branches_value, list):
        branches: list[JsonValue] = [branch for branch in branches_value if branch != {"type": "null"}]
        if len(branches) == 1 and isinstance(branches[0], dict):
            rest: dict[str, JsonValue] = {key: item for key, item in value.items() if key != "anyOf"}
            return _schema({**rest, **branches[0]})
    out: dict[str, JsonValue] = {}
    for key, item in value.items():
        if key in _DROPPED_SCHEMA_KEYS:
            continue
        # Under "properties" the keys are the names a call passes, not keywords of the dialect: a parameter called
        # "title" or "default" keeps its place, and the "required" list still finds it.
        if key in _NAME_MAPS and isinstance(item, dict):
            out[key] = {name: _schema(sub) for name, sub in item.items()}
        else:
            out[key] = _schema(item)
    return out


def _tool_call(part: JsonObject, index: int, origin: str) -> ToolCall | None:
    """The call a part of a Gemini reply carries, with the thought signature the next request has to give back, and
    the model that wrote it, since only that model takes the signature back."""
    call: JsonObject = get_obj(part, "functionCall")
    if not call:
        return None
    name: str = get_str(call, "name")
    arguments: JsonValue = call.get("args")
    parsed: JsonObject = {}
    if isinstance(arguments, dict):
        parsed = arguments
    elif isinstance(arguments, str) and arguments.strip():
        try:
            decoded: JsonValue = json.loads(arguments)
        except json.JSONDecodeError:
            return ToolCall(
                id=get_str(call, "id") or f"call_{index}",
                name=name,
                arguments=invalid_arguments(arguments),
                extra=None,
                extra_origin=origin,
            )
        parsed = decoded if isinstance(decoded, dict) else {}
    signature: str = get_str(part, "thoughtSignature")
    return ToolCall(
        id=get_str(call, "id") or f"call_{index}",
        name=name,
        arguments=parsed,
        extra={"thoughtSignature": signature} if signature else None,
        extra_origin=origin,
    )


def _parts_of(message: Message, origin: str) -> list[JsonValue]:
    """One turn as Gemini parts: its text, the calls it made (each with its thought signature, when this model wrote
    it), the results it answers with, and its pictures."""
    parts: list[JsonValue] = []
    if message.role == "tool":
        parts.append({"functionResponse": {"name": message.tool_name or "", "response": {"result": message.content}}})
    else:
        if message.content:
            parts.append({"text": message.content})
        for call in message.tool_calls:
            part: JsonObject = {"functionCall": {"name": call.name, "args": call.arguments}}
            signature: str = ""
            if call.extra_origin == origin:
                signature = get_str(call.extra or {}, "thoughtSignature")
            if signature:
                part["thoughtSignature"] = signature
            parts.append(part)
    for image in message.images:
        parts.append({"inlineData": {"mimeType": _image_type(image), "data": image.data_b64}})
    return parts


def _image_type(image: ImageData) -> str:
    return image.media_type or "image/png"


def build_request_body(request: ChatRequest, traits: ModelTraits, project: str) -> JsonObject:
    """The Cloud Code envelope around a Gemini request for one chat call."""
    origin: str = f"antigravity:{request.model}"
    contents: list[JsonValue] = []
    answers_calls: bool = False
    for message in request.messages:
        if message.role == "system":
            continue
        parts: list[JsonValue] = _parts_of(message, origin)
        if not parts:
            continue
        # Gemini takes every answer to the calls of one turn in the content of that turn: a run that called three
        # tools at once sends their three results as one user content, not three.
        if message.role == "tool" and answers_calls and contents:
            previous = contents[-1]
            if isinstance(previous, dict):
                previous["parts"] = [*previous["parts"], *parts]  # type: ignore[misc]
                continue
        contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})
        answers_calls = message.role == "tool"
    gemini: JsonObject = {"contents": contents}
    system: str = "\n\n".join(
        message.content for message in request.messages if message.role == "system" and message.content
    )
    if system:
        system_parts: list[JsonValue] = [{"text": system}]
        gemini["systemInstruction"] = {"parts": system_parts}
    declarations: list[JsonValue] = [
        {"name": spec.name, "description": spec.description, "parameters": _schema(spec.parameters)}
        for spec in request.tools
    ]
    if declarations:
        tool_groups: list[JsonValue] = [{"functionDeclarations": declarations}]
        gemini["tools"] = tool_groups
    config: JsonObject = {}
    if request.temperature is not None and traits.temperature:
        config["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        config["maxOutputTokens"] = min(request.max_output_tokens, traits.max_output_tokens)
    level: str | None = choose_effort(traits.efforts, request.effort)
    if level is not None:
        config["thinkingConfig"] = {"thinkingLevel": level}
    if config:
        gemini["generationConfig"] = config
    return {
        "model": request.model,
        "project": project,
        "request": gemini,
        "requestType": "agent",
        "userAgent": "antigravity",
        "requestId": f"agent-{int(time.time() * 1000)}",
    }


@dataclass
class StreamState:
    """The reply as the stream has given it so far; the counters of a Gemini stream are cumulative, so the largest of
    each is kept."""

    text: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    finish: str = ""
    raw: list[JsonObject] = field(default_factory=list)


def _initial(model: str) -> StreamState:
    return StreamState()


def _finish_reason(state: StreamState, raw: str) -> str:
    if raw == "MAX_TOKENS":
        return "length"
    if state.calls:
        return "tool_calls"
    if raw == "STOP":
        return "stop"
    return "other"


def _fold(
    state: StreamState, event: SseEvent, context: JsonObject, origin: str
) -> tuple[StreamState, tuple[StreamDelta, ...]]:
    parsed: JsonObject = parse_event_object(event, context)
    error: JsonObject = get_obj(parsed, "error")
    if error:
        raise ProviderProtocolError(
            "the Antigravity route reported an error inside the stream",
            {**context, "code": get_int(error, "code"), "message": get_str(error, "message")[:500]},
        )
    response: JsonObject = get_obj(parsed, "response") or parsed
    deltas: list[StreamDelta] = []
    for candidate in get_objects(response, "candidates"):
        for part in get_objects(get_obj(candidate, "content"), "parts"):
            piece: str = get_str(part, "text")
            if part.get("thought") is True:
                if piece:
                    state.thinking.append(piece)
                    deltas.append(StreamDelta(kind="thinking", text=piece))
            elif piece:
                state.text.append(piece)
                deltas.append(StreamDelta(kind="text", text=piece))
            call: ToolCall | None = _tool_call(part, len(state.calls), origin)
            if call is not None:
                state.calls.append(call)
        finish: str = get_str(candidate, "finishReason")
        if finish:
            state.finish = finish
    usage: JsonObject = get_obj(response, "usageMetadata")
    state.input_tokens = max(state.input_tokens, get_int(usage, "promptTokenCount"))
    state.output_tokens = max(state.output_tokens, get_int(usage, "candidatesTokenCount"))
    state.cached_tokens = max(state.cached_tokens, get_int(usage, "cachedContentTokenCount"))
    state.raw.append(response)
    return state, tuple(deltas)


def _is_final(state: StreamState) -> bool:
    return state.finish != ""


def _to_reply(state: StreamState, model: str) -> ModelReply:
    return ModelReply(
        model=model,
        text="".join(state.text),
        thinking="".join(state.thinking),
        tool_calls=tuple(state.calls),
        usage=Usage(
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
            cached_tokens=state.cached_tokens,
        ),
        finish=_finish_reason(state, state.finish),
        raw_finish=state.finish,
        raw=(),
    )


def accepts_the_end_of_a_stream() -> bool:
    """No: a Cloud Code stream that stops before its ``finishReason`` was cut, and a cut read as an answer would give a
    run a reply the model never finished."""
    return False


def cloud_code_stream(model: str) -> StreamCodec[StreamState]:
    """How the route's SSE events fold into a reply."""
    origin: str = f"antigravity:{model}"
    return StreamCodec(
        initial=_initial,
        fold=lambda state, event, context: _fold(state, event, context, origin),
        is_final=_is_final,
        # A stream that stops before its finishReason is a cut, not an answer: the call would read it as finished.
        accepts_eof=lambda state: accepts_the_end_of_a_stream(),
        to_reply=lambda state: _to_reply(state, model),
    )


class AntigravityProvider:
    """Connector to the models of a Google Antigravity account, over Cloud Code Assist."""

    def __init__(self, config: AntigravityConfig) -> None:
        self._config: AntigravityConfig = config
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._tokens: TokenSource | None = None
        self._entries: dict[str, JsonObject] = {}
        self._slots: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent)
        self._policy: RetryPolicy = RetryPolicy(attempts=config.attempts, base_delay_s=1.0, max_delay_s=30.0)

    @property
    def name(self) -> str:
        return "antigravity"

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    async def _tokens_source(self) -> TokenSource:
        if self._tokens is None:
            credentials: Credentials = await asyncio.to_thread(read_credentials, self._config.credentials_file)
            project: str = self._config.project or credentials.project
            if not project:
                raise ProviderAuthError(
                    "the Antigravity login names no Cloud Code project",
                    0,
                    {"path": self._config.credentials_file, "hint": "set [providers.antigravity] project"},
                )
            self._tokens = TokenSource(credentials.model_copy(update={"project": project}), self._client)
        return self._tokens

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": self._config.user_agent,
            "Accept": "text/event-stream",
        }

    async def _project(self) -> str:
        return (await self._tokens_source()).project

    async def _load_models(self) -> None:
        tokens: TokenSource = await self._tokens_source()
        url: str = self._url("/v1internal:fetchAvailableModels")
        payload: JsonObject = {"project": await self._project()}
        headers: dict[str, str] = self._headers(await tokens.token())
        listed: JsonObject = await post_json(
            self._client, url, headers, payload, classify_generic, self._policy, "antigravity.models"
        )
        models: JsonObject = get_obj(listed, "models")
        self._entries = {
            model: entry for model, entry in models.items() if isinstance(entry, dict) and is_chat_model(model, entry)
        }

    async def model_info(self, model: str) -> ModelInfo:
        if model not in self._entries:
            await self._load_models()
        entry: JsonObject | None = self._entries.get(model)
        traits: ModelTraits = traits_of(model, entry) if entry is not None else assumed_traits(model)
        return info_from_traits(model, traits, True)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        await self._load_models()
        return tuple(
            info_from_traits(model, traits_of(model, entry), True) for model, entry in sorted(self._entries.items())
        )

    async def _run(self, body: JsonObject, model: str, sink: DeltaSink, renew_login: bool) -> ModelReply:
        tokens: TokenSource = await self._tokens_source()
        url: str = self._url("/v1internal:streamGenerateContent?alt=sse")
        payload: str = dumps_compact(body)
        context: JsonObject = request_context(url, body)
        headers: dict[str, str] = self._headers(await tokens.token())

        async def attempt() -> ModelReply:
            return await stream_model_reply(
                self._client,
                url,
                payload,
                headers,
                context,
                classify_generic,
                cloud_code_stream(model),
                model,
                sink,
            )

        try:
            async with self._slots:
                return await retry_with_reset(attempt, sink, self._policy, "antigravity.chat")
        except ProviderAuthError:
            if not renew_login:
                raise
            tokens.forget()
        return await self._run(body, model, sink, False)

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if request.model not in self._entries:
            await self._load_models()
        entry: JsonObject | None = self._entries.get(request.model)
        traits: ModelTraits = traits_of(request.model, entry) if entry is not None else assumed_traits(request.model)
        body: JsonObject = build_request_body(request, traits, await self._project())
        return await self._run(body, request.model, sink, True)

    async def aclose(self) -> None:
        await self._client.aclose()
