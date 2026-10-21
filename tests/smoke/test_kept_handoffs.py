"""Steps kept for the owner (item 482) in the real loop with a scripted model. With ``[policy] unanswered = "park"`` a
step that a rule of the owner hands to them, and that nobody answers in time, is kept for them: the run is told it did
not happen and goes on to its end, its next hand-off is kept at once and the same step is kept once. "I did it" carries
the work on in the same chat as Dot's words, a new run that starts tainted as the run that kept the step ended, or a
steer of the run working there that leaves its taint as it was; Skip starts nothing, and a step past its time lapses.
A question that asks the owner's yes is refused as before, and so is every hand-off with ``"refuse"``, the default; a
yolo run asks nothing. No network and no model service is used.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio

from dotagent.agent.approvals import QUESTION_LEFT_EVENT
from dotagent.agent.gate import TAINT_EVENTS, tainted_after
from dotagent.agent.manager import RunManager
from dotagent.agent.parking import handed_back_text
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import RunConflict
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import UI_SOURCE
from dotagent.store.models import ApprovalRecord, EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "File the invoices of October."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIMEOUT_S: float = 0.3
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


class _HeldModel(ScriptedProvider):
    """The scripted model, whose ``held``-th call waits until the test lets it go: the test acts while the run is in
    that step."""

    def __init__(self, turns: Sequence[ScriptedTurn], held: int) -> None:
        super().__init__(_PROVIDER, turns)
        self._held: int = held
        self._calls: int = 0
        self.entered: asyncio.Event = asyncio.Event()
        self.release: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self._calls += 1
        if self._calls == self._held:
            self.entered.set()
            await self.release.wait()
        return await super().complete(request, sink)


@asynccontextmanager
async def _session(
    login_env: dict[str, str], model: ScriptedProvider, unanswered: Literal["refuse", "park"]
) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose model is ``model``, whose questions wait ``_TIMEOUT_S`` and come to
    ``unanswered``, where the owner hands themselves the writes of invoices and is asked about a summary; its runs."""
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(
        update={"policy": base.policy.model_copy(update={"unanswered": unanswered, "approval_timeout_s": _TIMEOUT_S})}
    )
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            await runtime.store.add_rule("handoff", "write_file", "invoice", "You file the invoices yourself")
            await runtime.store.add_rule("ask", "write_file", "summary", "")
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _write(path: Path) -> ScriptedTurn:
    return _turn(ScriptedCall(tool="write_file", arguments={"path": str(path), "content": "filed"}))


def _finish(answer: str) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True, "needs_from_user": ""}))


async def _start(manager: RunManager, source: str, approvals: ApprovalMode) -> RunRecord:
    return await manager.start(
        GOAL, "task", SCRIPTED_MODEL, None, source, None, None, "standard", None, approvals, False, ()
    )


async def _ended(manager: RunManager, run_id: str) -> RunRecord:
    async with asyncio.timeout(_TIME_LIMIT_S):
        return await manager.wait(run_id)


async def _events(runtime: Runtime, run_id: str) -> list[EventRecord]:
    return await runtime.store.list_events(run_id, 0, _MAX_EVENTS)


def _results(events: Sequence[EventRecord], tool: str) -> list[str]:
    return [str(event.data["text"]) for event in events if event.kind == "tool_result" and event.data["tool"] == tool]


async def _approvals(runtime: Runtime, run_id: str) -> list[ApprovalRecord]:
    """The run's approvals, oldest first."""
    return sorted(
        (record for record in await runtime.store.list_approvals(None, 200) if record.run_id == run_id),
        key=lambda record: record.created_at,
    )


async def _runs_of(runtime: Runtime, thread_id: str) -> list[RunRecord]:
    """The runs of the chat, oldest first."""
    return list(reversed(await runtime.store.list_runs(50, thread_id)))


async def _ended_tainted(runtime: Runtime, run_id: str) -> bool:
    found: list[EventRecord] = [
        event for kind in TAINT_EVENTS if (event := await runtime.store.last_event(run_id, kind)) is not None
    ]
    return tainted_after(found)


