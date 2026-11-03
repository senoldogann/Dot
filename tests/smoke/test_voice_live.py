"""The program that writes down what is said, for real (items 626, 969): the daemon builds ``transcribe.swift`` with
this Mac's own Swift compiler and writes down what `say` recorded, in Turkish and in English, streamed to it in pieces
as a page streams them while the owner speaks, with macOS 26's dictation model on the device; the words come a moment
after the stream ends. It is run by hand on a Mac with macOS 26 or later and Apple's developer tools (``-m live``), and
skipped where either is missing, and where `say` has no voice of the language.

It uses no microphone: the recordings are made with `say`, which speaks into a file. The first use of a language may
download its model from Apple, which is why this is a `live` test and not part of the gate."""

import asyncio
import difflib
import os
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path

import pytest

from dotagent.core.errors import VoiceError
from dotagent.voice.helper import Transcriber
from dotagent.voice.toolchain import look_for_compiler
from dotagent.voice.wav import RECORDING_RATE

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

_SAY: str = "/usr/bin/say"
# What each voice says, and the language it is written down in. Spoken as a person speaks a sentence, with its commas.
_SENTENCES: tuple[tuple[str, str, str], ...] = (
    ("Yelda", "tr-TR", "Merhaba, bugün hava çok güzel. Yarın toplantıya gidiyorum."),
    (
        "Samantha",
        "en-US",
        "Hello there, how are you today? The weather is cold today and I would like to go for a walk.",
    ),
)
# How much of what was said must come back: the dictation model may write a word another way, and no more.
_LIKE: float = 0.9
# A quarter of a second, as a page sends, and how long the words may take after the stream ends: it wrote them down
# meanwhile (0.04 s to 0.08 s measured after 12 s of Turkish; a language's first use may download its model).
_PIECE_BYTES: int = RECORDING_RATE // 2
_AFTER_THE_END_S: float = 2.0


@pytest.fixture(autouse=True)
def real_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """This Mac's own compiler, not the smoke tests' missing one (``MISSING_SWIFTC``); the test is skipped where this
    Mac cannot build the program (macOS before 26, no developer tools)."""
    monkeypatch.delenv("DOT_SWIFTC", raising=False)
    found = look_for_compiler()
    if found.compiler is None:
        pytest.skip(f"this Mac cannot build the program that writes recordings down: {found.why}")


def _said(voice: str, sentence: str, folder: Path) -> bytes:
    """What `voice` says of `sentence`, recorded as 16 kHz mono 16-bit samples, as a page streams them."""
    if shutil.which(_SAY) is None:
        pytest.skip("say is not on this Mac")
    file: Path = folder / f"{voice}.wav"
    done = subprocess.run(
        [_SAY, "-v", voice, "-o", str(file), f"--data-format=LEI16@{RECORDING_RATE}", sentence],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip(f"say has no voice {voice} on this Mac: {done.stderr.strip()}")
    with wave.open(str(file)) as recorded:
        return recorded.readframes(recorded.getnframes())


async def _streamed(transcriber: Transcriber, pcm: bytes, language: str) -> tuple[str, float]:
    """The words of ``pcm`` streamed in ``language`` a quarter of a second at a time, at the pace it was spoken, and
    how long they took after the stream ended."""
    name: str = await transcriber.open(language)
    for number, at in enumerate(range(0, len(pcm), _PIECE_BYTES)):
        await transcriber.feed(name, number, pcm[at : at + _PIECE_BYTES])
        await asyncio.sleep(0.25)
    ended: float = time.monotonic()
    words: str = await transcriber.end(name)
    return words, time.monotonic() - ended


def _words(text: str) -> list[str]:
    """The words of `text` in lower case, with no punctuation: Turkish writes none and English does."""
    return re.findall(r"\w+", text.casefold())


async def test_the_real_program_writes_down_what_say_recorded_in_turkish_and_in_english(tmp_path: Path) -> None:
    # A folder with a space in its name, as Dot's own home has ("Application Support").
    transcriber: Transcriber = Transcriber(tmp_path / "Application Support" / "voice")
    assert (await transcriber.ability()).transcribe
    for voice, language, sentence in _SENTENCES:
        # Once to have the language's model, once timed.
        await _streamed(transcriber, _said(voice, sentence, tmp_path), language)
        written, took = await _streamed(transcriber, _said(voice, sentence, tmp_path), language)
        print(f"{language}: {written!r} {took:.2f} s after the end")
        like: float = difflib.SequenceMatcher(None, _words(sentence), _words(written)).ratio()
        assert like >= _LIKE, f"{language}: said {sentence!r}, written down as {written!r}"
        assert took < _AFTER_THE_END_S, f"{language}: the words took {took:.2f} s after the end"
    # Built once for both; no sound is left behind.
    voice_folder: Path = tmp_path / "Application Support" / "voice"
    names: list[str] = await asyncio.to_thread(os.listdir, voice_folder)
    assert [name.startswith("build-") for name in names] == [True]


async def test_the_real_program_says_what_it_cannot_write_down(tmp_path: Path) -> None:
    transcriber: Transcriber = Transcriber(tmp_path / "voice")
    said: bytes = _said("Samantha", "Hello there.", tmp_path)
    with pytest.raises(VoiceError, match="cannot write down xx-YY") as unsupported:
        await _streamed(transcriber, said, "xx-YY")
    assert unsupported.value.context["code"] == "unsupported-language"
    # A silence is no error: it is written down as nothing.
    assert (await _streamed(transcriber, bytes(RECORDING_RATE * 2 * 2), "en-US"))[0] == ""
