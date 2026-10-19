"""The desktop's secret checks that need no screen: a password manager is never controlled, nor Dot's own window outside
a yolo run (item 135), a paste is typing, a long typed text goes through the clipboard only into a known field that is
not secure, and an application named by its bundle's path anywhere but under /System is one a run may have made
(item 418)."""

import plistlib
from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import DesktopError
from dotagent.desktop import actions, apps
from dotagent.desktop import input as hid
from dotagent.desktop.actions import Inspection, StepEnv, _pastes, refuse_secrets
from dotagent.desktop.input import parse_chord
from dotagent.desktop.safety import (
    AppRole,
    StepContext,
    app_role,
    assess_step,
    enters_text,
    step_role,
    window_role,
)
from dotagent.desktop.types import AppInfo, Step
from dotagent.help.apps import app_identities
from dotagent.policy.elements import Element, StepAssessment
from dotagent.policy.engine import auto_review
from dotagent.policy.types import ActionInfo
from dotagent.tools.apple import secrets_reason
from dotagent.tools.desktop import _bundle_of, _on_the_sealed_system_volume


@pytest.mark.parametrize(
    ("chord", "enters"),
    [("a", True), ("shift+1", True), ("cmd+v", True), ("cmd+shift+v", True), ("cmd+c", False), ("return", False)],
)
def test_a_paste_puts_text_in_like_a_character(chord: str, enters: bool) -> None:
    assert enters_text(parse_chord(chord)) is enters


def test_a_step_at_a_password_manager_is_refused_and_one_elsewhere_is_not() -> None:
    with pytest.raises(DesktopError, match="never controlled"):
        refuse_secrets(StepContext(role="secrets", app="1Password", element=None, secure=False, line=None))
    refuse_secrets(StepContext(role="other", app="TextEdit", element=None, secure=True, line=None))


def test_a_long_typed_text_is_pasted_only_into_a_known_ordinary_field_and_never_a_key() -> None:
    field: StepContext = StepContext(
        role="other", app="TextEdit", element=Element(role="AXTextField", name="Name"), secure=False, line=None
    )
    long_text: str = "x" * 41
    assert _pastes(long_text, field, 40)
    assert not _pastes("x" * 40, field, 40)
    assert not _pastes(long_text, field, 0)
    assert not _pastes(long_text + "\n", field, 40)
    assert not _pastes(long_text + "\t", field, 40)
    assert not _pastes(long_text, field.model_copy(update={"secure": True}), 40)
    assert not _pastes(long_text, field.model_copy(update={"element": None}), 40)
    api_key: StepContext = field.model_copy(update={"element": Element(role="AXTextField", name="OpenAI API key")})
    assert not _pastes("sk-proj-" + "a" * 40, api_key, 40)
    for name in ("Recovery code", "Card number"):
        assert not _pastes(long_text, field.model_copy(update={"element": Element(role="AXTextField", name=name)}), 40)


def test_a_step_in_dots_own_window_is_refused_and_one_elsewhere_is_not(tmp_path: Path) -> None:
    approve: Element = Element(role="AXButton", name="Approve")
    for name, bundle, refused in (("Dot", "dev.dot.desktop", True), ("TextEdit", "com.apple.TextEdit", False)):
        context: StepContext = StepContext(
            role=app_role(name, bundle), app=name, element=approve, secure=False, line=None
        )
        assessment: StepAssessment = assess_step(Step(action="click", x=10, y=10), context, tmp_path, (), ())
        info: ActionInfo = ActionInfo(
            tool="desktop_act",
            kind="desktop_input",
            risk=assessment.risk,
            summary="click",
            target=name,
            flags=assessment.flags,
        )
        assert (auto_review(info) is not None) is refused, (name, assessment)


@pytest.mark.parametrize(
    ("name", "bundle", "title", "role"),
    [
        ("Google Chrome", "com.google.Chrome", "Dot", "dot"),
        ("Google Chrome", "com.google.Chrome", "Dot - Google Chrome - Work", "dot"),
        ("Google Chrome", "com.google.Chrome", "(2) Plan the trip · Dot", "dot"),
        ("Safari", "com.apple.Safari", "Settings · Dot", "dot"),
        ("Firefox", "org.mozilla.firefox", "(1) Dot — Mozilla Firefox", "dot"),
        ("Helium", "net.imput.helium", "Approvals · Dot - Helium", "dot"),
        ("Google Chrome", "com.google.Chrome", "Dotfiles - GitHub", "other"),
        ("Google Chrome", "com.google.Chrome", "Polka dot - Wikipedia", "other"),
        ("Google Chrome", "com.google.Chrome", None, "other"),
        ("Terminal", "com.apple.Terminal", "Dot — zsh — 80×24", "terminal"),
        ("Finder", "com.apple.finder", "Dot", "finder"),
    ],
)
def test_a_window_that_shows_dots_web_ui_is_dots_own(name: str, bundle: str, title: str | None, role: AppRole) -> None:
    assert window_role(name, bundle, title) == role


