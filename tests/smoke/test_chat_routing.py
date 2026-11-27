"""Where a message sent to a chat with several tasks goes (item 2012, slice 2 of item 1982's design): a stop by its
words, a quote to the task it names, ``/new`` to a new task, and the rest to the open task the fast model names, whose
verdict never vouches; a doubt goes where the message went before, and a task past the cap waits for a free one.
**Move** sends a wrongly routed message or run elsewhere. Served by a real server and the queue's own route, the runs
played by a scripted model held by gates.

No network and no model service is used: the scripted model plays the runs and answers for the fast model too."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.busy import ROUTE_TOOL
from dotagent.agent.lanes import RoutedMessage
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import InvalidValueError, ProviderProtocolError
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.toolcalls_play import no_keychain
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.store.models import RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "routing-test-token"
WAIT_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
FORM_GOAL: str = "Fill the FORM on gistsmith.com and ask how their apps work."
FORM_ASKS: str = "I found the form: it wants your e-mail and a message. Which e-mail address should I write?"
X_GOAL: str = "Go to XCOM and improve my account."
X_ASKS: str = "Your profile has no bio yet. Which handle should the bio name?"
_LANE_MARK: str = "The new task, the owner's words:"


class _Model(ScriptedProvider):
    """Runs that finish at their first step once the gate named by a word of their task's own words is open, with the
    answer named by the first such word (``answers``); and a router whose verdict is ``route`` (``None``:
    it fails), each question it was asked kept in ``routed``."""

    def __init__(self, gates: dict[str, asyncio.Event], answers: dict[str, str]) -> None:
        super().__init__(_PROVIDER, [])
        self.gates: dict[str, asyncio.Event] = gates
        self.answers: dict[str, str] = answers
        self.route: int | None = 0
        self.routed: list[str] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if any(spec.name == ROUTE_TOOL for spec in request.tools):
            self.routed.append(request.messages[-1].content)
            if self.route is None:
                raise ProviderProtocolError("the fast model failed", {})
            verdict: ScriptedTurn = ScriptedTurn(
                text="", calls=(ScriptedCall(tool=ROUTE_TOOL, arguments={"task": self.route}),)
            )
            return await ScriptedProvider(_PROVIDER, [verdict]).complete(request, sink)
        # The task's own words: its thread's first message, past what the chat said before a lane (the system notes
        # name the other tasks at work, so they are not read).
        first: str = next((message.content for message in request.messages if message.role == "user"), "")
        said: str = first.split(_LANE_MARK)[-1]
        for word, gate in self.gates.items():
            if word in said:
                await gate.wait()
        answer: str = next((text for word, text in self.answers.items() if word in said), "Done.")
        finish: ScriptedTurn = ScriptedTurn(
            text="", calls=(ScriptedCall(tool="finish", arguments={"answer": answer, "success": True}),)
        )
        return await ScriptedProvider(_PROVIDER, [finish]).complete(request, sink)


def _config(chat_tasks: int) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"busy_parallel": True, "chat_tasks": chat_tasks}),
            "models": config.models.model_copy(update={"fast": SCRIPTED_MODEL}),
        }
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _served(
    login_env: dict[str, str], model: _Model, chat_tasks: int
) -> AsyncIterator[tuple[Runtime, RunManager, httpx.AsyncClient]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(chat_tasks), login_env)
        await runtime.router.register(_PROVIDER, model)
        manager: RunManager = RunManager(runtime)
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                assert not serving.done(), "the server stopped while starting"
                await asyncio.sleep(0.05)
            headers: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=WAIT_S
            ) as client:
                yield runtime, manager, client
        finally:
            for gate in model.gates.values():
                gate.set()
            server.should_exit = True
            stopping.set()
            await serving
            await manager.shutdown()
            await close_runtime(runtime)


async def _send(client: httpx.AsyncClient, thread_id: str, text: str) -> JsonObject:
    answer: httpx.Response = await client.post(
        f"/api/threads/{thread_id}/queue", json={"text": text, "mode": "task", "model": SCRIPTED_MODEL}
    )
    assert answer.status_code == 201, answer.text
    body: JsonObject = answer.json()
    return body


def _routed(view: JsonObject) -> JsonObject:
    routed: object = view["routed"]
    assert isinstance(routed, dict), view
    return routed


async def _until(read: Callable[[], Awaitable[bool]], what: str) -> None:
    deadline: float = time.monotonic() + WAIT_S
    while not await read():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.05)


async def _ended(runtime: Runtime, run_id: str) -> RunRecord:
    async def done() -> bool:
        return (await runtime.store.get_run(run_id)).status in ("done", "failed", "cancelled")

    await _until(done, f"the end of {run_id}")
    return await runtime.store.get_run(run_id)


async def _tasks_of(runtime: Runtime, chat_id: str) -> list[str]:
    """What each task of the chat ``chat_id`` stands as: its runs' goals and statuses, its queue and its hold, for the
    message of a wait that timed out."""
    told: list[str] = []
    for thread_id in (chat_id, *await runtime.store.lanes_of(chat_id)):
        runs: list[str] = [f"{run.goal!r} {run.status}" for run in await runtime.store.list_runs(5, thread_id)]
        queued: list[str] = [message.text for message in await runtime.store.list_queued(thread_id)]
        hold: str | None = await runtime.store.queue_hold_reason(thread_id)
        told.append(f"{thread_id}: runs {runs}, queued {queued}, held {hold}")
    return told


async def _newest(runtime: Runtime, thread_id: str) -> RunRecord:
    (newest,) = await runtime.store.list_runs(1, thread_id)
    return newest


async def _form_that_asks(runtime: Runtime, manager: RunManager) -> RunRecord:
    """The owner's case: a form task that ended asking for their e-mail address."""
    form: RunRecord = await manager.start(
        FORM_GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )
    ended: RunRecord = await _ended(runtime, form.id)
    assert ended.status == "done" and ended.result == FORM_ASKS
    return ended


