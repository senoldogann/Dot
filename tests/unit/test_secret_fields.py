"""What a field of a web page says about the secret it takes, what kind of action typing into it is, and how a drop
onto an element is judged."""

import pytest

from dotagent.browser.safety import assess_step, key_enters_text, secret_field_flag, secret_field_label, secret_kind
from dotagent.browser.types import SecretKind, Step
from dotagent.policy.elements import Element
from dotagent.policy.types import Flag


@pytest.mark.parametrize(
    ("marking", "kind"),
    [
        ("password", "password"),
        ("one-time-code", "one-time-code"),
        ("section-pay billing CC-Number", "cc-number"),
        ("username current-password", "current-password"),
        ("password current-password", "password"),
        ("password cc-csc", "cc-csc"),
        ("email", None),
        ("", None),
        (None, None),
    ],
)
def test_a_field_is_secret_by_its_own_marking(marking: object, kind: SecretKind | None) -> None:
    assert secret_kind(marking) == kind


@pytest.mark.parametrize(
    ("kind", "flag", "label"),
    [
        ("password", "credential", "a password field"),
        ("one-time-code", "credential", "a one-time-code field"),
        ("cc-csc", "financial", "a cc-csc field"),
    ],
)
def test_a_card_field_is_about_money_and_every_other_secret_is_a_credential(
    kind: SecretKind, flag: Flag, label: str
) -> None:
    assert (secret_field_flag(kind), secret_field_label(kind)) == (flag, label)


@pytest.mark.parametrize(
    ("key", "enters"),
    [
        ("4", True),
        ("Shift+A", True),
        ("Digit4", True),
        ("Space", True),
        ("Control+V", True),
        ("Meta+v", True),
        ("Enter", False),
        ("Tab", False),
        ("Backspace", False),
        ("Control+A", False),
        ("F5", False),
    ],
)
def test_a_key_that_puts_text_in_is_typing(key: str, enters: bool) -> None:
    assert key_enters_text(key) is enters


@pytest.mark.parametrize(
    ("target", "flags"),
    [
        (Element(role="list", name="Delete forever"), ("destructive",)),
        (Element(role="region", name="Send to customer"), ("external_send", "message_send")),
        (Element(role="textbox", name="Account password"), ("credential",)),
        (Element(role="list", name="Done"), ()),
    ],
)
def test_a_drop_is_judged_by_the_name_of_what_it_drops_onto_whatever_its_role(
    target: Element, flags: tuple[Flag, ...]
) -> None:
    dragged: Element = Element(role="listitem", name="Delete account")
    assert assess_step(Step(action="drag", ref="e1", to_ref="e2"), dragged, target).flags == flags
