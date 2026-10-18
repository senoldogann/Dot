"""What a probe of a candidate shows against the probe of the base (item 391, docs/harness.md): per task, whether the
candidate passed more, as often or less often; in all, whether it did better (more on a task, less on none), the same,
worse (less on a task), or whether that cannot be told (a probe cut short before a task was played). A contrast says
how a candidate behaved, not that it is right: runs vary, and the models share their blind spots."""

from collections.abc import Sequence
from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.harness.probe import ProbeReport, Scorecard

type Verdict = Literal["better", "same", "worse", "unclear"]


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
    """A candidate against the base: a verdict per task and in all, how many tasks it did better and worse on, and how
    its tokens compare (the candidate's less the base's, over the tasks both played)."""

    label: str
    tasks: tuple[TaskContrast, ...]
    verdict: Verdict
    better: int
    worse: int
    tokens_delta: int


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


def contrast(base: ProbeReport, candidate: ProbeReport) -> Contrast:
    """The candidate's report against the base's, task by task over the base's tasks."""
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
    overall: Verdict
    if worse > 0:
        overall = "worse"
    elif "unclear" in verdicts:
        overall = "unclear"
    elif better > 0:
        overall = "better"
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
    )


def contrast_text(found: Contrast) -> str:
    """One paragraph of a contrast, as the composer and the experience writer read it."""
    parts: list[str] = []
    for task in found.tasks:
        if task.base is None or task.candidate is None:
            parts.append(f"{task.task_id}: not played under both")
            continue
        parts.append(
            f"{task.task_id}: {task.verdict} ({task.candidate.passes}/{task.candidate.attempts} passed against "
            f"{task.base.passes}/{task.base.attempts}; {task.candidate.steps:.1f} steps against {task.base.steps:.1f})"
        )
    sign: str = "+" if found.tokens_delta >= 0 else ""
    return (
        f"In all {found.verdict}: better on {found.better}, worse on {found.worse}, tokens {sign}{found.tokens_delta}. "
        + "; ".join(parts)
        + "."
    )


def choose(contrasts: Sequence[Contrast]) -> Contrast | None:
    """The contrast of the candidate that survives: better in all, on the most tasks, then with the fewest tokens;
    ``None`` when none did better."""
    winners: list[Contrast] = [found for found in contrasts if found.verdict == "better"]
    if not winners:
        return None
    return max(winners, key=lambda found: (found.better, -found.tokens_delta))
