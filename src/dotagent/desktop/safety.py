"""Pure desktop safety rules: what kind of application a step acts in, and how much damage the step can do.

Names of applications and controls come from the machine and from third parties, so they can only make a step look
riskier than the action alone, never safer. The policy engine decides what the resulting risk and flags mean.
"""

import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Literal

from dotagent.core.types import FrozenModel
from dotagent.desktop.input import Chord, parse_chord
from dotagent.desktop.types import Rect, Step, WindowInfo
from dotagent.policy.elements import (
    Element,
    StepAssessment,
    assessment_for,
    clicking_reasons,
    combine_assessments,
    quiet_assessment,
    typing_reasons,
)
from dotagent.policy.shell import ShellAssessment, assess_typed, typed_lines
from dotagent.policy.types import Flag, Risk

type AppRole = Literal["terminal", "system_settings", "secrets", "dot", "chrome_grant", "messaging", "finder", "other"]

DESKTOP_CLICKABLE_ROLES: frozenset[str] = frozenset(
    {
        "AXButton",
        "AXMenuItem",
        "AXMenuBarItem",
        "AXLink",
        "AXCheckBox",
        "AXRadioButton",
        "AXPopUpButton",
        "AXMenuButton",
        "AXDisclosureTriangle",
    }
)

_RISK_ORDER: tuple[Risk, ...] = ("safe", "low", "medium", "high")
_QUIET_ACTIONS: frozenset[str] = frozenset({"hover", "scroll", "wait", "focus"})
_CLICK_ACTIONS: frozenset[str] = frozenset({"click", "double_click", "right_click", "press", "drag"})
_TEXT_ACTIONS: frozenset[str] = frozenset({"type", "paste", "set_value"})
_TERMINAL_BUNDLES: frozenset[str] = frozenset(
    {
        "com.apple.terminal",
        "com.googlecode.iterm2",
        "dev.warp.warp-stable",
        "com.mitchellh.ghostty",
        "net.kovidgoyal.kitty",
        "org.alacritty",
        "io.alacritty",
        "com.github.wez.wezterm",
        "co.zeit.hyper",
        "com.raphaelamorim.rio",
        "org.tabby",
    }
)
_TERMINAL_NAMES: frozenset[str] = frozenset(
    {"terminal", "iterm2", "iterm", "warp", "ghostty", "kitty", "alacritty", "wezterm", "hyper", "tabby", "rio"}
)
_SECRETS_BUNDLES: frozenset[str] = frozenset(
    {
        "com.apple.keychainaccess",
        "com.apple.passwords",
        "com.1password.1password",
        "com.1password.1password7",
        "com.agilebits.onepassword7",
        "com.agilebits.onepassword-osx",
        "com.bitwarden.desktop",
        "org.keepassxc.keepassxc",
        "com.lastpass.lastpass",
        "com.dashlane.dashlanephonefinal",
        "in.sinew.enpass-desktop",
        "me.proton.pass.electron",
        "com.nordsec.nordpass",
    }
)
_SECRETS_NAME_PARTS: tuple[str, ...] = (
    "1password",
    "bitwarden",
    "keepass",
    "lastpass",
    "dashlane",
    "enpass",
    "nordpass",
    "proton pass",
    "keychain access",
    "anahtar zinciri",
    "parolalar",
)
_SECRETS_NAMES: frozenset[str] = frozenset({"passwords"})
_SETTINGS_BUNDLES: frozenset[str] = frozenset({"com.apple.systempreferences"})
# Dot's own desktop shell (``identifier`` of desktop/src-tauri/tauri.conf.json): its window shows the approvals, the
# rules and the settings that decide what a run may do.
_DOT_BUNDLE: str = "dev.dot.desktop"
_DOT_NAME: str = "dot"
# The title Dot's web UI gives its page ("Dot", "<page> · Dot", "(2) Dot" while two things wait for the user), as a
# window shows it: a browser may put its own name or the profile's after a dash.
_DOT_TITLE: re.Pattern[str] = re.compile(r"(?:\(\d+\) )?(?:.* · )?Dot(?: [-–—] .*)?")
# Where Chrome lets a program take control of it (item 343): the remote-debugging switch of chrome://inspect ("Allow
# remote debugging for this browser instance", on the page "Inspect with Chrome Developer Tools") and the Allow Chrome
# asks before a connection. Both are the owner's to give, so a run never works there, in any mode.
_CHROME_BUNDLE: str = "com.google.chrome"
_CHROME_GRANT: re.Pattern[str] = re.compile(r"remote debugging|inspect with chrome developer tools", re.IGNORECASE)
_MESSAGING_BUNDLES: frozenset[str] = frozenset(
    {
        "com.apple.mobilesms",
        "com.apple.mail",
        "com.tinyspeck.slackmacgap",
        "com.hnc.discord",
        "ru.keepcoder.telegram",
        "org.telegram.desktop",
        "com.tdesktop.telegram",
        "net.whatsapp.whatsapp",
        "desktop.whatsapp",
        "org.whispersystems.signal-desktop",
        "com.microsoft.teams2",
        "com.microsoft.teams",
        "com.microsoft.outlook",
        "com.readdle.smartemail-mac",
        "im.riot.app",
        "com.skype.skype",
    }
)
_FINDER_BUNDLE: str = "com.apple.finder"
_APPLE_MENU: str = "apple"
_QUIT_ITEM: re.Pattern[str] = re.compile(r"(?i)\bquit\b|\b[çc][ıi]k(?:\s|$)")
_SEND_KEYS: frozenset[frozenset[str]] = frozenset({frozenset(), frozenset({"cmd"})})
_MAIL_SEND_MODIFIERS: frozenset[str] = frozenset({"cmd", "shift"})
_SHELL_FLOOR: Risk = "medium"
# What a key sends to the program in a terminal, as text; Escape stands for any other key a line editor acts upon.
_SENT_KEYS: dict[str, str] = {"return": "\n", "delete": "\x7f", "space": " ", "tab": "\t"}
_ESCAPE: str = "\x1b"
_LINE_KEEPING_ACTIONS: frozenset[str] = frozenset({"hover", "scroll", "wait", "activate"})

