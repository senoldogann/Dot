"""The DevTools port of Dot's own Chrome (item 265): with ``[browser] debug_port`` set, Chrome takes DevTools
connections on that port of this Mac's loopback address besides Playwright's pipe, so that a run's scripts can drive
it with the Chrome DevTools Protocol. A port another program holds keeps Dot's Chrome from starting, so that a script
told the address never drives another Chrome.
"""

import errno
import socket

from dotagent.config import BrowserConfig
from dotagent.core.errors import BrowserError

LOOPBACK: str = "127.0.0.1"
# A program that holds the port without answering is given this long before the port counts as taken.
_PROBE_TIMEOUT_S: float = 0.5


def port_is_taken(port: int) -> bool:
    """Whether a program of this Mac holds ``port`` of the loopback address: one that listens there (also on every
    address), or one bound to it without listening, so that Chrome could not listen there itself."""
    try:
        with socket.create_connection((LOOPBACK, port), timeout=_PROBE_TIMEOUT_S):
            return True
    except TimeoutError:
        return True
    except ConnectionRefusedError:
        # Nothing listens there; a program may still be bound to it.
        pass
    # Bound as servers bind (Chromium's among them), so that a port the connections of a closed Chrome still wait on
    # is free, as it is to Chrome.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((LOOPBACK, port))
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                return True
            raise
    return False


def port_taken(port: int) -> BrowserError:
    """The error of a DevTools port another program holds."""
    return BrowserError(
        f"port {port} of this Mac is taken by another program, so Dot's Chrome cannot take DevTools connections there; "
        "choose another port in Settings › Browser, or 0 for none",
        {"debug_port": port},
    )


def devtools_switches(port: int) -> list[str]:
    """The switch that has Chrome listen for DevTools connections on ``port`` of the loopback address; none for 0."""
    return [f"--remote-debugging-port={port}"] if port else []


def devtools_address(config: BrowserConfig) -> str | None:
    """Where a run's scripts reach the Chrome of ``config`` with the DevTools Protocol: the address of Dot's own Chrome
    when a port is set, else ``None`` (the Chrome of the user is reached through Chrome's own switch)."""
    if config.mode != "own" or config.debug_port == 0:
        return None
    return f"http://{LOOPBACK}:{config.debug_port}"
