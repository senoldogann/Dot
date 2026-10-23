"""The router of the channels (item 572): what a channel receives goes into the channel's own chat ("iMessage") as the
owner's message, and what the runs of that chat come to goes back on the channel.

Inbound, a message is posted as the composer of the web UI posts one: with no run at work in the chat it starts one
(:meth:`RunManager.start_picked`), else it is queued behind it (:meth:`RunManager.queue`), with the choices the chat
keeps for its next follow-up (the owner can change them in the chat's composer). It is the owner's own message, trusted
as one typed in the UI: a channel lets a message through only when it knows it is the owner's, so the router asks
nothing more. ``/stop`` stops the run at work in the chat.

Outbound, the answer of each run of the chat is sent when the run ends, masked of the secrets Dot holds, as plain text
and cut to the channel's size with a link to the chat for the rest. A run that waits for the owner (an approval, a
question) sends one line with what it asks and the link. Approvals and questions are never answered on a channel: the
account of a channel, if taken, must not be able to approve."""

import asyncio
import time
from collections.abc import Callable, Mapping

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.manager import RunManager
from dotagent.agent.mode_words import resolved_mode
from dotagent.channels.text import as_plain_text, cut_with_link, one_line
from dotagent.channels.types import STOP_COMMAND, TITLES, Channel, ChannelId
from dotagent.core.errors import DotError, NotFoundError, RunConflict
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_known
from dotagent.events import Subscription
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import (
    NO_PICKS,
    WORKING_STATUSES,
    ChatChoices,
    ChatMode,
    EventRecord,
    RunMode,
    RunRecord,
    ThreadRecord,
)

log = get_logger(__name__)

# The events the router tells the owner of: the end of a run, and an approval or a question that waits for them.
WATCHED_KINDS: frozenset[str] = frozenset({"run_finished", "approval_requested", QUESTION_LEFT_EVENT})
# The kv key of the chat of each channel (``channels.thread.imessage``).
_THREAD_KEY: str = "channels.thread."
# A run waits for the owner a few times in a row now and then: the owner is told of the first, not of each.
ASKS_APART_S: float = 20.0
# How far up from a helper's run the run of the channel's chat is looked for.
_ROOT_DEPTH: int = 8
_ASKING_LEAD: dict[str, str] = {"question": "Dot has a question", "handoff": "Dot needs your help"}
_FINISHED_LEAD: dict[str, str] = {"failed": "The run failed.", "cancelled": "The run was stopped."}
_ASK_CHARS: int = 240
_FIRST_LINE_CHARS: int = 300
DEFAULT_CHOICES: ChatChoices = ChatChoices(
    mode="auto", model=None, effort=None, speed="standard", approvals=None, goal_mode=None
)


