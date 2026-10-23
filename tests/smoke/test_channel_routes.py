"""The iMessage channel end to end (item 572), on a real runtime, run manager and server with a scripted model, a
``chat.db`` the test writes and a runner that stands for ``osascript``: the owner pairs a number with a code typed back,
switches the channel on, and a message they send to themselves starts a run in the "iMessage" chat whose answer comes
back to them marked ◆, while no other conversation is ever read as a command."""

import asyncio
import dataclasses
import threading
import time
import tomllib
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, FakeMacos, Osascript, Replies, finish
from messages_db import URL_BALLOON, MessagesDb

from dotagent.agent.manager import RunManager
from dotagent.channels.imessage import MARKER
from dotagent.channels.service import Channels
from dotagent.config import ChannelsConfig, load_config
from dotagent.core.errors import DesktopError
from dotagent.core.paths import config_path
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import RunRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "channel-routes-test-token"
PHONE: str = "+358401234567"
_WAIT_S: float = 30.0


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


@pytest.fixture(scope="module")
def messages(tmp_path_factory: pytest.TempPathFactory) -> MessagesDb:
    return MessagesDb(tmp_path_factory.mktemp("messages") / "chat.db")


class World:
    """The channels of a daemon over its stand-ins, and a client of its API."""

    def __init__(
        self,
        channels: Channels,
        runtime: Runtime,
        http: httpx.AsyncClient,
        osascript: Osascript,
        macos: FakeMacos,
        messages: MessagesDb,
    ) -> None:
        self.channels: Channels = channels
        self.runtime: Runtime = runtime
        self.http: httpx.AsyncClient = http
        self.osascript: Osascript = osascript
        self.macos: FakeMacos = macos
        self.messages: MessagesDb = messages

    async def get(self, path: str) -> JsonObject:
        answered: httpx.Response = await self.http.get(path, headers={"Authorization": f"Bearer {TOKEN}"})
        assert answered.status_code == 200, answered.text
        body: JsonObject = answered.json()
        return body

    async def post(self, path: str, body: JsonObject, status: int = 200) -> JsonObject:
        answered: httpx.Response = await self.http.post(path, json=body, headers={"Authorization": f"Bearer {TOKEN}"})
        assert answered.status_code == status, answered.text
        result: JsonObject = answered.json()
        return result

    async def runs(self) -> list[RunRecord]:
        """The runs of the iMessage chat, oldest first; none while it has no chat."""
        stored: str | None = await self.runtime.store.kv_get("channels.thread.imessage")
        return [] if stored is None else list(reversed(await self.runtime.store.list_runs(50, stored)))


def _view(body: JsonObject) -> JsonObject:
    imessage: object = body["imessage"]
    assert isinstance(imessage, dict)
    return imessage


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(runtime: Runtime, manager: RunManager, messages: MessagesDb) -> AsyncIterator[World]:
    osascript: Osascript = Osascript()
    macos: FakeMacos = FakeMacos()
    channels: Channels = Channels(
        runtime,
        manager,
        "127.0.0.1",
        8765,
        script=osascript,
        messages_db=messages.path,
        macos=macos,
        poll_s=0.05,
        ask_wait_s=0.3,
    )
    await channels.start()
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
        channels=channels,
    )
    transport: httpx.ASGITransport = httpx.ASGITransport(app=create_app(context))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as http:
        try:
            yield World(channels, runtime, http, osascript, macos, messages)
        finally:
            await channels.stop()


async def _until(condition: Callable[[], bool | Awaitable[bool]], what: str) -> None:
    deadline: float = time.monotonic() + _WAIT_S
    while time.monotonic() < deadline:
        found: bool | Awaitable[bool] = condition()
        if await found if isinstance(found, Awaitable) else found:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"not in {_WAIT_S:g} s: {what}")


def _code_sent(osascript: Osascript) -> str:
    return "".join(char for char in osascript.sent[-1][1].split("code is ")[1][:7] if char.isdigit())


