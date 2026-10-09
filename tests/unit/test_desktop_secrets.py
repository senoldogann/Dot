"""The desktop's secret checks that need no screen: a password manager is never controlled, and a paste is typing."""

import pytest

from dotagent.core.errors import DesktopError
from dotagent.desktop.actions import refuse_secrets
from dotagent.desktop.input import parse_chord
from dotagent.desktop.safety import StepContext, enters_text


@pytest.mark.parametrize(
    ("chord", "enters"),
    [("a", True), ("shift+1", True), ("cmd+v", True), ("cmd+shift+v", True), ("cmd+c", False), ("return", False)],
)
def test_a_paste_puts_text_in_like_a_character(chord: str, enters: bool) -> None:
    assert enters_text(parse_chord(chord)) is enters


def test_a_step_at_a_password_manager_is_refused_and_one_elsewhere_is_not() -> None:
    with pytest.raises(DesktopError, match="never controlled"):
        refuse_secrets(StepContext(role="secrets", app="1Password", element=None, secure=False))
    refuse_secrets(StepContext(role="other", app="TextEdit", element=None, secure=True))
