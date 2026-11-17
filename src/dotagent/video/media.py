"""The programs ``watch_video`` reads a video with (item 1212): ``ffprobe`` and ``ffmpeg`` for a file, ``yt-dlp`` for a
link. Each starts with an argument list (never a shell) and a time limit, in the run's sandbox (the words of
:class:`dotagent.policy.sandbox.Sandbox` it is given), so that what it reads and writes is held as a command of the
run is.

ffmpeg reads a file and nothing else: no address (``-protocol_whitelist file``), and no list that names other files
(``-format_whitelist``, :data:`_FORMATS`: a concat list or a playlist named ``talk.mp4`` is refused). A frame comes
back on ffmpeg's output and the sound in pieces, so neither is written to a file. What yt-dlp downloads goes to a
folder of its own per address under the Mac's temporary folder (:func:`link_folder`), kept a few hours so that a
second look at another part of the same video does not download it again, and removed after that by the next call.
"""

import asyncio
import base64
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dotagent.core.errors import ToolExecutionError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.llm.types import ImageData
from dotagent.video.watch import CaptionTrack, Span, VideoInfo

log = get_logger(__name__)

INSTALL: str = "install them with: brew install ffmpeg yt-dlp"
# A frame's longest edge: enough to read a slide's text, and forty of them stay a small part of a model's window.
FRAME_EDGE: int = 768
_FRAME_QUALITY: str = "4"  # ffmpeg's JPEG scale, 2 (best) to 31
# The containers ffmpeg may open (its demuxers' names): videos and sounds, never a list that names other files or
# addresses (concat, hls, image sequences), so a file reaches no further than itself. Measured: mp4, mov, m4a, webm,
# mkv, avi, ts, wmv, flv, mp3 and wav open; an ffconcat list named talk.mp4 is refused.
_FORMATS: str = "mov,matroska,avi,flv,mpegts,mpeg,ogg,wav,mp3,aac,flac,asf,aiff,caf"
# The sound the Mac's dictation model takes (dotagent.voice.wav): 16 kHz mono 16-bit samples.
_RATE: str = "16000"
_PROBE_TIMEOUT_S: float = 30.0
_FRAME_TIMEOUT_S: float = 60.0
_SOUND_TIMEOUT_S: float = 120.0
_INFO_TIMEOUT_S: float = 90.0
_CAPTIONS_TIMEOUT_S: float = 90.0
_DOWNLOAD_TIMEOUT_S: float = 900.0
# The frames taken at the same time: ffmpeg decodes from the nearest keyframe for each.
FRAMES_AT_ONCE: int = 4
# What a link may download: a video small enough for a frame to read text, with its sound.
_FORMAT: str = "bv*[height<=480]+ba/b[height<=480]/b"
_MAX_FILESIZE: str = "2G"
# How long a downloaded video is kept for a second look.
KEPT_S: float = 6 * 3600.0
_FOLDER: str = "dot-video"
# What yt-dlp writes a download as, and the name a finished one is given.
_PART_STEM: str = "download"
_VIDEO_STEM: str = "video"
_LOCK: str = ".lock"
_LOCK_POLL_S: float = 0.5
_STDERR_CHARS: int = 800


class _Stream(BaseModel):
    model_config = ConfigDict(extra="ignore")
    codec_type: str


class _Format(BaseModel):
    model_config = ConfigDict(extra="ignore")
    duration: float | None = None


class Probe(BaseModel):
    """What ffprobe says of a file: its streams and its length."""

    model_config = ConfigDict(extra="ignore")
    streams: list[_Stream] = Field(default_factory=list)
    format: _Format = Field(default_factory=_Format)

    def has(self, kind: str) -> bool:
        return any(stream.codec_type == kind for stream in self.streams)


def program(name: str) -> str:
    """The full path of ``name`` (``ffmpeg``, ``ffprobe``, ``yt-dlp``) on the daemon's ``PATH``."""
    found: str | None = shutil.which(name)
    if found is None:
        raise ToolExecutionError(f"{name} is not installed on this Mac: {INSTALL}", {"program": name})
    return found


