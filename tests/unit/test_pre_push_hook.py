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
        f'mkdir -p "{tmp_path / "goals"}"; cat > "{tmp_path / "goals"}/$(echo "$model" | tr :/ __)"\n'
        f'echo "$model" >> "{asked}"\n'
        f'answer="{answers_dir}/$model"; [ -f "$answer" ] || answer="{answers_dir}/default"\n'
        'if [ "$(cat "$answer")" = SLEEP ]; then exec sleep 60; fi\n'
        'cat "$answer"\n'
        'echo "run run_test, thread thr_test"\n',
        encoding="utf-8",
    )
    stand_in.chmod(0o755)
    # `uv run python scripts/prompt_surface.py --check` (item 1971) and `uv run pytest` of the first step's budget
    # (item 1997): records that it ran and its DOT_HOME; when `uv_exit` is there fails with that exit status and what
    # `uv_says` holds, and the budget's run alone fails so when `pytest_exit` is there.
    uv: Path = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path / "uv_called"}"\n'
        f'echo "$DOT_HOME" >> "{tmp_path / "uv_homes"}"\n'
        f'if [ "$2" = pytest ] && [ -f "{tmp_path / "pytest_exit"}" ]; then\n'
        f'  cat "{tmp_path / "uv_says"}"; exit "$(cat "{tmp_path / "pytest_exit"}")"\n'
        "fi\n"
        f'if [ -f "{tmp_path / "uv_exit"}" ]; then\n'
        f'  cat "{tmp_path / "uv_says"}" >&2; exit "$(cat "{tmp_path / "uv_exit"}")"\n'
        "fi\n"
        'echo "prompt surface: up to date"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("DOTX", None)
    env.pop("DOT_HOME", None)
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


def _goal_of(tmp_path: Path, model: str) -> str:
    """The goal the stand-in was handed for ``model`` (what the review is asked)."""
    return (tmp_path / "goals" / model.replace(":", "_").replace("/", "_")).read_text(encoding="utf-8")


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


def _review_dir(repo: Path) -> Path:
    folder: Path = Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "claude-review"
    folder.mkdir(exist_ok=True)
    return folder


def test_a_push_after_a_blocked_review_reads_only_what_came_after_it_and_names_that_review(
    repo: Path, tmp_path: Path
) -> None:
    """Item 1954: a first push that BLOCKs never reaches the remote, so each round was read again from where the branch
    leaves main (29 reviews in one chat). The next round reads from the commit whose review BLOCKed, and the reviewer is
    pointed at that review to check its findings are fixed."""
    remote_tip: str = _git(repo, "rev-parse", "HEAD")
    blocked: str = _commit(repo, "first_round.txt", "the first round\n", "round one")
    (_review_dir(repo) / f"{blocked}.md").write_text("1 Important\n\nVERDICT: BLOCK\n", encoding="utf-8")
    tip: str = _commit(repo, "second_round.txt", "the fix\n", "round two")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    diff: str = (_review_dir(repo) / f"{tip}.diff").read_text(encoding="utf-8")
    assert "second_round.txt" in diff and "first_round.txt" not in diff
    assert (_review_dir(repo) / f"{tip}.paths").read_text(encoding="utf-8").split() == ["second_round.txt"]
    goal: str = _goal_of(tmp_path, "first:model")
    assert f"{blocked}.md" in goal and "which BLOCKed" in goal
    assert "Read only the files the change touches" in goal


def test_a_commit_that_passed_here_but_never_reached_the_remote_is_not_read_again(repo: Path, tmp_path: Path) -> None:
    remote_tip: str = _git(repo, "rev-parse", "HEAD")
    passed: str = _commit(repo, "passed.txt", "passed\n", "passed here")
    _passed(repo, passed)
    tip: str = _commit(repo, "later.txt", "later\n", "later")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    diff: str = (_review_dir(repo) / f"{tip}.diff").read_text(encoding="utf-8")
    assert "later.txt" in diff and "passed.txt" not in diff
    assert "which BLOCKed" not in _goal_of(tmp_path, "first:model")


