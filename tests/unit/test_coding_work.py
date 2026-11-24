"""A real Git repository has two parallel worktrees with separate change summaries."""

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from dotagent.coding_work import CodingWork, inspect_worktree, pull_state
from dotagent.core.errors import ToolInputError


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ("/usr/bin/git", "-C", str(path), *args),
        check=True,
        capture_output=True,
        text=True,
        env={
            "HOME": "/var/empty",
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "Dot test",
            "GIT_AUTHOR_EMAIL": "dot@example.test",
            "GIT_COMMITTER_NAME": "Dot test",
            "GIT_COMMITTER_EMAIL": "dot@example.test",
        },
    ).stdout.strip()


def test_two_linked_worktrees_report_their_own_commits(tmp_path: Path) -> None:
    repo: Path = tmp_path.resolve() / "project"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "commit", "--allow-empty", "-m", "Initial")
    base: str = _git(repo, "rev-parse", "HEAD")
    observed: list[CodingWork] = []
    for name in ("first", "second"):
        worktree: Path = repo.parent / name
        _git(repo, "worktree", "add", "-b", name, str(worktree))
        (worktree / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
        _git(worktree, "add", ".")
        _git(worktree, "commit", "-m", name)
        observed.append(inspect_worktree(worktree, base, None, {}))
    first, second = observed
    assert (first.branch, second.branch) == ("first", "second")
    assert "first.txt" in first.diff_stat and "second.txt" not in first.diff_stat
    assert "second.txt" in second.diff_stat and "first.txt" not in second.diff_stat
    assert first.head != second.head and first.base == second.base == base
    with pytest.raises(ToolInputError, match="linked Git worktree"):
        inspect_worktree(repo, base, None, {})


def test_pull_state_checks_the_branch_and_head(tmp_path: Path) -> None:
    head: str = "a" * 40
    response: str = json.dumps({"state": "open", "merged": False, "head": {"sha": head, "ref": "feature"}})
    gh: Path = tmp_path / "gh"
    gh.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(response)}\n", encoding="utf-8")
    gh.chmod(0o700)
    env: dict[str, str] = {"PATH": str(tmp_path)}
    url: str = "https://github.com/example/project/pull/1"
    assert pull_state(url, head, "feature", env) == "open"
    assert pull_state(url, head, "other", env) == "unverified"
    assert pull_state(url, "b" * 40, "feature", env) == "unverified"


def test_failed_pull_lookup_retries_then_reports_the_error(tmp_path: Path) -> None:
    calls: Path = tmp_path / "calls"
    gh: Path = tmp_path / "gh"
    gh.write_text(
        f"#!/bin/sh\nprintf 'called\\n' >> {shlex.quote(str(calls))}\nprintf 'HTTP 403\\n' >&2\nexit 1\n",
        encoding="utf-8",
    )
    gh.chmod(0o700)
    url: str = "https://github.com/example/project/pull/1"
    with pytest.raises(ToolInputError, match="Could not verify the GitHub pull request") as failure:
        pull_state(url, "a" * 40, "feature", {"PATH": str(tmp_path)})
    assert calls.read_text(encoding="utf-8").splitlines() == ["called", "called"]
    assert failure.value.context["url"] == url
    assert failure.value.context["exit"] == 1
    assert "HTTP 403" in str(failure.value.context["stderr"])
