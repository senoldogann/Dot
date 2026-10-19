"""A path is classified by the place it names, not by its letters (item 399): macOS joins the Data volume to the system
volume with firmlinks, so ``/System/Volumes/Data/private/…`` is ``/private/…`` and ``/System/Volumes/Data/Users/…`` is
``/Users/…``, which ``realpath`` does not see; spelled so, a protected file read as a system one, with no question."""

import errno
import os
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest

from dotagent.policy.engine import decide
from dotagent.policy.paths import classify_path, expand_path, expand_protected, kernel_path, mentions_protected
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import ActionInfo, Decision
from dotagent.tools.files import read_action, write_action

_DATA_VOLUME: str = "/System/Volumes/Data"
# How long naming a pipe may take: at once, as it is never opened.
_PIPE_WAIT_S: float = 5.0


def test_a_path_through_the_data_volume_or_in_another_case_is_the_place_it_names(tmp_path: Path) -> None:
    secret: Path = tmp_path / "secret"
    secret.mkdir()
    (secret / "key").write_text("not for a model", encoding="utf-8")
    own: Path = tmp_path / "own"
    own.mkdir()
    workspace: Path = tmp_path / "work"
    workspace.mkdir()
    protected: tuple[Path, ...] = expand_protected([str(secret)], ())
    dot_paths: tuple[Path, ...] = expand_protected([], (own,))
    real: Path = Path(os.path.realpath(tmp_path))
    plain: Path = expand_path(str(secret / "key"), workspace)
    through_data: Path = expand_path(f"{_DATA_VOLUME}{real / 'secret' / 'key'}", workspace)
    shouted: Path = expand_path(str(real / "secret" / "key").upper(), workspace)
    assert through_data == plain
    # Behind the prefixes that ask the kernel to follow no link, or to resolve as its flags say, it is the same place.
    for prefix in ("/.nofollow", "/.resolve/1"):
        assert expand_path(f"{prefix}{real / 'secret' / 'key'}", workspace) == plain, prefix
    # The folders as the kernel names them; a file keeps the letters it was written in, which a comparison of places
    # reads letter case aside (``within``).
    assert shouted.parent == plain.parent and shouted.name.casefold() == plain.name.casefold()
    assert classify_path(shouted, workspace, protected, dot_paths) == "protected"
    assert classify_path(through_data, workspace, protected, dot_paths) == "protected"
    # A file that is not there yet keeps its name after the place the kernel names for its folder.
    new: Path = expand_path(f"{_DATA_VOLUME}{real / 'own' / 'new.py'}", workspace)
    assert new == kernel_path(Path(os.path.realpath(own))) / "new.py"
    assert classify_path(new, workspace, protected, dot_paths) == "dot"
    # So the policy refuses what it refuses for the plain spelling.
    reading: ActionInfo = read_action("read_file", through_data, workspace, protected, dot_paths, "Read")
    assert reading.flags == ("secret_access",)
    refused: Decision = decide(reading, "task", False, "aggressive", [], None)
    assert (refused.verdict, refused.source) == ("deny", "auto_review")
    assert write_action("write_file", new, workspace, protected, dot_paths, "Write").flags != ()


def test_a_place_that_is_not_there_is_named_as_written(tmp_path: Path) -> None:
    gone: Path = Path(os.path.realpath(tmp_path)) / "missing" / "deeper" / "file.txt"
    assert kernel_path(gone) == kernel_path(Path(os.path.realpath(tmp_path))) / "missing" / "deeper" / "file.txt"


def test_a_pipe_is_named_at_once_and_a_closed_folder_through_the_folder_it_is_in(tmp_path: Path) -> None:
    real: Path = Path(os.path.realpath(tmp_path))
    pipe: Path = real / "pipe"
    os.mkfifo(pipe)
    # Opening a pipe nobody writes to waits for a writer; only folders are opened, so the pipe is named at once.
    with ThreadPoolExecutor(max_workers=1) as pool:
        naming: Future[Path] = pool.submit(kernel_path, Path(f"{_DATA_VOLUME}{pipe}"))
        try:
            named: Path = naming.result(timeout=_PIPE_WAIT_S)
        finally:
            # A writer ends an open that waits, so that a failing test does not hang.
            if not naming.done():
                os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
    assert named == kernel_path(real) / "pipe"
    closed: Path = real / "closed"
    closed.mkdir()
    closed.chmod(0o311)
    try:
        inside: Path = kernel_path(Path(f"{_DATA_VOLUME}{closed / 'key'}"))
    finally:
        closed.chmod(0o700)
    assert inside == kernel_path(real) / "closed" / "key"


