"""``dotx playbooks``: the playbooks Dot distilled from its runs, what each holds and where it comes from, dropping one,
and trusting one learned from third-party content."""

import asyncio
import sys
from pathlib import Path

from dotagent.agent.playbooks import PlaybookEntry, PlaybookMeta, drop_playbook, list_playbooks, trust_playbook
from dotagent.cli_commands import (
    Command,
    PlaybooksDropCommand,
    PlaybooksListCommand,
    PlaybooksShowCommand,
    PlaybooksTrustCommand,
)
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.paths import playbooks_dir

_DESCRIPTION_CHARS: int = 90


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def origin(meta: PlaybookMeta) -> str | None:
    """What the listing says of where a playbook's steps come from, or ``None`` for steps runs are offered as written."""
    if meta.tainted is None:
        return "origin unknown"
    return "learned from third-party content" if meta.tainted else None


def render_entry(entry: PlaybookEntry) -> str:
    state: str = "retired" if entry.retired else f"used {entry.meta.uses}, failed {entry.meta.failures}"
    marked: str | None = origin(entry.meta)
    shown: str = state if marked is None else f"{state}, {marked}"
    return f"  {entry.skill.name}  [{shown}]  {_clip(entry.skill.description, _DESCRIPTION_CHARS)}"


def render_list(entries: tuple[PlaybookEntry, ...], root: Path) -> list[str]:
    if not entries:
        return [f"No learned skills yet (a task run that ends done after enough acting steps writes one under {root})."]
    lines: list[str] = [f"Learned skills ({len(entries)}):", *(render_entry(entry) for entry in entries)]
    if any(origin(entry.meta) is not None for entry in entries):
        lines.append(
            "Runs are offered a playbook marked as learned from third-party content, or of unknown origin, with the "
            "steps that name an address, run a command or reach the home folder or the Mac's own folders withheld; "
            "'dotx playbooks trust "
            "<name>' offers one with all its steps."
        )
    return lines


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


async def cmd_playbooks_trust(command: PlaybooksTrustCommand) -> int:
    try:
        meta: PlaybookMeta = await asyncio.to_thread(trust_playbook, playbooks_dir(), command.playbook)
    except NotFoundError as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 1
    runs: str = ", ".join(meta.tainted_runs) or "none recorded"
    sys.stdout.write(
        f"trusted {command.playbook}: runs are offered all its steps until a run that reads third-party content "
        f"rewrites them (runs that read such content: {runs})\n"
    )
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
        case PlaybooksTrustCommand():
            return await cmd_playbooks_trust(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
