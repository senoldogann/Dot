"""A read of the clipboard asks in every preset (item 534): it can hold a password the user has just copied from their
password manager, which a run's tool, its shell (``pbpaste``) or a script it runs (AppleScript's ``the clipboard``,
JXA's ``theClipboard()``, the pasteboard of the Objective-C bridge) would hand to the model. A script that only puts
text on the clipboard is not told from one that reads it, so it asks too; ``pbcopy`` and the tool that writes hand
nothing and stay as they were."""

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
from dotagent.tools.desktop import ClipboardWriteArgs, _clipboard_read_tool, _clipboard_write_tool

_PRESETS: tuple[Preset, ...] = ("aggressive", "balanced", "cautious")
# Scripts that name the clipboard, in every form the reviews found: the words, a copy and an assignment, a `copy` in a
# string or a comment before the read, a write that hides nothing, the raw events, and JXA's names however they are
# called.
_NAMING_IT: list[str] = [
    "the clipboard",
    "get the clipboard as text",
    "return (the clipboard)",
    "clipboard info",
    "copy the clipboard to c\nreturn c",
    "set c to the clipboard\nreturn c",
    'set the clipboard to (the clipboard & "x")',
    'return "copy: " & (the clipboard) & " goes to the clipboard"',
    'if "copy" is not "" then set c to the clipboard',
    "(* copy *) set c to the clipboard",
    "set c to the ¬\n clipboard",
    'set the clipboard to "x"',
    'copy "x" to the clipboard',
    'tell application "System Events" to set the clipboard to "x"',
    "return «event JonsgClp»",
    "return «event JonsiClp»",
    "return <<event JonsgClp>>",
    "return <<event JonsiClp>>",
    '«event JonspClp» "x"',
    "Application.currentApplication().theClipboard()",
    "(app.theClipboard)()",
    "var c = app.theClipboard; c.call(app)",
    "app.theClipboard.call(app)",
    "app.theClipboard/**/()",
    "app.clipboardInfo()",
    "app.clipboardInfo",
    "Application.currentApplication().setTheClipboardTo('x')",
    "ObjC.import('AppKit'); $.NSPasteboard.generalPasteboard.stringForType($.NSPasteboardTypeString)",
    # The command that reads it, however a script runs it, and the names a JXA script writes with escapes
    # or in Carbon's.
    'do shell script "pbpaste"',
    '«event sysoexec» "pbpaste"',
    "var a = Application.currentApplication(); (a.doShellScript)('pbpaste')",
    "a['doShellScript']('pbpaste')",
    "a.doShellScript.call(a, 'pbpaste')",
    "ObjC.import('stdlib'); $.system('pbpaste')",
    "a['\\x74heClipboard']()",
    "a.the\\u0043lipboard()",
    "a.the\\u{43}lipboard()",
    "ObjC.import('ApplicationServices'); $.PasteboardCreate($('com.apple.pasteboard.clipboard'), r)",
]


# What the security review of the branch found, each a line a run could use and that passed in the aggressive
# preset: the command in another letter case (this Mac's file system does not tell them apart) or behind a
# wrapper the guard does not look through, and osascript given its script by a pipe, a here-document, a
# variable or a glued -e.
_FOUND_BY_THE_REVIEW: list[str] = [
    "PBPASTE",
    "/usr/bin/PbPaste | head -c 8",
    "x=$(PBPASTE)",
    "bash -c 'PBPASTE'",
    "OSASCRIPT -e 'the clipboard'",
    "/usr/bin/time -l pbpaste",
    "arch -arm64e pbpaste",
    "lockf -t 1 f pbpaste",
    "sandbox-exec -n no-network pbpaste",
    "tar -cf - . -I pbpaste",
    "rsync -e pbpaste a b",
    "ssh -o ProxyCommand=pbpaste h",
    "osascript -e'the clipboard'",
    "osascript - <<< 'the clipboard'",
    "echo 'the clipboard' | osascript -",
    "cat <<'EOF' | osascript\nthe clipboard\nEOF",
    "cat <<< 'the clipboard' | osascript",
    "echo 'the\\x20clipboard' | osascript",
    "x='the clipboard'; osascript -e \"$x\"",
    "osascript -e \"$(echo 'the clipboard')\"",
    "echo 'the clipboard' | xargs -0 osascript -e",
    'osascript -l JavaScript -e "Application.currentApplication().theClipboard()"',
    # Inline code in another language names the pasteboard's class.
    "python3 -c 'from AppKit import NSPasteboard; print(NSPasteboard.generalPasteboard())'",
    "swift -e 'import AppKit; print(NSPasteboard.general.string(forType: .string) ?? \"\")'",
    "ruby -e 'require \"osx/cocoa\"; puts OSX::NSPasteboard.generalPasteboard'",
]


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
        "/usr/bin/pbpaste",
        "env pbpaste",
        "osascript -e 'do shell script \"pbpaste\"'",
        "osascript -e 'the clipboard'",
        "osascript -e 'copy the clipboard to c' -e 'return c'",
        "osascript -l JavaScript -e 'var app = Application.currentApplication(); (app.theClipboard)()'",
        "osascript -e 'return «event JonsgClp»'",
    ],
)
def test_a_read_of_the_clipboard_by_the_shell_asks_in_every_preset(command: str, tmp_path: Path) -> None:
    assessment, verdicts = _verdicts(command, tmp_path)
    assert verdicts == ["ask", "ask", "ask"], (command, assessment)
    assert "protected_read" in assessment.flags and "destructive" not in assessment.flags


@pytest.mark.parametrize("command", _FOUND_BY_THE_REVIEW)
def test_what_the_security_review_found_asks_in_every_preset(command: str, tmp_path: Path) -> None:
    assessment, verdicts = _verdicts(command, tmp_path)
    assert verdicts == ["ask", "ask", "ask"], (command, assessment)
    assert "protected_read" in assessment.flags and "destructive" not in assessment.flags


@pytest.mark.parametrize("script", _NAMING_IT)
def test_a_script_of_a_shell_line_that_names_the_clipboard_asks_in_every_preset(script: str, tmp_path: Path) -> None:
    command: str = "osascript -e '" + script.replace("'", "'\\''") + "'"
    assessment, verdicts = _verdicts(command, tmp_path)
    assert verdicts == ["ask", "ask", "ask"], (command, assessment)


@pytest.mark.parametrize("script", _NAMING_IT)
def test_a_script_of_the_apple_script_tool_that_names_the_clipboard_asks_in_every_preset(
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
    "command",
    ["pbcopy < notes.txt", "echo hi | pbcopy", "echo the clipboard", "osascript -e 'display dialog \"hello\"'"],
)
def test_what_names_no_clipboard_or_only_writes_by_a_program_asks_nothing_more_than_before(
    command: str, tmp_path: Path
) -> None:
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


async def test_the_desktop_tool_that_writes_the_clipboard_asks_nothing_more_than_before() -> None:
    prepared: PreparedCall = await _clipboard_write_tool().prepare(
        ClipboardWriteArgs(text="hello").model_dump(), cast(ToolContext, None)
    )
    assert "protected_read" not in prepared.action.flags
    assert [preset_verdict(preset, prepared.action)[0] for preset in ("aggressive", "balanced")] == ["allow", "allow"]
