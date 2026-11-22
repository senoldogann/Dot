"""The Mac's own view of the phone and the way to start its WebDriverAgent runner: ``xcrun devicectl``.

The daemon asks it only two things: is the phone listed as available to this Mac, and launch the runner app (opening
it starts the server). Both go through :class:`DeviceControl`, so that a test supplies its own and no process starts.
The output of ``devicectl list devices`` is read as the JSON it writes with ``--json-output``.
"""

import asyncio
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from dotagent.core.errors import PhoneError
from dotagent.core.types import FrozenModel

_LIST_TIMEOUT_S: float = 30.0
_LAUNCH_TIMEOUT_S: float = 60.0
_OUTPUT_CHARS: int = 600
_UNAVAILABLE: str = "unavailable"


class CommandResult(FrozenModel):
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Runs one program to its end within ``timeout_s`` seconds."""

    async def __call__(self, argv: Sequence[str], timeout_s: float) -> CommandResult: ...


class DeviceControl(Protocol):
    """What the daemon asks of the Mac about the phone."""

    async def is_available(self, udid: str) -> bool: ...

    async def launch(self, udid: str, bundle: str) -> None: ...


# The parts of ``devicectl list devices --json-output`` that Dot reads, each required: every device of the owner's Mac's
# listing had all of them (an Apple Watch, iPhones and an iPad, 2026-11-15), and a listing of another shape fails loudly
# rather than reading as no phone or as one in reach.
class _Connection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tunnelState: str


class _Hardware(BaseModel):
    model_config = ConfigDict(extra="ignore")

    udid: str


class _Device(BaseModel):
    model_config = ConfigDict(extra="ignore")

    identifier: str
    hardwareProperties: _Hardware
    connectionProperties: _Connection


class _Result(BaseModel):
    model_config = ConfigDict(extra="ignore")

    devices: list[_Device]


class _Listing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: _Result


def device_available(listing: str, udid: str) -> bool:
    """Whether the JSON ``listing`` of ``devicectl list devices`` has the device ``udid`` and says it is not
    unavailable (a phone that is plugged in, or on the network and awake, is). Raises :class:`PhoneError` for a listing
    that is not what devicectl writes."""
    try:
        parsed: _Listing = _Listing.model_validate_json(listing)
    except ValueError as error:
        raise PhoneError(
            "the list of devices devicectl wrote is not JSON of the shape known", {"head": listing[:_OUTPUT_CHARS]}
        ) from error
    return any(
        udid in (device.identifier, device.hardwareProperties.udid)
        and device.connectionProperties.tunnelState != _UNAVAILABLE
        for device in parsed.result.devices
    )


def launch_argv(udid: str, bundle: str) -> tuple[str, ...]:
    """The command that opens ``bundle`` on the phone, closing it first when it runs."""
    return ("xcrun", "devicectl", "device", "process", "launch", "--device", udid, "--terminate-existing", bundle)


def list_argv(output: Path) -> tuple[str, ...]:
    return ("xcrun", "devicectl", "list", "devices", "--json-output", str(output))


async def run_command(argv: Sequence[str], timeout_s: float) -> CommandResult:
    """The real :class:`CommandRunner`: the program as a child process, killed when it outlives ``timeout_s``."""
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as error:
        # ``xcrun`` missing, or no process to spare: the keeper's own error, not one that ends its loop (item 1313).
        raise PhoneError("a command could not be started", {"argv": list(argv), "error": str(error)}) from error
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise PhoneError("a command did not end in time", {"argv": list(argv), "timeout_s": timeout_s}) from error
    returncode: int | None = process.returncode
    if returncode is None:
        raise PhoneError("a command ended without an exit status", {"argv": list(argv)})
    # Their words go into an error's context only: a byte that is no UTF-8 is shown as a replacement mark there.
    return CommandResult(
        returncode=returncode, stdout=out.decode(errors="replace"), stderr=err.decode(errors="replace")
    )


class XcrunDevicectl:
    """:class:`DeviceControl` over ``xcrun devicectl``, through ``runner``."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner: CommandRunner = runner

    async def is_available(self, udid: str) -> bool:
        try:
            with tempfile.TemporaryDirectory(prefix="dot-devicectl-") as folder:
                output: Path = Path(folder) / "devices.json"
                result: CommandResult = await self._runner(list_argv(output), _LIST_TIMEOUT_S)
                if result.returncode != 0:
                    raise PhoneError(
                        "devicectl could not list the devices",
                        {"returncode": result.returncode, "stderr": result.stderr[:_OUTPUT_CHARS]},
                    )
                listing: str = await asyncio.to_thread(output.read_text, encoding="utf-8")
        except OSError as error:
            # A folder that cannot be made, or a listing devicectl wrote nowhere or unreadably (item 1313).
            raise PhoneError("devicectl's list of devices could not be read", {"error": str(error)}) from error
        return device_available(listing, udid)

    async def launch(self, udid: str, bundle: str) -> None:
        result: CommandResult = await self._runner(launch_argv(udid, bundle), _LAUNCH_TIMEOUT_S)
        if result.returncode != 0:
            raise PhoneError(
                "devicectl could not launch the runner app on the phone",
                {
                    "udid": udid,
                    "bundle": bundle,
                    "returncode": result.returncode,
                    "stdout": result.stdout[:_OUTPUT_CHARS],
                    "stderr": result.stderr[:_OUTPUT_CHARS],
                },
            )
