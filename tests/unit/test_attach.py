"""Finding the Chrome that is open: what its port file says, and the errors that tell the user what to do."""

import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from dotagent.browser.attach import (
    INSPECT_PAGE,
    ChromeState,
    DebugEndpoint,
    endpoint_url,
    inspect_chrome,
    parse_port_file,
    read_debug_endpoint,
    redact_endpoint,
)
from dotagent.browser.modes import CHROME_APP_ID, chrome_app_for
from dotagent.core.errors import BrowserError
from dotagent.core.paths import default_chrome_data_dir

_GUID: str = "5c1f0f6e-2b57-4d0a-9d3a-0f6b8d6f2e11"
_PATH: str = f"/devtools/browser/{_GUID}"
_SOURCE: Path = Path("/chrome/DevToolsActivePort")


def running_chrome(data_dir: Path) -> Path:
    """``data_dir`` as the data directory of a running Chrome: its lock names a live process (this one)."""
    os.symlink(f"testhost-{os.getpid()}", data_dir / "SingletonLock")
    return data_dir


def closed_port() -> int:
    """A port nothing listens on: one the system handed out a moment ago and that was given back."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


@pytest.fixture
def open_port() -> Iterator[int]:
    """A port something listens on, the way the debugging server of Chrome does; nobody is ever asked to click Allow."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        yield listener.getsockname()[1]


def test_a_port_file_names_the_port_and_the_browser_path() -> None:
    endpoint: DebugEndpoint = parse_port_file(f"9222\n{_PATH}\n", _SOURCE)
    assert endpoint.port == 9222
    assert endpoint_url(endpoint) == f"ws://127.0.0.1:9222{_PATH}"


def test_a_port_file_with_windows_line_endings_and_no_final_newline_is_read() -> None:
    endpoint: DebugEndpoint = parse_port_file(f"9222\r\n{_PATH}", _SOURCE)
    assert endpoint == DebugEndpoint(port=9222, path=_PATH)


def test_the_path_of_an_endpoint_stays_out_of_its_text_forms() -> None:
    endpoint: DebugEndpoint = parse_port_file(f"9222\n{_PATH}\n", _SOURCE)
    assert _GUID not in repr(endpoint)
    assert _GUID not in str(endpoint)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "9222",
        "9222\n",
        "9222\n\n",
        f"abc\n{_PATH}",
        f" \n{_PATH}",
        f"0\n{_PATH}",
        f"65536\n{_PATH}",
        f"-1\n{_PATH}",
        f"９２２２\n{_PATH}",
        "9222\n/json/version",
        "9222\n/devtools/browser/",
    ],
)
def test_a_port_file_that_names_no_browser_endpoint_is_refused_without_quoting_the_path(text: str) -> None:
    with pytest.raises(BrowserError, match="DevToolsActivePort") as raised:
        parse_port_file(text, _SOURCE)
    assert _GUID not in str(raised.value)


def test_redaction_removes_the_path_from_the_error_text_of_a_driver() -> None:
    text: str = f"WebSocket error: ws://127.0.0.1:9222{_PATH} 404 Not Found"
    assert redact_endpoint(text) == "WebSocket error: ws://127.0.0.1:9222/devtools/browser/<redacted> 404 Not Found"


@pytest.mark.parametrize("quote", ["'", '"'])
def test_redaction_stops_at_a_closing_quote(quote: str) -> None:
    text: str = f"connecting to {quote}ws://127.0.0.1:1{_PATH}{quote} failed"
    assert redact_endpoint(text) == f"connecting to {quote}ws://127.0.0.1:1/devtools/browser/<redacted>{quote} failed"


def test_redaction_leaves_text_without_a_path_alone() -> None:
    assert redact_endpoint("connect ECONNREFUSED 127.0.0.1:9222") == "connect ECONNREFUSED 127.0.0.1:9222"