async def test_a_new_request_starts_beside_a_task_that_waits_and_the_answer_goes_back_to_it(
    login_env: dict[str, str],
) -> None:
    gates: dict[str, asyncio.Event] = {"XCOM": asyncio.Event()}
    model: _Model = _Model(gates, {"FORM": FORM_ASKS})
    async with _served(login_env, model, 5) as (runtime, manager, client):
        form: RunRecord = await _form_that_asks(runtime, manager)
        # The x.com request, while the form waits on the owner: the router says it is a task of its own.
        model.route = 0
        started: JsonObject = _routed(await _send(client, form.thread_id, X_GOAL))
        assert started["kind"] == "new" and started["thread_id"] != form.thread_id
        x_thread: str = str(started["thread_id"])
        assert (await runtime.store.get_thread(x_thread)).lane_of == form.thread_id
        # The router was shown the form task's goal and its question, the owner's words and an untainted question.
        assert FORM_GOAL in model.routed[0] and "Which e-mail address" in model.routed[0]
        # The e-mail address, unquoted, while x.com works: the router names the form task, the chat's own, which the
        # message was sent to, so it is the owner's follow-up there, as before, and starts at once beside x.com.
        model.route = 1
        view: JsonObject = await _send(client, form.thread_id, "me@example.com")
        assert view["routed"] is None and view["messages"] == []

        async def answered() -> bool:
            return (await _newest(runtime, form.thread_id)).goal == "me@example.com"

        await _until(answered, "the form task's next run")
        assert (await _newest(runtime, x_thread)).status == "running"


