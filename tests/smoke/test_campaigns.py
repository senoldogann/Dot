"""Campaigns in the real loop, the real scheduler and the API: an opening session records the targets before it ends,
the scheduler starts the next session with the brief, only a claim the goal checker accepts ends the campaign, a
session the daemon cut off is followed by a new one, the answer to a question an ended session left goes to no other
run and brings the next session forward, a moment planned for the next session keeps the rhythm of the sessions, and
the owner stops, lets go on and marks a campaign achieved.

No network and no model service is used: the scripted model plays the sessions and answers for the goal checker too,
and the notification of the owner is recorded instead of shown.
"""

import asyncio
import dataclasses
import secrets
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.loop import CAMPAIGN_TARGETS_EVENT
from dotagent.agent.manager import RunManager, thread_title
from dotagent.agent.review import GOAL_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.scheduler.runner import Scheduler
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import (
    WORKING_STATUSES,
    ApprovalRecord,
    CampaignEntry,
    CampaignRecord,
    EventRecord,
    RunRecord,
    ThreadRecord,
)
from dotagent.tools.campaign import (
    CAMPAIGN_ACHIEVED_TOOL,
    CAMPAIGN_NOTE_TOOL,
    CAMPAIGN_SCHEDULE_TOOL,
    CAMPAIGN_TARGETS_TOOL,
    CAMPAIGN_TOOLS,
)
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Collect 50 leads into leads.csv."
TARGETS: str = "50 leads in leads.csv"
VERIFICATION: str = "wc -l leads.csv counts 51 lines with the header"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000
_POLL_S: float = 0.05
TOKEN: str = f"MARKER-{secrets.token_hex(8)}"
PORT: int = 18766
BASE: str = f"http://127.0.0.1:{PORT}"


class RecordingProvider:
    """The scripted model, keeping every request it is sent."""

    def __init__(self, script: ScriptedProvider) -> None:
        self._script: ScriptedProvider = script
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()

    def of(self, session_key: str) -> list[ChatRequest]:
        return [request for request in self.requests if request.session_key == session_key]


class Notices:
    """The notifications the owner would have seen."""

    def __init__(self) -> None:
        self.shown: list[tuple[str, str]] = []

    async def notify(self, title: str, message: str) -> None:
        self.shown.append((title, message))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s)


def _config(gap_minutes: float) -> AppConfig:
    """An eval run's configuration with this gap between sessions, a scheduler that ticks fast, and the scripted model
    as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"campaign_gap_minutes": gap_minutes}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
            "scheduler": config.scheduler.model_copy(update={"tick_s": 0.2}),
        }
    )


@asynccontextmanager
async def _session(
    config: AppConfig, login_env: dict[str, str], model: RecordingProvider, notices: Notices
) -> AsyncIterator[tuple[Runtime, RunManager]]:
    """A runtime in a throw-away home whose model is ``model`` and whose notifications go to ``notices``, and its
    runs."""
    with scratch_home() as home, eval_environment(home, {}):
        opened: Runtime = await open_runtime(config, login_env)
        try:
            await opened.router.register(_PROVIDER, model)
            runtime: Runtime = dataclasses.replace(opened, notify=notices.notify)
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(opened)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _call(tool: str, arguments: JsonObject) -> ScriptedCall:
    return ScriptedCall(tool=tool, arguments=arguments)


def _finish(answer: str) -> ScriptedTurn:
    return _turn(_call(FINISH_TOOL, {"answer": answer, "success": True}))


def _targets() -> ScriptedCall:
    return _call(CAMPAIGN_TARGETS_TOOL, {"targets": TARGETS, "verification": VERIFICATION})


def _progress(text: str, leads: int) -> ScriptedCall:
    return _call(CAMPAIGN_NOTE_TOOL, {"kind": "progress", "text": text, "numbers": [{"name": "leads", "value": leads}]})


def _verdict(decision: str, reason: str) -> ScriptedTurn:
    return _turn(_call(GOAL_TOOL, {"decision": decision, "reason": reason}))


def _offered(request: ChatRequest) -> frozenset[str]:
    return frozenset(spec.name for spec in request.tools)


def _system(request: ChatRequest) -> str:
    return request.messages[0].content


def _first_user(request: ChatRequest) -> str:
    return next(message.content for message in request.messages if message.role == "user")


async def _events(runtime: Runtime, run_id: str, kind: str) -> list[EventRecord]:
    return [event for event in await runtime.store.list_events(run_id, 0, _MAX_EVENTS) if event.kind == kind]


async def _start_campaign(manager: RunManager) -> RunRecord:
    return await manager.start_campaign(GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, "ask", None, ())


async def _ended_sessions(runtime: Runtime, campaign_id: str, count: int) -> list[RunRecord]:
    """The campaign's sessions once ``count`` of them have ended."""
    while True:
        sessions: list[RunRecord] = await runtime.store.campaign_sessions(campaign_id)
        if len(sessions) >= count and all(session.status not in WORKING_STATUSES for session in sessions):
            return sessions
        await asyncio.sleep(_POLL_S)


