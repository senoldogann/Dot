"""The sound the page streams to the Mac (items 626, 969): 16 kHz mono 16-bit little-endian samples, five minutes at the
most, and the silence the daemon puts after them before the program's input ends. Measured on this Mac, the dictation
model drops the last word of a recording that ends as the word does, and writes it once 200 milliseconds of silence
follow."""

# What the page records, and how long a recording may be.
RECORDING_RATE: int = 16_000
MAX_RECORDING_SECONDS: int = 300
# The silence that goes after the samples, so that the last word is written: well past the 200 ms that measured enough.
TAIL_SECONDS: float = 0.8
# The most a piece the page sends may hold: two seconds of sound (it sends one about every quarter of a second, and
# what waited while one was on its way goes in the next).
MAX_PIECE_BYTES: int = 2 * RECORDING_RATE * 2
