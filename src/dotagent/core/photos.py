"""The photos of an iPhone, which Pillow does not read (HEIC and HEIF): macOS's ImageIO draws them as PNG first (items
556 and 806), for the file tools and for a picture the owner sends on a channel."""

from Foundation import NSData, NSMutableData
from Quartz import (
    CGImageDestinationAddImage,
    CGImageDestinationCreateWithData,
    CGImageDestinationFinalize,
    CGImageSourceCreateThumbnailAtIndex,
    CGImageSourceCreateWithData,
    kCGImageSourceCreateThumbnailFromImageAlways,
    kCGImageSourceCreateThumbnailWithTransform,
)

MACOS_IMAGE_SUFFIXES: frozenset[str] = frozenset({".heic", ".heif"})
_PNG_TYPE: str = "public.png"
# What ImageIO is asked for a photo: the image itself, not the small one stored beside it, with no limit on its size,
# turned as its orientation says.
_DRAWN_FULL_SIZE_TURNED: dict[str, bool] = {
    kCGImageSourceCreateThumbnailFromImageAlways: True,
    kCGImageSourceCreateThumbnailWithTransform: True,
}


def drawn_by_macos(data: bytes) -> bytes:
    """A HEIC or HEIF photo as PNG, drawn by macOS's ImageIO at its full size and turned the way it is held (an
    iPhone's portrait photos are stored on their side, with the turn kept beside them, which the PNG would lose);
    ``data`` as it is when macOS cannot read it either, for :func:`~dotagent.core.images.encode_image` to say so."""
    source = CGImageSourceCreateWithData(NSData.dataWithBytes_length_(data, len(data)), None)
    image = CGImageSourceCreateThumbnailAtIndex(source, 0, _DRAWN_FULL_SIZE_TURNED) if source is not None else None
    if image is None:
        return data
    drawn = NSMutableData.data()
    destination = CGImageDestinationCreateWithData(drawn, _PNG_TYPE, 1, None)
    if destination is None:
        return data
    CGImageDestinationAddImage(destination, image, None)
    return bytes(drawn) if CGImageDestinationFinalize(destination) else data
