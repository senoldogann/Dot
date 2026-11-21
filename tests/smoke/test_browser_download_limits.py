"""A download's size against the cap and the folder's quota, in a real headless Chrome (item 1298): a file past
``download_max_mb`` is cancelled as it comes in and deleted, a folder at its quota takes no more, and a download within
both is saved as it always was."""

import asyncio
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
import pytest_asyncio

from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import PageView, Step
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server

_CHUNK: bytes = b"x" * 1_000_000
# What the stream sends before it stops: far past the 1 MB cap, slowly enough for Chrome to report progress.
_STREAMED_CHUNKS: int = 120


class _Handler(BaseHTTPRequestHandler):
    sent: ClassVar[list[int]] = []

    def do_GET(self) -> None:
        if self.path.startswith("/endless"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="endless.bin"')
            self.end_headers()
            count: int = 0
            try:
                for _ in range(_STREAMED_CHUNKS):
                    self.wfile.write(_CHUNK)
                    count += 1
                    time.sleep(0.03)
            except OSError:
                pass
            finally:
                _Handler.sent.append(count)
            return
        if self.path.startswith("/small"):
            body: bytes = b"small report"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="small.txt"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        page: bytes = (
            b'<html><body><a href="/endless">Download endless</a> <a href="/small">Download small</a></body></html>'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _config(tmp_path_factory: pytest.TempPathFactory, extra: str) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text(f"[browser]\nheadless = true\n{extra}", encoding="utf-8")
    return load_config(settings).browser


async def _session(
    tmp_path_factory: pytest.TempPathFactory, downloads: Path, extra: str
) -> AsyncIterator[BrowserSession]:
    session: BrowserSession = BrowserSession(
        _config(tmp_path_factory, extra), SERVER, tmp_path_factory.mktemp("profile"), downloads, no_runs_at_work
    )
    yield session
    await session.aclose()


@pytest.fixture(scope="module")
def capped_downloads(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("capped")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def capped(tmp_path_factory: pytest.TempPathFactory, capped_downloads: Path) -> AsyncIterator[BrowserSession]:
    async for session in _session(tmp_path_factory, capped_downloads, "download_max_mb = 1\n"):
        yield session


@pytest.fixture(scope="module")
def full_downloads(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder: Path = tmp_path_factory.mktemp("full")
    (folder / "earlier.bin").write_bytes(b"y" * 2_000_000)
    return folder


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def full(tmp_path_factory: pytest.TempPathFactory, full_downloads: Path) -> AsyncIterator[BrowserSession]:
    async for session in _session(tmp_path_factory, full_downloads, "download_quota_mb = 1\n"):
        yield session


def _names(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ref(body: str, label: str) -> str:
    match: re.Match[str] | None = re.search(rf"{re.escape(label)}[^\n]*?\[ref=([^\]]+)\]", body)
    assert match is not None, f"{label!r} not in:\n{body}"
    return match.group(1)


async def _click(session: BrowserSession, thread: str, site: str, label: str) -> str:
    view: PageView = await session.open_url(thread, f"{site}/", True)
    outcome = await session.act(
        thread, [Step(action="click", ref=_ref(view.body, label))], False, refuse_secret_field, True
    )
    notes: list[str] = list(outcome.view.notes)
    for _ in range(100):
        later: PageView = await session.snapshot(thread, True)
        notes.extend(later.notes)
        if any("download" in note and ("saved" in note or "deleted" in note or "failed" in note) for note in notes):
            break
        await asyncio.sleep(0.1)
    return " | ".join(notes)


async def test_a_download_past_the_cap_is_cancelled_as_it_comes_in_and_deleted(
    capped: BrowserSession, capped_downloads: Path, site: str
) -> None:
    notes: str = await _click(capped, "endless", site, 'link "Download endless"')

    assert "endless.bin was cancelled and deleted" in notes, notes
    assert "download_max_mb" in notes, notes
    assert _names(capped_downloads) == []
    # Chrome stopped reading: the server was not made to send all of what it had.
    await asyncio.sleep(0.5)
    assert _Handler.sent and _Handler.sent[-1] < _STREAMED_CHUNKS, _Handler.sent


async def test_a_download_within_the_cap_is_saved_as_before(
    capped: BrowserSession, capped_downloads: Path, site: str
) -> None:
    notes: str = await _click(capped, "small", site, 'link "Download small"')

    assert "download saved to" in notes, notes
    assert _names(capped_downloads) == ["small.txt"]
    assert _text(capped_downloads / "small.txt") == "small report"


async def test_a_folder_at_its_quota_takes_no_more_and_says_why(
    full: BrowserSession, full_downloads: Path, site: str
) -> None:
    notes: str = await _click(full, "quota", site, 'link "Download small"')

    assert "small.txt was cancelled and deleted" in notes, notes
    assert "download_quota_mb" in notes, notes
    assert _names(full_downloads) == ["earlier.bin"]
