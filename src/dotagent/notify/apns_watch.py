"""What Dot's iPhone app hears of the daemon (item 997): the events the web app's Web Push tells as alerts, and the
Live Activity of each run of the owner's, from its start to its end.

A run of the owner's (no run delegated it, and no schedule, watcher, own initiative or reply started it) starts a Live
Activity on every phone whose app gave a push-to-start token; the app sends back the token of the activity, which the
updates and the end go to. The activity says the run works, waits for the owner (an approval or a question asks), works
again once answered, and ends done, failed or stopped. While it works it says what it does now (item 1004: thinking,
browsing the web, running a command; :mod:`dotagent.notify.live_now`), at most once every
:data:`PROGRESS_EVERY_S` per run, the latest step kept for the next push rather than each one sent: the steps come
faster than Apple lets a Live Activity change, and a run that waits or ends is told at once."""

import asyncio
import time

from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.events import SYSTEM_RUN_ID, Subscription
from dotagent.notify.apns import (
    ApnsReply,
    ApnsSender,
    LiveState,
    LiveStatus,
    PushPriority,
    alert_payload,
    approval_payload,
    gone,
    live_end_payload,
    live_start_payload,
    live_update_payload,
    short_title,
)
from dotagent.notify.live_now import PROGRESS_KINDS, THINKING, WAITING, WORKING, LiveNow, live_now
from dotagent.notify.owner import OWNER_TOLD_EVENT
from dotagent.notify.push import PUSH_KINDS, PushMessage, notice_message, push_message
from dotagent.proactive import UNWATCHED_SOURCES
from dotagent.store.db import Store
from dotagent.store.models import WORKING_STATUSES, ApnsDevice, EventRecord, RunRecord

log = get_logger(__name__)

# The app gave the token of a run's Live Activity (``POST /api/phone/apns/activity``), a live event of Dot's own whose
# ``run_id`` names the run: a run that ended before it came has its activity ended at once, or it would stand at
# "Working" until it went stale.
LIVE_REGISTERED_EVENT: str = "live_activity_registered"
# What the app hears: the alerts of Web Push, a run's start, an answer that lets a waiting run work again, and a Live
# Activity's token.
APNS_KINDS: frozenset[str] = frozenset(
    {*PUSH_KINDS, *PROGRESS_KINDS, "run_started", "approval_decided", LIVE_REGISTERED_EVENT}
)
# What makes a run wait for the owner: an approval or a question it asks. A question it leaves for later does not.
_WAITS: frozenset[str] = frozenset({"approval_requested"})
_ENDED: dict[str, tuple[LiveStatus, LiveNow]] = {
    "done": ("done", LiveNow(kind="done", line="Done")),
    "failed": ("failed", LiveNow(kind="failed", line="Failed")),
    "cancelled": ("failed", LiveNow(kind="failed", line="Stopped")),
}
_ENDED_ELSE: tuple[LiveStatus, LiveNow] = ("failed", LiveNow(kind="failed", line="Ended"))
# How often a run's Live Activity is told what it does now, at most.
PROGRESS_EVERY_S: float = 4.0
_AT_ONCE: PushPriority = "10"
_WHEN_IT_SUITS: PushPriority = "5"
_WAIT_S: float = 1.0
# The approvals the lock screen answers: a step of low or medium risk. A step of high risk (money, a message or a post
# sent for the owner, a credential: ``policy.engine.approval_severity``) opens Dot, as the alert says nothing of what it
# asks and the owner must see the amount or the receiver first; so do a question (it wants words), a secret (its value)
# and a handoff (the owner's hands on the Mac).
_ANSWERABLE: frozenset[str] = frozenset({"low", "medium"})
_DOT: str = "Dot"
# The title of an alert of a message Dot wrote to the owner.
TOLD_TITLE: str = "Dot has a message for you"


def approval_answerable(event: EventRecord) -> str | None:
    """The id of the approval ``event`` asks for when the lock screen may answer it (item 1008): a yes or a no to a
    step of low or medium risk (:data:`_ANSWERABLE`); ``None`` otherwise, and the alert opens Dot."""
    if event.kind != "approval_requested" or event.data.get("severity") not in _ANSWERABLE:
        return None
    approval_id: object = event.data.get("approval_id")
    return approval_id if isinstance(approval_id, str) and approval_id else None


def follows_live(run: RunRecord) -> bool:
    """Whether a run has a Live Activity: the owner's own, not a helper's, a schedule's or Dot's own work."""
    return run.parent_run_id is None and run.source not in UNWATCHED_SOURCES and run.source != EVAL_SOURCE


def live_state(title: str, status: LiveStatus, now: LiveNow, run: RunRecord) -> LiveState:
    return LiveState(title=short_title(title), status=status, kind=now.kind, line=now.line, startedAt=run.created_at)