_SECRETS_ASSESSMENT: StepAssessment = StepAssessment(
    risk="high", flags=("secret_access",), reasons=("works inside a password manager or the keychain",)
)
_SETTINGS_ASSESSMENT: StepAssessment = StepAssessment(
    risk="high", flags=("system_settings",), reasons=("changes system settings",)
)
_CHROME_GRANT_ASSESSMENT: StepAssessment = StepAssessment(
    risk="high",
    flags=("secret_access",),
    reasons=("works on Chrome's remote debugging, which only the owner switches on and allows",),
)
_DOT_ASSESSMENT: StepAssessment = StepAssessment(
    risk="high",
    flags=("secret_access",),
    reasons=("works in Dot's own window, where its approvals, rules and settings are",),
)


class StepContext(FrozenModel):
    """What a step acts upon, read from the machine right before the step: the application, the control, its secrecy,
    and in a terminal the command line as far as Dot typed it since the line was last empty (``None`` when that cannot
    be told, and outside a terminal)."""

    role: AppRole
    app: str
    element: Element | None
    secure: bool
    line: str | None


def _key(text: str) -> str:
    return text.strip().casefold()


def is_secrets_app(name: str, bundle_id: str | None) -> bool:
    """Whether the application holds passwords or keys: password managers and the keychain."""
    folded: str = _key(name)
    return (
        _key(bundle_id or "") in _SECRETS_BUNDLES
        or folded in _SECRETS_NAMES
        or any(part in folded for part in _SECRETS_NAME_PARTS)
    )


def app_role(name: str, bundle_id: str | None) -> AppRole:
    """The kind of application, which decides what typing or pressing Return in it means."""
    bundle: str = _key(bundle_id or "")
    if is_secrets_app(name, bundle_id):
        return "secrets"
    if bundle == _DOT_BUNDLE or _key(name) == _DOT_NAME:
        return "dot"
    if bundle in _SETTINGS_BUNDLES:
        return "system_settings"
    if bundle in _TERMINAL_BUNDLES or _key(name) in _TERMINAL_NAMES:
        return "terminal"
    if bundle in _MESSAGING_BUNDLES:
        return "messaging"
    if bundle == _FINDER_BUNDLE:
        return "finder"
    return "other"


def window_role(name: str, bundle_id: str | None, title: str | None) -> AppRole:
    """The kind of window a step acts in: that of its application (:func:`app_role`), or Dot's own when the window of
    any other application shows Dot's web UI (a browser, an installed web app), which holds the same approvals, rules
    and settings as the desktop app. A page that only takes such a title is refused as well: a refusal is the safe
    side of a guess."""
    role: AppRole = app_role(name, bundle_id)
    if role == "other" and title is not None and _DOT_TITLE.fullmatch(title) is not None:
        return "dot"
    return role


