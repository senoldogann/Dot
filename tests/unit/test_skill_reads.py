"""The date of a skill's last read (item 585, round 3): the read dates it (what a skill's rest is counted from) and
changes nothing else of its record; it is cheap (a date of the last hour is left, and no lock is taken for it), safe
(two runs reading at once, and a run counting its use, lose neither a date nor a count; the date never goes back) and
never fails the read it belongs to (a date that cannot be written is logged). Real files in a throwaway folder."""

import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from dotagent.agent import playbooks
from dotagent.agent.playbooks import (
    Playbook,
    PlaybookMeta,
    date_read,
    date_reads,
    drop_playbook,
    playbook_name,
    read_meta,
    record_use,
    recorded,
    skill_resting,
    write_playbook,
)
from dotagent.agent.review import PlaybookDraft
from dotagent.skills.catalog import PLAYBOOK_SOURCE
from dotagent.skills.types import Skill

KIND: str = "Find a product's price"
NAME: str = playbook_name(KIND)
DAY_S: float = 86400.0


def _skill(root: Path, written: float) -> None:
    write_playbook(
        root,
        Playbook(
            name=NAME,
            kind=KIND,
            host="shop.example.com",
            draft=PlaybookDraft(
                kind=KIND,
                description="Search a product and read its price.",
                when="A goal that asks for the price of a product.",
                steps=["Search for <product>.", "Read the price of the first result."],
                parameters=["<product>: what to look for"],
            ),
            run_id="run-1",
            agent_id=None,
            goal="Find a price",
            written_at=written,
            acting_steps=3,
            tainted=False,
        ),
    )


def _listed(name: str, source: str, root: Path) -> Skill:
    return Skill(name=name, description="d", root=root / name, body="b", source=source, icon="")


def test_a_read_dates_the_skill_and_leaves_the_rest_of_its_record_as_it_was(tmp_path: Path) -> None:
    now: float = time.time()
    root: Path = tmp_path / "playbooks"
    _skill(root, now - 30 * DAY_S)
    before: PlaybookMeta = read_meta(root / NAME)
    assert before.last_read_at is None
    assert date_read(root, NAME, now) is True
    after: PlaybookMeta = read_meta(root / NAME)
    assert after.last_read_at == pytest.approx(now)
    assert after == before.model_copy(update={"last_read_at": now})
    assert (after.uses, after.failures, after.failing, after.version) == (0, 0, 0, 1)


def test_a_skill_that_is_read_rests_after_the_days_from_its_read_and_one_nobody_read_rests_sooner(
    tmp_path: Path,
) -> None:
    """Written 59 days ago, a skill rests in a day when nothing reads it; a read starts its days again."""
    now: float = time.time()
    root: Path = tmp_path / "playbooks"
    _skill(root, now - 59 * DAY_S)
    unread: PlaybookMeta = read_meta(root / NAME)
    assert not skill_resting(unread, now, 60) and skill_resting(unread, now + 5 * DAY_S, 60)
    date_read(root, NAME, now)
    read: PlaybookMeta = read_meta(root / NAME)
    assert [skill_resting(read, now + days * DAY_S, 60) for days in (5, 59, 61)] == [False, False, True]
    assert (read.uses, read.failures) == (0, 0)


def test_a_date_within_the_hour_is_left_as_it_is_without_the_lock_or_a_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now: float = time.time()
    root: Path = tmp_path / "playbooks"
    _skill(root, now - 30 * DAY_S)
    writes: list[str] = []
    locks: list[Path] = []
    real_save, real_changing = playbooks.save_meta, playbooks.changing

    def counting_save(folder: Path, meta: PlaybookMeta) -> None:
        writes.append(folder.name)
        real_save(folder, meta)

    @contextmanager
    def counting_lock(held: Path) -> Iterator[None]:
        locks.append(held)
        with real_changing(held):
            yield

    monkeypatch.setattr(playbooks, "save_meta", counting_save)
    monkeypatch.setattr(playbooks, "changing", counting_lock)
    assert date_read(root, NAME, now) is True and (len(writes), len(locks)) == (1, 1)
    # Read again and again within the hour: nothing is written and nothing is locked.
    assert [date_read(root, NAME, now + seconds) for seconds in (1, 60, 3599)] == [False, False, False]
    assert (len(writes), len(locks)) == (1, 1)
    assert date_read(root, NAME, now + 3601) is True and (len(writes), len(locks)) == (2, 2)
    assert read_meta(root / NAME).last_read_at == pytest.approx(now + 3601)


