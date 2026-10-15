"""Checking the end of a run in the real loop: with ``[agent] verify_answer_paths`` an answer that names paths that do
not exist is sent back once, and in goal mode the goal check reads the facts the harness measured next to its digest.

No network and no model service is used: the scripted model plays the run and answers for the review model too.
"""

import asyncio
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.review import GOAL_TOOL
from dotagent.agent.verify import paths_note
from dotagent.config import AppConfig, load_default_config
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Build report.md from data.csv and say where both files are."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000
_FACTS: re.Pattern[str] = re.compile(
    r"<<FACTS \(measured by the harness\) (\S+)>>\n(.*)\n<<end FACTS \(measured by the harness\) \1>>", re.DOTALL
)


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


class Played(FrozenModel):
    """A run that is over: the workspace it worked in, its record, the data of its ``answer_paths`` and ``goal_check``
    events, and the requests of its steps and of its goal checks."""

    workspace: Path
    record: RunRecord
    answer_paths: tuple[JsonObject, ...]
    goal_checks: tuple[JsonObject, ...]
    steps: tuple[ChatRequest, ...]
    checks: tuple[ChatRequest, ...]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config(verify_answer_paths: bool) -> AppConfig:
    """The configuration of an eval run, with the scripted model as the review model too."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"verify_answer_paths": verify_answer_paths}),
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
        }
    )


async def _play(
    config: AppConfig,
    login_env: dict[str, str],
    goal_mode: bool,
    script: Callable[[Path], Sequence[ScriptedTurn]],
) -> Played:
    """``GOAL`` as an unattended run, in a throw-away home, of a model that plays the turns ``script`` makes for the
    run's workspace."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            model: RecordingProvider = RecordingProvider(ScriptedProvider(_PROVIDER, script(runtime.workspace)))
            await runtime.router.register(_PROVIDER, model)
            manager: RunManager = RunManager(runtime)
            try:
                run: RunRecord = await manager.start(
                    GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "ask", goal_mode, ()
                )
                async with asyncio.timeout(_TIME_LIMIT_S):
                    record: RunRecord = await manager.wait(run.id)
            finally:
                await manager.shutdown()
            events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _MAX_EVENTS)
        finally:
            await close_runtime(runtime)
    return Played(
        workspace=runtime.workspace,
        record=record,
        answer_paths=tuple(event.data for event in events if event.kind == "answer_paths"),
        goal_checks=tuple(event.data for event in events if event.kind == "goal_check"),
        steps=tuple(request for request in model.requests if request.session_key == record.thread_id),
        checks=tuple(request for request in model.requests if request.session_key == f"{record.thread_id}:goal"),
    )


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _finish(answer: str, success: bool) -> ScriptedTurn:
    return _turn(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": answer, "success": success}))


def _claim(workspace: Path) -> str:
    """An answer that says two files are in ``workspace``: a run writes report.md at most, never data.csv."""
    return f"The report is at {workspace}/report.md, the data it is built on at {workspace}/data.csv."


WRITE: ScriptedCall = ScriptedCall(tool="write_file", arguments={"path": "report.md", "content": "# Report\n"})


async def test_an_answer_that_names_paths_that_do_not_exist_is_sent_back_once(login_env: dict[str, str]) -> None:
    played: Played = await _play(
        _config(True),
        login_env,
        False,
        lambda workspace: [_finish(_claim(workspace), True), _turn(WRITE), _finish(_claim(workspace), True)],
    )
    missing: list[str] = [f"{played.workspace}/data.csv", f"{played.workspace}/report.md"]
    assert played.answer_paths == ({"missing": missing},)
    assert played.steps[1].messages[-1].content == paths_note(missing)
    # The second answer still names data.csv: a run is sent back once, never twice.
    assert len(played.steps) == 3
    assert played.record.status == "done", played.record
    assert played.record.result == _claim(played.workspace)


async def test_the_paths_of_an_answer_are_not_checked_when_the_check_is_off_or_the_run_failed(
    login_env: dict[str, str],
) -> None:
    assert not load_default_config().agent.verify_answer_paths
    off: Played = await _play(_config(False), login_env, False, lambda workspace: [_finish(_claim(workspace), True)])
    failed: Played = await _play(_config(True), login_env, False, lambda workspace: [_finish(_claim(workspace), False)])
    assert (off.record.status, off.answer_paths, len(off.steps)) == ("done", (), 1)
    assert (failed.record.status, failed.answer_paths, len(failed.steps)) == ("failed", (), 1)


async def test_the_goal_check_reads_the_facts_the_harness_measured(login_env: dict[str, str]) -> None:
    command: str = f"{sys.executable} -m unittest"
    verdict: ScriptedCall = ScriptedCall(
        tool=GOAL_TOOL, arguments={"decision": "achieved", "reason": "It is there.", "unproven_steps": []}
    )

    def script(workspace: Path) -> list[ScriptedTurn]:
        return [
            _turn(WRITE, ScriptedCall(tool="read_file", arguments={"path": "notes.txt"})),
            _turn(ScriptedCall(tool="shell", arguments={"command": command})),
            _finish(f"{_claim(workspace)} I checked https://example.org/data.", True),
            _turn(verdict),
        ]

    played: Played = await _play(_config(False), login_env, True, script)
    assert played.record.status == "done", played.record
    assert [check["decision"] for check in played.goal_checks] == ["achieved"]
    assert len(played.checks) == 1
    system, prompt = (message.content for message in played.checks[0].messages)
    assert "the facts are right" in system
    found: re.Match[str] | None = _FACTS.search(prompt)
    assert found is not None, prompt
    facts: str = found.group(2)
    workspace: Path = played.workspace
    assert f"Files the file tools wrote or edited:\n- {workspace}/report.md\n\n" in facts
    assert f"- read_file {workspace}/notes.txt: " in facts
    # Python's unittest exits with 5 when it finds no test.
    assert f"- shell {command}: [exit 5 in " in facts
    assert f"- {command}: failed, [exit 5 in " in facts
    assert "… NO TESTS RAN" in facts
    assert f"that no successful call named:\n- {workspace}/data.csv\n\n" in facts
    assert facts.endswith("that no call of the run opened:\n- https://example.org/data")
