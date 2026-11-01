"""The values of password, one-time-code and card fields stay out of what a run reads (item 467), in a real headless
Chrome against local pages: the snapshot, a search, the changes view after a value changed, the text and the links of
the page, and a screenshot, a field of another origin's frame included; an ordinary field and a page of sixty filled
fields keep their values; and yolo, which hides nothing, sees them all."""

import io
import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio
from PIL import Image
from test_browser import ref_of

from dotagent.browser.safety import refuse_secret_field
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.browser.types import (
    ActOutcome,
    FindResult,
    PageView,
    Screenshot,
    ScriptRun,
    SecretField,
    Step,
    TextView,
)
from dotagent.config import BrowserConfig, ServerConfig, load_config, load_default_config
from dotagent.core.errors import BrowserError

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

SERVER: ServerConfig = load_default_config().server
_SECRETS: tuple[str, ...] = ("hunter2", "123456", "4242424242424242", "5555555555554444")
_MASK: tuple[int, int, int] = (51, 51, 51)
# A JPEG keeps a flat colour within a few levels.
_TOLERANCE: int = 14
_CARD_FRAME: str = (
    "<!doctype html><title>Card</title>"
    "<style>body{margin:0}input{width:100%;height:76px;border:0;font-size:20px}</style>"
    '<input aria-label="Frame card" autocomplete="cc-number" value="5555555555554444" placeholder="Card">'
)


def _form(frame_origin: str) -> str:
    return (
        "<!doctype html><title>Pay</title><style>input{display:block;width:300px;height:40px;margin:8px;"
        "font-size:20px}</style>"
        '<label>Account password <input id="pass" type="password" value="hunter2"></label>'
        '<label>Login code <input id="code" autocomplete="one-time-code" value="123456" placeholder="6 digits"></label>'
        '<label>Card number <input id="card" autocomplete="section-pay cc-number" value="4242424242424242" '
        'placeholder="1234 1234 1234 1234"></label>'
        '<label>Full name <input id="name" value="Ada" placeholder="Your name"></label>'
        f'<iframe id="frame" title="Card frame" src="{frame_origin}/card" width="320" height="80"></iframe>'
        '<textarea aria-label="Notes">Some notes</textarea><a href="/next">Next</a>'
    )


def _frame_only(frame_origin: str) -> str:
    """A page whose only secret field is in a frame of another origin, as a payment provider's is."""
    return f'<!doctype html><title>Frame only</title><iframe title="Card frame" src="{frame_origin}/card"></iframe>'


# A card's expiry in dropdowns, as a checkout page has it, and a dropdown that is no secret (item 554).
_EXPIRY: str = (
    "<!doctype html><title>Expiry</title><style>select{display:block;width:200px;height:40px;margin:8px}</style>"
    '<label>Expiry month <select id="month" autocomplete="cc-exp-month">'
    "<option>01</option><option selected>07</option></select></label>"
    '<label>Expiry year <select id="year" autocomplete="section-pay cc-exp-year">'
    "<option>2029</option><option selected>2031</option></select></label>"
    '<label>Country <select id="country"><option selected>Norway</option><option>Sweden</option></select></label>'
)
_MANY: str = "<!doctype html><title>Many</title>" + "".join(
    f'<label>Field {number} <input value="Item {number}"></label>' for number in range(1, 61)
)


