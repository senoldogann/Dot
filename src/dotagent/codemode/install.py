"""Puts what runs inside the sandbox where the sandbox reads it (item 1500).

The launcher, ``dot_tools`` (its ``__init__``, the bridge, the catalogue of tools and the server folders) and the
folders of a run's code are written, by :class:`dotagent.codemode.tree.Tree`, under ``<workspace>/code/<run id>/``:
``tools`` (read only to the code) and ``scratch`` (where it writes). The launcher and the bridge are copies of the two
files of this package that run inside the sandbox, written before every start: the code cannot change them (the profile
gives it no write there), and what the daemon starts is what the package holds.
"""

from collections.abc import Mapping
from pathlib import Path

from dotagent.codemode.sandbox import Layout
from dotagent.codemode.tree import Tree

type Files = Mapping[tuple[str, ...], bytes]

# The files that are the same for every run, by where they go in the tools folder and where this package keeps them.
_CHILD: dict[tuple[str, ...], str] = {
    ("launcher.py",): "child_launcher.py",
    ("dot_tools", "__init__.py"): "child_tools_init.py",
    ("dot_tools", "_bridge.py"): "child_bridge.py",
}
EMPTY_CATALOG: bytes = b'{"servers":[],"tools":[]}'
_CATALOG: tuple[str, ...] = ("dot_tools", "_catalog.json")
_SERVERS: tuple[str, ...] = ("dot_tools", "servers", "__init__.py")


def child_files() -> dict[tuple[str, ...], bytes]:
    """The files of the tools folder that every run has: the launcher, the package's ``__init__`` and the bridge, as
    this package holds them, and an empty catalogue where the run's has not been given."""
    here: Path = Path(__file__).parent
    files: dict[tuple[str, ...], bytes] = {parts: (here / name).read_bytes() for parts, name in _CHILD.items()}
    files[_CATALOG] = EMPTY_CATALOG
    files[_SERVERS] = b""
    return files


def install(layout: Layout, tool_files: Files | None = None) -> None:
    """Make the run's folders and write the tools folder: the files every run has, then ``tool_files`` (the catalogue
    and the functions of the tools the run may call, :mod:`dotagent.codemode.toolfiles`) over them. Blocking. An
    :class:`OSError` says a folder or a file could not be made, or that a link stands where one is to be."""
    base: tuple[str, ...] = layout.run.relative_to(layout.root).parts
    files: dict[tuple[str, ...], bytes] = child_files()
    if tool_files is not None:
        files.update(tool_files)
    with Tree(layout.root) as tree:
        tree.folder((*base, "scratch"))
        for parts, data in files.items():
            tree.write((*base, "tools", *parts), data)
