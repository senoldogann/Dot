"""The week of the harness in the real runtime and store (item 988): a way a tool failed in three chats on three days is
a pattern, the proposer is shown it and writes a version, and the version is tried on real runs half and half, each
run recording its half; the night weighs the trial and ends it; a version whose text names the shell is set aside
unused; one pattern-less week proposes nothing. The proposer is the scripted model: no network, no model service."""

import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest

from dotagent.agent.review import PROPOSE_TOOL
from dotagent.config import AppConfig, load_default_config
from dotagent.core.paths import db_path
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.harness.seat import TRIAL_EVENT, text_for_run, trial_of
from dotagent.harness.trial import DAY_S, PROPOSED_KEY, TRIAL_KEY, Trial, arm_of
from dotagent.harness.weekly import night
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import HarnessStep, HarnessVersion, RunRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
HABIT: str = "- Wait for a page to settle before you read it."
SNAPSHOT: str = "browser_open: taking the page snapshot failed"


def _propose(habits: str) -> ScriptedTurn:
    arguments: dict[str, object] = {
        "keep": False,
        "habits": habits,
        "tool_guidance": None,
        "moments": ["the snapshot failed in three chats"],
        "summary": "Wait for pages",
    }
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=PROPOSE_TOOL, arguments=arguments),))


@pytest.fixture(scope="module")
def login_env() -> dict[str, str]:
    return asyncio.run(read_login_env(load_default_config().shell.login_env_timeout_s, ()))


def _config() -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(update={"models": config.models.model_copy(update={"review": SCRIPTED_MODEL})})


@asynccontextmanager
async def _session(login_env: dict[str, str], turns: list[ScriptedTurn]) -> AsyncIterator[Runtime]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(), login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
            yield runtime
        finally:
            await close_runtime(runtime)


def _at(statement: str, *arguments: object) -> None:
    """Put a record at the time the test needs: the store writes the time of now and nothing else."""
    connection: sqlite3.Connection = sqlite3.connect(db_path())
    try:
        connection.execute(statement, arguments)
        connection.commit()
    finally:
        connection.close()


async def _failing_run(runtime: Runtime, when: float, ok: bool) -> RunRecord:
    """A run of the owner's in a chat of its own, ended at ``when``, whose page snapshot failed (or went well)."""
    thread = await runtime.store.create_thread("A chat", "ui")
    run: RunRecord = await runtime.store.create_run(
        thread.id, "goal", "task", SCRIPTED_MODEL, None, "ui", None, "standard", None, "ask", False, None, None, None
    )
    text: str = "Opened." if ok else "taking the page snapshot failed: target 7 closed"
    event = await runtime.store.add_event(run.id, "tool_result", {"tool": "browser_open", "ok": ok, "text": text})
    await runtime.store.finish_run(run.id, "done", "ok")
    _at("UPDATE events SET ts = ? WHERE id = ?", when, event.id)
    _at("UPDATE runs SET finished_at = ? WHERE id = ?", when, run.id)
    return run


def _id_in(half: str) -> Callable[[int], str]:
    """Run ids whose hash puts them in ``half``."""

    def nth(count: int) -> str:
        found: int = 0
        number: int = 0
        while True:
            name: str = f"run_test_{number}"
            number += 1
            if arm_of(name) == half:
                found += 1
                if found == count:
                    return name

    return nth


