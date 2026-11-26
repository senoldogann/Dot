"""A search that prints more than the cap is ended at it (item 1956): a search for a word in a tree that holds it
everywhere printed 397 MB (1.8 M lines) to the call, which the tool decoded and split to show 300 lines, 1.8 GB of the
daemon's memory. ripgrep's output is read as it comes up to the cap, whole lines only, and the search is ended."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from dotagent.tools import files
from dotagent.tools.files import RgOutput, _drain_keeping, _unread, run_rg

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="needs ripgrep")

_LINE: str = "needle " + "x" * 60 + "\n"


async def _search(tree: Path) -> RgOutput:
    return await run_rg(
        ["--line-number", "--no-heading", "--color", "never", "-e", "needle", "--", str(tree)],
        (),
        tree,
        dict(os.environ),
    )


async def test_a_search_that_prints_more_than_the_cap_is_ended_at_it_with_whole_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(files, "_RG_STDOUT_BYTES", 256 * 1024)
    for number in range(8):
        (tmp_path / f"f{number}.txt").write_text(_LINE * 20_000, encoding="utf-8")
    found: RgOutput = await _search(tmp_path)
    assert found.capped and found.code == 0
    assert len(found.stdout.encode("utf-8")) <= 256 * 1024
    lines: list[str] = found.stdout.splitlines()
    assert len(lines) > 1000 and all(line.rstrip().endswith("x" * 60) for line in lines), "a line was cut in two"


async def test_a_search_under_the_cap_is_whole_and_its_exit_code_is_ripgreps(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text(_LINE * 3, encoding="utf-8")
    found: RgOutput = await _search(tmp_path)
    assert (found.capped, found.code, len(found.stdout.splitlines())) == (False, 0, 3)
    (tmp_path / "a.txt").write_text("nothing here\n", encoding="utf-8")
    nothing: RgOutput = await _search(tmp_path)
    assert (nothing.capped, nothing.code, nothing.stdout) == (False, 1, "")


async def test_what_ripgrep_says_past_the_cap_is_read_and_let_go_so_that_it_never_blocks_on_it() -> None:
    """The review of item 1956: stderr was no longer read once it passed its cap, so ripgrep could stall on a full pipe
    and the search hung (some 2 600 unreadable paths are enough)."""
    stream: asyncio.StreamReader = asyncio.StreamReader()
    stream.feed_data(b"rg: a path (os error 13)\n" * 40_000)
    stream.feed_eof()
    kept: bytes = await asyncio.wait_for(_drain_keeping(stream, 1000), timeout=5)
    assert len(kept) == 1000 and kept.startswith(b"rg: a path")
    assert stream.at_eof() and len(stream._buffer) == 0  # type: ignore[attr-defined]  # whole stream was consumed


def _fake_rg(tmp_path: Path) -> Path:
    """A folder with an `rg` that finds one match and says 9 000 paths were not readable, past the stderr cap."""
    bin_dir: Path = tmp_path / "bin"
    bin_dir.mkdir()
    fake: Path = bin_dir / "rg"
    fake.write_text(
        "#!/bin/sh\n"
        'echo "a.txt:1:needle"\n'
        'i=0; while [ $i -lt 9000 ]; do echo "rg: /Users/x/Library/path$i: Operation not permitted (os error 1)" >&2; '
        "i=$((i+1)); done\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir


async def test_a_search_with_more_unreadable_paths_than_the_stderr_cap_still_reports_them_whole(tmp_path: Path) -> None:
    """The review of item 1956: stderr cut at a byte left a last line `_unread` could not read, and the search failed
    with "ripgrep failed", dropping its matches."""
    bin_dir: Path = _fake_rg(tmp_path)
    found: RgOutput = await run_rg(
        ["-e", "needle"], (), tmp_path, {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    )
    assert found.code == 2 and found.stdout.startswith("a.txt")
    assert _unread(found.stderr) is not None, "a cut line made the unreadable paths unreadable"
