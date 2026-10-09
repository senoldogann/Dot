"""Web tools end to end: a local fixture server stands in for pages, redirects, PDFs and failures, and one read-only
search reaches the public internet.

Covers what the model relies on: pages as clean fenced text, paging with ``start``, PDFs, text in other character sets,
redirects checked hop by hop, refusal of private addresses, error pages that point to the browser, and search results
that stay inside a fence.
"""

import asyncio
import re
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.config import load_config
from dotagent.core.errors import DotError, WebError, WebTransportError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.web.client import WebClient

pytestmark = pytest.mark.asyncio(loop_scope="module")

_CUT_BYTES: int = 30000
_TURKISH_SENTENCE: str = "Şehir ağaçları çiçek açtı."
_PDF_SENTENCE: str = "Dot fixture PDF sentence"
_FENCE: re.Pattern[str] = re.compile(
    r'<web_content nonce="([0-9a-f]{16})" url="[^"\n]+">\n(.*)\n</web_content nonce="\1">', re.DOTALL
)


@dataclass(frozen=True)
class _Reply:
    status: int
    content_type: str
    body: bytes
    location: str | None


def _html(body: str) -> _Reply:
    return _Reply(200, "text/html; charset=utf-8", body.encode(), None)


def _pdf(sentence: str) -> bytes:
    """A one-page PDF with ``sentence`` in Helvetica; the offsets of its cross-reference table are computed."""
    stream: bytes = f"BT /F1 18 Tf 72 720 Td ({sentence}) Tj ET".encode()
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
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


