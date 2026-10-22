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
    bin_dir: Path = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    asked: Path = tmp_path / "asked"
    stand_in: Path = bin_dir / "claude"
    stand_in.write_text(
        f'#!/bin/sh\necho asked >> "{asked}"\necho "No issues found."\necho "VERDICT: PASS"\n', encoding="utf-8"
    )
    stand_in.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    done = subprocess.run(
        ["bash", str(_HOOK)],
        cwd=repo,
        input=f"refs/heads/feature {local} refs/heads/feature {remote}\n",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return done, len(asked.read_text().splitlines()) if asked.exists() else 0


def test_a_first_push_is_reviewed(repo: Path, tmp_path: Path) -> None:
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), _ZERO)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr


def test_a_push_that_only_brings_main_in_is_not_reviewed_again(repo: Path, tmp_path: Path) -> None:
    pushed: str = _git(repo, "rev-parse", "HEAD")
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
    merged = subprocess.run(["git", "merge", "-q", "main"], cwd=repo, capture_output=True, text=True, check=False)
    assert merged.returncode != 0
    (repo / "shared.txt").write_text("both one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-q", "-m", "merge main, both")
    _git(repo, "fetch", "-q", "origin")
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr


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
    _commit(repo, "feature.txt", "  its own change\n", "re-indent")
    done, asked = _run_hook(repo, tmp_path, _git(repo, "rev-parse", "HEAD"), pushed)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