def test_a_review_that_gave_no_verdict_is_not_taken_as_read(repo: Path, tmp_path: Path) -> None:
    """A review that ended with no verdict (refused, stopped) read nothing for good: its commit is read again."""
    remote_tip: str = _git(repo, "rev-parse", "HEAD")
    unread: str = _commit(repo, "unread.txt", "unread\n", "no verdict")
    (_review_dir(repo) / f"{unread}.md").write_text("API Error: refused\n", encoding="utf-8")
    tip: str = _commit(repo, "later.txt", "later\n", "later")
    done, _ = _run_hook(repo, tmp_path, tip, remote_tip)
    assert done.returncode == 0, done.stdout + done.stderr
    diff: str = (_review_dir(repo) / f"{tip}.diff").read_text(encoding="utf-8")
    assert "later.txt" in diff and "unread.txt" in diff


def test_a_model_off_the_claude_plan_is_told_its_budget_and_one_on_it_is_not(repo: Path, tmp_path: Path) -> None:
    """Item 1954: DeepSeek stopped at the time limit in 21 of 61 reviews with no verdict, having read the repository."""
    refs: str = f"refs/heads/feature {_git(repo, 'rev-parse', 'HEAD')} refs/heads/feature {_ZERO}\n"
    plan: str = "size full\nlimit 900\nroute security\ntry opencode_go:deepseek medium\ntry claude_code:opus high\n"
    done, _ = _run_hook_on(repo, tmp_path, refs, plan=plan, answers={"opencode_go:deepseek": "API Error: refused"})
    assert done.returncode == 0, done.stdout + done.stderr
    deepseek: str = _goal_of(tmp_path, "opencode_go:deepseek")
    assert "at most 40 tool calls and 12 minutes" in deepseek and "VERDICT line" in deepseek
    assert "Your budget" not in _goal_of(tmp_path, "claude_code:opus")
    assert "route security" in done.stdout


def test_a_block_that_counts_no_important_finding_is_not_taken_as_read(repo: Path, tmp_path: Path) -> None:
    """The review of item 1954: the prompt asks for BLOCK when the review could not read the change (or ran out of its
    budget), which says nothing of the code: its commit is read again, and only a BLOCK that counts findings stands."""
    remote_tip: str = _git(repo, "rev-parse", "HEAD")
    unread: str = _commit(repo, "unread.txt", "unread\n", "could not review")
    (_review_dir(repo) / f"{unread}.md").write_text(
        "I could not read the change.\n\nVERDICT: BLOCK\n", encoding="utf-8"
    )
    tip: str = _commit(repo, "later.txt", "later\n", "later")
    done, _ = _run_hook(repo, tmp_path, tip, remote_tip)
    assert done.returncode == 0, done.stdout + done.stderr
    diff: str = (_review_dir(repo) / f"{tip}.diff").read_text(encoding="utf-8")
    assert "later.txt" in diff and "unread.txt" in diff


def _uv_fails(tmp_path: Path, status: int, says: str) -> None:
    """Makes the stand-in `uv` fail with ``status`` and ``says``."""
    (tmp_path / "uv_says").write_text(f"{says}\n", encoding="utf-8")
    (tmp_path / "uv_exit").write_text(f"{status}\n", encoding="utf-8")


def _with_surface_script(repo: Path) -> str:
    """Puts the script the hook looks for into the branch, and returns the tip before the branch's next commit."""
    (repo / "scripts").mkdir(exist_ok=True)
    _commit(repo, "scripts/prompt_surface.py", "# stand-in\n", "the script")
    return _git(repo, "rev-parse", "HEAD")


