"""A run that waits on a helper (item 1351): a message of the owner's ends the wait and the helper goes on in the
background, and its report reaches the chat when it ends, the way the end of a command in the background does (item
229).

The delegate call returns at once with the helper's id and that it works on (the helper is not stopped); the run reads
the owner's message and goes on; the helper's report comes as a note of Dot's to the run at work in the chat, or, with
none, a run is started to read it. A report not yet told when the daemon stops is told when it starts again. A Stop of
the run stops the helpers it let go on, before its own end is stored (item 197).

No network and no model service is used: a scripted model plays the runs, one script for the helpers and one for every
other run (they are told apart by the first words a helper is given).
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Literal

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.agent.helper_reports import (
    HELPER_RELEASED_EVENT,
    HELPER_RESUMED_EVENT,
    HELPER_SOURCE,
    DetachedHelper,
    keep_detached,
    kept_detached,
)
from dotagent.agent.manager import QUEUE_CHANGED_EVENT, RunManager
from dotagent.agent.plan import PLAN_EVENT, plan_from_event
from dotagent.config import load_default_config
from dotagent.core.errors import ModelUnavailableError
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.endings import RESULT_RESTARTED, RESULT_SHUTDOWN, RESULT_STOPPED
from dotagent.store.models import WORKING_STATUSES, EventRecord, RunRecord, RunStatus, ThreadRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Run the plan."
TOKEN: str = "helper-wake-test-token"
WAIT_S: float = 40.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
# What the goal of every helper starts with: the model told apart by it plays the helper.
_HELPER_GOAL: str = "HELPER-JOB"
_NOTE_OPENING: str = "[system note] A helper that kept working in the background ended."
_CUT_NOTE_OPENING: str = "[system note] A helper that kept working in the background did not fail: a restart of Dot cut"


def _turn(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


def _finish(answer: str) -> ScriptedTurn:
    return _turn("finish", {"answer": answer, "success": True})


def _delegate(name: str, **more: object) -> ScriptedCall:
    return ScriptedCall(tool="delegate", arguments={"goal": f"{_HELPER_GOAL} {name}", **more})


def _report(name: str) -> str:
    return f"Report of {name}: all good.\n\nVerification: ran the job, it passed."


class _Roles:
    """Two scripted models behind one name: the helpers' and everyone else's. The runs that are not helpers never ask
    at the same time in these tests, so the order of their turns is the order of their asks."""

    def __init__(self, main: Sequence[ScriptedTurn], helpers: Sequence[ScriptedTurn]) -> None:
        self._main: ScriptedProvider = ScriptedProvider(_PROVIDER, main)
        self._helpers: ScriptedProvider = ScriptedProvider(_PROVIDER, helpers)
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        first: str = next((message.content for message in request.messages if message.role == "user"), "")
        plays: ScriptedProvider = self._helpers if first.startswith(_HELPER_GOAL) else self._main
        return await plays.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._main.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._main.list_models()

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@asynccontextmanager
async def _session(login_env: dict[str, str], model: _Roles) -> AsyncIterator[tuple[Runtime, RunManager]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(eval_config(load_default_config()), login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _until(check: Callable[[], Awaitable[bool]], what: str) -> None:
    """Wait until ``check`` holds; a timeout fails the test, saying what was waited for."""
    try:
        async with asyncio.timeout(WAIT_S):
            while not await check():  # noqa: ASYNC110 - polls the store, which has nothing to wait on
                await asyncio.sleep(0.05)
    except TimeoutError:
        pytest.fail(f"timed out waiting for: {what}")


async def _start(manager: RunManager) -> RunRecord:
    return await manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
    )


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(WAIT_S):
        return await manager.wait(run_id)


async def _helpers_of(runtime: Runtime, parent: RunRecord, count: int = 1) -> list[RunRecord]:
    """The runs the run delegated, once ``count`` of them are at work in a tool call: the run that delegated them waits
    on them by then, as a stop that came while it started them is another matter."""
    found: list[RunRecord] = []

    async def there() -> bool:
        found[:] = [run for run in await runtime.store.list_runs(100, None) if run.parent_run_id == parent.id]
        if len(found) < count:
            return False
        return all([await runtime.store.events_of_kind(run.id, "tool_call", 1) for run in found])

    await _until(there, "the helpers started a tool")
    return found


async def _runs_of_chat(runtime: Runtime, thread_id: str) -> list[RunRecord]:
    return list(reversed(await runtime.store.list_runs(50, thread_id)))


async def _thread_text(runtime: Runtime, thread_id: str) -> str:
    return "\n".join(message.content for message in await runtime.store.load_messages(thread_id, 0))


async def _delegate_results(runtime: Runtime, run_id: str) -> list[str]:
    events: list[EventRecord] = await runtime.store.list_events(run_id, 0, 500)
    return [str(e.data["text"]) for e in events if e.kind == "tool_result" and e.data["tool"] == "delegate"]


async def _has_runs(runtime: Runtime, thread_id: str, count: int) -> bool:
    return len(await _runs_of_chat(runtime, thread_id)) >= count


async def _settled(runtime: Runtime, manager: RunManager, thread_id: str) -> None:
    """No run works in the chat, no report waits to be told, none is kept."""

    async def quiet() -> bool:
        working: bool = any(run.status in WORKING_STATUSES for run in await _runs_of_chat(runtime, thread_id))
        return not working and not manager._untold.get(thread_id) and await kept_detached(runtime.store) == []

    await _until(quiet, "the chat settled")


async def test_a_message_ends_the_wait_and_the_helpers_report_starts_a_run_in_the_idle_chat(
    login_env: dict[str, str],
) -> None:
    """The owner writes while the run waits: the delegate call returns, the helper is not stopped, the run reads the
    message and ends; the helper's report, told when it ends, starts a run in the chat that reads it."""
    roles: _Roles = _Roles(
        main=[
            _turn("update_plan", {"steps": [{"text": "Check the thing", "status": "in_progress"}]}),
            ScriptedTurn(text="", calls=(_delegate("one", step=1),)),
            _turn("update_plan", {"steps": [{"text": "Check the thing", "status": "skipped"}]}),
            _finish("Went on after your message."),
            _finish("Told."),
        ],
        helpers=[_turn("shell", {"command": "sleep 3"}), _finish(_report("one"))],
    )
    async with _session(login_env, roles) as (runtime, manager):
        async with runtime.bus.subscribe_kinds(frozenset({QUEUE_CHANGED_EVENT})) as feed:
            parent: RunRecord = await _start(manager)
            (helper,) = await _helpers_of(runtime, parent)
            manager.steer(parent.id, "Do not wait for it, just say hi.", ())
            ended: RunRecord = await _ended(manager, parent.id)
            assert (ended.status, ended.result) == ("done", "Went on after your message.")
            # The helper was not stopped by the release: it works on, and the parent's page was told.
            assert (await runtime.store.get_run(helper.id)).status in WORKING_STATUSES
            (released,) = await runtime.store.events_of_kind(parent.id, HELPER_RELEASED_EVENT, 5)
            assert released.data == {"run_id": helper.id, "step": 1, "asked": False}
            [result] = await _delegate_results(runtime, parent.id)
            assert result.startswith(f"Delegated run {helper.id} on {SCRIPTED_MODEL} keeps working in the background.")
            assert "Plan step 1 stays as it is" in result and "Verification" not in result
            # The step is not closed by the release, nor, below, by the report that comes late.
            plans: list[EventRecord] = await runtime.store.events_of_kind(parent.id, PLAN_EVENT, 20)
            assert {step.status for plan in plans for step in plan_from_event(plan).steps} == {"in_progress", "skipped"}
            assert "Do not wait for it" in await _thread_text(runtime, parent.thread_id)
            # Its end starts the run that tells the chat.
            async with asyncio.timeout(WAIT_S):
                started_id: str = ""
                while started_id == "":
                    change: EventRecord = await feed.queue.get()
                    if change.data["thread_id"] == parent.thread_id and change.data["started_run_id"] is not None:
                        started_id = str(change.data["started_run_id"])
            told: RunRecord = await runtime.store.get_run(started_id)
            assert told.source == HELPER_SOURCE and told.parent_run_id is None
            assert (await _ended(manager, told.id)).status == "done"
        assert (await runtime.store.get_run(helper.id)).status == "done"
        text: str = await _thread_text(runtime, parent.thread_id)
        assert _NOTE_OPENING in text and "Report of one: all good." in text
        # A late report closes no plan step: it says so, and the run marks the step itself.
        assert "so no plan step was marked by it" in text and "stays as it is: read the report" in text
        await _settled(runtime, manager, parent.thread_id)


