"""The notes file of a run is written where it is named or nowhere (item 556): a notes write is never asked, so a link
at the notes file or at its folder, which a command of the run can make, is refused rather than followed, and what it
leads to (Dot's own code, a file outside the workspace) is left as it was. Writing the file and adding to it work as
before, and the change is noted in the undo journal under the path that was written."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.core.errors import ToolExecutionError
from dotagent.core.types import JsonObject
from dotagent.tools.base import RegisteredTool, ToolResult
from dotagent.tools.notes import NOTES_TOOL, build_notes_tools, notes_path
from dotagent.undo.journal import read_journal, run_journal

RUN_ID: str = "run_1"
MIB: int = 1024 * 1024
OWN_CODE: str = "print('Dot runs this at its next start')\n"
NOTES: str = "# Findings\n\n- The catalogue sells lamps."
MORE: str = "- Thirty products in all."
REFUSED: str = "nothing was written"


def _context(home: Path, dot_paths: tuple[Path, ...]) -> SimpleNamespace:
    """What write_notes reads of the tool context: the workspace, the run, the places the file tools guard and where
    the undo journal is kept."""
    return SimpleNamespace(
        workspace=home / "workspace",
        run_id=RUN_ID,
        runtime=SimpleNamespace(
            protected=(),
            dot_paths=dot_paths,
            undo_dir=home / "undo",
            config=SimpleNamespace(agent=SimpleNamespace(undo_max_file_bytes=MIB, undo_max_run_bytes=MIB)),
        ),
    )


def _home(tmp_path: Path) -> tuple[Path, Path]:
    """A home with its workspace, and a file of Dot's own code outside it."""
    home: Path = Path(os.path.realpath(tmp_path))
    (home / "workspace").mkdir()
    code: Path = home / "dotagent" / "loop.py"
    code.parent.mkdir()
    code.write_text(OWN_CODE, encoding="utf-8")
    return home, code


def _link(link: Path, to: Path) -> None:
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(to)


def _workspace_led_to(home: Path, to: Path) -> None:
    (home / "workspace").rmdir()
    (home / "workspace").symlink_to(to)


def _left_alone(code: Path) -> tuple[str, list[str]]:
    """What the file of Dot's own code holds, and what its folder holds."""
    return code.read_text(encoding="utf-8"), sorted(entry.name for entry in code.parent.iterdir())


def _written(home: Path) -> tuple[str, list[str], list[tuple[str, str, bool]]]:
    """What the notes file holds, what its folder holds, and what the undo journal noted of it."""
    path: Path = notes_path(home / "workspace", RUN_ID)
    noted: list[tuple[str, str, bool]] = [
        (entry.tool, entry.path, entry.existed) for entry in read_journal(run_journal(home / "undo", RUN_ID))
    ]
    return path.read_text(encoding="utf-8"), sorted(entry.name for entry in path.parent.iterdir()), noted


async def _notes(ctx: SimpleNamespace, arguments: JsonObject) -> ToolResult:
    tool: RegisteredTool = next(tool for tool in build_notes_tools() if tool.spec.name == NOTES_TOOL)
    prepared = await tool.prepare(arguments, ctx)  # type: ignore[arg-type]
    return await prepared.run(ctx)  # type: ignore[arg-type]


async def test_the_notes_are_written_and_added_to_in_their_own_file(tmp_path: Path) -> None:
    home, code = _home(tmp_path)
    ctx: SimpleNamespace = _context(home, (code.parent,))
    await _notes(ctx, {"content": NOTES})
    await _notes(ctx, {"content": MORE, "append": True})
    path: str = str(notes_path(home / "workspace", RUN_ID))
    assert _written(home) == (
        f"{NOTES}\n\n{MORE}",
        [f"{RUN_ID}.md"],
        [(NOTES_TOOL, path, False), (NOTES_TOOL, path, True)],
    )


@pytest.mark.parametrize("append", [False, True])
async def test_a_link_at_the_notes_file_is_refused_and_what_it_leads_to_is_left_alone(
    tmp_path: Path, append: bool
) -> None:
    home, code = _home(tmp_path)
    ctx: SimpleNamespace = _context(home, (code.parent,))
    _link(notes_path(home / "workspace", RUN_ID), code)
    with pytest.raises(ToolExecutionError, match=REFUSED):
        await _notes(ctx, {"content": NOTES, "append": append})
    assert _left_alone(code) == (OWN_CODE, [code.name])
    assert read_journal(run_journal(home / "undo", RUN_ID)) == []


async def test_a_link_at_the_notes_folder_is_refused_and_nothing_is_made_where_it_leads(tmp_path: Path) -> None:
    home, code = _home(tmp_path)
    ctx: SimpleNamespace = _context(home, (code.parent,))
    _link(home / "workspace" / "notes", code.parent)
    with pytest.raises(ToolExecutionError, match=REFUSED):
        await _notes(ctx, {"content": NOTES})
    assert _left_alone(code) == (OWN_CODE, [code.name])


async def test_a_workspace_that_leads_into_dots_own_files_is_refused(tmp_path: Path) -> None:
    home, code = _home(tmp_path)
    ctx: SimpleNamespace = _context(home, (code.parent,))
    _workspace_led_to(home, code.parent)
    with pytest.raises(ToolExecutionError, match=REFUSED):
        await _notes(ctx, {"content": NOTES})
    assert _left_alone(code) == (OWN_CODE, [code.name])
