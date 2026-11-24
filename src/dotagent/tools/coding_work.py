"""Record a verified linked worktree in the coding run that used it."""

import asyncio
from pathlib import Path

from dotagent.coding_work import CODING_WORK_EVENT, CodingWork, CommitId, PullAddress, inspect_worktree
from dotagent.core.errors import ToolInputError
from dotagent.core.text import redact_json, redact_known
from dotagent.core.types import JsonObject
from dotagent.policy.paths import classify_path, expand_path
from dotagent.policy.types import ActionInfo
from dotagent.secrets import known_secrets
from dotagent.tools.base import RegisteredTool, ToolArgs, ToolResult, make_path_tool, ok
from dotagent.tools.context import ToolContext


class TrackCodingWorkArgs(ToolArgs):
    worktree: str
    base: CommitId
    pr_url: PullAddress | None = None


def _classify(args: TrackCodingWorkArgs, ctx: ToolContext) -> ActionInfo:
    path: Path = expand_path(args.worktree, ctx.workspace)
    if classify_path(path, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths) in (
        "protected",
        "dot",
        "system",
    ):
        raise ToolInputError("Name a project worktree outside Dot's protected files.", {"worktree": args.worktree})
    return ActionInfo(
        tool="track_coding_work",
        kind="read",
        risk="low",
        summary=f"Record the coding worktree {path}",
        target=str(path),
        flags=(),
    )


async def _track(args: TrackCodingWorkArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets, filled=True)
    try:
        tracked: CodingWork = await asyncio.to_thread(
            inspect_worktree, Path(authorized.target), args.base, args.pr_url, ctx.runtime.shell_env
        )
    except ToolInputError as exc:
        details: JsonObject = {key: redact_json(value, secrets) for key, value in exc.context.items()}
        raise ToolInputError(redact_known(exc.message, secrets), details) from None
    shown: CodingWork = tracked.model_copy(
        update={
            "worktree": redact_known(tracked.worktree, secrets),
            "branch": redact_known(tracked.branch, secrets),
            "diff_stat": redact_known(tracked.diff_stat, secrets),
        }
    )
    data: JsonObject = shown.model_dump(mode="json")
    await ctx.runtime.bus.emit(ctx.run_id, CODING_WORK_EVENT, data, True)
    return ok(shown.model_dump_json())


def build_coding_work_tool() -> RegisteredTool:
    return make_path_tool(
        "track_coding_work",
        "Attach your linked Git worktree to this coding run. Give its root, the full base commit ID, and an optional "
        "GitHub pull request URL. Dot records the branch, committed change summary, and PR state when verified. "
        "Call it again after pushing or when asked for an updated snapshot.",
        TrackCodingWorkArgs,
        frozenset({"task"}),
        False,
        35,
        _classify,
        _track,
    )
