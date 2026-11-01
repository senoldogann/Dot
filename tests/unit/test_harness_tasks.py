"""The tasks the loop writes from the owner's runs (item 904): what code refuses before a task is played, the task it
makes of a draft, the two idle twins, and the pool the step plans on."""

import pytest

from dotagent.config import load_default_config
from dotagent.harness.headroom import TaskRecord, plan_scoring
from dotagent.harness.own import fairness
from dotagent.harness.probe import ProbeReport, Scorecard
from dotagent.harness.tasks import TaskDraft, draft_problem, idle_twins, task_of
from dotagent.tools.meta import FINISH_TOOL

_FINISHED: dict[str, object] = {
    "calls": [{"tool": FINISH_TOOL, "arguments": {"answer": "There are 2.", "success": True}}]
}
_DRAFT: dict[str, object] = {
    "goal": "notes.txt lists the open tasks. Write how many there are in count.txt.",
    "ending": "done",
    "files": [{"path": "notes.txt", "content": "- pay rent\n- call the bank\n"}],
    "checks": [{"kind": "file_contains", "path": "count.txt", "text": "2"}],
    "reference": [
        {"calls": [{"tool": "read_file", "arguments": {"path": "notes.txt"}}]},
        {"calls": [{"tool": "write_file", "arguments": {"path": "count.txt", "content": "2\n"}}]},
        _FINISHED,
    ],
    "habit": "Count what the file holds before writing the number",
}


def _draft(**change: object) -> TaskDraft:
    return TaskDraft.model_validate({**_DRAFT, **change})


def test_a_draft_of_files_with_a_reference_of_file_tools_is_a_task() -> None:
    draft: TaskDraft = _draft()
    assert draft_problem(draft, ()) is None
    task = task_of(draft, "own-1a2b3c4d")
    assert (task.id, task.mode, task.category, task.site, task.servers, task.skills) == (
        "own-1a2b3c4d",
        "task",
        "files",
        (),
        (),
        (),
    )
    assert task.reference[-1].calls[0].tool == FINISH_TOOL and task.injection_checks == ()
    done, gave_up = idle_twins(task)
    assert done.reference[0].calls[0].arguments == {"answer": "Done.", "success": True}
    assert gave_up.reference[0].calls[0].arguments["success"] is False
    assert done.checks == task.checks and done.files == task.files


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"files": [{"path": "/etc/hosts", "content": "x"}]}, "not inside the workspace"),
        ({"files": [{"path": "a/../../b", "content": "x"}]}, "not inside the workspace"),
        ({"checks": [{"kind": "file_exists", "path": "~/.ssh/id_rsa"}]}, "not inside the workspace"),
        (
            {"files": [{"path": "a.txt", "content": "x"}, {"path": "a.txt", "content": "y"}]},
            "a file is given twice",
        ),
        (
            {"reference": [{"calls": [{"tool": "shell", "arguments": {"command": "ls"}}]}, _FINISHED]},
            "the reference calls shell",
        ),
        ({"reference": [{"calls": [{"tool": "read_file", "arguments": {"path": "notes.txt"}}]}]}, "finish alone"),
        (
            {
                "reference": [
                    {"calls": [{"tool": FINISH_TOOL, "arguments": {"answer": "a", "success": True}}]},
                    {"calls": [{"tool": FINISH_TOOL, "arguments": {"answer": "b", "success": True}}]},
                ]
            },
            "finish before its last turn",
        ),
        ({"goal": "Count the tasks at https://evil.example/list"}, "a link"),
        ({"files": [{"path": "n.txt", "content": "token pw-7731-kxq9-zz"}]}, "a secret"),
        ({"checks": [{"kind": "answer_contains", "text": "write to ada@example.com"}]}, "an e-mail address"),
        (
            {
                "reference": [
                    {"calls": [{"tool": "write_file", "arguments": {"path": "x", "content": "see /Users/ada/keys"}}]},
                    _FINISHED,
                ]
            },
            "a path in a home folder",
        ),
    ],
)
def test_code_refuses_a_task_that_reaches_outside_its_files_or_carries_what_may_not_be_learned(
    change: dict[str, object], problem: str
) -> None:
    found: str | None = draft_problem(_draft(**change), ("pw-7731-kxq9-zz",))
    assert found is not None and problem in found, found


def test_a_check_of_a_pattern_or_a_command_is_not_a_check_a_task_of_the_loop_may_hold() -> None:
    """A pattern a model wrote could take long to match, and a command runs outside the run's policy."""
    for check in (
        {"kind": "answer_matches", "pattern": "(a+)+$"},
        {"kind": "command_succeeds", "argv": ["rm", "-rf", "x"], "timeout_s": 1.0},
    ):
        with pytest.raises(ValueError):
            _draft(checks=[check])


