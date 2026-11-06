"""What Dot's iPhone app hears of the daemon (item 997): the events the web app's Web Push tells as alerts, and the
Live Activity of each run of the owner's, from its start to its end.

A run of the owner's (no run delegated it, and no schedule, watcher, own initiative or reply started it) starts a Live
Activity on every phone whose app gave a push-to-start token; the app sends back the token of the activity, which the
updates and the end go to. The activity says the run works, waits for the owner (an approval or a question asks), works
again once answered, and ends done, failed or stopped."""

import asyncio
import time

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
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
from dotagent.store.models import ApnsDevice, EventRecord, RunRecord

log = get_logger(__name__)

# What the app hears: the alerts of Web Push, a run's start, and an answer that lets a waiting run work again.
APNS_KINDS: frozenset[str] = frozenset({*PUSH_KINDS, "run_started", "approval_decided"})
_WAITS: frozenset[str] = frozenset({"approval_requested", QUESTION_LEFT_EVENT})
_ENDED: dict[str, LiveStatus] = {"done": "done", "failed": "failed", "cancelled": "failed", "blocked": "failed"}
_ENDED_LINE: dict[str, str] = {"done": "Done", "failed": "Failed", "cancelled": "Stopped", "blocked": "Blocked"}
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
            await self._forget_if_gone(device, reply)

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
            await self._to_activities(run, "waiting", "Waiting for you", now, False)
        elif event.kind == "approval_decided":
            await self._to_activities(run, "working", "Working", now, False)
        elif event.kind == "run_finished":
            status: str = str(event.data.get("status", run.status))
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
