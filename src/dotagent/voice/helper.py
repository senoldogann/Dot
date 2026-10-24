"""Writing recordings down on this Mac with a program the daemon builds (item 626).

The program is ``transcribe.swift``: macOS 26's own dictation model, on the device, behind a command line (``<program>
<WAV file> <language>`` prints one line of JSON). The daemon builds it with the Swift compiler of Apple's developer
tools (:mod:`dotagent.voice.toolchain`), once in its life, the first time it is needed or a page asks whether this Mac
can write down (:meth:`Transcriber.warm`), in a folder of mode 0700 under Dot's home that a run's commands may not
change (``runtime._dot_paths``). It keeps the SHA-256 of what it built in memory and hashes the file again before each
run: a file that is not the one built is never run, and the program is built anew.

Whether this Mac can write down needs up to three programs of the Mac's (``xcode-select -p`` and two of ``xcrun``, ten
seconds each at the most), and a page asks at each load. So the daemon asks the Mac once and remembers: a compiler that
was found for the daemon's life, what was missing for ``MISSING_KEPT_S`` seconds, so that installing the tools is seen
without a restart. A build that fails forgets it, since an Xcode that moved leaves a path that is out of date, and once
the program is built the Mac is not asked at all. One question at a time: the others wait for its answer.

A recording is written to a file of mode 0600 in a private folder, for as long as the program takes to read it, and the
file is removed whether it worked or not. The recording and the words are never logged and never kept; the log has how
long the recording was and how long it took to write it down. The program is started with an argument list (never a
shell), a path and a language tag that is checked first, a small environment and a time limit.
"""

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dotagent.core.errors import InvalidValueError, VoiceError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.voice.toolchain import Toolchain, find_compiler, small_environment
from dotagent.voice.wav import Recording, canonical_wav

log = get_logger(__name__)

SOURCE: Path = Path(__file__).parent / "transcribe.swift"
_PROGRAM: str = "transcribe"
_BUILD_PREFIX: str = "build-"
# The name ``tempfile.mkdtemp`` gives a folder: its prefix and eight characters of its own alphabet.
_BUILD_FOLDER: re.Pattern[str] = re.compile(rf"{_BUILD_PREFIX}[0-9a-z_]{{8}}")
_RECORDINGS: str = "recordings"
_RECORDING_PREFIX: str = "recording-"
_RECORDING_SUFFIX: str = ".wav"
_PRIVATE_FOLDER: int = 0o700
_PRIVATE_PROGRAM: int = 0o700
# Building with a cold cache of the compiler's modules takes a minute; a warm one, seven seconds.
BUILD_TIMEOUT_S: float = 180.0
# Writing a recording down takes about a second; the first of a language in a daemon's life may download its model.
RUN_TIMEOUT_S: float = 60.0
FIRST_RUN_TIMEOUT_S: float = 300.0
# A build that failed is not tried again for this long: the page asks at each load, and the compiler is not quick.
RETRY_BUILD_AFTER_S: float = 600.0
# A Mac that lacks what it needs to build with (macOS 26, Apple's developer tools, a compiler and an SDK in them) is not
# asked again for this long: long enough that the page loads of a Mac without them ask nothing, short enough that the
# owner who installs the tools and reloads the page is not made to restart the daemon.
MISSING_KEPT_S: float = 30.0
# Recordings written down at one time; the others wait, as the model is large.
_AT_ONCE: int = 2
# A language as the program is given it: ``tr-TR``, ``en-US``, ``yue-CN``, or a bare ``en``.
LANGUAGE_TAG: re.Pattern[str] = re.compile(r"[a-z]{2,3}(-[A-Z]{2})?")
_ERROR_CHARS: int = 600
# How much of the compiler's first complaint the owner is told: it is read on a phone.
_COMPLAINT_CHARS: int = 200
_TAG_CHARS: int = 40
_TEXT_CHARS: int = 20_000
UNREADABLE: str = "The Mac could not write the recording down"


class Ability(FrozenModel):
    """``GET /api/voice``: whether this Mac can write a recording down, and when it cannot, why, in plain words."""

    transcribe: bool
    why: str | None