async def test_an_answer_to_a_task_of_another_thread_starts_its_next_run_as_carried_words(
    login_env: dict[str, str],
) -> None:
    gates: dict[str, asyncio.Event] = {"REPORT": asyncio.Event()}
    model: _Model = _Model(gates, {"XCOM": X_ASKS})
    async with _served(login_env, model, 5) as (runtime, manager, client):
        chat: RunRecord = await manager.start(
            "Write the REPORT.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        model.route = 0
        x_thread: str = str(_routed(await _send(client, chat.thread_id, X_GOAL))["thread_id"])
        x_run: RunRecord = await _ended(runtime, (await _newest(runtime, x_thread)).id)
        assert x_run.result == X_ASKS
        # The owner's answer, sent to the chat while its report works: the router names the x.com task (2: after
        # the chat's own), which ended asking. Its next run starts with the words carried, as tainted as it ended.
        model.route = 2
        routed: JsonObject = _routed(await _send(client, chat.thread_id, "@dot_owner"))
        assert routed["kind"] == "continued" and routed["thread_id"] == x_thread
        continued: RunRecord = await runtime.store.get_run(str(routed["run_id"]))
        assert continued.goal == "@dot_owner" and continued.thread_id == x_thread
        # The chat's own run was not given the words.
        assert await runtime.store.list_queued(chat.thread_id) == []
        # The router names the report at work, the chat the message was sent to: queued there, as before.
        model.route = 1
        here: JsonObject = await _send(client, chat.thread_id, "Use the March figures too.")
        assert here["routed"] is None
        assert [message.text for message in await runtime.store.list_queued(chat.thread_id)] == [
            "Use the March figures too."
        ]
        # Sent to the x.com task's thread and named for the report at work there: its queue, never a steer.
        model.route = 1
        queued: JsonObject = _routed(await _send(client, x_thread, "And the totals."))
        assert queued["kind"] == "queued" and queued["thread_id"] == chat.thread_id
        assert len(await runtime.store.list_queued(chat.thread_id)) == 2
        # A doubt: queued where it was sent, as before.
        model.route = None
        doubt: JsonObject = await _send(client, chat.thread_id, "And the April ones.")
        assert doubt["routed"] is None and len(await runtime.store.list_queued(chat.thread_id)) == 3


async def test_words_route_without_the_model_a_stop_a_quote_and_new(login_env: dict[str, str]) -> None:
    gates: dict[str, asyncio.Event] = {"REPORT": asyncio.Event(), "XCOM": asyncio.Event(), "DENTIST": asyncio.Event()}
    model: _Model = _Model(gates, {})
    async with _served(login_env, model, 5) as (runtime, manager, client):
        chat: RunRecord = await manager.start(
            "Write the REPORT.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        asked: int = len(model.routed)
        # /new starts a task with its words alone, and asks no model.
        new: JsonObject = _routed(await _send(client, chat.thread_id, f"/new {X_GOAL}"))
        assert new["kind"] == "new" and len(model.routed) == asked
        x_thread: str = str(new["thread_id"])
        x_run: RunRecord = await _newest(runtime, x_thread)
        assert x_run.goal == X_GOAL
        # A quote of the x.com task's words steers its run, the owner's own word for it.
        quoted: JsonObject = _routed(await _send(client, chat.thread_id, f"> {X_GOAL}\n\nAlso fix the avatar."))
        assert quoted["kind"] == "steered" and quoted["thread_id"] == x_thread and len(model.routed) == asked
        # A quoted stop stops that task alone.
        stopped: JsonObject = _routed(await _send(client, chat.thread_id, f"> {X_GOAL}\n\ndur"))
        assert stopped["kind"] == "stopped" and stopped["run_id"] == x_run.id
        assert (await _ended(runtime, x_run.id)).status == "cancelled"
        assert (await runtime.store.get_run(chat.id)).status == "running"
        # A plain "dur" stops every task of the chat.
        second: JsonObject = _routed(await _send(client, chat.thread_id, "/new Find a DENTIST."))
        all_stopped: JsonObject = _routed(await _send(client, chat.thread_id, "Dur!"))
        assert all_stopped["kind"] == "stopped" and all_stopped["thread_id"] == chat.thread_id
        assert (await _ended(runtime, chat.id)).status == "cancelled"
        assert (await _ended(runtime, str(second["run_id"]))).status == "cancelled"
        assert len(model.routed) == asked


async def test_move_sends_a_message_elsewhere_and_a_task_past_the_cap_waits_for_a_free_one(
    login_env: dict[str, str],
) -> None:
    gates: dict[str, asyncio.Event] = {"REPORT": asyncio.Event(), "XCOM": asyncio.Event(), "DENTIST": asyncio.Event()}
    model: _Model = _Model(gates, {})
    async with _served(login_env, model, 2) as (runtime, manager, client):
        chat: RunRecord = await manager.start(
            "Write the REPORT.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        x_thread: str = str(_routed(await _send(client, chat.thread_id, f"/new {X_GOAL}"))["thread_id"])
        # Two tasks work: a third waits in the queue of the chat it was sent to.
        third: JsonObject = _routed(await _send(client, chat.thread_id, "/new Call the bank."))
        assert third["kind"] == "queued" and third["thread_id"] == chat.thread_id
        # Move it to the x.com task, the owner's own word for it: steered into its run at work.
        moved: httpx.Response = await client.post(
            f"/api/queue/{third['message_id']}/move", json={"to_thread_id": x_thread}
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["kind"] == "steered" and moved.json()["thread_id"] == x_thread
        assert await runtime.store.list_queued(chat.thread_id) == []
        # A task of another chat is refused.
        fourth: JsonObject = _routed(await _send(client, chat.thread_id, "/new Call the bank again."))
        other: RunRecord = await manager.start(
            "Write the REPORT twice.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        refused: httpx.Response = await client.post(
            f"/api/queue/{fourth['message_id']}/move", json={"to_thread_id": other.thread_id}
        )
        assert refused.status_code == 422, refused.text
        await client.delete(f"/api/queue/{fourth['message_id']}")
        # The chat's report ends; a dentist task takes its place: two tasks work again, and the chat's own is free.
        gates["REPORT"].set()
        await _ended(runtime, chat.id)
        dentist: JsonObject = _routed(await _send(client, chat.thread_id, "/new Find a DENTIST open on Saturday."))
        assert dentist["kind"] == "new"
        # A message the model cannot place goes where it was sent, as before, but waits there for a free task: the
        # chat's own thread is free, and starting it would make a third.
        model.route = None
        doubt: JsonObject = await _send(client, chat.thread_id, "Thanks for the report.")
        assert doubt["routed"] is None
        assert await runtime.store.queue_hold_reason(chat.thread_id) == "tasks"
        # A new task waits for a free one too, in the same queue.
        waits: JsonObject = _routed(await _send(client, chat.thread_id, "/new Book a table for two."))
        assert waits["kind"] == "queued" and waits["thread_id"] == chat.thread_id
        assert (await _newest(runtime, chat.thread_id)).id == chat.id
        # The dentist task ends: the waiting one starts.
        gates["DENTIST"].set()

        # It may end at once, and the next of the queue start after it, before this looks: any run of the chat counts.
        async def took() -> bool:
            return any(run.goal == "Thanks for the report." for run in await runtime.store.list_runs(5, chat.thread_id))

        try:
            await _until(took, "the task that waited for a free one")
        except AssertionError as missed:
            raise AssertionError(f"{missed}; the chat stands as {await _tasks_of(runtime, chat.thread_id)}") from missed
        assert await runtime.store.queue_hold_reason(chat.thread_id) is None


async def test_a_verdict_never_vouches_and_a_quoted_stop_of_a_task_at_rest_stops_nothing(
    login_env: dict[str, str],
) -> None:
    """The pre-push review of item 2012: words the model sent to a task at work start its next run carried, so they
    clear nothing the task's thread kept marked, whenever they start; a quoted "dur" of a task at rest stops no other
    task; and a routed message sent again with its key says no start."""
    gates: dict[str, asyncio.Event] = {"REPORT": asyncio.Event(), "XCOM": asyncio.Event()}
    model: _Model = _Model(gates, {})
    async with _served(login_env, model, 5) as (runtime, manager, client):
        chat: RunRecord = await manager.start(
            "Write the REPORT.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        x_thread: str = str(_routed(await _send(client, chat.thread_id, f"/new {X_GOAL}"))["thread_id"])
        x_run: RunRecord = await _newest(runtime, x_thread)
        # The x.com task's thread keeps the mark of words a third party wrote (item 952), as a chat does.
        await runtime.store._db.execute("INSERT INTO thread_taints(thread_id, ended) VALUES(?, 1)", (x_thread,))
        await runtime.store._db.commit()
        model.route = 2
        queued: JsonObject = _routed(await _send(client, chat.thread_id, "Also fix the avatar."))
        assert queued["kind"] == "queued" and queued["thread_id"] == x_thread
        (waiting,) = await runtime.store.list_queued(x_thread)
        assert waiting.carried_from == x_run.id
        gates["XCOM"].set()

        async def started() -> bool:
            return (await _newest(runtime, x_thread)).goal == "Also fix the avatar."

        await _until(started, "the x.com task's next run")
        after: RunRecord = await _newest(runtime, x_thread)
        opened = [event for event in await runtime.store.list_events(after.id, 0, 100) if event.kind == "run_queued"]
        # Carried words start the run marked and leave the thread's mark: an owner's message would clear both.
        assert opened[0].data["tainted"] is True
        assert await runtime.store.thread_tainted(x_thread)
        await _ended(runtime, after.id)
        # A quoted "dur" of the x.com task, now at rest, stops nothing; the report works on.
        rest: JsonObject = _routed(await _send(client, chat.thread_id, f"> {X_GOAL}\n\ndur"))
        assert rest["kind"] == "stopped" and rest["thread_id"] == x_thread and rest["run_id"] is None
        assert (await runtime.store.get_run(chat.id)).status == "running"
        # A quote of that task at rest starts its next run at once: the reply names the run, no message that is gone,
        # so that Move takes the run (the review of item 2013).
        began: JsonObject = _routed(await _send(client, chat.thread_id, f"> {X_GOAL}\n\nAnd the logo too."))
        assert began["kind"] == "queued" and began["thread_id"] == x_thread
        assert began["run_id"] is not None and began["message_id"] is None
        # A quote of the report steers it; the same message sent again with its key says no start.
        body: dict[str, str] = {
            "text": "> Write the REPORT.\n\nUse the March figures.",
            "mode": "task",
            "model": SCRIPTED_MODEL,
            "key": "quoted-report-0001",
        }
        first: httpx.Response = await client.post(f"/api/threads/{chat.thread_id}/queue", json=body)
        again: httpx.Response = await client.post(f"/api/threads/{chat.thread_id}/queue", json=body)
        assert first.json()["routed"]["kind"] == "steered"
        assert first.json()["beside_run_id"] is None and again.json()["beside_run_id"] is None


async def test_a_routed_message_is_checked_as_a_queued_one_and_move_keeps_what_it_cannot_move(
    login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second pre-push review of item 2012: words the model sends to a task at work are checked as any queued
    message is (a model with no key is refused, and nothing is queued); Move refuses a message with picks, which stays
    in its queue."""
    gates: dict[str, asyncio.Event] = {"REPORT": asyncio.Event(), "XCOM": asyncio.Event()}
    model: _Model = _Model(gates, {})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with no_keychain():
        async with _served(login_env, model, 5) as (runtime, manager, client):
            chat: RunRecord = await manager.start(
                "Write the REPORT.",
                "task",
                SCRIPTED_MODEL,
                None,
                UI_SOURCE,
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            x_thread: str = str(_routed(await _send(client, chat.thread_id, f"/new {X_GOAL}"))["thread_id"])
            model.route = 2
            refused: httpx.Response = await client.post(
                f"/api/threads/{chat.thread_id}/queue",
                json={"text": "Also fix the avatar.", "mode": "task", "model": "openai:gpt-5.5"},
            )
            assert refused.status_code >= 400, refused.text
            assert "openai:gpt-5.5" in refused.text
            assert await runtime.store.list_queued(x_thread) == []
            # A message with picks is queued where it was sent and stays there: Move refuses it.
            picked: httpx.Response = await client.post(
                f"/api/threads/{chat.thread_id}/queue",
                json={"text": "Plan the trip.", "mode": "task", "model": SCRIPTED_MODEL, "plan_first": True},
            )
            assert picked.status_code == 201, picked.text
            (waiting,) = await runtime.store.list_queued(chat.thread_id)
            moved: httpx.Response = await client.post(f"/api/queue/{waiting.id}/move", json={"to_thread_id": x_thread})
            assert moved.status_code == 422, moved.text
            assert [message.id for message in await runtime.store.list_queued(chat.thread_id)] == [waiting.id]
            assert await runtime.store.list_queued(x_thread) == []


async def test_a_move_that_fails_loses_nothing(login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The third pre-push review of item 2012: a queued message whose move fails after it was taken out is back in its
    queue, and a run whose move is refused by its checks is not stopped."""
    gates: dict[str, asyncio.Event] = {"REPORT": asyncio.Event(), "XCOM": asyncio.Event()}
    model: _Model = _Model(gates, {})
    async with _served(login_env, model, 5) as (runtime, manager, client):
        chat: RunRecord = await manager.start(
            "Write the REPORT.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        x_thread: str = str(_routed(await _send(client, chat.thread_id, f"/new {X_GOAL}"))["thread_id"])
        x_run: RunRecord = await _newest(runtime, x_thread)
        model.route = None
        await _send(client, chat.thread_id, "Use the March figures.")
        (waiting,) = await runtime.store.list_queued(chat.thread_id)

        async def fails(*_: object) -> RoutedMessage:
            raise InvalidValueError("the task could not take it", {})

        # The re-send fails after the message was taken out: it is put back where it was.
        monkeypatch.setattr(manager, "_send_quoted", fails)
        moved: httpx.Response = await client.post(f"/api/queue/{waiting.id}/move", json={"to_thread_id": x_thread})
        assert moved.status_code == 422, moved.text
        assert [message.text for message in await runtime.store.list_queued(chat.thread_id)] == [
            "Use the March figures."
        ]
        # A run whose move its checks refuse goes on working.
        monkeypatch.setattr(manager, "_check_queued", fails)
        refused: httpx.Response = await client.post(f"/api/runs/{x_run.id}/move", json={"to_thread_id": None})
        assert refused.status_code == 422, refused.text
        assert (await runtime.store.get_run(x_run.id)).status == "running"


async def test_a_chats_view_lists_its_tasks_and_moves_with_them(login_env: dict[str, str]) -> None:
    """Item 2013: the chat's tasks for the pages and the phone, and a lane's change moves the chat's revision."""
    gates: dict[str, asyncio.Event] = {"REPORT": asyncio.Event(), "XCOM": asyncio.Event()}
    model: _Model = _Model(gates, {})
    async with _served(login_env, model, 5) as (runtime, manager, client):
        chat: RunRecord = await manager.start(
            "Write the REPORT.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        alone: httpx.Response = await client.get(f"/api/threads/{chat.thread_id}/tasks")
        assert alone.status_code == 200 and alone.json()["tasks"] == [] and alone.json()["cap"] == 5
        before: int = await runtime.store.thread_revision(chat.thread_id)
        x_thread: str = str(_routed(await _send(client, chat.thread_id, f"/new {X_GOAL}"))["thread_id"])
        tasks: JsonObject = (await client.get(f"/api/threads/{x_thread}/tasks")).json()
        assert tasks["chat_id"] == chat.thread_id
        rows: object = tasks["tasks"]
        assert isinstance(rows, list)
        assert [(row["thread_id"], row["own"], row["state"]) for row in rows] == [
            (chat.thread_id, True, "working"),
            (x_thread, False, "working"),
        ]
        # The lane's view says its chat's tasks too, and the chat's revision moved with the lane's.
        view: JsonObject = (await client.get(f"/api/threads/{x_thread}/view")).json()
        assert view["tasks"]["chat_id"] == chat.thread_id
        after_start: int = await runtime.store.thread_revision(chat.thread_id)
        assert after_start > before
        changed = await runtime.store.thread_revisions_after(after_start)
        assert changed == []
        gates["XCOM"].set()
        await _ended(runtime, (await _newest(runtime, x_thread)).id)
        moved = await runtime.store.thread_revisions_after(after_start)
        assert chat.thread_id in {change.thread_id for change in moved}
        # The lane's view shows its chat's tasks, so its revision is its family's, as the chat's is.
        assert await runtime.store.thread_revision(x_thread) == await runtime.store.thread_revision(chat.thread_id)
        assert await runtime.store.thread_revision(chat.thread_id) > after_start
        ended: JsonObject = (await client.get(f"/api/threads/{chat.thread_id}/tasks")).json()
        assert [row["state"] for row in ended["tasks"]] == ["working", "ended"]
