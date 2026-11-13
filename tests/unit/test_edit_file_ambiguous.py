"""An ambiguous ``edit_file`` names where its text is (item 1145)."""

from pathlib import Path

import pytest

from dotagent.core.errors import ToolExecutionError
from dotagent.policy.paths import expand_path
from dotagent.tools.files import _edit_blocking
from dotagent.undo.journal import JournalTarget


def test_an_ambiguous_edit_names_the_lines_of_its_text(tmp_path: Path) -> None:
    """The owner's runs sent a text found twice and were told only how many times, so each read the file again to find
    where; the error names the lines, in a CRLF file too, and in a third party's file, as numbers say nothing of it."""
    workspace: Path = expand_path(str(tmp_path), tmp_path)
    journal: JournalTarget = JournalTarget(
        directory=tmp_path / "undo", tool="edit_file", max_file_bytes=1_000_000, max_run_bytes=1_000_000
    )
    for folder, newline in (("notes", "\n"), ("drafts", "\r\n"), ("mcp-results", "\n")):
        path: Path = workspace / folder / "post.md"
        path.parent.mkdir()
        body: str = newline.join(["# Title", "Read more", "", "text", "Read more", ""])
        path.write_bytes(body.encode("utf-8"))
        with pytest.raises(ToolExecutionError) as raised:
            _edit_blocking(path, "Read more", "Read on", False, journal, workspace)
        assert raised.value.context["at_lines"] == [2, 5]
        assert path.read_bytes() == body.encode("utf-8")
