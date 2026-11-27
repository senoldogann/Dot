"""cua-driver, the opt-in background driver of the desktop steps (item 2007): the steps that would take the Mac's own
pointer or keyboard are sent to the target application's window through it, in the background, with the pointer and the
front left alone.

Dot starts the driver as its own child, in the embedded mode its makers document for a host application
(``cua-driver serve --embedded --no-overlay --socket <private socket>``, then ``cua-driver mcp --embedded --socket``
as a proxy that speaks line-delimited JSON-RPC, MCP ``2025-06-18``, on its standard input and output). As a child it is
in Dot's responsibility chain, so macOS answers its Accessibility and Screen Recording checks with Dot's own grants
and it never asks for one of its own. Its telemetry is switched off in its environment.

Only the version measured on this Mac is used (:data:`PINNED_VERSION`), signed by Cua AI (:data:`TEAM_ID`): a driver
of another version or signature is refused, as its tools may have changed. Dot never installs it; ``dotx doctor`` says
the one command that does (:data:`INSTALL_COMMAND`).

This module holds what reads and builds the driver's messages (pure functions) and the adapter that runs it.
"""

import contextlib
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import JsonValue

from dotagent.config import DesktopConfig
from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.desktop.types import Point, Rect
from dotagent.tools.env_filter import hides_secret

_log = get_logger(__name__)

# The version measured on this Mac (item 2007) and the Developer ID team that signs cua-driver's releases.
PINNED_VERSION: str = "0.28.2"
TEAM_ID: str = "YCK386LBJ7"
# Where cua-driver's installer and its Homebrew cask put it.
DEFAULT_PATH: str = "/Applications/CuaDriver.app/Contents/MacOS/cua-driver"
# The pinned release, installed by cua-driver's own installer without wiring it into other coding agents and without
# its self-test; Homebrew's cask (cuadriver) installs only the latest release, which is not the measured one.
INSTALL_COMMAND: str = (
    f"curl -fsSL https://cua.ai/driver/install.sh | CUA_DRIVER_RS_VERSION={PINNED_VERSION} bash -s -- "
    "--no-connect --no-test"
)
_PROTOCOL: str = "2025-06-18"
# How long the driver may take to start (its socket, the proxy's answer), and one call: a capture of a large window or a
# long text may take seconds, and the driver watches a second after each step for a window it opened.
START_TIMEOUT_S: float = 15.0
# How long the check of the binary may take: codesign reads the whole 60 MB bundle (4 to 8 s on the owner's Mac).
CHECK_TIMEOUT_S: float = 60.0
CALL_TIMEOUT_S: float = 60.0
# The label of the driver's session that every call of Dot's repeats.
SESSION: str = "dot"
# How many pixels of a scroll one wheel notch of the driver moves (measured on the desktop fixture, item 2007), and
# the most notches one call takes.
PX_PER_NOTCH: int = 20
MAX_NOTCHES: int = 50
# The keys the driver names (its press_key's vocabulary), as Dot's chords name them.
KEYS: frozenset[str] = frozenset(
    {
        "return",
        "tab",
        "escape",
        "up",
        "down",
        "left",
        "right",
        "space",
        "delete",
        "home",
        "end",
        "pageup",
        "pagedown",
        *(f"f{number}" for number in range(1, 13)),
        *"abcdefghijklmnopqrstuvwxyz0123456789",
    }
)
# A Unix socket's path must fit in sun_path (104 bytes on macOS), so the private folder is a short one in /tmp.
_SOCKET_ROOT: str = "/tmp"
_SOCKET_POLL_S: float = 0.05
_STDERR_TAIL: int = 2000


type Arguments = Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CuaResult:
    """What a tool of the driver answered: its text, whether it is an error, and what it says of the delivery
    (``route`` accessibility or synthetic events, ``effect`` confirmed or unverifiable), with its structured part."""

    text: str
    is_error: bool
    route: str | None
    effect: str | None
    structured: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class CuaFrame:
    """The pixel frame of a window's last capture, in which the driver reads the x and y of a step: the window's
    bounds in screen points and the capture's width in pixels."""

    bounds: Rect
    width_px: int


@dataclass(frozen=True, slots=True)
class CuaBinary:
    """A cua-driver found on this Mac: where, its version and the team that signed it (``None``: not signed)."""

    path: Path
    version: str
    team_id: str | None