def test_a_data_directory_without_a_running_chrome_says_to_open_chrome(tmp_path: Path) -> None:
    with pytest.raises(BrowserError, match="Chrome is not running; open Chrome") as raised:
        read_debug_endpoint(tmp_path)
    assert raised.value.context == {"data_dir": str(tmp_path)}


def test_a_chrome_that_does_not_allow_remote_debugging_is_told_where_to_switch_it_on(tmp_path: Path) -> None:
    with pytest.raises(BrowserError, match="does not allow remote debugging") as raised:
        read_debug_endpoint(running_chrome(tmp_path))
    assert INSPECT_PAGE in raised.value.message
    assert raised.value.context == {"data_dir": str(tmp_path), "pid": os.getpid()}


def test_a_chrome_that_allows_remote_debugging_is_found(tmp_path: Path, open_port: int) -> None:
    (tmp_path / "DevToolsActivePort").write_text(f"{open_port}\n{_PATH}\n", encoding="utf-8")
    assert read_debug_endpoint(running_chrome(tmp_path)) == DebugEndpoint(port=open_port, path=_PATH)


def test_a_port_file_left_over_from_an_earlier_start_counts_as_switched_off(tmp_path: Path) -> None:
    stale: int = closed_port()
    (tmp_path / "DevToolsActivePort").write_text(f"{stale}\n{_PATH}\n", encoding="utf-8")
    with pytest.raises(BrowserError, match="does not allow remote debugging") as raised:
        read_debug_endpoint(running_chrome(tmp_path))
    assert raised.value.context == {"data_dir": str(tmp_path), "pid": os.getpid(), "stale_port": stale}
    assert inspect_chrome(tmp_path) == ChromeState(running=True, remote_debugging=False, port=None)


def test_the_state_of_chrome_is_read_without_connecting_to_it(tmp_path: Path, open_port: int) -> None:
    assert inspect_chrome(tmp_path) == ChromeState(running=False, remote_debugging=False, port=None)
    running_chrome(tmp_path)
    assert inspect_chrome(tmp_path) == ChromeState(running=True, remote_debugging=False, port=None)
    (tmp_path / "DevToolsActivePort").write_text(f"{open_port}\n{_PATH}\n", encoding="utf-8")
    assert inspect_chrome(tmp_path) == ChromeState(running=True, remote_debugging=True, port=open_port)


def test_a_port_file_of_a_chrome_that_quit_is_not_believed(tmp_path: Path, open_port: int) -> None:
    (tmp_path / "DevToolsActivePort").write_text(f"{open_port}\n{_PATH}\n", encoding="utf-8")
    assert inspect_chrome(tmp_path) == ChromeState(running=False, remote_debugging=False, port=None)


def test_a_port_file_that_is_not_complete_yet_is_reported(tmp_path: Path) -> None:
    (tmp_path / "DevToolsActivePort").write_text("9222\n", encoding="utf-8")
    with pytest.raises(BrowserError, match="incomplete"):
        read_debug_endpoint(running_chrome(tmp_path))


def test_a_port_file_that_cannot_be_read_is_reported_with_its_path(tmp_path: Path) -> None:
    (tmp_path / "DevToolsActivePort").mkdir()
    with pytest.raises(BrowserError, match="could not be read") as raised:
        read_debug_endpoint(running_chrome(tmp_path))
    assert raised.value.context["path"] == str(tmp_path / "DevToolsActivePort")


def test_a_closed_chrome_is_opened_only_on_its_own_data_directory(tmp_path: Path) -> None:
    """Item 261: a switch to a closed Chrome opens Google Chrome for its own data directory, and no app for another
    (``DOT_CHROME_DIR``, as in the tests), so the tests never open the Chrome of the user."""
    assert chrome_app_for(default_chrome_data_dir()) == CHROME_APP_ID == "com.google.Chrome"
    assert chrome_app_for(tmp_path / "chrome") is None
