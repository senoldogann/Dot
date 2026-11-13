"""What a step of a browser batch came to, beyond its being sent (item 1145), in a real headless Chrome against a local
site that keeps what it was sent.

Every claim is checked twice: by the step's own report, and by what the site recorded, so that a step is never taken
for verified by its report's words alone. A fill, a choice and a tick are read back; a field that puts its value back
fails its step; a value the page cuts holds the next click back; a click that named what it expects is waited for,
verified when that shows and uncertain when it does not, and is never sent twice; a step that expects what the page
shows already is not sent at all.
"""

import asyncio
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import Page

from dotagent.browser import session as browser_session
from dotagent.browser.driver import StepResult
from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ActOutcome, PageView, SecretField, SecretFieldCheck, Step, StepReport
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
# How long the site takes to answer its slow form, and a result the page shows by a timer of its own.
_SLOW_S: float = 1.5
_LATER_MS: int = 1200
_PASSWORD: str = "correct horse battery"
_PAGE: str = f"""<!doctype html><title>Order desk</title>
<form id="order" onsubmit="return false">
<label>Name <input aria-label="Name" name="name"></label>
<label>Phone <input aria-label="Phone" name="phone"></label>
<label>Locked <input aria-label="Locked" name="locked" value="fixed" oninput="this.value = 'fixed'"></label>
<label>Code <input aria-label="Code" name="code" oninput="this.value = this.value.slice(0, 5)"></label>
<label>Site <input aria-label="Site" name="site"
  oninput="if (this.value && !this.value.startsWith('https://')) this.value = 'https://' + this.value"></label>
<label>Plan <select aria-label="Plan" name="plan"><option>Basic</option><option>Pro</option></select></label>
<label>Stuck <select aria-label="Stuck" onchange="this.selectedIndex = 0"><option>One</option><option>Two</option>
</select></label>
<label><input type="checkbox" aria-label="Terms" name="terms"> Terms</label>
<label><input type="checkbox" aria-label="Bounce" onchange="setTimeout(() => {{ this.checked = false; }}, 0)">
Bounce</label>
<label><input type="checkbox" aria-label="Snap" onchange="this.checked = false"> Snap</label>
<label>Secret <input type="password" aria-label="Secret" name="secret"></label>
</form>
<button type="button" aria-label="Dead">Dead</button>
<button type="button" aria-label="Send" onclick="send('/submit')">Send</button>
<button type="button" aria-label="Slow" onclick="send('/slow')">Slow</button>
<button type="button" aria-label="Later" onclick="later()">Later</button>
<button type="button" aria-label="Count"
  onclick="navigator.sendBeacon('/count', 'pressed'); out.textContent = 'Counted'">Count</button>
<a href="/done">Finished orders</a>
<form action="/submit" method="post"><input type="hidden" name="name" value="Grace">
<button aria-label="Post">Post</button>
<button aria-label="Quiet" onclick="this.dataset.pressed = 1; return false">Quiet</button>
<button aria-label="Checked"
  onclick="this.dataset.pressed = 1; setTimeout(() => send('/submit'), 200); return false">Checked</button></form>
<button type="button" aria-label="Pulse" onclick="later(); setTimeout(() => send('/submit'), 700)">Pulse</button>
<button type="button" aria-label="Poll" onclick="setTimeout(() => fetch('/done'), 1200)">Poll</button>
<form action="/slow" method="post" target="_blank"><input type="hidden" name="name" value="Elsa">
<button aria-label="Elsewhere">Elsewhere</button></form>
<p id="out"></p>
<script>
const out = document.getElementById('out');
document.querySelector('[name=phone]').addEventListener('input', (event) => {{
  const digits = event.target.value.replace(/\\D/g, '').slice(0, 10);
  if (digits.length < 10) return;
  event.target.value = `(${{digits.slice(0, 3)}}) ${{digits.slice(3, 6)}}-${{digits.slice(6)}}`;
}});
async function send(path) {{
  const body = new URLSearchParams(new FormData(document.getElementById('order')));
  const answer = await fetch(path, {{ method: 'POST', body }});
  out.textContent = await answer.text();
}}
function later() {{ setTimeout(() => {{ out.textContent = 'Arrived by itself'; }}, {_LATER_MS}); }}
</script>"""
# A page that looks like the answer of a sent order, and is no order: the site records nothing for it.
_DONE: str = "<!doctype html><title>Finished orders</title><p>Order 1 received for Ada</p>"