def step_role(name: str, bundle_id: str | None, title: str | None, label: str | None) -> AppRole:
    """The kind of window a step acts in (:func:`window_role`), or Chrome's grant of remote debugging when the step is
    on a Chrome window or control that is about it (item 343): what an error tells the owner to do there ("click
    Allow", "switch it on") is not the run's to do."""
    if _key(bundle_id or "").startswith(_CHROME_BUNDLE) and any(
        text is not None and _CHROME_GRANT.search(text) is not None for text in (title, label)
    ):
        return "chrome_grant"
    return window_role(name, bundle_id, title)


def hidden_regions(windows: Sequence[WindowInfo]) -> tuple[Rect, ...]:
    """Where password managers and the keychain show on screen; screenshots black these out."""
    return tuple(window.bounds for window in windows if window.on_screen and is_secrets_app(window.owner, None))


def enters_text(chord: Chord) -> bool:
    """Whether the chord puts text into a field: a character (a shortcut with cmd or ctrl does not), or the clipboard
    pasted with cmd+v."""
    modifiers: set[str] = set(chord.modifiers)
    return (len(chord.key) == 1 and not ({"cmd", "ctrl"} & modifiers)) or (chord.key == "v" and "cmd" in modifiers)


def reaches_a_shell(step: Step, context: StepContext) -> bool:
    """Whether a step puts text or keys into a terminal, where Return or a Ctrl- shortcut runs the line."""
    return context.role == "terminal" and step.action in ("type", "paste", "key")


def _keys_sent(chord: Chord) -> str | None:
    """What a chord sends to the program in a terminal: Return, Delete, Space and Tab as their characters, a Ctrl-
    letter as its control character, Escape for any other key a line editor acts upon (an arrow, a function key, an
    Option or Shift combination, Cmd-V and its clipboard). ``None`` for a chord whose text depends on the keyboard
    layout or that the terminal application keeps (another Cmd shortcut may open or switch a tab): the line is then
    unknown, and nothing ran."""
    modifiers: frozenset[str] = frozenset(chord.modifiers)
    if "cmd" in modifiers:
        return _ESCAPE if chord.key == "v" else None
    if modifiers == {"ctrl"} and len(chord.key) == 1 and chord.key.isalpha():
        return chr(ord(chord.key) & 0x1F)
    if not modifiers and chord.key in _SENT_KEYS:
        return _SENT_KEYS[chord.key]
    if not modifiers and len(chord.key) == 1:
        return None
    return _ESCAPE


def _line_after(line: str | None, step: Step) -> str | None:
    """A terminal's command line after ``step`` in it; ``None`` once a click, focus, menu, drag or set_value may have
    switched tabs, pasted or replaced text."""
    if step.action in _LINE_KEEPING_ACTIONS:
        return line
    if step.action in ("type", "paste"):
        return typed_lines(line, step.text or "")[1]
    sent: str | None = _keys_sent(parse_chord(step.key)) if step.action == "key" and step.key is not None else None
    return None if sent is None else typed_lines(line, sent)[1]


def lines_after(lines: Mapping[str, str], step: Step, context: StepContext) -> dict[str, str]:
    """The command line of each terminal application, by name, as far as Dot typed it, after ``step``: one that cannot
    be told is left out. A drag may drop a path into any terminal."""
    if step.action == "drag":
        return {}
    if context.role != "terminal":
        return dict(lines)
    others: dict[str, str] = {app: line for app, line in lines.items() if app != context.app}
    after: str | None = _line_after(context.line, step)
    return others if after is None else {**others, context.app: after}


def exceeds(live: StepAssessment, risk: Risk, flags: Collection[Flag]) -> bool:
    """Whether a step is riskier than what was authorised: a higher risk, or a flag that was not part of the
    approval."""
    return _RISK_ORDER.index(live.risk) > _RISK_ORDER.index(risk) or any(flag not in flags for flag in live.flags)


