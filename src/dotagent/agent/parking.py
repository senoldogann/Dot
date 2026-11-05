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

from pydantic import JsonValue

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
# Of them, the ones whose send is kept but never exact (item 922 keeps item 483's line: a later yes does not answer
# it by itself, the run it reaches asks for the step).
UNKEPT_SOURCES: frozenset[DecisionSource] = _UNPERMITTED_SOURCES


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
    evaluated: bool,
    conversation_id: str,
    campaign_id: str | None,
    schedule_id: str | None,
) -> Parking | None:
    """The parking of a run nobody delegated in the conversation ``conversation_id`` (of the campaign ``campaign_id``
    for a session), or None where a question nobody answers ends as a refusal: ``unanswered`` is ``"refuse"``; the run
    is a yolo one, which asks nothing; or an eval's. A delegated run keeps its steps where the run that delegated it
    does (item 485)."""
    if policy.unanswered != "park" or approval_mode == "yolo" or evaluated:
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
            # What the step runs, whole, as the card shows it (a page's script, item 985).
            *((info.body,) if info.body else ()),
        )
    )
    return hashlib.blake2b(parts.encode(), digest_size=16).hexdigest()


# What a run of a chat Dot opened on its own initiative keeps for the owner's yes rather than wait for it (item 922): a
# message sent for them, a post in their name, a project's change put live (the steps of item 850). Nobody is there to
# answer while it works: the step is left as a card, its draft kept, and done once the owner says yes.
SEND_FLAGS: frozenset[Flag] = frozenset({"message_send", "public_post", "release"})
# How long such a step waits for the owner's yes: the longest a kept step may (``[policy] parked_hours`` at most 30
# days), for the owner's word of 2026-11-02 is that their answer has no time limit.
SEND_KEEP_S: float = 30 * 24 * 3600.0


# The marks a send kept for the owner's yes carries on its card (item 922): that it is one, and whether its fingerprint
# is the step exactly as the card shows it (:func:`kept_digest`), which their yes then lets through once.
KEPT_SEND_KEY: str = "kept_send"
# The tools whose step its card shows whole, so that a later run can repeat it exactly: a command of the shell (the
# folder it runs in is in its summary when the call names one, the workspace else) and an application's script. Any
# other is not, a page's or a screen's step (its references are new), nor a terminal's, whose card names a session by a
# number that a restart gives again to another folder or program (the independent review of item 922).
_EXACT_TOOLS: frozenset[str] = frozenset({"shell", "apple_script"})
EXACT_KEY: str = "exact"


def kept_send_digest(info: ActionInfo) -> tuple[str, bool]:
    """The fingerprint a send kept for the owner's yes is kept under (item 922), and whether it is the step exactly as
    its card shows it: :func:`kept_digest`, of a tool whose step a later run can repeat (:data:`_EXACT_TOOLS`); or, for
    any other step and one whose card shows less than all of it, its tool and what of it reaches others, which keeps
    one such step of a kind waiting in a chat and which no yes lets through."""
    exact: str | None = kept_digest(info)
    if exact is not None and info.tool in _EXACT_TOOLS:
        return exact, True
    parts: str = "\n".join(("send", info.tool, ",".join(sorted(SEND_FLAGS.intersection(info.flags)))))
    return hashlib.blake2b(parts.encode(), digest_size=16).hexdigest(), False


def is_kept_send(args: Mapping[str, JsonValue]) -> bool:
    """Whether a kept step is a send kept for the owner's yes (item 922), by the mark its card carries."""
    return args.get(KEPT_SEND_KEY) is True


def send_parking(conversation_id: str, campaign_id: str | None, schedule_id: str | None) -> Parking:
    """Where a run of a chat Dot opened on its own initiative keeps its sends for the owner's yes (item 922), whatever
    ``[policy] unanswered`` says: its conversation, for :data:`SEND_KEEP_S`."""
    return Parking(thread_id=conversation_id, campaign_id=campaign_id, schedule_id=schedule_id, keep_s=SEND_KEEP_S)


def kept_send_text(approval_id: str, created: bool) -> str:
    """What a run is told of a send kept for the owner's yes (item 922): it did not happen; its draft stays."""
    waits: str = (
        "is kept for the user's yes"
        if created
        else "joins a card that waits for the user's yes already, of a send of this kind in this chat, which may be "
        "of another draft"
    )
    return (
        f"This step {waits} ({approval_id}): it did not happen, and nobody is asked now. Keep what you wrote as a "
        "draft, as it is to go out (a mail saved in Drafts, a post or a release left ready), and do not send or "
        "publish it another way. Go on with what does not depend on it, then end the run and say where each draft is, "
        "so that the user's yes finds it. When the user says yes, the work goes on in this conversation, and it is "
        "done then."
    )


def allowed_send_text(approval_id: str, summary: str, note: str, exact: bool) -> str:
    """What the run the owner's yes starts is told of a send kept for it (item 922): Dot's words. A step its card
    showed exactly goes through once, done exactly so; any other asks the owner once more, as its page is new."""
    said: str = "" if note.strip() == "" else f" They wrote: {note.strip()}"
    how: str = (
        "Do it now, exactly as the card showed it, once: it goes through without asking."
        if exact
        else "Open the drafts kept for it (the mails in Drafts, the posts or the releases left ready, as the answers "
        "of this conversation name them) and do each as it stands: the step itself asks them once more, as its page is "
        "new."
    )
    return (
        f"The user said yes to the step that waited for them ({approval_id}): {summary}. {how} Send or publish "
        f"nothing else.{said}"
    )


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