async def test_a_message_releases_every_delegate_call_of_the_step_together(login_env: dict[str, str]) -> None:
    roles: _Roles = _Roles(
        main=[
            ScriptedTurn(text="", calls=(_delegate("a"), _delegate("b"))),
            _finish("Went on."),
            _finish("Told once."),
            _finish("Told twice."),
        ],
        helpers=[
            _turn("shell", {"command": "sleep 3"}),
            _turn("shell", {"command": "sleep 3"}),
            _finish(_report("a")),
            _finish(_report("b")),
        ],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        helpers: list[RunRecord] = await _helpers_of(runtime, parent, 2)
        manager.steer(parent.id, "Stop waiting.", ())
        assert (await _ended(manager, parent.id)).status == "done"
        results: list[str] = await _delegate_results(runtime, parent.id)
        assert len(results) == 2 and all("keeps working in the background." in text for text in results)
        released: list[EventRecord] = await runtime.store.events_of_kind(parent.id, HELPER_RELEASED_EVENT, 5)
        assert {str(event.data["run_id"]) for event in released} == {helper.id for helper in helpers}
        await _settled(runtime, manager, parent.thread_id)
        text: str = await _thread_text(runtime, parent.thread_id)
        assert "Report of a: all good." in text and "Report of b: all good." in text
        assert {(await runtime.store.get_run(helper.id)).status for helper in helpers} == {"done"}


async def test_a_helper_started_without_a_wait_reports_to_the_run_that_goes_on_working(
    login_env: dict[str, str],
) -> None:
    """With ``wait`` false the call returns at once; the run is still at work when the helper ends, so the report
    comes to it as a note at its next step, and no run is started."""
    roles: _Roles = _Roles(
        main=[
            ScriptedTurn(text="", calls=(_delegate("quick", wait=False),)),
            _turn("shell", {"command": "sleep 2"}),
            _finish("Read the report."),
        ],
        helpers=[_finish(_report("quick"))],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        assert (await _ended(manager, parent.id)).status == "done"
        [result] = await _delegate_results(runtime, parent.id)
        assert "keeps working in the background." in result and "You asked for no wait." in result
        assert (await runtime.store.events_of_kind(parent.id, HELPER_RELEASED_EVENT, 5))[0].data["asked"] is True
        assert "Report of quick: all good." in await _thread_text(runtime, parent.thread_id)
        assert [run.id for run in await _runs_of_chat(runtime, parent.thread_id)] == [parent.id]
        # The note is typed (item 1686): a client draws it as Dot's row and knows whose report it is.
        (helper,) = await runtime.store.list_child_runs(parent.id)
        (noted,) = await runtime.store.events_of_kind(parent.id, "steer", 5)
        assert (noted.data["of_dot"], noted.data["source"], noted.data["helper_run_id"]) == (
            True,
            "helper_report",
            helper.id,
        )
        await _settled(runtime, manager, parent.thread_id)


async def test_a_stop_while_the_run_waits_stops_the_helper_before_the_runs_end_is_stored(
    login_env: dict[str, str],
) -> None:
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("slow"),))], helpers=[_turn("shell", {"command": "sleep 60"})]
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        (helper,) = await _helpers_of(runtime, parent)
        await manager.stop(parent.id)
        ended: RunRecord = await _ended(manager, parent.id)
        stopped: RunRecord = await runtime.store.get_run(helper.id)
        assert (ended.status, stopped.status) == ("cancelled", "cancelled")
        assert stopped.finished_at is not None and ended.finished_at is not None
        assert stopped.finished_at <= ended.finished_at