class _Site(ThreadingHTTPServer):
    """The site, and every order it was sent, in the order they came."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.orders: list[dict[str, str]] = []


class _Handler(BaseHTTPRequestHandler):
    server: _Site

    def do_GET(self) -> None:
        self._send(_DONE if urlsplit(self.path).path == "/done" else _PAGE)

    def do_POST(self) -> None:
        sent: bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if urlsplit(self.path).path == "/count":
            # A beacon: the page never reads its answer, and it is no order.
            self._send("")
            return
        if urlsplit(self.path).path == "/slow":
            time.sleep(_SLOW_S)
        order: dict[str, str] = dict(parse_qsl(sent.decode("utf-8"), keep_blank_values=True))
        self.server.orders.append(order)
        self._send(f"Order {len(self.server.orders)} received for {order.get('name', '')}")

    def _send(self, text: str) -> None:
        body: bytes = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def site() -> Iterator[_Site]:
    server: _Site = _Site()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def _address(site: _Site) -> str:
    return f"http://127.0.0.1:{site.server_address[1]}"


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


def _ref(view: PageView, name: str, role: str) -> str:
    found: re.Match[str] | None = re.search(rf'{role} "{name}"[^\n]*\[ref=([^\]]+)\]', view.body)
    assert found is not None, view.body
    return found.group(1)


async def _let_in(field: SecretField, text: str | None) -> None:
    """The check of a run that may type into a field that takes a secret: it lets the text in."""
    del field, text


def _statuses(report: StepReport) -> list[str]:
    return [check.status for check in report.checks]


async def test_a_fill_a_choice_and_a_tick_are_read_back_and_the_order_the_site_took_holds_them(
    browser: BrowserSession, site: _Site
) -> None:
    view: PageView = await browser.open_url("read-back", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "read-back",
        [
            Step(action="fill", ref=_ref(view, "Name", "textbox"), text="Ada"),
            Step(action="type", ref=_ref(view, "Phone", "textbox"), text="5551234567"),
            Step(action="select", ref=_ref(view, "Plan", "combobox"), text="Pro"),
            Step(action="check", ref=_ref(view, "Terms", "checkbox")),
            Step(action="click", ref=_ref(view, "Send", "button"), expect_text="Order 1 received for Ada"),
        ],
        False,
        refuse_secret_field,
        True,
    )
    assert outcome.stopped is None, outcome
    assert [(step.outcome, step.dispatched, _statuses(step)) for step in outcome.steps] == [
        ("done", "sent", ["verified"])
    ] * 5
    # The phone is verified though the page wrote it its own way: the same digits, formatted.
    assert outcome.steps[1].checks[0].evidence == "formatted by the page"
    assert site.orders == [
        {
            "name": "Ada",
            "phone": "(555) 123-4567",
            "locked": "fixed",
            "code": "",
            "site": "",
            "plan": "Pro",
            "terms": "on",
            "secret": "",
        }
    ]


async def test_a_field_that_puts_its_value_back_fails_its_step_and_nothing_is_sent(
    browser: BrowserSession, site: _Site
) -> None:
    view: PageView = await browser.open_url("locked", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "locked",
        [
            Step(action="fill", ref=_ref(view, "Locked", "textbox"), text="mine"),
            Step(action="click", ref=_ref(view, "Send", "button")),
        ],
        False,
        refuse_secret_field,
        True,
    )
    wrote, click = outcome.steps
    assert (wrote.outcome, wrote.dispatched, _statuses(wrote)) == ("failed", "sent", ["contradicted"])
    assert "did not take the text" in wrote.detail
    assert (click.outcome, click.dispatched) == ("skipped", "not_sent")
    assert outcome.stopped == "step 1 failed"
    assert site.orders == []


async def test_a_dropdown_and_a_box_the_page_puts_back_fail_their_steps(browser: BrowserSession, site: _Site) -> None:
    view: PageView = await browser.open_url("put-back", f"{_address(site)}/", True)
    chose: ActOutcome = await browser.act(
        "put-back",
        [Step(action="select", ref=_ref(view, "Stuck", "combobox"), text="Two")],
        False,
        refuse_secret_field,
        True,
    )
    assert (chose.steps[0].outcome, _statuses(chose.steps[0])) == ("failed", ["contradicted"])
    # Playwright saw the box ticked; the page cleared it a moment later, which only the reading after its turn sees.
    ticked: ActOutcome = await browser.act(
        "put-back", [Step(action="check", ref=_ref(view, "Bounce", "checkbox"))], False, refuse_secret_field, True
    )
    assert (ticked.steps[0].outcome, ticked.steps[0].dispatched, _statuses(ticked.steps[0])) == (
        "failed",
        "sent",
        ["contradicted"],
    )
    # A box the page clears at once is clear at Playwright's own reading, which raises (item 1157). It is the same
    # finding, told the same way: on a slow runner the timer above came first too, and the step read "may have been
    # sent" with nothing checked.
    snapped: ActOutcome = await browser.act(
        "put-back", [Step(action="check", ref=_ref(view, "Snap", "checkbox"))], False, refuse_secret_field, True
    )
    assert (snapped.steps[0].outcome, snapped.steps[0].dispatched, _statuses(snapped.steps[0])) == (
        "failed",
        "sent",
        ["contradicted"],
    )


async def test_a_value_the_page_cuts_is_uncertain_and_holds_the_click_after_it_back(
    browser: BrowserSession, site: _Site
) -> None:
    view: PageView = await browser.open_url("cut", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "cut",
        [
            Step(action="fill", ref=_ref(view, "Code", "textbox"), text="ABCDEFGH"),
            # The fields of one form do not wait for each other: this one is still written.
            Step(action="fill", ref=_ref(view, "Name", "textbox"), text="Ada"),
            Step(action="click", ref=_ref(view, "Send", "button")),
        ],
        False,
        refuse_secret_field,
        True,
    )
    cut, name, click = outcome.steps
    assert (cut.outcome, cut.dispatched, _statuses(cut)) == ("done", "sent", ["uncertain"])
    # What the field holds is told by its length, never by its text.
    assert cut.checks[0].evidence == "it holds 5 characters where 8 were written"
    assert "ABCDE" not in cut.model_dump_json()
    assert (name.outcome, _statuses(name)) == ("done", ["verified"])
    assert (click.outcome, click.dispatched) == ("skipped", "not_sent")
    assert outcome.stopped is not None and "step 1's field is not verified" in outcome.stopped
    # What to do next is said: where the field's value shows, and that the steps go again without the write.
    assert "send the steps from 3 on again without writing it again" in outcome.stopped
    assert site.orders == []


async def test_an_address_the_page_puts_its_scheme_before_is_held_and_the_click_after_it_goes(
    browser: BrowserSession, site: _Site
) -> None:
    """Item 1149, from a run of the owner's: the field held what was written with ``https://`` before it, the fill
    was called uncertain and the click after it held back, twice."""
    view: PageView = await browser.open_url("scheme", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "scheme",
        [
            Step(action="fill", ref=_ref(view, "Site", "textbox"), text="gistsmith.example"),
            Step(action="click", ref=_ref(view, "Send", "button"), expect_text="Order 1 received"),
        ],
        False,
        refuse_secret_field,
        True,
    )
    wrote, click = outcome.steps
    assert (wrote.outcome, _statuses(wrote)) == ("done", ["verified"]), outcome
    assert wrote.checks[0].evidence == "the page added to it: 25 characters where 17 were written"
    assert (click.outcome, _statuses(click)) == ("done", ["verified"])
    assert outcome.stopped is None
    assert [order["site"] for order in site.orders] == ["https://gistsmith.example"]
    # Written again, the same text leaves the field as it was, and the field holds it: the step does not fail.
    again: ActOutcome = await browser.act(
        "scheme",
        [Step(action="fill", ref=_ref(view, "Site", "textbox"), text="gistsmith.example")],
        False,
        refuse_secret_field,
        True,
    )
    assert (again.steps[0].outcome, _statuses(again.steps[0])) == ("done", ["verified"]), again


async def test_a_field_that_takes_a_secret_is_never_read_back(browser: BrowserSession, site: _Site) -> None:
    view: PageView = await browser.open_url("secret", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "secret", [Step(action="fill", ref=_ref(view, "Secret", "textbox"), text=_PASSWORD)], False, _let_in, True
    )
    (wrote,) = outcome.steps
    assert (wrote.outcome, wrote.dispatched, wrote.checks) == ("done", "sent", ())
    assert wrote.detail == "a field that takes a secret is not read back"
    assert _PASSWORD not in outcome.model_dump_json()


async def test_a_click_that_does_nothing_is_not_verified_and_stops_the_batch(
    browser: BrowserSession, site: _Site
) -> None:
    view: PageView = await browser.open_url("dead", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "dead",
        [
            Step(action="click", ref=_ref(view, "Dead", "button"), expect_text="Order 1 received"),
            Step(action="click", ref=_ref(view, "Send", "button")),
        ],
        False,
        refuse_secret_field,
        True,
    )
    dead, after = outcome.steps
    assert (dead.outcome, dead.dispatched, _statuses(dead)) == ("done", "sent", ["uncertain"])
    assert "not seen" in dead.checks[0].evidence and "before sending it again" in dead.checks[0].reason
    # The page stayed as it was, which is said without the advice to reach the element another way.
    assert dead.detail.startswith("but the page did not change") and "browser_eval" not in dead.detail
    assert (after.outcome, after.dispatched) == ("skipped", "not_sent")
    assert outcome.stopped is not None and "was not seen" in outcome.stopped
    assert site.orders == []


async def test_an_answer_that_comes_late_is_waited_for_and_the_order_is_sent_once(
    browser: BrowserSession, site: _Site
) -> None:
    view: PageView = await browser.open_url("slow", f"{_address(site)}/", True)
    await browser.act(
        "slow", [Step(action="fill", ref=_ref(view, "Name", "textbox"), text="Ada")], False, refuse_secret_field, True
    )
    started: float = time.monotonic()
    outcome: ActOutcome = await browser.act(
        "slow",
        [Step(action="click", ref=_ref(view, "Slow", "button"), expect_text="Order 1 received for Ada")],
        False,
        refuse_secret_field,
        True,
    )
    (click,) = outcome.steps
    assert (click.outcome, _statuses(click)) == ("done", ["verified"])
    assert time.monotonic() - started >= _SLOW_S
    assert len(site.orders) == 1


async def test_a_result_the_page_shows_by_itself_later_is_seen_and_not_called_unchanged(
    browser: BrowserSession, site: _Site
) -> None:
    """Half a second after the click nothing had changed and no request had gone: the click named what it expects, so
    it is waited for, and its report does not say the page stayed as it was."""
    view: PageView = await browser.open_url("later", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "later",
        [Step(action="click", ref=_ref(view, "Later", "button"), expect_text="Arrived by itself")],
        False,
        refuse_secret_field,
        True,
    )
    (click,) = outcome.steps
    assert (click.outcome, click.detail, _statuses(click)) == ("done", "", ["verified"])


async def test_a_step_that_expects_what_the_page_shows_already_is_not_sent(
    browser: BrowserSession, site: _Site
) -> None:
    view: PageView = await browser.open_url("shown", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "shown",
        [Step(action="click", ref=_ref(view, "Send", "button"), expect_text="Finished orders")],
        False,
        refuse_secret_field,
        True,
    )
    (click,) = outcome.steps
    assert (click.outcome, click.dispatched, click.checks) == ("failed", "not_sent", ())
    assert "shows already" in click.detail
    assert site.orders == []


async def test_an_address_is_waited_for_and_a_page_that_only_looks_like_an_answer_is_no_order(
    browser: BrowserSession, site: _Site
) -> None:
    view: PageView = await browser.open_url("address", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "address",
        [Step(action="click", ref=_ref(view, "Finished orders", "link"), expect_url="/done")],
        False,
        refuse_secret_field,
        True,
    )
    (click,) = outcome.steps
    assert (click.outcome, _statuses(click)) == ("done", ["verified"])
    assert "Order 1 received for Ada" in outcome.view.body
    # The page says an order was received, and the site took none: only its record says what was done.
    assert site.orders == []


async def test_a_step_says_what_its_page_sent_and_what_the_server_answered(
    browser: BrowserSession, site: _Site
) -> None:
    """Item 1158: a run whose click sent a form read the page, its requests and its scripts to learn whether anything
    went out. The step says it: what the page sent to its own site, and the server's answer."""
    view: PageView = await browser.open_url("sent", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "sent", [Step(action="click", ref=_ref(view, "Send", "button"))], False, refuse_secret_field, True
    )
    (click,) = outcome.steps
    assert (click.outcome, click.checks) == ("done", ())
    assert click.detail == f"its page sent POST {_address(site)}/submit → 200"
    assert outcome.stopped is None and len(site.orders) == 1
    # A beacon is not what a step was for: the page never reads its answer, so the step neither names nor awaits it.
    counted: ActOutcome = await browser.act(
        "sent", [Step(action="click", ref=_ref(view, "Count", "button"))], False, refuse_secret_field, True
    )
    assert counted.steps[0].detail == "" and "Counted" in counted.view.body


