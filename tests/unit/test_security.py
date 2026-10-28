"""The security scan (item 781): the forms of a host of the owner's, the payloads it tries, and what a form's answer
shows. Every request goes to a site of this Mac on 127.0.0.1; no model is called and nothing leaves the machine."""

import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import cast
from urllib.parse import parse_qs, urlsplit

from dotagent.config import load_default_config
from dotagent.tools.context import ToolContext
from dotagent.tools.security import SecurityArgs, _run, forms_on
from dotagent.web.client import WebClient

_LOGIN = """<html><body>
<form action="/login" method="post">
  <input name="csrf" type="hidden" value="token">
  <input name="username" type="text">
  <input name="password" type="password">
  <button type="submit">Sign in</button>
</form></body></html>"""

_SEARCH = """<html><body>
<form action="/search" method="post">
  <input name="q" type="search">
  <input type="submit" value="Go">
</form></body></html>"""

_CROSS = """<html><body>
<form action="http://other.example/login" method="post"><input name="q" type="text"></form>
</body></html>"""


def _site() -> tuple[ThreadingHTTPServer, threading.Thread, Counter[str]]:
    hits: Counter[str] = Counter()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
            data: bytes = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            hits["total"] += 1
            path: str = urlsplit(self.path).path
            if path == "/login":
                self._send(200, _LOGIN)
            elif path == "/search":
                self._send(200, _SEARCH)
            elif path == "/cross":
                self._send(200, _CROSS)
            elif path == "/no-forms":
                self._send(200, "<html><body>no form here</body></html>")
            else:
                self._send(404, "not found")

        def do_POST(self) -> None:
            hits["total"] += 1
            hits["post"] += 1
            length: int = int(self.headers.get("Content-Length", "0"))
            data: dict[str, list[str]] = parse_qs(self.rfile.read(length).decode("utf-8"))
            path: str = urlsplit(self.path).path
            if path == "/login":
                name: str = (data.get("username") or [""])[0]
                secret: str = (data.get("password") or [""])[0]
                if "' OR" in name or (name == "admin" and secret == "admin"):
                    self._send(302, "", {"Location": "/dashboard", "Set-Cookie": "sid=1; Path=/"})
                else:
                    self._send(200, "<html><body>Invalid username or password</body></html>")
            elif path == "/search":
                query: str = (data.get("q") or [""])[0]
                if "'" in query:
                    self._send(
                        200,
                        "<html><body>You have an error in your SQL syntax; check the manual</body></html>",
                    )
                elif "<script>" in query:
                    self._send(200, f"<html><body>Results for {query}</body></html>")
                else:
                    self._send(200, "<html><body>No results</body></html>")
            else:
                self._send(404, "not found")

        def log_message(self, format: str, *args: object) -> None:
            """Keep the test's output for the test."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, hits


@contextmanager
def _running_site() -> Iterator[tuple[int, Counter[str]]]:
    server, thread, hits = _site()
    try:
        yield int(server.server_address[1]), hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _context(owned: tuple[str, ...] = ("127.0.0.1",)) -> ToolContext:
    base = load_default_config()
    config = base.model_copy(
        update={
            "recon": base.recon.model_copy(update={"owned_hosts": owned}),
            "web": base.web.model_copy(update={"allow_private_hosts": True}),
        }
    )
    web = WebClient(config.web, config.browser.allowed_hosts, config.browser.blocked_hosts)
    return cast(
        ToolContext,
        SimpleNamespace(runtime=SimpleNamespace(config=config, web=web), output_chars=40_000),
    )


async def test_a_host_that_is_not_the_owners_is_refused() -> None:
    result = await _run(SecurityArgs(target="https://example.com/login"), _context(owned=()))
    assert result.is_error
    assert "[recon] owned_hosts" in result.text


async def test_listing_forms_sends_no_payload() -> None:
    with _running_site() as (port, hits):
        result = await _run(SecurityArgs(target=f"http://127.0.0.1:{port}/login", depth="forms"), _context())
    assert not result.is_error
    assert "username" in result.text
    assert hits["post"] == 0


async def test_a_login_bypass_and_default_credentials_are_found() -> None:
    with _running_site() as (port, _hits):
        result = await _run(SecurityArgs(target=f"http://127.0.0.1:{port}/login", max_attempts=20), _context())
    assert not result.is_error
    assert "Login bypass" in result.text
    assert "Default credentials" in result.text


async def test_a_sql_error_and_reflected_xss_are_found() -> None:
    with _running_site() as (port, _hits):
        result = await _run(SecurityArgs(target=f"http://127.0.0.1:{port}/search", max_attempts=20), _context())
    assert not result.is_error
    assert "SQL injection" in result.text
    assert "Reflected XSS" in result.text


async def test_a_form_that_posts_to_another_host_is_not_tried() -> None:
    with _running_site() as (port, hits):
        result = await _run(SecurityArgs(target=f"http://127.0.0.1:{port}/cross"), _context())
    assert not result.is_error
    assert "another host" in result.text
    assert hits["post"] == 0


async def test_the_attempts_are_capped() -> None:
    with _running_site() as (port, hits):
        result = await _run(SecurityArgs(target=f"http://127.0.0.1:{port}/search", max_attempts=2), _context())
    assert not result.is_error
    assert "2 attempt(s)" in result.text
    assert hits["post"] == 2


def test_forms_keep_hidden_fields_and_the_selected_option() -> None:
    forms = forms_on(
        "https://example.com/",
        """<form action="/x" method="post">
        <input name="csrf" type="hidden" value="t">
        <select name="role"><option value="user">User</option><option value="admin" selected>Admin</option></select>
        <textarea name="note"></textarea>
        </form>""",
    )
    assert len(forms) == 1
    assert forms[0].action == "https://example.com/x"
    assert [(field.name, field.kind, field.value) for field in forms[0].fields] == [
        ("csrf", "hidden", "t"),
        ("role", "select", "admin"),
        ("note", "textarea", ""),
    ]


def test_a_form_on_another_scheme_is_not_kept() -> None:
    assert forms_on("https://example.com/", '<form action="mailto:hello@example.com"></form>') == ()
