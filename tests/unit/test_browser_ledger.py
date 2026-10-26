"""The ledger of the tabs Dot has open in the Chrome of the user (item 703) is never written or read through a link:
Dot runs outside the sandbox, so a link a run planted where one of its files goes would have Dot overwrite, or copy
out, a file the run itself may not touch. And its files are among those the sandbox keeps every run from.

Everything happens in a folder of the test's own; the files a link points at are the test's too.
"""

import json
import os
from pathlib import Path

import pytest

from dotagent.browser import ledger
from dotagent.config import load_default_config
from dotagent.runtime import _guarded_files


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder: Path = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("DOT_HOME", str(folder))
    return folder


def _victim(tmp_path: Path, text: str) -> Path:
    victim: Path = tmp_path / "victim.json"
    victim.write_text(text, encoding="utf-8")
    victim.chmod(0o644)
    return victim


def test_a_link_where_the_ledger_is_staged_is_replaced_and_what_it_names_is_untouched(
    home: Path, tmp_path: Path
) -> None:
    victim: Path = _victim(tmp_path, '{"keep": ["me"]}')
    staged: Path = ledger.ledger_path().with_suffix(".tmp")
    staged.symlink_to(victim)
    ledger.record(ledger.ledger_path(), frozenset({"T1"}))
    assert victim.read_text(encoding="utf-8") == '{"keep": ["me"]}'
    assert victim.stat().st_mode & 0o777 == 0o644
    written: Path = ledger.ledger_path()
    assert not written.is_symlink() and json.loads(written.read_text(encoding="utf-8")) == {str(os.getpid()): ["T1"]}
    assert not staged.exists()


def test_a_link_where_the_ledger_is_is_not_read_and_nothing_it_names_is_copied_out(home: Path, tmp_path: Path) -> None:
    victim: Path = _victim(tmp_path, '{"tokens": ["s3cr3t-a", "s3cr3t-b"]}')
    ledger.ledger_path().symlink_to(victim)
    ledger.record(ledger.ledger_path(), frozenset({"T1"}))
    written: str = ledger.ledger_path().read_text(encoding="utf-8")
    assert "s3cr3t" not in written
    assert victim.read_text(encoding="utf-8") == '{"tokens": ["s3cr3t-a", "s3cr3t-b"]}'
    assert ledger.take_left(ledger.ledger_path()) == frozenset()


def test_a_link_where_the_lock_is_refuses_the_change(home: Path, tmp_path: Path) -> None:
    victim: Path = _victim(tmp_path, "{}")
    ledger.ledger_path().with_suffix(".lock").symlink_to(victim)
    with pytest.raises(OSError):
        ledger.record(ledger.ledger_path(), frozenset({"T1"}))
    assert victim.read_text(encoding="utf-8") == "{}"
    assert not ledger.ledger_path().exists()


def test_the_ledgers_files_are_guarded_from_every_run(home: Path) -> None:
    guarded: tuple[Path, ...] = _guarded_files(load_default_config())
    assert set(ledger.ledger_files()) <= set(guarded)
    assert {path.name for path in ledger.ledger_files()} == {
        "browser-tabs.json",
        "browser-tabs.lock",
        "browser-tabs.tmp",
    }
