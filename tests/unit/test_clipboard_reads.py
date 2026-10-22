"""A read of the clipboard asks in every preset (item 534): it can hold a password the user has just copied from their
password manager, which a run's tool, its shell (``pbpaste``) or an AppleScript it runs (``the clipboard``) would
hand to the model. A write to the clipboard, which hands nothing, stays as it was."""

from pathlib import Path
from typing import cast

import pytest

from dotagent.config import load_default_config
from dotagent.policy.engine import decide, preset_verdict
from dotagent.policy.paths import expand_protected
from dotagent.policy.shell import ShellAssessment, assess_applescript, assess_shell
from dotagent.policy.types import ActionInfo, Preset, Verdict
from dotagent.tools.base import PreparedCall
from dotagent.tools.context import ToolContext
from dotagent.tools.desktop import _clipboard_read_tool

_PRESETS: tuple[Preset, ...] = ("aggressive", "balanced", "cautious")


def _verdicts(command: str, workspace: Path) -> tuple[ShellAssessment, list[Verdict]]:
    protected: tuple[Path, ...] = expand_protected(load_default_config().policy.refused_paths, ())
    assessment: ShellAssessment = assess_shell(command, workspace, workspace, protected, ())
    info: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk=assessment.risk, summary=command, target=command, flags=assessment.flags
    )
    return assessment, [decide(info, "task", False, preset, [], None).verdict for preset in _PRESETS]


@pytest.mark.parametrize(
    "command",
    [
        "pbpaste",
        "pbpaste | head -c 8",
        "secret=$(pbpaste); echo done",
        "osascript -e 'the clipboard'",
        "osascript -e 'get the clipboard as text'",
        "osascript -e 'return (the clipboard)'",
        "osascript -e 'clipboard info'",
        # An assignment is a read as well: ``copy x to y`` copies the clipboard, and ``set y to`` takes it.
        "osascript -e 'copy the clipboard to c' -e 'return c'",
        "osascript -e 'set c to the clipboard' -e 'return c'",
        "osascript -e 'set the clipboard to (the clipboard & \"x\")'",
        # JXA, by its own method and by the pasteboard of the Objective-C bridge.
        "osascript -l JavaScript -e 'Application.currentApplication().theClipboard()'",
        "osascript -l JavaScript -e 'var app = Application.currentApplication(); app.includeStandardAdditions = true;"
        " app.clipboardInfo()'",
        'osascript -l JavaScript -e \'ObjC.import("AppKit"); $.NSPasteboard.generalPasteboard.stringForType('
        "$.NSPasteboardTypeString)'",
    ],
)
def test_a_read_of_the_clipboard_by_the_shell_asks_in_every_preset(command: str, tmp_path: Path) -> None:
    assessment, verdicts = _verdicts(command, tmp_path)
    assert verdicts == ["ask", "ask", "ask"], (command, assessment)
    assert "protected_read" in assessment.flags and "destructive" not in assessment.flags


@pytest.mark.parametrize(
    "command",
    [
        "pbcopy < notes.txt",
        "echo hi | pbcopy",
        "osascript -e 'set the clipboard to \"x\"'",
        "osascript -e 'copy \"x\" to the clipboard'",
        'osascript -e \'tell application "System Events" to set the clipboard to "x"\'',
        "osascript -l JavaScript -e 'Application.currentApplication().setTheClipboardTo(\"x\")'",
        "echo the clipboard",
    ],
)
def test_a_write_to_the_clipboard_asks_nothing_more_than_before(command: str, tmp_path: Path) -> None:
    assessment, _ = _verdicts(command, tmp_path)
    assert "protected_read" not in assessment.flags, (command, assessment)


async def test_the_desktop_tool_that_reads_the_clipboard_asks_in_every_preset() -> None:
    prepared: PreparedCall = await _clipboard_read_tool().prepare({}, cast(ToolContext, None))
    action: ActionInfo = prepared.action
    assert (action.tool, action.kind, action.flags) == (
        "desktop_clipboard_read",
        "desktop_observe",
        ("protected_read",),
    )
    assert [preset_verdict(preset, action)[0] for preset in _PRESETS] == ["ask", "ask", "ask"]


@pytest.mark.parametrize(
    "script",
    [
        "the clipboard",
        "get the clipboard as text",
        "copy the clipboard to c\nreturn c",
        "set c to the clipboard\nreturn c",
        "clipboard info",
        'set the clipboard to (the clipboard & "x")',
        "Application.currentApplication().theClipboard()",
        "app.clipboardInfo()",
        "ObjC.import('AppKit'); $.NSPasteboard.generalPasteboard.stringForType($.NSPasteboardTypeString)",
    ],
)
def test_a_script_of_the_apple_script_tool_that_reads_the_clipboard_asks_in_every_preset(
    script: str, tmp_path: Path
) -> None:
    protected: tuple[Path, ...] = expand_protected(load_default_config().policy.refused_paths, ())
    assessment: ShellAssessment = assess_applescript(script, tmp_path, tmp_path, protected, ())
    info: ActionInfo = ActionInfo(
        tool="apple_script",
        kind="app_control",
        risk=assessment.risk,
        summary=script,
        target=script,
        flags=assessment.flags,
    )
    assert "protected_read" in assessment.flags, (script, assessment)
    assert [preset_verdict(preset, info)[0] for preset in _PRESETS] == ["ask", "ask", "ask"], (script, assessment)


@pytest.mark.parametrize(
    "script",
    [
        'set the clipboard to "x"',
        'copy "x" to the clipboard',
        'tell application "System Events" to set the clipboard to "x"',
        "Application.currentApplication().setTheClipboardTo('x')",
    ],
)
def test_a_script_that_only_puts_text_on_the_clipboard_asks_nothing_more_than_before(
    script: str, tmp_path: Path
) -> None:
    protected: tuple[Path, ...] = expand_protected(load_default_config().policy.refused_paths, ())
    assessment: ShellAssessment = assess_applescript(script, tmp_path, tmp_path, protected, ())
    assert "protected_read" not in assessment.flags, (script, assessment)
