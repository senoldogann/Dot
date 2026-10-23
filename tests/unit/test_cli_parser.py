"""The flags of ``dotx run`` and ``dotx prune`` as the parser takes and describes them (item 558)."""

import pytest

from dotagent.cli import build_parser, parse_command
from dotagent.cli_commands import RunCommand, ServiceInstallCommand


@pytest.mark.parametrize("browser", ["own", "attach"])
def test_a_standing_run_takes_the_browser_it_works_in(browser: str) -> None:
    """``--browser`` took no value at all: its choices were read from an alias that has none to give."""
    command = parse_command(build_parser(), ["run", "--standing", "--browser", browser, "tidy the downloads"])
    assert isinstance(command, RunCommand)
    assert command.standing and command.browser == browser


def test_a_browser_that_is_neither_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse_command(build_parser(), ["run", "--standing", "--browser", "elsewhere", "tidy the downloads"])
    assert "invalid choice: 'elsewhere' (choose from 'own', 'attach')" in capsys.readouterr().err


def test_the_help_of_a_run_lists_the_browsers(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--help"])
    assert "--browser {own,attach}" in capsys.readouterr().out


def test_service_install_takes_the_launcher_the_desktop_app_carries() -> None:
    """Item 575: ``--launcher`` names the prebuilt program for Dot.app; left out, it is none (and DOT_APP_LAUNCHER, or
    clang, decides)."""
    named = parse_command(build_parser(), ["service", "install", "--launcher", "/Applications/Dot.app/launcher"])
    assert isinstance(named, ServiceInstallCommand) and named.launcher == "/Applications/Dot.app/launcher"
    plain = parse_command(build_parser(), ["service", "install"])
    assert isinstance(plain, ServiceInstallCommand) and plain.launcher is None


def test_the_help_of_prune_names_everything_that_apply_deletes(capsys: pytest.CaptureFixture[str]) -> None:
    """An owner who reads ``--help`` to free screenshot space must see that the timelines of old runs and the journals
    that ``dotx undo`` takes changes back from go too."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["prune", "--help"])
    said: str = " ".join(capsys.readouterr().out.split())
    for part in ("events", "retention_days", "screenshot", "undo_keep_days", "no longer be taken back", "free space"):
        assert part in said, part
