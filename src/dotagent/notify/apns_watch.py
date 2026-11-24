"""What Dot's iPhone app hears of the daemon (item 997): the events the web app's Web Push tells as alerts, and the
one Live Activity of the phone, a row for each run of the owner's (item 1093).

A run of the owner's (no run delegated it, and no schedule, watcher, own initiative or reply started it) is a row of
the activity from its start: it works, waits for the owner (an approval asks), works again once answered, and ends
done, failed or stopped; an ended row stays :data:`DISMISS_AFTER_S`. The activity shows the runs at work first, then
those that ended last, at most :data:`FLEET_ROWS`, and how many are at work. The app opens it itself when the owner
starts a run on the phone, which brings no notification; it sends back the activity's token, and every change after
goes to that token; once no row is left the activity ends and goes. A run started on the Mac opens none, but for a
phone that gave its push-to-start token (item 1425): when the phone shows no activity and is not in the foreground, a
run of Dot's own that works, or any run that has worked :data:`START_AFTER_S`, starts one by push, with the alert Apple
requires, at most once in :data:`START_EVERY_S`. While a run works its row says what it does now
(item 1004: thinking, browsing the web, running a command; :mod:`dotagent.notify.live_now`), the activity changed for
that at most once every :data:`PROGRESS_EVERY_S`, the latest step kept rather than each one sent: the steps come faster
than Apple lets a Live Activity change, and a run that starts, waits or ends is told at once."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping

from pydantic import ValidationError

from dotagent.core.errors import DotError, NotFoundError
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
    badged,
    gone,
    live_end_payload,
    live_start_payload,
    live_update_payload,
    short_title,
)
from dotagent.notify.leases import PhoneLeases
from dotagent.notify.live_now import PROGRESS_KINDS, THINKING, WAITING, WORKING, LiveNow, live_now
from dotagent.notify.live_start import forget_live_start_token, live_start_token_of
from dotagent.notify.owner import OWNER_TOLD_EVENT
from dotagent.notify.push import (
    PUSH_KINDS,
    PushGroup,
    PushMessage,
    notice_message,
    push_group,
    push_message,
    quiet_while_shown,
)
from dotagent.notify.settings import push_settings_of
from dotagent.proactive import UNWATCHED_SOURCES
from dotagent.store.db import Store
from dotagent.store.models import WORKING_STATUSES, ApnsDevice, EventRecord, LiveActivity, RunRecord

log = get_logger(__name__)

# The app gave the token of the Live Activity (``POST /api/phone/apns/activity``), a live event of Dot's own: the
# activity is told the rows as they stand now, which changed while the token was on its way.
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
# How long iOS keeps a Live Activity at work (eight hours, then it ends it): a token older than that is of an activity
# that is no more, and the app opens a new one at the next run it starts.
ACTIVITY_LIFE_S: float = 8 * 3600.0
# How long an activity the app opened waits for a row (item 1096): its token may come before the run's start, and a
# request that started no run after all (a message queued behind a held queue) opened it for nothing, which then ends.
OPEN_WAIT_S: float = 60.0
# How often the rows at work are read against their runs, which may have ended where the feed did not tell (item 1108).
CHECK_EVERY_S: float = 60.0
# The title of an alert of a message Dot wrote to the owner.
TOLD_TITLE: str = "Dot has a message for you"
# When a phone whose app shows no Live Activity is sent one by push (item 1425): at most one start for a phone in this
# long, for a run nobody watches at once and for any other once it has worked :data:`START_AFTER_S`. The sources that
# start at once are the runs of Dot's own that are rows of the activity: a command that ended in the background
# (``agent.manager.BACKGROUND_SOURCE``, which a test pins). A schedule's run is no row (``UNWATCHED_SOURCES``, item
# 1096), so it cannot start one.
START_EVERY_S: float = 600.0
START_AFTER_S: float = 30.0
START_AT_ONCE: frozenset[str] = frozenset({"background"})
# How often the rows at work are read for a start they have earned by working long.
START_CHECK_S: float = 5.0
# What APNs answers a push it took.
_APNS_OK: int = 200


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
    """A run as the activity shows it, when it ended (``None`` while it is at work) and the source that started it."""

    state: LiveState
    ended_at: float | None
    source: str


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

    def __init__(
        self,
        store: Store,
        sender: ApnsSender,
        leases: PhoneLeases | None = None,
        badge: Callable[[], Awaitable[int]] | None = None,
    ) -> None:
        self._store: Store = store
        self._sender: ApnsSender = sender
        # How many things wait on the owner (item 1422): the number an alert sets the app's badge to. None: no badge.
        self._badge: Callable[[], Awaitable[int]] | None = badge
        # Which phones show Dot now (item 1420): the alerts of a run's end are not sent to them. None: every alert goes.
        self._leases: PhoneLeases | None = leases
        # The runs the activity shows, by id (item 1093).
        self._rows: dict[str, _Row] = {}
        # When the activity was last told, and whether a step came since that waits its turn (item 1004).
        self._told_at: float = 0.0
        self._due: bool = False
        # When the oldest activity kept with no row to show was registered, while one is (item 1096).
        self._empty_at: float | None = None
        # When the rows at work were last read against their runs (item 1108).
        self._checked_at: float = 0.0
        self._others: set[str] = set()
        # The phones a Live Activity was started on by push (item 1425), each with when and for which runs: another
        # start waits :data:`START_EVERY_S` and a run that is new.
        self._started: dict[str, tuple[float, frozenset[str]]] = {}
        # When the rows at work were last read for a start.
        self._start_checked_at: float = 0.0

    async def _forget_if_gone(self, device: ApnsDevice, reply: ApnsReply) -> None:
        if gone(reply):
            await self._store.delete_apns_device(device.phone_id)
            log.info("apns_device_gone", phone=device.phone_id, status=reply.status, reason=reply.reason)

    async def alert(self, message: PushMessage, now: float, kind: str) -> None:
        await self._alert(alert_payload(message.title, message.route), now, kind)

    def _shown(self, phone_id: str, now: float) -> bool:
        """Whether the app of ``phone_id`` said it is in the foreground and its lease still holds at ``now``."""
        return self._leases is not None and self._leases.live(phone_id, now) is not None

    async def _badged(self, payload: JsonObject) -> JsonObject:
        """``payload`` with the badge of what waits on the owner (item 1422); as it was when that cannot be counted: an
        alert is not lost for its badge."""
        if self._badge is None:
            return payload
        try:
            return badged(payload, await self._badge())
        except DotError as error:
            log.warning("apns_badge_not_counted", error=error.message)
            return payload
        except ValidationError as error:
            # A stored record that cannot be read ends no push: the alert goes without the badge.
            log.warning("apns_badge_not_counted", error=str(error))
            return payload

    async def _alert(self, payload: JsonObject, now: float, kind: str) -> None:
        """Send ``payload``, the alert of an event of ``kind``, to every phone that is told pushes of its kind (item
        1421: each phone switches the three kinds of :data:`dotagent.notify.push.PUSH_GROUPS` for itself) and, for the
        end of a run, which the app shows itself, is not showing Dot now (item 1420)."""
        group: PushGroup | None = push_group(kind)
        quiet: bool = quiet_while_shown(kind)
        payload = await self._badged(payload)
        for device in await self._store.list_apns_devices():
            if group is not None and not (await push_settings_of(self._store, device.phone_id)).wants(group):
                log.info("apns_alert_switched_off", phone=device.phone_id, group=group)
                continue
            if quiet and self._shown(device.phone_id, now):
                log.info("apns_alert_kept_quiet", phone=device.phone_id)
                continue
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
        self._rows[run.id] = _Row(state=state, ended_at=ended_at, source=run.source)
        self._empty_at = None

    async def _tell(self, priority: PushPriority, now: float) -> None:
        """Tell every phone's activity the rows as they stand: an update where the app gave the activity's token, and
        the end once no row is left, but to one opened less than :data:`OPEN_WAIT_S` ago, which :meth:`flush` ends then
        if no row came. A phone whose app opened none is told nothing (item 1096)."""
        state: FleetState = fleet_state(self._rows)
        self._told_at = now
        self._due = False
        activities: dict[str, LiveActivity] = {
            activity.phone_id: activity for activity in await self._store.live_activities_of(FLEET_ID)
        }
        for device in await self._store.list_apns_devices():
            activity: LiveActivity | None = activities.get(device.phone_id)
            # Its age counts from its first token (a token that turns keeps it, ``Store.set_live_activity``): a new
            # activity is told only once the row of the one before is gone (its end, its age, the app's forget).
            if activity is not None and now - activity.created_at >= ACTIVITY_LIFE_S:
                # iOS has ended it: the token goes nowhere, and the app opens a new one at the next run it starts.
                await self._store.delete_live_activity(FLEET_ID, device.phone_id)
                activity = None
            if not state.rows:
                if activity is not None and now - activity.created_at < OPEN_WAIT_S:
                    opened: float = activity.created_at
                    self._empty_at = opened if self._empty_at is None else min(self._empty_at, opened)
                elif activity is not None:
                    await self._send(device, activity.token, _AT_ONCE, live_end_payload(state, now))
                    await self._store.delete_live_activity(FLEET_ID, device.phone_id)
            elif activity is not None:
                await self._send(device, activity.token, priority, live_update_payload(state, now))

    async def _send(self, device: ApnsDevice, token: str, priority: PushPriority, payload: JsonObject) -> None:
        try:
            await self._sender.send(device.environment, token, "liveactivity", priority, payload)
        except DotError as error:
            log.warning("apns_live_update_failed", phone=device.phone_id, error=error.message)

    async def _start_where_none_shows(self, now: float) -> None:
        """Start the Live Activity by push (item 1425, iOS 17.2 and later) on every phone whose app gave its
        push-to-start token, shows none, is not in the foreground (its lease, item 1420) and was not started on in
        :data:`START_EVERY_S`, when a row at work is worth it: a run nobody watches, or one that has worked
        :data:`START_AFTER_S`. The start carries the rows as they stand, and an alert with a fixed title and line, which
        Apple requires of it. The token the activity then sends goes the way it did when the app opened one
        (``POST /api/phone/apns/activity``)."""
        self._start_checked_at = now
        worth: frozenset[str] = frozenset(
            run_id
            for run_id, row in self._rows.items()
            if row.ended_at is None and (row.source in START_AT_ONCE or now - row.state.startedAt >= START_AFTER_S)
        )
        if not worth:
            return
        showing: set[str] = {activity.phone_id for activity in await self._store.live_activities_of(FLEET_ID)}
        state: FleetState = fleet_state(self._rows)
        for device in await self._store.list_apns_devices():
            if device.phone_id in showing or self._shown(device.phone_id, now):
                continue
            started: tuple[float, frozenset[str]] | None = self._started.get(device.phone_id)
            if started is not None and (now - started[0] < START_EVERY_S or worth <= started[1]):
                continue
            token: str | None = await live_start_token_of(self._store, device.phone_id)
            if token is None:
                continue
            # The attempt counts for the time, sent or not: a push service that fails is not asked again every few
            # seconds. The runs count only once a start went out for them, so that one that failed is tried again for
            # them when :data:`START_EVERY_S` is over.
            self._started[device.phone_id] = (now, frozenset() if started is None else started[1])
            try:
                reply: ApnsReply = await self._sender.send(
                    device.environment, token, "liveactivity", _AT_ONCE, live_start_payload(state, now)
                )
            except DotError as error:
                log.warning("apns_live_start_failed", phone=device.phone_id, error=error.message)
                continue
            if gone(reply):
                # Its start token is no use (the app gives a new one when iOS changes it); the device's own stands.
                await forget_live_start_token(self._store, device.phone_id)
                log.info("apns_live_start_token_gone", phone=device.phone_id, status=reply.status, reason=reply.reason)
            elif reply.status == _APNS_OK:
                self._started[device.phone_id] = (now, worth)
                log.info("apns_live_started", phone=device.phone_id, runs=len(worth))
            else:
                log.warning("apns_live_start_refused", phone=device.phone_id, status=reply.status, reason=reply.reason)

    async def on_event(self, event: EventRecord, now: float) -> None:
        """Send what ``event`` says: an alert as Web Push would, and the change of the run's row."""
        if event.kind == LIVE_REGISTERED_EVENT:
            # The app opens the activity as the run it starts is made, and its token may come before the run's start:
            # an activity of no row yet waits for it (:data:`OPEN_WAIT_S`).
            await self._tell(_AT_ONCE, now)
            return
        if event.run_id == SYSTEM_RUN_ID:
            notice: PushMessage | None = notice_message(event)
            if notice is not None:
                await self.alert(apns_message(event, notice), now, event.kind)
            return
        if event.kind in PROGRESS_KINDS:
            await self.progress(event, now)
            return
        run: RunRecord = await self._store.get_run(event.run_id)
        message: PushMessage | None = push_message(event, run) if event.kind in PUSH_KINDS else None
        answerable: str | None = approval_answerable(event)
        if message is not None and answerable is not None:
            await self._alert(approval_payload(message.title, message.route, answerable), now, event.kind)
        elif message is not None:
            await self.alert(message, now, event.kind)
        if not follows_live(run):
            # Known as no row's while it works, so that its steps are passed over without a read.
            if event.kind == "run_finished":
                self._others.discard(run.id)
            else:
                self._others.add(run.id)
            return
        if event.kind == "run_started":
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
        if event.kind == "run_started":
            await self._start_where_none_shows(now)

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
        """Take off the rows of runs that ended :data:`DISMISS_AFTER_S` ago, send the step that waited its turn once
        the last push is :data:`PROGRESS_EVERY_S` old, and end an activity that waited :data:`OPEN_WAIT_S` for a row
        in vain."""
        gone_rows: list[str] = [
            run_id
            for run_id, row in self._rows.items()
            if row.ended_at is not None and now - row.ended_at >= DISMISS_AFTER_S
        ]
        for run_id in gone_rows:
            del self._rows[run_id]
        ended_rows: bool = await self._end_rows_of_ended_runs(now)
        waited: bool = self._empty_at is not None and now - self._empty_at >= OPEN_WAIT_S
        if waited:
            self._empty_at = None
        if gone_rows or waited or (self._due and now - self._told_at >= PROGRESS_EVERY_S):
            await self._tell(_WHEN_IT_SUITS, now)
        elif ended_rows:
            await self._tell(_AT_ONCE, now)
        if self._rows and now - self._start_checked_at >= START_CHECK_S:
            await self._start_where_none_shows(now)

    async def _end_rows_of_ended_runs(self, now: float) -> bool:
        """Every :data:`CHECK_EVERY_S`, end the rows still at work whose run has ended or is gone (item 1108): a run
        closed by the store alone (the daemon's start, a command's process found gone) or whose end the feed dropped
        stood "Working" until the daemon started again. Whether a row changed."""
        if now - self._checked_at < CHECK_EVERY_S:
            return False
        self._checked_at = now
        changed: bool = False
        for run_id, row in list(self._rows.items()):
            if row.ended_at is not None:
                continue
            try:
                run: RunRecord = await self._store.get_run(run_id)
            except NotFoundError:
                del self._rows[run_id]
                changed = True
                continue
            if run.status in WORKING_STATUSES:
                continue
            ended, last = _ENDED.get(run.status, _ENDED_ELSE)
            await self._row(run, ended, last, run.finished_at or now)
            self._others.discard(run_id)
            log.info("apns_row_ended_late", run_id=run_id, status=run.status)
            changed = True
        return changed


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
