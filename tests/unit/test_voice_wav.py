"""The recording the page sends (item 626) is read into a model before anything is done with it: only 16-bit mono sound
of five minutes at the most gets through, whatever chunks the WAV has around it, and what Apple's audio code is given is
a file of Dot's own making, with a stretch of silence after the sound so that the last word is written."""

import base64
import json
import shutil
import struct
import subprocess

import pytest

from dotagent.core.errors import InvalidValueError
from dotagent.server.app import STATIC_DIR
from dotagent.voice.wav import (
    MAX_RECORDING_BYTES,
    MAX_RECORDING_SECONDS,
    RECORDING_RATE,
    TAIL_SECONDS,
    Recording,
    canonical_wav,
    read_wav,
)


def _fmt(rate: int = RECORDING_RATE, channels: int = 1, bits: int = 16, tag: int = 1) -> bytes:
    return b"fmt " + struct.pack("<IHHIIHH", 16, tag, channels, rate, rate * channels * bits // 8, 2, bits)


def _chunk(name: bytes, body: bytes) -> bytes:
    """A chunk with its length, and the byte that pads one of odd length."""
    return name + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")


def _wav(*chunks: bytes) -> bytes:
    body: bytes = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _samples(count: int) -> bytes:
    return b"".join(struct.pack("<h", (index * 37) % 2000 - 1000) for index in range(count))


def test_a_wav_of_the_form_the_page_writes_is_read_into_its_samples() -> None:
    pcm: bytes = _samples(1600)
    recording: Recording = read_wav(_wav(_fmt(), _chunk(b"data", pcm)))
    assert recording.rate == RECORDING_RATE
    assert recording.pcm == pcm
    assert recording.seconds == pytest.approx(0.1)


def test_chunks_around_the_sound_are_passed_over_as_macos_writes_them() -> None:
    """`say` writes a JUNK chunk before the format and a player may add a LIST after the sound; an odd-sized chunk is
    followed by a byte that pads it."""
    pcm: bytes = _samples(800)
    wav: bytes = _wav(
        _chunk(b"JUNK", bytes(28)), _chunk(b"odd ", b"abc"), _fmt(), _chunk(b"data", pcm), _chunk(b"LIST", b"INFO")
    )
    assert read_wav(wav).pcm == pcm


def test_a_sound_chunk_that_says_it_is_longer_than_the_bytes_there_are_is_read_as_far_as_it_goes() -> None:
    """A file cut short, or one written as it came in, says a size past its end; a half sample at the end goes."""
    pcm: bytes = _samples(400)
    cut: bytes = _wav(_fmt()) + struct.pack("<4sI", b"data", 0xFFFFFFFF) + pcm + b"\x01"
    assert read_wav(cut).pcm == pcm


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"hello",
        b"RIFF\0\0\0\0AVI LIST",
        b"RIFX\0\0\0\0WAVE",
        b"audio/webm;codecs=opus",
        _wav(_fmt()),
        _chunk(b"data", b"\0\0"),
    ],
)
def test_bytes_that_are_no_wav_with_sound_in_them_are_refused(data: bytes) -> None:
    with pytest.raises(InvalidValueError):
        read_wav(data)


@pytest.mark.parametrize(
    "format_chunk",
    [
        _fmt(channels=2),
        _fmt(bits=8),
        _fmt(bits=24),
        _fmt(tag=3, bits=32),
        _fmt(tag=0xFFFE),
        _fmt(rate=96_000),
        _fmt(rate=4_000),
        _fmt(rate=0),
        b"fmt " + struct.pack("<IHH", 4, 1, 1),
    ],
)
def test_a_recording_that_is_not_16_bit_mono_sound_is_refused(format_chunk: bytes) -> None:
    with pytest.raises(InvalidValueError):
        read_wav(_wav(format_chunk, _chunk(b"data", _samples(100))))


