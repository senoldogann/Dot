"""The mark of a running daemon (item 151): a daemon that ends without an error removes it; one that ended with an
error, or never ended (it was killed), leaves it, and the next start finds it."""

from pathlib import Path

import pytest

from dotagent.server.daemon import running_mark


def test_a_daemon_that_did_not_end_cleanly_leaves_its_mark_for_the_next_start(tmp_path: Path) -> None:
    mark: Path = tmp_path / "daemon.running"
    with running_mark(mark) as unclean:
        assert (unclean, mark.exists()) == (False, True)
    assert not mark.exists(), "a daemon that ends without an error removes its mark"
    with pytest.raises(RuntimeError, match="the scheduler loop failed"), running_mark(mark):
        raise RuntimeError("the scheduler loop failed")
    assert mark.exists(), "a daemon that ended with an error leaves its mark"
    with running_mark(mark) as unclean:
        assert unclean
    assert not mark.exists()
