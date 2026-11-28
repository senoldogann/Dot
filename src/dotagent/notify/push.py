"""Web push to the owner's phone (item 52): the VAPID key pair the daemon signs with, the message an event turns into,
the send to a subscription's push service, and the watcher that sends every subscription what the web UI's desktop
notifications say: an approval or a question a run asked or left, a draft written for the owner to send, the end of a
run, a campaign that ended by itself, a firing a schedule missed, a start of the daemon after the one before it did not
end cleanly, and what Dot tells the owner on its own: the morning brief and the watchers (``notify/owner.py``). The
subscriptions come
from the web UI (``POST /api/push/subscriptions``, which refuses keys that cannot encrypt a message) and live in the
store; one whose push service says it is gone, or whose keys cannot encrypt a message, is deleted."""

import asyncio
import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import structlog
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid, b64urlencode

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.campaign import CAMPAIGN_ENDED_EVENT, CampaignEnded
from dotagent.agent.learning_health import LEARNING_STALLED_EVENT, LearningStalled
from dotagent.core.errors import DotError
from dotagent.core.types import FrozenModel
from dotagent.drafts.service import DRAFT_CREATED_EVENT
from dotagent.events import DAEMON_RESTARTED_EVENT, SYSTEM_RUN_ID, DaemonRestarted, Subscription
from dotagent.notify.hosts import push_host_problem
from dotagent.notify.keys import keys_problem
from dotagent.notify.owner import OWNER_TOLD_EVENT, OwnerTold
from dotagent.scheduler import (
    SCHEDULE_MISSED_EVENT,
    SCHEDULE_RUN_ENDED_EVENT,
    SCHEDULE_SOURCE,
    SCHEDULE_STOPPED_EVENT,
    ScheduleMissed,
    ScheduleRunEnded,
    ScheduleStopped,
)
from dotagent.store.db import Store
from dotagent.store.models import DraftRecord, EventRecord, PushSubscription, RunRecord

log = structlog.get_logger(__name__)

# The claim a push service sees in the signed header: a way to reach whoever runs the sender, as an https: address or a
# mailto: one (RFC 8292). Apple's service refuses a subject it cannot reach (403 BadJwtToken): "mailto:dot@localhost"
# had every push to an iPhone refused (item 992; 253 of 253 in the owner's log), so it is the project's own page.
SUBJECT: str = "https://github.com/senoldogann/Dot"
# py_vapid's own check of the subject is narrower than RFC 8292: it takes an https: address without a path only, and
# refused the project's page with ``VapidException`` before any push was sent (item 1019). Its "no-strict" setting asks
# only that there is a subject.
_SIGNING: dict[str, bool] = {"no-strict": True}
# How much of a refusing push service's answer is kept: Apple says why in it ({"reason": "BadJwtToken"}).
_ANSWER_CHARS: int = 300
_TTL_S: int = 600
_SEND_TIMEOUT_S: float = 10.0
_WAIT_S: float = 1.0
_BODY_CHARS: int = 140
_PRIVATE_MODE: int = stat.S_IRUSR | stat.S_IWUSR
_GONE: frozenset[int] = frozenset({404, 410})
# What a push service answers when it is busy or failing, not taking the message: it is asked once more (R13).
_ASKED_AGAIN: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_AGAIN_AFTER_S: float = 1.0
_ASKED_KINDS: frozenset[str] = frozenset({"approval_requested", QUESTION_LEFT_EVENT})
_ASKING_TITLE: dict[str, str] = {"question": "Dot has a question", "handoff": "Dot needs your help"}
# The title of the end of a run that is done as far as it can go and waits on the owner (item 2070).
WAITING_TITLE: str = "Dot is waiting for you"
_FINISHED_TITLE: dict[str, str] = {
    "done": "A task is done",
    "failed": "A task failed",
    "cancelled": "A task was stopped",
}
_DRAFT_TITLE: dict[str, str] = {"mail": "Dot drafted an email", "message": "Dot drafted a message"}
type PushState = Literal["idle", "waiting", "done", "failed"]
# What sending one message to one subscription came to: taken, refused or unreachable (kept), or deleted.
type PushOutcome = Literal["sent", "failed", "removed"]
_FINISHED_STATE: dict[str, PushState] = {"done": "done", "failed": "failed"}
type PushGroup = Literal["needs_you", "ready", "done"]
# The three kinds of push the owner switches, each phone for itself (item 1421), and which kind each event of the pushes
# below is told as. A new kind of push names its group here, which is how it joins ``PUSH_KINDS``.
PUSH_GROUPS: tuple[PushGroup, ...] = ("needs_you", "ready", "done")
_GROUP_OF: dict[str, PushGroup] = {
    # A run waits on the owner: an approval, a question, a secret or a hand-off (each is a card of
    # ``approval_requested``, or of ``question_left`` once the run went on without an answer).
    **{kind: "needs_you" for kind in _ASKED_KINDS},
    # Dot's own work broke, which the owner is to look at: a schedule that did not run or was switched off, learning
    # that stopped, a restart after an unexpected stop. They remain in their own group so they can be switched
    # independently of task ends. DotNext's APNs topic takes only task alerts.
    SCHEDULE_MISSED_EVENT: "needs_you",
    SCHEDULE_STOPPED_EVENT: "needs_you",
    DAEMON_RESTARTED_EVENT: "needs_you",
    LEARNING_STALLED_EVENT: "needs_you",
    # Something is ready for the owner to look over or hand over: a draft written for them to send, and what Dot tells
    # them on its own (the morning brief, what a watcher found).
    DRAFT_CREATED_EVENT: "ready",
    OWNER_TOLD_EVENT: "ready",
    # Something finished or failed: a run, a schedule's run (told at its schedule's level, ``scheduler.notices``) and
    # a campaign.
    "run_finished": "done",
    CAMPAIGN_ENDED_EVENT: "done",
    SCHEDULE_RUN_ENDED_EVENT: "done",
}
PUSH_KINDS: frozenset[str] = frozenset(_GROUP_OF)


