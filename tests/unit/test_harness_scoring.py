"""What the harness is scored on and what its settings may say (item 589): the scoring set holds a task of honesty and
one with an instruction hidden in a file beside the tasks of files and the shell, no task is in both the scoring and
the final set, a scope is proposed once, the margin and the days the records are kept are bounds the configuration
checks, and the API takes no component past a bound."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dotagent.config import HarnessConfig, load_config, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.evals.catalog import select_tasks
from dotagent.server.views import HarnessRequest

DEFAULT: HarnessConfig = load_default_config().harness


def _changed(**more: object) -> dict[str, object]:
    return {**DEFAULT.model_dump(), **more}


def test_the_scoring_set_holds_a_task_of_honesty_and_one_that_hides_an_instruction() -> None:
    """Item 589 (M5): a set of tasks that all end done, none with a page or a file that asks for something else, cannot
    see a candidate that overclaims or follows an injection; the set has one of each, as the final set has. Since item
    719 they are the guards, played every step, and the tasks of files and the shell they stood beside are in the pool,
    where a step scores the ones the version in use still fails."""
    scoring = select_tasks(list(DEFAULT.scoring_tasks))
    honest = [task for task in scoring if task.category == "honesty"]
    assert [task.id for task in honest] == ["impossible-missing-file"] and honest[0].ending == "failed"
    lured = [task for task in scoring if task.injection_checks]
    assert [task.id for task in lured] == ["injection-in-invoice"] and lured[0].category == "safety"
    assert len(scoring) == 2
    pool = select_tasks(list(DEFAULT.pool_tasks))
    assert len(pool) == len(DEFAULT.pool_tasks)
    assert {"files", "shell"} <= {task.category for task in pool}
    # Nothing of the pool reads the web, and nothing of it is of the final set or a guard.
    assert not [task.id for task in pool if task.category == "research"]
    assert set(DEFAULT.pool_tasks).isdisjoint({*DEFAULT.final_tasks, *DEFAULT.scoring_tasks})


def test_no_task_is_in_the_scoring_set_and_the_final_set() -> None:
    final = select_tasks(list(DEFAULT.final_tasks))
    assert {task.category for task in final} & {"honesty", "safety", "recovery"}
    assert set(DEFAULT.scoring_tasks).isdisjoint(DEFAULT.final_tasks)
    assert len(set(DEFAULT.scoring_tasks)) == len(DEFAULT.scoring_tasks)


def test_a_task_in_both_sets_is_refused_by_the_configuration_and_says_which() -> None:
    with pytest.raises(ValidationError, match="share fix-add-bug"):
        HarnessConfig.model_validate(_changed(scoring_tasks=[*DEFAULT.scoring_tasks, "fix-add-bug"]))
    assert HarnessConfig.model_validate(_changed(final_tasks=["report-failing-tests"])).final_tasks == [
        "report-failing-tests"
    ]


def _loaded(tmp_path: Path, toml: str) -> HarnessConfig:
    file: Path = tmp_path / "config.toml"
    file.write_text(toml, encoding="utf-8")
    return load_config(file).harness


def test_a_task_the_owner_names_in_one_set_leaves_the_default_of_the_other(tmp_path: Path) -> None:
    """Item 589, review round 1 (N13): ``injection-in-invoice`` and ``impossible-missing-file`` are in the default
    scoring set, so a config that names one of them in ``final_tasks`` alone could not be loaded, and Dot did not
    start. What the owner writes wins over a default that grew a task since: it leaves the other set's default."""
    final: HarnessConfig = _loaded(tmp_path, '[harness]\nfinal_tasks = ["injection-in-invoice", "fix-add-bug"]\n')
    assert final.final_tasks == ["injection-in-invoice", "fix-add-bug"]
    assert final.scoring_tasks == [task for task in DEFAULT.scoring_tasks if task != "injection-in-invoice"]
    scoring: HarnessConfig = _loaded(tmp_path, '[harness]\nscoring_tasks = ["fix-add-bug", "sum-column"]\n')
    assert scoring.scoring_tasks == ["fix-add-bug", "sum-column"]
    assert scoring.final_tasks == [task for task in DEFAULT.final_tasks if task != "fix-add-bug"]
    # A config that names neither, or both without a task in common, is as it is.
    assert _loaded(tmp_path, "") == DEFAULT
    both: HarnessConfig = _loaded(
        tmp_path, '[harness]\nscoring_tasks = ["sum-column"]\nfinal_tasks = ["fix-add-bug"]\n'
    )
    assert (both.scoring_tasks, both.final_tasks) == (["sum-column"], ["fix-add-bug"])
    # Two lists the owner wrote that share a task are theirs to settle, and the error says how.
    with pytest.raises(ConfigError, match=r"share sum-column.*Name both lists in \[harness\]"):
        _loaded(tmp_path, '[harness]\nscoring_tasks = ["sum-column"]\nfinal_tasks = ["sum-column"]\n')
    # A final set the default scoring set leaves with nothing is refused, not loaded empty.
    with pytest.raises(ConfigError, match="final_tasks"):
        _loaded(tmp_path, f"[harness]\nscoring_tasks = {json.dumps(DEFAULT.final_tasks)}\n")


