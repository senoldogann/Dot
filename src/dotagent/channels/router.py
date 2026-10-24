"""The router of the channels (item 572): what a channel receives goes into the channel's own chat ("iMessage") as the
owner's message, and what the runs of that chat come to goes back on the channel.

Inbound, a message is posted as the composer of the web UI posts one: with no run at work in the chat it starts one
(:meth:`RunManager.start_picked`), else it is queued behind it (:meth:`RunManager.queue`), with the choices the chat
keeps for its next follow-up (the owner can change them in the chat's composer). It is the owner's own message, trusted
as one typed in the UI: a channel lets a message through only when it knows it is the owner's, so the router asks
nothing more but one thing (item 611): while the owner's emergency stop is engaged a message starts no run and queues
nothing, and the owner is told so. ``/stop``, or a message of the plain stop words, stops the run at work in the chat
whatever the emergency stop, and a channel's pairing is not the router's, so the owner can still be reached.

Outbound, the answer of each run of the chat is sent when the run ends, masked of the secrets Dot holds, as plain text
and cut to the channel's size with a link to the chat for the rest. A run that waits for the owner (an approval, a
question) sends one line with what it asks and the link. Approvals and questions are never answered on a channel: the
account of a channel, if taken, must not be able to approve. The links are made from ``[server] reach``, the name a
phone reaches Dot by; with none there is no address a phone can open, and no link is sent (item 611).

What Dot tells the owner on its own (the morning brief and the watchers, ``owner_told`` of ``notify/owner.py``, item
582) goes out on each channel that is on and paired as one line: the title, the text and the link to the page it opens.
A channel gets one such line in a window of :data:`ASKS_APART_S`; the tells that come inside it are held and told after
it as one line, "and N more". The lines of runs that wait for the owner keep a window of their own in the same way: the
first goes at once, and those that come inside it are held and told when it ends, never dropped (item 611)."""

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.manager import RunManager
from dotagent.agent.mode_words import resolved_mode
from dotagent.channels.intake import UNREAD_ANSWER, UNREAD_NOTE
from dotagent.channels.text import as_plain_text, cut_with_link, one_line
from dotagent.channels.types import TITLES, Channel, ChannelId, is_stop
from dotagent.core.errors import DotError, NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.paths import failsafe_path
from dotagent.core.text import redact_known
from dotagent.events import Subscription
from dotagent.notify.owner import OWNER_TOLD_EVENT, OwnerTold
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import (
    NO_PICKS,
    WORKING_STATUSES,
    ApprovalRecord,
    ChatChoices,
    ChatMode,
    EventRecord,
    RunMode,
    RunRecord,
    ThreadRecord,
)

log = get_logger(__name__)

# The events the router tells the owner of: the end of a run, an approval or a question that waits for them, and what
# Dot tells them on its own (item 582).
WATCHED_KINDS: frozenset[str] = frozenset({"run_finished", "approval_requested", QUESTION_LEFT_EVENT, OWNER_TOLD_EVENT})
# The kv key of the chat of each channel (``channels.thread.imessage``).
_THREAD_KEY: str = "channels.thread."
# A run waits for the owner a few times in a row now and then: the owner is told of the first at once, and of the waits
# that come inside this time as one line when it is over. A watcher tells a few things in a row too: a channel gets one
# line of them in this time, and the rest as one line after it.
ASKS_APART_S: float = 20.0
# The title of a line that holds tells of different titles.
_FOLDED_TITLE: str = "Dot"
# How far up from a helper's run the run of the channel's chat is looked for.
_ROOT_DEPTH: int = 8
_ASKING_LEAD: dict[str, str] = {"question": "Dot has a question", "handoff": "Dot needs your help"}
_FINISHED_LEAD: dict[str, str] = {"failed": "The run failed.", "cancelled": "The run was stopped."}
_ASK_CHARS: int = 240
_FIRST_LINE_CHARS: int = 300
# The statuses of an approval or a question that still waits for the owner.
_OPEN: frozenset[str] = frozenset({"pending", "left"})
# What a message that comes while the owner's emergency stop is engaged is answered (item 611): nothing was started, and
# how to go on. The banner of an engaged stop, on every page, has the button that releases it.
STOPPED: str = (
    "Dot is stopped: the emergency stop is engaged, so nothing was started. Release it in Dot (the banner at the top "
    "of any page has the button), then send the message again."
)
# What a message that comes while a run works is answered: it waits, and the owner is told how to stop the run.
QUEUED: str = "Queued: the run at work in this chat goes first. Send /stop to stop it."
DEFAULT_CHOICES: ChatChoices = ChatChoices(
    mode="auto", model=None, effort=None, speed="standard", approvals=None, goal_mode=None
)


