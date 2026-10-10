"""The real hosted services with the user's own keys: OpenAI on both wires, OpenCode Go on all three routes, the Claude
models of Anthropic, the hosted web search of Ollama and TypeSafe's typed judgments.

``test_wire_codecs.py`` makes the same calls against a local Ollama that stands in for these services. Here every test
sends a handful of tiny requests (cents at most) and is skipped when its key cannot be resolved (environment, the
``.env`` file of the Dot home or the Keychain), so a machine without keys still runs the whole suite.
"""

from pathlib import Path

import pytest
from test_wire_codecs import (
    SCREENSHOT_TOOL,
    first_tool_call_reply,
    greedy_request_for,
    ignore_delta,
    solid_image,
    tool_round_trip,
)

from dotagent.agent.judge import CLICK_QUESTIONS
from dotagent.config import AppConfig, GoRoute, load_config
from dotagent.llm.anthropic import AnthropicProvider
from dotagent.llm.openai import OpenAIProvider
from dotagent.llm.opencode_go import OpenCodeGoProvider, route_of
from dotagent.llm.types import (
    ChatProvider,
    ModelInfo,
    ModelReply,
    assistant_from_reply,
    system_message,
    tool_message,
    user_message,
)
from dotagent.llm.typesafe import NoulQuestion, TypeSafeClient
from dotagent.secrets import resolve_secret
from dotagent.web.client import WebClient
from dotagent.web.search import search_ollama
from dotagent.web.types import SearchHit

# No such file: the defaults of the application are used.
DEFAULTS: AppConfig = load_config(Path(__file__).with_name("no-such-config.toml"))

OPENAI_KEY: str | None = resolve_secret("OPENAI_API_KEY")
OPENCODE_KEY: str | None = resolve_secret("OPENCODE_API_KEY")
OLLAMA_KEY: str | None = resolve_secret("OLLAMA_API_KEY")
ANTHROPIC_KEY: str | None = resolve_secret("ANTHROPIC_API_KEY")
TYPESAFE_KEY: str | None = resolve_secret(DEFAULTS.judge.api_key_env)

requires_openai = pytest.mark.skipif(OPENAI_KEY is None, reason="needs OPENAI_API_KEY")
requires_opencode_go = pytest.mark.skipif(OPENCODE_KEY is None, reason="needs OPENCODE_API_KEY")
requires_ollama_search = pytest.mark.skipif(OLLAMA_KEY is None, reason="needs OLLAMA_API_KEY")
requires_anthropic = pytest.mark.skipif(ANTHROPIC_KEY is None, reason="needs ANTHROPIC_API_KEY")
requires_typesafe = pytest.mark.skipif(TYPESAFE_KEY is None, reason="needs TYPESAFE_API_KEY")

OPENAI_MODEL: str = "gpt-4.1-mini"
# The smallest current Claude model; it thinks, so the round trip also replays a signed thinking block.
ANTHROPIC_MODEL: str = "claude-haiku-5-5"

# One vision model per route; the second Responses model takes the OpenAI-style options (original image detail,
# images inside tool results), the first one the plain ones.
GO_MODELS: list[tuple[GoRoute, str]] = [
    ("chat", "kimi-k3"),
    ("messages", "qwen3.8-flash"),
    ("responses", "grok-4.6"),
    ("responses", "gpt-5.6-luna"),
]


def openai_provider(wire: str) -> ChatProvider:
    assert OPENAI_KEY is not None
    return OpenAIProvider(DEFAULTS.providers.openai.model_copy(update={"wire": wire}), OPENAI_KEY, None, None)


def go_provider() -> ChatProvider:
    assert OPENCODE_KEY is not None
    return OpenCodeGoProvider(DEFAULTS.providers.opencode_go, OPENCODE_KEY)


def anthropic_provider() -> ChatProvider:
    assert ANTHROPIC_KEY is not None
    return AnthropicProvider(DEFAULTS.providers.anthropic, ANTHROPIC_KEY)


