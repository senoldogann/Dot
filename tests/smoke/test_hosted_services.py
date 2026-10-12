"""The real hosted services with the user's own keys: OpenAI on both wires, OpenCode Go on all three routes (and its
prompt cache on the Messages route), the Claude models of Anthropic, the hosted web search of Ollama and TypeSafe's typed
judgments.

``test_wire_codecs.py`` makes the same calls against a local Ollama that stands in for these services. Here every test
sends a handful of tiny requests (cents at most) and is skipped when its key cannot be resolved (environment, the
``.env`` file of the Dot home or the Keychain), so a machine without keys still runs the whole suite.
"""

import time
from pathlib import Path

import pytest
from test_wire_codecs import (
    SCREENSHOT_TOOL,
    first_tool_call_reply,
    greedy_request_for,
    ignore_delta,
    request_for,
    solid_image,
    tool_round_trip,
)

from dotagent.agent.judge import CLAIM_QUESTION, CLICK_QUESTIONS
from dotagent.agent.verify import new_facts, render_facts
from dotagent.config import AppConfig, GoRoute, load_config
from dotagent.core.types import JsonObject
from dotagent.llm.anthropic import AnthropicProvider
from dotagent.llm.openai import OpenAIProvider
from dotagent.llm.opencode_go import OpenCodeGoProvider, route_of
from dotagent.llm.types import (
    ChatProvider,
    ChatRequest,
    ModelInfo,
    ModelReply,
    assistant_from_reply,
    system_message,
    tool_message,
    user_message,
)
from dotagent.llm.typesafe import NoulQuestion, TypeSafeClient
from dotagent.secrets import resolve_secret
from dotagent.tools.judge_items import Item, noul_questions, state_of
from dotagent.tools.posts import Post, equal_weights, post_questions, posts_state, ranked_posts
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
# images inside tool results), the first one the plain ones; Claude on the Messages route thinks behind a switch and
# replays its signed thinking.
GO_MODELS: list[tuple[GoRoute, str]] = [
    ("chat", "kimi-k3"),
    ("messages", "qwen3.8-flash"),
    ("messages", "claude-haiku-5-5"),
    ("responses", "grok-4.6"),
    ("responses", "gpt-5.6-luna"),
]
# Claude on Go reads a repeated prompt from the cache only when the prompt is marked for it (measured 2026-10-12).
GO_CACHED_MODEL: str = "claude-haiku-5-5"
# A system prompt above every model's least cacheable length (about ten thousand tokens).
CACHED_PROMPT: str = " ".join(f"Rule {number}: answer in one short sentence." for number in range(1, 1000))


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
@requires_opencode_go
async def test_opencode_go_reads_a_repeated_claude_prompt_from_the_cache() -> None:
    request: ChatRequest = request_for(
        GO_CACHED_MODEL, (system_message(CACHED_PROMPT), user_message("Say ok.", ())), ()
    )
    provider: ChatProvider = go_provider()
    try:
        await provider.complete(request, ignore_delta)
        second: ModelReply = await provider.complete(request, ignore_delta)
    finally:
        await provider.aclose()
    assert second.usage.cached_tokens > 0, second.usage


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
async def test_typesafe_flags_click_labels_the_patterns_do_not_know() -> None:
    """Labels no pattern of the policy knows, on the pages they belong to, as the judge gets them (a repost, a share, a
    payment and a deletion in other languages): each one gets its flag at ``public_action_threshold``, since a missed
    flag lets the click through without its question in ask and auto. The probabilities are printed."""
    assert TYPESAFE_KEY is not None
    clicks: tuple[tuple[str, str, str], ...] = (
        ("Boost", "https://mastodon.social/home", "public_post"),
        ("Teilen", "https://www.facebook.com/", "public_post"),
        ("Compartir", "https://www.linkedin.com/feed/", "public_post"),
        ("Jetzt bezahlen", "https://www.amazon.de/checkout", "financial"),
        ("Konto löschen", "https://x.com/settings/deactivate", "destructive"),
    )
    questions: tuple[NoulQuestion, ...] = tuple(question for question, _ in CLICK_QUESTIONS)
    client: TypeSafeClient = TypeSafeClient(DEFAULTS.judge, TYPESAFE_KEY)
    try:
        answers: list[dict[str, float]] = [
            await client.nouls({"control": {"label": label, "role": "button"}, "page": page}, questions)
            for label, page, _ in clicks
        ]
    finally:
        await client.aclose()
    for (label, _, _), probabilities in zip(clicks, answers, strict=True):
        print(label, probabilities)
    threshold: float = DEFAULTS.judge.public_action_threshold
    assert all(found[flag] >= threshold for (_, _, flag), found in zip(clicks, answers, strict=True))


