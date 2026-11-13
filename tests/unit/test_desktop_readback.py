"""What a desktop step that wrote into a field checks of itself (item 1146). The accessibility calls are replaced:
nothing reaches the screen, so the gate runs these where the tests on the real fixture window do not run."""

from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError, ValueNotKept
from dotagent.desktop import actions, ax
from dotagent.desktop.actions import Inspection, StepEnv
from dotagent.desktop.ax import ElementState
from dotagent.desktop.safety import StepContext
from dotagent.desktop.types import AppInfo, DisplayInfo, Rect, Step

_NOTES: AppInfo = AppInfo(
    pid=700, name="Notes", bundle_id="com.apple.Notes", bundle_path="", kind="regular", frontmost=True
)
_OLD_VALUE: str = "hunter2 old"


def _env(tmp_path: Path) -> StepEnv:
    return StepEnv(
        app=_NOTES,
        app_element="app-element",
        elements={},
        running=(_NOTES,),
        displays=(DisplayInfo(display_id=1, bounds=Rect(0, 0, 2000, 1200), main=True),),
        scale=1.0,
        source=object(),
        config=load_default_config().desktop,
        failsafe_file=tmp_path / "STOP",
        expected_front=None,
        expected_pointer=None,
        preferred=frozenset(),
        secure_field=lambda inspection, what: None,
    )


def _state(role: str, subrole: str) -> ElementState:
    return ElementState(
        role=role, subrole=subrole, label="Name", frame=None, enabled=True, focused=True, selected=False
    )


def _readings(monkeypatch: pytest.MonkeyPatch, values: list[str | None]) -> list[object]:
    """Make the field read, in turn, each of ``values`` (the last one from then on), with no waiting between."""
    asked: list[object] = []

    def read_value(element: object, pid: int) -> str | None:
        asked.append(element)
        return values[min(len(asked), len(values)) - 1]

    monkeypatch.setattr(ax, "read_value", read_value)
    monkeypatch.setattr(actions.time, "sleep", lambda seconds: None)
    return asked


def test_typed_text_is_verified_once_the_field_holds_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # The keys reach the application after the step returns: the first reading still shows the field as it was.
    asked: list[object] = _readings(monkeypatch, ["", "", "Ada Lovelace"])
    (check,) = actions._written_checks(actions._Field(element="field", pid=700, held=""), "Ada Lovelace")
    assert (check.status, check.evidence) == ("verified", "as written")
    assert len(asked) == 3


def test_a_field_that_is_as_it_was_is_uncertain_and_never_contradicted(monkeypatch: pytest.MonkeyPatch) -> None:
    _readings(monkeypatch, [""])
    (check,) = actions._written_checks(actions._Field(element="field", pid=700, held=""), "letters")
    assert check.status == "uncertain"
    assert "after the keys were sent" in check.evidence and "before typing again" in check.reason
    assert "letters" not in check.model_dump_json()


def test_nothing_is_checked_for_a_field_that_is_not_read_or_a_text_that_presses_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[object] = _readings(monkeypatch, ["Ada"])
    assert actions._written_checks(None, "Ada") == ()
    # A secure field, and one whose value is no text, held nothing that may be read.
    assert actions._written_checks(actions._Field(element="field", pid=700, held=None), "Ada") == ()
    # Return may send what the field holds and empty it, which is no refusal of the text.
    assert actions._written_checks(actions._Field(element="field", pid=700, held=""), "Ada\n") == ()
    assert actions._written_checks(actions._Field(element="field", pid=700, held=""), "Ada\t") == ()
    assert asked == []
    # A field that may no longer be read after the keys (it turned secure) is not checked either.
    _readings(monkeypatch, [None])
    assert actions._written_checks(actions._Field(element="field", pid=700, held=""), "Ada") == ()


def test_a_field_that_cannot_be_read_after_the_keys_is_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    def gone(element: object, pid: int) -> str | None:
        raise DesktopError("cannot read the element: the element no longer exists", {"pid": pid, "code": -25202})

    monkeypatch.setattr(ax, "read_value", gone)
    assert actions._written_checks(actions._Field(element="field", pid=700, held=""), "Ada") == ()


def test_only_a_text_of_a_fields_size_is_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    states: dict[str, ElementState] = {
        "plain": _state("AXTextField", ""),
        "secure": _state("AXTextField", "AXSecureTextField"),
        "slider": _state("AXSlider", ""),
        "terminal": _state("AXTextArea", ""),
    }
    answers: dict[tuple[str, str], object] = {
        ("plain", "AXValue"): "Ada",
        ("plain", "AXNumberOfCharacters"): 3,
        ("secure", "AXValue"): "••••",
        ("slider", "AXValue"): 0.5,
        ("terminal", "AXValue"): "x" * 5000,
        ("terminal", "AXNumberOfCharacters"): 5000,
    }
    read: list[tuple[str, str]] = []

    def read_optional(element: str, attribute: str, pid: int) -> object | None:
        read.append((element, attribute))
        return answers.get((element, attribute))

    monkeypatch.setattr(ax, "read_state", lambda element, pid: states[element])
    monkeypatch.setattr(ax, "read_optional", read_optional)
    assert ax.read_value("plain", 700) == "Ada"
    # What a secure field holds is never asked for.
    assert ax.read_value("secure", 700) is None and not any(element == "secure" for element, _ in read)
    # A slider's value is no text, and a terminal's scrollback is too long to read after every step: its size is
    # asked, its text is not.
    assert ax.read_value("slider", 700) is None
    assert ax.read_value("terminal", 700) is None and ("terminal", "AXValue") not in read


def test_a_value_a_field_did_not_keep_fails_the_step_by_its_length_and_not_its_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(actions, "_element_for", lambda env, raw: (3, "element"))
    monkeypatch.setattr(actions, "_aim_at", lambda env, number, element: None)
    monkeypatch.setattr(ax, "set_attribute", lambda element, attribute, value, pid: None)
    monkeypatch.setattr(ax, "read_text", lambda element, pid: ("Name", _OLD_VALUE, len(_OLD_VALUE)))
    inspection: Inspection = Inspection(
        target='textfield "Name" a3 in Notes',
        context=StepContext(role="other", app="Notes", element=None, secure=False, line=None),
    )
    with pytest.raises(ValueNotKept) as refused:
        actions._set_value(_env(tmp_path), Step(action="set_value", ref="a3", text="new value"), inspection)
    assert refused.value.message.startswith("a3 did not keep the value: it holds 11 characters where 9 were set.")
    # A value typed into a plain field may be a password: the step's report never carries what a field holds.
    assert _OLD_VALUE not in str(refused.value)
