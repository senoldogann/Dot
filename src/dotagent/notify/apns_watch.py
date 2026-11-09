"""What Dot's iPhone app hears of the daemon (item 997): the events the web app's Web Push tells as alerts, and the
one Live Activity of the phone, a row for each run of the owner's (item 1093).

A run of the owner's (no run delegated it, and no schedule, watcher, own initiative or reply started it) is a row of
the activity from its start: it works, waits for the owner (an approval asks), works again once answered, and ends
done, failed or stopped; an ended row stays :data:`DISMISS_AFTER_S`. The activity shows the runs at work first, then
those that ended last, at most :data:`FLEET_ROWS`, and how many are at work. The first run starts it on every phone
whose app gave a push-to-start token, the app sends back the activity's token, and every change after goes to that
token; once no row is left the activity ends and goes. While a run works its row says what it does now (item 1004:
thinking, browsing the web, running a command; :mod:`dotagent.notify.live_now`), the activity changed for that at
most once every :data:`PROGRESS_EVERY_S`, the latest step kept rather than each one sent: the steps come faster than
Apple lets a Live Activity change, and a run that starts, waits or ends is told at once."""

import asyncio
import time
from collections.abc import Mapping

from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.events import SYSTEM_RUN_ID, Subscription
from dotagent.notify.apns import (
    DISMISS_AFTER_S,
    FLEET_ID,
    FLEET_ROWS,
    ApnsReply,
    ApnsSender,
    FleetState,
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
from dotagent.store.models import WORKING_STATUSES, ApnsDevice, EventRecord, LiveActivity, RunRecord

log = get_logger(__name__)

# The app gave the token of the Live Activity (``POST /api/phone/apns/activity``), a live event of Dot's own: the
# activity is told the rows as they stand now, which changed while the token was on its way.
# The app said the owner swiped the activity away (``DELETE /api/phone/apns/activity?token=``): no new one is started
# on that phone before the next run of the owner's starts, or the card would be back in a moment.
LIVE_DISMISSED_EVENT: str = "live_activity_dismissed"
LIVE_REGISTERED_EVENT: str = "live_activity_registered"
# What the app hears: the alerts of Web Push, a run's start, an answer that lets a waiting run work again, and a Live
# Activity's token.
APNS_KINDS: frozenset[str] = frozenset(
    {*PUSH_KINDS, *PROGRESS_KINDS, "run_started", "approval_decided", LIVE_REGISTERED_EVENT, LIVE_DISMISSED_EVENT}
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
# What APNs answers a push it took.
_TAKEN: int = 200
# How long a push-to-start waits for the app's token before the wait is told as a warning. No second start goes to the
# phone meanwhile, however long (the pre-push review): an app whose token cannot reach the daemon would otherwise open
# a card each time; the wait ends with the token, or once no row is left.
START_WAIT_S: float = 60.0
# How long iOS keeps a Live Activity at work (eight hours, then it ends it): a token older than that is of an activity
# that is no more, and the next change starts a new one.
ACTIVITY_LIFE_S: float = 8 * 3600.0
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


def live_state(run: RunRecord, title: str, agent: str, status: LiveStatus, now: LiveNow) -> LiveState:
    return LiveState(
        runId=run.id,
        title=short_title(title),
        agent=agent,
        status=status,
        kind=now.kind,
        line=now.line,
        startedAt=run.created_at,
    )


class _Row(FrozenModel):
    """A run as the activity shows it, and when it ended (``None`` while it is at work)."""

    state: LiveState
    ended_at: float | None


def fleet_state(rows: Mapping[str, _Row]) -> FleetState:
    """The activity's state of ``rows``: the runs at work, the newest first, then those that ended, the last to end
    first, at most :data:`FLEET_ROWS`."""
    working: list[_Row] = sorted(
        (row for row in rows.values() if row.ended_at is None), key=lambda row: -row.state.startedAt
    )
    ended: list[_Row] = sorted(
        (row for row in rows.values() if row.ended_at is not None), key=lambda row: -(row.ended_at or 0.0)
    )
    return FleetState(active=len(working), rows=tuple(row.state for row in [*working, *ended][:FLEET_ROWS]))


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
        # The runs the activity shows, by id (item 1093).
        self._rows: dict[str, _Row] = {}
        # When the activity was last told, and whether a step came since that waits its turn (item 1004).
        self._told_at: float = 0.0
        self._due: bool = False
        # When a push-to-start went to each phone whose activity's token has not come yet, and those told as late.
        self._starting: dict[str, float] = {}
        self._late: set[str] = set()
        # The phones whose activity the owner swiped away, until the next run starts.
        self._dismissed: set[str] = set()
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

    async def _row(self, run: RunRecord, status: LiveStatus, doing: LiveNow, ended_at: float | None) -> None:
        """Make ``run``'s row say ``status`` and ``doing``: its title and agent read once, when it is new."""
        shown: _Row | None = self._rows.get(run.id)
        if shown is None:
            title, agent = await self._title_and_agent(run)
            state: LiveState = live_state(run, title, agent, status, doing)
        else:
            state = shown.state.model_copy(update={"status": status, "kind": doing.kind, "line": doing.line})
        self._rows[run.id] = _Row(state=state, ended_at=ended_at)

    async def _tell(self, priority: PushPriority, now: float) -> None:
        """Tell every phone's activity the rows as they stand: an update where the app gave the activity's token, a
        start where it has not (one at a time, :data:`START_WAIT_S`), and the end once no row is left."""
        state: FleetState = fleet_state(self._rows)
        self._told_at = now
        self._due = False
        activities: dict[str, LiveActivity] = {
            activity.phone_id: activity for activity in await self._store.live_activities_of(FLEET_ID)
        }
        for device in await self._store.list_apns_devices():
            activity: LiveActivity | None = activities.get(device.phone_id)
            # Its age counts from its first token (a token that turns keeps it, ``Store.set_live_activity``): a new
            # activity is started only once the row of the one before is gone (its end, its age, the app's forget).
            if activity is not None and now - activity.created_at >= ACTIVITY_LIFE_S:
                # iOS has ended it: the token goes nowhere, and a new activity is started.
                await self._store.delete_live_activity(FLEET_ID, device.phone_id)
                activity = None
            if not state.rows:
                if activity is not None:
                    await self._send(device, activity.token, _AT_ONCE, live_end_payload(state, now))
                    await self._store.delete_live_activity(FLEET_ID, device.phone_id)
                self._starting.pop(device.phone_id, None)
                self._late.discard(device.phone_id)
            elif activity is not None:
                await self._send(device, activity.token, priority, live_update_payload(state, now))
            elif device.phone_id in self._starting:
                waited: float = now - self._starting[device.phone_id]
                if waited >= START_WAIT_S and device.phone_id not in self._late:
                    self._late.add(device.phone_id)
                    log.warning("apns_live_token_late", phone=device.phone_id, waited_s=round(waited))
            elif device.start_token is not None and device.phone_id not in self._dismissed:
                # Waited on only once APNs took it: a start that did not go is sent again at the next change.
                if await self._start(device, device.start_token, live_start_payload(state, now)):
                    self._starting[device.phone_id] = now

    async def _send(self, device: ApnsDevice, token: str, priority: PushPriority, payload: JsonObject) -> None:
        try:
            await self._sender.send(device.environment, token, "liveactivity", priority, payload)
        except DotError as error:
            log.warning("apns_live_update_failed", phone=device.phone_id, error=error.message)

    async def _start(self, device: ApnsDevice, token: str, payload: JsonObject) -> bool:
        """Send a push-to-start; whether APNs took it."""
        try:
            reply: ApnsReply = await self._sender.send(device.environment, token, "liveactivity", _AT_ONCE, payload)
        except DotError as error:
            log.warning("apns_live_start_failed", phone=device.phone_id, error=error.message)
            return False
        # A start token that is gone leaves the device's alerts as they are: only the token is forgotten, until the app
        # gives a new one.
        if gone(reply):
            await self._store.set_apns_device(
                device.phone_id, device.device_token, None, device.environment, device.updated_at
            )
            log.info("apns_start_token_gone", phone=device.phone_id, status=reply.status, reason=reply.reason)
        return reply.status == _TAKEN

    async def on_event(self, event: EventRecord, now: float) -> None:
        """Send what ``event`` says: an alert as Web Push would, and the change of the run's row."""
        if event.kind == LIVE_DISMISSED_EVENT:
            dismissed: object = event.data.get("phone_id")
            if isinstance(dismissed, str):
                self._dismissed.add(dismissed)
            return
        if event.kind == LIVE_REGISTERED_EVENT:
            phone: object = event.data.get("phone_id")
            if isinstance(phone, str):
                self._starting.pop(phone, None)
                self._late.discard(phone)
            await self._tell(_AT_ONCE, now)
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
            # Known as no row's while it works, so that its steps are passed over without a read.
            if event.kind == "run_finished":
                self._others.discard(run.id)
            else:
                self._others.add(run.id)
            return
        if event.kind == "run_started":
            # A new run of the owner's brings the activity back on a phone where it was swiped away.
            self._dismissed.clear()
            await self._row(run, "working", THINKING, None)
        elif event.kind in (*_WAITS, "approval_decided") and run.status not in WORKING_STATUSES:
            # An answer to a question a run left, or to a step it held, comes after it ended: its row stays as it ended.
            return
        elif event.kind in _WAITS:
            await self._row(run, "waiting", WAITING, None)
        elif event.kind == "approval_decided":
            await self._row(run, "working", WORKING, None)
        elif event.kind == "run_finished":
            self._others.discard(run.id)
            ended: LiveStatus
            last: LiveNow
            ended, last = _ENDED.get(str(event.data.get("status", run.status)), _ENDED_ELSE)
            await self._row(run, ended, last, now)
        else:
            return
        await self._tell(_AT_ONCE, now)

    async def progress(self, event: EventRecord, now: float) -> None:
        """A step of a run: what it does now (:func:`live_now`) goes to the activity at once when its last push is
        :data:`PROGRESS_EVERY_S` old, and waits for :meth:`flush` otherwise, a later step taking its place."""
        if event.run_id in self._others:
            return
        doing: LiveNow | None = live_now(event)
        if doing is None:
            return
        shown: _Row | None = self._rows.get(event.run_id)
        if shown is None:
            run: RunRecord = await self._store.get_run(event.run_id)
            if not follows_live(run):
                self._others.add(run.id)
                return
            # A run the daemon was not told the start of (it started before a restart): it is a row from now on.
            await self._row(run, "working", doing, None)
        elif (
            shown.ended_at is not None
            or shown.state.status == "waiting"
            or (shown.state.kind, shown.state.line) == (doing.kind, doing.line)
        ):
            # An ended row stays as it ended, a waiting one waits for the owner, and the same step is no news.
            return
        else:
            self._rows[event.run_id] = shown.model_copy(
                update={"state": shown.state.model_copy(update={"kind": doing.kind, "line": doing.line})}
            )
        if now - self._told_at >= PROGRESS_EVERY_S:
            await self._tell(_WHEN_IT_SUITS, now)
        else:
            self._due = True

    async def settle(self, now: float) -> None:
        """As the daemon starts: take the runs still at work as rows, so that an activity kept from before goes on
        showing them, and end it when none is, as its rows were of the daemon before and would stand until they went
        stale."""
        for run in await self._store.working_runs():
            if follows_live(run) and run.id not in self._rows:
                status: LiveStatus = "waiting" if run.status == "blocked" else "working"
                await self._row(run, status, WAITING if status == "waiting" else WORKING, None)
        if await self._store.live_activities_of(FLEET_ID):
            await self._tell(_AT_ONCE, now)

    async def flush(self, now: float) -> None:
        """Take off the rows of runs that ended :data:`DISMISS_AFTER_S` ago, and send the step that waited its turn
        once the last push is :data:`PROGRESS_EVERY_S` old."""
        gone_rows: list[str] = [
            run_id
            for run_id, row in self._rows.items()
            if row.ended_at is not None and now - row.ended_at >= DISMISS_AFTER_S
        ]
        for run_id in gone_rows:
            del self._rows[run_id]
        if gone_rows or (self._due and now - self._told_at >= PROGRESS_EVERY_S):
            await self._tell(_WHEN_IT_SUITS, now)


async def watch_apns(watch: ApnsWatch, feed: Subscription, stopping: asyncio.Event) -> None:
    """Send the app what the events of ``feed`` (:data:`APNS_KINDS`) say, until the daemon starts to stop; an event
    that cannot be sent is logged and the next one still goes."""
    try:
        await watch.settle(time.time())
    except DotError as error:
        log.warning("apns_settle_failed", error=error.message)
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
