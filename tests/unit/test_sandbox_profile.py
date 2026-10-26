"""The profile of the sandbox the processes of a run start in: what it names and how, and that macOS holds a process to
it whatever path, link or letter case the process uses, and keeps it from having another program act for it: not the
program it starts but the service that would run what the program hands it (item 418); and its switch (item 262): off,
a process starts without it, and on, only once its profiles start."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import ConfigError
from dotagent.core.paths import voice_dir, web_research_dir
from dotagent.dot_app import app_path, app_program, build_path, runtime_path
from dotagent.policy.paths import expand_protected
from dotagent.policy.sandbox import (
    CLIPBOARD_SERVICES,
    HANDING_PROGRAMS,
    HANDING_SERVICES,
    SANDBOX_EXEC,
    UNSANDBOXED,
    Sandbox,
    SwitchedSandbox,
    build_sandbox,
    hard_linked,
    sandbox_profile,
)
from dotagent.runtime import _dot_paths, _own_code


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


def test_macos_closes_dots_home_to_a_command_but_for_the_workspace(tmp_path: Path) -> None:
    """Item 676: the daemon opens and writes files in its home and its logs with the owner's rights, so a command may
    leave nothing there, not even a link in place of the daemon's pid file, which the daemon would empty and write at
    its next start; in the workspace it works as before, but may not remove, move or replace the workspace itself."""
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    workspace: Path = home / "workspace"
    logs: Path = Path(os.path.realpath(tmp_path)) / "logs"
    for folder in (workspace, logs):
        folder.mkdir(parents=True)
    token: Path = home / "api-token"
    token.write_text("secret", encoding="utf-8")
    pid: Path = home / "daemon.pid"
    pid.write_text("1\n", encoding="utf-8")
    agents: Path = home / "Library" / "LaunchAgents"
    words: tuple[str, ...] = build_sandbox((token,), (token,), (), agents, (home, logs), (workspace,)).commands

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    refused: list[subprocess.CompletedProcess[str]] = [
        run(f"rm -f {pid}"),
        run(f"ln -s {token} {home}/daemon.running"),
        run(f"echo planted > {home}/looks.txt"),
        run(f"ln -s {token} {logs}/opencode.log"),
        run(f"mv {workspace} {home}/elsewhere"),
        run(f"rmdir {workspace}"),
    ]
    allowed: subprocess.CompletedProcess[str] = run(
        f"cd {workspace} && mkdir -p a/b && echo note > a/b/note.txt && ln -s a/b/note.txt link && mv a c && "
        "cat link c/b/note.txt; rm -r c link && echo done"
    )
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 6
    assert pid.read_text(encoding="utf-8") == "1\n" and not pid.is_symlink()
    assert sorted(path.name for path in home.iterdir()) == ["api-token", "daemon.pid", "workspace"]
    assert list(logs.iterdir()) == []
    assert allowed.returncode == 0 and allowed.stdout.endswith("done\n"), allowed.stderr
    assert list(workspace.iterdir()) == []


def test_the_open_folders_are_allowed_back_before_every_other_rule(tmp_path: Path) -> None:
    rules: list[str] = sandbox_profile(
        (Path("/home/api-token"),),
        (Path("/home/skills"),),
        Path("/home/Library/LaunchAgents"),
        (Path("/home"), Path("/logs")),
        (Path("/home/workspace"),),
    ).splitlines()
    assert rules[2:5] == [
        '(deny file-write* (subpath "/home") (subpath "/logs"))',
        '(allow file-write* (subpath "/home/workspace"))',
        '(deny file-read* file-write* (subpath "/home/api-token"))',
    ]
    kept: str = next(rule for rule in rules if rule.startswith("(deny file-write* (literal "))
    assert '(literal "/home/workspace")' in kept and '(literal "/home")' in kept


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


def test_dots_own_code_names_the_app_the_daemon_starts_with_and_the_folder_it_is_built_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 561: launchd runs the program of Dot.app, outside the sandbox, at every start of the daemon."""
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    app: set[Path] = {app_path(home), build_path(home)}
    assert app <= set(_own_code())
    assert app <= set(_dot_paths(load_default_config()))
    assert build_path(home) == home / ".dot-app-build"


