"""The router of the channels (item 572), on a real runtime and run manager with a scripted model: a message of a
channel starts a run in the channel's own chat as the owner's own, with the choices the chat keeps; one that comes while
a run works is queued; the answer of each run of that chat goes back on the channel, masked, as plain text and cut to
its size with a link to the chat; a run that waits for the owner sends one line, and nothing more is answered on the
channel."""

import asyncio
import contextlib
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, Replies, finish, stop_file
from picture_files import HEADS, NOT_IMAGE

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.manager import RunManager
from dotagent.channels.intake import UNREAD_ANSWER, UNREAD_NOTE
from dotagent.channels.pictures import (
    NOT_A_PICTURE,
    OUT_OF_PLACE,
    TOO_BIG,
    TOO_MANY,
    Found,
    Left,
    Picture,
    Pictures,
)
from dotagent.channels.router import DEFAULT_CHOICES, QUEUED, STOPPED, WATCHED_KINDS, ChannelRouter
from dotagent.channels.types import Receive
from dotagent.config import load_config
from dotagent.core.errors import ChannelError, ConfigError
from dotagent.core.paths import env_file_path
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import write_env_value
from dotagent.store.models import ApprovalRecord, RunRecord, ThreadRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

BASE: str = "https://mac.example.ts.net/"
_WAIT_S: float = 30.0
LIMIT: int = 400
# How long the router waits before it tells again that a run waits for the owner: short, so that a test can see both.
_ASKS_APART_S: float = 1.0


class FakeChannel:
    """A channel that records what is sent on it."""

    id = "imessage"
    limit: int = LIMIT

    def __init__(self) -> None:
        self.sent: list[str] = []
        # The pictures sent (item 617), by name, the bytes each held as it was sent, the names whose send fails, and
        # everything in the order it went: ("text", words) or ("picture", name).
        self.pictures: list[str] = []
        self.picture_bytes: list[bytes] = []
        self.refuses: set[str] = set()
        self.explodes: set[str] = set()
        self.order: list[tuple[str, str]] = []

    def paired(self) -> bool:
        return True

    async def run(self, receive: Receive) -> None:
        raise AssertionError("the router reads no channel: the tests post for it")

    async def send(self, text: str) -> None:
        self.sent.append(text)
        self.order.append(("text", text))

    async def send_picture(self, picture: Picture) -> None:
        if picture.name in self.explodes:
            raise RuntimeError("a bug in the channel")
        if picture.name in self.refuses:
            raise ChannelError(f"Messages did not take {picture.name}", {})
        self.pictures.append(picture.name)
        self.picture_bytes.append(await asyncio.to_thread(picture.path.read_bytes))
        self.order.append(("picture", picture.name))

    async def start_from_now(self) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def the_replies() -> Replies:
    return Replies()


@pytest.fixture
def replies(the_replies: Replies) -> Replies:
    """The model's replies for one test: those a test left unread (a run it stopped before its call) are dropped."""
    the_replies.clear()
    return the_replies


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, the_replies: Replies) -> AsyncIterator[Runtime]:
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
    await built.router.register(PROVIDER, the_replies)
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


class Wired:
    """A router over a fake channel, and the task that follows the runs of its chat."""

    def __init__(self, runtime: Runtime, manager: RunManager, base: str | None = BASE) -> None:
        self.runtime: Runtime = runtime
        self.manager: RunManager = manager
        self.channel: FakeChannel = FakeChannel()
        self.on: bool = True
        # The pictures an answer names (item 617) go from the runtime's workspace or the owner's home, which is a
        # folder of the test's own here.
        self.home: Path = runtime.workspace.parent / "owner"
        self.router: ChannelRouter = ChannelRouter(
            runtime,
            manager,
            {"imessage": self.channel},
            base,
            lambda _: self.on,
            _ASKS_APART_S,
            Pictures(runtime.workspace, self.home, runtime.protected),
        )

    async def thread(self) -> ThreadRecord:
        stored: str | None = await self.runtime.store.kv_get("channels.thread.imessage")
        assert stored is not None
        return await self.runtime.store.get_thread(stored)

    async def runs(self) -> list[RunRecord]:
        """The runs of the channel's chat, oldest first."""
        return list(reversed(await self.runtime.store.list_runs(50, (await self.thread()).id)))


@contextlib.asynccontextmanager
async def _following(runtime: Runtime, manager: RunManager, base: str | None) -> AsyncIterator[Wired]:
    """A router over a fake channel, with the task that follows the runs of its chat."""
    wiring: Wired = Wired(runtime, manager, base)
    # As the service starts it: the router reads which chat is whose.
    await wiring.router.load()
    async with runtime.bus.subscribe_kinds(WATCHED_KINDS) as feed:
        watching: asyncio.Task[None] = asyncio.create_task(wiring.router.watch(feed))
        try:
            yield wiring
        finally:
            watching.cancel()
            await asyncio.gather(watching, return_exceptions=True)


@pytest_asyncio.fixture(loop_scope="module")
async def wired(runtime: Runtime, manager: RunManager) -> AsyncIterator[Wired]:
    async with _following(runtime, manager, BASE) as wiring:
        yield wiring


@pytest_asyncio.fixture(loop_scope="module")
async def unlinked(runtime: Runtime, manager: RunManager) -> AsyncIterator[Wired]:
    """The same with no address a phone can open (no ``[server] reach``): the router has no base for its links."""
    async with _following(runtime, manager, None) as wiring:
        yield wiring


@contextlib.contextmanager
def _engaged() -> Iterator[Path]:
    """The owner's emergency stop engaged: the STOP file of the home, touched by hand (which ends no run, as the docs
    say), and taken away when the block is done."""
    stop: Path = stop_file()
    stop.touch()
    try:
        yield stop
    finally:
        stop.unlink(missing_ok=True)


