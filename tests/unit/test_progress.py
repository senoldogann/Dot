"""The progress watchdog: its detectors, its ladder and the end it writes, over fixed steps."""

from collections.abc import Sequence

import pytest

from dotagent.agent.continuity import LedgerAction, RunLedger
from dotagent.agent.progress import (
    SINGLE_ACTION_STEPS,
    ActCall,
    Alert,
    CallSeen,
    Limits,
    Pattern,
    Rung,
    SingleAction,
    WatchState,
    advance,
    can_ask_user,
    clear_window,
    ladder_for,
    limits_for,
    new_watch,
    restriction_for,
    single_action,
    single_action_steer,
    single_action_streak,
    stop_result,
)
from dotagent.config import load_default_config
from dotagent.evals import EVAL_SOURCE
from dotagent.policy.types import ApprovalMode
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import UI_SOURCE
from dotagent.store.endings import PREFIX_STALLED, classify_ending
from dotagent.tools.delegate import DELEGATE_SOURCE
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL, MARK_PLAN_STEP_TOOL, UPDATE_PLAN_TOOL

type Step = tuple[CallSeen, ...]
# A step that fired: its number (from 1), the rung reached and the pattern seen.
type Fired = tuple[int, Rung, Pattern]

ESCALATING: tuple[Rung, ...] = ladder_for(True, False)


def _limits(escalation: bool) -> Limits:
    return limits_for(
        load_default_config().agent.model_copy(
            update={"stall_escalation": escalation, "stall_limit": 4, "failure_streak": 6, "revisit_window": 8}
        )
    )


def _ok(name: str, arguments: str, result: str) -> Step:
    return (CallSeen(name=name, arguments=arguments, result=result, failed=False, images=()),)


def _error(name: str, arguments: str, result: str) -> Step:
    return (CallSeen(name=name, arguments=arguments, result=result, failed=True, images=()),)


def _play(
    state: WatchState, steps: Sequence[Step], limits: Limits, ladder: tuple[Rung, ...]
) -> tuple[WatchState, list[Fired]]:
    """The state after ``steps``, and the steps among them that raised an alert."""
    fired: list[Fired] = []
    for number, calls in enumerate(steps, start=1):
        alert: Alert | None
        state, alert = advance(state, calls, limits, ladder)
        if alert is not None:
            fired.append((number, alert.rung, alert.pattern))
    return state, fired


def _read(path: str, content: str) -> Step:
    return _ok("read_file", f'{{"path":"{path}"}}', content)


def test_the_same_step_with_the_same_result_four_times_is_a_repeat_but_a_changing_result_is_progress() -> None:
    state, fired = _play(new_watch(), [_read("notes.txt", "draft")] * 4, _limits(True), ESCALATING)
    assert fired == [(4, "nudge", "repeat")]
    assert (state.window, state.level) == ((), 1)
    growing: list[Step] = [_read("build.log", f"{lines} lines") for lines in range(10)]
    assert _play(new_watch(), growing, _limits(True), ESCALATING)[1] == []


@pytest.mark.parametrize(("period", "number"), [(2, 4), (3, 6)])
def test_a_circle_of_steps_with_the_same_results_every_round_is_a_cycle(period: int, number: int) -> None:
    rounds: list[Step] = [_read(f"page{index}.html", f"page {index}") for index in range(period)] * 3
    fired: list[Fired] = _play(new_watch(), rounds, _limits(True), ESCALATING)[1]
    assert fired[0] == (number, "nudge", "cycle")


def test_a_streak_of_failed_steps_counts_even_when_each_tries_something_else() -> None:
    failing: list[Step] = [_error("shell", f'{{"command":"make try{index}"}}', f"error {index}") for index in range(6)]
    assert _play(new_watch(), failing, _limits(True), ESCALATING)[1] == [(6, "nudge", "failures")]
    # A step in which one call worked is no failed step.
    mixed: Step = (*_error("shell", '{"command":"make"}', "error"), *_read("Makefile", "all:"))
    assert _play(new_watch(), [*failing[:5], mixed], _limits(True), ESCALATING)[1] == []


def test_steps_that_only_redo_earlier_calls_with_the_same_results_are_revisiting() -> None:
    a, b, c, d = (_read(f"{name}.txt", name) for name in "abcd")
    # Four first visits, then the same four again in an order that is neither a repeat nor a cycle.
    steps: list[Step] = [a, b, c, d, b, d, a, c, b, a, d]
    assert _play(new_watch(), steps, _limits(True), ESCALATING)[1] == [(11, "nudge", "revisiting")]


