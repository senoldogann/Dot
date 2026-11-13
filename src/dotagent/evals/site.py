"""The local site of an eval task (item 54): a few pages on a loopback port for the length of an attempt, so that a
browser or research task depends on no network and no account. The goal and the reference of such a task name the
site as ``{site}``; the runner puts the address in. A site may take POSTs too (item 1147): it keeps what each of its
endpoints was sent, so that a task is judged by what its site took."""

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl

from pydantic import JsonValue

from dotagent.evals.types import EvalTask, ScriptedCall, ScriptedTurn, SiteEndpoint, SitePage, SiteRecord

SITE_TOKEN: str = "{site}"
_NOT_FOUND: str = "<!doctype html><title>Not found</title><h1>Not found</h1><p>There is no such page.</p>"


class _Query(dict[str, str]):
    """The query string as the page's placeholders read it: a name it does not hold is filled with nothing."""

    def __missing__(self, key: str) -> str:
        return ""


def render_page(page: SitePage, query: str) -> str:
    """The body of ``page`` with its placeholders filled from ``query`` (the part of the address after ``?``)."""
    return page.body.format_map(_Query(parse_qsl(query, keep_blank_values=True)))


def render_answer(endpoint: SiteEndpoint, fields: dict[str, str], count: int) -> str:
    """What ``endpoint`` answers to a request that sent ``fields``, the ``count``-th it took."""
    return endpoint.answer.format_map(_Query({**fields, "count": str(count)}))


class _Site(ThreadingHTTPServer):
    def __init__(self, pages: tuple[SitePage, ...], endpoints: tuple[SiteEndpoint, ...]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.pages: dict[str, SitePage] = {page.path: page for page in pages}
        self.endpoints: dict[str, SiteEndpoint] = {endpoint.path: endpoint for endpoint in endpoints}
        self._records: list[SiteRecord] = []
        self._lock: threading.Lock = threading.Lock()

    def take(self, path: str, fields: dict[str, str]) -> int:
        """Keep a request an endpoint took, and say how many its path has taken with it."""
        with self._lock:
            self._records.append(SiteRecord(path=path, fields=fields))
            return sum(1 for record in self._records if record.path == path)

    def taken(self) -> tuple[SiteRecord, ...]:
        """Every request the endpoints took so far, in the order they came."""
        with self._lock:
            return tuple(self._records)


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

    def do_POST(self) -> None:
        """Take a form an endpoint was sent: it is kept as it arrives, and answered once the endpoint's time has
        passed, so that a second request sent while the first waits is a second record."""
        path, _, _ = self.path.partition("?")
        endpoint: SiteEndpoint | None = self.server.endpoints.get(path)
        length: str = self.headers.get("Content-Length", "0")
        if endpoint is None or not length.isdigit():
            self._send(404 if endpoint is None else 400, _NOT_FOUND, None)
            return
        sent: str = self.rfile.read(int(length)).decode("utf-8", errors="replace")
        fields: dict[str, str] = dict(parse_qsl(sent, keep_blank_values=True))
        count: int = self.server.take(path, fields)
        time.sleep(endpoint.delay_s)
        self._send(200, render_answer(endpoint, fields, count), None)

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


def _nothing_taken() -> tuple[SiteRecord, ...]:
    return ()


@dataclass(frozen=True)
class ServedSite:
    """A task's site while it is served: its address (empty for a task without a site) and ``taken``, the requests its
    endpoints took so far."""

    address: str
    taken: Callable[[], tuple[SiteRecord, ...]]


@contextmanager
def serve_recording_site(pages: tuple[SitePage, ...], endpoints: tuple[SiteEndpoint, ...]) -> Iterator[ServedSite]:
    """Serve ``pages`` and ``endpoints`` on a free loopback port until the block ends (no server, an empty address and
    nothing taken for a task without a site)."""
    if not pages and not endpoints:
        yield ServedSite(address="", taken=_nothing_taken)
        return
    server: _Site = _Site(pages, endpoints)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield ServedSite(address=f"http://127.0.0.1:{server.server_address[1]}", taken=server.taken)
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def serve_site(pages: tuple[SitePage, ...]) -> Iterator[str]:
    """Serve ``pages`` on a free loopback port until the block ends, and give their address (no server and an empty
    address for a task without a site)."""
    with serve_recording_site(pages, ()) as served:
        yield served.address


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
