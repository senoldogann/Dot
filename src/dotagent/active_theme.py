"""Versioned shared appearance with portable color roles and bounded raster artwork."""

import fcntl
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StringConstraints, model_validator

from dotagent.core.errors import RunConflict
from dotagent.core.files import write_whole
from dotagent.core.held import read_regular
from dotagent.core.types import FrozenModel

ThemeRevision = Annotated[StrictInt, Field(ge=0)]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[a-fA-F0-9]{6}$")]
ThemeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)]
MAX_ARTWORK_CHARS: int = 1_000_000


def _luminance(color: str) -> float:
    channels: tuple[float, ...] = tuple(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    red, green, blue = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


class SharedPalette(FrozenModel):
    """sRGB colors that have the same meaning in SwiftUI and CSS."""

    canvas: HexColor
    surface: HexColor
    raised: HexColor
    text: HexColor
    muted: HexColor
    accent: HexColor

    @model_validator(mode="after")
    def readable(self) -> "SharedPalette":
        for role in ("text", "muted", "accent"):
            ink: float = _luminance(getattr(self, role))
            for ground_role in ("canvas", "surface", "raised"):
                ground: float = _luminance(getattr(self, ground_role))
                if (max(ink, ground) + 0.05) / (min(ink, ground) + 0.05) < 4.5:
                    raise ValueError(f"{role} needs at least 4.5:1 contrast against {ground_role}")
        return self


class ActiveTheme(FrozenModel):
    """A theme composed for the owner's request. Both palettes are explicit."""

    name: ThemeName
    light: SharedPalette
    dark: SharedPalette
    artwork: Literal["quiet-forest", "custom"] | None


class StoredAppearance(FrozenModel):
    theme: ActiveTheme
    jpeg: Annotated[str, StringConstraints(max_length=MAX_ARTWORK_CHARS)] | None


class ThemeSelection(FrozenModel):
    """The shared appearance and its single undo step. Null restores each device's local choice."""

    revision: ThemeRevision
    active: StoredAppearance | None
    previous: StoredAppearance | None


class AppearanceSnapshot(FrozenModel):
    revision: ThemeRevision
    active: ActiveTheme | None
    previous: ActiveTheme | None


def snapshot(selection: ThemeSelection) -> AppearanceSnapshot:
    return AppearanceSnapshot(
        revision=selection.revision,
        active=None if selection.active is None else selection.active.theme,
        previous=None if selection.previous is None else selection.previous.theme,
    )


def read_selection(home: Path) -> ThemeSelection:
    """Read the last committed selection without taking the writer lock. Blocking."""
    try:
        data: bytes = read_regular(home / "active-theme.json")
    except FileNotFoundError:
        return ThemeSelection(revision=0, active=None, previous=None)
    return ThemeSelection.model_validate_json(data)


def replace_selection(home: Path, revision: int, active: StoredAppearance | None) -> ThemeSelection:
    """Compare and replace under a writer lock. The palette and its artwork commit together. Blocking."""
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock: int = os.open(home / ".active-theme.lock", os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current: ThemeSelection = read_selection(home)
        if current.revision != revision:
            raise RunConflict(
                "The active theme changed. Read the current selection before applying another change.",
                {"expected_revision": revision, "current_revision": current.revision},
            )
        if current.active == active:
            return current
        selection: ThemeSelection = ThemeSelection(revision=revision + 1, active=active, previous=current.active)
        write_whole(home / "active-theme.json", selection.model_dump_json() + "\n")
        return selection
    finally:
        os.close(lock)


def quiet_forest() -> StoredAppearance:
    """A bundled example. Chat can compose any pair of readable palettes."""
    return StoredAppearance(
        theme=ActiveTheme(
            name="Quiet Forest",
            light=SharedPalette(
                canvas="#e1e9de",
                surface="#e7efe4",
                raised="#d4e3d1",
                text="#203426",
                muted="#435849",
                accent="#36583e",
            ),
            dark=SharedPalette(
                canvas="#19251e",
                surface="#253328",
                raised="#304233",
                text="#e0e8dc",
                muted="#b0c0ac",
                accent="#b0c8a8",
            ),
            artwork="quiet-forest",
        ),
        jpeg=None,
    )