def test_edits_of_one_file_that_get_the_same_answer_are_work_and_the_same_edit_again_is_a_cycle() -> None:
    check: Step = _ok("shell", '{"command":"python3 test_app.py"}', "1 failed")
    editing: list[Step] = [
        step
        for version in range(15)
        for step in (_ok("write_file", f'{{"path":"app.py","content":"v{version}"}}', "Wrote 1 line."), check)
    ]
    assert _play(new_watch(), editing, _limits(True), ESCALATING)[1] == []
    same: Step = _ok("write_file", '{"path":"app.py","content":"v0"}', "Wrote 1 line.")
    assert _play(new_watch(), [same, check, same, check], _limits(True), ESCALATING)[1] == [(4, "nudge", "cycle")]


@pytest.mark.parametrize(
    ("can_ask", "rungs"),
    [(True, ["nudge", "replan", "consult", "ask", "stop"]), (False, ["nudge", "replan", "consult", "stop"])],
)
def test_every_alert_needs_fresh_evidence_and_climbs_one_rung_up_to_the_stop(can_ask: bool, rungs: list[Rung]) -> None:
    ladder: tuple[Rung, ...] = ladder_for(True, can_ask)
    fired: list[Fired] = _play(new_watch(), [_read("notes.txt", "draft")] * 4 * len(rungs), _limits(True), ladder)[1]
    assert fired == [(4 * level, rung, "repeat") for level, rung in enumerate(rungs, start=1)]


def test_a_long_quiet_stretch_brings_the_ladder_one_rung_down() -> None:
    limits: Limits = _limits(True)
    warned: WatchState = _play(new_watch(), [_read("notes.txt", "draft")] * 4, limits, ESCALATING)[0]
    work: list[Step] = [_read(f"chapter{index}.md", f"chapter {index}") for index in range(12)]
    assert _play(warned, work[:11], limits, ESCALATING)[0].level == 1
    calmed: WatchState = _play(warned, work, limits, ESCALATING)[0]
    assert calmed.level == 0
    assert _play(calmed, [_read("todo.txt", "todo")] * 4, limits, ESCALATING)[1] == [(4, "nudge", "repeat")]


def test_a_fresh_start_forgets_the_recent_steps_but_keeps_the_rung() -> None:
    limits: Limits = _limits(True)
    warned: WatchState = _play(new_watch(), [_read("notes.txt", "draft")] * 4, limits, ESCALATING)[0]
    three: WatchState = _play(warned, [_read("todo.txt", "todo")] * 3, limits, ESCALATING)[0]
    restarted: WatchState = clear_window(three)
    assert restarted.level == 1
    assert _play(restarted, [_read("todo.txt", "todo")] * 4, limits, ESCALATING)[1] == [(4, "replan", "repeat")]


def test_without_escalation_only_the_same_step_again_and_again_counts_and_the_third_alert_stops() -> None:
    limits: Limits = _limits(False)
    ladder: tuple[Rung, ...] = ladder_for(False, True)
    circle: list[Step] = [_read("a.txt", "a"), _read("b.txt", "b")] * 6
    failing: list[Step] = [_error("shell", f'{{"command":"make try{index}"}}', "error") for index in range(10)]
    assert _play(new_watch(), [*circle, *failing], limits, ladder)[1] == []
    fired: list[Fired] = _play(new_watch(), [_read("notes.txt", "draft")] * 12, limits, ladder)[1]
    assert fired == [(4, "nudge", "repeat"), (8, "nudge", "repeat"), (12, "stop", "repeat")]


@pytest.mark.parametrize(
    ("rung", "can_ask", "allowed"),
    [
        ("replan", False, {UPDATE_PLAN_TOOL, MARK_PLAN_STEP_TOOL, FINISH_TOOL}),
        ("replan", True, {UPDATE_PLAN_TOOL, MARK_PLAN_STEP_TOOL, FINISH_TOOL, ASK_USER_TOOL}),
        ("ask", True, {ASK_USER_TOOL, FINISH_TOOL}),
        ("nudge", True, None),
        ("consult", True, None),
    ],
)
def test_a_replan_or_a_question_limits_the_next_step_and_the_other_rungs_do_not(
    rung: Rung, can_ask: bool, allowed: set[str] | None
) -> None:
    assert restriction_for(rung, can_ask) == (None if allowed is None else frozenset(allowed))