class ChannelRouter:
    """Posts the owner's messages of the channels into their chats and tells the owner what comes of them. ``base`` is
    the address the links open at (with its trailing slash), never one that carries the access token; ``is_on`` says
    whether a channel is switched on."""

    def __init__(
        self,
        runtime: Runtime,
        manager: RunManager,
        channels: Mapping[ChannelId, Channel],
        base: str,
        is_on: Callable[[ChannelId], bool],
        asks_apart_s: float = ASKS_APART_S,
    ) -> None:
        self._store: Store = runtime.store
        self._manager: RunManager = manager
        self._channels: Mapping[ChannelId, Channel] = channels
        self._base: str = base
        self._is_on: Callable[[ChannelId], bool] = is_on
        # The chat of each channel by its thread's id, read from the store as the service starts and made on a first
        # message; one chat is made at a time.
        self._chats: dict[str, ChannelId] = {}
        self._making: asyncio.Lock = asyncio.Lock()
        self._asks_apart_s: float = asks_apart_s
        self._asked_at: dict[ChannelId, float] = {}

    async def load(self) -> None:
        """Read which thread is the chat of each channel."""
        for channel_id in self._channels:
            stored: str | None = await self._store.kv_get(f"{_THREAD_KEY}{channel_id}")
            if stored is not None:
                self._chats[stored] = channel_id

    def link(self, run_id: str) -> str:
        """The page of ``run_id`` in Dot, as the owner opens it from the channel: the run's chat."""
        return f"{self._base}#/runs/{run_id}"

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
        at work. What fails is told to the owner on the channel."""
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
        thread: ThreadRecord = await self._thread(channel_id)
        newest: list[RunRecord] = await self._store.list_runs(1, thread.id)
        working: bool = bool(newest) and newest[0].status in WORKING_STATUSES
        if text.strip().casefold() == STOP_COMMAND:
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
        await self._say(channel_id, "Queued: the run at work in this chat goes first.", None)

    async def _stop(self, channel_id: ChannelId, working: RunRecord | None) -> None:
        """``/stop``: the run at work in the chat is stopped, as the Stop of the pages stops it."""
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
        """Tell the owner what the events of ``feed`` (:data:`WATCHED_KINDS`) say of the runs of the channels' chats,
        until cancelled. An event that cannot be told of (its run was deleted, the store failed) is logged and the next
        one still goes."""
        while True:
            event: EventRecord = await feed.queue.get()
            try:
                await self._tell_of(event)
            except DotError as error:
                log.warning("channel_event_not_told", run_id=event.run_id, kind=event.kind, error=error.message)

    async def _tell_of(self, event: EventRecord) -> None:
        run: RunRecord = await self._store.get_run(event.run_id)
        root: RunRecord = await self._root(run)
        channel_id: ChannelId | None = self._chats.get(root.thread_id)
        if channel_id is None or not self._is_on(channel_id):
            return
        if event.kind == "run_finished":
            # The end of a helper's run is not the chat's: the run that delegated it answers.
            if run.parent_run_id is None:
                await self._say(channel_id, self._answer(event), run.id)
            return
        now: float = time.monotonic()
        if now - self._asked_at.get(channel_id, -self._asks_apart_s) < self._asks_apart_s:
            log.info("channel_wait_not_told_again", channel=channel_id, run_id=root.id)
            return
        self._asked_at[channel_id] = now
        await self._say(channel_id, self._asking(event, self.link(root.id)), root.id)

    async def _root(self, run: RunRecord) -> RunRecord:
        """The run at the top of ``run``'s delegations."""
        for _ in range(_ROOT_DEPTH):
            if run.parent_run_id is None:
                break
            run = await self._store.get_run(run.parent_run_id)
        return run

    @staticmethod
    def _answer(event: EventRecord) -> str:
        """What the end of a run tells: its answer, or how it ended."""
        status: object = event.data.get("status")
        result: object = event.data.get("result")
        words: str = result.strip() if isinstance(result, str) else ""
        if status == "done":
            return words or "Done."
        lead: str = _FINISHED_LEAD.get(str(status), "The run ended.")
        first: str = one_line(words, _FIRST_LINE_CHARS) if words else ""
        return f"{lead} {first}".strip()

    @staticmethod
    def _asking(event: EventRecord, link: str) -> str:
        """The one line of a run that waits for the owner: what it asks, and where to answer."""
        severity: object = event.data.get("severity")
        args: object = event.data.get("args")
        question: object = args.get("question") if severity == "question" and isinstance(args, dict) else None
        summary: object = event.data.get("summary")
        what: str = question if isinstance(question, str) else str(summary or "")
        lead: str = _ASKING_LEAD.get(str(severity), "Dot waits for your approval")
        return f"{lead}: {one_line(what, _ASK_CHARS)}\nAnswer it in Dot, not here: {link}"

    async def _say(self, channel_id: ChannelId, text: str, run_id: str | None) -> bool:
        """Send ``text`` to the owner on ``channel_id``: masked of the secrets Dot holds, as plain text and cut to the
        channel's size, with the link to the run's page for the rest. Returns whether it went; a failure is logged."""
        channel: Channel = self._channels[channel_id]
        known: tuple[str, ...] = await asyncio.to_thread(known_secrets)
        words: str = as_plain_text(redact_known(text, known))
        link: str = self._base if run_id is None else self.link(run_id)
        try:
            await channel.send(cut_with_link(words, channel.limit, link))
        except DotError as error:
            log.warning("channel_send_failed", channel=channel_id, error=redact_known(error.message, known))
            return False
        return True

    async def tell(self, text: str, run_id: str | None = None) -> tuple[ChannelId, ...]:
        """Send ``text`` to the owner on every channel that is on and knows who to answer (a morning brief, a watcher's
        line); returns the channels it went on."""
        reached: list[ChannelId] = []
        for channel_id, channel in self._channels.items():
            if self._is_on(channel_id) and channel.paired() and await self._say(channel_id, text, run_id):
                reached.append(channel_id)
        return tuple(reached)