def test_a_change_under_src_dotagent_is_checked_against_the_prompt_surface_before_the_review(
    repo: Path, tmp_path: Path
) -> None:
    """Item 1971: the prompt surface's test runs on macOS only, so a pull request never saw a stale fixture and main
    went red twice in a day. A fresh fixture lets the review go on."""
    remote_tip: str = _with_surface_script(repo)
    (repo / "src" / "dotagent").mkdir(parents=True)
    tip: str = _commit(repo, "src/dotagent/tools.py", "a tool\n", "a tool's description")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    assert (tmp_path / "uv_called").read_text().strip() == "run python scripts/prompt_surface.py --check"


def test_a_stale_prompt_surface_stops_the_push_with_the_command_that_fixes_it_and_no_model_is_asked(
    repo: Path, tmp_path: Path
) -> None:
    remote_tip: str = _with_surface_script(repo)
    (repo / "src" / "dotagent").mkdir(parents=True)
    tip: str = _commit(repo, "src/dotagent/tools.py", "a tool\n", "a tool's description")
    _uv_fails(tmp_path, 3, "prompt surface: stale (tools.json)")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (1, 0), done.stdout + done.stderr
    assert "the prompt surface is stale" in done.stderr
    assert "uv run python scripts/prompt_surface.py" in done.stderr and "tests/fixtures/prompt_surface" in done.stderr


def test_a_change_outside_src_dotagent_does_not_run_the_prompt_surface_check(repo: Path, tmp_path: Path) -> None:
    remote_tip: str = _with_surface_script(repo)
    tip: str = _commit(repo, "docs.md", "words\n", "docs")
    _uv_fails(tmp_path, 3, "stale")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    assert not (tmp_path / "uv_called").exists()


def test_a_check_that_failed_to_run_is_not_reported_as_a_stale_surface(repo: Path, tmp_path: Path) -> None:
    remote_tip: str = _with_surface_script(repo)
    (repo / "src" / "dotagent").mkdir(parents=True)
    tip: str = _commit(repo, "src/dotagent/tools.py", "a tool\n", "a tool's description")
    _uv_fails(tmp_path, 1, "ModuleNotFoundError: no module named dotagent")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (1, 0), done.stdout + done.stderr
    assert "could not be checked (exit 1)" in done.stderr and "ModuleNotFoundError" in done.stderr
    assert "is stale" not in done.stderr


def test_uncommitted_changes_to_what_the_surface_is_made_of_stop_the_push_instead_of_a_check_of_another_tree(
    repo: Path, tmp_path: Path
) -> None:
    """The review of item 1971: a fixture written and not committed passed the check while the pushed commit kept the
    stale one."""
    remote_tip: str = _with_surface_script(repo)
    (repo / "src" / "dotagent").mkdir(parents=True)
    tip: str = _commit(repo, "src/dotagent/tools.py", "a tool\n", "a tool's description")
    (repo / "tests" / "fixtures" / "prompt_surface").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "prompt_surface" / "tools.json").write_text("{}\n", encoding="utf-8")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (1, 0), done.stdout + done.stderr
    assert "not committed" in done.stderr and "commit or stash" in done.stderr
    assert not (tmp_path / "uv_called").exists()


def test_a_branch_that_is_not_checked_out_is_not_checked_and_says_so(repo: Path, tmp_path: Path) -> None:
    """The check reads the working tree, so a push of another branch goes on to its review with a note."""
    remote_tip: str = _with_surface_script(repo)
    (repo / "src" / "dotagent").mkdir(parents=True)
    tip: str = _commit(repo, "src/dotagent/tools.py", "a tool\n", "a tool's description")
    _git(repo, "checkout", "-q", "main")
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "prompt_surface.py").write_text("# stand-in\n", encoding="utf-8")
    _uv_fails(tmp_path, 3, "stale")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    assert "is not checked out, so its prompt surface is not checked here" in done.stderr
    assert not (tmp_path / "uv_called").exists()


_BUDGET_TEST: str = "tests/smoke/test_context_budget.py::test_a_fresh_homes_first_step_stays_within_its_budget"