@pytest.mark.parametrize(
    ("source", "parent", "approvals", "asks"),
    [
        (UI_SOURCE, None, "ask", True),
        (UI_SOURCE, None, "auto", True),
        (UI_SOURCE, None, "yolo", False),
        (UI_SOURCE, "run_parent", "ask", False),
        (DELEGATE_SOURCE, "run_parent", "ask", False),
        (SCHEDULE_SOURCE, None, "ask", False),
        (EVAL_SOURCE, None, "ask", False),
    ],
)
def test_only_a_watched_run_whose_user_wants_questions_can_ask(
    source: str, parent: str | None, approvals: ApprovalMode, asks: bool
) -> None:
    assert can_ask_user(source, parent, approvals) is asks


def test_a_stopped_run_says_what_it_tried_with_secrets_masked_and_counts_as_stalled() -> None:
    actions: tuple[LedgerAction, ...] = tuple(
        LedgerAction(tool="web_fetch", args=f'{{"url":"https://example.com/{index}"}}', ok=False, result="HTTP 503")
        for index in range(10)
    )
    ledger: RunLedger = RunLedger(
        goal="Download the report.",
        instructions=(),
        actions=(
            *actions,
            LedgerAction(tool="shell", args='{"command":"curl -H token=abcdef0123456789"}', ok=False, result="401"),
        ),
        action_count=11,
        touched=("https://example.com/report",),
        compactions=0,
    )
    result: str = stop_result("The same step ran 4 times in a row.", ledger, "1. [ ] download the report")
    assert result.startswith(f"{PREFIX_STALLED}. The same step ran 4 times in a row.")
    assert classify_ending("failed", result) == "stalled"
    shown: list[str] = result.split("What it did last:\n")[1].split("\nFiles and addresses")[0].splitlines()
    # The newest eight calls.
    assert len(shown) == 8
    assert "https://example.com/3" in shown[0]
    assert "abcdef0123456789" not in result
    assert "Files and addresses it touched: https://example.com/report" in result
    assert "Its plan:\n1. [ ] download the report" in result
    empty: RunLedger = RunLedger(goal="x", instructions=(), actions=(), action_count=0, touched=(), compactions=0)
    bare: str = stop_result("Nothing worked.", empty, "")
    assert bare.splitlines()[0] == f"{PREFIX_STALLED}. Nothing worked."
    assert "What it did last:" not in bare
    assert "Its plan:" not in bare


def _act(tool: str, steps: int, target: str) -> ActCall:
    return ActCall(tool=tool, steps=steps, target=target)


def test_a_step_is_single_action_when_every_act_carried_one_step_on_one_target() -> None:
    page: SingleAction = SingleAction(tool="browser_act", target="https://x.com/compose")
    assert single_action([_act("browser_act", 1, "https://x.com/compose")]) == page
    assert (
        single_action(
            [_act("browser_act", 1, "https://x.com/compose"), _act("browser_act", 1, "https://x.com/compose")]
        )
        == page
    )
    # A batch of several steps, no act, acts on two pages or with two tools: not a wasted round trip.
    assert single_action([_act("browser_act", 3, "https://x.com/compose")]) is None
    assert single_action([]) is None
    assert single_action([_act("browser_act", 1, "https://x.com/a"), _act("browser_act", 1, "https://x.com/b")]) is None
    assert single_action([_act("browser_act", 1, "Safari"), _act("desktop_act", 1, "Safari")]) is None


def test_the_streak_grows_on_the_same_target_restarts_on_a_new_one_and_breaks_on_a_batch() -> None:
    page: SingleAction = SingleAction(tool="browser_act", target="https://x.com/compose")
    other: SingleAction = SingleAction(tool="browser_act", target="https://x.com/home")
    streak: int = single_action_streak(0, None, page)
    assert streak == 1
    streak = single_action_streak(streak, page, page)
    streak = single_action_streak(streak, page, page)
    assert streak == SINGLE_ACTION_STEPS
    assert single_action_streak(streak, page, other) == 1
    assert single_action_streak(streak, page, None) == 0


def test_the_steer_names_the_tool_and_the_target_without_its_secrets() -> None:
    note: str = single_action_steer(
        SingleAction(tool="browser_act", target="https://x.com/compose?token=abcdef0123456789")
    )
    assert note.startswith(f"[{SINGLE_ACTION_STEPS} steps in a row acted with one step each on this page")
    assert "browser_act" in note and "abcdef0123456789" not in note
    desktop: str = single_action_steer(SingleAction(tool="desktop_act", target="Safari"))
    assert "this application (Safari)" in desktop and "desktop_act" in desktop
