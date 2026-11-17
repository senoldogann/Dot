"""``watch_video`` (item 1212): a video file of this Mac or a video at a link, given to the model as frames of what it
shows and a transcript of what is said in it, by the minute, for a model of any kind (:mod:`dotagent.video.watch`).

A link is read with yt-dlp: its own captions when the uploader wrote some or the site recognised the speech (no
download then, unless frames are asked for), else its video, downloaded. What is said in a file, or in a link with no
captions, is written down by the Mac's own dictation model (:class:`dotagent.voice.helper.Transcriber`), a minute at a
time: nothing leaves the Mac and no key is needed. Frames are taken by ffmpeg at the middle of equal slices of the part
looked at.

A file is read as ``read_file`` reads it: refused in a protected location. A link is an address as ``web_fetch``'s is
(:func:`dotagent.tools.web.checked_address`), so ``[browser] allowed_hosts`` and ``blocked_hosts`` bind it and a long
one earns ``exfiltration``, and its host must be on the public internet unless ``[web] allow_private_hosts``; yt-dlp
connects and follows the site's redirects itself, which are not checked. The programs start in the run's sandbox. What a
video says and shows is anyone's, so the result marks the run untrusted, and the title and the transcript are fenced as
a page is.
"""

import asyncio
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import SplitResult, urlsplit

from pydantic import Field

from dotagent.browser.snapshot import fence_untrusted
from dotagent.core.errors import InvalidValueError, ToolExecutionError, ToolInputError, VoiceError
from dotagent.core.text import truncate_middle
from dotagent.llm.types import ImageData
from dotagent.policy.sandbox import Sandbox
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, at_most, make_path_tool, mark_untrusted
from dotagent.tools.context import ToolContext, run_sandbox
from dotagent.tools.files import read_action, resolve_path
from dotagent.tools.web import checked_address
from dotagent.video import media
from dotagent.video.media import Probe
from dotagent.video.watch import (
    CaptionTrack,
    Cue,
    Span,
    VideoInfo,
    caption_blocks,
    choose_track,
    clock,
    frame_times,
    parse_vtt,
    span_error,
    span_of,
    speech_pieces,
    transcript_text,
)
from dotagent.voice.helper import LANGUAGE_TAG, Transcriber
from dotagent.voice.wav import MAX_PIECE_BYTES
from dotagent.web.client import checked_addresses

WATCH_VIDEO_TOOL: str = "watch_video"
MAX_FRAMES: int = 40
# A download of up to fifteen minutes, then an hour of sound written down a minute at a time (about 2.5 s each,
# measured on the owner's Mac), and the frames.
_TIMEOUT_S: float = 1800.0
_HEADROOM_CHARS: int = 1500
_NONCE_BYTES: int = 16
_LINK_SCHEMES: tuple[str, ...] = ("http://", "https://")


class WatchVideoArgs(ToolArgs):
    source: str = Field(min_length=1, description="A video file or page address.")
    start: float = Field(default=0.0, ge=0, description="Seconds from the video's start.")
    end: float | None = Field(default=None, gt=0, description="Seconds where it ends (default: the end).")
    frames: Annotated[int, at_most(MAX_FRAMES)] = Field(default=16, ge=0, description="Frames over the part (max 40).")
    # Checked by _spoken (LANGUAGE_TAG) when the Mac writes the sound down: a pattern here costs every run's prompt.
    language: str | None = Field(default=None, description="Language spoken: en-US, tr-TR…")


def is_link(source: str) -> bool:
    return source.strip().lower().startswith(_LINK_SCHEMES)


def _classify(args: WatchVideoArgs, ctx: ToolContext) -> ActionInfo:
    if is_link(args.source):
        url, flags = checked_address(args.source.strip(), ctx.runtime.config)
        return ActionInfo(
            tool=WATCH_VIDEO_TOOL, kind="network", risk="low", summary=f"Watch {url}", target=url, flags=flags
        )
    return read_action(
        WATCH_VIDEO_TOOL,
        resolve_path(args.source, ctx),
        ctx.workspace,
        ctx.runtime.protected,
        ctx.runtime.dot_paths,
        "Watch",
    )


def _span(duration: float, args: WatchVideoArgs) -> Span:
    problem: str | None = span_error(duration, args.start, args.end)
    if problem is not None:
        raise ToolInputError(f"watch_video: {problem}", {"start": args.start, "end": args.end, "duration": duration})
    return span_of(duration, args.start, args.end)


def _spoken(language: str | None, tracks: str) -> str:
    """The language the Mac writes the sound down in; the call must name one when neither it nor the link does.
    ``tracks`` names the caption tracks a link has, for the call to name one of them; empty for a file."""
    if language is None or LANGUAGE_TAG.fullmatch(language) is None:
        raise ToolInputError(
            "watch_video: name the language spoken in the video (language='en-US', 'tr-TR'…) so the Mac can write "
            f"down what is said{tracks}",
            {"language": language},
        )
    return language