def _terminal_input(
    line: str | None, sent: str, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> StepAssessment:
    """Keys sent to a terminal after ``line``: the lines they run are shell commands, judged whole with what Dot typed
    on them before, from the home folder, since the terminal may be anywhere."""
    lines, _ = typed_lines(line, sent)
    if not lines:
        return StepAssessment(risk="low", flags=(), reasons=("types on the command line of a terminal",))
    shell: ShellAssessment = assess_typed(lines, Path.home(), workspace, protected, dot_paths)
    risk: Risk = max((shell.risk, _SHELL_FLOOR), key=_RISK_ORDER.index)
    return StepAssessment(
        risk=risk, flags=shell.flags, reasons=tuple(f"terminal input: {reason}" for reason in shell.reasons)
    )


def _typing_parts(
    step: Step, context: StepContext, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> list[StepAssessment]:
    reasons: list[str] = typing_reasons(context.element, step.text)
    if context.secure and "credential" not in reasons:
        reasons.append("credential")
    parts: list[StepAssessment] = [assessment_for(reasons)]
    text: str = step.text or ""
    if context.role == "terminal" and step.action != "set_value":
        parts.append(_terminal_input(context.line, text, workspace, protected, dot_paths))
    if context.role == "messaging" and step.action == "type" and "\n" in text:
        parts.append(StepAssessment(risk="medium", flags=("external_send",), reasons=("a newline sends the message",)))
    return parts


def _chord_parts(
    chord: Chord, context: StepContext, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> list[StepAssessment]:
    modifiers: frozenset[str] = frozenset(chord.modifiers)
    parts: list[StepAssessment] = []
    sent: str | None = _keys_sent(chord) if context.role == "terminal" else None
    if sent is not None:
        parts.append(_terminal_input(context.line, sent, workspace, protected, dot_paths))
    if enters_text(chord):
        parts.append(assessment_for(typing_reasons(context.element, None) + (["credential"] if context.secure else [])))
    if chord.key == "q" and modifiers == {"cmd"}:
        parts.append(StepAssessment(risk="medium", flags=(), reasons=("quits the application",)))
    if chord.key == "q" and modifiers == {"cmd", "shift"}:
        parts.append(StepAssessment(risk="high", flags=("system_settings",), reasons=("logs the user out of the Mac",)))
    if chord.key == "q" and modifiers == {"cmd", "ctrl"}:
        parts.append(StepAssessment(risk="high", flags=("system_settings",), reasons=("locks the screen",)))
    if chord.key in ("delete", "forwarddelete") and "cmd" in modifiers and context.role in ("finder", "messaging"):
        empties: bool = "shift" in modifiers
        parts.append(
            StepAssessment(
                risk="high" if empties else "medium",
                flags=("destructive",),
                reasons=("empties the Trash" if empties else "moves the selection to the Trash or deletes it",),
            )
        )
    sends_with_return: bool = chord.key == "return" and modifiers in _SEND_KEYS
    sends_mail: bool = chord.key == "d" and modifiers == _MAIL_SEND_MODIFIERS
    if context.role == "messaging" and (sends_with_return or sends_mail):
        parts.append(StepAssessment(risk="medium", flags=("external_send",), reasons=("sends the message",)))
    return parts


def _menu_parts(path: Sequence[str]) -> list[StepAssessment]:
    parts: list[StepAssessment] = [
        assessment_for(clicking_reasons(Element(role="AXMenuItem", name=" ".join(path)), DESKTOP_CLICKABLE_ROLES))
    ]
    if _key(path[0]) == _APPLE_MENU:
        parts.append(_SETTINGS_ASSESSMENT)
    if _QUIT_ITEM.search(path[-1]) is not None:
        parts.append(StepAssessment(risk="medium", flags=(), reasons=("quits the application",)))
    return parts


def assess_step(
    step: Step, context: StepContext, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> StepAssessment:
    """Risk and flags of one step, judged from the action, the kind of application and the control it targets."""
    if context.role == "secrets" and step.action != "wait":
        return _SECRETS_ASSESSMENT
    if context.role == "dot" and step.action != "wait":
        return _DOT_ASSESSMENT
    if context.role == "chrome_grant" and step.action != "wait":
        return _CHROME_GRANT_ASSESSMENT
    parts: list[StepAssessment] = [quiet_assessment() if step.action in _QUIET_ACTIONS else assessment_for([])]
    if context.role == "system_settings" and step.has_side_effects():
        parts.append(_SETTINGS_ASSESSMENT)
    if step.action in _TEXT_ACTIONS:
        parts.extend(_typing_parts(step, context, workspace, protected, dot_paths))
    if step.action in _CLICK_ACTIONS:
        parts.append(assessment_for(clicking_reasons(context.element, DESKTOP_CLICKABLE_ROLES)))
    if step.action == "key" and step.key is not None:
        parts.extend(_chord_parts(parse_chord(step.key), context, workspace, protected, dot_paths))
    if step.action == "menu" and step.path:
        parts.extend(_menu_parts(step.path))
    return combine_assessments(parts)