async def _blocked(runtime: Runtime, run_id: str) -> None:
    """Return once the run waits for the owner."""
    while True:
        if (await runtime.store.get_run(run_id)).status == "blocked":
            return
        await asyncio.sleep(_POLL_S)


async def test_the_opening_session_records_the_targets_before_it_ends_and_plans_the_next_session(
    login_env: dict[str, str],
) -> None:
    model: RecordingProvider = RecordingProvider(
        ScriptedProvider(
            _PROVIDER,
            [
                _finish("Started on the leads."),
                _turn(_targets(), _progress("Wrote 5 leads to leads.csv.", 5)),
                _finish("5 of 50 leads are in leads.csv."),
                # A run of the same daemon that is not a session of a campaign.
                _finish("Done."),
            ],
        )
    )
    async with _session(_config(60), login_env, model, Notices()) as (runtime, manager):
        opening: RunRecord = await _start_campaign(manager)
        assert opening.campaign_id is not None
        async with asyncio.timeout(_TIME_LIMIT_S):
            ended: RunRecord = await manager.wait(opening.id)
        campaign: CampaignRecord = await runtime.store.get_campaign(opening.campaign_id)
        entries: list[CampaignEntry] = await runtime.store.campaign_entries(campaign.id, 10)
        reminders: list[EventRecord] = await _events(runtime, opening.id, CAMPAIGN_TARGETS_EVENT)
        other: RunRecord = await manager.start(
            GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "ask", False, ()
        )
        async with asyncio.timeout(_TIME_LIMIT_S):
            await manager.wait(other.id)
    assert (ended.status, ended.result, ended.goal_mode) == ("done", "5 of 50 leads are in leads.csv.", False), ended
    # The finish before the targets were recorded was sent back once.
    assert len(reminders) == 1
    steps: list[ChatRequest] = model.of(opening.thread_id)
    assert len(steps) == 3
    assert steps[1].messages[-1].content.startswith("[system note] The campaign has no targets yet.")
    assert _offered(steps[0]) >= CAMPAIGN_TOOLS
    assert "# Campaign\nThis run is session 1 of a campaign" in _system(steps[0])
    assert "# Open-ended goals" not in _system(steps[0])
    assert _first_user(steps[0]) == GOAL
    assert (campaign.status, campaign.title, campaign.thread_id) == ("active", thread_title(GOAL), opening.thread_id)
    assert (campaign.targets, campaign.verification) == (TARGETS, VERIFICATION)
    assert ended.finished_at is not None and campaign.next_session_at is not None
    assert abs(campaign.next_session_at - (ended.finished_at + 3600)) < 5
    assert [(entry.kind, entry.run_id, entry.numbers) for entry in entries] == [("progress", opening.id, {"leads": 5})]
    # Any other run is offered no campaign tool and told nothing of campaigns.
    (step,) = model.of(other.thread_id)
    assert not CAMPAIGN_TOOLS & _offered(step)
    assert "# Campaign" not in _system(step)
    assert "# Open-ended goals" in _system(step)