def test_macos_keeps_a_command_from_changing_the_program_launchd_starts_the_daemon_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 561: a command that wrote a program into Dot.app and then ended the daemon (launchd keeps its job) would
    have it run outside the sandbox with the owner's rights. The profile of a run's commands, built from the paths of
    Dot's own code as the runtime builds it, refuses the program whether it is there or not yet, and the folder Dot
    builds it in."""
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    words: tuple[str, ...] = build_sandbox((), (), expand_protected([], _own_code()), home / "agents").commands
    program: Path = app_program(app_path(home))

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    # Not there yet: a command can make neither the app nor the folder it is built in.
    refused: list[subprocess.CompletedProcess[str]] = [
        run(f"mkdir -p {program.parent} && echo planted > {program}"),
        run(f"mkdir -p {build_path(home)}/Dot.app && echo planted > {build_path(home)}/Dot.app/Dot"),
    ]
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 2
    assert not app_path(home).exists() and not build_path(home).exists()
    # Made as `dotx service install` makes it, outside the sandbox: it is read, and neither changed nor moved.
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    refused = [
        run(f"printf planted >> {program}"),
        run(f"echo planted > {program}"),
        run(f"mv {program} {program}.moved"),
        run(f"mv {app_path(home)} {app_path(home)}.moved"),
    ]
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 4
    assert program.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    read: subprocess.CompletedProcess[str] = run(f"cat {program}; echo note > {home}/note.txt")
    assert (read.returncode, read.stdout) == (0, "#!/bin/sh\nexit 0\n")
    assert (home / "note.txt").read_text(encoding="utf-8") == "note\n"


def test_macos_keeps_a_command_from_changing_what_the_web_research_server_reads_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 583: the web-research server that comes with Dot starts with the daemon, outside the sandbox, and reads its
    crawl jobs back from its data folder: a database, a link or a file a command put there would be opened by it with
    the owner's rights. The profile of a run's commands, built from what Dot loads and runs as the runtime builds it,
    refuses each, whether the folder is there or not yet, and lets the command read it and write elsewhere in Dot's
    home."""
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    words: tuple[str, ...] = build_sandbox(
        (), (), expand_protected([], _dot_paths(load_default_config())), home / "agents"
    ).commands
    folder: Path = web_research_dir()
    assert folder == home / "web-research"

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    # Not there yet: a command can make neither the folder nor anything in it.
    refused: list[subprocess.CompletedProcess[str]] = [
        run(f"mkdir {folder}"),
        run(f"mkdir -p {folder} && echo planted > {folder}/crawl-jobs.sqlite3"),
    ]
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 2
    assert not folder.exists()
    # Made as the server makes it, outside the sandbox: it is read, and nothing in it is changed, made or moved.
    folder.mkdir()
    jobs: Path = folder / "crawl-jobs.sqlite3"
    jobs.write_text("jobs", encoding="utf-8")
    refused = [
        run(f"echo planted > {jobs}"),
        run(f"printf planted >> {jobs}"),
        run(f"echo planted > {folder}/other.sqlite3"),
        run(f"ln -s /tmp {folder}/link"),
        run(f"mv {jobs} {jobs}.moved"),
        run(f"mv {folder} {folder}.moved"),
    ]
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 6
    assert jobs.read_text(encoding="utf-8") == "jobs"
    assert sorted(path.name for path in folder.iterdir()) == ["crawl-jobs.sqlite3"]
    read: subprocess.CompletedProcess[str] = run(f"cat {jobs}; echo note > {home}/note.txt")
    assert (read.returncode, read.stdout) == (0, "jobs")
    assert (home / "note.txt").read_text(encoding="utf-8") == "note\n"


def test_macos_keeps_a_command_from_changing_the_program_the_daemon_builds_to_write_recordings_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 626: the daemon builds the program that writes recordings down in a folder of its own under Dot's home and
    runs it, outside the sandbox, with the owner's rights: a command that could replace it, or put a program of its own
    in a folder the daemon builds in, would have it run at the next recording. The profile of a run's commands, built
    from what Dot loads and runs as the runtime builds it, refuses each, whether the folder is there or not yet, and
    lets the command read what is there."""
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    words: tuple[str, ...] = build_sandbox(
        (), (), expand_protected([], _dot_paths(load_default_config())), home / "agents"
    ).commands
    folder: Path = voice_dir()
    assert folder == home / "voice"

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    def refusals(done: list[subprocess.CompletedProcess[str]]) -> list[tuple[bool, bool]]:
        return [(one.returncode != 0, "not permitted" in one.stderr) for one in done]

    # Not there yet: a command can make neither the folder nor a build folder, nor a program in one.
    refused: list[subprocess.CompletedProcess[str]] = [
        run(f"mkdir {folder}"),
        run(f"mkdir -p {folder}/build-abcd1234 && echo planted > {folder}/build-abcd1234/transcribe"),
    ]
    assert refusals(refused) == [(True, True)] * 2
    assert not folder.exists()
    # Built as the daemon builds it, outside the sandbox: nothing in it is changed, made or moved.
    built: Path = folder / "build-abcd1234"
    built.mkdir(parents=True)
    program: Path = built / "transcribe"
    program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    refused = [
        run(f"echo planted > {program}"),
        run(f"printf planted >> {program}"),
        run(f"mv {program} {program}.moved"),
        run(f"mv {built} {built}.moved"),
        run(f"mkdir {folder}/build-wxyz5678"),
        run(f"ln -s /tmp {folder}/link"),
        run(f"echo planted > {folder}/recordings"),
    ]
    assert refusals(refused) == [(True, True)] * 7
    assert program.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    assert sorted(entry.name for entry in folder.iterdir()) == ["build-abcd1234"]
    read: subprocess.CompletedProcess[str] = run(f"cat {program}; echo note > {home}/note.txt")
    assert (read.returncode, read.stdout) == (0, "#!/bin/sh\nexit 0\n")
    assert (home / "note.txt").read_text(encoding="utf-8") == "note\n"


def test_dots_own_code_names_the_runtime_folder_the_desktop_app_installs_dot_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 575: the desktop app runs uv from its own bundle and Dot's Python from this folder at its start, outside
    the sandbox, and the daemon runs from it: a program, a cache or a marker a command put there would be run."""
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    assert runtime_path(home) == home / "runtime"
    assert runtime_path(home) in _own_code()
    assert runtime_path(home) in _dot_paths(load_default_config())


