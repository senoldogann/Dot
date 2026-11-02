"""The repair night (items 919-921), played whole against a real git repository on this Mac: a bare ``origin`` and the
owner's checkout of it, with a REVIEW.md of its own; stand-ins for ``claude`` and ``gh`` (programs this test writes,
which call no model and reach no network) on the PATH the night is given.

What it shows: a problem of the book is handed to a session in a checkout of its own, whose environment holds no API
key and throwaway folders of Dot; a pull request whose checks pass is merged with ``merge = "on_pass"`` and the restart
asked for; one that changes a path REVIEW.md lists waits for the owner, merged never; a session that finds no defect
lets the problem go; the plan's window refusing a session ends the night; the owner is told what is theirs."""

import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import pytest
import pytest_asyncio

from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.events import Subscription
from dotagent.notify.owner import OWNER_TOLD_EVENT
from dotagent.repair import night as night_module
from dotagent.repair import second as second_module
from dotagent.repair.night import FIXED_TITLE, NEEDS_YOU_TITLE, SESSIONS_KEY, RepairSession, read_sessions, repair_night
from dotagent.repair.problems import BOOK_KEY, EMPTY_BOOK, Book, Sighting, read_book, with_sightings
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

type Mode = Literal[
    "pr", "no_fix", "limited", "rogue", "rename", "tamper", "busy", "tracked", "pulled", "rewound", "selfpush"
]

