"""What Dot tells the owner on its own reaches iMessage too (item 582), on a real runtime and run manager with a
scripted model, a ``chat.db`` the test writes and a runner that stands for ``osascript``. The morning brief's and the
watchers' tell (``tell_owner``, the event ``owner_told``) goes into the owner's conversation with themselves as one line
marked ◆, with the link to the page it opens, once, and only while the channel is on and paired; a burst of tells is one
line and one more after the channel's window; a secret Dot holds is masked; and the row Messages keeps of such a line
is never read back as a command."""

import asyncio
import contextlib
import dataclasses
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, FakeMacos, Osascript, Replies, finish, own_places
from messages_db import URL_BALLOON, MessagesDb

from dotagent.agent.manager import RunManager
from dotagent.channels.handles import STATE_KEY, ImessageState
from dotagent.channels.imessage import MARKER
from dotagent.channels.service import Channels
from dotagent.config import load_config
from dotagent.core.errors import DesktopError
from dotagent.core.paths import env_file_path
from dotagent.desktop.script import ScriptLanguage
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.notify.owner import tell_owner
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import write_env_value
from dotagent.store.models import RunRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

PHONE: str = "+358401234567"
# The name a phone reaches the Mac by (``[server] reach``), which the links of a line open at: with none there is no
# address a phone can open and a line carries no link (item 611).
REACH: str = "mac.example.ts.net"
BASE: str = f"https://{REACH}/"
_WAIT_S: float = 30.0
# The window of a channel between two lines of tells: short, so that a test can see a line held and then sent.
APART_S: float = 0.8


class Timed(Osascript):
    """The stand-in for ``osascript`` that also notes when each message went."""

    def __init__(self) -> None:
        super().__init__()
        self.at: list[float] = []

    async def __call__(
        self,
        language: ScriptLanguage,
        script: str,
        argv: tuple[str, ...],
        confinement: tuple[str, ...],
        timeout_s: float,
    ) -> str:
        answered: str = await super().__call__(language, script, argv, confinement, timeout_s)
        self.at.append(time.monotonic())
        return answered


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def replies() -> Replies:
    return Replies()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, replies: Replies) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nlessons = "off"\n'
        "playbooks = false\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(PROVIDER, replies)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def manager(runtime: Runtime) -> AsyncIterator[RunManager]:
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()


class World:
    """The channels of a daemon over its stand-ins."""

    def __init__(
        self, channels: Channels, runtime: Runtime, osascript: Timed, messages: MessagesDb, base: str | None = BASE
    ) -> None:
        self.channels: Channels = channels
        self.runtime: Runtime = runtime
        self.osascript: Timed = osascript
        self.messages: MessagesDb = messages
        self.base: str | None = base

    async def tell(self, title: str, text: str, route: str = "#/runs/run_1") -> None:
        """What the brief and the watchers do: tell the owner, through the one function they share."""
        await tell_owner(self.runtime.bus, title, text, route)

    def line(self, title: str, text: str, route: str = "#/runs/run_1") -> str:
        """What reaches Messages for such a tell: marked ◆, the title, a colon, the text, then the link (none without
        a name to reach the Mac by)."""
        link: str = "" if self.base is None else f"\n{self.base}{route}"
        return f"{MARKER}{title}: {text}{link}"

    async def pair(self) -> None:
        """Pair the owner's number, as the pairing keeps it in the store."""
        await self.runtime.store.kv_set(STATE_KEY, ImessageState(handles=(PHONE,)).model_dump_json())
        await self.channels.imessage.load()

    async def runs(self) -> list[RunRecord]:
        """The runs of the iMessage chat, oldest first; none while it has no chat."""
        stored: str | None = await self.runtime.store.kv_get("channels.thread.imessage")
        return [] if stored is None else list(reversed(await self.runtime.store.list_runs(50, stored)))