def test_an_open_that_fails_for_another_reason_is_raised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def exhausted(path: object, flags: int) -> int:
        raise OSError(errno.EMFILE, "Too many open files", str(path))

    monkeypatch.setattr(os, "open", exhausted)
    with pytest.raises(OSError) as raised:
        kernel_path(tmp_path)
    assert raised.value.errno == errno.EMFILE


def test_a_file_named_by_its_number_is_protected(tmp_path: Path) -> None:
    # ``/.vol/<device>/<inode>`` names any file by its number: a folder so named is the place it is, a file cannot be
    # named, and neither can a step back from a folder so named, which realpath takes away by its letters alone.
    real: Path = Path(os.path.realpath(tmp_path))
    folder: Path = real / "folder"
    folder.mkdir()
    note: Path = folder / "note.txt"
    note.write_text("by number", encoding="utf-8")
    device: int = note.stat().st_dev
    workspace: Path = real / "work"
    by_folder: Path = expand_path(f"/.vol/{device}/{folder.stat().st_ino}/note.txt", workspace)
    assert by_folder == kernel_path(folder) / "note.txt"
    for spelled in (
        f"/.vol/{device}/{note.stat().st_ino}",
        f"/.vol/{device}/{folder.stat().st_ino}/../folder/note.txt",
    ):
        path: Path = expand_path(spelled, workspace)
        assert classify_path(path, workspace, (), ()) == "protected", path
        assert read_action("read_file", path, workspace, (), (), "Read").flags == ("secret_access",)


def test_the_shell_guard_sees_a_protected_location_through_a_link_a_glob_or_a_number(tmp_path: Path) -> None:
    real: Path = Path(os.path.realpath(tmp_path))
    secret: Path = real / "secret"
    secret.mkdir()
    key: Path = secret / "key"
    key.write_text("not for a model", encoding="utf-8")
    workspace: Path = real / "work"
    workspace.mkdir()
    protected: tuple[Path, ...] = expand_protected([str(secret)], ())
    # Quotes split the location's letters, so that no text of it is in the line; a glob keeps the first of each part.
    split: str = f"{secret.parent}/se'c'ret"
    globbed: str = "".join(f"/{part[0]}*" for part in secret.parts[1:])
    # The temporary folders are under /private, which the links /var and /tmp at the root lead into.
    assert secret.parts[1] == "private"
    linked: str = "/" + "/".join(secret.parts[2:])
    linked_glob: str = "".join(f"/{part[0]}*" for part in secret.parts[2:])
    device: int = key.stat().st_dev
    number: int = key.stat().st_ino
    for line in (
        f"ln -s / x; cat x/System/Volumes/Data{split}/key",
        f"cat /S*/V*/D*{globbed}/key",
        f"cat /.nofollow{globbed}/key",
        f"cat /.resolve/1{globbed}/key",
        f"cat {linked_glob}/key",
        f"python3 -c \"print(open('{linked}/key').read())\"",
        f"cat /.vol/{device}/{number}",
        f"python3 -c \"print(open('/.vol/{device}/{number}').read())\"",
        f"cat /.v*/{device}/{number}",
    ):
        assessment: ShellAssessment = assess_shell(line, workspace, workspace, protected, ())
        assert "secret_access" in assessment.flags, line
    # A hidden folder whose name begins as the namespace's does is no number.
    assert "secret_access" not in assess_shell(f"ls {real}/.volta/bin", workspace, workspace, protected, ()).flags


def test_a_home_written_in_another_case_still_gives_its_tilde_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The protected locations are named as the kernel names them, so a $HOME in other letters is compared letter
    # case aside when the ``~`` spellings of a location are made.
    real: Path = Path(os.path.realpath(tmp_path))
    (real / "secret").mkdir()
    monkeypatch.setenv("HOME", str(real).swapcase())
    protected: tuple[Path, ...] = expand_protected(["~/secret"], ())
    assert mentions_protected("cat ~/secret/key", protected)
