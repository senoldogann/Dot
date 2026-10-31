"""The themes of one's own (item 128): theme files in the ``themes`` folder of Dot's home, one ``<id>.json`` per theme,
the file's name its id. A file is a theme file (version 1: a name, light or dark, the colours of its roles, and the
other scheme's colours under ``variants``) or the short form of one (a canvas and an accent the page derives the other
roles from, with ``colors`` laid over them). The page draws them (``static/js/appearance/own.js``); the daemon keeps
them. It checks a file the way the page reads it, on the way in and on the way out, writes a file whole or not at all,
and caps what the folder may hold as the theme format's own checks do (see
``src/dotagent/server/static/js/appearance/LICENSE``), so that a mistake dropped in the folder costs little. A file the
page could not draw is left out of the list with the reason, never read as a theme."""

import errno
import math
import os
import re
import stat
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, StrictBool, StringConstraints, ValidationError, model_validator
from pydantic_core import ErrorDetails

from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.files import write_whole
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel

log = get_logger(__name__)

THEME_SUFFIX: str = ".json"
MAX_THEME_FILES: int = 32
MAX_THEME_FILE_BYTES: int = 32 * 1024
MAX_THEME_FOLDER_BYTES: int = 192 * 1024
MAX_NAME_UNITS: int = 48
THEME_ID: re.Pattern[str] = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,47})$")
# The ids of Dot's own themes and the names of the colour schemes, which no theme of one's own takes.
RESERVED_THEME_IDS: frozenset[str] = frozenset(
    {
        "neutral",
        "paper",
        "clean",
        "rose",
        "grove",
        "ocean",
        "ember",
        "iris",
        "midnight",
        "nord",
        "solarized",
        "sepia",
        "forest",
        "graphite",
        "contrast",
        "system",
        "light",
        "dark",
    }
)
# The colour roles of a theme file; appearance/generator.js names the same, and tests/unit/test_theme_folder.py holds
# the two lists together.
THEME_ROLES: frozenset[str] = frozenset(
    {
        "canvas",
        "chrome",
        "toolbar",
        "toolbarForeground",
        "toolbarBorder",
        "toolbarControl",
        "toolbarControlForeground",
        "toolbarControlHover",
        "surface",
        "surfaceRaised",
        "surfaceOverlay",
        "text",
        "textMuted",
        "border",
        "input",
        "focus",
        "accent",
        "accentForeground",
        "secondary",
        "secondaryForeground",
        "muted",
        "mutedForeground",
        "placeholder",
        "secondaryLabel",
        "iconMuted",
        "error",
        "errorForeground",
        "errorSurface",
        "warning",
        "warningForeground",
        "warningSurface",
        "update",
        "updateForeground",
        "updateSurface",
        "accentSurface",
        "accentSurfaceForeground",
        "messageSurface",
        "messageForeground",
        "messageAction",
        "messageActionForeground",
        "messageActionHover",
        "codeBackground",
        "codeForeground",
        "searchMatchBackground",
        "searchMatchForeground",
        "searchMatchActiveBackground",
        "searchMatchActiveForeground",
        "sidebar",
        "sidebarForeground",
        "sidebarMutedForeground",
        "sidebarControlSurface",
        "sidebarRowHover",
        "sidebarRowActive",
        "sidebarRowSelected",
        "sidebarBorder",
        "terminalBackground",
        "terminalForeground",
        "terminalCursor",
        "terminalSelection",
        "terminalScrollbar",
        "terminalScrollbarHover",
    }
)

# The colours appearance/colour.js reads (parseColour), read the same way here so that the daemon keeps no colour the
# page cannot draw.
_HEX: re.Pattern[str] = re.compile(r"^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$")
_FUNCTION: re.Pattern[str] = re.compile(r"^(rgba?|oklch)\(\s*([^()]*)\)$")
_NUMBER: re.Pattern[str] = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?(?:%|deg)?$")


def _is_number(part: str) -> bool:
    if part == "none":
        return True
    return _NUMBER.fullmatch(part) is not None and math.isfinite(float(part.removesuffix("%").removesuffix("deg")))


def is_theme_colour(value: str) -> bool:
    """Whether ``value`` is a colour the page reads: hex (#rgb, #rgba, #rrggbb, #rrggbbaa), ``rgb()``/``rgba()``,
    ``oklch()`` or ``transparent``, each channel a finite number."""
    text: str = value.strip().lower()
    if text == "transparent" or _HEX.fullmatch(text) is not None:
        return True
    call: re.Match[str] | None = _FUNCTION.fullmatch(text)
    if call is None:
        return False
    sides: list[str] = call.group(2).split("/")
    main: str = sides[0]
    parts: list[str] = main.split(",") if "," in main else main.split()
    if len(sides) > 1:
        parts.append(sides[1])
    values: list[str] = [part.strip() for part in parts if part.strip() != ""]
    return 3 <= len(values) <= 4 and all(_is_number(part) for part in values)


