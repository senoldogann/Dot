"""The Mac's own pointer and keyboard are the owner's while they are at the Mac (item 1890): a desktop step that takes
them waits until nobody but Dot has touched them for ``[desktop] away_after_s``, the owner's first touch ends Dot's use
of them at the next step, and the batch goes on once they stepped away. A fake idle clock stands in for macOS's; the
accessibility calls and the events are replaced, so nothing reaches the screen."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dotagent.config import DesktopConfig, load_default_config
from dotagent.core.errors import OwnerAtTheMac, RunCancelled
from dotagent.core.outcomes import StepReport
from dotagent.desktop import actions, ax, session
from dotagent.desktop import input as hid
from dotagent.desktop.actions import Effect, Inspection, StepEnv
from dotagent.desktop.presence import OWN_INPUT_SLACK_S, OwnerPresence, OwnInput, is_away, owner_input_at
from dotagent.desktop.safety import StepContext
from dotagent.desktop.session import DesktopSession, StepReview, no_browser_chrome
from dotagent.desktop.types import ActOutcome, AppInfo, DisplayInfo, Rect, Step
from dotagent.tools.context import ToolContext
from dotagent.tools.desktop import (
    DESKTOP_AWAY_EVENT,
    DESKTOP_WAITING_EVENT,
    DesktopBatch,
    act_when_away,
    joined,
)

_MAIL: AppInfo = AppInfo(
    pid=700, name="Mail", bundle_id="com.apple.mail", bundle_path="", kind="regular", frontmost=True
)
_AWAY_S: float = 600.0


class _Mac:
    """macOS's clock of the last input, and the time: the owner touches the Mac with :meth:`touch`, Dot's own posted
    events touch it too (a step of :class:`DesktopSession` that posts calls :meth:`dot_posts`)."""

    def __init__(self) -> None:
        self.now: float = 10_000.0
        self.last_input: float = 0.0

    def idle(self) -> float:
        return self.now - self.last_input

    def clock(self) -> float:
        return self.now

    def touch(self) -> None:
        self.last_input = self.now

    def presence(self) -> OwnerPresence:
        return OwnerPresence(self.idle, self.clock, _AWAY_S)


def test_the_owner_is_away_after_the_threshold_and_dots_own_input_is_not_theirs() -> None:
    mac: _Mac = _Mac()
    presence: OwnerPresence = mac.presence()
    mac.touch()
    mac.now += _AWAY_S - 1
    assert not presence.away()
    mac.now += 1
    assert presence.away()
    # Dot posts a step: macOS's clock reads 0 s idle, which is Dot's touch, not the owner's.
    began: float = presence.now()
    mac.now += 0.5
    mac.touch()
    mac.now += 0.2
    presence.note_own_input(began, presence.now())
    mac.now += 3
    assert presence.away()
    # The owner's first touch after it is theirs.
    mac.now += 5
    mac.touch()
    assert not presence.away()


def test_what_macos_cannot_say_is_not_away_and_the_pure_rules_hold() -> None:
    assert not OwnerPresence(lambda: None, lambda: 0.0, _AWAY_S).away()
    assert not is_away(None, 1000.0, 10.0)
    assert is_away(990.0, 1000.0, 10.0) and not is_away(991.0, 1000.0, 10.0)
    span: OwnInput = OwnInput(began=100.0, ended=102.0)
    assert owner_input_at(101.0, span, 5.0, OWN_INPUT_SLACK_S) == 5.0
    assert owner_input_at(102.0 + OWN_INPUT_SLACK_S, span, 5.0, OWN_INPUT_SLACK_S) == 5.0
    assert owner_input_at(104.0, span, 5.0, OWN_INPUT_SLACK_S) == 104.0
    assert owner_input_at(50.0, None, 5.0, OWN_INPUT_SLACK_S) == 50.0


def test_which_steps_take_the_pointer_or_the_keyboard() -> None:
    takes: list[Step] = [
        Step(action="click", x=10.0, y=10.0),
        Step(action="click", ref="a3", modifiers=["cmd"]),
        Step(action="double_click", ref="a3"),
        Step(action="type", text="hi"),
        Step(action="key", key="cmd+s"),
        Step(action="activate", text="Notes"),
        Step(action="scroll", ref="a3", direction="down"),
    ]
    free: list[Step] = [
        Step(action="click", ref="a3"),
        Step(action="press", ref="a3"),
        Step(action="focus", ref="a3"),
        Step(action="set_value", ref="a3", text="x"),
        Step(action="menu", path=["File", "Save"]),
        Step(action="wait", seconds=1),
    ]
    assert all(actions.takes_input(step) for step in takes)
    assert not any(actions.takes_input(step) for step in free)


def _env(tmp_path: Path, owner_here: bool) -> StepEnv:
    return StepEnv(
        app=_MAIL,
        app_element="app-element",
        elements={},
        running=(_MAIL,),
        displays=(DisplayInfo(display_id=1, bounds=Rect(0, 0, 2000, 1200), main=True),),
        scale=1.0,
        source=object(),
        config=load_default_config().desktop,
        failsafe_file=tmp_path / "STOP",
        expected_front=_MAIL.pid,
        expected_pointer=None,
        preferred=frozenset(),
        secure_field=lambda inspection, what: None,
        owner_here=owner_here,
    )


def _inspection() -> Inspection:
    return Inspection(
        target="button 'Send'", context=StepContext(role="other", app="Mail", element=None, secure=False, line=None)
    )


def test_a_step_that_takes_the_keyboard_is_not_sent_while_the_owner_is_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[object] = []
    monkeypatch.setattr(actions, "check_failsafe", lambda env: None)
    monkeypatch.setattr(actions, "_reviewed_again", lambda env, step, reviewed: reviewed)
    monkeypatch.setattr(hid, "post", lambda events, check=None: posted.append(events))
    monkeypatch.setattr(hid, "chord_events", lambda source, chord: ("chord", chord.key))
    step: Step = Step(action="key", key="cmd+s")
    with pytest.raises(OwnerAtTheMac):
        actions.execute_step(_env(tmp_path, True), step, _inspection())
    assert posted == []
    actions.execute_step(_env(tmp_path, False), step, _inspection())
    assert posted == [("chord", "s")]


def _batch_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mac: _Mac, during: Callable[[Step], None]
) -> tuple[DesktopSession, list[Step]]:
    """A session on fakes whose steps run :func:`actions.execute_step`'s check of the owner, ``during`` called as each
    step that went is performed; ``sent`` lists them."""
    sent: list[Step] = []
    monkeypatch.setattr(session.apps, "ensure_window_server", lambda: None)
    monkeypatch.setattr(DesktopSession, "_prepare_acting", lambda self: None)
    monkeypatch.setattr(DesktopSession, "_prepare_listing", lambda self: None)
    monkeypatch.setattr(session, "_needs_front", lambda steps, nodes: False)
    monkeypatch.setattr(session, "_STEP_GAP_S", 0.0)

    def open_env(self: DesktopSession, state: object, name: object, steps: object, secure_field: object) -> StepEnv:
        return _env(tmp_path, self._owner_here())

    async def closing_view(
        self: DesktopSession, state: object, app: object, max_chars: int
    ) -> tuple[None, str, object]:
        return None, "no view", state

    monkeypatch.setattr(DesktopSession, "_open_env", open_env)
    monkeypatch.setattr(DesktopSession, "_closing_view", closing_view)
    monkeypatch.setattr(session, "_remeasure", lambda env: env)
    monkeypatch.setattr(session, "_front_taken_by", lambda env, remeasured: None)
    monkeypatch.setattr(ax, "wait_until_quiet", lambda pid, quiet_s, max_s: None)
    monkeypatch.setattr(actions, "inspect_step", lambda env, step, lines: _inspection())

    def execute(env: StepEnv, step: Step, reviewed: Inspection) -> Effect:
        if actions.takes_input(step) and env.owner_here:
            raise OwnerAtTheMac("the owner is at the Mac", {})
        if actions.takes_input(step):
            mac.touch()  # what Dot's own posted events do to macOS's clock
        sent.append(step)
        during(step)
        return Effect("done", None, False, None)

    monkeypatch.setattr(actions, "execute_step", execute)
    config: DesktopConfig = load_default_config().desktop.model_copy(update={"show_pointer": False})
    return DesktopSession(config, tmp_path / "STOP", no_browser_chrome, None, mac.presence()), sent


async def _allow(review: StepReview) -> None:
    return None


async def _act(held: DesktopSession, steps: list[Step], first: int) -> ActOutcome:
    return await held.act("thread", None, steps, _allow, lambda inspection, what: None, 1000, "Dot", first)


async def test_while_the_owner_is_here_accessibility_goes_and_the_pointer_step_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mac: _Mac = _Mac()
    mac.touch()
    held, sent = _batch_session(tmp_path, monkeypatch, mac, lambda step: None)
    steps: list[Step] = [
        Step(action="press", ref="a3"),
        Step(action="key", key="cmd+s"),
        Step(action="press", ref="a4"),
    ]
    try:
        outcome: ActOutcome = await _act(held, steps, 1)
        assert [report.outcome for report in outcome.steps] == ["done", "skipped", "skipped"]
        assert outcome.waits_from == 2 and not outcome.owner_came_back
        assert outcome.stopped is not None and "owner is at the Mac" in outcome.stopped
        assert sent == steps[:1]
        # Away: the rest goes, numbered as the batch's own.
        mac.now += _AWAY_S
        rest: ActOutcome = await _act(held, steps[1:], 2)
        assert [report.index for report in rest.steps] == [2, 3]
        assert [report.outcome for report in rest.steps] == ["done", "done"] and rest.waits_from is None
        assert sent == steps
    finally:
        await held.close()


async def test_the_owners_return_ends_dots_use_of_the_pointer_at_the_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mac: _Mac = _Mac()

    held, sent = _batch_session(tmp_path, monkeypatch, mac, lambda step: None)

    def owner_comes_back(env: StepEnv) -> StepEnv:
        # After Dot's own events of the first step went, the owner moves the mouse.
        mac.now += 2
        mac.touch()
        return env

    monkeypatch.setattr(session, "_remeasure", owner_comes_back)
    steps: list[Step] = [Step(action="key", key="tab"), Step(action="key", key="tab")]
    try:
        outcome: ActOutcome = await _act(held, steps, 1)
        assert [report.outcome for report in outcome.steps] == ["done", "skipped"]
        assert outcome.waits_from == 2 and outcome.owner_came_back
        assert outcome.stopped is not None and "came back" in outcome.stopped
        assert sent == steps[:1]
    finally:
        await held.close()


async def test_a_step_the_owner_approves_at_the_mac_is_not_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner was away as the step began, came back to answer its review at the Mac: the step waits."""
    mac: _Mac = _Mac()
    held, sent = _batch_session(tmp_path, monkeypatch, mac, lambda step: None)

    async def answered_at_the_mac(review: StepReview) -> None:
        mac.now += 30
        mac.touch()

    try:
        outcome: ActOutcome = await held.act(
            "thread", None, [Step(action="key", key="return")], answered_at_the_mac, lambda i, w: None, 1000, "Dot", 1
        )
        assert [report.outcome for report in outcome.steps] == ["skipped"] and outcome.waits_from == 1
        assert sent == []
    finally:
        await held.close()


