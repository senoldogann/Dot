"""The Changes of a chat (item 368): the files its runs changed, noted in their journals as write_file and edit_file
note them, each diffed from before the chat's first change to it; a file the panel does not show, a binary file, one
that is gone and one whose first change kept no copy are listed with why they show no diff; a secret Dot holds is
masked."""

from pathlib import Path

import pytest

from dotagent.server.changes import chat_changes
from dotagent.undo.journal import JournalTarget, note_change, run_journal

_KEY: str = "sk-test-0123456789abcdefghij"
# A private key in PEM, over several lines (not a real one).
_PEM: bytes = (
    b"-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n"
    b"-----END PRIVATE KEY-----\n"
)


def _write(root: Path, run_id: str, path: Path, data: bytes, max_file_bytes: int) -> None:
    """Change ``path`` to ``data`` as a run's write_file does: the change noted first, then made."""
    target = JournalTarget(
        directory=run_journal(root, run_id), tool="write_file", max_file_bytes=max_file_bytes, max_run_bytes=10_000_000
    )
    note_change(target, path, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def places(tmp_path: Path) -> tuple[Path, Path, Path]:
    """The undo dir, the home folder and the workspace of a throwaway Dot."""
    root, home = tmp_path / "undo", tmp_path / "home"
    workspace = home / "workspace"
    for folder in (root, home, workspace):
        folder.mkdir(parents=True)
    return root, home, workspace


def test_a_file_two_runs_changed_is_diffed_from_before_the_first_and_one_they_made_is_all_added(
    places: tuple[Path, Path, Path],
) -> None:
    root, home, workspace = places
    notes = workspace / "notes.md"
    notes.write_text("one\ntwo\nthree\n", encoding="utf-8")
    _write(root, "run-1", notes, b"one\n2\nthree\n", 1_000_000)
    _write(root, "run-2", notes, b"one\n2\nthree\nfour\n", 1_000_000)
    made = workspace / "app" / "main.py"
    _write(root, "run-2", made, b"print('hello')\n", 1_000_000)
    view = chat_changes(root, ["run-1", "run-2"], home, workspace, ())
    by_path = {file.path: file for file in view.files}
    changed = by_path[str(notes)]
    assert (changed.created, changed.gone, changed.added, changed.removed, changed.changes) == (False, False, 2, 1, 2)
    assert "-two\n" in changed.diff and "+2\n" in changed.diff and "+four\n" in changed.diff
    assert changed.diff.startswith("@@ -1,3 +1,4 @@\n")
    created = by_path[str(made)]
    assert (created.created, created.added, created.removed) == (True, 1, 0)
    assert (view.added, view.removed) == (3, 1)
    # The one changed last comes first.
    assert view.files[0].path == str(made)


def test_files_without_a_diff_say_why(places: tuple[Path, Path, Path]) -> None:
    root, home, workspace = places
    hidden = home / ".config" / "tool.toml"
    _write(root, "run-1", hidden, b"a = 1\n", 1_000_000)
    binary = workspace / "logo.bin"
    _write(root, "run-1", binary, b"\x89PNG\x00\xff", 1_000_000)
    gone = workspace / "draft.txt"
    _write(root, "run-1", gone, b"a draft\n", 1_000_000)
    gone.unlink()
    big = workspace / "big.txt"
    big.write_text("x" * 100, encoding="utf-8")
    # A first change that may copy nothing keeps no copy of what was there.
    _write(root, "run-1", big, b"y" * 100, 10)
    view = chat_changes(root, ["run-1"], home, workspace, ())
    notes = {Path(file.path).name: (file.note, file.diff, file.gone) for file in view.files}
    assert notes["tool.toml"] == ("no diff: the file is in a hidden folder", "", False)
    assert notes["logo.bin"] == ("a binary file", "", False)
    assert notes["draft.txt"] == ("the file is not there any more", "", True)
    assert notes["big.txt"][0] is not None and notes["big.txt"][0].startswith(
        "no copy of what was there before was kept"
    )


def test_a_secret_dot_holds_is_masked_in_the_diff(places: tuple[Path, Path, Path]) -> None:
    root, home, workspace = places
    config = workspace / "settings.env"
    _write(root, "run-1", config, f"OPENAI_API_KEY={_KEY}\n".encode(), 1_000_000)
    view = chat_changes(root, ["run-1"], home, workspace, (_KEY,))
    assert _KEY not in view.files[0].diff
    assert view.files[0].added == 1


def test_a_private_key_of_many_lines_is_masked_whatever_part_of_it_changed(places: tuple[Path, Path, Path]) -> None:
    root, home, workspace = places
    created = workspace / "deploy.pem"
    _write(root, "run-1", created, _PEM, 1_000_000)
    edited = workspace / "old.pem"
    edited.write_bytes(_PEM)
    _write(root, "run-1", edited, _PEM.replace(b"BKcwggSjAgEA", b"BKcwggSjAgEB"), 1_000_000)
    view = chat_changes(root, ["run-1"], home, workspace, ())
    by_name = {Path(file.path).name: file for file in view.files}
    for file in by_name.values():
        assert "MIIEvQIBADAN" not in file.diff and "BKcwggSjAgE" not in file.diff, file.diff
    # A key the run wrote is a masked line added; a change inside a key shows no line of it, and says why.
    assert by_name["deploy.pem"].added == 1
    assert (by_name["old.pem"].diff, by_name["old.pem"].note) == ("", "the change is inside a secret, which is masked")


def test_a_file_a_delegated_run_made_and_its_parent_edited_is_diffed_from_before_both(
    places: tuple[Path, Path, Path],
) -> None:
    root, home, workspace = places
    notes = workspace / "notes.md"
    # The delegated run makes the file, then the run that delegated it edits it; the page lists the parent first.
    _write(root, "child", notes, b"one\n", 1_000_000)
    _write(root, "parent", notes, b"one\ntwo\n", 1_000_000)
    [changed] = chat_changes(root, ["parent", "child"], home, workspace, ()).files
    assert (changed.created, changed.added, changed.removed, changed.changes) == (True, 2, 0, 2)


def test_a_file_that_cannot_be_read_says_so_and_the_others_keep_their_diffs(places: tuple[Path, Path, Path]) -> None:
    root, home, workspace = places
    kept = workspace / "kept.md"
    _write(root, "run-1", kept, b"kept\n", 1_000_000)
    locked = workspace / "locked.md"
    _write(root, "run-1", locked, b"locked\n", 1_000_000)
    locked.chmod(0)
    try:
        files = {Path(file.path).name: file for file in chat_changes(root, ["run-1"], home, workspace, ()).files}
    finally:
        locked.chmod(0o644)
    assert files["locked.md"].note is not None and files["locked.md"].note.startswith("cannot be read")
    assert files["kept.md"].added == 1


def test_a_chat_without_journals_has_no_changes(places: tuple[Path, Path, Path]) -> None:
    root, home, workspace = places
    view = chat_changes(root, ["run-without-changes"], home, workspace, ())
    assert (view.files, view.added, view.removed) == ((), 0, 0)