def test_a_set_that_takes_every_task_of_the_others_default_says_so_and_which_list_to_write(tmp_path: Path) -> None:
    """Item 589, pre-push review round 3: a ``final_tasks`` that names every default scoring task left the default
    ``scoring_tasks`` empty, and the file failed on pydantic's "List should have at least 1 item", which does not say
    that the owner's own list emptied the other. The error says it, and which list to write too."""
    file: Path = tmp_path / "config.toml"
    for named, default, taken, other in (
        ("final_tasks", "scoring_tasks", DEFAULT.scoring_tasks, ["fix-add-bug"]),
        ("scoring_tasks", "final_tasks", DEFAULT.final_tasks, ["sum-column"]),
    ):
        file.write_text(f"[harness]\n{named} = {json.dumps(taken)}\n", encoding="utf-8")
        with pytest.raises(ConfigError) as refused:
            load_config(file)
        assert refused.value.message == (
            f"configuration failed validation: harness.{named} names every task of the default {default}, which "
            f"leaves it none: name {default} too"
        )
        assert refused.value.context["path"] == str(file)
        assert refused.value.context["fix"] == f"name {default} as well as {named} under [harness] in {file}"
        # Naming the other list too is what settles it, and the owner's two lists then load as they wrote them.
        file.write_text(
            f"[harness]\n{named} = {json.dumps(taken)}\n{default} = {json.dumps(other)}\n", encoding="utf-8"
        )
        assert getattr(load_config(file).harness, default) == other
    # One task left over is enough: the default keeps it and the file loads.
    file.write_text(f"[harness]\nfinal_tasks = {json.dumps(DEFAULT.scoring_tasks[:-1])}\n", encoding="utf-8")
    assert load_config(file).harness.scoring_tasks == [DEFAULT.scoring_tasks[-1]]


def test_a_scope_is_proposed_once() -> None:
    with pytest.raises(ValidationError, match="names a scope once"):
        HarnessConfig.model_validate(_changed(step_candidates=["invariant", "invariant"]))
    assert HarnessConfig.model_validate(_changed(step_candidates=["domain"])).step_candidates == ["domain"]


def test_the_margin_and_the_days_the_records_are_kept_are_bounded() -> None:
    assert (DEFAULT.step_margin, DEFAULT.keep_days) == (2, 180)
    with pytest.raises(ValidationError):
        HarnessConfig.model_validate(_changed(step_margin=0))
    with pytest.raises(ValidationError):
        HarnessConfig.model_validate(_changed(keep_days=-1))
    # No days means the records are kept for ever.
    assert HarnessConfig.model_validate(_changed(keep_days=0)).keep_days == 0


def test_the_api_takes_no_component_of_more_than_twenty_thousand_characters() -> None:
    """Item 589 (L8): the bound keeps a request that is no text from being read at all; the limits of ``[harness]`` say
    the rest of what a component may be."""
    request: dict[str, object] = {"habits": None, "tool_guidance": None, "summary": "s", "adopt": False}
    assert HarnessRequest.model_validate({**request, "habits": "x" * 20_000}).habits == "x" * 20_000
    for name in ("habits", "tool_guidance"):
        with pytest.raises(ValidationError, match="at most 20000 characters"):
            HarnessRequest.model_validate({**request, name: "x" * 20_001})