async def test_a_stop_after_the_wait_was_released_stops_the_helper_first_and_tells_nothing_of_it(
    login_env: dict[str, str],
) -> None:
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("slow"),)), _turn("shell", {"command": "sleep 60"})],
        helpers=[_turn("shell", {"command": "sleep 60"})],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        (helper,) = await _helpers_of(runtime, parent)
        manager.steer(parent.id, "Stop waiting.", ())

        async def going_on() -> bool:
            return (
                len(await _delegate_results(runtime, parent.id)) == 1
                and (await runtime.store.get_run(parent.id)).steps >= 2
            )

        await _until(going_on, "the run went on")
        await manager.stop(parent.id)
        ended: RunRecord = await _ended(manager, parent.id)
        stopped: RunRecord = await runtime.store.get_run(helper.id)
        assert (ended.status, stopped.status) == ("cancelled", "cancelled")
        assert stopped.finished_at is not None and ended.finished_at is not None
        assert stopped.finished_at <= ended.finished_at
        # What the stop ended is told to nobody: nothing is kept, nothing waits, no run was started.
        await asyncio.sleep(0.5)
        assert await kept_detached(runtime.store) == [] and not manager._untold
        assert [run.id for run in await _runs_of_chat(runtime, parent.thread_id)] == [parent.id]


