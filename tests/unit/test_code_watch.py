"""What the daemon sees of the code's process from outside (item 1500): its memory, and its scratch folder."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dotagent.codemode import watch
from dotagent.codemode.watch import memory_is_watchable, resident_bytes, scratch_usage


@pytest.mark.macos
def test_the_resident_memory_of_a_process_is_read_as_it_grows_and_of_one_that_is_gone_is_none() -> None:
    assert memory_is_watchable()
    child: subprocess.Popen[bytes] = subprocess.Popen(
        [sys.executable, "-c", "import time; x = b'x' * 120_000_000; time.sleep(5)"]
    )
    try:
        seen: int = 0
        for _ in range(100):
            time.sleep(0.05)
            seen = resident_bytes(child.pid) or 0
            if seen > 100_000_000:
                break
        assert seen > 100_000_000
    finally:
        child.kill()
        child.wait()
    assert resident_bytes(child.pid) is None


def test_the_scratch_folder_is_measured_by_what_its_files_take_and_a_link_is_not_followed(tmp_path: Path) -> None:
    scratch: Path = tmp_path / "scratch"
    (scratch / "deep" / "deeper").mkdir(parents=True)
    (scratch / "a.bin").write_bytes(b"x" * 100_000)
    (scratch / "deep" / "deeper" / "b.bin").write_bytes(b"x" * 50_000)
    outside: Path = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge.bin").write_bytes(b"x" * 5_000_000)
    os.symlink(outside, scratch / "link")
    size, entries = scratch_usage(scratch, 100)
    assert 150_000 <= size < 1_000_000
    assert entries == 5
    # Past the count it is allowed, counting stops: a folder of a million files is not walked.
    _, stopped = scratch_usage(scratch, 2)
    assert stopped == 3
    assert scratch_usage(tmp_path / "missing", 10) == (0, 0)


@pytest.mark.macos
def test_the_probe_asks_whether_another_process_can_be_read_not_the_daemon_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A daemon may read itself where it may not read a process it started: the probe starts one and reads that."""
    assert memory_is_watchable()
    calls: list[int] = []

    def refuses_others(pid: int) -> int | None:
        calls.append(pid)
        return None

    monkeypatch.setattr(watch, "resident_bytes", refuses_others)
    assert not memory_is_watchable()
    assert calls and os.getpid() not in calls
