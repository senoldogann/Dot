"""Whether a phone reaches Dot away from home through Tailscale (item 271), and setting it up (docs/phone.md): Tailscale
runs on the Mac signed in, ``tailscale serve`` presents this daemon's port to the owner's tailnet as
``https://<mac>.<tailnet>.ts.net``, and ``[server] reach`` names that host, so the daemon serves requests for it.
"""

import asyncio
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from dotagent.core.errors import PhoneSetupError
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel

# The command line Tailscale's app for the Mac carries, used before a tailscale on the PATH. Run by a process with no
# terminal, as the daemon is, the app's program starts its window unless _AS_COMMAND_LINE says to answer as the command
# line (Homebrew's tailscale ignores it).
_APP_CLI: Path = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
_AS_COMMAND_LINE: dict[str, str] = {"TAILSCALE_BE_CLI": "1"}
_STATUS_TIMEOUT_S: float = 10.0
_SERVE_TIMEOUT_S: float = 30.0
_RUNNING: str = "Running"
_HTTPS_PORT: int = 443
_ERROR_CHARS: int = 400


class TailnetState(FrozenModel):
    """How a phone reaches Dot: ``installed``, Tailscale is on this Mac; ``running``, it runs signed in; ``name``, the
    Mac's name on the tailnet (``None`` while it is not known); ``served``, ``tailscale serve`` presents this daemon's
    port at ``https://<name>/``; ``reached``, the running daemon serves requests for that name (``[server] reach``);
    ``written``, the config file names it, which a daemon takes up at its start; ``problem``, what Tailscale said when
    it could not be asked (its app closed, a call that failed or took too long), ``None`` when it answered."""

    installed: bool
    running: bool
    name: str | None
    served: bool
    reached: bool
    written: bool
    problem: str | None


class _Peer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    DNSName: str = ""


class _Status(BaseModel):
    model_config = ConfigDict(extra="ignore")
    BackendState: str = ""
    Self: _Peer | None = None


class _Handler(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Proxy: str | None = None


class _Host(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Handlers: dict[str, _Handler] = {}


class _Serve(BaseModel):
    model_config = ConfigDict(extra="ignore")
    Web: dict[str, _Host] = {}


def tailscale_program() -> str | None:
    """Tailscale's command line on this Mac: the one ``DOT_TAILSCALE`` names, so that tests point at their own and never
    reach the owner's; else the app's own, else the one on the PATH (Homebrew's); ``None`` when there is none."""
    override: str | None = os.environ.get("DOT_TAILSCALE")
    if override is not None and override != "":
        return override if Path(override).is_file() else None
    if _APP_CLI.is_file():
        return str(_APP_CLI)
    return shutil.which("tailscale")


def running_name(status: str) -> tuple[bool, str | None]:
    """Whether ``tailscale status --json`` says it runs signed in, and the Mac's name on the tailnet."""
    try:
        parsed: _Status = _Status.model_validate_json(status)
    except ValidationError as error:
        raise PhoneSetupError(
            "Tailscale's status could not be read", {"status": truncate_middle(status, _ERROR_CHARS)}
        ) from error
    name: str = parsed.Self.DNSName.rstrip(".") if parsed.Self is not None else ""
    return parsed.BackendState == _RUNNING, name if name else None


def serves(serve_status: str, name: str, port: int) -> bool:
    """Whether ``tailscale serve status --json`` presents ``http://127.0.0.1:<port>`` at ``https://<name>/``."""
    try:
        parsed: _Serve = _Serve.model_validate_json(serve_status if serve_status.strip() else "{}")
    except ValidationError as error:
        raise PhoneSetupError(
            "what Tailscale serves could not be read", {"status": truncate_middle(serve_status, _ERROR_CHARS)}
        ) from error
    host: _Host | None = parsed.Web.get(f"{name}:{_HTTPS_PORT}")
    handler: _Handler | None = None if host is None else host.Handlers.get("/")
    return handler is not None and handler.Proxy in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")


def with_said(message: str, *printed: bytes) -> str:
    """``message`` with what Tailscale printed, its error output first, its lines joined: it says what it wants, such
    as a link to turn a feature on, on a line of its own."""
    for text in printed:
        said: str = " ".join(
            line.strip() for line in text.decode("utf-8", errors="replace").splitlines() if line.strip()
        )
        if said:
            return f"{message}: {truncate_middle(said, _ERROR_CHARS)}"
    return message


async def _tailscale(program: str, arguments: tuple[str, ...], timeout_s: float) -> str:
    """What ``tailscale <arguments>`` prints; :class:`PhoneSetupError`, with what it printed, when it cannot start,
    fails or takes too long, its process ended either way."""
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            program,
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **_AS_COMMAND_LINE},
        )
    except OSError as error:
        raise PhoneSetupError(
            f"Tailscale's command line could not be started: {error.strerror or error}",
            {"program": program, "arguments": list(arguments)},
        ) from error
    reading: asyncio.Task[tuple[bytes, bytes]] = asyncio.create_task(process.communicate())
    try:
        out, err = await asyncio.wait_for(asyncio.shield(reading), timeout_s)
    except TimeoutError as error:
        process.kill()
        # What it printed before it was ended, which may say what it was waiting for.
        out, err = await reading
        raise PhoneSetupError(
            with_said(f"Tailscale did not answer in {timeout_s:g} s", err, out),
            {"arguments": list(arguments), "timeout_s": timeout_s},
        ) from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        reading.cancel()
    if process.returncode != 0:
        raise PhoneSetupError(
            with_said("Tailscale refused", err, out),
            {
                "arguments": list(arguments),
                "returncode": process.returncode,
                "stderr": truncate_middle(err.decode("utf-8", errors="replace"), _ERROR_CHARS),
            },
        )
    return out.decode("utf-8", errors="replace")


