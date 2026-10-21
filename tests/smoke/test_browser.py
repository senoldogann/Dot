"""Browser use end to end: a real headless Chrome drives pages served by a local fixture server.

Covers what the model relies on: compact snapshots with refs and viewport pruning, delta views after actions, batches
that stop on surprises, popups, dialogs, downloads, iframes, cookie banners, secret-field refusal, URL rules, parallel
threads and late-rendering pages.
"""

import asyncio
import os
import re
import signal
import statistics
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.browser.render import render_page_view
from dotagent.browser.safety import parse_element, refuse_secret_field
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ActOutcome, PageView, Step, TextWait
from dotagent.browser.upstream import ignore_repeated_frame_detach
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError
from dotagent.policy.elements import Element
from dotagent.tools.base import ToolResult
from dotagent.tools.browser import wait_text_result

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

# Whose interface the browser never opens: the daemon's default address.
SERVER: ServerConfig = load_default_config().server
_SLOW_REST_S: float = 8.0
_LATE_HEADERS_S: float = 3.0

# How late the pages of /opens-late answer: within the wait for a new tab (one second), and after it.
_LATE_TAB_S: float = 0.4
_LATER_TAB_S: float = 1.6
_ITEMS: str = "".join(f"<li><button>Item {n}</button></li>" for n in range(300))
_NUMBERS: str = "".join(f"<option{' selected' if n == 250 else ''}>Number {n}</option>" for n in range(300))
# The states arrive 600 ms after the click, the way a script fills a dropdown from a request.
_LOAD_STATES: str = (
    "setTimeout(() => ['Ohio', 'Texas'].forEach((name) => "
    "document.querySelector('[name=state]').add(new Option(name))), 600)"
)
_PAGES: dict[str, str] = {
    "/": f"""<!doctype html><title>Home</title>
<h1>Dot fixture</h1>
<button onclick="document.getElementById('n').textContent =
  String(Number(document.getElementById('n').textContent) + 1)">Increment</button>
<span id="n">0</span>
<a href="/popup" target="_blank">Open popup</a>
<button onclick="alert('saved!')">Say alert</button>
<button onclick="document.getElementById('c').textContent =
  confirm('Proceed?') ? 'accepted' : 'declined'">Ask confirm</button>
<span id="c">unanswered</span>
<a href="/file.txt">Download report</a>
<iframe src="/frame" title="inner" width="400" height="120"></iframe>
<input type="password" aria-label="Account password">
<ul>{_ITEMS}</ul>""",
    "/form": """<!doctype html><title>Form</title>
<form action="/done" method="get">
<label>Name <input name="name"></label>
<label>Color <select name="color"><option value="red">Red</option><option value="blue">Blue</option></select></label>
<label><input type="checkbox" name="agree"> I agree</label>
<button type="submit">Submit form</button>
</form>""",
    "/dropdown": f"""<!doctype html><title>Dropdown</title>
<label>Shipping <select name="ship"><option value="std">Standard (3-5 days)</option>
<option value="exp">Express (next day)</option><option value="pick" disabled>Pick up in store</option></select></label>
<label>State <select name="state"><option value="">Choose a state</option></select></label>
<button onclick="{_LOAD_STATES}">Load states</button>
<label>Number <select name="number">{_NUMBERS}</select></label>""",
    "/done": """<!doctype html><title>Done</title><h1>Received</h1><p id="q"></p>
<script>document.getElementById('q').textContent = decodeURIComponent(location.search)</script>""",
    "/popup": """<!doctype html><title>Popup</title><h1>Popup page</h1><button>Close me</button>
<button onclick="window.close()">Close now</button>""",
    # Tabs whose page answers late (item 342): Playwright reports a tab only once its page has answered, long after the
    # click returns, and the later one after the batch has stopped waiting for it.
    "/opens-late": """<!doctype html><title>Opens late</title>
<a href="/late-tab" target="_blank">Open late tab</a><a href="/later-tab" target="_blank">Open later tab</a>
<button>Stay</button>""",
    "/late-tab": "<!doctype html><title>Late tab</title><h1>Late tab</h1>",
    "/later-tab": "<!doctype html><title>Later tab</title><h1>Later tab</h1>",
    # A popup that does its job and closes at once, the way a sign-in callback does (item 57).
    "/opener": (
        '<!doctype html><title>Opener</title><a href="/selfclose" target="_blank">Sign in</a><button>Stay</button>'
    ),
    "/selfclose": "<!doctype html><title>Callback</title><script>window.close()</script>",
    "/frame": """<!doctype html><title>Frame</title>
<button onclick="document.getElementById('o').textContent = 'clicked inside frame'">Frame button</button>
<span id="o"></span>""",
    "/slow": """<!doctype html><title>Slow</title><div id="root"></div>
<script>setTimeout(() => { document.getElementById('root').innerHTML =
'<h1>Loaded late</h1><button>Late button</button><a href="/">one</a><a href="/">two</a>' }, 350)</script>""",
    # A hidden copy of the awaited text comes first, the way a template or a closed menu would.
    "/later": """<!doctype html><title>Later</title><p hidden>Order placed</p>
<button onclick="setTimeout(() => {
  document.getElementById('s').textContent = 'Order placed' }, 800)">Place order</button>
<p id="s">Working</p>""",
    # The page leaves half a second after the click, for one whose end comes only after _SLOW_REST_S.
    "/leaving": """<!doctype html><title>Leaving</title>
<button onclick="setTimeout(() => { location.href = '/half-page' }, 500)">Leave</button>""",
    # The page leaves half a second after the click for another host, whose answer comes after _LATE_HEADERS_S.
    "/to-blocked": """<!doctype html><title>Leaving</title>
<button onclick="setTimeout(() => {
  location.href = `http://localhost:${location.port}/late-headers` }, 500)">Leave</button>""",
    "/late-headers": "<!doctype html><title>Blocked</title><h1>Blocked page</h1>",
    # An editor that takes text only through beforeinput, as Draft.js and Lexical do, and drops what comes before it is
    # ready, ?ready= milliseconds after it takes the focus (30 by default); it shows a change ?apply= milliseconds after
    # the input (at once by default).
    "/editor": """<!doctype html><title>Editor</title>
<div id="ed" contenteditable="true" role="textbox" aria-label="Post text"
  style="min-height:40px;border:1px solid"></div>
<p id="holds">Editor holds: </p>
<input aria-label="Plain field">
<div contenteditable="true"><p>First note</p><p>Second note</p><input aria-label="Inner field"></div>
<script>
const ed = document.getElementById('ed');
const readyMs = Number(new URLSearchParams(location.search).get('ready') ?? '30');
const applyMs = Number(new URLSearchParams(location.search).get('apply') ?? '0');
let ready = false, model = '';
const render = () => {
  ed.replaceChildren(document.createTextNode(model));
  document.getElementById('holds').textContent = 'Editor holds: ' + model;
  if (document.activeElement !== ed) return;
  const range = document.createRange();
  range.selectNodeContents(ed);
  range.collapse(false);
  getSelection().removeAllRanges();
  getSelection().addRange(range);
};
ed.addEventListener('focus', () => { ready = false; setTimeout(() => { ready = true; render(); }, readyMs); });
ed.addEventListener('beforeinput', (event) => {
  event.preventDefault();
  if (!ready) return;
  const all = model !== '' && getSelection().toString() === model;
  const apply = () => {
    if (event.inputType === 'insertText') model = (all ? '' : model) + (event.data ?? '');
    else if (event.inputType.startsWith('delete')) model = all ? '' : model.slice(0, -1);
    render();
  };
  if (applyMs) setTimeout(apply, applyMs); else apply();
});
</script>""",
    # A document in designMode is editable everywhere and has no editor: no element is contenteditable.
    "/design": """<!doctype html><title>Design</title><p>Old text</p><input aria-label="Name">
<script>document.designMode = 'on'</script>""",
    # A search box built on contenteditable whose Enter opens the results.
    "/search-box": """<!doctype html><title>Search</title>
<div contenteditable="true" role="textbox" aria-label="Search">cats</div>
<script>
const box = document.querySelector('[role=textbox]');
box.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  location.href = '/done?q=' + encodeURIComponent(box.textContent);
});
</script>""",
    "/board": """<!doctype html><title>Board</title>
<ul aria-label="To do"><li draggable="true" id="a">Write tests</li><li draggable="true" id="b">Ship it</li></ul>
<ul aria-label="Done" style="min-height:40px;border:1px solid"><li>Plan</li></ul>
<p id="moved">Nothing moved</p>
<script>
for (const item of document.querySelectorAll('[draggable=true]'))
  item.addEventListener('dragstart', (event) => event.dataTransfer.setData('text/plain', item.id));
for (const list of document.querySelectorAll('ul')) {
  list.addEventListener('dragover', (event) => event.preventDefault());
  list.addEventListener('drop', (event) => {
    event.preventDefault();
    const item = document.getElementById(event.dataTransfer.getData('text/plain'));
    list.appendChild(item);
    document.getElementById('moved').textContent = `Moved ${item.textContent} to ${list.getAttribute('aria-label')}`;
  });
}
</script>""",
    "/colon": """<!doctype html><title>Colon</title>
<button>Buy now: confirm</button><a href="/done?x=1">Read: more</a> | <a href="javascript:void(0)">Script link</a>""",
    "/cookies": """<!doctype html><title>Cookies</title>
<div id="onetrust-banner-sdk" style="position:fixed;inset:0;background:#fffe;z-index:9999">
<p>We use cookies</p><button>Accept all</button>
<button onclick="this.parentElement.remove()">Reject all</button></div>
<button onclick="document.getElementById('r').textContent = 'page action done'">Page action</button>
<span id="r"></span>""",
    "/modal": """<!doctype html><title>Modal</title>
<button onclick="document.getElementById('m').textContent = 'bought'">Buy now</button>
<span id="m"></span>
<div role="dialog" aria-label="Newsletter" style="position:fixed;inset:0;background:#fff;z-index:9999">
<p>Get our weekly deals</p><input aria-label="Email"><button>Subscribe</button>
<button onclick="this.parentElement.remove()">No thanks</button></div>""",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        if path == "/late-headers":
            time.sleep(_LATE_HEADERS_S)
        if path == "/late-tab":
            time.sleep(_LATE_TAB_S)
        if path == "/later-tab":
            time.sleep(_LATER_TAB_S)
        if path == "/half-page":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<!doctype html><title>Half</title><h1>Half a page</h1>")
            self.wfile.flush()
            time.sleep(_SLOW_REST_S)
            self.wfile.write(b"<p>The rest</p>")
            return
        if path == "/file.txt":
            body: bytes = b"report body"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", 'attachment; filename="../evil/report.txt"')
        elif path in _PAGES:
            body = _PAGES[path].encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        else:
            body = b"not found"
            self.send_response(404)
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
def downloads(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("downloads")


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    browser_config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory, downloads: Path
) -> AsyncIterator[BrowserSession]:
    session: BrowserSession = BrowserSession(
        browser_config, SERVER, tmp_path_factory.mktemp("profile"), downloads, no_runs_at_work
    )
    yield session
    await session.aclose()


def ref_of(body: str, label: str) -> str:
    match: re.Match[str] | None = re.search(rf"{re.escape(label)}[^\n]*?\[ref=([^\]]+)\]", body)
    assert match is not None, f"{label!r} not in:\n{body}"
    return match.group(1)


def value_of(body: str, label: str) -> str:
    """What the snapshot shows after the ref of the first element ``label``: a field's value, an element's text."""
    match: re.Match[str] | None = re.search(rf"{re.escape(label)}[^\n]*?\[ref=[^\]]+\]: (.*)$", body, re.M)
    assert match is not None, f"{label!r} with a value not in:\n{body}"
    return match.group(1)


def saved_files(directory: Path) -> list[Path]:
    return sorted(directory.iterdir())


async def act(browser: BrowserSession, thread: str, steps: list[Step], accept: bool) -> ActOutcome:
    return await browser.act(thread, steps, accept, refuse_secret_field)


async def test_snapshot_has_refs_and_prunes_what_is_far_below_the_fold(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("prune", f"{site}/")
    assert view.title == "Home"
    assert view.status == 200
    assert ref_of(view.body, 'button "Increment"')
    assert "Item 250" not in view.body
    assert view.offscreen_refs > 150


async def test_click_returns_only_what_changed(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("delta", f"{site}/")
    outcome: ActOutcome = await act(
        browser, "delta", [Step(action="click", ref=ref_of(view.body, 'button "Increment"'))], False
    )
    assert [report.outcome for report in outcome.steps] == ["done"]
    assert outcome.stopped is None
    assert outcome.view.mode == "delta"
    assert '"1"' in outcome.view.body or ": 1" in outcome.view.body
    assert len(outcome.view.body) < len(view.body) / 2


async def test_form_batch_fills_selects_checks_and_submits(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("form", f"{site}/form")
    steps: list[Step] = [
        Step(action="fill", ref=ref_of(view.body, 'textbox "Name"'), text="Ada"),
        Step(action="select", ref=ref_of(view.body, 'combobox "Color"'), text="blue"),
        Step(action="check", ref=ref_of(view.body, 'checkbox "I agree"')),
        Step(action="click", ref=ref_of(view.body, 'button "Submit form"')),
    ]
    outcome: ActOutcome = await act(browser, "form", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done"] * 4, outcome
    assert outcome.view.title == "Done"
    assert "name=Ada" in outcome.view.body
    assert "color=blue" in outcome.view.body


async def test_batch_stops_when_the_page_opens_a_tab_and_the_tab_can_be_used(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("popup", f"{site}/")
    steps: list[Step] = [
        Step(action="click", ref=ref_of(view.body, 'link "Open popup"')),
        Step(action="click", ref=ref_of(view.body, 'button "Increment"')),
    ]
    outcome: ActOutcome = await act(browser, "popup", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "skipped"]
    assert outcome.stopped is not None
    assert "new tab" in outcome.stopped
    assert len(outcome.view.tabs) == 2
    switched: PageView = await browser.switch_tab("popup", 2)
    assert switched.title == "Popup"
    assert 'button "Close me"' in switched.body
    back: PageView = await browser.close_tab("popup", 2)
    assert back.title == "Home"


async def test_a_tab_whose_page_answers_late_still_stops_the_batch(browser: BrowserSession, site: str) -> None:
    """Chrome tells of the tab before the click returns; Playwright reports it only once its page has answered. The
    batch waited 40 ms for it before (item 342) and went on with its next step on the page it was on."""
    view: PageView = await browser.open_url("late-tab", f"{site}/opens-late")
    steps: list[Step] = [
        Step(action="click", ref=ref_of(view.body, 'link "Open late tab"')),
        Step(action="click", ref=ref_of(view.body, 'button "Stay"')),
    ]
    outcome: ActOutcome = await act(browser, "late-tab", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "skipped"]
    assert outcome.stopped == "a new tab opened; use browser_switch_tab to work in it"
    assert [tab.title for tab in outcome.view.tabs] == ["Opens late", "Late tab"]


async def test_a_tab_still_opening_when_the_wait_ends_stops_the_batch_and_joins_later(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("later-tab", f"{site}/opens-late")
    steps: list[Step] = [
        Step(action="click", ref=ref_of(view.body, 'link "Open later tab"')),
        Step(action="click", ref=ref_of(view.body, 'button "Stay"')),
    ]
    outcome: ActOutcome = await act(browser, "later-tab", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "skipped"]
    assert outcome.stopped is not None and outcome.stopped.startswith("a new tab is opening")
    assert len(outcome.view.tabs) == 1
    await asyncio.sleep(_LATER_TAB_S)
    again: PageView = await browser.snapshot("later-tab")
    assert [tab.title for tab in again.tabs] == ["Opens late", "Later tab"]
    assert "a new tab opened as tab 2" in again.notes


async def test_a_popup_that_closes_itself_is_a_note_not_an_error(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("callback", f"{site}/opener")
    outcome: ActOutcome = await act(
        browser, "callback", [Step(action="click", ref=ref_of(view.body, 'link "Sign in"'))], False
    )
    assert outcome.steps[0].outcome == "done"
    # The popup is adopted and closes, or closes before Dot could adopt it; either way the thread keeps one tab, no act
    # failed, and a later act works on the opener.
    assert len(outcome.view.tabs) == 1
    stayed: ActOutcome = await act(
        browser, "callback", [Step(action="click", ref=ref_of(outcome.view.body, 'button "Stay"'))], False
    )
    assert [report.outcome for report in stayed.steps] == ["done"]
    assert stayed.view.title == "Opener"


async def test_a_tab_that_closes_under_a_batch_stops_it_without_a_failure_and_shows_the_current_tab(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("closing", f"{site}/")
    await act(browser, "closing", [Step(action="click", ref=ref_of(view.body, 'link "Open popup"'))], False)
    popup: PageView = await browser.switch_tab("closing", 2)
    steps: list[Step] = [
        Step(action="click", ref=ref_of(popup.body, 'button "Close now"')),
        Step(action="click", ref=ref_of(popup.body, 'button "Close me"')),
    ]
    outcome: ActOutcome = await act(browser, "closing", steps, False)
    assert "failed" not in [report.outcome for report in outcome.steps]
    assert outcome.steps[1].outcome == "skipped"
    assert outcome.stopped is not None and outcome.stopped.startswith("the tab was closed by the page")
    assert "the tab now current is shown below" in outcome.stopped
    assert outcome.view.title == "Home" and len(outcome.view.tabs) == 1


async def test_dialogs_are_resolved_and_reported(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("dialog", f"{site}/")
    alert: ActOutcome = await act(
        browser, "dialog", [Step(action="click", ref=ref_of(view.body, 'button "Say alert"'))], False
    )
    assert any("alert dialog accepted: saved!" in note for note in alert.view.notes)
    ask_ref: str = ref_of(view.body, 'button "Ask confirm"')
    declined: ActOutcome = await act(browser, "dialog", [Step(action="click", ref=ask_ref)], False)
    assert any("confirm dialog dismissed" in note for note in declined.view.notes)
    assert "declined" in (await browser.read_text("dialog", None, 0, 4000)).text
    accepted: ActOutcome = await act(browser, "dialog", [Step(action="click", ref=ask_ref)], True)
    assert any("confirm dialog accepted" in note for note in accepted.view.notes)
    assert "accepted" in (await browser.read_text("dialog", None, 0, 4000)).text


async def test_download_is_saved_under_a_safe_name(browser: BrowserSession, site: str, downloads: Path) -> None:
    view: PageView = await browser.open_url("download", f"{site}/")
    outcome: ActOutcome = await act(
        browser, "download", [Step(action="click", ref=ref_of(view.body, 'link "Download report"'))], False
    )
    assert outcome.stopped is not None
    later: PageView = await browser.snapshot("download")
    notes: str = " ".join((*outcome.view.notes, *later.notes))
    assert "download saved to" in notes
    saved: list[Path] = saved_files(downloads)
    assert len(saved) == 1
    assert saved[0].name.endswith("report.txt")
    assert saved[0].parent == downloads
    assert saved[0].read_text(encoding="utf-8") == "report body"
    assert not (downloads.parent / "evil").exists()


async def test_iframe_content_gets_refs_that_work(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("frame", f"{site}/")
    ref: str = ref_of(view.body, 'button "Frame button"')
    await act(browser, "frame", [Step(action="click", ref=ref)], False)
    assert "clicked inside frame" in (await browser.snapshot("frame")).body


async def test_password_fields_are_never_typed_into(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("secret", f"{site}/")
    ref: str = ref_of(view.body, 'textbox "Account password"')
    outcome: ActOutcome = await act(browser, "secret", [Step(action="fill", ref=ref, text="hunter2")], False)
    assert outcome.steps[0].outcome == "failed"
    assert "password field" in outcome.steps[0].detail
    assert "hunter2" not in outcome.steps[0].description


async def test_urls_outside_the_rules_are_refused(browser: BrowserSession, site: str) -> None:
    for url in ("http://169.254.169.254/latest/meta-data/", "file:///etc/passwd", "javascript:alert(1)"):
        with pytest.raises(BrowserError):
            await browser.open_url("rules", url)


async def test_the_interface_of_dot_is_never_opened(browser_config: BrowserConfig, site: str, tmp_path: Path) -> None:
    """The test site stands in for Dot's interface: a session whose daemon listens on the site's port refuses it."""
    port: int | None = urlsplit(site).port
    assert port is not None
    session: BrowserSession = BrowserSession(
        browser_config,
        SERVER.model_copy(update={"port": port}),
        tmp_path / "profile",
        tmp_path / "downloads",
        no_runs_at_work,
    )
    try:
        with pytest.raises(BrowserError) as refused:
            await session.open_url("own", f"{site}/")
        assert "Dot's own interface" in str(refused.value.context["reason"]), refused.value.context
    finally:
        await session.aclose()


async def test_threads_keep_separate_tabs(browser: BrowserSession, site: str) -> None:
    first, second = await asyncio.gather(
        browser.open_url("thread-a", f"{site}/popup"), browser.open_url("thread-b", f"{site}/form")
    )
    assert (first.title, second.title) == ("Popup", "Form")
    assert (browser.current_url("thread-a") or "").endswith("/popup")
    assert (browser.current_url("thread-b") or "").endswith("/form")


async def test_find_reaches_offscreen_elements_and_refs_stay_usable(browser: BrowserSession, site: str) -> None:
    await browser.open_url("find", f"{site}/")
    found = await browser.find("find", "Item 250", 5)
    assert len(found.hits) == 1
    ref: str = ref_of(found.hits[0], "Item 250")
    outcome: ActOutcome = await act(
        browser, "find", [Step(action="scroll_into_view", ref=ref), Step(action="click", ref=ref)], False
    )
    assert [report.outcome for report in outcome.steps] == ["done", "done"]


async def test_page_text_is_paged_and_screenshots_are_jpeg(browser: BrowserSession, site: str) -> None:
    await browser.open_url("text", f"{site}/")
    first = await browser.read_text("text", None, 0, 100)
    assert len(first.text) == 100
    assert first.total_chars > 1000
    assert (await browser.read_text("text", None, 100, 100)).text != first.text
    shot = await browser.screenshot("text")
    assert shot.data[:2] == b"\xff\xd8"
    assert (shot.width, shot.height) == (1280, 860)


async def test_late_rendering_pages_are_shown_after_they_render(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("slow", f"{site}/slow")
    assert "Loaded late" in view.body


async def test_a_scroll_shows_the_part_of_the_page_it_moved_to(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("scroll", f"{site}/")
    assert 'button "Item 100"' not in view.body
    scrolled: PageView = await browser.scroll("scroll", "down", 2000, None)
    assert 'button "Item 100"' in scrolled.body
    assert 'button "Item 10"' not in scrolled.body


async def test_a_wait_for_text_says_after_how_long_it_appeared_and_shows_what_changed(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("wait-ok", f"{site}/later")
    await act(browser, "wait-ok", [Step(action="click", ref=ref_of(view.body, 'button "Place order"'))], False)
    wait: TextWait = await browser.wait_text("wait-ok", "Order placed", 5.0)
    assert wait.appeared
    assert 0.2 < wait.waited_s < 2.0, wait.waited_s
    assert "Order placed" in wait.view.body
    result: ToolResult = wait_text_result(wait, "n0nce", 20_000)
    assert not result.is_error
    assert result.text.startswith(f"The text appeared after {wait.waited_s:.1f} s · Tab 1 of 1 · "), result.text


async def test_a_wait_for_text_that_does_not_come_fails_and_still_shows_the_page(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("wait-miss", f"{site}/later")
    await act(browser, "wait-miss", [Step(action="click", ref=ref_of(view.body, 'button "Place order"'))], False)
    wait: TextWait = await browser.wait_text("wait-miss", "Payment failed", 1.5)
    assert not wait.appeared
    assert 1.4 < wait.waited_s < 3.0, wait.waited_s
    assert "Order placed" in wait.view.body
    result: ToolResult = wait_text_result(wait, "n0nce", 20_000)
    assert result.is_error
    assert result.untrusted  # the gate counts no failed result as a read, and this one shows the page
    assert result.text.startswith(f"The text did not appear within {wait.waited_s:.1f} s; the page now · Tab 1 of 1 · ")


async def test_a_wait_that_times_out_while_a_page_still_loads_shows_it_without_waiting_for_the_rest(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("wait-leaving", f"{site}/leaving")
    await act(browser, "wait-leaving", [Step(action="click", ref=ref_of(view.body, 'button "Leave"'))], False)
    started: float = time.monotonic()
    wait: TextWait = await browser.wait_text("wait-leaving", "Arrived", 1.5)
    took: float = time.monotonic() - started
    assert not wait.appeared
    assert took < 3.0, (took, wait.waited_s)
    assert "the page is still loading; what is shown may be incomplete" in wait.view.notes
    assert 'heading "Half a page"' in wait.view.body


async def test_a_wait_that_times_out_checks_where_a_pending_navigation_went_before_showing_the_page(
    browser_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """The page leaves for a blocked host that answers after the wait timed out; reading the page waits for that
    navigation to commit, so the URL rules are checked once more before anything is shown."""
    config: BrowserConfig = browser_config.model_copy(update={"blocked_hosts": ["localhost"]})
    (tmp_path / "profile").mkdir()
    (tmp_path / "downloads").mkdir()
    session: BrowserSession = BrowserSession(
        config, SERVER, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work
    )
    try:
        view: PageView = await session.open_url("blocked", f"{site}/to-blocked")
        await act(session, "blocked", [Step(action="click", ref=ref_of(view.body, 'button "Leave"'))], False)
        with pytest.raises(BrowserError, match="may not be opened"):
            await session.wait_text("blocked", "Arrived", 1.0)
    finally:
        await session.aclose()


async def test_type_insert_and_fill_reach_an_editor_that_gets_ready_after_the_focus(
    browser: BrowserSession, site: str
) -> None:
    """The first three editor steps give the editor the focus anew (the first step, then each after a step in the plain
    field); the last three fill it while it has the focus, two of them with what it holds already, the empty text
    included, which changes nothing in it."""
    view: PageView = await browser.open_url("editor", f"{site}/editor")
    editor: str = ref_of(view.body, 'textbox "Post text"')
    plain: str = ref_of(view.body, 'textbox "Plain field"')
    for step, holds in (
        (Step(action="type", ref=editor, text="Hello"), "Hello"),
        (Step(action="type", ref=plain, text="key"), "Hello"),
        (Step(action="insert", ref=editor, text=" world, at once"), "Hello world, at once"),
        (Step(action="type", ref=plain, text=" by key"), "Hello world, at once"),
        (Step(action="fill", ref=editor, text="Replaced"), "Replaced"),
        (Step(action="fill", ref=editor, text="Replaced"), "Replaced"),
        (Step(action="fill", ref=editor, text=""), ""),
        (Step(action="fill", ref=editor, text=""), ""),
    ):
        outcome: ActOutcome = await act(browser, "editor", [step], False)
        assert [report.outcome for report in outcome.steps] == ["done"], (step, outcome.steps)
        shown: str = value_of((await browser.snapshot("editor")).body, "paragraph")
        assert shown == f'"{("Editor holds: " + holds).rstrip()}"', step
    assert value_of((await browser.snapshot("editor")).body, 'textbox "Plain field"') == "key by key"


async def test_fill_reaches_a_paragraph_of_an_editor_without_a_role_and_a_field_inside_an_editor(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("notes", f"{site}/editor")
    paragraph: re.Match[str] | None = re.search(r"paragraph \[ref=([^\]]+)\]: Second note", view.body)
    assert paragraph is not None, view.body
    steps: list[Step] = [
        Step(action="fill", ref=paragraph.group(1), text="Replaced note"),
        Step(action="fill", ref=ref_of(view.body, 'textbox "Inner field"'), text="inside"),
    ]
    outcome: ActOutcome = await act(browser, "notes", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome.steps
    body: str = (await browser.snapshot("notes")).body
    assert re.findall(r"paragraph \[ref=[^\]]+\]: (.*)", body)[1:] == ["First note", "Replaced note"]
    assert value_of(body, 'textbox "Inner field"') == "inside"


async def test_fill_writes_into_a_document_in_design_mode(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("design", f"{site}/design")
    paragraph: re.Match[str] | None = re.search(r"paragraph \[ref=([^\]]+)\]: Old text", view.body)
    assert paragraph is not None, view.body
    steps: list[Step] = [
        Step(action="fill", ref=paragraph.group(1), text="New text"),
        Step(action="fill", ref=ref_of(view.body, 'textbox "Name"'), text="Ada"),
    ]
    outcome: ActOutcome = await act(browser, "design", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome.steps
    body: str = (await browser.snapshot("design")).body
    assert "New text" in body
    assert value_of(body, 'textbox "Name"') == "Ada"


async def test_a_key_that_makes_an_editor_open_another_page_is_done(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("search-box", f"{site}/search-box")
    box: str = ref_of(view.body, 'textbox "Search"')
    outcome: ActOutcome = await act(browser, "search-box", [Step(action="type", ref=box, text="\n")], False)
    assert [report.outcome for report in outcome.steps] == ["done"], outcome.steps
    assert "?q=cats" in (await browser.snapshot("search-box")).body


async def test_an_insert_at_an_element_that_takes_no_text_fails_instead_of_writing_where_the_focus_is(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("no-text", f"{site}/editor")
    steps: list[Step] = [
        Step(action="type", ref=ref_of(view.body, 'textbox "Plain field"'), text="kept"),
        Step(action="insert", ref=ref_of(view.body, "paragraph"), text=" lost"),
    ]
    outcome: ActOutcome = await act(browser, "no-text", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "failed"], outcome.steps
    assert "insert needs a text field or an editor" in outcome.steps[1].detail
    assert value_of((await browser.snapshot("no-text")).body, 'textbox "Plain field"') == "kept"


async def test_a_click_into_an_editor_then_a_write_waits_until_the_editor_is_ready(
    browser: BrowserSession, site: str
) -> None:
    """The click gives the editor the focus, so the write that follows finds the focus in it already."""
    view: PageView = await browser.open_url("clicked-editor", f"{site}/editor?ready=70")
    editor: str = ref_of(view.body, 'textbox "Post text"')
    steps: list[Step] = [Step(action="click", ref=editor), Step(action="type", ref=editor, text="Hello")]
    outcome: ActOutcome = await act(browser, "clicked-editor", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome.steps
    assert value_of((await browser.snapshot("clicked-editor")).body, "paragraph") == '"Editor holds: Hello"'


async def test_a_write_the_editor_drops_fails_the_step_instead_of_reporting_done(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("late-editor", f"{site}/editor?ready=400")
    editor: str = ref_of(view.body, 'textbox "Post text"')
    outcome: ActOutcome = await act(browser, "late-editor", [Step(action="insert", ref=editor, text="Lost")], False)
    assert [report.outcome for report in outcome.steps] == ["failed"], outcome.steps
    assert "the editor did not take the text" in outcome.steps[0].detail


async def test_an_editor_that_shows_the_text_a_moment_after_the_write_took_it(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("slow-editor", f"{site}/editor?apply=150")
    editor: str = ref_of(view.body, 'textbox "Post text"')
    outcome: ActOutcome = await act(browser, "slow-editor", [Step(action="insert", ref=editor, text="Late")], False)
    assert [report.outcome for report in outcome.steps] == ["done"], outcome.steps
    assert value_of((await browser.snapshot("slow-editor")).body, "paragraph") == '"Editor holds: Late"'


async def test_drag_moves_an_item_onto_another_list(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("board", f"{site}/board")
    item: re.Match[str] | None = re.search(r"listitem \[ref=([^\]]+)\]: Write tests", view.body)
    assert item is not None, view.body
    outcome: ActOutcome = await act(
        browser, "board", [Step(action="drag", ref=item.group(1), to_ref=ref_of(view.body, 'list "Done"'))], False
    )
    assert [report.outcome for report in outcome.steps] == ["done"], outcome.steps
    assert "Moved Write tests to Done" in (await browser.snapshot("board")).body


async def test_cookie_banners_are_declined_instead_of_blocking_actions(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("cookies", f"{site}/cookies")
    outcome: ActOutcome = await act(
        browser, "cookies", [Step(action="click", ref=ref_of(view.body, 'button "Page action"'))], False
    )
    assert outcome.steps[0].outcome == "done", outcome
    assert "page action done" in (await browser.read_text("cookies", None, 0, 1000)).text


async def test_a_dialog_in_front_of_the_page_is_shown_first_and_a_blocked_click_says_what_covers_it(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("modal", f"{site}/modal")
    assert view.overlays == 1
    assert 'dialog "Newsletter"' in view.overlay
    assert 'button "No thanks"' in view.overlay
    assert "Buy now" not in view.overlay
    rendered: str = render_page_view(view, "n0nce", 20_000)
    assert "a dialog is open in front of the page" in rendered.splitlines()[0]
    assert rendered.index("in front of the page:") < rendered.index("the page:")

    blocked: ActOutcome = await act(
        browser, "modal", [Step(action="click", ref=ref_of(view.body, 'button "Buy now"'))], False
    )
    assert blocked.steps[0].outcome == "failed", blocked
    assert "lies in front of the element" in blocked.steps[0].detail
    assert 'role="dialog"' in blocked.steps[0].detail

    closed: ActOutcome = await act(
        browser, "modal", [Step(action="click", ref=ref_of(blocked.view.overlay, 'button "No thanks"'))], False
    )
    assert closed.steps[0].outcome == "done", closed
    assert closed.view.overlays == 0
    assert closed.view.overlay == ""
    bought: ActOutcome = await act(
        browser, "modal", [Step(action="click", ref=ref_of(view.body, 'button "Buy now"'))], False
    )
    assert bought.steps[0].outcome == "done", bought
    assert "bought" in (await browser.read_text("modal", None, 0, 1000)).text


async def test_view_leaves_scaffolding_out_and_links_serve_absolute_targets(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("links", f"{site}/")
    assert "/url:" not in view.body
    assert "cursor=pointer" not in view.body
    found = await browser.links("links", "popup", 10)
    assert len(found.hits) == 1
    assert found.hits[0].endswith(f"Open popup -> {site}/popup")
    assert (await browser.links("links", "no such link anywhere", 10)).hits == ()


async def test_names_with_a_colon_are_parsed_as_elements_and_links_keep_their_targets(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("colon", f"{site}/colon")
    button: str | None = browser.peek_line("colon", ref_of(view.body, "Buy now: confirm"))
    assert button is not None
    assert parse_element(button) == Element(role="button", name="Buy now: confirm")
    found = await browser.links("colon", "", 10)
    assert [hit.split("  ", 1)[1] for hit in found.hits] == [f"Read: more -> {site}/done?x=1"]


def line_of(body: str, label: str) -> str:
    return next(line for line in body.splitlines() if label in line)


async def test_collapsed_dropdowns_list_their_options_and_the_choice_shows_in_the_delta(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("dropdown-view", f"{site}/dropdown")
    listed: str = line_of(view.body, 'combobox "Shipping"')
    assert listed.endswith(
        ': options "Standard (3-5 days)" [selected], "Express (next day)", "Pick up in store" [disabled]'
    )
    ref: str = ref_of(view.body, 'combobox "Shipping"')
    assert parse_element(browser.peek_line("dropdown-view", ref) or "") == Element(role="combobox", name="Shipping")
    outcome: ActOutcome = await act(
        browser, "dropdown-view", [Step(action="select", ref=ref, text="Express (next day)")], False
    )
    assert [report.outcome for report in outcome.steps] == ["done"], outcome
    assert '"Express (next day)" [selected]' in outcome.view.body
    assert '"Standard (3-5 days)" [selected]' not in outcome.view.body


async def test_a_missing_option_fails_fast_and_names_the_options(
    browser: BrowserSession, browser_config: BrowserConfig, site: str
) -> None:
    view: PageView = await browser.open_url("dropdown-miss", f"{site}/dropdown")
    step: Step = Step(action="select", ref=ref_of(view.body, 'combobox "Shipping"'), text="Overnight")
    started: float = time.perf_counter()
    outcome: ActOutcome = await act(browser, "dropdown-miss", [step], False)
    elapsed: float = time.perf_counter() - started
    assert outcome.steps[0].outcome == "failed"
    assert '"Express (next day)"' in outcome.steps[0].detail
    assert '"Standard (3-5 days)" [selected]' in outcome.steps[0].detail
    assert elapsed < browser_config.action_timeout_s / 2, elapsed


async def test_an_option_a_script_adds_a_moment_later_is_still_chosen(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("dropdown-late", f"{site}/dropdown")
    steps: list[Step] = [
        Step(action="click", ref=ref_of(view.body, 'button "Load states"')),
        Step(action="select", ref=ref_of(view.body, 'combobox "State"'), text="Texas"),
    ]
    outcome: ActOutcome = await act(browser, "dropdown-late", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome
    assert '"Texas" [selected]' in line_of((await browser.snapshot("dropdown-late")).body, 'combobox "State"')


async def test_a_long_dropdown_keeps_its_line_short_and_names_a_choice_it_cannot_show(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("dropdown-long", f"{site}/dropdown")
    listed: str = line_of(view.body, 'combobox "Number"')
    assert len(listed) < 400
    assert listed.endswith('(selected: "Number 250")')
    outcome: ActOutcome = await act(
        browser,
        "dropdown-long",
        [Step(action="select", ref=ref_of(view.body, 'combobox "Number"'), text="Number 299")],
        False,
    )
    assert outcome.steps[0].outcome == "done", outcome
    assert line_of(outcome.view.body, 'combobox "Number"').endswith('(selected: "Number 299")')


async def timed_act(browser: BrowserSession, thread: str, steps: list[Step]) -> float:
    started: float = time.perf_counter()
    outcome: ActOutcome = await act(browser, thread, steps, False)
    elapsed: float = time.perf_counter() - started
    assert [report.outcome for report in outcome.steps] == ["done"] * len(steps), outcome
    return elapsed


@pytest.mark.parametrize("quiet_ms", [120, 60])
async def test_a_click_and_a_batch_of_five_clicks_stay_fast(
    browser_config: BrowserConfig, site: str, tmp_path: Path, quiet_ms: int
) -> None:
    """20 acts of one click and 5 of five clicks on the home page, in a Chrome of their own with the DOM quiet window
    ``quiet_ms``. The medians are printed (``-s`` shows them): ROADMAP item 13 keeps them."""
    config: BrowserConfig = browser_config.model_copy(update={"settle_quiet_ms": quiet_ms})
    (tmp_path / "profile").mkdir()
    (tmp_path / "downloads").mkdir()
    session: BrowserSession = BrowserSession(
        config, SERVER, tmp_path / "profile", tmp_path / "downloads", no_runs_at_work
    )
    try:
        view: PageView = await session.open_url("timing", f"{site}/")
        click: Step = Step(action="click", ref=ref_of(view.body, 'button "Increment"'))
        singles: list[float] = [await timed_act(session, "timing", [click]) for _ in range(20)]
        batches: list[float] = [await timed_act(session, "timing", [click] * 5) for _ in range(5)]
    finally:
        await session.aclose()
    single: float = statistics.median(singles)
    batch: float = statistics.median(batches)
    print(f"\nsettle_quiet_ms {quiet_ms}: one click {single:.3f} s, five clicks {batch:.3f} s (medians)")
    assert single < 1.5
    assert batch < 3.0


def driver_pids() -> list[int]:
    """The Playwright driver is the only node process that is a child of the test process."""
    listing: subprocess.CompletedProcess[str] = subprocess.run(
        ["pgrep", "-P", str(os.getpid()), "-x", "node"], capture_output=True, text=True, check=False
    )
    return [int(pid) for pid in listing.stdout.split()]


async def test_a_dead_driver_fails_one_call_and_the_browser_restarts(browser: BrowserSession, site: str) -> None:
    """Runs last: it replaces the Chrome that the tests above used."""
    await browser.open_url("driver", f"{site}/")
    before: list[int] = driver_pids()
    assert len(before) == 1, before
    os.kill(before[0], signal.SIGKILL)
    with pytest.raises(BrowserError, match="connection to the browser broke"):
        await browser.open_url("driver", f"{site}/form")
    view: PageView = await browser.open_url("driver", f"{site}/form")
    assert view.title == "Form"
    assert driver_pids() not in ([], before)


async def test_a_frame_detached_twice_does_not_break_the_next_call() -> None:
    # Pages that churn iframes can make Playwright handle one detachment twice (playwright-python #1268); unfixed, the
    # second one leaves a ValueError that the next API call raises.
    ignore_repeated_frame_detach()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content("<iframe srcdoc='<p>inner</p>'></iframe>")
            frame = page.frames[1]
            await page.evaluate("document.querySelector('iframe').remove()")
            await page.wait_for_function("document.querySelectorAll('iframe').length === 0")
            page._impl_obj._on_frame_detached(frame._impl_obj)
            assert await page.locator("iframe").count() == 0
        finally:
            await browser.close()


async def _profile_free(profile: Path) -> bool:
    """Whether no Chrome holds ``profile`` within a few seconds: Chrome lets go of it as it exits."""
    for _ in range(50):
        if profile_holder_pid(profile) is None:
            return True
        await asyncio.sleep(0.2)
    return False


async def test_dots_own_chrome_closes_once_unused_and_starts_again_for_the_next_call(
    browser_config: BrowserConfig, site: str, tmp_path: Path
) -> None:
    """Dot's own Chrome stays while a run at work holds tabs in it and while a use no chat holds is under way (a page
    read for the web tools), closes with its driver once nothing used it for ``idle_close_s`` (item 454), and starts
    again on the same profile for the next call."""
    config: BrowserConfig = browser_config.model_copy(update={"idle_close_s": 0.2})
    (tmp_path / "profile").mkdir()
    (tmp_path / "downloads").mkdir()
    at_work: set[str] = {"working"}
    session: BrowserSession = BrowserSession(
        config, SERVER, tmp_path / "profile", tmp_path / "downloads", lambda: frozenset(at_work)
    )
    try:
        await session.open_url("working", f"{site}/")
        await asyncio.sleep(0.3)
        await session._close_unused()
        assert profile_holder_pid(tmp_path / "profile") is not None
        at_work.clear()
        with session._in_use():
            await asyncio.sleep(0.3)
            await session._close_unused()
            assert profile_holder_pid(tmp_path / "profile") is not None
        await session._close_unused()
        assert profile_holder_pid(tmp_path / "profile") is not None
        await asyncio.sleep(0.3)
        await session._close_unused()
        assert await _profile_free(tmp_path / "profile")
        view: PageView = await session.open_url("next", f"{site}/")
        assert view.title == "Home"
    finally:
        await session.aclose()
