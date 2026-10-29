"""Screenshot files follow the chats that own them; what nothing names any more is found and removed on request, and the
pages a delete frees go back to the disk once the file is compacted."""

import base64
import os
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from dotagent.core.errors import NotFoundError, RunConflict, StoreError
from dotagent.llm.types import ImageData, Message, ToolCall, assistant_message, user_message
from dotagent.store import db
from dotagent.store.db import Store, image_file_name
from dotagent.store.models import RunRecord, ThreadRecord


def _image(seed: bytes) -> ImageData:
    return ImageData(media_type="image/png", data_b64=base64.b64encode(seed).decode("ascii"))


def _names(folder: Path) -> set[str]:
    return {path.name for path in folder.iterdir()}


def _free_pages(database: Path) -> int:
    connection: sqlite3.Connection = sqlite3.connect(database)
    try:
        row: tuple[int] = connection.execute("PRAGMA freelist_count").fetchone()
        return int(row[0])
    finally:
        connection.close()


def _age(folder: Path, seconds: float) -> None:
    """Make every file in ``folder`` look as if it was last saved ``seconds`` ago."""
    then: float = time.time() - seconds
    for path in folder.iterdir():
        os.utime(path, (then, then))


async def test_deleting_a_thread_removes_its_screenshots_but_keeps_shared_ones(tmp_path: Path) -> None:
    images: Path = tmp_path / "images"
    store: Store = await Store.open(tmp_path / "dot.db", images)
    try:
        first: ThreadRecord = await store.create_thread("first", "test")
        second: ThreadRecord = await store.create_thread("second", "test")
        own, shared, from_event = _image(b"only the first"), _image(b"in both"), _image(b"named by an event")
        await store.append_messages(first.id, [user_message("a", (own, shared))])
        await store.append_messages(second.id, [user_message("b", (shared,))])
        run: RunRecord = await store.create_run(
            first.id,
            "goal",
            "task",
            "ollama:x",
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        await store.add_event(run.id, "tool_result", {"images": await store.save_images([from_event])})
        await store.finish_run(run.id, "done", "Done.")
        assert _names(images) == {image_file_name(image) for image in (own, shared, from_event)}
        _age(images, 7200)
        assert await store.delete_threads([first.id]) == [first.id]
        assert _names(images) == {image_file_name(shared)}
    finally:
        await store.close()


async def test_a_screenshot_saved_again_just_now_stays_until_a_sweep_finds_it(tmp_path: Path) -> None:
    images: Path = tmp_path / "images"
    store: Store = await Store.open(tmp_path / "dot.db", images)
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        seen_twice: ImageData = _image(b"seen twice")
        await store.append_messages(thread.id, [user_message("a", (seen_twice,))])
        _age(images, 7200)
        # A run in another chat takes the same screenshot: the file is the same one, and it is saved again.
        await store.save_images([seen_twice])
        await store.delete_threads([thread.id])
        assert _names(images) == {image_file_name(seen_twice)}
        found: list[Path] = await store.orphan_images(time.time() + 7200, 3600)
        assert [path.name for path in found] == [image_file_name(seen_twice)]
    finally:
        await store.close()


async def test_orphans_are_the_old_files_that_nothing_names(tmp_path: Path) -> None:
    images: Path = tmp_path / "images"
    store: Store = await Store.open(tmp_path / "dot.db", images)
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        named, stale, fresh = _image(b"named by a message"), _image(b"stale"), _image(b"fresh")
        await store.append_messages(thread.id, [user_message("a", (named,))])
        await store.save_images([stale, fresh])
        now: float = time.time()
        os.utime(images / image_file_name(stale), (now - 7200, now - 7200))
        found: list[Path] = await store.orphan_images(now, 3600)
        assert [path.name for path in found] == [image_file_name(stale)]
        await store.remove_images(found, now - 3600)
        assert _names(images) == {image_file_name(named), image_file_name(fresh)}
    finally:
        await store.close()


async def test_compact_gives_free_pages_back_and_later_deletes_keep_doing_so(tmp_path: Path) -> None:
    database: Path = tmp_path / "dot.db"
    store: Store = await Store.open(database, tmp_path / "images")
    try:
        threads: list[ThreadRecord] = [await store.create_thread(f"thread {number}", "test") for number in range(30)]
        for thread in threads:
            await store.append_messages(thread.id, [user_message("x" * 20_000, ())])
        await store.delete_threads([thread.id for thread in threads])
        assert _free_pages(database) > 0
        assert await store.reclaimable_bytes() > 0
        await store.compact()
        assert _free_pages(database) == 0
        assert await store.reclaimable_bytes() == 0
        late: ThreadRecord = await store.create_thread("late", "test")
        await store.append_messages(late.id, [user_message("y" * 200_000, ())])
        await store.delete_threads([late.id])
        assert _free_pages(database) == 0
    finally:
        await store.close()


async def test_a_screenshot_left_cut_short_under_its_name_is_written_again_whole(tmp_path: Path) -> None:
    folder: Path = tmp_path / "screenshots"
    store: Store = await Store.open(tmp_path / "dot.sqlite3", folder)
    try:
        image: ImageData = _image(b"the whole screenshot")
        # What a write stopped part way left under the image's name.
        (folder / image_file_name(image)).write_bytes(b"the whole")
        thread: ThreadRecord = await store.create_thread("t", "test")
        await store.append_messages(thread.id, [user_message("look", (image,))])
        assert (folder / image_file_name(image)).read_bytes() == b"the whole screenshot"
        assert _names(folder) == {image_file_name(image)}
    finally:
        await store.close()


async def test_only_messages_with_screenshots_take_the_places_of_the_screenshots_read_back(tmp_path: Path) -> None:
    """Item 144: a call whose arguments name a ``file`` has no screenshot, and does not keep the newest one from being
    read back."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("chat", "test")
        shot: ImageData = _image(b"the page")
        read: ToolCall = ToolCall(id="call_1", name="read_file", arguments={"file": "notes.txt"})
        await store.append_messages(thread.id, [user_message("look", (shot,)), assistant_message("", None, (read,))])
        history: list[Message] = await store.load_messages(thread.id, 1)
    finally:
        await store.close()
    assert [message.images for message in history] == [(shot,), ()]


async def test_a_chat_whose_delete_is_refused_keeps_its_screenshots(tmp_path: Path) -> None:
    """Item 183: a delete refused because a run works on the chat (item 179) removes nothing, its files neither."""
    images: Path = tmp_path / "images"
    store: Store = await Store.open(tmp_path / "dot.db", images)
    try:
        thread: ThreadRecord = await store.create_thread("at work", "test")
        await store.append_messages(thread.id, [user_message("a", (_image(b"of a chat at work"),))])
        run: RunRecord = await store.create_run(
            thread.id,
            "goal",
            "task",
            "ollama:x",
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        await store.add_event(run.id, "tool_result", {"images": await store.save_images([_image(b"of its run")])})
        _age(images, 7200)
        kept: set[str] = _names(images)
        with pytest.raises(RunConflict):
            await store.delete_threads([thread.id])
        assert _names(images) == kept
        assert (await store.get_thread(thread.id)).id == thread.id
    finally:
        await store.close()


async def test_deleting_a_chat_releases_its_files_after_the_lock_is_let_go_and_never_under_one_long_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 686: the files are released after the delete committed and the write lock is let go, the stored text read
    a question at a time, never in the one long read under the lock that ``referenced_images`` is (the owner's
    database is hundreds of megabytes)."""
    images: Path = tmp_path / "images"
    store: Store = await Store.open(tmp_path / "dot.db", images)
    try:
        first: ThreadRecord = await store.create_thread("first", "test")
        second: ThreadRecord = await store.create_thread("second", "test")
        own, shared = _image(b"only the first"), _image(b"in both")
        await store.append_messages(first.id, [user_message("a", (own, shared))])
        await store.append_messages(second.id, [user_message("b", (shared,))])
        _age(images, 7200)

        async def whole() -> set[str]:
            raise AssertionError("the whole database was read")

        held: list[bool] = []
        remove = store.remove_images

        async def removing(paths: Sequence[Path], unchanged_since: float) -> None:
            held.append(store._write_lock.locked())
            await remove(paths, unchanged_since)

        monkeypatch.setattr(store, "referenced_images", whole)
        monkeypatch.setattr(store, "remove_images", removing)
        assert await store.delete_threads([first.id]) == [first.id]
        assert held == [False]
        assert _names(images) == {image_file_name(shared)}
    finally:
        await store.close()


async def test_a_file_that_cannot_be_removed_after_a_committed_delete_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    images: Path = tmp_path / "images"
    store: Store = await Store.open(tmp_path / "dot.db", images)
    try:
        thread: ThreadRecord = await store.create_thread("a thread", "test")
        await store.append_messages(thread.id, [user_message("a", (_image(b"stuck"),))])
        _age(images, 7200)

        async def refusing(paths: Sequence[Path], unchanged_since: float) -> None:
            raise StoreError("some screenshot files could not be removed", {"files": ["x.png: Permission denied"]})

        monkeypatch.setattr(store, "remove_images", refusing)
        with capture_logs() as logs:
            assert await store.delete_threads([thread.id]) == [thread.id]
        assert [entry["event"] for entry in logs] == ["screenshot_files_not_removed"]
        assert logs[0]["files"] == ["x.png: Permission denied"]
        with pytest.raises(NotFoundError):
            await store.get_thread(thread.id)
    finally:
        await store.close()


async def test_a_file_an_event_past_the_first_part_names_is_kept_and_a_failed_read_keeps_them_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 686: the events are read a part at a time by their id; a file named only by an event of a later part is
    still named. A read that fails leaves every file and is logged: the delete is done."""
    monkeypatch.setattr(db, "_ROWS_PER_READ", 2)
    images: Path = tmp_path / "images"
    store: Store = await Store.open(tmp_path / "dot.db", images)
    try:
        doomed: ThreadRecord = await store.create_thread("doomed", "test")
        kept: ThreadRecord = await store.create_thread("kept", "test")
        own, shared = _image(b"only the doomed chat"), _image(b"named by a late event")
        await store.append_messages(doomed.id, [user_message("a", (own, shared))])
        run: RunRecord = await store.create_run(
            kept.id,
            "goal",
            "task",
            "ollama:x",
            None,
            "test",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        for number in range(5):
            await store.add_event(run.id, "note", {"text": f"event {number}"})
        await store.add_event(run.id, "tool_result", {"images": [image_file_name(shared)]})
        await store.finish_run(run.id, "done", "Done.")
        _age(images, 7200)
        assert await store.delete_threads([doomed.id]) == [doomed.id]
        assert _names(images) == {image_file_name(shared)}

        again: ThreadRecord = await store.create_thread("again", "test")
        await store.append_messages(again.id, [user_message("b", (_image(b"left after a failed read"),))])
        _age(images, 7200)
        read = store._all

        async def failing(sql: str, params: tuple[object, ...]) -> list[object]:
            if sql.startswith("SELECT body AS text FROM messages"):
                raise sqlite3.OperationalError("database is locked")
            return await read(sql, params)

        monkeypatch.setattr(store, "_all", failing)
        before: set[str] = _names(images)
        with capture_logs() as logs:
            assert await store.delete_threads([again.id]) == [again.id]
        assert "screenshot_files_not_released" in [entry["event"] for entry in logs]
        assert _names(images) == before
    finally:
        await store.close()


async def test_a_screenshot_whose_file_is_gone_is_left_out_and_the_chat_reads_back(tmp_path: Path) -> None:
    """Item 824: a folder deleted by mistake took screenshot files on 2026-10-22, and each run of their chats failed on
    "a stored screenshot file is missing". The message is read back without the screenshot, and the log says so."""
    images: Path = tmp_path / "images"
    store: Store = await Store.open(tmp_path / "dot.db", images)
    try:
        thread: ThreadRecord = await store.create_thread("chat", "test")
        gone, kept = _image(b"gone"), _image(b"kept")
        await store.append_messages(thread.id, [user_message("look", (gone, kept))])
        (images / image_file_name(gone)).unlink()
        with capture_logs() as logs:
            history: list[Message] = await store.load_messages(thread.id, 1)
    finally:
        await store.close()
    assert [message.images for message in history] == [(kept,)]
    assert [entry["event"] for entry in logs] == ["screenshot_missing"]