class BackgroundDriver(Protocol):
    """What the desktop's steps need of a background driver: one call of one of its tools."""

    def call(self, tool: str, arguments: Arguments) -> CuaResult: ...


class RunningDriver(BackgroundDriver, Protocol):
    """A background driver Dot started: whether it still runs, and its end."""

    @property
    def alive(self) -> bool: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------------------------- what it reads and says


def notches_of(amount_px: int) -> int:
    """The wheel notches a scroll of ``amount_px`` pixels takes, at least one and at most :data:`MAX_NOTCHES`."""
    return max(1, min(MAX_NOTCHES, round(amount_px / PX_PER_NOTCH)))


def frame_of(structured: Mapping[str, JsonValue]) -> CuaFrame:
    """The pixel frame of a capture from ``get_window_state``'s structured answer."""
    bounds: JsonValue = structured.get("window_bounds")
    width: JsonValue = structured.get("screenshot_width")
    if not isinstance(bounds, dict) or not isinstance(width, int) or width <= 0:
        raise DesktopError(
            "cua-driver gave no pixel frame for the window, so a point in it cannot be placed; nothing was sent",
            {"answer": json.dumps(structured)[:_STDERR_TAIL]},
        )
    values: list[float] = []
    for key in ("x", "y", "width", "height"):
        value: JsonValue = bounds.get(key)
        if not isinstance(value, int | float):
            raise DesktopError("cua-driver gave a window without bounds; nothing was sent", {"bounds": bounds})
        values.append(float(value))
    if values[2] <= 0:
        raise DesktopError("cua-driver gave a window of no width; nothing was sent", {"bounds": bounds})
    return CuaFrame(bounds=Rect(values[0], values[1], values[2], values[3]), width_px=width)


def pixel_of(point: Point, frame: CuaFrame) -> tuple[int, int]:
    """Where a screen point is in the window's capture, in its pixels: the driver reads a step's x and y there."""
    if not frame.bounds.contains(point):
        raise DesktopError(
            "the point is outside the window cua-driver captured; nothing was sent",
            {"x": point.x, "y": point.y, "window": [frame.bounds.x, frame.bounds.y, frame.bounds.width]},
        )
    scale: float = frame.width_px / frame.bounds.width
    return round((point.x - frame.bounds.x) * scale), round((point.y - frame.bounds.y) * scale)


