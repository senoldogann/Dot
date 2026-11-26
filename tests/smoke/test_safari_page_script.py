"""The script Dot runs in a page of the owner's Safari (item 1959), in WebKit, Safari's engine, against pages served by
a local fixture server: no real Safari is driven.

A read lists the controls of the page, of a frame of the same site and of a shadow root, each with a ref that stays
the same at the next read; a field a page keeps in its own state (as React does) takes a fill, Enter in a field sends
its form, a hidden checkbox drawn by its label is ticked, and a step on a ref the page lost, or on an element that no
longer shows what was read, is refused before anything is done.
"""

import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright

from dotagent.core.types import JsonObject
from dotagent.safari.page_script import page_command
from dotagent.safari.types import PageElement, ReadReply, StepAnswer, StepReply

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_PAGES: dict[str, str] = {
    "/": """<!doctype html><meta charset="utf-8"><title>Sign in</title>
<h1>Sign in</h1>
<form id="form" onsubmit="event.preventDefault(); document.getElementById('out').textContent = 'Sent ' +
  document.getElementById('email').value;">
<label for="email">Email address</label><input id="email" autocomplete="username">
<label>Password <input id="password" type="password" autocomplete="current-password"></label>
<label><input type="checkbox" id="keep" style="opacity:0;position:absolute;width:0;height:0"> Keep me signed in</label>
<select id="country" aria-label="Country"><option>Finland</option><option value="tr">Türkiye</option></select>
<button type="submit">Continue</button>
</form>
<div id="react"></div>
<p id="out"></p>
<my-widget></my-widget>
<iframe src="/frame" width="400" height="120"></iframe>
<script>
const field = document.createElement('input'); field.setAttribute('aria-label', 'Domain');
field.addEventListener('input', (event) => {
  document.getElementById('out').textContent = 'state=' + event.target.value; });
document.getElementById('react').appendChild(field);
customElements.define('my-widget', class extends HTMLElement { constructor() { super();
  const root = this.attachShadow({mode: 'open'}); root.innerHTML = '<button>Register</button>';
  root.querySelector('button').onclick = () => { document.getElementById('out').textContent = 'registered'; }; } });
</script>""",
    "/frame": """<!doctype html><input aria-label="Apple ID" id="id"><button
  onclick="parent.document.getElementById('out').textContent = 'frame ' + document.getElementById('id').value">Next
</button>""",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = _PAGES.get(self.path, "<!doctype html><title>Missing</title>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def webkit() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.webkit.launch(headless=True)
        try:
            yield browser
        finally:
            await browser.close()


@pytest_asyncio.fixture(loop_scope="module")
async def page(webkit: Browser, site: str) -> AsyncIterator[Page]:
    opened: Page = await webkit.new_page()
    await opened.goto(site)
    await opened.wait_for_function("document.querySelector('iframe').contentDocument?.getElementById('id') !== null")
    try:
        yield opened
    finally:
        await opened.close()


async def _run(page: Page, command: JsonObject) -> str:
    said: object = await page.evaluate(page_command(command))
    assert isinstance(said, str)
    return said


async def _read(page: Page) -> dict[str, PageElement]:
    reply: ReadReply = ReadReply.model_validate(
        json.loads(await _run(page, {"op": "read", "limit": 100, "text_chars": 500}))
    )
    assert reply.ok and reply.page is not None
    return {item.name: item for item in reply.page.elements}


async def _step(page: Page, step: JsonObject) -> StepAnswer:
    reply: StepReply = StepReply.model_validate(json.loads(await _run(page, {"op": "step", "step": step})))
    assert reply.ok and reply.step is not None
    return reply.step


async def test_a_read_lists_the_controls_of_the_page_its_frame_and_its_shadow_root_with_lasting_refs(
    page: Page,
) -> None:
    named: dict[str, PageElement] = await _read(page)
    assert {"Sign in", "Email address", "Password", "Keep me signed in", "Country", "Continue", "Domain"} <= set(named)
    assert {"Register", "Apple ID", "Next"} <= set(named)
    assert named["Password"].mark == "password"
    assert named["Country"].options == ("Finland", "Türkiye")
    # Enter in the email field may press the form's button, which judges it as browser_act judges Chrome's.
    assert named["Email address"].near == (named["Continue"].ref,)
    again: dict[str, PageElement] = await _read(page)
    assert {name: item.ref for name, item in again.items()} == {name: item.ref for name, item in named.items()}


async def test_steps_fill_tick_choose_and_send_as_a_person_would(page: Page) -> None:
    named: dict[str, PageElement] = await _read(page)
    domain: StepAnswer = await _step(page, {"action": "fill", "ref": named["Domain"].ref, "text": "gistsmith.com"})
    assert domain.verified and await page.text_content("#out") == "state=gistsmith.com"
    keep: StepAnswer = await _step(page, {"action": "check", "ref": named["Keep me signed in"].ref})
    assert keep.verified and await page.is_checked("#keep")
    country: StepAnswer = await _step(page, {"action": "select", "ref": named["Country"].ref, "text": "Türkiye"})
    assert country.verified and await page.eval_on_selector("#country", "element => element.value") == "tr"
    await _step(page, {"action": "fill", "ref": named["Apple ID"].ref, "text": "me@example.com"})
    await _step(page, {"action": "click", "ref": named["Next"].ref})
    assert await page.text_content("#out") == "frame me@example.com"
    await _step(page, {"action": "click", "ref": named["Register"].ref})
    assert await page.text_content("#out") == "registered"
    await _step(page, {"action": "fill", "ref": named["Email address"].ref, "text": "ada@example.com"})
    sent: StepAnswer = await _step(page, {"action": "press", "ref": named["Email address"].ref, "key": "Enter"})
    assert sent.detail == "sent its form" and await page.text_content("#out") == "Sent ada@example.com"


async def test_a_step_on_a_lost_ref_or_on_an_element_that_changed_since_the_read_is_refused(page: Page) -> None:
    named: dict[str, PageElement] = await _read(page)
    lost: StepAnswer = await _step(page, {"action": "click", "ref": "s99999"})
    assert not lost.done and "read the page again" in lost.detail
    await page.evaluate("document.querySelector('button[type=submit]').textContent = 'Pay now'")
    changed: StepAnswer = await _step(page, {"action": "click", "ref": named["Continue"].ref, "name": "Continue"})
    assert not changed.done and "no longer shows what was read" in changed.detail
    assert await page.text_content("#out") == ""