def test_a_change_of_the_prompt_surface_runs_the_first_steps_budget_in_a_throwaway_home(
    repo: Path, tmp_path: Path
) -> None:
    """Item 1997: the budget's test is a macOS smoke test a pull request never runs, and main went red on it four times
    in a day; a pushed change of the surface is measured against it here, in a home of its own."""
    remote_tip: str = _with_surface_script(repo)
    (repo / "src" / "dotagent").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "prompt_surface").mkdir(parents=True)
    (repo / "src" / "dotagent" / "tools.py").write_text("a longer tool\n", encoding="utf-8")
    (repo / "tests" / "fixtures" / "prompt_surface" / "tools.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a tool's description and its surface")
    tip: str = _git(repo, "rev-parse", "HEAD")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    called: list[str] = (tmp_path / "uv_called").read_text().splitlines()
    assert called[0] == "run python scripts/prompt_surface.py --check"
    assert called[1].startswith("run pytest ") and called[1].endswith(_BUDGET_TEST)
    home: str = (tmp_path / "uv_homes").read_text().splitlines()[1]
    assert home != "" and not Path(home).exists()


def test_a_first_step_over_its_budget_stops_the_push_with_the_measured_numbers_and_no_model_is_asked(
    repo: Path, tmp_path: Path
) -> None:
    remote_tip: str = _with_surface_script(repo)
    (repo / "tests" / "fixtures" / "prompt_surface").mkdir(parents=True)
    (repo / "src" / "dotagent").mkdir(parents=True)
    (repo / "src" / "dotagent" / "tools.py").write_text("a longer tool\n", encoding="utf-8")
    (repo / "tests" / "fixtures" / "prompt_surface" / "tools.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a tool's description and its surface")
    tip: str = _git(repo, "rev-parse", "HEAD")
    (tmp_path / "uv_says").write_text(
        "F\nE       AssertionError: in all 33732, system 9235 (...); raise the budget\nE       assert 33732 <= 33700\n",
        encoding="utf-8",
    )
    (tmp_path / "pytest_exit").write_text("1\n", encoding="utf-8")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (1, 0), done.stdout + done.stderr
    assert "the first step is over its budget" in done.stderr
    assert "in all 33732, system 9235" in done.stderr and "assert 33732 <= 33700" in done.stderr


def test_a_change_under_src_dotagent_that_leaves_the_surface_as_it_was_does_not_run_the_budget(
    repo: Path, tmp_path: Path
) -> None:
    remote_tip: str = _with_surface_script(repo)
    (repo / "src" / "dotagent").mkdir(parents=True)
    tip: str = _commit(repo, "src/dotagent/store.py", "a query\n", "a query")
    (tmp_path / "pytest_exit").write_text("1\n", encoding="utf-8")
    (tmp_path / "uv_says").write_text("over\n", encoding="utf-8")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (0, 1), done.stdout + done.stderr
    assert (tmp_path / "uv_called").read_text().splitlines() == ["run python scripts/prompt_surface.py --check"]


def test_a_push_of_the_budget_alone_runs_it(repo: Path, tmp_path: Path) -> None:
    """The review of item 1997: the check sat under the condition of a change under src/dotagent, so a push that raised
    the budget, or wrote the surface again alone, ran nothing."""
    remote_tip: str = _with_surface_script(repo)
    (repo / "tests" / "smoke").mkdir(parents=True)
    tip: str = _commit(repo, "tests/smoke/test_context_budget.py", "BUDGET_TOKENS = 1\n", "a budget")
    (tmp_path / "pytest_exit").write_text("1\n", encoding="utf-8")
    (tmp_path / "uv_says").write_text("E       assert 33732 <= 1\n", encoding="utf-8")
    done, asked = _run_hook(repo, tmp_path, tip, remote_tip)
    assert (done.returncode, asked) == (1, 0), done.stdout + done.stderr
    assert "assert 33732 <= 1" in done.stderr
