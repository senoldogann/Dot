"""Claude Code as a worker of Dot's (item 919): the ``claude`` program the owner installed and signed in to with their
own plan, run without a terminal (``claude -p``) in a folder Dot gives it, its answer read as it streams.

What Anthropic allows is the owner's own sign-in used through the unmodified program, and that is all this module does:
it never reads the sign-in's token, and it takes every API key out of the program's environment, since with
``ANTHROPIC_API_KEY`` set ``claude -p`` bills that key instead of the plan. Which account is signed in is read from
``~/.claude.json`` (its ``oauthAccount``: the address and the organisation, no secret), so that each session says whose
plan it used; a switcher that signs another account in is followed at the next session.

The program streams JSON lines (``--output-format stream-json``): ``system``/``init`` names the session, which a later
call resumes (``--resume``); ``rate_limit_event`` says how much of the plan's window is used and when it resets; and
``result`` ends it with the answer, the turns, the tokens and what it would have cost on the API. A session works with
``--permission-mode auto``, nobody to answer a prompt (``--permission-prompts none``: what would ask is refused). What
is meant to commit on its own branch and leave the rest to Dot, and its environment has no way to GitHub by default
(:func:`worker_env`): a default its own shell could set back, so what holds is what the night checks after each
session (:func:`~dotagent.repair.github.merged_behind_dot`). The tools of :data:`DENIED_TOOLS` are refused besides, a
line that reads commands by their first words, not every spelling of them."""

import asyncio
import contextlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, ValidationError

from dotagent.core.errors import RepairError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.tools.shell import kill_group

log = get_logger(__name__)

# The tools a session is refused, whatever its permission mode decides.
DENIED_TOOLS: tuple[str, ...] = (
    "Bash(gh pr merge:*)",
    "Bash(gh pr close:*)",
    "Bash(gh repo:*)",
    "Bash(gh release:*)",
    "Bash(gh secret:*)",
    "Bash(git push --force:*)",
    "Bash(git push -f:*)",
    "Bash(git push --force-with-lease:*)",
    "Bash(git push origin main:*)",
    "Bash(git push origin HEAD:main:*)",
    "Bash(dotx service:*)",
    "Bash(launchctl:*)",
    "Bash(security:*)",
    "Bash(pkill:*)",
    "Bash(killall:*)",
    "Bash(sudo:*)",
    "Bash(gh auth:*)",
    "Bash(git credential:*)",
    "Bash(git credential-osxkeychain:*)",
)
# The names of the environment a session never gets: each makes ``claude`` bill an API or a cloud instead of the plan.
_BILLING_NAMES: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    }
)
# The ends of names that hold a secret of Dot's or of another program's, which a session has no need of (GitHub's own
# tokens among them).
_SECRET_ENDS: tuple[str, ...] = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASSWORD_FILE", "_KEY")
# What keeps a session from GitHub by default (the review of item 921): ``gh`` reads its sign-in from an empty folder,
# git's credential helpers (the Keychain's among them) are reset to none and it may not ask for a password, and ssh is
# no way out, so that Dot pushes, opens the pull request and merges it. Checked on the owner's Mac (2026-11-02): ``git
# push`` stops at "could not read Username", ``gh`` says it is not signed in. A command of the session's own can set
# these back: the night checks after each session what reached main.
_NO_GITHUB: dict[str, str] = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "credential.helper",
    "GIT_CONFIG_VALUE_0": "",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/usr/bin/false",
    "SSH_ASKPASS": "/usr/bin/false",
    "GIT_SSH_COMMAND": "/usr/bin/false",
}
_DROPPED_NAMES: frozenset[str] = frozenset({"SSH_AUTH_SOCK", "GH_HOST", "GH_ENTERPRISE_TOKEN"})
# The folders of Dot's own state, each pointed somewhere new for a session, so that no test it runs touches the
# owner's Dot (CLAUDE.md: "Point DOT_HOME, DOT_LOG_DIR and DOT_CHROME_DIR at throwaway folders first").
SCRATCH_NAMES: tuple[str, ...] = ("DOT_HOME", "DOT_LOG_DIR", "DOT_CHROME_DIR")
# A line of the stream may be long (a turn that read a big file): the reader takes lines up to this size.
_LINE_LIMIT: int = 32 * 1024 * 1024
# How long the stream is read after the program exited and its group was ended.
_STREAM_END_S: float = 30.0
# What is kept of what the program wrote to its error stream, for a failure's context.
_STDERR_KEPT: int = 4000
# The kinds of line an outcome is read from; the turns in between are the session's own.
_KEPT_TYPES: frozenset[str] = frozenset({"system", "rate_limit_event", "result"})