def _claim(claim: str) -> JsonObject:
    """A claim of a campaign on X as the campaign tool hands it to the judge: the goal, the targets and how they are
    verified, the ledger, what the session's calls did (nothing the goal checker would hold against it) and the claim."""
    return {
        "goal": "Grow the X account @dot_example to 500 followers.",
        "targets": "1. The account @dot_example has at least 500 followers.\nHow they are verified: Open "
        "https://x.com/dot_example and read the follower count on the profile.",
        "ledger": "Progress recorded in the campaign's ledger:\n- 3 days ago: 212 followers\n- yesterday: 431 "
        "followers\n- 2 hours ago: 488 followers",
        "session_facts": render_facts(new_facts(), (), ()),
        "claim": claim,
    }


@pytest.mark.hosted
@requires_typesafe
async def test_typesafe_lets_a_true_campaign_claim_through_and_stops_one_resting_on_a_hope() -> None:
    """Two claims of the same campaign: one that the session's own read of the profile shows, at or above
    ``target_threshold``, and one that rests on posts still to come, below it. A true claim the judge refused would keep
    a campaign from ending; the probabilities are printed."""
    assert TYPESAFE_KEY is not None
    client: TypeSafeClient = TypeSafeClient(DEFAULTS.judge, TYPESAFE_KEY)
    try:
        read: dict[str, float] = await client.nouls(
            _claim(
                "I opened https://x.com/dot_example in this session and read '512 Followers' on the profile; the target "
                "of 500 is reached."
            ),
            (CLAIM_QUESTION,),
        )
        hoped: dict[str, float] = await client.nouls(
            _claim(
                "I opened https://x.com/dot_example in this session and read '488 Followers'. With tonight's scheduled "
                "posts we will pass 500, so the target is reached."
            ),
            (CLAIM_QUESTION,),
        )
    finally:
        await client.aclose()
    print("read", read, "hoped", hoped)
    assert read[CLAIM_QUESTION.id] >= DEFAULTS.judge.target_threshold > hoped[CLAIM_QUESTION.id]


_ON_TOPIC: tuple[str, ...] = (
    "uv 0.5 ships a lockfile format that replaces requirements.txt; here is how we migrated our monorepo",
    "Why wheels beat sdists: a short history of Python packaging and what PEP 517 fixed",
    "pip 25 drops the legacy resolver for good. Pin your builds before you upgrade",
    "We cut our Docker image by 400 MB by building Python wheels in a separate stage",
    "Poetry or uv in 2026? A comparison of lockfiles, workspaces and publish flows",
)
_OFF_TOPIC: tuple[str, ...] = (
    "My cat learned to open the fridge and now nothing is safe",
    "Weekend hike photos from the Alps, finally some snow",
    "Rust's borrow checker finally clicked for me after three years",
    "Our team moved to a four-day week; a thread on what changed",
    "Best espresso grinder under 300 euros? Asking for a friend",
    "The new Kubernetes release notes are out; the gateway API is stable",
    "Just shipped dark mode to our React app, took one afternoon with CSS variables",
)


@pytest.mark.hosted
@requires_typesafe
async def test_typesafe_ranks_the_posts_on_a_topic_above_every_other_in_one_request() -> None:
    """Twelve posts, five on Python packaging, as judge_items sends them: one request, one question per post. Every
    on-topic post has to come out above every off-topic one; the probabilities and the time of the request are
    printed, since they set ``[judge] items_per_request``."""
    assert TYPESAFE_KEY is not None
    items: tuple[Item, ...] = tuple(
        Item(id=f"p{number}", text=text) for number, text in enumerate([*_ON_TOPIC, *_OFF_TOPIC], 1)
    )
    question: str = "Is this post about Python packaging (installing, building or publishing Python packages)?"
    client: TypeSafeClient = TypeSafeClient(DEFAULTS.judge, TYPESAFE_KEY)
    started: float = time.monotonic()
    try:
        answers: dict[str, float] = await client.nouls(state_of(items), noul_questions(question, items))
    finally:
        await client.aclose()
    print(f"{time.monotonic() - started:.2f} s", answers)
    on_topic: list[float] = [answers[f"p{number}"] for number in range(1, len(_ON_TOPIC) + 1)]
    off_topic: list[float] = [answers[f"p{number}"] for number in range(len(_ON_TOPIC) + 1, len(items) + 1)]
    assert min(on_topic) > max(off_topic), (on_topic, off_topic)


