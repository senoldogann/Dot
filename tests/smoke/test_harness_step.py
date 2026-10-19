"""One step of the improvement loop in the real runtime and store (item 391): the proposer writes a candidate from a
run that ended, the scoring set is played under the base and the candidate (the references through the scripted
model, in throw-away homes), the candidate that did no better is retired with its contrast, the experience writer is
asked, and the step is recorded; a step with no run to learn from is skipped. No model service is used."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from dotagent.agent.manager import RunManager
from dotagent.agent.review import COMPOSE_TOOL, EXPERIENCE_TOOL, PROPOSE_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.harness.probe import BASE, ProbeAsk, Prober, ProbeReport, Scorecard, probe
from dotagent.harness.service import adopted
from dotagent.harness.step import run_step
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import UI_SOURCE
from dotagent.store.models import HarnessStep, HarnessVersion, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

WAIT_S: float = 30.0
HABIT: str = "- Read a file before you change it."
GUIDANCE: str = "- The port is in config.toml, under server."
TASKS: list[str] = ["write-todo-note", "read-config-port"]
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


def _turn(tool: str, arguments: dict[str, object]) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))


_FINISH: ScriptedTurn = _turn(FINISH_TOOL, {"answer": "Done.", "success": True})


@pytest.fixture(scope="module")
def login_env() -> dict[str, str]:
    return asyncio.run(read_login_env(load_default_config().shell.login_env_timeout_s, ()))


def _config(more: dict[str, object]) -> AppConfig:
    """An eval configuration whose roles answer through the scripted model, with a scoring set of two small tasks
    played once, both scopes proposed, and ``more`` of `[harness]` set."""
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
            "harness": config.harness.model_copy(
                update={
                    "scoring_tasks": TASKS,
                    "step_repeats": 1,
                    "step_minutes": 30.0,
                    "step_tokens": 10_000_000,
                    **more,
                }
            ),
        }
    )


@asynccontextmanager
async def _session(
    login_env: dict[str, str], turns: list[ScriptedTurn], more: dict[str, object]
) -> AsyncIterator[tuple[Runtime, RunManager]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(more), login_env)
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


def _prober(config: AppConfig, login_env: dict[str, str]) -> Prober:
    """A prober of this process: the test runs nothing else while a probe plays."""

    async def play(ask: ProbeAsk) -> ProbeReport:
        return await probe(config, login_env, {}, ask)

    return play


def _scored(base_fails: frozenset[str]) -> Prober:
    """A prober that plays nothing and scores every attempt as passed, but the base's on ``base_fails``: the contrast
    and the choice without a run."""

    async def play(ask: ProbeAsk) -> ProbeReport:
        cards: tuple[Scorecard, ...] = tuple(
            Scorecard(
                task_id=task,
                attempt=attempt,
                passed=passed,
                status="done" if passed else "failed",
                cut="none",
                steps=3,
                input_tokens=100,
                output_tokens=10,
                tool_errors=0,
                seconds=1.0,
            )
            for task in ask.tasks
            for attempt in range(1, ask.repeats + 1)
            for passed in (not (ask.label == BASE and task in base_fails),)
        )
        return ProbeReport(
            label=ask.label,
            tasks=ask.tasks,
            repeats=ask.repeats,
            cards=cards,
            tokens=110 * len(cards),
            seconds=1.0,
            cut_short=None,
        )

    return play


async def test_a_step_proposes_probes_keeps_the_base_when_nothing_did_better_and_is_recorded(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [
        _FINISH,
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
        _turn(
            EXPERIENCE_TOOL,
            {"effects": [{"edit": 1, "effect": "no_effect", "why": "the same on both tasks"}], "entry": None},
        ),
    ]
    async with _session(login_env, turns, {}) as (runtime, manager):
        run: RunRecord = await _run(manager)
        step: HarnessStep = await run_step(runtime, 1.0, run.id, _prober(_config({}), login_env), True)
        assert (step.state, step.from_run, step.base_version, step.error) == ("keep", run.id, None, None)
        facts: dict[str, object] = json.loads(step.outcome)
        versions: list[HarnessVersion] = await runtime.store.list_harness_versions()
        assert len(versions) == 1 and facts["candidates"] == [versions[0].id]
        candidate: HarnessVersion = versions[0]
        # The scripted references play the same under both texts, so the candidate did no better and is retired.
        assert candidate.state == "retired"
        notes: dict[str, object] = json.loads(candidate.notes)
        assert notes["retired"] == "no better than the base on the scoring set" and "In all same" in str(
            notes["contrast"]
        )
        assert str(facts["decision"]).startswith("the base is kept") and facts["scripted"] is True
        probes = await runtime.store.harness_probes_of(step.id)
        assert sorted((probe.label, probe.task_id, probe.passed) for probe in probes) == sorted(
            [(label, task, True) for label in (BASE, candidate.id) for task in TASKS]
        )
        assert all(probe.version_id == (None if probe.label == BASE else candidate.id) for probe in probes)
        assert await runtime.store.list_harness_experiences(10) == []
        assert (await runtime.store.list_harness_steps(5))[0].id == step.id


async def test_a_step_with_no_run_to_learn_from_is_skipped(login_env: dict[str, str]) -> None:
    async with _session(login_env, [], {}) as (runtime, _manager):
        step: HarnessStep = await run_step(runtime, 1.0, None, _prober(_config({}), login_env), True)
        assert (step.state, step.from_run) == ("skipped", None)
        assert json.loads(step.outcome)["reason"].startswith("no run of the owner's ended")
        assert await runtime.store.harness_probes_of(step.id) == []


async def test_two_winners_are_composed_and_the_composition_is_probed_before_it_is_adopted(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [
        _FINISH,
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
        _turn(
            PROPOSE_TOOL,
            {
                "keep": False,
                "habits": None,
                "tool_guidance": GUIDANCE,
                "moments": ["step 2 guessed the port"],
                "summary": "Port in config",
            },
        ),
        _turn(
            COMPOSE_TOOL,
            {
                "keep_base": False,
                "habits": HABIT,
                "tool_guidance": GUIDANCE,
                "decisions": [
                    {"candidate": 1, "edit": "the habit", "verdict": "kept", "evidence": "passed what the base failed"},
                    {"candidate": 2, "edit": "the guidance", "verdict": "kept", "evidence": "the same"},
                ],
                "summary": "Read first, port in config",
            },
        ),
        _turn(
            EXPERIENCE_TOOL,
            {
                "effects": [
                    {"edit": 1, "effect": "helped", "why": "passed what the base failed"},
                    {"edit": 2, "effect": "helped", "why": "the same"},
                    {"edit": 3, "effect": "helped", "why": "both"},
                ],
                "entry": {
                    "cue": "a file is changed",
                    "lesson": "read it first (habits)",
                    "evidence": "edits 1 and 3",
                    "confidence": "medium",
                },
            },
        ),
    ]
    async with _session(login_env, turns, {"step_repeats": 2, "adopt": "auto"}) as (runtime, manager):
        run: RunRecord = await _run(manager)
        step: HarnessStep = await run_step(runtime, 1.0, run.id, _scored(frozenset(TASKS)), True)
        assert (step.state, step.error) == ("done", None)
        facts: dict[str, object] = json.loads(step.outcome)
        versions: dict[str, HarnessVersion] = {v.id: v for v in await runtime.store.list_harness_versions()}
        composed: HarnessVersion = versions[str(facts["probed"])]
        # The merge of the two winners was played in its turn, did better, and (adopt = "auto", two repeats) is in use.
        assert (composed.made_by, composed.state, facts["adopted"]) == ("composer", "adopted", True)
        assert "composed of" in str(facts["decision"]) and len(list(facts["candidates"])) == 3  # type: ignore[call-overload]
        in_use: HarnessVersion | None = await adopted(runtime.store)
        assert in_use is not None and in_use.id == composed.id
        others: list[HarnessVersion] = [v for v in versions.values() if v.id != composed.id]
        assert len(others) == 2 and all(
            v.state == "retired" and json.loads(v.notes)["retired"] == f"beaten by {composed.id} on the scoring set"
            for v in others
        )
        labels: set[str] = {probe.label for probe in await runtime.store.harness_probes_of(step.id)}
        assert labels == {BASE, composed.id, *(v.id for v in others)}
        assert [e.version_id for e in await runtime.store.list_harness_experiences(10)] == [composed.id]


async def test_a_step_whose_budget_is_spent_plays_nothing_and_keeps_the_base(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [
        _FINISH,
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
        _turn(
            EXPERIENCE_TOOL,
            {"effects": [{"edit": 1, "effect": "confounded", "why": "nothing was played"}], "entry": None},
        ),
    ]
    async with _session(login_env, turns, {"step_minutes": 0.0}) as (runtime, manager):
        run: RunRecord = await _run(manager)
        step: HarnessStep = await run_step(
            runtime, 1.0, run.id, _prober(_config({"step_minutes": 0.0}), login_env), True
        )
        assert (step.state, step.error) == ("keep", None)
        assert await runtime.store.harness_probes_of(step.id) == []
        candidate: HarnessVersion = (await runtime.store.list_harness_versions())[0]
        assert candidate.state == "retired" and "In all unclear" in str(json.loads(candidate.notes)["contrast"])


async def test_a_composer_that_fails_leaves_the_step_failed_and_the_best_candidate_probed(
    login_env: dict[str, str],
) -> None:
    bad_composition: ScriptedTurn = _turn(
        COMPOSE_TOOL,
        {"keep_base": True, "habits": "- not the base", "tool_guidance": "", "decisions": [], "summary": "x"},
    )
    turns: list[ScriptedTurn] = [
        _FINISH,
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
        _turn(
            PROPOSE_TOOL,
            {
                "keep": False,
                "habits": None,
                "tool_guidance": GUIDANCE,
                "moments": ["step 2 guessed the port"],
                "summary": "Port in config",
            },
        ),
        # Asked once more with what was wrong, the composer answers wrongly again, and its error is the step's.
        bad_composition,
        bad_composition,
        _turn(
            EXPERIENCE_TOOL,
            {
                "effects": [
                    {"edit": 1, "effect": "helped", "why": "passed what the base failed"},
                    {"edit": 2, "effect": "helped", "why": "the same"},
                ],
                "entry": None,
            },
        ),
    ]
    async with _session(login_env, turns, {"step_repeats": 2}) as (runtime, manager):
        run: RunRecord = await _run(manager)
        step: HarnessStep = await run_step(runtime, 1.0, run.id, _scored(frozenset(TASKS)), True)
        assert step.state == "failed" and step.error is not None and step.error.startswith("the composer failed")
        facts: dict[str, object] = json.loads(step.outcome)
        versions: dict[str, HarnessVersion] = {v.id: v for v in await runtime.store.list_harness_versions()}
        probed: HarnessVersion = versions[str(facts["probed"])]
        assert probed.state == "probed" and "the composer failed, so the best candidate stands" in str(
            facts["decision"]
        )
        assert len(versions) == 2 and all(v.state == "retired" for v in versions.values() if v.id != probed.id)
        assert len(list(facts["errors"])) == 1 and facts["tainted"] is False  # type: ignore[call-overload]
