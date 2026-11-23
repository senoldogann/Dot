"""The goal check that goes on to the next model when its own cannot answer (item 1354).

A goal-mode run that ends is checked once by the review model. When that model's quota, rate, credits or key are spent
the check cannot run, and the run ended ``failed`` although its work was done: on 2026-11-23 five of the nine failed
goal-mode runs of the owner's Dot failed for that alone (HTTP 429, "resets in 116 h"). This module asks the review model
first and, when it cannot answer for a while (:func:`dotagent.llm.router.unavailable_reason`), each model of
``[models] standby`` after it, as the run itself moves to a standby model when its own is spent (item 1225). Any other
error is raised as it comes, and when no model can answer the first one's error is raised, naming every model asked, so
the run says why it was not checked. A model the router holds paused is left out while another can be asked, as the
learning's questions do (a call to it spends the connector's retries on an answer its provider has refused already);
the first standby model is often the run's own, so the check may then be the run's own model's: the log line
``goal_check_passed_on`` says so.

Only the goal check goes on this way. The review of a run's actions (``dotagent.agent.review``) is not touched: it asks
its one model and never another.
"""

import time
from collections.abc import Callable, Collection, Sequence

from dotagent.agent.review import GoalChecker, GoalRequest, GoalVerdict, goal_checker_for
from dotagent.core.errors import ConfigError, DotError
from dotagent.core.logs import get_logger
from dotagent.llm.router import ModelRouter, parse_model_ref, unavailable_reason

log = get_logger(__name__)


def _label(model: str) -> str:
    """``model``'s label as the router pauses it, or the reference itself when it cannot be read."""
    try:
        return parse_model_ref(model).label
    except ConfigError:
        return model


def check_with_standby(
    checkers: Sequence[tuple[str, GoalChecker]],
    run_id: str | None,
    paused: Callable[[], Collection[str]] = lambda: (),
) -> GoalChecker:
    """A goal checker that asks the first of ``checkers`` (a model's reference and its checker) and, for an answer it
    cannot give for a while, the next one, until one answers. A model whose label ``paused`` names when the check comes
    is left out while another can be asked. ``checkers`` is not empty."""
    if not checkers:
        raise ValueError("a goal check needs at least one model to ask")

    async def check(request: GoalRequest) -> GoalVerdict:
        held: Collection[str] = paused()
        ready: list[tuple[str, GoalChecker]] = [pair for pair in checkers if _label(pair[0]) not in held]
        asked: list[tuple[str, GoalChecker]] = ready or list(checkers[:1])
        first: DotError | None = None
        passed: list[str] = [f"{model} (paused)" for model, _ in checkers if all(model != name for name, _ in asked)]
        for model, checker in asked:
            if first is not None:
                log.warning("goal_check_passed_on", run_id=run_id, passed=passed[-1], to=model)
            try:
                return await checker(request)
            except DotError as error:
                reason: str | None = unavailable_reason(error)
                if reason is None:
                    raise
                first = first or error
                passed.append(f"{model} ({reason})")
        assert first is not None
        if len(passed) > 1:
            first.message = f"{first.message}; no model could take it: {', '.join(passed)}"
            first.context["asked"] = list(passed)
        raise first

    return check


def goal_checker_with_standby(
    router: ModelRouter,
    review_model: str,
    standby: Sequence[str],
    timeout_s: float,
    session_key: str,
    run_id: str | None,
) -> GoalChecker:
    """The goal checker of run ``run_id`` asking ``review_model`` and then, when it cannot answer, each of ``standby``
    in turn (a model named twice is asked once)."""
    models: list[str] = list(dict.fromkeys((review_model, *standby)))
    return check_with_standby(
        [(model, goal_checker_for(router, model, timeout_s, session_key, run_id)) for model in models],
        run_id,
        lambda: set(router.paused(time.time())),
    )