@pytest.mark.parametrize(
    ("title", "label", "refused"),
    [
        (
            "Inspect with Chrome Developer Tools - Google Chrome",
            "Allow remote debugging for this browser instance",
            True,
        ),
        ("Inspect with Chrome Developer Tools - Google Chrome - Work", None, True),
        ("Allow remote debugging?", "Allow", True),
        ("The weekly report - Google Chrome", "Allow remote debugging for this browser instance", True),
        ("The weekly report - Google Chrome", "Allow", False),
        (None, "Save", False),
    ],
)
def test_a_step_on_chromes_remote_debugging_is_refused_in_every_mode(
    title: str | None, label: str | None, refused: bool, tmp_path: Path
) -> None:
    """Item 343: the switch of chrome://inspect and the Allow Chrome asks are the owner's, whatever an error tells."""
    role: AppRole = step_role("Google Chrome", "com.google.Chrome", title, label)
    context: StepContext = StepContext(
        role=role, app="Google Chrome", element=Element(role="AXButton", name=label or ""), secure=False, line=None
    )
    assessment: StepAssessment = assess_step(Step(action="click", x=10, y=10), context, tmp_path, (), ())
    info: ActionInfo = ActionInfo(
        tool="desktop_act",
        kind="desktop_input",
        risk=assessment.risk,
        summary="click",
        target="Google Chrome",
        flags=assessment.flags,
    )
    assert (role == "chrome_grant") is refused
    assert (auto_review(info) is not None) is refused, assessment
    assert step_role("Safari", "com.apple.Safari", "Inspect with Chrome Developer Tools", None) == "other"


def _notes_env(tmp_path: Path, expected_front: int | None) -> StepEnv:
    """A batch on Notes (pid 101) with Terminal (pid 202) running; only what typing reads is real."""
    notes: AppInfo = AppInfo(
        pid=101, name="Notes", bundle_id="com.apple.Notes", bundle_path="", kind="regular", frontmost=False
    )
    terminal: AppInfo = AppInfo(
        pid=202, name="Terminal", bundle_id="com.apple.Terminal", bundle_path="", kind="regular", frontmost=True
    )
    return StepEnv(
        app=notes,
        app_element=object(),
        elements={},
        running=(notes, terminal),
        displays=(),
        scale=1.0,
        source=object(),
        config=load_default_config().desktop,
        failsafe_file=tmp_path / "STOP",
        expected_front=expected_front,
        expected_pointer=None,
        preferred=frozenset(),
        secure_field=lambda inspection, what: None,
    )


