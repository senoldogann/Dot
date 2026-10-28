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
from dotagent.browser.types import ActOutcome, FindResult, PageView, Step, TextWait
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
# How long the server takes to answer a page's own request (item 774): past the time a wait may end early.
_ANSWER_LATE_S: float = 7.0

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
    # A drag from one point to another, the way a captcha's puzzle or a slider is moved (item 792).
    "/drag": """<!doctype html><title>Drag</title>
<style>#pad{width:400px;height:200px;background:#eee;position:relative}
#knob{width:40px;height:40px;background:#333;position:absolute;left:10px;top:10px;border-radius:50%}</style>
<div id="pad"><div id="knob"></div></div><p id="where">waiting</p>
<script>
const pad = document.getElementById("pad"), knob = document.getElementById("knob");
let down = false;
knob.addEventListener("pointerdown", () => { down = true; });
window.addEventListener("pointermove", (event) => {
  if (!down) return;
  const box = pad.getBoundingClientRect();
  knob.style.left = `${event.clientX - box.left - 20}px`;
  knob.style.top = `${event.clientY - box.top - 20}px`;
});
window.addEventListener("pointerup", (event) => {
  if (!down) return;
  down = false;
  document.getElementById("where").textContent = `${Math.round(event.clientX)},${Math.round(event.clientY)}`;
});
</script>""",
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
    # The text comes 1.2 s after the click: the click's own watch of its effect (item 739) takes the first half second.
    "/later": """<!doctype html><title>Later</title><p hidden>Order placed</p>
<button onclick="setTimeout(() => {
  document.getElementById('s').textContent = 'Order placed' }, 1200)">Place order</button>
<p id="s">Working</p>""",
    # A page that keeps changing, and one whose answer waits on its server (item 774): neither stands still.
    "/ticking": """<!doctype html><title>Ticking</title><p id="t">0</p>
<script>let n = 0; setInterval(() => { document.getElementById('t').textContent = String(++n) }, 300)</script>""",
    # A page that asks its server every 1.5 s with a short request, as a payment's approval does, and shows the answer
    # at the fourth: its DOM stands still meanwhile, and a request is rarely under way when it is looked at.
    "/polling": """<!doctype html><title>Polling</title><p id="s">Waiting for approval</p>
<script>let polls = 0; const tick = () => fetch('/poll').then(() => { polls += 1;
  if (polls === 4) { document.getElementById('s').textContent = 'Approved' } else { setTimeout(tick, 1500) } });
setTimeout(tick, 1500)</script>""",
    "/pending": """<!doctype html><title>Pending</title>
<button onclick="fetch('/answer-late').then((r) => r.text()).then(() => {
  document.getElementById('s').textContent = 'Sent' })">Send</button>
<p id="s">Sending</p>""",
    # File fields as sites show them (item 704): one moved off the screen under an icon, one hidden in its label, one
    # next to a button that does not open it, and two behind one button that opens the chooser of the second. Each says
    # what it got.
    "/upload": """<!doctype html><title>Upload</title>
<script>function got(id) { return (e) => { document.getElementById(id).textContent =
  [...e.target.files].map((f) => f.name).join(',') || 'none' } }</script>
<div style="position:relative"><span aria-hidden="true" style="position:absolute;left:0;top:0">📎</span>
<input type="file" aria-label="CV" style="position:absolute;left:-9999px" onchange="got('cv')(event)"></div>
<p>CV: <span id="cv">none</span></p>
<label>Attach letter<input type="file" hidden onchange="got('letter')(event)"></label>
<div><button type="button">Attach note</button><input type="file" hidden onchange="got('note')(event)"></div>
<p>Note: <span id="note">none</span></p>
<p>Letter: <span id="letter">none</span></p>
<div><input type="file" hidden><input type="file" id="second" hidden onchange="got('photo')(event)">
<button type="button" onclick="document.getElementById('second').click()">Add photo</button></div>
<p>Photo: <span id="photo">none</span></p>""",
    # Upload controls whose click does not simply open the chooser (item 745): a button that only names its field by
    # aria-controls (two fields, so none is the page's only one), a button that never stops moving (a click waits for it
    # to hold still) and opens the chooser of the second field, and, on /upload-one, a button beside the page's only
    # field that opens nothing.
    "/upload-aria": """<!doctype html><title>Upload by name</title>
<script>function got(id) { return (e) => { document.getElementById(id).textContent =
  [...e.target.files].map((f) => f.name).join(',') || 'none' } }</script>
<style>@keyframes drift { from { transform: translateX(0) } to { transform: translateX(4px) } }
#moving { animation: drift 40ms infinite alternate; width: 200px; height: 40px }</style>
<div role="button" tabindex="0" aria-controls="resume">Choose resume</div>
<input type="file" id="resume" hidden onchange="got('resume-name')(event)">
<input type="file" id="cover" hidden onchange="got('cover-name')(event)">
<button type="button" id="moving" onclick="document.getElementById('cover').click()">Choose cover letter</button>
<p>Resume: <span id="resume-name">none</span></p>
<p>Cover: <span id="cover-name">none</span></p>""",
    # A button under a layer that takes a click of its own: the forced click is not made, so the layer is not pressed.
    # Item 754: a see-through file field laid over the button it looks like (beside another field, so that it is not
    # the page's only one), and a button of a web component that names its field inside the component's shadow root.
    "/upload-overlay": """<!doctype html><title>Overlay</title><input type="file" hidden>
<div style="position:relative;width:200px;height:40px">
<button type="button" style="width:200px;height:40px">Add CV</button>
<input type="file" style="position:absolute;inset:0;opacity:0"
  onchange="document.getElementById('cv').textContent = event.target.files[0].name"></div>
<p>CV: <span id="cv">none</span></p>""",
    "/upload-shadow": """<!doctype html><title>Shadow</title><input type="file" hidden><cv-picker></cv-picker>
<p>CV: <span id="cv">none</span></p>
<script>customElements.define('cv-picker', class extends HTMLElement { connectedCallback() {
  const root = this.attachShadow({mode: 'open'});
  root.innerHTML = '<button type="button" aria-controls="f">Pick CV</button><input type="file" id="f" hidden>';
  root.getElementById('f').addEventListener('change', (e) => {
    document.getElementById('cv').textContent = e.target.files[0].name; });
} });</script>""",
    "/upload-covered": """<!doctype html><title>Covered</title>
<input type="file" id="a" hidden><input type="file" id="b" hidden>
<button type="button" onclick="document.getElementById('a').click()">Choose file</button>
<div style="position:fixed;inset:0;opacity:0.01" onclick="document.getElementById('layer').textContent = 'pressed'">
</div><p>Layer: <span id="layer">untouched</span></p>""",
    # A button that leaves for a page with one file field: that page's field is not the one the upload was for.
    "/upload-leaves": """<!doctype html><title>Leaves</title><input type="file" hidden><input type="file" hidden>
<button type="button" onclick="location.href = '/upload-one'">Next step</button>""",
    "/upload-one": """<!doctype html><title>One field</title>
<button type="button">Attach CV</button><input type="file" hidden onchange="document.getElementById('one').textContent =
  event.target.files[0].name"><p>CV: <span id="one">none</span></p>""",
    # A dialog opens 0.9 s after the first click (past the watch of its effect, item 739), one that says the order is
    # placed after the second (item 698).
    "/asks": """<!doctype html><title>Asks</title>
<button onclick="setTimeout(() => { const d = document.createElement('div'); d.setAttribute('role', 'dialog');
  d.setAttribute('aria-label', 'Confirm'); d.innerHTML = '<p>Are you sure?</p><button>Yes</button>';
  document.body.append(d) }, 900)">Pay</button>
<button onclick="setTimeout(() => { const d = document.createElement('div'); d.setAttribute('role', 'dialog');
  d.innerHTML = '<p>Order placed</p>'; document.body.append(d) }, 900)">Order</button>""",
    # The page leaves 0.9 s after the click (past the watch of a click's effect, item 739), for one whose end comes
    # only after _SLOW_REST_S.
    "/leaving": """<!doctype html><title>Leaving</title>
<button onclick="setTimeout(() => { location.href = '/half-page' }, 900)">Leave</button>""",
    # The page leaves 0.9 s after the click for another host, whose answer comes after _LATE_HEADERS_S.
    "/to-blocked": """<!doctype html><title>Leaving</title>
<button onclick="setTimeout(() => {
  location.href = `http://localhost:${location.port}/late-headers` }, 900)">Leave</button>""",
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
        if path == "/poll":
            self._plain(b"not yet")
            return
        if path == "/answer-late":
            time.sleep(_ANSWER_LATE_S)
            self._plain(b"ok")
            return
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

    def _plain(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
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
    return await browser.act(thread, steps, accept, refuse_secret_field, True)


async def test_snapshot_has_refs_and_prunes_what_is_far_below_the_fold(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("prune", f"{site}/", True)
    assert view.title == "Home"
    assert view.status == 200
    assert ref_of(view.body, 'button "Increment"')
    assert "Item 250" not in view.body
    assert view.offscreen_refs > 150


async def test_click_returns_only_what_changed(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("delta", f"{site}/", True)
    outcome: ActOutcome = await act(
        browser, "delta", [Step(action="click", ref=ref_of(view.body, 'button "Increment"'))], False
    )
    assert [report.outcome for report in outcome.steps] == ["done"]
    assert outcome.stopped is None
    assert outcome.view.mode == "delta"
    assert '"1"' in outcome.view.body or ": 1" in outcome.view.body
    assert len(outcome.view.body) < len(view.body) / 2


async def test_drag_at_moves_a_knob_from_one_pixel_to_another(browser: BrowserSession, site: str) -> None:
    await browser.open_url("drag", f"{site}/drag", True)
    outcome: ActOutcome = await act(browser, "drag", [Step(action="drag_at", x=38, y=38, to_x=220, to_y=120)], False)
    assert [report.outcome for report in outcome.steps] == ["done"], outcome.steps
    view: PageView = await browser.snapshot("drag", True)
    assert "220,120" in view.body, view.body


async def test_form_batch_fills_selects_checks_and_submits(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("form", f"{site}/form", True)
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
    view: PageView = await browser.open_url("popup", f"{site}/", True)
    steps: list[Step] = [
        Step(action="click", ref=ref_of(view.body, 'link "Open popup"')),
        Step(action="click", ref=ref_of(view.body, 'button "Increment"')),
    ]
    outcome: ActOutcome = await act(browser, "popup", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "skipped"]
    assert outcome.stopped is not None
    assert "new tab" in outcome.stopped
    assert len(outcome.view.tabs) == 2
    switched: PageView = await browser.switch_tab("popup", 2, True)
    assert switched.title == "Popup"
    assert 'button "Close me"' in switched.body
    back: PageView = await browser.close_tab("popup", 2, True)
    assert back.title == "Home"


async def test_a_tab_whose_page_answers_late_still_stops_the_batch(browser: BrowserSession, site: str) -> None:
    """Chrome tells of the tab before the click returns; Playwright reports it only once its page has answered. The
    batch waited 40 ms for it before (item 342) and went on with its next step on the page it was on."""
    view: PageView = await browser.open_url("late-tab", f"{site}/opens-late", True)
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
    view: PageView = await browser.open_url("later-tab", f"{site}/opens-late", True)
    steps: list[Step] = [
        Step(action="click", ref=ref_of(view.body, 'link "Open later tab"')),
        Step(action="click", ref=ref_of(view.body, 'button "Stay"')),
    ]
    outcome: ActOutcome = await act(browser, "later-tab", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "skipped"]
    assert outcome.stopped is not None and outcome.stopped.startswith("a new tab is opening")
    assert len(outcome.view.tabs) == 1
    await asyncio.sleep(_LATER_TAB_S)
    again: PageView = await browser.snapshot("later-tab", True)
    assert [tab.title for tab in again.tabs] == ["Opens late", "Later tab"]
    assert "a new tab opened as tab 2" in again.notes


async def test_a_popup_that_closes_itself_is_a_note_not_an_error(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("callback", f"{site}/opener", True)
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
    view: PageView = await browser.open_url("closing", f"{site}/", True)
    await act(browser, "closing", [Step(action="click", ref=ref_of(view.body, 'link "Open popup"'))], False)
    popup: PageView = await browser.switch_tab("closing", 2, True)
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
    view: PageView = await browser.open_url("dialog", f"{site}/", True)
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
    view: PageView = await browser.open_url("download", f"{site}/", True)
    outcome: ActOutcome = await act(
        browser, "download", [Step(action="click", ref=ref_of(view.body, 'link "Download report"'))], False
    )
    assert outcome.stopped is not None
    later: PageView = await browser.snapshot("download", True)
    notes: str = " ".join((*outcome.view.notes, *later.notes))
    assert "download saved to" in notes
    saved: list[Path] = saved_files(downloads)
    assert len(saved) == 1
    assert saved[0].name.endswith("report.txt")
    assert saved[0].parent == downloads
    assert saved[0].read_text(encoding="utf-8") == "report body"
    assert not (downloads.parent / "evil").exists()


async def test_iframe_content_gets_refs_that_work(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("frame", f"{site}/", True)
    ref: str = ref_of(view.body, 'button "Frame button"')
    await act(browser, "frame", [Step(action="click", ref=ref)], False)
    assert "clicked inside frame" in (await browser.snapshot("frame", True)).body


async def test_password_fields_are_never_typed_into(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("secret", f"{site}/", True)
    ref: str = ref_of(view.body, 'textbox "Account password"')
    outcome: ActOutcome = await act(browser, "secret", [Step(action="fill", ref=ref, text="hunter2")], False)
    assert outcome.steps[0].outcome == "failed"
    assert "password field" in outcome.steps[0].detail
    assert "hunter2" not in outcome.steps[0].description


async def test_urls_outside_the_rules_are_refused(browser: BrowserSession, site: str) -> None:
    for url in ("http://169.254.169.254/latest/meta-data/", "file:///etc/passwd", "javascript:alert(1)"):
        with pytest.raises(BrowserError):
            await browser.open_url("rules", url, True)


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
            await session.open_url("own", f"{site}/", True)
        assert "Dot's own interface" in str(refused.value.context["reason"]), refused.value.context
    finally:
        await session.aclose()


async def test_threads_keep_separate_tabs(browser: BrowserSession, site: str) -> None:
    first, second = await asyncio.gather(
        browser.open_url("thread-a", f"{site}/popup", True), browser.open_url("thread-b", f"{site}/form", True)
    )
    assert (first.title, second.title) == ("Popup", "Form")
    assert (browser.current_url("thread-a") or "").endswith("/popup")
    assert (browser.current_url("thread-b") or "").endswith("/form")


async def test_find_reaches_offscreen_elements_and_refs_stay_usable(browser: BrowserSession, site: str) -> None:
    await browser.open_url("find", f"{site}/", True)
    found = await browser.find("find", "Item 250", 5, True)
    assert len(found.hits) == 1
    ref: str = ref_of(found.hits[0], "Item 250")
    outcome: ActOutcome = await act(
        browser, "find", [Step(action="scroll_into_view", ref=ref), Step(action="click", ref=ref)], False
    )
    assert [report.outcome for report in outcome.steps] == ["done", "done"]


async def test_page_text_is_paged_and_screenshots_are_jpeg(browser: BrowserSession, site: str) -> None:
    await browser.open_url("text", f"{site}/", True)
    first = await browser.read_text("text", None, 0, 100)
    assert len(first.text) == 100
    assert first.total_chars > 1000
    assert (await browser.read_text("text", None, 100, 100)).text != first.text
    shot = await browser.screenshot("text", True)
    assert shot.data[:2] == b"\xff\xd8"
    assert (shot.width, shot.height) == (1280, 860)


async def test_late_rendering_pages_are_shown_after_they_render(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("slow", f"{site}/slow", True)
    assert "Loaded late" in view.body


async def test_a_scroll_shows_the_part_of_the_page_it_moved_to(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("scroll", f"{site}/", True)
    assert 'button "Item 100"' not in view.body
    scrolled: PageView = await browser.scroll("scroll", "down", 2000, None, True)
    assert 'button "Item 100"' in scrolled.body
    assert 'button "Item 10"' not in scrolled.body


async def test_a_wait_for_text_says_after_how_long_it_appeared_and_shows_what_changed(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("wait-ok", f"{site}/later", True)
    await act(browser, "wait-ok", [Step(action="click", ref=ref_of(view.body, 'button "Place order"'))], False)
    wait: TextWait = await browser.wait_text("wait-ok", "Order placed", 5.0, True)
    assert wait.appeared
    assert 0.2 < wait.waited_s < 2.0, wait.waited_s
    assert "Order placed" in wait.view.body
    result: ToolResult = wait_text_result(wait, "n0nce", 20_000)
    assert not result.is_error
    assert result.text.startswith(f"The text appeared after {wait.waited_s:.1f} s · Tab 1 of 1 · "), result.text


async def test_a_wait_for_text_that_does_not_come_fails_and_still_shows_the_page(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("wait-miss", f"{site}/later", True)
    await act(browser, "wait-miss", [Step(action="click", ref=ref_of(view.body, 'button "Place order"'))], False)
    wait: TextWait = await browser.wait_text("wait-miss", "Payment failed", 1.5, True)
    assert not wait.appeared
    assert 1.4 < wait.waited_s < 3.0, wait.waited_s
    assert "Order placed" in wait.view.body
    result: ToolResult = wait_text_result(wait, "n0nce", 20_000)
    assert result.is_error
    assert result.untrusted  # the gate counts no failed result as a read, and this one shows the page
    assert result.text.startswith(f"The text did not appear within {wait.waited_s:.1f} s; the page now · Tab 1 of 1 · ")


async def test_a_wait_for_text_on_a_page_that_stands_still_ends_once_it_has_for_three_seconds(
    browser: BrowserSession, site: str
) -> None:
    """Item 774: a text the page shows no sign of bringing is not waited for to the end of the wait's time."""
    view: PageView = await browser.open_url("wait-still", f"{site}/later", True)
    await act(browser, "wait-still", [Step(action="click", ref=ref_of(view.body, 'button "Place order"'))], False)
    wait: TextWait = await browser.wait_text("wait-still", "Payment failed", 20.0, True)
    assert not wait.appeared and wait.still_s == 3.0 and wait.ended_by is None
    assert 4.9 < wait.waited_s < 7.5, wait.waited_s
    assert "Order placed" in (await browser.read_text("wait-still", None, 0, 2000)).text
    result: ToolResult = wait_text_result(wait, "n0nce", 20_000)
    assert result.is_error and result.untrusted
    assert result.text.startswith(
        f"The text has not appeared; the wait ended after {wait.waited_s:.1f} s because the page has not changed for "
        "3 s and waits on nothing: as it stands the text is not coming; the page now · Tab 1 of 1 · "
    ), result.text


async def test_a_page_that_keeps_changing_or_waits_on_its_server_is_waited_for(
    browser: BrowserSession, site: str
) -> None:
    """Item 774: a page that changes, one of whose requests is under way, or one that polls its server, does not stand
    still. Their tabs are closed after, so that a page that keeps ticking slows no later test of the module."""
    try:
        await browser.open_url("wait-ticking", f"{site}/ticking", True)
        ticking: TextWait = await browser.wait_text("wait-ticking", "Done", 6.5, True)
        assert not ticking.appeared and ticking.still_s is None
        assert ticking.waited_s >= 6.4, ticking.waited_s
        view: PageView = await browser.open_url("wait-pending", f"{site}/pending", True)
        await act(browser, "wait-pending", [Step(action="click", ref=ref_of(view.body, 'button "Send"'))], False)
        sent: TextWait = await browser.wait_text("wait-pending", "Sent", 15.0, True)
        assert sent.appeared, sent
        assert _ANSWER_LATE_S - 1.0 < sent.waited_s < _ANSWER_LATE_S + 2.0, sent.waited_s
        # A page that polls its server is at work although no request may be under way when it is looked at.
        await browser.open_url("wait-polling", f"{site}/polling", True)
        approved: TextWait = await browser.wait_text("wait-polling", "Approved", 15.0, True)
        assert approved.appeared, approved
    finally:
        for thread in ("wait-ticking", "wait-pending", "wait-polling"):
            await browser.release_thread(thread)


def _file(path: Path) -> Path:
    path.write_bytes(b"%PDF-1.4")
    return path


async def test_files_go_into_a_hidden_file_field_and_through_the_chooser_a_button_opens(
    browser: BrowserSession, site: str, tmp_path: Path
) -> None:
    """Item 704: a file field a site moved off the screen, or hid in its label, takes the files without a click (the
    click on such a field waited eight seconds and failed); a button whose box holds two fields is clicked, and the
    chooser it opens takes them."""
    cv: Path = _file(tmp_path / "cv.pdf")
    view: PageView = await browser.open_url("upload", f"{site}/upload", True)
    # Off the screen, the field is not in the view: a search names it, as it named the ones that failed.
    found: FindResult = await browser.find("upload", "CV", 5, True)
    field: str = ref_of("\n".join(found.hits), "CV")
    started: float = time.monotonic()
    after: PageView = await browser.upload("upload", field, [str(cv)], True)
    assert time.monotonic() - started < 3.0
    assert "CV: cv.pdf" in after.body or "cv.pdf" in after.body, after.body
    view = await browser.snapshot("upload", True)
    label: re.Match[str] | None = re.search(r"\[ref=([^\]]+)\]: Attach letter", view.body)
    assert label is not None, view.body
    await browser.upload("upload", label.group(1), [str(cv)], True)
    assert "Letter: cv.pdf" in (await browser.read_text("upload", None, 0, 2000)).text
    # A field beside the button is another control's for all Dot knows: the button is clicked, opens no chooser, and
    # the field gets nothing.
    view = await browser.snapshot("upload", True)
    with pytest.raises(BrowserError, match="uploading the files failed"):
        await browser.upload("upload", ref_of(view.body, 'button "Attach note"'), [str(cv)], True)
    assert "Note: none" in (await browser.read_text("upload", None, 0, 2000)).text
    view = await browser.snapshot("upload", True)
    await browser.upload("upload", ref_of(view.body, 'button "Add photo"'), [str(cv)], True)
    assert "Photo: cv.pdf" in (await browser.read_text("upload", None, 0, 2000)).text


async def test_a_control_that_names_its_field_or_never_holds_still_or_opens_nothing_still_uploads(
    browser: BrowserSession, site: str, tmp_path: Path
) -> None:
    """Item 745: files reach the field a button names with aria-controls (no click); a button that keeps moving is
    clicked without waiting for it to hold still, and its chooser takes them; a button that opens nothing on a page
    with one file field gives them to that field."""
    cv: Path = _file(tmp_path / "cv.pdf")
    view: PageView = await browser.open_url("upload2", f"{site}/upload-aria", True)
    started: float = time.monotonic()
    await browser.upload("upload2", ref_of(view.body, 'button "Choose resume"'), [str(cv)], True)
    assert time.monotonic() - started < 3.0
    assert "Resume: cv.pdf" in (await browser.read_text("upload2", None, 0, 2000)).text
    view = await browser.snapshot("upload2", True)
    await browser.upload("upload2", ref_of(view.body, 'button "Choose cover letter"'), [str(cv)], True)
    assert "Cover: cv.pdf" in (await browser.read_text("upload2", None, 0, 2000)).text
    view = await browser.open_url("upload2", f"{site}/upload-one", True)
    await browser.upload("upload2", ref_of(view.body, 'button "Attach CV"'), [str(cv)], True)
    assert "CV: cv.pdf" in (await browser.read_text("upload2", None, 0, 2000)).text
    view = await browser.open_url("upload2", f"{site}/upload-leaves", True)
    with pytest.raises(BrowserError, match="uploading the files failed"):
        await browser.upload("upload2", ref_of(view.body, 'button "Next step"'), [str(cv)], True)
    assert "CV: none" in (await browser.read_text("upload2", None, 0, 2000)).text
    for path, button in (("/upload-overlay", "Add CV"), ("/upload-shadow", "Pick CV")):
        view = await browser.open_url("upload2", f"{site}{path}", True)
        await browser.upload("upload2", ref_of(view.body, f'button "{button}"'), [str(cv)], True)
        assert "CV: cv.pdf" in (await browser.read_text("upload2", None, 0, 2000)).text, path
    view = await browser.open_url("upload2", f"{site}/upload-covered", True)
    with pytest.raises(BrowserError, match="uploading the files failed"):
        await browser.upload("upload2", ref_of(view.body, 'button "Choose file"'), [str(cv)], True)
    assert "Layer: untouched" in (await browser.read_text("upload2", None, 0, 2000)).text


async def test_a_dialog_that_opens_during_a_wait_ends_it_and_one_that_brings_the_text_is_the_text(
    browser: BrowserSession, site: str
) -> None:
    """A wait for a text that a dialog stands in front of ends when the dialog opens, not at its time (item 698): the
    text would not come until the dialog is answered. A dialog that holds the text is the text appearing."""
    view: PageView = await browser.open_url("wait-asks", f"{site}/asks", True)
    await act(browser, "wait-asks", [Step(action="click", ref=ref_of(view.body, 'button "Pay"'))], False)
    wait: TextWait = await browser.wait_text("wait-asks", "Payment received", 10.0, True)
    assert not wait.appeared
    assert wait.ended_by == "a dialog opened in front of the page"
    assert 0.2 < wait.waited_s < 2.0, wait.waited_s
    assert "Are you sure?" in wait.view.body or "Are you sure?" in wait.view.overlay
    result: ToolResult = wait_text_result(wait, "n0nce", 20_000)
    assert result.is_error and result.untrusted
    assert result.text.startswith("The text has not appeared; the wait ended after "), result.text
    assert "because a dialog opened in front of the page: answer it first" in result.text
    # With a dialog open already, another wait is for the text alone.
    again: TextWait = await browser.wait_text("wait-asks", "Payment received", 1.0, True)
    assert not again.appeared and again.ended_by is None and again.waited_s >= 0.9
    view = await browser.open_url("wait-asks", f"{site}/asks", True)
    await act(browser, "wait-asks", [Step(action="click", ref=ref_of(view.body, 'button "Order"'))], False)
    placed: TextWait = await browser.wait_text("wait-asks", "Order placed", 10.0, True)
    assert placed.appeared and placed.ended_by is None, placed


async def test_a_wait_that_times_out_while_a_page_still_loads_shows_it_without_waiting_for_the_rest(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("wait-leaving", f"{site}/leaving", True)
    await act(browser, "wait-leaving", [Step(action="click", ref=ref_of(view.body, 'button "Leave"'))], False)
    started: float = time.monotonic()
    wait: TextWait = await browser.wait_text("wait-leaving", "Arrived", 1.5, True)
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
        view: PageView = await session.open_url("blocked", f"{site}/to-blocked", True)
        await act(session, "blocked", [Step(action="click", ref=ref_of(view.body, 'button "Leave"'))], False)
        with pytest.raises(BrowserError, match="may not be opened"):
            await session.wait_text("blocked", "Arrived", 1.0, True)
    finally:
        await session.aclose()


async def test_type_insert_and_fill_reach_an_editor_that_gets_ready_after_the_focus(
    browser: BrowserSession, site: str
) -> None:
    """The first three editor steps give the editor the focus anew (the first step, then each after a step in the plain
    field); the last three fill it while it has the focus, two of them with what it holds already, the empty text
    included, which changes nothing in it."""
    view: PageView = await browser.open_url("editor", f"{site}/editor", True)
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
        shown: str = value_of((await browser.snapshot("editor", True)).body, "paragraph")
        assert shown == f'"{("Editor holds: " + holds).rstrip()}"', step
    assert value_of((await browser.snapshot("editor", True)).body, 'textbox "Plain field"') == "key by key"


async def test_fill_reaches_a_paragraph_of_an_editor_without_a_role_and_a_field_inside_an_editor(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("notes", f"{site}/editor", True)
    paragraph: re.Match[str] | None = re.search(r"paragraph \[ref=([^\]]+)\]: Second note", view.body)
    assert paragraph is not None, view.body
    steps: list[Step] = [
        Step(action="fill", ref=paragraph.group(1), text="Replaced note"),
        Step(action="fill", ref=ref_of(view.body, 'textbox "Inner field"'), text="inside"),
    ]
    outcome: ActOutcome = await act(browser, "notes", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome.steps
    body: str = (await browser.snapshot("notes", True)).body
    assert re.findall(r"paragraph \[ref=[^\]]+\]: (.*)", body)[1:] == ["First note", "Replaced note"]
    assert value_of(body, 'textbox "Inner field"') == "inside"


async def test_fill_writes_into_a_document_in_design_mode(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("design", f"{site}/design", True)
    paragraph: re.Match[str] | None = re.search(r"paragraph \[ref=([^\]]+)\]: Old text", view.body)
    assert paragraph is not None, view.body
    steps: list[Step] = [
        Step(action="fill", ref=paragraph.group(1), text="New text"),
        Step(action="fill", ref=ref_of(view.body, 'textbox "Name"'), text="Ada"),
    ]
    outcome: ActOutcome = await act(browser, "design", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome.steps
    body: str = (await browser.snapshot("design", True)).body
    assert "New text" in body
    assert value_of(body, 'textbox "Name"') == "Ada"


async def test_a_key_that_makes_an_editor_open_another_page_is_done(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("search-box", f"{site}/search-box", True)
    box: str = ref_of(view.body, 'textbox "Search"')
    outcome: ActOutcome = await act(browser, "search-box", [Step(action="type", ref=box, text="\n")], False)
    assert [report.outcome for report in outcome.steps] == ["done"], outcome.steps
    assert "?q=cats" in (await browser.snapshot("search-box", True)).body


async def test_an_insert_at_an_element_that_takes_no_text_fails_instead_of_writing_where_the_focus_is(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("no-text", f"{site}/editor", True)
    steps: list[Step] = [
        Step(action="type", ref=ref_of(view.body, 'textbox "Plain field"'), text="kept"),
        Step(action="insert", ref=ref_of(view.body, "paragraph"), text=" lost"),
    ]
    outcome: ActOutcome = await act(browser, "no-text", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "failed"], outcome.steps
    assert "insert needs a text field or an editor" in outcome.steps[1].detail
    assert value_of((await browser.snapshot("no-text", True)).body, 'textbox "Plain field"') == "kept"


async def test_a_click_into_an_editor_then_a_write_waits_until_the_editor_is_ready(
    browser: BrowserSession, site: str
) -> None:
    """The click gives the editor the focus, so the write that follows finds the focus in it already."""
    view: PageView = await browser.open_url("clicked-editor", f"{site}/editor?ready=70", True)
    editor: str = ref_of(view.body, 'textbox "Post text"')
    steps: list[Step] = [Step(action="click", ref=editor), Step(action="type", ref=editor, text="Hello")]
    outcome: ActOutcome = await act(browser, "clicked-editor", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome.steps
    assert value_of((await browser.snapshot("clicked-editor", True)).body, "paragraph") == '"Editor holds: Hello"'


async def test_a_write_the_editor_drops_fails_the_step_instead_of_reporting_done(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("late-editor", f"{site}/editor?ready=400", True)
    editor: str = ref_of(view.body, 'textbox "Post text"')
    outcome: ActOutcome = await act(browser, "late-editor", [Step(action="insert", ref=editor, text="Lost")], False)
    assert [report.outcome for report in outcome.steps] == ["failed"], outcome.steps
    assert "the editor did not take the text" in outcome.steps[0].detail


async def test_an_editor_that_shows_the_text_a_moment_after_the_write_took_it(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("slow-editor", f"{site}/editor?apply=150", True)
    editor: str = ref_of(view.body, 'textbox "Post text"')
    outcome: ActOutcome = await act(browser, "slow-editor", [Step(action="insert", ref=editor, text="Late")], False)
    assert [report.outcome for report in outcome.steps] == ["done"], outcome.steps
    assert value_of((await browser.snapshot("slow-editor", True)).body, "paragraph") == '"Editor holds: Late"'


async def test_drag_moves_an_item_onto_another_list(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("board", f"{site}/board", True)
    item: re.Match[str] | None = re.search(r"listitem \[ref=([^\]]+)\]: Write tests", view.body)
    assert item is not None, view.body
    outcome: ActOutcome = await act(
        browser, "board", [Step(action="drag", ref=item.group(1), to_ref=ref_of(view.body, 'list "Done"'))], False
    )
    assert [report.outcome for report in outcome.steps] == ["done"], outcome.steps
    assert "Moved Write tests to Done" in (await browser.snapshot("board", True)).body


async def test_cookie_banners_are_declined_instead_of_blocking_actions(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("cookies", f"{site}/cookies", True)
    outcome: ActOutcome = await act(
        browser, "cookies", [Step(action="click", ref=ref_of(view.body, 'button "Page action"'))], False
    )
    assert outcome.steps[0].outcome == "done", outcome
    assert "page action done" in (await browser.read_text("cookies", None, 0, 1000)).text


async def test_a_dialog_in_front_of_the_page_is_shown_first_and_a_blocked_click_says_what_covers_it(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("modal", f"{site}/modal", True)
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
    view: PageView = await browser.open_url("links", f"{site}/", True)
    assert "/url:" not in view.body
    assert "cursor=pointer" not in view.body
    found = await browser.links("links", "popup", 10, True)
    assert len(found.hits) == 1
    assert found.hits[0].endswith(f"Open popup -> {site}/popup")
    assert (await browser.links("links", "no such link anywhere", 10, True)).hits == ()


async def test_names_with_a_colon_are_parsed_as_elements_and_links_keep_their_targets(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("colon", f"{site}/colon", True)
    button: str | None = browser.peek_line("colon", ref_of(view.body, "Buy now: confirm"))
    assert button is not None
    assert parse_element(button) == Element(role="button", name="Buy now: confirm")
    found = await browser.links("colon", "", 10, True)
    assert [hit.split("  ", 1)[1] for hit in found.hits] == [f"Read: more -> {site}/done?x=1"]


def line_of(body: str, label: str) -> str:
    return next(line for line in body.splitlines() if label in line)


async def test_collapsed_dropdowns_list_their_options_and_the_choice_shows_in_the_delta(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("dropdown-view", f"{site}/dropdown", True)
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
    view: PageView = await browser.open_url("dropdown-miss", f"{site}/dropdown", True)
    step: Step = Step(action="select", ref=ref_of(view.body, 'combobox "Shipping"'), text="Overnight")
    started: float = time.perf_counter()
    outcome: ActOutcome = await act(browser, "dropdown-miss", [step], False)
    elapsed: float = time.perf_counter() - started
    assert outcome.steps[0].outcome == "failed"
    assert '"Express (next day)"' in outcome.steps[0].detail
    assert '"Standard (3-5 days)" [selected]' in outcome.steps[0].detail
    assert elapsed < browser_config.action_timeout_s / 2, elapsed


async def test_an_option_a_script_adds_a_moment_later_is_still_chosen(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("dropdown-late", f"{site}/dropdown", True)
    steps: list[Step] = [
        Step(action="click", ref=ref_of(view.body, 'button "Load states"')),
        Step(action="select", ref=ref_of(view.body, 'combobox "State"'), text="Texas"),
    ]
    outcome: ActOutcome = await act(browser, "dropdown-late", steps, False)
    assert [report.outcome for report in outcome.steps] == ["done", "done"], outcome
    assert '"Texas" [selected]' in line_of((await browser.snapshot("dropdown-late", True)).body, 'combobox "State"')


async def test_a_long_dropdown_keeps_its_line_short_and_names_a_choice_it_cannot_show(
    browser: BrowserSession, site: str
) -> None:
    view: PageView = await browser.open_url("dropdown-long", f"{site}/dropdown", True)
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
        view: PageView = await session.open_url("timing", f"{site}/", True)
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
    await browser.open_url("driver", f"{site}/", True)
    before: list[int] = driver_pids()
    assert len(before) == 1, before
    os.kill(before[0], signal.SIGKILL)
    with pytest.raises(BrowserError, match="connection to the browser broke"):
        await browser.open_url("driver", f"{site}/form", True)
    view: PageView = await browser.open_url("driver", f"{site}/form", True)
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
        await session.open_url("working", f"{site}/", True)
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
        view: PageView = await session.open_url("next", f"{site}/", True)
        assert view.title == "Home"
    finally:
        await session.aclose()
