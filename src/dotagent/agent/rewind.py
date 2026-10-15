"""Rewinding a chat to before one of the owner's messages (item 282), as T3 Code's Edit from here does: the message and
all that came after it leave the chat, the history the model reads ends where it ended before the message, and the
message's words and attachments go back to the owner's composer. When the owner asks for it, the file changes of the
runs taken out, and of the runs they delegated to, are taken back as ``dotx undo`` takes them back: the changes made with
Dot's file tools, from their journals, each file only while it is as the runs left it; the others are named. What a
command changed stays, as no journal holds it. The chat's OpenCode session saw what was taken out, so the chat's next run
on OpenCode starts a new one, which reads the chat's history (``opencode.run``)."""

import asyncio
from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.agent.attachments import ATTACHMENTS_EVENT, AttachedFile
from dotagent.core.errors import StoreError
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


class FilesTakenBack(FrozenModel):
    """The file changes of a rewind's runs: ``reverted`` were taken back; ``left`` were not, as the file changed after
    the runs' last change to it or no copy of it was kept (each step says why)."""

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


async def rewind_chat(runtime: Runtime, run_id: str) -> RewindCut:
    """Rewind the chat of ``run_id`` to before the run's first message (``Store.rewind_chat``), let the chat's OpenCode
    session go and tell the pages."""
    cut: RewindCut = await runtime.store.rewind_chat(run_id)
    await asyncio.to_thread(forget_chat, cut.thread_id)
    rewound: JsonArray = [run.id for run in cut.rewound]
    await runtime.bus.emit(
        run_id, CHAT_REWOUND_EVENT, {"thread_id": cut.thread_id, "rewound": rewound, "back_to": cut.back_to}, False
    )
    log.info("chat_rewound", run_id=run_id, thread_id=cut.thread_id, rewound=rewound, back_to=cut.back_to)
    return cut


async def take_back_files(runtime: Runtime, runs: Sequence[RunRecord]) -> FilesTakenBack:
    """Take back the file changes of ``runs`` and of the runs they delegated to (``undo.runs``): each file that is as they
    left it gets back the copy their first change kept, or is deleted when they created it, and the folders they created
    are removed once empty."""
    family: list[RunRecord] = [member for run in runs for member in await runs_with_children(runtime.store, run)]
    plan: UndoPlan = await asyncio.to_thread(plan_for, runtime.undo_dir, family)
    ready: list[UndoStep] = due_steps(plan.steps)
    await asyncio.to_thread(apply_undo, ready, runtime.undo_dir)
    await asyncio.to_thread(remove_empty_dirs, plan.folders)
    left: tuple[UndoStep, ...] = tuple(step for step in plan.steps if step.state in ("conflict", "cannot"))
    log.info(
        "rewound_files_taken_back",
        run_ids=[run.id for run in runs],
        reverted=[step.path for step in ready],
        left=[step.path for step in left],
    )
    return FilesTakenBack(reverted=tuple(ready), left=left)