class ClaudeAccount(FrozenModel):
    """The account ``claude`` is signed in with: its address and its organisation (``oauthAccount`` of its state)."""

    email: str
    organization: str


class LimitWindow(FrozenModel):
    """The plan's window as the last ``rate_limit_event`` of a session told it: its kind (``five_hour``), whether a
    call is allowed, how much of it is used (0 to 1, when said) and when it resets (epoch seconds, when said)."""

    kind: str
    status: str
    utilization: float | None
    resets_at: float | None


class ClaudeOutcome(FrozenModel):
    """What a session came to. ``ok`` is a result that is no error from a program that exited with 0; ``text`` is the
    result's words, or why there are none."""

    session_id: str | None
    ok: bool
    text: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    turns: int
    denials: int
    limit: LimitWindow | None
    exit_code: int | None
    timed_out: bool


def account_of(state_text: str) -> ClaudeAccount | None:
    """The account the state file of ``claude`` (``~/.claude.json``, as text) says is signed in, or ``None`` when none
    is (or the file is no JSON object)."""
    try:
        state: object = json.loads(state_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(state, dict):
        return None
    account: object = state.get("oauthAccount")
    if not isinstance(account, dict):
        return None
    email: object = account.get("emailAddress")
    organization: object = account.get("organizationName")
    if not isinstance(email, str) or not email:
        return None
    return ClaudeAccount(email=email, organization=organization if isinstance(organization, str) else "")


def read_account(state_file: Path) -> ClaudeAccount | None:
    """The account signed in, from ``state_file``; ``None`` when there is no such file."""
    try:
        return account_of(state_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def worker_env(env: Mapping[str, str], scratch: Path) -> dict[str, str]:
    """The environment of a session: ``env`` without what would bill another than the plan or holds a secret it has no
    need of, kept from GitHub (:data:`_NO_GITHUB`), and with the folders of Dot's own state in ``scratch``."""
    kept: dict[str, str] = {
        name: value
        for name, value in env.items()
        if name not in _BILLING_NAMES
        and not name.upper().endswith(_SECRET_ENDS)
        and name not in SCRATCH_NAMES
        and name not in _DROPPED_NAMES
        and not name.startswith("GIT_CONFIG_")
    }
    for name in SCRATCH_NAMES:
        kept[name] = str(scratch / name.removeprefix("DOT_").lower())
    kept["GH_CONFIG_DIR"] = str(scratch / "gh")
    kept.update(_NO_GITHUB)
    return kept


def claude_argv(binary: str, prompt: str, resume: str | None) -> tuple[str, ...]:
    """The command of one session: ``prompt`` to a new session, or to the one ``resume`` names."""
    again: tuple[str, ...] = () if resume is None else ("--resume", resume)
    return (
        binary,
        "-p",
        prompt,
        *again,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "auto",
        "--permission-prompts",
        "none",
        "--disallowedTools",
        *DENIED_TOOLS,
    )


def _limit_of(info: object) -> LimitWindow | None:
    """The window a ``rate_limit_event``'s ``rate_limit_info`` tells, or ``None`` when it tells none."""
    if not isinstance(info, dict):
        return None
    kind: object = info.get("rateLimitType")
    status: object = info.get("status")
    resets: object = info.get("resetsAt")
    used: object = None
    windows: object = info.get("unifiedWindows")
    if isinstance(windows, dict) and isinstance(kind, str):
        window: object = windows.get(kind)
        if isinstance(window, dict):
            used = window.get("utilization")
    return LimitWindow(
        kind=kind if isinstance(kind, str) else "",
        status=status if isinstance(status, str) else "",
        utilization=float(used) if isinstance(used, int | float) and not isinstance(used, bool) else None,
        resets_at=float(resets) if isinstance(resets, int | float) and not isinstance(resets, bool) else None,
    )


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def outcome_of(lines: Iterable[str], exit_code: int | None, timed_out: bool, stderr: str) -> ClaudeOutcome:
    """What the stream's ``lines`` say a session came to, with how the program ended. A line that is no JSON object is
    passed over: the program may write a warning among them."""
    session_id: str | None = None
    limit: LimitWindow | None = None
    result: dict[str, object] | None = None
    for line in lines:
        try:
            item: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        kind: object = item.get("type")
        named: object = item.get("session_id")
        if isinstance(named, str) and named:
            session_id = named
        if kind == "rate_limit_event":
            limit = _limit_of(item.get("rate_limit_info")) or limit
        elif kind == "result":
            result = item
    if result is None:
        why: str = "the session ran out of time" if timed_out else (stderr.strip() or "the session gave no result")
        return ClaudeOutcome(
            session_id=session_id,
            ok=False,
            text=why[-_STDERR_KEPT:],
            cost_usd=0.0,
            input_tokens=0,
            output_tokens=0,
            turns=0,
            denials=0,
            limit=limit,
            exit_code=exit_code,
            timed_out=timed_out,
        )
    usage: object = result.get("usage")
    tokens: dict[str, object] = usage if isinstance(usage, dict) else {}
    said: object = result.get("result")
    denied: object = result.get("permission_denials")
    return ClaudeOutcome(
        session_id=session_id,
        ok=result.get("is_error") is False and exit_code == 0 and not timed_out,
        text=said if isinstance(said, str) else "",
        cost_usd=_number(result.get("total_cost_usd")),
        input_tokens=int(
            _number(tokens.get("input_tokens"))
            + _number(tokens.get("cache_creation_input_tokens"))
            + _number(tokens.get("cache_read_input_tokens"))
        ),
        output_tokens=int(_number(tokens.get("output_tokens"))),
        turns=int(_number(result.get("num_turns"))),
        denials=len(denied) if isinstance(denied, list) else 0,
        limit=limit,
        exit_code=exit_code,
        timed_out=timed_out,
    )


def limited(outcome: ClaudeOutcome) -> bool:
    """Whether the plan's window refused the session (its last ``rate_limit_event`` was not *allowed*)."""
    return outcome.limit is not None and outcome.limit.status not in ("", "allowed", "allowed_warning")


async def _read_stream(stream: asyncio.StreamReader, kept: list[str]) -> None:
    """Keep the lines of ``stream`` an outcome is read from (:data:`_KEPT_TYPES`) until it ends."""
    while True:
        raw: bytes = await stream.readline()
        if not raw:
            return
        line: str = raw.decode("utf-8", errors="replace")
        try:
            item: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("type") in _KEPT_TYPES:
            kept.append(line)


async def _read_tail(stream: asyncio.StreamReader) -> str:
    data: bytes = await stream.read()
    return data.decode("utf-8", errors="replace")[-_STDERR_KEPT:]


async def run_claude(
    binary: str, cwd: Path, prompt: str, resume: str | None, env: Mapping[str, str], timeout_s: float
) -> ClaudeOutcome:
    """Run one session of ``binary`` in ``cwd`` with ``env``, at most ``timeout_s`` (then it is ended, with everything
    it started). Raises :class:`RepairError` when the program cannot be started."""
    argv: tuple[str, ...] = claude_argv(binary, prompt, resume)
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=dict(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=_LINE_LIMIT,
        )
    except OSError as error:
        raise RepairError(
            "Claude Code could not be started: is it installed, and is [repair] claude its command?",
            {"binary": binary, "cwd": str(cwd), "error": str(error)},
        ) from error
    assert process.stdout is not None and process.stderr is not None
    kept: list[str] = []
    timed_out: bool = False
    reading: asyncio.Task[None] = asyncio.create_task(_read_stream(process.stdout, kept))
    tail: asyncio.Task[str] = asyncio.create_task(_read_tail(process.stderr))
    try:
        async with asyncio.timeout(timeout_s):
            await process.wait()
        # What the session left behind in its group (a server, a test worker) may hold the stream open: it ends here,
        # so that the answer already given is read rather than waited past (the ninth review).
        kill_group(process.pid)
        async with asyncio.timeout(_STREAM_END_S):
            await reading
    except TimeoutError:
        timed_out = process.returncode is None
        kill_group(process.pid)
        await process.wait()
    finally:
        # What the session started and left behind (a server, a watcher) ends with it, whatever ended the session.
        kill_group(process.pid)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
        reading.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reading
    stderr: str = await tail
    outcome: ClaudeOutcome = outcome_of(kept, process.returncode, timed_out, stderr)
    log.info(
        "repair_claude_session",
        session_id=outcome.session_id,
        ok=outcome.ok,
        turns=outcome.turns,
        cost_usd=round(outcome.cost_usd, 4),
        timed_out=timed_out,
        exit_code=process.returncode,
    )
    return outcome


def validated_window(raw: str) -> LimitWindow | None:
    """A window kept as JSON, or ``None`` when what is kept is no window."""
    try:
        return LimitWindow.model_validate_json(raw)
    except ValidationError:
        return None


# ``claude -p /usage``: Claude Code's own report of the plan's windows, read locally with no model call (measured on the
# owner's Mac, 2026-11-02: no turn, no cost; Codync reads it the same way). The empty strict MCP config keeps it from
# starting every MCP server the owner set up.
USAGE_ARGS: tuple[str, ...] = (
    "-p",
    "/usage",
    "--output-format",
    "stream-json",
    "--verbose",
    "--no-session-persistence",
    "--strict-mcp-config",
    "--mcp-config",
    '{"mcpServers":{}}',
)
USAGE_TIMEOUT_S: float = 60.0


class PlanWindow(FrozenModel):
    """A window of the plan as ``/usage`` reports it: its kind (``session``, the five hours; ``weekly_all``;
    ``weekly_scoped``, one model's week), how much of it is used (0 to 100), whether it applies now and when it
    resets (epoch seconds, when said)."""

    kind: str
    percent: float
    active: bool
    resets_at: float | None


class _Limit(BaseModel):
    """One of ``usage_report.rate_limits.limits``, as Claude Code reports it (the fields Dot reads)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    kind: str
    percent: StrictInt | StrictFloat
    is_active: bool | None = None
    resets_at: str | None = None


class _Rates(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    limits: list[_Limit]


class _Report(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    rate_limits: _Rates


class _UsageLine(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    usage_report: _Report


def _resets(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def usage_of(lines: Iterable[str]) -> tuple[PlanWindow, ...]:
    """The windows the ``usage_report`` of a ``/usage`` stream lists, read through its model (the eighth review): a
    line that is no such report, or one shaped otherwise (``rate_limits`` missing or null), lists none."""
    for line in lines:
        try:
            report: _UsageLine = _UsageLine.model_validate_json(line)
        except ValidationError:
            continue
        return tuple(
            PlanWindow(
                kind=limit.kind,
                percent=float(limit.percent),
                active=limit.is_active is not False,
                resets_at=_resets(limit.resets_at),
            )
            for limit in report.usage_report.rate_limits.limits
        )
    return ()


def usage_room(windows: Iterable[PlanWindow], leave_room: float) -> bool:
    """Whether every window that applies now is used less than ``leave_room`` (0 to 1) of it; a week of one model the
    sessions do not use is not one."""
    return all(window.percent < leave_room * 100.0 for window in windows if window.active)


async def read_usage(binary: str, env: Mapping[str, str], cwd: Path) -> tuple[PlanWindow, ...]:
    """The plan's windows by ``/usage``. Raises :class:`RepairError` when the program fails, takes longer than
    :data:`USAGE_TIMEOUT_S` or reports none."""
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            binary,
            *USAGE_ARGS,
            cwd=str(cwd),
            env=dict(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise RepairError("Claude Code's usage could not be read", {"binary": binary, "error": str(error)}) from error
    try:
        async with asyncio.timeout(USAGE_TIMEOUT_S):
            out, err = await process.communicate()
    except TimeoutError as error:
        raise RepairError("Claude Code's usage took too long to read", {"binary": binary}) from error
    finally:
        kill_group(process.pid)
        if process.returncode is None:
            await asyncio.shield(process.wait())
    windows: tuple[PlanWindow, ...] = usage_of(out.decode("utf-8", errors="replace").splitlines())
    if not windows:
        raise RepairError(
            "Claude Code reported no usage",
            {"code": process.returncode, "stderr": err.decode("utf-8", errors="replace")[-_STDERR_KEPT:]},
        )
    return windows
