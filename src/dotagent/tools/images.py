"""``generate_image`` (item 1969): a picture the run makes and saves as a PNG in its workspace, shown in the chat.

Two ways, and no key of its own:

- **Drawn**: the run writes an SVG or a static HTML page and Dot's own browser draws it at the size asked
  (:meth:`dotagent.browser.session.BrowserSession.render_image`). It works for every model and needs nothing: logos,
  diagrams, social cards, covers, simple illustrations, charts.
- **Photo**: a picture model's, only through the key the owner already has: Gemini's picture model on
  ``GOOGLE_API_KEY`` (:meth:`dotagent.llm.google.GoogleProvider.generate_image`). With no key the tool says so and names
  the drawn way; it never draws instead by itself, and never asks for another key.

The page of a drawn picture is the run's own writing: its scripts are barred by a content security policy put first in
the page, and the browser refuses every request it makes, so a page loads what it holds inline only (``data:``
addresses for pictures and fonts). An SVG is drawn as a picture (``<img>``), which loads nothing and runs nothing.
"""

import asyncio
import base64
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from dotagent.core.errors import (
    MissingSecretError,
    ProviderAccessError,
    ProviderAuthError,
    ProviderQuotaError,
    ToolExecutionError,
    ToolInputError,
)
from dotagent.core.images import EncodedImage, encode_image, png_of, png_size
from dotagent.llm.calls import CallOrigin
from dotagent.llm.google_image import ASPECT_RATIO_PATTERN, GeneratedImage, ImageRequest
from dotagent.llm.types import ImageData
from dotagent.policy.paths import expand_path
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_path_tool, ok_with_images
from dotagent.tools.context import ToolContext
from dotagent.tools.files import atomic_write_bytes, journal_for, write_action

GENERATE_IMAGE_TOOL: str = "generate_image"
# The most a drawn picture is on a side, and the most characters of the drawing the run writes.
MAX_SIDE: int = 4096
MIN_SIDE: int = 64
DRAWING_MAX_CHARS: int = 400_000
PHOTO_PROMPT_MAX_CHARS: int = 4_000
# What the model that made the picture is shown of it: the longest side and the JPEG quality, so that a 4096-pixel card
# does not fill its window; the owner and the file keep the picture whole.
_MODEL_VIEW_EDGE: int = 1024
_MODEL_VIEW_QUALITY: int = 80
# A page may load nothing but what it holds: no script, no network, pictures and fonts only as data: addresses.
_POLICY: str = (
    "default-src 'none'; script-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; "
    "media-src 'none'; connect-src 'none'; object-src 'none'; frame-src 'none'; form-action 'none'"
)

_DESCRIPTION: str = (
    "Make a PNG in the workspace; it shows in the chat. DRAW it by default: svg (with a viewBox) or html (static, no "
    "scripts, no web addresses) for a logo, diagram, card, cover, icon, illustration or chart; you see the result and "
    "can redraw. Give photo (words) only for a photo-like image: it needs the owner's GOOGLE_API_KEY on a billed "
    "project, fails without one and never draws instead. Give exactly one of svg, html, photo."
)


class GenerateImageArgs(ToolArgs):
    path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = Field(
        description="Where to save it: a .png path."
    )
    svg: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DRAWING_MAX_CHARS)] | None = (
        Field(default=None, description="An SVG document.")
    )
    html: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DRAWING_MAX_CHARS)] | None
    ) = Field(default=None, description="A static HTML page.")
    photo: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PHOTO_PROMPT_MAX_CHARS)] | None
    ) = Field(default=None, description="The photo, described.")
    width: int = Field(default=1024, ge=MIN_SIDE, le=MAX_SIDE, description="Pixels, for svg or html.")
    height: int = Field(default=1024, ge=MIN_SIDE, le=MAX_SIDE, description="Pixels, for svg or html.")
    aspect_ratio: Annotated[str, StringConstraints(pattern=ASPECT_RATIO_PATTERN)] | None = Field(
        default=None, description="A photo's shape: 1:1, 16:9, 9:16, 4:3, 3:2."
    )

    @model_validator(mode="after")
    def one_source(self) -> Self:
        given: int = sum(source is not None for source in (self.svg, self.html, self.photo))
        if given != 1:
            raise ValueError("give exactly one of svg, html or photo")
        if not self.path.lower().endswith(".png"):
            raise ValueError("path must end in .png: pictures are saved as PNG")
        sized: bool = bool({"width", "height"} & self.model_fields_set)
        if self.photo is not None and sized:
            raise ValueError("width and height are for a drawn picture; a photo takes aspect_ratio")
        if self.photo is None and self.aspect_ratio is not None:
            raise ValueError("aspect_ratio is for a photo; a drawn picture takes width and height")
        return self