class _Answer(BaseModel):
    """The one line of JSON the program prints: ``text`` for the words, or an ``error`` code and its ``detail``."""

    model_config = ConfigDict(extra="ignore")
    text: str | None = Field(default=None, max_length=_TEXT_CHARS)
    error: str | None = Field(default=None, max_length=64)
    detail: str = Field(default="", max_length=1000)


@dataclass(frozen=True)
class _Built:
    """The program as it was built: where, and the SHA-256 it had then."""

    program: Path
    digest: str


@dataclass(frozen=True)
class _Outcome:
    returncode: int
    out: bytes
    err: bytes


def _digest_of(path: Path) -> str | None:
    """The SHA-256 of the file at ``path``, ``None`` when it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _clipped(raw: bytes) -> str:
    return truncate_middle(raw.decode("utf-8", errors="replace").strip(), _ERROR_CHARS)


def _complaint(stderr: bytes) -> str:
    """What the compiler complained of first, in a few words: its first line that says error, else its first line, and
    nothing when it said nothing. The owner reads it (it is part of what ``GET /api/voice`` says); a build that fails
    on a Mac with an Xcode whose license is not accepted says so in it."""
    lines: list[str] = [line.strip() for line in stderr.decode("utf-8", errors="replace").splitlines() if line.strip()]
    chosen: str = next((line for line in lines if "error" in line.lower()), lines[0] if lines else "")
    return truncate_middle(chosen, _COMPLAINT_CHARS)


def _private(folder: Path) -> Path:
    """``folder``, made when it is not there, readable by its owner alone."""
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, _PRIVATE_FOLDER)
    return folder


def _fill(descriptor: int, wav: bytes) -> None:
    """Write ``wav`` to the open file ``descriptor`` and close it."""
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(wav)


async def _exec(arguments: list[str], timeout_s: float) -> _Outcome:
    """Run the program ``arguments[0]`` with the rest as its arguments (no shell) in the small environment, and wait for
    it for ``timeout_s`` seconds: ``TimeoutError`` if it takes longer, ``OSError`` if it cannot be started. A program
    still running when this ends, for whatever reason, is killed."""
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=small_environment(),
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout_s)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return _Outcome(process.returncode if process.returncode is not None else -1, out, err)


class Transcriber:
    """Writes recordings down with the program built from ``transcribe.swift``. ``folder`` is where the program is built
    and where a recording is kept while it is written down (:func:`dotagent.core.paths.voice_dir`); a recording left
    there by a daemon that ended in the middle of one is removed when this is made."""

    def __init__(self, folder: Path) -> None:
        self._folder: Path = folder
        self._building: asyncio.Lock = asyncio.Lock()
        self._built: _Built | None = None
        self._failed: tuple[float, VoiceError] | None = None
        # What the Mac last said it has to build with, and when (:meth:`_toolchain`); one question at a time.
        self._tools: tuple[float, Toolchain] | None = None
        self._asking: asyncio.Lock = asyncio.Lock()
        self._warming: asyncio.Task[None] | None = None
        self._written: set[str] = set()
        self._slots: asyncio.Semaphore = asyncio.Semaphore(_AT_ONCE)
        self._sweep()

    def _sweep(self) -> None:
        """Remove the recordings an earlier daemon left: none is kept, however that daemon ended."""
        for old in self._folder.glob(f"{_RECORDINGS}/{_RECORDING_PREFIX}*{_RECORDING_SUFFIX}"):
            old.unlink(missing_ok=True)

    def _recent_failure(self) -> VoiceError | None:
        """The error of the last build when it failed less than ``RETRY_BUILD_AFTER_S`` ago."""
        if self._failed is None:
            return None
        failed_at, error = self._failed
        if time.monotonic() - failed_at >= RETRY_BUILD_AFTER_S:
            self._failed = None
            return None
        return error

    async def _toolchain(self) -> Toolchain:
        """What this Mac has to build the program with. The Mac is asked (:func:`find_compiler`: up to three programs,
        ten seconds each) only when the answer is not known: a compiler that was found is kept for the daemon's life,
        what was missing for ``MISSING_KEPT_S`` seconds. The callers at the same moment wait for one question."""
        async with self._asking:
            kept: tuple[float, Toolchain] | None = self._tools
            if kept is not None and (kept[1].compiler is not None or time.monotonic() - kept[0] < MISSING_KEPT_S):
                return kept[1]
            asked: Toolchain = await find_compiler()
            self._tools = (time.monotonic(), asked)
            return asked

    async def ability(self) -> Ability:
        """Whether this Mac can write a recording down now; when it cannot, the one thing to do about it. A program that
        was built stays built for the daemon's life: the Mac is not asked about it again."""
        if self._built is not None:
            return Ability(transcribe=True, why=None)
        found: Toolchain = await self._toolchain()
        if found.compiler is None:
            return Ability(transcribe=False, why=found.why)
        failure: VoiceError | None = self._recent_failure()
        if failure is not None:
            return Ability(transcribe=False, why=failure.message)
        return Ability(transcribe=True, why=None)

    def warm(self) -> None:
        """Start building the program in the background, so that the first recording does not wait for the compiler. A
        program built or being built is left alone; a build that fails is logged and said by :meth:`ability`."""
        if self._built is not None or (self._warming is not None and not self._warming.done()):
            return
        self._warming = asyncio.create_task(self._warm())

    async def _warm(self) -> None:
        try:
            await self._program()
        except VoiceError:
            # A build begun for no recording has no one to tell: ``_program`` logged why, and ``ability`` says it to the
            # page.
            return

    def _fresh_folder(self) -> Path:
        """A new folder of mode 0700 to build in; the folders of earlier builds go (a program that was built before
        and is not the one that was hashed goes with its folder)."""
        _private(self._folder)
        for old in self._folder.iterdir():
            if _BUILD_FOLDER.fullmatch(old.name) is not None and old.is_dir() and not old.is_symlink():
                try:
                    shutil.rmtree(old)
                except OSError as error:
                    log.warning("voice_old_build_kept", folder=old.name, error=str(error))
        return Path(tempfile.mkdtemp(prefix=_BUILD_PREFIX, dir=self._folder))

    async def _build(self, compiler: Path, sdk: Path | None) -> _Built:
        """Build the program with ``compiler``, told ``sdk`` when there is one, in a folder of its own, from a copy of
        the source made there, and note its SHA-256."""
        folder: Path = await asyncio.to_thread(self._fresh_folder)
        source: Path = folder / SOURCE.name
        program: Path = folder / _PROGRAM
        await asyncio.to_thread(shutil.copyfile, SOURCE, source)
        started: float = time.monotonic()
        told: list[str] = [] if sdk is None else ["-sdk", str(sdk)]
        arguments: list[str] = [str(compiler), *told, "-parse-as-library", "-O", "-o", str(program), str(source)]
        words: str = (
            "Apple's developer tools on this Mac could not build the program that writes recordings down: "
            "update them (Xcode, or xcode-select --install)"
        )
        try:
            outcome: _Outcome = await _exec(arguments, BUILD_TIMEOUT_S)
        except TimeoutError as error:
            raise VoiceError(
                f"{words}. Building it took longer than {BUILD_TIMEOUT_S:g} seconds",
                {"code": "failed", "compiler": str(compiler)},
            ) from error
        except OSError as error:
            raise VoiceError(
                words, {"code": "failed", "compiler": str(compiler), "error": error.strerror or str(error)}
            ) from error
        if outcome.returncode != 0:
            complaint: str = _complaint(outcome.err)
            raise VoiceError(
                f"{words}. The compiler said: {complaint}" if complaint != "" else words,
                {
                    "code": "failed",
                    "compiler": str(compiler),
                    "returncode": outcome.returncode,
                    "stderr": _clipped(outcome.err),
                },
            )
        await asyncio.to_thread(os.chmod, program, _PRIVATE_PROGRAM)
        digest: str | None = await asyncio.to_thread(_digest_of, program)
        if digest is None:
            raise VoiceError(words, {"code": "failed", "error": "the program was not written"})
        log.info("voice_program_built", compiler=str(compiler), took_s=round(time.monotonic() - started, 1))
        return _Built(program, digest)

    async def _program(self) -> Path:
        """The program that writes recordings down: built on first use in this daemon's life, one build at a time
        (callers wait for it), and built anew when the file is not the one that was built. A Mac that has no tools to
        build it with is asked again each time (the owner may have installed them since); a build that failed a short
        while ago is not tried again: its error is given again."""
        async with self._building:
            built: _Built | None = self._built
            if built is not None:
                if await asyncio.to_thread(_digest_of, built.program) == built.digest:
                    return built.program
                log.warning("voice_program_changed", program=str(built.program))
                self._built = None
            found: Toolchain = await self._toolchain()
            if found.compiler is None:
                raise VoiceError(
                    f"This Mac cannot write recordings down. {found.why}", {"code": "unavailable", "why": found.why}
                )
            failure: VoiceError | None = self._recent_failure()
            if failure is not None:
                raise VoiceError(failure.message, failure.context)
            try:
                self._built = await self._build(found.compiler, found.sdk)
            except VoiceError as error:
                self._failed = (time.monotonic(), error)
                # What the Mac said may be out of date (an Xcode that was moved): the next build asks the Mac again.
                self._tools = None
                log.warning("voice_program_not_built", error=error.message, context=error.context)
                raise
            self._failed = None
            return self._built.program

    def _new_file(self) -> tuple[int, str]:
        """An open file of mode 0600 for a recording, in a private folder: its descriptor and its path."""
        folder: Path = _private(_private(self._folder) / _RECORDINGS)
        return tempfile.mkstemp(prefix=_RECORDING_PREFIX, suffix=_RECORDING_SUFFIX, dir=folder)

    def _words(self, outcome: _Outcome, language: str) -> str:
        """The words in the program's answer, or the error it says in the owner's words."""
        context: dict[str, str | int] = {"returncode": outcome.returncode}
        try:
            answer: _Answer = _Answer.model_validate_json(outcome.out)
        except ValidationError:
            # Neither the answer nor what was wrong with it is kept, and the error is not chained to pydantic's, which
            # quotes the answer: it may hold the words.
            log.warning("voice_answer_unreadable", returncode=outcome.returncode, stderr=_clipped(outcome.err))
            raise VoiceError(UNREADABLE, {"code": "failed", **context}) from None
        if answer.text is not None:
            return answer.text
        match answer.error:
            case "unsupported-language":
                raise VoiceError(
                    f"This Mac cannot write down {language}: choose another language in Settings › General › Voice",
                    {"code": "unsupported-language", "language": language},
                )
            case "no-audio":
                raise VoiceError(
                    "The Mac could not read the recording as sound", {"code": "no-audio", "detail": answer.detail}
                )
            case _:
                log.warning("voice_program_failed", code=answer.error, detail=answer.detail)
                raise VoiceError(UNREADABLE, {"code": "failed", "detail": answer.detail, **context})

    async def _run(self, program: Path, path: Path, language: str) -> _Outcome:
        """Start the program on the file ``path``: ``VoiceError`` when it cannot start or takes too long."""
        seconds: float = RUN_TIMEOUT_S if language in self._written else FIRST_RUN_TIMEOUT_S
        try:
            return await _exec([str(program), str(path), language], seconds)
        except TimeoutError as error:
            raise VoiceError(
                f"The Mac took longer than {seconds:g} seconds to write the recording down: try a shorter one",
                {"code": "timeout", "seconds": seconds},
            ) from error
        except OSError as error:
            raise VoiceError(UNREADABLE, {"code": "failed", "error": error.strerror or str(error)}) from error

    async def transcribe(self, recording: Recording, language: str) -> str:
        """The words of ``recording`` in ``language`` (``tr-TR``), written down by this Mac. ``VoiceError`` says what to
        do when it cannot (``context["code"]``); ``InvalidValueError`` for a language that is not a tag."""
        if LANGUAGE_TAG.fullmatch(language) is None:
            raise InvalidValueError(
                "that is not a language: write it as en-US or tr-TR", {"language": language[:_TAG_CHARS]}
            )
        program: Path = await self._program()
        # Made before the file, so that nothing between making it and writing it can fail and leave it open.
        wav: bytes = canonical_wav(recording)
        async with self._slots:
            started: float = time.monotonic()
            descriptor, name = await asyncio.to_thread(self._new_file)
            path: Path = Path(name)
            try:
                await asyncio.to_thread(_fill, descriptor, wav)
                outcome: _Outcome = await self._run(program, path, language)
            finally:
                await asyncio.to_thread(path.unlink, missing_ok=True)
            text: str = self._words(outcome, language)
        self._written.add(language)
        log.info(
            "voice_written_down",
            language=language,
            seconds=round(recording.seconds, 1),
            took_s=round(time.monotonic() - started, 2),
        )
        return text
