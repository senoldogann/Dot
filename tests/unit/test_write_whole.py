"""A file written whole is the old one or the new one: a failure while the new bytes are written leaves the original
and no temporary file, and the new bytes are synced to disk before the name moves."""

import os
import stat
from pathlib import Path

import pytest

from dotagent.core.files import write_whole


def test_a_failure_mid_write_leaves_the_original_and_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that stops after a few bytes never replaces the file, and the temporary beside it is removed."""
    path: Path = tmp_path / "config.toml"
    path.write_text("kept whole\n", encoding="utf-8")
    kept_mode: int = path.stat().st_mode
    real_fdopen = os.fdopen

    def fdopen(fd: int, mode: str = "r", *args: object, **kwargs: object) -> object:
        handle = real_fdopen(fd, mode, *args, **kwargs)
        real_write = handle.write

        def write(text: str) -> int:
            real_write(text[:4])
            raise OSError("failed mid-write")

        handle.write = write  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(os, "fdopen", fdopen)
    with pytest.raises(OSError, match="failed mid-write"):
        write_whole(path, "replacement that must not land")
    assert path.read_text(encoding="utf-8") == "kept whole\n"
    assert path.stat().st_mode == kept_mode
    assert list(tmp_path.glob(".config.toml.*.tmp")) == []


def test_a_failure_while_syncing_the_new_bytes_leaves_the_original_and_no_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name moves only after the new bytes are durable: a sync that fails leaves the original file untouched."""
    path: Path = tmp_path / "state.json"
    original: str = '{"ok": true}\n'
    path.write_text(original, encoding="utf-8")
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("failed while syncing")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(OSError, match="failed while syncing"):
        write_whole(path, '{"ok": false}\n')
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_the_file_is_synced_before_it_replaces_the_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The new file is flushed and synced, then moved over the old one, and the directory is synced after that."""
    path: Path = tmp_path / "skill.md"
    path.write_text("old skill", encoding="utf-8")
    path.chmod(0o644)
    text: str = "café skill, whole\n"
    order: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync(fd: int) -> None:
        info: os.stat_result = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            order.append("dir")
        else:
            assert info.st_size == len(text.encode("utf-8"))
            order.append("file")
        real_fsync(fd)

    def replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        order.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    write_whole(path, text)
    assert path.read_text(encoding="utf-8") == text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert order == ["file", "replace", "dir"]
    assert list(tmp_path.glob(".skill.md.*.tmp")) == []