def push_group(kind: str) -> PushGroup | None:
    """The kind of push an event of ``kind`` is told as (item 1421), ``None`` for an event that is no push."""
    return _GROUP_OF.get(kind)


# The pushes whose subject the iPhone app shows itself while it is open: the end of a run, of a schedule's run and of a
# campaign. While the app says it shows Dot (item 1420) these are not sent to it as alerts. Every other push is: what
# asks something of the owner, a draft, what Dot tells the owner, and the warnings of Dot itself (a schedule that did
# not run or was switched off, learning that stopped, a restart), which the app shows nowhere.
QUIET_WHILE_SHOWN: frozenset[str] = frozenset({"run_finished", CAMPAIGN_ENDED_EVENT, SCHEDULE_RUN_ENDED_EVENT})


def quiet_while_shown(kind: str) -> bool:
    """Whether the alert of an event of ``kind`` is held back from a phone whose app shows Dot (item 1420)."""
    return kind in QUIET_WHILE_SHOWN


class PushError(DotError):
    """A push service refused or could not be reached."""


class PushKeys(FrozenModel):
    """The daemon's VAPID key pair: the private key as PEM (never logged), the public key as the base64url of its
    uncompressed point, which is what a page gives ``PushManager.subscribe`` as ``applicationServerKey``."""

    private_pem: str
    public_key: str


class PushMessage(FrozenModel):
    """What the phone shows: the same words as the web UI's desktop notifications, and the route a tap opens."""

    title: str
    body: str
    route: str
    state: PushState
    title_tr: str | None = None
    body_tr: str | None = None


class PushTotals(FrozenModel):
    """What sending one event came to (the code review's Y15): the subscriptions that took it, the ones whose push
    service refused it or could not be reached (logged and kept), and the ones deleted as gone or unusable."""

    sent: int
    failed: int
    removed: int


def _keys_of(vapid: Vapid) -> PushKeys:
    point: bytes = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return PushKeys(private_pem=vapid.private_pem().decode("ascii"), public_key=b64urlencode(point))


def read_or_create_keys(path: Path) -> PushKeys:
    """The key pair at ``path``, made on first use and kept readable by the owner alone."""
    vapid: Vapid = Vapid.from_file(str(path))
    os.chmod(path, _PRIVATE_MODE)
    return _keys_of(vapid)


def _shorten(text: str) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= _BODY_CHARS else f"{flat[: _BODY_CHARS - 1]}…"


def _first_line(text: str) -> str:
    """The first line of ``text`` that is not blank; nothing when there is none."""
    return next((line for line in text.splitlines() if line.strip()), "")


def _cut_off(runs: int) -> str:
    if runs == 0:
        return "No task was cut off."
    return "1 task was cut off." if runs == 1 else f"{runs} tasks were cut off."


def finished_message(status: str, result: str, run_id: str) -> PushMessage:
    """The end of a scheduled run: how it ended and the first line of its answer, at the schedule's own notice level."""
    return PushMessage(
        title=_FINISHED_TITLE.get(status, "A task has ended"),
        body=_shorten(_first_line(result)),
        route=f"#/runs/{run_id}",
        state=_FINISHED_STATE.get(status, "idle"),
    )


