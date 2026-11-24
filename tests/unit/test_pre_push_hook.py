"""The pre-push review hook (item 540): a push that only brings main in is not reviewed again, a push that changes the
branch's own change is, and the hook says so after three earlier reviews of the branch that BLOCKed. The models it tries
are `dotx review plan`'s (item 1663): a model that gives no verdict hands the change to the next, and a review that
none gives a verdict, or that runs past its time, stops the push. A stand-in for the `dotx` command answers the plan and
the review and records the model it was asked on: no model is called."""

import os
import subprocess
import time
from pathlib import Path

import pytest

_HOOK: Path = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "pre-push-review.sh"
_ZERO: str = "0" * 40
_PLAN: str = "size full\nlimit 900\ntry first:model medium\ntry second:model high\n"


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


def _run_hook(
    repo: Path, tmp_path: Path, local: str, remote: str, answer: str = "No issues found.\nVERDICT: PASS"
) -> tuple[subprocess.CompletedProcess[str], int]:
    """The hook as git runs it before a push of ``local`` over ``remote``, and how many times the stand-in was asked."""
    return _run_hook_on(repo, tmp_path, f"refs/heads/feature {local} refs/heads/feature {remote}\n", answer)


def _run_hook_on(
    repo: Path,
    tmp_path: Path,
    refs: str,
    answer: str = "No issues found.\nVERDICT: PASS",
    plan: str = _PLAN,
    answers: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], int]:
    """The hook as git runs it with ``refs`` on its stdin, and how many times the stand-in was asked to review. The
    stand-in prints ``plan`` for `dotx review plan`; asked to review on a model, it prints that model's entry of
    ``answers`` (``SLEEP`` sleeps until it is stopped), else ``answer``, and records the model."""
    bin_dir: Path = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    asked: Path = tmp_path / "asked"
    stand_in: Path = bin_dir / "dotx"
    answers_dir: Path = tmp_path / "answers"
    answers_dir.mkdir(exist_ok=True)
    (answers_dir / "default").write_text(answer + "\n", encoding="utf-8")
    for model, words in (answers or {}).items():
        (answers_dir / model).write_text(words + "\n", encoding="utf-8")
    (tmp_path / "plan").write_text(plan, encoding="utf-8")
    stand_in.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = review ]; then cat "{tmp_path / "plan"}"; exit 0; fi\n'
        'model=""; previous=""\n'
        'for arg in "$@"; do [ "$previous" = --model ] && model=$arg; previous=$arg; done\n'
        "cat > /dev/null\n"
        f'echo "$model" >> "{asked}"\n'
        f'answer="{answers_dir}/$model"; [ -f "$answer" ] || answer="{answers_dir}/default"\n'
        'if [ "$(cat "$answer")" = SLEEP ]; then exec sleep 60; fi\n'
        'cat "$answer"\n'
        'echo "run run_test, thread thr_test"\n',
        encoding="utf-8",
    )
    stand_in.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("DOTX", None)
    done = subprocess.run(
        ["bash", str(_HOOK)],
        cwd=repo,
        input=refs,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    return done, len(asked.read_text().splitlines()) if asked.exists() else 0


def _asked_models(tmp_path: Path) -> list[str]:
    return (tmp_path / "asked").read_text().splitlines()


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
    """Item 1020: a review read a piped stdin; the hook's stdin is git's list of refs, so a review that inherited it
    swallowed the second ref, which was then pushed unreviewed."""
    first: str = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "second", "main")
    second: str = _commit(repo, "second.txt", "the second branch's change\n", "the second change")
    refs: str = (
        f"refs/heads/feature {first} refs/heads/feature {_ZERO}\nrefs/heads/second {second} refs/heads/second {_ZERO}\n"
    )
    done, asked = _run_hook_on(repo, tmp_path, refs)
    assert (done.returncode, asked) == (0, 2), done.stdout + done.stderr


def test_a_block_stops_the_push(repo: Path, tmp_path: Path) -> None:
    """A review that found an Important thing stops the push: the verdict line decides, not the run's exit status,
    which says only that the run finished (item 1211: the Claude review refused this diff with [cyber] and stopped a
    push it never read)."""
    done, asked = _run_hook(
        repo,
        tmp_path,
        _git(repo, "rev-parse", "HEAD"),
        _ZERO,
        answer="One important finding in the hook.\nVERDICT: BLOCK",
    )
    assert (done.returncode, asked) == (1, 1), done.stdout + done.stderr
    assert "push stopped" in done.stderr


def test_a_model_that_gives_no_verdict_hands_the_change_to_the_next(repo: Path, tmp_path: Path) -> None:
    """Item 1211's refusal ("the safeguards flagged this message"): the next model of the plan reviews the change."""
    refs: str = f"refs/heads/feature {_git(repo, 'rev-parse', 'HEAD')} refs/heads/feature {_ZERO}\n"
    done, asked = _run_hook_on(
        repo, tmp_path, refs, answers={"first:model": "API Error: the safeguards flagged this message"}
    )
    assert (done.returncode, asked) == (0, 2), done.stdout + done.stderr
    assert _asked_models(tmp_path) == ["first:model", "second:model"]
    assert "first:model gave no verdict" in done.stderr


def test_a_change_no_model_gives_a_verdict_on_stops_the_push_and_says_why(repo: Path, tmp_path: Path) -> None:
    refs: str = f"refs/heads/feature {_git(repo, 'rev-parse', 'HEAD')} refs/heads/feature {_ZERO}\n"
    done, asked = _run_hook_on(repo, tmp_path, refs, answer="API Error: refused")
    assert (done.returncode, asked) == (1, 2), done.stdout + done.stderr
    assert "could not be reviewed: no model gave a verdict (first:model, second:model)" in done.stderr
    assert "--no-verify" in done.stderr


def test_a_review_past_its_time_is_stopped_and_stops_the_push(repo: Path, tmp_path: Path) -> None:
    """The time is the whole review's: the model that ran it out is stopped, and no other is tried."""
    refs: str = f"refs/heads/feature {_git(repo, 'rev-parse', 'HEAD')} refs/heads/feature {_ZERO}\n"
    started: float = time.monotonic()
    done, asked = _run_hook_on(
        repo, tmp_path, refs, plan=_PLAN.replace("limit 900", "limit 2"), answers={"first:model": "SLEEP"}
    )
    assert time.monotonic() - started < 30
    assert (done.returncode, asked) == (1, 1), done.stdout + done.stderr
    assert "could not be reviewed: the review ran past" in done.stderr and "--no-verify" in done.stderr


def test_a_plan_that_cannot_be_read_stops_the_push(repo: Path, tmp_path: Path) -> None:
    refs: str = f"refs/heads/feature {_git(repo, 'rev-parse', 'HEAD')} refs/heads/feature {_ZERO}\n"
    bin_dir: Path = tmp_path / "bin"
    bin_dir.mkdir()
    stand_in: Path = bin_dir / "dotx"
    stand_in.write_text("#!/bin/sh\necho 'error: [code_review] max_minutes must be at least 1' >&2\nexit 2\n")
    stand_in.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "DOTX"}
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    done = subprocess.run(
        ["bash", str(_HOOK)], cwd=repo, input=refs, capture_output=True, text=True, env=env, check=False, timeout=60
    )
    assert done.returncode == 1
    assert "could not be reviewed" in done.stderr and "max_minutes must be at least 1" in done.stderr
