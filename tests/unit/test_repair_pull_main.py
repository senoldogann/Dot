"""The night's pull of the owner's checkout (item 1937): fast-forward to main's green commit, then ``uv sync --locked``
in it, and a sync that fails puts the checkout back on the commit it stood on (synced again) and raises, so nothing
restarts onto code its packages do not match. Real git in a throw-away folder; ``uv`` is a stand-in on ``PATH`` that
records its arguments and fails on the commit named in ``FAIL_ON``."""

import asyncio
import os
import stat
import subprocess
from pathlib import Path

import pytest

from dotagent.core.errors import RepairError
from dotagent.repair.github import pull_main

_FAKE_UV: str = """#!/bin/sh
head=$(git rev-parse HEAD)
echo "$head $*" >> "$UV_CALLS"
[ "$head" = "$FAIL_ON" ] && { echo "lock does not match" >&2; exit 2; }
exit 0
"""


def _git(env: dict[str, str], cwd: Path, *argv: str) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def _commit(env: dict[str, str], repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    _git(env, repo, "add", name)
    _git(env, repo, "commit", "--quiet", "-m", name)
    return _git(env, repo, "rev-parse", "HEAD")


@pytest.fixture
def setting(tmp_path: Path) -> tuple[dict[str, str], Path, Path, str, str]:
    """An origin with two commits on main, and the owner's checkout cloned at the first; the env, the checkout, the
    file of ``uv``'s calls, and the two commits."""
    bin_dir: Path = tmp_path / "bin"
    bin_dir.mkdir()
    uv: Path = bin_dir / "uv"
    uv.write_text(_FAKE_UV)
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    calls: Path = tmp_path / "uv-calls"
    env: dict[str, str] = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "UV_CALLS": str(calls),
        "FAIL_ON": "none",
    }
    origin: Path = tmp_path / "origin"
    origin.mkdir()
    _git(env, origin, "init", "--quiet", "--initial-branch=main")
    first: str = _commit(env, origin, "a")
    repo: Path = tmp_path / "checkout"
    _git(env, tmp_path, "clone", "--quiet", str(origin), str(repo))
    second: str = _commit(env, origin, "b")
    return env, repo, calls, first, second


def test_a_pull_fast_forwards_and_then_syncs_the_lock(setting: tuple[dict[str, str], Path, Path, str, str]) -> None:
    env, repo, calls, _first, second = setting
    assert asyncio.run(pull_main(repo, env, second)) is True
    assert _git(env, repo, "rev-parse", "HEAD") == second
    assert calls.read_text().splitlines() == [f"{second} sync --locked --quiet"]


def test_a_failed_sync_puts_the_checkout_back_and_raises(setting: tuple[dict[str, str], Path, Path, str, str]) -> None:
    env, repo, calls, first, second = setting
    env["FAIL_ON"] = second
    with pytest.raises(RepairError) as raised:
        asyncio.run(pull_main(repo, env, second))
    assert "put back" in raised.value.message and "nor" not in raised.value.message
    assert raised.value.context["pulled"] == second and raised.value.context["put_back_to"] == first
    assert _git(env, repo, "rev-parse", "HEAD") == first
    # The new lock was tried, then the old one synced again.
    assert calls.read_text().splitlines() == [f"{second} sync --locked --quiet", f"{first} sync --locked --quiet"]


def test_a_checkout_off_main_is_left_alone(setting: tuple[dict[str, str], Path, Path, str, str]) -> None:
    env, repo, calls, first, second = setting
    _git(env, repo, "switch", "--quiet", "-c", "work")
    assert asyncio.run(pull_main(repo, env, second)) is False
    assert _git(env, repo, "rev-parse", "HEAD") == first
    assert not calls.exists()