@contextlib.asynccontextmanager
async def serving(
    runtime: Runtime,
    manager: RunManager,
    folder: Path,
    *,
    reach: list[str] | None = None,
    paired: bool = True,
    on: bool = True,
) -> AsyncIterator[World]:
    """The channels as a daemon runs them, with the owner's number paired (or not) and iMessage switched on (or off).
    ``reach`` is the name a phone reaches the Mac by, which the links of a line open at (:data:`REACH` unless a test
    says otherwise; an empty list is a Mac with no such name)."""
    names: list[str] = [REACH] if reach is None else reach
    configured: Runtime = dataclasses.replace(
        runtime,
        config=runtime.config.model_copy(update={"server": runtime.config.server.model_copy(update={"reach": names})}),
    )
    messages: MessagesDb = MessagesDb(folder / "chat.db")
    osascript: Timed = Timed()
    await runtime.store.kv_set(STATE_KEY, ImessageState(handles=(PHONE,) if paired else ()).model_dump_json())
    channels: Channels = Channels(
        configured,
        manager,
        "127.0.0.1",
        8765,
        script=osascript,
        messages_db=messages.path,
        macos=FakeMacos(),
        poll_s=0.05,
        asks_apart_s=APART_S,
        **own_places(),
    )
    await channels.start()
    try:
        if on:
            await channels.switch("imessage", True)
        yield World(
            channels,
            runtime,
            osascript,
            messages,
            base=f"https://{names[0]}/" if names else None,
        )
    finally:
        await channels.stop()
        messages.close()


@pytest_asyncio.fixture(loop_scope="module")
async def world(runtime: Runtime, manager: RunManager, tmp_path: Path) -> AsyncIterator[World]:
    async with serving(runtime, manager, tmp_path) as made:
        yield made


async def _until(condition: Callable[[], bool | Awaitable[bool]], what: str) -> None:
    deadline: float = time.monotonic() + _WAIT_S
    while time.monotonic() < deadline:
        found: bool | Awaitable[bool] = condition()
        if await found if isinstance(found, Awaitable) else found:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"not in {_WAIT_S:g} s: {what}")


async def _reading(world: World) -> None:
    """The channel's first poll set its cursor: what is written in the conversation from now on is read."""

    async def has_cursor() -> bool:
        kept: str | None = await world.runtime.store.kv_get(STATE_KEY)
        return kept is not None and ImessageState.model_validate_json(kept).cursor is not None

    await _until(has_cursor, "the first poll")


async def test_a_tell_reaches_a_paired_imessage_once_marked_and_is_never_read_back_as_a_command(
    world: World, replies: Replies
) -> None:
    replies.clear()
    await _reading(world)
    await world.tell("Your morning brief", "Nothing needs you today.", "#/runs/run_brief")
    await _until(lambda: len(world.osascript.sent) == 1, "the line reached Messages")
    line: str = world.line("Your morning brief", "Nothing needs you today.", "#/runs/run_brief")
    assert world.osascript.sent == [(PHONE, line)] and line.startswith(MARKER)
    await asyncio.sleep(0.3)
    assert len(world.osascript.sent) == 1
    # Messages keeps what Dot sent as a row the owner sent, in the owner's own conversation. It is not a command: the
    # message after it, which is, is the only run.
    before: int = len(await world.runs())
    own: int = world.messages.chat(PHONE)
    world.messages.message(own, line, at=time.time())
    replies.push(finish("Four."))
    world.messages.message(own, "what is two and two?", at=time.time())
    await _until(lambda: len(world.osascript.sent) == 2, "the answer to the message that is a command")
    assert world.osascript.sent[1] == (PHONE, f"{MARKER}Four.")
    runs: list[RunRecord] = await world.runs()
    assert [run.goal for run in runs[before:]] == ["what is two and two?"]


async def test_the_link_messages_splits_off_a_tell_is_never_read_as_a_command(world: World, replies: Replies) -> None:
    """Messages may keep the link at the end of a message as a row of its own, a link preview, flagged as the owner's
    and with no ◆: as a link preview or as plain words, it is Dot's own and is not run."""
    replies.clear()
    await _reading(world)
    await world.tell("Bank mail", "Mail 1 from the bank", "#/runs/run_1")
    await _until(lambda: len(world.osascript.sent) == 1, "the line reached Messages")
    link: str = f"{BASE}#/runs/run_1"
    assert world.osascript.sent[0][1].endswith(f"\n{link}")
    before: int = len(await world.runs())
    own: int = world.messages.chat(PHONE)
    world.messages.message(own, link, balloon=URL_BALLOON, at=time.time())
    world.messages.message(own, link, at=time.time())
    replies.push(finish("Four."))
    world.messages.message(own, "what is two and two?", at=time.time())
    await _until(lambda: len(world.osascript.sent) == 2, "the answer to the message that is a command")
    assert [run.goal for run in (await world.runs())[before:]] == ["what is two and two?"]


