"""Settings › Channels (item 572) in a real browser, wide and on a phone, against a real server whose channels run on
a ``chat.db`` the test makes and a runner that stands for ``osascript``: the tab stands beside Phone in the nav, says
where iMessage stands (what macOS has granted, who is paired), pairs a number with the code typed back, switches the
channel on, forgets a number, and shows what keeps the channel from working with the step that sets it right."""

import asyncio
import os
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio
from channel_stand_ins import FakeMacos, Osascript
from messages_db import MessagesDb
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.channels import service as channels_service
from dotagent.channels.imessage import STATE_KEY, ImessageState
from dotagent.channels.seen import WAL_UNSEEN, wal_path
from dotagent.channels.service import Channels
from dotagent.config import load_config
from dotagent.core.errors import ChannelError, DesktopError
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "channels-page-token"
PHONE: str = "+358401234567"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (1280, "dark"), (390, "light"))
# How far past the screen's edge the page may reach: none, but the rounding of a fraction.
_OVER_PX: int = 1
_WAIT_S: float = 15.0
_RUNS_AS_YOU: str = "Messages from this address run as you"
_OVERFLOW: str = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


class Served:
    """The address of the daemon, and what its channels stand on."""

    def __init__(
        self, address: str, channels: Channels, osascript: Osascript, macos: FakeMacos, messages: MessagesDb
    ) -> None:
        self.address: str = address
        self.channels: Channels = channels
        self.osascript: Osascript = osascript
        self.macos: FakeMacos = macos
        self.messages: MessagesDb = messages

    @property
    def port(self) -> int:
        return int(self.address.rpartition(":")[2])


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[memory]\nembed_enabled = false\n", encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Served]:
    manager: RunManager = RunManager(runtime)
    messages: MessagesDb = MessagesDb(tmp_path_factory.mktemp("messages") / "chat.db")
    osascript: Osascript = Osascript()
    macos: FakeMacos = FakeMacos()
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    channels: Channels = Channels(
        runtime, manager, "127.0.0.1", port, script=osascript, messages_db=messages.path, macos=macos, poll_s=0.05
    )
    await channels.start()
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
        channels=channels,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        yield Served(f"http://127.0.0.1:{port}", channels, osascript, macos, messages)
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await channels.stop()
        await manager.shutdown()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def browser(runtime: Runtime) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


async def _opened(browser: Browser, served: Served, width: int, scheme: Literal["light", "dark"]) -> Page:
    context: BrowserContext = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
    )
    page: Page = await context.new_page()
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", served.port, TOKEN))
    await page.goto(f"{served.address}/#/settings/channels")
    await expect(page.get_by_role("group", name="iMessage", exact=True)).to_be_visible()
    return page


async def _fits(page: Page, label: str) -> None:
    over: object = await page.evaluate(_OVERFLOW)
    assert isinstance(over, int | float)
    assert over <= _OVER_PX, f"{label}: {over} px past the screen's edge"


async def _until(condition: Callable[[], bool]) -> None:
    deadline: float = time.monotonic() + _WAIT_S
    while not condition():
        assert time.monotonic() < deadline, "not in time"
        await asyncio.sleep(0.05)


async def _said(page: Page, text: str) -> None:
    """A toast says ``text``; it is taken away (a click does it), so that the next one is the only one."""
    toast: Locator = page.locator(".toast", has_text=text)
    await expect(toast).to_have_count(1)
    await toast.click()
    await expect(toast).to_have_count(0)


def _row(page: Page, title: str) -> Locator:
    return page.locator(".set-row").filter(has=page.get_by_text(title, exact=True))


def _fact(page: Page, label: str) -> Locator:
    """What the facts of Messages' database say under ``label``."""
    seen: Locator = _row(page, "What Dot sees in Messages")
    return seen.locator("dl > div").filter(has=page.get_by_text(label, exact=True)).locator("dd")


def _code_sent(served: Served) -> str:
    return "".join(char for char in served.osascript.sent[-1][1].split("code is ")[1][:7] if char.isdigit())