async def test_a_session_started_by_the_scheduler_reads_the_brief_and_only_an_accepted_claim_ends_the_campaign(
    login_env: dict[str, str],
) -> None:
    claim: str = "wc -l leads.csv counts 51 lines: 50 leads and the header."
    model: RecordingProvider = RecordingProvider(
        ScriptedProvider(
            _PROVIDER,
            [
                # Session 1, the opening one.
                _turn(_targets(), _progress("Wrote 5 leads to leads.csv.", 5)),
                _finish("5 of 50 leads are in leads.csv."),
                # Session 2, which the scheduler starts: a claim the goal checker refuses, then one it accepts.
                _turn(_call(CAMPAIGN_ACHIEVED_TOOL, {"evidence": "leads.csv has 50 leads."})),
                _verdict("continue", "The ledger measures 5 leads, not 50."),
                _turn(_progress("Wrote 45 more leads; wc -l counts 51 lines.", 50)),
                _turn(_call(CAMPAIGN_ACHIEVED_TOOL, {"evidence": claim})),
                _verdict("achieved", "The newest progress entry measures 50 leads, as the targets ask."),
                _finish("The campaign is achieved: 50 leads are in leads.csv."),
            ],
        )
    )
    notices: Notices = Notices()
    # A gap of a tenth of a second: the scheduler starts the second session at its next tick.
    async with _session(_config(0.1 / 60), login_env, model, notices) as (runtime, manager):
        scheduler: Scheduler = Scheduler(runtime, manager)
        await scheduler.start()
        try:
            opening: RunRecord = await _start_campaign(manager)
            assert opening.campaign_id is not None
            async with asyncio.timeout(_TIME_LIMIT_S):
                sessions: list[RunRecord] = await _ended_sessions(runtime, opening.campaign_id, 2)
        finally:
            await scheduler.stop()
        campaign: CampaignRecord = await runtime.store.get_campaign(opening.campaign_id)
        entries: list[CampaignEntry] = await runtime.store.campaign_entries(campaign.id, 10)
        second: RunRecord = sessions[1]
        thread: ThreadRecord = await runtime.store.get_thread(second.thread_id)
        results: list[EventRecord] = await _events(runtime, second.id, "tool_result")
        chats: list[str] = [chat.id for chat in await runtime.store.list_thread_summaries(50)]
    assert [session.id for session in sessions] == [opening.id, second.id]
    assert (second.status, second.source, second.goal, second.goal_mode) == ("done", SCHEDULE_SOURCE, GOAL, False)
    assert thread.title == f"{campaign.title} · session 2"
    # The campaign's conversation is a chat; the thread of a later session is listed on the campaign's page only.
    assert opening.thread_id in chats and second.thread_id not in chats
    steps: list[ChatRequest] = model.of(second.thread_id)
    assert "# Campaign\nThis run is session 2 of a campaign" in _system(steps[0])
    brief: str = _first_user(steps[0])
    assert brief.startswith(f"This is session 2 of a campaign. Its goal:\n{GOAL}\n\n")
    assert f"Targets: {TARGETS}\nVerified by: {VERIFICATION}\n\n" in brief
    assert "- [progress, 0 min ago] Wrote 5 leads to leads.csv. (leads 5)\n" in brief
    assert "Session 1 ended 0 min ago (done).\nIts answer: 5 of 50 leads are in leads.csv." in brief
    # The refused claim failed with the checker's reason, and the session went on; the accepted one ended the campaign.
    claims: list[JsonObject] = [event.data for event in results if event.data["tool"] == CAMPAIGN_ACHIEVED_TOOL]
    assert [claimed["ok"] for claimed in claims] == [False, True]
    assert str(claims[0]["text"]).startswith("Not accepted: The ledger measures 5 leads, not 50. The campaign goes on")
    assert str(claims[1]["text"]).startswith("Accepted: The newest progress entry measures 50 leads")
    checks: list[ChatRequest] = model.of(f"{second.thread_id}:campaign")
    assert len(checks) == 2
    question: str = checks[1].messages[-1].content
    assert TARGETS in question and f"How they are verified: {VERIFICATION}" in question
    assert "Wrote 45 more leads; wc -l counts 51 lines. (leads 50)" in question and claim in question
    assert (campaign.status, campaign.evidence, campaign.next_session_at) == ("achieved", claim, None)
    assert [entry.kind for entry in entries] == ["progress", "progress", "note"]
    assert entries[-1].text.startswith("The goal checker accepted the outcome: The newest progress entry")
    assert notices.shown == [("Campaign achieved", campaign.title)]


async def test_a_session_the_daemon_cut_off_is_not_resumed_and_the_next_session_says_so(
    login_env: dict[str, str],
) -> None:
    model: RecordingProvider = RecordingProvider(
        ScriptedProvider(_PROVIDER, [_finish("Checked leads.csv: the 5 leads are there.")])
    )
    async with _session(_config(60), login_env, model, Notices()) as (runtime, manager):
        store = runtime.store
        thread: ThreadRecord = await store.create_thread("Leads", EVAL_SOURCE)
        campaign: CampaignRecord = await store.create_campaign(
            "Leads", GOAL, thread.id, SCRIPTED_MODEL, runtime.config.models.effort, "ask", ()
        )
        await store.set_campaign_targets(campaign.id, TARGETS, VERIFICATION)
        cut: RunRecord = await store.create_run(
            thread.id,
            GOAL,
            "task",
            SCRIPTED_MODEL,
            None,
            EVAL_SOURCE,
            runtime.config.models.effort,
            "ask",
            False,
            campaign.id,
        )
        await store.set_run_status(cut.id, "running")
        # What the daemon does when it starts again after it died while the session ran.
        assert await manager.recover() == 1
        assert not await manager.resumable(await store.get_run(cut.id))
        before: float = time.time()
        assert await manager.resume_campaigns() == 1
        planned: CampaignRecord = await store.get_campaign(campaign.id)
        # Planned already: nothing more to do.
        assert await manager.resume_campaigns() == 0
        session: RunRecord = await manager.start_session(campaign.id)
        async with asyncio.timeout(_TIME_LIMIT_S):
            ended: RunRecord = await manager.wait(session.id)
        followed: CampaignRecord = await store.get_campaign(campaign.id)
    # The session may be what brought the daemon down: the next one starts after the usual gap, not at once.
    assert planned.next_session_at is not None and abs(planned.next_session_at - (before + 3600)) < 5
    assert ended.status == "done", ended
    (step,) = model.of(session.thread_id)
    assert "Session 1 was cut off by a stop of the daemon 0 min ago, before it ended" in _first_user(step)
    assert followed.next_session_at is not None and ended.finished_at is not None
    assert abs(followed.next_session_at - (ended.finished_at + 3600)) < 5


