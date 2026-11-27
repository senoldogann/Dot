"""Validated conversation cards and the tool that adds them to a run's event stream."""

import asyncio
import base64
import io
import os
from pathlib import Path
from typing import Annotated, Literal, Self

from PIL import Image
from pydantic import Field, StringConstraints, WithJsonSchema, model_validator

from dotagent.agent.held_secrets import all_held_values
from dotagent.core.errors import ToolInputError
from dotagent.core.held import held_folder, open_regular
from dotagent.core.text import redact_json
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import ImageData
from dotagent.policy.paths import classify_path, expand_path
from dotagent.policy.types import ActionInfo
from dotagent.secrets import known_secrets
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext
from dotagent.tools.third_party import holds_third_party

SHOW_CARDS_TOOL: str = "show_cards"
CARDS_EVENT: str = "cards_shown"
MAX_IMAGE_BYTES: int = 2_000_000
MAX_IMAGE_PIXELS: int = 8_000_000
_MAX_MARKS: int = 60
_MEDIA: dict[str, str] = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}

# The model reads the schema with every request of every run that has the tool, so the bounds of a text and of a number
# are checked here and left out of it (`WithJsonSchema`): the description of the tool says the sizes that matter.
type Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120), WithJsonSchema({"type": "string"})
]
type PostText = Annotated[
    str, StringConstraints(min_length=1, max_length=5000, pattern=r"\S"), WithJsonSchema({"type": "string"})
]
type Cell = Annotated[str, StringConstraints(max_length=240), WithJsonSchema({"type": "string"})]
type Row = Annotated[tuple[Cell, ...], Field(min_length=1, max_length=8)]
type Number = Annotated[
    float, Field(strict=True, ge=-1e12, le=1e12, allow_inf_nan=False), WithJsonSchema({"type": "number"})
]
type ChoiceText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240), WithJsonSchema({"type": "string"})
]


class WorkspaceImage(FrozenModel):
    path: Annotated[str, StringConstraints(min_length=1, max_length=500), WithJsonSchema({"type": "string"})] = Field(
        description="Raster file in the workspace."
    )
    alt: Label


class StoredImage(FrozenModel):
    name: str
    alt: Label


class PostDraft[Media](FrozenModel):
    kind: Literal["post_draft"]
    platform: Label
    account: Label
    text: PostText
    media: tuple[Media, ...] = Field(default=(), max_length=4)


class Series(FrozenModel):
    name: Label
    values: tuple[Number, ...] = Field(min_length=1, max_length=_MAX_MARKS)


class Chart(FrozenModel):
    kind: Literal["chart"]
    title: Label
    chart_type: Literal["line", "bar", "area", "pie", "scatter"]
    x: tuple[Label | Number, ...] = Field(
        min_length=1, max_length=_MAX_MARKS, description="Shared x labels, or numeric coordinates for scatter."
    )
    series: tuple[Series, ...] = Field(min_length=1, max_length=6)
    x_label: Label | None = None
    y_label: Label | None = None
    unit: Label | None = None

    @model_validator(mode="after")
    def _matches_axes(self) -> Self:
        if any(len(series.values) != len(self.x) for series in self.series):
            raise ValueError("every series needs one value per x entry")
        if len({series.name for series in self.series}) != len(self.series):
            raise ValueError("series names must be distinct")
        if self.chart_type == "scatter" and any(isinstance(x, str) for x in self.x):
            raise ValueError("scatter needs numeric x coordinates")
        if self.chart_type == "pie":
            if len(self.series) != 1:
                raise ValueError("pie needs exactly one series")
            values: tuple[Number, ...] = self.series[0].values
            if min(values) < 0 or sum(values) <= 0:
                raise ValueError("pie needs nonnegative values with a positive total")
        return self


class Table(FrozenModel):
    kind: Literal["table"]
    title: Label
    columns: tuple[Label, ...] = Field(min_length=1, max_length=8)
    rows: tuple[Row, ...] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _rectangular(self) -> Self:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every row needs one cell per column")
        return self


class Choice(FrozenModel):
    kind: Literal["choice"]
    prompt: Label
    options: tuple[ChoiceText, ...] = Field(
        min_length=2,
        max_length=6,
        description="Visible answers; a tap writes one into the owner's message box for them to send.",
    )

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if len(set(self.options)) != len(self.options):
            raise ValueError("choice answers must be distinct")
        return self


class ImageGallery[Media](FrozenModel):
    kind: Literal["image_gallery"]
    title: Label
    images: tuple[Media, ...] = Field(min_length=1, max_length=4)


