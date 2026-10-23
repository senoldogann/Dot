"""What a probe of a candidate shows against the probe of the base (item 391, docs/harness.md): per task, whether the
candidate passed more, as often or less often; in all, whether it did better (more on a task, less on none, and at least
a margin of passes more in all, item 589), the same, worse (less on a task), or whether that cannot be told (a probe cut
short before a task was played, or a gain under the margin, which one lucky attempt can make); the contrast says why.
A contrast says how a candidate behaved, not that it is right: runs vary, and the models share their blind spots."""

from collections.abc import Sequence
from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.harness.probe import ProbeReport, Scorecard

type Verdict = Literal["better", "same", "worse", "unclear"]
# How many passes more than the base a candidate needs over the whole scoring set to be better (``[harness]
# step_margin``, whose default this is): a task passed one time in two passes the other time by luck, so one pass more
# is no evidence, two are the least that is.
DEFAULT_MARGIN: int = 2


# The confirmation that comes before a step adopts a survivor on its own (item 589, review round 1): the base and the
# survivor are played again on new attempts, this many times as many, and the survivor has to be better again by this
# many times the margin, the same gain for each attempt on a sample this much larger. Luck gives a text that changes
# nothing a better score in 6 to 17 steps of a hundred (two attempts, two passes more), and the better of two scopes in
# 19 to 27; it gives it twice in a row in under 2 (simulated in tests/unit/test_harness_confirmation.py).
CONFIRM_FACTOR: int = 2


class TaskScore(FrozenModel):
    """How one text fared on one task: the attempts played, how many passed, and the mean steps, tokens and seconds."""

    task_id: str
    attempts: int
    passes: int
    steps: float
    tokens: float
    seconds: float


class TaskContrast(FrozenModel):
    task_id: str
    base: TaskScore | None
    candidate: TaskScore | None
    verdict: Verdict


class Contrast(FrozenModel):
    """A candidate against the base: a verdict per task and in all, how many tasks it did better and worse on, how many
    passes more it had in all (``gain``), and how its tokens compare (the candidate's less the base's, over the tasks
    both played). ``why`` says, in counts and never by task, why the verdict is ``unclear`` (empty for any other)."""

    label: str
    tasks: tuple[TaskContrast, ...]
    verdict: Verdict
    better: int
    worse: int
    tokens_delta: int
    gain: int = 0
    why: str = ""


def scores_of(report: ProbeReport) -> dict[str, TaskScore]:
    """Each task's score in ``report``, from the cards it holds."""
    by_task: dict[str, list[Scorecard]] = {}
    for card in report.cards:
        by_task.setdefault(card.task_id, []).append(card)
    return {
        task_id: TaskScore(
            task_id=task_id,
            attempts=len(cards),
            passes=sum(1 for card in cards if card.passed),
            steps=sum(card.steps for card in cards) / len(cards),
            tokens=sum(card.input_tokens + card.output_tokens for card in cards) / len(cards),
            seconds=sum(card.seconds for card in cards) / len(cards),
        )
        for task_id, cards in by_task.items()
    }


def _task_verdict(base: TaskScore | None, candidate: TaskScore | None, repeats: int) -> Verdict:
    if base is None or candidate is None or base.attempts < repeats or candidate.attempts < repeats:
        return "unclear"
    base_rate: float = base.passes / base.attempts
    candidate_rate: float = candidate.passes / candidate.attempts
    if candidate_rate > base_rate:
        return "better"
    if candidate_rate < base_rate:
        return "worse"
    return "same"


def _cut_short(base: ProbeReport, candidate: ProbeReport, unplayed: int, tasks: int) -> str:
    """Why a verdict cannot be told: ``unplayed`` of ``tasks`` were not played as many times as asked under both texts,
    and the probe that stopped short, with the budget it stopped at."""
    stopped: list[str] = [
        f"the {name}'s probe stopped at its {report.cut_short} budget"
        for name, report in (("base", base), ("candidate", candidate))
        if report.cut_short is not None
    ]
    said: str = f"{unplayed} of {tasks} tasks were not played {base.repeats} times under both texts"
    return f"{said} ({'; '.join(stopped)})" if stopped else said


