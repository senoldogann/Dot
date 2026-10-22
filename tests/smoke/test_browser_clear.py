"""Clearing a profile of Dot's own Chrome signs every site out of it (item 532): a real headless Chrome on a throwaway
profile is let fill every kind of storage a site has, closed, cleared, and started again, and the page finds nothing of
what it stored, as the Settings button says. A control that is not cleared finds all of it, so the page really kept it.
What the profile holds besides stays, and Chrome starts on the profile cleared."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dotagent.browser.profiles import clear_site_data
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ScriptRun
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

SERVER: ServerConfig = load_default_config().server
_SCRIPT_S: float = 20.0
_PAGE: bytes = b"<!doctype html><title>Site</title><p>A site that keeps a sign-in in every place it can.</p>"
_FILL: str = """async () => {
  document.cookie = "signed_in=yes; path=/; max-age=3600";
  localStorage.setItem("token", "abc");
  await new Promise((resolve, reject) => {
    const open = indexedDB.open("store", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("rows");
    open.onsuccess = () => {
      const transaction = open.result.transaction("rows", "readwrite");
      transaction.objectStore("rows").put("value", "key");
      transaction.oncomplete = () => { open.result.close(); resolve(); };
    };
    open.onerror = () => reject(open.error);
  });
  const cache = await caches.open("shell");
  await cache.put("/asset", new Response("asset"));
  return "filled";
}"""
_READ: str = """async () => ({
  cookie: document.cookie,
  token: localStorage.getItem("token"),
  databases: (await indexedDB.databases()).map((one) => one.name),
  caches: await caches.keys(),
})"""
_EVERYTHING: dict[str, object] = {
    "cookie": "signed_in=yes",
    "token": "abc",
    "databases": ["store"],
    "caches": ["shell"],
}
_NOTHING: dict[str, object] = {"cookie": "", "token": None, "databases": [], "caches": []}


class _Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def config(tmp_path: Path) -> BrowserConfig:
    settings: Path = tmp_path / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


async def _script(session: BrowserSession, thread: str, site: str, script: str) -> object:
    await session.open_url(thread, f"{site}/", True)
    run: ScriptRun = await session.run_script(thread, script, _SCRIPT_S)
    assert run.end == "value", run.text
    return json.loads(run.text)


async def _stored_then_found_again(
    config: BrowserConfig, profile: Path, downloads: Path, site: str, clear: bool
) -> object:
    """What a page finds of the storage it filled once Chrome has closed, ``clear`` has been applied to the profile (or
    not), and a Chrome starts on it again."""
    first: BrowserSession = BrowserSession(config, SERVER, profile, downloads, no_runs_at_work)
    try:
        assert await _script(first, "fill", site, _FILL) == "filled"
    finally:
        await first.aclose()
    if clear:
        clear_site_data(profile)
    again: BrowserSession = BrowserSession(config, SERVER, profile, downloads, no_runs_at_work)
    try:
        return await _script(again, "read", site, _READ)
    finally:
        await again.aclose()


async def test_a_profile_that_is_not_cleared_keeps_all_the_storage_a_site_filled(
    config: BrowserConfig, tmp_path: Path, site: str
) -> None:
    found: object = await _stored_then_found_again(config, tmp_path / "kept", tmp_path / "downloads", site, clear=False)
    assert found == _EVERYTHING


async def test_clearing_a_profile_leaves_a_site_none_of_its_storage_and_keeps_the_history(
    config: BrowserConfig, tmp_path: Path, site: str
) -> None:
    profile: Path = tmp_path / "cleared"
    found: object = await _stored_then_found_again(config, profile, tmp_path / "downloads", site, clear=True)
    assert found == _NOTHING
    # The history and the settings are the profile's, not the sites': they stay, and Chrome wrote them again.
    assert (profile / "Default" / "History").is_file()
    assert (profile / "Default" / "Preferences").is_file()