def _serve(pages: dict[str, str]) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body: bytes = pages.get(self.path.split("?", 1)[0], "<!doctype html><title>Next</title>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def frame_site() -> Iterator[str]:
    """A second server: another port is another origin, as a payment provider's frame is."""
    yield from _serve({"/card": _CARD_FRAME})


@pytest.fixture(scope="module")
def site(frame_site: str) -> Iterator[str]:
    yield from _serve(
        {"/form": _form(frame_site), "/many": _MANY, "/frame-only": _frame_only(frame_site), "/expiry": _EXPIRY}
    )


@pytest.fixture(scope="module")
def browser_config(tmp_path_factory: pytest.TempPathFactory) -> BrowserConfig:
    settings: Path = tmp_path_factory.mktemp("settings") / "config.toml"
    settings.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    return load_config(settings).browser


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(
    browser_config: BrowserConfig, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[BrowserSession]:
    config: BrowserConfig = browser_config
    session: BrowserSession = BrowserSession(
        config, SERVER, tmp_path_factory.mktemp("profile"), tmp_path_factory.mktemp("downloads"), no_runs_at_work
    )
    yield session
    await session.aclose()


def _leaks(text: str) -> list[str]:
    return [secret for secret in _SECRETS if secret in text]


async def test_no_view_a_run_reads_shows_a_secret_fields_value(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("hidden", f"{site}/form", True)
    assert _leaks(view.body) == [], view.body
    assert 'textbox "Account password"' in view.body and "[hidden]" in view.body
    # An ordinary field, a text area and the placeholders keep what they show.
    assert "Ada" in view.body and "Some notes" in view.body and "6 digits" in view.body
    for needle in ("hunter2", "password", "Card", "4242"):
        found: FindResult = await browser.find("hidden", needle, 20, True)
        assert _leaks(" ".join(found.hits)) == [], (needle, found.hits)
    # What the owner types in a takeover, played by the page: the changes view after it shows no value either.
    changed: ScriptRun = await browser.run_script(
        "hidden",
        "document.querySelector('#pass').value = 'typed-in-a-takeover';"
        " document.querySelector('#name').value = 'Grace'; true",
        5.0,
        False,
    )
    assert changed.end == "value", changed
    after: PageView = await browser.scroll("hidden", "down", 5, None, True)
    assert "typed-in-a-takeover" not in after.body and _leaks(after.body) == [], after.body
    text: str = (await browser.read_text("hidden", None, 0, 20_000)).text
    links: FindResult = await browser.links("hidden", "", 50, True)
    assert _leaks(text) == [] and "typed-in-a-takeover" not in text
    assert _leaks(" ".join(links.hits)) == []


def _pixel(image: Image.Image, box: dict[str, float]) -> tuple[int, int, int]:
    centre: tuple[int, int] = (round(box["x"] + box["w"] / 2), round(box["y"] + box["h"] / 2))
    red, green, blue = image.convert("RGB").getpixel(centre)  # type: ignore[misc]
    return int(red), int(green), int(blue)


def _masked(colour: tuple[int, int, int]) -> bool:
    return all(abs(channel - mask) <= _TOLERANCE for channel, mask in zip(colour, _MASK, strict=True))


async def _boxes(browser: BrowserSession, thread: str) -> dict[str, dict[str, float]]:
    ran: ScriptRun = await browser.run_script(
        thread,
        "Object.fromEntries(['pass', 'code', 'card', 'name', 'frame'].map((id) => {"
        " const r = document.getElementById(id).getBoundingClientRect();"
        " return [id, {x: r.x, y: r.y, w: r.width, h: r.height}]; }))",
        5.0,
        False,
    )
    assert ran.end == "value", ran
    boxes: dict[str, dict[str, float]] = json.loads(ran.text)
    return boxes


async def test_a_screenshot_covers_the_secret_fields_in_every_frame_but_not_the_others(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("shot", f"{site}/form", True)
    boxes: dict[str, dict[str, float]] = await _boxes(browser, "shot")
    shot: Screenshot = await browser.screenshot("shot", True)
    image: Image.Image = Image.open(io.BytesIO(shot.data))
    for secret in ("pass", "code", "card", "frame"):
        assert _masked(_pixel(image, boxes[secret])), (secret, _pixel(image, boxes[secret]))
    assert not _masked(_pixel(image, boxes["name"]))


async def test_a_card_expiry_dropdown_hides_its_choice_refuses_the_step_and_is_covered_in_a_screenshot(
    browser: BrowserSession, site: str
) -> None:
    """The month and the year of a card are dropdowns more often than fields (item 554): they take a card secret, as the
    card's number does. Outside yolo the view does not say which option is chosen, the step is refused and a screenshot
    covers them; a dropdown that is no secret keeps all three; yolo hides nothing and refuses nothing."""
    view: PageView = await browser.open_url("expiry", f"{site}/expiry", True)
    assert '"07"' in view.body and '"2031"' in view.body, view.body
    assert '"07" [selected]' not in view.body and '"2031" [selected]' not in view.body, view.body
    assert '"Norway" [selected]' in view.body, view.body
    month, country = ref_of(view.body, 'combobox "Expiry month"'), ref_of(view.body, 'combobox "Country"')
    refused: ActOutcome = await browser.act(
        "expiry", [Step(action="select", ref=month, text="01")], False, refuse_secret_field, True
    )
    assert [step.outcome for step in refused.steps] == ["failed"], refused
    assert "cc-exp-month" in refused.steps[0].detail and "ask the user" in refused.steps[0].detail, refused
    allowed: ActOutcome = await browser.act(
        "expiry", [Step(action="select", ref=country, text="Sweden")], False, refuse_secret_field, True
    )
    assert [step.outcome for step in allowed.steps] == ["done"], allowed
    chosen: ScriptRun = await browser.run_script(
        "expiry",
        "[document.getElementById('month').value, document.getElementById('country').value]",
        5.0,
        False,
    )
    assert json.loads(chosen.text) == ["07", "Sweden"], chosen
    boxes: ScriptRun = await browser.run_script(
        "expiry",
        "Object.fromEntries(['month', 'year', 'country'].map((id) => {"
        " const r = document.getElementById(id).getBoundingClientRect();"
        " return [id, {x: r.x, y: r.y, w: r.width, h: r.height}]; }))",
        5.0,
        False,
    )
    where: dict[str, dict[str, float]] = json.loads(boxes.text)
    shot: Screenshot = await browser.screenshot("expiry", True)
    image: Image.Image = Image.open(io.BytesIO(shot.data))
    assert _masked(_pixel(image, where["month"])) and _masked(_pixel(image, where["year"]))
    assert not _masked(_pixel(image, where["country"]))

    async def allow(field: SecretField, text: str | None) -> None:
        return None

    open_view: PageView = await browser.open_url("expiry-yolo", f"{site}/expiry", False)
    assert '"07" [selected]' in open_view.body, open_view.body
    month_ref: str = ref_of(open_view.body, 'combobox "Expiry month"')
    chosen_in_yolo: ActOutcome = await browser.act(
        "expiry-yolo", [Step(action="select", ref=month_ref, text="01")], False, allow, False
    )
    assert [step.outcome for step in chosen_in_yolo.steps] == ["done"], chosen_in_yolo


async def test_yolo_sees_every_value(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("yolo", f"{site}/form", False)
    assert _leaks(view.body) == list(_SECRETS), view.body
    boxes: dict[str, dict[str, float]] = await _boxes(browser, "yolo")
    shot: Screenshot = await browser.screenshot("yolo", False)
    assert not _masked(_pixel(Image.open(io.BytesIO(shot.data)), boxes["pass"]))


async def test_a_page_of_sixty_filled_fields_keeps_their_values(browser: BrowserSession, site: str) -> None:
    view: PageView = await browser.open_url("many", f"{site}/many", True)
    found: FindResult = await browser.find("many", "Item", 100, True)
    shown: str = view.body + "\n".join(found.hits)
    assert all(f"Item {number}" in shown for number in range(1, 61)), shown
    assert "[hidden]" not in shown


@pytest.mark.parametrize("page", ["form", "frame-only"])
async def test_a_script_stays_off_a_page_that_holds_a_secret_field_in_any_frame_and_yolo_runs_it(
    browser: BrowserSession, site: str, page: str
) -> None:
    """A script reads a field's value whole, a value Chrome filled in too (item 533): outside yolo it is refused on a
    page that holds a password, a code or a card field, the one in a frame of another origin included."""
    await browser.open_url(f"script-{page}", f"{site}/{page}", True)
    with pytest.raises(BrowserError, match="a script stays off a page that holds a password"):
        await browser.run_script(f"script-{page}", "document.title", 5.0, True)
    # Yolo hides nothing: the same script runs.
    ran: ScriptRun = await browser.run_script(f"script-{page}", "document.title", 5.0, False)
    assert ran.end == "value", ran


async def test_a_script_is_run_on_a_page_without_one_and_what_it_gives_back_hides_the_cookies(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("cookies", f"{site}/many", True)
    ran: ScriptRun = await browser.run_script(
        "cookies",
        "() => { document.cookie = 'session=abcdef0123456789; path=/'; document.cookie = 'lang=en; path=/';"
        " document.cookie = 'consent=dismissed; path=/'; return document.cookie; }",
        5.0,
        True,
    )
    # The value of the session is no part of what the model reads; a short value and a plain word, no session, stay.
    assert (ran.end, json.loads(ran.text)) == ("value", "session=[hidden]; lang=en; consent=dismissed"), ran
    assert "abcdef0123456789" not in ran.text
    # JSON writes it as the value is when it is a string inside an object, too.
    nested: ScriptRun = await browser.run_script(
        "cookies", "() => ({ all: document.cookie, parts: document.cookie.split('; ') })", 5.0, True
    )
    assert "abcdef0123456789" not in nested.text and "session=[hidden]" in nested.text, nested
    # Yolo sees all it reads (item 443): a chat of its own, which no script held a cookie of.
    await browser.open_url("cookies-yolo", f"{site}/many", True)
    shown: ScriptRun = await browser.run_script("cookies-yolo", "document.cookie", 5.0, False)
    assert json.loads(shown.text) == "session=abcdef0123456789; lang=en; consent=dismissed"


async def test_a_thrown_error_and_the_address_a_script_leads_to_show_no_cookie_value_either(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("cookie-out", f"{site}/many", True)
    await browser.run_script(
        "cookie-out",
        "() => { document.cookie = 'session=abcdef0123456789; path=/';"
        " document.cookie = 'api=tok/en+value==12345; path=/'; }",
        5.0,
        False,
    )
    thrown: ScriptRun = await browser.run_script("cookie-out", "() => { throw new Error(document.cookie); }", 5.0, True)
    # (The profile is the module's: the short cookie an earlier test set is still there, and stays.)
    assert thrown.end == "error" and thrown.text.startswith("Error: session=[hidden]"), thrown
    assert "abcdef0123456789" not in thrown.text and "tok/en" not in thrown.text and "api=[hidden]" in thrown.text
    # A script that carries a cookie into an address (written as an address writes it) says it in the landing and in the
    # surprise: neither shows the value.
    led: ScriptRun = await browser.run_script(
        "cookie-out", "() => { location.href = '/landing?c=' + encodeURIComponent(document.cookie); }", 5.0, True
    )
    shown: str = f"{led.text} {led.url} {led.surprise}"
    assert "abcdef0123456789" not in shown and "tok%2Fen%2Bvalue%3D%3D12345" not in shown and "tok/en" not in shown, led
    assert led.surprise is not None and "[hidden]" in led.surprise, led
    # Yolo sees all it is shown.
    await browser.open_url("cookie-out-yolo", f"{site}/many", True)
    seen: ScriptRun = await browser.run_script(
        "cookie-out-yolo", "() => { throw new Error(document.cookie); }", 5.0, False
    )
    assert "abcdef0123456789" in seen.text, seen


async def test_a_cookie_written_into_the_page_is_hidden_before_the_text_is_cut_into_slices(
    browser: BrowserSession, site: str
) -> None:
    """A slice boundary inside the value would leave a half of it in each of two slices that masking cannot match (item
    533): the text is masked whole, and then cut."""
    await browser.open_url("slices", f"{site}/many", True)
    await browser.run_script(
        "slices",
        "() => { document.cookie = 'session=abcdef0123456789; path=/'; document.body.append(document.cookie); }",
        5.0,
        True,
    )
    whole: TextView = await browser.read_text("slices", None, 0, 20_000)
    assert "abcdef0123456789" not in whole.text and "session=[hidden]" in whole.text, whole.text
    # Every slice of seven characters, from every offset: none holds a half of the value.
    slices: list[str] = [(await browser.read_text("slices", None, at, 7)).text for at in range(whole.total_chars)]
    assert not [piece for piece in slices if "abcdef01" in piece or "23456789" in piece], slices


async def test_a_cookie_of_a_narrower_path_is_hidden_when_a_frame_of_that_path_gives_it_back(
    browser: BrowserSession, site: str
) -> None:
    await browser.open_url("paths", f"{site}/many", True)
    ran: ScriptRun = await browser.run_script(
        "paths",
        "async () => { document.cookie = 'acct=zxcvbnm1234567890; path=/account';"
        " const frame = document.createElement('iframe'); frame.src = '/account'; document.body.append(frame);"
        " await new Promise((done) => { frame.onload = done; }); return frame.contentDocument.cookie; }",
        10.0,
        True,
    )
    # (The profile is the module's: the cookies of the tests before are among what the frame gives back.)
    assert ran.end == "value" and "acct=[hidden]" in ran.text and "zxcvbnm1234567890" not in ran.text, ran
