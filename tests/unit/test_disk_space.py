"""The free-space floor (item 1938): below ``[store] min_free_gb`` the owner is told once (again only after the disk
recovered), and a copy of the database or an uploaded file is refused before it is written, with an error the web
answers as 507."""

import asyncio
import shutil
from pathlib import Path

import pytest

from dotagent.core.errors import DiskSpaceError
from dotagent.server.app import status_for
from dotagent.store.backup import ensure_backup_space
from dotagent.store.space import GIB, LOOK_EVERY_S, ensure_space, floor_bytes, short_of_space, space_change, watch_space


def test_the_floor_and_its_changes() -> None:
    assert floor_bytes(5.0) == 5 * GIB and floor_bytes(0.0) == 0
    assert short_of_space(6 * GIB, 2 * GIB, 5 * GIB) and not short_of_space(8 * GIB, 2 * GIB, 5 * GIB)
    assert not short_of_space(0, 10 * GIB, 0)  # 0: no floor
    assert space_change(False, 4 * GIB, 5 * GIB) == "low"
    assert space_change(True, 4 * GIB, 5 * GIB) == "same"
    assert space_change(True, 6 * GIB, 5 * GIB) == "recovered"
    assert space_change(False, 6 * GIB, 5 * GIB) == "same"


def test_a_write_that_would_go_below_the_floor_is_refused_before_it_begins(tmp_path: Path) -> None:
    free: int = shutil.disk_usage(tmp_path).free
    ensure_space(tmp_path, 1024, 1, "a file")
    with pytest.raises(DiskSpaceError) as raised:
        ensure_space(tmp_path / "not-made-yet", 1024, free + GIB, "the uploaded file")
    assert "nearly full" in raised.value.message and raised.value.context["needed_bytes"] == 1024
    assert status_for(raised.value) == 507
    database: Path = tmp_path / "dot.sqlite3"
    database.write_bytes(b"x" * 4096)
    with pytest.raises(DiskSpaceError):
        ensure_backup_space(database, tmp_path / "copies", tmp_path / "partial", free + GIB)
    ensure_backup_space(database, tmp_path / "copies", tmp_path / "partial", 1)
    # Nothing was written by a refusal.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["dot.sqlite3"]


def test_the_owner_is_told_once_while_the_disk_stays_low_and_again_after_it_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frees: list[int] = [4 * GIB, 3 * GIB, 6 * GIB, 4 * GIB]
    told: list[str] = []
    slept: list[float] = []
    monkeypatch.setattr("dotagent.store.space.free_bytes", lambda _folder: frees.pop(0))

    async def tell(text: str) -> None:
        told.append(text)

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        if not frees:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(watch_space(tmp_path, 5 * GIB, tell, sleep))
    assert len(told) == 2 and "4.0 GiB free" in told[0] and "5 GiB" in told[0]
    assert slept == [LOOK_EVERY_S] * 4
