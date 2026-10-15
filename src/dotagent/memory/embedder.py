"""Connectors to embedding endpoints: text in, unit vectors out.

``OllamaEmbedder`` speaks to ``/api/embed`` of an Ollama server (local, or ollama.com with an API key) and
``OpenAIEmbedder`` to ``/embeddings`` of the OpenAI API. :func:`build_embedder` makes the one that
``[memory] embed_provider`` names; a failing call is never answered by the other provider, because the vectors of two
models cannot be compared and the second one would receive the text of the notes without being asked.
"""

import asyncio
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import JsonValue

from dotagent.config import AppConfig, OllamaConfig, OpenAIConfig
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.retry import RetryPolicy
from dotagent.core.types import JsonObject
from dotagent.llm.connector import get_json, post_json
from dotagent.llm.http import build_client, request_context
from dotagent.llm.openai import request_headers
from dotagent.llm.provider_errors import classify_ollama, classify_openai
from dotagent.llm.wire import get_str
from dotagent.memory.ranking import Vector, unit_vector
from dotagent.secrets import require_secret, resolve_secret

_OLLAMA_EMBED_PATH: str = "/api/embed"
_OPENAI_EMBED_PATH: str = "/embeddings"
_RETRY: RetryPolicy = RetryPolicy(attempts=3, base_delay_s=0.5, max_delay_s=4.0)


class Embedder(Protocol):
    """A connector to one embedding service: what the memory needs of it."""

    @property
    def model(self) -> str: ...

    async def embed(self, texts: Sequence[str]) -> list[Vector]: ...

    async def aclose(self) -> None: ...


def _numbers(value: JsonValue) -> list[float] | None:
    """The numbers of a JSON array, or ``None`` when ``value`` is not an array of plain numbers."""
    if not isinstance(value, list):
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return None
        numbers.append(float(item))
    return numbers


def _entries(answer: JsonObject, field: str, expected: int, context: JsonObject) -> list[JsonValue]:
    """The array at ``field`` of an embedding answer, which must hold one entry per input text."""
    raw: JsonValue = answer.get(field)
    if not isinstance(raw, list) or len(raw) != expected:
        shown: JsonValue = len(raw) if isinstance(raw, list) else type(raw).__name__
        answer_keys: list[JsonValue] = [*sorted(answer)]
        raise ProviderProtocolError(
            "the embedding answer does not hold one embedding per text",
            {**context, "expected": expected, "received": shown, "answer_keys": answer_keys},
        )
    return raw


def _unit_vectors(rows: Sequence[JsonValue], context: JsonObject) -> list[Vector]:
    """One unit vector per row; every row must be an array of plain numbers and all of them of the same dimension."""
    vectors: list[Vector] = []
    for index, item in enumerate(rows):
        numbers: list[float] | None = _numbers(item)
        if numbers is None:
            raise ProviderProtocolError("an embedding is not an array of numbers", {**context, "index": index})
        vectors.append(unit_vector(numbers, {**context, "index": index}))
    if len({len(vector) for vector in vectors}) > 1:
        raise ProviderProtocolError(
            "the embeddings of one answer differ in dimension",
            {**context, "dimensions": [len(vector) for vector in vectors]},
        )
    return vectors


def parse_embeddings(answer: JsonObject, expected: int, context: JsonObject) -> list[Vector]:
    """The unit vectors of an Ollama ``/api/embed`` answer: exactly one per input text, all of the same dimension."""
    return _unit_vectors(_entries(answer, "embeddings", expected, context), context)


def parse_openai_embeddings(answer: JsonObject, expected: int, context: JsonObject) -> list[Vector]:
    """The unit vectors of an OpenAI ``/embeddings`` answer, in the order of the input texts.

    Each entry of ``data`` names the input it belongs to (``index``); the indexes must be exactly ``0..expected-1``."""
    ordered: dict[int, JsonValue] = {}
    for position, entry in enumerate(_entries(answer, "data", expected, context)):
        if not isinstance(entry, dict):
            raise ProviderProtocolError("an embedding entry is not an object", {**context, "position": position})
        index: JsonValue = entry.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < expected or index in ordered:
            raise ProviderProtocolError(
                "an embedding entry has no valid, unique index", {**context, "position": position, "index": index}
            )
        ordered[index] = entry.get("embedding")
    return _unit_vectors([ordered[index] for index in range(expected)], context)


