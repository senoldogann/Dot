"""Watch's stream (item 1264): the size of a frame and a frame as it goes on the wire, which views/watch.js reads."""

import struct

from dotagent.desktop.live import DELTA, KEY, avc_message, frame_size


def test_a_frame_is_full_hd_at_most_never_more_than_the_display_and_even() -> None:
    # A MacBook's display: 1710 x 1112 points, 3420 x 2224 pixels.
    assert frame_size((1710.0, 1112.0), (3420, 2224), 1920) == (1920, 1248)
    # A display of fewer pixels than the limit is sent at its own.
    assert frame_size((1280.0, 800.0), (1280, 800), 1920) == (1280, 800)
    # H.264's 4:2:0 takes even sides only.
    width, height = frame_size((1001.0, 667.0), (1001, 667), 1920)
    assert width % 2 == 0 and height % 2 == 0


def test_a_key_frame_carries_its_configuration_and_a_delta_only_itself() -> None:
    key: bytes = avc_message(KEY, 1_500_000, b"\x01\x64\x00\x32", b"\x00\x00\x00\x01\x65")
    kind, length, micros = struct.unpack(">BIQ", key[:13])
    assert (kind, length, micros) == (KEY, 2 + 4 + 5, 1_500_000)
    assert key[13:] == b"\x00\x04" + b"\x01\x64\x00\x32" + b"\x00\x00\x00\x01\x65"
    delta: bytes = avc_message(DELTA, 1_516_666, b"", b"\x00\x00\x00\x01\x41")
    assert struct.unpack(">BIQ", delta[:13]) == (DELTA, 5, 1_516_666)
    assert delta[13:] == b"\x00\x00\x00\x01\x41"