async def test_a_pattern_of_a_week_is_proposed_for_and_tried_half_and_half(login_env: dict[str, str]) -> None:
    async with _session(login_env, [_propose(HABIT)]) as runtime:
        now: float = time.time()
        for day in (1, 2, 3):
            await _failing_run(runtime, now - day * DAY_S, False)
        step: HarnessStep = await night(runtime, now)
        record = json.loads(step.outcome)
        assert record["week"].startswith("trying “Wait for pages” on half of the runs, for ")
        assert SNAPSHOT in record["week"]
        trial: Trial | None = await trial_of(runtime.store)
        assert trial is not None and trial.signatures == (SNAPSHOT,) and trial.in_use is None
        version: HarnessVersion = await runtime.store.get_harness_version(trial.version_id)
        assert (version.state, version.habits) == ("candidate", HABIT)
        # A run takes the candidate or the version in use by its id, and says which half it is in.
        candidate_text, candidate_version, seat = await text_for_run(runtime.store, _id_in("candidate")(1), (), now)
        assert (candidate_text.habits, candidate_version) == (HABIT, version.id)
        assert seat is not None and (seat.version, seat.arm) == (version.id, "candidate")
        in_use_text, in_use_version, other = await text_for_run(runtime.store, _id_in("in_use")(1), (), now)
        assert (in_use_text.habits, in_use_version) == ("", None)
        assert other is not None and other.arm == "in_use"
        # Past its 21 days no run takes the candidate, though no night ended the trial (the learning switched off).
        late_text, late_version, late_seat = await text_for_run(
            runtime.store, _id_in("candidate")(1), (), now + 21 * DAY_S
        )
        assert (late_text.habits, late_version, late_seat) == ("", None, None)
        # Twenty runs a half, the candidate's ending done far less often: the trial stops as worse, and the candidate is
        # set aside; the version in use was never replaced.
        for arm, done in (("candidate", 10), ("in_use", 20)):
            for number in range(20):
                thread = await runtime.store.create_thread("A chat", "ui")
                run = await runtime.store.create_run(
                    thread.id,
                    "g",
                    "task",
                    SCRIPTED_MODEL,
                    None,
                    "ui",
                    None,
                    "standard",
                    None,
                    "ask",
                    False,
                    None,
                    None,
                    None,
                )
                await runtime.store.add_event(run.id, TRIAL_EVENT, {"version": version.id, "arm": arm})
                await runtime.store.finish_run(run.id, "done" if number < done else "failed", "ok")
        ended: HarnessStep = await night(runtime, time.time() + 1)
        assert json.loads(ended.outcome)["week"].startswith("the trial of “Wait for pages”: worse: ")
        assert await trial_of(runtime.store) is None
        assert (await runtime.store.get_harness_version(version.id)).state == "retired"
        assert await runtime.store.adopted_harness_id() is None


async def test_a_version_that_names_the_shell_is_never_tried_and_a_quiet_week_proposes_nothing(
    login_env: dict[str, str],
) -> None:
    async with _session(login_env, [_propose("- Run the page through curl in the shell first.")]) as runtime:
        now: float = time.time()
        # One chat's failures on one day are no pattern.
        quiet: HarnessStep = await night(runtime, now)
        assert json.loads(quiet.outcome)["week"].startswith("no way a tool failed recurred")
        for day in (1, 2, 3):
            await _failing_run(runtime, now + 8 * DAY_S - day * DAY_S, False)
        # A week later the pattern is there, and the proposal names the shell: set aside, no trial.
        steered: HarnessStep = await night(runtime, now + 8 * DAY_S)
        assert json.loads(steered.outcome)["week"].startswith("a proposal named shell")
        assert await trial_of(runtime.store) is None
        versions: list[HarnessVersion] = await runtime.store.list_harness_versions(10)
        assert [one.state for one in versions] == ["retired"]
        assert float(await runtime.store.kv_get(PROPOSED_KEY) or 0) == now + 8 * DAY_S
        # A trial whose version is gone ends at the next night, rather than fail every night after it.
        gone: Trial = Trial(version_id="hrn_gone", started_at=now, signatures=(SNAPSHOT,), in_use=None)
        await runtime.store.kv_set(TRIAL_KEY, gone.model_dump_json())
        ended: HarnessStep = await night(runtime, now + 9 * DAY_S)
        assert (ended.state, json.loads(ended.outcome)["week"]) == ("keep", "the trial ended: its version is gone")
        assert await trial_of(runtime.store) is None
