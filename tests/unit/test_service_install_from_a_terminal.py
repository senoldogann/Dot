"""``dotx service install`` typed in a terminal on an install of the desktop app (item 575's review).

The desktop app sets ``DOT_APP_TOOLS`` and ``DOT_APP_LAUNCHER`` for its own steps only. The README tells the owner of an
app install to put ``runtime/bin`` on their ``PATH`` and use ``dotx``, and ``dotx setup`` (which installs the service
unless told not to) or ``dotx service install`` then runs with neither: the job's ``PATH`` lost the app's ``rg``, and an
app that was out of date fell back to clang, which a Mac with no Xcode tools has not. The command is run here against
stand-ins of launchd, of the daemon's health check and of clang and codesign: nothing of this Mac's launchd, daemon or
Xcode is touched."""

import os
import plistlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dotagent import cli_daemon, dot_app
from dotagent.cli_commands import ServiceInstallCommand
from dotagent.cli_daemon import cmd_service_install
from dotagent.dot_app import app_path, app_program, ensure_app, tools_path
from dotagent.launchd import SERVICE_PATH, ServiceStatus
from dotagent.server.views import HealthView

# The first bytes of a universal Mach-O program, then the program the desktop app carried.
PREBUILT: bytes = b"\xca\xfe\xba\xbe" + b"the program the desktop app carried"
INSTALL: ServiceInstallCommand = ServiceInstallCommand(name="service-install", launcher=None)


@dataclass
class _World:
    """Dot's home, what the command asked of clang and codesign, and each property list it was told to load."""

    home: Path
    asked: list[list[str]] = field(default_factory=list)
    plists: list[bytes] = field(default_factory=list)

    def tools_run(self) -> list[str]:
        return [Path(words[0]).name if "clang" not in words else "clang" for words in self.asked]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    """A home of this test's own, with the command's contacts with the world replaced: no ``launchctl``, no request to a
    daemon, no property list in the real ``LaunchAgents`` folder, and clang and codesign as the unit tests of the
    launcher have them. The variables only the desktop app's steps set are not set."""
    home: Path = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    for name in ("DOT_APP_TOOLS", "DOT_APP_LAUNCHER"):
        monkeypatch.delenv(name, raising=False)
    seen: _World = _World(home)

    async def status(path: Path, uid: int) -> ServiceStatus:
        return ServiceStatus(plist=str(path), installed=False, job=None)

    async def nobody(url: str) -> None:
        return None

    async def install(path: Path, content: bytes, uid: int) -> bool:
        seen.plists.append(content)
        return False

    async def answers(url: str, seconds: float) -> HealthView:
        return HealthView(ok=True, version="0.0.0-test")

    def run(arguments: list[str], failure: str, fix: str) -> None:
        seen.asked.append(arguments)
        if "clang" in arguments:
            Path(arguments[arguments.index("-o") + 1]).write_bytes(b"\xca\xfe\xba\xbe" + b"built by clang")
        elif arguments[0].endswith("codesign"):
            (Path(arguments[-1]) / "Contents" / "_CodeSignature").mkdir()

    monkeypatch.setattr(cli_daemon, "_plist_path", lambda: tmp_path / "LaunchAgents" / "dev.dotagent.dot.plist")
    monkeypatch.setattr(cli_daemon, "service_status", status)
    monkeypatch.setattr(cli_daemon, "probe_daemon", nobody)
    monkeypatch.setattr(cli_daemon, "install_job", install)
    monkeypatch.setattr(cli_daemon, "wait_for_daemon", answers)
    monkeypatch.setattr(dot_app, "_run", run)
    return seen


@pytest.fixture
def app_install(world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    """What the desktop app's install left in Dot's home: the folder of links to its tools, and the Dot.app that its
    installer made from the program it carries, which is not at hand any more. Then an upgrade changes the app's
    Info.plist, so that the app is out of date: before, clang was the way left."""
    tools_path(world.home).mkdir(parents=True)
    carried: Path = tmp_path / "dot-launcher"
    carried.write_bytes(PREBUILT)
    os.chmod(carried, 0o755)
    ensure_app(world.home, carried)
    carried.unlink()
    monkeypatch.setattr(dot_app, "render_info_plist", lambda: b"<an-info-plist-of-a-later-dot/>")
    world.asked.clear()
    return world


async def test_a_service_installed_from_a_terminal_keeps_the_apps_tools_and_its_launcher(app_install: _World) -> None:
    assert await cmd_service_install(INSTALL) == 0
    (written,) = app_install.plists
    job = plistlib.loads(written)
    assert job["EnvironmentVariables"]["PATH"] == f"{tools_path(app_install.home)}:{SERVICE_PATH}"
    assert job["ProgramArguments"][0] == str(app_program(app_path(app_install.home)))
    assert app_program(app_path(app_install.home)).read_bytes() == PREBUILT
    assert app_install.tools_run() == ["codesign"], app_install.asked


async def test_a_service_installed_on_a_checkout_has_the_standard_path_and_an_app_built_with_clang(
    world: _World,
) -> None:
    """No folder of the app's tools in Dot's home, so none is put on the path; nothing was made from a prebuilt program,
    so clang builds the app, as it did."""
    assert await cmd_service_install(INSTALL) == 0
    (written,) = world.plists
    assert plistlib.loads(written)["EnvironmentVariables"]["PATH"] == SERVICE_PATH
    assert world.tools_run() == ["clang", "codesign"]
