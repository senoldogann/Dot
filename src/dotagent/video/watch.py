"""What ``watch_video`` makes of a video, as pure functions (item 1212): the part of it a call looks at, the moments its
frames are taken at, the pieces its sound is written down in, the caption track of a link it reads, the cues of that
track, and the text the model reads.

No model takes a video: Claude takes pictures and text, and so does every model Dot runs (a model that cannot see has
the pictures described by the vision model, :mod:`dotagent.agent.vision`). So a video is given as both: frames of the
part looked at, one or more in each of its scenes (:func:`frame_plan`, item 1214), each with its moment, and what is
said, by the minute.
"""

import re
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dotagent.core.types import FrozenModel

# The longest part one call writes down: a longer video is looked at a part at a time (start and end).
MAX_SPAN_S: float = 3600.0
# A scene shorter than this (a flash, a step of a fade) is no scene of its own (:func:`scenes_of`).
MIN_SCENE_S: float = 1.0
# The length of a piece of sound the Mac writes down at a time: its moment is the transcript's timestamp.
SPEECH_PIECE_S: float = 60.0
# A paragraph of captions starts at most this often, so a transcript reads in blocks and not a line per cue.
CAPTION_BLOCK_S: float = 30.0
# A cue's timing line: ``00:01:02.345 --> 00:01:04.000 align:start``; hours may be left out.
_TIMING: re.Pattern[str] = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s+-->")
# The tags inside a YouTube cue: ``<00:00:01.234>`` word timings and ``<c>`` spans.
# A tag holds no ``<``: an unclosed one is read up to the next, not to the end of the line (item 1242).
_TAG: re.Pattern[str] = re.compile(r"<[^<>]*>")
# A caption track's name as a language tag (``en``, ``en-orig``, ``pt-BR``, ``zh-Hans``): the names come from the site
# (yt-dlp's generic reader takes a page's ``<track srclang>``), and a name of another shape is dropped as the answer is
# read, so no site text reaches Dot's own lines, an error or a file's name.
TRACK_NAME: re.Pattern[str] = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8}){0,3}")
_ENTITIES: Mapping[str, str] = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&nbsp;": " ", "&#39;": "'", "&quot;": '"'}


class Span(FrozenModel):
    """The part of a video a call looks at, in seconds from its start; ``cut`` when the call asked for more than
    :data:`MAX_SPAN_S` and the part ends sooner than asked."""

    start: float
    end: float
    cut: bool


class Cue(FrozenModel):
    """A line said, and the moment it starts, in seconds from the video's start."""

    start: float
    text: str


# Scenes that cover this share of a part or more cover all of it, as far as a line of the result says.
_WHOLE: float = 0.95
# How the frames of a look were chosen (:func:`frame_plan`): one or more in every scene, or spread evenly because the
# part has more scenes than frames, none, or scenes over less than half of it.
type FrameChoice = Literal["by_scene", "too_many", "no_scene", "quick_cuts"]


class FramePlan(FrozenModel):
    """The moments the frames are taken at, how they were chosen, how many scenes the part has and the share of the
    part they cover (from 0 to 1; what is left is quick cuts, no scene)."""

    times: tuple[float, ...]
    choice: FrameChoice
    scenes: int
    covered: float


class CaptionTrack(FrozenModel):
    """The caption track of a link that is read: its language as the site names it, and whether the site made it by
    speech recognition (``automatic``) or the uploader wrote it."""

    language: str
    automatic: bool


class TrackFormat(BaseModel):
    """One format of a caption track as yt-dlp lists it; only that there is one matters."""

    model_config = ConfigDict(extra="ignore")
    ext: str


class VideoInfo(BaseModel):
    """What yt-dlp says of a link (``yt-dlp -J``): the fields read, nothing else. ``duration`` is ``None`` for a live
    stream; ``language`` is the language the video is in, when the site says."""

    model_config = ConfigDict(extra="ignore")
    title: str = Field(max_length=2000)
    duration: float | None = None
    language: str | None = None
    uploader: str | None = None
    subtitles: dict[str, list[TrackFormat]] = Field(default_factory=dict)
    automatic_captions: dict[str, list[TrackFormat]] = Field(default_factory=dict)

    @field_validator("language")
    @classmethod
    def _tag_or_none(cls, language: str | None) -> str | None:
        """The video's language as the site says it, when it is a language tag; anything else is no language."""
        return language if language is not None and TRACK_NAME.fullmatch(language) is not None else None

    @field_validator("subtitles", "automatic_captions")
    @classmethod
    def _tags_only(cls, tracks: dict[str, list[TrackFormat]]) -> dict[str, list[TrackFormat]]:
        return {name: formats for name, formats in tracks.items() if TRACK_NAME.fullmatch(name) is not None}


