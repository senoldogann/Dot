"""Steps kept for the owner (item 482). A question of the policy that nobody answers within ``approval_timeout_s`` ends
as a refusal by default (``[policy] unanswered = "refuse"``). With ``"park"``, a step the run hands to the owner (money,
a credential, a rule of theirs that hands it off) is kept for them instead: the run is told it did not happen and goes
on without it, and the owner's "I did it" later carries the work on in the same conversation. Nothing is ever done for
the owner by it: a hand-off is theirs to do. Pure: the gate and the manager do the rest."""

import hashlib

from dotagent.config import PolicyConfig
from dotagent.core.types import FrozenModel
from dotagent.policy.spend import render_amount
from dotagent.policy.types import ActionInfo, ApprovalMode

# How many kept steps may wait at once: in a chat (or the chats of one schedule's firings), and in all. Past them a
# question nobody answers is refused as before: a page that set off hand-off after hand-off fills no list of cards.
KEPT_HERE: int = 20
KEPT_OVERALL: int = 50


class Parking(FrozenModel):
    """Where a run keeps the steps nobody answers (item 482): its chat, the schedule whose chat it is (None for none),
    and how long a kept step waits, in seconds."""

    thread_id: str
    schedule_id: str | None
    keep_s: float


def parking_for(
    policy: PolicyConfig,
    approval_mode: ApprovalMode,
    delegated: bool,
    session: bool,
    evaluated: bool,
    thread_id: str,
    schedule_id: str | None,
) -> Parking | None:
    """The run's parking, or None where a question nobody answers ends as a refusal: ``unanswered`` is ``"refuse"``; the
    run is a yolo one, which asks nothing; a delegated run, whose parent decides; a session of a campaign (its questions
    wait for the next session); or an eval's."""
    if policy.unanswered != "park" or approval_mode == "yolo" or delegated or session or evaluated:
        return None
    return Parking(thread_id=thread_id, schedule_id=schedule_id, keep_s=policy.parked_hours * 3600.0)


def kept_digest(info: ActionInfo) -> str:
    """The fingerprint of an action, which keeps the same step once in a chat: its tool, kind, summary, target, amount
    and flags, exactly as its card showed them."""
    amount: str = "" if info.amount is None else render_amount(info.amount)
    parts: str = "\n".join((info.tool, info.kind, info.summary, info.target, amount, ",".join(sorted(info.flags))))
    return hashlib.blake2b(parts.encode(), digest_size=16).hexdigest()


def kept_text(approval_id: str, created: bool) -> str:
    """What the run is told of a step kept for the owner: it did not happen, and waits for them."""
    if not created:
        return (
            f"This step waits for the user already ({approval_id}): it did not happen. Do not try it again or another "
            "way round it; go on with what does not depend on it, or end the run and say what waits for the user."
        )
    return (
        f"Nobody answered in time, so this step was kept for the user ({approval_id}): it did not happen, and it waits "
        "for them to do it. Do not try it again or another way round it. Go on with what does not depend on it; if "
        "nothing can, end the run and say exactly what waits for the user. When they say they did it, the work goes on "
        "in this conversation."
    )


def handed_back_text(approval_id: str, note: str) -> str:
    """What the run is told when the owner did a step kept for them themselves: Dot's words, not the owner's, so it
    neither clears the run's taint nor becomes an instruction (as an "I did it" during the run does neither)."""
    said: str = "" if note.strip() == "" else f" They wrote: {note.strip()}"
    return f"The user did the step that waited for them ({approval_id}) themselves; go on from there.{said}"