async def test_the_line_opens_a_page_of_dot_at_the_address_its_links_open_at(
    runtime: Runtime, manager: RunManager, tmp_path: Path
) -> None:
    async with serving(runtime, manager, tmp_path, reach=["mac.example.ts.net"]) as named:
        await named.tell("Watchers", "Mail from the bank", "#/schedules")
        await _until(lambda: len(named.osascript.sent) == 1, "the line reached Messages")
        assert named.osascript.sent == [
            (PHONE, f"{MARKER}Watchers: Mail from the bank\nhttps://mac.example.ts.net/#/schedules")
        ]
        # Never an address that carries the access token, which a message would hand to the messaging service.
        assert "token" not in named.osascript.sent[0][1]


async def test_with_no_name_a_phone_reaches_the_mac_by_the_lines_carry_no_link(
    runtime: Runtime, manager: RunManager, tmp_path: Path
) -> None:
    """This Mac's own address, 127.0.0.1, opens nothing on a phone: without ``[server] reach`` a line of tells is the
    tell and nothing after it (item 611), and what the tab says of links says why."""
    async with serving(runtime, manager, tmp_path, reach=[]) as bare:
        assert bare.base is None and bare.channels.router.link("run_1") is None
        for number in range(1, 5):
            await bare.tell("Bank mail", f"Mail {number} from the bank", f"#/runs/run_{number}")
        await _until(lambda: len(bare.osascript.sent) == 1, "the first line")
        assert bare.osascript.sent[0] == (PHONE, f"{MARKER}Bank mail: Mail 1 from the bank")
        await _until(lambda: len(bare.osascript.sent) == 2, "the line of the rest")
        assert bare.osascript.sent[1] == (PHONE, f"{MARKER}Bank mail: and 3 more")
        assert not any("http" in line or "127.0.0.1" in line for _, line in bare.osascript.sent)
        assert (await bare.channels.view()).imessage.links is None


async def test_with_a_name_the_view_of_the_tab_says_where_the_links_open(
    runtime: Runtime, manager: RunManager, tmp_path: Path
) -> None:
    async with serving(runtime, manager, tmp_path) as named:
        assert (await named.channels.view()).imessage.links == BASE


async def test_a_channel_that_is_off_or_has_no_one_paired_is_told_nothing(world: World) -> None:
    await world.channels.switch("imessage", False)
    await world.tell("Off", "Not while the channel is off.")
    await asyncio.sleep(0.3)
    await world.channels.switch("imessage", True)
    await world.channels.imessage.remove(PHONE)
    await world.tell("Unpaired", "Nowhere to send it.")
    await asyncio.sleep(0.3)
    assert world.osascript.sent == []
    # Those two were let go, not kept for later: the next one is the first line.
    await world.pair()
    await world.tell("Back", "Now it goes.")
    await _until(lambda: len(world.osascript.sent) == 1, "the line reached Messages")
    await asyncio.sleep(APART_S * 1.5)
    assert world.osascript.sent == [(PHONE, world.line("Back", "Now it goes."))]


async def test_a_burst_of_tells_is_one_line_and_the_rest_one_line_after_the_window(world: World) -> None:
    for number in range(1, 5):
        await world.tell("Bank mail", f"Mail {number} from the bank", f"#/runs/run_{number}")
    await _until(lambda: len(world.osascript.sent) == 1, "the first line")
    assert world.osascript.sent[0] == (PHONE, world.line("Bank mail", "Mail 1 from the bank", "#/runs/run_1"))
    await _until(lambda: len(world.osascript.sent) == 2, "the line of the rest")
    assert world.osascript.sent[1] == (PHONE, f"{MARKER}Bank mail: and 3 more\n{BASE}#/runs/run_4")
    # At most one line of tells in a window, and nothing after the two.
    assert world.osascript.at[1] - world.osascript.at[0] >= APART_S
    await asyncio.sleep(APART_S * 1.5)
    assert len(world.osascript.sent) == 2


