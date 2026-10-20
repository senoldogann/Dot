"""The recording of a task the owner shows Dot (item 423): each step as the line the owner reads and the skill writer
is shown, and a secret field's value out of it, never named either."""

import json
from pathlib import Path

import pytest

from dotagent import teach as teach_module
from dotagent.agent.playbooks import list_playbooks, retires
from dotagent.browser.teach import Recording, TaughtStep, taught_step
from dotagent.teach import TaughtSkill, save_taught_skill


def _reported(**step: object) -> str:
    return json.dumps(step)


@pytest.mark.parametrize(
    ("reported", "kind", "line"),
    [
        ({"kind": "page", "label": "https://example.com/pricing"}, "page", "Opened https://example.com/pricing"),
        ({"kind": "click", "label": "Sign in"}, "click", 'Clicked "Sign in"'),
        ({"kind": "fill", "label": "Email"}, "fill", 'Filled "Email"'),
        ({"kind": "choice", "label": "Plan"}, "choice", 'Chose "Plan"'),
        ({"kind": "enter", "label": "Search"}, "enter", 'Pressed Enter in "Search"'),
    ],
)
def test_a_step_is_the_line_the_owner_reads(reported: dict[str, object], kind: str, line: str) -> None:
    step: TaughtStep | None = taught_step(_reported(**reported), now=1.0)
    assert step is not None
    assert (step.kind, step.line, step.at) == (kind, line, 1.0)


def test_a_page_is_named_by_where_it_is_and_not_by_what_follows_its_address() -> None:
    """Item 423: a form's words, an OAuth code and a token in a magic link are in the address a page opens at, and a
    recording must not carry them into the skill or to the review model that writes it."""
    for label, line in (
        ("https://example.com/cb?code=abc&state=x", "Opened https://example.com/cb"),
        ("https://example.com/in?q=my+words#access_token=yz", "Opened https://example.com/in"),
        # A reset or share link carries its token in the path, and a part that long is not a word.
        ("https://example.com/reset/9f8e7d6c5b4a3210", "Opened https://example.com/reset/…"),
        ("https://example.com/s/AbCdEfGhIjKlMnOpQr", "Opened https://example.com/s/…"),
        # A word of a path stays: the step still says which page of the site it was.
        ("https://example.com/settings/billing", "Opened https://example.com/settings/billing"),
    ):
        step: TaughtStep | None = taught_step(_reported(kind="page", label=label))
        assert step is not None
        assert step.line == line, (label, step.line)


def test_a_secret_field_is_recorded_without_its_value_or_its_name() -> None:
    reported: dict[str, object] = {"kind": "fill", "label": "Password", "secret": True}
    step: TaughtStep | None = taught_step(_reported(**reported))
    assert step is not None
    assert (step.label, step.secret, step.line) == ("", True, "Filled a secret field")
    # Enter in a secret field is a secret step too: the name of the field does not reach the recording.
    pressed: TaughtStep | None = taught_step(_reported(kind="enter", label="Password", secret=True))
    assert pressed is not None
    assert (pressed.label, pressed.secret, pressed.line) == ("", True, "Pressed Enter in a secret field")
    # A value the page let through is dropped with the name: a secret field is recorded as one, nothing of it kept.
    leaked: TaughtStep | None = taught_step(_reported(kind="fill", label="hunter2", secret=True))
    assert leaked is not None and leaked.label == ""
    assert leaked.line == "Filled a secret field"


def test_what_the_recorder_reports_that_is_not_a_step_is_left_out() -> None:
    nothing: list[str] = [
        "not json",
        _reported(kind="nonsense", label="x"),
        _reported(kind="click", label=""),
        _reported(kind="fill"),
        json.dumps(["a", "list"]),
    ]
    assert [taught_step(payload) for payload in nothing] == [None] * len(nothing)
    # A page opened is a step even with nothing in its address.
    assert taught_step(_reported(kind="page")) is not None


def test_a_recording_takes_the_steps_of_its_own_page_only() -> None:
    page, elsewhere = object(), object()
    recording: Recording = Recording(thread_id="t", page=page, tab_index=0)
    recording.take({"page": page}, _reported(kind="click", label="Sign in"))
    recording.take({"page": elsewhere}, _reported(kind="click", label="Another tab"))
    recording.take({"page": page}, "not json")
    assert [step.line for step in recording.steps] == ['Clicked "Sign in"']
    assert recording.digest() == '1. Clicked "Sign in"'


def test_a_taught_skill_is_written_under_its_own_header_and_is_never_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 423: the folder says the owner taught it, stands behind no run, and is never retired for failures: whether
    it still fits is theirs to say."""
    monkeypatch.setattr(teach_module, "playbooks_dir", lambda: tmp_path)
    skill = TaughtSkill(
        name="taught-invoices",
        kind="invoices",
        host="example.com",
        description="Pay the invoices of the month.",
        when="At the start of a month, with the invoices page open.",
        steps=("Open https://example.com/invoices.", "Download each invoice as a PDF."),
        parameters=(),
        shown=("Opened https://example.com/invoices", 'Clicked "Download"'),
    )
    assert save_taught_skill(skill, written_at=1.7e9) == "taught-invoices"
    text = (tmp_path / "taught-invoices" / "SKILL.md").read_text(encoding="utf-8")
    assert "Taught by the owner on 2023-11-14" in text
    assert "Learned by Dot" not in text
    entries = list_playbooks(tmp_path)
    assert [entry.meta.name for entry in entries] == ["taught-invoices"]
    assert (entries[0].meta.taught, entries[0].meta.tainted) == (True, False)
    # Two failures in a row retire a learned skill; a taught one is the owner's to keep.
    failed = entries[0].meta.model_copy(update={"failing": 99})
    assert retires(failed) is False


def test_the_page_a_recording_starts_on_is_its_first_step_once() -> None:
    """Item 423: the tab is usually already on a page the script has nothing new to say about, and the skill needs the
    page it begins from; a step the page reports itself is not taken twice."""
    page = object()
    recording: Recording = Recording(thread_id="t", page=page, tab_index=0)
    recording.opened("https://example.com/invoices")
    recording.opened("https://example.com/invoices")
    recording.take({"page": page}, _reported(kind="page", label="https://example.com/invoices"))
    assert [step.line for step in recording.steps] == ["Opened https://example.com/invoices"]
