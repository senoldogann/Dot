"""The pure decisions of a schedule's task tried once more (item 1206): which ends are tried, when, which tasks never
are, how a try is told apart and claimed once, and how an error that ended a run fares with time."""

from dotagent.agent.resume import InterruptedCall
from dotagent.core.errors import (
    ClaudeCodeBroke,
    ClaudeCodeError,
    ClaudePlanLimitError,
    ProviderAuthError,
    ProviderHttpError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransportError,
    failure_kind,
)
from dotagent.scheduler.recovery import (
    AFTER_RESET_S,
    NO_RETRIES,
    RESET_WAIT_MOST_S,
    RETRY_AFTER_S,
    EndFacts,
    NoRetry,
    PendingRetry,
    Retry,
    StartedRetry,
    claimed,
    decide,
    is_try_run,
    outward_tools,
    retry_why,
    superseded,
    with_pending,
    with_run,
)
from dotagent.store.models import ResultStep, RunRecord

NOW: float = 2_000_000.0


def _facts(why: str | None, outward: tuple[str, ...], is_try: bool, resets_at: float | None) -> EndFacts:
    return EndFacts.model_validate(
        {
            "run_id": "run_a",
            "why": why,
            "resets_at": resets_at,
            "ended_at": NOW - 5,
            "is_try": is_try,
            "tried": False,
            "outward": outward,
        }
    )


def _step(tool: str, kind: str, flags: tuple[str, ...]) -> ResultStep:
    return ResultStep(tool=tool, kind=kind, target="", flags=flags)


def _cut(tool: str, kind: str | None, flags: tuple[str, ...]) -> InterruptedCall:
    return InterruptedCall.model_validate(
        {"tool": tool, "arguments": "{}", "kind": kind, "end": "cut_off", "result": "", "flags": flags}
    )


def _run(run_id: str, thread_id: str, created_at: float) -> RunRecord:
    return RunRecord.model_validate(
        {
            "id": run_id,
            "thread_id": thread_id,
            "goal": "g",
            "mode": "task",
            "model": "m",
            "status": "running",
            "created_at": created_at,
            "finished_at": None,
            "result": "",
            "steps": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "parent_run_id": None,
            "source": "schedule",
            "effort": None,
            "speed": "standard",
            "once": None,
            "approvals": "yolo",
            "goal_mode": False,
            "campaign_id": None,
            "agent_id": None,
            "rewindable": True,
        }
    )


def test_the_ends_a_try_helps_are_a_cut_a_passing_error_a_stall_and_the_plans_limit() -> None:
    assert retry_why("daemon_restarted", None) == "cut_off"
    assert retry_why("daemon_shutdown", None) == "cut_off"
    assert retry_why("stalled", None) == "stalled"
    assert retry_why("empty_replies", None) == "stalled"
    assert retry_why("failed", "passing") == "passing_error"
    assert retry_why("failed", "plan_limit") == "plan_limit"
    # A check that could not run is no end a try helps either: the work was done, and a try would do it again.
    for ending in (
        "stopped",
        "emergency_stop",
        "limit",
        "goal_check_refused",
        "goal_check_unavailable",
        "internal_error",
        "finished",
    ):
        assert retry_why(ending, None) is None
    assert retry_why("failed", "lasting") is None
    assert retry_why("failed", None) is None


def test_a_passing_failure_is_tried_soon_and_the_plans_limit_after_its_reset() -> None:
    soon: Retry | NoRetry | None = decide(_facts("cut_off", (), False, None), NOW)
    assert isinstance(soon, Retry) and soon.pending.due_at == NOW + RETRY_AFTER_S
    reset: Retry | NoRetry | None = decide(_facts("plan_limit", (), False, NOW + 600), NOW)
    assert isinstance(reset, Retry) and reset.pending.due_at == NOW + 600 + AFTER_RESET_S
    unknown: Retry | NoRetry | None = decide(_facts("plan_limit", (), False, None), NOW)
    assert isinstance(unknown, NoRetry) and "did not say" in unknown.reason
    far: Retry | NoRetry | None = decide(_facts("plan_limit", (), False, NOW + RESET_WAIT_MOST_S + 1), NOW)
    assert isinstance(far, NoRetry)
    assert decide(_facts(None, (), False, None), NOW) is None