async def test_a_forms_button_that_sends_nothing_says_so_and_one_that_sends_its_form_says_where(
    browser: BrowserSession, site: _Site
) -> None:
    view: PageView = await browser.open_url("form", f"{_address(site)}/", True)
    quiet: ActOutcome = await browser.act(
        "form", [Step(action="click", ref=_ref(view, "Quiet", "button"))], False, refuse_secret_field, True
    )
    # The page changed (the button marked itself pressed), and nothing went out: the form was not sent.
    assert quiet.steps[0].detail == (
        "its form sent nothing to the site within 0.5 s (no request, the same address): the page below shows whether "
        "it took the press"
    )
    assert site.orders == []
    posted: ActOutcome = await browser.act(
        "form", [Step(action="click", ref=_ref(view, "Post", "button"))], False, refuse_secret_field, True
    )
    assert posted.steps[0].detail == f"its page sent POST {_address(site)}/submit → 200"
    assert posted.stopped == f"the page navigated to {_address(site)}/submit"
    assert site.orders == [{"name": "Grace"}]
    # A button that is no form's says nothing of a form.
    again: PageView = await browser.open_url("form", f"{_address(site)}/", True)
    later: ActOutcome = await browser.act(
        "form", [Step(action="click", ref=_ref(again, "Later", "button"))], False, refuse_secret_field, True
    )
    assert "form" not in later.steps[0].detail