# Literal kinds make the alternatives exclusive while keeping the exported tool schema on anyOf.
type Card = PostDraft[WorkspaceImage] | Chart | Table | Choice | ImageGallery[WorkspaceImage]
type ShownCard = PostDraft[StoredImage] | Chart | Table | Choice | ImageGallery[StoredImage]


class ShowCardsArgs(ToolArgs):
    cards: tuple[Card, ...] = Field(min_length=1, max_length=6, description="Cards in display order.")


class CardsShown(FrozenModel):
    version: Literal[1]
    cards: tuple[ShownCard, ...]


def _image_data(
    source: WorkspaceImage, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> ImageData:
    """Read a bounded raster through a held path. Reject formats that can carry active markup."""
    path: Path = expand_path(source.path, workspace)
    if (
        Path(source.path).is_absolute()
        or classify_path(path, workspace, protected, dot_paths) != "workspace"
        or holds_third_party(path, workspace)
    ):
        raise ToolInputError(
            "card images must be raster files you made in the workspace, named by a path relative to it",
            {"path": source.path},
        )

    folder: int = held_folder(path.parent)
    try:
        descriptor: int = open_regular(folder, path.name)
    finally:
        os.close(folder)
    with os.fdopen(descriptor, "rb") as handle:
        data: bytes = handle.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image exceeds {MAX_IMAGE_BYTES:,} bytes")
    with Image.open(io.BytesIO(data), formats=tuple(_MEDIA)) as image:
        if image.format is None or image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError(f"image must have a supported format and at most {MAX_IMAGE_PIXELS:,} pixels")
        media: str = _MEDIA[image.format]
        image.verify()
    return ImageData(media_type=media, data_b64=base64.b64encode(data).decode("ascii"))


async def _store_images(images: tuple[WorkspaceImage, ...], ctx: ToolContext) -> tuple[StoredImage, ...]:
    loaded: list[ImageData] = []
    for source in images:
        try:
            loaded.append(
                await asyncio.to_thread(
                    _image_data, source, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths
                )
            )
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ToolInputError(
                "card image could not be read as a bounded PNG, JPEG, WebP or GIF",
                {"path": source.path, "error": str(exc)},
            ) from exc
    names: list[str] = await ctx.runtime.store.save_images(loaded)
    return tuple(StoredImage(name=name, alt=source.alt) for name, source in zip(names, images, strict=True))


async def _shown(card: Card, ctx: ToolContext) -> ShownCard:
    match card:
        case PostDraft():
            media = await _store_images(card.media, ctx)
            return PostDraft[StoredImage](
                kind=card.kind, platform=card.platform, account=card.account, text=card.text, media=media
            )
        case ImageGallery():
            return ImageGallery[StoredImage](
                kind=card.kind, title=card.title, images=await _store_images(card.images, ctx)
            )
        case _:
            return card


def build_cards_tool() -> RegisteredTool:
    def classify(args: ShowCardsArgs, ctx: ToolContext) -> ActionInfo:
        return ActionInfo(
            tool=SHOW_CARDS_TOOL,
            kind="meta",
            risk="safe",
            summary="Show cards in the conversation",
            target="",
            flags=(),
        )

    async def run(args: ShowCardsArgs, ctx: ToolContext) -> ToolResult:
        cards: list[ShownCard] = [await _shown(card, ctx) for card in args.cards]
        shown: CardsShown = CardsShown(version=1, cards=tuple(cards))
        known: tuple[str, ...] = (
            *await asyncio.to_thread(known_secrets, filled=True),
            *await asyncio.to_thread(all_held_values),
        )
        data: JsonObject = shown.model_dump(mode="json")
        masked: JsonObject = {field: redact_json(value, known) for field, value in data.items()}
        await ctx.runtime.bus.emit(ctx.run_id, CARDS_EVENT, masked, True)
        return ok("The cards are shown in the conversation. Nothing was sent or published.")

    return make_tool(
        name=SHOW_CARDS_TOOL,
        description=(
            "Draw a chart, table, choice or image gallery, or show post drafts in the conversation. "
            "Group draft alternatives in one call. Post drafts are previews only. "
            "Use draft_message for sendable email and iMessage drafts. "
            "Images must be raster files you made in the workspace. "
            "Titles and labels take up to 120 characters, a post 5,000, a table cell 240. "
            "The web UI draws cards; the phone's app and the channels do not yet, so say what matters in your answer."
        ),
        args_model=ShowCardsArgs,
        modes=ALL_MODES,
        parallel_safe=False,
        timeout_s=30.0,
        classify=classify,
        run=run,
    )
