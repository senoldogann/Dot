"""Which versions of the ``claude`` program Dot knows to work with (item 1673).

The program updates itself, and what Dot relies on (the stream-json protocol, ``--resume`` after a cut, the init line,
how it ends on a signal) was measured on given versions. :data:`CLAUDE_VERSIONS` says, for ranges of versions,
whether Dot was measured on them (``supported``), runs on them without having been measured there (``graceful``: a
warning, once), or is known to fail on them (``broken``: a run is refused, saying why and what to install). A version
in no range is ``unknown``: a warning, once. :func:`claude_version` asks the program (``claude --version``); the rest
here is pure.
"""

import asyncio
import contextlib
import os
import re
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

type Support = Literal["supported", "graceful", "broken", "unknown"]
type Version = tuple[int, int, int]

_VERSION: re.Pattern[str] = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_VERSION_TIMEOUT_S: float = 15.0


class VersionRange(FrozenModel):
    """The versions from ``low`` (included) to ``high`` (left out; ``None``: no end), how Dot fares on them, and why,
    in the words a refusal or a warning shows."""

    low: Version
    high: Version | None
    support: Literal["supported", "graceful", "broken"]
    why: str


# Measured: 2.1.330 and 2.1.331 (items 1580 to 1582: the cut, the exit codes, ``--resume`` of a cut session). Older 2.x
# runs as far as anything shows, unmeasured. No version is known broken yet: one found so is added here with what broke,
# and a run on it is refused.
CLAUDE_VERSIONS: tuple[VersionRange, ...] = (
    VersionRange(
        low=(2, 1, 330),
        high=(2, 2, 0),
        support="supported",
        why="measured with Dot (items 1580 to 1582)",
    ),
    VersionRange(
        low=(2, 0, 0),
        high=(2, 1, 330),
        support="graceful",
        why="older than the versions Dot was measured on (2.1.330 and later): a cut program may not be continued as "
        "Dot expects; update Claude Code with 'claude update'",
    ),
)


class VersionVerdict(FrozenModel):
    """How Dot fares on the program's version: ``version`` as it said it (``None`` when it said none that reads as
    one), the support, and why."""

    version: Version | None
    support: Support
    why: str

    @property
    def shown(self) -> str:
        return "an unknown version" if self.version is None else ".".join(str(part) for part in self.version)


def parse_version(said: str) -> Version | None:
    """The version in what ``claude --version`` wrote (``2.1.331 (Claude Code)``), ``None`` when none reads as one."""
    found: re.Match[str] | None = _VERSION.search(said)
    if found is None:
        return None
    return (int(found.group(1)), int(found.group(2)), int(found.group(3)))


def verdict_of(version: Version | None, table: Sequence[VersionRange]) -> VersionVerdict:
    """How Dot fares on ``version`` by ``table`` (:data:`CLAUDE_VERSIONS`)."""
    if version is not None:
        for entry in table:
            if entry.low <= version and (entry.high is None or version < entry.high):
                return VersionVerdict(version=version, support=entry.support, why=entry.why)
    return VersionVerdict(
        version=version,
        support="unknown",
        why="a version Dot has not been measured on; if runs on it fail, tell Dot's maintainers which version it is",
    )


def refusal(verdict: VersionVerdict) -> str:
    """The words of a run refused on a ``broken`` version."""
    return (
        f"Claude Code {verdict.shown} is known not to work with Dot: {verdict.why}. Install another version "
        "(claude update, or claude install <version>) and try again."
    )


async def claude_version(program: Path, env: Mapping[str, str]) -> str:
    """What ``program --version`` writes, or the empty string when it fails or does not end within its time (a
    program that says no version is an unknown one, which only warns)."""
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            str(program),
            "--version",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(env),
            start_new_session=True,
        )
    except OSError as error:
        log.warning("claude_version_not_read", program=str(program), error=str(error))
        return ""
    try:
        async with asyncio.timeout(_VERSION_TIMEOUT_S):
            out, _ = await process.communicate()
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        log.warning("claude_version_not_read", program=str(program), error="timed out")
        return ""
    return out.decode("utf-8", errors="replace").strip()
