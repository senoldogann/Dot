"""The search APIs that take a key of their own and the order Settings › Web search keeps (item 655): each API is
asked as its documentation says, the key in a header and nowhere else, and its answer read into hits; a limited key
benches the rung, a refused one says so, and one with no key is passed over saying which to set. The order is the
ladder; the auto ladder puts the APIs whose keys are set and the web-research server's search before the scraped
engines."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import WebError, WebThrottledError
from dotagent.doctor import _search_row
from dotagent.web.keyed import (
    SEARCH_KEYS,
    search_brave_api,
    search_exa,
    search_serper,
    search_tavily,
    web_research_hits,
)
from dotagent.web.ladder import EngineBench, SearchEngine, SearchOutcome
from dotagent.web.search import climb, engine_attempts, search_ladder
from dotagent.web.types import FetchedDocument, SearchHit

KEY: str = "test-key-0123456789"


class _Api:
    """A web client that answers every API call with ``status`` and ``answer``, and keeps what it was asked."""

    def __init__(self, answer: object, status: int = 200) -> None:
        self.answer: bytes = json.dumps(answer).encode()
        self.status: int = status
        self.asked: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def _document(self, url: str) -> FetchedDocument:
        return FetchedDocument(
            url=url,
            status=self.status,
            media_type="application/json",
            charset="utf-8",
            body=self.answer,
            truncated=False,
            redirected_from=(),
        )

    async def post_json(self, url: str, headers: dict[str, str], body: bytes, max_bytes: int) -> FetchedDocument:
        self.asked.append(("POST", url, dict(headers), body))
        return self._document(url)

    async def get_json(self, url: str, headers: dict[str, str], max_bytes: int) -> FetchedDocument:
        self.asked.append(("GET", url, dict(headers), None))
        return self._document(url)


def _key_only_in_a_header(api: _Api) -> None:
    """The key is in one header of each request, and in neither its address nor its body."""
    for _method, url, headers, body in api.asked:
        assert KEY not in url
        assert body is None or KEY.encode() not in body
        assert any(KEY in value for value in headers.values())


async def test_brave_api_is_asked_by_get_with_its_token_header_and_its_marks_taken_out() -> None:
    api = _Api(
        {
            "web": {
                "results": [
                    {
                        "title": "<strong>SQLite</strong> FTS5",
                        "url": "https://sqlite.org/fts5.html",
                        "description": "Full-text <strong>search</strong>",
                    }
                ]
            }
        }
    )
    hits = await search_brave_api(api, KEY, "sqlite fts5", 5)  # type: ignore[arg-type]
    assert hits == (SearchHit(title="SQLite FTS5", url="https://sqlite.org/fts5.html", snippet="Full-text search"),)
    method, url, headers, _ = api.asked[0]
    assert method == "GET" and urlsplit(url).netloc == "api.search.brave.com"
    assert parse_qs(urlsplit(url).query) == {"q": ["sqlite fts5"], "count": ["5"]}
    assert headers == {"X-Subscription-Token": KEY}
    _key_only_in_a_header(api)


async def test_tavily_exa_and_serper_are_asked_with_their_bodies_and_read_with_their_dates() -> None:
    tavily = _Api({"results": [{"title": "A", "url": "https://a.example/", "content": "about a"}]})
    assert await search_tavily(tavily, KEY, "q", 3) == (
        SearchHit(title="A", url="https://a.example/", snippet="about a"),
    )  # type: ignore[arg-type]
    assert json.loads(tavily.asked[0][3] or b"") == {"query": "q", "max_results": 3}
    assert tavily.asked[0][2]["Authorization"] == f"Bearer {KEY}"
    exa = _Api(
        {
            "results": [
                {
                    "title": None,
                    "url": "https://b.example/",
                    "highlights": ["one", "two"],
                    "publishedDate": "2026-10-20T08:00:00Z",
                }
            ]
        }
    )
    assert await search_exa(exa, KEY, "q", 3) == (  # type: ignore[arg-type]
        SearchHit(title="https://b.example/", url="https://b.example/", snippet="2026-10-20 · one two"),
    )
    assert exa.asked[0][2] == {"x-api-key": KEY}
    serper = _Api(
        {"organic": [{"title": "C", "link": "https://c.example/", "snippet": "about c", "date": "2026-10-21"}]}
    )
    assert await search_serper(serper, KEY, "q", 3) == (  # type: ignore[arg-type]
        SearchHit(title="C", url="https://c.example/", snippet="2026-10-21 · about c"),
    )
    assert json.loads(serper.asked[0][3] or b"") == {"q": "q", "num": 3}
    for api in (tavily, exa, serper):
        _key_only_in_a_header(api)


async def test_a_limited_key_benches_and_a_refused_key_says_where_to_look() -> None:
    with pytest.raises(WebThrottledError, match="limiting this key"):
        await search_tavily(_Api({}, 429), KEY, "q", 3)  # type: ignore[arg-type]
    with pytest.raises(WebError, match=r"refused the key .*Settings › Web search") as refused:
        await search_serper(_Api({}, 401), KEY, "q", 3)  # type: ignore[arg-type]
    assert KEY not in str(refused.value)


async def test_an_engine_with_no_key_is_passed_over_saying_which_key_to_set() -> None:
    api = _Api({"results": [{"title": "A", "url": "https://a.example/", "content": "about a"}]})
    attempts = engine_attempts(api, None, None, None, None, {"tavily": KEY})  # type: ignore[arg-type]
    outcome: SearchOutcome = await climb(("exa", "tavily"), attempts, EngineBench(), "q", 3, lambda: 0.0)
    assert outcome.engine == "tavily" and [hit.url for hit in outcome.hits] == ["https://a.example/"]
    assert outcome.skipped[0].engine == "exa"
    assert outcome.skipped[0].reason == f"no key: set {SEARCH_KEYS['exa']} in Settings › Web search"


def test_the_web_research_answer_is_read_into_hits_with_their_dates() -> None:
    answer = {
        "results": [
            {"title": "T", "url": "https://t.example/", "snippet": "s", "date": "2026-10-24"},
            {"url": "https://u.example/"},
        ]
    }
    assert web_research_hits(answer, "q", 5) == (
        SearchHit(title="T", url="https://t.example/", snippet="2026-10-24 · s"),
        SearchHit(title="https://u.example/", url="https://u.example/", snippet=""),
    )
    with pytest.raises(WebError, match="does not match its shape"):
        web_research_hits({"results": [{"title": "no address"}]}, "q", 5)


def test_the_order_is_the_ladder_and_the_auto_ladder_takes_the_keyed_apis_and_the_server() -> None:
    order: tuple[SearchEngine, ...] = ("ollama", "web_research", "duckduckgo", "browser")
    # The browser rung only with Dot's own browser, the server's only while it runs; the rest as ordered.
    assert search_ladder("auto", True, None, False, False, order, (), True) == ("ollama", "web_research", "duckduckgo")
    assert search_ladder("auto", True, None, True, False, order, (), False) == ("ollama", "duckduckgo", "browser")
    # No order: the auto ladder, the APIs whose keys are set and the server's search before the scraped engines.
    assert search_ladder("auto", True, None, False, False, (), ("serper", "brave_api"), True) == (
        "ollama",
        "brave_api",
        "serper",
        "web_research",
        "duckduckgo",
        "brave",
    )
    # As before, with no key and no server.
    assert search_ladder("auto", False, None, False, False) == ("duckduckgo", "brave")


def test_the_config_takes_an_order_of_known_engines_each_once() -> None:
    web = load_default_config().web
    assert web.search_order == []
    assert web.model_copy(update={"search_order": ["tavily", "duckduckgo"]}).search_order == ["tavily", "duckduckgo"]
    with pytest.raises(ValueError, match="twice"):
        type(web).model_validate({**web.model_dump(), "search_order": ["tavily", "tavily"]})
    with pytest.raises(ValueError):
        type(web).model_validate({**web.model_dump(), "search_order": ["altavista"]})


async def test_ollama_and_anthropic_with_no_key_in_an_order_are_passed_over_and_the_search_goes_on() -> None:
    """The pre-push review's finding: an order holding Ollama or Anthropic with no key ended every search with the key's
    error; each is passed over now, saying so, as a search API with no key is."""
    api = _Api({"results": [{"title": "A", "url": "https://a.example/", "content": "about a"}]})
    attempts = engine_attempts(api, None, None, None, None, {"tavily": KEY})  # type: ignore[arg-type]
    outcome: SearchOutcome = await climb(
        ("ollama", "anthropic", "tavily"), attempts, EngineBench(), "q", 3, lambda: 0.0
    )
    assert outcome.engine == "tavily"
    assert [(skip.engine, skip.reason.split(":")[0]) for skip in outcome.skipped] == [
        ("ollama", "no key"),
        ("anthropic", "not set up"),
    ]
    assert "Settings › AI services" in outcome.skipped[0].reason


async def test_a_key_an_api_echoes_in_an_error_is_masked() -> None:
    api = _Api({"error": f"invalid request for key {KEY}"}, 400)
    with pytest.raises(WebError) as failed:
        await search_tavily(api, KEY, "q", 3)  # type: ignore[arg-type]
    assert KEY not in str(failed.value.context)


def test_an_order_with_no_rung_that_can_be_climbed_here_leaves_the_search_to_the_ladder() -> None:
    assert search_ladder("auto", False, None, False, False, ("browser", "web_research"), (), False) == (
        "duckduckgo",
        "brave",
    )


async def test_a_web_research_rung_whose_server_stopped_is_passed_over() -> None:
    """The pre-push review's nit: the server checked running for the ladder and gone for the attempts ended the search
    with a KeyError; its rung is passed over now."""
    api = _Api({"results": [{"title": "A", "url": "https://a.example/", "content": "about a"}]})
    attempts = engine_attempts(api, None, None, None, None, {"tavily": KEY}, None)  # type: ignore[arg-type]
    outcome: SearchOutcome = await climb(("web_research", "tavily"), attempts, EngineBench(), "q", 3, lambda: 0.0)
    assert outcome.engine == "tavily" and outcome.skipped[0].engine == "web_research"


def test_the_doctor_shows_the_order_a_run_climbs_and_the_keys_it_lacks() -> None:
    """The pre-push review's finding: ``dotx doctor`` showed the ladder of search_backend whatever the order was."""
    config = load_default_config()
    ordered = config.model_copy(
        update={"web": config.web.model_copy(update={"search_order": ["tavily", "exa", "duckduckgo"]})}
    )
    row = _search_row(ordered, False, False, frozenset({"tavily"}), False)
    assert (
        row.detail
        == "tavily -> exa -> duckduckgo ([web] search_order, Settings › Web search; no key for exa, passed over)"
    )
    auto = _search_row(config, False, False, frozenset({"serper"}), True)
    assert auto.detail.startswith("serper -> web_research -> duckduckgo -> brave")