async def test_a_second_stop_while_the_end_of_a_run_with_a_released_helper_is_stored_still_stores_it(
    login_env: dict[str, str],
) -> None:
    """The run's end is stored after the helper it let go has ended; a second stop that comes meanwhile does not
    keep the end from being stored (the run's waiters are released, the daemon's stop does not hang)."""
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("slow"),)), _turn("shell", {"command": "sleep 60"})],
        helpers=[_turn("shell", {"command": "sleep 60"})],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        (helper,) = await _helpers_of(runtime, parent)
        manager.steer(parent.id, "Stop waiting.", ())

        async def going_on() -> bool:
            return (
                len(await _delegate_results(runtime, parent.id)) == 1
                and (await runtime.store.get_run(parent.id)).steps >= 2
            )

        await _until(going_on, "the run went on")
        await manager.stop(parent.id)
        manager.cancel(parent.id)
        assert (await _ended(manager, parent.id)).status == "cancelled"
        assert (await runtime.store.get_run(helper.id)).status == "cancelled"


async def test_the_daemons_stop_while_a_run_waits_on_a_helper_ends_both(login_env: dict[str, str]) -> None:
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("slow"),))], helpers=[_turn("shell", {"command": "sleep 60"})]
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        (helper,) = await _helpers_of(runtime, parent)
        async with asyncio.timeout(20):
            await manager.shutdown()
        assert (await runtime.store.get_run(parent.id)).status == "cancelled"
        assert (await runtime.store.get_run(helper.id)).status == "cancelled"


