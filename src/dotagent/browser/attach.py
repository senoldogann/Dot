"""Finding the Chrome that is already open, so Dot works in a tab of it instead of starting a browser of its own.

Chrome refuses ``--remote-debugging-port`` on its default data directory (since version 136). Since version 144 the same
thing is a switch the user flips at ``chrome://inspect/#remote-debugging`` ("Allow remote debugging for this browser
instance"). With it on, Chrome writes ``DevToolsActivePort`` into its data directory, the port on the first line and the
WebSocket path of the browser on the second, and it asks the user to click Allow when a client connects (the connection
waits for the answer). The path is a capability, since whoever knows it may ask to connect: it is used to build the
endpoint and is never printed, logged or put into an error (``redact_endpoint`` removes it from driver messages).
"""

import re
import socket
from pathlib import Path

from pydantic import Field

from dotagent.browser.profile_lock import profile_holder_pid
from dotagent.core.errors import BrowserError
from dotagent.core.types import FrozenModel

INSPECT_PAGE: str = "chrome://inspect/#remote-debugging"

_PORT_FILE: str = "DevToolsActivePort"
_BROWSER_PATH_PREFIX: str = "/devtools/browser/"
_LOOPBACK: str = "127.0.0.1"
_MAX_PORT: int = 65_535
_SHOWN_CHARS: int = 20
_CONNECT_TIMEOUT_S: float = 0.5
_BROWSER_PATH: re.Pattern[str] = re.compile(r"/devtools/browser/[^\s'\"<>]+")
_REDACTED: str = "/devtools/browser/<redacted>"
CHROME_NOT_RUNNING: str = "Chrome is not running; open Chrome and try again"
_NOT_ALLOWED: str = (
    f"Chrome does not allow remote debugging; open {INSPECT_PAGE} in Chrome, switch on 'Allow remote debugging for "
    "this browser instance' and try again"
)


class DebugEndpoint(FrozenModel):
    """Where the open Chrome listens for a debugger. ``path`` is secret, so it stays out of ``repr``."""

    port: int
    path: str = Field(repr=False)


def parse_port_file(text: str, source: Path) -> DebugEndpoint:
    """The endpoint that the contents of a ``DevToolsActivePort`` file name: the port, then the browser path."""
    lines: list[str] = text.splitlines()
    if len(lines) < 2:
        raise BrowserError(
            "the DevToolsActivePort file of Chrome is incomplete", {"path": str(source), "lines": len(lines)}
        )
    port_text: str = lines[0].strip()
    path: str = lines[1].strip()
    if not (port_text.isascii() and port_text.isdigit() and 0 < int(port_text) <= _MAX_PORT):
        raise BrowserError(
            "the DevToolsActivePort file of Chrome names no port",
            {"path": str(source), "first_line": port_text[:_SHOWN_CHARS]},
        )
    if not path.startswith(_BROWSER_PATH_PREFIX) or path == _BROWSER_PATH_PREFIX:
        raise BrowserError("the DevToolsActivePort file of Chrome names no browser endpoint", {"path": str(source)})
    return DebugEndpoint(port=int(port_text), path=path)


def endpoint_url(endpoint: DebugEndpoint) -> str:
    """The WebSocket address a debugger connects to. It holds the secret path: never print or log it."""
    return f"ws://{_LOOPBACK}:{endpoint.port}{endpoint.path}"


def redact_endpoint(text: str) -> str:
    """``text`` without any browser WebSocket path in it; driver errors quote the address they connected to."""
    return _BROWSER_PATH.sub(_REDACTED, text)


def _listening(port: int) -> bool:
    """Whether something answers on the port. Chrome never removes its port file, so one is left behind when the
    switch is turned off or Chrome quits; a plain connection tells the two apart. Chrome asks the user only at the
    WebSocket handshake, never for a connection that is closed again at once."""
    try:
        with socket.create_connection((_LOOPBACK, port), timeout=_CONNECT_TIMEOUT_S):
            return True
    except OSError:
        return False


class ChromeState(FrozenModel):
    """Whether the Chrome on a data directory runs and, when it allows remote debugging now, on which port it
    listens."""

    running: bool
    remote_debugging: bool
    port: int | None


def inspect_chrome(data_dir: Path) -> ChromeState:
    """The state of the Chrome that runs on ``data_dir``. A port file left behind by a Chrome that is not running, or
    that stopped listening, counts for nothing."""
    if profile_holder_pid(data_dir) is None:
        return ChromeState(running=False, remote_debugging=False, port=None)
    port_file: Path = data_dir / _PORT_FILE
    try:
        text: str = port_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ChromeState(running=True, remote_debugging=False, port=None)
    except OSError as error:
        raise BrowserError(
            "the DevToolsActivePort file of Chrome could not be read", {"path": str(port_file), "error": str(error)}
        ) from error
    port: int = parse_port_file(text, port_file).port
    if not _listening(port):
        return ChromeState(running=True, remote_debugging=False, port=None)
    return ChromeState(running=True, remote_debugging=True, port=port)


def read_debug_endpoint(data_dir: Path) -> DebugEndpoint:
    """Where the Chrome that runs on ``data_dir`` listens. Raises when Chrome is not running or does not allow remote
    debugging; the message says what to do."""
    holder: int | None = profile_holder_pid(data_dir)
    if holder is None:
        raise BrowserError(CHROME_NOT_RUNNING, {"data_dir": str(data_dir)})
    port_file: Path = data_dir / _PORT_FILE
    try:
        text: str = port_file.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise BrowserError(_NOT_ALLOWED, {"data_dir": str(data_dir), "pid": holder}) from error
    except OSError as error:
        raise BrowserError(
            "the DevToolsActivePort file of Chrome could not be read", {"path": str(port_file), "error": str(error)}
        ) from error
    endpoint: DebugEndpoint = parse_port_file(text, port_file)
    if not _listening(endpoint.port):
        raise BrowserError(_NOT_ALLOWED, {"data_dir": str(data_dir), "pid": holder, "stale_port": endpoint.port})
    return endpoint