class OllamaEmbedder:
    """Connector to the embedding endpoint of an Ollama server (local, or ollama.com with an API key)."""

    def __init__(self, config: OllamaConfig, api_key: str | None, model: str) -> None:
        self._config: OllamaConfig = config
        self._model: str = model
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One unit vector per text, in the order of ``texts``."""
        if not texts:
            return []
        inputs: list[JsonValue] = list(texts)
        payload: JsonObject = {
            "model": self._model,
            "input": inputs,
            "truncate": True,
            "keep_alive": self._config.keep_alive,
        }
        url: str = f"{self._config.base_url.rstrip('/')}{_OLLAMA_EMBED_PATH}"
        answer: JsonObject = await post_json(
            self._client, url, self._headers, payload, classify_ollama, _RETRY, "ollama.embed"
        )
        return parse_embeddings(answer, len(texts), request_context(url, payload))

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAIEmbedder:
    """Connector to the embedding endpoint of the OpenAI API (or of a server that speaks its wire format).

    The text of every note and search is sent to that service."""

    def __init__(
        self, config: OpenAIConfig, api_key: str, organization: str | None, project: str | None, model: str
    ) -> None:
        self._config: OpenAIConfig = config
        self._model: str = model
        self._client: httpx.AsyncClient = build_client(config.connect_timeout_s, config.read_timeout_s)
        self._headers: dict[str, str] = request_headers(api_key, organization, project)

    @property
    def model(self) -> str:
        return self._model

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One unit vector per text, in the order of ``texts``."""
        if not texts:
            return []
        inputs: list[JsonValue] = list(texts)
        payload: JsonObject = {"model": self._model, "input": inputs, "encoding_format": "float"}
        url: str = self._url(_OPENAI_EMBED_PATH)
        answer: JsonObject = await post_json(
            self._client, url, self._headers, payload, classify_openai, _RETRY, "openai.embed"
        )
        return parse_openai_embeddings(answer, len(texts), request_context(url, payload))

    async def served_model(self) -> str:
        """The id the API reports for the configured model. The call is free and proves the key, the project's access
        to the model and the network path."""
        listed: JsonObject = await get_json(
            self._client, self._url(f"/models/{self._model}"), self._headers, classify_openai, _RETRY, "openai.model"
        )
        return get_str(listed, "id")

    async def aclose(self) -> None:
        await self._client.aclose()


async def build_ollama_embedder(config: AppConfig) -> OllamaEmbedder:
    ollama: OllamaConfig = config.providers.ollama
    key: str | None = await asyncio.to_thread(resolve_secret, ollama.api_key_env)
    return OllamaEmbedder(ollama, key, config.memory.embed_model)


async def build_openai_embedder(config: AppConfig) -> OpenAIEmbedder:
    """The OpenAI embedder; without ``OPENAI_API_KEY`` this is a configuration error."""
    openai: OpenAIConfig = config.providers.openai
    purpose: str = 'the OpenAI embeddings ([memory] embed_provider = "openai")'
    api_key: str = await asyncio.to_thread(require_secret, openai.api_key_env, purpose)
    organization: str | None = await asyncio.to_thread(resolve_secret, openai.organization_env)
    project: str | None = await asyncio.to_thread(resolve_secret, openai.project_env)
    return OpenAIEmbedder(openai, api_key, organization, project, config.memory.embed_model)


async def build_embedder(config: AppConfig) -> Embedder:
    """The connector ``[memory] embed_provider`` names."""
    match config.memory.embed_provider:
        case "ollama":
            return await build_ollama_embedder(config)
        case "openai":
            return await build_openai_embedder(config)
