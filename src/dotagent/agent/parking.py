"""Steps kept for the owner (items 482, 483). A question of the policy that nobody answers within
``approval_timeout_s`` ends as a refusal by default (``[policy] unanswered = "refuse"``). With ``"park"``, a step the
run hands to the owner (money, a credential, a rule of theirs that hands it off) is kept for them instead: the run is
told it did not happen and goes on without it, and the owner's "I did it" later carries the work on in the same
conversation; nothing is done for the owner by it, as a hand-off is theirs to do. A question that asks the owner's yes
is kept too when its card shows all of the step and nothing of it reaches other people (:func:`permit_parkable`): their
"Allow once" carries the work on, and the run may then do that exact step once in that conversation, within
``permit_hours``; the gate decides it again first, so the yes turns a question into a go and nothing else. Pure: the
gate and the manager do the rest."""

import hashlib
from collections.abc import Mapping

from dotagent.agent.declined import declined_key
from dotagent.config import PolicyConfig
from dotagent.core.types import FrozenModel
from dotagent.policy.spend import render_amount
from dotagent.policy.types import ActionInfo, ActionKind, ApprovalMode, Decision, DecisionSource, Flag

# How many kept steps may wait at once: in a chat (or the chats of one schedule's firings), and in all. Past them a
# question nobody answers is refused as before: a page that set off hand-off after hand-off fills no list of cards.
KEPT_HERE: int = 20
KEPT_OVERALL: int = 50
# Who let a step through on the owner's yes given when it was kept for them (item 483).
PERMIT_DECIDER: str = "permit"
# The tools whose question may wait for a yes given later, each with the kind of its steps: a command (where it runs on
# its card when the call names it), a file read and a folder listing, a page read and a web search, whose cards show all
# of the step. Every other tool is left out: a page, a screen, a terminal session or an application's window will not be
# as the card showed them when the owner answers, and a message, an MCP call, work handed to another run, a search's
# pattern, an extract's sources, a schedule's chat or a note's tags are more than its card says.
_PERMIT_TOOLS: Mapping[str, ActionKind] = {
    "shell": "shell",
    "read_file": "read",
    "list_dir": "read",
    "web_fetch": "network",
    "web_search": "network",
}
# What a yes given later never covers: a step that reaches other people, or takes data off the Mac.
_UNPERMITTED_FLAGS: frozenset[Flag] = frozenset(
    {"external_send", "message_send", "public_post", "social", "upload", "exfiltration"}
)
# The questions a yes given later never answers: the one the run's untrusted reading raised (the policy's stop for an
# injection), and the one about Dot's own files, which the owner answers as it happens.
_UNPERMITTED_SOURCES: frozenset[DecisionSource] = frozenset({"taint", "own_files"})


class Parking(FrozenModel):
    """Where a run keeps the steps nobody answers (items 482, 484): the conversation they belong to (``thread_id``: the
    run's chat, or the campaign's conversation for a session of a campaign, where the owner talks to it), the campaign
    whose sessions share them (None for a chat), the schedule whose chat it is (None for none), and how long a kept
    step waits, in seconds. The same step is kept once there, and the owner's yes and Skip hold there."""

    thread_id: str
    campaign_id: str | None
    schedule_id: str | None
    keep_s: float


def parking_for(
    policy: PolicyConfig,
    approval_mode: ApprovalMode,
    delegated: bool,
    evaluated: bool,
    conversation_id: str,
    campaign_id: str | None,
    schedule_id: str | None,
) -> Parking | None:
    """The run's parking in the conversation ``conversation_id`` (of the campaign ``campaign_id`` for a session), or
    None where a question nobody answers ends as a refusal: ``unanswered`` is ``"refuse"``; the run is a yolo one, which
    asks nothing; a delegated run, whose parent decides; or an eval's."""
    if policy.unanswered != "park" or approval_mode == "yolo" or delegated or evaluated:
        return None
    return Parking(
        thread_id=conversation_id,
        campaign_id=campaign_id,
        schedule_id=schedule_id,
        keep_s=policy.parked_hours * 3600.0,
    )