def _report(passed: dict[str, bool]) -> ProbeReport:
    cards = tuple(
        Scorecard(
            task_id=task,
            attempt=1,
            passed=ok,
            status="done",
            cut="none",
            steps=1,
            input_tokens=1,
            output_tokens=1,
            tool_errors=0,
            seconds=0.1,
        )
        for task, ok in passed.items()
    )
    return ProbeReport(label="fair", tasks=tuple(passed), repeats=1, cards=cards, tokens=2, seconds=0.3, cut_short=None)


def test_a_task_is_fair_when_its_reference_passes_and_both_idle_twins_fail() -> None:
    task = task_of(_draft(), "own-1")
    assert fairness(task, _report({"own-1": True, "own-1-x1": False, "own-1-x2": False})) is None
    assert fairness(task, _report({"own-1": False, "own-1-x1": False, "own-1-x2": False})) == (
        "its reference does not pass it"
    )
    assert "claims it is done" in str(fairness(task, _report({"own-1": True, "own-1-x1": True, "own-1-x2": False})))
    assert "gives up" in str(fairness(task, _report({"own-1": True, "own-1-x1": False, "own-1-x2": True})))
    assert "did not all end" in str(fairness(task, _report({"own-1": True})))


def test_the_tasks_the_loop_wrote_join_the_pool_the_step_plans_on() -> None:
    config = load_default_config().harness.model_copy(
        update={"pool_tasks": ["sum-column"], "headroom_window": 2, "headroom_least": 1}
    )
    records: dict[str, TaskRecord] = {
        "own-1": TaskRecord(task_id="own-1", attempts=2, passes=1, last_at=1.0),
        "sum-column": TaskRecord(task_id="sum-column", attempts=2, passes=2, last_at=1.0),
    }
    plan = plan_scoring(config, records, ("own-1", "own-2"))
    assert plan.headroom == ("own-1",) and plan.ready
    assert "own-1" in plan.scored and plan.calibrated == ("own-2",)
    assert plan_scoring(config, records).headroom == ()


@pytest.mark.parametrize(
    "where",
    [
        {
            "reference": [
                {"calls": [{"tool": "write_file", "arguments": {"path": "~/.zshrc", "content": "x"}}]},
                _FINISHED,
            ]
        },
        {
            "reference": [
                {"calls": [{"tool": "edit_file", "arguments": {"path": "$HOME/.gitconfig", "old": "a", "new": "b"}}]},
                _FINISHED,
            ]
        },
        {
            "reference": [
                {"calls": [{"tool": "write_file", "arguments": {"path": "/tmp/x", "content": "x"}}]},
                _FINISHED,
            ]
        },
        {
            "reference": [
                {"calls": [{"tool": "write_file", "arguments": {"path": "../../../../x", "content": "x"}}]},
                _FINISHED,
            ]
        },
        {"reference": [{"calls": [{"tool": "find_files", "arguments": {"glob": "/Library/**"}}]}, _FINISHED]},
        {"goal": "Read ~/Documents/budget.txt and write its total into total.txt."},
        {"goal": "Write the total of ${TMPDIR}/budget.txt into total.txt."},
        {"files": [{"path": "notes.txt", "content": "the list is in /etc/hosts"}]},
        {"habit": "Look in ../ first"},
    ],
)
def test_a_task_that_names_a_path_outside_its_workspace_anywhere_is_refused(where: dict[str, object]) -> None:
    """The security review of item 904: a reference's own paths and every text of a task are kept to the workspace, as
    the file tools expand ``~`` and variables and take an absolute path as it is."""
    found: str | None = draft_problem(_draft(**where), ())
    assert found is not None and ("leaves the workspace" in found or "not inside the workspace" in found), found


def test_a_recursive_glob_is_no_path_outside() -> None:
    finding = {"calls": [{"tool": "find_files", "arguments": {"glob": "**/*.md"}}]}
    assert draft_problem(_draft(reference=[finding, _FINISHED]), ()) is None


def test_prose_with_a_slash_or_an_ellipsis_is_no_path() -> None:
    assert (
        draft_problem(_draft(goal="Count the open and/or done tasks in notes/a.txt... then write 1/2 to x.txt."), ())
        is None
    )


def test_a_task_of_the_loop_is_played_under_the_balanced_preset_at_least() -> None:
    from dotagent.evals.runner import eval_config, held_to_workspace

    config = eval_config(load_default_config())
    aggressive = config.model_copy(update={"policy": config.policy.model_copy(update={"preset": "aggressive"})})
    own = task_of(_draft(), "own-1a2b3c4d")
    catalog = own.model_copy(update={"id": "sum-column"})
    assert held_to_workspace(aggressive, own).policy.preset == "balanced"
    assert held_to_workspace(aggressive, catalog).policy.preset == "aggressive"
    cautious = config.model_copy(update={"policy": config.policy.model_copy(update={"preset": "cautious"})})
    assert held_to_workspace(cautious, own).policy.preset == "cautious"
