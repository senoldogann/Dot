"""What the daemon opens where a run's commands write too (item 681) is opened along the path that was checked, with no
link followed on the way: a link a command put in a folder's place since does not have the daemon read or write
elsewhere, a named pipe is refused at once rather than holding a thread for good, and the browser is given copies of
what was checked, which a link made after cannot change."""

import os
import time
from pathlib import Path

import pytest

from dotagent.agent.attachments import store_attachment
from dotagent.core.errors import ToolExecutionError, ToolInputError, UndoError
from dotagent.core.held import read_regular
from dotagent.tools.browser import upload_copies
from dotagent.tools.files import _edit_blocking, _read_blocking, _walk, atomic_write
from dotagent.tools.pages import page_file
from dotagent.undo.journal import JournalTarget


def _rooms(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace with a folder ``notes`` holding ``a.txt``, and a folder the run may not touch holding ``key``."""
    root: Path = Path(os.path.realpath(tmp_path))
    workspace: Path = root / "workspace"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "notes" / "a.txt").write_text("mine\n", encoding="utf-8")
    secret: Path = root / "secret"
    secret.mkdir()
    (secret / "a.txt").write_text("secret\n", encoding="utf-8")
    return workspace, secret


def _swap(folder: Path, to: Path) -> None:
    """Put a link to ``to`` in the place of ``folder``, as a command of the run would."""
    folder.rename(folder.with_name(folder.name + "-moved"))
    folder.symlink_to(to)


def _journal(tmp_path: Path) -> JournalTarget:
    return JournalTarget(
        directory=tmp_path / "journal", tool="write_file", max_file_bytes=1 << 20, max_run_bytes=1 << 22
    )


def test_a_file_tool_follows_no_link_put_on_the_path_it_checked(tmp_path: Path) -> None:
    workspace, secret = _rooms(tmp_path)
    checked: Path = workspace / "notes" / "a.txt"
    assert "mine" in _read_blocking(checked, 1, 100, 10_000, 512, 80).text
    _swap(workspace / "notes", secret)
    with pytest.raises(ToolExecutionError, match="is a link now"):
        _read_blocking(checked, 1, 100, 10_000, 512, 80)
    # The journal's copy of what is there is read along the path first, and refuses the write.
    with pytest.raises(UndoError, match="Not a directory"):
        atomic_write(checked, "planted\n", _journal(tmp_path))
    with pytest.raises(ToolExecutionError):
        _edit_blocking(checked, "secret", "planted", False, _journal(tmp_path), workspace)
    with pytest.raises(ToolExecutionError, match="is a link now"):
        atomic_write(workspace / "notes" / "new.txt", "planted\n", _journal(tmp_path))
    assert sorted(path.name for path in secret.iterdir()) == ["a.txt"]
    assert (secret / "a.txt").read_text(encoding="utf-8") == "secret\n"
    with pytest.raises(OSError):
        read_regular(checked)


def test_a_listing_follows_no_link_put_on_the_path_it_checked(tmp_path: Path) -> None:
    """The review of item 681's eighth round: list_dir lists the folder it was allowed, and a link inside is said to be
    one with where it leads, never entered, so a folder swapped for a link to another does not show that one's names."""
    workspace, secret = _rooms(tmp_path)
    (workspace / "notes" / "inner").mkdir()
    (workspace / "notes" / "inner" / "b.txt").write_text("b\n", encoding="utf-8")
    (workspace / "notes" / "out").symlink_to(secret)
    checked: Path = workspace / "notes"
    assert _walk(checked, 2) == ["inner/", "  b.txt  (2 B)", "a.txt  (5 B)", f"out -> {secret}  (link)"]
    _swap(workspace / "notes", secret)
    with pytest.raises(ToolExecutionError, match="is a link now"):
        _walk(checked, 2)
    with pytest.raises(ToolExecutionError, match="not a directory"):
        _walk(workspace / "notes-moved" / "a.txt", 1)
    with pytest.raises(ToolExecutionError, match="does not exist"):
        _walk(workspace / "gone", 1)


def test_a_named_pipe_is_refused_at_once(tmp_path: Path) -> None:
    workspace, _ = _rooms(tmp_path)
    pipe: Path = workspace / "notes" / "pipe"
    os.mkfifo(pipe)
    started: float = time.monotonic()
    with pytest.raises(ToolExecutionError, match="not a file Dot reads"):
        _read_blocking(pipe, 1, 100, 10_000, 512, 80)
    assert time.monotonic() - started < 2.0


def test_the_daemons_own_files_in_the_workspace_are_made_with_no_link_followed(tmp_path: Path) -> None:
    workspace, secret = _rooms(tmp_path)
    (workspace / "attachments").symlink_to(secret)
    with pytest.raises(OSError):
        store_attachment(workspace, "notes.txt", "text/plain", b"two lines\n")
    assert sorted(path.name for path in secret.iterdir()) == ["a.txt"]
    (workspace / "attachments").unlink()
    stored = store_attachment(workspace, "notes.txt", "text/plain", b"two lines\n")
    assert Path(stored.path).read_bytes() == b"two lines\n"


def test_the_browser_is_given_copies_of_what_was_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    workspace, secret = _rooms(tmp_path)
    checked: Path = workspace / "notes" / "a.txt"
    old: Path = tmp_path / "home" / "uploads" / "old"
    old.mkdir(parents=True)
    os.utime(old, (time.time() - 2 * 86400, time.time() - 2 * 86400))
    copies: list[str] = upload_copies([checked, checked], time.time())
    assert [Path(copy).read_text(encoding="utf-8") for copy in copies] == ["mine\n", "mine\n"]
    assert not old.exists() and all(not copy.startswith(str(workspace)) for copy in copies)
    _swap(workspace / "notes", secret)
    with pytest.raises(OSError):
        upload_copies([checked], time.time())


def test_a_page_shown_by_its_path_is_read_with_no_link_followed(tmp_path: Path) -> None:
    """The fifth code review of item 681: ``show_page`` read the page by its name, following a link a command put on
    its path after it was judged."""
    workspace, secret = _rooms(tmp_path)
    page: Path = workspace / "notes" / "page.html"
    page.write_text("<p>mine</p>", encoding="utf-8")
    (secret / "page.html").write_text("<p>secret</p>", encoding="utf-8")
    assert page_file(page, workspace) == "<p>mine</p>"
    _swap(workspace / "notes", secret)
    with pytest.raises(ToolInputError, match="there is no page"):
        page_file(page, workspace)
