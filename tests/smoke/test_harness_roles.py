"""The roles of the harness in the real runtime and store (item 390): the proposer, asked about a run that ended,
writes a candidate from the adopted version or keeps, and the other roles answer their questions; a wrong answer is
asked again with what was wrong. No model service is used: the scripted model plays the run and the roles."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from dotagent.agent.manager import RunManager
from dotagent.agent.review import (
    COMPOSE_TOOL,
    EXPERIENCE_TOOL,
    PROPOSE_TOOL,
    SELECT_TOOL,
    CandidateBrief,
    CompositionRequest,
    EditOutcome,
    ExperienceRequest,
    HarnessBounds,
    SelectionRequest,
)
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.harness.components import HarnessText
from dotagent.harness.propose import PROPOSER, propose_from_run
from dotagent.harness.roles import composer_of, experience_writer_of, selector_of
from dotagent.harness.service import OWNER, adopt, create_version
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import HarnessVersion, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

WAIT_S: float = 30.0
HABIT: str = "- Read a file before you change it."
GUIDANCE: str = "- rg wants --glob for a file pattern."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _turn(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


_FINISH: ScriptedTurn = _turn(FINISH_TOOL, {"answer": "Done.", "success": True})


@pytest.fixture(scope="module")
def login_env() -> dict[str, str]:
    return asyncio.run(read_login_env(load_default_config().shell.login_env_timeout_s, ()))


def _config() -> AppConfig:
    """An eval configuration whose review model, and so every role, is the scripted model."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"models": config.models.model_copy(update={"review": SCRIPTED_MODEL})})


@asynccontextmanager
async def _session(login_env: dict[str, str], turns: list[ScriptedTurn]) -> AsyncIterator[tuple[Runtime, RunManager]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


async def _run(manager: RunManager) -> RunRecord:
    run: RunRecord = await manager.start(
        "Change the port in the config.",
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
    async with asyncio.timeout(WAIT_S):
        ended: RunRecord = await manager.wait(run.id)
    assert ended.status == "done", ended.result
    return ended


async def test_the_proposer_writes_a_candidate_from_a_run_or_keeps_and_a_wrong_answer_is_asked_again(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [
        _FINISH,
        # The first proposal has no moment: asked again, the second one is kept.
        _turn(PROPOSE_TOOL, {"keep": False, "habits": HABIT, "tool_guidance": None, "moments": [], "summary": "s"}),
        _turn(
            PROPOSE_TOOL,
            {
                "keep": False,
                "habits": HABIT,
                "tool_guidance": None,
                "moments": ["step 1 wrote blind"],
                "summary": "Read first",
            },
        ),
        _turn(PROPOSE_TOOL, {"keep": True, "habits": None, "tool_guidance": None, "moments": [], "summary": "keep"}),
    ]
    async with _session(login_env, turns) as (runtime, manager):
        run: RunRecord = await _run(manager)
        base: HarnessVersion = await create_version(
            runtime.store,
            runtime.config.harness,
            HarnessText(habits="", tool_guidance=GUIDANCE),
            "guidance",
            OWNER,
            "",
            (),
        )
        await adopt(runtime.store, base.id, 1.0)
        candidate: HarnessVersion | None = await propose_from_run(runtime, run.id, "invariant")
        assert candidate is not None
        assert (candidate.state, candidate.made_by, candidate.parent_id, candidate.summary) == (
            "candidate",
            PROPOSER,
            base.id,
            "Read first",
        )
        # The component the proposal left alone is kept from the adopted version.
        assert (candidate.habits, candidate.tool_guidance) == (HABIT, GUIDANCE)
        assert json.loads(candidate.notes) == {
            "from_run": run.id,
            "scope": "invariant",
            "moments": ["step 1 wrote blind"],
            "tainted": False,
            "parent": base.id,
        }
        assert await propose_from_run(runtime, run.id, "domain") is None
        assert [version.id for version in await runtime.store.list_harness_versions()] == [candidate.id, base.id]


async def test_the_composer_the_selector_and_the_experience_writer_answer_their_questions(
    login_env: dict[str, str],
) -> None:
    bounds: HarnessBounds = HarnessBounds(habits_chars=200, tool_guidance_chars=200, component_lines=4)
    turns: list[ScriptedTurn] = [
        _turn(
            COMPOSE_TOOL,
            {
                "keep_base": False,
                "habits": HABIT,
                "tool_guidance": "",
                "decisions": [
                    {"candidate": 1, "edit": "the habit", "verdict": "kept", "evidence": "the probe ended done twice"}
                ],
                "summary": "Read first",
            },
        ),
        _turn(SELECT_TOOL, {"choice": 2, "why": "delivery: the second names the port"}),
        _turn(
            EXPERIENCE_TOOL,
            {
                "effects": [{"edit": 1, "effect": "helped", "why": "no blind write"}],
                "entry": {
                    "cue": "a file is changed",
                    "lesson": "read it first (habits)",
                    "evidence": "edit 1",
                    "confidence": "medium",
                },
            },
        ),
    ]
    async with _session(login_env, turns) as (runtime, _manager):
        composed = await composer_of(runtime, "t:harness", None)(
            CompositionRequest(
                goal="g",
                habits="",
                tool_guidance="",
                candidates=(
                    CandidateBrief(
                        number=1,
                        scope="invariant",
                        summary="s",
                        habits=HABIT,
                        tool_guidance="",
                        moments=("m",),
                        contrast="done twice",
                    ),
                ),
                bounds=bounds,
            )
        )
        assert (composed.keep_base, composed.habits, composed.decisions[0].verdict) == (False, HABIT, "kept")
        chosen = await selector_of(runtime, "t:harness", None)(
            SelectionRequest(goal="g", checks=("names the port",), answers=("no", "port 8765"))
        )
        assert chosen.choice == 2
        experience = await experience_writer_of(runtime, "t:harness", None)(
            ExperienceRequest(
                goal="g", edits=(EditOutcome(component="habits", change="c", observed="o"),), adopted=True, summary="s"
            )
        )
        assert experience.entry is not None and experience.entry.confidence == "medium"
