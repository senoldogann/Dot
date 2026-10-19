"""The QR codes of Settings › Phone (item 421): macOS's own generator makes them, and its detector reads back what they
open."""

import Quartz

from dotagent.qr import qr_png


def _read(png: bytes) -> list[str]:
    detector = Quartz.CIDetector.detectorOfType_context_options_(
        Quartz.CIDetectorTypeQRCode, None, {Quartz.CIDetectorAccuracy: Quartz.CIDetectorAccuracyHigh}
    )
    return [str(feature.messageString()) for feature in detector.featuresInImage_(Quartz.CIImage.imageWithData_(png))]


def test_a_qr_code_reads_back_as_the_address_it_was_made_of() -> None:
    for address in ("https://tailscale.com/download", "https://mac.tail1234.ts.net/"):
        png: bytes = qr_png(address)
        assert png.startswith(b"\x89PNG")
        assert _read(png) == [address]
