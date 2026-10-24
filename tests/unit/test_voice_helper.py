"""The program that writes a recording down (item 626), built by the daemon and run on a recording, with a compiler and
a program of the test's own in the place of Apple's: no test builds Swift or touches Apple's frameworks.

The stand-in compiler writes the stand-in program where `-o` says and notes each build; the stand-in program notes each
run and answers as the test has set it. So these tests run the real code that builds once, hashes, starts a program
with an argument list and a small environment, reads its answer, and keeps no recording."""

import asyncio
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from dotagent.config import load_default_config
from dotagent.core.errors import InvalidValueError, VoiceError
from dotagent.core.paths import voice_dir
from dotagent.runtime import _dot_paths
from dotagent.voice import helper
from dotagent.voice.helper import LANGUAGE_TAG, SOURCE, Ability, Transcriber
from dotagent.voice.toolchain import NO_COMPILER
from dotagent.voice.wav import RECORDING_RATE, TAIL_SECONDS, Recording

# A recording of a tenth of a second of silence, and what the program is given of it: the header, the samples and the
# silence after them.
RECORDING: Recording = Recording(rate=RECORDING_RATE, pcm=bytes(3200))
GIVEN_BYTES: int = 44 + 3200 + round(TAIL_SECONDS * RECORDING_RATE) * 2
SECRET_WORD: str = "swordfish"

_COMPILER: str = """#!/bin/sh
echo "$@" >> "{builds}"
[ -f "{delay}" ] && sleep "$(cat "{delay}")"
if [ -f "{refuse}" ]; then echo "error: the stand-in compiler refuses" >&2; exit 1; fi
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; fi
  shift
done
cp "{program}" "$out"
chmod 755 "$out"
"""

_PROGRAM: str = """#!/bin/sh
echo "$1" >> "{files}"
echo "$# $2" >> "{runs}"
echo $$ >> "{pids}"
case "$(cat "{control}")" in
  environment)
    echo "PATH=$PATH HOME=$HOME" >> "{seen}"
    env | cut -d= -f1 | sort | tr '\\n' ' ' >> "{seen}"
    echo >> "{seen}"
    printf '{{"text":"seen"}}\\n' ;;
  words) printf '{{"text":"%s %s %s"}}\\n' "$2" "$(wc -c < "$1" | tr -d ' ')" "$(stat -f %Lp "$1")" ;;
  secret) printf '{{"text":"the password is {secret}"}}\\n' ;;
  silence) printf '{{"text":""}}\\n' ;;
  language) printf '{{"error":"unsupported-language","detail":"nope"}}\\n'; exit 1 ;;
  audio) printf '{{"error":"no-audio","detail":"empty"}}\\n'; exit 1 ;;
  failed) printf '{{"error":"failed","detail":"the model broke"}}\\n'; exit 1 ;;
  crash) echo "Fatal error: boom" >&2; exit 139 ;;
  garbage) echo "{secret} is not json" ;;
  slow) exec sleep 30 ;;
esac
"""


