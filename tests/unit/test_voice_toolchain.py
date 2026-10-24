"""Whether this Mac can write a recording down (item 626): macOS 26 or later, Apple's developer tools (which
``xcode-select -p`` says are there) and, in them, a Swift compiler and a macOS SDK. Each missing piece is said in words
an owner can act on, and nothing that could open Apple's "install the developer tools" dialog on the owner's screen is
run before ``xcode-select -p`` has succeeded: `platform` and `subprocess` are stood in for here, so no test starts a
real program."""

import os
import subprocess
from pathlib import Path

import pytest

from dotagent.voice import toolchain
from dotagent.voice.toolchain import (
    INSTALL_TOOLS,
    NO_COMPILER,
    OLD_MACOS,
    Toolchain,
    find_compiler,
    look_for_compiler,
    small_environment,
)


class FakeMac:
    """A Mac as `platform` and `subprocess` tell it: its version, and what each command prints, or that it fails."""

    def __init__(self, tmp_path: Path, version: str = "26.6.2") -> None:
        self.version: str = version
        self.tools: Path = tmp_path / "Developer"
        self.tools.mkdir()
        self.compiler: Path = tmp_path / "swiftc"
        self.compiler.write_text("#!/bin/sh\n", encoding="utf-8")
        self.compiler.chmod(0o755)
        self.sdk: Path = tmp_path / "MacOSX.sdk"
        self.sdk.mkdir()
        self.answers: dict[tuple[str, ...], str | None] = {
            ("/usr/bin/xcode-select", "-p"): str(self.tools),
            ("/usr/bin/xcrun", "--find", "swiftc"): str(self.compiler),
            ("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"): str(self.sdk),
        }
        self.run: list[tuple[str, ...]] = []
        self.environments: list[dict[str, str]] = []

    def mac_ver(self) -> tuple[str, tuple[str, str, str], str]:
        return (self.version, ("", "", ""), "arm64")

    def subprocess_run(self, arguments: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        key: tuple[str, ...] = tuple(arguments)
        self.run.append(key)
        env: object = options["env"]
        assert isinstance(env, dict)
        self.environments.append(env)
        answer: str | None = self.answers[key]
        return subprocess.CompletedProcess(arguments, 0 if answer is not None else 2, stdout=answer or "", stderr="")


@pytest.fixture
def mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMac:
    fake: FakeMac = FakeMac(tmp_path)
    monkeypatch.delenv("DOT_SWIFTC", raising=False)
    monkeypatch.setattr(toolchain.platform, "mac_ver", fake.mac_ver)
    monkeypatch.setattr(toolchain.subprocess, "run", fake.subprocess_run)
    return fake


def test_a_mac_with_macos_26_and_the_tools_builds_with_the_compiler_the_tools_name(mac: FakeMac) -> None:
    assert look_for_compiler() == Toolchain(mac.compiler, None, mac.sdk)
    assert mac.run == [
        ("/usr/bin/xcode-select", "-p"),
        ("/usr/bin/xcrun", "--find", "swiftc"),
        ("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"),
    ]


@pytest.mark.parametrize("version", ["25.4", "15.7.1", "14.0", "10.15.7", ""])
def test_a_mac_before_macos_26_says_so_and_runs_nothing(mac: FakeMac, version: str) -> None:
    mac.version = version
    assert look_for_compiler() == Toolchain(None, "This Mac needs macOS 26 or later")
    assert OLD_MACOS == "This Mac needs macOS 26 or later"
    assert mac.run == []


@pytest.mark.parametrize("version", ["26.0", "26.6.2", "27.1", "30.0"])
def test_macos_26_or_later_is_enough(mac: FakeMac, version: str) -> None:
    mac.version = version
    assert look_for_compiler().compiler == mac.compiler


def test_swiftc_is_never_run_when_the_developer_tools_are_not_installed(mac: FakeMac) -> None:
    """macOS's `swiftc` and `xcrun` are stubs where the tools are missing: they open a dialog on the owner's screen.
    Only `xcode-select -p`, which does not, is run until it has succeeded."""
    mac.answers[("/usr/bin/xcode-select", "-p")] = None
    assert look_for_compiler() == Toolchain(None, "Install Apple's developer tools on the Mac: xcode-select --install")
    assert INSTALL_TOOLS.endswith("xcode-select --install")
    assert mac.run == [("/usr/bin/xcode-select", "-p")]


def test_a_developer_folder_that_is_not_there_counts_as_no_tools(mac: FakeMac, tmp_path: Path) -> None:
    """`xcode-select -p` names a folder that was deleted: the tools are not there, and nothing more is run."""
    mac.answers[("/usr/bin/xcode-select", "-p")] = str(tmp_path / "deleted" / "Developer")
    assert look_for_compiler().why == INSTALL_TOOLS
    assert mac.run == [("/usr/bin/xcode-select", "-p")]
    mac.answers[("/usr/bin/xcode-select", "-p")] = "Developer"
    assert look_for_compiler().why == INSTALL_TOOLS


@pytest.mark.parametrize("what", ["compiler", "sdk"])
def test_tools_without_a_compiler_or_an_sdk_are_said_to_be_unable_to_build(mac: FakeMac, what: str) -> None:
    key: tuple[str, ...] = (
        ("/usr/bin/xcrun", "--find", "swiftc")
        if what == "compiler"
        else ("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path")
    )
    mac.answers[key] = None
    assert look_for_compiler() == Toolchain(None, NO_COMPILER)
    mac.answers[key] = str(mac.compiler.parent / "nothing-here")
    assert look_for_compiler().why == NO_COMPILER


def test_a_compiler_that_is_not_a_program_is_not_used(mac: FakeMac) -> None:
    mac.compiler.chmod(0o644)
    assert look_for_compiler().why == NO_COMPILER


@pytest.mark.parametrize("failure", [OSError("no such program"), subprocess.TimeoutExpired("xcode-select", 10)])
def test_a_command_that_cannot_run_or_hangs_is_a_tool_that_is_missing(
    mac: FakeMac, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def broken(arguments: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        raise failure

    monkeypatch.setattr(toolchain.subprocess, "run", broken)
    assert look_for_compiler().why == INSTALL_TOOLS


def test_the_compiler_a_test_names_is_used_as_it_is_without_asking_the_mac(
    mac: FakeMac, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DOT_SWIFTC: tests point at a compiler of their own, whatever macOS they run on and whether it has tools."""
    mac.version = "14.0"
    monkeypatch.setenv("DOT_SWIFTC", str(mac.compiler))
    assert look_for_compiler() == Toolchain(mac.compiler, None, None)
    monkeypatch.setenv("DOT_SWIFTC", str(mac.compiler.parent / "missing"))
    assert look_for_compiler() == Toolchain(None, NO_COMPILER)
    monkeypatch.setenv("DOT_SWIFTC", "")
    assert look_for_compiler().why == OLD_MACOS
    assert mac.run == []


async def test_asking_is_done_off_the_event_loop(mac: FakeMac) -> None:
    assert (await find_compiler()).compiler == mac.compiler


def test_the_programs_asked_get_a_small_environment_not_the_daemons_own(
    mac: FakeMac, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-a-compiler")
    monkeypatch.setenv("DOT_TEST_SECRET", "nor-this")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")
    look_for_compiler()
    assert mac.environments
    for env in mac.environments:
        assert env == small_environment()
        assert set(env) <= {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG"}
        assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
        assert env["HOME"] == os.environ["HOME"]
