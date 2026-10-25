"""What the driver does with the fields of a later document after a login was filled (item 621): which fields hold a
value that was filled, compared by their letters and digits alone; which document a frame is of; and that a value is
looked for in the documents of the origin it was typed into and in no other, in a document that is still the one Dot
read the address of."""

import pytest

from dotagent.browser.driver import _FIELD_VALUES_SCRIPT, _held_core, _holding, document_of, remark_held
from dotagent.browser.types import HeldValue
from dotagent.vault.hosts import SameOrigin


def test_a_value_is_held_by_a_field_whatever_the_page_does_to_its_spaces_and_signs() -> None:
    assert _held_core("08 / 39") == _held_core("08/39") == "0839"
    assert _held_core("4242 4242-4242.4242") == "4242424242424242"
    assert _held_core("S3cret-Passw0rd!") == "s3cretpassw0rd"
    wanted: frozenset[str] = frozenset({_held_core("4242424242424242"), "0839", "9183", "08august"})
    values: list[list[str]] = [
        ["4242 4242 4242 4242"],
        ["a note"],
        ["08 / 39"],
        ["", "08 - August"],
        ["9183"],
        [""],
        ["918"],
        ["91830"],
    ]
    assert _holding(values, wanted) == [0, 2, 3, 4]


def test_what_a_page_answers_that_is_not_the_list_of_values_holds_nothing() -> None:
    wanted: frozenset[str] = frozenset({"9183"})
    assert _holding(None, wanted) == []
    assert _holding("9183", wanted) == []
    assert _holding([None, "9183", [3, None], ["9183"]], wanted) == [3]


class _Frame:
    """A frame as the driver reads it: its address, the frame it lies in, whether it is gone, and the script it was
    asked to run. ``moves_to`` is the address of the document the frame is in by the time the script runs, when it is
    not the one the frame had when Dot read it; ``looked`` counts the scripts that were run in it, ``read`` those that
    read a field of the document they ran in, ``marked`` the indexes of the fields marked."""

    def __init__(self, url: str, parent: "_Frame | None" = None, fields: list[list[str]] | None = None) -> None:
        self.url: str = url
        self.parent_frame: _Frame | None = parent
        self.moves_to: str | None = None
        self._fields: list[list[str]] = fields or []
        self.looked: int = 0
        self.read: int = 0
        self.marked: list[int] = []
        self._expected: str = ""

    def is_detached(self) -> bool:
        return False

    async def evaluate_handle(self, script: str, expected: str) -> "_Frame":
        self.looked += 1
        self._expected = expected
        return self

    async def evaluate(self, script: str, arg: object = None) -> object:
        if script == _FIELD_VALUES_SCRIPT:
            self.read += 1
            return self._fields
        if "=== null" in script:
            return (self.moves_to or self.url) != self._expected
        self.marked = list(arg)  # type: ignore[call-overload]
        return None

    async def dispose(self) -> None:
        return None


class _Page:
    def __init__(self, *frames: _Frame) -> None:
        self.frames: list[_Frame] = list(frames)


def _origin(address: str) -> SameOrigin:
    origin: SameOrigin | None = SameOrigin.of(address)
    assert origin is not None
    return origin


@pytest.mark.parametrize(
    ("shows", "document"),
    [
        ("https://shop.test/pay", "https://shop.test/pay"),
        ("about:blank", "https://shop.test/pay"),
        ("about:srcdoc", "https://shop.test/pay"),
        ("", "https://shop.test/pay"),
        ("blob:https://shop.test/3f2a", "https://shop.test/3f2a"),
        ("data:text/html,<p>x</p>", "data:text/html,<p>x</p>"),
        ("https://evil.test/a", "https://evil.test/a"),
    ],
)
def test_a_frame_is_of_the_site_it_shows_a_blank_one_of_the_frame_it_lies_in(shows: str, document: str) -> None:
    shop: _Frame = _Frame("https://shop.test/pay")
    assert document_of(_Frame(shows, shop)) == document  # type: ignore[arg-type]


