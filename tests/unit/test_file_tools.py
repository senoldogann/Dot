"""What the file tools make of a few inputs they read wrong before item 556: an empty text to replace, which edit_file
found between every two characters; an iPhone's photo (HEIC), which read_file could not draw; and a new file, which
write_file made private where any program makes it with the usual permission bits."""

import base64
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.core.errors import ToolInputError
from dotagent.tools.base import RegisteredTool, ToolResult
from dotagent.tools.files import _read_blocking, atomic_write, build_file_tools
from dotagent.undo.journal import JournalTarget

MIB: int = 1024 * 1024
# A 16 by 12 picture in HEIC, as macOS's ImageIO writes it: the format an iPhone keeps its photos in.
HEIC: bytes = base64.b64decode(
    "AAAAJGZ0eXBoZWljAAAAAG1pZjFNaVBybWlhZk1pSEJoZWljAAABhm1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAHBpY3QAAAAAAAAAAAAAAAAAAAAA"
    "JGRpbmYAAAAcZHJlZgAAAAAAAAABAAAADHVybCAAAAABAAAADnBpdG0AAAAAAAEAAAAjaWluZgAAAAAAAQAAABVpbmZlAgAAAAABAABodmMxAAAA"
    "AOZpcHJwAAAAxWlwY28AAAATY29scm5jbHgAAgACAAaAAAAADGNsbGkAywBAAAAAFGlzcGUAAAAAAAAAEAAAAAwAAAAJaXJvdAAAAAAQcGl4aQAA"
    "AAADCAgIAAAAcWh2Y0MBA3AAAACwAAAAAAAe8AD8/fj4AAALA6AAAQAXQAEMAf//A3AAAAMAsAAAAwAAAwAecCShAAEAI0IBAQNwAAADALAAAAMA"
    "AAMAHqAUIEHAkw3iHuRZVNwICBgCogABAAlEAcBhcshAUyQAAAAZaXBtYQAAAAAAAAABAAEGgQIDBYaEAAAAHmlsb2MAAAAARAAAAQABAAAAAQAA"
    "AboAAAA+AAAAAW1kYXQAAAAAAAAATgAAADooAa+i+kaBfP/9iT//8cfsvuY59V/+o58j2L8v98RPdMsmf6Ag+cI52SAQ12G/yrQIvYV+CtwhJsiu"
)
# A 16 by 12 picture stored as it is and turned a quarter on show, as an iPhone stores a photo taken upright.
HEIC_ON_ITS_SIDE: bytes = base64.b64decode(
    "AAAAJGZ0eXBoZWljAAAAAG1pZjFNaVBybWlhZk1pSEJoZWljAAABw21ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAHBpY3QAAAAAAAAAAAAAAAAAAAAAJGRp"
    "bmYAAAAcZHJlZgAAAAAAAAABAAAADHVybCAAAAABAAAADnBpdG0AAAAAAAEAAAA4aWluZgAAAAAAAgAAABVpbmZlAgAAAAABAABodmMxAAAAABVpbmZl"
    "AgAAAQACAABFeGlmAAAAABppcmVmAAAAAAAAAA5jZHNjAAIAAQABAAAA5mlwcnAAAADFaXBjbwAAABNjb2xybmNseAACAAIABoAAAAAMY2xsaQDLAEAA"
    "AAAUaXNwZQAAAAAAAAAQAAAADAAAAAlpcm90AwAAABBwaXhpAAAAAAMICAgAAABxaHZjQwEDcAAAALAAAAAAAB7wAPz9+PgAAAsDoAABABdAAQwB//8D"
    "cAAAAwCwAAADAAADAB5wJKEAAQAjQgEBA3AAAAMAsAAAAwAAAwAeoBQgQcCTDeIe5FlU3AgIGAKiAAEACUQBwGFyyEBTJAAAABlpcG1hAAAAAAAAAAEA"
    "AQaBAgMFhoQAAAAsaWxvYwAAAABEAAACAAEAAAABAAACGwAAAEkAAgAAAAEAAAH3AAAAJAAAAAFtZGF0AAAAAAAAAH0AAAAGRXhpZgAATU0AKgAAAAgA"
    "AQESAAMAAAABAAYAAAAAAAAAAABFKAGvo1kaYiP/EL/4f2WCprf3aDTT0H3Yw//+Z2X2/07SZMVt//MMlf+rY+PTqqyhiGg9Yxqnv9yft+Hkv7q1u4LZ"
    "XrnA"
)


def _journal(tmp_path: Path) -> JournalTarget:
    return JournalTarget(directory=tmp_path / "undo", tool="write_file", max_file_bytes=MIB, max_run_bytes=MIB)


def _umask() -> int:
    current: int = os.umask(0o022)
    os.umask(current)
    return current


async def test_an_empty_text_to_replace_is_refused_before_anything_runs() -> None:
    tool: RegisteredTool = next(tool for tool in build_file_tools() if tool.spec.name == "edit_file")
    with pytest.raises(ToolInputError, match="old"):
        await tool.prepare({"path": "a.txt", "old": "", "new": "X", "replace_all": True}, SimpleNamespace())  # type: ignore[arg-type]


def test_a_heic_photo_is_read_as_an_image(tmp_path: Path) -> None:
    photo: Path = tmp_path / "IMG_0001.HEIC"
    photo.write_bytes(HEIC)
    read: ToolResult = _read_blocking(photo, 1, 100, 10_000, 1568, 80)
    assert (read.is_error, read.text, len(read.images)) == (False, "Image IMG_0001.HEIC: 16x12px", 1)


def test_a_heic_photo_stored_on_its_side_is_read_as_it_is_held(tmp_path: Path) -> None:
    photo: Path = tmp_path / "IMG_0002.HEIC"
    photo.write_bytes(HEIC_ON_ITS_SIDE)
    read: ToolResult = _read_blocking(photo, 1, 100, 10_000, 1568, 80)
    assert (read.is_error, read.text, len(read.images)) == (False, "Image IMG_0002.HEIC: 12x16px", 1)


def test_a_new_file_gets_the_usual_permission_bits_and_a_file_that_was_there_keeps_its_own(tmp_path: Path) -> None:
    made: Path = tmp_path / "site" / "index.html"
    atomic_write(made, "<p>Hello</p>\n", _journal(tmp_path))
    kept: Path = tmp_path / "private.txt"
    kept.write_text("one\n", encoding="utf-8")
    kept.chmod(0o600)
    atomic_write(kept, "two\n", _journal(tmp_path))
    assert stat.S_IMODE(made.stat().st_mode) == 0o666 & ~_umask()
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert sorted(entry.name for entry in made.parent.iterdir()) == ["index.html"]