async def test_the_page_shows_a_channel_that_is_off_with_no_one_paired(world: World) -> None:
    imessage: JsonObject = _view(await world.get("/api/channels"))
    assert imessage == {
        "on": False,
        "handles": [],
        "verifying": None,
        "database": "granted",
        "automation": "allowed",
        "problem": None,
        "needs": None,
        "chat_run_id": None,
        "program": "Dot",
    }


async def test_the_routes_need_the_token(world: World) -> None:
    for answered in (
        await world.http.get("/api/channels"),
        await world.http.post("/api/channels/imessage/switch", json={"on": True}),
        await world.http.post("/api/channels/imessage/code", json={"handle": PHONE}),
    ):
        assert answered.status_code == 401


async def test_a_number_is_paired_by_the_code_sent_to_it_typed_back(world: World) -> None:
    refused: JsonObject = await world.post("/api/channels/imessage/code", {"handle": "0401234567"}, 422)
    assert "country code" in str(refused["message"])
    view: JsonObject = _view(await world.post("/api/channels/imessage/code", {"handle": "+358 40 123 4567"}))
    verifying: object = view["verifying"]
    assert isinstance(verifying, dict) and verifying["handle"] == PHONE
    assert view["handles"] == []
    handle, text = world.osascript.sent[-1]
    assert handle == PHONE and text.startswith(MARKER) and "Settings › Channels" in text
    code: str = _code_sent(world.osascript)
    wrong: JsonObject = await world.post("/api/channels/imessage/confirm", {"code": "000000"}, 422)
    assert "tries are left" in str(wrong["message"])
    paired: JsonObject = _view(await world.post("/api/channels/imessage/confirm", {"code": f"{code[:3]} {code[3:]}"}))
    assert paired["handles"] == [PHONE] and paired["verifying"] is None


async def test_a_message_the_owner_sends_to_themselves_starts_a_run_and_its_answer_comes_back_marked(
    world: World, replies: Replies
) -> None:
    replies.clear()
    own: int = world.messages.chat(PHONE)
    switched: JsonObject = _view(await world.post("/api/channels/imessage/switch", {"on": True}))
    assert switched["on"] is True
    written: object = tomllib.loads(config_path().read_text(encoding="utf-8")).get("channels")
    assert written == {"imessage": True}
    # What reaches the Mac in other conversations, or from other people, is never a command.
    world.messages.message(world.messages.chat("+358409999999"), "to a friend", at=time.time())
    world.messages.message(world.messages.chat("chat555", style=43), "to a group", at=time.time())
    world.messages.message(own, "a text from them", from_me=False, at=time.time())
    replies.push(finish("Four, said **plainly**."))
    sent_before: int = len(world.osascript.sent)
    world.messages.message(own, "what is two and two?", at=time.time())
    await _until(lambda: len(world.osascript.sent) == sent_before + 1, "the answer was sent")
    assert world.osascript.sent[-1] == (PHONE, f"{MARKER}Four, said plainly.")
    (run,) = await world.runs()
    assert (run.goal, run.source, run.status) == ("what is two and two?", "imessage", "done")
    chat: JsonObject = _view(await world.get("/api/channels"))
    assert chat["chat_run_id"] == run.id and chat["on"] is True
    # Dot's own answer, which landed in the same conversation as the owner's, is not read as one.
    world.messages.message(own, f"{MARKER}Four, said plainly.", at=time.time())
    await asyncio.sleep(0.4)
    assert len(await world.runs()) == 1


async def test_switched_off_the_channel_reads_nothing_and_switched_on_again_it_starts_from_now(
    world: World, replies: Replies
) -> None:
    replies.clear()
    own: int = world.messages.chat(PHONE)
    off: JsonObject = _view(await world.post("/api/channels/imessage/switch", {"on": False}))
    assert off["on"] is False
    assert tomllib.loads(config_path().read_text(encoding="utf-8"))["channels"] == {"imessage": False}
    world.messages.message(own, "sent while it is off", at=time.time())
    await asyncio.sleep(0.4)
    assert len(await world.runs()) == 1
    await world.post("/api/channels/imessage/switch", {"on": True})
    await asyncio.sleep(0.4)
    assert len(await world.runs()) == 1
    replies.push(finish("Now it reads."))
    sent_before: int = len(world.osascript.sent)
    world.messages.message(own, "sent after it was switched on", at=time.time())
    await _until(lambda: len(world.osascript.sent) == sent_before + 1, "the answer after the switch")
    assert [run.goal for run in await world.runs()] == ["what is two and two?", "sent after it was switched on"]


