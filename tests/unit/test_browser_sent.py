"""What a step's line says its page sent (item 1158): requests to the page's own site alone, each with the server's
answer, and the one that has none yet."""

from dotagent.browser.driver import NEVER_PRESSED, never_pressed
from dotagent.browser.session import Sent, _dispatch_of, form_sent_nothing, same_site, sent_words, unanswered
from dotagent.core.errors import BrowserError, StepNotSent


def test_what_a_page_sends_to_another_site_is_not_what_its_step_was_for() -> None:
    assert same_site("https://shop.example/basket", "https://shop.example/api/order")
    assert same_site("https://www.shop.example/basket", "https://api.shop.example/order")
    assert not same_site("https://shop.example/basket", "https://counter.example/collect")
    # A host written as numbers is itself alone: two of them that end alike are two machines.
    assert same_site("http://127.0.0.1:8000/form", "http://127.0.0.1:8000/submit")
    assert not same_site("http://10.0.0.1/form", "http://127.0.0.1/submit")
    assert not same_site("about:blank", "https://shop.example/")


def test_a_steps_line_names_each_request_with_its_answer() -> None:
    took: Sent = Sent(method="POST", where="https://shop.example/order", status=200, failed="")
    refused: Sent = Sent(method="PUT", where="https://shop.example/cart", status=422, failed="")
    waiting: Sent = Sent(method="POST", where="https://shop.example/pay", status=None, failed="")
    lost: Sent = Sent(method="DELETE", where="https://shop.example/item/3", status=None, failed="net::ERR_FAILED")
    assert sent_words(()) == ""
    assert sent_words((took,)) == "its page sent POST https://shop.example/order → 200"
    assert sent_words((took, refused, waiting, lost)) == (
        "its page sent POST https://shop.example/order → 200; PUT https://shop.example/cart → 422; "
        "POST https://shop.example/pay → no answer yet and 1 more"
    )
    assert sent_words((lost,)) == "its page sent DELETE https://shop.example/item/3 → failed (net::ERR_FAILED)"
    # Only a request the server may still answer holds the batch: one that failed will not be answered.
    assert unanswered((took, refused, lost)) is None
    assert unanswered((took, waiting)) is waiting


def test_a_press_playwright_tried_and_never_made_was_not_sent() -> None:
    """The pre-push review of item 1158: a press that timed out waiting for its element was "may have been sent",
    so what its page sent by itself in those seconds (a counter, a heartbeat) was told as the step's. Playwright's
    own log says whether the press was made."""
    waited: str = (
        'Locator.click: Timeout 8000ms exceeded.\nCall log:\n  - waiting for locator("aria-ref=e9")\n'
        "  - locator resolved to <button>Send</button>\n  - attempting click action\n"
        "    - waiting for element to be visible, enabled and stable\n    - element is not stable\n"
    )
    made: str = waited + "    - scrolling into view if needed\n    - done scrolling\n    - performing click action\n"
    assert never_pressed(waited) and not never_pressed(made)
    assert never_pressed("  - attempting dblclick action\n  - waiting for element to be visible")
    assert not never_pressed("  - attempting hover action\n  - performing hover action")
    # A failure that is no press says nothing either way: it may have gone out. Playwright logs that it tries a
    # fill and never that it makes one, so a fill is not read by this rule.
    assert not never_pressed("Locator.fill: Timeout 8000ms exceeded.")
    assert not never_pressed(
        "  - attempting fill action\n    - waiting for element to be visible, enabled and editable"
    )
    assert _dispatch_of(BrowserError("click failed", {NEVER_PRESSED: "true"})) == "not_sent"
    assert _dispatch_of(BrowserError("click failed", {"error": made})) == "unknown"
    assert _dispatch_of(StepNotSent("the ref is gone", {})) == "not_sent"


def test_a_form_is_said_to_have_sent_nothing_only_when_nothing_says_otherwise() -> None:
    """Each fact that takes the line away, alone: the page asked for something, the form may have gone by a socket
    or to a tab still on its way (the pre-push review of item 1158: no page of the smoke tests holds a new tab
    back), the batch has other news of the press, or the step's line says already that nothing changed."""
    assert form_sent_nothing(asked=False, elsewhere=False, moved_on=False, told=False)
    assert not form_sent_nothing(asked=True, elsewhere=False, moved_on=False, told=False)
    assert not form_sent_nothing(asked=False, elsewhere=True, moved_on=False, told=False)
    assert not form_sent_nothing(asked=False, elsewhere=False, moved_on=True, told=False)
    assert not form_sent_nothing(asked=False, elsewhere=False, moved_on=False, told=True)
