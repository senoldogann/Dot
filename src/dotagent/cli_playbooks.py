"""``dotx playbooks``: the playbooks Dot distilled from its runs, what each holds, and dropping one."""

import asyncio
import sys
from pathlib import Path

from dotagent.agent.playbooks import PlaybookEntry, drop_playbook, list_playbooks
from dotagent.cli_commands import Command, PlaybooksDropCommand, PlaybooksListCommand, PlaybooksShowCommand
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.paths import playbooks_dir

_DESCRIPTION_CHARS: int = 90


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def render_entry(entry: PlaybookEntry) -> str:
    state: str = "retired" if entry.retired else f"used {entry.meta.uses}, failed {entry.meta.failures}"
    return f"  {entry.skill.name}  [{state}]  {_clip(entry.skill.description, _DESCRIPTION_CHARS)}"


def render_list(entries: tuple[PlaybookEntry, ...], root: Path) -> list[str]:
    if not entries:
        return [f"No learned skills yet (a task run that ends done after enough acting steps writes one under {root})."]
    return [f"Learned skills ({len(entries)}):", *(render_entry(entry) for entry in entries)]


async def cmd_playbooks_list(command: PlaybooksListCommand) -> int:
    root: Path = playbooks_dir()
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, root)
    sys.stdout.write("\n".join(render_list(entries, root)) + "\n")
    return 0


async def cmd_playbooks_show(command: PlaybooksShowCommand) -> int:
    entries: tuple[PlaybookEntry, ...] = await asyncio.to_thread(list_playbooks, playbooks_dir())
    found: PlaybookEntry | None = next((entry for entry in entries if entry.skill.name == command.playbook), None)
    if found is None:
        sys.stderr.write(f"there is no playbook called {command.playbook}\n")
        return 1
    sys.stdout.write(f"{render_entry(found).strip()}\n{found.skill.root}\n\n{found.skill.body}\n")
    return 0


async def cmd_playbooks_drop(command: PlaybooksDropCommand) -> int:
    try:
        folder: Path = await asyncio.to_thread(drop_playbook, playbooks_dir(), command.playbook)
    except NotFoundError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 1
    sys.stdout.write(f"dropped {command.playbook} ({folder})\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case PlaybooksListCommand():
            return await cmd_playbooks_list(command)
        case PlaybooksShowCommand():
            return await cmd_playbooks_show(command)
        case PlaybooksDropCommand():
            return await cmd_playbooks_drop(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