async def _run(sandbox: Sequence[str], argv: Sequence[str], env: dict[str, str], timeout_s: float) -> bytes:
    """What the program ``argv`` printed, started in ``sandbox``; it is killed past ``timeout_s`` and an exit that is
    not 0 is an error that carries the end of what it wrote to its error output."""
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        *sandbox,
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout_s)
    except TimeoutError as error:
        process.kill()
        await process.wait()
        raise ToolExecutionError(
            f"{Path(argv[0]).name} took longer than {timeout_s:g} seconds", {"argv": list(argv[1:])}
        ) from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode != 0:
        said: str = truncate_middle(err.decode("utf-8", "replace").strip(), _STDERR_CHARS)
        raise ToolExecutionError(
            f"{Path(argv[0]).name} failed: {said}", {"argv": list(argv[1:]), "returncode": process.returncode}
        )
    return out


async def probe(sandbox: Sequence[str], env: dict[str, str], path: Path) -> Probe:
    """The streams and the length of the video at ``path``."""
    argv: list[str] = [
        program("ffprobe"),
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        _FORMATS,
        "-show_entries",
        "format=duration:stream=codec_type",
        "-of",
        "json",
        str(path),
    ]
    out: bytes = await _run(sandbox, argv, env, _PROBE_TIMEOUT_S)
    try:
        return Probe.model_validate_json(out)
    except ValidationError as error:
        raise ToolExecutionError("ffprobe's answer could not be read", {"path": str(path)}) from error


async def frame(sandbox: Sequence[str], env: dict[str, str], path: Path, at: float) -> ImageData:
    """The frame of the video at ``path`` shown at ``at`` seconds, as a JPEG no wider or taller than
    :data:`FRAME_EDGE`."""
    scale: str = f"scale='min({FRAME_EDGE},iw)':'min({FRAME_EDGE},ih)':force_original_aspect_ratio=decrease"
    argv: list[str] = [
        program("ffmpeg"),
        "-v",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        _FORMATS,
        "-ss",
        f"{at:.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        scale,
        "-q:v",
        _FRAME_QUALITY,
        "-f",
        "image2",
        "-c:v",
        "mjpeg",
        "pipe:1",
    ]
    out: bytes = await _run(sandbox, argv, env, _FRAME_TIMEOUT_S)
    if not out:
        raise ToolExecutionError("ffmpeg gave no frame", {"path": str(path), "at": at})
    return ImageData(media_type="image/jpeg", data_b64=base64.b64encode(out).decode("ascii"))


async def sound(sandbox: Sequence[str], env: dict[str, str], path: Path, span: Span) -> bytes:
    """The sound of ``span`` of the video at ``path`` as the Mac's dictation model takes it."""
    argv: list[str] = [
        program("ffmpeg"),
        "-v",
        "error",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        _FORMATS,
        "-ss",
        f"{span.start:.3f}",
        "-t",
        f"{span.end - span.start:.3f}",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        _RATE,
        "-f",
        "s16le",
        "pipe:1",
    ]
    return await _run(sandbox, argv, env, _SOUND_TIMEOUT_S)


def _held(lock: Path) -> int | None:
    """The lock file ``lock`` opened and held alone, or ``None`` while another call holds it. Blocking, never waits."""
    handle: int = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(handle)
        return None
    return handle


def _sweep(root: Path, keep: str, now: float) -> None:
    """Remove the folders of other links under ``root`` that no call used for :data:`KEPT_S` and none holds now; a
    folder that cannot be removed is logged and left. Blocking."""
    for old in root.iterdir():
        if old.name == keep or not old.is_dir() or old.is_symlink() or now - old.stat().st_mtime <= KEPT_S:
            continue
        handle: int | None = _held(old / _LOCK)
        if handle is None:
            continue
        try:
            shutil.rmtree(old)
        except OSError as error:
            log.warning("video_folder_kept", folder=old.name, error=str(error))
        finally:
            os.close(handle)


def _open_folder(url: str, now: float) -> Path:
    """The folder of ``url``'s downloads, made when it is not there and marked used now; the old folders of other links
    are swept first. Blocking."""
    root: Path = Path(tempfile.gettempdir()) / _FOLDER
    root.mkdir(mode=0o700, exist_ok=True)
    name: str = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    _sweep(root, name, now)
    folder: Path = root / name
    folder.mkdir(mode=0o700, exist_ok=True)
    os.utime(folder, (now, now))
    return folder


@asynccontextmanager
async def link_folder(url: str, now: float) -> AsyncIterator[Path]:
    """The folder of ``url``'s downloads, held by this call alone while it lasts: a second look at the same link waits
    for the first, and no sweep removes it meanwhile."""
    folder: Path = await asyncio.to_thread(_open_folder, url, now)
    while True:
        handle: int | None = await asyncio.to_thread(_held, folder / _LOCK)
        if handle is not None:
            break
        await asyncio.sleep(_LOCK_POLL_S)
    try:
        yield folder
    finally:
        os.close(handle)