_ARTICLE: str = """<!doctype html><html lang="en"><head><title>Fixture article</title>
<meta name="description" content="A page for the web tools."></head><body>
<nav><a href="/">Home</a> <a href="/about">About us</a></nav>
<main><h1>Fixture article</h1>
<ul><li><a lang="de" hreflang="de" href="/de">Deutsch</a></li><li><a lang="fr" hreflang="fr" href="/fr">Français</a></li></ul>
<p>First paragraph with a <a href="/more">relative link</a>.</p>
<h2>Steps</h2><ul><li>Install</li><li>Configure</li></ul>
<table><tr><th>Name</th><th>Value</th></tr><tr><td>alpha</td><td>1</td></tr></table>
</main><footer>Footer text</footer></body></html>"""
_LONG: str = (
    "<!doctype html><title>Long</title><main>"
    + "".join(f"<p>Paragraph {number}: {'lorem ipsum ' * 60}</p>" for number in range(60))
    + "</main>"
)
_NOT_FOUND: _Reply = _Reply(404, "text/html; charset=utf-8", b"<!doctype html><title>Gone</title><p>Nothing.</p>", None)
_REPLIES: dict[str, _Reply] = {
    "/article": _html(_ARTICLE),
    "/long": _html(_LONG),
    "/doc.pdf": _Reply(200, "application/pdf", _pdf(_PDF_SENTENCE), None),
    "/latin": _Reply(
        200, "text/html; charset=iso-8859-9", f"<!doctype html><p>{_TURKISH_SENTENCE}</p>".encode("iso-8859-9"), None
    ),
    "/meta-charset": _Reply(
        200,
        "text/html",
        f'<!doctype html><meta charset="windows-1254"><p>{_TURKISH_SENTENCE}</p>'.encode("cp1254"),
        None,
    ),
    "/blob": _Reply(200, "application/octet-stream", bytes(range(256)) * 4, None),
    "/forbidden": _Reply(403, "text/html; charset=utf-8", b"<!doctype html><title>No</title><p>Denied.</p>", None),
    "/redirect": _Reply(302, "text/plain", b"", "/article"),
    "/loop": _Reply(302, "text/plain", b"", "/loop"),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        port: int = self.server.server_address[1]
        reply: _Reply = (
            _Reply(302, "text/plain", b"", f"http://127.0.0.1:{port}/article")
            if path == "/hop"
            else _REPLIES.get(path, _NOT_FOUND)
        )
        self.send_response(reply.status)
        self.send_header("Content-Type", reply.content_type)
        if reply.location is not None:
            self.send_header("Location", reply.location)
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        self.wfile.write(reply.body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _Recorder:
    """Stands in for the run gate: lets every action through."""

    async def authorize(self, info: ActionInfo) -> str:
        return ""

    def add_instruction(self, text: str) -> None:
        return None


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    path.write_text(f"[web]\nallow_private_hosts = true\nfetch_max_bytes = {_CUT_BYTES}\n", encoding="utf-8")
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(settings: Path, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(tmp_path_factory.mktemp("home")))
    built: Runtime = await build_runtime(load_config(settings))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _call(runtime: Runtime, name: str, arguments: JsonObject) -> tuple[ToolResult, ActionInfo]:
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        run_id="web-test",
        thread_id="web-test",
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=_Recorder(),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
    )
    prepared = await runtime.registry.tools[name].prepare(arguments, ctx)
    return await prepared.run(ctx), prepared.action


async def test_fetch_gives_clean_text_inside_a_fence(runtime: Runtime, site: str) -> None:
    result, action = await _call(runtime, "web_fetch", {"url": f"{site}/article"})
    assert not result.is_error
    assert result.untrusted
    assert (action.tool, action.kind, action.flags) == ("web_fetch", "network", ())
    assert "HTTP 200, text/html" in result.text
    fence: re.Match[str] | None = _FENCE.search(result.text)
    assert fence is not None, result.text
    inside: str = fence.group(2)
    assert "Title: Fixture article" in inside
    assert "Description: A page for the web tools." in inside
    assert "# Fixture article" in inside
    assert f"[relative link]({site}/more)" in inside
    assert "- Install\n- Configure" in inside
    assert "alpha | 1" in inside
    for left_out in ("Deutsch", "Français", "About us", "Footer text"):
        assert left_out not in result.text


async def test_long_documents_come_in_parts_that_fit(runtime: Runtime, site: str) -> None:
    parts: list[str] = []
    start: int = 0
    for _ in range(20):
        result, _ = await _call(runtime, "web_fetch", {"url": f"{site}/long", "start": start})
        assert not result.is_error
        assert len(result.text) <= runtime.config.agent.tool_output_chars
        parts.append(result.text)
        more: re.Match[str] | None = re.search(r"call web_fetch again with start=(\d+)", result.text)
        if more is None:
            break
        assert int(more.group(1)) > start
        start = int(more.group(1))
    else:
        pytest.fail("the document never ended")
    assert len(parts) >= 3
    assert "Paragraph 0:" in parts[0]
    assert f"The download was cut at {_CUT_BYTES} bytes" in parts[-1]
    past_the_end, _ = await _call(runtime, "web_fetch", {"url": f"{site}/article", "start": 100000})
    assert past_the_end.is_error
    assert "past the end" in past_the_end.text


async def test_pdf_text_is_read(runtime: Runtime, site: str) -> None:
    result, _ = await _call(runtime, "web_fetch", {"url": f"{site}/doc.pdf"})
    assert not result.is_error
    assert "application/pdf" in result.text
    assert _PDF_SENTENCE in result.text


async def test_other_character_sets_are_decoded(runtime: Runtime, site: str) -> None:
    for path in ("/latin", "/meta-charset"):
        result, _ = await _call(runtime, "web_fetch", {"url": f"{site}{path}"})
        assert _TURKISH_SENTENCE in result.text, path


async def test_redirects_are_followed_and_loops_end(runtime: Runtime, site: str) -> None:
    result, _ = await _call(runtime, "web_fetch", {"url": f"{site}/redirect"})
    assert "after 1 redirect(s)" in result.text
    assert "# Fixture article" in result.text
    with pytest.raises(WebError, match="too many redirects"):
        await _call(runtime, "web_fetch", {"url": f"{site}/loop"})


async def test_failures_are_reported_with_a_way_forward(runtime: Runtime, site: str) -> None:
    blob, _ = await _call(runtime, "web_fetch", {"url": f"{site}/blob"})
    assert blob.is_error
    assert "not a page, a PDF or a text file" in blob.text
    forbidden, _ = await _call(runtime, "web_fetch", {"url": f"{site}/forbidden"})
    assert forbidden.is_error
    assert forbidden.untrusted
    assert "HTTP 403" in forbidden.text
    assert "browser_open" in forbidden.text
    missing, _ = await _call(runtime, "web_fetch", {"url": f"{site}/missing"})
    assert missing.is_error
    assert "HTTP 404" in missing.text
    assert "browser_open" not in missing.text


async def test_addresses_on_the_block_list_are_refused_before_any_request(runtime: Runtime) -> None:
    with pytest.raises(DotError, match="not an address Dot may open"):
        await _call(runtime, "web_fetch", {"url": "http://169.254.169.254/latest/meta-data"})


async def test_private_addresses_are_refused_unless_allowed(
    site: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    strict: WebClient = WebClient(load_config(tmp_path_factory.mktemp("strict") / "none.toml").web, (), ())
    try:
        for url in (
            f"{site}/article",
            "http://localhost:9/",
            "http://[::1]:9/",
            "http://[::ffff:127.0.0.1]:9/",
            "http://2130706433:9/",
            "http://0.0.0.0:9/",
            "http://10.1.2.3/",
            "http://192.168.0.1/",
            "http://169.254.169.254/",
        ):
            with pytest.raises(WebError, match="private or local network"):
                await strict.get(url, 1000)
    finally:
        await strict.aclose()


async def test_every_redirect_hop_is_checked(site: str, settings: Path) -> None:
    port: str = site.rsplit(":", 1)[1]
    client: WebClient = WebClient(load_config(settings).web, (), ("127.0.0.1",))
    try:
        with pytest.raises(WebError, match="will not open this address"):
            await client.get(f"http://localhost:{port}/hop", 100000)
    finally:
        await client.aclose()


async def test_a_refused_connection_is_tried_twice_and_the_error_names_its_cause(settings: Path) -> None:
    closed: socket.socket = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port: int = closed.getsockname()[1]
    closed.close()
    client: WebClient = WebClient(load_config(settings).web, (), ())
    started: float = time.perf_counter()
    try:
        with pytest.raises(WebTransportError) as failure:
            await client.get(f"http://127.0.0.1:{port}/", 1000)
    finally:
        await client.aclose()
    assert time.perf_counter() - started >= 0.15  # the pause between the two tries is 0.2 to 0.4 s
    assert failure.value.context["error_type"] == "ConnectError"
    assert "ConnectionRefusedError" in str(failure.value.context["error"])


@pytest.mark.internet
async def test_search_results_stay_inside_a_fence_and_pages_can_be_read(runtime: Runtime) -> None:
    found, action = await _call(runtime, "web_search", {"query": "Python programming language", "max_results": 5})
    assert not found.is_error
    assert found.untrusted
    assert (action.kind, action.target, action.flags) == ("network", "duckduckgo", ())
    assert re.search(r'<web_content nonce="[0-9a-f]{16}" url="web_search">', found.text)
    assert re.findall(r"^\s+https?://\S+$", found.text, re.MULTILINE)
    page, _ = await _call(runtime, "web_fetch", {"url": "https://www.iana.org/help/example-domains"})
    assert not page.is_error
    assert "Example Domains" in page.text
