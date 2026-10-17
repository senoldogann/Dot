"""The undo journal: what the file tools note before they change a file, and how ``dotx undo`` takes the changes of a
run back from those notes and from what is at each path now."""

import shutil
import stat
from pathlib import Path

import pytest

from dotagent.core.errors import UndoError
from dotagent.tools.files import _edit_blocking, atomic_write
from dotagent.undo.journal import JOURNAL_FILE, JournalTarget, backup_path, read_journal, run_journal
from dotagent.undo.restore import (
    RunChange,
    UndoStep,
    apply_undo,
    created_dirs,
    current_states,
    due_steps,
    forced_steps,
    plan_undo,
    remove_empty_dirs,
)

RUN_ID: str = "run-1"
CHILD_ID: str = "run-2"
MIB: int = 1024 * 1024


def _target(root: Path, run_id: str, max_file_bytes: int, max_run_bytes: int) -> JournalTarget:
    """The journal of ``run_id`` below the undo dir ``root``, with the given caps on the copies."""
    return JournalTarget(
        directory=run_journal(root, run_id),
        tool="write_file",
        max_file_bytes=max_file_bytes,
        max_run_bytes=max_run_bytes,
    )


def _changes(root: Path, run_ids: tuple[str, ...]) -> list[RunChange]:
    return [
        RunChange(run_id=run_id, entry=entry) for run_id in run_ids for entry in read_journal(run_journal(root, run_id))
    ]


def _plan(changes: list[RunChange]) -> list[UndoStep]:
    return plan_undo(changes, current_states(sorted({change.entry.path for change in changes})))


