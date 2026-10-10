"""Long-term memory end to end: notes are saved and found again by meaning and by words.

The embeddings come from the real local Ollama server (``bge-m3``); the tests that need it are skipped when it is not
there. Covers recall by meaning and by words, duplicates, the notes that say nearly the same, the vectors
that belong to one embedding model, strict failure when the embedder is down, the words-only mode and the tools the
model calls. That model is multilingual: Turkish notes are found by meaning too.
"""

import asyncio
import math
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.verify import new_facts
from dotagent.config import AppConfig, load_config
from dotagent.core.errors import ConfigError, ProviderAuthError, ProviderTransportError, StoreError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.memory.embedder import OpenAIEmbedder, build_embedder, build_ollama_embedder, build_openai_embedder
from dotagent.memory.service import (
    MemoryHit,
    MemoryIndex,
    close_memory_index,
    open_memory_index,
    prompt_memories,
    recall,
    reindex,
    save_memory,
    sync_embedding_model,
)
from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.db import Store
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.registry import tools_for_mode

pytestmark = pytest.mark.asyncio(loop_scope="module")

OLLAMA_ROOT: str = "http://localhost:11434"
EMBED_MODEL: str = "bge-m3"
MODEL_KEY: str = "memory.embed_model"

DOG: str = "The user's dog is called Biscuit and is a golden retriever."
DARK_MODE: str = "The user prefers dark mode in every code editor."
TEA: str = "Kullanıcı sabahları kahve yerine çay içmeyi tercih ediyor."
LAPTOP: str = "The user's work laptop is a MacBook Pro with an M3 chip."
WIFI: str = "The wifi network at the user's home is called Nebula."
CITY: str = "Kullanıcı İstanbul'da yaşıyor."
MEETING_NOTES: str = "Toplantı notları Notion yerine Obsidian'da tutuluyor."
# Looks like a key to the policy; it is only classified here, never saved or sent anywhere.
KEY_LIKE: str = "sk-" + "Ab12Cd34" * 3
# Ollama answers the OpenAI wire format under /v1 and ignores the key, so the OpenAI embedder can run for real here.
COMPAT_KEY_ENV: str = "DOT_TEST_COMPAT_EMBED_KEY"
UNSET_KEY_ENV: str = "DOT_TEST_SURELY_UNSET_OPENAI_KEY"
# Not a credential: a made-up string the real OpenAI API refuses.
MADE_UP_KEY: str = "sk-dot-test-this-key-does-not-exist-0000000000000000"
OPENAI_EMBED_MODEL: str = "text-embedding-3-small"