_REVIEW: str = "# Review\n\n## Always needs a human\n\n- `src/dotagent/policy/`\n- `src/dotagent/agent/review.py`\n"
_GIT_ENV: dict[str, str] = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}
# The stand-in of ``claude``: it writes down how it was called and what its environment held, makes a commit in the
# checkout it was started in, and streams what its mode says.
_CLAUDE: str = """
import json, os, subprocess, sys
from pathlib import Path
here = Path(__file__).resolve().parent
mode = (here / "mode").read_text().strip()
if sys.argv[1:3] == ["-p", "/usage"]:
    used = (here / "usage").read_text().strip() if (here / "usage").exists() else "10"
    print(json.dumps({"type": "system", "subtype": "local_command", "usage_report": {"rate_limits": {"limits": [
        {"kind": "session", "percent": float(used), "resets_at": "2100-01-01T00:00:00+00:00", "is_active": True},
        {"kind": "weekly_scoped", "percent": 100, "is_active": False}]}}}))
    sys.exit(0)
if "--tools" in sys.argv:
    # A second look (item 933): it may only read; its verdict is the file "look"'s, MERGE by default.
    with (here / "looks.jsonl").open("a") as looks:
        looks.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
    verdict = (here / "look").read_text().strip() if (here / "look").exists() else "MERGE: the change is its fix alone"
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "look-1"}))
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Read it.\\n" + verdict,
                      "session_id": "look-1", "total_cost_usd": 0.1, "num_turns": 2, "permission_denials": [],
                      "usage": {"input_tokens": 50, "output_tokens": 5}}))
    sys.exit(0)
# The session's transcript, where Claude Code keeps it: its writes, as its tools name them.
import re
projects = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
projects.mkdir(parents=True, exist_ok=True)
def wrote(path):
    use = {"type": "tool_use", "name": "Write", "input": {"file_path": str(Path(path).resolve())}}
    with (projects / "s-7.jsonl").open("a") as transcript:
        transcript.write(json.dumps({"type": "assistant", "message": {"content": [use]}}) + "\\n")
with (here / "calls.jsonl").open("a") as calls:
    calls.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "keys": sorted(os.environ),
                            "dot_home": os.environ.get("DOT_HOME", ""),
                            "gh_config": os.environ.get("GH_CONFIG_DIR", ""),
                            "helper": [os.environ.get("GIT_CONFIG_KEY_0"), os.environ.get("GIT_CONFIG_VALUE_0")],
                            "prompt": os.environ.get("GIT_TERMINAL_PROMPT")}) + "\\n")
def say(item):
    print(json.dumps(item), flush=True)
say({"type": "system", "subtype": "init", "session_id": "s-7"})
# A step, as Claude Code streams one (item 939).
say({"type": "assistant", "session_id": "s-7", "message": {"content": [{"type": "tool_use", "name": "Read",
     "input": {"file_path": os.getcwd() + "/REVIEW.md"}}]}})
status = "rejected" if mode == "limited" else "allowed"
say({"type": "rate_limit_event", "session_id": "s-7", "rate_limit_info": {"status": status, "resetsAt": 4102444800,
     "rateLimitType": "five_hour", "unifiedWindows": {"five_hour": {"utilization": 0.3}}}})
if mode == "tamper":
    # Outside its own checkout, in the owner's (three folders up: .claude/worktrees/<name>), and a fix of its own.
    Path("../../../loosened.py").write_text("ALLOW_EVERYTHING = True\\n")
    wrote("../../../loosened.py")
    Path("fix.txt").write_text("fixed\\n")
    wrote("fix.txt")
    subprocess.run(["git", "add", "fix.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: fix"], check=True)
    answer = "Fixed.\\nREADY: fixed"
elif mode == "rename":
    subprocess.run(["git", "mv", "REVIEW.md", "docs-review.md"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: move the review notes"], check=True)
    answer = "Moved.\\nREADY: moved"
elif mode == "rogue":
    Path("fix.txt").write_text("fixed\\n")
    subprocess.run(["git", "add", "fix.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: fix"], check=True)
    # Past what its environment was meant to keep it from: straight to main (a local origin needs no sign-in).
    subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], check=True)
    answer = "Fixed.\\nREADY: fixed"
elif mode == "selfahead":
    # A commit of its own pushed to its branch by itself, then one more on top, for Dot's push to bring alone.
    Path("sneak.txt").write_text("unreviewed\\n")
    wrote("sneak.txt")
    subprocess.run(["git", "add", "sneak.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: sneak"], check=True)
    shown = subprocess.run(["git", "branch", "--show-current"], check=True, capture_output=True, text=True)
    ref = "HEAD:refs/heads/" + shown.stdout.strip()
    subprocess.run(["git", "push", "-q", "--no-verify", "origin", ref], check=True)
    Path("fix.txt").write_text("fixed\\n")
    wrote("fix.txt")
    subprocess.run(["git", "add", "fix.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: fix"], check=True)
    answer = "Fixed it.\\nREADY: fixed"
elif mode == "selfpush":
    Path("fix.txt").write_text("fixed\\n")
    wrote("fix.txt")
    subprocess.run(["git", "add", "fix.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: fix"], check=True)
    # Past what its environment was meant to keep it from: its own branch to GitHub, no hook run on it.
    shown = subprocess.run(["git", "branch", "--show-current"], check=True, capture_output=True, text=True)
    branch = shown.stdout.strip()
    subprocess.run(["git", "push", "-q", "--no-verify", "origin", "HEAD:refs/heads/" + branch], check=True)
    answer = "Fixed it.\\nREADY: fixed"
elif mode == "rewound":
    # A command of its own takes the owner's checkout back to an older commit of main: a fix of main's undone there.
    subprocess.run(["git", "-C", "../../..", "reset", "-q", "--hard", "HEAD~1"], check=True)
    Path("fix.txt").write_text("fixed\\n")
    wrote("fix.txt")
    subprocess.run(["git", "add", "fix.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: fix"], check=True)
    answer = "Fixed it.\\nREADY: fixed"
elif mode == "pulled":
    # The owner deploys meanwhile: their checkout pulls main along, to a commit of main's (item 940).
    subprocess.run(["git", "-C", "../../..", "pull", "--ff-only", "-q", "origin", "main"], check=True)
    Path("fix.txt").write_text("fixed\\n")
    wrote("fix.txt")
    subprocess.run(["git", "add", "fix.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: fix"], check=True)
    answer = "Fixed it.\\nREADY: fixed"
elif mode == "tracked":
    # Another agent's `git push -u` meanwhile: a section of its branch in the owner's git config, which a pull of main
    # never reads (item 935).
    subprocess.run(["git", "-C", "../../..", "config", "branch.another-agent.remote", "origin"], check=True)
    Path("fix.txt").write_text("fixed\\n")
    wrote("fix.txt")
    subprocess.run(["git", "add", "fix.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: fix"], check=True)
    answer = "Fixed it.\\nREADY: fixed"
elif mode in ("pr", "busy"):
    again = "--resume" in sys.argv
    name = "fix-again.txt" if again else "fix.txt"
    Path(name).write_text("fixed\\n")
    wrote(name)
    if mode == "busy":
        # Meanwhile another agent of the owner's leaves a folder in their checkout: no write of the session's.
        Path("../../../another-agent").mkdir(exist_ok=True)
        Path("../../../another-agent/notes.md").write_text("mine\\n")
    subprocess.run(["git", "add", name], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Item 1: answer the review" if again else "Item 1: fix"], check=True)
    if mode == "busy" and (here / "push_main").exists():
        # And, by a command of its own its transcript does not show, a commit of its own straight to main.
        def out(*argv):
            return subprocess.run(argv, check=True, capture_output=True, text=True).stdout.strip()
        base = out("git", "rev-parse", "origin/main")
        sneak = out("git", "commit-tree", base + "^{tree}", "-p", base, "-m", "sneak")
        subprocess.run(["git", "push", "-q", "origin", sneak + ":refs/heads/main"], check=True)
    if (here / "forge").exists():
        # A mark of a passed review, as the hook writes one, for the commit it has not reviewed.
        git_dir = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], check=True,
                                 capture_output=True, text=True).stdout.strip()
        head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        Path(git_dir, "claude-review").mkdir(exist_ok=True)
        Path(git_dir, "claude-review", f"{head}.pass").write_text("PASS\\n")
    answer = "Fixed it.\\nREADY: a schedule starts with an effort its model offers"
elif mode == "no_fix":
    answer = "Looked.\\nNO FIX: the site was down, no defect of Dot's"
else:
    answer = "You've hit your limit"
say({"type": "result", "subtype": "success", "is_error": mode == "limited", "result": answer, "session_id": "s-7",
     "total_cost_usd": 0.5, "num_turns": 3, "permission_denials": [],
     "usage": {"input_tokens": 100, "output_tokens": 20}})
"""
# The stand-in of ``gh``: the files of the pull request from the file ``files``, every check passed, a merge written
# down.
_GH: str = """
import json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
args = sys.argv[1:]
if args[:2] == ["pr", "view"] and "state,headRefName,headRefOid,mergeStateStatus" in args:
    import subprocess
    listed = subprocess.run(["git", "-C", str(here.parent / "origin.git"), "branch", "--list", "repair/*",
                             "--format=%(refname:short) %(objectname)"], check=True, capture_output=True, text=True)
    branch, sha = listed.stdout.split()
    merged = (here / "merges").exists()
    behind = (here / "merge_state").exists() and not (here / "updates").exists()
    print(json.dumps({"state": "MERGED" if merged else "OPEN", "headRefName": branch, "headRefOid": sha,
                      "mergeStateStatus": "BEHIND" if behind else "CLEAN"}))
elif args[:2] == ["pr", "view"] and "commits" in args:
    print("Item 1: fix")
elif args[:1] == ["api"] and "/commits/" in args[1]:
    # Every commit of main belongs to a merged pull request, but when the session pushed to main itself.
    print("0" if (here / "push_main").exists() else "1")
elif args[:2] == ["pr", "view"]:
    files = [line for line in (here / "files").read_text().splitlines() if line]
    print(json.dumps({"files": [{"path": name} for name in files]}))
elif args[:2] == ["pr", "diff"]:
    print("diff --git a/fix.txt b/fix.txt\\n+fixed")
elif args[:2] == ["pr", "update-branch"]:
    # As GitHub does: a merge of main into the branch, a new head.
    import subprocess
    origin = str(here.parent / "origin.git")
    def git(*argv):
        return subprocess.run(["git", "-C", origin, *argv], check=True, capture_output=True, text=True).stdout.strip()
    branch = git("branch", "--list", "repair/*", "--format=%(refname:short)")
    merge = git("commit-tree", branch + "^{tree}", "-p", branch, "-p", "main", "-m", "Merge main")
    git("update-ref", "refs/heads/" + branch, merge)
    with (here / "updates").open("a") as updates:
        updates.write(" ".join(args) + "\\n")
elif args[:2] == ["pr", "list"]:
    print("[]")
elif args[:2] == ["pr", "create"]:
    print("https://github.com/o/r/pull/7")
elif args[:2] == ["pr", "checks"]:
    print(json.dumps([{"name": "gate", "bucket": "pass", "link": "https://example.invalid/1"}]))
elif args[:2] == ["api", "--paginate"]:
    for name in (here / "files").read_text().splitlines():
        if name:
            print(name)
elif args[:3] == ["api", "-X", "DELETE"]:
    with (here / "deleted").open("a") as deleted:
        deleted.write(args[3] + "\\n")
elif args[:2] == ["pr", "merge"]:
    with (here / "merges").open("a") as merges:
        merges.write(" ".join(args) + "\\n")
else:
    sys.exit(2)
"""