def test_sound_before_its_format_is_refused_and_an_empty_recording_too() -> None:
    with pytest.raises(InvalidValueError, match="format comes after"):
        read_wav(_wav(_chunk(b"data", _samples(100)), _fmt()))
    with pytest.raises(InvalidValueError, match="empty"):
        read_wav(_wav(_fmt(), _chunk(b"data", b"")))
    with pytest.raises(InvalidValueError, match="empty"):
        read_wav(_wav(_fmt(), _chunk(b"data", b"\x01")))


def test_five_minutes_are_the_most_and_a_wav_of_that_is_inside_the_byte_limit() -> None:
    five: bytes = bytes(MAX_RECORDING_SECONDS * RECORDING_RATE * 2)
    wav: bytes = _wav(_fmt(), _chunk(b"data", five))
    assert len(wav) <= MAX_RECORDING_BYTES
    assert read_wav(wav).seconds == MAX_RECORDING_SECONDS
    with pytest.raises(InvalidValueError, match="longer than five minutes") as refused:
        read_wav(_wav(_fmt(), _chunk(b"data", five + bytes(RECORDING_RATE * 2))))
    assert refused.value.context == {"seconds": MAX_RECORDING_SECONDS + 1, "most": MAX_RECORDING_SECONDS}


def test_a_header_that_lists_chunks_without_end_is_given_up_on() -> None:
    """A WAV has a handful of chunks: a file of thousands is read no further than a few dozen."""
    noise: bytes = b"".join(_chunk(b"JUNK", b"x") for _ in range(2000))
    with pytest.raises(InvalidValueError, match="no sound"):
        read_wav(_wav(noise, _fmt(), _chunk(b"data", _samples(100))))


def test_what_goes_to_the_program_is_a_header_of_dots_own_and_the_samples_and_silence_after_them() -> None:
    pcm: bytes = _samples(1600)
    # Whatever the file came in, the program is given the standard 44-byte header.
    recording: Recording = read_wav(
        _wav(_chunk(b"JUNK", bytes(28)), _fmt(), _chunk(b"data", pcm), _chunk(b"LIST", b"x"))
    )
    made: bytes = canonical_wav(recording)
    tail: int = round(TAIL_SECONDS * RECORDING_RATE) * 2
    assert made[:4] == b"RIFF" and made[8:16] == b"WAVEfmt "
    assert struct.unpack_from("<IHHIIHH", made, 16) == (16, 1, 1, RECORDING_RATE, RECORDING_RATE * 2, 2, 16)
    assert made[36:40] == b"data"
    assert struct.unpack_from("<I", made, 4)[0] == len(made) - 8
    assert struct.unpack_from("<I", made, 40)[0] == len(pcm) + tail
    assert made[44:] == pcm + bytes(tail)
    # And it reads back as what it is: the samples, then the silence.
    assert read_wav(made) == Recording(rate=RECORDING_RATE, pcm=pcm + bytes(tail))
    assert canonical_wav(recording, tail_seconds=0)[44:] == pcm


def test_a_wav_the_page_writes_is_read_by_the_daemon_as_the_samples_it_was_made_of() -> None:
    """The page's wav.js is run in node: its header, its rate and its five minutes are the daemon's own."""
    node: str | None = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the page's WAV cannot be made here")
    module: str = (STATIC_DIR / "js" / "wav.js").as_uri()
    script: str = (
        f"import {{ wavOf, base64Of, RECORDING_RATE, MAX_RECORDING_S }} from '{module}';"
        "const samples = Int16Array.from([0, 1, -1, 32767, -32768, 1234, -4321]);"
        "const wav = base64Of(wavOf(samples));"
        "console.log(JSON.stringify({ wav, rate: RECORDING_RATE, seconds: MAX_RECORDING_S }));"
    )
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    made: dict[str, object] = json.loads(done.stdout)
    assert isinstance(made["wav"], str)
    recording: Recording = read_wav(base64.b64decode(made["wav"]))
    assert recording.rate == RECORDING_RATE == made["rate"]
    assert struct.unpack("<7h", recording.pcm) == (0, 1, -1, 32767, -32768, 1234, -4321)
    assert made["seconds"] == MAX_RECORDING_SECONDS