@dataclass
class Stand:
    """The stand-in compiler and program, and the notes they keep: one line per build, per run and per file given."""

    folder: Path
    builds: Path
    runs: Path
    files: Path
    seen: Path
    pids: Path
    control: Path
    delay: Path
    refuse: Path
    program: Path

    def built(self) -> int:
        return len(self.builds.read_text(encoding="utf-8").splitlines()) if self.builds.exists() else 0

    def ran(self) -> list[str]:
        return self.runs.read_text(encoding="utf-8").splitlines() if self.runs.exists() else []

    def says(self, what: str) -> None:
        self.control.write_text(what, encoding="utf-8")

    def program_built(self) -> Path:
        """The one program the daemon built, in its folder under the voice folder."""
        (found,) = (self.folder / "voice").glob("build-*/transcribe")
        return found

    def recordings(self) -> list[str]:
        """The files left in the folder recordings are kept in."""
        kept: Path = self.folder / "voice" / "recordings"
        return sorted(path.name for path in kept.iterdir()) if kept.exists() else []

    def folders(self) -> list[str]:
        """What the voice folder holds: the folders the program was built in, and the folder of recordings."""
        voice: Path = self.folder / "voice"
        return sorted(path.name for path in voice.iterdir()) if voice.exists() else []

    @staticmethod
    def mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    @staticmethod
    def exists(path: Path) -> bool:
        return path.exists()

    @staticmethod
    def write(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def remove(path: Path) -> None:
        path.unlink()


@pytest.fixture
def stand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Stand:
    made: Stand = Stand(
        folder=tmp_path,
        builds=tmp_path / "builds.log",
        runs=tmp_path / "runs.log",
        files=tmp_path / "files.log",
        seen=tmp_path / "seen.log",
        pids=tmp_path / "pids.log",
        control=tmp_path / "control",
        delay=tmp_path / "delay",
        refuse=tmp_path / "refuse",
        program=tmp_path / "stand-in-program",
    )
    made.says("words")
    made.program.write_text(
        _PROGRAM.format(
            runs=made.runs, files=made.files, seen=made.seen, control=made.control, pids=made.pids, secret=SECRET_WORD
        ),
        encoding="utf-8",
    )
    compiler: Path = tmp_path / "swiftc"
    compiler.write_text(
        _COMPILER.format(builds=made.builds, delay=made.delay, refuse=made.refuse, program=made.program),
        encoding="utf-8",
    )
    compiler.chmod(0o755)
    monkeypatch.setenv("DOT_SWIFTC", str(compiler))
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    return made


def _transcriber(stand: Stand) -> Transcriber:
    return Transcriber(stand.folder / "voice")


async def test_the_program_is_built_once_and_writes_a_recording_down_each_time(stand: Stand) -> None:
    transcriber: Transcriber = _transcriber(stand)
    assert await transcriber.transcribe(RECORDING, "tr-TR") == f"tr-TR {GIVEN_BYTES} 600"
    assert await transcriber.transcribe(RECORDING, "en-US") == f"en-US {GIVEN_BYTES} 600"
    assert stand.built() == 1
    # The compiler was given the program's own source, copied into the folder it builds in, and a place for the program.
    program: Path = stand.program_built()
    arguments: list[str] = stand.builds.read_text(encoding="utf-8").split()
    assert arguments[:4] == ["-parse-as-library", "-O", "-o", str(program)]
    assert arguments[4] == str(program.parent / "transcribe.swift")
    assert (program.parent / "transcribe.swift").read_bytes() == SOURCE.read_bytes()
    # The folder it was built in is private to the owner, and so is the program.
    assert [stand.mode(program.parent), stand.mode(program), stand.mode(stand.folder / "voice")] == [0o700] * 3


async def test_the_program_is_started_with_a_path_and_a_language_only_and_a_small_environment(
    stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-the-program")
    monkeypatch.setenv("DOT_TEST_SECRET", "nor-this")
    stand.says("environment")
    await _transcriber(stand).transcribe(RECORDING, "tr-TR")
    assert stand.ran() == ["2 tr-TR"]
    path, home = stand.seen.read_text(encoding="utf-8").splitlines()[0].split(" ")
    assert path == "PATH=/usr/bin:/bin:/usr/sbin:/sbin"
    assert home == f"HOME={os.environ['HOME']}"
    names: set[str] = set(stand.seen.read_text(encoding="utf-8").splitlines()[1].split())
    assert {"OPENAI_API_KEY", "DOT_TEST_SECRET", "DOT_SWIFTC", "DOT_HOME", "SSH_AUTH_SOCK"}.isdisjoint(names)
    assert {"PATH", "HOME"} <= names


async def test_the_recording_is_a_file_of_mode_0600_in_a_private_folder_and_is_gone_after_the_words(
    stand: Stand,
) -> None:
    transcriber: Transcriber = _transcriber(stand)
    # The program reads the mode of the file it is given: 600 (`words` answers it, after the size).
    assert (await transcriber.transcribe(RECORDING, "tr-TR")).endswith(" 600")
    given: Path = Path(stand.files.read_text(encoding="utf-8").strip())
    assert given.parent == stand.folder / "voice" / "recordings"
    assert stand.mode(given.parent) == 0o700
    assert given.name.startswith("recording-") and given.suffix == ".wav"
    assert not stand.exists(given)
    assert stand.recordings() == []


@pytest.mark.parametrize("mode", ["language", "audio", "failed", "crash", "garbage", "slow"])
async def test_no_recording_is_left_after_a_program_that_failed(
    stand: Stand, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(helper, "FIRST_RUN_TIMEOUT_S", 1.5 if mode == "slow" else 300.0)
    stand.says(mode)
    with pytest.raises(VoiceError):
        await _transcriber(stand).transcribe(RECORDING, "tr-TR")
    assert stand.recordings() == []
    assert len(stand.files.read_text(encoding="utf-8").splitlines()) == 1


async def test_a_program_that_reports_an_error_says_what_to_do_in_plain_words(stand: Stand) -> None:
    transcriber: Transcriber = _transcriber(stand)
    stand.says("language")
    with pytest.raises(VoiceError, match="This Mac cannot write down xx-YY: choose another language") as language:
        await transcriber.transcribe(RECORDING, "xx-YY")
    assert language.value.context == {"code": "unsupported-language", "language": "xx-YY"}
    stand.says("audio")
    with pytest.raises(VoiceError, match="could not read the recording as sound") as audio:
        await transcriber.transcribe(RECORDING, "tr-TR")
    assert audio.value.context["code"] == "no-audio"
    stand.says("failed")
    with pytest.raises(VoiceError, match="The Mac could not write the recording down") as failed:
        await transcriber.transcribe(RECORDING, "tr-TR")
    assert failed.value.context == {"code": "failed", "detail": "the model broke", "returncode": 1}


async def test_a_crash_or_an_answer_that_is_no_json_says_it_failed_and_keeps_the_words_out_of_the_error(
    stand: Stand,
) -> None:
    transcriber: Transcriber = _transcriber(stand)
    for mode, returncode in (("crash", 139), ("garbage", 0)):
        stand.says(mode)
        with (
            capture_logs() as logs,
            pytest.raises(VoiceError, match="The Mac could not write the recording down") as failed,
        ):
            await transcriber.transcribe(RECORDING, "tr-TR")
        assert failed.value.context == {"code": "failed", "returncode": returncode}
        assert SECRET_WORD not in repr(failed.value) and SECRET_WORD not in repr(logs)
        # Not chained to pydantic's error, which quotes the answer: a traceback in a log would carry the words.
        assert failed.value.__cause__ is None and failed.value.__suppress_context__
    assert any(entry["event"] == "voice_answer_unreadable" for entry in logs)


async def test_silence_is_no_error_the_words_are_none(stand: Stand) -> None:
    stand.says("silence")
    assert await _transcriber(stand).transcribe(RECORDING, "tr-TR") == ""


async def test_a_program_that_takes_too_long_is_killed_and_the_longer_wait_is_for_a_languages_first_use(
    stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A program that has only to start must not be timed too closely, a slow one only to be outlasted.
    monkeypatch.setattr(helper, "FIRST_RUN_TIMEOUT_S", 1.2)
    monkeypatch.setattr(helper, "RUN_TIMEOUT_S", 0.8)
    transcriber: Transcriber = _transcriber(stand)
    stand.says("slow")
    with pytest.raises(VoiceError, match=r"took longer than 1\.2 seconds") as first:
        await transcriber.transcribe(RECORDING, "tr-TR")
    assert first.value.context == {"code": "timeout", "seconds": 1.2}
    # The program that was started is gone, not left running.
    pid: int = int(stand.pids.read_text(encoding="utf-8").split()[0])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert stand.recordings() == []
    # A language that was written down once in this daemon's life has the shorter wait.
    stand.says("words")
    monkeypatch.setattr(helper, "RUN_TIMEOUT_S", 60.0)
    await transcriber.transcribe(RECORDING, "tr-TR")
    stand.says("slow")
    monkeypatch.setattr(helper, "RUN_TIMEOUT_S", 0.8)
    with pytest.raises(VoiceError) as again:
        await transcriber.transcribe(RECORDING, "tr-TR")
    assert again.value.context == {"code": "timeout", "seconds": 0.8}
    with pytest.raises(VoiceError) as other:
        await transcriber.transcribe(RECORDING, "en-US")
    assert other.value.context == {"code": "timeout", "seconds": 1.2}


async def test_a_program_changed_under_the_daemon_is_never_run_and_is_built_again(stand: Stand) -> None:
    transcriber: Transcriber = _transcriber(stand)
    assert await transcriber.transcribe(RECORDING, "tr-TR") == f"tr-TR {GIVEN_BYTES} 600"
    first: Path = stand.program_built()
    hacked: Path = stand.folder / "hacked.log"
    stand.write(first, f'#!/bin/sh\necho ran >> "{hacked}"\necho \'{{"text":"hacked"}}\'\n')
    assert await transcriber.transcribe(RECORDING, "tr-TR") == f"tr-TR {GIVEN_BYTES} 600"
    assert not stand.exists(hacked), "the program that was changed was run"
    assert stand.built() == 2
    # The folder of the changed program went with it; the program built again is in a folder of its own.
    second: Path = stand.program_built()
    assert second != first and not stand.exists(first.parent)
    # A program that is gone is built again too, and one that is unchanged is not.
    stand.remove(second)
    assert await transcriber.transcribe(RECORDING, "tr-TR") == f"tr-TR {GIVEN_BYTES} 600"
    assert await transcriber.transcribe(RECORDING, "tr-TR") == f"tr-TR {GIVEN_BYTES} 600"
    assert stand.built() == 3


async def test_callers_at_the_same_moment_wait_for_one_build(stand: Stand) -> None:
    (stand.folder / "delay").write_text("0.4", encoding="utf-8")
    transcriber: Transcriber = _transcriber(stand)
    answers: list[str] = await asyncio.gather(*(transcriber.transcribe(RECORDING, "tr-TR") for _ in range(5)))
    assert answers == [f"tr-TR {GIVEN_BYTES} 600"] * 5
    assert stand.built() == 1
    assert len(stand.ran()) == 5


async def test_asking_whether_this_mac_can_builds_the_program_in_the_background(stand: Stand) -> None:
    transcriber: Transcriber = _transcriber(stand)
    assert await transcriber.ability() == Ability(transcribe=True, why=None)
    assert stand.built() == 0
    transcriber.warm()
    transcriber.warm()
    for _ in range(100):
        if stand.built() and (stand.folder / "voice").glob("build-*/transcribe"):
            break
        await asyncio.sleep(0.05)
    assert stand.built() == 1
    await asyncio.sleep(0.1)
    assert await transcriber.transcribe(RECORDING, "tr-TR") == f"tr-TR {GIVEN_BYTES} 600"
    assert stand.built() == 1
    # Built already: nothing more to start.
    transcriber.warm()
    await asyncio.sleep(0.2)
    assert stand.built() == 1


async def test_a_build_that_fails_is_told_and_not_tried_again_for_a_while(
    stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand.refuse.write_text("yes", encoding="utf-8")
    transcriber: Transcriber = _transcriber(stand)
    with (
        capture_logs() as logs,
        pytest.raises(VoiceError, match="could not build the program that writes recordings down") as refused,
    ):
        await transcriber.transcribe(RECORDING, "tr-TR")
    assert refused.value.context["code"] == "failed"
    assert refused.value.context["returncode"] == 1
    assert refused.value.context["stderr"] == "error: the stand-in compiler refuses"
    # The owner is told what the compiler complained of, and the log says why the program was not built, once.
    assert refused.value.message.endswith(". The compiler said: error: the stand-in compiler refuses")
    assert [entry["event"] for entry in logs if entry["event"] == "voice_program_not_built"] == [
        "voice_program_not_built"
    ]
    assert stand.built() == 1
    # The page asks at each load: it is told why, and the compiler is not started for each ask.
    said: Ability = await transcriber.ability()
    assert said.transcribe is False and said.why == refused.value.message
    with capture_logs() as again, pytest.raises(VoiceError, match="could not build the program"):
        await transcriber.transcribe(RECORDING, "tr-TR")
    assert stand.built() == 1
    assert [entry for entry in again if entry["event"] == "voice_program_not_built"] == []
    # Once the wait is over the next recording builds again, and now it works.
    stand.refuse.unlink()
    monkeypatch.setattr(helper, "RETRY_BUILD_AFTER_S", 0.0)
    assert await transcriber.ability() == Ability(transcribe=True, why=None)
    assert await transcriber.transcribe(RECORDING, "tr-TR") == f"tr-TR {GIVEN_BYTES} 600"
    assert stand.built() == 2


async def test_a_mac_without_a_compiler_cannot_and_says_so_without_building(
    stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_SWIFTC", str(stand.folder / "no-such-compiler"))
    transcriber: Transcriber = _transcriber(stand)
    assert await transcriber.ability() == Ability(transcribe=False, why=NO_COMPILER)
    with pytest.raises(VoiceError, match="This Mac cannot write recordings down") as unavailable:
        await transcriber.transcribe(RECORDING, "tr-TR")
    assert unavailable.value.context == {"code": "unavailable", "why": NO_COMPILER}
    transcriber.warm()
    await asyncio.sleep(0.1)
    assert stand.built() == 0 and not (stand.folder / "voice").exists()
    # The owner installs the tools and reloads the page: a Mac without them is asked again, not remembered.
    monkeypatch.setenv("DOT_SWIFTC", str(stand.folder / "swiftc"))
    assert await transcriber.ability() == Ability(transcribe=True, why=None)
    assert await transcriber.transcribe(RECORDING, "tr-TR") == f"tr-TR {GIVEN_BYTES} 600"
    assert stand.built() == 1


@pytest.mark.parametrize(
    "language",
    ["tr_TR", "../x", "-h", "tr-TR; id", "", "TR-tr", "english", "tr-TRX", "tr-tr", "t", "trtr", "en-US\n", " en-US",
     "tr-TR\0", "/etc/passwd", "$(id)", "tr-T", "tr-TR-x"],
)  # fmt: skip
async def test_a_language_that_is_not_a_tag_starts_no_program(stand: Stand, language: str) -> None:
    with pytest.raises(InvalidValueError, match="not a language"):
        await _transcriber(stand).transcribe(RECORDING, language)
    assert stand.built() == 0 and stand.ran() == []
    assert not (stand.folder / "voice").exists()


@pytest.mark.parametrize("language", ["en", "tr", "en-US", "tr-TR", "yue-CN", "zh-CN", "pt-BR", "fil", "fil-PH"])
def test_a_language_is_a_tag_of_two_or_three_letters_and_a_region(language: str) -> None:
    assert LANGUAGE_TAG.fullmatch(language) is not None


async def test_the_log_has_how_long_it_was_and_how_long_it_took_never_the_words(stand: Stand) -> None:
    stand.says("secret")
    with capture_logs() as logs:
        words: str = await _transcriber(stand).transcribe(RECORDING, "tr-TR")
    assert SECRET_WORD in words
    assert SECRET_WORD not in repr(logs)
    written: list[dict[str, object]] = [entry for entry in logs if entry["event"] == "voice_written_down"]
    assert len(written) == 1
    assert set(written[0]) == {"event", "log_level", "language", "seconds", "took_s"}
    assert written[0]["language"] == "tr-TR" and written[0]["seconds"] == 0.1


def test_recordings_an_earlier_daemon_left_are_removed_when_the_daemon_starts(tmp_path: Path) -> None:
    kept: Path = tmp_path / "voice" / "recordings"
    kept.mkdir(parents=True)
    (kept / "recording-abc123.wav").write_bytes(b"RIFF")
    (kept / "recording-def456.wav").write_bytes(b"RIFF")
    (kept / "notes.txt").write_text("not a recording", encoding="utf-8")
    Transcriber(tmp_path / "voice")
    assert [path.name for path in kept.iterdir()] == ["notes.txt"]


async def test_the_folders_of_earlier_builds_go_with_the_next_build_and_nothing_else_does(stand: Stand) -> None:
    voice: Path = stand.folder / "voice"
    (voice / "build-abcd1234").mkdir(parents=True)
    (voice / "build-abcd1234" / "transcribe").write_text("old", encoding="utf-8")
    (voice / "other-folder").mkdir()
    (voice / "build-notours").mkdir()
    await _transcriber(stand).transcribe(RECORDING, "tr-TR")
    assert sorted(name for name in stand.folders() if name.startswith("build-")) == sorted(
        [stand.program_built().parent.name, "build-notours"]
    )
    assert "other-folder" in stand.folders()


_LICENCE: str = "Agreeing to the Xcode license requires admin privileges, please run sudo xcodebuild -license"


@pytest.mark.parametrize(
    ("stderr", "told"),
    [
        (b"", ""),
        (b"\n   \n", ""),
        (b"warning: unused\nerror: unable to load the library\nnote: see more\n", "error: unable to load the library"),
        # An Xcode whose license is not accepted says so without the word "error": its first line is what is needed.
        (f"{_LICENCE}\nnote: x\n".encode(), _LICENCE),
    ],
)
def test_the_owner_is_told_the_compilers_first_complaint(stderr: bytes, told: str) -> None:
    assert helper._complaint(stderr) == told


def test_a_complaint_that_runs_on_is_cut_to_a_few_words() -> None:
    told: str = helper._complaint(b"error: " + b"x" * 1000)
    assert told.startswith("error: x") and len(told) <= 200


def test_what_the_daemon_builds_and_runs_is_among_the_paths_a_runs_commands_may_read_and_not_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    assert voice_dir() == tmp_path / "home" / "voice"
    assert voice_dir() in _dot_paths(load_default_config())