def test_macos_keeps_a_command_from_changing_what_the_desktop_app_runs_at_its_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 575: a command that planted a program, a link or a file uv unpacks in the runtime folder, or the marker the
    app trusts, would have it run outside the sandbox with the owner's rights at the app's next start. The profile of a
    run's commands, built from the paths of Dot's own code as the runtime builds it, refuses each, whether the folder is
    there or not yet, and lets the command read it and write elsewhere in Dot's home."""
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    words: tuple[str, ...] = build_sandbox((), (), expand_protected([], _own_code()), home / "agents").commands
    runtime: Path = runtime_path(home)

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    # Not there yet: a command can make neither the folder nor anything in it.
    refused: list[subprocess.CompletedProcess[str]] = [
        run(f"mkdir {runtime}"),
        run(f"mkdir -p {runtime}/bin && echo planted > {runtime}/bin/dotx"),
        run(f"mkdir -p {runtime}/cache/archive-v0 && echo planted > {runtime}/cache/archive-v0/wheel"),
    ]
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 3
    assert not runtime.exists()
    # Made as the app makes it, outside the sandbox: it is read, and nothing in it is changed, made or moved.
    (runtime / "bin").mkdir(parents=True)
    marker: Path = runtime / "app-install.json"
    marker.write_text('{"version": "0.1.0", "complete": true}', encoding="utf-8")
    program: Path = runtime / "bin" / "dotx"
    program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    refused = [
        run(f"echo planted > {marker}"),
        run(f"printf planted >> {program}"),
        run(f"echo planted > {runtime}/uv.toml"),
        run(f"ln -s /tmp {runtime}/app-tools"),
        run(f"mv {program} {program}.moved"),
        run(f"mv {runtime} {runtime}.moved"),
    ]
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 6
    assert marker.read_text(encoding="utf-8") == '{"version": "0.1.0", "complete": true}'
    assert program.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"
    assert sorted(path.name for path in runtime.iterdir()) == ["app-install.json", "bin"]
    read: subprocess.CompletedProcess[str] = run(f"cat {marker}; echo note > {home}/note.txt")
    assert (read.returncode, read.stdout) == (0, '{"version": "0.1.0", "complete": true}')
    assert (home / "note.txt").read_text(encoding="utf-8") == "note\n"


def test_a_sealed_file_that_has_another_name_is_found(tmp_path: Path) -> None:
    """Item 561: the profile matches paths, so a hard link to a sealed file made where it did not hold is a name it does
    not know; the daemon says so in its log at its start."""
    token: Path = tmp_path / "api-token"
    token.write_text("secret", encoding="utf-8")
    alone: Path = tmp_path / ".env"
    alone.write_text("x", encoding="utf-8")
    (tmp_path / "workspace").mkdir()
    os.link(token, tmp_path / "workspace" / "link")
    assert hard_linked([token, alone, tmp_path / "workspace", tmp_path / "missing"]) == {token: 2}
    assert hard_linked([]) == {}


def test_a_hard_link_made_outside_the_sandbox_is_the_one_name_of_a_sealed_file_the_profile_does_not_close(
    tmp_path: Path,
) -> None:
    """What ``docs/safety.md`` says it does not cover (item 561): a symbolic link, another letter case or a link made in
    the sandbox are refused, and a hard link that a process outside it made earlier reads the file."""
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    (home / "sealed").mkdir(parents=True)
    (home / "workspace").mkdir()
    secret: Path = home / "sealed" / "token"
    secret.write_text("secret", encoding="utf-8")
    os.link(secret, home / "workspace" / "hard")
    words: tuple[str, ...] = build_sandbox((home / "sealed",), (), (), home / "agents").commands

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    assert run(f"cat {secret}").returncode != 0
    assert run(f"ln {secret} {home}/workspace/made-inside").returncode != 0
    symbolic: subprocess.CompletedProcess[str] = run(
        f"ln -s {secret} {home}/workspace/soft && cat {home}/workspace/soft"
    )
    assert symbolic.returncode != 0 and "secret" not in symbolic.stdout
    read: subprocess.CompletedProcess[str] = run(f"cat {home}/workspace/hard")
    assert (read.returncode, read.stdout) == (0, "secret")