async def _frames(sandbox: Sandbox, env: dict[str, str], path: Path, times: tuple[float, ...]) -> tuple[ImageData, ...]:
    """The frames at ``times``, a few at once; the first that fails stops the others."""
    turns: asyncio.Semaphore = asyncio.Semaphore(media.FRAMES_AT_ONCE)

    async def one(at: float) -> ImageData:
        async with turns:
            return await media.frame(sandbox.search, env, path, at)

    tasks: list[asyncio.Task[ImageData]] = []
    try:
        async with asyncio.TaskGroup() as group:
            tasks.extend(group.create_task(one(at)) for at in times)
    except ExceptionGroup as failed:
        # The first failure is the one to tell, as the error it is (the executor shows a DotError to the model).
        raise failed.exceptions[0] from failed
    return tuple(task.result() for task in tasks)


def _heard_error(error: VoiceError, language: str) -> ToolExecutionError:
    """What the Mac's dictation model could not do, in words for a run (its own are for the owner at the composer)."""
    if error.context.get("code") == "unsupported-language":
        return ToolExecutionError(
            f"watch_video: this Mac cannot write down {language}; name the language spoken as another tag",
            error.context,
        )
    return ToolExecutionError(f"watch_video: the Mac's dictation model failed: {error.message}", error.context)


async def _written(transcriber: Transcriber, pcm: bytes, language: str) -> str:
    """What the Mac's dictation model writes down of ``pcm``."""
    try:
        name: str = await transcriber.open(language)
    except InvalidValueError as error:
        raise ToolInputError(
            f"watch_video: {language!r} is not a language tag: write it as en-US", error.context
        ) from error
    except VoiceError as error:
        raise _heard_error(error, language) from error
    try:
        for index, at in enumerate(range(0, len(pcm), MAX_PIECE_BYTES)):
            await transcriber.feed(name, index, pcm[at : at + MAX_PIECE_BYTES])
        return await transcriber.end(name)
    except VoiceError as error:
        await transcriber.drop(name)
        raise _heard_error(error, language) from error
    except BaseException:
        await transcriber.drop(name)
        raise


async def _dictated(
    transcriber: Transcriber, sandbox: Sandbox, env: dict[str, str], path: Path, span: Span, language: str
) -> tuple[Cue, ...]:
    """What is said in ``span`` of the file at ``path``, a minute at a time."""
    cues: list[Cue] = []
    for piece in speech_pieces(span):
        pcm: bytes = await media.sound(sandbox.search, env, path, piece)
        cues.append(Cue(start=piece.start, text=(await _written(transcriber, pcm, language)).strip()))
    return tuple(cues)


@dataclass(frozen=True)
class _Look:
    """What one look found: the video's length, its part, the moments and the frames, the transcript, the line that
    says where the transcript came from, and the video's title (the uploader's words)."""

    duration: float
    span: Span
    times: tuple[float, ...]
    images: tuple[ImageData, ...]
    transcript: str
    heard_by: str
    title: str


async def _see_and_hear(
    ctx: ToolContext, sandbox: Sandbox, path: Path, probe: Probe, span: Span, frames: int, language: str | None
) -> tuple[tuple[float, ...], tuple[ImageData, ...], tuple[Cue, ...] | None]:
    """The frames of ``span`` and, when ``language`` is given, what the Mac writes down of its sound, at once."""
    env: dict[str, str] = ctx.runtime.shell_env
    times: tuple[float, ...] = frame_times(span, frames) if frames > 0 and probe.has("video") else ()
    hearing: asyncio.Future[tuple[Cue, ...]] | None = (
        asyncio.ensure_future(_dictated(ctx.runtime.transcriber, sandbox, env, path, span, language))
        if language is not None and probe.has("audio")
        else None
    )
    try:
        images: tuple[ImageData, ...] = await _frames(sandbox, env, path, times)
    except BaseException:
        if hearing is not None:
            hearing.cancel()
            # Its stream is dropped and its ffmpeg killed before the call ends; its own error is not the one to tell.
            await asyncio.wait([hearing])
        raise
    return times, images, (None if hearing is None else await hearing)


def _dictation_note(probe: Probe, cues: tuple[Cue, ...] | None, language: str | None) -> tuple[str, str]:
    """The transcript of the Mac's dictation and the line that says where it came from."""
    if not probe.has("audio"):
        return "", "The video has no sound."
    return transcript_text(
        cues or ()
    ), f"Transcript: written down by this Mac's dictation model ({language}), by the minute."


async def _watch_file(ctx: ToolContext, args: WatchVideoArgs, path: Path) -> _Look:
    sandbox: Sandbox = run_sandbox(ctx)
    probe: Probe = await media.probe(sandbox.search, ctx.runtime.shell_env, path)
    duration: float | None = probe.format.duration
    if duration is None or duration <= 0 or not (probe.has("video") or probe.has("audio")):
        raise ToolExecutionError(
            f"watch_video: {path.name} is not a video or a sound ffmpeg can read", {"path": str(path)}
        )
    span: Span = _span(duration, args)
    language: str | None = _spoken(args.language, "") if probe.has("audio") else None
    times, images, cues = await _see_and_hear(ctx, sandbox, path, probe, span, args.frames, language)
    transcript, heard_by = _dictation_note(probe, cues, language)
    return _Look(duration, span, times, images, transcript, heard_by, path.name)