@pytest.mark.parametrize("text", ["Dear Ada", "Dear Ada, the minutes of the meeting are in the shared folder."])
def test_keys_for_a_ref_go_only_to_its_application_when_it_holds_the_keyboard(
    text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code review's Y2: a type step at a ref focused the control in its application and sent the keys, or a long
    text through the clipboard, to whichever application was in front, which an earlier step of the batch may have
    brought there."""
    sent: list[str] = []
    front: list[int] = [202]
    monkeypatch.setattr(actions, "_focus", lambda env, ref: None)
    monkeypatch.setattr(apps, "frontmost_pid", lambda: front[0])
    monkeypatch.setattr(hid, "text_events", lambda source, text: [text])
    monkeypatch.setattr(hid, "post", sent.extend)
    monkeypatch.setattr(hid, "paste_text", lambda source, text, settle_s: sent.append(text))
    step: Step = Step(action="type", ref="a5", text=text)
    inspection: Inspection = Inspection(
        context=StepContext(
            role="other", app="Notes", element=Element(role="AXTextArea", name="Body"), secure=False, line=None
        ),
        target='text area "Body" a5 in Notes',
    )
    with pytest.raises(DesktopError, match="Terminal has the keyboard focus, not Notes"):
        actions._type(_notes_env(tmp_path, 202), step, inspection)
    # Notes in front, but not as the previous step left the keyboard: something else moved it.
    front[0] = 101
    with pytest.raises(DesktopError, match="Notes took the keyboard focus after the previous step"):
        actions._type(_notes_env(tmp_path, 202), step, inspection)
    assert sent == []
    actions._type(_notes_env(tmp_path, 101), step, inspection)
    assert sent == [text]


@pytest.mark.parametrize(
    ("script", "reaches"),
    [
        ('tell application "1Password" to activate', True),
        ('tell app "1Password" to activate', True),
        ('tell application id "com.1password.1password" to activate', True),
        ("Application('com.1password.1password').activate()", True),
        ('tell application "System Events" to tell process "Bitwarden" to keystroke "c" using command down', True),
        ('activate application "Passwords"', True),
        ("tell application appName to activate", True),
        ('tell application "System Events" to tell (first process whose frontmost is true) to keystroke "c"', True),
        ("set front to path to frontmost application as text", True),
        ("Application(name).activate()", True),
        ('Application("System Events").processes.byName(target).windows()', True),
        ('tell application "Notes" to get name of every note', False),
        ('tell application "System Events" to tell process "Safari" to set frontmost to true', False),
        ('tell application "System Events" to get name of application process "Finder"', False),
        ("var app = Application.currentApplication(); app.includeStandardAdditions = true", False),
        ('display dialog "Change your passwords today"', False),
    ],
)
def test_a_script_that_names_or_computes_a_password_manager_reaches_secrets(script: str, reaches: bool) -> None:
    """The code review's Y10: only ``tell application "X"`` was read, so ``tell app``, a bundle id, a process of System
    Events or a target the script computes drove a password manager with no question."""
    assert (secrets_reason(script, {}) is not None) is reaches, secrets_reason(script, {})


def test_an_application_a_script_names_is_known_by_its_bundle_id(tmp_path: Path) -> None:
    """The code review's Y10: a name was matched against the names of password managers alone, so one under another
    name on this Mac ("Vault", a renamed 1Password) passed; its bundle id says what it is."""
    for name, bundle_id in (("Vault", "com.1password.1password"), ("Notes", "com.apple.Notes")):
        contents: Path = tmp_path / f"{name}.app" / "Contents"
        contents.mkdir(parents=True)
        info: dict[str, str] = {"CFBundleIdentifier": bundle_id, "CFBundleName": name}
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    identities: dict[str, frozenset[str]] = app_identities((tmp_path,))
    assert identities["vault"] == frozenset({"com.1password.1password"})
    renamed: str = 'tell application "Vault" to activate'
    assert secrets_reason(renamed, {}) is None
    assert secrets_reason(renamed, identities) == "it names 'Vault', which holds passwords or keys"
    assert secrets_reason('tell application "Notes" to get name of every note', identities) is None


def test_a_bundle_outside_the_sealed_system_volume_is_one_a_run_may_have_made_however_it_is_named() -> None:
    made: list[Path] = [
        Path("/tmp/x.app"),
        Path("x.app"),
        Path("Notes.APP"),
        Path("/System/../tmp/x.app"),
        Path("~/Applications/Tool.app"),
        Path("/System/Volumes/Data/Users/someone/x.app"),
        # The Mac's file systems read the letters of a path without case, so these are the same volume.
        Path("/System/volumes/data/Users/someone/x.app"),
        Path("/system/volumes/Data/Users/someone/x.app"),
    ]
    installed: list[Path] = [
        Path("/System/Applications/Notes.app"),
        # Apple's own applications are on the sealed system volume, Safari's under its Cryptexes.
        Path("/Applications/Safari.app"),
    ]
    assert [not _on_the_sealed_system_volume(path) for path in made] == [True] * len(made)
    assert [not _on_the_sealed_system_volume(path) for path in installed] == [False] * len(installed)


def test_a_name_or_a_bundle_id_is_the_bundle_launch_services_resolves_it_to() -> None:
    """Item 418: the name or the bundle id a run can give is resolved through Launch Services, so it is judged by the
    bundle it reaches; a path to anything but a bundle names none, and open refuses it as not an application."""
    assert _bundle_of("Notes") == Path("/System/Applications/Notes.app")
    assert _bundle_of("com.apple.Notes") == Path("/System/Applications/Notes.app")
    # A name that ends in .app is read as a path, whatever its letter case, as open reads it.
    assert [_bundle_of(app) for app in ("x.app", "Notes.APP")] == [Path("x.app"), Path("Notes.APP")]
    assert [_bundle_of(app) for app in ("123", "com.example.no-such-application", "/usr/bin/node")] == [None] * 3


@pytest.mark.parametrize("app", ["Google Chrome", "com.google.Chrome"])
def test_a_name_or_a_bundle_id_launch_services_resolves_outside_system_may_be_one_a_run_made(app: str) -> None:
    """Item 418: a run may register a bundle it made, so the name or the bundle id it gave it resolves through Launch
    Services to the same path a bundle outside /System has, and asks as that path does."""
    bundle: Path | None = _bundle_of(app)
    if bundle is None:
        pytest.skip(f"no {app} installed outside the sealed system volume")
    assert not _on_the_sealed_system_volume(bundle)