class _Shown(FrozenModel):
    """What a run's Live Activity was last told it does, and when; and the step that came since and waits its turn."""

    now: LiveNow
    at: float
    next: LiveNow | None


def apns_message(event: EventRecord, message: PushMessage) -> PushMessage:
    """``message`` as APNs may carry it: a message Dot wrote to the owner has a title of its own words, which Apple
    would read, so it goes as :data:`TOLD_TITLE`; every other title is one of Dot's fixed ones."""
    return message.model_copy(update={"title": TOLD_TITLE}) if event.kind == OWNER_TOLD_EVENT else message


class ApnsWatch:
    """Sends the app what the events say, each device and activity on its own: one that fails keeps no other from
    its push, and one APNs calls gone is forgotten."""

    def __init__(self, store: Store, sender: ApnsSender) -> None:
        self._store: Store = store
        self._sender: ApnsSender = sender
        # The runs at work that have a Live Activity, by id, and what each was last told (item 1004): read once per
        # run, as the steps come many a second.
        self._live: dict[str, _Shown] = {}
        self._others: set[str] = set()

    async def _forget_if_gone(self, device: ApnsDevice, reply: ApnsReply) -> None:
        if gone(reply):
            await self._store.delete_apns_device(device.phone_id)
            log.info("apns_device_gone", phone=device.phone_id, status=reply.status, reason=reply.reason)

    async def alert(self, message: PushMessage) -> None:
        await self._alert(alert_payload(message.title, message.route))

    async def _alert(self, payload: JsonObject) -> None:
        for device in await self._store.list_apns_devices():
            try:
                reply: ApnsReply = await self._sender.send(
                    device.environment,
                    device.device_token,
                    "alert",
                    _AT_ONCE,
                    payload,
                )
            except DotError as error:
                log.warning("apns_alert_failed", phone=device.phone_id, error=error.message)
                continue
            await self._forget_if_gone(device, reply)

    async def _title_and_agent(self, run: RunRecord) -> tuple[str, str]:
        title: str = (await self._store.get_thread(run.thread_id)).title
        agent: str = _DOT if run.agent_id is None else (await self._store.get_agent(run.agent_id)).name
        return title, agent

    async def start(self, run: RunRecord, now: float) -> None:
        title, agent = await self._title_and_agent(run)
        payload = live_start_payload(run.id, agent, live_state(title, "working", THINKING, run), now)
        self._live[run.id] = _Shown(now=THINKING, at=now, next=None)
        for device in await self._store.list_apns_devices():
            if device.start_token is None:
                continue
            try:
                reply: ApnsReply = await self._sender.send(
                    device.environment, device.start_token, "liveactivity", _AT_ONCE, payload
                )
            except DotError as error:
                log.warning("apns_live_start_failed", phone=device.phone_id, error=error.message)
                continue
            # A start token that is gone leaves the device's alerts as they are: only the token is forgotten, until
            # the app gives a new one.
            if gone(reply):
                await self._store.set_apns_device(
                    device.phone_id, device.device_token, None, device.environment, device.updated_at
                )
                log.info("apns_start_token_gone", phone=device.phone_id, status=reply.status, reason=reply.reason)

    async def _to_activities(
        self, run: RunRecord, status: LiveStatus, doing: LiveNow, priority: PushPriority, now: float, ended: bool
    ) -> None:
        activities = await self._store.live_activities_of(run.id)
        if not activities:
            return
        title, _ = await self._title_and_agent(run)
        state: LiveState = live_state(title, status, doing, run)
        payload = live_end_payload(state, now) if ended else live_update_payload(state, now)
        devices: dict[str, ApnsDevice] = {device.phone_id: device for device in await self._store.list_apns_devices()}
        for activity in activities:
            device: ApnsDevice | None = devices.get(activity.phone_id)
            if device is None:
                continue
            try:
                await self._sender.send(device.environment, activity.token, "liveactivity", priority, payload)
            except DotError as error:
                log.warning("apns_live_update_failed", phone=device.phone_id, error=error.message)
        if ended:
            await self._store.delete_live_activities(run.id)

    async def on_event(self, event: EventRecord, now: float) -> None:
        """Send what ``event`` says: an alert as Web Push would, and the run's Live Activity's change."""
        if event.kind == LIVE_REGISTERED_EVENT:
            await self.catch_up(await self._store.get_run(str(event.data.get("run_id", ""))), now)
            return
        if event.run_id == SYSTEM_RUN_ID:
            notice: PushMessage | None = notice_message(event)
            if notice is not None:
                await self.alert(apns_message(event, notice))
            return
        if event.kind in PROGRESS_KINDS:
            await self.progress(event, now)
            return
        run: RunRecord = await self._store.get_run(event.run_id)
        message: PushMessage | None = push_message(event, run) if event.kind in PUSH_KINDS else None
        answerable: str | None = approval_answerable(event)
        if message is not None and answerable is not None:
            await self._alert(approval_payload(message.title, message.route, answerable))
        elif message is not None:
            await self.alert(message)
        if not follows_live(run):
            # Known as no Live Activity's while it works, so that its steps are passed over without a read.
            if event.kind == "run_finished":
                self._others.discard(run.id)
            else:
                self._others.add(run.id)
            return
        if event.kind == "run_started":
            await self.start(run, now)
        elif event.kind in _WAITS:
            self._live[run.id] = _Shown(now=WAITING, at=now, next=None)
            await self._to_activities(run, "waiting", WAITING, _AT_ONCE, now, False)
        elif event.kind == "approval_decided":
            self._live[run.id] = _Shown(now=WORKING, at=now, next=None)
            await self._to_activities(run, "working", WORKING, _AT_ONCE, now, False)
        elif event.kind == "run_finished":
            await self.end(run, str(event.data.get("status", run.status)), now)

    async def progress(self, event: EventRecord, now: float) -> None:
        """A step of a run: what it does now (:func:`live_now`) goes to its Live Activity at once when the last push
        is :data:`PROGRESS_EVERY_S` old, and waits for :meth:`flush` otherwise, a later step taking its place."""
        if event.run_id in self._others:
            return
        doing: LiveNow | None = live_now(event)
        if doing is None:
            return
        shown: _Shown | None = self._live.get(event.run_id)
        if shown is None:
            run: RunRecord = await self._store.get_run(event.run_id)
            if not follows_live(run):
                self._others.add(run.id)
                return
            # A run the daemon was not told the start of (it started before a restart): what it does is still news.
            shown = _Shown(now=WORKING, at=0.0, next=None)
            self._live[run.id] = shown
        if shown.now.kind == "waiting":
            return
        if doing == shown.now:
            self._live[event.run_id] = shown.model_copy(update={"next": None})
            return
        if now - shown.at < PROGRESS_EVERY_S:
            self._live[event.run_id] = shown.model_copy(update={"next": doing})
            return
        await self._tell(event.run_id, doing, now)

    async def flush(self, now: float) -> None:
        """Send each run's step that waited its turn, once its last push is :data:`PROGRESS_EVERY_S` old."""
        due: list[tuple[str, LiveNow]] = [
            (run_id, shown.next)
            for run_id, shown in self._live.items()
            if shown.next is not None and now - shown.at >= PROGRESS_EVERY_S
        ]
        for run_id, doing in due:
            await self._tell(run_id, doing, now)

    async def _tell(self, run_id: str, doing: LiveNow, now: float) -> None:
        self._live[run_id] = _Shown(now=doing, at=now, next=None)
        await self._to_activities(await self._store.get_run(run_id), "working", doing, _WHEN_IT_SUITS, now, False)

    async def catch_up(self, run: RunRecord, now: float) -> None:
        """Bring the Live Activities of ``run`` to where it stands now: whatever happened before the app's token came
        (an approval asked, the end) went to no activity, so the one just registered is told the run's state."""
        if run.status == "blocked":
            await self._to_activities(run, "waiting", WAITING, _AT_ONCE, now, False)
        elif run.status not in WORKING_STATUSES:
            await self.end(run, run.status, now)
        else:
            shown: _Shown | None = self._live.get(run.id)
            doing: LiveNow = THINKING if shown is None else (shown.next or shown.now)
            await self._to_activities(run, "working", doing, _AT_ONCE, now, False)

    async def end(self, run: RunRecord, status: str, now: float) -> None:
        """End the Live Activities of ``run``, which ended with ``status``, and forget them."""
        self._live.pop(run.id, None)
        self._others.discard(run.id)
        ended: LiveStatus
        last: LiveNow
        ended, last = _ENDED.get(status, _ENDED_ELSE)
        await self._to_activities(run, ended, last, _AT_ONCE, now, True)


async def watch_apns(watch: ApnsWatch, feed: Subscription, stopping: asyncio.Event) -> None:
    """Send the app what the events of ``feed`` (:data:`APNS_KINDS`) say, until the daemon starts to stop; an event
    that cannot be sent is logged and the next one still goes."""
    while not stopping.is_set():
        event: EventRecord | None
        try:
            event = await asyncio.wait_for(feed.queue.get(), _WAIT_S)
        except TimeoutError:
            event = None
        if event is not None:
            try:
                await watch.on_event(event, time.time())
            except DotError as error:
                log.warning("apns_event_failed", run_id=event.run_id, kind=event.kind, error=error.message)
        try:
            await watch.flush(time.time())
        except DotError as error:
            log.warning("apns_flush_failed", error=error.message)
