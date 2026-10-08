"""Pure desktop safety rules: what kind of application a step acts in, and how much damage the step can do.

Names of applications and controls come from the machine and from third parties, so they can only make a step look
riskier than the action alone, never safer. The policy engine decides what the resulting risk and flags mean.
"""

import re
from collections.abc import Collection, Sequence
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
from dotagent.policy.shell import ShellAssessment, assess_shell
from dotagent.policy.types import Flag, Risk

type AppRole = Literal["terminal", "system_settings", "secrets", "messaging", "finder", "other"]

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

_SECRETS_ASSESSMENT: StepAssessment = StepAssessment(
    risk="high", flags=("secret_access",), reasons=("works inside a password manager or the keychain",)
)
_SETTINGS_ASSESSMENT: StepAssessment = StepAssessment(
    risk="high", flags=("system_settings",), reasons=("changes system settings",)
)


class StepContext(FrozenModel):
    """What a step acts upon, read from the machine right before the step: the application, the control, its secrecy."""

    role: AppRole
    app: str
    element: Element | None
    secure: bool


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
    if bundle in _SETTINGS_BUNDLES:
        return "system_settings"
    if bundle in _TERMINAL_BUNDLES or _key(name) in _TERMINAL_NAMES:
        return "terminal"
    if bundle in _MESSAGING_BUNDLES:
        return "messaging"
    if bundle == _FINDER_BUNDLE:
        return "finder"
    return "other"


def hidden_regions(windows: Sequence[WindowInfo]) -> tuple[Rect, ...]:
    """Where password managers and the keychain show on screen; screenshots black these out."""
    return tuple(window.bounds for window in windows if window.on_screen and is_secrets_app(window.owner, None))


def types_a_character(chord: Chord) -> bool:
    """Whether the chord enters a character into a text field (a shortcut with cmd or ctrl does not)."""
    return len(chord.key) == 1 and not ({"cmd", "ctrl"} & set(chord.modifiers))


def reaches_a_shell(step: Step, context: StepContext) -> bool:
    """Whether a step puts text or Return into a terminal, which makes it a shell command."""
    if context.role != "terminal":
        return False
    if step.action in ("type", "paste"):
        return True
    return step.action == "key" and step.key is not None and parse_chord(step.key).key == "return"


def exceeds(live: StepAssessment, risk: Risk, flags: Collection[Flag]) -> bool:
    """Whether a step is riskier than what was authorised: a higher risk, or a flag that was not part of the approval."""
    return _RISK_ORDER.index(live.risk) > _RISK_ORDER.index(risk) or any(flag not in flags for flag in live.flags)


def _terminal_text(text: str, workspace: Path, protected: tuple[Path, ...]) -> StepAssessment:
    """Text typed into a terminal is a shell command the moment Return follows, so it is judged like one."""
    shell: ShellAssessment = assess_shell(text, Path.home(), workspace, protected)
    risk: Risk = max((shell.risk, _SHELL_FLOOR), key=_RISK_ORDER.index)
    return StepAssessment(
        risk=risk, flags=shell.flags, reasons=tuple(f"terminal input: {reason}" for reason in shell.reasons)
    )


def _typing_parts(
    step: Step, context: StepContext, workspace: Path, protected: tuple[Path, ...]
) -> list[StepAssessment]:
    reasons: list[str] = typing_reasons(context.element, step.text)
    if context.secure and "credential" not in reasons:
        reasons.append("credential")
    parts: list[StepAssessment] = [assessment_for(reasons)]
    text: str = step.text or ""
    if context.role == "terminal" and step.action != "set_value":
        parts.append(_terminal_text(text, workspace, protected))
    if context.role == "messaging" and step.action == "type" and "\n" in text:
        parts.append(StepAssessment(risk="medium", flags=("external_send",), reasons=("a newline sends the message",)))
    return parts


def _chord_parts(chord: Chord, context: StepContext) -> list[StepAssessment]:
    modifiers: frozenset[str] = frozenset(chord.modifiers)
    parts: list[StepAssessment] = []
    if types_a_character(chord):
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
    if chord.key == "return" and context.role == "terminal" and not modifiers:
        parts.append(StepAssessment(risk="medium", flags=(), reasons=("runs the command line in a terminal",)))
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


def assess_step(step: Step, context: StepContext, workspace: Path, protected: tuple[Path, ...]) -> StepAssessment:
    """Risk and flags of one step, judged from the action, the kind of application and the control it targets."""
    if context.role == "secrets" and step.action != "wait":
        return _SECRETS_ASSESSMENT
    parts: list[StepAssessment] = [quiet_assessment() if step.action in _QUIET_ACTIONS else assessment_for([])]
    if context.role == "system_settings" and step.has_side_effects():
        parts.append(_SETTINGS_ASSESSMENT)
    if step.action in _TEXT_ACTIONS:
        parts.extend(_typing_parts(step, context, workspace, protected))
    if step.action in _CLICK_ACTIONS:
        parts.append(assessment_for(clicking_reasons(context.element, DESKTOP_CLICKABLE_ROLES)))
    if step.action == "key" and step.key is not None:
        parts.extend(_chord_parts(parse_chord(step.key), context))
    if step.action == "menu" and step.path:
        parts.extend(_menu_parts(step.path))
    return combine_assessments(parts)
