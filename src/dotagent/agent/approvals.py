"""Human-in-the-loop requests: approvals, hand-offs and questions, resolved by the UI or CLI."""

import asyncio
from typing import Literal

from dotagent.core.errors import RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.events import EventBus
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord

log = get_logger(__name__)

# The event of a question a run left for the user and went on without; it is decided later with ``approval_decided``.
QUESTION_LEFT_EVENT: str = "question_left"


class ApprovalOutcome(FrozenModel):
    approval_id: str
    status: Literal["approved", "denied", "expired"]
    decided_by: str
    note: str


def _abandoned(approval_id: str, run_id: str) -> ApprovalOutcome:
    """How a question ends when its run stops waiting for it: the wait was cancelled, or the run is over."""
    return ApprovalOutcome(
        approval_id=approval_id, status="expired", decided_by="system", note=f"run {run_id} stopped waiting"
    )


class ApprovalBroker:
    """Creates approval records, waits for a decision, and wakes the waiting run when one arrives.

    Every record a run waits for ends up decided, whatever ends the wait: a person (through ``resolve``), the deadline,
    or the run itself. ``approval_requested`` is always followed by ``approval_decided``, so no UI keeps offering a
    question that nobody can answer any more. A question a run left (``leave``) holds nothing up: it stays open, after
    the run too, until the user answers or skips it.
    """

    def __init__(self, store: Store, bus: EventBus, timeout_s: float) -> None:
        self._store: Store = store
        self._bus: EventBus = bus
        self._timeout_s: float = timeout_s
        self._waiting: dict[str, asyncio.Future[ApprovalOutcome]] = {}
        self._run_of: dict[str, str] = {}

    async def request(self, run_id: str, tool: str, summary: str, severity: str, args: JsonObject) -> ApprovalOutcome:
        record: ApprovalRecord = await self._store.create_approval(run_id, tool, summary, severity, args)
        future: asyncio.Future[ApprovalOutcome] = asyncio.get_running_loop().create_future()
        self._waiting[record.id] = future
        self._run_of[record.id] = run_id
        # What the question comes to when the wait is cut short from outside, which only a cancellation does; one that
        # comes while the run is being marked blocked is decided too, so the record never stays open (item 116).
        outcome: ApprovalOutcome = _abandoned(record.id, run_id)
        try:
            await self._store.set_run_status(run_id, "blocked")
            await self._bus.emit(
                run_id,
                "approval_requested",
                {"approval_id": record.id, "tool": tool, "summary": summary, "severity": severity, "args": args},
                True,
            )
            outcome = await asyncio.wait_for(future, timeout=self._timeout_s)
        except TimeoutError:
            outcome = ApprovalOutcome(
                approval_id=record.id, status="expired", decided_by="timeout", note="no decision before the deadline"
            )
        finally:
            self._waiting.pop(record.id, None)
            self._run_of.pop(record.id, None)
            # A decision made through ``resolve`` is stored already and stays as it is; the other endings are stored
            # here. The run acts on what is stored, also when the deadline passed while a person's decision was being
            # stored.
            stored: ApprovalRecord = await self._store.decide_approval(
                record.id, outcome.status, outcome.decided_by, outcome.note
            )
            if stored.status in ("approved", "denied") and stored.status != outcome.status:
                outcome = ApprovalOutcome(
                    approval_id=record.id,
                    status="approved" if stored.status == "approved" else "denied",
                    decided_by=stored.decided_by or "",
                    note=stored.note,
                )
            await self._store.set_run_status(run_id, "running")
            await self._bus.emit(
                run_id,
                "approval_decided",
                {"approval_id": record.id, "status": outcome.status, "note": outcome.note},
                True,
            )
        return outcome

    async def record_automatic(
        self,
        run_id: str,
        tool: str,
        summary: str,
        severity: str,
        args: JsonObject,
        status: Literal["approved", "denied"],
        decided_by: str,
        note: str,
    ) -> ApprovalOutcome:
        """Keep the record of a question that was answered without a person (yolo, or the review model).

        Nobody waits for it: the run keeps its status and no ``approval_requested`` is sent, so no screen offers it as a
        question. ``approval_auto`` is the one event, so that what was decided for the user can always be listed.
        """
        record: ApprovalRecord = await self._store.create_decided_approval(
            run_id, tool, summary, severity, args, status, decided_by, note
        )
        await self._bus.emit(
            run_id,
            "approval_auto",
            {
                "approval_id": record.id,
                "tool": tool,
                "summary": summary,
                "severity": severity,
                "args": args,
                "status": status,
                "decided_by": decided_by,
                "note": note,
            },
            True,
        )
        return ApprovalOutcome(approval_id=record.id, status=status, decided_by=decided_by, note=note)

    async def leave(self, run_id: str, tool: str, summary: str, severity: str, args: JsonObject) -> ApprovalRecord:
        """Keep a question that nobody answers during the run: the record stays ``left`` until the user answers or skips
        it, the run keeps its status and goes on, and ``question_left`` is the one event until then."""
        record: ApprovalRecord = await self._store.create_left_approval(run_id, tool, summary, severity, args)
        await self._bus.emit(
            run_id,
            QUESTION_LEFT_EVENT,
            {"approval_id": record.id, "tool": tool, "summary": summary, "severity": severity, "args": args},
            True,
        )
        return record

    async def resolve(self, approval_id: str, approve: bool, decided_by: str, note: str) -> ApprovalRecord:
        """Record a decision. Raises if the approval does not exist or was already decided. A question a run left has
        no waiter, so its ``approval_decided`` is sent here; one a run of another process waits for is taken from the
        store by that process (:meth:`take_stored_decisions`)."""
        record: ApprovalRecord = await self._store.get_approval(approval_id)
        if record.status not in ("pending", "left"):
            raise RunConflict("approval is not open", {"approval_id": approval_id, "status": record.status})
        status: Literal["approved", "denied"] = "approved" if approve else "denied"
        decided: ApprovalRecord = await self._store.decide_approval(approval_id, status, decided_by, note)
        if (decided.status, decided.decided_by, decided.note) != (status, decided_by, note):
            # Another decision was stored between the check above and this one (two tabs, the deadline, the run).
            raise RunConflict("approval was decided meanwhile", {"approval_id": approval_id, "status": decided.status})
        future: asyncio.Future[ApprovalOutcome] | None = self._waiting.get(approval_id)
        if future is not None and not future.done():
            future.set_result(ApprovalOutcome(approval_id=approval_id, status=status, decided_by=decided_by, note=note))
        elif record.status == "left":
            await self._bus.emit(
                record.run_id, "approval_decided", {"approval_id": approval_id, "status": status, "note": note}, True
            )
        else:
            log.info("approval_decided_for_another_process", approval_id=approval_id, run_id=record.run_id)
        return decided

    async def settled(self, approval_id: str) -> None:
        """Return once this process no longer waits for the approval ``approval_id``: it was decided here, through
        another process (:meth:`take_stored_decisions`) or by the deadline; at once for one it does not wait for."""
        future: asyncio.Future[ApprovalOutcome] | None = self._waiting.get(approval_id)
        if future is not None:
            await asyncio.wait({future})

    async def take_stored_decisions(self) -> None:
        """Wake the waits whose question a person decided through another process (the pages of the daemon, for a run
        of a ``dotx`` command), which stored the decision with no wait of its own to wake."""
        for approval_id, future in tuple(self._waiting.items()):
            if future.done():
                continue
            record: ApprovalRecord = await self._store.get_approval(approval_id)
            if record.status in ("approved", "denied") and record.decided_by is not None and not future.done():
                future.set_result(
                    ApprovalOutcome(
                        approval_id=approval_id,
                        status="approved" if record.status == "approved" else "denied",
                        decided_by=record.decided_by,
                        note=record.note,
                    )
                )

    def release_run(self, run_id: str) -> None:
        """Wake waiters of a run that is over so they unwind instead of waiting for the deadline."""
        for approval_id, owner in tuple(self._run_of.items()):
            future: asyncio.Future[ApprovalOutcome] | None = self._waiting.get(approval_id)
            if owner == run_id and future is not None and not future.done():
                future.set_result(_abandoned(approval_id, run_id))
