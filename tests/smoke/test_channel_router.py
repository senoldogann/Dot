"""The router of the channels (item 572), on a real runtime and run manager with a scripted model: a message of a
channel starts a run in the channel's own chat as the owner's own, with the choices the chat keeps; one that comes while
a run works is queued; the answer of each run of that chat goes back on the channel, masked, as plain text and cut to
its size with a link to the chat; a run that waits for the owner sends one line, and nothing more is answered on the
channel."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, Replies, finish, stop_file

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.manager import RunManager
from dotagent.channels.intake import UNREAD_ANSWER, UNREAD_NOTE
from dotagent.channels.router import DEFAULT_CHOICES, QUEUED, STOPPED, WATCHED_KINDS, ChannelRouter
from dotagent.channels.types import Receive
from dotagent.config import load_config
from dotagent.core.errors import ConfigError
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

    def paired(self) -> bool:
        return True

    async def run(self, receive: Receive) -> None:
        raise AssertionError("the router reads no channel: the tests post for it")

    async def send(self, text: str) -> None:
        self.sent.append(text)

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
        self.router: ChannelRouter = ChannelRouter(
            runtime, manager, {"imessage": self.channel}, base, lambda _: self.on, _ASKS_APART_S
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