def _program(folder: Path, name: str, body: str) -> Path:
    path: Path = folder / name
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _pushed(origin: Path) -> str:
    """The commit the one ``repair/`` branch of ``origin`` stands on."""
    listed: str = subprocess.run(
        ("git", "-C", str(origin), "branch", "--list", "repair/*", "--format=%(objectname)"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return listed.strip()


def _review_hook(hook: Path, review: Path) -> None:
    """A pre-push hook that stops the first push as the review hook does and lets the next one go. Like the real one,
    it writes its review in the git folder and names it by a path relative to the folder it runs in
    (``.git/claude-review/…``); ``review`` (absolute) marks that it has run."""
    stop: str = "claude-review: push stopped; the review is in .git/claude-review/review.md. Fix what it found"
    # As the real hook does, a commit it holds a mark of a passed review for is not reviewed again.
    passed: str = 'read _ sha _ _; [ -e "$(git rev-parse --absolute-git-dir)/claude-review/$sha.pass" ] && exit 0\n'
    # Where the hook runs: its reviewer reads REVIEW.md and its settings from there.
    where: str = f'pwd -P > "{review}.where"\n'
    hook.write_text(
        f'#!/bin/sh\n{where}{passed}if [ ! -e "{review}" ]; then\n'
        f'  touch "{review}"\n  mkdir -p .git/claude-review\n'
        f'  echo "Important: the fix has no test" > .git/claude-review/review.md\n'
        f'  echo "{stop}" >&2\n  exit 1\nfi\n'
        'mkdir -p "$(git rev-parse --absolute-git-dir)/claude-review"\n'
        'touch "$(git rev-parse --absolute-git-dir)/claude-review/$sha.pass"\nexit 0\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)


# What the real hook does for a commit it passed: a mark of it in the git folder, which Dot reads after the push.
_PASS_MARK: str = (
    'while read _ sha _ _; do mkdir -p "$(git rev-parse --absolute-git-dir)/claude-review"; '
    'touch "$(git rev-parse --absolute-git-dir)/claude-review/$sha.pass"; done\n'
)


def _passing_hook(hook: Path) -> None:
    """A pre-push hook that passes every push and marks it, as the review hook does on a PASS."""
    hook.write_text(f"#!/bin/sh\n{_PASS_MARK}exit 0\n", encoding="utf-8")
    hook.chmod(0o755)


def _refusing_hook(hook: Path) -> None:
    """A pre-push hook that refuses every push without a review."""
    hook.write_text("#!/bin/sh\necho 'refused' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True, env={**os.environ, **_GIT_ENV})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


@pytest.fixture
def stand_ins(tmp_path: Path) -> Iterator[Path]:
    """A folder with the stand-ins of ``claude`` and ``gh``, the state of a signed-in ``claude``, and the owner's
    checkout of a repository whose ``origin`` is a bare one beside it."""
    folder: Path = tmp_path / "bin"
    folder.mkdir()
    _program(folder, "claude", _CLAUDE)
    _program(folder, "gh", _GH)
    (folder / "files").write_text("src/dotagent/tools/web.py\ntests/unit/test_web.py\n", encoding="utf-8")
    (folder / ".claude.json").write_text(
        json.dumps({"oauthAccount": {"emailAddress": "owner@example.invalid", "organizationName": "Org"}}),
        encoding="utf-8",
    )
    origin: Path = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo: Path = tmp_path / "Dot"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    _git(repo, "checkout", "-q", "-b", "main")
    # The repository's own hooks, as Dot's checkout has them: a global core.hooksPath (Codex sets one) is not its.
    _git(repo, "config", "core.hooksPath", str(repo / ".git" / "hooks"))
    (repo / "REVIEW.md").write_text(_REVIEW, encoding="utf-8")
    # The repository's own way to its next item number, which Dot runs for the session (it has no way to GitHub).
    (repo / "scripts").mkdir()
    (repo / "scripts" / "next_item.py").write_text('print("931")\n', encoding="utf-8")
    _git(repo, "add", "REVIEW.md", "scripts/next_item.py")
    _git(repo, "commit", "-q", "-m", "start")
    _git(repo, "push", "-q", "origin", "main")
    # The repository's review hook, as one that passes every push (a test that needs another writes its own).
    _passing_hook(repo / ".git" / "hooks" / "pre-push")
    yield folder


def _config(repo: Path, claude: Path, merge: Literal["ask", "on_pass"]) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "repair": config.repair.model_copy(
                update={"repo": str(repo), "claude": str(claude), "merge": merge, "checks_minutes": 5}
            )
        }
    )


@asynccontextmanager
async def _night(
    login_env: dict[str, str], stand_ins: Path, mode: Mode, merge: Literal["ask", "on_pass"]
) -> AsyncIterator[tuple[Runtime, list[str]]]:
    """A runtime set for a night, with one problem in its book seen three times, and the restarts it asks for."""
    (stand_ins / "mode").write_text(mode, encoding="utf-8")
    repo: Path = stand_ins.parent / "Dot"
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(repo, stand_ins / "claude", merge), login_env)
        runtime.shell_env.update(
            {
                "PATH": f"{stand_ins}{os.pathsep}{runtime.shell_env.get('PATH', os.environ['PATH'])}",
                "CLAUDE_CONFIG_DIR": str(stand_ins),
                "ANTHROPIC_API_KEY": "sk-ant-must-not-reach-the-session",
                "GH_TOKEN": "gho-must-not-reach-the-session",
                **_GIT_ENV,
            }
        )
        seen: list[Sighting] = [
            Sighting(kind="daemon", words="schedule_start_failed: no such effort", evidence=f"line {n}", at=10.0 + n)
            for n in range(3)
        ]
        book: Book = with_sightings(EMPTY_BOOK, seen, 20.0, 0.0)
        await runtime.store.kv_set(BOOK_KEY, book.model_dump_json())
        restarts: list[str] = []
        try:
            yield runtime, restarts
        finally:
            await close_runtime(runtime)


# A daemon that began after every commit of the test's repository: it runs the code its checkout holds (item 943).
_FRESH: float = time.time() + 365 * 24 * 3600.0


async def _play(
    runtime: Runtime, restarts: list[str], switcher: Path | None = None, running_since: float = _FRESH
) -> tuple[tuple[RepairSession, ...], list[EventRecord]]:
    """A night, its sessions and what the owner was told. With ``switcher`` (the stand-ins' folder), the night's wait
    after a refused session plays the owner's switcher: another account is signed in, whose plan has room."""

    async def restart() -> None:
        restarts.append("restart")

    async def sleep(_seconds: float) -> None:
        if switcher is not None:
            (switcher / ".claude.json").write_text(
                json.dumps({"oauthAccount": {"emailAddress": "second@example.invalid", "organizationName": "Org"}}),
                encoding="utf-8",
            )
            (switcher / "mode").write_text("pr", encoding="utf-8")

    told: list[EventRecord] = []
    async with runtime.bus.subscribe_kinds(frozenset({OWNER_TOLD_EVENT})) as feed:
        sessions = await repair_night(runtime, restart, lambda: 1_000_000.0, sleep, running_since)
        told.extend(_drained(feed))
    return sessions, told


def _drained(feed: Subscription) -> list[EventRecord]:
    events: list[EventRecord] = []
    while not feed.queue.empty():
        events.append(feed.queue.get_nowait())
    return events


def _calls(stand_ins: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (stand_ins / "calls.jsonl").read_text(encoding="utf-8").splitlines()]


async def test_a_fix_whose_checks_pass_is_merged_and_the_restart_asked(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    async with _night(login_env, stand_ins, "pr", "on_pass") as (runtime, restarts):
        sessions, told = await _play(runtime, restarts)
        (session,) = sessions
        assert (session.end, session.pr_url, session.account) == (
            "merged",
            "https://github.com/o/r/pull/7",
            "owner@example.invalid",
        )
        merged: list[str] = (stand_ins / "merges").read_text(encoding="utf-8").split()
        # Merged by Dot, only while the head is the commit whose files and checks it looked at: the one it pushed.
        assert merged[:5] == ["pr", "merge", "7", "--squash", "--match-head-commit"]
        assert _pushed(stand_ins.parent / "origin.git") == merged[5], "Dot pushed the branch the session committed on"
        # The merged branch is deleted on GitHub by Dot, after the merge (gh's own deletion fails on a branch a
        # worktree holds), and locally once the checkout is gone.
        (deleted,) = (stand_ins / "deleted").read_text(encoding="utf-8").split()
        assert deleted.startswith("repos/{owner}/{repo}/git/refs/heads/repair/prb_")
        assert restarts == ["restart"]
        (call,) = _calls(stand_ins)
        # The session ran in a checkout of its own, with no API key, with Dot's folders somewhere new, and with no way
        # to GitHub: no token, gh's sign-in in an empty folder, git's credential helpers reset and no prompt.
        assert "/.claude/worktrees/repair-prb_" in str(call["cwd"])
        assert "ANTHROPIC_API_KEY" not in call["keys"] and "dot-repair-" in str(call["dot_home"])
        assert "GH_TOKEN" not in call["keys"] and "dot-repair-" in str(call["gh_config"])
        assert call["helper"] == ["credential.helper", ""] and call["prompt"] == "0"
        assert "--permission-mode" in call["argv"] and "Bash(gh pr merge:*)" in call["argv"]
        assert "Your item number is 931" in str(call["argv"])
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        assert [problem.status for problem in book.problems] == ["fixed"]
        # What the page showed while it worked is gone once it has ended (item 939).
        assert await runtime.store.kv_get(night_module.LIVE_KEY) is None
        assert [event.data["title"] for event in told] == [FIXED_TITLE]
        assert read_sessions(await runtime.store.kv_get(SESSIONS_KEY)) == sessions
        # The checkout is gone; its branch stays, for its pull request.
        assert not list((stand_ins.parent / "Dot" / ".claude" / "worktrees").iterdir())


async def test_a_fix_that_changes_what_needs_the_owner_waits_for_them_and_is_never_merged(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    (stand_ins / "files").write_text("src/dotagent/policy/shell.py\n", encoding="utf-8")
    async with _night(login_env, stand_ins, "pr", "on_pass") as (runtime, restarts):
        sessions, told = await _play(runtime, restarts)
        (session,) = sessions
        assert session.end == "needs_owner" and "src/dotagent/policy/shell.py" in session.note
        assert not (stand_ins / "merges").exists() and restarts == []
        assert [event.data["title"] for event in told] == [NEEDS_YOU_TITLE]
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        assert [problem.status for problem in book.problems] == ["waiting"]


async def test_no_defect_found_lets_the_problem_go_and_tells_nothing(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    async with _night(login_env, stand_ins, "no_fix", "on_pass") as (runtime, restarts):
        sessions, told = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["declined"] and told == []
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        assert [(problem.status, problem.note) for problem in book.problems] == [
            ("declined", "the site was down, no defect of Dot's")
        ]


async def test_the_plans_window_refusing_a_session_ends_the_night(login_env: dict[str, str], stand_ins: Path) -> None:
    """The report had room, and the plan refused the session as it worked (the window filled meanwhile)."""
    (stand_ins / "usage").write_text("50", encoding="utf-8")
    async with _night(login_env, stand_ins, "limited", "on_pass") as (runtime, restarts):
        sessions, _told = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["limited"]
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        # A refused session was no try: the problem is as it was.
        assert [(problem.status, problem.tries) for problem in book.problems] == [("open", 0)]
        # The next night reads the window full and asks Claude Code for no session before it resets.
        (stand_ins / "usage").write_text("100", encoding="utf-8")
        again, _told = await _play(runtime, restarts)
        assert again == () and len(_calls(stand_ins)) == 1


async def test_a_switcher_that_signs_another_account_in_lets_the_night_go_on(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """The owner's switcher signs the next account in when one's limit is reached: the night goes on with it, at once,
    rather than waiting hours for the first one's window to reset."""
    (stand_ins / "usage").write_text("50", encoding="utf-8")
    async with _night(login_env, stand_ins, "limited", "on_pass") as (runtime, restarts):
        sessions, _told = await _play(runtime, restarts, stand_ins)
        assert [(session.end, session.account) for session in sessions] == [
            ("limited", "owner@example.invalid"),
            ("merged", "second@example.invalid"),
        ]
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        assert [problem.status for problem in book.problems] == ["fixed"]


async def test_a_plan_used_past_the_room_left_for_the_owner_starts_no_session(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Claude Code's own report of the plan's windows (``/usage``, no model call) is read before each session: one used
    past ``leave_room`` starts none; a week of a model the sessions do not use is no reason to wait."""
    (stand_ins / "usage").write_text("90", encoding="utf-8")
    async with _night(login_env, stand_ins, "pr", "on_pass") as (runtime, restarts):
        sessions, _told = await _play(runtime, restarts)
        assert sessions == () and not (stand_ins / "calls.jsonl").exists()


async def test_the_review_hooks_findings_go_back_to_the_same_session_and_its_answer_is_pushed(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Dot pushes the session's branch through the repository's pre-push hook: the review it wrote when it stopped the
    push goes back to the same session (``--resume``), whose new commit is pushed and merged."""
    _review_hook(stand_ins.parent / "Dot" / ".git" / "hooks" / "pre-push", stand_ins / "review.md")
    # The session leaves a mark of a passed review for its own commit: Dot removes it, so the hook reviews it.
    (stand_ins / "forge").write_text("yes", encoding="utf-8")
    async with _night(login_env, stand_ins, "pr", "on_pass") as (runtime, restarts):
        sessions, _told = await _play(runtime, restarts)
        assert [(session.end, session.rounds) for session in sessions] == [("merged", 2)]
        first, second = _calls(stand_ins)
        assert "--resume" not in first["argv"] and second["argv"][2:4] == ["--resume", "s-7"]
        assert "the fix has no test" in second["argv"][1] and "<<REVIEW " in second["argv"][1]
        # The hook ran in the owner's checkout, whose files Dot's print guards, never in the session's own.
        where: str = (stand_ins / "review.md.where").read_text(encoding="utf-8").strip()
        assert where == str((stand_ins.parent / "Dot").resolve())


async def test_main_moved_during_the_session_by_no_merge_of_dots_stops_the_night_and_is_told(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """The review of item 921: the session's way to GitHub is cut by its environment, which a command of its own can
    set back. Dot reads where main stands on GitHub before and after each session, and before it merges: main moved by
    no merge of Dot's (here the session pushed to it itself) stops the night, nothing is merged, and the owner is
    told."""
    async with _night(login_env, stand_ins, "rogue", "on_pass") as (runtime, restarts):
        sessions, told = await _play(runtime, restarts)
        # It opened no pull request, so there is none to look at again (item 933).
        assert [session.end for session in sessions] == ["main_moved"]
        assert not (stand_ins / "merges").exists() and restarts == []
        assert not (stand_ins / "looks.jsonl").exists()
        assert [event.data["title"] for event in told] == [NEEDS_YOU_TITLE]
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        # The problem is tried again: the session was stopped, not answered.
        assert [problem.status for problem in book.problems] == ["open"]


async def test_a_change_to_the_gates_own_sources_waits_for_the_owner_whatever_review_md_lists(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """The review of item 921: REVIEW.md, the repair night's code and the hooks need the owner's look by Dot's code,
    so that no change merged on its own can loosen the gate for the nights after it."""
    (stand_ins / "files").write_text("src/dotagent/repair/night.py\n", encoding="utf-8")
    async with _night(login_env, stand_ins, "pr", "on_pass") as (runtime, restarts):
        sessions, _told = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["needs_owner"]
        assert "src/dotagent/repair/night.py" in sessions[0].note and not (stand_ins / "merges").exists()


async def test_a_round_the_nights_time_cannot_hold_is_not_started_and_the_branch_waits(
    login_env: dict[str, str], stand_ins: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fourth review of item 921: the whole night is bounded, so that a restart asked meanwhile never gives up on
    it. A round that what is left of the night cannot hold is not started; the problem is tried again later."""
    monkeypatch.setattr(night_module, "ROUND_LEAST_S", night_module.NIGHT_LIMIT_S + 1)
    async with _night(login_env, stand_ins, "pr", "on_pass") as (runtime, restarts):
        sessions, _told = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["out_of_time"] and not (stand_ins / "calls.jsonl").exists()
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        assert [problem.status for problem in book.problems] == ["open"]


async def test_moving_a_protected_file_away_is_a_change_to_it(login_env: dict[str, str], stand_ins: Path) -> None:
    """The fifth review of item 921: a file moved out of a protected path is listed by its old path too, so that
    moving REVIEW.md (or the hook) away waits for the owner like changing it."""
    async with _night(login_env, stand_ins, "rename", "on_pass") as (runtime, restarts):
        sessions, _told = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["needs_owner"]
        assert "REVIEW.md" in sessions[0].note and not (stand_ins / "merges").exists()


async def test_a_session_that_writes_in_the_owners_checkout_stops_the_night_before_any_merge(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """The sixth review of item 921: a session runs with the owner's rights and could write in their checkout outside
    its own, which Dot then pulls and restarts with. Dot compares a print of the owner's checkout (its files, commit,
    git config and hooks) before it merges: a change stops the night, nothing is merged, pulled or restarted."""
    async with _night(login_env, stand_ins, "tamper", "on_pass") as (runtime, restarts):
        sessions, told = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["main_moved", "needs_owner"]
        assert "your checkout of Dot" in sessions[0].note
        # Its second look (item 933) reads the session's transcript: a write outside its checkout holds it, with no
        # model asked.
        assert sessions[1].note.startswith("Second look: its session wrote outside its checkout: ")
        assert sessions[1].note.endswith("loosened.py") and not (stand_ins / "looks.jsonl").exists()
        assert not (stand_ins / "merges").exists() and restarts == []
        assert [event.data["title"] for event in told] == [NEEDS_YOU_TITLE, NEEDS_YOU_TITLE]


async def test_a_session_whose_push_then_fails_is_still_checked_for_what_it_changed_outside(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """The ninth review of item 921: main and the owner's checkout are read again however the rounds ended, a push
    that failed too (here a hook that refuses it without a review), so that the next session never takes its print
    from a checkout the last one changed."""
    _refusing_hook(stand_ins.parent / "Dot" / ".git" / "hooks" / "pre-push")
    async with _night(login_env, stand_ins, "tamper", "on_pass") as (runtime, restarts):
        sessions, told = await _play(runtime, restarts)
        assert sessions[0].end == "main_moved" and restarts == []
        assert not (stand_ins / "merges").exists()
        assert told[0].data["title"] == NEEDS_YOU_TITLE


async def test_a_checkout_another_agent_changed_during_the_session_gets_a_second_look_that_merges(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Item 933: the owner's checkout moved while the session ran, by no write of the session's (another agent left a
    folder in it). The night stops the session as before, then looks again: the transcript shows the session wrote in
    its own checkout only, the branch is brought up to main, its checks pass, and a session that may only read answers
    MERGE. The pull request is merged at the head it looked at, and the restart is asked for."""
    (stand_ins / "merge_state").write_text("BEHIND", encoding="utf-8")
    async with _night(login_env, stand_ins, "busy", "on_pass") as (runtime, restarts):
        sessions, told = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["main_moved", "merged"], [s.note for s in sessions]
        assert sessions[1].note == "Second look: the change is its fix alone"
        assert sessions[1].session_id == "look-1" and sessions[1].pr_url == "https://github.com/o/r/pull/7"
        (looked,) = [json.loads(line) for line in (stand_ins / "looks.jsonl").read_text(encoding="utf-8").splitlines()]
        # It may only read, with no MCP server of the owner's, in a checkout of its own of the head.
        assert looked["argv"][looked["argv"].index("--tools") + 1 : looked["argv"].index("--tools") + 4] == [
            "Read",
            "Grep",
            "Glob",
        ]
        assert "--strict-mcp-config" in looked["argv"] and "/.claude/worktrees/look-" in str(looked["cwd"])
        assert "<<COMMANDS " in str(looked["argv"]) and "your checkout of Dot" in str(looked["argv"])
        assert (stand_ins / "updates").read_text(encoding="utf-8").split() == ["pr", "update-branch", "7"]
        merged: list[str] = (stand_ins / "merges").read_text(encoding="utf-8").split()
        assert merged[:5] == ["pr", "merge", "7", "--squash", "--match-head-commit"]
        assert merged[5] == _pushed(stand_ins.parent / "origin.git")
        assert restarts == ["restart"]
        assert [event.data["title"] for event in told] == [NEEDS_YOU_TITLE, FIXED_TITLE]
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        assert [problem.status for problem in book.problems] == ["fixed"]


async def test_a_second_look_that_holds_leaves_the_pull_request_to_the_owner(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Item 933: the look answers HOLD; nothing is merged, and the problem waits for the owner with its reason."""
    (stand_ins / "look").write_text("HOLD: a command wrote in the owner's home", encoding="utf-8")
    async with _night(login_env, stand_ins, "busy", "on_pass") as (runtime, restarts):
        sessions, _ = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["main_moved", "needs_owner"]
        assert sessions[1].note == "Second look: a command wrote in the owner's home"
        assert not (stand_ins / "merges").exists() and restarts == []
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        assert [(problem.status, problem.note) for problem in book.problems] == [
            ("waiting", "Second look: a command wrote in the owner's home")
        ]


async def test_a_pull_request_left_by_an_earlier_night_is_looked_at_first(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Item 933: a pull request a session of an earlier night left because something moved is looked at when the next
    night begins, before any new session."""
    async with _night(login_env, stand_ins, "busy", "on_pass") as (runtime, restarts):
        (stand_ins / "look").write_text("NO VERDICT", encoding="utf-8")
        first, _ = await _play(runtime, restarts)
        assert [session.end for session in first] == ["main_moved", "needs_owner"]
        # The earlier night as it was before its look: the main_moved session is the newest of its problem.
        await runtime.store.kv_set(SESSIONS_KEY, night_module.SessionLog(sessions=first[:1]).model_dump_json())
        (stand_ins / "look").unlink()
        second, _ = await _play(runtime, restarts)
        assert [session.end for session in second] == ["merged"] and restarts == ["restart"]
        assert second[0].note == "Second look: the change is its fix alone"


async def test_a_second_look_reads_mains_commits_from_github_not_from_the_nights_note(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """The review of item 933: the session wrote in the owner's checkout and pushed to main by a command its transcript
    does not show; the night's note names the checkout only. The look reads main's commits since the session began
    from GitHub, finds one of no pull request and holds it, with no model asked."""
    (stand_ins / "push_main").write_text("", encoding="utf-8")
    async with _night(login_env, stand_ins, "busy", "on_pass") as (runtime, restarts):
        sessions, _ = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["main_moved", "needs_owner"]
        assert "your checkout of Dot" in sessions[0].note and sessions[0].main_before is not None
        assert sessions[1].note.startswith("Second look: main holds commits of no pull request: ")
        assert not (stand_ins / "looks.jsonl").exists() and not (stand_ins / "merges").exists()
        assert restarts == []


async def test_with_merge_ask_a_pull_request_stopped_because_something_moved_gets_no_second_look(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """The third pre-push review of item 933: with ``merge = "ask"`` every pull request is the owner's, so none is
    looked at again or merged."""
    async with _night(login_env, stand_ins, "busy", "ask") as (runtime, restarts):
        sessions, _ = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["main_moved"]
        assert not (stand_ins / "looks.jsonl").exists() and not (stand_ins / "merges").exists()
        assert restarts == []


async def test_a_look_the_nights_time_cannot_hold_is_left_to_the_next_night(
    login_env: dict[str, str], stand_ins: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sixth pre-push review of item 933: a look whose waits left too little of the night is not made and keeps
    nothing, so the next night looks again, as a session cut short is tried again."""
    async with _night(login_env, stand_ins, "busy", "on_pass") as (runtime, restarts):
        with monkeypatch.context() as patch:
            patch.setattr(second_module, "LOOK_LEAST_S", night_module.NIGHT_LIMIT_S + 1)
            first, _ = await _play(runtime, restarts)
        assert [session.end for session in first] == ["main_moved"]
        assert not (stand_ins / "looks.jsonl").exists() and not (stand_ins / "merges").exists()
        second, _ = await _play(runtime, restarts)
        assert [session.end for session in second] == ["merged"] and restarts == ["restart"]


async def test_a_pull_request_found_merged_already_counts_fixed_and_the_night_goes_on(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """The sixth pre-push review of item 933: no restart is asked, and the night takes its next problem."""
    async with _night(login_env, stand_ins, "busy", "on_pass") as (runtime, restarts):
        (stand_ins / "look").write_text("NO VERDICT", encoding="utf-8")
        first, _ = await _play(runtime, restarts)
        # A session kept before item 933, with no record of where main stood (item 935: #752's).
        unknown = first[0].model_copy(update={"main_before": None})
        await runtime.store.kv_set(SESSIONS_KEY, night_module.SessionLog(sessions=(unknown,)).model_dump_json())
        # The owner merged it meanwhile; another problem waits in the book.
        (stand_ins / "merges").write_text("pr merge 7 --squash\n", encoding="utf-8")
        book: Book = read_book(await runtime.store.kv_get(BOOK_KEY))
        other: list[Sighting] = [
            Sighting(kind="daemon", words="memory_failed: no such note", evidence=f"m {n}", at=30.0 + n)
            for n in range(3)
        ]
        await runtime.store.kv_set(BOOK_KEY, with_sightings(book, other, 40.0, 0.0).model_dump_json())
        second, _ = await _play(runtime, restarts)
        assert second[0].end == "merged_unpulled" and second[0].note == "Second look: it was merged already"
        # The look asked for no restart: only a merge of the next session's does.
        assert len(second) > 1 and second[1].problem_id != second[0].problem_id
        assert restarts == ["restart" for session in second if session.end == "merged"]
        statuses: dict[str, str] = {p.id: p.status for p in read_book(await runtime.store.kv_get(BOOK_KEY)).problems}
        assert statuses[second[0].problem_id] == "fixed"


async def test_another_branchs_section_written_in_the_owners_git_config_does_not_stop_the_night(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Item 935: on the owner's Mac a `git push -u` of another session's branch stopped a night's session as a change of
    their checkout; such a section is left out of the print, and the fix is merged."""
    async with _night(login_env, stand_ins, "tracked", "on_pass") as (runtime, restarts):
        sessions, _ = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["merged"] and restarts == ["restart"]


async def test_a_branch_the_session_pushed_itself_is_never_opened_nor_merged(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Item 943 (the review of 2026-11-02): a session that reached GitHub by a command of its own and pushed its branch
    leaves Dot's push nothing to send, so the review hook never reviews it and git exits 0. A push is a passed review
    only with the hook's mark of it: here none, so no pull request is opened and the session is the owner's."""
    async with _night(login_env, stand_ins, "selfpush", "on_pass") as (runtime, restarts):
        sessions, _ = await _play(runtime, restarts)
        assert sessions[0].end == "needs_owner", [session.note for session in sessions]
        # Read first where the branch stands on GitHub: not where Dot left it (nowhere), so Dot pushes nothing.
        assert "not where Dot left it" in sessions[0].note and sessions[0].pr_url == ""
        assert not (stand_ins / "merges").exists() and restarts == []


async def test_a_night_in_a_process_older_than_its_checkouts_code_asks_for_the_restart_and_repairs_nothing(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Item 943 (the review of 2026-11-02): a merge's restart that gave up (a run waited for the owner for two hours)
    left the daemon on the code before it. The next night works on no problem in it: it asks for the restart again and
    tells the owner."""
    async with _night(login_env, stand_ins, "pr", "on_pass") as (runtime, restarts):
        sessions, told = await _play(runtime, restarts, None, 0.0)
        assert sessions == () and restarts == ["restart"]
        assert [event.data["title"] for event in told] == [night_module.RESTART_TITLE]
        assert not (stand_ins / "calls.jsonl").exists()


async def test_the_owners_pull_along_main_during_a_session_does_not_stop_it(
    login_env: dict[str, str], stand_ins: Path, tmp_path: Path
) -> None:
    """Item 940: on the owner's Mac a deploy during a night (a pull of main into their checkout) stopped its session as
    a change of the checkout. A commit of main on GitHub is no change of Dot's to fear; the fix is merged."""
    origin: Path = stand_ins.parent / "origin.git"
    other: Path = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    (other / "merged-before.txt").write_text("a pull request of the day\n", encoding="utf-8")
    _git(other, "add", "merged-before.txt")
    _git(other, "commit", "-q", "-m", "Item 0: merged earlier")
    _git(other, "push", "-q", "origin", "HEAD:main")
    async with _night(login_env, stand_ins, "pulled", "on_pass") as (runtime, restarts):
        sessions, _ = await _play(runtime, restarts)
        assert [session.end for session in sessions] == ["merged"], [session.note for session in sessions]


async def test_a_session_that_takes_the_owners_checkout_back_along_main_is_stopped(
    login_env: dict[str, str], stand_ins: Path, tmp_path: Path
) -> None:
    """The pre-push review of item 940: only forward along main is a pull of the owner's; a checkout taken back to an
    older commit of main (a fix undone in what Dot restarts with) stops the night, and nothing is merged."""
    origin: Path = stand_ins.parent / "origin.git"
    other: Path = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    (other / "guard-fix.txt").write_text("a fix of main's\n", encoding="utf-8")
    _git(other, "add", "guard-fix.txt")
    _git(other, "commit", "-q", "-m", "Item 0: a fix")
    _git(other, "push", "-q", "origin", "HEAD:main")
    _git(stand_ins.parent / "Dot", "pull", "-q", "--ff-only", "origin", "main")
    async with _night(login_env, stand_ins, "rewound", "on_pass") as (runtime, restarts):
        sessions, _ = await _play(runtime, restarts)
        assert sessions[0].end == "main_moved", [session.note for session in sessions]
        assert "went to a commit that is not main's ahead of where it stood" in sessions[0].note
        assert not (stand_ins / "merges").exists() and restarts == []


async def test_a_branch_the_session_pushed_part_of_itself_is_never_opened_nor_merged(
    login_env: dict[str, str], stand_ins: Path
) -> None:
    """Item 943, the pre-push review: a session pushed a commit of its own to its branch, then made one more. The hook
    would review only what Dot's push brings on top and mark the head passed; the branch on GitHub is not where Dot
    left it (nowhere), so nothing is pushed, opened or merged."""
    async with _night(login_env, stand_ins, "selfahead", "on_pass") as (runtime, restarts):
        sessions, _ = await _play(runtime, restarts)
        assert sessions[0].end == "needs_owner", [session.note for session in sessions]
        assert "not where Dot left it" in sessions[0].note and sessions[0].pr_url == ""
        assert not (stand_ins / "merges").exists() and restarts == []
