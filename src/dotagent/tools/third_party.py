"""The folders of the workspace whose files hold what third parties wrote (item 494): the pages of a crawl too long for
one result (``crawl``) and the MCP answers too long for one (``mcp-results``). The call that wrote such a file marked
the run untrusted; reading the file back marks it again, since a message of the owner may have cleared the mark in
between."""

import re
from pathlib import Path

from dotagent.policy.paths import expand_path

CRAWL_FOLDER: str = "crawl"
MCP_FOLDER: str = "mcp-results"
THIRD_PARTY_FOLDERS: tuple[str, ...] = (CRAWL_FOLDER, MCP_FOLDER)


def third_party_roots(workspace: Path) -> tuple[Path, ...]:
    """The folders of third-party files of ``workspace``, as the kernel names them."""
    return tuple(expand_path(folder, workspace) for folder in THIRD_PARTY_FOLDERS)


def holds_third_party(path: Path, workspace: Path) -> bool:
    """Whether the resolved ``path`` is one of the folders of third-party files of ``workspace`` or lies in one."""
    return any(path.is_relative_to(root) for root in third_party_roots(workspace))


def matches_third_party(lines: list[str], workspace: Path) -> bool:
    """Whether a line of ripgrep's ``path:line:text`` answer, searched by a resolved path, comes from a third-party
    file."""
    roots: tuple[Path, ...] = third_party_roots(workspace)
    return any(Path(line.split(":", 1)[0]).is_relative_to(root) for line in lines for root in roots)


def _spellings(paths: tuple[Path, ...], here: Path) -> frozenset[str]:
    """How a shell line may write ``paths``: whole, from the home folder with ``~``, and from ``here``."""
    home: Path = Path.home()
    found: set[str] = {str(path) for path in paths}
    for path in paths:
        if path.is_relative_to(home):
            found.add(f"~/{path.relative_to(home)}")
        if path.is_relative_to(here):
            found.add(str(path.relative_to(here)))
    return frozenset(found)


def _names(command: str, spelling: str) -> bool:
    return re.search(rf"(?<![\w.~/-])(?:\./)?{re.escape(spelling)}(?![\w.-])", command) is not None


def mentions_third_party(command: str, cwd: Path, workspace: Path) -> bool:
    """Whether a shell line run in ``cwd`` runs inside a folder of third-party files, or names one or a file in one by
    its whole path, its ``~`` path or its path from ``cwd``. The words of the line are read, not what it does: a path
    it builds otherwise (a variable, a glob of the folder's name, a search of a parent folder) is not seen."""
    here: Path = expand_path(str(cwd), workspace)
    for folder in THIRD_PARTY_FOLDERS:
        root: Path = expand_path(folder, workspace)
        if here.is_relative_to(root):
            return True
        if any(_names(command, spelling) for spelling in _spellings((root, workspace / folder), here)):
            return True
    return False
