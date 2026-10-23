"""What a desktop step may run, judged without a screen (item 555): in a terminal application a button, a menu item or a
field is a command nobody read, an editor with a terminal and an application that runs scripts ask at the step that runs
what is written in them, Return presses the button that is focused or the default one, and the card of a step that may
run a command shows the command."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from dotagent.config import load_default_config
from dotagent.desktop.safety import (
    UNREAD_LINE,
    AppRole,
    StepContext,
    app_role,
    assess_step,
    lines_after,
    reaches_a_shell,
    shell_input,
)
from dotagent.desktop.session import StepReview
from dotagent.desktop.types import ActOutcome, Step
from dotagent.policy.elements import Element, StepAssessment
from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo
from dotagent.tools.context import ToolContext
from dotagent.tools.desktop import DESKTOP_ACT_TOOL, build_desktop_tools

_KEY: str = "sk-proj-" + "a1B2" * 8


def _context(
    role: AppRole,
    app: str,
    element: Element | None = None,
    line: str | None = None,
    default_button: Element | None = None,
) -> StepContext:
    return StepContext(role=role, app=app, element=element, secure=False, line=line, default_button=default_button)


def _terminal(element: Element | None = None, line: str | None = None) -> StepContext:
    return _context("terminal", "Terminal", element, line)


def _assess(step: Step, context: StepContext, tmp_path: Path) -> StepAssessment:
    return assess_step(step, context, tmp_path, (), ())


def _verdict(assessment: StepAssessment, kind: str, preset: str = "aggressive") -> str:
    info: ActionInfo = ActionInfo(
        tool="desktop_act",
        kind=kind,  # type: ignore[arg-type]
        risk=assessment.risk,
        summary="step",
        target="Terminal",
        flags=assessment.flags,
    )
    return decide(info, "task", False, preset, [], None).verdict  # type: ignore[arg-type]


_RUN: Element = Element(role="AXButton", name="Run")
_DESTROY: str = "rm -rf ~/Documents"
# What reaches a command with no key typed on a line Dot reads, in a terminal application.
_UNREAD_STEPS: list[tuple[Step, Element | None]] = [
    (Step(action="set_value", ref="a3", text=_DESTROY), Element(role="AXTextField", name="Command")),
    (Step(action="press", ref="a7"), _RUN),
    (Step(action="click", ref="a7"), _RUN),
    (Step(action="click", x=120, y=80), None),
    (Step(action="double_click", x=120, y=80), None),
    (Step(action="right_click", x=120, y=80), None),
    (Step(action="drag", x=120, y=80, to_x=200, to_y=80), None),
    (Step(action="menu", path=["Shell", "New Command…"]), None),
    (Step(action="menu", path=["Edit", "Paste"]), None),
]


@pytest.mark.parametrize(("step", "element"), _UNREAD_STEPS, ids=lambda value: getattr(value, "action", None))
def test_a_step_in_a_terminal_that_may_run_a_command_nobody_read_is_a_line_it_cannot_tell(
    step: Step, element: Element | None, tmp_path: Path
) -> None:
    """The audit's F9: a set_value, a press of Run, a menu item were judged low, and no question was asked, while the
    same text typed into Terminal asked."""
    context: StepContext = _terminal(element)
    assessment: StepAssessment = _assess(step, context, tmp_path)
    assert reaches_a_shell(step, context)
    assert assessment.risk == "high"
    assert {"destructive", "outside_workspace"} <= set(assessment.flags)
    for preset in ("cautious", "balanced", "aggressive"):
        assert _verdict(assessment, "shell", preset) == "ask", preset
    # The same step in an ordinary application stays what it was.
    elsewhere: StepContext = _context("other", "TextEdit", element)
    assert not reaches_a_shell(step, elsewhere)
    assert _assess(step, elsewhere, tmp_path).flags == ()


@pytest.mark.parametrize(
    "step",
    [
        Step(action="hover", x=10, y=10),
        Step(action="scroll", direction="down", x=10, y=10),
        Step(action="focus", ref="a3"),
        Step(action="wait", seconds=1),
        Step(action="activate", text="Terminal"),
        Step(action="menu", path=["Terminal", "Quit Terminal"]),
    ],
    ids=lambda step: step.action,
)
def test_what_only_looks_or_moves_the_keyboard_in_a_terminal_is_no_command(step: Step, tmp_path: Path) -> None:
    context: StepContext = _terminal()
    assert not reaches_a_shell(step, context)
    assert _assess(step, context, tmp_path).flags == ()


def test_the_new_judgement_keeps_the_line_a_terminal_holds_for_what_is_typed(tmp_path: Path) -> None:
    """A line typed in parts and run with Return is still judged whole, and what clicks leaves the line unknown."""
    steps: list[Step] = [
        Step(action="type", text="rm -rf ~/Doc"),
        Step(action="type", text="uments"),
        Step(action="key", key="return"),
    ]
    lines: dict[str, str] = {"Terminal": ""}
    last: StepAssessment | None = None
    for step in steps:
        context: StepContext = _terminal(line=lines.get("Terminal"))
        last = _assess(step, context, tmp_path)
        lines = lines_after(lines, step, context)
    assert last is not None and "destructive" in last.flags
    clicked: dict[str, str] = lines_after({"Terminal": "ls"}, Step(action="press", ref="a7"), _terminal())
    assert clicked == {}


@pytest.mark.parametrize(
    ("name", "bundle", "role"),
    [
        ("Code", "com.microsoft.VSCode", "shell_host"),
        ("Cursor", "com.todesktop.230313mzl4w4u92", "shell_host"),
        ("Zed", "dev.zed.Zed", "shell_host"),
        ("PyCharm", "com.jetbrains.pycharm", "shell_host"),
        ("Script Editor", "com.apple.ScriptEditor2", "shell_host"),
        ("Automator", "com.apple.Automator", "shell_host"),
        ("Shortcuts", "com.apple.shortcuts", "shell_host"),
        ("Visual Studio Code", None, "shell_host"),
        ("Terminal", "com.apple.Terminal", "terminal"),
        ("Notes", "com.apple.Notes", "other"),
        ("Google Chrome", "com.google.Chrome", "other"),
    ],
)
def test_an_editor_with_a_terminal_and_an_application_that_runs_scripts_are_known(
    name: str, bundle: str | None, role: AppRole
) -> None:
    assert app_role(name, bundle) == role


@pytest.mark.parametrize(
    ("step", "element", "asks"),
    [
        (Step(action="type", text=f"{_DESTROY}\n"), None, True),
        (Step(action="paste", text="echo hi\n"), None, True),
        (Step(action="key", key="return"), None, True),
        (Step(action="key", key="shift+return"), None, True),
        (Step(action="key", key="cmd+r"), None, True),
        (Step(action="key", key="f5"), None, True),
        (Step(action="press", ref="a2"), Element(role="AXButton", name="Run Script"), True),
        (Step(action="click", x=5, y=5), Element(role="AXButton", name="Play"), True),
        (Step(action="menu", path=["Run", "Start Debugging"]), None, True),
        (Step(action="menu", path=["Script", "Çalıştır"]), None, True),
        (Step(action="type", text="def f(): return 1"), None, False),
        (Step(action="key", key="cmd+s"), None, False),
        (Step(action="key", key="tab"), None, False),
        (Step(action="press", ref="a2"), Element(role="AXButton", name="Explorer"), False),
        (Step(action="menu", path=["File", "Save"]), None, False),
    ],
)
def test_the_step_that_runs_what_is_written_in_an_editor_or_a_script_application_asks(
    step: Step, element: Element | None, asks: bool, tmp_path: Path
) -> None:
    """The audit's F9: typing a line and a line break into the terminal of VS Code, or cmd+R in Script Editor, ran a
    command with no question, while code typed there is no shell line and does not ask."""
    for name, bundle in (("Code", "com.microsoft.VSCode"), ("Script Editor", "com.apple.ScriptEditor2")):
        context: StepContext = _context(app_role(name, bundle), name, element)
        assessment: StepAssessment = _assess(step, context, tmp_path)
        assert reaches_a_shell(step, context) is asks, (name, assessment)
        assert ("privilege" in assessment.flags) is asks, (name, assessment)
        assert (_verdict(assessment, "shell") == "ask") is asks, (name, assessment)


def test_return_presses_the_button_that_has_the_keyboard_and_the_one_the_window_defaults_to(tmp_path: Path) -> None:
    """The second audit's finding: Return or Space on a focused Pay, Send or Delete was judged as a key and nothing
    more."""
    other: AppRole = "other"
    pay: Element = Element(role="AXButton", name="Pay €49.00")
    delete: Element = Element(role="AXCheckBox", name="Delete my account")
    field: Element = Element(role="AXTextField", name="Send to")
    judged: dict[str, StepAssessment] = {
        "return on pay": _assess(Step(action="key", key="return"), _context(other, "Shop", pay), tmp_path),
        "enter on pay": _assess(Step(action="key", key="enter"), _context(other, "Shop", pay), tmp_path),
        "space on delete": _assess(Step(action="key", key="space"), _context(other, "Shop", delete), tmp_path),
        "return in a field": _assess(Step(action="key", key="return"), _context(other, "Shop", field), tmp_path),
        "default pay": _assess(
            Step(action="key", key="return"), _context(other, "Shop", field, default_button=pay), tmp_path
        ),
        "default send": _assess(
            Step(action="key", key="return"),
            _context(other, "Mail Helper", field, default_button=Element(role="AXButton", name="Send")),
            tmp_path,
        ),
        "typed line break": _assess(
            Step(action="type", text="4111\n"), _context(other, "Shop", field, default_button=pay), tmp_path
        ),
        "shift return": _assess(
            Step(action="key", key="shift+return"), _context(other, "Shop", field, default_button=pay), tmp_path
        ),
        "return, no default": _assess(Step(action="key", key="return"), _context(other, "Shop", field), tmp_path),
        "text, no line break": _assess(
            Step(action="type", text="4111"), _context(other, "Shop", field, default_button=pay), tmp_path
        ),
    }
    assert "financial" in judged["return on pay"].flags and judged["return on pay"].risk == "high"
    assert "financial" in judged["enter on pay"].flags
    assert "destructive" in judged["space on delete"].flags
    assert "financial" in judged["default pay"].flags
    assert {"external_send", "message_send"} <= set(judged["default send"].flags)
    assert "financial" in judged["typed line break"].flags
    for quiet in ("return in a field", "shift return", "return, no default", "text, no line break"):
        assert judged[quiet].flags == (), quiet


@pytest.mark.parametrize(
    ("step", "line", "shown"),
    [
        (Step(action="type", text="ls -la\n"), "", "ls -la"),
        (Step(action="type", text="rm -rf ~/Projects\n"), None, "rm -rf ~/Projects"),
        (Step(action="type", text="two\nlines\n"), "echo ", "echo two\nlines"),
        (Step(action="key", key="return"), "echo hi", "echo hi"),
        (Step(action="key", key="return"), None, None),
        (Step(action="type", text="not run"), "", None),
        (Step(action="key", key="escape"), "ls", UNREAD_LINE),
        (Step(action="key", key="cmd+t"), "ls", None),
        (Step(action="press", ref="a1"), "ls", None),
        (Step(action="type", text=f"export KEY={_KEY}\n"), "", "export KEY=***"),
    ],
)
def test_the_card_of_a_terminal_step_shows_the_command_lines_it_runs(
    step: Step, line: str | None, shown: str | None
) -> None:
    """The audit's F91: 'Terminal: type (17 chars)' told the owner nothing of what the question was about."""
    assert shell_input(step, _terminal(line=line)) == shown


