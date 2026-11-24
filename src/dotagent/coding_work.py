"""Capture the Git state a coding run wants to show, without changing any checkout."""

import os
import re
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from dotagent.core.errors import ToolInputError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

CODING_WORK_EVENT: str = "coding_work_captured"
_OID = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
CommitId = Annotated[str, StringConstraints(pattern=f"^{_OID}$")]
_PR = r"https://github\.com/([a-zA-Z0-9-]+)/([a-zA-Z0-9_.-]+)/pull/([1-9][0-9]{0,9})"
PullAddress = Annotated[str, StringConstraints(pattern=f"^{_PR}$")]
PullState = Literal["open", "closed", "merged", "unverified"]
log = get_logger(__name__)


class CodingWork(FrozenModel):
    worktree: str
    branch: str
    head: CommitId
    base: CommitId
    diff_stat: str
    pr_url: PullAddress | None
    pr_state: PullState | None
    captured_at: float


class _PullRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    state: Literal["open", "closed"]
    merged: bool
    head: "_PullHead"


class _PullHead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: CommitId
    ref: str


def _command(argv: tuple[str, ...], cwd: Path, env: Mapping[str, str]) -> str:
    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolInputError("Could not read the coding work.", {"command": argv[0], "error": str(exc)}) from exc
    if result.returncode:
        raise ToolInputError(
            "Could not read the coding work.",
            {"command": argv[0], "exit": result.returncode, "stderr": result.stderr[-800:]},
        )
    return result.stdout


def _git(worktree: Path, args: tuple[str, ...]) -> str:
    return _command(
        (
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ),
        worktree,
        {
            "PATH": "/usr/bin:/bin",
            "HOME": "/var/empty",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    ).strip()


def pull_state(url: str, head: str, branch: str, cli_env: Mapping[str, str]) -> PullState:
    """Confirm a GitHub PR still points to this exact branch and commit before displaying its state."""
    match: re.Match[str] | None = re.fullmatch(_PR, url)
    if match is None:
        raise ToolInputError("Use a GitHub pull request address.", {"url": url})
    owner, repo, number = match.groups()
    env: dict[str, str] = {**cli_env, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"}
    request: tuple[str, ...] = ("gh", "api", "--hostname", "github.com", f"repos/{owner}/{repo}/pulls/{number}")
    for attempt in (1, 2):
        try:
            record = _PullRecord.model_validate_json(_command(request, Path("/"), env))
            break
        except (ToolInputError, ValidationError) as exc:
            log.warning(
                "coding_work_pr_check_failed",
                repository=f"{owner}/{repo}",
                number=number,
                attempt=attempt,
                reason=type(exc).__name__,
            )
            if attempt == 2:
                details = exc.context if isinstance(exc, ToolInputError) else {"response_error": str(exc)}
                raise ToolInputError("Could not verify the GitHub pull request.", {"url": url, **details}) from exc
            time.sleep(0.4)
    if (record.head.sha, record.head.ref) != (head, branch):
        return "unverified"
    return "merged" if record.merged else record.state


def inspect_worktree(worktree: Path, base: str, url: str | None, cli_env: Mapping[str, str]) -> CodingWork:
    """Summarize committed changes on a verified linked worktree. Blocking."""
    marker: Path = worktree / ".git"
    root: str = _git(worktree, ("rev-parse", "--show-toplevel"))
    git_dir: str = _git(worktree, ("rev-parse", "--absolute-git-dir"))
    common: str = _git(worktree, ("rev-parse", "--path-format=absolute", "--git-common-dir"))
    if (
        not marker.is_file()
        or os.path.realpath(root) != str(worktree)
        or os.path.realpath(git_dir) == os.path.realpath(common)
    ):
        raise ToolInputError("Name the root of a linked Git worktree.", {"worktree": str(worktree)})
    listing: str = _git(worktree, ("worktree", "list", "--porcelain", "-z"))
    roots: set[str] = {
        os.path.realpath(line.removeprefix("worktree ")) for line in listing.split("\0") if line.startswith("worktree ")
    }
    if str(worktree) not in roots:
        raise ToolInputError("Git does not list this worktree.", {"worktree": str(worktree)})
    if re.fullmatch(_OID, base) is None:
        raise ToolInputError("Use a full base commit ID.", {"base": base})
    head: str = _git(worktree, ("rev-parse", "--verify", "HEAD^{commit}"))
    branch: str = _git(worktree, ("symbolic-ref", "--short", "HEAD"))
    _git(worktree, ("merge-base", "--is-ancestor", base, head))
    changes: str = _git(
        worktree,
        ("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--stat=100,80,40", base, head, "--"),
    )
    if head != _git(worktree, ("rev-parse", "--verify", "HEAD^{commit}")) or branch != _git(
        worktree, ("symbolic-ref", "--short", "HEAD")
    ):
        raise ToolInputError(
            "The worktree changed during capture. Capture its current state again.", {"worktree": str(worktree)}
        )
    return CodingWork(
        worktree=str(worktree),
        branch=branch,
        head=head,
        base=base,
        diff_stat=changes,
        pr_url=url,
        pr_state=None if url is None else pull_state(url, head, branch, cli_env),
        captured_at=time.time(),
    )
