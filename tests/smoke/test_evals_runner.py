"""The eval runner end to end: a scripted model plays the real loop, the real tools, the real policy and a real store.

No network and no model service is used. What is not real is only the model, which replays a script.
"""

import os
from collections.abc import Sequence
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.cli_eval import EvalCheckCommand, cmd_eval_check
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.catalog import select_tasks
from dotagent.evals.results import EvalReport, EvalSettings, TaskOutcome
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, replay_reference
from dotagent.evals.scoring import Scorecard, score_report
from dotagent.evals.types import EvalTask, ScriptedCall, ScriptedTurn
from dotagent.policy.types import QuestionMode
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

# The name of an API key that nobody sets: a provider that needs it cannot be called.
UNSET_KEY: str = "DOT_TEST_SURELY_UNSET_KEY"


@pytest.fixture(scope="module")
def config() -> AppConfig:
    return eval_config(load_default_config())


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env(config: AppConfig) -> dict[str, str]:
    return await read_login_env(config.shell.login_env_timeout_s, ())


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _finish(answer: str, success: bool) -> ScriptedTurn:
    return _turn(ScriptedCall(tool="finish", arguments={"answer": answer, "success": success}))


def _with_reference(task_id: str, reference: Sequence[ScriptedTurn]) -> EvalTask:
    return select_tasks([task_id])[0].model_copy(update={"reference": tuple(reference)})


@pytest.mark.browser
async def test_every_task_of_the_catalog_is_sound(capsys: pytest.CaptureFixture[str]) -> None:
    """The whole check of ``dotx eval check``, which plays the form task in a headless Chrome too (item 558: marked, so
    that ``-m "not browser"`` starts no Chrome; the gate runs the same check as a step of its own)."""
    assert await cmd_eval_check(EvalCheckCommand(name="eval-check")) == 0
    printed: str = capsys.readouterr().out
    assert "FAIL" not in printed
    assert "Every task can be passed and cannot be passed by doing nothing." in printed


@pytest.fixture
def sentinel_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty directory that stands for the home of the user while a test runs."""
    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.delenv("DOT_CHROME_DIR", raising=False)
    return home


def _names(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


async def test_an_attempt_leaves_the_environment_and_the_home_as_they_were(
    config: AppConfig, login_env: dict[str, str], sentinel_home: Path
) -> None:
    outcome: TaskOutcome = await replay_reference(select_tasks(["write-todo-note"])[0], config, login_env)
    assert outcome.passed
    assert os.environ["DOT_HOME"] == str(sentinel_home)
    assert "DOT_CHROME_DIR" not in os.environ
    assert _names(sentinel_home) == []


async def test_a_run_that_writes_the_wrong_thing_but_says_it_is_done_fails_and_counts_as_a_false_success(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    sloppy: EvalTask = _with_reference(
        "write-todo-note",
        [
            _turn(ScriptedCall(tool="write_file", arguments={"path": "notes/todo.md", "content": "# Todo\n"})),
            _finish("Done, the note has all three items.", True),
        ],
    )
    outcome: TaskOutcome = await replay_reference(sloppy, config, login_env)
    assert not outcome.passed
    assert outcome.status == "done"
    assert [check.passed for check in outcome.checks] == [True, False, False, False]
    card: Scorecard = score_report(
        EvalReport(
            model=SCRIPTED_MODEL,
            started_at=0.0,
            finished_at=1.0,
            settings=EvalSettings(effort="medium", goal_mode=False, context_tokens=48000, preset="aggressive"),
            outcomes=(outcome,),
        )
    )
    assert card.false_successes == 1


@pytest.mark.parametrize("questions", ["leave", "wait"])
async def test_a_question_nobody_can_answer_is_left_or_declined_and_counted(
    config: AppConfig, login_env: dict[str, str], questions: QuestionMode
) -> None:
    # Left, the run goes on at once; waited for, the harness declines it.
    asking: EvalTask = _with_reference(
        "read-config-port",
        [
            _turn(ScriptedCall(tool="ask_user", arguments={"question": "Which section do you mean?"})),
            _turn(ScriptedCall(tool="read_file", arguments={"path": "data/config.ini"})),
            _finish("8431", True),
        ],
    )
    asked: AppConfig = config.model_copy(update={"agent": config.agent.model_copy(update={"questions": questions})})
    outcome: TaskOutcome = await replay_reference(asking, asked, login_env)
    assert outcome.passed
    assert outcome.metrics.unanswered_questions == 1


async def test_a_goal_check_that_cannot_run_fails_the_attempt_which_is_judged_and_counted(
    config: AppConfig, login_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[agent] goal_mode`` puts every attempt in goal mode, and the review model's key is nowhere, as when an eval
    run did not pass it on: the goal check cannot run. The run ends as failed and says why, and the harness counts
    the check and judges the attempt, so the eval run goes on (item 233)."""
    monkeypatch.delenv(UNSET_KEY, raising=False)
    keyless: AppConfig = config.model_copy(
        update={
            "agent": config.agent.model_copy(update={"goal_mode": True}),
            "models": config.models.model_copy(update={"review": "anthropic:claude-haiku-5-5"}),
            "providers": config.providers.model_copy(
                update={"anthropic": config.providers.anthropic.model_copy(update={"api_key_env": UNSET_KEY})}
            ),
        }
    )
    outcome: TaskOutcome = await replay_reference(select_tasks(["write-todo-note"])[0], keyless, login_env)
    assert outcome.status == "failed", outcome
    assert "the check could not run (missing API key)" in outcome.answer
    assert all(check.passed for check in outcome.checks) and not outcome.passed
    assert outcome.metrics.unproven_steps == 0


