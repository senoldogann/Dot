"""Item 515: data Pillow cannot read as a picture is a picture that is not readable, whatever Pillow raises for it: a
text chunk that inflates past its limit and a chunk cut short (ValueError), a picture larger than Pillow draws
(DecompressionBombError); the run is told so, as it is of any other unreadable data, and its tool does not fail on an
error Dot did not expect."""

import struct
import zlib

import pytest

from dotagent.core.errors import ToolExecutionError
from dotagent.core.images import encode_image, jpeg_of_size

_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _png(width: int, height: int, *extra: bytes) -> bytes:
    """An 8-bit RGB PNG of ``width`` by ``height`` black pixels, with the ``extra`` chunks before its pixels."""
    pixels: bytes = zlib.compress(b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(min(height, 8))))
    header: bytes = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return _SIGNATURE + _chunk(b"IHDR", header) + b"".join(extra) + _chunk(b"IDAT", pixels) + _chunk(b"IEND", b"")


_UNREADABLE: dict[str, bytes] = {
    "a text that inflates past Pillow's limit": _png(
        4, 4, _chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"a" * (2 * 1024 * 1024)))
    ),
    "a transparency chunk cut short": _png(4, 4, _chunk(b"tRNS", b"\x00\x01")),
    "a picture larger than Pillow draws": _png(30_000, 30_000),
}


@pytest.mark.parametrize("data", list(_UNREADABLE.values()), ids=list(_UNREADABLE))
def test_data_pillow_cannot_read_is_a_picture_that_is_not_readable(data: bytes) -> None:
    with pytest.raises(ToolExecutionError, match="the data is not a readable image"):
        encode_image(data, 1568, 80)
    with pytest.raises(ToolExecutionError, match="the data is not a readable image"):
        jpeg_of_size(data, 10, 10, 80)


def test_a_picture_pillow_reads_is_encoded() -> None:
    encoded = encode_image(_png(4, 4), 1568, 80)
    assert (encoded.image.media_type, encoded.width, encoded.height) == ("image/jpeg", 4, 4)
