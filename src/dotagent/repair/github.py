"""What the repair night asks of git and of GitHub's CLI (item 921): a checkout of its own for a session, the files and
the checks of the pull request a session opened, the merge, and the pull of the merged change into the owner's Dot.

Every command is run with a list of words, never a shell, in the repository the night was given, and one that fails
raises :class:`~dotagent.core.errors.RepairError` with what it said."""

import asyncio
import fnmatch
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dotagent.core.errors import RepairError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.tools.shell import kill_group

log = get_logger(__name__)

type CheckBucket = Literal["pass", "fail", "pending", "skipping", "cancel"]

# How long one command of git or GitHub's CLI may take.
COMMAND_TIMEOUT_S: float = 300.0
# How long a push may take: it runs the repository's pre-push hook, a whole review by a model of its own (Opus on the
# owner's Mac), which takes minutes for a fix with its test and its entries.
PUSH_TIMEOUT_S: float = 1800.0
# What is kept of a command's output, for a failure's context.
_OUTPUT_KEPT: int = 4000
_PR_URL: re.Pattern[str] = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)")
# The heading of REVIEW.md's list of the paths whose change needs the owner's look, and the paths in it.
_HUMAN_HEADING: str = "## Always needs a human"
_BACKTICKED: re.Pattern[str] = re.compile(r"`([^`\s]+)`")
# A commit of an item: ``Item 12: ...``, ``Items 12 and 13: ...``.
_ITEM_SUBJECT: re.Pattern[str] = re.compile(r"^Items? ([\d ,and]+):")
# What the review hook prints when it stops a push: where its review is.
_REVIEW_FILE: re.Pattern[str] = re.compile(r"the review is in (\S+\.md)")
# The run of a check, in its link.
_RUN_ID: re.Pattern[str] = re.compile(r"/actions/runs/(\d+)")
# The folder of Claude Code's own checkouts under the owner's.
_WORKTREES: str = ".claude/worktrees/"
# The most commits of main a second look reads for one with no pull request (item 933).
_MOST_COMMITS_READ: int = 100
# What is kept of a failed check's log for the session.
_LOG_KEPT: int = 6000


class Check(FrozenModel):
    """A check of a pull request: its name, its bucket (``pass``, ``fail``, ``pending``, ``skipping``, ``cancel``) and
    the page of its run."""

    name: str
    bucket: CheckBucket
    link: str


def pr_number(text: str) -> int | None:
    """The number of the last pull request on GitHub that ``text`` links to, or ``None``."""
    found: list[str] = _PR_URL.findall(text)
    return int(found[-1]) if found else None


def human_paths(review: str) -> tuple[str, ...]:
    """The paths REVIEW.md lists under its "Always needs a human" heading (``review`` is its text): a path ending in
    ``/`` is a folder, any other a file or a pattern. Empty when there is no such heading, which the night takes as
    every path needing the owner."""
    start: int = review.find(_HUMAN_HEADING)
    if start < 0:
        return ()
    body: str = review[start + len(_HUMAN_HEADING) :]
    end: int = body.find("\n## ")
    section: str = body if end < 0 else body[:end]
    return tuple(dict.fromkeys(_BACKTICKED.findall(section)))


def needs_the_owner(files: Sequence[str], paths: Sequence[str]) -> tuple[str, ...]:
    """The ``files`` of a change that are under ``paths`` (:func:`human_paths`): in a folder of them, one of them, or
    matching one as a pattern."""
    return tuple(
        name
        for name in files
        if any(
            name.startswith(path) if path.endswith("/") else (name == path or fnmatch.fnmatch(name, path))
            for path in paths
        )
    )


def checks_of(raw: str) -> tuple[Check, ...]:
    """The checks ``gh pr checks --json name,bucket,link`` printed; a bucket GitHub's CLI may add later is pending."""
    items: object = _json(raw, "the checks")
    if not isinstance(items, list):
        raise RepairError("GitHub's CLI gave no list of checks", {"output": raw[:_OUTPUT_KEPT]})
    checks: list[Check] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        bucket: object = item.get("bucket")
        checks.append(
            Check(
                name=str(item.get("name", "")),
                bucket=bucket if bucket in ("pass", "fail", "pending", "skipping", "cancel") else "pending",
                link=str(item.get("link", "")),
            )
        )
    return tuple(checks)