async def test_a_run_that_takes_longer_than_its_task_allows_is_cut_and_its_command_is_ended(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    slow: EvalTask = _with_reference(
        "count-errors",
        [_turn(ScriptedCall(tool="shell", arguments={"command": "sleep 60"})), _finish("7", True)],
    ).model_copy(update={"time_limit_s": 1.5})
    outcome: TaskOutcome = await replay_reference(slow, config, login_env)
    assert outcome.cut == "time_limit"
    assert outcome.status == "cancelled"
    assert not outcome.passed
    assert outcome.seconds < 30


async def test_a_run_that_uses_more_tokens_than_its_task_allows_is_cut(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    read: ScriptedCall = ScriptedCall(tool="read_file", arguments={"path": "logs/app.log"})
    hungry: EvalTask = _with_reference(
        "count-errors", [_turn(read), _turn(read), _turn(read), _turn(read), _finish("7", True)]
    ).model_copy(update={"token_budget": 1500})
    outcome: TaskOutcome = await replay_reference(hungry, config, login_env)
    assert outcome.cut == "token_budget"
    assert outcome.status == "cancelled"
    assert not outcome.passed


@pytest.mark.browser
async def test_the_site_tasks_are_served_for_the_attempt_and_count_the_numbers_of_a_long_run(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    """The careers task reads ten pages of a site served for the attempt, meets the one that is not there and reports
    it; the form task fills in and submits the form in the headless browser and the scripted review model's verdict
    ends its goal mode (item 54). Item 53's numbers come out of the attempts."""
    careers: TaskOutcome = await replay_reference(select_tasks(["careers-of-ten-companies"])[0], config, login_env)
    assert careers.passed, careers
    assert careers.metrics.steps >= 12 and careers.metrics.tool_errors == 1
    assert (careers.metrics.not_found, careers.metrics.rejected_arguments) == (1, 0)
    form: TaskOutcome = await replay_reference(select_tasks(["newsletter-form-verified"])[0], config, login_env)
    assert form.passed, form
    assert form.status == "done" and form.metrics.unproven_steps == 0


async def test_the_help_tasks_bring_their_skills_and_server_and_tell_whether_a_run_took_the_easy_way(
    config: AppConfig, login_env: dict[str, str], sentinel_home: Path
) -> None:
    """Each help task's reference takes its easy way: a skill read from the attempt's home, a tool of the MCP server
    started for the attempt. A run that works the orders out itself passes too, but did not take it (item 167)."""
    for task in select_tasks(["memo-in-house-style", "paid-orders-by-skill", "working-days-by-mcp"]):
        outcome: TaskOutcome = await replay_reference(task, config, login_env)
        assert (outcome.passed, outcome.easy_way_taken, outcome.metrics.tool_errors) == (True, True, 0), outcome
    own: str = (
        "awk -F, '$3 == \"paid\" {cents += $2} END {print cents / 100}' "
        "orders/2026-07.csv orders/2026-08.csv orders/2026-09.csv"
    )
    hard: EvalTask = _with_reference(
        "paid-orders-by-skill",
        [_turn(ScriptedCall(tool="shell", arguments={"command": own})), _finish("967.35 EUR", True)],
    )
    outcome = await replay_reference(hard, config, login_env)
    assert (outcome.passed, outcome.easy_way_taken) == (True, False)
    assert _names(sentinel_home) == []