def contrast(base: ProbeReport, candidate: ProbeReport, margin: int = DEFAULT_MARGIN) -> Contrast:
    """The candidate's report against the base's, task by task over the base's tasks. It is better when it passed more
    on a task and less on none and ``margin`` passes more in all: a gain under the margin is ``unclear`` (and says so),
    as is a verdict a probe cut short cannot give."""
    base_scores: dict[str, TaskScore] = scores_of(base)
    candidate_scores: dict[str, TaskScore] = scores_of(candidate)
    tasks: list[TaskContrast] = [
        TaskContrast(
            task_id=task_id,
            base=base_scores.get(task_id),
            candidate=candidate_scores.get(task_id),
            verdict=_task_verdict(base_scores.get(task_id), candidate_scores.get(task_id), base.repeats),
        )
        for task_id in base.tasks
    ]
    verdicts: list[Verdict] = [task.verdict for task in tasks]
    better: int = verdicts.count("better")
    worse: int = verdicts.count("worse")
    # The passes the candidate had more than the base, on the tasks it did better on.
    gain: int = sum(
        task.candidate.passes - task.base.passes
        for task in tasks
        if task.verdict == "better" and task.base is not None and task.candidate is not None
    )
    overall: Verdict
    why: str = ""
    if worse > 0:
        overall = "worse"
    elif "unclear" in verdicts:
        overall = "unclear"
        why = _cut_short(base, candidate, verdicts.count("unclear"), len(tasks))
    elif better > 0 and gain >= margin:
        overall = "better"
    elif better > 0:
        overall = "unclear"
        why = (
            f"it passed {gain} {'attempt' if gain == 1 else 'attempts'} more than the base over the scoring set, and "
            f"{margin} are the least that tells a better text from a lucky attempt"
        )
    else:
        overall = "same"
    tokens_delta: int = round(
        sum(
            task.candidate.tokens - task.base.tokens
            for task in tasks
            if task.base is not None and task.candidate is not None
        )
    )
    return Contrast(
        label=candidate.label,
        tasks=tuple(tasks),
        verdict=overall,
        better=better,
        worse=worse,
        tokens_delta=tokens_delta,
        gain=gain,
        why=why,
    )


def confirm_contrast(base: ProbeReport, survivor: ProbeReport, margin: int) -> Contrast:
    """The survivor's confirmation against the base's, both played again on new attempts, :data:`CONFIRM_FACTOR` times
    as many as the step's own (the reports say how many): better when it did better on a task, worse on none and
    :data:`CONFIRM_FACTOR` times ``margin`` passes more in all."""
    return contrast(base, survivor, margin * CONFIRM_FACTOR)


def contrast_text(found: Contrast) -> str:
    """A contrast as the models read it (item 588): the verdict in all, on how many of the tasks the candidate did
    better, worse and could not be judged, how its tokens compare and, for an unclear one, why. Never a task's id nor
    its verdict: what a candidate is judged on is never shown to the model that writes it (the composer, the experience
    writer)."""
    unclear: int = sum(1 for task in found.tasks if task.verdict == "unclear")
    sign: str = "+" if found.tokens_delta >= 0 else ""
    return (
        f"In all {found.verdict}: better on {found.better} of {len(found.tasks)} tasks, worse on {found.worse}"
        + (f", not played enough to judge on {unclear}" if unclear else "")
        + f", tokens {sign}{found.tokens_delta}."
        + (f" Why unclear: {found.why}." if found.why else "")
    )


def contrast_detail(found: Contrast) -> str:
    """A contrast for the owner, task by task with its id: what is kept in the notes of a version and in the record of a
    step, to be read on the page and with ``dotx harness show``, and never shown to a model (:func:`contrast_text`)."""
    parts: list[str] = []
    for task in found.tasks:
        if task.base is None or task.candidate is None:
            parts.append(f"{task.task_id}: not played under both")
            continue
        parts.append(
            f"{task.task_id}: {task.verdict} ({task.candidate.passes}/{task.candidate.attempts} passed against "
            f"{task.base.passes}/{task.base.attempts}; {task.candidate.steps:.1f} steps against {task.base.steps:.1f})"
        )
    return f"{contrast_text(found)} " + "; ".join(parts) + "."


def choose(contrasts: Sequence[Contrast]) -> Contrast | None:
    """The contrast of the candidate that survives: better in all, on the most tasks, then with the fewest tokens;
    ``None`` when none did better."""
    winners: list[Contrast] = [found for found in contrasts if found.verdict == "better"]
    if not winners:
        return None
    return max(winners, key=lambda found: (found.better, -found.tokens_delta))
