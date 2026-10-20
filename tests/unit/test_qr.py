"""The QR codes of Settings › Phone (item 421): macOS's own generator makes them, and its detector reads back what they
open."""

import subprocess
import sys

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


_CONCURRENT_CODES = """
import threading

from dotagent.qr import qr_png

THREADS = 12
barrier = threading.Barrier(THREADS)
failures: list[str] = []


def work() -> None:
    barrier.wait()
    try:
        qr_png("https://mac.tail1234.ts.net/")
    except Exception as error:
        failures.append(repr(error))


threads = [threading.Thread(target=work) for _ in range(THREADS)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
print(failures)
"""


def test_two_codes_made_at_once_on_worker_threads_do_not_fail() -> None:
    """Settings › Phone asks for two codes together: PyObjC's lazy binding of a Core Graphics function raised a KeyError
    for the second thread that read it unbound. A fresh interpreter, so no name is bound yet."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", _CONCURRENT_CODES], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"
