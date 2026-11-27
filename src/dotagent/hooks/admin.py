"""Making, rotating, switching and deleting hooks, as Settings › Hooks and ``dotx hooks`` do: what is bound is checked
against the store, then the file is changed under its lock (:func:`dotagent.hooks.book.change_hooks`)."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from dotagent.core.errors import InvalidValueError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.hooks.book import (
    change_hooks,
    hook_in,
    new_hook_id,
    new_secret,
    read_hooks,
    replaced,
    without,
)
from dotagent.hooks.model import Hook, HookDraft, HookFile, check_fields, check_template
from dotagent.store.db import Store

log = get_logger(__name__)

# The address a hook is called at, after the daemon's own (``docs/hooks.md``).
HOOK_PATH: str = "/api/hooks/{id}"


class MadeHook(FrozenModel):
    """A hook just made or whose secret was just rotated, with its secret: the one answer that carries it."""

    hook: Hook
    secret: str


async def check_target(store: Store, draft: HookDraft) -> None:
    """Raise :class:`NotFoundError` when the schedule or agent ``draft`` binds is not there."""
    if draft.binding == "schedule":
        await store.get_schedule(draft.target_id)
    else:
        await store.get_agent(draft.target_id)


def _add(
    draft: HookDraft, fields: tuple[str, ...], template: str, secret: str, now: float
) -> Callable[[HookFile], tuple[HookFile, Hook]]:
    """The change of the file that adds the hook of ``draft``."""

    def change(book: HookFile) -> tuple[HookFile, Hook]:
        hook: Hook = Hook(
            id=new_hook_id(frozenset(kept.id for kept in book.hooks)),
            name=draft.name.strip(),
            schedule_id=draft.target_id if draft.binding == "schedule" else None,
            agent_id=draft.target_id if draft.binding == "agent" else None,
            template=template,
            fields=fields,
            per_minute=draft.per_minute,
            enabled=True,
            secret=secret,
            created_at=now,
            calls=(),
            seen=(),
        )
        return HookFile(hooks=(*book.hooks, hook)), hook

    return change


async def add_hook(store: Store, path: Path, draft: HookDraft, now: float) -> MadeHook:
    """Make a hook of ``draft``, switched on, with a new secret, once what it binds is found and its fields and template
    hold together (:func:`check_fields`, :func:`check_template`)."""
    fields: tuple[str, ...] = check_fields(draft.fields)
    template: str = check_template(draft.template, fields, draft.binding)
    await check_target(store, draft)
    secret: str = new_secret()
    hook: Hook = await asyncio.to_thread(change_hooks, path, _add(draft, fields, template, secret, now))
    log.info("hook_made", hook_id=hook.id, binding=draft.binding, target_id=draft.target_id)
    return MadeHook(hook=hook, secret=secret)


async def rotate_hook(path: Path, hook_id: str) -> MadeHook:
    """Give the hook ``hook_id`` a new secret: calls signed with the old one are refused from now on."""
    secret: str = new_secret()

    def change(book: HookFile) -> tuple[HookFile, Hook]:
        hook: Hook = hook_in(book, hook_id).model_copy(update={"secret": secret, "seen": ()})
        return replaced(book, hook), hook

    hook: Hook = await asyncio.to_thread(change_hooks, path, change)
    log.info("hook_secret_rotated", hook_id=hook_id)
    return MadeHook(hook=hook, secret=secret)


async def switch_hook(path: Path, hook_id: str, enabled: bool) -> Hook:
    """Switch the hook ``hook_id`` on or off: a call to one that is off is refused."""

    def change(book: HookFile) -> tuple[HookFile, Hook]:
        hook: Hook = hook_in(book, hook_id).model_copy(update={"enabled": enabled})
        return replaced(book, hook), hook

    hook: Hook = await asyncio.to_thread(change_hooks, path, change)
    log.info("hook_switched", hook_id=hook_id, enabled=enabled)
    return hook


async def remove_hook(path: Path, hook_id: str) -> Hook:
    """Delete the hook ``hook_id``; its address answers no call again."""

    def change(book: HookFile) -> tuple[HookFile, Hook]:
        return without(book, hook_id), hook_in(book, hook_id)

    hook: Hook = await asyncio.to_thread(change_hooks, path, change)
    log.info("hook_removed", hook_id=hook_id)
    return hook


async def list_hooks(path: Path) -> tuple[Hook, ...]:
    return (await asyncio.to_thread(read_hooks, path)).hooks


def draft_of(
    name: str, schedule_id: str | None, agent_id: str | None, template: str, fields: tuple[str, ...], per_minute: int
) -> HookDraft:
    """The draft of a hook bound to ``schedule_id`` or ``agent_id``, exactly one of them."""
    if schedule_id is not None and agent_id is None:
        return HookDraft(
            name=name,
            binding="schedule",
            target_id=schedule_id,
            template=template,
            fields=fields,
            per_minute=per_minute,
        )
    if agent_id is not None and schedule_id is None:
        return HookDraft(
            name=name, binding="agent", target_id=agent_id, template=template, fields=fields, per_minute=per_minute
        )
    raise InvalidValueError("a hook is bound to a schedule or to an agent, one of them", {})
