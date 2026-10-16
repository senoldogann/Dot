"""The profile of the sandbox the processes of a run start in: what it names and how, and that macOS holds a process to
it whatever path, link or letter case the process uses; and its switch (item 262): off, a process starts without it, and
on, only once its profiles start."""

import os
import subprocess
from pathlib import Path

import pytest

from dotagent.core.errors import ConfigError
from dotagent.policy.sandbox import (
    UNSANDBOXED,
    Sandbox,
    SwitchedSandbox,
    build_sandbox,
    confined_words,
    sandbox_profile,
)
from dotagent.policy.shell import assess_shell
from dotagent.tools.shell import command_confinement


def test_the_profile_seals_guards_and_keeps_in_place_each_folder_above_once_and_quoted() -> None:
    profile: str = sandbox_profile(
        (Path('/home/a "b"/token'), Path("/home/a \\b/key")), (Path("/home/skills"), Path("/home/skills"))
    )
    assert profile.splitlines() == [
        "(version 1)",
        "(allow default)",
        '(deny file-read* file-write* (subpath "/home/a \\"b\\"/token") (subpath "/home/a \\\\b/key"))',
        '(deny file-write* (subpath "/home/skills"))',
        '(deny file-write* (literal "/home") (literal "/home/a \\"b\\"") (literal "/home/a \\\\b"))',
    ]


def test_macos_holds_a_command_to_its_profile(tmp_path: Path) -> None:
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    skills: Path = home / "skills"
    skills.mkdir(parents=True)
    token: Path = home / "token"
    token.write_text("secret", encoding="utf-8")
    words: tuple[str, ...] = build_sandbox((token,), (token,), (skills,), ()).commands

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    refused: list[subprocess.CompletedProcess[str]] = [
        run(f"cat {token}"),
        run(f"cat {str(token).upper()}"),
        run(f"ln -s {token} {home}/link && cat {home}/link"),
        run(f"echo planted > {skills}/SKILL.md"),
        run(f"mv {home} {home}-moved"),
    ]
    allowed: subprocess.CompletedProcess[str] = run(f"echo note > {home}/note.txt && cat {home}/note.txt")
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 5
    assert "secret" not in "".join(done.stdout for done in refused)
    assert (allowed.returncode, allowed.stdout) == (0, "note\n")
    assert UNSANDBOXED.commands == UNSANDBOXED.search == ()


def test_macos_holds_a_command_of_a_workspace_to_it_and_the_places_allowed(tmp_path: Path) -> None:
    """A held command writes in its workspace, the places the sandbox allows and the devices, nowhere else, and stays
    out of Dot's own files as every command does. The temporary folders are among the places a run's sandbox allows;
    here they are left out, so that a folder of the test stands for one outside."""
    home: Path = Path(os.path.realpath(tmp_path)) / "home"
    workspace: Path = home / "workspace"
    allowed: Path = home / "cache"
    outside: Path = home / "Desktop"
    skills: Path = home / "skills"
    for folder in (workspace, allowed, outside, skills):
        folder.mkdir(parents=True)
    token: Path = home / "token"
    token.write_text("secret", encoding="utf-8")
    words: tuple[str, ...] = confined_words(build_sandbox((token,), (token,), (skills,), (allowed,)), workspace)

    def run(line: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*words, "/bin/zsh", "-c", line], capture_output=True, text=True, check=False)

    written: list[subprocess.CompletedProcess[str]] = [
        run(f"echo a > {workspace}/a.txt && mkdir {workspace}/sub && echo b > {workspace}/sub/b.txt"),
        run(f"echo c > {allowed}/c.txt"),
        run("echo d > /dev/null"),
    ]
    refused: list[subprocess.CompletedProcess[str]] = [
        run(f"echo e > {outside}/e.txt"),
        run(f"python3 -c \"open('{outside}/f.txt', 'w')\""),
        run(f"cat {token}"),
        run(f"echo planted > {skills}/SKILL.md"),
    ]
    assert [done.returncode for done in written] == [0, 0, 0], [done.stderr for done in written]
    assert [(done.returncode != 0, "not permitted" in done.stderr) for done in refused] == [(True, True)] * 4
    assert sorted(path.name for path in outside.iterdir()) == []
    assert confined_words(UNSANDBOXED, workspace) == ()


def test_the_shell_holds_to_its_workspace_only_a_line_the_guard_reads_as_plain_work(tmp_path: Path) -> None:
    """A line that names no place outside is held; one that names a place outside, which the guard judges, starts in
    the sandbox as it is; with the sandbox off, neither is."""
    built: Sandbox = build_sandbox((tmp_path / "token",), (), (tmp_path / "skills",), (tmp_path / "cache",))

    def words(line: str, sandbox: Sandbox) -> tuple[str, ...]:
        return command_confinement(sandbox, assess_shell(line, tmp_path, tmp_path, (), ()), tmp_path)

    held: tuple[str, ...] = words("python3 build.py > out.txt", built)
    assert held == confined_words(built, tmp_path) and "(deny file-write*)" in held[-1]
    assert words("cp out.txt ~/Desktop/", built) == built.commands
    assert words("python3 build.py", UNSANDBOXED) == words("cp out.txt ~/Desktop/", UNSANDBOXED) == ()


async def test_the_switch_starts_processes_without_the_sandbox_while_off_and_with_it_once_on(tmp_path: Path) -> None:
    built: Sandbox = build_sandbox((tmp_path / "token",), (tmp_path / "token",), (tmp_path / "skills",), ())
    switched: SwitchedSandbox = SwitchedSandbox(built, False)
    assert (switched.on, switched.current()) == (False, UNSANDBOXED)
    await switched.switch(True)
    assert (switched.on, switched.current()) == (True, built)
    await switched.switch(False)
    assert (switched.on, switched.current()) == (False, UNSANDBOXED)


async def test_a_sandbox_whose_program_does_not_start_is_never_switched_on(tmp_path: Path) -> None:
    missing: str = str(tmp_path / "no-sandbox-exec")
    broken: Sandbox = Sandbox(
        commands=(missing, "-p", "(version 1)"),
        search=(missing, "-p", "(version 1)"),
        sealed=(),
        unwritable=(),
        writable=(),
    )
    switched: SwitchedSandbox = SwitchedSandbox(broken, False)
    with pytest.raises(ConfigError, match="cannot start") as refused:
        await switched.switch(True)
    assert refused.value.context["program"] == missing
    assert (switched.on, switched.current()) == (False, UNSANDBOXED)
    # Off needs nothing to start.
    await switched.switch(False)
    assert switched.on is False
