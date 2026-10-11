"""The local site of an eval task (item 54): a few pages on a loopback port for the length of an attempt, so that a
browser or research task depends on no network and no account. The goal and the reference of such a task name the
site as ``{site}``; the runner puts the address in."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl

from pydantic import JsonValue

from dotagent.evals.types import EvalTask, ScriptedCall, ScriptedTurn, SitePage

SITE_TOKEN: str = "{site}"
_NOT_FOUND: str = "<!doctype html><title>Not found</title><h1>Not found</h1><p>There is no such page.</p>"


class _Query(dict[str, str]):
    """The query string as the page's placeholders read it: a name it does not hold is filled with nothing."""

    def __missing__(self, key: str) -> str:
        return ""


def render_page(page: SitePage, query: str) -> str:
    """The body of ``page`` with its placeholders filled from ``query`` (the part of the address after ``?``)."""
    return page.body.format_map(_Query(parse_qsl(query, keep_blank_values=True)))


class _Site(ThreadingHTTPServer):
    def __init__(self, pages: tuple[SitePage, ...]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.pages: dict[str, SitePage] = {page.path: page for page in pages}


class _Handler(BaseHTTPRequestHandler):
    server: _Site

    def do_GET(self) -> None:
        path, _, query = self.path.partition("?")
        page: SitePage | None = self.server.pages.get(path)
        if page is None:
            self._send(404, _NOT_FOUND, None)
        elif page.location is not None:
            self._send(page.status, "", page.location)
        else:
            self._send(page.status, render_page(page, query), None)

    def _send(self, status: int, body: str, location: str | None) -> None:
        data: bytes = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if location is not None:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def serve_site(pages: tuple[SitePage, ...]) -> Iterator[str]:
    """Serve ``pages`` on a free loopback port until the block ends, and give their address (no server and an empty
    address for a task without a site)."""
    if not pages:
        yield ""
        return
    server: _Site = _Site(pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _placed(value: JsonValue, address: str) -> JsonValue:
    if isinstance(value, str):
        return value.replace(SITE_TOKEN, address)
    if isinstance(value, list):
        return [_placed(item, address) for item in value]
    if isinstance(value, dict):
        return {key: _placed(item, address) for key, item in value.items()}
    return value


def with_site(task: EvalTask, address: str) -> EvalTask:
    """The task with ``address`` in place of ``{site}`` in its goal and in the arguments of its reference."""
    reference: tuple[ScriptedTurn, ...] = tuple(
        turn.model_copy(
            update={
                "calls": tuple(
                    ScriptedCall(
                        tool=call.tool,
                        arguments={key: _placed(value, address) for key, value in call.arguments.items()},
                    )
                    for call in turn.calls
                )
            }
        )
        for turn in task.reference
    )
    return task.model_copy(update={"goal": task.goal.replace(SITE_TOKEN, address), "reference": reference})