async def test_an_answer_to_a_question_an_ended_session_left_goes_to_no_other_run_and_brings_the_next_session_forward(
    login_env: dict[str, str],
) -> None:
    model: RecordingProvider = RecordingProvider(
        ScriptedProvider(
            _PROVIDER,
            [
                # The opening session leaves a question for the owner and ends needing its answer.
                _turn(_call(ASK_USER_TOOL, {"question": "Which region do the leads come from?"}), _targets()),
                _turn(
                    _call(
                        FINISH_TOOL,
                        {"answer": "5 leads so far.", "success": True, "needs_from_user": "The region of the leads."},
                    )
                ),
                # The owner's next message in the campaign's conversation: a run that waits for its own question.
                _turn(_call(ASK_USER_TOOL, {"question": "Which CRM do you use?"})),
            ],
        )
    )
    async with _session(_config(60), login_env, model, Notices()) as (runtime, manager):
        opening: RunRecord = await _start_campaign(manager)
        assert opening.campaign_id is not None
        async with asyncio.timeout(_TIME_LIMIT_S):
            ended: RunRecord = await manager.wait(opening.id)
        waiting: CampaignRecord = await runtime.store.get_campaign(opening.campaign_id)
        follow_up: RunRecord = await manager.start(
            "Also note the CRM.", "task", SCRIPTED_MODEL, opening.thread_id, UI_SOURCE, None, None, "ask", False, ()
        )
        async with asyncio.timeout(_TIME_LIMIT_S):
            await _blocked(runtime, follow_up.id)
        (left,) = await runtime.store.run_questions(opening.id, ASK_USER_TOOL)
        decided: ApprovalRecord = await manager.decide(left.id, True, UI_SOURCE, "Europe")
        forward: CampaignRecord = await runtime.store.get_campaign(opening.campaign_id)
        on_thread: list[RunRecord] = await runtime.store.list_runs(10, opening.thread_id)
        manager.cancel(follow_up.id)
        async with asyncio.timeout(_TIME_LIMIT_S):
            await manager.wait(follow_up.id)
        steered: list[EventRecord] = await _events(runtime, follow_up.id, "steer")
    assert (ended.status, left.status) == ("done", "left"), ended
    # It ended needing the owner, with its question unanswered: the next session waits a day for the answer.
    assert ended.finished_at is not None and waiting.next_session_at is not None
    assert abs(waiting.next_session_at - (ended.finished_at + 24 * 3600)) < 5
    assert (decided.status, decided.note) == ("approved", "Europe")
    assert forward.next_session_at is not None and forward.next_session_at <= time.time()
    # The owner's run on the conversation did not get the answer, and no run started for it.
    assert steered == []
    assert [run.id for run in on_thread] == [follow_up.id, opening.id]


