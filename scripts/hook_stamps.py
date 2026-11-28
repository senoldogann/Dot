"""Which commits of a branch were made without the repository's hooks (item 2100).

The commit hook (`.claude/hooks/commit-msg.sh`) stamps every commit with the trailer ``Dot-Hooks: commit-msg``. A commit
made with the hooks switched off (``git -c core.hooksPath=/dev/null commit``, ``git commit --no-verify``) has no stamp,
and is the commit that skipped what the hooks check; one helper did it on 2026-11-28. This lists them; the pre-push hook
refuses to push one, and the gate fails a pull request that holds one, so that a push made with ``--no-verify`` too is
seen.

Not held to the stamp:

- a commit whose first parent does not hold the commit hook's file: it was made on a tree from before the stamp
  existed (a branch older than item 2100);
- a commit written before :data:`STAMPED_SINCE`, by its author date, which a rebase keeps: the hook was installed in
  the owner's repository then, and a branch begun before, rebased onto a main that holds the hook, is not refused for
  commits no hook could stamp;
- a commit GitHub made (its committer ``noreply@github.com``): the merge of main ``gh pr update-branch`` makes for
  scripts/merge_pr.py, and the rebase and squash merges it makes on main, which keep the stamps of the commits they
  carry.

What keeps a stamp without running the hook: ``git rebase`` (the merge train's and a repair session's bringing a branch
up to main), ``git cherry-pick`` and ``git commit --amend`` keep the message, and ``git merge`` runs the hook. ``git
revert --no-edit`` does not run it: amend the revert (``git commit --amend --no-edit``), which does.

A stamp is a trailer anyone can write: it shows the hooks were skipped by mistake or by habit, not that they were run.

Stdlib only. Usage: ``hook_stamps.py --base <rev> --head <rev>``: the commits of ``base..head`` (from their merge
base). Exit 0: every commit held to it has the stamp; 1: those that lack it, listed; 2: git failed.
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

STAMP: str = "Dot-Hooks: commit-msg"
HOOK_FILE: str = ".claude/hooks/commit-msg.sh"
GITHUB_COMMITTER: str = "noreply@github.com"
# When the commit hook was installed in the owner's repository (2026-11-28T12:00:00Z), as epoch seconds.
STAMPED_SINCE: int = 1795867200
_STAMP_LINE = re.compile(rf"^{re.escape(STAMP)}\s*$", re.MULTILINE)
_FIELD: str = "\x00"
_RECORD: str = "\x1e"


@dataclass(frozen=True)
class Commit:
    """A commit of the branch: its sha, its first parent (empty for a root), its committer's email, its author date
    (epoch seconds), its message, and whether its first parent's tree holds the commit hook's file."""

    sha: str
    parent: str
    committer: str
    authored: int
    message: str
    parent_has_hook: bool


def stamped(message: str) -> bool:
    """Whether ``message`` carries the commit hook's stamp on a line of its own."""
    return bool(_STAMP_LINE.search(message))


def held(commit: Commit) -> bool:
    """Whether ``commit`` must carry the stamp: written since the hook was installed, on a tree that had it, and not
    by GitHub."""
    return (
        commit.parent_has_hook and commit.authored >= STAMPED_SINCE and commit.committer.casefold() != GITHUB_COMMITTER
    )


def unstamped(commits: tuple[Commit, ...]) -> tuple[Commit, ...]:
    """The commits held to the stamp that lack it, in the order given."""
    return tuple(commit for commit in commits if held(commit) and not stamped(commit.message))


def _git(args: list[str], stdin: str) -> str:
    done: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", *args], input=stdin, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({done.returncode}): {done.stderr.strip()[:600]}")
    return done.stdout


def _commits(base: str, head: str) -> tuple[Commit, ...]:
    fork: str = _git(["merge-base", base, head], "").strip()
    log: str = _git(["log", "--format=%H%x00%P%x00%ce%x00%at%x00%B%x1e", f"{fork}..{head}"], "")
    fields: list[list[str]] = [block.lstrip("\n").split(_FIELD, 4) for block in log.split(_RECORD) if block.strip()]
    parents: list[str] = [row[1].split(" ")[0] if row[1] else "" for row in fields]
    asked: list[str] = [f"{parent}:{HOOK_FILE}" for parent in parents if parent]
    answers: list[str] = _git(["cat-file", "--batch-check"], "".join(f"{line}\n" for line in asked)).splitlines()
    present: dict[str, bool] = {
        line.split(":", 1)[0]: not answer.endswith(" missing") for line, answer in zip(asked, answers, strict=True)
    }
    return tuple(
        Commit(
            sha=row[0],
            parent=parent,
            committer=row[2],
            authored=int(row[3]),
            message=row[4],
            parent_has_hook=present.get(parent, False),
        )
        for row, parent in zip(fields, parents, strict=True)
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        missing: tuple[Commit, ...] = unstamped(_commits(args.base, args.head))
    except RuntimeError as failed:
        print(f"hook_stamps: {failed}", file=sys.stderr)
        return 2
    if not missing:
        print("hook_stamps: every commit was made with the repository's hooks")
        return 0
    print(
        f"hook_stamps: these commits were made without the repository's hooks (no `{STAMP}` trailer):", file=sys.stderr
    )
    for commit in missing:
        print(f"  {commit.sha[:12]} {commit.message.splitlines()[0] if commit.message else ''}", file=sys.stderr)
    print(
        'Install the commit hook once (`cp .claude/hooks/commit-msg.sh "$(git rev-parse --git-path hooks)/commit-msg"`'
        "), never switch the hooks off, and make each commit again with them: `git commit --amend --no-edit` for the "
        "last, `git rebase --exec 'git commit --amend --no-edit' <base>` for several.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
