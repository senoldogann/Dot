"""The Actions panel of a run's page: its acting steps from its events, and its file changes from the undo plan."""

from collections import Counter
from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from dotagent.agent.authorization import StepDecision
from dotagent.core.text import truncate_middle
from dotagent.policy.types import READ_ONLY_KINDS
from dotagent.server.views import ActionStep, FileEdit, OutsideCategory, OutsideSteps
from dotagent.store.models import EventRecord, RanStep
from dotagent.undo.restore import UndoStep

_EVIDENCE_CHARS: int = 400
# The risks of a call recorded before the event carried the kind of its action: those above "safe" acted.
_ACTING_RISKS: frozenset[str] = frozenset({"low", "medium", "high"})


def _text(data: Mapping[str, JsonValue], key: str) -> str:
    value: object = data.get(key, "")
    return value if isinstance(value, str) else ""


def _acted(data: Mapping[str, JsonValue]) -> bool:
    """Whether a tool result is of a call that changed something: its kind is not read-only (for an event recorded
    before the kind was carried, its risk is above safe). A call without an action (an unknown tool, bad arguments)
    is left out."""
    kind: str = _text(data, "kind")
    if kind:
        return kind not in READ_ONLY_KINDS
    return _text(data, "risk") in _ACTING_RISKS


def _decision_of(data: Mapping[str, JsonValue]) -> StepDecision | None:
    """What the policy decided of a step, as its result recorded it (item 468); ``None`` for a result from before,
    never a guess."""
    raw: object = data.get("decision")
    return None if raw is None else StepDecision.model_validate(raw)


def _flags_of(data: Mapping[str, JsonValue]) -> list[str]:
    raw: object = data.get("flags")
    return [flag for flag in raw if isinstance(flag, str)] if isinstance(raw, list) else []


def action_steps(events: Sequence[EventRecord]) -> list[ActionStep]:
    """The acting steps among the tool results of a run, in their order."""
    steps: list[ActionStep] = []
    for event in events:
        if event.kind != "tool_result" or not _acted(event.data):
            continue
        steps.append(
            ActionStep(
                event_id=event.id,
                at=event.ts,
                tool=_text(event.data, "tool"),
                channel=_text(event.data, "channel"),
                kind=_text(event.data, "kind"),
                target=_text(event.data, "target") or _text(event.data, "site"),
                summary=_text(event.data, "summary"),
                ok=event.data.get("ok") is True,
                evidence=truncate_middle(_text(event.data, "text"), _EVIDENCE_CHARS),
                flags=_flags_of(event.data),
                decision=_decision_of(event.data),
            )
        )
    return steps


# The kinds of action that act through a tool whose effect the undo journal does not hold, by the category the owner
# reads (item 1404). Opening a page reads, and what Dot keeps in its own memory or schedules is no step on the world, so
# neither is counted; the journal holds `write_file`, `edit_file` and `write_notes`.
_OUTSIDE_KINDS: dict[str, OutsideCategory] = {
    "shell": "shell",
    "browser_input": "browser",
    "browser_click": "browser",
    "desktop_input": "desktop",
    "app_control": "desktop",
    "mcp": "app",
}
_MESSAGE_FLAG: str = "message_send"
_OUTSIDE_ORDER: tuple[OutsideCategory, ...] = ("shell", "browser", "desktop", "message", "app")


def outside_steps(ran: Sequence[RanStep]) -> list[OutsideSteps]:
    """The steps of ``ran`` that acted through a tool with no inverse in the undo journal, counted by category, only the
    categories that have any, in a fixed order (item 1404). A step that sends a message or mail (its action raised
    ``message_send``) is a message, whatever tool it went through; a batched call counts the steps it carried. This says
    what ran, never what it did."""
    counts: Counter[OutsideCategory] = Counter()
    for step in ran:
        category: OutsideCategory | None = "message" if _MESSAGE_FLAG in step.flags else _OUTSIDE_KINDS.get(step.kind)
        if category is not None:
            counts[category] += step.steps
    return [OutsideSteps(category=category, steps=counts[category]) for category in _OUTSIDE_ORDER if counts[category]]


def file_edits(steps: Sequence[UndoStep]) -> list[FileEdit]:
    """The undo plan as the panel shows it, in the plan's order (the path changed last first)."""
    return [
        FileEdit(
            path=step.path,
            state=step.state,
            action=step.action,
            reason=step.reason,
            tool=step.origin.entry.tool,
            run_id=step.origin.run_id,
            noted_at=step.origin.entry.ts,
            changes=step.changes,
        )
        for step in steps
    ]
