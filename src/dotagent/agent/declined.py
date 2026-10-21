"""A no is not asked again in the same run (item 456): when the owner refuses an action the policy asked about, the
gate keeps the refusal, and the same action, as its card showed it, is refused at once until the owner writes again (a
steer, the answer to a question). It replaces a question and nothing else: an action the policy allows runs as before,
and in yolo, where nothing is asked, nothing is kept (item 443). The kept refusals live with the run's gate, so a run
resumed or delegated starts with none."""

from collections.abc import Mapping

from dotagent.core.types import FrozenModel
from dotagent.policy.spend import render_amount
from dotagent.policy.types import ActionInfo, ActionKind

# Kinds whose summary says where something is written or typed but not what (a path, a field and the length of the
# text): a no to one text there is no answer about another, so they are never kept.
_UNBOUND_KINDS: frozenset[ActionKind] = frozenset({"write", "browser_input", "desktop_input"})


class Declined(FrozenModel):
    """The owner's no to an action: the note they gave, and how many of their messages the run had when they gave it."""

    note: str
    spoken: int


def declined_key(info: ActionInfo) -> str | None:
    """The action as its card showed it, exactly (a browser element keeps its ref while its role and name stay the
    same, so another Reply button is another action); None for a kind that says not what it writes."""
    if info.kind in _UNBOUND_KINDS:
        return None
    amount: str = "" if info.amount is None else render_amount(info.amount)
    return "\n".join((info.tool, info.summary, info.target, amount))


def still_declined(declined: Mapping[str, Declined], key: str | None, spoken: int) -> Declined | None:
    """The owner's no to the action ``key`` while they have written nothing since (``spoken`` is the count now)."""
    if key is None:
        return None
    earlier: Declined | None = declined.get(key)
    return earlier if earlier is not None and earlier.spoken == spoken else None


def refusal_text(earlier: Declined) -> str:
    """What the run is told of an action the owner already refused: that it stays refused, and what it can do."""
    said: str = "" if earlier.note == "" else f", with the note “{earlier.note}”"
    return (
        f"the user declined this exact action earlier in this run{said}; it stays declined until the user writes "
        "again: carry on with what is allowed, ask the user with ask_user if you need them, or finish and say what is "
        "missing"
    )
