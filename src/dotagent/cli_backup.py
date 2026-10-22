"""``dotx backup now`` copies Dot's database at once, as the daemon does every ``[store] backup_every_hours`` hours,
and thins the older copies; ``dotx backup list`` lists the copies kept, newest first (item 526)."""

import asyncio
import sys
import time
from pathlib import Path

from dotagent.cli_commands import BackupListCommand, BackupNowCommand, Command
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import db_path
from dotagent.store.backup import Backup, backup_folder, list_backups, make_backup, thin_backups

_MIB: int = 1024 * 1024


def _size(size: int) -> str:
    return f"{size / _MIB:.1f} MiB"


def _age(made_at: float, now: float) -> str:
    hours: float = (now - made_at) / 3600.0
    return f"{hours:.1f} h ago" if hours < 48 else f"{hours / 24:.0f} days ago"


async def cmd_backup_now() -> int:
    config: AppConfig = load_user_config()
    folder: Path = backup_folder(config.store)
    made: Backup = await asyncio.to_thread(make_backup, db_path(), folder, time.time())
    gone: list[Backup] = await asyncio.to_thread(
        thin_backups, folder, config.store.backup_keep_hourly, config.store.backup_keep_days, time.time()
    )
    sys.stdout.write(f"Copied the database to {made.path} ({_size(made.size)}).\n")
    if gone:
        sys.stdout.write(f"Removed {len(gone)} older copies past what [store] keeps.\n")
    return 0


async def cmd_backup_list() -> int:
    config: AppConfig = load_user_config()
    folder: Path = backup_folder(config.store)
    copies: list[Backup] = await asyncio.to_thread(list_backups, folder)
    if not copies:
        sys.stdout.write(f"No copies in {folder}.\n")
        return 0
    now: float = time.time()
    sys.stdout.write(f"{len(copies)} copies in {folder}, newest first:\n")
    for copy in copies:
        sys.stdout.write(f"  {Path(copy.path).name}  {_size(copy.size)}  {_age(copy.made_at, now)}\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case BackupNowCommand():
            return await cmd_backup_now()
        case BackupListCommand():
            return await cmd_backup_list()
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
