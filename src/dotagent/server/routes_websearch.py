"""Settings › Web search (item 657): the engines web_search asks, in the order the owner puts them, each with what it
needs (a key, a SearXNG address, the web-research server, Dot's own browser) and whether it has it now; the keys of
the search APIs, saved in Dot's ``.env`` as the keys of the AI services are and never sent back (the page sees their
last characters); and a try of one engine, which asks it one search. A new order and a saved key hold from the next
search, with no restart."""

import asyncio
import time

from fastapi import APIRouter
from pydantic import ValidationError

from dotagent.config import WebConfig
from dotagent.config_edit import set_search_order
from dotagent.core.errors import InvalidValueError, NotFoundError, WebError
from dotagent.core.logs import get_logger
from dotagent.core.paths import config_path, env_file_path
from dotagent.core.text import redact_secrets
from dotagent.core.types import FrozenModel
from dotagent.llm.router import parse_model_ref
from dotagent.secrets import SecretSource, SecretState, optional_secret, remove_env_value, secret_state, write_env_value
from dotagent.server.context import ServerContext
from dotagent.server.views import KeyRequest
from dotagent.tools.web import web_research_runs, web_research_search
from dotagent.web.keyed import SEARCH_KEYS
from dotagent.web.ladder import SEARCH_ENGINES, SearchEngine
from dotagent.web.search import AnthropicSearch, engine_attempts, search_ladder
from dotagent.web.types import SearchHit

log = get_logger(__name__)

# What the page says of each engine: its name, and what it is.
_ABOUT: dict[SearchEngine, tuple[str, str]] = {
    "searxng": (
        "SearXNG",
        "Your own SearXNG instance: its engines answer, with no key. Set its address in config.toml.",
    ),
    "ollama": ("Ollama web search", "Ollama's hosted search, with the Ollama key of AI services; each search counts."),
    "anthropic": (
        "Anthropic web search",
        "Claude searches and answers with its sources; paid per search, with the key.",
    ),
    "brave_api": ("Brave Search API", "Brave's own index; the free plan has a monthly allowance."),
    "tavily": ("Tavily", "A search made for agents, with a short answer of each page; a monthly allowance."),
    "exa": ("Exa", "A search by meaning, with a passage of each page and its date."),
    "serper": ("Serper", "Google's results through Serper's API, with their dates."),
    "web_research": (
        "web-research",
        "The server that comes with Dot: DuckDuckGo, Google read in a browser like a person's, and Yahoo, each "
        "result with its date. No key.",
    ),
    "duckduckgo": (
        "DuckDuckGo",
        "DuckDuckGo's page, read as a browser reads it. No key; it may ask to prove a person.",
    ),
    "brave": ("Brave Search page", "Brave Search's page, read as a browser reads it. No key."),
    "browser": (
        "DuckDuckGo in Dot's browser",
        "Opened in Dot's own Chrome, where its check passes. Slower; last resort.",
    ),
}
# The words of a try: a search any engine answers.
_TRY_QUERY: str = "Dot web search test"
_TRY_RESULTS: int = 3


class SearchEngineView(FrozenModel):
    """An engine as the page shows it: ``on`` whether it is in the order, ``ready`` whether it can answer now and, when
    not, ``needs`` what it lacks; a search API's key by its variable's name, where it comes from and its last
    characters."""

    engine: SearchEngine
    label: str
    about: str
    on: bool
    ready: bool
    needs: str | None
    key_name: str | None
    key_source: SecretSource | None
    key_saved: bool
    key_tail: str


class WebSearchView(FrozenModel):
    """The engines in the order web_search asks them (``saved``: the owner's; otherwise the auto ladder), then the ones
    that are off."""

    saved: bool
    engines: tuple[SearchEngineView, ...]


class SearchOrderRequest(FrozenModel):
    order: list[SearchEngine]


class SearchTryView(FrozenModel):
    engine: SearchEngine
    ok: bool
    seconds: float
    titles: tuple[str, ...]
    error: str | None


