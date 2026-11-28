"""The commit hook's stamp (`.claude/hooks/commit-msg.sh`) and scripts/hook_stamps.py, which finds the commits made
without it: on 2026-11-28 a helper committed with `git -c core.hooksPath=/dev/null`, and nothing showed it after."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT: Path = Path(__file__).resolve().parents[2]
_SCRIPT: Path = _ROOT / "scripts" / "hook_stamps.py"
_COMMIT_HOOK: Path = _ROOT / ".claude" / "hooks" / "commit-msg.sh"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hook_stamps", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stamps = _load()


def _git(repo: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository whose main holds the commit hook's file, on a branch `feature`; its hooks folder holds the hook."""
    work: Path = tmp_path / "repo"
    work.mkdir()
    hooks: Path = tmp_path / "hooks"
    hooks.mkdir()
    shutil.copy(_COMMIT_HOOK, hooks / "commit-msg")
    env: dict[str, str] = _env(tmp_path, {})
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
    ):
        assert _git(work, args, env).returncode == 0
    (work / ".claude" / "hooks").mkdir(parents=True)
    shutil.copy(_COMMIT_HOOK, work / ".claude" / "hooks" / "commit-msg.sh")
    assert _git(work, ["add", "."], env).returncode == 0
    assert _git(work, ["commit", "-q", "-m", "main"], env).returncode == 0
    assert _git(work, ["checkout", "-q", "-b", "feature"], env).returncode == 0
    return work


def _env(tmp_path: Path, extra: dict[str, str]) -> dict[str, str]:
    """Git's environment: the hooks at ``tmp_path``/hooks, no global or system config, and ``extra``."""
    env: dict[str, str] = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(tmp_path / "hooks"),
        }
    )
    env.update(extra)
    return env


def _commit(repo: Path, tmp_path: Path, name: str, args: list[str], extra: dict[str, str]) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    env: dict[str, str] = _env(tmp_path, extra)
    assert _git(repo, ["add", name], env).returncode == 0
    done = _git(repo, [*args, "commit", "-q", "-m", f"Item 1: {name}"], env)
    assert done.returncode == 0, done.stderr
    return _git(repo, ["rev-parse", "HEAD"], env).stdout.strip()


def _check(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--base", "main", "--head", "feature"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_commit_made_with_the_hooks_is_stamped_and_passes(repo: Path, tmp_path: Path) -> None:
    sha: str = _commit(repo, tmp_path, "a.txt", [], {})
    message: str = _git(repo, ["log", "-1", "--format=%B", sha], _env(tmp_path, {})).stdout
    assert message.rstrip().endswith("Dot-Hooks: commit-msg")
    done = _check(repo)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_commit_made_with_the_hooks_switched_off_is_listed(repo: Path, tmp_path: Path) -> None:
    _commit(repo, tmp_path, "a.txt", [], {})
    skipped: str = _commit(repo, tmp_path, "b.txt", ["-c", "core.hooksPath=/dev/null"], {})
    (repo / "c.txt").write_text("c", encoding="utf-8")
    env: dict[str, str] = _env(tmp_path, {})
    assert _git(repo, ["add", "c.txt"], env).returncode == 0
    assert _git(repo, ["commit", "-q", "--no-verify", "-m", "Item 1: c.txt"], env).returncode == 0
    no_verify: str = _git(repo, ["rev-parse", "HEAD"], env).stdout.strip()
    done = _check(repo)
    assert done.returncode == 1
    assert skipped[:12] in done.stderr and no_verify[:12] in done.stderr
    assert "git commit --amend --no-edit" in done.stderr
    # Amended with the hooks, the last is stamped; the one before it still is not.
    assert _git(repo, ["commit", "-q", "--amend", "--no-edit"], env).returncode == 0
    again = _check(repo)
    assert again.returncode == 1 and skipped[:12] in again.stderr and no_verify[:12] not in again.stderr


def test_github_s_commits_older_commits_and_commits_on_a_tree_without_the_hook_are_not_held(
    repo: Path, tmp_path: Path
) -> None:
    off: list[str] = ["-c", "core.hooksPath=/dev/null"]
    _commit(repo, tmp_path, "a.txt", off, {"GIT_COMMITTER_EMAIL": "noreply@github.com", "GIT_COMMITTER_NAME": "GitHub"})
    _commit(repo, tmp_path, "b.txt", off, {"GIT_AUTHOR_DATE": "2026-11-27T09:00:00Z"})
    done = _check(repo)
    assert done.returncode == 0, done.stdout + done.stderr
    held = stamps.Commit(
        sha="x", parent="p", committer="a@b", authored=stamps.STAMPED_SINCE, message="m", parent_has_hook=True
    )
    assert stamps.held(held)
    assert not stamps.held(stamps.Commit(**{**held.__dict__, "parent_has_hook": False}))
    assert not stamps.held(stamps.Commit(**{**held.__dict__, "authored": stamps.STAMPED_SINCE - 1}))
    assert not stamps.held(stamps.Commit(**{**held.__dict__, "committer": "noreply@github.com"}))


def test_a_rebase_keeps_the_stamp_without_running_the_hook(repo: Path, tmp_path: Path) -> None:
    """The merge train and a repair session bring a branch up to main with `git rebase`, which runs no commit hook."""
    _commit(repo, tmp_path, "a.txt", [], {})
    env: dict[str, str] = _env(tmp_path, {})
    assert _git(repo, ["checkout", "-q", "main"], env).returncode == 0
    _commit(repo, tmp_path, "m.txt", [], {})
    assert _git(repo, ["checkout", "-q", "feature"], env).returncode == 0
    (tmp_path / "hooks" / "commit-msg").unlink()
    rebased = _git(repo, ["rebase", "-q", "main"], env)
    assert rebased.returncode == 0, rebased.stderr
    done = _check(repo)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_hook_leaves_an_empty_message_empty_so_git_still_aborts(repo: Path, tmp_path: Path) -> None:
    (repo / "e.txt").write_text("e", encoding="utf-8")
    env: dict[str, str] = _env(tmp_path, {})
    assert _git(repo, ["add", "e.txt"], env).returncode == 0
    done = _git(repo, ["commit", "-q", "-m", ""], env)
    assert done.returncode != 0 and "empty commit message" in done.stderr