@pytest.fixture
def stopped(runtime: Runtime) -> Iterator[Path]:
    """The emergency stop engaged for the whole test (``runtime`` first: it points DOT_HOME at the throwaway home)."""
    with _engaged() as stop:
        yield stop


async def _until(condition: Callable[[], bool | Awaitable[bool]], what: str) -> None:
    deadline: float = time.monotonic() + _WAIT_S
    while time.monotonic() < deadline:
        found: bool | Awaitable[bool] = condition()
        if await found if isinstance(found, Awaitable) else found:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"not in {_WAIT_S:g} s: {what}")


async def _settled(manager: RunManager) -> None:
    """Every run of the manager ended."""
    await _until(lambda: not manager.active_run_ids(), "the runs ended")


async def test_a_message_starts_a_run_in_the_channels_own_chat_as_the_owners_and_its_answer_comes_back(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    replies.push(finish("It is **sunny** in Oulu."))
    await wired.router.inbound("imessage", "what is the weather in Oulu?")
    await _until(lambda: bool(wired.channel.sent), "the answer came back")
    assert wired.channel.sent == ["It is sunny in Oulu."]
    thread: ThreadRecord = await wired.thread()
    assert (thread.title, thread.source) == ("iMessage", "imessage")
    (run,) = await wired.runs()
    assert (run.goal, run.source, run.mode, run.status) == ("what is the weather in Oulu?", "imessage", "task", "done")
    # The owner's default: the approvals the configuration names, the chat's choices those of a new chat's composer.
    assert run.approvals == runtime.config.policy.approvals
    assert await runtime.store.chat_choices(thread.id) == DEFAULT_CHOICES
    # The chat is in the sidebar like any other, and it is the one the next message of the channel goes to.
    assert thread.id in {summary.id for summary in await runtime.store.list_thread_summaries(50)}


async def test_the_next_message_goes_to_the_same_chat(wired: Wired, replies: Replies) -> None:
    replies.push(finish("Second answer."))
    before: int = len(wired.channel.sent)
    await wired.router.inbound("imessage", "and tomorrow?")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the second answer")
    assert wired.channel.sent[-1] == "Second answer."
    assert [run.goal for run in await wired.runs()][-2:] == ["what is the weather in Oulu?", "and tomorrow?"]


async def test_the_choices_the_owner_keeps_in_the_chat_are_the_ones_its_next_message_starts_with(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    thread: ThreadRecord = await wired.thread()
    kept = DEFAULT_CHOICES.model_copy(update={"mode": "research", "approvals": "supervised"})
    await runtime.store.set_chat_choices(thread.id, kept)
    try:
        replies.push(finish("Looked, changed nothing."))
        before: int = len(wired.channel.sent)
        await wired.router.inbound("imessage", "look into the build folder")
        await _until(lambda: len(wired.channel.sent) == before + 1, "the answer")
        run: RunRecord = (await wired.runs())[-1]
        assert (run.mode, run.approvals) == ("research", "supervised")
    finally:
        await runtime.store.set_chat_choices(thread.id, DEFAULT_CHOICES)


async def test_an_answer_is_masked_of_the_secrets_dot_holds_and_cut_to_the_channels_size_with_the_link(
    wired: Wired, replies: Replies
) -> None:
    secret: str = "hunter2-horse-battery-staple"
    write_env_value(env_file_path(), "SOME_SERVICE_PASSWORD_FILE", secret)
    replies.push(finish(f"The password is {secret}. " + "More words. " * 100))
    before: int = len(wired.channel.sent)
    await wired.router.inbound("imessage", "tell me everything")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the long answer")
    sent: str = wired.channel.sent[-1]
    run: RunRecord = (await wired.runs())[-1]
    assert secret not in sent and "The password is ***." in sent
    assert len(sent) <= LIMIT
    assert sent.endswith(f"{BASE}#/runs/{run.id}") and "The rest of the answer is in Dot" in sent


async def test_a_run_that_failed_says_how_it_ended(wired: Wired, replies: Replies) -> None:
    # A model call that raises ends the run as failed.
    replies.push()
    before: int = len(wired.channel.sent)
    await wired.router.inbound("imessage", "this one fails")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the failure was told")
    assert wired.channel.sent[-1].startswith("The run failed.")


async def test_a_message_that_comes_while_a_run_works_is_queued_and_both_are_answered_in_order(
    wired: Wired, replies: Replies, manager: RunManager
) -> None:
    replies.push(finish("First, held."), finish("Second, queued."))
    replies.gate = asyncio.Event()
    before: int = len(wired.channel.sent)
    try:
        await wired.router.inbound("imessage", "the long one")
        await _until(lambda: bool(manager.active_run_ids()), "the run is at work")
        await wired.router.inbound("imessage", "one more thing")
        assert wired.channel.sent[before:] == [
            "Queued: the run at work in this chat goes first. Send /stop to stop it."
        ]
        queued = await wired.runtime.store.list_queued((await wired.thread()).id)
        assert [(message.text, message.source) for message in queued] == [("one more thing", "imessage")]
    finally:
        replies.gate.set()
        replies.gate = None
    await _until(lambda: len(wired.channel.sent) == before + 3, "both answers")
    assert wired.channel.sent[before + 1 :] == ["First, held.", "Second, queued."]
    await _settled(manager)


async def test_stop_stops_the_run_at_work_in_the_chat_and_says_when_none_is(
    wired: Wired, replies: Replies, manager: RunManager
) -> None:
    await wired.router.inbound("imessage", "/stop")
    assert wired.channel.sent[-1] == "No run is at work in this chat."
    replies.push(finish("never said"))
    replies.gate = asyncio.Event()
    try:
        await wired.router.inbound("imessage", "a run to stop")
        await _until(lambda: bool(manager.active_run_ids()), "the run is at work")
        await wired.router.inbound("imessage", " /STOP ")
        await _until(lambda: not manager.active_run_ids(), "the run was stopped")
    finally:
        replies.gate.set()
        replies.gate = None
    # The end is said once: the lead of a stopped run and its result are the same sentence (item 611).
    await _until(lambda: wired.channel.sent[-1:] == ["The run was stopped."], "the end was told")
    assert "Stopping the run." in wired.channel.sent
    assert not any(line.count("The run was stopped.") > 1 for line in wired.channel.sent)
    assert (await wired.runs())[-1].status == "cancelled"
    assert (await wired.runs())[-1].goal == "a run to stop"


async def test_a_run_that_waits_for_the_owner_sends_one_line_and_asks_for_the_answer_in_dot(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    replies.push(finish("Done."))
    await wired.router.inbound("imessage", "make a run to ask about")
    await _until(lambda: wired.channel.sent[-1:] == ["Done."], "the run ended")
    run: RunRecord = (await wired.runs())[-1]
    before: int = len(wired.channel.sent)
    await runtime.bus.emit(
        run.id,
        "approval_requested",
        {"approval_id": "apr_1", "tool": "shell", "summary": "Run `rm -r build`", "severity": "ask", "args": {}},
        False,
    )
    await _until(lambda: len(wired.channel.sent) == before + 1, "the line about the approval")
    assert wired.channel.sent[-1] == (
        f"Dot waits for your approval: Run rm -r build\nAnswer it in Dot, not here: {BASE}#/runs/{run.id}"
    )
    # A second ask a moment later is not told again, and nothing on the channel answers it.
    await runtime.bus.emit(
        run.id,
        "question_left",
        {
            "approval_id": "apr_2",
            "tool": "ask_user",
            "summary": "s",
            "severity": "question",
            "args": {"question": "q?"},
        },
        False,
    )
    await asyncio.sleep(0.3)
    assert len(wired.channel.sent) == before + 1
    # It is held, not dropped (item 611): it is told when the window of the first line ends.
    await _until(lambda: len(wired.channel.sent) == before + 2, "the held question was told")
    assert wired.channel.sent[-1] == f"Dot has a question: q?\nAnswer it in Dot, not here: {BASE}#/runs/{run.id}"


async def test_a_question_is_asked_in_the_owners_words(wired: Wired, replies: Replies, runtime: Runtime) -> None:
    replies.push(finish("Done."))
    await wired.router.inbound("imessage", "make a run to ask a question in")
    await _until(lambda: wired.channel.sent[-1:] == ["Done."], "the run ended")
    run: RunRecord = (await wired.runs())[-1]
    before: int = len(wired.channel.sent)
    # The line about an earlier ask was told a moment ago; one told after the interval is told again.
    await asyncio.sleep(_ASKS_APART_S + 0.2)
    await runtime.bus.emit(
        run.id,
        "question_left",
        {
            "approval_id": "apr_3",
            "tool": "ask_user",
            "summary": "Asked a question",
            "severity": "question",
            "args": {"question": "Which folder should I clean up?"},
        },
        False,
    )
    await _until(lambda: len(wired.channel.sent) == before + 1, "the question was told")
    assert wired.channel.sent[-1] == (
        f"Dot has a question: Which folder should I clean up?\nAnswer it in Dot, not here: {BASE}#/runs/{run.id}"
    )


async def test_a_run_of_another_chat_is_not_told_on_the_channel(
    wired: Wired, replies: Replies, manager: RunManager
) -> None:
    replies.push(finish("Typed in the web UI."))
    before: int = len(wired.channel.sent)
    run: RunRecord = await manager.start(
        "a goal typed in the page", "task", None, None, "ui", None, None, "standard", None, None, None, ()
    )
    await manager.wait(run.id)
    await asyncio.sleep(0.3)
    assert len(wired.channel.sent) == before


async def test_a_channel_that_is_off_is_not_told_of_its_runs(
    wired: Wired, replies: Replies, manager: RunManager
) -> None:
    replies.push(finish("Said to nobody."))
    wired.on = False
    before: int = len(wired.channel.sent)
    try:
        await wired.router.inbound("imessage", "while the switch is off")
        await _settled(manager)
        await asyncio.sleep(0.3)
    finally:
        wired.on = True
    assert len(wired.channel.sent) == before


async def test_a_message_that_cannot_start_a_run_is_told_to_the_owner(
    wired: Wired, manager: RunManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(*arguments: object) -> RunRecord:
        raise ConfigError("the model cannot be used", {})

    monkeypatch.setattr(manager, "start_picked", refused)
    await wired.router.inbound("imessage", "this cannot start")
    assert wired.channel.sent[-1] == "Dot could not start on that: the model cannot be used."


async def test_a_message_that_fails_unexpectedly_says_so_and_the_next_one_still_goes(
    wired: Wired, replies: Replies, manager: RunManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*arguments: object) -> RunRecord:
        raise RuntimeError("a bug")

    with monkeypatch.context() as patch:
        patch.setattr(manager, "start_picked", broken)
        await wired.router.inbound("imessage", "this meets a bug")
    assert wired.channel.sent[-1] == "Dot failed on that message. The log has what went wrong."
    replies.push(finish("Still working."))
    before: int = len(wired.channel.sent)
    await wired.router.inbound("imessage", "and this goes")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the next answer")
    assert wired.channel.sent[-1] == "Still working."


async def test_the_page_of_a_channel_links_to_the_newest_run_of_its_chat(wired: Wired) -> None:
    newest: RunRecord = (await wired.runs())[-1]
    assert await wired.router.chat_run_id("imessage") == newest.id
    assert wired.router.link(newest.id) == f"{BASE}#/runs/{newest.id}"


async def test_the_router_reads_which_chat_is_whose_when_it_starts(wired: Wired, runtime: Runtime) -> None:
    fresh: ChannelRouter = ChannelRouter(runtime, wired.manager, {"imessage": wired.channel}, BASE, lambda _: True)
    await fresh.load()
    thread: ThreadRecord = await wired.thread()
    assert thread.id in fresh._chats and fresh._chats[thread.id] == "imessage"


async def test_a_chat_the_owner_deleted_is_made_again(wired: Wired, replies: Replies, runtime: Runtime) -> None:
    old: ThreadRecord = await wired.thread()
    await runtime.store.delete_threads([old.id])
    replies.push(finish("In the new chat."))
    before: int = len(wired.channel.sent)
    await wired.router.inbound("imessage", "after the chat was deleted")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the answer")
    new: ThreadRecord = await wired.thread()
    assert new.id != old.id and new.title == "iMessage"
    assert [run.goal for run in await wired.runs()] == ["after the chat was deleted"]


# ----- the emergency stop holds the channel too (item 611) ---------------------------------------------


async def test_while_the_emergency_stop_is_engaged_a_message_starts_no_run_and_the_owner_is_told_how_to_go_on(
    wired: Wired, replies: Replies, runtime: Runtime, stopped: Path
) -> None:
    replies.push(finish("Must never be said."))
    runs_before: int = len(await runtime.store.list_runs(100, None))
    sent_before: int = len(wired.channel.sent)
    await wired.router.inbound("imessage", "what is the weather in Oulu?")
    assert wired.channel.sent[sent_before:] == [STOPPED]
    assert "emergency stop is engaged" in STOPPED and "Release it in Dot" in STOPPED
    # Each message is answered once, and nothing is kept to run when the stop is released.
    await wired.router.inbound("imessage", "and tomorrow?")
    assert wired.channel.sent[sent_before:] == [STOPPED, STOPPED]
    await asyncio.sleep(0.3)
    assert len(await runtime.store.list_runs(100, None)) == runs_before and not wired.manager.active_run_ids()
    # Released, the next message runs, and only it.
    await asyncio.to_thread(stopped.unlink)
    replies.clear()
    replies.push(finish("Now it runs."))
    await wired.router.inbound("imessage", "and now?")
    await _until(lambda: wired.channel.sent[-1:] == ["Now it runs."], "the answer after the release")
    assert (await wired.runs())[-1].goal == "and now?"
    assert len(await runtime.store.list_runs(100, None)) == runs_before + 1


async def test_a_message_that_comes_while_a_run_works_and_the_stop_is_engaged_is_not_queued(
    wired: Wired, replies: Replies, manager: RunManager, runtime: Runtime
) -> None:
    replies.push(finish("First, held."), finish("Never started."))
    replies.gate = asyncio.Event()
    try:
        await wired.router.inbound("imessage", "the long one")
        await _until(lambda: bool(manager.active_run_ids()), "the run is at work")
        with _engaged():
            before: int = len(wired.channel.sent)
            await wired.router.inbound("imessage", "one more thing")
            assert wired.channel.sent[before:] == [STOPPED]
            assert await runtime.store.list_queued((await wired.thread()).id) == []
    finally:
        replies.gate.set()
        replies.gate = None
    await _until(lambda: wired.channel.sent[-1:] == ["First, held."], "the run that worked answered")
    await _settled(manager)
    await asyncio.sleep(0.3)
    # The message refused was not kept: it never ran after the first run, and the chat's queue is empty.
    assert [run.goal for run in await wired.runs()][-1] == "the long one"
    assert await runtime.store.list_queued((await wired.thread()).id) == []


@pytest.mark.parametrize("word", ["/stop", "stop", "Dur"])
async def test_the_stop_words_stop_the_run_at_work_whatever_the_emergency_stop_says(
    wired: Wired, replies: Replies, manager: RunManager, word: str
) -> None:
    replies.push(finish("never said"))
    replies.gate = asyncio.Event()
    try:
        await wired.router.inbound("imessage", "a run to stop")
        await _until(lambda: bool(manager.active_run_ids()), "the run is at work")
        with _engaged():
            before: int = len(wired.channel.sent)
            await wired.router.inbound("imessage", word)
            await _until(lambda: not manager.active_run_ids(), "the run was stopped")
            assert STOPPED not in wired.channel.sent[before:] and "Stopping the run." in wired.channel.sent[before:]
    finally:
        replies.gate.set()
        replies.gate = None
    await _until(lambda: wired.channel.sent[-1:] == ["The run was stopped."], "the end was told")
    assert (await wired.runs())[-1].status == "cancelled"


async def test_a_stop_word_with_no_run_at_work_says_so_and_starts_no_run_even_when_the_stop_is_engaged(
    wired: Wired, runtime: Runtime, manager: RunManager
) -> None:
    await _settled(manager)
    runs_before: int = len(await runtime.store.list_runs(100, None))
    for word in ("stop", "durdur"):
        await wired.router.inbound("imessage", word)
        assert wired.channel.sent[-1] == "No run is at work in this chat."
    with _engaged():
        await wired.router.inbound("imessage", "/stop")
        assert wired.channel.sent[-1] == "No run is at work in this chat."
    assert len(await runtime.store.list_runs(100, None)) == runs_before


async def test_a_stop_that_comes_while_the_run_is_being_started_ends_it_as_an_emergency_stop(
    wired: Wired, replies: Replies, manager: RunManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As a schedule's run is ended when the stop is engaged as it starts: the file appears between the check and the
    start, and the message must not leave a run at work."""
    real = manager.start_picked
    stop: Path = stop_file()

    async def starting(*arguments: object) -> RunRecord:
        started: RunRecord = await real(*arguments)  # type: ignore[arg-type]
        await asyncio.to_thread(stop.touch)
        return started

    replies.push(finish("never said"))
    replies.gate = asyncio.Event()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(manager, "start_picked", starting)
            await wired.router.inbound("imessage", "a run that starts as the stop comes")
        await _until(lambda: not manager.active_run_ids(), "the run was ended")
    finally:
        await asyncio.to_thread(stop.unlink, True)
        replies.gate.set()
        replies.gate = None
    run: RunRecord = (await wired.runs())[-1]
    assert (run.status, run.result.startswith("Emergency stop")) == ("cancelled", True)
    await _until(
        lambda: wired.channel.sent[-1:] == ["The run was stopped. Emergency stop: every active run was stopped."],
        "the end was told",
    )


# ----- plain words stop a run (item 611) ---------------------------------------------------------------


@pytest.mark.parametrize("word", ["stop", "Stop", "dur", "durdur", "/stop", "Durdur."])
async def test_a_message_of_a_plain_stop_word_stops_the_run_at_work_and_is_not_queued_behind_it(
    wired: Wired, replies: Replies, manager: RunManager, runtime: Runtime, word: str
) -> None:
    replies.push(finish("never said"))
    replies.gate = asyncio.Event()
    before: int = len(wired.channel.sent)
    try:
        await wired.router.inbound("imessage", "a run to stop")
        await _until(lambda: bool(manager.active_run_ids()), "the run is at work")
        await wired.router.inbound("imessage", word)
        await _until(lambda: not manager.active_run_ids(), "the run was stopped")
    finally:
        replies.gate.set()
        replies.gate = None
    # The word is no message of the chat: it waits in no queue, and the end of the run is said once.
    await _until(lambda: wired.channel.sent[-1:] == ["The run was stopped."], "the end was told")
    assert await runtime.store.list_queued((await wired.thread()).id) == []
    assert wired.channel.sent[before:] == ["Stopping the run.", "The run was stopped."]
    assert [run.goal for run in await wired.runs()][-1] == "a run to stop"


async def test_a_sentence_that_holds_a_stop_word_is_a_message_like_any_other(
    wired: Wired, replies: Replies, manager: RunManager
) -> None:
    replies.push(finish("First, held."), finish("Second, queued."))
    replies.gate = asyncio.Event()
    before: int = len(wired.channel.sent)
    try:
        await wired.router.inbound("imessage", "the long one")
        await _until(lambda: bool(manager.active_run_ids()), "the run is at work")
        await wired.router.inbound("imessage", "please stop and tell me why")
        assert wired.channel.sent[before:] == [QUEUED] and bool(manager.active_run_ids())
    finally:
        replies.gate.set()
        replies.gate = None
    await _until(lambda: wired.channel.sent[-1:] == ["Second, queued."], "both answers")
    await _settled(manager)
    assert wired.channel.sent[before:] == [QUEUED, "First, held.", "Second, queued."]


# ----- a wait is held, never dropped (item 611) --------------------------------------------------------


async def _a_finished_run(wired: Wired, replies: Replies, goal: str) -> RunRecord:
    """A run of the channel's chat that ended, and the window of ask lines over: the waits a test then leaves it are
    the first of their window."""
    replies.push(finish("Done."))
    await wired.router.inbound("imessage", goal)
    await _until(lambda: wired.channel.sent[-1:] == ["Done."], "the run ended")
    await asyncio.sleep(_ASKS_APART_S + 0.2)
    return (await wired.runs())[-1]


async def _leaves_a_question(runtime: Runtime, run: RunRecord, question: str) -> str:
    """The run leaves a question and goes on, as ``ask_user`` with ``wait`` false does: its record and its event."""
    args: JsonObject = {"question": question}
    record: ApprovalRecord = await runtime.store.create_left_approval(
        run.id, "ask_user", "Asked a question", "question", args
    )
    await runtime.bus.emit(
        run.id,
        QUESTION_LEFT_EVENT,
        {
            "approval_id": record.id,
            "tool": "ask_user",
            "summary": "Asked a question",
            "severity": "question",
            "args": args,
        },
        False,
    )
    return record.id


async def _blocks_on(runtime: Runtime, run: RunRecord, summary: str) -> str:
    """The run blocks on an approval: its record and its event."""
    record: ApprovalRecord = await runtime.store.create_approval(run.id, "shell", summary, "high", {})
    await runtime.bus.emit(
        run.id,
        "approval_requested",
        {"approval_id": record.id, "tool": "shell", "summary": summary, "severity": "high", "args": {}},
        False,
    )
    return record.id


async def test_a_run_that_blocks_inside_the_window_of_an_earlier_line_has_its_notice_held_and_told_after_it(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    """A run leaves a question it does not wait for and goes on, and its next step needs an approval: the second line
    used to be dropped, and the run sat blocked with the phone saying nothing."""
    run: RunRecord = await _a_finished_run(wired, replies, "sort my downloads")
    before: int = len(wired.channel.sent)
    link: str = f"{BASE}#/runs/{run.id}"
    await _leaves_a_question(runtime, run, "Which folder do you mean?")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the question was told")
    assert (
        wired.channel.sent[-1] == f"Dot has a question: Which folder do you mean?\nAnswer it in Dot, not here: {link}"
    )
    told_at: float = time.monotonic()
    await _blocks_on(runtime, run, "Run `sudo -n true`")
    await asyncio.sleep(0.3)
    assert len(wired.channel.sent) == before + 1
    await _until(lambda: len(wired.channel.sent) == before + 2, "the notice of the block was told")
    assert (
        wired.channel.sent[-1] == f"Dot waits for your approval: Run sudo -n true\nAnswer it in Dot, not here: {link}"
    )
    # After the window that the first line opened, and not before.
    assert time.monotonic() - told_at >= _ASKS_APART_S - 0.15
    await asyncio.sleep(_ASKS_APART_S + 0.3)
    assert len(wired.channel.sent) == before + 2


async def test_a_held_wait_that_was_answered_before_the_window_ended_is_not_told(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    run: RunRecord = await _a_finished_run(wired, replies, "tidy the desktop")
    before: int = len(wired.channel.sent)
    await _leaves_a_question(runtime, run, "Which folder do you mean?")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the question was told")
    approval: str = await _blocks_on(runtime, run, "Run `sudo -n true`")
    # The owner answered it in Dot meanwhile: the line that says Dot waits for them would be false.
    await runtime.store.decide_approval(approval, "approved", "owner", "")
    await asyncio.sleep(_ASKS_APART_S + 0.5)
    assert len(wired.channel.sent) == before + 1


async def test_waits_held_together_are_told_as_one_line_with_how_many_more_wait(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    run: RunRecord = await _a_finished_run(wired, replies, "clean up the build folder")
    before: int = len(wired.channel.sent)
    await _leaves_a_question(runtime, run, "Which folder do you mean?")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the question was told")
    await _leaves_a_question(runtime, run, "And the cache as well?")
    await _blocks_on(runtime, run, "Run `rm -r build`")
    await _until(lambda: len(wired.channel.sent) == before + 2, "the held line was told")
    link: str = f"{BASE}#/runs/{run.id}"
    assert wired.channel.sent[-1] == (
        f"Dot waits for your approval: Run rm -r build (and 1 more)\nAnswer them in Dot, not here: {link}"
    )
    await asyncio.sleep(_ASKS_APART_S + 0.3)
    assert len(wired.channel.sent) == before + 2


# ----- a picture or a file sent with words (item 611) --------------------------------------------------


async def test_the_answer_to_a_message_that_came_with_a_picture_says_in_its_first_line_that_the_picture_was_not_read(
    wired: Wired, replies: Replies
) -> None:
    replies.push(finish("It looks fine to me."), finish("Four."))
    before: int = len(wired.channel.sent)
    await wired.router.inbound("imessage", f"What is wrong with this screenshot?\n\n{UNREAD_NOTE}")
    await _until(lambda: len(wired.channel.sent) == before + 1, "the answer")
    run: RunRecord = (await wired.runs())[-1]
    assert run.goal == f"What is wrong with this screenshot?\n\n{UNREAD_NOTE}"
    assert wired.channel.sent[-1] == f"{UNREAD_ANSWER}\n\nIt looks fine to me."
    assert wired.channel.sent[-1].splitlines()[0] == UNREAD_ANSWER
    # A message with no picture is answered as it always was.
    await wired.router.inbound("imessage", "what is two and two?")
    await _until(lambda: len(wired.channel.sent) == before + 2, "the second answer")
    assert wired.channel.sent[-1] == "Four."


async def test_a_message_with_a_picture_that_waited_in_the_queue_is_answered_with_the_same_first_line(
    wired: Wired, replies: Replies, manager: RunManager
) -> None:
    replies.push(finish("First, held."), finish("Second, queued."))
    replies.gate = asyncio.Event()
    before: int = len(wired.channel.sent)
    try:
        await wired.router.inbound("imessage", "the long one")
        await _until(lambda: bool(manager.active_run_ids()), "the run is at work")
        await wired.router.inbound("imessage", f"and this one, with a picture\n\n{UNREAD_NOTE}")
    finally:
        replies.gate.set()
        replies.gate = None
    await _until(lambda: len(wired.channel.sent) == before + 3, "both answers")
    assert wired.channel.sent[before + 1 :] == ["First, held.", f"{UNREAD_ANSWER}\n\nSecond, queued."]
    await _settled(manager)


# ----- links that open on the phone (item 611) ---------------------------------------------------------


async def test_with_no_address_a_phone_can_open_the_lines_carry_no_link_and_say_where_to_look(
    unlinked: Wired, replies: Replies, runtime: Runtime
) -> None:
    """Without ``[server] reach`` the only address is this Mac's own, which opens nothing on a phone."""
    assert unlinked.router.link("run_1") is None
    replies.push(finish("It is **sunny** in Oulu. " + "More words. " * 100))
    await unlinked.router.inbound("imessage", "tell me everything")
    await _until(lambda: bool(unlinked.channel.sent), "the long answer")
    cut: str = unlinked.channel.sent[-1]
    assert len(cut) <= LIMIT and cut.endswith("… The rest of the answer is in Dot on your Mac.")
    assert "http" not in cut and "127.0.0.1" not in cut
    run: RunRecord = (await unlinked.runs())[-1]
    before: int = len(unlinked.channel.sent)
    await asyncio.sleep(_ASKS_APART_S + 0.2)
    await _blocks_on(runtime, run, "Run `rm -r build`")
    await _until(lambda: len(unlinked.channel.sent) == before + 1, "the line about the approval")
    assert unlinked.channel.sent[-1] == (
        "Dot waits for your approval: Run rm -r build\nAnswer it in Dot on your Mac, not here."
    )


async def test_with_an_address_the_links_are_made_from_it(wired: Wired) -> None:
    newest: RunRecord = (await wired.runs())[-1]
    assert wired.router.link(newest.id) == f"{BASE}#/runs/{newest.id}"


# ----- the pictures an answer names, and its text read clean (item 617) -----------------------------------------


def _make_picture(path: Path, kind: str = "png", data: bytes | None = None) -> Path:
    """A picture (or what is named like one) made at ``path``, the folders above it too."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(HEADS[kind] if data is None else data)
    return path


def _linked(link: Path, target: Path) -> Path:
    """A link at ``link`` that leads to ``target``."""
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return link


async def _answered(wired: Wired, replies: Replies, answer: str, goal: str = "send me a picture") -> list[str]:
    """Post ``goal`` and let the scripted model answer ``answer``; returns the texts the channel was sent for it, once
    the first has come (the router sends the text first, then the pictures, then a line for those that failed)."""
    replies.push(finish(answer))
    before: int = len(wired.channel.order)
    await wired.router.inbound("imessage", goal)
    await _until(lambda: len(wired.channel.order) > before, "the answer")
    return [words for kind, words in wired.channel.order[before:] if kind == "text"]


async def _quiet(wired: Wired) -> None:
    """The runs ended and the router had time to send what it was going to."""
    await _settled(wired.manager)
    await asyncio.sleep(0.3)


async def test_the_owners_answer_names_a_screenshot_and_the_picture_goes_after_its_text(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    shot: Path = _make_picture(runtime.workspace / "screenshots" / "ekran-2026-10-24-1617.png", "png")
    before: int = len(wired.channel.order)
    words: list[str] = await _answered(
        wired, replies, f"I sent the screenshot above, and saved it as a file at {shot}", "screenshot of my screen"
    )
    await _until(lambda: wired.channel.pictures[-1:] == [shot.name], "the picture")
    # The text first, with the file's name where the long path was; then the picture, a message of its own.
    assert words == ["I sent the screenshot above, and saved it as a file at ekran-2026-10-24-1617.png"]
    assert wired.channel.order[before:] == [("text", words[0]), ("picture", shot.name)]
    assert wired.channel.picture_bytes[-1] == HEADS["png"]


async def test_the_text_of_an_answer_reads_clean_on_the_channel(wired: Wired, replies: Replies) -> None:
    answer: str = (
        "## Plan\n- **First**: `build` it\n  - then test\n1. one\n2. two\n\n> A note\n\n---\n\n"
        "| App | CPU |\n|---|---|\n| Safari | 4% |"
    )
    assert await _answered(wired, replies, answer, "explain the plan") == [
        "Plan\n• First: build it\n  ◦ then test\n1. one\n2. two\n\nA note\n\nApp · CPU\nSafari · 4%"
    ]


async def test_a_markdown_image_goes_whole_and_a_picture_in_the_home_folder_goes_too(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    chart: Path = _make_picture(runtime.workspace / "chart-a.png")
    holiday: Path = _make_picture(wired.home / "Pictures" / "holiday-a.jpg", "jpeg")
    words: list[str] = await _answered(
        wired, replies, f"The chart:\n\n![chart]({chart})\n\nAnd the photo at {holiday}.", "show me both"
    )
    await _until(lambda: wired.channel.pictures[-2:] == ["chart-a.png", "holiday-a.jpg"], "both pictures")
    assert words == ["The chart:\n\nAnd the photo at holiday-a.jpg."]


async def test_what_is_left_out_is_said_in_one_line_at_the_end_of_the_text(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    names: list[str] = [str(_make_picture(runtime.workspace / "many" / f"m{number}.png")) for number in range(6)]
    hidden: Path = _make_picture(wired.home / ".ssh" / "hidden-b.png")
    fake: Path = _make_picture(runtime.workspace / "fake-b.png", data=NOT_IMAGE)
    big: Path = _make_picture(runtime.workspace / "big-b.png")
    os.truncate(big, 20 * 1024 * 1024 + 1)
    before: int = len(wired.channel.pictures)
    wired.channel.limit = 4000
    try:
        words: list[str] = await _answered(
            wired, replies, "Here: " + " ".join([*names, str(hidden), str(fake), str(big)]), "everything"
        )
        await _until(lambda: len(wired.channel.pictures) == before + 4, "four pictures")
    finally:
        wired.channel.limit = LIMIT
    assert wired.channel.pictures[before:] == ["m0.png", "m1.png", "m2.png", "m3.png"]
    (message,) = words
    assert message.rsplit("\n\n", 1)[-1] == (
        f"Left out: hidden-b.png ({OUT_OF_PLACE}), fake-b.png ({NOT_A_PICTURE}), big-b.png ({TOO_BIG}), "
        f"2 more ({TOO_MANY})."
    )
    # What was left out keeps its words in the text; what went is its name.
    assert message.startswith("Here: m0.png m1.png m2.png m3.png ") and str(hidden) in message


async def test_the_one_line_of_what_was_left_out_stays_within_the_channels_size_with_a_cut_answer(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    fake: Path = _make_picture(runtime.workspace / "fake-g.png", data=NOT_IMAGE)
    words: list[str] = await _answered(
        wired, replies, f"{fake} " + "More words. " * 100, "a long one with a bad picture"
    )
    (message,) = words
    run: RunRecord = (await wired.runs())[-1]
    assert len(message) <= LIMIT and message.endswith(f"Left out: fake-g.png ({NOT_A_PICTURE}).")
    assert f"The rest of the answer is in Dot: {BASE}#/runs/{run.id}" in message


async def test_an_answer_that_is_only_a_picture_sends_the_picture_and_no_empty_text(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    only: Path = _make_picture(runtime.workspace / "only-c.png")
    before: int = len(wired.channel.order)
    replies.push(finish(f"![]({only})"))
    await wired.router.inbound("imessage", "just the picture")
    await _until(lambda: wired.channel.pictures[-1:] == ["only-c.png"], "the picture")
    await _quiet(wired)
    assert wired.channel.order[before:] == [("picture", "only-c.png")]


async def test_a_picture_that_cannot_be_sent_is_logged_and_told_in_one_line_after_the_others(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    good: Path = _make_picture(runtime.workspace / "good-d.png")
    bad: Path = _make_picture(runtime.workspace / "bad-d.png")
    wired.channel.refuses.add("bad-d.png")
    before: int = len(wired.channel.order)
    await _answered(wired, replies, f"Both: {bad} and {good}", "send both")
    await _until(lambda: len(wired.channel.order) == before + 3, "the text, the picture and the line")
    assert wired.channel.order[before + 1 :] == [
        ("picture", "good-d.png"),
        ("text", "Dot could not send bad-d.png. The log has why."),
    ]


async def test_a_file_a_link_leads_to_is_left_out_under_the_name_the_answer_wrote(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    """The line of what was left out goes to the owner's phone (the review of item 617): a link in the workspace to a
    file in ``~/.ssh`` must not tell the name of that file."""
    link: Path = _linked(runtime.workspace / "latest-j.png", _make_picture(wired.home / ".ssh" / "bank-token-j.png"))
    before: int = len(wired.channel.pictures)
    words: list[str] = await _answered(wired, replies, f"It is {link}", "where is it")
    await _quiet(wired)
    (message,) = words
    assert message.endswith(f"Left out: latest-j.png ({OUT_OF_PLACE}).") and "bank-token-j" not in message
    assert len(wired.channel.pictures) == before


async def test_what_is_said_of_a_picture_is_masked_whatever_name_a_finder_gives_it(
    wired: Wired, replies: Replies, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The note of what was left out and the line of what could not be sent are masked of the secrets Dot holds as the
    answer is, even when a finder names a file by what it read on the disk (the review of item 617)."""
    secret: str = "hunter2-horse-battery-staple-11"
    write_env_value(env_file_path(), "SOME_FIFTH_SERVICE_PASSWORD_FILE", secret)
    named: Path = _make_picture(runtime.workspace / "card-k.png")

    def finding(self: Pictures, text: str) -> Found:
        picture: Picture = Picture(named, f"card-{secret}.png", "png", named.stat().st_size)
        return Found(text, (picture,), (Left(f"id-{secret}.png", OUT_OF_PLACE),))

    monkeypatch.setattr(Pictures, "find", finding)
    wired.channel.refuses.add(f"card-{secret}.png")
    before: int = len(wired.channel.order)
    await _answered(wired, replies, "Here you go.", "the card")
    await _until(lambda: len(wired.channel.order) == before + 2, "the text and the line")
    note, unsent = (words for _kind, words in wired.channel.order[before:])
    assert secret not in note + unsent
    assert note.endswith(f"Left out: id-***.png ({OUT_OF_PLACE}).")
    assert unsent == "Dot could not send card-***.png. The log has why."


async def test_the_words_of_an_answer_go_whatever_looking_for_its_pictures_comes_to(
    wired: Wired, replies: Replies, runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    named: Path = _make_picture(runtime.workspace / "boom-h.png")

    def broken(self: Pictures, text: str) -> object:
        raise OSError("the disk said no")

    monkeypatch.setattr(Pictures, "find", broken)
    words: list[str] = await _answered(wired, replies, f"It is at {named}", "where is it")
    await _quiet(wired)
    # Nothing was looked for: the answer goes as it was written, and the watcher goes on.
    assert words == [f"It is at {named}"] and "boom-h.png" not in wired.channel.pictures
    replies.push(finish("Still following."))
    await wired.router.inbound("imessage", "and now?")
    await _until(lambda: wired.channel.sent[-1:] == ["Still following."], "the next answer")


async def test_a_picture_that_fails_in_a_way_nobody_expected_does_not_stop_the_others_or_the_watcher(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    first: Path = _make_picture(runtime.workspace / "first-i.png")
    second: Path = _make_picture(runtime.workspace / "second-i.png")
    wired.channel.explodes.add("first-i.png")
    before: int = len(wired.channel.order)
    await _answered(wired, replies, f"Both: {first} and {second}", "send both")
    await _until(lambda: len(wired.channel.order) == before + 3, "the text, the picture and the line")
    assert wired.channel.order[before + 1 :] == [
        ("picture", "second-i.png"),
        ("text", "Dot could not send first-i.png. The log has why."),
    ]
    wired.channel.explodes.clear()
    replies.push(finish("Still following."))
    await wired.router.inbound("imessage", "and now?")
    await _until(lambda: wired.channel.sent[-1:] == ["Still following."], "the next answer")


async def test_a_run_that_did_not_end_done_sends_no_pictures(wired: Wired, replies: Replies, runtime: Runtime) -> None:
    named: Path = _make_picture(runtime.workspace / "named-e.png")
    replies.push(finish("Done."))
    await wired.router.inbound("imessage", "make a run")
    await _until(lambda: wired.channel.sent[-1:] == ["Done."], "the run ended")
    run: RunRecord = (await wired.runs())[-1]
    before: int = len(wired.channel.order)
    await runtime.bus.emit(run.id, "run_finished", {"status": "failed", "result": f"The tool broke on {named}"}, False)
    await _until(lambda: len(wired.channel.order) == before + 1, "the failure was told")
    await asyncio.sleep(0.3)
    assert wired.channel.order[before:] == [("text", f"The run failed. The tool broke on {named}")]


async def test_an_answer_is_masked_before_the_pictures_in_it_are_looked_for(
    wired: Wired, replies: Replies, runtime: Runtime
) -> None:
    secret: str = "hunter2-horse-battery-staple-9"
    write_env_value(env_file_path(), "SOME_OTHER_SERVICE_PASSWORD_FILE", secret)
    named: Path = _make_picture(runtime.workspace / f"card-{secret}.png")
    before: int = len(wired.channel.pictures)
    words: list[str] = await _answered(wired, replies, f"The card is {named}", "the card")
    await _quiet(wired)
    assert secret not in words[0] and "card-***.png" in words[0]
    assert len(wired.channel.pictures) == before


async def test_a_router_with_no_finder_sends_an_answer_as_text_and_names_no_picture(
    runtime: Runtime, manager: RunManager, replies: Replies
) -> None:
    named: Path = _make_picture(runtime.workspace / "plain-f.png")
    channel: FakeChannel = FakeChannel()
    router: ChannelRouter = ChannelRouter(runtime, manager, {"imessage": channel}, BASE, lambda _: True, _ASKS_APART_S)
    await router.load()
    async with runtime.bus.subscribe_kinds(WATCHED_KINDS) as feed:
        watching: asyncio.Task[None] = asyncio.create_task(router.watch(feed))
        try:
            replies.push(finish(f"It is at {named}"))
            await router.inbound("imessage", "where is it")
            await _until(lambda: bool(channel.sent), "the answer")
            await asyncio.sleep(0.3)
        finally:
            watching.cancel()
            await asyncio.gather(watching, return_exceptions=True)
    assert channel.sent == [f"It is at {named}"] and channel.pictures == []
