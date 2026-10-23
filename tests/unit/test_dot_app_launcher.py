"""Dot.app made from a prebuilt launcher (item 575), against stand-ins for clang and codesign. The desktop app carries
Dot.app's program already built and signed, so that a Mac with no Xcode tools can run the service: ``ensure_app``
copies it in place of the clang build, signs the bundle as before, keeps which program the app was made from, and
leaves an app that is current as it is, so that what macOS granted it holds. A launcher that is not a program is
refused before anything is made."""

import hashlib
import os
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from dotagent import dot_app
from dotagent.cli_commands import ServiceInstallCommand
from dotagent.cli_daemon import prebuilt_launcher
from dotagent.core.errors import ServiceError
from dotagent.doctor import _xcode_tools_row
from dotagent.dot_app import LAUNCHER_ENV, app_path, app_program, ensure_app, has_kept_launcher, kept_launcher

# The first bytes of a universal Mach-O program, then what tells two programs apart.
MACHO: bytes = b"\xca\xfe\xba\xbe"
CODESIGN: str = "/usr/bin/codesign"

Run = Callable[[list[str], str, str], None]


class Tools:
    """What ``ensure_app`` asked the system to run: each command's words. clang writes the program it is asked for and
    codesign makes the bundle's signature folder, as the real ones do."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(self, arguments: list[str], failure: str, fix: str) -> None:
        self.commands.append(arguments)
        if "clang" in arguments:
            Path(arguments[arguments.index("-o") + 1]).write_bytes(MACHO + b"built by clang")
        elif arguments[0] == CODESIGN:
            (Path(arguments[-1]) / "Contents" / "_CodeSignature").mkdir()

    def names(self) -> list[str]:
        return [Path(arguments[0]).name if "clang" not in arguments else "clang" for arguments in self.commands]


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> Tools:
    stand_in: Tools = Tools()
    monkeypatch.setattr(dot_app, "_run", stand_in.run)
    return stand_in


def launcher_at(folder: Path, content: bytes = b"the prebuilt program", mode: int = 0o755) -> Path:
    path: Path = folder / "dot-launcher"
    path.write_bytes(MACHO + content)
    os.chmod(path, mode)
    return path


def test_the_prebuilt_launcher_is_copied_in_and_clang_is_not_run(tmp_path: Path, tools: Tools) -> None:
    home: Path = tmp_path / "home"
    home.mkdir()
    launcher: Path = launcher_at(tmp_path)
    program: Path = ensure_app(home, launcher)
    assert program == app_program(app_path(home))
    assert program.read_bytes() == launcher.read_bytes()
    assert program.stat().st_mode & 0o777 == 0o755
    # Only codesign ran: no clang, so no Xcode tools. The bundle is signed ad hoc as when clang built it.
    assert tools.names() == ["codesign"]
    assert tools.commands[0][1:4] == ["--force", "--sign", "-"]
    resources: Path = app_path(home) / "Contents" / "Resources"
    assert (resources / "dot_app.c").read_text(encoding="utf-8") == dot_app.SOURCE.read_text(encoding="utf-8")
    assert (resources / "launcher.sha256").read_text(encoding="utf-8") == hashlib.sha256(
        launcher.read_bytes()
    ).hexdigest()
    assert not dot_app.build_path(home).exists()


def test_an_app_made_from_the_same_program_is_left_as_it_is(tmp_path: Path, tools: Tools) -> None:
    """What macOS granted Dot.app holds while the app is the same: asking again changes nothing, and asking without a
    launcher (a later ``dotx service install`` typed in a terminal) leaves an app made from one alone too."""
    home: Path = tmp_path / "home"
    home.mkdir()
    launcher: Path = launcher_at(tmp_path)
    program: Path = ensure_app(home, launcher)
    made: os.stat_result = program.stat()
    tools.commands.clear()
    assert ensure_app(home, launcher) == program
    assert ensure_app(home) == program
    assert tools.commands == []
    assert (program.stat().st_ino, program.stat().st_mtime_ns) == (made.st_ino, made.st_mtime_ns)


def test_another_launcher_takes_the_old_apps_place(tmp_path: Path, tools: Tools) -> None:
    home: Path = tmp_path / "home"
    home.mkdir()
    program: Path = ensure_app(home, launcher_at(tmp_path, b"version one"))
    other: Path = tmp_path / "other"
    other.mkdir()
    assert ensure_app(home, launcher_at(other, b"version two")) == program
    assert program.read_bytes() == MACHO + b"version two"
    assert tools.names() == ["codesign", "codesign"]


def test_an_app_built_with_clang_is_replaced_by_the_launcher_asked_for(tmp_path: Path, tools: Tools) -> None:
    home: Path = tmp_path / "home"
    home.mkdir()
    program: Path = ensure_app(home)
    assert program.read_bytes() == MACHO + b"built by clang"
    assert tools.names() == ["clang", "codesign"]
    ensure_app(home, launcher_at(tmp_path))
    assert program.read_bytes() == MACHO + b"the prebuilt program"
    assert tools.names() == ["clang", "codesign", "codesign"]


@pytest.mark.parametrize(
    ("make", "says"),
    [
        (lambda folder: Path("dot-launcher"), "full path"),
        (lambda folder: folder / "missing", "not a program that can be run"),
        (lambda folder: launcher_at(folder, mode=0o644), "not a program that can be run"),
        (lambda folder: folder, "not a program that can be run"),
    ],
)
def test_a_launcher_that_cannot_be_run_is_refused_before_anything_is_made(
    tmp_path: Path, tools: Tools, make: Callable[[Path], Path], says: str
) -> None:
    home: Path = tmp_path / "home"
    home.mkdir()
    with pytest.raises(ServiceError) as raised:
        ensure_app(home, make(tmp_path))
    assert says in raised.value.message
    assert "fix" in raised.value.context
    assert tools.commands == []
    assert list(home.iterdir()) == []


def test_a_file_that_is_not_a_macos_program_is_refused(tmp_path: Path, tools: Tools) -> None:
    script: Path = tmp_path / "launcher"
    script.write_text("#!/bin/sh\necho not the launcher\n", encoding="utf-8")
    os.chmod(script, 0o755)
    with pytest.raises(ServiceError) as raised:
        ensure_app(tmp_path / "home", script)
    assert "not a macOS program" in raised.value.message
    assert tools.commands == []


def test_the_launcher_is_the_flag_then_the_environment_then_none() -> None:
    """``--launcher`` wins over ``DOT_APP_LAUNCHER``; an empty name is none, and Dot.app is built with clang."""
    flagged: ServiceInstallCommand = ServiceInstallCommand(name="service-install", launcher="/flag/launcher")
    plain: ServiceInstallCommand = ServiceInstallCommand(name="service-install", launcher=None)
    environ: Mapping[str, str] = {LAUNCHER_ENV: "/env/launcher"}
    assert prebuilt_launcher(flagged, environ) == Path("/flag/launcher")
    assert prebuilt_launcher(plain, environ) == Path("/env/launcher")
    assert prebuilt_launcher(plain, {}) is None
    assert prebuilt_launcher(plain, {LAUNCHER_ENV: ""}) is None


def test_the_xcode_row_of_setup_needs_nothing_when_the_desktop_app_carries_the_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 575: ``dotx setup`` run by the desktop app does not tell the person to install tools nothing uses."""
    monkeypatch.setenv(LAUNCHER_ENV, "/Applications/Dot.app/Contents/Resources/tools/dot-launcher")
    row = _xcode_tools_row(True)
    assert row.ok and not row.required and "not needed" in row.detail


