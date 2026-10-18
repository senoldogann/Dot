"""Web push to the owner's phone (item 52): the VAPID key pair the daemon signs with, the message an event turns into,
the send to a subscription's push service, and the watcher that sends every subscription what the web UI's desktop
notifications say: an approval or a question a run asked or left, a draft written for the owner to send, the end of a
run, a campaign that ended by itself, a firing a schedule missed, a start of the daemon after the one before it did not
end cleanly. The subscriptions come
from the web UI (``POST /api/push/subscriptions``, which refuses keys that cannot encrypt a message) and live in the
store; one whose push service says it is gone, or whose keys cannot encrypt a message, is deleted."""

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Literal

import structlog
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid, b64urlencode
from pywebpush import WebPushException, webpush

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.campaign import CAMPAIGN_ENDED_EVENT, CampaignEnded
from dotagent.agent.learning_health import LEARNING_STALLED_EVENT, LearningStalled
from dotagent.core.errors import DotError
from dotagent.core.types import FrozenModel
from dotagent.drafts.service import DRAFT_CREATED_EVENT
from dotagent.events import DAEMON_RESTARTED_EVENT, SYSTEM_RUN_ID, DaemonRestarted, Subscription
from dotagent.notify.keys import keys_problem
from dotagent.scheduler import SCHEDULE_MISSED_EVENT, ScheduleMissed
from dotagent.store.db import Store
from dotagent.store.models import DraftRecord, EventRecord, PushSubscription, RunRecord

log = structlog.get_logger(__name__)

# The claim a push service sees in the signed header; the daemon has no address of its own.
_SUBJECT: str = "mailto:dot@localhost"
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
_FINISHED_TITLE: dict[str, str] = {"done": "A run is done", "failed": "A run failed", "cancelled": "A run was stopped"}
_DRAFT_TITLE: dict[str, str] = {"mail": "Dot drafted an email", "message": "Dot drafted a message"}
type PushState = Literal["idle", "waiting", "done", "failed"]
# What sending one message to one subscription came to: taken, refused or unreachable (kept), or deleted.
type PushOutcome = Literal["sent", "failed", "removed"]
_FINISHED_STATE: dict[str, PushState] = {"done": "done", "failed": "failed"}
PUSH_KINDS: frozenset[str] = frozenset(
    {
        *_ASKED_KINDS,
        DRAFT_CREATED_EVENT,
        "run_finished",
        CAMPAIGN_ENDED_EVENT,
        SCHEDULE_MISSED_EVENT,
        DAEMON_RESTARTED_EVENT,
        LEARNING_STALLED_EVENT,
    }
)


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
        return "No run was cut off."
    return "1 run was cut off." if runs == 1 else f"{runs} runs were cut off."


def push_message(event: EventRecord, run: RunRecord) -> PushMessage | None:
    """The message an event of a run is worth, or ``None``: an approval or a question asked or left; a draft written
    for the owner to send (item 314); the end of a run of its own (the sub-runs of a run stay quiet, as in the web UI),
    with the first line of its result; the end of a campaign by itself."""
    if event.kind in _ASKED_KINDS:
        severity: object = event.data.get("severity")
        args: object = event.data.get("args")
        question: object = args.get("question") if isinstance(args, dict) else None
        summary: object = event.data.get("summary")
        text: str = question if severity == "question" and isinstance(question, str) else str(summary or "")
        title: str = _ASKING_TITLE.get(str(severity), "Dot asks for approval")
        return PushMessage(title=title, body=_shorten(text), route=f"#/runs/{run.id}", state="waiting")
    if event.kind == DRAFT_CREATED_EVENT:
        draft: DraftRecord = DraftRecord.model_validate(event.data)
        about: str = draft.subject if draft.channel == "mail" else draft.body
        return PushMessage(
            title=_DRAFT_TITLE[draft.channel],
            body=_shorten(f"To {', '.join(draft.to)}: {about}"),
            route=f"#/runs/{run.id}",
            state="waiting",
        )
    if event.kind == "run_finished" and run.parent_run_id is None:
        status: str = str(event.data.get("status", ""))
        result: object = event.data.get("result")
        return PushMessage(
            title=_FINISHED_TITLE.get(status, "A run has ended"),
            body=_shorten(_first_line(result if isinstance(result, str) else "")),
            route=f"#/runs/{run.id}",
            state=_FINISHED_STATE.get(status, "idle"),
        )
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


