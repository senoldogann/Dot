"""Writing down what the owner says on this Mac with a program the daemon builds (items 626, 969).

The program is ``transcribe.swift``: macOS 26's own dictation model, on the device, behind a command line
(``<program> <language>`` reads 16 kHz mono 16-bit samples on its standard input, writes them down as they come, and
prints one line of JSON when that input ends). The daemon builds it with the Swift compiler of Apple's developer tools
(:mod:`dotagent.voice.toolchain`), once in its life, the first time it is needed or a page asks whether this Mac can
write down (:meth:`Transcriber.warm`), in a folder of mode 0700 under Dot's home that a run's commands may not change
(``runtime._dot_paths``). It keeps the SHA-256 of what it built in memory and hashes the file again before each run: a
file that is not the one built is never run, and the program is built anew.

Whether this Mac can write down needs up to three programs of the Mac's (``xcode-select -p`` and two of ``xcrun``, ten
seconds each at the most), and a page asks at each load. So the daemon asks the Mac once and remembers: a compiler that
was found for the daemon's life, what was missing for ``MISSING_KEPT_S`` seconds, so that installing the tools is seen
without a restart. A build that fails forgets it, since an Xcode that moved leaves a path that is out of date, and once
the program is built the Mac is not asked at all. One question at a time: the others wait for its answer.

A page writes down by a stream (item 969): it opens one as the owner presses the microphone (:meth:`Transcriber.open`),
which starts the program, hands it the sound in pieces as it records (:meth:`Transcriber.feed`), each piece numbered so
that none is lost or taken twice, and ends it with the owner's tick (:meth:`Transcriber.end`), which gives the program
``TAIL_SECONDS`` of silence, ends its input and returns the words a moment later; the cross drops it
(:meth:`Transcriber.drop`). A stream no piece came to for ``STREAM_IDLE_S`` seconds (a page that went away) or that
outlived the longest recording is dropped by itself, and no more than ``_AT_ONCE`` are open. The sound is never written
to a file. The words are never logged and never kept; the log has how long the recording was and how long the answer
took after the tick. The program is started with an argument list (never a shell), a language tag that is checked
first, a small environment and a time limit.
"""

import asyncio
import hashlib
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dotagent.core.errors import InvalidValueError, NotFoundError, VoiceError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.voice.toolchain import Toolchain, find_compiler, small_environment
from dotagent.voice.wav import MAX_RECORDING_SECONDS, RECORDING_RATE, TAIL_SECONDS

log = get_logger(__name__)

SOURCE: Path = Path(__file__).parent / "transcribe.swift"
_PROGRAM: str = "transcribe"
_BUILD_PREFIX: str = "build-"
# The name ``tempfile.mkdtemp`` gives a folder: its prefix and eight characters of its own alphabet.
_BUILD_FOLDER: re.Pattern[str] = re.compile(rf"{_BUILD_PREFIX}[0-9a-z_]{{8}}")
_PRIVATE_FOLDER: int = 0o700
_PRIVATE_PROGRAM: int = 0o700
# Building with a cold cache of the compiler's modules takes a minute; a warm one, seven seconds.
BUILD_TIMEOUT_S: float = 180.0
# How long the program may take to answer after the tick: a moment, as it wrote down while the owner spoke; the first
# stream of a language in a daemon's life may download its model.
RUN_TIMEOUT_S: float = 60.0
FIRST_RUN_TIMEOUT_S: float = 300.0
# A stream no piece came to for this long is dropped: the page that opened it went away. A page sends a piece about
# every quarter of a second while it records. Before its first piece a stream waits longer: the page opens it at the
# press, and the browser may ask the owner for the microphone first (an iPhone asks twice), for as long as they read.
STREAM_IDLE_S: float = 20.0
STREAM_FIRST_PIECE_S: float = 120.0
# The most a stream may live, past the longest recording: what is left open longer is dropped.
STREAM_LIFE_S: float = MAX_RECORDING_SECONDS + 60.0
_STREAM_WATCH_S: float = 2.0
_MOST_STREAM_BYTES: int = MAX_RECORDING_SECONDS * RECORDING_RATE * 2
_TAIL: bytes = bytes(round(TAIL_SECONDS * RECORDING_RATE) * 2)
_STREAM_ID_BYTES: int = 16
# A build that failed is not tried again for this long: the page asks at each load, and the compiler is not quick.
RETRY_BUILD_AFTER_S: float = 600.0
# A Mac that lacks what it needs to build with (macOS 26, Apple's developer tools, a compiler and an SDK in them) is not
# asked again for this long: long enough that the page loads of a Mac without them ask nothing, short enough that the
# owner who installs the tools and reloads the page is not made to restart the daemon.
MISSING_KEPT_S: float = 30.0
# Streams open at one time, as the model is large: one more is refused until one ends.
_AT_ONCE: int = 2
# A language as the program is given it: ``tr-TR``, ``en-US``, ``yue-CN``, or a bare ``en``.
LANGUAGE_TAG: re.Pattern[str] = re.compile(r"[a-z]{2,3}(-[A-Z]{2})?")
_ERROR_CHARS: int = 600
# How much of the compiler's first complaint the owner is told: it is read on a phone.
_COMPLAINT_CHARS: int = 200
_TAG_CHARS: int = 40
_TEXT_CHARS: int = 20_000
UNREADABLE: str = "The Mac could not write down what you said"


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