async def test_dots_own_steps_do_not_read_as_the_owner_coming_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mac: _Mac = _Mac()

    def dot_takes_its_time(step: Step) -> None:
        mac.now += 0.3

    held, sent = _batch_session(tmp_path, monkeypatch, mac, dot_takes_its_time)
    steps: list[Step] = [Step(action="type", text="hello"), Step(action="key", key="return")]
    try:
        outcome: ActOutcome = await _act(held, steps, 1)
        assert [report.outcome for report in outcome.steps] == ["done", "done"]
        assert sent == steps
    finally:
        await held.close()


async def test_the_wait_for_the_owner_to_step_away_ends_when_they_did_at_its_limit_or_when_the_run_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mac: _Mac = _Mac()
    mac.touch()
    monkeypatch.setattr(session, "_AWAY_POLL_S", 0.01)
    held, _ = _batch_session(tmp_path, monkeypatch, mac, lambda step: None)
    try:
        assert await held.until_away(asyncio.Event(), 0.05) is None

        async def steps_away() -> None:
            await asyncio.sleep(0.03)
            mac.now += _AWAY_S

        stepping: asyncio.Task[None] = asyncio.create_task(steps_away())
        waited: float | None = await held.until_away(asyncio.Event(), 5.0)
        await stepping
        assert waited is not None and waited < 5.0
        mac.touch()
        stopped: asyncio.Event = asyncio.Event()
        stopped.set()
        with pytest.raises(RunCancelled):
            await held.until_away(stopped, 5.0)
    finally:
        await held.close()


