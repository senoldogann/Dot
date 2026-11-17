"""A video or a sound attached to a message (item 1213): kept as it came, larger than a document may be, and the run is
told to watch it with watch_video."""

from pathlib import Path

import pytest

from dotagent.agent.attachments import (
    MAX_ATTACHMENT_BYTES,
    StoredAttachment,
    attachments_text,
    media_kind,
    store_attachment,
)
from dotagent.core.errors import InvalidValueError

# The head of a Core Audio file, as an iPhone's voice message starts.
_CAF: bytes = b"caff\x00\x01\x00\x00desc" + b"\x00" * 40


def test_videos_and_sounds_are_told_by_their_type_or_their_name() -> None:
    assert media_kind("IMG_0042.MOV", "") == "video"
    assert media_kind("clip", "video/mp4") == "video"
    assert media_kind("Audio Message.caf", "") == "audio"
    assert media_kind("memo", "audio/x-m4a") == "audio"
    assert media_kind("notes.pdf", "application/pdf") is None


def test_a_video_is_kept_as_it_came_and_the_run_is_told_to_watch_it(tmp_path: Path) -> None:
    # Past the limit of a document: a minute of an iPhone's video is tens of megabytes.
    data: bytes = b"\x00" * (MAX_ATTACHMENT_BYTES + 1)
    stored: StoredAttachment = store_attachment(tmp_path, "IMG_0042.MOV", "video/quicktime", data)
    assert (stored.kind, stored.media_type, stored.size, stored.chars) == ("video", "video/quicktime", len(data), 0)
    assert Path(stored.path).read_bytes() == data
    assert sorted(path.name for path in Path(stored.path).parent.iterdir()) == ["IMG_0042.MOV", "meta.json"]
    told: str = attachments_text([stored])
    assert 'kind="video"' in told and "watch_video on the path above" in told and "name the language spoken" in told


def test_a_voice_message_is_kept_as_a_sound(tmp_path: Path) -> None:
    stored: StoredAttachment = store_attachment(tmp_path, "Audio Message.caf", "audio/caf", _CAF)
    assert stored.kind == "audio"
    assert "a sound or a voice message: watch_video" in attachments_text([stored])


def test_a_document_keeps_its_own_limit(tmp_path: Path) -> None:
    with pytest.raises(InvalidValueError, match="too large"):
        store_attachment(tmp_path, "big.txt", "text/plain", b"a" * (MAX_ATTACHMENT_BYTES + 1))


def test_a_text_file_a_browser_calls_a_video_is_read_as_text(tmp_path: Path) -> None:
    # A browser calls a TypeScript file video/mp2t: its first bytes read as text, so it is no video.
    stored: StoredAttachment = store_attachment(tmp_path, "app.ts", "video/mp2t", b"export const x = 1;\n")
    assert stored.kind == "text"
    assert "export const x = 1;" in attachments_text([stored])
