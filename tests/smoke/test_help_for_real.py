"""Item 902, the owner's word of 2026-11-01: "I press Let it help and nothing happens; when a feature is added, make
sure with tests that it works". The note of item 896 asked the run at work to hand a part over, and a run near its
end went on without. Now Let it help starts the helper's run, which reads where the one at work stands; the run at
work is told, and the helper's answer reaches it as Dot's note. Played whole in the real loop and the real route,
with a scripted model: no network and no model service is used.
"""

import asyncio
import time
from collections.abc import Sequence

import httpx
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
from dotagent.server import UI_SOURCE
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.store.models import AgentRecord, EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.office import READ_AGENT_CHAT_TOOL
from dotagent.tools.shell import read_login_env
from dotagent.tools.waits import WAIT_UNTIL_TOOL

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "help-test-token"
GOAL: str = "Find why the export of the report fails."
FOUND: str = "The export fails on line 3: the date column is empty."
GO_ON: str = "go-on.txt"
OTHER_GOAL: str = "Plan the team's party."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


class _TwoRuns(ScriptedProvider):
    """The scripted model of two runs at once, each with its own turns, told apart by what they were asked: the run at
    work's (its goal) and the helper's."""

    def __init__(self, at_work: Sequence[ScriptedTurn], helper: Sequence[ScriptedTurn]) -> None:
        super().__init__(_PROVIDER, [])
        self._at_work: ScriptedProvider = ScriptedProvider(_PROVIDER, at_work)
        self.helper: ScriptedProvider = ScriptedProvider(_PROVIDER, helper)

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        # By the first thing each was asked: the helper reads the other's goal later, in what read_agent_chat says.
        first: str = next((message.content for message in request.messages if message.role == "user"), "")
        return await (self._at_work if first.startswith(GOAL) else self.helper).complete(request, sink)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _finish(answer: str) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": True, "needs_from_user": ""}))


async def _events(runtime: Runtime, run_id: str) -> list[EventRecord]:
    return await runtime.store.list_events(run_id, 0, 10_000)


