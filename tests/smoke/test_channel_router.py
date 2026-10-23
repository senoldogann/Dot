"""The router of the channels (item 572), on a real runtime and run manager with a scripted model: a message of a
channel starts a run in the channel's own chat as the owner's own, with the choices the chat keeps; one that comes while
a run works is queued; the answer of each run of that chat goes back on the channel, masked, as plain text and cut to
its size with a link to the chat; a run that waits for the owner sends one line, and nothing more is answered on the
channel."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from channel_stand_ins import PROVIDER, Replies, finish

from dotagent.agent.manager import RunManager
from dotagent.channels.router import DEFAULT_CHOICES, WATCHED_KINDS, ChannelRouter
from dotagent.channels.types import Receive
from dotagent.config import load_config
from dotagent.core.errors import ConfigError
from dotagent.core.paths import env_file_path
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import write_env_value
from dotagent.store.models import RunRecord, ThreadRecord

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

    def __init__(self, runtime: Runtime, manager: RunManager) -> None:
        self.runtime: Runtime = runtime
        self.manager: RunManager = manager
        self.channel: FakeChannel = FakeChannel()
        self.on: bool = True
        self.router: ChannelRouter = ChannelRouter(
            runtime, manager, {"imessage": self.channel}, BASE, lambda _: self.on, _ASKS_APART_S
        )

    async def thread(self) -> ThreadRecord:
        stored: str | None = await self.runtime.store.kv_get("channels.thread.imessage")
        assert stored is not None
        return await self.runtime.store.get_thread(stored)

    async def runs(self) -> list[RunRecord]:
        """The runs of the channel's chat, oldest first."""
        return list(reversed(await self.runtime.store.list_runs(50, (await self.thread()).id)))


@pytest_asyncio.fixture(loop_scope="module")
async def wired(runtime: Runtime, manager: RunManager) -> AsyncIterator[Wired]:
    wiring: Wired = Wired(runtime, manager)
    # As the service starts it: the router reads which chat is whose.
    await wiring.router.load()
    async with runtime.bus.subscribe_kinds(WATCHED_KINDS) as feed:
        watching: asyncio.Task[None] = asyncio.create_task(wiring.router.watch(feed))
        try:
            yield wiring
        finally:
            watching.cancel()
            await asyncio.gather(watching, return_exceptions=True)


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
        assert wired.channel.sent[before:] == ["Queued: the run at work in this chat goes first."]
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
    await _until(lambda: wired.channel.sent[-1].startswith("The run was stopped"), "the end was told")
    assert "Stopping the run." in wired.channel.sent
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
