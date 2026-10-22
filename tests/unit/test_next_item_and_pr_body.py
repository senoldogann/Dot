"""scripts/next_item.py and scripts/pr_body.py (item 540): the number a new item takes, from where numbers are held,
and a pull request's title and body from the item's roadmap entry."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS: Path = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


next_item = _load("next_item")
pr_body = _load("pr_body")


def test_the_next_item_is_one_more_than_the_highest_taken_on_main_in_a_pull_request_or_on_a_branch() -> None:
    files = ["roadmap/536.md", "changes/536.md", "roadmap/539.md", "changes/539.md"]
    assert next_item.next_item(files, [], []) == 540
    # A pull request and a pushed branch hold a number main does not have yet.
    assert next_item.next_item(files, ["Item 541: something", "Docs: a step stays on one line"], []) == 542
    assert next_item.next_item(files, [], ["item-543-faster-flow", "main", "item-12-x"]) == 544
    assert next_item.next_item([], [], []) == 1


def test_where_a_number_was_seen_is_told() -> None:
    found = next_item.taken(["roadmap/539.md"], ["Item 540: x"], ["item-541-y", "item-539-z"])
    assert found == {539: "main", 540: "pull request", 541: "branch"}


def test_a_number_with_a_leading_zero_or_in_another_place_is_no_item() -> None:
    assert next_item.taken(["docs/539.md", "roadmap/0539.md", "roadmap/README.md"], ["Items 12 and 13"], []) == {}


_ENTRY: str = """### 540. A change takes fewer pushes and reruns

**Status: done (2026-10-22).** Why.

**As built.**
- One thing.

Checked by a test.

Log:

- 2026-10-22: item 540 on `item-540-x`.
"""


def test_the_title_is_the_headings_with_its_first_letter_in_lower_case() -> None:
    assert pr_body.title(_ENTRY) == "Item 540: a change takes fewer pushes and reruns"
    assert pr_body.title("### 12. UI of the chat\n") == "Item 12: UI of the chat"


def test_the_body_is_the_entry_without_its_heading_and_its_log_and_ends_with_the_attribution() -> None:
    text: str = pr_body.body(_ENTRY)
    assert text.startswith("**Status: done (2026-10-22).** Why.")
    assert "Checked by a test." in text and "Log:" not in text and "item 540 on" not in text
    assert text.endswith("🤖 Generated with [Claude Code](https://claude.com/claude-code)\n")


def test_an_entry_without_its_heading_is_refused() -> None:
    with pytest.raises(pr_body.EntryError):
        pr_body.body("**Status: done.**\n")
