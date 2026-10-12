"""Screenshot files follow the chats that own them; what nothing names any more is found and removed on request, and the
pages a delete frees go back to the disk once the file is compacted."""

import base64
import os
import sqlite3
import time
from pathlib import Path

from dotagent.llm.types import ImageData, user_message
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
            first.id, "goal", "task", "ollama:x", None, "test", "medium", "ask", False, None, None
        )
        await store.add_event(run.id, "tool_result", {"images": await store.save_images([from_event])})
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
