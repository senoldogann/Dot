"""An unchanged screenshot is not encoded again."""

from PIL import Image

from dotagent.core.images import render_jpeg


def test_the_same_frame_returns_the_jpeg_already_encoded() -> None:
    picture: Image.Image = Image.new("RGB", (64, 32), (12, 24, 36))
    first: bytes = render_jpeg(picture, 32, 16, 60)
    second: bytes = render_jpeg(picture, 32, 16, 60)
    assert second is first
    assert first.startswith(b"\xff\xd8")
    changed: Image.Image = picture.copy()
    changed.putpixel((0, 0), (255, 0, 0))
    third: bytes = render_jpeg(changed, 32, 16, 60)
    assert third is not first
    assert third.startswith(b"\xff\xd8")
