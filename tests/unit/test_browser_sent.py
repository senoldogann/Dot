"""What a step's line says its page sent (item 1158): requests to the page's own site alone, each with the server's
answer, and the one that has none yet."""

from dotagent.browser.session import Sent, same_site, sent_words, unanswered


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
