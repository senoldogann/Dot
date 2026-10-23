"""The confirmation before a step adopts on its own (item 589, review round 1): luck gives a text that changes nothing a
better score in a step in six to twenty-seven, and the confirmation (the base and the survivor played again on new
attempts, twice as many, which the survivor must beat by twice the margin) gives it in one in fifty.

The attempts are drawn from fixed rates (a task that passes half the time, one that always passes) and the decision is
the one the step makes: :func:`dotagent.harness.contrast.contrast` and :func:`choose` for the survivor,
:func:`confirm_contrast` for its confirmation. The rates are those of the review's simulation (``probes/luck.py``).
"""

import json
import random

from dotagent.cli_harness import render_step
from dotagent.harness.contrast import CONFIRM_FACTOR, DEFAULT_MARGIN, choose, confirm_contrast, contrast
from dotagent.harness.probe import BASE, ProbeReport, Scorecard
from dotagent.store.models import HarnessStep

REPEATS: int = 2


def _card(task: str, attempt: int, passed: bool) -> Scorecard:
    """An attempt, built unchecked: thousands of them are drawn."""
    return Scorecard.model_construct(
        task_id=task,
        attempt=attempt,
        passed=passed,
        status="done",
        cut="none",
        steps=3,
        input_tokens=100,
        output_tokens=10,
        tool_errors=0,
        seconds=1.0,
        approvals_asked=0,
        checks_passed=0,
        checks_total=0,
    )


def _report(label: str, cards: tuple[Scorecard, ...], tasks: tuple[str, ...], repeats: int) -> ProbeReport:
    return ProbeReport.model_construct(
        label=label, tasks=tasks, repeats=repeats, cards=cards, tokens=0, seconds=0.0, cut_short=None
    )


def _drawn(label: str, rates: list[float], repeats: int, rng: random.Random) -> ProbeReport:
    """The attempts of a probe, each passed with the rate of its task."""
    tasks: tuple[str, ...] = tuple(f"t{number}" for number in range(len(rates)))
    cards: tuple[Scorecard, ...] = tuple(
        _card(task, attempt, rng.random() < rate)
        for task, rate in zip(tasks, rates, strict=True)
        for attempt in range(1, repeats + 1)
    )
    return _report(label, cards, tasks, repeats)


def _fixed(label: str, passes: list[int], repeats: int) -> ProbeReport:
    """A probe of tasks that passed ``passes`` times each of ``repeats``."""
    tasks: tuple[str, ...] = tuple(f"t{number}" for number in range(len(passes)))
    cards: tuple[Scorecard, ...] = tuple(
        _card(task, attempt, attempt <= won)
        for task, won in zip(tasks, passes, strict=True)
        for attempt in range(1, repeats + 1)
    )
    return _report(label, cards, tasks, repeats)


def _steps(base_rates: list[float], candidates: list[list[float]], trials: int, seed: int) -> tuple[float, float]:
    """In how many steps of ``trials`` a survivor is chosen, and in how many it is confirmed as well: what ``auto``
    would have adopted before, and what it adopts now."""
    rng: random.Random = random.Random(seed)
    chosen: int = 0
    confirmed: int = 0
    again: int = REPEATS * CONFIRM_FACTOR
    for _ in range(trials):
        base: ProbeReport = _drawn(BASE, base_rates, REPEATS, rng)
        found = [
            contrast(base, _drawn(f"c{number}", rates, REPEATS, rng), DEFAULT_MARGIN)
            for number, rates in enumerate(candidates)
        ]
        winner = choose(found)
        if winner is None:
            continue
        chosen += 1
        survivor: list[float] = candidates[int(winner.label[1:])]
        verdict = confirm_contrast(
            _drawn(BASE, base_rates, again, rng), _drawn("again", survivor, again, rng), DEFAULT_MARGIN
        )
        confirmed += verdict.verdict == "better"
    return chosen / trials, confirmed / trials


def _flaky(count: int) -> list[float]:
    """Ten tasks, ``count`` of them passed half the time and the rest always."""
    return [1.0] * (10 - count) + [0.5] * count


def test_a_text_that_changes_nothing_is_adopted_in_under_three_steps_of_a_hundred() -> None:
    """The review's cases: tasks that pass half the time and tasks that pass nine times in ten, one candidate or the two
    scopes, none of them better than the base. Chosen in 6 to 27 steps of a hundred before; adopted in 2 at most."""
    for label, rates, candidates, at_least in (
        ("2 flaky tasks, one candidate", _flaky(2), 1, 0.08),
        ("3 flaky tasks, two scopes", _flaky(3), 2, 0.15),
        ("4 flaky tasks, two scopes", _flaky(4), 2, 0.15),
        ("all tasks at 0.9, two scopes", [0.9] * 10, 2, 0.12),
    ):
        chosen, adopted = _steps(rates, [rates] * candidates, 2500, seed=11)
        assert chosen > at_least, (label, chosen)
        assert adopted < 0.03, (label, chosen, adopted)


def test_a_text_that_is_better_on_a_few_tasks_is_still_confirmed_mostly() -> None:
    """The confirmation is no wall: a text that passes where the base passes half the time, or never, is adopted."""
    better: list[float] = [1.0] * 10
    for label, base, at_least in (
        ("4 tasks 0.5 to 1", _flaky(4), 0.85),
        ("3 tasks 0 to 1", [1.0] * 7 + [0.0] * 3, 0.99),
    ):
        _chosen, adopted = _steps(base, [better], 1500, seed=5)
        assert adopted > at_least, (label, adopted)


def test_a_step_printed_by_the_command_says_what_its_confirmation_showed() -> None:
    outcome: str = json.dumps(
        {
            "decision": "hrn_1 did better on 3 and worse on none; left to you: it was not better again on new attempts",
            "probed": "hrn_1",
            "confirmation": "In all same: better on 0 of 10 tasks, worse on 0, tokens +0. write-todo-note: same (4/4)",
        }
    )
    step: HarnessStep = HarnessStep(
        id="hst_1",
        started_at=1.0,
        ended_at=2.0,
        from_run="run_1",
        base_version=None,
        state="done",
        outcome=outcome,
        error=None,
    )
    lines: list[str] = render_step(step)
    assert lines[0] == "Step hst_1: done"
    assert lines.index("  probed: hrn_1") + 1 == next(
        i for i, line in enumerate(lines) if line.startswith("  confirmation: In all same")
    )


def test_the_confirmation_asks_twice_the_attempts_and_twice_the_margin() -> None:
    assert CONFIRM_FACTOR == 2
    # Two tasks: the base never passes the first. Two attempts each, the survivor passing both: two passes more.
    first: ProbeReport = _fixed(BASE, [0, 2], REPEATS)
    assert contrast(first, _fixed("c", [2, 2], REPEATS), DEFAULT_MARGIN).verdict == "better"
    # The confirmation plays four attempts each and asks four passes more: all four, not three.
    again: int = REPEATS * CONFIRM_FACTOR
    base: ProbeReport = _fixed(BASE, [0, 4], again)
    assert confirm_contrast(base, _fixed("again", [4, 4], again), DEFAULT_MARGIN).verdict == "better"
    three = confirm_contrast(base, _fixed("again", [3, 4], again), DEFAULT_MARGIN)
    assert three.verdict == "unclear" and three.gain == 3 and "4 are the least" in three.why
    assert contrast(base, _fixed("again", [3, 4], again), DEFAULT_MARGIN).verdict == "better"
    # A task it does worse on is worse, as in the step.
    assert confirm_contrast(base, _fixed("again", [4, 3], again), DEFAULT_MARGIN).verdict == "worse"
