"""Image helpers: downscale and JPEG-encode pictures before they go to a model."""

import base64
import io

from PIL import Image

from dotagent.core.errors import ToolExecutionError
from dotagent.core.types import FrozenModel
from dotagent.llm.types import ImageData


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
    except OSError as exc:
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