# These fixed task notices have the same words across APNs, Web Push and desktop notifications. The detailed
# answer or approval stays in its chat. The English strings are also DotNext's APNs localization keys.
_TASK_NOTICE_TR: dict[str, str] = {
    "Dot has a question": "Dot'un bir sorusu var",
    "Dot needs your help": "Dot'un yardımınıza ihtiyacı var",
    "Dot asks for approval": "Dot onay istiyor",
    "A task is done": "Bir görev bitti",
    "A task failed": "Bir görev başarısız oldu",
    "A task is blocked": "Bir görev ilerleyemedi",
    WAITING_TITLE: "Dot sizi bekliyor",
}
_TASK_NOTICE_BODY: str = "Open Dot to see it."
_TASK_NOTICE_BODY_TR: str = "Görmek için Dot'u açın."


def task_notice(title: str, run_id: str, state: PushState) -> PushMessage:
    return PushMessage(
        title=title,
        body=_TASK_NOTICE_BODY,
        route=f"#/runs/{run_id}",
        state=state,
        title_tr=_TASK_NOTICE_TR[title],
        body_tr=_TASK_NOTICE_BODY_TR,
    )


def _told_by_its_schedule(run: RunRecord) -> bool:
    """Whether the end of ``run`` is its schedule's to tell (item 461): a run a schedule started, at its time or by
    hand, and not a campaign's session, which carries the same source."""
    return run.source == SCHEDULE_SOURCE and run.campaign_id is None


def push_message(event: EventRecord, run: RunRecord) -> PushMessage | None:
    """The message an event of a run is worth, or ``None``: an approval or a question asked or left; a draft written
    for the owner to send (item 314); a successful, failed or blocked end of a run with no parent, or the end of one that waits on
    the owner (item 2070), apart from a scheduled run, which its schedule tells at its notice level; or the end of a campaign."""
    if event.kind in _ASKED_KINDS:
        severity: object = event.data.get("severity")
        title: str = _ASKING_TITLE.get(str(severity), "Dot asks for approval")
        return task_notice(title, run.id, "waiting")
    if event.kind == DRAFT_CREATED_EVENT:
        draft: DraftRecord = DraftRecord.model_validate(event.data)
        about: str = draft.subject if draft.channel == "mail" else draft.body
        return PushMessage(
            title=_DRAFT_TITLE[draft.channel],
            body=_shorten(f"To {', '.join(draft.to)}: {about}"),
            route=f"#/runs/{run.id}",
            state="waiting",
        )
    if event.kind == "run_finished" and run.parent_run_id is None and not _told_by_its_schedule(run):
        needs: object = event.data.get("needs")
        needed: str = needs.strip() if isinstance(needs, str) else ""
        status: object = event.data.get("status")
        if event.data.get("waiting") is True or (status == "failed" and needed != ""):
            # Done as far as it can go, waiting on the owner (item 2070; such an end carries ``goal_blocked`` too), or
            # failed having said what it needs from them (item 1061): "waiting for you", not "done", "failed" nor
            # "blocked", as the web and the phone show it.
            return task_notice(WAITING_TITLE, run.id, "waiting")
        if event.data.get("goal_blocked") is True or status == "blocked":
            return task_notice("A task is blocked", run.id, "waiting")
        if status == "done":
            return task_notice(_FINISHED_TITLE["done"], run.id, "done")
        if status == "failed":
            return task_notice(_FINISHED_TITLE["failed"], run.id, "failed")
        return None
    if event.kind == CAMPAIGN_ENDED_EVENT:
        ended: CampaignEnded = CampaignEnded.model_validate(event.data)
        achieved: bool = ended.status == "achieved"
        return PushMessage(
            title="A campaign is achieved" if achieved else "A campaign stopped",
            body=_shorten(f"{ended.title}: {ended.note}"),
            route=f"#/campaigns/{ended.campaign_id}",
            state="done" if achieved else "idle",
        )
    return None


def message_group(kind: str, message: PushMessage) -> PushGroup | None:
    """The kind of push ``message``, made of an event of ``kind``, is told as: the end of a run that waits on the owner
    (item 2070, its state ``waiting``) with what waits on them, so that the switch of that kind decides it; any other
    as :func:`push_group` says."""
    if kind == "run_finished" and message.state == "waiting":
        return "needs_you"
    return push_group(kind)


