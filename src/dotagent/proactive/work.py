"""The owner's objectives and the work orders that serve them, as the runs that read Dot's record of itself get them
(items 907 and 908): the look for work on Dot's own initiative (:mod:`dotagent.proactive.initiative`) and the morning
brief (:mod:`dotagent.proactive.brief`). What a run wrote after it read content third parties can author is counted and
not quoted, as a thing on Dot's list is (``initiative._listed``): the title of an objective or an order is a model's
words, and a page could have put its own into them."""

import time
from collections.abc import Sequence

from dotagent.agent.orders import waiting_too_long
from dotagent.proactive.text import quoted
from dotagent.store.db import Store
from dotagent.store.models import OPEN_WORK_ORDERS, ObjectiveRecord, WorkOrderRecord

_READ: int = 50
_SHOWN: int = 8
_TITLE_CHARS: int = 120
_MEASURE_CHARS: int = 200
_NOTE_CHARS: int = 160
_STATE_WORDS: dict[str, str] = {
    "assigned": "being handed out",
    "working": "at work",
    "in_review": "waiting for review",
    "returned": "sent back",
    "escalated": "waiting for the owner",
}


def _clean(objective: ObjectiveRecord) -> bool:
    """Whether every word of the objective, its notes too, was written by a run that read nothing third parties can
    author."""
    return not objective.tainted and not any(note.tainted for note in objective.notes)


def _order_line(order: WorkOrderRecord, names: dict[str, str]) -> str:
    agent: str = quoted(names.get(order.agent_id, "an agent that is gone"), 60)
    return f"  - {_STATE_WORDS.get(order.status, order.status)}: {quoted(order.title, _TITLE_CHARS)} ({agent})"


async def objective_lines(store: Store) -> list[str]:
    """The active objectives, each with how progress is judged, its newest line of progress and its open work orders;
    one a run wrote after reading outside content is counted, not quoted."""
    active: list[ObjectiveRecord] = await store.list_objectives(("active",), _READ)
    if not active:
        return ["The owner's objectives (what they want moved forward over time): none written down."]
    orders: list[WorkOrderRecord] = await store.list_work_orders(OPEN_WORK_ORDERS, _READ * 4)
    names: dict[str, str] = {agent.id: agent.name for agent in await store.list_agents()}
    lines: list[str] = [f"The owner's objectives, as Dot wrote them down (active: {len(active)}):"]
    for objective in active[:_SHOWN]:
        if not _clean(objective):
            lines.append("- one that came of content third parties wrote, not quoted here")
            continue
        newest: str = f"; newest progress: {quoted(objective.notes[-1].text, _NOTE_CHARS)}" if objective.notes else ""
        lines.append(
            f"- {quoted(objective.title, _TITLE_CHARS)}; judged by: {quoted(objective.measure, _MEASURE_CHARS)}{newest}"
        )
        serving: list[WorkOrderRecord] = [order for order in orders if order.objective_id == objective.id]
        unquoted: int = sum(1 for order in serving if order.tainted)
        lines.extend(_order_line(order, names) for order in serving if not order.tainted)
        if unquoted:
            lines.append(f"  - and {unquoted} more work orders, not quoted: they came of content third parties wrote")
    if len(active) > _SHOWN:
        lines.append(f"- and {len(active) - _SHOWN} more")
    return lines


async def order_lines(store: Store, now: float | None = None) -> list[str]:
    """The work orders that wait: for the lead's review (those for more than a day first), and for the owner
    (escalated). One a run wrote after reading outside content is counted, not quoted."""
    moment: float = time.time() if now is None else now
    orders: list[WorkOrderRecord] = await store.list_work_orders(("in_review", "escalated"), _READ)
    if not orders:
        return ["Work orders waiting for review or for the owner: none."]
    names: dict[str, str] = {agent.id: agent.name for agent in await store.list_agents()}
    late: list[WorkOrderRecord] = [order for order in orders if waiting_too_long(order, moment)]
    lines: list[str] = [
        f"Work orders waiting for review or for the owner ({len(orders)}; {len(late)} for review for more than a day):"
    ]
    ranked: Sequence[WorkOrderRecord] = [
        *late,
        *(order for order in orders if order not in late and order.status == "escalated"),
        *(order for order in orders if order not in late and order.status != "escalated"),
    ]
    shown: list[WorkOrderRecord] = [order for order in ranked if not order.tainted][:_SHOWN]
    lines.extend(_order_line(order, names).removeprefix("  ") for order in shown)
    unquoted: int = sum(1 for order in orders if order.tainted)
    if unquoted:
        lines.append(f"- and {unquoted} more, not quoted: they came of content third parties wrote")
    return lines
