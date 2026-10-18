"""The search ladder (item 59): Brave's result page, the climb past benched and failing engines, the bench and the
answer memory, and the header's account of the rungs skipped. A SearXNG instance (item 372) is served on the loopback
address."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import WebError, WebThrottledError
from dotagent.tools.web import skipped_note
from dotagent.web.client import WebClient
from dotagent.web.ladder import BENCH_S, MEMORY_S, AnswerMemory, EngineBench, SearchEngine, SearchOutcome, Skipped
from dotagent.web.search import (
    Attempt,
    brave_address,
    climb,
    parse_brave,
    parse_searxng,
    search_searxng,
    searxng_search_url,
)
from dotagent.web.types import SearchHit

# The result markup of search.brave.com/search, cut to what the parser reads: a web snippet with its link, title and
# description (the class names carry build hashes), a nested block that is not a result, and a snippet that is not web.
_BRAVE_PAGE: str = """<!doctype html><html><body><div id="results">
<div class="snippet svelte-jmfu5f" data-pos="0" data-type="web" data-keynav="true">
<a href="https://www.reaktor.com/careers/locations/helsinki" class="svelte-jz8lnl">
<div class="url-wrapper svelte-on1hvy"><cite>reaktor.com</cite></div>
<div class="title search-snippet-title line-clamp-1 svelte-14r20fy">Helsinki <strong>careers</strong></div></a>
<div class="generic-snippet svelte-1cwdgg3"><div class="content"><div class="snippet-description desktop-small-regular
t-tertiary svelte-on1hvy">We are hiring in <strong>Helsinki</strong>.</div></div></div>
<div class="sitelinks"><a href="https://www.reaktor.com/careers/">Careers</a></div></div>
<div class="snippet svelte-1ajsqxo" data-pos="1" data-type="news"><a href="https://news.example/1">
<div class="title desktop-default-regular line-clamp-1 svelte-1ajsqxo">News item</div></a></div>
<div class="snippet svelte-jmfu5f" data-pos="2" data-type="web"><a href="https://en.wikipedia.org/wiki/Reaktor">
<div class="title svelte-14r20fy">Reaktor - Wikipedia</div></a>
<div class="description desktop-default-regular t-secondary line-clamp-2 svelte-1ajsqxo">A Finnish company.</div></div>
<div class="snippet svelte-jmfu5f" data-pos="3" data-type="web"><a href="https://www.reaktor.com/careers/locations/helsinki">
<div class="title svelte-14r20fy">The same page again</div></a></div>
<div class="snippet svelte-jmfu5f" data-pos="4" data-type="web"><a href="https://search.brave.com/images?q=reaktor">
<div class="title svelte-14r20fy">Brave's own page</div></a></div>
</div></body></html>"""


def test_brave_links_are_absolute_addresses_and_its_own_pages_are_dropped() -> None:
    assert brave_address("https://example.org/page") == "https://example.org/page"
    assert brave_address("//example.org/page") == "https://example.org/page"
    assert brave_address("https://search.brave.com/images?q=x") is None
    assert brave_address("javascript:void(0)") is None


def test_the_brave_page_is_read_in_order_web_results_only_each_address_once() -> None:
    page = parse_brave(_BRAVE_PAGE)
    assert [hit.url for hit in page.hits] == [
        "https://www.reaktor.com/careers/locations/helsinki",
        "https://en.wikipedia.org/wiki/Reaktor",
    ]
    assert page.hits[0].title == "Helsinki careers"
    assert page.hits[0].snippet == "We are hiring in Helsinki."
    assert page.hits[1].snippet == "A Finnish company."


def test_a_brave_page_without_web_results_announces_nothing() -> None:
    wall = parse_brave("<html><body>Your request has been flagged as suspicious</body></html>")
    assert wall.hits == () and not wall.announced_no_results


def _hit(url: str) -> SearchHit:
    return SearchHit(title="t", url=url, snippet="")


def _answering(url: str) -> Attempt:
    async def attempt(query: str, max_results: int) -> tuple[SearchHit, ...]:
        return (_hit(url),)

    return attempt


def _failing(error: WebError) -> Attempt:
    async def attempt(query: str, max_results: int) -> tuple[SearchHit, ...]:
        raise error

    return attempt


_RUNGS: tuple[SearchEngine, ...] = ("duckduckgo", "brave", "browser")


async def test_a_throttled_engine_is_benched_and_the_next_rung_answers() -> None:
    bench: EngineBench = EngineBench()
    clock: list[float] = [100.0]
    attempts: dict[SearchEngine, Attempt] = {
        "duckduckgo": _failing(WebThrottledError("DuckDuckGo is limiting the searches of this machine", {})),
        "brave": _answering("https://brave.example/1"),
        "browser": _answering("https://browser.example/1"),
    }
    first: SearchOutcome = await climb(_RUNGS, attempts, bench, "q", 5, lambda: clock[0])
    assert first.engine == "brave" and [hit.url for hit in first.hits] == ["https://brave.example/1"]
    assert first.skipped == (
        Skipped(engine="duckduckgo", reason="DuckDuckGo is limiting the searches of this machine"),
    )
    # The next search within the bench does not ask DuckDuckGo at all; after the bench it does.
    clock[0] += BENCH_S["duckduckgo"] / 2
    second: SearchOutcome = await climb(_RUNGS, attempts, bench, "q2", 5, lambda: clock[0])
    assert second.skipped[0].engine == "duckduckgo" and "benched for 5 more min" in second.skipped[0].reason
    clock[0] += BENCH_S["duckduckgo"]
    assert bench.remaining("duckduckgo", clock[0]) is None


async def test_a_plain_failure_is_skipped_without_a_bench_and_the_last_rung_may_answer() -> None:
    bench: EngineBench = EngineBench()
    attempts: dict[SearchEngine, Attempt] = {
        "duckduckgo": _failing(WebError("DuckDuckGo did not answer the search", {"status": 500})),
        "brave": _failing(WebError("Brave Search did not answer the search", {"status": 500})),
        "browser": _answering("https://browser.example/1"),
    }
    outcome: SearchOutcome = await climb(_RUNGS, attempts, bench, "q", 5, lambda: 0.0)
    assert outcome.engine == "browser" and len(outcome.skipped) == 2
    assert bench.remaining("duckduckgo", 0.0) is None and bench.remaining("brave", 0.0) is None


async def test_when_every_rung_fails_the_error_names_each_engine_and_its_reason() -> None:
    attempts: dict[SearchEngine, Attempt] = {
        "duckduckgo": _failing(WebThrottledError("DuckDuckGo is limiting the searches of this machine", {})),
        "brave": _failing(WebThrottledError("Brave Search is limiting the searches of this machine", {})),
    }
    with pytest.raises(WebError) as failure:
        await climb(("duckduckgo", "brave"), attempts, EngineBench(), "q", 5, lambda: 0.0)
    assert failure.value.message == (
        "duckduckgo: DuckDuckGo is limiting the searches of this machine; "
        "brave: Brave Search is limiting the searches of this machine"
    )
    assert failure.value.context["engines"] == ["duckduckgo", "brave"]


def test_the_memory_answers_the_same_query_with_its_age_and_forgets_old_ones() -> None:
    memory: AnswerMemory = AnswerMemory()
    outcome: SearchOutcome = SearchOutcome(hits=(_hit("https://a.example"),), engine="brave", skipped=(), age_s=0.0)
    memory.keep("Bittium  careers", outcome, 1000.0)
    recalled: SearchOutcome | None = memory.recall("bittium careers", 1000.0 + 90.0)
    assert recalled is not None and recalled.age_s == 90.0 and recalled.hits == outcome.hits
    assert memory.recall("bittium careers", 1000.0 + MEMORY_S + 1.0) is None
    assert memory.recall("other", 1000.0) is None


def test_the_header_accounts_for_the_rungs_skipped_and_a_remembered_answer() -> None:
    fresh: SearchOutcome = SearchOutcome(hits=(), engine="duckduckgo", skipped=(), age_s=0.0)
    assert skipped_note(fresh) == ""
    skipped: SearchOutcome = SearchOutcome(
        hits=(), engine="brave", skipped=(Skipped(engine="duckduckgo", reason="benched for 7 more min"),), age_s=130.0
    )
    assert skipped_note(skipped) == (
        " (duckduckgo skipped: benched for 7 more min; the same query was answered 2 min ago; this is that answer)"
    )


# The JSON answer of a SearXNG instance, cut to what the parser reads: two engines' results, one address twice, a
# result whose link is no page, one without a title and one whose snippet carries the page's line breaks.
_SEARXNG_ANSWER: bytes = rb"""{"query": "reaktor", "results": [
  {"url": "https://www.reaktor.com/", "title": "Reaktor", "content": "A Finnish technology company.",
   "engines": ["google cse", "duckduckgo"]},
  {"url": "https://www.reaktor.com/", "title": "Reaktor again", "content": "The same page.", "engines": ["brave"]},
  {"url": "javascript:void(0)", "title": "Not a page", "content": ""},
  {"url": "https://example.org/no-title", "title": "", "content": "A result without a title."},
  {"url": "https://example.org/careers", "title": "Careers", "content": "Open\nroles  in Helsinki."}
]}"""


def test_searxng_url_carries_the_json_format_and_an_escaped_query() -> None:
    assert (
        searxng_search_url("http://127.0.0.1:8888/", "a b&c")
        == "http://127.0.0.1:8888/search?q=a+b%26c&format=json&safesearch=0"
    )


def test_the_searxng_answer_is_read_in_order_each_address_once() -> None:
    page = parse_searxng(_SEARXNG_ANSWER)
    assert [hit.url for hit in page.hits] == ["https://www.reaktor.com/", "https://example.org/careers"]
    assert page.hits[0].title == "Reaktor"
    assert page.hits[0].snippet == "A Finnish technology company."
    assert page.hits[1].snippet == "Open roles in Helsinki."
    assert not page.announced_no_results


def test_a_searxng_answer_with_no_results_announces_it_and_another_body_is_an_error() -> None:
    empty = parse_searxng(b'{"query": "nothing", "results": []}')
    assert empty.hits == () and empty.announced_no_results
    with pytest.raises(WebError):
        parse_searxng(b"<html>not json</html>")


def test_a_searxng_answer_whose_engines_all_refused_is_an_error_not_an_empty_page() -> None:
    refused = b'{"query": "x", "results": [], "unresponsive_engines": [["brave", "Suspended: too many requests"]]}'
    with pytest.raises(WebError):
        parse_searxng(refused)
    # An engine without its reason is not the shape SearXNG writes: the answer is refused as a whole.
    with pytest.raises(WebError, match="without the JSON of a search"):
        parse_searxng(b'{"query": "x", "results": [], "unresponsive_engines": [["brave"]]}')


class _SearxHandler(BaseHTTPRequestHandler):
    """Four instances under one address: ``/ok`` answers the search, ``/blank`` answers with results that name no page,
    ``/off`` has the JSON format off (SearXNG's 403 for a format its settings do not list) and ``/busy`` is limiting
    this machine (its limiter's 429)."""

    def do_GET(self) -> None:
        instance: str = self.path.split("/")[1]
        status: int = {"ok": 200, "blank": 200, "off": 403, "busy": 429}[instance]
        body: bytes = (
            _SEARXNG_ANSWER
            if instance == "ok"
            else b'{"results": [{"url": "javascript:void(0)", "title": "Not a page"}]}'
            if instance == "blank"
            else b"<!doctype html><title>Refused</title>"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if status == 200 else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def searx() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _SearxHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


async def test_a_searxng_instance_answers_and_a_limit_is_told_from_a_format_turned_off(searx: str) -> None:
    client: WebClient = WebClient(load_default_config().web, (), ())
    try:
        hits = await search_searxng(client, f"{searx}/ok", "reaktor", 1)
        assert [hit.url for hit in hits] == ["https://www.reaktor.com/"]
        # A limit benches the rung for a while; a format turned off is the instance's settings, so it says which.
        with pytest.raises(WebThrottledError):
            await search_searxng(client, f"{searx}/busy", "reaktor", 5)
        with pytest.raises(WebError, match=r"list json in search\.formats") as refused:
            await search_searxng(client, f"{searx}/off", "reaktor", 5)
        assert not isinstance(refused.value, WebThrottledError)
        # Results that name no page are no answer: the next rung is asked, and this one is not benched.
        with pytest.raises(WebError, match="name no page") as blank:
            await search_searxng(client, f"{searx}/blank", "reaktor", 5)
        assert not isinstance(blank.value, WebThrottledError)
    finally:
        await client.aclose()
