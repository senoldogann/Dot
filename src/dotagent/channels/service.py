"""The channels of one daemon (item 572): the channels, their router, the tasks that read them while they are on, and
what Settings › Channels shows and does. The daemon starts it after the runs the last start cut off are resumed, so that
no message of a channel reaches a chat before them, and stops it before the manager."""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Protocol

from dotagent.agent.manager import RunManager
from dotagent.channels.imessage import (
    MESSAGES_BUNDLE,
    POLL_S,
    Access,
    IMessageChannel,
    Script,
    database_access,
)
from dotagent.channels.router import ASKS_APART_S, WATCHED_KINDS, ChannelRouter
from dotagent.channels.seen import Seen, SeenError
from dotagent.channels.types import Channel, ChannelId
from dotagent.channels.views import AutomationAnswer, ChannelsView, ImessageView
from dotagent.connections import mac
from dotagent.connections.mac import AutomationState
from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.core.text import redact_known
from dotagent.desktop import perms
from dotagent.desktop.codes import chat_db_path
from dotagent.desktop.script import run_osascript
from dotagent.events import Subscription
from dotagent.runtime import Runtime
from dotagent.secrets import known_secrets
from dotagent.server.links import base_url

log = get_logger(__name__)

# A task of the service that stopped with an error is started again after this long.
RESTART_S: float = 30.0
# How long a step waits for the owner's answer to macOS's question before it answers that the question waits; the
# question stays on the Mac until it is answered.
ASK_WAIT_S: float = 60.0


class Macos(Protocol):
    """What the service asks macOS: whether the app that runs Dot may control an application, asked without a question
    for the owner and then with one, and the name of that app."""

    def automation(self, bundle_id: str) -> AutomationState: ...

    def ask(self, bundle_id: str) -> AutomationState: ...

    def program(self) -> str | None: ...


class RealMacos:
    def automation(self, bundle_id: str) -> AutomationState:
        return mac.automation_state(bundle_id)

    def ask(self, bundle_id: str) -> AutomationState:
        """macOS's question for the owner whether the app that runs Dot may control ``bundle_id``, the application
        started hidden first when it does not run, as Customize › Connectors asks it; blocks until it is answered."""
        state: AutomationState = mac.automation_state(bundle_id)
        if state == "not_open":
            state = mac.start_hidden(bundle_id)
        return mac.ask_automation(bundle_id) if state == "not_asked" else state

    def program(self) -> str | None:
        try:
            return perms.program_name(perms.responsible_program())
        except DesktopError as error:
            log.warning("responsible_program_unknown", error=error.message)
            return None


async def _kept_up(name: str, work: Callable[[], Awaitable[None]]) -> None:
    """Run ``work`` until cancelled; one that stops with an error is logged, with its traceback, and run again after a
    pause, so that a channel does not stay silent for a fault of one message."""
    while True:
        try:
            await work()
        except Exception:
            log.exception("channel_task_failed", task=name)
        await asyncio.sleep(RESTART_S)