async def blue_screenshot_description(provider: ChatProvider, model: str) -> str:
    """The model calls a screenshot tool, gets a solid blue image back as the result and names the color."""
    system = system_message("Answer in one short sentence.")
    ask = user_message("Take a screenshot with the tool, then tell me the single color that fills it.", ())
    first: ModelReply = await first_tool_call_reply(provider, model, (system, ask), (SCREENSHOT_TOOL,))
    call = first.tool_calls[0]
    history = (
        system,
        ask,
        assistant_from_reply(first, f"{provider.name}:{model}"),
        tool_message(call, "screenshot captured", (solid_image(20, 20, 220),)),
    )
    final: ModelReply = await provider.complete(greedy_request_for(model, history, (SCREENSHOT_TOOL,)), ignore_delta)
    return final.text.lower()


@pytest.mark.hosted
@requires_openai
@pytest.mark.parametrize("wire", ["responses", "chat"])
async def test_openai_round_trips_tools_and_reads_screenshots_on_each_wire(wire: str) -> None:
    provider: ChatProvider = openai_provider(wire)
    try:
        final: ModelReply = await tool_round_trip(provider, OPENAI_MODEL)
        described: str = await blue_screenshot_description(provider, OPENAI_MODEL)
    finally:
        await provider.aclose()
    assert "21" in final.text and final.usage.input_tokens > 0
    assert "blue" in described


@pytest.mark.hosted
@requires_opencode_go
@pytest.mark.parametrize(("route", "model"), GO_MODELS)
async def test_opencode_go_round_trips_tools_and_reads_screenshots_on_each_route(route: GoRoute, model: str) -> None:
    assert route_of(model, DEFAULTS.providers.opencode_go.route_overrides) == route
    provider: ChatProvider = go_provider()
    try:
        final: ModelReply = await tool_round_trip(provider, model)
        described: str = await blue_screenshot_description(provider, model)
    finally:
        await provider.aclose()
    assert "21" in final.text and final.usage.input_tokens > 0
    assert "blue" in described


@pytest.mark.hosted
@requires_anthropic
async def test_anthropic_lists_its_models_round_trips_tools_and_reads_screenshots() -> None:
    provider: ChatProvider = anthropic_provider()
    try:
        listed: tuple[ModelInfo, ...] = await provider.list_models()
        final: ModelReply = await tool_round_trip(provider, ANTHROPIC_MODEL)
        described: str = await blue_screenshot_description(provider, ANTHROPIC_MODEL)
    finally:
        await provider.aclose()
    assert ANTHROPIC_MODEL in {info.id for info in listed}
    assert "21" in final.text and final.usage.input_tokens > 0
    assert "blue" in described


@pytest.mark.hosted
@requires_ollama_search
async def test_the_hosted_search_of_ollama_answers_with_titles_and_addresses() -> None:
    assert OLLAMA_KEY is not None
    client: WebClient = WebClient(DEFAULTS.web, (), ())
    try:
        hits: tuple[SearchHit, ...] = await search_ollama(client, OLLAMA_KEY, "Python programming language", 5)
    finally:
        await client.aclose()
    assert 1 <= len(hits) <= 5
    assert all(hit.title and hit.url.startswith(("http://", "https://")) for hit in hits)
    assert any("python" in f"{hit.title} {hit.snippet}".lower() for hit in hits)


@pytest.mark.hosted
@requires_typesafe
async def test_typesafe_judges_click_labels_the_patterns_do_not_know() -> None:
    """Labels no pattern of the policy knows, as the judge gets them (a repost, a share, a payment and a deletion in
    other languages), with the probability of each flag the judge asks about, in its own words: printed for the owner to
    set the thresholds by, and checked only for being probabilities."""
    assert TYPESAFE_KEY is not None
    labels: tuple[str, ...] = ("Boost", "Teilen", "Compartir", "Jetzt bezahlen", "Konto löschen")
    questions: tuple[NoulQuestion, ...] = tuple(question for question, _ in CLICK_QUESTIONS)
    client: TypeSafeClient = TypeSafeClient(DEFAULTS.judge, TYPESAFE_KEY)
    try:
        answers: list[dict[str, float]] = [
            await client.nouls({"control": {"label": label, "role": "button"}, "page": "https://x.com/home"}, questions)
            for label in labels
        ]
    finally:
        await client.aclose()
    for label, probabilities in zip(labels, answers, strict=True):
        print(label, probabilities)
    assert all(set(found) == {question.id for question in questions} for found in answers)
    assert all(0.0 <= value <= 1.0 for found in answers for value in found.values())
