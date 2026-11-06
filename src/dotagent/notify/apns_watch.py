"""What Dot's iPhone app hears of the daemon (item 997): the events the web app's Web Push tells as alerts, and the
Live Activity of each run of the owner's, from its start to its end.

A run of the owner's (no run delegated it, and no schedule, watcher, own initiative or reply started it) starts a Live
Activity on every phone whose app gave a push-to-start token; the app sends back the token of the activity, which the
updates and the end go to. The activity says the run works, waits for the owner (an approval or a question asks), works
again once answered, and ends done, failed or stopped."""

import asyncio
import time

from dotagent.core.errors import DotError
from dotagent.core.logs import get_logger
from dotagent.evals import EVAL_SOURCE
from dotagent.events import SYSTEM_RUN_ID, Subscription
from dotagent.notify.apns import (
    ApnsReply,
    ApnsSender,
    LiveState,
    LiveStatus,
    alert_payload,
    gone,
    live_end_payload,
    live_start_payload,
    live_update_payload,
    short_title,
)
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
APNS_KINDS: frozenset[str] = frozenset({*PUSH_KINDS, "run_started", "approval_decided", LIVE_REGISTERED_EVENT})
# What makes a run wait for the owner: an approval or a question it asks. A question it leaves for later does not.
_WAITS: frozenset[str] = frozenset({"approval_requested"})
_ENDED: dict[str, LiveStatus] = {"done": "done", "failed": "failed", "cancelled": "failed"}
_ENDED_LINE: dict[str, str] = {"done": "Done", "failed": "Failed", "cancelled": "Stopped"}
_WAITING_LINE: str = "Waiting for you"
_WAIT_S: float = 1.0
_DOT: str = "Dot"
# The title of an alert of a message Dot wrote to the owner.
TOLD_TITLE: str = "Dot has a message for you"


def follows_live(run: RunRecord) -> bool:
    """Whether a run has a Live Activity: the owner's own, not a helper's, a schedule's or Dot's own work."""
    return run.parent_run_id is None and run.source not in UNWATCHED_SOURCES and run.source != EVAL_SOURCE


def live_state(title: str, status: LiveStatus, line: str, run: RunRecord) -> LiveState:
    return LiveState(title=short_title(title), status=status, line=line, startedAt=run.created_at)


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

    async def _forget_if_gone(self, device: ApnsDevice, reply: ApnsReply) -> None:
        if gone(reply):
            await self._store.delete_apns_device(device.phone_id)
            log.info("apns_device_gone", phone=device.phone_id, status=reply.status, reason=reply.reason)

    async def alert(self, message: PushMessage) -> None:
        for device in await self._store.list_apns_devices():
            try:
                reply: ApnsReply = await self._sender.send(
                    device.environment, device.device_token, "alert", alert_payload(message.title, message.route)
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
        payload = live_start_payload(run.id, agent, live_state(title, "working", "Working", run), now)
        for device in await self._store.list_apns_devices():
            if device.start_token is None:
                continue
            try:
                reply: ApnsReply = await self._sender.send(
                    device.environment, device.start_token, "liveactivity", payload
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

    async def _to_activities(self, run: RunRecord, status: LiveStatus, line: str, now: float, ended: bool) -> None:
        activities = await self._store.live_activities_of(run.id)
        if not activities:
            return
        title, _ = await self._title_and_agent(run)
        state: LiveState = live_state(title, status, line, run)
        payload = live_end_payload(state, now) if ended else live_update_payload(state, now)
        devices: dict[str, ApnsDevice] = {device.phone_id: device for device in await self._store.list_apns_devices()}
        for activity in activities:
            device: ApnsDevice | None = devices.get(activity.phone_id)
            if device is None:
                continue
            try:
                await self._sender.send(device.environment, activity.token, "liveactivity", payload)
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
        run: RunRecord = await self._store.get_run(event.run_id)
        message: PushMessage | None = push_message(event, run) if event.kind in PUSH_KINDS else None
        if message is not None:
            await self.alert(message)
        if not follows_live(run):
            return
        if event.kind == "run_started":
            await self.start(run, now)
        elif event.kind in _WAITS:
            await self._to_activities(run, "waiting", _WAITING_LINE, now, False)
        elif event.kind == "approval_decided":
            await self._to_activities(run, "working", "Working", now, False)
        elif event.kind == "run_finished":
            await self.end(run, str(event.data.get("status", run.status)), now)

    async def catch_up(self, run: RunRecord, now: float) -> None:
        """Bring the Live Activities of ``run`` to where it stands now: whatever happened before the app's token came
        (an approval asked, the end) went to no activity, so the one just registered is told the run's state."""
        if run.status == "blocked":
            await self._to_activities(run, "waiting", _WAITING_LINE, now, False)
        elif run.status not in WORKING_STATUSES:
            await self.end(run, run.status, now)

    async def end(self, run: RunRecord, status: str, now: float) -> None:
        """End the Live Activities of ``run``, which ended with ``status``, and forget them."""
        await self._to_activities(run, _ENDED.get(status, "failed"), _ENDED_LINE.get(status, "Ended"), now, True)


async def watch_apns(watch: ApnsWatch, feed: Subscription, stopping: asyncio.Event) -> None:
    """Send the app what the events of ``feed`` (:data:`APNS_KINDS`) say, until the daemon starts to stop; an event
    that cannot be sent is logged and the next one still goes."""
    while not stopping.is_set():
        try:
            event: EventRecord = await asyncio.wait_for(feed.queue.get(), _WAIT_S)
        except TimeoutError:
            continue
        try:
            await watch.on_event(event, time.time())
        except DotError as error:
            log.warning("apns_event_failed", run_id=event.run_id, kind=event.kind, error=error.message)