def _svg_page(svg: str, width: int, height: int) -> str:
    """The SVG as a picture of the page's size: an ``<img>``, which neither loads nor runs anything the SVG names."""
    if "<svg" not in svg[:2000].lower():
        raise ToolInputError("svg is not an SVG document: it must hold an <svg> element", {"start": svg[:80]})
    encoded: str = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="data:image/svg+xml;base64,{encoded}" width="{width}" height="{height}" alt="">'


def drawn_page(svg: str | None, html: str | None, width: int, height: int) -> str:
    """The page the browser draws for a drawn picture: the policy that bars scripts and requests first, a white
    ground of the picture's size, then the run's SVG (as an image) or its HTML as it wrote it. A policy of the run's own
    can only narrow this one: every policy of a page applies."""
    if (svg is None) == (html is None):
        raise ToolInputError("give the drawing as svg or as html, one of the two", {})
    body: str = _svg_page(svg, width, height) if svg is not None else (html or "")
    return (
        f'<meta http-equiv="Content-Security-Policy" content="{_POLICY}">'
        f"<style>html,body{{margin:0;padding:0;background:#fff}}"
        f"body{{width:{width}px;height:{height}px;overflow:hidden}}img{{display:block}}</style>"
        f"{body}"
    )


def _model_view(png: bytes) -> ImageData:
    encoded: EncodedImage = encode_image(png, _MODEL_VIEW_EDGE, _MODEL_VIEW_QUALITY)
    return encoded.image


def _png_image(png: bytes) -> ImageData:
    return ImageData(media_type="image/png", data_b64=base64.b64encode(png).decode("ascii"))


def _no_key_words(env_name: str) -> str:
    return (
        f"No photo was made: {env_name} is not set, and a photo comes only from Google's picture model on that key "
        f"(`dotx auth set {env_name}`, from aistudio.google.com; the project needs billing, since pictures are not on "
        "the free tier). Nothing else was tried. Draw the picture instead with svg or html if a drawing will do, or "
        "tell the user a photo needs the key."
    )


async def _photo(args: GenerateImageArgs, ctx: ToolContext) -> tuple[bytes, str]:
    """The PNG of a photo from Google's picture model, and the words that say how it was made. Raises
    :class:`MissingSecretError` with no key."""
    if args.photo is None:
        raise ToolInputError("no photo prompt was given", {})
    google = ctx.runtime.config.providers.google
    request: ImageRequest = ImageRequest(model=google.image_model, prompt=args.photo, aspect_ratio=args.aspect_ratio)
    try:
        made: GeneratedImage = await ctx.runtime.router.generate_image(
            request, CallOrigin(run_id=ctx.run_id, purpose="image")
        )
    except (ProviderAccessError, ProviderQuotaError, ProviderAuthError) as error:
        raise ToolExecutionError(
            f"Google would not make the picture with {google.image_model}: {error.message}. A picture model is not on "
            "the free tier: the key's project needs billing (aistudio.google.com), or its key is wrong. Nothing else "
            "was tried; draw it with svg or html if a drawing will do.",
            {"model": google.image_model, **error.context},
        ) from error
    png, width, height = await asyncio.to_thread(png_of, made.data)
    said: str = f" The model also wrote: {made.note[:300]}" if made.note else ""
    return (
        png,
        f"a photo from google:{google.image_model} ({width}x{height}px, {made.usage.output_tokens} tokens).{said}",
    )


async def _drawing(args: GenerateImageArgs, ctx: ToolContext) -> tuple[bytes, str]:
    """The PNG of the drawing, and the words that say how it was made."""
    page: str = drawn_page(args.svg, args.html, args.width, args.height)
    png: bytes = await ctx.browser.render_image(page, args.width, args.height)
    width, height = await asyncio.to_thread(png_size, png)
    return png, f"a drawing of {width}x{height}px, drawn by Dot's browser."


def _generate_image_tool() -> RegisteredTool:
    def classify(args: GenerateImageArgs, ctx: ToolContext) -> ActionInfo:
        path: Path = expand_path(args.path, ctx.workspace)
        action: ActionInfo = write_action(
            GENERATE_IMAGE_TOOL, path, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths, "Make a picture at"
        )
        return action

    async def run(args: GenerateImageArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        path: Path = Path(authorized.target)
        try:
            png, how = await (_photo(args, ctx) if args.photo is not None else _drawing(args, ctx))
        except MissingSecretError as error:
            return fail(_no_key_words(str(error.context.get("name", "GOOGLE_API_KEY"))))
        written: int = await asyncio.to_thread(atomic_write_bytes, path, png, journal_for(ctx, GENERATE_IMAGE_TOOL))
        text: str = f"Saved {path} ({written:,} bytes): {how} It is shown in the conversation."
        return ok_with_images(text, (_model_view(png),), (_png_image(png),))

    return make_path_tool(
        name=GENERATE_IMAGE_TOOL,
        description=_DESCRIPTION,
        args_model=GenerateImageArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=180.0,
        classify=classify,
        run=run,
    )


def build_image_tools() -> tuple[RegisteredTool, ...]:
    return (_generate_image_tool(),)
