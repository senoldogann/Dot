"""The recording the page sends (item 626): a WAV, read into a model, and written out again in the one form the program
that writes it down is given.

The page writes 16 kHz mono 16-bit samples. The bytes are read here, chunk by chunk, so that only a recording of that
kind gets through, five minutes long at the most, and nothing of the container a client sent reaches Apple's audio code:
the samples are written again behind a header of Dot's own (:func:`canonical_wav`), with a stretch of silence after
them. Measured on this Mac, the dictation model drops the last word of a recording that ends as the word does, and
writes it once 200 milliseconds of silence follow."""

import struct

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel

# What the page writes, how long a recording may be, and the most bytes it may take (about 10 MB: five minutes of what
# the page writes, with room for a header that has chunks of its own).
RECORDING_RATE: int = 16_000
MAX_RECORDING_SECONDS: int = 300
MAX_RECORDING_BYTES: int = 10_000_000
# The sample rates the model converts by itself; the page's own is RECORDING_RATE.
_RATES: range = range(8_000, 48_001)
_BYTES_PER_SAMPLE: int = 2
# The silence that goes after the samples, so that the last word is written: well past the 200 ms that measured enough.
TAIL_SECONDS: float = 0.8
_PCM: int = 1
_MONO: int = 1
_BITS: int = 16
_FMT_BYTES: int = 16
_RIFF_BYTES: int = 12
_CHUNK_HEAD_BYTES: int = 8
# A WAV has a handful of chunks; one with dozens is no recording of the page's.
_MOST_CHUNKS: int = 32


class Recording(FrozenModel):
    """The samples of a recording: ``pcm`` is 16-bit little-endian mono sound at ``rate`` samples a second."""

    rate: int
    pcm: bytes

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (self.rate * _BYTES_PER_SAMPLE)


def _rate_of(format_chunk: bytes) -> int:
    """The sample rate of a ``fmt `` chunk that says 16-bit mono PCM; anything else is refused."""
    if len(format_chunk) < _FMT_BYTES:
        raise InvalidValueError("the recording's format is cut short", {"bytes": len(format_chunk)})
    fields: tuple[int, ...] = struct.unpack_from("<HHIIHH", format_chunk)
    tag, channels, rate, _, _, bits = fields
    if tag != _PCM or channels != _MONO or bits != _BITS:
        raise InvalidValueError(
            "the recording is not 16-bit mono sound, which is what Dot's page records",
            {"format": tag, "channels": channels, "bits": bits},
        )
    if rate not in _RATES:
        raise InvalidValueError("the recording's sample rate is not one Dot reads", {"rate": rate})
    return rate


def _recording(rate: int, pcm: bytes) -> Recording:
    """The recording of ``pcm`` (whole samples), none that is empty or longer than five minutes."""
    whole: bytes = pcm[: len(pcm) - len(pcm) % _BYTES_PER_SAMPLE]
    if whole == b"":
        raise InvalidValueError("the recording is empty", {})
    recording: Recording = Recording(rate=rate, pcm=whole)
    if recording.seconds > MAX_RECORDING_SECONDS:
        raise InvalidValueError(
            "the recording is longer than five minutes",
            {"seconds": round(recording.seconds), "most": MAX_RECORDING_SECONDS},
        )
    return recording


def read_wav(data: bytes) -> Recording:
    """The sound of the WAV file ``data``: ``InvalidValueError`` for bytes that are no WAV, a recording that is not
    16-bit mono sound, one that is empty and one longer than five minutes. A sound chunk that says it is longer than the
    bytes there are (a cut-short file, or one written as it came in) is read as far as it goes."""
    if len(data) < _RIFF_BYTES or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise InvalidValueError("the recording is not a WAV file", {"bytes": len(data)})
    rate: int | None = None
    position: int = _RIFF_BYTES
    for _ in range(_MOST_CHUNKS):
        if position + _CHUNK_HEAD_BYTES > len(data):
            break
        name: bytes = data[position : position + 4]
        size: int = struct.unpack_from("<I", data, position + 4)[0]
        start: int = position + _CHUNK_HEAD_BYTES
        if name == b"fmt ":
            rate = _rate_of(data[start : start + size])
        elif name == b"data":
            if rate is None:
                raise InvalidValueError("the recording's format comes after its sound", {})
            return _recording(rate, data[start : start + size])
        # A chunk of odd length is followed by a byte that pads it.
        position = start + size + (size & 1)
    raise InvalidValueError("the recording has no sound in it", {})


def canonical_wav(recording: Recording, tail_seconds: float = TAIL_SECONDS) -> bytes:
    """``recording`` as a WAV file of Dot's own making: the standard 44-byte header, the samples, and ``tail_seconds``
    of silence."""
    pcm: bytes = recording.pcm + bytes(round(tail_seconds * recording.rate) * _BYTES_PER_SAMPLE)
    header: bytes = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        _FMT_BYTES,
        _PCM,
        _MONO,
        recording.rate,
        recording.rate * _BYTES_PER_SAMPLE,
        _BYTES_PER_SAMPLE,
        _BITS,
        b"data",
        len(pcm),
    )
    return header + pcm
