"""Masking the values of secret fields in what a run reads (item 467): which lines show a field's value, the value of a
marked field shown as ``[hidden]`` on its own line and on the ``text`` line a field with a placeholder puts it on, the
rest untouched; yolo hides nothing; and a page that cannot say whether a field is secret has it hidden."""

from dataclasses import dataclass, field
from typing import get_args

import pytest
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.driver import capture_lines, secret_refs
from dotagent.browser.safety import hides_secrets, secret_selector
from dotagent.browser.snapshot import (
    HIDDEN_VALUE,
    SnapshotLine,
    filled_fields,
    find_lines,
    fold_options,
    hide_values,
    parse_snapshot,
    render_lines,
)
from dotagent.browser.types import SecretKind
from dotagent.policy.types import ApprovalMode

# What Chrome's AI snapshot printed for a page of filled fields (Playwright 1.63, item 467's probe): a field without a
# placeholder shows its value on its line, one with a placeholder on a "text" line under it, quoted when it is a number.
# A change of this form in a later Playwright must fail here, not only in the browser tests the gate leaves out.
GOLDEN: str = """- generic [active] [ref=e1]:
  - generic [ref=e2]:
    - text: Pass
    - textbox "Pass" [ref=e3]: hunter2
  - generic [ref=e4]:
    - text: Code
    - textbox "Code" [ref=e5]:
      - /placeholder: 6 digits
      - text: "123456"
  - generic [ref=e6]:
    - text: Card
    - textbox "Card" [ref=e7]:
      - /placeholder: 1234 1234 1234 1234
      - text: "4242424242424242"
  - generic [ref=e8]:
    - text: Name
    - textbox "Name" [ref=e9]:
      - /placeholder: Your name
      - text: Ada
  - generic [ref=e10]:
    - text: Plain
    - textbox "Plain" [ref=e11]: Grace
  - textbox "Notes" [ref=e12]: Some notes
  - searchbox "Find" [ref=e13]: apples
  - textbox "Empty" [ref=e14]
  - link "Pass the test" [ref=e15]
  - button "Send" [ref=e16]
"""
SECRET_REFS: frozenset[str] = frozenset({"e3", "e5", "e7"})


def test_the_fields_that_show_a_value_are_found_on_their_line_and_under_it() -> None:
    lines: tuple[SnapshotLine, ...] = parse_snapshot(GOLDEN)
    assert [line.ref for line in filled_fields(lines)] == ["e3", "e5", "e7", "e9", "e11", "e12", "e13"]


def test_a_marked_fields_value_is_hidden_on_its_line_and_its_text_line_and_the_rest_stays() -> None:
    hidden: tuple[SnapshotLine, ...] = hide_values(parse_snapshot(GOLDEN), SECRET_REFS)
    view: str = render_lines(hidden)
    for value in ("hunter2", "123456", "4242424242424242"):
        assert value not in view, value
    assert 'textbox "Pass" [ref=e3]: [hidden]' in view
    assert "- text: [hidden]" in view and "- /placeholder: 6 digits" in view
    for kept in ("Ada", "Grace", "Some notes", "apples", 'link "Pass the test" [ref=e15]', 'button "Send" [ref=e16]'):
        assert kept in view, kept
    # A search finds the field by its name, never by its value.
    assert find_lines(hidden, "hunter2", 10) == ()
    assert any("[hidden]" in hit for hit in find_lines(hidden, "Pass", 10))


# A card's expiry in dropdowns, as Chrome's snapshot lists a collapsed one: its options without refs, the chosen one
# marked (item 554).
DROPDOWNS: str = """- generic [active] [ref=e1]:
  - combobox "Expiry month" [ref=e2]:
    - option "01" [box=0,0,0,0]
    - option "02" [selected] [box=0,0,0,0]
  - combobox "Country" [ref=e3]:
    - option "Norway" [selected] [box=0,0,0,0]
    - option "Sweden" [box=0,0,0,0]
  - combobox "Empty" [ref=e4]:
    - option "None" [box=0,0,0,0]
"""