class _Desktop:
    """The desktop of :func:`act_when_away`: the first batch waits at step 2, the owner steps away after a while."""

    def __init__(self, away_after: int) -> None:
        self.calls: list[tuple[str | None, int, int]] = []
        self.away_after: int = away_after
        self.looks: int = 0

    async def act(
        self,
        thread_id: str,
        app: str | None,
        steps: tuple[Step, ...],
        review: object,
        secure_field: object,
        max_chars: int,
        who: str,
        first: int,
    ) -> ActOutcome:
        self.calls.append((app, len(steps), first))
        if first == 1:
            reports = (
                StepReport(index=1, description="press", outcome="done", detail="", dispatched="sent"),
                StepReport(index=2, description="key", outcome="skipped", detail="", dispatched="not_sent"),
            )
            return ActOutcome(steps=reports, stopped="waits", view=None, view_note=None, waits_from=2)
        done = StepReport(index=first, description="key", outcome="done", detail="", dispatched="sent")
        return ActOutcome(steps=(done,), stopped=None, view=None, view_note=None)

    async def until_away(self, cancel: asyncio.Event, limit_s: float) -> float | None:
        return 42.0 if self.away_after >= 0 else None


def _tool_context(desktop: _Desktop, events: list[tuple[str, dict[str, Any]]]) -> ToolContext:
    async def emit(run_id: str, kind: str, data: dict[str, Any], persist: bool) -> None:
        events.append((kind, data))

    runtime = SimpleNamespace(config=load_default_config(), desktop=desktop, bus=SimpleNamespace(emit=emit))
    return ToolContext(
        runtime=cast(Any, runtime),
        browser=cast(Any, None),
        run_id="run_test",
        agent_id=None,
        thread_id="thread_test",
        mode="task",
        approvals="yolo",
        questions="leave",
        workspace=Path("/tmp"),
        gate=cast(Any, None),
        cancel=asyncio.Event(),
        output_chars=1000,
        facts=cast(Any, lambda: None),
    )


