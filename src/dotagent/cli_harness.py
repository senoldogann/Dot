"""``dotx harness``: the versions of what a run's prompt takes from the harness (item 389, docs/harness.md): list,
show and diff them, write one of the owner's own, adopt or retire one."""

import asyncio
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotagent.cli_commands import (
    Command,
    HarnessAdoptCommand,
    HarnessDiffCommand,
    HarnessListCommand,
    HarnessProposeCommand,
    HarnessRetireCommand,
    HarnessSetCommand,
    HarnessShowCommand,
)
from dotagent.config import load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import db_path, screenshots_dir
from dotagent.harness.components import COMPONENTS, NO_HARNESS, HarnessText
from dotagent.harness.propose import propose_from_run
from dotagent.harness.service import OWNER, adopt, adopted, create_version, diff_text, retire, text_of
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import HarnessVersion

# What a version's line names in place of a text: Dot's own text, which a run takes while none is adopted.
OWN_TEXT: str = "Dot's own text"


@asynccontextmanager
async def _store() -> AsyncIterator[Store]:
    """The store alone, opened without the rest of the runtime and closed afterwards."""
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        yield store
    finally:
        await store.close()


def _when(ts: float | None) -> str:
    return "" if ts is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def version_line(version: HarnessVersion) -> str:
    """``hrn_… adopted  owner  2026-10-19 03:40  A habit of checking the file first``."""
    return f"{version.id}  {version.state:<9} {version.made_by:<6} {_when(version.created_at)}  {version.summary}"


def render_version(version: HarnessVersion) -> list[str]:
    """The version's line, its parent, then each component under its name, ``(nothing)`` for an empty one."""
    lines: list[str] = [version_line(version), f"made from: {version.parent_id or OWN_TEXT}"]
    for name in COMPONENTS:
        text: str = text_of(version).component(name)
        lines.extend(["", f"## {name}", text if text != "" else "(nothing)"])
    if version.notes != "":
        lines.extend(["", "## notes", version.notes])
    return lines


def _read_component(place: str | None) -> str | None:
    """The text of the file ``place`` names, standard input for ``-``, ``None`` when no file was named."""
    if place is None:
        return None
    if place == "-":
        return sys.stdin.read()
    return Path(place).expanduser().read_text(encoding="utf-8")


async def cmd_harness_list(command: HarnessListCommand) -> int:
    async with _store() as store:
        versions: list[HarnessVersion] = await store.list_harness_versions()
    if not versions:
        sys.stdout.write(f"No version yet: every run takes {OWN_TEXT}. Write one with 'dotx harness set'.\n")
        return 0
    for version in versions:
        sys.stdout.write(f"{version_line(version)}\n")
    return 0


async def cmd_harness_show(command: HarnessShowCommand) -> int:
    async with _store() as store:
        version: HarnessVersion | None = (
            await adopted(store) if command.version_id is None else await store.get_harness_version(command.version_id)
        )
    if version is None:
        sys.stdout.write(f"No version is adopted: every run takes {OWN_TEXT}.\n")
        return 0
    sys.stdout.write("\n".join(render_version(version)) + "\n")
    return 0


async def cmd_harness_diff(command: HarnessDiffCommand) -> int:
    async with _store() as store:
        version: HarnessVersion = await store.get_harness_version(command.version_id)
        against: HarnessVersion | None
        if command.against is not None:
            against = await store.get_harness_version(command.against)
        else:
            current: HarnessVersion | None = await adopted(store)
            against = None if current is None or current.id == version.id else current
    before: HarnessText = NO_HARNESS if against is None else text_of(against)
    diff: str = diff_text(before, text_of(version), OWN_TEXT if against is None else against.id, version.id)
    sys.stdout.write(f"{diff}\n" if diff != "" else "The texts are the same.\n")
    return 0


async def cmd_harness_set(command: HarnessSetCommand) -> int:
    if command.habits == "-" and command.tool_guidance == "-":
        raise InvalidValueError("standard input can be read for one component only", {})
    habits: str | None = _read_component(command.habits)
    tool_guidance: str | None = _read_component(command.tool_guidance)
    if habits is None and tool_guidance is None:
        raise InvalidValueError("name a file for --habits or --tool-guidance", {})
    config = load_user_config()
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    async with _store() as store:
        current: HarnessVersion | None = await adopted(store)
        kept: HarnessText = NO_HARNESS if current is None else text_of(current)
        text: HarnessText = HarnessText(
            habits=kept.habits if habits is None else habits,
            tool_guidance=kept.tool_guidance if tool_guidance is None else tool_guidance,
        )
        version: HarnessVersion = await create_version(store, config.harness, text, command.summary, OWNER, "", secrets)
        if command.adopt:
            version = await adopt(store, version.id, time.time())
    sys.stdout.write(f"{'Adopted' if command.adopt else 'Written'}: {version_line(version)}\n")
    if not command.adopt:
        sys.stdout.write(f"Adopt it with 'dotx harness adopt {version.id}'.\n")
    return 0


async def cmd_harness_adopt(command: HarnessAdoptCommand) -> int:
    async with _store() as store:
        version: HarnessVersion = await adopt(store, command.version_id, time.time())
    sys.stdout.write(f"Adopted: {version_line(version)}\nThe runs that start from now on take it.\n")
    return 0


async def cmd_harness_retire(command: HarnessRetireCommand) -> int:
    async with _store() as store:
        version: HarnessVersion = await retire(store, command.version_id, time.time())
        current: HarnessVersion | None = await adopted(store)
    sys.stdout.write(f"Retired: {version_line(version)}\n")
    sys.stdout.write(f"The runs that start from now on take {OWN_TEXT if current is None else current.id}.\n")
    return 0


async def cmd_harness_propose(command: HarnessProposeCommand) -> int:
    """One question to the proposer about a run; its candidate waits for 'dotx harness adopt'."""
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        version: HarnessVersion | None = await propose_from_run(runtime, command.run_id, command.scope)
    finally:
        await close_runtime(runtime)
    if version is None:
        sys.stdout.write(f"The proposer keeps the harness as it is: nothing in {command.run_id} argues for a change.\n")
        return 0
    sys.stdout.write("\n".join(render_version(version)) + "\n")
    sys.stdout.write(
        f"Adopt it with 'dotx harness adopt {version.id}', or see what it changes with 'dotx harness diff'.\n"
    )
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case HarnessListCommand():
            return await cmd_harness_list(command)
        case HarnessShowCommand():
            return await cmd_harness_show(command)
        case HarnessDiffCommand():
            return await cmd_harness_diff(command)
        case HarnessSetCommand():
            return await cmd_harness_set(command)
        case HarnessAdoptCommand():
            return await cmd_harness_adopt(command)
        case HarnessRetireCommand():
            return await cmd_harness_retire(command)
        case HarnessProposeCommand():
            return await cmd_harness_propose(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