async def test_a_report_not_told_when_the_daemon_stopped_is_told_when_it_starts_again(
    login_env: dict[str, str],
) -> None:
    """The run ended while its helper worked on; the daemon stopped (which ends the helper too) before the helper's end
    was told. The report is kept, and the next start tells the chat, from the helper's record. With resuming off the
    helper is not resumed (item 1668): the note says the restart cut it off, not that it failed, and why."""
    roles: _Roles = _Roles(
        main=[
            ScriptedTurn(text="", calls=(_delegate("long"),)),
            _finish("Went on."),
            _finish("Told after the restart."),
        ],
        helpers=[_turn("shell", {"command": "sleep 60"})],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        (helper,) = await _helpers_of(runtime, parent)
        manager.steer(parent.id, "Stop waiting.", ())
        assert (await _ended(manager, parent.id)).status == "done"
        await manager.shutdown()
        assert (await runtime.store.get_run(helper.id)).status == "cancelled"
        assert [one.helper_run_id for one in await kept_detached(runtime.store)] == [helper.id]
        assert len(await _runs_of_chat(runtime, parent.thread_id)) == 1
        again: RunManager = RunManager(runtime)
        again.set_resume_mode("off")
        try:
            assert await again.tell_helper_reports() == 0
            runs: list[RunRecord] = await _runs_of_chat(runtime, parent.thread_id)
            assert [run.source for run in runs][1:] == [HELPER_SOURCE]
            assert (await _ended(again, runs[1].id)).status == "done"
            text: str = await _thread_text(runtime, parent.thread_id)
            assert _CUT_NOTE_OPENING in text and f"Delegated run {helper.id}" in text and "ended as cancelled" in text
            assert "resuming cut-off runs is off" in text
            assert await kept_detached(runtime.store) == []
        finally:
            await again.shutdown()


async def test_a_helper_the_daemons_stop_cut_is_resumed_at_the_next_start_and_reports_when_it_really_ends(
    login_env: dict[str, str],
) -> None:
    """Item 1668: the daemon's stop cuts a helper that works in the background for a chat whose run ended done. The
    next start resumes it as a helper of the same run, in its own chat, from the note of the cut; the parent's page
    hears it goes on; the chat is told the resumed helper's report, which says a restart cut it and Dot resumed it."""
    roles: _Roles = _Roles(
        main=[
            ScriptedTurn(text="", calls=(_delegate("long"),)),
            _finish("Went on."),
            _finish("Read the resumed report."),
        ],
        helpers=[_turn("shell", {"command": "sleep 60"}), _finish(_report("long"))],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        (helper,) = await _helpers_of(runtime, parent)
        manager.steer(parent.id, "Stop waiting.", ())
        assert (await _ended(manager, parent.id)).status == "done"
        await manager.shutdown()
        assert (await runtime.store.get_run(helper.id)).status == "cancelled"
        again: RunManager = RunManager(runtime)
        try:
            assert await again.tell_helper_reports() == 0
            [resumed] = [run for run in await _runs_of_chat(runtime, helper.thread_id) if run.id != helper.id]
            assert (resumed.parent_run_id, resumed.source) == (parent.id, helper.source)
            [moved] = await runtime.store.events_of_kind(parent.id, HELPER_RESUMED_EVENT, 5)
            assert moved.data == {"run_id": resumed.id, "from_run_id": helper.id, "step": None}
            assert [one.helper_run_id for one in await kept_detached(runtime.store)] == [resumed.id]
            assert (await _ended(again, resumed.id)).status == "done"
            assert (await runtime.store.events_of_kind(resumed.id, "resumed", 5))[0].data["cut_off"] == (
                "daemon_shutdown"
            )
            await _until(
                lambda: _has_runs(runtime, parent.thread_id, 2), "the chat was woken for the resumed helper's report"
            )
            runs: list[RunRecord] = await _runs_of_chat(runtime, parent.thread_id)
            assert runs[1].source == HELPER_SOURCE
            assert (await _ended(again, runs[1].id)).status == "done"
            text: str = await _thread_text(runtime, parent.thread_id)
            assert _NOTE_OPENING in text
            assert f"A restart of Dot cut this helper off while it worked (run {helper.id})" in text
            assert "Report of long: all good." in text and _CUT_NOTE_OPENING not in text
            await _settled(runtime, again, parent.thread_id)
        finally:
            await again.shutdown()


async def test_the_daemons_stop_keeps_the_helpers_of_a_run_it_cuts_and_the_next_start_resumes_them(
    login_env: dict[str, str],
) -> None:
    """Item 1668: the daemon's stop cuts a run that let a helper go on and is still at work. Its helper is not forgotten
    as a stop of the owner's would (that run did not fail): the next start resumes it."""
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("long"),)), _turn("shell", {"command": "sleep 60"})],
        helpers=[_turn("shell", {"command": "sleep 60"}), _finish(_report("long"))],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        (helper,) = await _helpers_of(runtime, parent)
        manager.steer(parent.id, "Stop waiting.", ())

        async def going_on() -> bool:
            return (await runtime.store.get_run(parent.id)).steps >= 2

        await _until(going_on, "the run went on")
        await manager.shutdown()
        ended: RunRecord = await runtime.store.get_run(parent.id)
        cut: RunRecord = await runtime.store.get_run(helper.id)
        assert (ended.status, cut.status) == ("cancelled", "cancelled")
        assert cut.finished_at is not None and ended.finished_at is not None and cut.finished_at <= ended.finished_at
        assert [one.helper_run_id for one in await kept_detached(runtime.store)] == [helper.id]
        again: RunManager = RunManager(runtime)
        try:
            await again.tell_helper_reports()
            [resumed] = [run for run in await _runs_of_chat(runtime, helper.thread_id) if run.id != helper.id]
            assert resumed.parent_run_id == parent.id
            assert (await _ended(again, resumed.id)).status == "done"
        finally:
            await again.shutdown()