async def test_the_tab_stands_beside_phone_and_says_where_imessage_stands(browser: Browser, served: Served) -> None:
    for width, scheme in _SHOTS:
        page: Page = await _opened(browser, served, width, scheme)
        card: Locator = page.get_by_role("group", name="iMessage", exact=True)
        await expect(card.get_by_role("switch", name="iMessage")).to_have_attribute("aria-checked", "false")
        await expect(card.get_by_text("None paired yet.")).to_be_visible()
        # Pairing shows the code reached a person, not whose number it is: the tab says to pair one's own only.
        await expect(
            card.get_by_text("Pair only your own number or Apple ID email: the conversation with yourself.")
        ).to_be_visible()
        await expect(_row(page, "Full Disk Access").locator(".badge")).to_have_text("granted")
        await expect(_row(page, "Automation for Messages").locator(".badge")).to_have_text("allowed")
        await expect(
            card.get_by_text("Lets Dot send Dot's answers through Messages, each starting with ◆.")
        ).to_be_visible()
        # What the channel sees of Messages' database (item 595), before it has looked, with Check now.
        seen: Locator = _row(page, "What Dot sees in Messages")
        await expect(seen.get_by_role("button", name="Check now")).to_be_visible()
        await expect(seen.locator("dt")).to_have_text(
            ["Newest message", "Read up to", "Last looked", "chat.db", "chat.db-wal", "Last command", "Last error"]
        )
        await expect(seen.get_by_text("Dot has not looked yet.")).to_be_visible()
        if width >= 1280:
            nav: Locator = page.get_by_role("navigation", name="Settings")
            labels: Locator = nav.locator(".settings-nav-label")
            await expect(labels.nth(8)).to_have_text("Phone")
            await expect(labels.nth(9)).to_have_text("Channels")
            await expect(nav.locator("[aria-current='page'] .settings-nav-label")).to_have_text("Channels")
            await expect(page.get_by_role("heading", level=1)).to_have_text("Channels")
        await _fits(page, f"{width} {scheme}")
        await page.context.close()


async def test_a_number_is_paired_with_the_code_typed_back_and_the_switch_turns_the_channel_on(
    browser: Browser, served: Served
) -> None:
    page: Page = await _opened(browser, served, 1280, "light")
    card: Locator = page.get_by_role("group", name="iMessage", exact=True)
    field: Locator = card.get_by_label("Your phone number or Apple ID email")
    await field.fill("0401234567")
    await card.get_by_role("button", name="Send a code").click()
    await _said(page, "country code")
    await field.fill("+358 40 123 4567")
    await card.get_by_role("button", name="Send a code").click()
    await expect(card.get_by_text(f"A code went to {PHONE}.")).to_be_visible()
    handle, text = served.osascript.sent[-1]
    assert handle == PHONE and text.startswith("◆ ")
    code: str = _code_sent(served)
    entry: Locator = card.get_by_label("The code you read on your iPhone")
    await entry.fill("000000" if code != "000000" else "111111")
    await card.get_by_role("button", name="Confirm").click()
    await _said(page, "tries are left")
    await entry.fill(f"{code[:3]} {code[3:]}")
    await card.get_by_role("button", name="Confirm").click()
    await expect(card.locator(".channel-handle")).to_have_text(PHONE)
    # What pairing means stands with the handle: what it writes to this Mac runs as the owner's command (item 595).
    await expect(card.locator(".channel-paired").filter(has_text=PHONE)).to_contain_text(_RUNS_AS_YOU)
    await expect(card.get_by_text(_RUNS_AS_YOU)).to_have_count(1)
    await expect(card.get_by_text("None paired yet.")).to_have_count(0)
    await expect(card.get_by_label("The code you read on your iPhone")).to_have_count(0)
    assert served.channels.imessage.handles == (PHONE,)
    # The switch turns the channel on at once; a page opened again finds it on.
    await card.get_by_role("switch", name="iMessage").click()
    await expect(card.get_by_role("switch", name="iMessage")).to_have_attribute("aria-checked", "true")
    assert served.channels.is_on("imessage")
    await page.reload()
    await expect(
        page.get_by_role("group", name="iMessage", exact=True).get_by_role("switch", name="iMessage")
    ).to_have_attribute("aria-checked", "true")
    await page.context.close()


async def test_a_paired_number_is_forgotten_with_its_button(browser: Browser, served: Served) -> None:
    page: Page = await _opened(browser, served, 390, "light")
    card: Locator = page.get_by_role("group", name="iMessage", exact=True)
    await expect(card.locator(".channel-handle")).to_have_text(PHONE)
    await expect(card.get_by_text(_RUNS_AS_YOU)).to_be_visible()
    await expect(card.get_by_text("pairing someone else's gives them Dot")).to_be_visible()
    await _fits(page, "390 with a number paired")
    await card.get_by_role("button", name=f"Stop reading {PHONE}").click()
    await expect(card.get_by_text("None paired yet.")).to_be_visible()
    assert served.channels.imessage.handles == ()
    await page.context.close()


