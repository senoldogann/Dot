"""Where a step scores a text (item 719): on 2026-10-24 the base passed 20 attempts of 20 on the scoring set, so no
candidate could be better and every night kept the base. A step scores on the guards and on the tasks the version in
use still fails, checks a few it passes, and tries the pool it knows least until enough tasks have headroom."""

from dotagent.config import HarnessConfig, load_default_config
from dotagent.harness.headroom import Attempt, ScoringPlan, plan_scoring, pool_of, records_of

DEFAULT: HarnessConfig = load_default_config().harness


def _config(**changes: object) -> HarnessConfig:
    return DEFAULT.model_copy(
        update={
            "scoring_tasks": ["guard"],
            "final_tasks": ["final"],
            "pool_tasks": ["a", "b", "c", "d", "e", "final", "guard"],
            "headroom_window": 3,
            "headroom_tasks": 2,
            "regression_tasks": 1,
            "headroom_least": 2,
            "calibrate_tasks": 2,
            **changes,
        }
    )


def _tried(task: str, passes: list[bool], start: float = 0.0) -> list[Attempt]:
    return [Attempt(task_id=task, passed=passed, at=start + index) for index, passed in enumerate(passes)]


def test_a_record_holds_the_newest_attempts_of_a_task_within_the_window() -> None:
    # Failed long ago, passed the newest three times: saturated now, whatever came before.
    records = records_of([*_tried("a", [False, True, True, True]), *_tried("b", [True, False])], 3)
    assert (records["a"].attempts, records["a"].passes, records["a"].last_at) == (3, 3, 3.0)
    assert (records["b"].attempts, records["b"].passes, records["b"].rate) == (2, 1, 0.5)


def test_the_pool_leaves_out_the_final_set_and_the_guards() -> None:
    assert pool_of(_config()) == ("a", "b", "c", "d", "e")


def test_with_too_little_headroom_known_the_base_tries_the_tasks_it_knows_least() -> None:
    config: HarnessConfig = _config()
    nothing: ScoringPlan = plan_scoring(config, {})
    assert not nothing.ready and nothing.untried == 5
    assert nothing.calibrated == ("a", "b")
    # One task with headroom is not enough; the untried come first, then the fewest attempts, then the oldest.
    records = records_of(
        [*_tried("a", [True, False]), *_tried("b", [True, True, True], 10.0), *_tried("c", [True], 5.0)], 3
    )
    some: ScoringPlan = plan_scoring(config, records)
    assert not some.ready and some.headroom == ("a",)
    assert some.calibrated == ("d", "e")
    assert some.untried == 2


def test_a_ready_step_scores_the_guards_the_most_headroom_and_a_saturated_task() -> None:
    records = records_of(
        [
            *_tried("a", [True, False, True]),
            *_tried("b", [False, False, True]),
            *_tried("c", [False, True, True]),
            *_tried("d", [True, True, True], 20.0),
            *_tried("e", [True, True, True], 30.0),
        ],
        3,
    )
    plan: ScoringPlan = plan_scoring(_config(), records)
    assert plan.ready
    # The lowest pass rate first (b, then a and c at 2 of 3, the one tried longest ago first), two at most; then the
    # saturated task tried longest ago.
    assert plan.headroom == ("b", "a", "c")
    assert plan.scored == ("guard", "b", "a", "d")
    assert plan.untried == 0


def test_the_defaults_score_no_more_tasks_than_the_ten_of_the_scoring_set_before() -> None:
    assert len(DEFAULT.scoring_tasks) + DEFAULT.headroom_tasks + DEFAULT.regression_tasks == 10
    assert DEFAULT.headroom_least <= DEFAULT.headroom_tasks