def test_a_blank_frame_of_an_unnamed_frame_is_of_no_site_and_a_blank_main_frame_is_of_none() -> None:
    evil: _Frame = _Frame("https://evil.test/a", _Frame("https://shop.test/pay"))
    assert document_of(_Frame("about:blank", _Frame("about:srcdoc", evil))) == "https://evil.test/a"  # type: ignore[arg-type]
    assert document_of(_Frame("about:blank")) is None  # type: ignore[arg-type]
    assert document_of(_Frame("", _Frame("about:blank"))) is None  # type: ignore[arg-type]


async def test_a_value_is_looked_for_in_the_documents_of_the_origin_it_was_typed_into_and_in_no_other() -> None:
    """A frame of another origin is never asked anything, whatever it holds: by which of its fields Dot marked it would
    learn the value. A document of another origin of the same login is another document: the value was typed into one
    field, of one origin. The marks are placed in a blank frame of the origin too."""
    password: HeldValue = HeldValue("S3cret-Passw0rd!", _origin("https://shop.test/login"))
    elsewhere: HeldValue = HeldValue("Other-Passw0rd!", _origin("https://other.test/login"))
    shop: _Frame = _Frame("https://shop.test/pay", fields=[["a note"], ["S3cret-Passw0rd!"], ["Other-Passw0rd!"]])
    blank: _Frame = _Frame("about:blank", shop, fields=[["S3cret Passw0rd"]])
    subdomain: _Frame = _Frame("https://pay.shop.test/f", shop, fields=[["S3cret-Passw0rd!"]])
    widget: _Frame = _Frame("https://widget.test/o", shop, fields=[["S3cret-Passw0rd!"]])
    inside: _Frame = _Frame("about:blank", widget, fields=[["S3cret-Passw0rd!"]])
    clear: _Frame = _Frame("https://shop.test:8443/pay", shop, fields=[["S3cret-Passw0rd!"]])
    plain: _Frame = _Frame("http://shop.test/pay", shop, fields=[["S3cret-Passw0rd!"]])
    frames: tuple[_Frame, ...] = (shop, blank, subdomain, widget, inside, clear, plain)
    await remark_held(_Page(*frames), {password, elsewhere})  # type: ignore[arg-type]
    assert [frame.looked for frame in frames] == [1, 1, 0, 0, 0, 0, 0]
    # Only the values of the origin a document is of are compared in it: the other login's password is no one's here.
    assert shop.marked == [1] and blank.marked == [0]
    # Nothing to look for in a document that holds no value filled for it.
    await remark_held(_Page(widget, inside), {password, elsewhere})  # type: ignore[arg-type]
    assert widget.looked == 0 and inside.looked == 0


async def test_a_frame_that_moves_between_the_reading_of_its_address_and_the_script_is_not_touched() -> None:
    """The script is told the address Dot read, and returns before it touches a field of a document that is another
    by then: a frame of the origin that sends itself to a document of another site and back would otherwise be
    looked into as the origin's, and read what Dot marked."""
    password: HeldValue = HeldValue("S3cret-Passw0rd!", _origin("https://shop.test/login"))
    moving: _Frame = _Frame("https://shop.test/pay", fields=[["S3cret-Passw0rd!"]])
    moving.moves_to = "https://widget.test/o"
    steady: _Frame = _Frame("https://shop.test/pay", fields=[["S3cret-Passw0rd!"]])
    await remark_held(_Page(moving, steady), {password})  # type: ignore[arg-type]
    assert (moving.looked, moving.read, moving.marked) == (1, 0, [])
    assert (steady.looked, steady.read, steady.marked) == (1, 1, [0])


def test_a_value_that_was_held_shows_in_no_repr() -> None:
    held: HeldValue = HeldValue("S3cret-Passw0rd!", _origin("https://github.com/login"))
    assert "S3cret" not in repr(held) and "S3cret" not in str({held})