def notice_message(event: EventRecord) -> PushMessage | None:
    """The message an event of Dot itself (``SYSTEM_RUN_ID``) is worth, or ``None``: a firing a schedule missed, a
    schedule switched off after its runs failed in a row, a start of the daemon after the one before it did not end
    cleanly, learning that has stopped, and what Dot tells the owner on its own (items 570 and 571)."""
    if event.kind == OWNER_TOLD_EVENT:
        owner: OwnerTold = OwnerTold.model_validate(event.data)
        return PushMessage(title=owner.title, body=_shorten(owner.text), route=owner.url, state="idle")
    if event.kind == LEARNING_STALLED_EVENT:
        stalled: LearningStalled = LearningStalled.model_validate(event.data)
        return PushMessage(
            title="Dot's learning has stopped", body=_shorten(stalled.reason), route="#/settings/memory", state="failed"
        )
    if event.kind == SCHEDULE_MISSED_EVENT:
        missed: ScheduleMissed = ScheduleMissed.model_validate(event.data)
        return PushMessage(
            title="A schedule did not run",
            body=_shorten(f"{missed.name}: {missed.status}"),
            route="#/settings/schedules",
            state="failed",
        )
    if event.kind == SCHEDULE_RUN_ENDED_EVENT:
        ended: ScheduleRunEnded = ScheduleRunEnded.model_validate(event.data)
        return finished_message(ended.status, ended.result, ended.run_id)
    if event.kind == SCHEDULE_STOPPED_EVENT:
        stopped: ScheduleStopped = ScheduleStopped.model_validate(event.data)
        return PushMessage(
            title="A schedule was switched off",
            body=_shorten(f"{stopped.name}: its last {stopped.failures} tasks failed"),
            route="#/settings/schedules",
            state="failed",
        )
    if event.kind == DAEMON_RESTARTED_EVENT:
        restarted: DaemonRestarted = DaemonRestarted.model_validate(event.data)
        return PushMessage(
            title="Dot restarted after an unexpected stop",
            body=_cut_off(restarted.runs_cut_off),
            route="#/runs",
            state="failed",
        )
    return None


def _signer(keys: PushKeys) -> Vapid:
    """The daemon's key, set to sign with ``SUBJECT``."""
    return Vapid(private_key=Vapid.from_pem(keys.private_pem.encode("ascii")).private_key, conf=dict(_SIGNING))


def _send(keys: PushKeys, subscription: PushSubscription, message: PushMessage) -> None:
    # pywebpush loads aiohttp and requests, about 25 MB: the first notification sent loads it, not the daemon's start
    # (item 729).
    from py_vapid import VapidException
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(message.model_dump(exclude_none=True)),
            vapid_private_key=_signer(keys),
            vapid_claims={"sub": SUBJECT},
            ttl=_TTL_S,
            timeout=_SEND_TIMEOUT_S,
        )
    except WebPushException as exc:
        status: int | None = None if exc.response is None else int(exc.response.status_code)
        answer: str = "" if exc.response is None else str(exc.response.text)[:_ANSWER_CHARS]
        raise PushError(
            "the push service did not take the message",
            {"endpoint": subscription.endpoint, "status": status, "answer": answer, "detail": str(exc)},
        ) from exc
    except VapidException as exc:
        # The message could not be signed (item 1058: its claims refused, as before item 1019): this one is not sent,
        # and the watch of the pushes goes on to the next, where the error had ended it and the phone heard nothing
        # until the daemon started again.
        raise PushError(
            "the push could not be signed", {"endpoint": subscription.endpoint, "status": None, "detail": str(exc)}
        ) from exc
    except OSError as exc:
        # The network failed on the way (requests raises its errors as OSError): the service never answered.
        raise PushError(
            "the push service could not be reached",
            {"endpoint": subscription.endpoint, "status": None, "detail": str(exc)},
        ) from exc


async def send_push(keys: PushKeys, subscription: PushSubscription, message: PushMessage) -> None:
    """Encrypt ``message`` for ``subscription`` and post it to its push service; a service that is busy or failing (429,
    5xx), which did not take it, is asked once more after a short wait (the outside audit of 2026-10-17, R13). Raises
    ``PushError``."""
    try:
        await asyncio.to_thread(_send, keys, subscription, message)
    except PushError as error:
        if error.context.get("status") not in _ASKED_AGAIN:
            raise
        log.warning("push_asked_again", endpoint=subscription.endpoint, status=error.context.get("status"))
        await asyncio.sleep(_AGAIN_AFTER_S)
        await asyncio.to_thread(_send, keys, subscription, message)


