"""What a step of a run leaves in the store, made by a test with the times it chooses (item 572): the record of the
step that ended, which the executor writes when the step is over, and the approval of it, which the gate stores before
the step goes ahead. The iMessage channel reads them to tell which rows a run may have written."""

from dotagent.core.ids import new_id
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, ApprovalStatus

# What the guard raises for a step that sends a message.
SENDS: tuple[str, ...] = ("external_send", "message_send")


async def step_ended(
    store: Store,
    run_id: str,
    *,
    at: float,
    seconds: float = 2.0,
    flags: tuple[str, ...] = SENDS,
    tool: str = "apple_script",
    text: str = "",
) -> None:
    """The ``tool_result`` event of a step that took ``seconds`` and ended at ``at``, with the flags its action
    raised."""
    await store._insert_event(
        run_id, at, "tool_result", {"tool": tool, "ok": True, "text": text, "seconds": seconds, "flags": list(flags)}
    )
    await store._db.commit()


async def step_answered(
    store: Store,
    run_id: str,
    *,
    at: float,
    flags: tuple[str, ...] = SENDS,
    status: ApprovalStatus = "approved",
    tool: str = "apple_script",
) -> None:
    """The approval of a step, decided at ``at`` (by the owner, the review model or yolo), as it is stored before the
    step goes ahead."""
    await store._insert_approval(
        ApprovalRecord(
            id=new_id("apr"),
            run_id=run_id,
            created_at=at,
            decided_at=None if status == "pending" else at,
            status=status,
            tool=tool,
            summary="Send a message",
            severity="high",
            args={"tool": tool, "flags": list(flags)},
            decided_by=None if status == "pending" else "reviewer",
            note="",
            digest=None,
            kept_until=None,
            permit_until=None,
            used_at=None,
            scope_thread_id=None,
            scope_campaign_id=None,
        )
    )


async def run_closed(store: Store, run_id: str, *, at: float) -> None:
    """The ``run_finished`` event of a run that was cut off with its daemon and closed when the daemon started again,
    which carries the time of the run's last sign of life."""
    await store._insert_event(run_id, at, "run_finished", {"status": "failed", "result": "Dot was stopped"})
    await store._db.commit()