class Channels:
    """The channels a daemon runs. ``script`` runs the AppleScript that sends an iMessage (a test gives its own),
    ``messages_db`` is Messages' database, ``macos`` answers the questions about permissions."""

    def __init__(
        self,
        runtime: Runtime,
        manager: RunManager,
        host: str,
        port: int,
        *,
        script: Script = run_osascript,
        messages_db: Path | None = None,
        macos: Macos | None = None,
        poll_s: float = POLL_S,
        ask_wait_s: float = ASK_WAIT_S,
        asks_apart_s: float = ASKS_APART_S,
    ) -> None:
        self._runtime: Runtime = runtime
        self._macos: Macos = RealMacos() if macos is None else macos
        self._ask_wait_s: float = ask_wait_s
        # The question macOS asks the owner now, if one is: it ends with their answer.
        self._asking: asyncio.Future[AutomationState] | None = None
        self._messages_db: Path = chat_db_path() if messages_db is None else messages_db
        self._on: dict[ChannelId, bool] = {"imessage": runtime.config.channels.imessage}
        # The links of a message go to the name a phone reaches Dot by (``[server] reach``, the address the phone's own
        # pages open at). With none there is no address a phone can open (item 611): this Mac's own, 127.0.0.1, opens
        # nothing on a phone, so no link is sent, and the tab says why. Never an address that carries the access token,
        # which a message would hand to the messaging service. The channel is told this Mac's address too when there is
        # no name: a row that is only a link into it is Dot's own, never a command of the owner's.
        reach: list[str] = runtime.config.server.reach
        self._links: str | None = f"https://{reach[0]}/" if reach else None
        self._imessage: IMessageChannel = IMessageChannel(
            runtime.store,
            script,
            self._messages_db,
            runtime.config.desktop.osascript_timeout_s,
            self._links or f"{base_url(host, port)}/",
            poll_s,
        )
        self._channels: dict[ChannelId, Channel] = {"imessage": self._imessage}
        self._router: ChannelRouter = ChannelRouter(
            runtime, manager, self._channels, self._links, self.is_on, asks_apart_s
        )
        self._tasks: dict[ChannelId, asyncio.Task[None]] = {}
        self._watcher: asyncio.Task[None] | None = None
        self._stack: contextlib.AsyncExitStack = contextlib.AsyncExitStack()
        # One switch and one pairing step at a time.
        self.changing: asyncio.Lock = asyncio.Lock()

    @property
    def router(self) -> ChannelRouter:
        return self._router

    @property
    def imessage(self) -> IMessageChannel:
        return self._imessage

    def is_on(self, channel_id: ChannelId) -> bool:
        return self._on[channel_id]

    async def start(self) -> None:
        """Read what the channels kept, follow the runs of their chats, and read each channel that is on."""
        await self._imessage.load()
        await self._router.load()
        if self._links is None:
            log.info("channel_links_not_sent", why="[server] reach names no address a phone can open")
        feed: Subscription = await self._stack.enter_async_context(self._runtime.bus.subscribe_kinds(WATCHED_KINDS))
        self._watcher = asyncio.create_task(_kept_up("watch", partial(self._router.watch, feed)), name="channels-watch")
        for channel_id, on in self._on.items():
            if on:
                self._launch(channel_id)

    async def stop(self) -> None:
        tasks: list[asyncio.Task[None]] = [*self._tasks.values()]
        if self._watcher is not None:
            tasks.append(self._watcher)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._watcher = None
        await self._stack.aclose()

    def _launch(self, channel_id: ChannelId) -> None:
        channel: Channel = self._channels[channel_id]
        self._tasks[channel_id] = asyncio.create_task(
            _kept_up(channel_id, partial(channel.run, partial(self._router.inbound, channel_id))),
            name=f"channel-{channel_id}",
        )

    async def switch(self, channel_id: ChannelId, on: bool) -> None:
        """Switch a channel on or off at once. Switched on, it reads what comes in from then on: what arrived while it
        was off is not run."""
        if self._on[channel_id] == on:
            return
        if on:
            await self._channels[channel_id].start_from_now()
            self._on[channel_id] = True
            self._launch(channel_id)
            log.info("channel_switched_on", channel=channel_id)
            return
        self._on[channel_id] = False
        task: asyncio.Task[None] | None = self._tasks.pop(channel_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        log.info("channel_switched_off", channel=channel_id)

    # ----- what the page shows ---------------------------------------------------------------

    async def view(self) -> ChannelsView:
        return ChannelsView(imessage=await self._imessage_view())

    async def _imessage_view(self) -> ImessageView:
        access: Access = "granted"
        problem: str | None = self._imessage.problem
        try:
            access = await asyncio.to_thread(database_access, self._messages_db)
        except DesktopError as error:
            problem = error.message
        automation: AutomationAnswer = "unknown"
        try:
            automation = await asyncio.to_thread(self._macos.automation, MESSAGES_BUNDLE)
        except DesktopError as error:
            log.warning("automation_state_unknown", error=error.message)
        return ImessageView(
            on=self._on["imessage"],
            handles=self._imessage.handles,
            verifying=self._imessage.verifying(),
            database=access,
            automation=automation,
            problem=problem,
            needs=self._imessage.needs,
            chat_run_id=await self._router.chat_run_id("imessage"),
            program=await asyncio.to_thread(self._macos.program),
            seen=await self._seen(),
            links=self._links,
        )

    async def _seen(self) -> Seen:
        """What the channel sees of Messages' database (item 595), the last error masked of the secrets Dot holds: what
        osascript says of a message that did not go may quote it."""
        seen: Seen = self._imessage.seen()
        if seen.error is None:
            return seen
        known: tuple[str, ...] = await asyncio.to_thread(known_secrets)
        masked: SeenError = seen.error.model_copy(update={"message": redact_known(seen.error.message, known)})
        return seen.model_copy(update={"error": masked})

    async def poll_now(self) -> None:
        """Look at Messages' database at once (Settings › Channels, **Check now**). With the channel on and a handle
        paired it is a poll as the loop's own, and reads and runs what is new; otherwise it only looks, and reads no
        message, so that the switch keeps its meaning."""
        if self._on["imessage"] and self._imessage.paired():
            await self._imessage.poll(partial(self._router.inbound, "imessage"))
        else:
            await self._imessage.look()

    async def ask_macos_about_messages(self) -> None:
        """Show macOS's question whether Dot may control Messages, one at a time, and wait a while for the owner's
        answer; a question not answered in time stays on the Mac, and the page reads the answer when it comes."""
        question: asyncio.Future[AutomationState] | None = self._asking
        if question is None:
            question = asyncio.ensure_future(asyncio.to_thread(self._macos.ask, MESSAGES_BUNDLE))
            self._asking = question
            question.add_done_callback(self._answered)
        try:
            await asyncio.wait_for(asyncio.shield(question), self._ask_wait_s)
        except TimeoutError:
            log.info("automation_question_waits", waited_s=self._ask_wait_s)

    def _answered(self, done: asyncio.Future[AutomationState]) -> None:
        self._asking = None
        if not done.cancelled() and done.exception() is not None:
            log.warning("automation_question_failed", error=str(done.exception()))