async def test_a_chat_that_cannot_be_told_at_start_keeps_its_report_and_the_start_goes_on(
    login_env: dict[str, str],
) -> None:
    """A chat whose model has no key cannot be woken for a report: the start must not fail on it (the owner has to
    reach Settings to mend the key), and the report stays kept for the chat's next run."""
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("long"),)), _finish("Went on.")],
        helpers=[_turn("shell", {"command": "sleep 60"})],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        (helper,) = await _helpers_of(runtime, parent)
        manager.steer(parent.id, "Stop waiting.", ())
        assert (await _ended(manager, parent.id)).status == "done"
        await manager.shutdown()
        again: RunManager = RunManager(runtime)
        again.set_resume_mode("off")

        async def refused(thread_id: str) -> None:
            raise ModelUnavailableError("the chat's model has no key", {"thread_id": thread_id})

        again._tell_helper_reports = refused  # type: ignore[method-assign]
        try:
            assert await again.tell_helper_reports() == 1
            assert [one.helper_run_id for one in await kept_detached(runtime.store)] == [helper.id]
            assert len(await _runs_of_chat(runtime, parent.thread_id)) == 1
        finally:
            await again.shutdown()


async def test_a_report_the_chat_could_not_be_woken_for_is_read_by_the_next_run_at_its_first_step(
    login_env: dict[str, str],
) -> None:
    """The chat's queue is held, so no run is started for the report: the owner's next message starts a run that reads
    it first."""
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("late"),)), _finish("Went on."), _finish("Read it.")],
        helpers=[_turn("shell", {"command": "sleep 2"}), _finish(_report("late"))],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        await _helpers_of(runtime, parent)
        manager.steer(parent.id, "Stop waiting.", ())
        assert (await _ended(manager, parent.id)).status == "done"
        await runtime.store.hold_queue(parent.thread_id)

        async def waits() -> bool:
            return len(manager._untold.get(parent.thread_id, [])) == 1

        await _until(waits, "the report waits")
        assert len(await _runs_of_chat(runtime, parent.thread_id)) == 1
        assert await kept_detached(runtime.store) != []
        next_run: RunRecord = await manager.start(
            "And now?",
            "task",
            SCRIPTED_MODEL,
            parent.thread_id,
            UI_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        assert (await _ended(manager, next_run.id)).status == "done"
        assert "Report of late: all good." in await _thread_text(runtime, parent.thread_id)
        assert await kept_detached(runtime.store) == [] and not manager._untold


@asynccontextmanager
async def _served(login_env: dict[str, str], model: _Roles) -> AsyncIterator[tuple[Runtime, RunManager, str]]:
    """``_session``'s runtime and runs, served by a real server at the address it yields."""
    async with _session(login_env, model) as (runtime, manager):
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
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield runtime, manager, f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving


@pytest.mark.browser
async def test_the_line_over_the_composer_says_the_run_waits_only_while_it_is_blocked_on_its_helpers(
    login_env: dict[str, str],
) -> None:
    """While the run waits, the line says "Waiting on 1 subagent"; once a message released it, the same line says the
    subagent keeps working in the background, and a page loaded again says the same."""
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("slow"),)), _turn("shell", {"command": "sleep 60"})],
        helpers=[_turn("shell", {"command": "sleep 60"})],
    )
    async with _served(login_env, roles) as (runtime, manager, address), async_playwright() as playwright:
        parent: RunRecord = await _start(manager)
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            shots: tuple[tuple[int, Literal["light", "dark"]], ...] = ((1280, "light"), (390, "dark"))
            for width, scheme in shots:
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
                await page.goto(f"{address}/#/runs/{parent.id}")
                line = page.locator(".wait-line-text")
                if width == 1280:
                    await expect(line).to_have_text("Waiting on 1 subagent", timeout=WAIT_S * 1000)
                    manager.steer(parent.id, "Stop waiting.", ())
                await expect(line).to_have_text("1 subagent keeps working in the background", timeout=WAIT_S * 1000)
                await expect(page.locator(".wait-line")).to_have_attribute(
                    "aria-label", "Subagents work in the background"
                )
                await page.close()
            await manager.stop(parent.id)
            assert (await _ended(manager, parent.id)).status == "cancelled"
        finally:
            await browser.close()


