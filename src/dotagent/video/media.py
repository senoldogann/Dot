"""The programs ``watch_video`` reads a video with (item 1212): ``ffprobe`` and ``ffmpeg`` for a file, ``yt-dlp`` for a
link. Each starts with an argument list (never a shell) and a time limit, in the run's sandbox (the words of
:class:`dotagent.policy.sandbox.Sandbox` it is given), so that what it reads and writes is held as a command of the
run is.

ffmpeg reads a file and nothing else: no address (``-protocol_whitelist file``), and no list that names other files
(``-format_whitelist``, :data:`_FORMATS`: a concat list or a playlist named ``talk.mp4`` is refused). A frame comes
back on ffmpeg's output and the sound in pieces, so neither is written to a file. What yt-dlp downloads goes to a
folder of its own per address under the Mac's temporary folder (:func:`download_folder`), kept a few hours so that a
second look at another part of the same video does not download it again, and removed after that by the next call.
"""

import asyncio
import base64
import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Sequence
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
_MEDIA_STEM: str = "media"
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


def download_folder(url: str, now: float) -> Path:
    """The folder a link's video is downloaded to, made when it is not there; the folders of other links older than
    :data:`KEPT_S` are removed first. Blocking."""
    root: Path = Path(tempfile.gettempdir()) / _FOLDER
    root.mkdir(mode=0o700, exist_ok=True)
    name: str = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    for old in root.iterdir():
        if old.name != name and old.is_dir() and not old.is_symlink() and now - old.stat().st_mtime > KEPT_S:
            shutil.rmtree(old, ignore_errors=True)
    folder: Path = root / name
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


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


async def captions(sandbox: Sequence[str], env: dict[str, str], url: str, track: CaptionTrack, folder: Path) -> str:
    """The WebVTT text of the caption ``track`` of the video at ``url``, downloaded into ``folder``."""
    kind: str = "--write-auto-subs" if track.automatic else "--write-subs"
    stem: str = f"captions-{'auto' if track.automatic else 'own'}"
    words: list[str] = [
        "--skip-download",
        kind,
        "--sub-langs",
        track.language,
        "--sub-format",
        "vtt",
        "-o",
        str(folder / f"{stem}.%(ext)s"),
    ]
    await _run(sandbox, _yt_dlp(url, words), env, _CAPTIONS_TIMEOUT_S)
    text: str | None = await asyncio.to_thread(_caption_text, folder, stem)
    if text is None:
        raise ToolExecutionError("yt-dlp wrote no caption file", {"url": url, "track": track.language})
    return text


def _caption_text(folder: Path, stem: str) -> str | None:
    """The text of the caption file yt-dlp wrote in ``folder`` as ``stem``, or ``None`` when it wrote none."""
    found: list[Path] = sorted(folder.glob(f"{stem}*.vtt"))
    return found[0].read_text("utf-8", "replace") if found else None


def _downloaded(folder: Path) -> Path | None:
    done: list[Path] = [path for path in folder.glob(f"{_MEDIA_STEM}.*") if path.suffix not in (".part", ".ytdl")]
    return done[0] if done else None


async def download(sandbox: Sequence[str], env: dict[str, str], url: str, folder: Path) -> Path:
    """The video at ``url`` as a file in ``folder``: the one downloaded earlier, or downloaded now (at most 480 lines
    high, with its sound)."""
    kept: Path | None = await asyncio.to_thread(_downloaded, folder)
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
        "-o",
        str(folder / f"{_MEDIA_STEM}.%(ext)s"),
    ]
    await _run(sandbox, _yt_dlp(url, words), env, _DOWNLOAD_TIMEOUT_S)
    got: Path | None = await asyncio.to_thread(_downloaded, folder)
    if got is None:
        raise ToolExecutionError(
            f"yt-dlp downloaded nothing (a video over {_MAX_FILESIZE}B is not downloaded)", {"url": url}
        )
    log.info("video_downloaded", took_s=round(time.monotonic() - started, 1))
    return got
