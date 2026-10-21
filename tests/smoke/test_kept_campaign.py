"""Steps kept for the owner in a campaign (item 484), with a scripted model. A session's question that nobody answers is
kept in the campaign's conversation: the next session waits for the owner (``campaign_wait_hours``) rather than the
gap; Allow once brings it forward, its brief says the step is allowed once, and it does the step once on that yes; the
same step later is kept again, once, as the campaign's. No network and no model service is used.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler.runner import Scheduler
from dotagent.server import UI_SOURCE
from dotagent.store.models import WORKING_STATUSES, ApprovalRecord, CampaignRecord, EventRecord, RunRecord
from dotagent.tools.campaign import CAMPAIGN_TARGETS_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Export the October report every day until the board has it."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIMEOUT_S: float = 0.3
_TIME_LIMIT_S: float = 60.0
_POLL_S: float = 0.05
_MAX_EVENTS: int = 10_000


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


class _Recording(ScriptedProvider):
    """The scripted model, which keeps what each step was sent."""

    def __init__(self, turns: Sequence[ScriptedTurn]) -> None:
        super().__init__(_PROVIDER, turns)
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await super().complete(request, sink)


@asynccontextmanager
async def _session(login_env: dict[str, str], model: ScriptedProvider) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose questions wait ``_TIMEOUT_S`` and are kept, where the owner is asked about
    the report's command, whose scheduler ticks fast; and its runs."""
    base: AppConfig = eval_config(load_default_config())
    config: AppConfig = base.model_copy(
        update={
            "policy": base.policy.model_copy(update={"unanswered": "park", "approval_timeout_s": _TIMEOUT_S}),
            "scheduler": base.scheduler.model_copy(update={"tick_s": 0.2}),
        }
    )
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            await runtime.store.add_rule("ask", "shell", "report", "")
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _report() -> ScriptedTurn:
    return _turn(ScriptedCall(tool="shell", arguments={"command": "echo exported >> report.txt"}))


def _finish(answer: str) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True}))


async def _ended_sessions(runtime: Runtime, campaign_id: str, count: int) -> list[RunRecord]:
    async with asyncio.timeout(_TIME_LIMIT_S):
        while True:
            sessions: list[RunRecord] = await runtime.store.campaign_sessions(campaign_id)
            if len(sessions) >= count and all(session.status not in WORKING_STATUSES for session in sessions):
                return sessions
            await asyncio.sleep(_POLL_S)


async def _kept(runtime: Runtime, run_id: str) -> list[ApprovalRecord]:
    return [record for record in await runtime.store.run_approvals(run_id) if record.kept_until is not None]


async def test_a_session_keeps_a_question_and_allow_once_brings_the_next_session_which_does_it_once(
    login_env: dict[str, str],
) -> None:
    targets: ScriptedCall = ScriptedCall(
        tool=CAMPAIGN_TARGETS_TOOL, arguments={"targets": "The board has the report", "verification": "Ask them"}
    )
    model: _Recording = _Recording(
        [
            # Session 1: the report's command is asked about, nobody answers, and it is kept.
            _turn(targets),
            _report(),
            _finish("The report waits for the owner's yes."),
            # Session 2, which Allow once brings forward: the step on that yes, then again, kept again.
            _report(),
            _report(),
            _finish("Exported once."),
        ]
    )
    async with _session(login_env, model) as (runtime, manager):
        scheduler: Scheduler = Scheduler(runtime, manager)
        await scheduler.start()
        try:
            opening: RunRecord = await manager.start_campaign(
                GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, "standard", None, "ask", None, (), None
            )
            assert opening.campaign_id is not None
            campaign_id: str = opening.campaign_id
            await _ended_sessions(runtime, campaign_id, 1)
            kept: list[ApprovalRecord] = await _kept(runtime, opening.id)
            campaign: CampaignRecord = await runtime.store.get_campaign(campaign_id)
            assert [(record.status, record.scope_campaign_id) for record in kept] == [("left", campaign_id)]
            assert kept[0].scope_thread_id == campaign.thread_id
            # The next session waits for the owner, not the hour's gap.
            assert campaign.next_session_at is not None and campaign.next_session_at > time.time() + 23 * 3600

            allowed: ApprovalRecord = await manager.decide(kept[0].id, True, UI_SOURCE, "Go ahead.")
            assert allowed.status == "approved" and allowed.used_at is None
            sessions: list[RunRecord] = await _ended_sessions(runtime, campaign_id, 2)
        finally:
            await scheduler.stop()
        second: RunRecord = sessions[1]
        brief: str = next(
            message.content
            for request in model.requests
            if request.session_key == second.thread_id
            for message in request.messages
            if message.role == "user"
        )
        allowed_line: str = "allowed by the owner once: do it when you reach it, exactly as it was, once."
        assert f"{allowed_line} They wrote: Go ahead." in brief, brief
        auto: list[EventRecord] = [
            event
            for event in await runtime.store.list_events(second.id, 0, _MAX_EVENTS)
            if event.kind == "approval_auto"
        ]
        assert [(event.data["decided_by"], event.data["status"]) for event in auto] == [("permit", "approved")]
        assert (runtime.workspace / "report.txt").read_text() == "exported\n"
        assert (await runtime.store.get_approval(kept[0].id)).used_at is not None
        # The same step again is kept again, as the campaign's, and the session after waits for the owner once more.
        again: list[ApprovalRecord] = await _kept(runtime, second.id)
        assert [(record.status, record.scope_campaign_id) for record in again] == [("left", campaign_id)]
        after: CampaignRecord = await runtime.store.get_campaign(campaign_id)
        assert after.next_session_at is not None and after.next_session_at > time.time() + 23 * 3600
