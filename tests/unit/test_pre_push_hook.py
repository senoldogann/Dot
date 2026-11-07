"""The pre-push review hook (item 540): a push that only brings main in is not reviewed again, a push that changes the
branch's own change is, and the hook says so after three earlier reviews of the branch that BLOCKed. A stand-in for the
`claude` command answers PASS and records that it was asked: no model is called."""

import os
import subprocess
from pathlib import Path

import pytest

_HOOK: Path = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "pre-push-review.sh"
_ZERO: str = "0" * 40


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _commit(repo: Path, name: str, text: str, message: str) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone of a bare remote, on a branch `feature` that has one change of its own and has been pushed."""
    remote: Path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work: Path = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "checkout", "-q", "-b", "main")
    (work / "shared.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (work / "other.txt").write_text("other\n", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "main")
    _git(work, "push", "-q", "origin", "main")
    _git(work, "checkout", "-q", "-b", "feature")
    _commit(work, "feature.txt", "its own change\n", "the change")
    _git(work, "push", "-q", "origin", "feature")
    return work


def _run_hook(repo: Path, tmp_path: Path, local: str, remote: str) -> tuple[subprocess.CompletedProcess[str], int]:
    """The hook as git runs it before a push of ``local`` over ``remote``, and how many times the stand-in was asked."""
    return _run_hook_on(repo, tmp_path, f"refs/heads/feature {local} refs/heads/feature {remote}\n")


def _run_hook_on(repo: Path, tmp_path: Path, refs: str) -> tuple[subprocess.CompletedProcess[str], int]:
    """The hook as git runs it with ``refs`` on its stdin; the stand-in reads its stdin whole, as `claude -p` does."""
    bin_dir: Path = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    asked: Path = tmp_path / "asked"
    stand_in: Path = bin_dir / "claude"
    stand_in.write_text(
        f'#!/bin/sh\ncat > /dev/null\necho asked >> "{asked}"\necho "No issues found."\necho "VERDICT: PASS"\n',
        encoding="utf-8",
    )
    stand_in.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    done = subprocess.run(
        ["bash", str(_HOOK)],
        cwd=repo,
        input=refs,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return done, len(asked.read_text().splitlines()) if asked.exists() else 0


def test_a_first_push_is_reviewed(repo: Path, tmp_path: Path) -> None:
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), _ZERO)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr


def _passed(repo: Path, sha: str) -> None:
    """Records that ``sha`` passed the review here, as the hook does after a PASS."""
    review_dir: Path = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "claude-review"
    review_dir.mkdir(exist_ok=True)
    (review_dir / f"{sha}.pass").touch()


def test_a_push_that_only_brings_main_in_is_not_reviewed_again(repo: Path, tmp_path: Path) -> None:
    pushed: str = _git(repo, "rev-parse", "HEAD")
    _passed(repo, pushed)
    # Main moves in a file the change does not touch, and the branch takes it in cleanly.
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "other.txt", "other, moved on\n", "main moves")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "-m", "merge main", "main")
    _git(repo, "fetch", "-q", "origin")
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), pushed)
    assert (done.returncode, asked) == (0, 0), done.stdout + done.stderr
    assert "only brings main in" in done.stdout


def test_a_push_that_changes_the_change_is_reviewed(repo: Path, tmp_path: Path) -> None:
    pushed: str = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "feature.txt", "its own change, changed\n", "a fix")
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr


def test_a_merge_of_main_that_resolved_a_conflict_by_editing_the_change_is_reviewed(repo: Path, tmp_path: Path) -> None:
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "shared.txt", "main one\ntwo\nthree\n", "main edits the line")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feature")
    _commit(repo, "shared.txt", "feature one\ntwo\nthree\n", "the change edits the same line")
    _git(repo, "push", "-q", "origin", "feature")
    pushed: str = _git(repo, "rev-parse", "HEAD")
    _passed(repo, pushed)
    merged = subprocess.run(["git", "merge", "-q", "main"], cwd=repo, capture_output=True, text=True, check=False)
    assert merged.returncode != 0
    (repo / "shared.txt").write_text("both one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-q", "-m", "merge main, both")
    _git(repo, "fetch", "-q", "origin")
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr


def test_a_branch_rebased_onto_main_is_reviewed_from_where_it_leaves_main(repo: Path, tmp_path: Path) -> None:
    """Item 955: a repair session rebases its branch onto main and renumbers its item, which changes its patch; its old
    head is no ancestor of the new one, so the review reads the branch from where it leaves main, not a diff from the
    old head that would hold every pull request main took meanwhile."""
    pushed: str = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "other.txt", "another session's merged change\n", "main takes another pull request")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "rebase", "-q", "main")
    _commit(repo, "feature.txt", "its own change, renumbered\n", "renumber")
    _git(repo, "fetch", "-q", "origin")
    head: str = _git(repo, "rev-parse", "HEAD")
    done, asked = _run_hook(repo, tmp_path, head, pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    git_dir: Path = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    reviewed: str = (git_dir / "claude-review" / f"{head}.diff").read_text(encoding="utf-8")
    assert "its own change, renumbered" in reviewed and "another session's merged change" not in reviewed


def test_after_three_blocked_reviews_the_hook_says_to_reconsider_the_approach(repo: Path, tmp_path: Path) -> None:
    review_dir: Path = repo / ".git" / "claude-review"
    review_dir.mkdir(exist_ok=True)
    pushed: str = _git(repo, "rev-parse", "HEAD")
    for number in range(3):
        sha: str = _commit(repo, "feature.txt", f"round {number}\n", f"round {number}")
        (review_dir / f"{sha}.md").write_text("1 Important\n\nVERDICT: BLOCK\n", encoding="utf-8")
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    assert "3 earlier reviews of this branch BLOCKed" in done.stderr and "reconsider" in done.stderr


def test_a_push_that_only_changes_whitespace_of_the_change_is_reviewed(repo: Path, tmp_path: Path) -> None:
    """Whitespace is meaning in Python and YAML: a dedent that moves a line out of an `if` is a change."""
    pushed: str = _git(repo, "rev-parse", "HEAD")
    _passed(repo, pushed)
    _commit(repo, "feature.txt", "  its own change\n", "re-indent")
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr


def _main_moves_and_the_branch_takes_it(repo: Path) -> None:
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "other.txt", "another session's merged change\n", "main takes another pull request")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "merge", "-q", "-m", "merge main", "main")
    _git(repo, "fetch", "-q", "origin")


def test_a_merge_of_main_whose_remote_tip_was_not_reviewed_here_is_reviewed(repo: Path, tmp_path: Path) -> None:
    """Item 1020: a remote tip pushed with --no-verify, or from another checkout, vouches for nothing."""
    pushed: str = _git(repo, "rev-parse", "HEAD")
    _main_moves_and_the_branch_takes_it(repo)
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr


def test_a_push_of_main_taken_in_and_a_commit_on_top_reads_only_the_branchs_change(repo: Path, tmp_path: Path) -> None:
    """Item 1020: the remote tip is an ancestor, but main moved under the branch since; a diff from that tip would hold
    the pull requests main took, so the review reads from where the branch leaves main."""
    pushed: str = _git(repo, "rev-parse", "HEAD")
    _main_moves_and_the_branch_takes_it(repo)
    head: str = _commit(repo, "feature.txt", "its own change, fixed after the merge\n", "a fix")
    done, asked = _run_hook(repo, tmp_path, head, pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    git_dir: Path = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    reviewed: str = (git_dir / "claude-review" / f"{head}.diff").read_text(encoding="utf-8")
    assert "fixed after the merge" in reviewed and "another session's merged change" not in reviewed


def test_a_push_of_two_branches_reviews_both(repo: Path, tmp_path: Path) -> None:
    """Item 1020: `claude -p` reads a piped stdin; the hook's stdin is git's list of refs, so a review that inherited it
    swallowed the second ref, which was then pushed unreviewed."""
    first: str = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "second", "main")
    second: str = _commit(repo, "second.txt", "the second branch's change\n", "the second change")
    refs: str = (
        f"refs/heads/feature {first} refs/heads/feature {_ZERO}\nrefs/heads/second {second} refs/heads/second {_ZERO}\n"
    )
    done, asked = _run_hook_on(repo, tmp_path, refs)
    assert (done.returncode, asked) == (0, 2), done.stdout + done.stderr