def _colour(value: str) -> str:
    if not is_theme_colour(value):
        raise ValueError("not a colour the page reads: hex, rgb() or oklch()")
    return value


def _roles(colours: dict[str, str]) -> dict[str, str]:
    unknown: list[str] = sorted(set(colours) - THEME_ROLES)
    if unknown:
        raise ValueError(f"not a colour role of a theme: {', '.join(unknown[:3])}")
    return colours


def _name(value: str) -> str:
    # Counted as the page and the theme format's own checks count it, in UTF-16 code units.
    if len(value.encode("utf-16-le")) // 2 > MAX_NAME_UNITS:
        raise ValueError(f"a name of {MAX_NAME_UNITS} characters at most")
    return value


def _no_nulls(value: object) -> object:
    # A key without a value is left out of a theme file: the page and the theme format's own checks refuse a null where
    # a value goes.
    if isinstance(value, dict):
        empty: list[str] = sorted(str(key) for key, item in value.items() if item is None)
        if empty:
            raise ValueError(f"a key without a value is left out, not null: {', '.join(empty[:3])}")
    return value


ThemeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(_name)]
ThemeColour = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64), AfterValidator(_colour)
]
# A seed of the short form of a theme file: a canvas or an accent.
Seed = Annotated[str, StringConstraints(pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")]
Palette = Annotated[dict[str, ThemeColour], AfterValidator(_roles)]
Appearance = Literal["light", "dark"]


class ThemeVariants(FrozenModel):
    """The colours of the scheme the theme's ``appearance`` is not."""

    light: Palette | None = None
    dark: Palette | None = None

    @model_validator(mode="before")
    @classmethod
    def _keys_with_values(cls, data: object) -> object:
        return _no_nulls(data)


class ThemeCollection(FrozenModel):
    """The family of themes a theme file is filed under; kept as it came."""

    id: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]{0,127}$")]
    label: ThemeName


class ThemeFile(FrozenModel):
    """A theme as its file holds it. A role a file leaves out is the one of the default palette for its scheme (Dot's
    Rose), and with a canvas and an accent the derived palette is under them."""

    version: Literal[1] | None = None
    id: Annotated[str, StringConstraints(pattern=THEME_ID.pattern)] | None = None
    name: ThemeName
    appearance: Appearance
    canvas: Seed | None = None
    accent: Seed | None = None
    colors: Palette | None = None
    variants: ThemeVariants | None = None
    collection: ThemeCollection | None = None
    managed: StrictBool | None = None

    @model_validator(mode="before")
    @classmethod
    def _keys_with_values(cls, data: object) -> object:
        return _no_nulls(data)

    @model_validator(mode="after")
    def _drawable(self) -> Self:
        if (self.canvas is None) != (self.accent is None):
            raise ValueError("a theme derived from two colours names both, the canvas and the accent")
        if self.canvas is None and not self.colors:
            raise ValueError("a theme names its colours, or a canvas and an accent to derive them from")
        if self.variants is not None and getattr(self.variants, self.appearance) is not None:
            raise ValueError(f"the variants repeat the theme's own appearance, {self.appearance}")
        return self


class StoredTheme(FrozenModel):
    """A theme of the folder: its id (the file's name without ``.json``) and its file."""

    id: str
    file: ThemeFile


class SkippedTheme(FrozenModel):
    """A file of the folder that is not drawn, and why."""

    file: str
    reason: str


class ThemeFolder(FrozenModel):
    """The folder of the themes of one's own, the themes in it in the order of their files' names, and the files of it
    left out."""

    folder: str
    themes: list[StoredTheme]
    skipped: list[SkippedTheme]


def checked_theme_id(theme_id: str) -> str:
    """``theme_id`` when a theme of one's own may have it: lowercase letters, digits and dashes, no built-in's."""
    if THEME_ID.fullmatch(theme_id) is None:
        raise InvalidValueError(
            "a theme's id is lowercase letters, digits and dashes, 48 at most, starting with a letter or a digit",
            {"theme": theme_id[:64]},
        )
    if theme_id in RESERVED_THEME_IDS:
        raise InvalidValueError("a built-in theme has this id", {"theme": theme_id})
    return theme_id


def _reason(error: ValidationError) -> str:
    first: ErrorDetails = error.errors(include_url=False, include_context=False, include_input=False)[0]
    where: str = ".".join(str(part) for part in first["loc"])
    message: str = first["msg"].removeprefix("Value error, ")
    return f"{where}: {message}" if where != "" else message


def parse_theme_file(raw: bytes) -> ThemeFile:
    """The theme of a file's bytes (JSON in UTF-8); ``InvalidValueError`` with what is wrong when it is none."""
    try:
        return ThemeFile.model_validate_json(raw)
    except ValidationError as error:
        raise InvalidValueError(_reason(error), {}) from error


def _read_guarded(path: Path) -> bytes:
    """The bytes of a regular file of at most ``MAX_THEME_FILE_BYTES``, read through one open handle so that every check
    binds to the file read: a link is refused at the open (``O_NOFOLLOW``), a pipe does not hold it up
    (``O_NONBLOCK``), and the type and the size are the open file's."""
    try:
        descriptor: int = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise InvalidValueError("a link is not read; put the file itself in the folder", {}) from error
        raise InvalidValueError(f"the file cannot be read: {error.strerror}", {}) from error
    try:
        info: os.stat_result = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise InvalidValueError("not a file", {})
        if info.st_size > MAX_THEME_FILE_BYTES:
            raise InvalidValueError(f"larger than {MAX_THEME_FILE_BYTES // 1024} KB", {})
        data: bytes = os.read(descriptor, MAX_THEME_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(data) > MAX_THEME_FILE_BYTES:
        raise InvalidValueError(f"larger than {MAX_THEME_FILE_BYTES // 1024} KB", {})
    return data


def _theme_files(folder: Path) -> list[Path]:
    """The files of the folder that name a theme of one's own, in the order of their names."""
    if not folder.is_dir():
        return []
    return sorted(entry for entry in folder.iterdir() if entry.name.endswith(THEME_SUFFIX))


def read_theme_folder(folder: Path) -> ThemeFolder:
    """The themes of ``folder``. A file whose name is not a theme id the page may use, or which cannot be read, is
    larger than the cap or is not a theme, is left out with the reason; the first ``MAX_THEME_FILES`` files are read
    and the themes kept to ``MAX_THEME_FOLDER_BYTES`` in all. Blocking."""
    themes: list[StoredTheme] = []
    skipped: list[SkippedTheme] = []
    total: int = 0
    for index, path in enumerate(_theme_files(folder)):
        if index >= MAX_THEME_FILES:
            log.warning("theme_skipped", file=path.name, reason="past the files read")
            skipped.append(SkippedTheme(file=path.name, reason=f"Dot reads the first {MAX_THEME_FILES} files"))
            break
        theme_id: str = path.name.removesuffix(THEME_SUFFIX)
        try:
            checked_theme_id(theme_id)
            raw: bytes = _read_guarded(path)
            theme: ThemeFile = parse_theme_file(raw)
        except InvalidValueError as error:
            log.warning("theme_skipped", file=path.name, reason=error.message)
            skipped.append(SkippedTheme(file=path.name, reason=error.message))
            continue
        total += len(raw)
        if total > MAX_THEME_FOLDER_BYTES:
            log.warning("theme_skipped", file=path.name, reason="past the bytes read")
            skipped.append(
                SkippedTheme(
                    file=path.name, reason=f"the themes before it fill the {MAX_THEME_FOLDER_BYTES // 1024} KB"
                )
            )
            break
        themes.append(StoredTheme(id=theme_id, file=theme))
    return ThemeFolder(folder=str(folder), themes=themes, skipped=skipped)


def theme_text(theme: ThemeFile) -> str:
    """The file of a theme: its JSON with two spaces of indent and a line end."""
    return theme.model_dump_json(indent=2, exclude_none=True) + "\n"


def save_theme(folder: Path, theme_id: str, theme: ThemeFile) -> StoredTheme:
    """Keep ``theme`` as ``<theme_id>.json`` in ``folder``, a new theme or over the one of that id, within the caps of
    the folder. Blocking."""
    checked_theme_id(theme_id)
    if theme.id is not None and theme.id != theme_id:
        raise InvalidValueError("the theme's id is not the one it is saved under", {"theme": theme_id, "id": theme.id})
    stored: ThemeFile = theme.model_copy(update={"id": theme_id})
    text: str = theme_text(stored)
    size: int = len(text.encode("utf-8"))
    if size > MAX_THEME_FILE_BYTES:
        raise InvalidValueError(
            f"a theme's file is {MAX_THEME_FILE_BYTES // 1024} KB at most", {"theme": theme_id, "bytes": size}
        )
    path: Path = folder / f"{theme_id}{THEME_SUFFIX}"
    others: list[Path] = [entry for entry in _theme_files(folder) if entry != path]
    if len(others) >= MAX_THEME_FILES:
        raise InvalidValueError(
            f"the folder holds {MAX_THEME_FILES} themes at most; delete one first",
            {"theme": theme_id, "files": len(others)},
        )
    total: int = size + sum(entry.lstat().st_size for entry in others)
    if total > MAX_THEME_FOLDER_BYTES:
        raise InvalidValueError(
            f"the folder holds {MAX_THEME_FOLDER_BYTES // 1024} KB of themes at most; delete one first",
            {"theme": theme_id, "bytes": total},
        )
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_whole(path, text)
    return StoredTheme(id=theme_id, file=stored)


def delete_theme(folder: Path, theme_id: str) -> None:
    """Delete the theme ``theme_id`` from ``folder``. Blocking."""
    checked_theme_id(theme_id)
    path: Path = folder / f"{theme_id}{THEME_SUFFIX}"
    if not path.is_file() or path.is_symlink():
        raise NotFoundError("no theme of one's own has this id", {"theme": theme_id})
    path.unlink()
