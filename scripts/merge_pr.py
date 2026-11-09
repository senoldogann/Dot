"""Merge a pull request whose checks passed, unless `main` moved under the same files since they ran (item 945).

`main` does not require a pull request to be up to date before its merge (item 944): a merge no longer sends every
other open one back to run its checks again. What that costs is two pull requests that each pass alone and break
`main` together. Most such breaks are in a file both changed; this script merges at once when `main` has moved since
the pull request's checks began under none of its files, and otherwise brings the branch up to `main`
(`gh pr update-branch`) and stops, so that the checks run again on what the merge would make. A break across files
that neither shares is what `main`'s own run of the gate, on every push, shows after the merge.

The checks' run is the latest of `gate.yml` for the pull request's head; the commits `main` took since are those its
log, first parent, dates from :data:`MARGIN` before that run was made (a merge's commit is dated when it is merged).
When it was made, not when it began: a run started again (`gh run rerun`) begins anew but checks the same merge of the
pull request into `main` that its first attempt made. And a margin before it: GitHub makes that merge when the push's
event fires and the run some time after, seconds or, with the runners all busy, minutes; a merge into `main` in
between was not checked. The margin errs one way only, toward bringing a branch up to `main` that did not need it.
A head that holds `main` as it is now was checked on all of it, the margin aside: it is merged (item 1078).

A pull request that changes only what the gate's `paths-ignore` names (prose) has no run of the gate to wait for, and
nothing of it can break `main`: it is merged as it stands.

Usage: uv run python scripts/merge_pr.py <number> <rebase|squash>
Exit 0: merged. Exit 2: brought up to main, run it again once its checks pass. Exit 1: it cannot be merged as it stands.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

_METHODS: tuple[str, ...] = ("rebase", "squash")
# How long before its run was made the merge a pull request's checks run on may have been made.
MARGIN: timedelta = timedelta(minutes=15)


@dataclass(frozen=True)
class GateRun:
    """The latest run of the gate for a pull request's head: its id, when it was made (its first attempt), its status
    and conclusion."""

    run_id: int
    created_at: str
    status: str
    conclusion: str


class MergeRefused(Exception):
    """The pull request cannot be merged as it stands; the message says why and what to do."""


def shared_files(changed: list[str], moved: list[str]) -> list[str]:
    """The files the pull request changes that `main` changed too since its checks began, sorted."""
    return sorted(set(changed) & set(moved))


def path_pattern(pattern: str) -> re.Pattern[str]:
    """A GitHub workflow path filter as a regular expression: ``**`` any characters, ``*`` any but ``/``."""
    parts: list[str] = [
        ".*" if piece == "**" else "[^/]*" if piece == "*" else re.escape(piece)
        for piece in re.split(r"(\*\*|\*)", pattern)
    ]
    return re.compile("".join(parts) + r"\Z")


def gate_runs_for(files: list[str], ignored: list[str]) -> bool:
    """Whether the gate runs for a pull request changing ``files``, its ``paths-ignore`` being ``ignored``: when one
    of them is not ignored."""
    patterns: list[re.Pattern[str]] = [path_pattern(pattern) for pattern in ignored]
    return any(not any(pattern.match(path) for pattern in patterns) for path in files)


def _gate_ignored(workflow: Path) -> list[str]:
    """The ``paths-ignore`` of the gate's ``pull_request`` trigger, read from ``workflow``."""
    loaded: object = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    # PyYAML reads the key ``on`` as True.
    triggers: object = loaded.get(True) if isinstance(loaded, dict) else None
    pull: object = triggers.get("pull_request") if isinstance(triggers, dict) else None
    ignored: object = pull.get("paths-ignore") if isinstance(pull, dict) else None
    if not isinstance(ignored, list) or not all(isinstance(item, str) for item in ignored):
        raise MergeRefused(f"{workflow} names no paths-ignore of its pull_request trigger")
    return [str(item) for item in ignored]


def counted_since(created_at: str, margin: timedelta) -> str:
    """The moment from which `main`'s commits count as unchecked: ``margin`` before the run was made (ISO 8601)."""
    made: datetime = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return (made - margin).isoformat()


def latest_gate_run(runs: list[GateRun], head: str) -> GateRun:
    """The newest of ``runs`` (newest first, as `gh run list` gives them); raises when there is none for ``head``."""
    if not runs:
        raise MergeRefused(f"no run of the gate for the head {head}: push it, or wait for its run to start")
    return runs[0]