async def test_a_helper_whose_end_the_store_has_not_taken_is_kept_and_told_once_it_has(
    login_env: dict[str, str],
) -> None:
    """A helper stored as still at work (its end refused, to be stored later) is not told and not forgotten: its report
    waits, and is told when the store has the end."""
    roles: _Roles = _Roles(main=[_finish("Done."), _finish("Told.")], helpers=[])
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        assert (await _ended(manager, parent.id)).status == "done"
        helper_chat: ThreadRecord = await runtime.store.create_thread("helper", "delegate")
        helper: RunRecord = await runtime.store.create_run(
            helper_chat.id, f"{_HELPER_GOAL} stored late", "task", SCRIPTED_MODEL, parent.id, "delegate",
            "medium", "standard", None, "yolo", False, None, None, None,
        )  # fmt: skip
        entry: DetachedHelper = DetachedHelper(
            helper_run_id=helper.id, parent_run_id=parent.id, thread_id=parent.thread_id, step=None, since=1.0
        )
        await keep_detached(runtime.store, entry)
        manager._untold[parent.thread_id] = [entry]
        await manager._tell_helper_reports(parent.thread_id)
        assert [run.id for run in await _runs_of_chat(runtime, parent.thread_id)] == [parent.id]
        assert manager._untold[parent.thread_id] == [entry] and await kept_detached(runtime.store) == [entry]
        data: JsonObject = {"status": "done", "result": _report("late"), "needs": ""}
        assert await runtime.store.end_run(helper.id, "done", _report("late"), data) is not None
        await manager._tell_helper_reports(parent.thread_id)
        told: list[RunRecord] = await _runs_of_chat(runtime, parent.thread_id)
        assert [run.source for run in told][1:] == [HELPER_SOURCE]
        assert (await _ended(manager, told[1].id)).status == "done"
        assert "Report of late: all good." in await _thread_text(runtime, parent.thread_id)
        assert await kept_detached(runtime.store) == [] and not manager._untold.get(parent.thread_id)


async def test_a_helper_whose_end_the_store_has_not_taken_is_kept_by_the_run_at_work_too(
    login_env: dict[str, str],
) -> None:
    """The same with a run at work in the chat: its notes are the way a report reaches it, and a helper the store has
    still at work is neither noted nor forgotten, so that the report comes once its end is stored."""
    roles: _Roles = _Roles(main=[_turn("shell", {"command": "sleep 6"}), _finish("Read it.")], helpers=[])
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)

        async def working() -> bool:
            return len(await runtime.store.events_of_kind(parent.id, "tool_call", 1)) == 1

        await _until(working, "the run is at work")
        helper_chat: ThreadRecord = await runtime.store.create_thread("helper", "delegate")
        helper: RunRecord = await runtime.store.create_run(
            helper_chat.id, f"{_HELPER_GOAL} stored late", "task", SCRIPTED_MODEL, parent.id, "delegate",
            "medium", "standard", None, "yolo", False, None, None, None,
        )  # fmt: skip
        entry: DetachedHelper = DetachedHelper(
            helper_run_id=helper.id, parent_run_id=parent.id, thread_id=parent.thread_id, step=None, since=1.0
        )
        await keep_detached(runtime.store, entry)
        manager._untold[parent.thread_id] = [entry]
        await manager._tell_helper_reports(parent.thread_id)
        assert manager._untold[parent.thread_id] == [entry] and await kept_detached(runtime.store) == [entry]
        assert "Report of stored: all good." not in await _thread_text(runtime, parent.thread_id)
        data: JsonObject = {"status": "done", "result": _report("stored"), "needs": ""}
        assert await runtime.store.end_run(helper.id, "done", _report("stored"), data) is not None
        await manager._tell_helper_reports(parent.thread_id)
        assert (await _ended(manager, parent.id)).status == "done"
        assert "Report of stored: all good." in await _thread_text(runtime, parent.thread_id)
        assert await kept_detached(runtime.store) == [] and not manager._untold.get(parent.thread_id)
        assert [run.id for run in await _runs_of_chat(runtime, parent.thread_id)] == [parent.id]