# A labeled sample of a feed for an account on Python packaging: ten posts worth answering (on topic, by a person, with
# something to add to) and ten not (off topic, bots and brands, closed statements). The owner's own labeled feed is to
# replace it (ROADMAP open decision 27).
_ANSWERABLE: tuple[tuple[str, str], ...] = (
    ("@mara", "Why does uv lock give me a different tree than pip freeze? Lost an afternoon to this."),
    ("@tomas", "Our wheels build fine on Linux but the macOS arm64 one fails on a missing symbol. Anyone seen this?"),
    ("@jules", "Is there any reason to keep setup.py around now that pyproject.toml covers everything?"),
    ("@ines", "Moving a 40-package monorepo to uv workspaces. What broke for you?"),
    (
        "@dev_sam",
        "Our CI pins with pip-compile but devs use uv locally; the two lockfiles drift. How do people handle it?",
    ),
    (
        "@priya",
        "First time publishing to PyPI with trusted publishing; the OIDC step keeps failing on the tag workflow.",
    ),
    ("@kofi", "Hot take: sdists should be optional for pure-Python packages. Change my mind."),
    ("@lena", "Does anyone ship a private index with uv? Looking at devpi vs a plain static bucket."),
    ("@omar", "Spent the morning on a hatchling vs setuptools decision for a new library and still unsure."),
    ("@yuki", "Why is my wheel 80 MB? It only has pure Python in it."),
)
_NOT_ANSWERABLE: tuple[tuple[str, str], ...] = (
    ("@dealsbot", "GIVEAWAY: retweet and follow to win a laptop! Winner announced Friday."),
    ("@acme_cloud", "Acme Cloud is proud to announce our Q3 earnings call. Join us at 9am ET."),
    ("@sofia", "Weekend hike photos from the Alps, finally some snow."),
    ("@grumpyrust", "Rust's borrow checker finally clicked for me after three years."),
    ("@coffeeguy", "Best espresso grinder under 300 euros? Asking for a friend."),
    ("@newsfeed_ai", "Top 10 Python libraries you MUST know in 2026 (thread) 1/10"),
    ("@pep8bot", "Reminder: PEP 8 says 79 characters. Daily reminder #1842."),
    ("@karl", "Shipped dark mode to our React app, one afternoon with CSS variables."),
    ("@python_quotes", '"Readability counts." - Tim Peters'),
    ("@liam", "Our team moved to a four-day week; thread on what changed."),
)


@pytest.mark.hosted
@requires_typesafe
async def test_typesafe_ranks_the_answerable_posts_of_a_feed_at_the_top() -> None:
    """The labeled sample above, ranked as rank_posts does it with equal weights: the precision of the top ten is
    printed and has to be at least 0.8 (eight of the ten are posts worth answering)."""
    assert TYPESAFE_KEY is not None
    posts: tuple[Post, ...] = tuple(
        Post(id=f"p{number}", author=author, text=text)
        for number, (author, text) in enumerate([*_ANSWERABLE, *_NOT_ANSWERABLE], 1)
    )
    answerable: frozenset[str] = frozenset(f"p{number}" for number in range(1, len(_ANSWERABLE) + 1))
    client: TypeSafeClient = TypeSafeClient(DEFAULTS.judge, TYPESAFE_KEY)
    started: float = time.monotonic()
    try:
        answers: dict[str, float] = await client.nouls(posts_state("Python packaging", posts), post_questions(posts))
    finally:
        await client.aclose()
    ranked = ranked_posts(answers, posts, equal_weights())
    top: list[str] = [item.id for item in ranked[: len(_ANSWERABLE)]]
    precision: float = len(set(top) & answerable) / len(top)
    print(f"{time.monotonic() - started:.2f} s, precision@10 {precision:.2f}")
    for item in ranked:
        print(item.id, f"{item.score:.2f}", {name: round(value, 2) for name, value in item.answers.items()})
    assert precision >= 0.8, top
