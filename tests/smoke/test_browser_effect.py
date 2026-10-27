"""What a click did to the page (item 739), in a real headless Chrome against pages served by a local fixture server.

A click, a double click or Enter that changed nothing (no change to the page's documents, no request, no new address,
tab or dialog) is reported as done but with the page unchanged, with the fields the page marks invalid, so that a run
does not take a click that did nothing for one that worked: a button with no listener, a form a required field held
back. A click that changed the page says nothing more, or what the page says new (an alert) and marks invalid; a click
that only sent a request changed the page. The owner's runs repeated 13 clicks that had done nothing, and waited 20 to
30 s for a thank-you text after forms that had not gone.
"""

import re
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio

from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ActOutcome, PageView, Step
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
_PAGE: str = """<!doctype html><title>Effect page</title>
<button aria-label="Nothing">Nothing</button>
<button aria-label="Change" onclick="document.getElementById('out').textContent = 'changed'">Change</button>
<p id="out">first</p>
<button aria-label="Save" onclick="document.getElementById('status').textContent = 'Saved'">Save</button>
<div role="alert" id="status"></div>
<button aria-label="Ping" onclick="fetch('/ping')">Ping</button>
<form action="/sent"><label>Email <input type="email" name="email" required></label>
<button aria-label="Send">Send</button></form>
<label><input type="checkbox" aria-label="Terms"> Terms</label>
<label>Note <textarea aria-label="Note"></textarea></label>
<label>Size <select aria-label="Size"><option>S</option><option>M</option></select></label>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path: str = urlsplit(self.path).path
        body: bytes = (_PAGE if path == "/" else "<!doctype html><title>Other</title><p>ok</p>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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


def _ref(view: PageView, name: str, role: str = "button") -> str:
    found: re.Match[str] | None = re.search(rf'{role} "{name}"[^\n]*\[ref=([^\]]+)\]', view.body)
    assert found is not None, view.body
    return found.group(1)


async def _click(browser: BrowserSession, thread: str, name: str, role: str = "button") -> str:
    """The detail of the report of a click on the element ``name`` (a button) of the thread's page."""
    view: PageView = await browser.snapshot(thread, True)
    outcome: ActOutcome = await browser.act(
        thread, [Step(action="click", ref=_ref(view, name, role))], False, refuse_secret_field, True
    )
    (report,) = outcome.steps
    assert report.outcome == "done", report
    return report.detail


async def test_a_click_that_changed_nothing_is_told_and_one_that_changed_the_page_is_not(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("effect", f"{site}/", True)
    nothing: str = await _click(browser, "effect", "Nothing")
    assert nothing.startswith("but the page did not change") and "browser_eval" in nothing
    assert await _click(browser, "effect", "Change") == ""
    # A request it sent is a change, though the page shows nothing new.
    assert await _click(browser, "effect", "Ping") == ""


async def test_what_the_page_says_new_and_a_form_a_required_field_held_back_are_told(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("said", f"{site}/", True)
    assert await _click(browser, "said", "Save") == "the page says: Saved"
    held: str = await _click(browser, "said", "Send")
    assert held.startswith("but the page did not change") and "fields it marks invalid: Email" in held
    view: PageView = await browser.snapshot("said", True)
    assert view.url == f"{site}/"


async def test_a_click_that_ticks_a_box_focuses_a_field_or_opens_a_dropdown_changed_the_page(
    browser: BrowserSession, site: str
) -> None:
    """A box ticked, a field focused and a native dropdown opened change no element of the page, and are changes all
    the same: a run told otherwise would click the box again and untick it."""
    await browser.open_url("form", f"{site}/", True)
    assert await _click(browser, "form", "Terms", "checkbox") == ""
    assert await _click(browser, "form", "Note", "textbox") == ""
    assert await _click(browser, "form", "Size", "combobox") == ""


async def test_enter_in_a_field_that_sends_nothing_is_told_though_the_field_keeps_the_focus(
    browser: BrowserSession, site: str
) -> None:
    """Enter pressed in a required field left empty sends no form, and the field keeps the focus: only a click is
    meant to focus a field, so the step says the page did not change."""
    await browser.open_url("enter", f"{site}/", True)
    view: PageView = await browser.snapshot("enter", True)
    outcome: ActOutcome = await browser.act(
        "enter",
        [Step(action="press", key="Enter", ref=_ref(view, "Email", "textbox"))],
        False,
        refuse_secret_field,
        True,
    )
    (report,) = outcome.steps
    assert report.detail.startswith("but the page did not change") and "Email" in report.detail, report