async def test_a_moment_planned_for_the_next_session_keeps_the_rhythm_of_the_sessions(
    login_env: dict[str, str],
) -> None:
    # A day ahead in this Mac's time; whatever the zone of the schedules, it is hours ahead.
    tomorrow: str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    model: RecordingProvider = RecordingProvider(
        ScriptedProvider(
            _PROVIDER,
            [
                _turn(_targets(), _call(CAMPAIGN_SCHEDULE_TOOL, {"cron": "0 9 * * *"})),
                _turn(_call(CAMPAIGN_SCHEDULE_TOOL, {"at": tomorrow})),
                _finish("The next session is planned for tomorrow."),
            ],
        )
    )
    async with _session(_config(60), login_env, model, Notices()) as (runtime, manager):
        opening: RunRecord = await _start_campaign(manager)
        assert opening.campaign_id is not None
        async with asyncio.timeout(_TIME_LIMIT_S):
            ended: RunRecord = await manager.wait(opening.id)
        campaign: CampaignRecord = await runtime.store.get_campaign(opening.campaign_id)
        results: list[EventRecord] = await _events(runtime, opening.id, "tool_result")
    assert ended.status == "done", ended
    assert campaign.cron == "0 9 * * *"
    assert campaign.next_session_at is not None and campaign.next_session_at > time.time() + 3 * 3600
    planned: list[JsonObject] = [event.data for event in results if event.data["tool"] == CAMPAIGN_SCHEDULE_TOOL]
    assert str(planned[-1]["text"]).endswith("The rhythm 0 9 * * * goes on after it.")


def _context(runtime: Runtime, manager: RunManager) -> ServerContext:
    return ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=PORT,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )


async def _post(client: httpx.AsyncClient, path: str, body: JsonObject | None) -> httpx.Response:
    return await client.post(f"/api{path}", json=body)


async def test_the_owner_starts_stops_lets_go_on_and_marks_a_campaign_achieved_through_the_api(
    login_env: dict[str, str],
) -> None:
    model: RecordingProvider = RecordingProvider(
        ScriptedProvider(
            _PROVIDER, [_turn(_targets(), _progress("Wrote 5 leads.", 5)), _finish("5 of 50 leads are in leads.csv.")]
        )
    )
    async with (
        _session(_config(60), login_env, model, Notices()) as (runtime, manager),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(_context(runtime, manager))),
            base_url=BASE,
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as client,
    ):
        start: JsonObject = {"goal": GOAL, "model": SCRIPTED_MODEL, "approvals": "ask", "standing": True}
        refused: Sequence[httpx.Response] = (
            await _post(client, "/runs", {**start, "mode": "research"}),
            await _post(client, "/runs", {**start, "goal_mode": True}),
        )
        started: httpx.Response = await _post(client, "/runs", start)
        assert started.status_code == 201, started.text
        run_id: str = started.json()["id"]
        campaign_id: str = started.json()["campaign_id"]
        async with asyncio.timeout(_TIME_LIMIT_S):
            await manager.wait(run_id)
        chip: JsonObject = (await client.get(f"/api/runs/{run_id}")).json()["campaign"]
        listed: list[JsonObject] = (await client.get("/api/campaigns")).json()
        page: JsonObject = (await client.get(f"/api/campaigns/{campaign_id}")).json()
        early_resume: httpx.Response = await _post(client, f"/campaigns/{campaign_id}/resume", None)
        stopped: httpx.Response = await _post(client, f"/campaigns/{campaign_id}/stop", None)
        stopped_again: httpx.Response = await _post(client, f"/campaigns/{campaign_id}/stop", None)
        resumed: httpx.Response = await _post(client, f"/campaigns/{campaign_id}/resume", None)
        achieved: httpx.Response = await _post(client, f"/campaigns/{campaign_id}/achieved", None)
        achieved_again: httpx.Response = await _post(client, f"/campaigns/{campaign_id}/achieved", None)
        missing: httpx.Response = await client.get("/api/campaigns/cmp_missing")
    assert [response.status_code for response in refused] == [422, 422]
    assert chip == {"id": campaign_id, "title": thread_title(GOAL), "session": 1}
    assert [(summary["sessions"], summary["working"]) for summary in listed] == [(1, None)]
    assert listed[0]["next_local"] is not None
    assert listed[0]["progress"]["numbers"] == {"leads": 5}
    assert [session["id"] for session in page["sessions"]] == [run_id]
    assert page["record"]["targets"] == TARGETS
    assert [entry["kind"] for entry in page["entries"]] == ["progress"]
    assert page["tokens"] > 0
    assert early_resume.status_code == 409
    assert stopped.status_code == 200 and stopped.json()["record"]["status"] == "stopped"
    assert stopped.json()["next_local"] is None
    assert stopped.json()["entries"][-1]["text"] == "The owner stopped the campaign."
    assert stopped_again.status_code == 409
    assert resumed.status_code == 200 and resumed.json()["record"]["status"] == "active"
    assert resumed.json()["record"]["next_session_at"] <= time.time()
    assert achieved.status_code == 200
    assert (achieved.json()["record"]["status"], achieved.json()["record"]["next_session_at"]) == ("achieved", None)
    assert achieved.json()["record"]["evidence"] == "The owner marked the campaign achieved."
    assert achieved_again.status_code == 409
    assert missing.status_code == 404
