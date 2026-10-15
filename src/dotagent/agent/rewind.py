"""Rewinding a chat to before one of the owner's messages (item 282), as T3 Code's Edit from here does: the message and
all that came after it leave the chat, the history the model reads ends where it ended before the message, and the
message's words and attachments go back to the owner's composer. When the owner asks for it, the file changes of the
runs taken out, and of the runs they delegated to, are taken back as ``dotx undo`` takes them back: the changes made with
Dot's file tools, from their journals, each file only while it is as the runs left it; the others are named. What a
command changed stays, as no journal holds it. The plan is made before the chat is rewound, so that a journal that
cannot be read stops the rewind; once the chat is rewound, a file that cannot be put back is named with why, as one
changed since is. The chat's OpenCode session saw what was taken out, so the chat's next run on OpenCode starts a new
one, which reads the chat's history (``opencode.run``)."""

import asyncio
from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.agent.attachments import ATTACHMENTS_EVENT, AttachedFile
from dotagent.core.errors import StoreError, UndoError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonArray
from dotagent.opencode.run import forget_chat
from dotagent.runtime import Runtime
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RewindCut, RunRecord
from dotagent.undo.restore import UndoStep, apply_undo, due_steps, remove_empty_dirs
from dotagent.undo.runs import UndoPlan, plan_for, runs_with_children

log = get_logger(__name__)

# The live event that tells the pages a chat was rewound, so that they read their lists of chats again.
CHAT_REWOUND_EVENT: str = "chat_rewound"
# The plan of a rewind that keeps the changes.
NOTHING_TO_TAKE_BACK: UndoPlan = UndoPlan(steps=(), folders=())


class FilesTakenBack(FrozenModel):
    """The file changes of a rewind's runs: ``reverted`` were taken back; ``left`` were not, as the file changed after
    the runs' last change to it, no copy of it was kept or putting it back failed (each step says why)."""

    reverted: tuple[UndoStep, ...]
    left: tuple[UndoStep, ...]


async def opening_files(store: Store, run_id: str) -> list[AttachedFile]:
    """The attachments of the run's first message, from its ``attachments`` event (none when it has none)."""
    event: EventRecord | None = await store.last_event(run_id, ATTACHMENTS_EVENT)
    if event is None:
        return []
    files: JsonValue = event.data.get("files")
    if not isinstance(files, list):
        raise StoreError("a stored attachments event holds no list of files", {"run_id": run_id, "event_id": event.id})
    return [AttachedFile.model_validate(item) for item in files]


async def rewind_chat(runtime: Runtime, run_id: str, expected: Sequence[str]) -> RewindCut:
    """Rewind the chat of ``run_id`` to before the run's first message (``Store.rewind_chat``, which ``expected``, the
    runs the caller planned for, guards), let the chat's OpenCode session go and tell the pages."""
    cut: RewindCut = await runtime.store.rewind_chat(run_id, expected)
    await asyncio.to_thread(forget_chat, cut.thread_id)
    rewound: JsonArray = [run.id for run in cut.rewound]
    await runtime.bus.emit(
        run_id, CHAT_REWOUND_EVENT, {"thread_id": cut.thread_id, "rewound": rewound, "back_to": cut.back_to}, False
    )
    log.info("chat_rewound", run_id=run_id, thread_id=cut.thread_id, rewound=rewound, back_to=cut.back_to)
    return cut


async def plan_take_back(runtime: Runtime, runs: Sequence[RunRecord]) -> UndoPlan:
    """The plan to take back the file changes of ``runs`` and of the runs they delegated to (``undo.runs``), from their
    journals and what is at each path now."""
    family: list[RunRecord] = [member for run in runs for member in await runs_with_children(runtime.store, run)]
    return await asyncio.to_thread(plan_for, runtime.undo_dir, family)


async def take_back(runtime: Runtime, plan: UndoPlan) -> FilesTakenBack:
    """Carry out ``plan`` once the chat is rewound: each file that is as the runs left it gets back the copy their first
    change kept, or is deleted when they created it, one by one; a file that cannot be put back is left, with the error
    as why, and the others go on. The folders the runs created are removed once empty; one that cannot be is logged and
    left, as nothing else depends on it."""
    reverted: list[UndoStep] = []
    left: list[UndoStep] = [step for step in plan.steps if step.state in ("conflict", "cannot")]
    for step in due_steps(plan.steps):
        try:
            await asyncio.to_thread(apply_undo, [step], runtime.undo_dir)
        except UndoError as exc:
            log.warning("rewound_file_left", path=step.path, error=exc.message, context=exc.context)
            left.append(step.model_copy(update={"state": "cannot", "reason": exc.message}))
            continue
        reverted.append(step)
    try:
        await asyncio.to_thread(remove_empty_dirs, plan.folders)
    except UndoError as exc:
        log.warning("rewound_folder_left", error=exc.message, context=exc.context)
    log.info("rewound_files_taken_back", reverted=[step.path for step in reverted], left=[step.path for step in left])
    return FilesTakenBack(reverted=tuple(reverted), left=tuple(left))