def _yt_dlp(url: str, words: Sequence[str]) -> list[str]:
    """yt-dlp's arguments: its own settings file is not read, so that a call does what it says on any Mac."""
    return [program("yt-dlp"), "--ignore-config", "--no-playlist", "--no-warnings", "--retries", "3", *words, url]


async def link_info(sandbox: Sequence[str], env: dict[str, str], url: str) -> VideoInfo:
    """What yt-dlp says of the video at ``url``."""
    out: bytes = await _run(sandbox, _yt_dlp(url, ["-J"]), env, _INFO_TIMEOUT_S)
    try:
        return VideoInfo.model_validate(json.loads(out))
    except (ValidationError, json.JSONDecodeError) as error:
        raise ToolExecutionError("yt-dlp's answer about the link could not be read", {"url": url}) from error


def _read_if_there(path: Path) -> str | None:
    return path.read_text("utf-8", "replace") if path.is_file() else None


async def captions(sandbox: Sequence[str], env: dict[str, str], url: str, track: CaptionTrack, folder: Path) -> str:
    """The WebVTT text of the caption ``track`` of the video at ``url``, downloaded into ``folder`` once: yt-dlp names
    the file after the track's language, so a track of another language is another file."""
    stem: str = f"captions-{'auto' if track.automatic else 'own'}"
    path: Path = folder / f"{stem}.{track.language}.vtt"
    kept: str | None = await asyncio.to_thread(_read_if_there, path)
    if kept is not None:
        return kept
    words: list[str] = [
        "--skip-download",
        "--write-auto-subs" if track.automatic else "--write-subs",
        "--sub-langs",
        track.language,
        "--sub-format",
        "vtt",
        "-o",
        str(folder / f"{stem}.%(ext)s"),
    ]
    await _run(sandbox, _yt_dlp(url, words), env, _CAPTIONS_TIMEOUT_S)
    text: str | None = await asyncio.to_thread(_read_if_there, path)
    if text is None:
        raise ToolExecutionError("yt-dlp wrote no caption file", {"url": url, "track": track.language})
    return text


def _finished(folder: Path) -> Path | None:
    """The video a download finished earlier: only a file yt-dlp said it finished is given this name."""
    done: list[Path] = sorted(folder.glob(f"{_VIDEO_STEM}.*"))
    return done[0] if done else None


def _take(written: Path, folder: Path) -> Path:
    """The file yt-dlp finished, named as a finished video of ``folder``. Blocking."""
    if written.resolve().parent != folder.resolve() or not written.is_file():
        raise ToolExecutionError("yt-dlp named a file it did not write", {"file": str(written)})
    return written.rename(folder / f"{_VIDEO_STEM}{written.suffix}")


async def download(sandbox: Sequence[str], env: dict[str, str], url: str, folder: Path) -> Path:
    """The video at ``url`` as a file in ``folder``, which the caller holds (:func:`link_folder`): the one finished
    earlier, or downloaded now (at most 480 lines high, with its sound). What yt-dlp leaves of a download it did not
    finish (the video and the sound before they are merged) never counts: only the file it names once it has merged
    and moved it, after it ended well, becomes the video."""
    kept: Path | None = await asyncio.to_thread(_finished, folder)
    if kept is not None:
        return kept
    started: float = time.monotonic()
    words: list[str] = [
        "-f",
        _FORMAT,
        "--max-filesize",
        _MAX_FILESIZE,
        "--ffmpeg-location",
        program("ffmpeg"),
        "--merge-output-format",
        "mp4",
        "--no-simulate",
        "--print",
        "after_move:filepath",
        "-o",
        str(folder / f"{_PART_STEM}.%(ext)s"),
    ]
    out: bytes = await _run(sandbox, _yt_dlp(url, words), env, _DOWNLOAD_TIMEOUT_S)
    lines: list[str] = out.decode("utf-8", "replace").strip().splitlines()
    if not lines:
        raise ToolExecutionError(
            f"yt-dlp downloaded nothing (a video over {_MAX_FILESIZE}B is not downloaded)", {"url": url}
        )
    got: Path = await asyncio.to_thread(_take, Path(lines[-1]), folder)
    log.info("video_downloaded", took_s=round(time.monotonic() - started, 1))
    return got
