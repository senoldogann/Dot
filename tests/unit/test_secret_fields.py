"""What a field of a web page says about the secret it takes, and what kind of action typing into it is."""

import pytest

from dotagent.browser.safety import key_enters_text, secret_field_flag, secret_field_label, secret_kind
from dotagent.browser.types import SecretKind
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
