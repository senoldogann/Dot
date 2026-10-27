"""The daemon starts itself again with malloc's space-efficient mode (item 730), once."""

import os
import sys

import pytest

from dotagent.core.heap import SPACE_EFFICIENT_ENV, ensure_space_efficient_heap, heap_restart

ARGV: list[str] = ["/opt/dot/bin/python", "-P", "-m", "dotagent", "daemon"]


def test_a_process_without_the_switch_starts_again_with_its_own_arguments() -> None:
    assert heap_restart({}, "/opt/dot/bin/python3.13", ARGV) == [
        "/opt/dot/bin/python3.13",
        "-P",
        "-m",
        "dotagent",
        "daemon",
    ]
    assert heap_restart({SPACE_EFFICIENT_ENV: "0"}, "/opt/dot/bin/python3.13", ARGV) is not None


def test_a_process_with_the_switch_goes_on_and_one_without_arguments_too() -> None:
    assert heap_restart({SPACE_EFFICIENT_ENV: "1"}, "/opt/dot/bin/python3.13", ARGV) is None
    assert heap_restart({}, "/opt/dot/bin/python3.13", []) is None


def test_the_restart_sets_the_switch_before_it_starts_the_process_again(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set first, so that the variable the restart writes is taken away again when the test ends.
    monkeypatch.setenv(SPACE_EFFICIENT_ENV, "")
    monkeypatch.delenv(SPACE_EFFICIENT_ENV)
    started: list[tuple[str, list[str], str | None]] = []

    def exec_process(path: str, arguments: list[str]) -> None:
        started.append((path, arguments, os.environ.get(SPACE_EFFICIENT_ENV)))

    ensure_space_efficient_heap(exec_process)
    assert started == [(sys.executable, [sys.executable, *sys.orig_argv[1:]], "1")]
    started.clear()
    ensure_space_efficient_heap(exec_process)
    assert started == []
