"""Image helpers: downscale and JPEG-encode pictures before they go to a model."""

import base64
import io
import threading
import zlib

from PIL import Image

from dotagent.core.errors import ToolExecutionError
from dotagent.core.types import FrozenModel
from dotagent.llm.types import ImageData

# What Pillow raises for data it cannot read as a picture (item 515): an OSError (UnidentifiedImageError among them), a
# ValueError for a chunk that is cut short or a text that inflates past its limit (Pillow 13 raises it where 12 raised
# an OSError), and DecompressionBombError for a picture that says it is larger than Pillow will draw.
UNREADABLE_IMAGE: tuple[type[Exception], ...] = (OSError, ValueError, Image.DecompressionBombError)


class EncodedImage(FrozenModel):
    """A model-ready image together with the geometry needed to map its pixels back to the source."""

    image: ImageData
    width: int
    height: int
    source_width: int
    source_height: int


def _flatten(picture: Image.Image) -> Image.Image:
    if picture.mode in ("RGBA", "LA") or (picture.mode == "P" and "transparency" in picture.info):
        rgba: Image.Image = picture.convert("RGBA")
        background: Image.Image = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return picture.convert("RGB")


def encode_image(data: bytes, max_edge: int, quality: int) -> EncodedImage:
    """Decode ``data``, shrink so the longest edge is at most ``max_edge``, and re-encode as JPEG."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            source_width, source_height = opened.size
            picture: Image.Image = _flatten(opened)
    except UNREADABLE_IMAGE as exc:
        raise ToolExecutionError("the data is not a readable image", {"bytes": len(data), "error": str(exc)}) from exc
    longest: int = max(source_width, source_height)
    if longest > max_edge:
        ratio: float = max_edge / longest
        picture = picture.resize(
            (max(1, round(source_width * ratio)), max(1, round(source_height * ratio))), Image.Resampling.LANCZOS
        )
    buffer: io.BytesIO = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=quality, optimize=True)
    encoded: str = base64.b64encode(buffer.getvalue()).decode("ascii")
    return EncodedImage(
        image=ImageData(media_type="image/jpeg", data_b64=encoded),
        width=picture.width,
        height=picture.height,
        source_width=source_width,
        source_height=source_height,
    )


# The last JPEG :func:`render_jpeg` encoded, and the checksum of the pixels it came from. A screenshot of a screen
# that has not changed is the common case after a click that did nothing. Measured on a 3024×1964 frame shrunk to
# 1366×887 at quality 72: encoding took about 71 ms, and recognising the same frame took about 8 ms. A frame that
# changed pays the checksum on top of the encode. One slot: the screenshot just taken is the one a run takes again.
_jpeg_lock: threading.Lock = threading.Lock()
_jpeg_key: tuple[int, int, int, int, int, int] | None = None
_jpeg_bytes: bytes = b""


def render_jpeg(picture: Image.Image, width: int, height: int, quality: int) -> bytes:
    """``picture`` shrunk to ``width`` by ``height`` and encoded as JPEG. The same pixels, size and quality return
    the JPEG already encoded (:data:`_jpeg_key`)."""
    flat: Image.Image = picture if picture.mode == "RGB" else _flatten(picture)
    raw: bytes = flat.tobytes()
    key: tuple[int, int, int, int, int, int] = (
        zlib.crc32(raw),
        flat.width,
        flat.height,
        width,
        height,
        quality,
    )
    global _jpeg_key, _jpeg_bytes
    with _jpeg_lock:
        if key == _jpeg_key:
            return _jpeg_bytes
        fitted: Image.Image = flat
        if flat.size != (width, height):
            fitted = flat.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=3.0)
        buffer: io.BytesIO = io.BytesIO()
        # optimize would shrink the file and did not pay for itself here: a miss was slower, and a repeat
        # returns the bytes already encoded.
        fitted.save(buffer, format="JPEG", quality=quality, optimize=False)
        encoded: bytes = buffer.getvalue()
        _jpeg_key = key
        _jpeg_bytes = encoded
        return encoded


def jpeg_of_size(data: bytes, width: int, height: int, quality: int) -> bytes:
    """The JPEG ``data`` at ``width`` by ``height``: as it is when it has that size, else redrawn at it (a page Chrome
    draws larger or smaller than its CSS pixels, item 358, so that a pixel of the picture is one for ``click_at``)."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.size == (width, height):
                return data
            flat: Image.Image = _flatten(opened)
    except UNREADABLE_IMAGE as exc:
        raise ToolExecutionError("the data is not a readable image", {"bytes": len(data), "error": str(exc)}) from exc
    # Out of the reading: a size the caller got wrong is its own error, not unreadable data.
    return render_jpeg(flat, width, height, quality)
