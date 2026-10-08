"""Finding skill folders under a skills directory: its children that hold a SKILL.md, and the children of container
folders a few levels down (``~/.claude/skills`` keeps symlinks to skills next to plugin folders with a ``skills/`` of
their own). Symlinks are followed; the depth bound keeps a loop finite. Hidden folders are left alone."""

from pathlib import Path

from dotagent.core.errors import SkillError
from dotagent.skills.types import SKILL_FILE

_SKIPPED: frozenset[str] = frozenset({"node_modules", "__pycache__"})
_MAX_DEPTH: int = 3


def _children(folder: Path) -> list[Path]:
    try:
        return sorted(folder.iterdir())
    except OSError as exc:
        raise SkillError("a skills directory cannot be read", {"path": str(folder), "error": str(exc)}) from exc


def _skill_files(folder: Path, depth: int) -> list[Path]:
    found: list[Path] = []
    for child in _children(folder):
        if child.name.startswith(".") or child.name in _SKIPPED or not child.is_dir():
            continue
        marker: Path = child / SKILL_FILE
        if marker.is_file():
            found.append(marker)
        elif depth < _MAX_DEPTH:
            found.extend(_skill_files(child, depth + 1))
    return found


def find_skill_files(root: Path) -> tuple[Path, ...]:
    """Every SKILL.md under ``root`` in a stable order, or ``root``'s own when ``root`` is one skill folder."""
    own: Path = root / SKILL_FILE
    if own.is_file():
        return (own,)
    return tuple(_skill_files(root, 1))