async def _emergency_stop_engaged() -> bool:
    """Whether the owner's emergency stop is engaged: its STOP file exists, as the scheduler and the watchers look."""
    return await asyncio.to_thread(failsafe_path().exists)


@dataclass
class _Held:
    """What Dot told the owner on its own while a channel's window was not over: how many tells, the title they share
    (``None`` once two differ) and the newest, whose page the line opens."""

    count: int
    title: str | None
    newest: OwnerTold


@dataclass(frozen=True)
class _HeldAsk:
    """A wait for the owner (an approval, a question) that came while a channel's window of ask lines was not over: its
    event, and the run at the top of its delegations, whose page the line opens (item 611)."""

    event: EventRecord
    run_id: str


class ChannelRouter:
    """Posts the owner's messages of the channels into their chats and tells the owner what comes of them. ``base`` is
    the address the links open at (with its trailing slash), never one that carries the access token, and ``None`` when
    Dot has no address a phone can open (no ``[server] reach``): then no link is sent, and the lines say where to look
    in words; ``is_on`` says whether a channel is switched on."""

    def __init__(
        self,
        runtime: Runtime,
        manager: RunManager,
        channels: Mapping[ChannelId, Channel],
        base: str | None,
        is_on: Callable[[ChannelId], bool],
        asks_apart_s: float = ASKS_APART_S,
    ) -> None:
        self._store: Store = runtime.store
        self._manager: RunManager = manager
        self._channels: Mapping[ChannelId, Channel] = channels
        self._base: str | None = base
        self._is_on: Callable[[ChannelId], bool] = is_on
        # The chat of each channel by its thread's id, read from the store as the service starts and made on a first
        # message; one chat is made at a time.
        self._chats: dict[str, ChannelId] = {}
        self._making: asyncio.Lock = asyncio.Lock()
        self._asks_apart_s: float = asks_apart_s
        # The lines of runs that wait for the owner (item 611): when each channel was last sent one, and the waits that
        # came inside its window, told when it ends.
        self._asked_at: dict[ChannelId, float] = {}
        self._held_asks: dict[ChannelId, list[_HeldAsk]] = {}
        # What Dot told the owner on its own (item 582): when each channel was last sent such a line, and what waits
        # for its window to end.
        self._told_at: dict[ChannelId, float] = {}
        self._held: dict[ChannelId, _Held] = {}

    async def load(self) -> None:
        """Read which thread is the chat of each channel."""
        for channel_id in self._channels:
            stored: str | None = await self._store.kv_get(f"{_THREAD_KEY}{channel_id}")
            if stored is not None:
                self._chats[stored] = channel_id

    def link(self, run_id: str) -> str | None:
        """The page of ``run_id`` in Dot, as the owner opens it from the channel: the run's chat; ``None`` when Dot has
        no address a phone can open (item 611)."""
        return None if self._base is None else f"{self._base}#/runs/{run_id}"

    async def chat_run_id(self, channel_id: ChannelId) -> str | None:
        """The newest run of the channel's chat, which the page of the channel links to; ``None`` before the first."""
        stored: str | None = await self._store.kv_get(f"{_THREAD_KEY}{channel_id}")
        if stored is None:
            return None
        try:
            runs: list[RunRecord] = await self._store.list_runs(1, stored)
        except NotFoundError:
            return None
        return runs[0].id if runs else None

    # ----- inbound ---------------------------------------------------------------------------

    async def inbound(self, channel_id: ChannelId, text: str) -> None:
        """Post the owner's ``text`` into the chat of ``channel_id``: a run starts on it, or it is queued behind the run
        at work; while the emergency stop is engaged neither happens, and the owner is told so (item 611). What fails
        is told to the owner on the channel."""
        try:
            await self._post(channel_id, text)
        except DotError as error:
            log.warning("channel_message_not_posted", channel=channel_id, error=error.message, context=error.context)
            await self._say(channel_id, f"Dot could not start on that: {error.message}.", None)
        except Exception:
            # Whatever else went wrong: the traceback is in the log, and the owner, who sees no answer, is told so.
            log.exception("channel_message_failed", channel=channel_id)
            await self._say(channel_id, "Dot failed on that message. The log has what went wrong.", None)

    async def _post(self, channel_id: ChannelId, text: str) -> None:
        stop: bool = is_stop(text)
        if not stop and await _emergency_stop_engaged():
            # A message on a channel is a way to start a run from away, and the owner's emergency stop holds every
            # way a run starts without the owner at the Mac: the scheduler, the campaigns and the watchers look at the
            # same file (item 611). The message starts nothing and queues nothing (a queued one starts its run when
            # the one at work ends, stop or no stop), and the owner, who sees no run, is told why and how to go on.
            # ``/stop`` is run in any case, and so is pairing, which is not the router's.
            log.info("channel_message_refused_emergency_stop", channel=channel_id, chars=len(text))
            await self._say(channel_id, STOPPED, None)
            return
        thread: ThreadRecord = await self._thread(channel_id)
        newest: list[RunRecord] = await self._store.list_runs(1, thread.id)
        working: bool = bool(newest) and newest[0].status in WORKING_STATUSES
        if stop:
            await self._stop(channel_id, newest[0] if working else None)
            return
        choices: ChatChoices = await self._store.chat_choices(thread.id) or DEFAULT_CHOICES
        asked: ChatMode = choices.mode
        mode: RunMode = resolved_mode(asked, text)
        # A chat has no goal mode: the choice is kept for a task or research message.
        goal_mode: bool | None = None if mode == "chat" else choices.goal_mode
        if not working:
            try:
                run: RunRecord = await self._manager.start_picked(
                    text,
                    mode,
                    choices.model,
                    thread.id,
                    channel_id,
                    None,
                    choices.effort,
                    choices.speed,
                    None,
                    choices.approvals,
                    goal_mode,
                    (),
                    NO_PICKS,
                )
            except RunConflict:
                # A run started on the chat since the newest one was read (the owner typed in its page): queued.
                log.info("channel_message_queued_after_a_race", channel=channel_id)
            else:
                log.info("channel_message_started_a_run", channel=channel_id, run_id=run.id, chars=len(text))
                if await _emergency_stop_engaged():
                    # The stop came while the run was being started: it ends as one, as a schedule's run does.
                    log.warning("channel_run_stopped_emergency_stop", channel=channel_id, run_id=run.id)
                    self._manager.end_by_emergency_stop(run.id)
                return
        await self._manager.queue(
            thread.id,
            text,
            (),
            mode,
            choices.model,
            choices.effort,
            choices.speed,
            None,
            NO_PICKS,
            choices.approvals,
            goal_mode,
            None,
            channel_id,
        )
        log.info("channel_message_queued", channel=channel_id, thread_id=thread.id, chars=len(text))
        await self._say(channel_id, QUEUED, None)

    async def _stop(self, channel_id: ChannelId, working: RunRecord | None) -> None:
        """``/stop``, or a message of the plain stop words (item 611): the run at work in the chat is stopped, as the
        Stop of the pages stops it."""
        if working is None:
            await self._say(channel_id, "No run is at work in this chat.", None)
            return
        try:
            await self._manager.stop(working.id)
        except RunConflict:
            await self._say(channel_id, "No run is at work in this chat.", None)
            return
        log.info("channel_run_stopped", channel=channel_id, run_id=working.id)
        await self._say(channel_id, "Stopping the run.", working.id)

    async def _thread(self, channel_id: ChannelId) -> ThreadRecord:
        """The chat of ``channel_id``, made with the first message, and again when the owner deleted it."""
        key: str = f"{_THREAD_KEY}{channel_id}"
        async with self._making:
            stored: str | None = await self._store.kv_get(key)
            if stored is not None:
                try:
                    return await self._store.get_thread(stored)
                except NotFoundError:
                    log.info("channel_chat_made_again", channel=channel_id)
            thread: ThreadRecord = await self._store.create_thread(TITLES[channel_id], channel_id)
            # The choices the chat's runs start with, kept as the composer of a chat keeps them (item 199): the
            # owner changes them there, and the messages of the channel follow.
            await self._store.set_chat_choices(thread.id, DEFAULT_CHOICES)
            await self._store.kv_set(key, thread.id)
            self._chats[thread.id] = channel_id
            return thread

    # ----- outbound --------------------------------------------------------------------------

    async def watch(self, feed: Subscription) -> None:
        """Tell the owner what the events of ``feed`` (:data:`WATCHED_KINDS`) say of the runs of the channels' chats and
        what Dot tells on its own, until cancelled. The tells and the waits held for a channel's window go out when it
        ends, between two events. An event that cannot be told of (its run was deleted, the store failed) is logged and
        the next one still goes."""
        while True:
            try:
                event: EventRecord = await asyncio.wait_for(feed.queue.get(), self._held_for())
            except TimeoutError:
                await self._tell_held()
                continue
            try:
                await self._tell_of(event)
            except DotError as error:
                log.warning("channel_event_not_told", run_id=event.run_id, kind=event.kind, error=error.message)

    async def _tell_of(self, event: EventRecord) -> None:
        if event.kind == OWNER_TOLD_EVENT:
            # An event of Dot itself: it has no run, and no chat of a channel is its own.
            await self._told_on_its_own(OwnerTold.model_validate(event.data))
            return
        run: RunRecord = await self._store.get_run(event.run_id)
        root: RunRecord = await self._root(run)
        channel_id: ChannelId | None = self._chats.get(root.thread_id)
        if channel_id is None or not self._is_on(channel_id):
            return
        if event.kind == "run_finished":
            # The end of a helper's run is not the chat's: the run that delegated it answers.
            if run.parent_run_id is None:
                await self._say(channel_id, self._answer(event, run.goal.endswith(UNREAD_NOTE)), run.id)
            return
        # A wait for the owner: the first of a window goes at once, and one that comes inside it is held and told when
        # it ends, never dropped (item 611): a run that stays blocked must reach the owner, and a run that leaves a
        # question and goes on to need an approval is the usual way to meet two within the window.
        self._held_asks.setdefault(channel_id, []).append(_HeldAsk(event, root.id))
        await self._tell_held()
        if channel_id in self._held_asks:
            log.info("channel_wait_held", channel=channel_id, run_id=root.id)

    async def _root(self, run: RunRecord) -> RunRecord:
        """The run at the top of ``run``'s delegations."""
        for _ in range(_ROOT_DEPTH):
            if run.parent_run_id is None:
                break
            run = await self._store.get_run(run.parent_run_id)
        return run

    @staticmethod
    def _answer(event: EventRecord, unread: bool = False) -> str:
        """What the end of a run tells: its answer, or how it ended. The answer of a run whose message came with a
        picture or a file that was not read (``unread``, item 611) starts with a line that says so. The words of how a
        run ended are said once: a stopped run's result is the sentence that leads them."""
        status: object = event.data.get("status")
        result: object = event.data.get("result")
        words: str = result.strip() if isinstance(result, str) else ""
        if status == "done":
            answer: str = words or "Done."
            return f"{UNREAD_ANSWER}\n\n{answer}" if unread else answer
        lead: str = _FINISHED_LEAD.get(str(status), "The run ended.")
        first: str = one_line(words, _FIRST_LINE_CHARS) if words else ""
        return first if first.startswith(lead) else f"{lead} {first}".strip()

    @staticmethod
    def _asking(event: EventRecord, link: str | None, more: int = 0) -> str:
        """The one line of a run that waits for the owner: what it asks, and where to answer; with ``more`` other waits
        held with it (item 611), how many. ``link`` is ``None`` when Dot has no address a phone can open: the line says
        where in words."""
        severity: object = event.data.get("severity")
        args: object = event.data.get("args")
        question: object = args.get("question") if severity == "question" and isinstance(args, dict) else None
        summary: object = event.data.get("summary")
        what: str = question if isinstance(question, str) else str(summary or "")
        lead: str = _ASKING_LEAD.get(str(severity), "Dot waits for your approval")
        also: str = f" (and {more} more)" if more else ""
        where: str = f"Answer {'them' if more else 'it'} in Dot"
        pointer: str = f"{where} on your Mac, not here." if link is None else f"{where}, not here: {link}"
        return f"{lead}: {one_line(what, _ASK_CHARS)}{also}\n{pointer}"

    async def _told_on_its_own(self, owner: OwnerTold) -> None:
        """What Dot tells the owner on its own (the morning brief, a watcher) goes on each channel that is on and
        paired, once the channel's window is over. The window is each channel's own, and the tells that come inside it
        are held for the line after it."""
        for channel_id in self._channels:
            if self._reachable(channel_id):
                self._hold(channel_id, owner)
        await self._tell_held()

    def _hold(self, channel_id: ChannelId, owner: OwnerTold) -> None:
        held: _Held | None = self._held.get(channel_id)
        if held is None:
            self._held[channel_id] = _Held(1, owner.title, owner)
            return
        held.count += 1
        held.title = held.title if held.title == owner.title else None
        held.newest = owner

    async def _tell_held(self) -> None:
        """Send the line of each channel whose window is over: the tell held for it, or how many are held, and the
        wait of a run for the owner held for it (item 611), or how many. A channel that was switched off, or lost its
        pairing, while its lines were held is not told, and no window starts."""
        for channel_id in tuple(self._held):
            if time.monotonic() < self._may_tell_at(channel_id):
                continue
            held: _Held = self._held.pop(channel_id)
            if self._reachable(channel_id):
                await self._say(channel_id, self._told_line(held), None)
                self._told_at[channel_id] = time.monotonic()
                log.info("channel_owner_told", channel=channel_id, tells=held.count)
        for channel_id in tuple(self._held_asks):
            if time.monotonic() < self._may_ask_at(channel_id):
                continue
            asks: list[_HeldAsk] = self._held_asks.pop(channel_id)
            if self._reachable(channel_id):
                await self._tell_asks(channel_id, asks)

    async def _tell_asks(self, channel_id: ChannelId, asks: list[_HeldAsk]) -> None:
        """Send the one line of the waits held for a channel: the newest of those that still wait, with how many more
        wait. A wait the owner answered, or that expired with its run, while it was held is not told: the line about
        it would be false."""
        waiting: list[_HeldAsk] = [ask for ask in asks if await self._still_waits(ask)]
        if not waiting:
            log.info("channel_waits_over_before_told", channel=channel_id, waits=len(asks))
            return
        newest: _HeldAsk = waiting[-1]
        await self._say(
            channel_id, self._asking(newest.event, self.link(newest.run_id), len(waiting) - 1), newest.run_id
        )
        self._asked_at[channel_id] = time.monotonic()
        log.info("channel_wait_told", channel=channel_id, waits=len(waiting))

    async def _still_waits(self, ask: _HeldAsk) -> bool:
        """Whether the approval or the question of ``ask`` is still open. One the store does not know is taken as open:
        nothing says it was answered."""
        approval_id: object = ask.event.data.get("approval_id")
        if not isinstance(approval_id, str):
            return True
        try:
            record: ApprovalRecord = await self._store.get_approval(approval_id)
        except DotError:
            return True
        return record.status in _OPEN

    def _held_for(self) -> float | None:
        """The seconds until the first window ends that has lines held for it; ``None`` when none is held."""
        ends: list[float] = [self._may_tell_at(channel_id) for channel_id in self._held]
        ends += [self._may_ask_at(channel_id) for channel_id in self._held_asks]
        if not ends:
            return None
        return max(0.0, min(ends) - time.monotonic())

    def _may_tell_at(self, channel_id: ChannelId) -> float:
        """When (``time.monotonic``) the window of ``channel_id`` since its last line of tells ends."""
        return self._told_at.get(channel_id, -self._asks_apart_s) + self._asks_apart_s

    def _may_ask_at(self, channel_id: ChannelId) -> float:
        """When (``time.monotonic``) the window of ``channel_id`` since its last line about a wait ends."""
        return self._asked_at.get(channel_id, -self._asks_apart_s) + self._asks_apart_s

    def _told_line(self, held: _Held) -> str:
        """The one line of the tells held for a channel: the tell itself when it is the only one, else how many there
        are under the title they share ("Dot" when they differ). The link opens the page of the newest: the route of a
        tell is one of Dot's own pages (:func:`~dotagent.notify.owner.told`), at the address the router's links open
        at; with no such address (item 611) the line has none."""
        pointer: str = "" if self._base is None else f"\n{self._base}{held.newest.url}"
        if held.count == 1:
            return f"{held.newest.title}: {held.newest.text}{pointer}"
        return f"{held.title or _FOLDED_TITLE}: and {held.count} more{pointer}"

    def _reachable(self, channel_id: ChannelId) -> bool:
        """Whether the owner can be told on ``channel_id``: it is switched on and knows who to answer."""
        return self._is_on(channel_id) and self._channels[channel_id].paired()

    async def _say(self, channel_id: ChannelId, text: str, run_id: str | None) -> None:
        """Send ``text`` to the owner on ``channel_id``: masked of the secrets Dot holds, as plain text and cut to the
        channel's size, with the link to the run's page for the rest (or, with no address a phone can open, the words
        that say it is in Dot). A failure is logged."""
        channel: Channel = self._channels[channel_id]
        known: tuple[str, ...] = await asyncio.to_thread(known_secrets)
        words: str = as_plain_text(redact_known(text, known))
        link: str | None = self._base if run_id is None else self.link(run_id)
        try:
            await channel.send(cut_with_link(words, channel.limit, link))
        except DotError as error:
            log.warning("channel_send_failed", channel=channel_id, error=redact_known(error.message, known))