def result_of(message: Mapping[str, JsonValue], tool: str) -> CuaResult:
    """A tool's answer from its JSON-RPC message. A JSON-RPC error raises: the call did not reach the tool."""
    error: JsonValue = message.get("error")
    if error is not None:
        raise DesktopError(f"cua-driver refused the call of {tool}", {"tool": tool, "error": error})
    result: JsonValue = message.get("result")
    if not isinstance(result, dict):
        raise DesktopError(f"cua-driver answered {tool} without a result", {"tool": tool, "message": dict(message)})
    content: JsonValue = result.get("content")
    texts: list[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(str(part["text"]))
    structured: JsonValue = result.get("structuredContent")
    facts: Mapping[str, JsonValue] = structured if isinstance(structured, dict) else {}
    route: JsonValue = facts.get("route")
    effect: JsonValue = facts.get("effect")
    return CuaResult(
        text=" ".join(" ".join(texts).split()),
        is_error=result.get("isError") is True,
        route=route if isinstance(route, str) else None,
        effect=effect if isinstance(effect, str) else None,
        structured=facts,
    )


def version_of(manifest: str) -> str:
    """The version ``cua-driver manifest`` reports."""
    try:
        parsed: JsonValue = json.loads(manifest)
    except json.JSONDecodeError as error:
        raise DesktopError("cua-driver's manifest is not JSON", {"output": manifest[:_STDERR_TAIL]}) from error
    version: JsonValue = parsed.get("binary_version") if isinstance(parsed, dict) else None
    if not isinstance(version, str):
        raise DesktopError("cua-driver's manifest names no version", {"output": manifest[:_STDERR_TAIL]})
    return version


def team_of(codesign_report: str) -> str | None:
    """The TeamIdentifier ``codesign -dv`` reports, ``None`` for an unsigned or ad-hoc signed file."""
    for line in codesign_report.splitlines():
        if line.startswith("TeamIdentifier="):
            team: str = line.removeprefix("TeamIdentifier=").strip()
            return None if team in ("", "not set") else team
    return None


def refusal_of(binary: CuaBinary) -> str | None:
    """Why Dot does not use ``binary`` (another version than the one measured, another signer), ``None`` when it may."""
    if binary.version != PINNED_VERSION:
        return (
            f"cua-driver {binary.version} is at {binary.path}, and Dot is measured with {PINNED_VERSION} only; install "
            f"that one: {INSTALL_COMMAND}"
        )
    if binary.team_id != TEAM_ID:
        return f"cua-driver at {binary.path} is not signed by Cua AI (team {TEAM_ID}): its team is {binary.team_id}"
    return None


# ------------------------------------------------------------------------------------------------- the binary on disk


def _signed_path(path: Path) -> Path:
    """What carries the signature: the application bundle of a binary inside one, else the binary itself."""
    for parent in path.parents:
        if parent.suffix == ".app":
            return parent
    return path


def _run(command: Sequence[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as error:
        raise DesktopError(
            f"{command[0]} did not finish within {timeout_s:g} s", {"command": list(command), "timeout_s": timeout_s}
        ) from error
    except OSError as error:
        raise DesktopError(f"{command[0]} could not be run: {error}", {"command": list(command)}) from error


def version_at(path: Path, timeout_s: float) -> str:
    """The version of the cua-driver at ``path``, from its manifest. Raises when it is not there or does not answer."""
    if not path.is_file() or not os.access(path, os.X_OK):
        raise DesktopError(
            f"cua-driver is not installed at {path}; install it with: {INSTALL_COMMAND}",
            {"path": str(path), "fix": INSTALL_COMMAND},
        )
    manifest: subprocess.CompletedProcess[str] = _run([str(path), "manifest"], timeout_s)
    if manifest.returncode != 0:
        raise DesktopError(
            "cua-driver did not report its version",
            {"path": str(path), "code": manifest.returncode, "stderr": manifest.stderr[-_STDERR_TAIL:]},
        )
    return version_of(manifest.stdout)


def signer_at(path: Path, timeout_s: float) -> str | None:
    """The team whose signature of the cua-driver at ``path`` verifies, ``None`` when none does."""
    signed: Path = _signed_path(path)
    verified: subprocess.CompletedProcess[str] = _run(
        ["/usr/bin/codesign", "--verify", "--strict", str(signed)], timeout_s
    )
    if verified.returncode != 0:
        return None
    report: subprocess.CompletedProcess[str] = _run(["/usr/bin/codesign", "-dv", "--verbose=2", str(signed)], timeout_s)
    return team_of(report.stderr)


def find_binary(path: Path, timeout_s: float) -> CuaBinary:
    """The cua-driver at ``path``: its version and the team of its verified signature."""
    version: str = version_at(path, timeout_s)
    return CuaBinary(path=path, version=version, team_id=signer_at(path, timeout_s))


# ------------------------------------------------------------------------------------------------------- the adapter


def _child_env() -> dict[str, str]:
    """The driver's environment: Dot's own without what holds a secret (as a command of a run is given it, items 555
    and 1293), embedded mode, its telemetry and its update check off."""
    return {
        **{name: value for name, value in os.environ.items() if not hides_secret(name, value)},
        "CUA_DRIVER_EMBEDDED": "1",
        "CUA_DRIVER_RS_TELEMETRY_ENABLED": "false",
        "CUA_DRIVER_RS_UPDATE_CHECK": "0",
    }


class CuaDriver:
    """A cua-driver daemon of Dot's own and the MCP proxy Dot speaks to it through; one call at a time. A call that
    fails on the way (no answer in time, the proxy gone) closes both: what it did is not known, and it is never sent
    again by Dot (the driver's own rule), so the next batch starts a new driver."""

    def __init__(self, binary: Path, start_timeout_s: float, call_timeout_s: float) -> None:
        self._call_timeout_s: float = call_timeout_s
        self._lock: threading.Lock = threading.Lock()
        self._next_id: int = 0
        self._buffer: bytes = b""
        self._folder: Path = Path(tempfile.mkdtemp(prefix="dotcua.", dir=_SOCKET_ROOT))
        self._closed: bool = False
        socket: Path = self._folder / "s"
        env: dict[str, str] = _child_env()
        self._serve_log: Path = self._folder / "serve.log"
        with self._serve_log.open("wb") as log:
            self._serve: subprocess.Popen[bytes] = subprocess.Popen(
                [str(binary), "serve", "--embedded", "--no-overlay", "--socket", str(socket)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
        self._proxy: subprocess.Popen[bytes] | None = None
        try:
            self._await_socket(socket, start_timeout_s)
            self._proxy = subprocess.Popen(
                [str(binary), "mcp", "--embedded", "--socket", str(socket)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            self._request(
                "initialize",
                "initialize",
                {
                    "protocolVersion": _PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "dot", "version": PINNED_VERSION},
                },
                start_timeout_s,
            )
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.close()
            raise
        _log.info("cua_driver_started", pid=self._serve.pid, socket=str(socket))

    @property
    def alive(self) -> bool:
        """Whether the daemon and the proxy still run and no call broke them."""
        return (
            not self._closed and self._serve.poll() is None and self._proxy is not None and self._proxy.poll() is None
        )

    def call(self, tool: str, arguments: Arguments) -> CuaResult:
        """Call one of the driver's tools; raises when it does not answer, or answers with an error."""
        with self._lock:
            if not self.alive:
                raise DesktopError("cua-driver is not running; nothing was sent", {"tool": tool, **self._tail()})
            began: float = time.monotonic()
            try:
                message: Mapping[str, JsonValue] = self._request(
                    "tools/call", tool, {"name": tool, "arguments": dict(arguments)}, self._call_timeout_s
                )
            except DesktopError:
                self.close()
                raise
        result: CuaResult = result_of(message, tool)
        _log.info(
            "cua_driver_call",
            tool=tool,
            seconds=round(time.monotonic() - began, 3),
            route=result.route,
            effect=result.effect,
            error=result.is_error,
        )
        if result.is_error:
            raise DesktopError(
                f"cua-driver could not do {tool}: {result.text}",
                {"tool": tool, "answer": json.dumps(result.structured)[:_STDERR_TAIL]},
            )
        return result

    def close(self) -> None:
        """Stop the proxy and the daemon and remove the private folder."""
        self._closed = True
        for process in (self._proxy, self._serve):
            if process is None or process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if self._proxy is not None and self._proxy.stdin is not None:
            with contextlib.suppress(OSError):
                self._proxy.stdin.close()
        shutil.rmtree(self._folder, ignore_errors=True)

    # -- the wire

    def _tail(self) -> dict[str, JsonValue]:
        try:
            told: str = self._serve_log.read_text(encoding="utf-8", errors="replace")[-_STDERR_TAIL:]
        except FileNotFoundError:
            told = ""
        return {"serve_log": told, "serve_code": self._serve.poll()}

    def _await_socket(self, socket: Path, timeout_s: float) -> None:
        deadline: float = time.monotonic() + timeout_s
        while not socket.is_socket():
            if self._serve.poll() is not None:
                raise DesktopError("cua-driver stopped as it started", {"socket": str(socket), **self._tail()})
            if time.monotonic() > deadline:
                raise DesktopError(
                    f"cua-driver did not open its socket within {timeout_s:g} s",
                    {"socket": str(socket), **self._tail()},
                )
            time.sleep(_SOCKET_POLL_S)

    def _send(self, message: Mapping[str, JsonValue]) -> None:
        proxy: subprocess.Popen[bytes] | None = self._proxy
        if proxy is None or proxy.stdin is None:
            raise DesktopError("cua-driver's proxy is not running", self._tail())
        try:
            proxy.stdin.write(json.dumps(message).encode() + b"\n")
            proxy.stdin.flush()
        except OSError as error:
            raise DesktopError("cua-driver's proxy closed its input", {"error": str(error), **self._tail()}) from error

    def _request(
        self, method: str, what: str, params: Mapping[str, JsonValue], timeout_s: float
    ) -> Mapping[str, JsonValue]:
        """Send a request and read until its answer; ``what`` names it in an error (the tool of a call)."""
        self._next_id += 1
        wanted: int = self._next_id
        self._send({"jsonrpc": "2.0", "id": wanted, "method": method, "params": dict(params)})
        deadline: float = time.monotonic() + timeout_s
        while True:
            line: bytes = self._read_line(deadline, what)
            try:
                message: JsonValue = json.loads(line)
            except json.JSONDecodeError as error:
                raise DesktopError(
                    "cua-driver's proxy wrote a line that is not JSON",
                    {"line": line[:_STDERR_TAIL].decode(errors="replace")},
                ) from error
            if isinstance(message, dict) and message.get("id") == wanted:
                return message

    def _read_line(self, deadline: float, what: str) -> bytes:
        proxy: subprocess.Popen[bytes] | None = self._proxy
        if proxy is None or proxy.stdout is None:
            raise DesktopError("cua-driver's proxy is not running", self._tail())
        stream = proxy.stdout
        with selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_READ)
            while b"\n" not in self._buffer:
                left: float = deadline - time.monotonic()
                if left <= 0 or not selector.select(left):
                    raise DesktopError(
                        f"cua-driver did not answer {what} in time; what it did is not known",
                        {"call": what, **self._tail()},
                    )
                chunk: bytes = os.read(stream.fileno(), 65536)
                if not chunk:
                    raise DesktopError(f"cua-driver's proxy ended while {what} waited", {"call": what, **self._tail()})
                self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line


def open_driver(path: Path, start_timeout_s: float, call_timeout_s: float) -> CuaDriver:
    """The cua-driver at ``path``, started, once it is the pinned version signed by Cua AI; raises otherwise."""
    version: str = version_at(path, CHECK_TIMEOUT_S)
    if version != PINNED_VERSION:
        # Another version is refused before its signature is read, which takes seconds.
        unread: CuaBinary = CuaBinary(path=path, version=version, team_id=None)
        raise DesktopError(str(refusal_of(unread)), {"path": str(path), "version": version})
    binary: CuaBinary = CuaBinary(path=path, version=version, team_id=signer_at(path, CHECK_TIMEOUT_S))
    refusal: str | None = refusal_of(binary)
    if refusal is not None:
        raise DesktopError(refusal, {"path": str(path), "version": binary.version, "team": binary.team_id})
    return CuaDriver(path, start_timeout_s, call_timeout_s)


def starter_of(config: DesktopConfig) -> Callable[[], RunningDriver] | None:
    """What starts the background driver ``config`` names, ``None`` when it is off."""
    if config.background_driver == "off":
        return None

    def start() -> RunningDriver:
        return open_driver(Path(config.background_driver_path).expanduser(), START_TIMEOUT_S, CALL_TIMEOUT_S)

    return start


# ------------------------------------------------------------------------------------------------ the calls it is sent


def capture_args(pid: int, window_id: int, bounds: Rect, out_file: Path) -> dict[str, JsonValue]:
    """A capture of the window alone, about a pixel per point, written to ``out_file``: it sets the frame the driver
    reads the next step's x and y in."""
    return {
        "pid": pid,
        "window_id": window_id,
        "include_accessibility_tree": False,
        "max_dimension": max(1, round(max(bounds.width, bounds.height))),
        "screenshot_out_file": str(out_file),
        "session": SESSION,
    }


def click_args(
    pid: int, window_id: int, pixel: tuple[int, int], button: Literal["left", "right"], count: int
) -> dict[str, JsonValue]:
    return {
        "pid": pid,
        "window_id": window_id,
        "x": pixel[0],
        "y": pixel[1],
        "button": button,
        "count": count,
        "delivery_mode": "background",
        "session": SESSION,
    }


def type_args(pid: int, window_id: int, text: str) -> dict[str, JsonValue]:
    return {"pid": pid, "window_id": window_id, "text": text, "delivery_mode": "background", "session": SESSION}


def key_args(pid: int, window_id: int, key: str, modifiers: Sequence[str]) -> dict[str, JsonValue]:
    return {
        "pid": pid,
        "window_id": window_id,
        "key": key,
        "modifiers": list(modifiers),
        "delivery_mode": "background",
        "session": SESSION,
    }


def scroll_args(pid: int, window_id: int, pixel: tuple[int, int], direction: str, notches: int) -> dict[str, JsonValue]:
    return {
        "pid": pid,
        "window_id": window_id,
        "x": pixel[0],
        "y": pixel[1],
        "direction": direction,
        "amount": notches,
        "by": "line",
        "delivery_mode": "background",
        "session": SESSION,
    }
