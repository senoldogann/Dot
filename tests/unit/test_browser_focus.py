"""A click at a pixel focuses a field with no ref to check (item 1142). The page's own word of where the keyboard
is, one slot a frame, is what says the click landed: in a field it was not in, caret included. Leaving a field, and a
field that stays put, are not that."""

from dotagent.browser.driver import focus_landed


def test_the_keyboard_landing_in_a_field_is_a_change() -> None:
    assert focus_landed(("",), ("1.0:0:0",))
    assert focus_landed((), ("2:0:0",))
    # The caret moved in the field that already had the keyboard.
    assert focus_landed(("1.0:0:0",), ("1.0:4:4",))
    # A field in a frame that had none.
    assert focus_landed(("", ""), ("", "0:0:0"))


def test_the_keyboard_leaving_a_field_or_staying_put_is_not() -> None:
    assert not focus_landed(("1.0:0:0",), ("1.0:0:0",))
    assert not focus_landed(("1.0:0:0",), ("",))
    assert not focus_landed(("", ""), ("", ""))
    assert not focus_landed((), ())