def _batch() -> DesktopBatch:
    return DesktopBatch(
        app="Mail",
        steps=(Step(action="press", ref="a3"), Step(action="key", key="cmd+s")),
        review=_allow,
        secure_field=lambda inspection, what: None,
        max_chars=1000,
        who="Dot",
    )


async def test_a_batch_that_waited_goes_on_with_its_rest_and_the_run_shows_the_wait() -> None:
    desktop: _Desktop = _Desktop(away_after=0)
    events: list[tuple[str, dict[str, Any]]] = []
    outcome, notes = await act_when_away(_tool_context(desktop, events), _batch())
    assert desktop.calls == [("Mail", 2, 1), (None, 1, 2)]
    assert [(report.index, report.outcome) for report in outcome.steps] == [(1, "done"), (2, "done")]
    assert [kind for kind, _ in events] == [DESKTOP_WAITING_EVENT, DESKTOP_AWAY_EVENT]
    assert events[0][1]["step"] == 2 and events[0][1]["away_after_s"] == 600
    assert events[1][1]["end"] == "away"
    assert len(notes) == 1 and "waited" in notes[0]


async def test_a_batch_whose_owner_stays_gives_up_its_steps_and_says_so() -> None:
    desktop: _Desktop = _Desktop(away_after=-1)
    events: list[tuple[str, dict[str, Any]]] = []
    outcome, notes = await act_when_away(_tool_context(desktop, events), _batch())
    assert desktop.calls == [("Mail", 2, 1)]
    assert outcome.waits_from == 2 and events[1][1]["end"] == "limit"
    assert "were not sent" in notes[0]


def test_a_joined_outcome_keeps_the_steps_before_the_wait() -> None:
    before = ActOutcome(
        steps=(
            StepReport(index=1, description="a", outcome="done", detail="", dispatched="sent"),
            StepReport(index=2, description="b", outcome="skipped", detail="", dispatched="not_sent"),
        ),
        stopped="waits",
        view=None,
        view_note=None,
        waits_from=2,
    )
    after = ActOutcome(
        steps=(StepReport(index=2, description="b", outcome="done", detail="", dispatched="sent"),),
        stopped=None,
        view=None,
        view_note=None,
    )
    both: ActOutcome = joined(before, after, 2)
    assert [(report.index, report.outcome) for report in both.steps] == [(1, "done"), (2, "done")]
    assert both.stopped is None and both.waits_from is None