async def test_what_was_sent_and_is_not_answered_stops_the_batch_and_is_not_sent_again(
    browser: BrowserSession, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server takes longer than a step waits: the step says that no answer has come, the steps after it are not
    run, and the order arrives once."""
    monkeypatch.setattr(browser_session, "_SENT_WAIT_S", 0.3)
    view: PageView = await browser.open_url("unanswered", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "unanswered",
        [
            Step(action="click", ref=_ref(view, "Slow", "button")),
            Step(action="click", ref=_ref(view, "Send", "button")),
        ],
        False,
        refuse_secret_field,
        True,
    )
    slow, after = outcome.steps
    assert slow.detail == f"its page sent POST {_address(site)}/slow → no answer yet"
    assert (after.outcome, after.dispatched) == ("skipped", "not_sent")
    assert outcome.stopped is not None and "has not answered yet: do not send the step again" in outcome.stopped
    for _ in range(50):
        if site.orders:
            break
        await asyncio.sleep(0.1)
    assert len(site.orders) == 1


async def test_a_form_its_script_sends_a_moment_later_is_not_said_to_have_sent_nothing(
    browser: BrowserSession, site: _Site
) -> None:
    """The independent reader of item 1158: a form whose script checks it first changes the page at once and sends
    later, and a line that said it sent nothing would have been read as a press to make again. The step waits as
    long as a page that did not change is watched, and names what went out."""
    view: PageView = await browser.open_url("checked", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "checked", [Step(action="click", ref=_ref(view, "Checked", "button"))], False, refuse_secret_field, True
    )
    assert outcome.steps[0].detail == f"its page sent POST {_address(site)}/submit → 200"
    assert len(site.orders) == 1


async def test_what_a_page_sends_by_itself_after_the_steps_look_is_not_the_steps(
    browser: BrowserSession, site: _Site
) -> None:
    """A page that sends something of its own while a step waits for what it expects (a connection it keeps open
    with POSTs): the request is not told as the step's, and the batch does not stop for it."""
    view: PageView = await browser.open_url("pulse", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "pulse",
        [Step(action="click", ref=_ref(view, "Pulse", "button"), expect_text="Arrived by itself")],
        False,
        refuse_secret_field,
        True,
    )
    (click,) = outcome.steps
    assert (_statuses(click), click.detail, outcome.stopped) == (["verified"], "", None)
    assert len(site.orders) == 1


async def test_what_a_page_reads_by_itself_during_a_steps_wait_leaves_its_unchanged_finding(
    browser: BrowserSession, site: _Site
) -> None:
    """The pre-push review of item 1158: a page that asks for something by itself while a step waits for its sight
    (a chat that polls) had the step's "did not change" taken off its line, since any request counted. Only what
    left by the end of the step's own look does."""
    view: PageView = await browser.open_url("poll", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "poll",
        [Step(action="click", ref=_ref(view, "Poll", "button"), expect_text="Never shown on this page")],
        False,
        refuse_secret_field,
        True,
    )
    (click,) = outcome.steps
    assert _statuses(click) == ["uncertain"]
    assert click.detail.startswith("but the page did not change"), click.detail


async def test_a_form_sent_to_a_new_tab_is_not_said_to_have_sent_nothing(browser: BrowserSession, site: _Site) -> None:
    """A form whose answer opens in a new tab goes out from that tab, so this one sends nothing: the batch stops for
    the new tab and the line does not say that the form sent nothing, however late the server answers. (The pre-push
    review of item 1158 named the case of a tab still on its way, which `_run_one` now excludes by itself; here
    Chrome reports the tab at once, so this test holds the behaviour and not that one branch.)"""
    view: PageView = await browser.open_url("elsewhere", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "elsewhere", [Step(action="click", ref=_ref(view, "Elsewhere", "button"))], False, refuse_secret_field, True
    )
    assert "sent nothing" not in outcome.steps[0].detail, outcome.steps[0].detail
    assert outcome.stopped is not None and "tab" in outcome.stopped
    for _ in range(50):
        if site.orders:
            break
        await asyncio.sleep(0.1)
    assert site.orders == [{"name": "Elsa"}]


async def test_a_step_that_failed_after_its_page_sent_something_says_what_was_sent(
    browser: BrowserSession, site: _Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A click that went out and then failed (the page left under it): a run told only that the step failed pressed
    again, which is an order sent twice. The line names what the page sent, and the batch's stop says to look."""
    stepped = browser_session.run_step

    async def lost_after(page: Page, step: Step, timeout_ms: int, secret_field: SecretFieldCheck) -> StepResult:
        await stepped(page, step, timeout_ms, secret_field)
        raise BrowserError("the page went away under the click", {})

    monkeypatch.setattr(browser_session, "run_step", lost_after)
    view: PageView = await browser.open_url("lost", f"{_address(site)}/", True)
    outcome: ActOutcome = await browser.act(
        "lost", [Step(action="click", ref=_ref(view, "Send", "button"))], False, refuse_secret_field, True
    )
    (click,) = outcome.steps
    assert (click.outcome, click.dispatched) == ("failed", "unknown")
    assert click.detail == f"the page went away under the click; its page sent POST {_address(site)}/submit → 200"
    assert outcome.stopped == (
        f"step 1 failed, and its page sent POST {_address(site)}/submit while it ran: look at the page before "
        "sending the step again"
    )
    assert len(site.orders) == 1