def test_the_card_of_a_step_in_an_editor_shows_the_text_typed_there_and_none_of_another_application() -> None:
    editor: StepContext = _context("shell_host", "Code")
    assert shell_input(Step(action="type", text="make test\n"), editor) == "make test\n".strip("\n") + "\n"
    assert shell_input(Step(action="key", key="return"), editor) is None
    assert shell_input(Step(action="type", text="make test\n"), _context("other", "TextEdit")) is None


def _fake_context(contexts: tuple[StepContext, ...], authorized: list[ActionInfo]) -> ToolContext:
    config = load_default_config()

    async def preview(thread_id: str, app: str | None, steps: list[Step]) -> tuple[StepContext, ...]:
        return contexts

    async def authorize(info: ActionInfo) -> str:
        authorized.append(info)
        return ""

    desktop = SimpleNamespace(preview=preview)
    runtime = SimpleNamespace(
        config=config,
        desktop=desktop,
        protected=(),
        dot_paths=(),
        peers=SimpleNamespace(take_app=lambda run_id, app: None),
    )
    return cast(
        ToolContext,
        SimpleNamespace(
            runtime=runtime,
            thread_id="thread",
            run_id="run",
            workspace=Path("/nonexistent-workspace"),
            approvals="ask",
            output_chars=20_000,
            gate=SimpleNamespace(authorize=authorize),
        ),
    )


