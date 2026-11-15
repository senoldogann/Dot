"""The state set (item 1147): a browser task is passed by what its site took, and by nothing a run says.

A scripted model plays the real loop, the real browser tools and a real headless Chrome against the task's own site.
Each reference passes; and each way of seeming done without having done it fails on the site's record, though its
answer is word for word the one a run that did the work gives: saying so, opening a page that looks like the answer,
pressing a button that took the press and did nothing, and sending the form twice.
"""

import pytest
import pytest_asyncio

from dotagent.config import AppConfig, load_default_config
from dotagent.evals.catalog import select_tasks
from dotagent.evals.results import TaskOutcome
from dotagent.evals.runner import do_nothing_passes, eval_config, replay_reference
from dotagent.evals.state_tasks import STATE_TASKS
from dotagent.evals.types import EvalTask, ScriptedCall, ScriptedTurn
from dotagent.tools.shell import child_env, read_login_env

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

_APPLIED: str = "Application 1 received for ada@example.org (3-5 years, Finland: yes)."
_SAVED: str = "Settings saved: display name Ada L. (change 1)."
_APPLY_STEPS: list[dict[str, str]] = [
    {"action": "fill", "ref": "e6", "text": "ada@example.org"},
    {"action": "select", "ref": "e8", "text": "3-5"},
    {"action": "check", "ref": "e10"},
]


@pytest.fixture(scope="module")
def config() -> AppConfig:
    return eval_config(load_default_config())


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env(config: AppConfig) -> dict[str, str]:
    return await read_login_env(config.shell.login_env_timeout_s, ())


def _turn(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall.model_validate({"tool": tool, "arguments": arguments}),))


def _finish(answer: str) -> ScriptedTurn:
    return _turn("finish", answer=answer, success=True)


def _played(task_id: str, *turns: ScriptedTurn) -> EvalTask:
    """The task ``task_id`` with ``turns`` as what the scripted model does in place of its reference."""
    return select_tasks([task_id])[0].model_copy(update={"reference": turns})


def _failed(outcome: TaskOutcome) -> list[str]:
    return [check.check.split(" at ")[0] for check in outcome.checks if not check.passed]


async def test_each_reference_does_the_work_and_passes(config: AppConfig, login_env: dict[str, str]) -> None:
    for task in select_tasks(STATE_TASKS):
        outcome: TaskOutcome = await replay_reference(task, config, login_env)
        assert outcome.passed, outcome
        assert outcome.metrics.tool_errors == 0


async def test_doing_nothing_passes_neither_task(config: AppConfig, login_env: dict[str, str]) -> None:
    env: dict[str, str] = child_env(login_env, config.shell.pass_env)
    for task in select_tasks(STATE_TASKS):
        assert not await do_nothing_passes(task, env), task.id


async def test_saying_it_is_done_without_sending_the_form_fails(config: AppConfig, login_env: dict[str, str]) -> None:
    outcome: TaskOutcome = await replay_reference(_played("state-apply-once", _finish(_APPLIED)), config, login_env)
    # The answer is the right one, and the run ended done: only the site's record says nothing was sent.
    assert (outcome.status, outcome.passed) == ("done", False)
    assert _failed(outcome) == ["the site took 1 request"]


async def test_opening_a_page_that_looks_like_the_answer_is_no_application(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    looked: EvalTask = _played(
        "state-apply-once", _turn("browser_open", url="{site}/jobs/7/example"), _finish(_APPLIED)
    )
    outcome: TaskOutcome = await replay_reference(looked, config, login_env)
    assert (outcome.status, outcome.passed) == ("done", False)
    assert _failed(outcome) == ["the site took 1 request"]


async def test_an_application_sent_twice_is_not_one_sent_once(config: AppConfig, login_env: dict[str, str]) -> None:
    """A run that does not wait for the answer and sends the form again: the site took two, and the task asks for
    one."""
    send: dict[str, str] = {"action": "click", "ref": "e11"}
    # The form opened a second time is the third document of its tab, which its refs say.
    again: list[dict[str, str]] = [{**step, "ref": f"f2{step['ref']}"} for step in (*_APPLY_STEPS, send)]
    twice: EvalTask = _played(
        "state-apply-once",
        _turn("browser_open", url="{site}/jobs/7"),
        _turn("browser_act", steps=[*_APPLY_STEPS, send]),
        _turn("browser_open", url="{site}/jobs/7"),
        _turn("browser_act", steps=again),
        _finish(_APPLIED),
    )
    outcome: TaskOutcome = await replay_reference(twice, config, login_env)
    assert (outcome.status, outcome.passed) == ("done", False)
    assert _failed(outcome) == ["the site took 1 request"]
    assert "the site took 2 there" in outcome.checks[0].detail


async def test_a_press_the_page_took_and_did_nothing_with_is_no_save(
    config: AppConfig, login_env: dict[str, str]
) -> None:
    """The button took the press and sent nothing. A run that reports done after it fails, with the answer a saved
    form gives."""
    once: EvalTask = _played(
        "state-save-second-press",
        _turn("browser_open", url="{site}/settings"),
        _turn(
            "browser_act",
            steps=[{"action": "fill", "ref": "e6", "text": "Ada L."}, {"action": "click", "ref": "e7"}],
        ),
        _finish(_SAVED),
    )
    outcome: TaskOutcome = await replay_reference(once, config, login_env)
    assert (outcome.status, outcome.passed) == ("done", False)
    assert _failed(outcome) == ["the site took 1 request"]