async def test_what_macos_has_not_granted_is_shown_with_the_step_that_sets_it_right(
    browser: Browser, served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(channels_service, "database_access", lambda db: "missing")
    served.macos.state = "refused"
    try:
        page: Page = await _opened(browser, served, 1280, "light")
        database: Locator = _row(page, "Full Disk Access")
        await expect(database.locator(".badge")).to_have_text("missing")
        await expect(database.get_by_role("button", name="Open Full Disk Access")).to_be_visible()
        automation: Locator = _row(page, "Automation for Messages")
        await expect(automation.locator(".badge")).to_have_text("refused")
        await expect(automation.get_by_role("button", name="Open Automation")).to_be_visible()
        served.macos.state = "not_asked"
        await page.get_by_role("button", name="Check again").click()
        await expect(automation.locator(".badge")).to_have_text("not asked yet")
        await automation.get_by_role("button", name="Ask macOS").click()
        await _until(lambda: served.macos.asked == ["com.apple.MobileSMS"])
        await _fits(page, "1280 with permissions missing")
        await page.context.close()
    finally:
        served.macos.state = "allowed"


async def test_what_keeps_the_channel_from_working_is_said_on_the_tab(
    browser: Browser, served: Served, runtime: Runtime
) -> None:
    # A number that was paired (as a restart finds it) and a Messages that refuses the answer Dot sends.
    await runtime.store.kv_set(STATE_KEY, ImessageState(handles=(PHONE,)).model_dump_json())
    await served.channels.imessage.load()
    served.osascript.error = DesktopError("osascript failed", {"stderr": "Not authorized to send Apple events (-1743)"})
    try:
        with pytest.raises(ChannelError, match="Not authorized"):
            await served.channels.imessage.send("an answer")
        page: Page = await _opened(browser, served, 1280, "dark")
        note: Locator = page.get_by_role("group", name="iMessage", exact=True).locator(".note")
        await expect(note).to_contain_text("macOS has not let the app that runs Dot control Messages")
        await page.context.close()
    finally:
        served.osascript.error = None
        await runtime.store.kv_set(STATE_KEY, ImessageState().model_dump_json())
        await served.channels.imessage.load()


async def test_check_now_looks_at_once_and_says_the_row_it_took_as_a_command(
    browser: Browser, served: Served, runtime: Runtime
) -> None:
    """A number paired with the channel on: a message the owner writes after the page is open is read when they press
    Check now (or by the loop a moment before: the page says the same), and the facts name the row."""
    before: int = served.messages.message(served.messages.chat("+358409999999"), "to a friend", at=time.time())
    await runtime.store.kv_set(STATE_KEY, ImessageState(handles=(PHONE,), cursor=before).model_dump_json())
    await served.channels.imessage.load()
    try:
        page: Page = await _opened(browser, served, 1280, "light")
        seen: Locator = _row(page, "What Dot sees in Messages")
        await expect(seen.get_by_text("Last command")).to_be_visible()
        # /stop needs no model: with no run at work Dot says so, and the row is the command it took.
        wanted: int = served.messages.message(served.messages.chat(PHONE), "/stop", at=time.time())
        await seen.get_by_role("button", name="Check now").click()
        await _said(page, f"Dot took row {wanted} as a command.")
        await expect(_fact(page, "Last command")).to_contain_text(f"row {wanted}, taken")
        await expect(_fact(page, "Newest message")).to_contain_text(f"row {wanted}, dated")
        await expect(_fact(page, "Read up to")).to_have_text(f"row {wanted}")
        await _until(lambda: served.osascript.sent[-1][1].startswith("◆ No run is at work"))
        # A press that finds nothing new says what it saw.
        await seen.get_by_role("button", name="Check now").click()
        await _said(page, f"Dot looked: the newest message is row {wanted}")
        await _fits(page, "1280 after Check now")
        await page.context.close()
    finally:
        await runtime.store.kv_set(STATE_KEY, ImessageState().model_dump_json())
        await served.channels.imessage.load()


async def test_messages_writing_its_log_while_dot_sees_nothing_new_is_warned_on_the_tab(
    browser: Browser, served: Served
) -> None:
    imessage = served.channels.imessage
    await imessage.look()
    # The log changed two minutes and more ago (here: five), and nothing Dot reads changed since.
    changed: float = time.time() - 300
    os.utime(wal_path(served.messages.path), (changed, changed))
    await imessage.look()
    page: Page = await _opened(browser, served, 390, "light")
    card: Locator = page.get_by_role("group", name="iMessage", exact=True)
    warning: Locator = card.locator(".note", has_text=WAL_UNSEEN)
    await expect(warning).to_have_count(1)
    await _fits(page, "390 with the warning")
    # The row Dot could not see is read: the warning goes.
    served.messages.message(served.messages.chat("+358409999999"), "a new one", at=time.time())
    await imessage.look()
    await page.get_by_role("button", name="Check again").click()
    await expect(warning).to_have_count(0)
    await page.context.close()
