"""Taking a plugin out of Dot (item 497): its folder in Dot's plugins goes to the owner's Trash, where Finder would put
it, so that it can be put back. A plugin read from a folder of ``[plugins] dirs`` is not Dot's to move: the owner takes
that folder out of the config."""

import shutil
from pathlib import Path

from dotagent.plugins.types import Plugin


def trash_folder() -> Path:
    """The owner's Trash, the one Finder empties."""
    return Path.home() / ".Trash"


def is_dots_own(plugin: Plugin, own: Path) -> bool:
    """Whether ``plugin`` is one of Dot's plugins folder (``own``), which Dot may move, rather than of ``[plugins]
    dirs``."""
    return plugin.root.parent.resolve() == own.resolve()


def move_to_trash(folder: Path, trash: Path) -> Path:
    """Move ``folder`` into ``trash`` under its own name, or with a number after it when the Trash holds one of that
    name already, as Finder does; where it went."""
    target: Path = trash / folder.name
    number: int = 2
    while target.exists():
        target = trash / f"{folder.name} {number}"
        number += 1
    trash.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(target))
    return target
