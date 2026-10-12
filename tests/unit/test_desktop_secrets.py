"""The desktop's secret checks that need no screen: a password manager is never controlled, nor Dot's own window outside
a yolo run (item 135), a paste is typing, and a long typed text goes through the clipboard only into a known field that
is not secure."""

from pathlib import Path

import pytest

from dotagent.core.errors import DesktopError
from dotagent.desktop.actions import _pastes, refuse_secrets
from dotagent.desktop.input import parse_chord
from dotagent.desktop.safety import AppRole, StepContext, app_role, assess_step, enters_text, window_role
from dotagent.desktop.types import Step
from dotagent.policy.elements import Element, StepAssessment
from dotagent.policy.engine import auto_review
from dotagent.policy.types import ActionInfo


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


def test_a_step_in_dots_own_window_is_refused_and_one_elsewhere_is_not(tmp_path: Path) -> None:
    approve: Element = Element(role="AXButton", name="Approve")
    for name, bundle, refused in (("Dot", "dev.dot.desktop", True), ("TextEdit", "com.apple.TextEdit", False)):
        context: StepContext = StepContext(
            role=app_role(name, bundle), app=name, element=approve, secure=False, line=None
        )
        assessment: StepAssessment = assess_step(Step(action="click", x=10, y=10), context, tmp_path, (), ())
        info: ActionInfo = ActionInfo(
            tool="desktop_act",
            kind="desktop_input",
            risk=assessment.risk,
            summary="click",
            target=name,
            flags=assessment.flags,
        )
        assert (auto_review(info) is not None) is refused, (name, assessment)


@pytest.mark.parametrize(
    ("name", "bundle", "title", "role"),
    [
        ("Google Chrome", "com.google.Chrome", "Dot", "dot"),
        ("Google Chrome", "com.google.Chrome", "Dot - Google Chrome - Work", "dot"),
        ("Google Chrome", "com.google.Chrome", "(2) Plan the trip · Dot", "dot"),
        ("Safari", "com.apple.Safari", "Settings · Dot", "dot"),
        ("Firefox", "org.mozilla.firefox", "(1) Dot — Mozilla Firefox", "dot"),
        ("Helium", "net.imput.helium", "Approvals · Dot - Helium", "dot"),
        ("Google Chrome", "com.google.Chrome", "Dotfiles - GitHub", "other"),
        ("Google Chrome", "com.google.Chrome", "Polka dot - Wikipedia", "other"),
        ("Google Chrome", "com.google.Chrome", None, "other"),
        ("Terminal", "com.apple.Terminal", "Dot — zsh — 80×24", "terminal"),
        ("Finder", "com.apple.finder", "Dot", "finder"),
    ],
)
def test_a_window_that_shows_dots_web_ui_is_dots_own(name: str, bundle: str, title: str | None, role: AppRole) -> None:
    assert window_role(name, bundle, title) == role
