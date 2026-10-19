"""What Tailscale's command line says of the Mac (item 271), as Settings › Phone reads it: whether it runs signed in,
the Mac's name on the tailnet, and whether it serves this daemon's port over HTTPS; which command line that is, and
what it said when it failed."""

import stat
from pathlib import Path

import pytest

from dotagent import tailnet
from dotagent.core.errors import PhoneSetupError
from dotagent.tailnet import first_link, running_name, serves, tailnet_state, tailscale_program, with_said

_STATUS: str = '{"BackendState": "Running", "Self": {"DNSName": "mac.tail1234.ts.net.", "HostName": "mac"}}'
_SERVED: str = (
    '{"TCP": {"443": {"HTTPS": true}}, "Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/": '
    '{"Proxy": "http://127.0.0.1:8765"}}}}}'
)


def test_the_status_says_whether_it_runs_and_the_macs_name() -> None:
    assert running_name(_STATUS) == (True, "mac.tail1234.ts.net")
    assert running_name('{"BackendState": "NeedsLogin", "Self": {"DNSName": ""}}') == (False, None)
    assert running_name('{"BackendState": "Stopped"}') == (False, None)
    with pytest.raises(PhoneSetupError):
        running_name("not json")


def test_serving_is_this_daemons_port_at_the_macs_name_over_https() -> None:
    assert serves(_SERVED, "mac.tail1234.ts.net", 8765)
    assert not serves(_SERVED, "mac.tail1234.ts.net", 8766)
    assert not serves(_SERVED, "other.tail1234.ts.net", 8765)
    assert not serves("", "mac.tail1234.ts.net", 8765)
    assert not serves("{}", "mac.tail1234.ts.net", 8765)


def test_what_tailscale_said_is_its_lines_joined_its_error_output_first() -> None:
    printed: bytes = (
        b"Serve is not enabled on your tailnet.\nTo enable, visit:\n\n    https://login.example/f/serve?node=n1\n"
    )
    assert with_said("Tailscale refused", printed, b"ignored") == (
        "Tailscale refused: Serve is not enabled on your tailnet. To enable, visit: "
        "https://login.example/f/serve?node=n1"
    )
    assert with_said("Tailscale refused", b"", b"out\n") == "Tailscale refused: out"
    assert with_said("Tailscale refused", b"  \n", b"") == "Tailscale refused"


def _program(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _unrunnable(path: Path) -> Path:
    """A file that is not a program: no one may run it."""
    path.write_text("not a program\n", encoding="utf-8")
    return path


def test_the_apps_command_line_comes_before_one_on_the_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOT_TAILSCALE", raising=False)
    (tmp_path / "bin").mkdir()
    on_path: Path = _program(tmp_path / "bin" / "tailscale")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(tailnet, "_APP_CLI", _program(tmp_path / "Tailscale"))
    assert tailscale_program() == str(tmp_path / "Tailscale")
    monkeypatch.setattr(tailnet, "_APP_CLI", tmp_path / "no-app")
    assert tailscale_program() == str(on_path)


async def test_a_command_line_that_cannot_start_is_a_problem_of_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_TAILSCALE", str(_unrunnable(tmp_path / "tailscale")))
    state = await tailnet_state(8765, (), ())
    assert (state.installed, state.running, state.problem) == (
        True,
        False,
        "Tailscale's command line could not be started: Permission denied",
    )


def _like_the_app(path: Path) -> Path:
    """A program that answers as the Tailscale app's own does: as the command line only when it is told to, else with
    the message of a window that could not start."""
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$TAILSCALE_BE_CLI" = "true" ]; then\n'
        '  echo \'{"BackendState": "Running", "Self": {"DNSName": "mac.tail1234.ts.net."}}\'\n'
        "else\n"
        "  echo 'The Tailscale GUI failed to start: The operation could not be completed.'\n"
        "fi\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


async def test_the_apps_program_is_asked_as_the_command_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAILSCALE_BE_CLI", raising=False)
    monkeypatch.setenv("DOT_TAILSCALE", str(_like_the_app(tmp_path / "Tailscale")))
    state = await tailnet_state(8765, (), ())
    assert (state.running, state.name, state.problem) == (True, "mac.tail1234.ts.net", None)


def test_the_page_tailscale_names_to_turn_a_feature_on_is_read_from_what_it_said() -> None:
    said: str = (
        "Tailscale refused: Serve is not enabled on your tailnet. To enable, visit: "
        "https://login.tailscale.com/f/serve?node=n123abc."
    )
    assert first_link(said) == "https://login.tailscale.com/f/serve?node=n123abc"
    assert first_link("Tailscale refused: something else") is None