def _kept(home: Path) -> Path:
    return app_path(home) / "Contents" / "Resources" / "launcher.program"


def test_the_program_an_app_is_made_from_is_kept_in_it(tmp_path: Path, tools: Tools) -> None:
    """Item 575's review: a ``dotx service install`` typed in a terminal is given no launcher, so the app keeps the one
    it was made from, as the desktop app carried it."""
    home: Path = tmp_path / "home"
    home.mkdir()
    launcher: Path = launcher_at(tmp_path)
    assert not has_kept_launcher(home)
    ensure_app(home, launcher)
    assert _kept(home).read_bytes() == launcher.read_bytes()
    assert _kept(home).stat().st_mode & 0o777 == 0o755
    assert kept_launcher(app_path(home)) == launcher.read_bytes()
    assert has_kept_launcher(home)


def test_an_app_that_is_out_of_date_is_made_again_from_the_program_it_keeps_and_clang_is_not_run(
    tmp_path: Path, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The finding: after an upgrade that changed Dot's Info.plist, a ``dotx service install`` typed in a terminal on a
    Mac the desktop app installed Dot on fell back to clang, which that Mac has not. The desktop app's own program is
    not at hand there (``DOT_APP_LAUNCHER`` is set by the installer's steps alone): the app's kept one is used."""
    home: Path = tmp_path / "home"
    home.mkdir()
    launcher: Path = launcher_at(tmp_path)
    program: Path = ensure_app(home, launcher)
    launcher.unlink()
    monkeypatch.setattr(dot_app, "render_info_plist", lambda: b"<another-info-plist/>")
    tools.commands.clear()
    assert ensure_app(home) == program
    assert program.read_bytes() == MACHO + b"the prebuilt program"
    assert tools.names() == ["codesign"]
    assert (app_path(home) / "Contents" / "Info.plist").read_bytes() == b"<another-info-plist/>"
    assert kept_launcher(app_path(home)) == MACHO + b"the prebuilt program"
    # Current again: asking once more changes nothing.
    tools.commands.clear()
    assert ensure_app(home) == program
    assert tools.commands == []


def test_a_change_of_the_source_alone_leaves_an_app_made_from_a_prebuilt_program_as_it_is(
    tmp_path: Path, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    """That program did not come from this package's source, so the source does not decide: what macOS granted the app
    holds. The desktop app, which names the program, still has the source it records brought up to date."""
    home: Path = tmp_path / "home"
    home.mkdir()
    launcher: Path = launcher_at(tmp_path)
    program: Path = ensure_app(home, launcher)
    made: os.stat_result = program.stat()
    changed: Path = tmp_path / "dot_app.c"
    changed.write_text("int main(void) { return 1; }\n", encoding="utf-8")
    monkeypatch.setattr(dot_app, "SOURCE", changed)
    tools.commands.clear()
    assert ensure_app(home) == program
    assert tools.commands == []
    assert (program.stat().st_ino, program.stat().st_mtime_ns) == (made.st_ino, made.st_mtime_ns)
    assert ensure_app(home, launcher) == program
    assert tools.names() == ["codesign"]
    assert (app_path(home) / "Contents" / "Resources" / "dot_app.c").read_text(encoding="utf-8") == changed.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("damage", ["gone", "another program"])
def test_an_app_without_its_program_or_with_another_is_built_with_clang_when_it_is_out_of_date(
    tmp_path: Path, tools: Tools, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    """An app made before the program was kept, or one whose kept program is not the one its hash names, has nothing to
    be made again from."""
    home: Path = tmp_path / "home"
    home.mkdir()
    ensure_app(home, launcher_at(tmp_path))
    if damage == "gone":
        _kept(home).unlink()
    else:
        _kept(home).write_bytes(MACHO + b"a program that is not the one it was made from")
    assert kept_launcher(app_path(home)) is None and not has_kept_launcher(home)
    monkeypatch.setattr(dot_app, "render_info_plist", lambda: b"<another-info-plist/>")
    tools.commands.clear()
    ensure_app(home)
    assert tools.names() == ["clang", "codesign"]


def test_clang_that_fails_says_where_the_desktop_apps_program_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without Xcode's tools and without a program to make the app from, ``dotx service install`` stops with the step
    that fixes it: the tools, or the program the desktop app carries."""
    xcrun: Path = tmp_path / "xcrun"
    xcrun.write_text("#!/bin/sh\necho 'xcrun: error: invalid active developer path' >&2\nexit 1\n", encoding="utf-8")
    os.chmod(xcrun, 0o755)
    monkeypatch.setattr(dot_app, "_XCRUN", str(xcrun))
    home: Path = tmp_path / "home"
    home.mkdir()
    with pytest.raises(ServiceError) as raised:
        ensure_app(home)
    fix: str = str(raised.value.context["fix"])
    assert "xcode-select --install" in fix
    assert "--launcher" in fix and "Dot.app/Contents/Resources/tools/dot-launcher" in fix


def test_the_xcode_row_of_setup_needs_nothing_when_the_app_in_dots_home_keeps_its_program(
    tmp_path: Path, tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dotx setup`` typed in a terminal on an install of the desktop app has no ``DOT_APP_LAUNCHER`` either, and the
    service it installs after needs no clang: the row does not stop the setup for tools nothing uses."""
    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv(LAUNCHER_ENV, raising=False)
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setattr(dot_app, "_XCRUN", str(tmp_path / "no-such-xcrun"))
    ensure_app(home, launcher_at(tmp_path))
    row = _xcode_tools_row(True)
    assert row.ok and not row.required and "not needed" in row.detail