def clock(seconds: float) -> str:
    """``seconds`` as a timestamp: ``04:07``, or ``1:02:03`` past an hour."""
    whole: int = max(0, int(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def span_of(duration: float, start: float, end: float | None) -> Span:
    """The part looked at: from ``start`` to ``end`` (the video's end when ``None``), within the video, and no longer
    than :data:`MAX_SPAN_S`. A start at or past the end is the caller's error: :func:`span_error` says so first."""
    last: float = duration if end is None else min(end, duration)
    if last - start > MAX_SPAN_S:
        return Span(start=start, end=start + MAX_SPAN_S, cut=True)
    return Span(start=start, end=last, cut=False)


def span_error(duration: float, start: float, end: float | None) -> str | None:
    """Why ``start`` and ``end`` name no part of a video of ``duration`` seconds, or ``None`` when they do."""
    if start >= duration:
        return f"start {clock(start)} is past the end of the video ({clock(duration)} long)"
    if end is not None and end <= start:
        return f"end {clock(end)} is not after start {clock(start)}"
    return None


def frame_times(span: Span, count: int) -> tuple[float, ...]:
    """The moments of ``count`` frames over ``span``: the middle of each of ``count`` equal slices, so the first and
    the last are not the black of a fade; at most one a second."""
    length: float = span.end - span.start
    taken: int = min(count, max(1, int(length)))
    return tuple(span.start + length * (index + 0.5) / taken for index in range(taken))


def scenes_of(span: Span, cuts: Sequence[float]) -> tuple[Span, ...]:
    """The scenes of ``span``: the parts between the moments the picture changes (``cuts``, in seconds from the video's
    start), each at least :data:`MIN_SCENE_S` long; a shorter one (a flash, a fade's step) is no scene."""
    inside: list[float] = sorted(cut for cut in cuts if span.start < cut < span.end)
    bounds: list[float] = [span.start, *inside, span.end]
    return tuple(Span(start=start, end=end, cut=False) for start, end in pairwise(bounds) if end - start >= MIN_SCENE_S)


def frame_plan(span: Span, count: int, scenes: Sequence[Span]) -> FramePlan:
    """The moments of ``count`` frames over ``span`` that see every scene: one in the middle of each, and the rest given
    to the longest scenes, each split again into equal slices. A slide that stays a second is seen as one that stays a
    minute is, where frames spread evenly over the time would miss it. At most one a second of the time the scenes
    cover (a flash dropped between them covers none). The frames are spread evenly (:func:`frame_times`) when there
    are more scenes than frames (a music video's cuts), none, or scenes over less than half the part (quick cuts, which
    say little of it); the plan says which."""
    covered: float = sum(scene.end - scene.start for scene in scenes)
    covered_share: float = covered / (span.end - span.start)
    taken: int = min(count, max(1, int(covered)))
    choice: FrameChoice
    if not scenes:
        choice = "no_scene"
    elif len(scenes) > taken:
        choice = "too_many"
    elif covered < (span.end - span.start) / 2:
        choice = "quick_cuts"
    else:
        choice = "by_scene"
    if choice != "by_scene":
        return FramePlan(times=frame_times(span, count), choice=choice, scenes=len(scenes), covered=covered_share)
    shares: list[int] = [1] * len(scenes)
    for _ in range(taken - len(scenes)):
        widest: int = max(range(len(scenes)), key=lambda at: (scenes[at].end - scenes[at].start) / shares[at])
        shares[widest] += 1
    times: tuple[float, ...] = tuple(
        scene.start + (scene.end - scene.start) * (index + 0.5) / share
        for scene, share in zip(scenes, shares, strict=True)
        for index in range(share)
    )
    return FramePlan(times=times, choice=choice, scenes=len(scenes), covered=covered_share)


def frame_plan_line(plan: FramePlan) -> str:
    """How the frames of ``plan`` were chosen, as the model reads it; frames chosen by scene say what of the part no
    scene covers (quick cuts), which no frame sees."""
    rest: str = (
        ""
        if plan.covered >= _WHOLE
        else (
            f" The other {round((1 - plan.covered) * 100)}% of the part changes too quickly to be a scene and has no "
            "frame."
        )
    )
    match plan.choice:
        case "by_scene" if plan.scenes > 1:
            return (
                f"The part has {plan.scenes} scenes (the picture changes between them); each is seen in a frame.{rest}"
            )
        case "by_scene":
            return f"The part is one scene (the picture does not change much): the frames are spread over it.{rest}"
        case "too_many":
            return f"The picture changes into {plan.scenes} scenes here, more than the frames: they are spread evenly."
        case "quick_cuts":
            found: str = (
                "The one scene of a second or more covers"
                if plan.scenes == 1
                else (f"The {plan.scenes} scenes of a second or more cover")
            )
            return f"{found} less than half of this part (the picture changes quickly): the frames are spread evenly."
        case "no_scene":
            return "No scene of a second or more was found (the picture changes quickly): the frames are spread evenly."


def speech_pieces(span: Span) -> tuple[Span, ...]:
    """``span`` cut into pieces of :data:`SPEECH_PIECE_S` seconds, the last one shorter, for the Mac to write down one
    at a time."""
    pieces: list[Span] = []
    at: float = span.start
    while at < span.end:
        until: float = min(at + SPEECH_PIECE_S, span.end)
        pieces.append(Span(start=at, end=until, cut=False))
        at = until
    return tuple(pieces)


def _base(language: str) -> str:
    """``en`` of ``en-US``, ``en-orig`` or ``en``."""
    return language.split("-", 1)[0].lower()


def choose_track(info: VideoInfo, spoken: str | None) -> CaptionTrack | None:
    """The caption track that says what is said in the video: one the uploader wrote in the language spoken, else the
    site's own recognition of the original sound (YouTube's ``<lang>-orig``, or the track in the video's language).
    ``spoken`` is the language the call names; the video's own when it names none. A track translated by the site is
    never taken: it is not what is said. ``None`` when there is none."""
    language: str | None = spoken if spoken is not None else info.language
    written: list[str] = [name for name, formats in info.subtitles.items() if formats]
    automatic: list[str] = [name for name in info.automatic_captions if info.automatic_captions[name]]
    if language is not None:
        wanted: str = _base(language)
        for name in written:
            if _base(name) == wanted:
                return CaptionTrack(language=name, automatic=False)
        if f"{wanted}-orig" in automatic:
            return CaptionTrack(language=f"{wanted}-orig", automatic=True)
        # Without an ``-orig`` track, the site's recognition is the track in the video's own language; a track in any
        # other language is the site's translation.
        own: str | None = info.language
        if own is None or _base(own) != wanted:
            return None
        for name in (own, wanted):
            if name in automatic:
                return CaptionTrack(language=name, automatic=True)
        return None
    if len(written) == 1:
        return CaptionTrack(language=written[0], automatic=False)
    original: list[str] = [name for name in automatic if name.endswith("-orig")]
    return CaptionTrack(language=original[0], automatic=True) if len(original) == 1 else None


def _seconds(match: re.Match[str]) -> float:
    hours: int = int(match.group(1) or 0)
    return hours * 3600 + int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000


def _plain(line: str) -> str:
    text: str = _TAG.sub("", line)
    for entity, character in _ENTITIES.items():
        text = text.replace(entity, character)
    return " ".join(text.split())


def parse_vtt(text: str) -> tuple[Cue, ...]:
    """The lines said in a WebVTT caption file, each once. YouTube's own recognition writes each line twice, in the cue
    it is said in and again above the next line in the next cue: a line the same as the one just taken is not taken
    again."""
    cues: list[Cue] = []
    last: str = ""
    start: float | None = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        timing: re.Match[str] | None = _TIMING.match(raw.strip())
        if timing is not None:
            start = _seconds(timing)
            continue
        if start is None:
            continue
        line: str = _plain(raw)
        if not line:
            # An empty line ends a cue; a line of a space (YouTube writes one above the first line of a cue) does not.
            if raw == "":
                start = None
            continue
        if line == last:
            continue
        cues.append(Cue(start=start, text=line))
        last = line
    return tuple(cues)


def caption_blocks(cues: Sequence[Cue], span: Span) -> tuple[Cue, ...]:
    """The cues inside ``span`` joined into paragraphs, a new one at most every :data:`CAPTION_BLOCK_S` seconds."""
    blocks: list[Cue] = []
    for cue in cues:
        if not span.start <= cue.start < span.end:
            continue
        if blocks and cue.start - blocks[-1].start < CAPTION_BLOCK_S:
            blocks[-1] = Cue(start=blocks[-1].start, text=f"{blocks[-1].text} {cue.text}")
        else:
            blocks.append(cue)
    return tuple(blocks)


def transcript_text(blocks: Sequence[Cue]) -> str:
    """The transcript as the model reads it: one paragraph per block, after its timestamp."""
    return "\n".join(f"[{clock(block.start)}] {block.text}" for block in blocks if block.text)