def gone(error: PushError) -> bool:
    """Whether the push service said the subscription no longer exists."""
    return error.context.get("status") in _GONE


async def _next_event(queue: asyncio.Queue[EventRecord], stopping: asyncio.Event) -> EventRecord | None:
    """The next event of ``queue``, or None once the daemon starts to stop. It waits a second at a time so that a stop
    is seen while nothing happens, in a loop: a call per quiet second would run out of stack in a quarter of an hour
    (item 116)."""
    while not stopping.is_set():
        try:
            return await asyncio.wait_for(queue.get(), _WAIT_S)
        except TimeoutError:
            continue
    return None


async def _push_to(
    store: Store, keys: PushKeys, subscription: PushSubscription, message: PushMessage, hosts: Sequence[str]
) -> PushOutcome:
    """Send ``message`` to one subscription: one whose keys cannot encrypt it or whose push service calls it gone is
    deleted, one at a host that is not on ``hosts`` (item 1311: a subscription kept before the door checked, or a host
    taken off the list) is not posted to and is kept, so that the list is the owner's to change, and any other refusal
    is logged and the subscription kept."""
    problem: str | None = keys_problem(subscription.p256dh, subscription.auth)
    if problem is not None:
        await store.delete_push_subscription(subscription.endpoint)
        log.warning("push_subscription_unusable", endpoint=subscription.endpoint, problem=problem)
        return "removed"
    refused: str | None = push_host_problem(subscription.endpoint, hosts, https_only=False)
    if refused is not None:
        log.warning(
            "push_subscription_host_refused",
            endpoint=subscription.endpoint,
            problem=refused,
            setting="[notify] push_hosts",
        )
        return "failed"
    try:
        await send_push(keys, subscription, message)
    except PushError as error:
        if gone(error):
            await store.delete_push_subscription(subscription.endpoint)
            log.info("push_subscription_gone", endpoint=subscription.endpoint, status=error.context.get("status"))
            return "removed"
        # What the service answered says why (item 992: Apple's reason was never logged, and a 403 hid for days).
        # A push that never reached the service (it could not be signed, item 1058, or the network failed) says why in
        # its detail, which a status and an answer do not hold.
        log.warning(
            "push_failed",
            endpoint=subscription.endpoint,
            status=error.context.get("status"),
            answer=error.context.get("answer"),
            error=error.message,
            detail=error.context.get("detail"),
        )
        return "failed"
    return "sent"


async def push_event(store: Store, keys: PushKeys, event: EventRecord, hosts: Sequence[str]) -> PushTotals:
    """Send one event to every subscription at a push service of ``hosts``, each on its own (:func:`_push_to`; the code
    review's Y15): one that fails keeps no other from its message. Returns the totals, all naught for an event the phone
    is not told of."""
    message: PushMessage | None = (
        notice_message(event)
        if event.run_id == SYSTEM_RUN_ID
        else push_message(event, await store.get_run(event.run_id))
    )
    if message is None:
        return PushTotals(sent=0, failed=0, removed=0)
    outcomes: list[PushOutcome] = [
        await _push_to(store, keys, subscription, message, hosts)
        for subscription in await store.list_push_subscriptions()
    ]
    return PushTotals(sent=outcomes.count("sent"), failed=outcomes.count("failed"), removed=outcomes.count("removed"))


async def watch_pushes(
    store: Store, feed: Subscription, keys: PushKeys, stopping: asyncio.Event, hosts: Sequence[str]
) -> None:
    """Send every subscription what the events of ``feed`` say, until the daemon starts to stop. ``feed`` takes the
    kinds of ``PUSH_KINDS``, and its caller opens it, so that what happens before this task first runs is sent too. An
    event that cannot be pushed (its run was deleted, the store failed) is logged and the next one still goes; the
    totals of one that was (:class:`PushTotals`) are logged, as a warning when a push service refused it."""
    while not stopping.is_set():
        event: EventRecord | None = await _next_event(feed.queue, stopping)
        if event is None:
            continue
        try:
            totals: PushTotals = await push_event(store, keys, event, hosts)
        except DotError as error:
            log.warning("push_event_failed", run_id=event.run_id, kind=event.kind, error=str(error))
            continue
        if totals.failed:
            log.warning("push_event_not_taken_by_all", run_id=event.run_id, kind=event.kind, **totals.model_dump())
        elif totals.sent or totals.removed:
            log.info("push_event_sent", run_id=event.run_id, kind=event.kind, **totals.model_dump())