def settled(checks: Sequence[Check]) -> bool:
    """Whether no check is still pending (and there is at least one)."""
    return bool(checks) and all(check.bucket != "pending" for check in checks)


def all_passed(checks: Sequence[Check]) -> bool:
    """Whether every check passed (one skipped is no failure), and there is at least one that ran."""
    return any(check.bucket == "pass" for check in checks) and all(
        check.bucket in ("pass", "skipping") for check in checks
    )


async def command(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> str:
    """Run ``argv`` in ``cwd`` and give what it printed, within :data:`COMMAND_TIMEOUT_S`
    (:func:`command_within`)."""
    return await command_within(argv, cwd, env, COMMAND_TIMEOUT_S)


async def command_within(argv: Sequence[str], cwd: Path, env: Mapping[str, str], timeout_s: float) -> str:
    """Run ``argv`` in ``cwd`` and give what it printed. Raises :class:`RepairError` when it fails or takes longer than
    ``timeout_s`` (then it is ended with everything it started)."""
    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=dict(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise RepairError(
            "a command of the repair could not start", {"argv": list(argv), "error": str(error)}
        ) from error
    try:
        async with asyncio.timeout(timeout_s):
            out, err = await process.communicate()
    except TimeoutError as error:
        raise RepairError(
            "a command of the repair took too long", {"argv": list(argv), "timeout_s": timeout_s}
        ) from error
    finally:
        # However it ended (a time limit, the emergency stop's cancel, the daemon's stop), nothing it started goes on:
        # a push through the review hook or a merge must not finish after the stop (the seventh review).
        kill_group(process.pid)
        if process.returncode is None:
            await asyncio.shield(process.wait())
    if process.returncode != 0:
        raise RepairError(
            "a command of the repair failed",
            {
                "argv": list(argv),
                "code": process.returncode,
                "stderr": err.decode("utf-8", errors="replace")[-_OUTPUT_KEPT:],
                "stdout": out.decode("utf-8", errors="replace")[-_OUTPUT_KEPT:],
            },
        )
    return out.decode("utf-8", errors="replace")


async def make_checkout(repo: Path, folder: Path, branch: str, env: Mapping[str, str]) -> None:
    """A checkout of its own at ``folder`` on a new ``branch`` from ``origin/main``, fetched first."""
    await command(("git", "-C", str(repo), "fetch", "--quiet", "origin", "main"), repo, env)
    await command(("git", "-C", str(repo), "worktree", "add", "-b", branch, str(folder), "origin/main"), repo, env)


async def drop_checkout(repo: Path, folder: Path, env: Mapping[str, str]) -> None:
    """Remove the checkout at ``folder`` (its branch stays, for its pull request)."""
    await command(("git", "-C", str(repo), "worktree", "remove", "--force", str(folder)), repo, env)


async def drop_branch(repo: Path, branch: str, env: Mapping[str, str]) -> None:
    """Delete the local ``branch`` of a session that opened no pull request."""
    await command(("git", "-C", str(repo), "branch", "-D", branch), repo, env)


def _json(raw: str, what: str) -> object:
    """``raw`` read as JSON; :class:`RepairError` naming ``what`` when it is none."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise RepairError(f"GitHub's CLI gave no JSON for {what}", {"output": raw[:_OUTPUT_KEPT]}) from error


async def branch_items(folder: Path, env: Mapping[str, str]) -> tuple[int, ...]:
    """The item numbers of the commits on the checkout at ``folder`` since ``origin/main``, oldest first, each once
    (a commit is ``Item <n>: ...`` or ``Items <n> and <m>: ...``)."""
    subjects: str = await command(
        ("git", "-C", str(folder), "log", "--reverse", "--format=%s", "origin/main..HEAD"), folder, env
    )
    return items_of(subjects.splitlines())


def items_of(subjects: Sequence[str]) -> tuple[int, ...]:
    """The item numbers of commit ``subjects``, in order, each once (``Item <n>: ...``, ``Items <n> and <m>: ...``)."""
    found: list[int] = []
    for subject in subjects:
        head: re.Match[str] | None = _ITEM_SUBJECT.match(subject)
        if head is not None:
            found.extend(int(number) for number in re.findall(r"\d+", head.group(1)))
    return tuple(dict.fromkeys(found))


async def branch_commits(folder: Path, env: Mapping[str, str]) -> tuple[str, ...]:
    """The messages of the commits on the checkout at ``folder`` since ``origin/main``, oldest first."""
    raw: str = await command(
        ("git", "-C", str(folder), "log", "--reverse", "--format=%B%x00", "origin/main..HEAD"), folder, env
    )
    return tuple(message.strip() for message in raw.split("\x00") if message.strip())


async def branch_files(folder: Path, env: Mapping[str, str]) -> tuple[str, ...]:
    """The paths the checkout at ``folder`` changes since it left ``origin/main``: a file moved is both its old path and
    its new one (``--no-renames``), so that moving a file out of a protected path is a change to that path."""
    raw: str = await command(
        ("git", "-C", str(folder), "diff", "--name-only", "--no-renames", "origin/main...HEAD"), folder, env
    )
    return tuple(line for line in raw.splitlines() if line)


async def head_of(folder: Path, env: Mapping[str, str]) -> str:
    """The commit the checkout at ``folder`` stands on."""
    return (await command(("git", "-C", str(folder), "rev-parse", "HEAD"), folder, env)).strip()


async def push_branch(repo: Path, head: str, branch: str, pushed: str | None, env: Mapping[str, str]) -> str | None:
    """Push the commit ``head`` as ``branch`` from the owner's checkout at ``repo``, never from the session's, through
    the repository's pre-push hook (the tenth review of item 921): the hook reads its instructions and its settings from
    the folder it runs in, and its base from ``origin/main``, which a session could have left changed in its own
    checkout and in the refs all checkouts share. The owner's checkout is the one Dot's print guards; ``main`` is
    fetched first, and a mark of a passed review for ``head`` is removed, so that only the hook's own review passes it.
    ``pushed`` is the commit Dot last pushed there, ``None`` before its first push. Gives ``None`` once it is pushed
    with the hook's mark of a passed review, or the review the hook wrote when it
    stopped the push (its BLOCK), for the session to answer. Raises :class:`UnreviewedPush` for a push that went through
    with no such mark, and :class:`RepairError` for any other failure."""
    await command(("git", "-C", str(repo), "fetch", "--quiet", "origin", "main"), repo, env)
    # The branch on GitHub must stand where Dot left it: nowhere before Dot's first push, at ``pushed`` after (item
    # 943, the pre-push review). A session that pushed a commit of its own there would have the hook review only what
    # came after it, and mark the head passed.
    listed: str = await command(("git", "-C", str(repo), "ls-remote", "origin", f"refs/heads/{branch}"), repo, env)
    remote: str | None = listed.split()[0] if listed.split() else None
    if remote != pushed:
        raise UnreviewedPush(
            "the branch on GitHub is not where Dot left it: the session may have pushed to it itself",
            {"branch": branch, "on_github": remote, "pushed_by_dot": pushed},
        )
    git_dir: str = (await command(("git", "-C", str(repo), "rev-parse", "--absolute-git-dir"), repo, env)).strip()
    await asyncio.to_thread((Path(git_dir) / "claude-review" / f"{head}.pass").unlink, True)
    # No ``-u``: an upstream would be written in the git config the print guards.
    argv: tuple[str, ...] = ("git", "-C", str(repo), "push", "origin", f"{head}:refs/heads/{branch}")
    mark: Path = Path(git_dir) / "claude-review" / f"{head}.pass"
    try:
        await command_within(argv, repo, env, PUSH_TIMEOUT_S)
    except RepairError as error:
        said: str = f"{error.context.get('stderr', '')}\n{error.context.get('stdout', '')}"
        found: re.Match[str] | None = _REVIEW_FILE.search(said)
        if found is None:
            raise
        # The hook names it from the folder it ran in (``.git/claude-review/<sha>.md``): the owner's checkout.
        named: Path = Path(found.group(1))
        review: Path = named if named.is_absolute() else repo / named
        try:
            return await asyncio.to_thread(review.read_text, encoding="utf-8")
        except OSError as missing:
            raise RepairError(
                "the review hook stopped the push and its review cannot be read", {"path": str(review)}
            ) from missing
    # A push that went through is a passed review only with the hook's own mark of it (item 943, the review of
    # 2026-11-02): a branch the session pushed itself leaves Dot's push nothing to send, and a hooks path it set in the
    # owner's global git config runs no hook; either way git exits 0 and nothing was reviewed.
    if not await asyncio.to_thread(mark.exists):
        raise UnreviewedPush(
            "the push went through with no review of the hook's: the branch may have been pushed by the session itself",
            {"branch": branch, "head": head},
        )
    return None


class UnreviewedPush(RepairError):
    """A push of a session's branch that went through without the review hook's mark of a passed review."""


async def open_pr(folder: Path, branch: str, title: str, body: str, env: Mapping[str, str]) -> str:
    """Open the pull request of ``branch`` and give its link."""
    out: str = await command(
        ("gh", "pr", "create", "--head", branch, "--base", "main", "--title", title, "--body", body), folder, env
    )
    if pr_number(out) is None:
        raise RepairError("GitHub's CLI gave no link of the pull request it opened", {"output": out[:_OUTPUT_KEPT]})
    return out.strip().splitlines()[-1]


async def pr_files(repo: Path, number: int, env: Mapping[str, str]) -> tuple[str, ...]:
    """The paths the pull request ``number`` changes, as GitHub has them, every page of them (a pull request's view
    lists the first hundred only), a renamed file by its old path too."""
    raw: str = await command(
        (
            "gh",
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/pulls/{number}/files",
            "--jq",
            ".[] | .filename, (.previous_filename // empty)",
        ),
        repo,
        env,
    )
    return tuple(line for line in raw.splitlines() if line)


async def pr_checks(repo: Path, number: int, env: Mapping[str, str]) -> tuple[Check, ...]:
    """The checks of the pull request ``number`` as they stand. ``gh pr checks`` exits 8 while some are pending and 1
    when one failed, the list printed either way, and says "no checks reported" before any has started: those are
    read. Any other failure (a sign-in, a pull request not found, the network) is raised."""
    argv: tuple[str, ...] = ("gh", "pr", "checks", str(number), "--json", "name,bucket,link")
    try:
        return checks_of(await command(argv, repo, env))
    except RepairError as error:
        printed: object = error.context.get("stdout")
        stderr: object = error.context.get("stderr")
        if error.context.get("code") in (1, 8) and isinstance(printed, str) and printed.strip():
            return checks_of(printed)
        if isinstance(stderr, str) and "no checks reported" in stderr:
            return ()
        raise


async def failed_log(repo: Path, check: Check, env: Mapping[str, str]) -> str:
    """The end of the log of the failed steps of ``check``'s run (:data:`_LOG_KEPT` characters)."""
    run: re.Match[str] | None = _RUN_ID.search(check.link)
    if run is None:
        raise RepairError(
            "a failed check names no run whose log could be read", {"check": check.name, "link": check.link}
        )
    out: str = await command(("gh", "run", "view", run.group(1), "--log-failed"), repo, env)
    return out[-_LOG_KEPT:]


async def rerun_failed(repo: Path, checks: Sequence[Check], env: Mapping[str, str]) -> None:
    """Run again the failed jobs of the runs of ``checks`` that failed, each run once."""
    runs: set[str] = set()
    for check in checks:
        found: re.Match[str] | None = _RUN_ID.search(check.link)
        if found is not None and check.bucket in ("fail", "cancel"):
            runs.add(found.group(1))
    for run in sorted(runs):
        await command(("gh", "run", "rerun", run, "--failed"), repo, env)


async def pr_state(repo: Path, number: int, env: Mapping[str, str]) -> str:
    """The state of the pull request ``number`` (``OPEN``, ``MERGED``, ``CLOSED``)."""
    data: object = _json(await command(("gh", "pr", "view", str(number), "--json", "state"), repo, env), "its state")
    return str(data.get("state", "")) if isinstance(data, dict) else ""


class PrHead(FrozenModel):
    """Where a pull request stands (item 933): its state (``OPEN``, ``MERGED``, ``CLOSED``), its branch, the commit its
    branch is at, and GitHub's ``mergeStateStatus`` of it (``BEHIND`` main, ``UNKNOWN`` while GitHub works it out,
    ``CLEAN``, ``BLOCKED`` and the others)."""

    state: str
    branch: str
    sha: str
    merge_state: str

    @property
    def behind(self) -> bool:
        return self.merge_state == "BEHIND"

    @property
    def known(self) -> bool:
        """Whether GitHub has said where it stands against main."""
        return self.merge_state not in ("", "UNKNOWN")


async def pr_head(repo: Path, number: int, env: Mapping[str, str]) -> PrHead:
    """Where the pull request ``number`` stands, read now. Raises :class:`RepairError` for an answer that names none."""
    raw: str = await command(
        ("gh", "pr", "view", str(number), "--json", "state,headRefName,headRefOid,mergeStateStatus"), repo, env
    )
    data: object = _json(raw, "its head")
    if not isinstance(data, dict):
        raise RepairError("GitHub's CLI named no head of the pull request", {"number": number, "output": raw[:400]})
    state: object = data.get("state")
    branch: object = data.get("headRefName")
    sha: object = data.get("headRefOid")
    if not isinstance(state, str) or not isinstance(branch, str) or not isinstance(sha, str) or not sha:
        raise RepairError("GitHub's CLI named no head of the pull request", {"number": number, "output": raw[:400]})
    merge_state: object = data.get("mergeStateStatus")
    return PrHead(state=state, branch=branch, sha=sha, merge_state=merge_state if isinstance(merge_state, str) else "")


async def pr_items(repo: Path, number: int, env: Mapping[str, str]) -> tuple[int, ...]:
    """The item numbers of the commits of the pull request ``number``, as GitHub holds them."""
    raw: str = await command(
        ("gh", "pr", "view", str(number), "--json", "commits", "--jq", ".commits[].messageHeadline"), repo, env
    )
    return items_of(raw.splitlines())


async def commits_without_pr(repo: Path, since: str, env: Mapping[str, str]) -> tuple[str, ...]:
    """The commits main holds on GitHub after ``since`` that belong to no merged pull request (item 933): GitHub's
    protection lets none in but through one, so one there came past it. Raises :class:`RepairError` for more than
    :data:`_MOST_COMMITS_READ` of them, which are not all read."""
    await command(("git", "-C", str(repo), "fetch", "--quiet", "origin", "main"), repo, env)
    listed: str = await command(("git", "-C", str(repo), "rev-list", f"{since}..origin/main"), repo, env)
    shas: list[str] = [line for line in listed.splitlines() if line]
    if len(shas) > _MOST_COMMITS_READ:
        raise RepairError("main moved by too many commits to read", {"since": since, "commits": len(shas)})
    strays: list[str] = []
    for sha in shas:
        merged: str = await command(
            (
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/commits/{sha}/pulls",
                "--jq",
                "[.[] | select(.merged_at != null)] | length",
            ),
            repo,
            env,
        )
        if merged.strip() in ("", "0"):
            strays.append(sha)
    return tuple(strays)


async def checkout_clean(repo: Path, env: Mapping[str, str]) -> bool:
    """Whether the owner's checkout at ``repo`` has no change to a tracked file and stands on a commit of main on
    GitHub (item 933): what a merge would pull into and restart with."""
    changed: str = await command(("git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"), repo, env)
    if changed.strip():
        return False
    # Main as GitHub holds it, fetched: the checkout's own origin/main is a ref a session could move (item 940).
    main: str = await main_on_github(repo, env)
    await command(("git", "-C", str(repo), "fetch", "--quiet", "origin", "main"), repo, env)
    head: str = (await command(("git", "-C", str(repo), "rev-parse", "HEAD"), repo, env)).strip()
    return await is_ancestor(repo, head, main, env)


async def update_branch(repo: Path, number: int, env: Mapping[str, str]) -> None:
    """Bring the branch of the pull request ``number`` up to main on GitHub (a merge of main into it), so that its
    checks run on what a merge would make."""
    await command(("gh", "pr", "update-branch", str(number)), repo, env)


async def pr_diff(repo: Path, number: int, env: Mapping[str, str]) -> str:
    """The change of the pull request ``number`` as GitHub holds it, a unified diff."""
    return await command(("gh", "pr", "diff", str(number)), repo, env)


async def checkout_at(repo: Path, folder: Path, branch: str, sha: str, env: Mapping[str, str]) -> None:
    """A checkout of its own at ``folder`` of the commit ``sha`` of ``branch`` on GitHub, fetched first, on no
    branch."""
    await command(("git", "-C", str(repo), "fetch", "--quiet", "origin", branch), repo, env)
    await command(("git", "-C", str(repo), "worktree", "add", "--detach", str(folder), sha), repo, env)


async def drop_remote_branch(repo: Path, branch: str, env: Mapping[str, str]) -> None:
    """Delete ``branch`` on GitHub, once its pull request is merged."""
    await command(("gh", "api", "-X", "DELETE", f"repos/{{owner}}/{{repo}}/git/refs/heads/{branch}"), repo, env)


async def merge_pr(
    repo: Path, number: int, head: str, method: Literal["squash", "rebase"], env: Mapping[str, str]
) -> None:
    """Merge the pull request ``number`` into main by ``method``, only while its head is still ``head``, the commit
    whose files and checks were looked at. Its branch is not deleted here: the session's checkout still holds it, and
    ``gh``'s ``--delete-branch`` fails on a branch checked out in a worktree after the merge has gone through (the
    seventh review). A merge that reports a failure is read back from GitHub: merged is merged."""
    try:
        await command(("gh", "pr", "merge", str(number), f"--{method}", "--match-head-commit", head), repo, env)
    except RepairError:
        if await pr_state(repo, number, env) != "MERGED":
            raise


async def main_on_github(repo: Path, env: Mapping[str, str]) -> str:
    """The commit ``main`` stands on, on GitHub, read now."""
    out: str = await command(("git", "-C", str(repo), "ls-remote", "origin", "refs/heads/main"), repo, env)
    sha: str = out.split()[0] if out.split() else ""
    if not sha:
        raise RepairError("GitHub named no commit of main", {"output": out[:_OUTPUT_KEPT]})
    return sha


async def merged_since(repo: Path, since: float, env: Mapping[str, str]) -> tuple[str, ...]:
    """The pull requests merged into main since ``since`` (epoch seconds), as ``#<n> <title> (<branch>)``."""
    stamp: str = datetime.fromtimestamp(since, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    data: object = _json(
        await command(
            (
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--base",
                "main",
                "--search",
                f"merged:>={stamp}",
                "--json",
                "number,title,headRefName",
            ),
            repo,
            env,
        ),
        "the pull requests merged",
    )
    if not isinstance(data, list):
        return ()
    return tuple(
        f"#{item.get('number')} {item.get('title')} ({item.get('headRefName')})"
        for item in data
        if isinstance(item, dict)
    )


async def review_on_main(repo: Path, env: Mapping[str, str]) -> str:
    """REVIEW.md as ``main`` holds it on GitHub (fetched first): what the owner merged, never a checkout's copy a
    session could have changed."""
    await command(("git", "-C", str(repo), "fetch", "--quiet", "origin", "main"), repo, env)
    try:
        return await command(("git", "-C", str(repo), "show", "origin/main:REVIEW.md"), repo, env)
    except RepairError as error:
        # Every path is then the owner's (the night's own rule for a REVIEW.md that lists none); the log says why.
        log.warning("repair_review_md_not_read", error=error.message, context=error.context)
        return ""


async def next_item(repo: Path, python: str, env: Mapping[str, str]) -> int:
    """The next free item number of the repository at ``repo``, by its own ``scripts/next_item.py`` run with Dot's
    sign-in to GitHub (it reads the open pull requests and pushed branches): a session has no way to GitHub, so it is
    given its number (the eleventh review of item 921). Raises :class:`RepairError` when none can be told."""
    out: str = await command((python, "scripts/next_item.py", "1"), repo, env)
    found: re.Match[str] | None = re.match(r"\s*(\d+)", out)
    if found is None:
        raise RepairError("scripts/next_item.py gave no item number", {"output": out[:_OUTPUT_KEPT]})
    return int(found.group(1))


async def has_commits(folder: Path, start: str, env: Mapping[str, str]) -> bool:
    """Whether the checkout at ``folder`` has commits since ``start``."""
    return bool((await command(("git", "-C", str(folder), "rev-list", f"{start}..HEAD"), folder, env)).strip())


def config_print(listed: str) -> str:
    """The owner's git config as the print reads it (item 935), from what ``git config --list -z`` gives (each entry its
    key, a line break and its value, ended by a NUL): every entry but those of a branch other than ``main``
    (``branch.<name>.<key>``). Those are what ``git push -u`` and ``git branch`` write for any branch, another agent's
    working in the owner's checkout among them, and on the owner's Mac they stopped every night's session as a change of
    the checkout; a pull of main reads none of them. ``main``'s entries (its remote, what it merges) and every other
    stay, as they decide what Dot pulls and runs. Git reads the file, so what it would act on is what the print holds
    (the pre-push review: a parser of Dot's own read two headers on a line, and quotes escaped in a header, otherwise
    than git)."""
    kept: list[str] = []
    for entry in listed.split("\0"):
        if not entry:
            continue
        key: str = entry.split("\n", 1)[0]
        if key.startswith("branch.") and key.count(".") >= 2:
            name: str = key[len("branch.") : key.rindex(".")]
            if name != "main":
                continue
        kept.append(entry)
    return "\0".join(kept)


def _files_print(paths: Sequence[Path]) -> str:
    """A digest of the contents of ``paths`` (a link read through to what it points at; a path with no file as such)."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<none>")
    return digest.hexdigest()


async def is_ancestor(repo: Path, older: str, newer: str, env: Mapping[str, str]) -> bool:
    """Whether the commit ``older`` is ``newer`` or comes before it in ``repo`` (``git merge-base --is-ancestor``). Its
    answer no is the command's exit 1; any other failure (an object missing, a ref locked) is raised, never read as no
    (the pre-push review of item 940)."""
    try:
        await command(("git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer), repo, env)
    except RepairError as error:
        if error.context.get("code") == 1:
            return False
        raise
    return True


async def along_main(repo: Path, before: str, after: str, env: Mapping[str, str]) -> bool:
    """Whether the owner's checkout went from the commit ``before`` to ``after`` along main (item 940): forward from
    where it stood, to a commit of main on GitHub (read from GitHub and fetched, never from the checkout's own refs,
    which a session could move). A pull of theirs along main is that, a deploy during a night; going back to an older
    commit, or to one main does not hold, is not."""
    if after == before:
        return True
    main: str = await main_on_github(repo, env)
    await command(("git", "-C", str(repo), "fetch", "--quiet", "origin", "main"), repo, env)
    return await is_ancestor(repo, before, after, env) and await is_ancestor(repo, after, main, env)


async def checkout_committed_at(repo: Path, env: Mapping[str, str]) -> float:
    """When the commit the owner's checkout at ``repo`` stands on was made (epoch seconds; item 943)."""
    out: str = await command(("git", "-C", str(repo), "log", "-1", "--format=%ct"), repo, env)
    return float(out.strip())


async def checkout_print(repo: Path, env: Mapping[str, str]) -> str:
    """A print of the owner's checkout at ``repo`` that changes when what Dot would pull and restart with does (the
    sixth and ninth reviews of item 921): the contents of every change to a tracked file
    (``git diff HEAD``), the contents of every file git does not track and does not ignore, its git config as git
    reads it (but the entries of branches other than main, :func:`config_print`) and its hooks (each read through its
    link: the review hook lives in the checkout). Files git ignores (``.venv``) are not in it, which docs/repair.md
    says. A session runs with the owner's rights, so it could write there outside its own checkout; Dot compares the
    print before and after it, and before it merges. The commit it stands on is read apart from it (item 940,
    :func:`along_main`): one it went forward to along main is no change of the session's."""
    changed: str = await command(("git", "-C", str(repo), "diff", "HEAD", "--binary"), repo, env)
    untracked: str = await command(
        ("git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z"), repo, env
    )
    common: str = (await command(("git", "-C", str(repo), "rev-parse", "--git-common-dir"), repo, env)).strip()
    folder: Path = (repo / common) if not Path(common).is_absolute() else Path(common)
    hooks: list[Path] = sorted((folder / "hooks").glob("*"))
    # The checkouts of other sessions under the owner's (Claude Code's own) are no file of it; Claude Code's local
    # settings are one git ignores, read by the review hook's reviewer.
    loose: list[Path] = [repo / name for name in untracked.split("\0") if name and not name.startswith(_WORKTREES)]
    settings: Path = repo / ".claude" / "settings.local.json"
    files: str = await asyncio.to_thread(_files_print, [*hooks, *loose, settings])
    # Read by git itself, its includes followed; a file git cannot read fails the print, which counts as changed.
    listed: str = await command(
        ("git", "config", "--file", str(folder / "config"), "--includes", "--list", "-z"), repo, env
    )
    return hashlib.sha256(f"{changed}\n{config_print(listed)}\n{files}".encode()).hexdigest()


async def pull_main(repo: Path, env: Mapping[str, str]) -> bool:
    """Bring the owner's checkout at ``repo`` up to main, fast-forward only, when it is on main; ``False`` when it is
    on another branch, and is left as it is."""
    branch: str = (await command(("git", "-C", str(repo), "branch", "--show-current"), repo, env)).strip()
    if branch != "main":
        return False
    await command(("git", "-C", str(repo), "pull", "--ff-only", "--quiet"), repo, env)
    return True
