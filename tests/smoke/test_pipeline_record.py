"""The record file a pipeline's ledger lives in (item 1227), on a real file: changes made at once by several runs take
turns under its lock and none is lost, a refused change leaves the file byte for byte, the owner's text around the block
stays, and the change can be undone (``dotx undo``). The record is a copy made here, never the owner's."""

import threading
from pathlib import Path

import pytest

from dotagent.core.errors import ToolExecutionError
from dotagent.pipeline import record
from dotagent.pipeline.ledger import (
    ClientChange,
    Ledger,
    LedgerRefusal,
    begin,
    end,
    parse,
    pending,
    upsert_client,
)
from dotagent.undo.journal import JournalTarget

OWNERS_TEXT: str = "# Asiakasputki — örnek\n\n| # | İşletme |\n|---|---|\n| 1 | Örnek Oy |\n\nSahibin notu.\n"


def _journal(tmp_path: Path) -> JournalTarget:
    return JournalTarget(
        directory=tmp_path / "undo" / "run_x",
        tool="pipeline_client",
        max_file_bytes=1_000_000,
        max_run_bytes=10_000_000,
    )


def _new(name: str) -> ClientChange:
    return ClientChange(name=name, contact="", status=None, expecting=None, next_step=None, next_at=None)


def test_changes_made_at_once_take_turns_and_none_is_lost(tmp_path: Path) -> None:
    path: Path = tmp_path / "leads" / "client-pipeline.md"
    path.parent.mkdir()
    path.write_text(OWNERS_TEXT, encoding="utf-8")
    errors: list[BaseException] = []

    def add(index: int) -> None:
        try:
            record.change(
                path,
                _journal(tmp_path),
                lambda ledger: (upsert_client(ledger, f"client-{index}", _new(f"Client {index}")), None),
            )
        except BaseException as error:  # noqa: BLE001 - the thread's failure is the test's
            errors.append(error)

    threads: list[threading.Thread] = [threading.Thread(target=add, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    text: str = path.read_text(encoding="utf-8")
    assert text.startswith(OWNERS_TEXT)
    assert sorted(client.id for client in parse(text).clients) == sorted(f"client-{index}" for index in range(12))


def test_two_runs_beginning_the_same_send_at_once_begin_it_once(tmp_path: Path) -> None:
    """A schedule's run and the reply's run it handed both about to send the same mail: one begins it, the other is
    refused, and whatever happens after that, it goes out once."""
    path: Path = tmp_path / "client-pipeline.md"
    record.change(path, _journal(tmp_path), lambda ledger: (upsert_client(ledger, "a-oy", _new("A Oy")), None))
    outcomes: list[str] = []
    gate: threading.Barrier = threading.Barrier(2)

    def try_begin() -> None:
        gate.wait()
        try:
            record.change(
                path,
                _journal(tmp_path),
                lambda ledger: (begin(ledger, "a-oy", "first-mail", "", "2026-11-19 10:00"), None),
            )
            outcomes.append("begun")
        except LedgerRefusal:
            outcomes.append("refused")

    threads: list[threading.Thread] = [threading.Thread(target=try_begin) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["begun", "refused"]
    assert [action.key for action in pending(record.read(path))] == ["a-oy/first-mail"]


def test_a_refused_change_leaves_the_file_as_it_was(tmp_path: Path) -> None:
    path: Path = tmp_path / "client-pipeline.md"
    path.write_text(OWNERS_TEXT, encoding="utf-8")
    record.change(path, _journal(tmp_path), lambda ledger: (upsert_client(ledger, "a-oy", _new("A Oy")), None))
    record.change(
        path, _journal(tmp_path), lambda ledger: (begin(ledger, "a-oy", "first-mail", "", "2026-11-19 10:00"), None)
    )
    before: bytes = path.read_bytes()

    def resend(ledger: Ledger) -> tuple[Ledger, None]:
        return begin(ledger, "a-oy", "first-mail", "", "2026-11-19 11:00"), None

    with pytest.raises(LedgerRefusal):
        record.change(path, _journal(tmp_path), resend)
    assert path.read_bytes() == before


def test_a_block_the_owner_broke_is_neither_read_nor_written(tmp_path: Path) -> None:
    path: Path = tmp_path / "client-pipeline.md"
    record.change(path, _journal(tmp_path), lambda ledger: (upsert_client(ledger, "a-oy", _new("A Oy")), None))
    broken: str = path.read_text(encoding="utf-8").replace("<!-- dot:pipeline:end -->", "")
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(ToolExecutionError, match="markers"):
        record.read(path)
    with pytest.raises(ToolExecutionError, match="markers"):
        record.change(path, _journal(tmp_path), lambda ledger: (upsert_client(ledger, "b-oy", _new("B Oy")), None))
    assert path.read_text(encoding="utf-8") == broken


def test_a_run_that_broke_off_after_its_send_leaves_the_next_one_the_action_to_look_at(tmp_path: Path) -> None:
    path: Path = tmp_path / "client-pipeline.md"
    record.change(path, _journal(tmp_path), lambda ledger: (upsert_client(ledger, "a-oy", _new("A Oy")), None))
    record.change(
        path, _journal(tmp_path), lambda ledger: (begin(ledger, "a-oy", "first-mail", "v1", "2026-11-19 10:00"), None)
    )
    # The process went here, before pipeline_end. The next run reads the record: the action is there to look at.
    unsettled = pending(record.read(path))
    assert [(action.key, action.state) for action in unsettled] == [("a-oy/first-mail", "begun")]
    record.change(
        path,
        _journal(tmp_path),
        lambda ledger: (end(ledger, "a-oy/first-mail", "sent", "In Sent at 10:00", "2026-11-19 10:30"), None),
    )
    assert pending(record.read(path)) == ()
    # Each write was noted for dotx undo.
    assert any((tmp_path / "undo" / "run_x").iterdir())