async def test_a_hand_off_nobody_answers_is_kept_and_i_did_it_carries_the_work_on(
    login_env: dict[str, str], tmp_path: Path
) -> None:
    first, second, third = (tmp_path / f"invoice-{number}.txt" for number in (1, 2, 3))
    summary: Path = tmp_path / "summary.txt"
    model: _HeldModel = _HeldModel(
        [
            _write(first),
            _write(second),
            _write(first),
            _write(summary),
            _finish("The invoices wait for you."),
            # The run that goes on once the first one is done by hand: a step of its own kept, then done during its
            # last step, which it reads before it ends.
            _write(third),
            _finish("The third waits for you."),
            _finish("All filed."),
        ],
        held=7,
    )
    async with _session(login_env, model, "park") as (runtime, manager):
        run: RunRecord = await _start(manager, SCHEDULE_SOURCE, "ask")
        ended: RunRecord = await _ended(manager, run.id)
        assert ended.status == "done", ended
        events: list[EventRecord] = await _events(runtime, run.id)
        records: list[ApprovalRecord] = await _approvals(runtime, run.id)
        # The first hand-off waited and expired, then was kept; the second was kept at once; the same step was not
        # kept twice; the question that asks a yes was refused as before.
        assert [(record.status, record.decided_by, record.digest is not None) for record in records] == [
            ("expired", "timeout", False),
            ("left", None, True),
            ("left", None, True),
            ("expired", "timeout", False),
        ], records
        kept_first, kept_second = records[1], records[2]
        assert (kept_first.severity, kept_first.tool) == ("handoff", "write_file")
        assert kept_first.kept_until is not None and kept_first.kept_until > time.time() + 71 * 3600
        written: list[str] = _results(events, "write_file")
        assert f"so this step was kept for the user ({kept_first.id})" in written[0], written
        assert f"so this step was kept for the user ({kept_second.id})" in written[1], written
        assert f"This step waits for the user already ({kept_first.id})" in written[2], written
        assert written[3].startswith("Not allowed:"), written
        assert [event.data["approval_id"] for event in events if event.kind == QUESTION_LEFT_EVENT] == [
            kept_first.id,
            kept_second.id,
        ]
        assert not any(path.exists() for path in (first, second, summary))

        # The run that kept them ended tainted: the run that goes on from it starts so.
        await runtime.bus.emit(run.id, "tool_result", {"tool": "web_fetch", "text": "", "tainted": True}, True)
        done: ApprovalRecord = await manager.decide(kept_first.id, True, UI_SOURCE, " Filed by hand. ")
        assert (done.status, done.decided_by) == ("approved", UI_SOURCE)
        runs: list[RunRecord] = await _runs_of(runtime, run.thread_id)
        assert [found.id for found in runs[:1]] == [run.id] and len(runs) == 2
        follow: RunRecord = runs[1]
        assert follow.goal == f"{GOAL}\n\n{handed_back_text(kept_first.id, 'Filed by hand.')}"
        assert (follow.source, follow.approvals) == (UI_SOURCE, "ask")
        queued: EventRecord | None = await runtime.store.last_event(follow.id, "run_queued")
        assert queued is not None and queued.data["tainted"] is True

        # Its own hand-off is kept; "I did it" during its last step reaches it as Dot's words: still tainted.
        async with asyncio.timeout(_TIME_LIMIT_S):
            await model.entered.wait()
        kept_third: ApprovalRecord = next(
            record for record in await _approvals(runtime, follow.id) if record.status == "left"
        )
        await manager.decide(kept_third.id, True, UI_SOURCE, "")
        model.release.set()
        assert (await _ended(manager, follow.id)).status == "done"
        steers: list[EventRecord] = [event for event in await _events(runtime, follow.id) if event.kind == "steer"]
        assert [(event.data["text"], event.data["vouches"], event.data["tainted"]) for event in steers] == [
            (handed_back_text(kept_third.id, ""), False, True)
        ]
        assert await _ended_tainted(runtime, follow.id)
        assert not third.exists()

        # Skip starts nothing; a step past its time is refused and lapses, and the sweep closes those due.
        skipped: ApprovalRecord = await manager.decide(kept_second.id, False, UI_SOURCE, "")
        assert skipped.status == "denied"
        late, due = [
            await runtime.store.create_kept_approval(
                run.id, "write_file", "file the invoice", "handoff", {}, digest, time.time() - 1.0
            )
            for digest in ("late", "due")
        ]
        with pytest.raises(RunConflict):
            await manager.decide(late.id, True, UI_SOURCE, "")
        assert (await runtime.store.get_approval(late.id)).status == "expired"
        assert await manager.lapse_kept(time.time()) == 1
        assert (await runtime.store.get_approval(due.id)).status == "expired"
        assert len(await _runs_of(runtime, run.thread_id)) == 2


async def test_with_refuse_and_in_yolo_nothing_is_kept(login_env: dict[str, str], tmp_path: Path) -> None:
    refused, yolo = tmp_path / "invoice-refused.txt", tmp_path / "invoice-yolo.txt"
    model: ScriptedProvider = ScriptedProvider(
        _PROVIDER, [_write(refused), _finish("Not filed."), _write(yolo), _finish("Filed.")]
    )
    async with _session(login_env, model, "refuse") as (runtime, manager):
        run: RunRecord = await _start(manager, SCHEDULE_SOURCE, "ask")
        assert (await _ended(manager, run.id)).status == "done"
        events: list[EventRecord] = await _events(runtime, run.id)
        assert [(record.status, record.decided_by, record.digest) for record in await _approvals(runtime, run.id)] == [
            ("expired", "timeout", None)
        ]
        assert _results(events, "write_file")[0].startswith("Not allowed:")
        assert not any(event.kind == QUESTION_LEFT_EVENT for event in events)
        assert not refused.exists()
    async with _session(login_env, model, "park") as (runtime, manager):
        run = await _start(manager, SCHEDULE_SOURCE, "yolo")
        assert (await _ended(manager, run.id)).status == "done"
        assert all(record.status != "left" for record in await _approvals(runtime, run.id))
        assert yolo.read_text() == "filed"