_PLAN_LIMIT: str = "Your Claude plan's limit is reached."


@pytest.mark.parametrize(
    ("status", "result", "error", "wakes"),
    [
        ("cancelled", RESULT_SHUTDOWN, None, True),
        ("failed", RESULT_RESTARTED, None, True),
        ("failed", _PLAN_LIMIT, {"kind": "plan_limit", "context": {"resets_at": 1.0}}, True),
        ("failed", _PLAN_LIMIT, {"kind": "plan_limit", "context": {"resets_at": None}}, True),
        ("failed", _PLAN_LIMIT, {"kind": "plan_limit", "context": {"resets_at": 4_000_000_000.0}}, False),
        ("cancelled", RESULT_STOPPED, None, False),
        ("failed", "The model said no.", {"kind": "lasting", "context": {}}, False),
    ],
    ids=["shutdown", "crash", "plan_limit_reset", "plan_limit_unnamed", "plan_limit_later", "owners_stop", "failed"],
)
async def test_a_helpers_report_wakes_a_chat_whose_newest_run_a_restart_or_the_plans_limit_ended(
    login_env: dict[str, str], status: RunStatus, result: str, error: JsonObject | None, wakes: bool
) -> None:
    """Item 1669: the chat's newest run did not end done, yet nothing of the owner's ended it: the daemon's end cut it
    off, or the Claude plan's limit ended it and the plan's window has reset (or the plan named no time). A helper's
    report then wakes the chat. A window that resets later sets a wake for that time; the owner's Stop and a failure of
    the run's own still leave the report to the owner's next run."""
    roles: _Roles = _Roles(
        main=[ScriptedTurn(text="", calls=(_delegate("late", wait=False),)), _finish("Went on."), _finish("Read it.")],
        helpers=[_turn("shell", {"command": "sleep 2"}), _finish(_report("late"))],
    )
    async with _session(login_env, roles) as (runtime, manager):
        parent: RunRecord = await _start(manager)
        assert (await _ended(manager, parent.id)).status == "done"
        # A newer run of the chat, ended as the case says before the helper ends (as a restart or the plan's limit
        # would have ended it).
        newer: RunRecord = await runtime.store.create_run(
            parent.thread_id,
            "Go on.",
            "task",
            SCRIPTED_MODEL,
            None,
            UI_SOURCE,
            "medium",
            "standard",
            None,
            "yolo",
            False,
            None,
            None,
            None,
        )
        data: JsonObject = {"status": status, "result": result, "needs": ""}
        if error is not None:
            data["error"] = error
        assert await runtime.store.end_run(newer.id, status, result, data) is not None

        async def told_or_waiting() -> bool:
            return len(await _runs_of_chat(runtime, parent.thread_id)) == 3 or bool(
                manager._untold.get(parent.thread_id)
            )

        await _until(told_or_waiting, "the helper's report was told or left waiting")
        runs: list[RunRecord] = await _runs_of_chat(runtime, parent.thread_id)
        if wakes:
            assert runs[2].source == HELPER_SOURCE
            assert (await _ended(manager, runs[2].id)).status == "done"
            assert "Report of late: all good." in await _thread_text(runtime, parent.thread_id)
        else:
            assert [run.id for run in runs] == [parent.id, newer.id]
            assert len(manager._untold[parent.thread_id]) == 1
        assert (parent.thread_id in manager._timed_wakes) == (
            error is not None and error["kind"] == "plan_limit" and not wakes
        )
