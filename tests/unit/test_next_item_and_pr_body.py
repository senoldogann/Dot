"""scripts/next_item.py and scripts/pr_body.py (item 540): the number a new item takes, from where numbers are held,
and a pull request's title and body from the item's roadmap entry."""

import importlib.util
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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


@dataclass(frozen=True)
class _Places:
    """What the commands the script runs answer: main holds up to 540, and nothing else holds a number unless set."""

    worktree: Path
    common: Path
    open_pulls: str = "[]"
    closed_pulls: str = "[]"
    branches: tuple[str, ...] = ("main", "origin/main")
    subjects: tuple[str, ...] = ()
    entries: tuple[str, ...] = ()
    failing: str | None = None


def _fake_sources(monkeypatch: pytest.MonkeyPatch, places: _Places) -> None:
    def lines(*command: str) -> list[str]:
        if places.failing is not None and places.failing in command:
            raise next_item.SourceError(f"`{' '.join(command)}` failed: no network")
        if command[:2] == ("git", "fetch"):
            return []
        if command[:2] == ("git", "rev-parse"):
            return [str(places.common)] if "--git-common-dir" in command else ["fix/here"]
        if command[:2] == ("git", "ls-tree"):
            return ["roadmap/539.md", "changes/539.md", "roadmap/540.md"]
        if command[:2] == ("git", "worktree"):
            return [
                f"worktree {places.worktree}",
                "HEAD abc123",
                "detached",
                "",
                "worktree /gone",
                "HEAD def",
                "prunable",
            ]
        if command[:2] == ("git", "for-each-ref"):
            return list(places.branches)
        if command[:2] == ("git", "log"):
            assert "--branches" in command and "--remotes=origin" in command and "abc123" in command
            return list(places.subjects if "--format=%s" in command else places.entries)
        assert command[:3] == ("gh", "pr", "list")
        return [places.open_pulls if "open" in command else places.closed_pulls]

    monkeypatch.setattr(next_item, "_lines", lines)


def _places(tmp_path: Path) -> _Places:
    (tmp_path / "worktree" / "roadmap").mkdir(parents=True)
    (tmp_path / "common").mkdir()
    return _Places(worktree=tmp_path / "worktree", common=tmp_path / "common")


def test_the_script_gives_the_numbers_of_a_batch_after_every_place_it_looked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    pulls: str = (
        '[{"title": "Items 541 and 542: a; b", "body": "", "headRefName": "item-541-batch",'
        ' "files": [{"path": "roadmap/542.md"}]}]'
    )
    places: _Places = replace(_places(tmp_path), open_pulls=pulls, entries=("roadmap/544.md", "roadmap/545.md"))
    _fake_sources(monkeypatch, places)
    assert next_item.main(["3"]) == 0
    printed = capsys.readouterr()
    # 545 is the highest taken: an entry a branch ahead of main adds, which its name does not say.
    assert printed.out == "546 547 548\n" and "545 is the highest taken, in branch" in printed.err
    # The second call sees the first one's reservations.
    assert next_item.main([]) == 0 and capsys.readouterr().out == "549\n"
    held = json.loads((places.common / "item-reservations.json").read_text(encoding="utf-8"))
    assert [row["number"] for row in held] == [546, 547, 548, 549] and {row["branch"] for row in held} == {"fix/here"}


_CLOSED: str = '[{"title": "Item 560: closed, may reopen", "body": "", "headRefName": "x"}]'
_IN_BODY: str = '[{"title": "Batch", "body": "## Item 560: a", "headRefName": "x"}]'