def test_the_date_never_goes_back_and_the_count_of_a_run_that_ended_is_not_earlier_than_the_read(
    tmp_path: Path,
) -> None:
    now: float = time.time()
    root: Path = tmp_path / "playbooks"
    _skill(root, now - 30 * DAY_S)
    date_read(root, NAME, now)
    # A clock that went back, and a run whose end was counted with an earlier time, do not take the date back.
    assert date_read(root, NAME, now - DAY_S) is False
    counted: PlaybookMeta | None = record_use(root, NAME, True, now - 2 * DAY_S)
    assert counted is not None and counted.last_read_at == pytest.approx(now) and counted.uses == 1
    later: PlaybookMeta | None = record_use(root, NAME, False, now + 5.0)
    assert (
        later is not None and later.last_read_at == pytest.approx(now + 5.0) and (later.uses, later.failures) == (1, 1)
    )
    # The record a run that ended gives is dated by the later of the two.
    meta: PlaybookMeta = read_meta(root / NAME)
    assert recorded(meta, True, now - DAY_S).last_read_at == pytest.approx(now + 5.0)
    assert recorded(meta, True, now + DAY_S).last_read_at == pytest.approx(now + DAY_S)
    assert recorded(meta, True).last_read_at == pytest.approx(now + 5.0)


def test_runs_reading_at_once_lose_neither_a_date_nor_a_count(tmp_path: Path) -> None:
    """Eight reads at times more than an hour apart and six runs that ended, all at once: the latest date is the one
    the record keeps, and every count is there."""
    now: float = time.time()
    root: Path = tmp_path / "playbooks"
    _skill(root, now - 30 * DAY_S)
    reads: list[float] = [now + 4000.0 * number for number in range(8)]
    with ThreadPoolExecutor(max_workers=14) as pool:
        futures = [pool.submit(date_read, root, NAME, at) for at in reads]
        futures += [pool.submit(record_use, root, NAME, True, now) for _ in range(6)]
        for future in futures:
            future.result(timeout=30)
    meta: PlaybookMeta = read_meta(root / NAME)
    assert meta.last_read_at == pytest.approx(reads[-1]) and (meta.uses, meta.failures) == (6, 0)


async def test_a_date_that_cannot_be_written_is_logged_and_the_read_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now: float = time.time()
    root: Path = tmp_path / "playbooks"
    _skill(root, now - 30 * DAY_S)
    learned: Skill = _listed(NAME, PLAYBOOK_SOURCE, root)
    elsewhere: Skill = _listed("home-skill", "home", root)

    def full_disk(folder: Path, meta: PlaybookMeta) -> None:
        raise OSError(28, "No space left on device")

    real_save = playbooks.save_meta
    monkeypatch.setattr(playbooks, "save_meta", full_disk)
    with capture_logs() as logs:
        await date_reads(root, [elsewhere, learned], now)
    assert [(entry["event"], entry["playbook"]) for entry in logs] == [("playbook_read_not_dated", NAME)]
    assert read_meta(root / NAME).last_read_at is None
    # The next read tries again, and a skill that is not a learned one is none to date.
    monkeypatch.setattr(playbooks, "save_meta", real_save)
    with capture_logs() as logs:
        await date_reads(root, [elsewhere, learned], now + 10)
    assert logs == [] and read_meta(root / NAME).last_read_at == pytest.approx(now + 10)
    # A skill that is gone since is none to date, and says nothing.
    drop_playbook(root, NAME)
    with capture_logs() as logs:
        await date_reads(root, [learned], now + 2 * DAY_S)
    assert logs == []
    assert date_read(root, "not/a/skill", now) is False