def test_the_choice_of_a_dropdown_the_page_marks_secret_is_hidden_and_the_others_stay() -> None:
    lines: tuple[SnapshotLine, ...] = parse_snapshot(DROPDOWNS)
    assert [line.ref for line in filled_fields(lines)] == ["e2", "e3"]
    view: str = render_lines(fold_options(hide_values(lines, frozenset({"e2"}))))
    assert 'combobox "Expiry month" [ref=e2]: options "01", "02"' in view and '"02" [selected]' not in view
    assert 'combobox "Country" [ref=e3]: options "Norway" [selected], "Sweden"' in view
    assert find_lines(hide_values(lines, frozenset({"e2"})), "selected", 10) == ()


def test_a_ref_written_into_a_name_and_a_quoted_key_are_not_mistaken() -> None:
    tricky: str = '- textbox "Note [ref=e3]" [ref=e9]: kept\n- \'textbox "Login: user" [ref=e3]\': hunter2\n'
    hidden: str = render_lines(hide_values(parse_snapshot(tricky), frozenset({"e3"})))
    assert "kept" in hidden and "hunter2" not in hidden


@pytest.mark.parametrize(("approvals", "hides"), [("supervised", True), ("ask", True), ("auto", True), ("yolo", False)])
def test_every_mode_but_yolo_hides_secret_values(approvals: ApprovalMode, hides: bool) -> None:
    assert hides_secrets(approvals) is hides


def test_the_selector_names_every_kind_of_secret_field() -> None:
    selector: str = secret_selector()
    assert 'input[type="password" i]' in selector
    for kind in get_args(SecretKind):
        assert f'[autocomplete~="{kind}" i]' in selector, kind


@dataclass
class _Locator:
    """A field as the page answers for it: still there or gone, with its marking, or unable to say."""

    present: bool
    marking: str | None

    async def count(self) -> int:
        return 1 if self.present else 0

    async def evaluate(self, expression: str, **options: float) -> object:
        if self.marking is None:
            raise PlaywrightError("Frame was detached")
        return self.marking


@dataclass
class _Page:
    """The one stub of these tests: a page whose every field answers as ``field`` says, and that counts its asks."""

    field: _Locator
    snapshot: str = '- textbox "Name" [ref=e5]: Ada\n'
    url: str = "https://example.com/login"
    asked: list[str] = field(default_factory=list)

    def locator(self, selector: str) -> _Locator:
        self.asked.append(selector)
        return self.field

    async def aria_snapshot(self, mode: str, boxes: bool, **options: float) -> str:
        return self.snapshot


async def test_a_field_that_cannot_be_asked_or_went_is_hidden_and_a_ref_of_another_shape_too() -> None:
    lines: tuple[SnapshotLine, ...] = parse_snapshot('- textbox "Name" [ref=e5]: Ada\n- textbox "Odd" [ref=x9]: v\n')
    unable: _Page = _Page(field=_Locator(present=True, marking=None))
    assert await secret_refs(unable, filled_fields(lines)) == frozenset({"e5", "x9"})  # type: ignore[arg-type]
    # A field taken away since the snapshot, in a frame that went or not, cannot answer for the value shown.
    gone: _Page = _Page(field=_Locator(present=False, marking=""))
    assert await secret_refs(gone, filled_fields(lines)) == frozenset({"e5", "x9"})  # type: ignore[arg-type]


async def test_an_ordinary_field_keeps_its_value_a_marked_one_does_not_and_yolo_asks_nothing() -> None:
    plain: _Page = _Page(field=_Locator(present=True, marking="name"))
    assert render_lines(await capture_lines(plain, 1000, True)) == '- textbox "Name" [ref=e5]: Ada'  # type: ignore[arg-type]
    marked: _Page = _Page(field=_Locator(present=True, marking="password current-password"))
    assert HIDDEN_VALUE in render_lines(await capture_lines(marked, 1000, True))  # type: ignore[arg-type]
    yolo: _Page = _Page(field=_Locator(present=True, marking=None))
    assert "Ada" in render_lines(await capture_lines(yolo, 1000, False))  # type: ignore[arg-type]
    assert yolo.asked == []