@pytest.mark.parametrize("read_outside", [False, True])
async def test_let_it_help_starts_the_helper_and_its_answer_reaches_the_run_at_work(
    login_env: dict[str, str], read_outside: bool
) -> None:
    # The helper's two steps while the run at work waits; then the run at work's end.
    # The run at work waits in code for a file the test makes once the helper's answer is on its way: it is at work
    # all along, with no model call to cut short.
    model: _TwoRuns = _TwoRuns(
        [
            _turn(ScriptedCall(tool=WAIT_UNTIL_TOOL, arguments={"file": GO_ON, "every_s": 2, "within_minutes": 2})),
            _finish("Fixed the export."),
        ],
        [],
    )
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, model)
            helper: AgentRecord = await runtime.store.create_agent(
                "Bug Finder", "Finds why a thing fails", "", "", None, None, (), None, None, ""
            )
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
            transport = httpx.ASGITransport(app=create_app(context))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1:8765", headers={"Authorization": f"Bearer {TOKEN}"}
            ) as http:
                at_work: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
                )
                if read_outside:
                    # The run at work has read what a page wrote (the pre-push review): the helper that reads its chat
                    # reads that, fenced, and is marked so, as is the answer it passes on.
                    await runtime.store.add_event(
                        at_work.id, "tool_result", {"tool": "web_fetch", "ok": True, "tainted": True}
                    )
                # A newer chat of Dot's than the one at work (the pre-push review): the helper must read the work it
                # helps, not Dot's newest chat.
                other = await runtime.store.create_thread("Other", UI_SOURCE)
                newer: RunRecord = await runtime.store.create_run(
                    other.id,
                    OTHER_GOAL,
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
                await runtime.store.finish_run(newer.id, "done", "Planned.")
                reads = {"agent": "Dot", "run": at_work.id}
                model.helper = ScriptedProvider(
                    _PROVIDER, [_turn(ScriptedCall(tool=READ_AGENT_CHAT_TOOL, arguments=reads)), _finish(FOUND)]
                )
                for _ in range(400):
                    if any(event.kind == "tool_call" for event in await _events(runtime, at_work.id)):
                        break
                    await asyncio.sleep(0.05)
                answered = await http.post(
                    "/api/agents/offers/help", json={"helper_id": helper.id, "run_id": at_work.id}
                )
                assert answered.status_code == 201, answered.text
                helping: RunRecord = RunRecord.model_validate(answered.json())
                # The helper works: a run of its own, told to read where the one at work stands.
                assert helping.agent_id == helper.id and READ_AGENT_CHAT_TOOL in helping.goal
                # A helper of the run at work, as message_agent makes one: it answers the policy's questions as that run
                # does (an initiative's asks included) and starts as untrusted as it is (the pre-push review).
                assert helping.parent_run_id == at_work.id
                # At work now, it is no longer free to help: a second press is refused, starting nothing.
                again = await http.post("/api/agents/offers/help", json={"helper_id": helper.id, "run_id": at_work.id})
                assert again.status_code == 409, again.text
                async with asyncio.timeout(_TIME_LIMIT_S):
                    ended_helper: RunRecord = await manager.wait(helping.id)
                assert (ended_helper.status, ended_helper.result) == ("done", FOUND)
                calls: list[str] = [
                    str(event.data["tool"]) for event in await _events(runtime, helping.id) if event.kind == "tool_call"
                ]
                assert READ_AGENT_CHAT_TOOL in calls
                read: list[str] = [
                    str(event.data.get("text", ""))
                    for event in await _events(runtime, helping.id)
                    if event.kind == "tool_result" and event.data.get("tool") == READ_AGENT_CHAT_TOOL
                ]
                assert len(read) == 1 and GOAL in read[0] and OTHER_GOAL not in read[0], read
                assert f"run: {at_work.id!r}" in helping.goal
                # Its answer is passed on while the run at work still works; then that run goes on and reads it.
                for _ in range(400):
                    if any("Its answer:" in text for text in _inbox_texts(manager, at_work.id)):
                        break
                    await asyncio.sleep(0.05)
                assert any("Its answer:" in text for text in _inbox_texts(manager, at_work.id))
                (runtime.workspace / GO_ON).write_text("go on\n", encoding="utf-8")
                async with asyncio.timeout(_TIME_LIMIT_S):
                    ended: RunRecord = await manager.wait(at_work.id)
                assert ended.status == "done"
                steers: list[str] = [
                    str(event.data["text"]) for event in await _events(runtime, at_work.id) if event.kind == "steer"
                ]
                assert any("has Bug Finder help you" in text for text in steers), steers
                assert any("Its answer:" in text and FOUND in text for text in steers), steers
                # Dot's notes, not the owner's words: they vouch for nothing. An answer of a helper that read what a
                # page wrote marks the run at work as having read it, as a reply through message_agent does.
                notes: list[EventRecord] = [
                    event for event in await _events(runtime, at_work.id) if event.kind == "steer"
                ]
                assert all(event.data.get("vouches") is False for event in notes)
                answer_note: EventRecord = next(event for event in notes if "Its answer:" in str(event.data["text"]))
                assert answer_note.data.get("tainted") is read_outside, answer_note.data
                assert ("<agent_reply" in str(answer_note.data["text"])) is read_outside
            # A run that is not at work is no one to help.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(context)),
                base_url="http://127.0.0.1:8765",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as http:
                late = await http.post("/api/agents/offers/help", json={"helper_id": helper.id, "run_id": at_work.id})
                assert late.status_code == 409, late.text
        finally:
            await manager.shutdown()
            await close_runtime(runtime)


def _inbox_texts(manager: RunManager, run_id: str) -> list[str]:
    """What waits in the inbox of a run at work, for the test to see the answer passed on before the run reads it."""
    handle = manager._handles.get(run_id)
    if handle is None:
        return []
    return [sent.text for sent in list(handle.inbox._queue)]  # type: ignore[attr-defined]


async def test_stopping_the_run_at_work_stops_its_helper(login_env: dict[str, str]) -> None:
    """The pre-push review: the helper is the run at work's, as a message_agent's is: when the owner stops that run,
    its helper stops with it and does nothing more."""
    model: _TwoRuns = _TwoRuns(
        [_turn(ScriptedCall(tool=WAIT_UNTIL_TOOL, arguments={"file": GO_ON, "every_s": 2, "within_minutes": 2}))],
        [_turn(ScriptedCall(tool=WAIT_UNTIL_TOOL, arguments={"file": "never.txt", "every_s": 2, "within_minutes": 2}))],
    )
    config: AppConfig = eval_config(load_default_config())
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, model)
            helper: AgentRecord = await runtime.store.create_agent(
                "Bug Finder", "Finds why a thing fails", "", "", None, None, (), None, None, ""
            )
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
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(context)),
                base_url="http://127.0.0.1:8765",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as http:
                at_work: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "yolo", False, ()
                )
                for _ in range(400):
                    if any(event.kind == "tool_call" for event in await _events(runtime, at_work.id)):
                        break
                    await asyncio.sleep(0.05)
                answered = await http.post(
                    "/api/agents/offers/help", json={"helper_id": helper.id, "run_id": at_work.id}
                )
                assert answered.status_code == 201, answered.text
                helping: RunRecord = RunRecord.model_validate(answered.json())
                for _ in range(400):
                    if any(event.kind == "tool_call" for event in await _events(runtime, helping.id)):
                        break
                    await asyncio.sleep(0.05)
                await manager.stop(at_work.id)
                async with asyncio.timeout(_TIME_LIMIT_S):
                    stopped: RunRecord = await manager.wait(helping.id)
                assert stopped.status == "cancelled", stopped.status
                assert (await runtime.store.get_run(at_work.id)).status == "cancelled"
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
