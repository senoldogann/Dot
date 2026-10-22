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
    files = ("main", ["roadmap/536.md", "changes/536.md", "roadmap/539.md", "changes/539.md"])
    assert next_item.next_item(files) == 540
    # A pull request and a pushed branch hold a number main does not have yet.
    assert (
        next_item.next_item(files, ("pull request", ["Item 541: something", "Docs: a step stays on one line"])) == 542
    )
    assert next_item.next_item(files, ("branch", ["item-543-faster-flow", "main", "item-12-x"])) == 544
    assert next_item.next_item() == 1


def test_where_a_number_was_seen_is_told() -> None:
    found = next_item.taken(
        ("main", ["roadmap/539.md"]), ("pull request", ["Item 540: x"]), ("branch", ["item-541-y", "item-539-z"])
    )
    assert found == {539: "main", 540: "pull request", 541: "branch"}


def test_a_batch_pull_request_holds_every_number_of_its_title_and_its_entries() -> None:
    """The review of item 541: a title of ``Items 541, 542 and 543: ...`` and the entries a pull request adds are read,
    so that a second session is not given 542."""
    found = next_item.taken(("pull request", ["Items 541, 542 and 543: a, b; c", "Items 12 and 13: older"]))
    assert found == {
        541: "pull request",
        542: "pull request",
        543: "pull request",
        12: "pull request",
        13: "pull request",
    }
    added = ("pull request", ["roadmap/541.md", "changes/542.md", "src/dotagent/x.py"])
    assert next_item.next_item(("main", ["roadmap/540.md"]), added) == 543


def test_a_number_with_a_leading_zero_or_in_another_place_is_no_item() -> None:
    texts = ["docs/539.md", "roadmap/0539.md", "roadmap/README.md", "Itemised 7", "Step 3 of 4"]
    assert next_item.taken(("anywhere", texts)) == {}


def _fake_sources(monkeypatch: pytest.MonkeyPatch, *, pulls: str = "[]", failing: str | None = None) -> None:
    """The commands the script runs, answered from here: main holds up to 540, a branch without a pull request 544."""

    def lines(*command: str) -> list[str]:
        if failing is not None and failing in command:
            raise next_item.SourceError(f"`{' '.join(command)}` failed: no network")
        if command[:2] == ("git", "fetch"):
            return []
        if command[:2] == ("git", "ls-tree"):
            return ["roadmap/539.md", "changes/539.md", "roadmap/540.md"]
        if command[:2] == ("git", "ls-remote"):
            return ["abc\trefs/heads/main", "def\trefs/heads/item-544-ahead", "123\trefs/heads/item-12-old"]
        if command[:2] == ("git", "diff"):
            assert command[3] == "origin/main...origin/item-544-ahead"
            return ["roadmap/544.md", "roadmap/545.md"]
        assert command[:3] == ("gh", "pr", "list")
        return [pulls]

    monkeypatch.setattr(next_item, "_lines", lines)


def test_the_script_gives_the_numbers_of_a_batch_after_every_place_it_looked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pulls: str = (
        '[{"title": "Items 541 and 542: a; b", "headRefName": "item-541-batch", "files": [{"path": "roadmap/542.md"}]}]'
    )
    _fake_sources(monkeypatch, pulls=pulls)
    assert next_item.main(["3"]) == 0
    printed = capsys.readouterr()
    # 545 is the highest taken: an entry a branch ahead of main adds, which its name does not say.
    assert printed.out == "546 547 548\n" and "545 is the highest taken, in branch" in printed.err
    assert next_item.main([]) == 0 and capsys.readouterr().out == "546\n"


def test_the_script_refuses_a_count_that_is_no_number_and_gives_none_when_a_source_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert next_item.main(["0"]) == 2 and next_item.main(["two"]) == 2 and next_item.main(["1", "2"]) == 2
    capsys.readouterr()
    _fake_sources(monkeypatch, failing="gh")
    assert next_item.main(["2"]) == 1
    printed = capsys.readouterr()
    assert printed.out == "" and "no number is given" in printed.err


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


_SECOND: str = """### 541. Pull requests carry batches

**Status: done (2026-10-22).** Why.

Log:

- a log line.
"""


def test_a_batch_has_all_its_numbers_and_headings_in_its_title() -> None:
    assert pr_body.batch_title([_ENTRY]) == "Item 540: a change takes fewer pushes and reruns"
    assert pr_body.batch_title([_ENTRY, _SECOND]) == (
        "Items 540 and 541: a change takes fewer pushes and reruns; pull requests carry batches"
    )
    third: str = "### 542. UI of the chat\n"
    assert pr_body.batch_title([_ENTRY, _SECOND, third]).startswith("Items 540, 541 and 542: ")
    assert pr_body.batch_title([_ENTRY, _SECOND, third]).endswith("; UI of the chat")


def test_a_batch_has_a_section_for_each_item_and_one_attribution() -> None:
    text: str = pr_body.batch_body([_ENTRY, _SECOND])
    assert text.index("## Item 540: a change takes fewer pushes and reruns") < text.index(
        "## Item 541: pull requests carry batches"
    )
    assert "Checked by a test." in text and "Log:" not in text and "a log line" not in text
    assert text.count("Generated with [Claude Code]") == 1 and text.endswith("\n")
    assert pr_body.batch_body([_ENTRY]) == pr_body.body(_ENTRY)