def notice_message(event: EventRecord) -> PushMessage | None:
    """The message an event of Dot itself (``SYSTEM_RUN_ID``) is worth, or ``None``: a firing a schedule missed, a
    start of the daemon after the one before it did not end cleanly, and learning that has stopped."""
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
    if event.kind == DAEMON_RESTARTED_EVENT:
        restarted: DaemonRestarted = DaemonRestarted.model_validate(event.data)
        return PushMessage(
            title="Dot restarted after an unexpected stop",
            body=_cut_off(restarted.runs_cut_off),
            route="#/runs",
            state="failed",
        )
    return None


def _send(keys: PushKeys, subscription: PushSubscription, message: PushMessage) -> None:
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(message.model_dump()),
            vapid_private_key=Vapid.from_pem(keys.private_pem.encode("ascii")),
            vapid_claims={"sub": _SUBJECT},
            ttl=_TTL_S,
            timeout=_SEND_TIMEOUT_S,
        )
    except WebPushException as exc:
        status: int | None = None if exc.response is None else int(exc.response.status_code)
        raise PushError(
            "the push service did not take the message",
            {"endpoint": subscription.endpoint, "status": status, "detail": str(exc)},
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


async def _push_to(store: Store, keys: PushKeys, subscription: PushSubscription, message: PushMessage) -> PushOutcome:
    """Send ``message`` to one subscription: one whose keys cannot encrypt it or whose push service calls it gone is
    deleted, and any other refusal is logged and the subscription kept."""
    problem: str | None = keys_problem(subscription.p256dh, subscription.auth)
    if problem is not None:
        await store.delete_push_subscription(subscription.endpoint)
        log.warning("push_subscription_unusable", endpoint=subscription.endpoint, problem=problem)
        return "removed"
    try:
        await send_push(keys, subscription, message)
    except PushError as error:
        if gone(error):
            await store.delete_push_subscription(subscription.endpoint)
            log.info("push_subscription_gone", endpoint=subscription.endpoint, status=error.context.get("status"))
            return "removed"
        log.warning("push_failed", endpoint=subscription.endpoint, status=error.context.get("status"))
        return "failed"
    return "sent"


async def push_event(store: Store, keys: PushKeys, event: EventRecord) -> PushTotals:
    """Send one event to every subscription, each on its own (:func:`_push_to`; the code review's Y15): one that fails
    keeps no other from its message. Returns the totals, all naught for an event the phone is not told of."""
    message: PushMessage | None = (
        notice_message(event)
        if event.run_id == SYSTEM_RUN_ID
        else push_message(event, await store.get_run(event.run_id))
    )
    if message is None:
        return PushTotals(sent=0, failed=0, removed=0)
    outcomes: list[PushOutcome] = [
        await _push_to(store, keys, subscription, message) for subscription in await store.list_push_subscriptions()
    ]
    return PushTotals(sent=outcomes.count("sent"), failed=outcomes.count("failed"), removed=outcomes.count("removed"))


async def watch_pushes(store: Store, feed: Subscription, keys: PushKeys, stopping: asyncio.Event) -> None:
    """Send every subscription what the events of ``feed`` say, until the daemon starts to stop. ``feed`` takes the
    kinds of ``PUSH_KINDS``, and its caller opens it, so that what happens before this task first runs is sent too. An
    event that cannot be pushed (its run was deleted, the store failed) is logged and the next one still goes; the
    totals of one that was (:class:`PushTotals`) are logged, as a warning when a push service refused it."""
    while not stopping.is_set():
        event: EventRecord | None = await _next_event(feed.queue, stopping)
        if event is None:
            continue
        try:
            totals: PushTotals = await push_event(store, keys, event)
        except DotError as error:
            log.warning("push_event_failed", run_id=event.run_id, kind=event.kind, error=str(error))
            continue
        if totals.failed:
            log.warning("push_event_not_taken_by_all", run_id=event.run_id, kind=event.kind, **totals.model_dump())
        elif totals.sent or totals.removed:
            log.info("push_event_sent", run_id=event.run_id, kind=event.kind, **totals.model_dump())
