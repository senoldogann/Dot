"""A path is classified by the place it names, not by its letters (item 399): macOS joins the Data volume to the system
volume with firmlinks, so ``/System/Volumes/Data/private/…`` is ``/private/…`` and ``/System/Volumes/Data/Users/…`` is
``/Users/…``, which ``realpath`` does not see; spelled so, a protected file read as a system one, with no question."""

import os
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from dotagent.policy.engine import decide
from dotagent.policy.paths import classify_path, expand_path, expand_protected, kernel_path
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
