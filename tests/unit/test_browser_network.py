"""The background calls a tab keeps (item 714): each keeps the number it was listed with while it is kept, however many
come after it and push the oldest out, and an address's secret parameters are hidden."""

from collections import deque
from dataclasses import dataclass, field

from dotagent.browser.network import KEPT_CALLS, Fetched, call_lines, hidden_address, note_call, read_answer


@dataclass
class _Request:
    resource_type: str
    method: str = "GET"


@dataclass
class _Response:
    url: str
    request: _Request
    status: int = 200
    headers: dict[str, str] = field(default_factory=lambda: {"content-type": "application/json"})


def _kept(count: int) -> deque[Fetched]:
    kept: deque[Fetched] = deque(maxlen=KEPT_CALLS)
    for index in range(count):
        note_call(kept, _Response(f"https://shop.example/api/items?page={index}", _Request("fetch")))  # type: ignore[arg-type]
    return kept


def test_a_call_keeps_its_number_as_newer_ones_push_the_oldest_out() -> None:
    kept: deque[Fetched] = _kept(KEPT_CALLS + 5)
    assert [call.number for call in kept] == list(range(5, KEPT_CALLS + 5))
    newest, *_ = call_lines(list(kept), "", 20, hidden_address)
    assert newest.startswith(f"#{KEPT_CALLS + 4} GET 200 application/json ") and newest.endswith("page=54")
    [found] = call_lines(list(kept), "page=7", 20, hidden_address)
    assert found.startswith("#7 ")


def test_only_the_calls_of_the_pages_scripts_are_kept() -> None:
    kept: deque[Fetched] = deque(maxlen=KEPT_CALLS)
    for kind in ("document", "image", "stylesheet", "xhr", "fetch"):
        note_call(kept, _Response(f"https://shop.example/{kind}", _Request(kind)))  # type: ignore[arg-type]
    assert [call.url for call in kept] == ["https://shop.example/xhr", "https://shop.example/fetch"]


def test_a_secret_parameter_of_an_address_is_hidden() -> None:
    assert hidden_address("https://a.example/x?q=kettle&api_key=k-1&Session=s&page=2") == (
        "https://a.example/x?q=kettle&api_key=[hidden]&Session=[hidden]&page=2"
    )
    assert hidden_address("https://a.example/x") == "https://a.example/x"


def test_an_answer_that_is_no_plain_json_has_its_secrets_hidden_too() -> None:
    """The code review of item 714: a JSON behind a guard (Google's ``)]}'``), JSON lines and a form's text came to the
    run as they were, their tokens with them."""
    assert read_answer(')]}\'\n{"a": 1, "session_id": "s"}', True) == '{"a":1,"session_id":"[hidden]"}'
    assert read_answer('{"token": "t"}\n{"b": 2}', True) == '{"token":"[hidden]"}\n{"b":2}'
    assert read_answer("access_token=abc&expires=3600", True) == "access_token=[hidden]&expires=3600"
    assert read_answer("name: Ada\napi_key: k-9", True) == "name: Ada\napi_key: [hidden]"
    assert read_answer("access_token=abc", False) == "access_token=abc"


def test_a_secret_in_a_text_of_a_json_answer_is_hidden_too() -> None:
    """The code review of item 714: a value whose key names no secret may hold one, in an address or in a JSON written
    as text, in an answer of JSON lines too."""
    assert read_answer('{"next": "/api/items?page=2&access_token=abc"}', True) == (
        '{"next":"/api/items?page=2&access_token=[hidden]"}'
    )
    assert read_answer('{"data": "{\\"session\\": \\"xyz\\"}"}', True) == '{"data":"{\\"session\\": [hidden]}"}'
    assert read_answer('{"a": "token=t"}\n{"b": 2}', True) == '{"a":"token=[hidden]"}\n{"b":2}'
    assert read_answer('{"next": "/api?access_token=abc"}', False) == '{"next":"/api?access_token=abc"}'


def test_a_hidden_value_cannot_be_found_by_filtering_on_it() -> None:
    """The code review of item 714: the filter matched the address before its secrets were hidden, so asking for
    ``access_token=a``, then ``access_token=ab`` and so on read the token from which calls were listed."""
    kept: deque[Fetched] = deque(maxlen=KEPT_CALLS)
    note_call(kept, _Response("https://a.example/api?access_token=ab12", _Request("xhr")))  # type: ignore[arg-type]
    assert call_lines(list(kept), "access_token=a", 20, hidden_address) == []
    assert len(call_lines(list(kept), "access_token=[hidden]", 20, hidden_address)) == 1
    assert len(call_lines(list(kept), "access_token=a", 20, lambda url: url)) == 1