async def test_a_question_macos_has_not_had_answered_stays_asked_and_the_page_does_not_wait_for_it(
    world: World,
) -> None:
    world.macos.asked.clear()
    world.macos.gate = threading.Event()
    try:
        started: float = time.monotonic()
        await world.post("/api/channels/imessage/check", {})
        await world.post("/api/channels/imessage/check", {})
        # Each press waited a moment (the cap here) and not for the owner; the second stacked no second question.
        assert time.monotonic() - started < 4.0
        assert world.macos.asked == ["com.apple.MobileSMS"]
    finally:
        world.macos.gate.set()
        world.macos.gate = None
    await asyncio.sleep(0.3)
    await world.post("/api/channels/imessage/check", {})
    assert world.macos.asked == ["com.apple.MobileSMS", "com.apple.MobileSMS"]


async def test_an_answer_that_could_not_go_leaves_the_channel_saying_what_it_needs_until_one_does(
    world: World, replies: Replies
) -> None:
    replies.clear()
    own: int = world.messages.chat(PHONE)
    world.osascript.error = DesktopError("osascript failed", {"stderr": "Not authorized to send Apple events (-1743)"})
    replies.push(finish("An answer that cannot be told."))
    world.messages.message(own, "a question while Messages refuses", at=time.time())

    async def says_automation() -> bool:
        shown: JsonObject = _view(await world.get("/api/channels"))
        return shown["needs"] == "automation" and "control Messages" in str(shown["problem"])

    await _until(says_automation, "the channel says what it needs")
    world.osascript.error = None
    replies.push(finish("This one goes."))
    sent_before: int = len(world.osascript.sent)
    world.messages.message(own, "and now it is allowed", at=time.time())
    await _until(lambda: len(world.osascript.sent) == sent_before + 1, "the answer goes")
    shown: JsonObject = _view(await world.get("/api/channels"))
    assert shown["needs"] is None and shown["problem"] is None


async def test_a_removed_number_is_no_longer_read(world: World) -> None:
    removed: JsonObject = _view(await world.post("/api/channels/imessage/remove", {"handle": PHONE}))
    assert removed["handles"] == []
    refused: JsonObject = await world.post("/api/channels/imessage/remove", {"handle": PHONE}, 422)
    assert "not paired" in str(refused["message"])
    before: int = len(await world.runs())
    world.messages.message(world.messages.chat(PHONE), "to a number no longer paired", at=time.time())
    await asyncio.sleep(0.4)
    assert len(await world.runs()) == before


async def test_what_macos_has_not_granted_is_shown_and_asked_for(world: World) -> None:
    world.macos.asked.clear()
    world.macos.state = "not_asked"
    assert _view(await world.get("/api/channels"))["automation"] == "not_asked"
    asked: JsonObject = _view(await world.post("/api/channels/imessage/check", {}))
    assert world.macos.asked == ["com.apple.MobileSMS"] and asked["automation"] == "not_asked"
    world.macos.state = "refused"
    assert _view(await world.get("/api/channels"))["automation"] == "refused"
    world.macos.state = "allowed"
    world.macos.name = None
    assert _view(await world.get("/api/channels"))["program"] is None


async def test_a_config_file_that_cannot_be_changed_leaves_the_switch_as_it_was(world: World) -> None:
    saved: str = config_path().read_text(encoding="utf-8")
    config_path().write_text("this is [not valid toml", encoding="utf-8")
    try:
        refused: JsonObject = await world.post("/api/channels/imessage/switch", {"on": False}, 422)
        assert "not valid TOML" in str(refused["message"])
        assert _view(await world.get("/api/channels"))["on"] is True
    finally:
        config_path().write_text(saved, encoding="utf-8")