def test_a_task_that_sent_something_or_is_a_try_is_never_tried() -> None:
    sent: Retry | NoRetry | None = decide(_facts("passing_error", ("send_mail",), False, None), NOW)
    assert isinstance(sent, NoRetry) and "send_mail" in sent.reason
    again: Retry | NoRetry | None = decide(_facts("cut_off", (), True, None), NOW)
    assert isinstance(again, NoRetry) and "itself the one try" in again.reason


def test_outward_steps_are_read_from_the_flags_hand_offs_and_unfinished_calls() -> None:
    steps: list[ResultStep] = [
        _step("web_fetch", "network", ()),
        _step("shell", "shell", ("upload",)),
        _step("delegate", "delegate", ()),
        _step("work_order", "delegate", ()),
        _step("write_file", "write", ("outside_workspace",)),
    ]
    cut: list[InterruptedCall] = [
        _cut("read_file", "read", ()),
        _cut("desktop_act", None, ()),
        _cut("browser_click", "browser_click", ("external_send",)),
    ]
    open_calls: list[str] = ["read_file", "desktop_act", "browser_click"]
    assert outward_tools(steps, cut, open_calls, 0) == ("shell", "work_order", "desktop_act", "browser_click")
    assert outward_tools([], [], [], 2) == ("a helper's unfinished call",)
    assert outward_tools([_step("read_file", "read", ())], [_cut("read_file", "read", ())], ["read_file"], 0) == ()
    # A run on the Claude plan keeps no calls in its chat: an unfinished call there is not told apart, so it counts.
    assert outward_tools([], [], ["read_file"], 0) == ("read_file",)
    # One inward cut call answers for one unfinished call of its tool, not two.
    assert outward_tools([], [_cut("read_file", "read", ())], ["read_file", "read_file"], 0) == ("read_file",)


def test_a_try_is_told_apart_by_its_mark_or_its_claim_and_claimed_once() -> None:
    pending: PendingRetry = PendingRetry(from_run_id="run_a", why="cut_off", due_at=NOW, ended_at=NOW - 20)
    waiting = with_pending(NO_RETRIES, "sch_1", pending)
    once = claimed(waiting, "sch_1", "run_a", "thr_2", NOW)
    assert once.pending == {} and once.started["sch_1"].thread_id == "thr_2"
    # A second claim finds nothing waiting, and changes nothing.
    assert claimed(once, "sch_1", "run_a", "thr_3", NOW + 1) == once
    started: StartedRetry = once.started["sch_1"]
    # Before its run is recorded, a run of that chat made since the claim is the try; none older, none elsewhere.
    assert is_try_run(_run("run_b", "thr_2", NOW + 1), False, started)
    assert not is_try_run(_run("run_b", "thr_9", NOW + 1), False, started)
    assert not is_try_run(_run("run_b", "thr_2", NOW - 1), False, started)
    recorded = with_run(once, "sch_1", "run_a", "run_b").started["sch_1"]
    assert is_try_run(_run("run_b", "thr_2", NOW + 1), False, recorded)
    assert not is_try_run(_run("run_c", "thr_2", NOW + 2), False, recorded)
    assert is_try_run(_run("run_c", "thr_9", NOW), True, None)
    # A firing supersedes both what waits and what was claimed.
    assert superseded(with_pending(once, "sch_1", pending), "sch_1") == NO_RETRIES


def test_how_an_error_that_ended_a_run_fares_with_time() -> None:
    assert failure_kind(ProviderTransportError("reset", {})) == "passing"
    assert failure_kind(ProviderRateLimitError("429", 429, None, {})) == "passing"
    assert failure_kind(ProviderHttpError("502", 502, {})) == "passing"
    assert failure_kind(ProviderHttpError("400", 400, {})) == "lasting"
    assert failure_kind(ProviderAuthError("401", 401, {})) == "lasting"
    assert failure_kind(ProviderQuotaError("spent", 402, None, {})) == "lasting"
    assert failure_kind(ClaudeCodeBroke("silent", {})) == "passing"
    assert failure_kind(ClaudeCodeError("more than Dot's tools", {})) == "lasting"
    limit: ClaudePlanLimitError = ClaudePlanLimitError("limit", NOW, {})
    assert failure_kind(limit) == "plan_limit" and limit.context["resets_at"] == NOW