def test_a_changed_file_is_put_back_as_it_was_with_its_permission_bits(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    notes: Path = tmp_path / "notes.txt"
    notes.write_text("one\n", encoding="utf-8")
    notes.chmod(0o640)
    target: JournalTarget = _target(root, RUN_ID, MIB, MIB)
    _edit_blocking(notes, "one", "two", False, target)
    atomic_write(notes, "three\n", target)
    first, second = read_journal(run_journal(root, RUN_ID))
    assert (first.seq, first.existed, first.mode, first.backup, first.copied) == (0, True, 0o640, True, 4)
    # Only the run's first change to a path keeps a copy: it holds what was there before the run.
    assert (second.seq, second.backup, second.copied) == (1, False, 0)
    plan: list[UndoStep] = _plan(_changes(root, (RUN_ID,)))
    assert [(step.path, step.state, step.action, step.changes) for step in plan] == [
        (str(notes), "ready", "restore", 2)
    ]
    apply_undo(due_steps(plan), root)
    assert notes.read_text(encoding="utf-8") == "one\n"
    assert stat.S_IMODE(notes.stat().st_mode) == 0o640
    assert [step.state for step in _plan(_changes(root, (RUN_ID,)))] == ["as_before"]


def test_a_created_file_is_deleted_and_the_folders_made_for_it_are_removed(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    work: Path = tmp_path / "work"
    work.mkdir()
    made: Path = work / "a" / "b" / "new.txt"
    atomic_write(made, "new\n", _target(root, RUN_ID, MIB, MIB))
    changes: list[RunChange] = _changes(root, (RUN_ID,))
    assert changes[0].entry.created_dirs == (str(work / "a"), str(work / "a" / "b"))
    plan: list[UndoStep] = _plan(changes)
    assert [(step.state, step.action) for step in plan] == [("ready", "delete")]
    apply_undo(due_steps(plan), root)
    assert remove_empty_dirs(created_dirs(changes)) == [work / "a" / "b", work / "a"]
    assert list(work.iterdir()) == []


def test_a_file_changed_after_the_run_is_a_conflict_taken_back_only_with_force(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    notes: Path = tmp_path / "notes.txt"
    notes.write_text("one\n", encoding="utf-8")
    atomic_write(notes, "two\n", _target(root, RUN_ID, MIB, MIB))
    notes.write_text("three\n", encoding="utf-8")
    plan: list[UndoStep] = _plan(_changes(root, (RUN_ID,)))
    assert [(step.state, step.reason) for step in plan] == [
        ("conflict", "it changed after the run's last change to it")
    ]
    assert due_steps(plan) == []
    apply_undo(forced_steps(plan), root)
    assert notes.read_text(encoding="utf-8") == "one\n"


def test_a_file_deleted_after_the_run_is_a_conflict_that_force_writes_back(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    notes: Path = tmp_path / "sub" / "notes.txt"
    notes.parent.mkdir()
    notes.write_text("one\n", encoding="utf-8")
    atomic_write(notes, "two\n", _target(root, RUN_ID, MIB, MIB))
    shutil.rmtree(notes.parent)
    plan: list[UndoStep] = _plan(_changes(root, (RUN_ID,)))
    assert [(step.state, step.reason) for step in plan] == [
        ("conflict", "it was deleted after the run's last change to it")
    ]
    apply_undo(forced_steps(plan), root)
    assert notes.read_text(encoding="utf-8") == "one\n"


def test_a_file_past_the_caps_is_changed_without_a_copy_and_cannot_be_put_back(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    big, first, second, new = (tmp_path / name for name in ("big.txt", "first.txt", "second.txt", "new.txt"))
    big.write_text("12345", encoding="utf-8")
    first.write_text("1234", encoding="utf-8")
    second.write_text("1234", encoding="utf-8")
    target: JournalTarget = _target(root, RUN_ID, 4, 6)
    for path in (big, first, second, new):
        atomic_write(path, "changed", target)
    assert {path.read_text(encoding="utf-8") for path in (big, first, second, new)} == {"changed"}
    steps: dict[str, UndoStep] = {step.path: step for step in _plan(_changes(root, (RUN_ID,)))}
    assert (steps[str(big)].state, steps[str(big)].reason) == (
        "cannot",
        "no copy of it was kept: it is larger than [agent] undo_max_file_bytes (4 bytes)",
    )
    assert (steps[str(second)].state, steps[str(second)].reason) == (
        "cannot",
        "no copy of it was kept: the copies of the run would pass [agent] undo_max_run_bytes (6 bytes)",
    )
    assert (steps[str(first)].state, steps[str(first)].action) == ("ready", "restore")
    # A file the run created needs no copy to be taken back.
    assert (steps[str(new)].state, steps[str(new)].action) == ("ready", "delete")


def test_the_file_is_left_as_it_is_when_its_change_cannot_be_noted(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    root.mkdir()
    run_journal(root, RUN_ID).write_text("a file where the journal folder belongs", encoding="utf-8")
    notes: Path = tmp_path / "notes.txt"
    notes.write_text("one\n", encoding="utf-8")
    with pytest.raises(UndoError, match="the file was not changed"):
        atomic_write(notes, "two\n", _target(root, RUN_ID, MIB, MIB))
    assert notes.read_text(encoding="utf-8") == "one\n"


def test_a_journal_line_cut_short_is_left_out_and_the_next_change_is_noted_after_it(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    notes: Path = tmp_path / "notes.txt"
    notes.write_text("one\n", encoding="utf-8")
    target: JournalTarget = _target(root, RUN_ID, MIB, MIB)
    atomic_write(notes, "two\n", target)
    # The disk filled as the line of the next change was written, in the middle of a letter: that change was not made.
    with (run_journal(root, RUN_ID) / JOURNAL_FILE).open("ab") as journal:
        journal.write(b'{"seq": 1, "ts": 1.5, "tool": "write_file", "path": "/caf\xc3')
    assert [entry.seq for entry in read_journal(run_journal(root, RUN_ID))] == [0]
    atomic_write(notes, "three\n", target)
    first, second = read_journal(run_journal(root, RUN_ID))
    assert (first.seq, second.seq, second.backup) == (0, 1, False)
    apply_undo(due_steps(_plan(_changes(root, (RUN_ID,)))), root)
    assert notes.read_text(encoding="utf-8") == "one\n"


def test_the_changes_of_a_delegated_run_are_taken_back_to_the_copy_its_parent_kept(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    notes: Path = tmp_path / "notes.txt"
    notes.write_text("one\n", encoding="utf-8")
    atomic_write(notes, "two\n", _target(root, RUN_ID, MIB, MIB))
    atomic_write(notes, "three\n", _target(root, CHILD_ID, MIB, MIB))
    plan: list[UndoStep] = _plan(_changes(root, (RUN_ID, CHILD_ID)))
    assert [(step.state, step.origin.run_id, step.changes) for step in plan] == [("ready", RUN_ID, 2)]
    apply_undo(due_steps(plan), root)
    assert notes.read_text(encoding="utf-8") == "one\n"


def test_a_copy_that_is_not_the_one_noted_is_not_written_back(tmp_path: Path) -> None:
    root: Path = tmp_path / "undo"
    notes: Path = tmp_path / "notes.txt"
    notes.write_text("one\n", encoding="utf-8")
    atomic_write(notes, "two\n", _target(root, RUN_ID, MIB, MIB))
    backup_path(run_journal(root, RUN_ID), 0).write_bytes(b"forged\n")
    with pytest.raises(UndoError, match="a change could not be taken back"):
        apply_undo(due_steps(_plan(_changes(root, (RUN_ID,)))), root)
    assert notes.read_text(encoding="utf-8") == "two\n"


def test_a_link_that_came_above_a_changed_file_is_not_followed(tmp_path: Path) -> None:
    """Item 350: the tools note a path with its links resolved, so a folder above it that is a link now came since.
    Taking the change back through it would put the file back, or delete it, in the folder the link names; the step
    cannot be taken, even with force, and a link that comes after the plan was made is refused when it is carried
    out."""
    root: Path = tmp_path / "undo"
    work: Path = tmp_path / "work"
    elsewhere: Path = tmp_path / "elsewhere"
    work.mkdir()
    elsewhere.mkdir()
    made: Path = work / "project" / "config"
    atomic_write(made, "made by the run\n", _target(root, RUN_ID, MIB, MIB))
    # The folder is moved away and a link to another folder, holding a file of that name, put in its place.
    (work / "project").rename(tmp_path / "moved")
    (elsewhere / "config").write_text("made by the run\n", encoding="utf-8")
    (work / "project").symlink_to(elsewhere, target_is_directory=True)
    plan: list[UndoStep] = _plan(_changes(root, (RUN_ID,)))
    assert [(step.state, step.action) for step in plan] == [("cannot", "delete")]
    assert plan[0].reason is not None and "link" in plan[0].reason
    assert forced_steps(plan) == []
    # A plan made before the link came is refused when it is carried out.
    with pytest.raises(UndoError, match="link"):
        apply_undo([plan[0].model_copy(update={"state": "ready"})], root)
    assert (elsewhere / "config").read_text(encoding="utf-8") == "made by the run\n"


def test_a_folder_the_run_made_below_a_link_that_came_since_is_not_removed(tmp_path: Path) -> None:
    """Item 350: the empty folders a run made are removed when its changes are taken back, but not one that a link
    above it now puts in another folder."""
    root: Path = tmp_path / "undo"
    work: Path = tmp_path / "work"
    elsewhere: Path = tmp_path / "elsewhere"
    work.mkdir()
    (elsewhere / "b").mkdir(parents=True)
    atomic_write(work / "a" / "b" / "new.txt", "new\n", _target(root, RUN_ID, MIB, MIB))
    changes: list[RunChange] = _changes(root, (RUN_ID,))
    shutil.rmtree(work / "a")
    (work / "a").symlink_to(elsewhere, target_is_directory=True)
    assert remove_empty_dirs(created_dirs(changes)) == []
    assert (elsewhere / "b").is_dir()