def kept_digest(info: ActionInfo) -> str | None:
    """The fingerprint of an action, which keeps the same step once in a chat: its tool, kind, summary, target, amount
    and flags, exactly as its card showed them, with the files it changes and the application it opens; None for a step
    whose card shows less than all of it (:func:`dotagent.agent.declined.declined_key`), which is kept anew each time,
    since two such steps may not be one."""
    if declined_key(info) is None:
        return None
    amount: str = "" if info.amount is None else render_amount(info.amount)
    parts: str = "\n".join(
        (
            info.tool,
            info.kind,
            info.summary,
            info.target,
            amount,
            ",".join(sorted(info.flags)),
            ",".join(info.changes),
            info.bundle or "",
        )
    )
    return hashlib.blake2b(parts.encode(), digest_size=16).hexdigest()


def permit_parkable(info: ActionInfo, decision: Decision) -> bool:
    """Whether a question of the policy about ``info`` may be kept for the owner's yes (item 483): a question (not a
    hand-off, which :func:`kept_text` keeps, nor a refusal), not raised by what the run read nor about Dot's own files,
    of a tool whose card the owner can weigh later (:data:`_PERMIT_TOOLS`), reaching nobody, and whose card shows all of
    the step."""
    return (
        decision.verdict == "ask"
        and decision.source not in _UNPERMITTED_SOURCES
        and _PERMIT_TOOLS.get(info.tool) == info.kind
        and not _UNPERMITTED_FLAGS.intersection(info.flags)
        and kept_digest(info) is not None
    )


def kept_text(approval_id: str, created: bool) -> str:
    """What the run is told of a step kept for the owner to do: it did not happen, and waits for them."""
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


def kept_ask_text(approval_id: str, created: bool) -> str:
    """What the run is told of a step kept for the owner's yes (item 483): it did not happen, and waits for them."""
    if not created:
        return (
            f"This step waits for the user's yes already ({approval_id}): it did not happen. Do not try it again or "
            "another way round it; go on with what does not depend on it, or end the run and say what waits for the "
            "user."
        )
    return (
        f"Nobody answered in time, so this step was kept for the user to allow ({approval_id}): it did not happen. Do "
        "not try it again or another way round it. Go on with what does not depend on it; if nothing can, end the run "
        "and say exactly what waits for the user. If they allow it, the work goes on in this conversation, where this "
        "exact step may then be done once."
    )


def allowed_text(approval_id: str, note: str) -> str:
    """What the run is told when the owner allowed a step kept for their yes (item 483): Dot's words, as
    :func:`handed_back_text`'s are; the step may be done once, as it was."""
    said: str = "" if note.strip() == "" else f" They wrote: {note.strip()}"
    return (
        f"The user allowed the step that waited for their yes ({approval_id}): do it now, exactly as it was, once; "
        f"it is not allowed again.{said}"
    )


def skipped_text(approval_id: str) -> str:
    """What the run is told of a step the owner skipped when it was kept for them (item 483), asked again in the same
    conversation before they wrote there again."""
    return (
        f"the user skipped this exact step when it was kept for them ({approval_id}); it stays skipped in this "
        "conversation until they write here again: carry on with what is allowed, ask the user with ask_user if you "
        "need them, or finish and say what is missing"
    )


def handed_back_text(approval_id: str, note: str) -> str:
    """What the run is told when the owner did a step kept for them themselves: Dot's words, not the owner's, so it
    clears no taint (as an "I did it" during the run does not), and only the ``note`` they wrote is an instruction."""
    said: str = "" if note.strip() == "" else f" They wrote: {note.strip()}"
    return f"The user did the step that waited for them ({approval_id}) themselves; go on from there.{said}"