def _run(argv: list[str]) -> str:
    done: subprocess.CompletedProcess[str] = subprocess.run(argv, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise MergeRefused(f"{' '.join(argv)} failed ({done.returncode}): {done.stderr.strip()[:600]}")
    return done.stdout


def _holds(head: str, commit: str) -> bool:
    """Whether ``commit`` is ``head`` or one of its ancestors."""
    argv: list[str] = ["git", "merge-base", "--is-ancestor", commit, head]
    done: subprocess.CompletedProcess[str] = subprocess.run(argv, capture_output=True, text=True, check=False)
    if done.returncode not in (0, 1):
        raise MergeRefused(f"{' '.join(argv)} failed ({done.returncode}): {done.stderr.strip()[:600]}")
    return done.returncode == 0


def _moved_since(run: GateRun) -> list[str]:
    """The files `main`'s commits changed from :data:`MARGIN` before ``run`` was made."""
    argv: list[str] = [
        "git", "log", "--first-parent", f"--since={counted_since(run.created_at, MARGIN)}", "--name-only",
        "--no-renames", "--format=", "origin/main",
    ]  # fmt: skip
    return [line for line in _run(argv).splitlines() if line]


def _gate_runs(head: str) -> list[GateRun]:
    raw: str = _run(
        [
            "gh", "run", "list", "--workflow", "gate.yml", "--commit", head, "--event", "pull_request",
            "--json", "databaseId,createdAt,status,conclusion", "--limit", "5",
        ]
    )  # fmt: skip
    loaded: object = json.loads(raw)
    if not isinstance(loaded, list):
        raise MergeRefused(f"gh run list gave no list: {raw[:400]}")
    runs: list[GateRun] = []
    for item in loaded:
        if not isinstance(item, dict) or not isinstance(item.get("databaseId"), int):
            raise MergeRefused(f"gh run list gave a run with no id: {raw[:400]}")
        created: object = item.get("createdAt")
        if not isinstance(created, str) or not created:
            raise MergeRefused(f"gh run list gave a run with no time it was made: {raw[:400]}")
        runs.append(
            GateRun(
                run_id=item["databaseId"],
                created_at=created,
                status=str(item.get("status", "")),
                conclusion=str(item.get("conclusion", "")),
            )
        )
    return runs


def _state_and_head(raw: str) -> tuple[str, str]:
    """The state and the head of a pull request as `gh pr view --json headRefOid,state` says them."""
    view: object = json.loads(raw)
    if not isinstance(view, dict):
        raise MergeRefused(f"gh pr view gave no object: {raw[:400]}")
    state: object = view.get("state")
    head: object = view.get("headRefOid")
    if not isinstance(state, str) or not isinstance(head, str) or not head:
        raise MergeRefused(f"gh pr view gave no state or head: {raw[:400]}")
    return state, head


def _changed_files(number: int) -> list[str]:
    """Every file the pull request ``number`` changes, read page by page from GitHub's REST API (`gh pr view` gives
    100 at most)."""
    return [
        line
        for line in _run(
            ["gh", "api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{number}/files", "--jq", ".[].filename"]
        ).splitlines()
        if line
    ]


def merge(number: int, method: str) -> int:
    """Merge the pull request ``number`` by ``method``, or bring it up to main; the script's exit code."""
    state, head = _state_and_head(_run(["gh", "pr", "view", str(number), "--json", "headRefOid,state"]))
    changed: list[str] = _changed_files(number)
    if state != "OPEN":
        raise MergeRefused(f"pull request {number} is {state}, not open")
    if not changed:
        raise MergeRefused(f"pull request {number} changes no file")
    workflow: Path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "gate.yml"
    if not gate_runs_for(changed, _gate_ignored(workflow)):
        _run(["gh", "pr", "merge", str(number), f"--{method}", "--delete-branch", "--match-head-commit", head])
        print(
            f"merged pull request {number} at {head} ({method}): it changes only prose, which the gate does not run for"
        )
        return 0
    run: GateRun = latest_gate_run(_gate_runs(head), head)
    if run.status != "completed" or run.conclusion != "success":
        raise MergeRefused(
            f"the gate's run {run.run_id} for {head} is {run.status}/{run.conclusion or 'none'}: merge once it passed"
        )
    _run(["git", "fetch", "--quiet", "origin", "main", head])
    # A head brought up to main since (item 1078): its checks ran on main as it is, every file of it.
    moved: list[str] = [] if _holds(head, "origin/main") else _moved_since(run)
    shared: list[str] = shared_files(changed, moved)
    if shared:
        _run(["gh", "pr", "update-branch", str(number)])
        print(f"main changed {', '.join(shared)} since the checks of {head} began: brought up to main; merge again "
              "once its checks pass")  # fmt: skip
        return 2
    _run(["gh", "pr", "merge", str(number), f"--{method}", "--delete-branch", "--match-head-commit", head])
    print(f"merged pull request {number} at {head} ({method})")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[0].isdigit() or argv[1] not in _METHODS:
        print(__doc__, file=sys.stderr)
        return 1
    try:
        return merge(int(argv[0]), argv[1])
    except MergeRefused as refused:
        print(f"not merged: {refused}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