async def test_a_daemon_that_starts_with_the_channel_on_reads_it_from_the_start(
    runtime: Runtime, manager: RunManager, messages: MessagesDb, replies: Replies
) -> None:
    replies.clear()
    configured: Runtime = dataclasses.replace(
        runtime, config=runtime.config.model_copy(update={"channels": ChannelsConfig(imessage=True)})
    )
    osascript: Osascript = Osascript()
    channels: Channels = Channels(
        configured,
        manager,
        "127.0.0.1",
        8765,
        script=osascript,
        messages_db=messages.path,
        macos=FakeMacos(),
        poll_s=0.05,
    )
    await runtime.store.kv_set("channels.imessage", '{"handles": ["+358401234567"], "cursor": null, "reply_to": null}')
    await channels.start()
    try:
        assert channels.is_on("imessage")
        # The first poll sets the cursor and answers nothing, whatever was in the conversation before.
        own: int = messages.chat(PHONE)
        messages.message(own, "from before the start", at=time.time())
        await asyncio.sleep(0.4)
        before: int = len(await runtime.store.list_runs(100, None))
        replies.push(finish("Read from the start."))
        messages.message(own, "after the start", at=time.time())
        await _until(lambda: len(osascript.sent) == 1, "the answer")
        assert osascript.sent == [(PHONE, f"{MARKER}Read from the start.")]
        assert len(await runtime.store.list_runs(100, None)) == before + 1
    finally:
        await channels.stop()


async def test_the_link_at_the_end_of_an_answer_is_not_read_back_as_a_command_when_messages_splits_it_off(
    runtime: Runtime, manager: RunManager, messages: MessagesDb, replies: Replies
) -> None:
    """Messages may keep the link at the end of a message as a row of its own, a link preview, which carries no ◆ and
    is flagged as the owner's. An answer that ends with a source would else start a run on it, and that run's answer
    another."""
    replies.clear()
    configured: Runtime = dataclasses.replace(
        runtime, config=runtime.config.model_copy(update={"channels": ChannelsConfig(imessage=True)})
    )
    osascript: Osascript = Osascript()
    channels: Channels = Channels(
        configured,
        manager,
        "127.0.0.1",
        8765,
        script=osascript,
        messages_db=messages.path,
        macos=FakeMacos(),
        poll_s=0.05,
    )
    await runtime.store.kv_set("channels.imessage", '{"handles": ["+358401234567"], "cursor": null, "reply_to": null}')
    await channels.start()
    try:
        own: int = messages.chat(PHONE)
        await asyncio.sleep(0.4)
        before: int = len(await runtime.store.list_runs(100, None))
        replies.push(finish("It was in the paper: https://news.example/a"))
        messages.message(own, "where did you read that?", at=time.time())
        await _until(lambda: len(osascript.sent) == 1, "the answer")
        assert osascript.sent == [(PHONE, f"{MARKER}It was in the paper: https://news.example/a")]
        messages.message(own, "https://news.example/a", balloon=URL_BALLOON, at=time.time())
        await asyncio.sleep(0.4)
        assert len(await runtime.store.list_runs(100, None)) == before + 1
        # A link of the owner's own, which Dot did not write, is a command.
        replies.push(finish("Read it."))
        messages.message(own, "https://news.example/b", balloon=URL_BALLOON, at=time.time())
        await _until(lambda: len(osascript.sent) == 2, "the answer to the owner's link")
        assert len(await runtime.store.list_runs(100, None)) == before + 2
    finally:
        await channels.stop()


async def test_a_server_without_channels_says_so(runtime: Runtime, manager: RunManager) -> None:
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    transport: httpx.ASGITransport = httpx.ASGITransport(app=create_app(context))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as http:
        answered: httpx.Response = await http.get("/api/channels", headers={"Authorization": f"Bearer {TOKEN}"})
    assert answered.status_code == 422 and "without channels" in answered.json()["message"]