@pytest.mark.parametrize(
    ("change", "holder"),
    [
        (lambda places: replace(places, subjects=("Item 560: only in a local branch's commit",)), "branch"),
        (lambda places: replace(places, branches=("main", "item-560-unpushed")), "branch"),
        (lambda places: replace(places, closed_pulls=_CLOSED), "pull request"),
        (lambda places: replace(places, closed_pulls=_IN_BODY), "pull request"),
    ],
)
def test_a_number_held_only_by_a_local_branch_or_a_pull_request_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    change: Callable[[_Places], _Places],
    holder: str,
) -> None:
    _fake_sources(monkeypatch, change(_places(tmp_path)))
    assert next_item.main([]) == 0
    printed = capsys.readouterr()
    assert printed.out == "561\n" and f"560 is the highest taken, in {holder}" in printed.err


def test_an_entry_not_yet_committed_in_another_worktree_is_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    places: _Places = _places(tmp_path)
    (places.worktree / "roadmap" / "570.md").write_text("### 570. x\n", encoding="utf-8")
    _fake_sources(monkeypatch, places)
    assert next_item.main([]) == 0 and capsys.readouterr().out == "571\n"


def test_the_script_refuses_a_count_that_is_no_number_and_gives_none_when_a_source_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert next_item.main(["0"]) == 2 and next_item.main(["two"]) == 2 and next_item.main(["1", "2"]) == 2
    capsys.readouterr()
    places: _Places = replace(_places(tmp_path), failing="gh")
    _fake_sources(monkeypatch, places)
    assert next_item.main(["2"]) == 1
    printed = capsys.readouterr()
    assert printed.out == "" and "no number is given" in printed.err
    assert not (places.common / "item-reservations.json").exists()


def test_a_broken_reservations_file_gives_no_number(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    places: _Places = _places(tmp_path)
    broken: str = '[{"number": "1890", "branch": "x", "time": "2026-11-25T00:00:00+00:00"}]'
    (places.common / "item-reservations.json").write_text(broken, encoding="utf-8")
    _fake_sources(monkeypatch, places)
    assert next_item.main([]) == 1
    printed = capsys.readouterr()
    assert printed.out == "" and "wrong shape" in printed.err and "no number is given" in printed.err


def test_concurrent_callers_never_get_the_same_number(tmp_path: Path) -> None:
    now: datetime = datetime(2026, 11, 25, tzinfo=UTC)

    def take(caller: int) -> list[int]:
        given = next_item.reserve(tmp_path, {540: "main"}, 2, f"branch-{caller}", now)
        return [held.number for held in given]

    with ThreadPoolExecutor(max_workers=16) as pool:
        numbers: list[int] = [number for batch in pool.map(take, range(32)) for number in batch]
    assert sorted(numbers) == list(range(541, 541 + 64))


def test_a_reservation_holds_its_number_for_three_days() -> None:
    now: datetime = datetime(2026, 11, 25, 12, tzinfo=UTC)
    old = next_item.Reservation(600, "a", (now - timedelta(days=3)).isoformat())
    fresh = next_item.Reservation(590, "b", (now - timedelta(days=2, hours=23)).isoformat())
    kept = next_item.live([old, fresh], now, next_item.RESERVATION_DAYS)
    assert kept == [fresh]
    assert [held.number for held in next_item.choose({550: "main"}, kept, 2, "c", now)] == [591, 592]


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


def test_the_body_is_the_entry_without_its_heading_and_its_log_and_no_line_of_the_tool() -> None:
    text: str = pr_body.body(_ENTRY)
    assert text.startswith("**Status: done (2026-10-22).** Why.")
    assert "Checked by a test." in text and "Log:" not in text and "item 540 on" not in text
    assert text.endswith("Checked by a test.\n") and "Claude" not in text


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


def test_a_batch_has_a_section_for_each_item_and_no_line_of_the_tool() -> None:
    text: str = pr_body.batch_body([_ENTRY, _SECOND])
    assert text.index("## Item 540: a change takes fewer pushes and reruns") < text.index(
        "## Item 541: pull requests carry batches"
    )
    assert "Checked by a test." in text and "Log:" not in text and "a log line" not in text
    assert "Claude" not in text and text.endswith("\n")
    assert pr_body.batch_body([_ENTRY]) == pr_body.body(_ENTRY)
