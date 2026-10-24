"""The program that writes a recording down, for real (item 626): the daemon builds ``transcribe.swift`` with this
Mac's own Swift compiler and writes down what `say` recorded, in Turkish and in English, with macOS 26's dictation
model on the device. It is run by hand on a Mac with macOS 26 or later and Apple's developer tools (``-m live``), and
skipped where either is missing, and where `say` has no voice of the language.

It uses no microphone: the recordings are made with `say`, which speaks into a file. The first use of a language may
download its model from Apple, which is why this is a `live` test and not part of the gate."""

import difflib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dotagent.core.errors import VoiceError
from dotagent.voice.helper import Transcriber
from dotagent.voice.toolchain import look_for_compiler
from dotagent.voice.wav import RECORDING_RATE, Recording, read_wav

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


@pytest.fixture(autouse=True)
def real_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """This Mac's own compiler, not the smoke tests' missing one (``MISSING_SWIFTC``); the test is skipped where this
    Mac cannot build the program (macOS before 26, no developer tools)."""
    monkeypatch.delenv("DOT_SWIFTC", raising=False)
    found = look_for_compiler()
    if found.compiler is None:
        pytest.skip(f"this Mac cannot build the program that writes recordings down: {found.why}")


def _said(voice: str, sentence: str, folder: Path) -> Recording:
    """What `voice` says of `sentence`, recorded to a file of 16 kHz mono 16-bit sound, read as the daemon reads one."""
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
    return read_wav(file.read_bytes())


def _words(text: str) -> list[str]:
    """The words of `text` in lower case, with no punctuation: Turkish writes none and English does."""
    return re.findall(r"\w+", text.casefold())


async def test_the_real_program_writes_down_what_say_recorded_in_turkish_and_in_english(tmp_path: Path) -> None:
    # A folder with a space in its name, as Dot's own home has ("Application Support").
    transcriber: Transcriber = Transcriber(tmp_path / "Application Support" / "voice")
    assert (await transcriber.ability()).transcribe
    for voice, language, sentence in _SENTENCES:
        recording: Recording = _said(voice, sentence, tmp_path)
        written: str = await transcriber.transcribe(recording, language)
        print(f"{language}: {written!r}")
        like: float = difflib.SequenceMatcher(None, _words(sentence), _words(written)).ratio()
        assert like >= _LIKE, f"{language}: said {sentence!r}, written down as {written!r}"
    # Built once for both; no recording is left behind.
    assert len(list((tmp_path / "Application Support" / "voice").glob("build-*"))) == 1
    assert list((tmp_path / "Application Support" / "voice" / "recordings").iterdir()) == []


async def test_the_real_program_says_what_it_cannot_write_down(tmp_path: Path) -> None:
    transcriber: Transcriber = Transcriber(tmp_path / "voice")
    recording: Recording = _said("Samantha", "Hello there.", tmp_path)
    with pytest.raises(VoiceError, match="cannot write down xx-YY") as unsupported:
        await transcriber.transcribe(recording, "xx-YY")
    assert unsupported.value.context["code"] == "unsupported-language"
    # A silence is no error: it is written down as nothing.
    silence: Recording = Recording(rate=RECORDING_RATE, pcm=bytes(RECORDING_RATE * 2 * 2))
    assert await transcriber.transcribe(silence, "en-US") == ""
