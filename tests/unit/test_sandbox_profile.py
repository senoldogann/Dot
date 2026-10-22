"""The profile of the sandbox the processes of a run start in: what it names and how, and that macOS holds a process to
it whatever path, link or letter case the process uses, and keeps it from having another program act for it: not the
program it starts but the service that would run what the program hands it (item 418); and its switch (item 262): off,
a process starts without it, and on, only once its profiles start."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dotagent.core.errors import ConfigError
from dotagent.policy.sandbox import (
    CLIPBOARD_SERVICES,
    HANDING_PROGRAMS,
    HANDING_SERVICES,
    SANDBOX_EXEC,
    UNSANDBOXED,
    Sandbox,
    SwitchedSandbox,
    build_sandbox,
    sandbox_profile,
)


def test_the_profile_seals_guards_and_keeps_in_place_each_folder_above_once_and_quoted() -> None:
    profile: str = sandbox_profile(
        (Path('/home/a "b"/token'), Path("/home/a \\b/key")),
        (Path("/home/skills"), Path("/home/skills")),
        Path("/home/Library/LaunchAgents"),
    )
    assert profile.splitlines()[:6] == [
        "(version 1)",
        "(allow default)",
        '(deny file-read* file-write* (subpath "/home/a \\"b\\"/token") (subpath "/home/a \\\\b/key"))',
        '(deny file-write* (subpath "/home/skills"))',
        '(deny file-write* (subpath "/home/Library/LaunchAgents"))',
        '(deny file-write* (literal "/home") (literal "/home/Library") (literal "/home/a \\"b\\"") '
        '(literal "/home/a \\\\b"))',
    ]


def test_the_profile_keeps_a_process_from_having_another_program_act_for_it() -> None:
    rules: list[str] = sandbox_profile((), (), Path("/home/Library/LaunchAgents")).splitlines()
    assert "(deny lsopen)" in rules
    # Apple Events are not refused: macOS does not hold a sandbox-exec profile to `appleevent-send` (measured on macOS
    # 26: with the rule over the Finder and the terminal apps, a script drove the Finder and started an application
    # that was not running), so no rule pretends to.
    assert not any(rule.startswith("(deny appleevent-send") for rule in rules)
    started: str = next(rule for rule in rules if rule.startswith("(deny process-exec "))
    assert all(f'(literal "{program}")' in started for program in HANDING_PROGRAMS)
    assert all(program.exists() for program in HANDING_PROGRAMS), HANDING_PROGRAMS
    # A copy of a program is not what is refused: the service it would hand the work to is, whatever program asks.
    handed: str = next(rule for rule in rules if rule.startswith("(deny mach-lookup "))
    assert all(f'(global-name "{service}")' in handed for service in HANDING_SERVICES)
    assert {"com.apple.siri.VoiceShortcuts.xpc", "com.apple.lsd.modifydb"} <= set(HANDING_SERVICES)
    assert "(deny job-creation)" in rules


def test_macos_holds_a_command_to_its_profile(tmp_path: Path) -> None:
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    skills: Path = home / "skills"
    skills.mkdir(parents=True)
    token: Path = home / "token"
    token.write_text("secret", encoding="utf-8")
    agents: Path = home / "Library" / "LaunchAgents"
    words: tuple[str, ...] = build_sandbox((token,), (token,), (skills,), agents).commands

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    refused: list[subprocess.CompletedProcess[str]] = [
        run(f"cat {token}"),
        run(f"cat {str(token).upper()}"),
        run(f"ln -s {token} {home}/link && cat {home}/link"),
        run(f"echo planted > {skills}/SKILL.md"),
        run(f"mv {home} {home}-moved"),
        # A job left for launchd, and launchd or Shortcuts handed one, would run outside the sandbox.
        run(f"mkdir -p {agents} && echo job > {agents}/planted.plist"),
        run("launchctl version"),
        run("shortcuts list"),
    ]
    allowed: subprocess.CompletedProcess[str] = run(f"echo note > {home}/note.txt && cat {home}/note.txt")
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 8
    assert not (agents / "planted.plist").exists()
    # What open starts runs outside the sandbox: Launch Services refuses it (-54), and the app does not start.
    opened: subprocess.CompletedProcess[str] = run("open -g -a 'Image Events'")
    assert opened.returncode != 0 and "-54" in opened.stderr, opened.stderr
    assert "secret" not in "".join(done.stdout for done in refused)
    assert (allowed.returncode, allowed.stdout) == (0, "note\n")
    assert UNSANDBOXED.commands == UNSANDBOXED.search == ()


def test_macos_refuses_the_service_that_would_run_what_a_program_hands_it(tmp_path: Path) -> None:
    """Item 418: what the profile refuses is not the program but the service behind it, so a program a run wrote is held
    to it too. ``shortcuts`` reaches the Shortcuts service, which runs a shortcut outside the sandbox ("Run Shell
    Script" among them); ``lsregister`` writes the Launch Services database, which is what ``open`` finds a bundle by,
    and a bundle it registered outside /System may be one the run made."""
    service: str = next(
        rule
        for rule in sandbox_profile((), (), tmp_path / "LaunchAgents").splitlines()
        if rule.startswith("(deny mach-lookup ")
    )

    def run(words: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [SANDBOX_EXEC, "-p", f"(version 1)\n(allow default)\n{service}", *words],
            capture_output=True,
            text=True,
            check=False,
        )

    # The Shortcuts service runs what shortcuts would have it run: refused, the program cannot reach it.
    shortcuts: Path = next(program for program in HANDING_PROGRAMS if program.name == "shortcuts")
    handed: subprocess.CompletedProcess[str] = run([str(shortcuts), "list"])
    assert handed.returncode != 0 and "helper application" in handed.stderr, handed.stderr
    # A registration would leave the bundle openable by its bundle id, outside the sandbox: it is refused too.
    bundle: Path = tmp_path / "Probe.app" / "Contents"
    (bundle / "MacOS").mkdir(parents=True)
    (bundle / "Info.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key>'
        "<string>com.example.dot-probe</string><key>CFBundleExecutable</key><string>Probe</string>"
        "<key>CFBundlePackageType</key><string>APPL</string></dict></plist>",
        encoding="utf-8",
    )
    executable: Path = bundle / "MacOS" / "Probe"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    registers: Path = next(program for program in HANDING_PROGRAMS if program.name == "lsregister")
    try:
        registered: subprocess.CompletedProcess[str] = run([str(registers), "-f", str(bundle.parent)])
        assert "failed to scan" in registered.stdout + registered.stderr, registered.stdout + registered.stderr
    finally:
        # Whatever the rule did, the bundle is not left registered with Launch Services: the test removes its own mark.
        subprocess.run([str(registers), "-u", str(bundle.parent)], capture_output=True, text=True, check=False)


async def test_the_switch_starts_processes_without_the_sandbox_while_off_and_with_it_once_on(tmp_path: Path) -> None:
    built: Sandbox = build_sandbox(
        (tmp_path / "token",), (tmp_path / "token",), (tmp_path / "skills",), tmp_path / "LaunchAgents"
    )
    switched: SwitchedSandbox = SwitchedSandbox(built, False)
    assert (switched.on, switched.current()) == (False, UNSANDBOXED)
    await switched.switch(True)
    assert (switched.on, switched.current()) == (True, built)
    await switched.switch(False)
    assert (switched.on, switched.current()) == (False, UNSANDBOXED)


async def test_a_sandbox_whose_program_does_not_start_is_never_switched_on(tmp_path: Path) -> None:
    missing: str = str(tmp_path / "no-sandbox-exec")
    broken: Sandbox = Sandbox(commands=(missing, "-p", "(version 1)"), search=(missing, "-p", "(version 1)"))
    switched: SwitchedSandbox = SwitchedSandbox(broken, False)
    with pytest.raises(ConfigError, match="cannot start") as refused:
        await switched.switch(True)
    assert refused.value.context["program"] == missing
    assert (switched.on, switched.current()) == (False, UNSANDBOXED)
    # Off needs nothing to start.
    await switched.switch(False)
    assert switched.on is False


LOOK_UP_THE_CLIPBOARD: Path = Path(__file__).resolve().parents[1] / "fixtures" / "look_up_the_clipboard.py"


def test_the_profile_closes_the_clipboard_to_a_process_of_a_run() -> None:
    """Item 534: the pasteboard's service is refused in a rule of its own, beside the services that hand work on."""
    rules: list[str] = sandbox_profile((), (), Path("/home/Library/LaunchAgents")).splitlines()
    assert CLIPBOARD_SERVICES == ("com.apple.pasteboard.1",)
    assert '(deny mach-lookup (global-name "com.apple.pasteboard.1"))' in rules
    handed: str = next(rule for rule in rules if rule.startswith("(deny mach-lookup "))
    assert not any(f'(global-name "{service}")' in handed for service in CLIPBOARD_SERVICES)


def test_macos_refuses_a_command_of_a_run_the_clipboards_service(tmp_path: Path) -> None:
    """Item 534: whatever the line that starts a program says (a name in quotes the shell takes out twice, a script in a
    file), the program cannot look the clipboard's service up, so it can neither read what the user copied nor put text
    there; a process outside the sandbox finds it. The look-up reads nothing from the clipboard."""
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    home.mkdir()
    words: tuple[str, ...] = build_sandbox(
        (home / "token",), (home / "token",), (home / "skills",), home / "agents"
    ).commands
    outside: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, str(LOOK_UP_THE_CLIPBOARD)], capture_output=True, text=True, check=False
    )
    if outside.stdout.strip() != "0":
        # The service is registered per login session: a Mac reached without one has nothing to refuse.
        pytest.skip(f"the clipboard's service is not found outside the sandbox here: {outside.stdout!r}")
    inside: subprocess.CompletedProcess[str] = subprocess.run(
        [*words, sys.executable, str(LOOK_UP_THE_CLIPBOARD)], capture_output=True, text=True, check=False
    )
    assert (inside.returncode, inside.stdout.strip()) == (0, "1100"), inside