async def _watch_link(ctx: ToolContext, args: WatchVideoArgs, url: str) -> _Look:
    sandbox: Sandbox = run_sandbox(ctx)
    env: dict[str, str] = ctx.runtime.shell_env
    # The host must be on the public internet, as web_fetch's is (yt-dlp connects by itself: a name that answers
    # otherwise a moment later, or a redirect, is not seen).
    parts: SplitResult = urlsplit(url)
    port: int = parts.port or (443 if parts.scheme == "https" else 80)
    await checked_addresses(parts.hostname or "", port, allow_private_hosts=ctx.runtime.config.web.allow_private_hosts)
    info: VideoInfo = await media.link_info(sandbox.commands, env, url)
    if info.duration is None or info.duration <= 0:
        raise ToolExecutionError("watch_video: the link is a live stream or has no length to watch", {"url": url})
    span: Span = _span(info.duration, args)
    track: CaptionTrack | None = choose_track(info, args.language)
    title: str = info.title if info.uploader is None else f"{info.title} — {info.uploader}"
    async with media.link_folder(url, time.time()) as folder:
        transcript: str = ""
        heard_by: str = ""
        if track is not None:
            vtt: str = await media.captions(sandbox.commands, env, url, track, folder)
            transcript = transcript_text(caption_blocks(parse_vtt(vtt), span))
            made: str = "the site's speech recognition" if track.automatic else "the uploader"
            heard_by = f"Transcript: the video's own captions ({track.language}, made by {made})."
        if args.frames == 0 and track is not None:
            return _Look(info.duration, span, (), (), transcript, heard_by, title)
        written: str = ", ".join(name for name in info.subtitles if name != "live_chat")
        tracks: str = f"; the link has captions in {written}, which naming the language takes" if written else ""
        language: str | None = None if track is not None else _spoken(args.language or info.language, tracks)
        path: Path = await media.download(sandbox.commands, env, url, folder)
        probe: Probe = await media.probe(sandbox.search, env, path)
        times, images, cues = await _see_and_hear(ctx, sandbox, path, probe, span, args.frames, language)
    if track is None:
        transcript, heard_by = _dictation_note(probe, cues, language)
    return _Look(info.duration, span, times, images, transcript, heard_by, title)


def _render(look: _Look, where: str, output_chars: int) -> ToolResult:
    """The look as the model reads it: Dot's lines, then the title and the transcript in a fence, then the frames."""
    lines: list[str] = [
        f"Video {where}: {clock(look.duration)} long; this look covers {clock(look.span.start)}-{clock(look.span.end)}."
    ]
    if look.span.cut:
        lines.append(f"One look covers at most an hour: call again with start={look.span.end:g} for what comes after.")
    lines.append(look.heard_by)
    if look.times:
        moments: str = ", ".join(f"{index}) {clock(at)}" for index, at in enumerate(look.times, start=1))
        lines.append(f"{len(look.times)} frames follow as images, in this order: {moments}.")
    else:
        lines.append("No frames were taken.")
    lines.append(
        "For a closer look at a moment, call again with start and end around it: the same frames over a shorter part."
    )
    budget: int = max(1000, output_chars - _HEADROOM_CHARS - sum(len(line) for line in lines))
    said: str = look.transcript if look.transcript else "(nothing is said in this part)"
    shown: str = truncate_middle(said, budget)
    if shown != said:
        lines.append("The middle of the transcript was cut to fit: look at a shorter part to read all of it.")
    body: str = f"Title: {look.title}\n\n{shown}"
    fenced: str = fence_untrusted(where, body, secrets.token_hex(_NONCE_BYTES))
    return mark_untrusted(ToolResult(text="\n".join([*lines, fenced]), images=look.images, is_error=False))


def _watch_tool() -> RegisteredTool:
    async def run(args: WatchVideoArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        if authorized.kind == "network":
            return _render(await _watch_link(ctx, args, authorized.target), authorized.target, ctx.output_chars)
        # The path as it was checked (item 681), not resolved again.
        path: Path = Path(authorized.target)
        return _render(await _watch_file(ctx, args, path), str(path), ctx.output_chars)

    return make_path_tool(
        name=WATCH_VIDEO_TOOL,
        description=(
            "Watch a video file or page: frames with their moments and a timestamped transcript, an hour per call "
            "(start/end to look closer, frames=0 for the transcript alone)."
        ),
        args_model=WatchVideoArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=_classify,
        run=run,
    )


def build_video_tools() -> tuple[RegisteredTool, ...]:
    return (_watch_tool(),)
