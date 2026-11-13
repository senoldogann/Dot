"""What a step of a browser batch came to, beyond its being sent (item 1145), in a real headless Chrome against a local
site that keeps what it was sent.

Every claim is checked twice: by the step's own report, and by what the site recorded, so that a step is never taken
for verified by its report's words alone. A fill, a choice and a tick are read back; a field that puts its value back
fails its step; a value the page cuts holds the next click back; a click that named what it expects is waited for,
verified when that shows and uncertain when it does not, and is never sent twice; a step that expects what the page
shows already is not sent at all.
"""

import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
import pytest_asyncio

from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import ActOutcome, PageView, SecretField, Step, StepReport
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config

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
<label>Plan <select aria-label="Plan" name="plan"><option>Basic</option><option>Pro</option></select></label>
<label>Stuck <select aria-label="Stuck" onchange="this.selectedIndex = 0"><option>One</option><option>Two</option>
</select></label>
<label><input type="checkbox" aria-label="Terms" name="terms"> Terms</label>
<label><input type="checkbox" aria-label="Bounce" onchange="setTimeout(() => {{ this.checked = false; }}, 0)">
Bounce</label>
<label>Secret <input type="password" aria-label="Secret" name="secret"></label>
</form>
<button type="button" aria-label="Dead">Dead</button>
<button type="button" aria-label="Send" onclick="send('/submit')">Send</button>
<button type="button" aria-label="Slow" onclick="send('/slow')">Slow</button>
<button type="button" aria-label="Later" onclick="later()">Later</button>
<a href="/done">Finished orders</a>
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
    assert site.orders == []


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