def websearch_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    # One change of the key file at a time, as Settings › AI services changes it.
    writing: asyncio.Lock = asyncio.Lock()

    def needs(engine: SearchEngine) -> str | None:
        """What ``engine`` lacks to answer now, or ``None``."""
        config = ctx.runtime.config
        if engine in SEARCH_KEYS and engine not in ctx.runtime.search_keys.keyed:
            return "a key"
        if engine == "ollama" and not ctx.runtime.search_key_present:
            return f"the Ollama key ({config.providers.ollama.api_key_env}) in AI services"
        if engine == "anthropic" and not ctx.runtime.anthropic_key_present:
            return f"the Anthropic key ({config.providers.anthropic.api_key_env}) in AI services"
        if engine == "anthropic" and parse_model_ref(config.models.search).provider != "anthropic":
            return "an Anthropic model as [models] search"
        if engine == "searxng" and not config.web.searxng_url:
            return "a SearXNG address ([web] searxng_url in config.toml)"
        if engine == "web_research" and not web_research_runs(ctx.runtime):
            return "the web-research server, which is off or did not start (Customize › Connectors)"
        if engine == "browser" and not ctx.runtime.browser.human_like:
            return "Dot's own browser (Settings › Browser)"
        return None

    def order_now() -> tuple[SearchEngine, ...]:
        """The order web_search asks the engines in now: the owner's, or the auto ladder as it stands."""
        keys = ctx.runtime.search_keys
        if keys.order:
            return keys.order
        config = ctx.runtime.config
        return search_ladder(
            config.web.search_backend,
            ctx.runtime.search_key_present,
            config.web.anthropic_search if ctx.runtime.anthropic_key_present else None,
            ctx.runtime.browser.human_like,
            bool(config.web.searxng_url),
            (),
            keys.keyed,
            web_research_runs(ctx.runtime),
        )

    def view_of(engine: SearchEngine, on: bool) -> SearchEngineView:
        label, about = _ABOUT[engine]
        name: str | None = SEARCH_KEYS.get(engine)
        state: SecretState | None = secret_state(name) if name is not None else None
        lacking: str | None = needs(engine)
        return SearchEngineView(
            engine=engine,
            label=label,
            about=about,
            on=on,
            ready=lacking is None,
            needs=lacking,
            key_name=name,
            key_source=None if state is None else state.source,
            key_saved=False if state is None else state.saved,
            key_tail="" if state is None else state.tail,
        )

    def view_now() -> WebSearchView:
        order: tuple[SearchEngine, ...] = order_now()
        rest: tuple[SearchEngine, ...] = tuple(engine for engine in SEARCH_ENGINES if engine not in order)
        return WebSearchView(
            saved=bool(ctx.runtime.search_keys.order),
            engines=(*(view_of(engine, True) for engine in order), *(view_of(engine, False) for engine in rest)),
        )

    @router.get("/web/search")
    async def web_search_settings() -> WebSearchView:
        """The engines of web_search, in their order, and the off ones after them."""
        return await asyncio.to_thread(view_now)

    @router.put("/web/search/order")
    async def set_order(request: SearchOrderRequest) -> WebSearchView:
        """Keep ``order`` as the engines web_search asks, in turn, from the next search on; the config file keeps it as
        ``[web] search_order``. An empty order is refused: a search must have an engine to ask."""
        if not request.order:
            raise InvalidValueError("a search needs at least one engine", {"order": []})
        try:
            checked: list[SearchEngine] = WebConfig.model_validate(
                {**ctx.runtime.config.web.model_dump(), "search_order": request.order}
            ).search_order
        except ValidationError as error:
            raise InvalidValueError("an order names each engine once", {"order": list(request.order)}) from error
        await asyncio.to_thread(set_search_order, config_path(), list(checked))
        ctx.runtime.search_keys.order = tuple(checked)
        log.info("search_order_set", order=list(checked))
        return await asyncio.to_thread(view_now)

    def key_of(engine: SearchEngine) -> str:
        name: str | None = SEARCH_KEYS.get(engine)
        if name is None:
            raise NotFoundError("this engine takes no key", {"engine": engine, "keyed": list(SEARCH_KEYS)})
        return name

    @router.post("/web/search/keys/{engine}")
    async def save_key(engine: SearchEngine, request: KeyRequest) -> WebSearchView:
        """Save the key of a search API in Dot's ``.env`` file; it holds from the next search on."""
        name: str = key_of(engine)
        async with writing:
            await asyncio.to_thread(write_env_value, env_file_path(), name, request.value)
        await ctx.runtime.search_keys.read_keyed()
        log.info("search_key_saved", engine=engine, name=name)
        return await asyncio.to_thread(view_now)

    @router.delete("/web/search/keys/{engine}")
    async def remove_key(engine: SearchEngine) -> WebSearchView:
        """Remove the key Dot's ``.env`` file holds for a search API. A key from the environment or the Keychain is not
        Dot's to remove: 404."""
        name: str = key_of(engine)
        async with writing:
            removed: bool = await asyncio.to_thread(remove_env_value, env_file_path(), name)
        if not removed:
            raise NotFoundError("Dot's .env file has no entry for this key", {"name": name})
        await ctx.runtime.search_keys.read_keyed()
        log.info("search_key_removed", engine=engine, name=name)
        return await asyncio.to_thread(view_now)

    @router.post("/web/search/try/{engine}")
    async def try_engine(engine: SearchEngine) -> SearchTryView:
        """Ask ``engine`` one search, as web_search would, and say what came back: a few titles, or why it failed (a
        search API counts it against the key's allowance). Not the browser rung, which opens a window."""
        if engine == "browser":
            raise InvalidValueError("the browser rung is tried by a search in a chat, as it opens Dot's window", {})
        lacking: str | None = needs(engine)
        if lacking is not None:
            return SearchTryView(engine=engine, ok=False, seconds=0.0, titles=(), error=f"It needs {lacking}.")
        config = ctx.runtime.config
        keys: dict[SearchEngine, str] = {}
        name: str | None = SEARCH_KEYS.get(engine)
        if name is not None:
            found: str | None = await asyncio.to_thread(optional_secret, name)
            if found is not None:
                keys[engine] = found
        ollama: str | None = (
            await asyncio.to_thread(optional_secret, config.providers.ollama.api_key_env)
            if engine == "ollama"
            else None
        )
        relay: AnthropicSearch | None = None
        if engine == "anthropic":
            anthropic_key: str | None = await asyncio.to_thread(optional_secret, config.providers.anthropic.api_key_env)
            if anthropic_key is not None:
                relay = AnthropicSearch(api_key=anthropic_key, model=parse_model_ref(config.models.search).model)
        attempts = engine_attempts(
            ctx.runtime.web,
            ollama,
            relay,
            None,
            config.web.searxng_url or None,
            keys,
            web_research_search(ctx.runtime) if engine == "web_research" else None,
        )
        started: float = time.monotonic()
        try:
            hits: tuple[SearchHit, ...] = await attempts[engine](_TRY_QUERY, _TRY_RESULTS)
        except WebError as error:
            return SearchTryView(
                engine=engine,
                ok=False,
                seconds=round(time.monotonic() - started, 2),
                titles=(),
                error=redact_secrets(error.message),
            )
        return SearchTryView(
            engine=engine,
            ok=True,
            seconds=round(time.monotonic() - started, 2),
            titles=tuple(hit.title for hit in hits[:_TRY_RESULTS]),
            error=None,
        )

    return router
