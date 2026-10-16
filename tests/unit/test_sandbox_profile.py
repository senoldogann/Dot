"""The profile of the sandbox the processes of a run start in: what it names and how, and that macOS holds a process to
it whatever path, link or letter case the process uses; and its switch (item 262): off, a process starts without it, and
on, only once its profiles start."""

import os
import subprocess
from pathlib import Path

import pytest

from dotagent.core.errors import ConfigError
from dotagent.policy.sandbox import UNSANDBOXED, Sandbox, SwitchedSandbox, build_sandbox, sandbox_profile


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
    words: tuple[str, ...] = build_sandbox((token,), (token,), (skills,)).commands

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


async def test_the_switch_starts_processes_without_the_sandbox_while_off_and_with_it_once_on(tmp_path: Path) -> None:
    built: Sandbox = build_sandbox((tmp_path / "token",), (tmp_path / "token",), (tmp_path / "skills",))
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
