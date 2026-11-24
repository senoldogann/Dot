"""Read, compose or restore Dot's shared appearance from an interactive owner chat."""

import asyncio
from pathlib import Path

from pydantic import Field, model_validator

from dotagent.active_theme import (
    MAX_ARTWORK_CHARS,
    ActiveTheme,
    StoredAppearance,
    ThemeRevision,
    ThemeSelection,
    read_selection,
    replace_selection,
    snapshot,
)
from dotagent.core.errors import RunConflict, ToolInputError
from dotagent.core.held import held_folder, open_regular
from dotagent.core.images import encode_image
from dotagent.core.paths import dot_home
from dotagent.policy.paths import classify_path, expand_path
from dotagent.policy.types import ActionInfo
from dotagent.store.models import RunRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, make_path_tool, make_tool, ok
from dotagent.tools.context import ToolContext

MAX_SOURCE_BYTES: int = 16 * 1024 * 1024


class ReadThemeArgs(ToolArgs):
    """Read the active theme revision."""


class ApplyThemeArgs(ToolArgs):
    revision: ThemeRevision
    theme: ActiveTheme | None = Field(description="Both palettes for the requested appearance, or null to clear it.")
    image_path: str | None = Field(
        default=None,
        description="Optional raster image in this chat's workspace. Omit it to reuse existing custom artwork.",
    )

    @model_validator(mode="after")
    def image_needs_custom_theme(self) -> "ApplyThemeArgs":
        if self.image_path is not None and (self.theme is None or self.theme.artwork != "custom"):
            raise ValueError("image_path requires a theme with custom artwork")
        return self


class RestoreThemeArgs(ToolArgs):
    revision: ThemeRevision


def _classify_read(args: ReadThemeArgs, ctx: ToolContext) -> ActionInfo:
    return ActionInfo(tool="theme_read", kind="read", risk="safe", summary="Read Dot's appearance", target="", flags=())


def _classify_apply(args: ApplyThemeArgs, ctx: ToolContext) -> ActionInfo:
    summary: str = "Restore device appearance" if args.theme is None else f"Apply the theme {args.theme.name}"
    artwork_path: str = ""
    if args.image_path is not None:
        path: Path = expand_path(args.image_path, ctx.workspace)
        if classify_path(path, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths) != "workspace":
            raise ToolInputError("Use a background image in this chat's workspace.", {"path": args.image_path})
        artwork_path = str(path)
        summary = f"{summary} with artwork from {path.name}"
    return ActionInfo(
        tool="theme_apply",
        kind="write",
        risk="low",
        summary=summary,
        target=str(dot_home() / "active-theme.json"),
        flags=("self_change",),
        body=artwork_path,
    )


def _classify_restore(args: RestoreThemeArgs, ctx: ToolContext) -> ActionInfo:
    return ActionInfo(
        tool="theme_undo",
        kind="write",
        risk="low",
        summary="Restore the previous shared appearance",
        target=str(dot_home() / "active-theme.json"),
        flags=("self_change",),
    )


async def _read(args: ReadThemeArgs, ctx: ToolContext) -> ToolResult:
    selection: ThemeSelection = await asyncio.to_thread(read_selection, dot_home())
    return ok(snapshot(selection).model_dump_json())


async def _require_owner(ctx: ToolContext) -> None:
    run: RunRecord = await ctx.runtime.store.get_run(ctx.run_id)
    if run.parent_run_id is not None or run.source != "ui":
        raise ToolInputError("Change Dot's appearance in a direct chat.", {"run_id": ctx.run_id})


def _read_artwork(path: Path) -> str:
    """Read the authorized regular file with a hard byte limit and store only a normalized JPEG."""
    import os

    folder: int = held_folder(path.parent)
    try:
        descriptor: int = open_regular(folder, path.name)
    finally:
        os.close(folder)
    with os.fdopen(descriptor, "rb") as handle:
        data: bytes = handle.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ToolInputError("Theme artwork is limited to 16 MB before conversion.", {"path": str(path)})
    encoded: str = encode_image(data, 1440, 72).image.data_b64
    if len(encoded) > MAX_ARTWORK_CHARS:
        raise ToolInputError("Theme artwork is too large after conversion. Use a smaller image.", {"path": str(path)})
    return encoded


async def _apply(args: ApplyThemeArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
    await _require_owner(ctx)
    home: Path = dot_home()
    current: ThemeSelection = await asyncio.to_thread(read_selection, home)
    if current.revision != args.revision:
        raise RunConflict("Read the current appearance before changing it.", {"current_revision": current.revision})
    jpeg: str | None = None
    if args.theme is not None and args.theme.artwork == "custom":
        if args.image_path is not None:
            jpeg = await asyncio.to_thread(_read_artwork, Path(authorized.body))
        elif current.active is not None:
            jpeg = current.active.jpeg
        if jpeg is None:
            raise ToolInputError("Supply image_path for new custom artwork.", {})
    active: StoredAppearance | None = None if args.theme is None else StoredAppearance(theme=args.theme, jpeg=jpeg)
    updated: ThemeSelection = await asyncio.to_thread(replace_selection, home, args.revision, active)
    return ok(snapshot(updated).model_dump_json())


async def _undo(args: RestoreThemeArgs, ctx: ToolContext) -> ToolResult:
    await _require_owner(ctx)
    home: Path = dot_home()
    current: ThemeSelection = await asyncio.to_thread(read_selection, home)
    updated: ThemeSelection = await asyncio.to_thread(replace_selection, home, args.revision, current.previous)
    return ok(snapshot(updated).model_dump_json())


def build_appearance_tools() -> tuple[RegisteredTool, ...]:
    writable = ALL_MODES.difference({"research"})
    return (
        make_tool(
            "theme_read",
            "Read Dot's live shared appearance and revision. Read it before applying or undoing a change.",
            ReadThemeArgs,
            ALL_MODES,
            True,
            10,
            _classify_read,
            _read,
        ),
        make_path_tool(
            "theme_apply",
            "When the owner explicitly asks to change Dot's appearance, compose any look as light and dark sRGB "
            "palettes, respecting their request, then apply it. All ink colors must contrast at least 4.5:1 with "
            "every ground. Keep the existing roles when the user requests a refinement. Use artwork quiet-forest "
            "for the bundled forest, or custom with an image_path in the workspace. Null restores device appearance. "
            "Quiet Forest is one example; use any suitable palette.",
            ApplyThemeArgs,
            writable,
            False,
            20,
            _classify_apply,
            _apply,
        ),
        make_tool(
            "theme_undo",
            "Restore the appearance before the last change when the owner asks.",
            RestoreThemeArgs,
            writable,
            False,
            10,
            _classify_restore,
            _undo,
        ),
    )