async def tailnet_state(port: int, reach: tuple[str, ...], written: tuple[str, ...]) -> TailnetState:
    """How a phone reaches the daemon on ``port`` now: ``reach`` is the running daemon's ``[server] reach``, ``written``
    the config file's."""
    program: str | None = tailscale_program()
    if program is None:
        return TailnetState(
            installed=False, running=False, name=None, served=False, reached=False, written=False, problem=None
        )
    try:
        running, name = running_name(await _tailscale(program, ("status", "--json"), _STATUS_TIMEOUT_S))
    except PhoneSetupError as error:
        # Its command line reaches no Tailscale while the app is not open: Tailscale is not running, and says why.
        return TailnetState(
            installed=True, running=False, name=None, served=False, reached=False, written=False, problem=error.message
        )
    if not running or name is None:
        return TailnetState(
            installed=True, running=running, name=name, served=False, reached=False, written=False, problem=None
        )
    served, problem = await _served(program, name, port)
    return TailnetState(
        installed=True,
        running=True,
        name=name,
        served=served,
        reached=name.lower() in {host.lower() for host in reach},
        written=name.lower() in {host.lower() for host in written},
        problem=problem,
    )


async def _served(program: str, name: str, port: int) -> tuple[bool, str | None]:
    """Whether Tailscale serves the daemon's ``port`` at ``https://<name>/``, and what it said if it could not tell."""
    try:
        return serves(await _tailscale(program, ("serve", "status", "--json"), _STATUS_TIMEOUT_S), name, port), None
    except PhoneSetupError as error:
        return False, error.message


async def serve_daemon(port: int) -> None:
    """Have Tailscale present the daemon's ``port`` to the tailnet at ``https://<mac>.<tailnet>.ts.net``, in the
    background as Tailscale keeps it (``tailscale serve --bg``)."""
    program: str | None = tailscale_program()
    if program is None:
        raise PhoneSetupError("Tailscale is not on this Mac: install it from tailscale.com and sign in", {})
    await _tailscale(program, ("serve", "--bg", str(port)), _SERVE_TIMEOUT_S)
