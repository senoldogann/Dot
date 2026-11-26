"""A search that prints more than the cap is ended at it (item 1956): a search for a word in a tree that holds it
everywhere printed 397 MB (1.8 M lines) to the call, which the tool decoded and split to show 300 lines, 1.8 GB of the
daemon's memory. ripgrep's output is read as it comes up to the cap, whole lines only, and the search is ended."""

import os
import shutil
from pathlib import Path

import pytest

from dotagent.tools import files
from dotagent.tools.files import RgOutput, run_rg

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
