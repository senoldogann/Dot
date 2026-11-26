"""An upload that arrives as the chunks of a body (item 1933): written to its file as they come, held to the limit of
its kind, and gone when it fails or is cut."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from dotagent.agent import attachments
from dotagent.agent.attachments import StoredAttachment, attachments_root, store_attachment_stream
from dotagent.core.errors import InvalidValueError

pytestmark = pytest.mark.macos


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def _left(workspace: Path) -> list[str]:
    root: Path = attachments_root(workspace)
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")) if root.exists() else []


async def test_a_video_is_written_to_its_file_as_each_chunk_comes(tmp_path: Path) -> None:
    first: bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 100_000
    second: bytes = b"\x02" * 5000
    seen: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        yield first
        # The first chunk is on the disk before the second is asked for: nothing waits for the whole body.
        seen.append(next(attachments_root(tmp_path).glob("*/clip.mp4")).stat().st_size)
        yield second

    stored: StoredAttachment = await store_attachment_stream(tmp_path, "clip.mp4", "video/mp4", chunks())
    assert seen == [len(first)]
    assert (stored.kind, stored.media_type, stored.size, stored.chars) == (
        "video",
        "video/mp4",
        len(first) + len(second),
        0,
    )
    assert await asyncio.to_thread(Path(stored.path).read_bytes) == first + second
    assert sorted(path.name for path in Path(stored.path).parent.iterdir()) == ["clip.mp4", "meta.json"]
    assert attachments.load_attachment(tmp_path, stored.id) == stored


async def test_a_video_past_its_limit_is_refused_at_the_chunk_that_passes_it_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(attachments, "MAX_MEDIA_BYTES", 1000)
    pulled: list[int] = []

    async def chunks() -> AsyncIterator[bytes]:
        for number in range(10):
            pulled.append(number)
            yield b"\x01" * 400

    with pytest.raises(InvalidValueError, match="too large"):
        await store_attachment_stream(tmp_path, "long.mp4", "video/mp4", chunks())
    assert pulled == [0, 1, 2]
    assert _left(tmp_path) == []


async def test_an_upload_that_is_cut_leaves_no_file_and_no_folder(tmp_path: Path) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"\x01" * 1000
        raise ConnectionResetError("the phone's link dropped")

    with pytest.raises(ConnectionResetError):
        await store_attachment_stream(tmp_path, "clip.mp4", "video/mp4", chunks())
    assert _left(tmp_path) == []


async def test_an_upload_that_is_cancelled_leaves_no_file_and_no_folder(tmp_path: Path) -> None:
    started: asyncio.Event = asyncio.Event()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"\x01" * 1000
        started.set()
        await asyncio.Event().wait()
        yield b""

    task: asyncio.Task[StoredAttachment] = asyncio.create_task(
        store_attachment_stream(tmp_path, "clip.mp4", "video/mp4", chunks())
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _left(tmp_path) == []


async def test_an_empty_body_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(InvalidValueError, match="empty"):
        await store_attachment_stream(tmp_path, "clip.mp4", "video/mp4", _chunks(b""))
    assert _left(tmp_path) == []


async def test_a_document_is_read_as_a_document_is_and_keeps_its_own_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: StoredAttachment = await store_attachment_stream(tmp_path, "notes.py", "", _chunks(b"X = 1\n", b"Y = 2\n"))
    assert (stored.kind, stored.size, stored.chars) == ("text", 12, 12)
    assert (Path(stored.path).parent / "text.txt").read_text(encoding="utf-8") == "X = 1\nY = 2\n"
    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", 100)
    with pytest.raises(InvalidValueError, match="too large"):
        await store_attachment_stream(tmp_path, "big.txt", "text/plain", _chunks(b"a" * 60, b"a" * 60))
    # A text file a browser calls a video is held to a document's limit once its first bytes show it is text.
    with pytest.raises(InvalidValueError, match="too large"):
        await store_attachment_stream(tmp_path, "app.ts", "video/mp2t", _chunks(b"export const x = 1;\n" * 10))
    assert [path.name for path in attachments_root(tmp_path).iterdir()] == [Path(stored.path).parent.name]
