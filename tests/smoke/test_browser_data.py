"""The data of a page read in one call (item 714), end to end in a real headless Chrome on pages a local fixture server
serves: the calls a page's scripts made in the background are listed newest first and one's JSON is read whole, its
secret keys and parameters hidden in every mode but yolo; the records of a page are read by CSS selectors, an attribute
too, and a field that takes a secret is never read. Both are read-only tools a research run has too."""

import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio

from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import FindResult, TextView
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.tools.registry import tools_for_mode

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
_ITEMS: dict[str, object] = {
    "items": [{"name": "Kettle", "price": 25}, {"name": "Toaster", "price": 40}],
    "session_token": "s3cr3t-session-value",
    "next": 2,
}
_PAGES: dict[str, tuple[str, str]] = {
    "/shop": (
        "text/html; charset=utf-8",
        """<!doctype html><title>Shop</title>
<ul id="list"></ul>
<label>Card number <input autocomplete="cc-number" value="4111111111111111"></label>
<meta name="csrf-token" content="csrf-value-42"><input type="hidden" name="session_id" value="sess-77">
<script>
fetch("/api/items?page=1&api_key=k-123").then((answer) => answer.json()).then((data) => {
  for (const item of data.items) {
    const row = document.createElement("li");
    row.className = "item";
    const link = `<a href="/p/${item.name}">More</a>`;
    row.innerHTML = `<h3>${item.name}</h3><span class="price">${item.price} EUR</span>${link}`;
    document.getElementById("list").append(row);
  }
  document.title = "Shop loaded";
});
</script>""",
    ),
    "/api/items": ("application/json", json.dumps(_ITEMS)),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        kind, text = _PAGES.get(urlsplit(self.path).path, ("text/plain", "not found"))
        body: bytes = text.encode()
        self.send_response(200 if urlsplit(self.path).path in _PAGES else 404)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    browser_config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[BrowserSession]:
    session: BrowserSession = BrowserSession(
        browser_config,
        SERVER,
        tmp_path_factory.mktemp("profile"),
        tmp_path_factory.mktemp("downloads"),
        no_runs_at_work,
    )
    yield session
    await session.aclose()


async def _shop(browser: BrowserSession, site: str, thread: str) -> None:
    await browser.open_url(thread, f"{site}/shop", True)
    await browser.wait_text(thread, "Toaster", 10.0, True)


async def test_the_pages_background_calls_are_listed_and_one_is_read_whole_with_its_secrets_hidden(
    browser: BrowserSession, site: str
) -> None:
    await _shop(browser, site, "calls")
    listed: FindResult = await browser.background_calls("calls", "", 20, True)
    [line] = listed.hits
    assert line.startswith("#0 GET 200 application/json ") and "/api/items?page=1&api_key=[hidden]" in line, line
    assert "k-123" not in line
    assert (await browser.background_calls("calls", "nothing-like-it", 20, True)).hits == ()
    answer: TextView = await browser.call_answer("calls", 0, True)
    read: object = json.loads(answer.text)
    assert read == {**_ITEMS, "session_token": "[hidden]"}
    # yolo hides nothing, as it shows the cookies a script reads.
    whole: TextView = await browser.call_answer("calls", 0, False)
    assert json.loads(whole.text) == _ITEMS and whole.url.endswith("api_key=k-123")
    with pytest.raises(BrowserError, match="no call kept in this tab has that number"):
        await browser.call_answer("calls", 5, True)


async def test_the_records_of_a_page_are_read_by_selectors_and_a_secret_field_never(
    browser: BrowserSession, site: str
) -> None:
    await _shop(browser, site, "records")
    found: TextView = await browser.extract(
        "records", "li.item", {"name": "h3", "price": ".price", "link": "a@href", "gone": ".nothing"}, 50, True
    )
    head, *lines = found.text.splitlines()
    assert head == f"2 record(s) from {site}/shop"
    assert [json.loads(line) for line in lines] == [
        {"name": "Kettle", "price": "25 EUR", "link": "/p/Kettle", "gone": None},
        {"name": "Toaster", "price": "40 EUR", "link": "/p/Toaster", "gone": None},
    ]
    card: TextView = await browser.extract("records", "", {"card": "input@value", "title": "title"}, 50, True)
    assert json.loads(card.text.splitlines()[1]) == {"card": "[hidden]", "title": "Shop loaded"}
    assert "4111" not in card.text
    with pytest.raises(BrowserError, match="a selector could not be read"):
        await browser.extract("records", "li[", {"name": "h3"}, 50, True)


async def test_both_are_read_only_tools_a_research_run_has(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    runtime: Runtime = await build_runtime(load_default_config())
    try:
        for mode in ("research", "task"):
            names: set[str] = {tool.spec.name for tool in tools_for_mode(runtime.registry, mode)}
            assert {"browser_requests", "browser_extract"} <= names, mode
    finally:
        await close_runtime(runtime)


async def test_each_read_holds_the_url_rules_on_either_side(
    browser: BrowserSession, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code review of item 714: the reads went past the URL rules every other read of a tab holds (item 554), so a
    tab that a page's timer or a popup led to a forbidden address was read."""
    await _shop(browser, site, "rules")
    held: list[bool] = []
    enforce = browser._enforce_landing

    async def watched(tab: object, after_read: bool = False) -> None:
        held.append(after_read)
        await enforce(tab, after_read=after_read)  # type: ignore[arg-type]

    monkeypatch.setattr(browser, "_enforce_landing", watched)
    await browser.background_calls("rules", "", 20, True)
    await browser.call_answer("rules", 0, True)
    await browser.extract("rules", "li.item", {"name": "h3"}, 50, True)
    assert held == [False, True, False, True, False, True]


async def test_an_attribute_whose_name_says_it_holds_a_secret_is_hidden_outside_yolo(
    browser: BrowserSession, site: str
) -> None:
    """The code review of item 714: a CSRF token in a meta tag or a session in a hidden input was read whole, which
    browser_requests hides in an answer."""
    await _shop(browser, site, "named")
    fields: dict[str, str] = {"csrf": "meta[name=csrf-token]@content", "session": "input[name=session_id]@value"}
    hidden: TextView = await browser.extract("named", "", fields, 50, True)
    assert json.loads(hidden.text.splitlines()[1]) == {"csrf": "[hidden]", "session": "[hidden]"}
    whole: TextView = await browser.extract("named", "", fields, 50, False)
    assert json.loads(whole.text.splitlines()[1]) == {"csrf": "csrf-value-42", "session": "sess-77"}