async def test_the_card_of_a_batch_in_terminal_shows_its_command_and_what_the_judgement_found(tmp_path: Path) -> None:
    tool = next(entry for entry in build_desktop_tools() if entry.spec.name == DESKTOP_ACT_TOOL)
    steps: list[dict[str, str]] = [{"action": "type", "text": "rm -rf ~/Projects\n"}]
    ctx: ToolContext = _fake_context((_terminal(line=""),), [])
    prepared = await tool.prepare({"steps": steps}, ctx)
    action: ActionInfo = prepared.action
    assert action.kind == "shell"
    assert action.target == "rm -rf ~/Projects"
    assert action.summary.startswith("Terminal: type (18 chars)")
    assert "terminal input" in action.summary
    assert {"destructive", "outside_workspace"} <= set(action.flags)


async def test_the_card_of_a_step_asked_again_shows_the_line_the_return_runs(tmp_path: Path) -> None:
    """The step that turns out riskier than what was approved goes back to the gate with the line it runs."""
    authorized: list[ActionInfo] = []
    reviews: list[StepReview] = []

    async def act(thread_id: str, app: str | None, steps: list[Step], review, secure_field, budget: int) -> ActOutcome:
        context: StepContext = _terminal(Element(role="AXTextArea", name="shell"), line="rm -rf ~/Projects")
        step: Step = Step(action="key", key="return")
        await review(StepReview(index=1, step=step, description="key return", context=context, target="textarea"))
        reviews.append(StepReview(index=1, step=step, description="", context=context, target=""))
        return ActOutcome(steps=(), stopped=None, view=None, view_note="none")

    ctx: ToolContext = _fake_context((_terminal(),), authorized)
    cast(SimpleNamespace, ctx.runtime.desktop).act = act
    tool = next(entry for entry in build_desktop_tools() if entry.spec.name == DESKTOP_ACT_TOOL)
    # What the owner approved: a harmless click, kind desktop_input.
    prepared = await tool.prepare({"steps": [{"action": "hover", "x": 5, "y": 5}]}, ctx)
    assert prepared.action.kind == "desktop_input"
    await prepared.run(ctx)
    assert len(authorized) == 1
    asked: ActionInfo = authorized[0]
    assert asked.kind == "shell"
    assert asked.target == "rm -rf ~/Projects"
    assert asked.summary.startswith("Terminal: key return")
    assert "terminal input" in asked.summary
    assert {"destructive", "outside_workspace"} <= set(asked.flags)