async def test_one_tell_inside_the_window_goes_as_itself_after_it(world: World) -> None:
    await world.tell("Bank mail", "First", "#/runs/run_1")
    await world.tell("Landlord", "Second", "#/schedules")
    await _until(lambda: len(world.osascript.sent) == 2, "both lines")
    assert world.osascript.sent == [
        (PHONE, world.line("Bank mail", "First", "#/runs/run_1")),
        (PHONE, world.line("Landlord", "Second", "#/schedules")),
    ]
    assert world.osascript.at[1] - world.osascript.at[0] >= APART_S


async def test_tells_of_different_titles_are_folded_under_dots_own_name(world: World) -> None:
    await world.tell("Bank mail", "One", "#/runs/run_1")
    await world.tell("Landlord", "Two", "#/runs/run_2")
    await world.tell("GitHub", "Three", "#/runs/run_3")
    await _until(lambda: len(world.osascript.sent) == 2, "both lines")
    assert world.osascript.sent[1] == (PHONE, f"{MARKER}Dot: and 2 more\n{BASE}#/runs/run_3")


async def test_a_tell_after_a_quiet_window_goes_at_once_again(world: World) -> None:
    await world.tell("Bank mail", "Before", "#/runs/run_1")
    await _until(lambda: len(world.osascript.sent) == 1, "the first line")
    await asyncio.sleep(APART_S + 0.2)
    await world.tell("Bank mail", "After", "#/runs/run_2")
    await _until(lambda: len(world.osascript.sent) == 2, "the second line")
    # It was not held for a window: the one before it was over.
    assert world.osascript.at[1] - world.osascript.at[0] < APART_S * 2 + 0.2
    assert world.osascript.sent[1] == (PHONE, world.line("Bank mail", "After", "#/runs/run_2"))


async def test_a_line_is_masked_of_the_secrets_dot_holds(world: World) -> None:
    secret: str = "hunter2-horse-battery-staple"
    write_env_value(env_file_path(), "SOME_SERVICE_PASSWORD_FILE", secret)
    await world.tell("Bank mail", f"Your code is {secret}.")
    await _until(lambda: len(world.osascript.sent) == 1, "the line reached Messages")
    sent: str = world.osascript.sent[0][1]
    assert secret not in sent
    assert sent == world.line("Bank mail", "Your code is ***.")


async def test_the_tells_held_when_the_channel_is_switched_off_are_let_go(world: World) -> None:
    await world.tell("First", "Goes at once.", "#/runs/run_1")
    await _until(lambda: len(world.osascript.sent) == 1, "the first line")
    await world.tell("Second", "Is held for the window.", "#/runs/run_2")
    await asyncio.sleep(0.2)
    await world.channels.switch("imessage", False)
    await asyncio.sleep(APART_S + 0.3)
    assert len(world.osascript.sent) == 1
    await world.channels.switch("imessage", True)
    await world.tell("Third", "Goes after the channel is on again.", "#/runs/run_3")
    await _until(lambda: len(world.osascript.sent) == 2, "the line after the switch")
    await asyncio.sleep(APART_S * 1.5)
    assert world.osascript.sent[1] == (
        PHONE,
        world.line("Third", "Goes after the channel is on again.", "#/runs/run_3"),
    )
    assert len(world.osascript.sent) == 2


async def test_a_line_that_could_not_go_is_not_sent_again_and_the_next_tell_still_goes(world: World) -> None:
    world.osascript.error = DesktopError("osascript failed", {"stderr": "Not authorized to send Apple events (-1743)"})
    await world.tell("First", "Messages refuses it.", "#/runs/run_1")
    await asyncio.sleep(0.3)
    assert world.osascript.sent == []
    world.osascript.error = None
    await world.tell("Second", "Messages takes it.", "#/runs/run_2")
    await _until(lambda: len(world.osascript.sent) == 1, "the next line")
    await asyncio.sleep(APART_S * 1.5)
    assert world.osascript.sent == [(PHONE, world.line("Second", "Messages takes it.", "#/runs/run_2"))]
