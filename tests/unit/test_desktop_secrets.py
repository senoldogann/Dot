"""The desktop's secret checks that need no screen: a password manager is never controlled, a paste is typing, and a long
typed text goes through the clipboard only into a known field that is not secure."""

import pytest

from dotagent.core.errors import DesktopError
from dotagent.desktop.actions import _pastes, refuse_secrets
from dotagent.desktop.input import parse_chord
from dotagent.desktop.safety import StepContext, enters_text
from dotagent.policy.elements import Element


@pytest.mark.parametrize(
    ("chord", "enters"),
    [("a", True), ("shift+1", True), ("cmd+v", True), ("cmd+shift+v", True), ("cmd+c", False), ("return", False)],
)
def test_a_paste_puts_text_in_like_a_character(chord: str, enters: bool) -> None:
    assert enters_text(parse_chord(chord)) is enters


def test_a_step_at_a_password_manager_is_refused_and_one_elsewhere_is_not() -> None:
    with pytest.raises(DesktopError, match="never controlled"):
        refuse_secrets(StepContext(role="secrets", app="1Password", element=None, secure=False, line=None))
    refuse_secrets(StepContext(role="other", app="TextEdit", element=None, secure=True, line=None))


def test_a_long_typed_text_is_pasted_only_into_a_known_ordinary_field_and_never_a_key() -> None:
    field: StepContext = StepContext(
        role="other", app="TextEdit", element=Element(role="AXTextField", name="Name"), secure=False, line=None
    )
    long_text: str = "x" * 41
    assert _pastes(long_text, field, 40)
    assert not _pastes("x" * 40, field, 40)
    assert not _pastes(long_text, field, 0)
    assert not _pastes(long_text + "\n", field, 40)
    assert not _pastes(long_text + "\t", field, 40)
    assert not _pastes(long_text, field.model_copy(update={"secure": True}), 40)
    assert not _pastes(long_text, field.model_copy(update={"element": None}), 40)
    api_key: StepContext = field.model_copy(update={"element": Element(role="AXTextField", name="OpenAI API key")})
    assert not _pastes("sk-proj-" + "a" * 40, api_key, 40)
    for name in ("Recovery code", "Card number"):
        assert not _pastes(long_text, field.model_copy(update={"element": Element(role="AXTextField", name=name)}), 40)
