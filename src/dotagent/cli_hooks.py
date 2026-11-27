"""``dotx hooks``: list, make, rotate and remove the hooks (``docs/hooks.md``), on the file the daemon reads, under its
lock; the daemon takes a change at the next call, nothing to restart."""

import sys
import time
from datetime import datetime

from dotagent.cli_commands import Command, HooksAddCommand, HooksListCommand, HooksRemoveCommand, HooksRotateCommand
from dotagent.cli_schedule import store_alone
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import hooks_path
from dotagent.hooks.admin import HOOK_PATH, MadeHook, add_hook, draft_of, list_hooks, remove_hook, rotate_hook
from dotagent.hooks.model import Hook
from dotagent.server.links import base_url


def _address(config: AppConfig, hook_id: str) -> str:
    return f"{base_url(config.server.host, config.server.port)}{HOOK_PATH.format(id=hook_id)}"


def _target(hook: Hook) -> str:
    return f"schedule {hook.schedule_id}" if hook.schedule_id is not None else f"agent {hook.agent_id}"


def render_hook(hook: Hook, address: str) -> str:
    """One hook as ``dotx hooks list`` prints it: id, name, on or off, what it starts, its address, its last call."""
    state: str = "on" if hook.enabled else "off"
    last: str = "never called"
    if hook.calls:
        call = hook.calls[-1]
        when: str = datetime.fromtimestamp(call.at).strftime("%Y-%m-%d %H:%M")
        last = f"last call {when}: {call.status} {call.reason}" + (f" (run {call.run_id})" if call.run_id else "")
    return f"{hook.id}  {hook.name} [{state}] starts {_target(hook)}\n  {address}\n  {last}"


def _secret_lines(made: MadeHook, address: str) -> str:
    return (
        f"Hook {made.hook.id}: {made.hook.name}\n"
        f"  address: {address}\n"
        f"  secret:  {made.secret}\n"
        "The secret is shown this once: keep it where the caller signs with it (docs/hooks.md)."
    )


async def cmd_hooks_list(command: HooksListCommand) -> int:
    config: AppConfig = load_user_config()
    hooks: tuple[Hook, ...] = await list_hooks(hooks_path())
    for hook in hooks:
        sys.stdout.write(f"{render_hook(hook, _address(config, hook.id))}\n")
    sys.stderr.write(f"{len(hooks)} hook{'s' if len(hooks) != 1 else ''}\n")
    return 0


async def cmd_hooks_add(command: HooksAddCommand) -> int:
    config: AppConfig = load_user_config()
    draft = draft_of(
        command.title,
        command.schedule,
        command.agent,
        " ".join(command.template),
        tuple(command.field),
        command.per_minute,
    )
    async with store_alone() as store:
        made: MadeHook = await add_hook(store, hooks_path(), draft, time.time())
    sys.stdout.write(f"{_secret_lines(made, _address(config, made.hook.id))}\n")
    return 0


async def cmd_hooks_rotate(command: HooksRotateCommand) -> int:
    config: AppConfig = load_user_config()
    made: MadeHook = await rotate_hook(hooks_path(), command.hook_id)
    sys.stdout.write(f"{_secret_lines(made, _address(config, made.hook.id))}\n")
    return 0


async def cmd_hooks_remove(command: HooksRemoveCommand) -> int:
    hook: Hook = await remove_hook(hooks_path(), command.hook_id)
    sys.stdout.write(f"Removed: {hook.id} {hook.name}\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case HooksListCommand():
            return await cmd_hooks_list(command)
        case HooksAddCommand():
            return await cmd_hooks_add(command)
        case HooksRotateCommand():
            return await cmd_hooks_rotate(command)
        case HooksRemoveCommand():
            return await cmd_hooks_remove(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