@dataclass
class _Stream:
    """A stream open with the program (item 969): the program, the language it writes down, the number the next piece
    must have, the bytes of sound it was given, and when it opened and last had a piece (the helper's clock)."""

    process: asyncio.subprocess.Process
    language: str
    next_piece: int
    given: int
    opened: float
    last: float
    lock: asyncio.Lock


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
        self._streams: dict[str, _Stream] = {}
        # The streams being opened: their place is taken before their program starts, so that two opens at the same
        # moment cannot both pass the count.
        self._opening: int = 0
        self._watching: asyncio.Task[None] | None = None

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
            case _:
                log.warning("voice_program_failed", code=answer.error, detail=answer.detail)
                raise VoiceError(UNREADABLE, {"code": "failed", "detail": answer.detail, **context})

    async def open(self, language: str) -> str:
        """Start the program for a stream in ``language`` (``tr-TR``) and return the stream's name. ``VoiceError`` says
        what to do when the Mac cannot (``context["code"]``, ``busy`` when ``_AT_ONCE`` are open);
        ``InvalidValueError`` for a language that is not a tag."""
        if LANGUAGE_TAG.fullmatch(language) is None:
            raise InvalidValueError(
                "that is not a language: write it as en-US or tr-TR", {"language": language[:_TAG_CHARS]}
            )
        program: Path = await self._program()
        taken: int = len(self._streams) + self._opening
        if taken >= _AT_ONCE:
            raise VoiceError(
                "The Mac is already writing down two other recordings: try again in a moment",
                {"code": "busy", "open": taken},
            )
        self._opening += 1
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                str(program),
                language,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=small_environment(),
            )
        except OSError as error:
            raise VoiceError(UNREADABLE, {"code": "failed", "error": error.strerror or str(error)}) from error
        finally:
            self._opening -= 1
        name: str = secrets.token_urlsafe(_STREAM_ID_BYTES)
        now: float = time.monotonic()
        self._streams[name] = _Stream(
            process=process, language=language, next_piece=0, given=0, opened=now, last=now, lock=asyncio.Lock()
        )
        if self._watching is None or self._watching.done():
            self._watching = asyncio.create_task(self._watch())
        return name

    def _stream(self, name: str) -> _Stream:
        found: _Stream | None = self._streams.get(name)
        if found is None:
            raise NotFoundError(
                "that recording is no longer open: press the microphone again", {"stream": name[:_TAG_CHARS]}
            )
        return found

    async def feed(self, name: str, piece: int, pcm: bytes) -> None:
        """Hand the stream ``name`` its piece number ``piece`` of sound (16 kHz mono 16-bit samples). A piece out of
        turn or past the longest recording drops the stream; a program that ended before its input did says why."""
        stream: _Stream = self._stream(name)
        async with stream.lock:
            if piece != stream.next_piece:
                await self._kill(name)
                raise InvalidValueError(
                    "a piece of the recording was lost on the way: press the microphone again",
                    {"piece": piece, "expected": stream.next_piece},
                )
            if stream.given + len(pcm) > _MOST_STREAM_BYTES:
                await self._kill(name)
                raise VoiceError(
                    f"A recording may be {MAX_RECORDING_SECONDS // 60} minutes long at the most",
                    {"code": "too-long", "seconds": MAX_RECORDING_SECONDS},
                )
            stream.next_piece += 1
            stream.given += len(pcm)
            stream.last = time.monotonic()
            await self._write(name, stream, pcm)

    async def _write(self, name: str, stream: _Stream, pcm: bytes) -> None:
        """Write ``pcm`` to the program's input. A program that ended already (a language it has no dictation of) is
        not written to again: what it said is raised."""
        pipe: asyncio.StreamWriter | None = stream.process.stdin
        if pipe is None:
            raise VoiceError(UNREADABLE, {"code": "failed", "error": "the program has no input"})
        try:
            pipe.write(pcm)
            await pipe.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            self._streams.pop(name, None)
            outcome: _Outcome = await self._answer(stream, RUN_TIMEOUT_S)
            self._words(outcome, stream.language)
            raise VoiceError(
                UNREADABLE, {"code": "failed", "error": "the program ended before the recording did"}
            ) from error

    async def end(self, name: str) -> str:
        """The owner's tick: the stream ``name`` is given its silence at the end, its input ends, and the words come
        back (``""`` when nothing was heard). ``VoiceError`` says what to do when the Mac could not write it down."""
        stream: _Stream = self._stream(name)
        async with stream.lock:
            started: float = time.monotonic()
            await self._write(name, stream, _TAIL)
            self._streams.pop(name, None)
            pipe: asyncio.StreamWriter | None = stream.process.stdin
            if pipe is not None:
                pipe.close()
            seconds: float = RUN_TIMEOUT_S if stream.language in self._written else FIRST_RUN_TIMEOUT_S
            outcome: _Outcome = await self._answer(stream, seconds)
            text: str = self._words(outcome, stream.language)
        self._written.add(stream.language)
        log.info(
            "voice_written_down",
            language=stream.language,
            seconds=round(stream.given / (RECORDING_RATE * 2), 1),
            took_s=round(time.monotonic() - started, 2),
        )
        return text

    async def drop(self, name: str) -> None:
        """The owner's cross: the stream ``name`` ends and nothing is written down. One that is no longer open is let
        be."""
        await self._kill(name)

    async def _kill(self, name: str) -> None:
        stream: _Stream | None = self._streams.pop(name, None)
        if stream is not None and stream.process.returncode is None:
            stream.process.kill()
            await stream.process.wait()

    async def _answer(self, stream: _Stream, seconds: float) -> _Outcome:
        """What the program printed once its input ended, waited for ``seconds``: a program that takes longer is
        killed."""
        process: asyncio.subprocess.Process = stream.process
        if process.stdout is None or process.stderr is None:
            raise VoiceError(UNREADABLE, {"code": "failed", "error": "the program has no output"})
        try:
            out, err, _ = await asyncio.wait_for(
                asyncio.gather(process.stdout.read(), process.stderr.read(), process.wait()), seconds
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise VoiceError(
                f"The Mac took longer than {seconds:g} seconds to write the recording down: try a shorter one",
                {"code": "timeout", "seconds": seconds},
            ) from error
        return _Outcome(process.returncode if process.returncode is not None else -1, out, err)

    async def _watch(self) -> None:
        """Drop the streams that no piece came to for ``STREAM_IDLE_S`` seconds (``STREAM_FIRST_PIECE_S`` before the
        first) or that lived past ``STREAM_LIFE_S``, until none is open."""
        while self._streams:
            await asyncio.sleep(_STREAM_WATCH_S)
            now: float = time.monotonic()
            for name, stream in list(self._streams.items()):
                waited: float = STREAM_IDLE_S if stream.next_piece > 0 else STREAM_FIRST_PIECE_S
                idle: bool = now - stream.last > waited
                if idle or now - stream.opened > STREAM_LIFE_S:
                    log.warning("voice_stream_dropped", reason="idle" if idle else "too long", language=stream.language)
                    await self._kill(name)