def embedding_model_is_ready() -> bool:
    try:
        answer: httpx.Response = httpx.get(f"{OLLAMA_ROOT}/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    if answer.status_code != 200:
        return False
    return any(model["name"].startswith(EMBED_MODEL) for model in answer.json()["models"])


requires_embedder = pytest.mark.skipif(
    not embedding_model_is_ready(), reason=f"needs a local Ollama server with {EMBED_MODEL}"
)


class _Recorder:
    """Stands in for the run gate: lets every action through."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


def _without_embeddings(config: AppConfig) -> AppConfig:
    return config.model_copy(update={"memory": config.memory.model_copy(update={"embed_enabled": False})})


def _with_openai_embeddings(config: AppConfig, base_url: str, api_key_env: str) -> AppConfig:
    """``config`` with the notes embedded by the OpenAI connector, which reads its key from ``api_key_env``."""
    openai = config.providers.openai.model_copy(update={"base_url": base_url, "api_key_env": api_key_env})
    return config.model_copy(
        update={
            "memory": config.memory.model_copy(update={"embed_provider": "openai"}),
            "providers": config.providers.model_copy(update={"openai": openai}),
        }
    )


@asynccontextmanager
async def _opened(tmp_path: Path, config: AppConfig) -> AsyncIterator[MemoryIndex]:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    index: MemoryIndex = await open_memory_index(store, config)
    try:
        yield index
    finally:
        await close_memory_index(index)
        await store.close()


@pytest_asyncio.fixture(loop_scope="module")
async def memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryIndex]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    async with _opened(tmp_path, load_config(tmp_path / "config.toml")) as index:
        yield index


@pytest_asyncio.fixture(loop_scope="module")
async def words_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryIndex]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    async with _opened(tmp_path, _without_embeddings(load_config(tmp_path / "config.toml"))) as index:
        yield index


AGENT_RUN: str = "run_earlier_0000"


async def _save_all(index: MemoryIndex, notes: list[str]) -> None:
    for note in notes:
        await save_memory(index, note, [], None)


async def _best(index: MemoryIndex, query: str) -> str:
    hits: list[MemoryHit] = await recall(index, query, 3)
    assert hits, f"nothing found for {query!r}"
    return hits[0].record.content


@requires_embedder
async def test_notes_are_found_by_meaning_and_by_words(memory: MemoryIndex) -> None:
    await _save_all(memory, [DOG, DARK_MODE, TEA, LAPTOP, MEETING_NOTES])
    assert await _best(memory, "what pet does the user have?") == DOG
    assert await _best(memory, "which computer does the user work on?") == LAPTOP
    assert await _best(memory, "Biscuit") == DOG
    assert await _best(memory, "kahve") == TEA
    assert await _best(memory, "Notlarımı hangi uygulamada saklıyorum?") == MEETING_NOTES


@requires_embedder
async def test_a_query_that_fits_nothing_finds_nothing(memory: MemoryIndex) -> None:
    await _save_all(memory, [DOG, DARK_MODE, TEA, LAPTOP])
    assert await recall(memory, "quantum chromodynamics lattice gauge theory", 5) == []
    assert await recall(memory, "recipe for sourdough bread with rye flour", 5) == []


@requires_embedder
async def test_the_same_text_stays_one_note_and_its_tags_merge(memory: MemoryIndex) -> None:
    first = await save_memory(memory, DARK_MODE, [], "run-1")
    again = await save_memory(memory, f"  {DARK_MODE}\n", ["Editor"], "run-2")
    assert first.created and not again.created
    assert again.record.id == first.record.id
    assert again.record.tags == ("editor",)
    assert await memory.store.count_memories() == 1


@requires_embedder
async def test_a_note_that_says_nearly_the_same_comes_back_with_the_old_one(memory: MemoryIndex) -> None:
    await _save_all(memory, ["The user lives in Istanbul.", DOG])
    moved = await save_memory(memory, "The user lives in Ankara.", [], None)
    assert moved.created
    assert [hit.record.content for hit in moved.similar] == ["The user lives in Istanbul."]
    assert moved.similar[0].similarity is not None
    assert moved.similar[0].similarity >= memory.config.similar_threshold
    assert (await save_memory(memory, LAPTOP, [], None)).similar == ()


@requires_embedder
async def test_vectors_of_another_embedding_model_are_dropped_and_made_again(memory: MemoryIndex) -> None:
    await _save_all(memory, [DOG, LAPTOP])
    assert len(await memory.store.all_memory_vectors()) == 2
    assert await memory.store.kv_get(MODEL_KEY) == EMBED_MODEL

    await memory.store.kv_set(MODEL_KEY, "an-older-model")
    await sync_embedding_model(memory)
    assert await memory.store.all_memory_vectors() == []
    assert await memory.store.kv_get(MODEL_KEY) == EMBED_MODEL

    assert await _best(memory, "what pet does the user have?") == DOG
    assert len(await memory.store.all_memory_vectors()) == 2


@requires_embedder
async def test_reindex_embeds_every_note_again(memory: MemoryIndex) -> None:
    await _save_all(memory, [DOG, LAPTOP, TEA])
    assert await reindex(memory) == 3
    assert len(await memory.store.all_memory_vectors()) == 3


@requires_embedder
async def test_a_run_starts_with_the_standing_notes_and_the_ones_that_fit_its_goal(memory: MemoryIndex) -> None:
    standing: str = "Always answer the user in Turkish."
    await save_memory(memory, standing, ["core"], None)
    # Notes an earlier run saved: only the ones that fit the goal come along.
    for note in [DOG, LAPTOP, DARK_MODE]:
        await save_memory(memory, note, [], AGENT_RUN)
    related: list[str] = [r.content for r in await prompt_memories(memory, "Take Biscuit the dog to the vet.")]
    assert related[0] == standing
    assert DOG in related
    assert len(related) == len(set(related))
    assert [r.content for r in await prompt_memories(memory, "Find a recipe for sourdough bread.")] == [standing]


@requires_embedder
async def test_a_note_the_user_wrote_reaches_every_run_even_when_its_goal_is_unrelated(memory: MemoryIndex) -> None:
    await save_memory(memory, DOG, [], AGENT_RUN)
    await save_memory(memory, CITY, ["aboutme"], None)
    given: list[str] = [r.content for r in await prompt_memories(memory, "Find a recipe for sourdough bread.")]
    assert given == [CITY]


async def test_a_failing_embedder_fails_the_call_instead_of_saving_without_meaning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    config: AppConfig = load_config(tmp_path / "config.toml")
    broken: AppConfig = config.model_copy(
        update={
            "providers": config.providers.model_copy(
                update={"ollama": config.providers.ollama.model_copy(update={"base_url": "http://127.0.0.1:9"})}
            )
        }
    )
    async with _opened(tmp_path, broken) as index:
        with pytest.raises(ProviderTransportError):
            await save_memory(index, DOG, [], None)
        assert await index.store.count_memories() == 0


@requires_embedder
async def test_the_openai_embedder_gets_the_vectors_of_the_native_route_over_the_openai_wire_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setenv(COMPAT_KEY_ENV, "ollama-ignores-this")
    config: AppConfig = _with_openai_embeddings(
        load_config(tmp_path / "config.toml"), f"{OLLAMA_ROOT}/v1", COMPAT_KEY_ENV
    )
    over_openai: OpenAIEmbedder = await build_openai_embedder(config)
    native = await build_ollama_embedder(config)
    try:
        texts: list[str] = [DOG, TEA, LAPTOP]
        agreement: list[float] = [
            round(math.sumprod(first, second), 3)
            for first, second in zip(await over_openai.embed(texts), await native.embed(texts), strict=True)
        ]
        assert agreement == [1.0, 1.0, 1.0]
        assert await over_openai.embed([]) == []
        assert (await over_openai.served_model()).startswith(EMBED_MODEL)
    finally:
        await over_openai.aclose()
        await native.aclose()


@requires_embedder
async def test_the_openai_provider_setting_drives_the_whole_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setenv(COMPAT_KEY_ENV, "ollama-ignores-this")
    config: AppConfig = _with_openai_embeddings(
        load_config(tmp_path / "config.toml"), f"{OLLAMA_ROOT}/v1", COMPAT_KEY_ENV
    )
    async with _opened(tmp_path, config) as index:
        assert isinstance(index.embedder, OpenAIEmbedder)
        await _save_all(index, [DOG, LAPTOP, TEA])
        assert await _best(index, "what pet does the user have?") == DOG
        assert await index.store.kv_get(MODEL_KEY) == EMBED_MODEL
        assert len(await index.store.all_memory_vectors()) == 3


async def test_the_openai_provider_without_a_key_is_an_error_and_never_a_change_of_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.delenv(UNSET_KEY_ENV, raising=False)
    config: AppConfig = _with_openai_embeddings(
        load_config(tmp_path / "config.toml"), "https://api.openai.com/v1", UNSET_KEY_ENV
    )
    with pytest.raises(ConfigError) as caught:
        await build_embedder(config)
    assert caught.value.context["name"] == UNSET_KEY_ENV
    assert "dotx auth set" in str(caught.value.context["hint"])


@pytest.mark.internet
async def test_the_real_openai_api_refuses_a_made_up_key_with_a_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the URL paths and the error mapping of the OpenAI embedder against the real service; no credential."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    openai = load_config(tmp_path / "config.toml").providers.openai
    embedder: OpenAIEmbedder = OpenAIEmbedder(openai, MADE_UP_KEY, None, None, OPENAI_EMBED_MODEL)
    try:
        with pytest.raises(ProviderAuthError) as embedding:
            await embedder.embed(["ping"])
        with pytest.raises(ProviderAuthError) as listing:
            await embedder.served_model()
    finally:
        await embedder.aclose()
    assert embedding.value.status == 401
    assert embedding.value.context["url"] == "https://api.openai.com/v1/embeddings"
    assert "invalid_api_key" in str(embedding.value.context["response_body"])
    assert listing.value.context["url"] == f"https://api.openai.com/v1/models/{OPENAI_EMBED_MODEL}"


async def test_without_embeddings_only_the_words_are_searched(words_only: MemoryIndex) -> None:
    saved = await save_memory(words_only, DOG, [], None)
    await _save_all(words_only, [LAPTOP, CITY])
    assert saved.similar == ()
    assert await words_only.store.all_memory_vectors() == []

    hits: list[MemoryHit] = await recall(words_only, "Biscuit", 5)
    assert [(hit.record.content, hit.similarity) for hit in hits] == [(DOG, None)]
    assert await recall(words_only, "pet", 5) == []
    assert await _best(words_only, "İstanbul") == CITY


async def test_a_word_found_in_most_notes_does_not_make_a_match(words_only: MemoryIndex) -> None:
    await _save_all(words_only, [DOG, LAPTOP, DARK_MODE, WIFI])
    assert [hit.record.content for hit in await recall(words_only, "user Biscuit", 5)] == [DOG]
    assert await recall(words_only, "the user", 5) == []


async def test_a_note_must_be_a_single_short_fact(words_only: MemoryIndex) -> None:
    with pytest.raises(StoreError, match="empty"):
        await save_memory(words_only, " \n ", [], None)
    with pytest.raises(StoreError, match=str(words_only.config.max_chars)):
        await save_memory(words_only, "word " * 200, [], None)
    assert await words_only.store.count_memories() == 0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    home: Path = tmp_path_factory.mktemp("home")
    patch.setenv("DOT_HOME", str(home))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _prepare(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[PreparedCall, ToolContext]:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="memory-test",
        thread_id="memory-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    return await runtime.registry.tools[name].prepare(arguments, ctx), ctx


async def _call(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[ToolResult, ActionInfo]:
    prepared, ctx = await _prepare(runtime, name, arguments)
    return await prepared.run(ctx), prepared.action


@requires_embedder
async def test_the_model_can_save_search_and_forget_notes(runtime: Runtime) -> None:
    saved, action = await _call(runtime, "memory_save", {"content": WIFI, "tags": ["Home"]})
    assert not saved.is_error
    assert (action.tool, action.kind, action.risk, action.flags) == (
        "memory_save",
        "memory_write",
        "low",
        ("persistence",),
    )
    identified: re.Match[str] | None = re.match(r"Saved: \[(\w+)\] ", saved.text)
    assert identified is not None, saved.text
    assert re.search(r" #home \((you saved it|the user wrote it), \d{4}-\d{2}-\d{2}\)$", saved.text), saved.text

    found, action = await _call(runtime, "memory_search", {"query": "what is the name of the wifi network at home?"})
    assert WIFI in found.text
    assert not found.untrusted
    assert (action.kind, action.risk) == ("memory_read", "safe")

    again, _ = await _call(runtime, "memory_save", {"content": WIFI, "tags": []})
    assert again.text.startswith("Already saved: ")

    renamed: str = WIFI.replace("Nebula", "Orion")
    updated, action = await _call(
        runtime, "memory_update", {"memory_id": identified.group(1), "content": renamed, "tags": ["home"]}
    )
    assert updated.text.startswith(f"Updated: [{identified.group(1)}] {renamed}"), updated.text
    assert action.kind == "memory_write"
    moved, _ = await _call(runtime, "memory_search", {"query": "what is the name of the wifi network at home?"})
    assert renamed in moved.text
    assert WIFI not in moved.text

    with pytest.raises(StoreError, match="not found"):
        await _call(runtime, "memory_forget", {"memory_id": "mem_missing"})
    forgotten, action = await _call(runtime, "memory_forget", {"memory_id": identified.group(1)})
    assert action.summary == f"Forget: {renamed}"
    assert forgotten.text.startswith("Forgot: ")

    gone, _ = await _call(runtime, "memory_search", {"query": "wifi network at home"})
    assert gone.text.startswith("No saved notes match")


async def test_the_policy_gates_what_the_model_may_remember(runtime: Runtime) -> None:
    plain, _ = await _prepare(runtime, "memory_save", {"content": DARK_MODE})
    keyed, _ = await _prepare(runtime, "memory_save", {"content": f"The OpenAI key is {KEY_LIKE}"})
    assert keyed.action.flags == ("persistence", "exfiltration")
    assert decide(plain.action, "task", False, "aggressive", []).verdict == "allow"
    tainted = decide(plain.action, "task", True, "aggressive", [])
    assert (tainted.verdict, tainted.source) == ("ask", "taint")
    refused = decide(keyed.action, "task", False, "aggressive", [])
    assert (refused.verdict, refused.source) == ("deny", "auto_review")
    # A research run may keep memory notes (item 34): the mode does not gate a save.
    assert decide(plain.action, "research", False, "aggressive", []).source == "preset"
    assert "memory_save" in {tool.spec.name for tool in tools_for_mode(runtime.registry, "research")}
    assert "memory_search" in {tool.spec.name for tool in tools_for_mode(runtime.registry, "research")}
